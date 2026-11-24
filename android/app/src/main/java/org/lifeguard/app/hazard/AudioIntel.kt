package org.lifeguard.app.hazard

import java.math.BigDecimal
import java.math.RoundingMode
import kotlin.math.abs
import kotlin.math.exp
import kotlin.math.max
import kotlin.math.sqrt

/** YAMNet: גל קול 16kHz מונו -> (scores[N][521], embeddings[N][1024]). במכשיר: TFLite (YamnetTflite). */
fun interface YamnetRunner {
    fun run(x16: FloatArray): YamnetOutput
}

class YamnetOutput(val scores: Array<FloatArray>, val emb: Array<FloatArray>)

data class Quality(
    /** ok / degraded / unavailable */
    val status: String,
    val reason: String,
    val seconds: Double? = null,
    val rms: Double? = null,
    val peak: Double? = null,
    val clipRatio: Double? = null,
)

/** שורת קטגוריה בדו"ח (audio_intel.analyze rows). score/threshold/margin מעוגלים כמו ב-Python; rawScore - לא. */
data class HazardEvent(
    val category: String,
    val labelHe: String?,
    val score: Double?,
    val threshold: Double?,
    val detected: Boolean,
    val atSeconds: Double = 0.0,
    val detectionConfidence: Double?,
    val faPerHour: Double? = null,
    val severityPrior: String?,
    val margin: Double?,
    val nearThreshold: Boolean,
    val model: String?,
    val rawScore: Double? = null,
)

data class DistressResult(
    /** logit מקסימלי אחרי החלקה, מעוגל ל-3 ספרות (כמו ב-Python) */
    val score: Double?,
    val detected: Boolean,
    val probability: Double?,
    val atSeconds: Double? = null,
    val variant: String? = null,
)

data class SuspectedUnvalidated(val category: String, val labelHe: String, val score: Double, val note: String)

data class HazardReport(
    val engine: String,
    val modelVersion: String?,
    val quality: Quality,
    val events: List<HazardEvent> = emptyList(),
    val top: List<HazardEvent> = emptyList(),
    val categoryScores: Map<String, Double> = emptyMap(),
    val distress: DistressResult? = null,
    val suspectedUnvalidated: List<SuspectedUnvalidated> = emptyList(),
)

/**
 * מנוע אירועי הסכנה הקוליים - העתק של audio_intel.analyze (v1 + v2) ושל acoustic_ai_module.analyze_embeddings
 * (ראש המצוקה). danger_probability לא קיים בכוונה (כמו ב-Windows: זיהוי צליל != סכנה). גלאי "צליל חריג" (anomaly)
 * לא מומש - אין לו מודל במאגר.
 */
class AudioIntel(val models: HazardModels) {
    private val m = models

    fun quality(x: FloatArray?, sr: Int = HazardFeatures.SR): Quality {
        if (x == null) return Quality("unavailable", "אין הקלטה מהמיקרופון")
        if (x.isEmpty()) return Quality("unavailable", "ההקלטה ריקה")
        var peak = 0f
        var sq = 0.0
        var clipped = 0
        val clipLevel = m.clipLevel.toFloat()
        for (v in x) {
            val a = abs(v)
            if (a > peak) peak = a
            sq += (v * v).toDouble()
            if (a >= clipLevel) clipped++
        }
        val rms = sqrt(sq / x.size)
        val seconds = x.size / sr.toDouble()
        val clip = clipped.toDouble() / x.size
        val base = Quality("ok", "", pyRound(seconds, 2), pyRound(rms, 5), pyRound(peak.toDouble(), 5), pyRound(clip, 4))
        return when {
            peak <= m.digitalSilencePeak ->
                base.copy(status = "unavailable", reason = "המיקרופון החזיר שקט דיגיטלי מוחלט - כנראה מושתק, חסום או תקול")
            seconds < m.minSeconds ->
                base.copy(status = "degraded", reason = "הקלטה קצרה מדי (${fmt(seconds, 2)} ש') לזיהוי אירועים")
            clip > m.clipRatioDegraded ->
                base.copy(status = "degraded", reason = "רוויה במיקרופון (${fmt(clip * 100, 1)}% מהדגימות בתקרה) - " +
                    "צליל חזק מאוד; הסיווג פחות אמין")
            else -> base
        }
    }

    /**
     * x: גל קול 16kHz. null כשאין YAMNet (המנוע לא זמין). שקט דיגיטלי -> דו"ח עם quality=unavailable ובלי אירועים.
     */
    fun analyze(x: FloatArray, yam: YamnetRunner?): HazardReport? {
        val q = quality(x)
        if (yam == null) return null
        val empty = HazardReport(m.engine, m.v1Version, q)
        if (q.status == "unavailable") return empty
        val out = yam.run(x)
        if (out.emb.isEmpty()) return empty
        var distress = analyzeEmbeddings(out.emb)
        var modelVersion = m.v1Version
        val v2Scores = HashMap<String, V2Score>()
        if (m.v2Version != null) {
            val wins = HazardFeatures.windows(x)
            val perWin = wins.map { (_, w) -> val o = if (wins.size == 1) out else yam.run(w); Triple(o.scores, o.emb, w) }
            for (c in m.v2) {
                var best: V2Score? = null
                for ((ws, we, w) in perWin) {
                    val r = v2WindowScore(c, ws, we, w)
                    if (best == null || r.final - r.thr > best.final - best.thr) best = r
                }
                v2Scores[c.name] = best!!
            }
            v2Scores["distress_voice"]?.let {
                distress = DistressResult(pyRound(it.final, 3), it.detected, pyRound(it.conf, 4), 0.0, "hazard_v2")
            }
            modelVersion = "${m.v1Version}+${m.v2Version}"
        }
        val rows = ArrayList<HazardEvent>()
        for (c in m.v1) {
            val v = v2Scores[c.name]
            if (v != null) {
                rows += HazardEvent(c.name, c.labelHe, pyRound(v.final, 4), pyRound(v.thr, 4), v.detected, 0.0,
                    pyRound(v.conf, 4), null, c.severityPrior, pyRound(v.final - v.thr, 4), abs(v.final - v.thr) <= 0.5,
                    if (m.v2ByName.getValue(c.name).twoStage) "v2_two_stage" else "v2_stage1", v.final)
                continue
            }
            val frames = categoryFrames(c, out.scores, out.emb)
            var i = 0
            for (j in frames.indices) if (frames[j] > frames[i]) i = j
            val s = frames[i]
            val thr = c.threshold
            val topThr = c.faTable.lastOrNull()?.first ?: thr
            val span = max(1e-6, abs(topThr - thr))
            rows += HazardEvent(c.name, c.labelHe, pyRound(s, 4), pyRound(thr, 4), s >= thr, pyRound(i * m.frameHopS, 2),
                pyRound(sigmoid(c.plattA * s + c.plattB), 4), faAt(c.faTable, s), c.severityPrior, pyRound(s - thr, 4),
                abs(s - thr) <= m.nearThresholdFraction * span, c.champion, s)
        }
        val sorted = rows.sortedByDescending { it.margin }          // יציב, כמו list.sort ב-Python
        val unval = m.unvalidated.mapNotNull { u ->
            var s = Double.NEGATIVE_INFINITY
            for (f in out.scores) for (k in u.yamIdx) s = max(s, f[k].toDouble())
            if (s >= u.threshold) SuspectedUnvalidated(u.name, u.labelHe, pyRound(s, 4), u.note) else null
        }
        return HazardReport(m.engine, modelVersion, q, sorted.filter { it.detected }, sorted.take(3),
            LinkedHashMap<String, Double>().apply { sorted.forEach { put(it.category, it.score!!) } }, distress, unval)
    }

    private class V2Score(val final: Double, val thr: Double, val detected: Boolean, val conf: Double)

    /** ציון v2 לחלון: שלב 1, ואם הקטגוריה דו-שלבית גם שלב 2 (מאמת הקשרי). */
    private fun v2WindowScore(c: V2Category, scores: Array<FloatArray>, emb: Array<FloatArray>, x16: FloatArray): V2Score {
        val l1 = c.s1.logit(HazardFeatures.stage1Vector(emb))
        var final = l1
        var thr = c.t1
        var det = l1 >= c.t1
        if (c.twoStage) {
            val l2 = c.s2!!.logit(HazardFeatures.verifierVector(l1, scores, m.curatedIdx, x16))
            final = l2; thr = c.t2
            det = det && l2 >= c.t2
        }
        return V2Score(final, thr, det, sigmoid(c.plattA * final + c.plattB))
    }

    private fun categoryFrames(c: V1Category, scores: Array<FloatArray>, emb: Array<FloatArray>): DoubleArray =
        if (c.champion == "head") DoubleArray(emb.size) { c.head!!.logit(emb[it]) }
        else DoubleArray(scores.size) { f -> c.yamIdx.maxOf { scores[f][it].toDouble() } }

    /** acoustic_ai_module.analyze_embeddings: context features -> ראש לינארי -> החלקה -> מקסימום. */
    fun analyzeEmbeddings(emb: Array<FloatArray>): DistressResult? {
        if (emb.isEmpty()) return null
        val d = m.distress
        val x = if (d.context) contextFeatures(emb) else emb
        val s = smoothScores(DoubleArray(x.size) { d.head.logit(x[it]) }, d.smooth)
        var i = 0
        for (j in s.indices) if (s[j] > s[i]) i = j
        val score = s[i]
        return DistressResult(pyRound(score, 3), score >= d.thresholdLogit, pyRound(sigmoid(score), 4),
            pyRound(i * HOP_SECONDS, 2))
    }

    companion object {
        const val FRAME_WIN = 15600
        const val FRAME_HOP = 7680
        const val HOP_SECONDS = FRAME_HOP / 16000.0

        /** מספר מסגרות YAMNet לקלט באורך n (research_tools/yamnet_detector.n_frames). */
        fun nFrames(n: Int): Int = 1 + max(0, Math.ceil((n - FRAME_WIN) / FRAME_HOP.toDouble()).toInt())

        fun sigmoid(z0: Double): Double {
            val z = z0.coerceIn(-60.0, 60.0)
            return 1.0 / (1.0 + exp(-z))
        }

        /** round(x, n) של Python: עיגול חצי-לזוגי של הערך הבינארי המדויק. */
        fun pyRound(x: Double, n: Int): Double =
            if (x.isNaN() || x.isInfinite()) x else BigDecimal(x).setScale(n, RoundingMode.HALF_EVEN).toDouble()

        /** f"{x:.nf}" של Python */
        fun fmt(x: Double, n: Int): String = BigDecimal(x).setScale(n, RoundingMode.HALF_EVEN).toPlainString()

        /** התראות שווא לשעה שנמדדו בסף הגבוה ביותר שעדיין <= הציון (שמרני). */
        fun faAt(table: List<Pair<Double, Double>>, score: Double): Double? {
            var rate: Double? = null
            for ((thr, fa) in table) { if (score >= thr) rate = fa else break }
            return rate
        }

        /** הקשר זמני לכל מסגרת: [היא, ממוצע ±1 מסגרת, מקסימום ±1] (yamnet_detector.context_features). */
        fun contextFeatures(emb: Array<FloatArray>): Array<FloatArray> {
            val n = emb.size
            val d = emb[0].size
            return Array(n) { t ->
                val a = emb[max(0, t - 1)]
                val b = emb[t]
                val c = emb[minOf(n - 1, t + 1)]
                FloatArray(3 * d).also { o ->
                    for (j in 0 until d) {
                        o[j] = b[j]
                        o[d + j] = (a[j] + b[j] + c[j]) / 3f
                        o[2 * d + j] = maxOf(a[j], b[j], c[j])
                    }
                }
            }
        }

        /** ממוצע נע קדימה ברוחב width (ריפוד בערך האחרון), כמו yamnet_detector.smooth_scores. */
        fun smoothScores(s: DoubleArray, width: Int): DoubleArray {
            if (width <= 1 || s.size < 2) return s
            val k = 1.0 / width
            return DoubleArray(s.size) { i ->
                var acc = 0.0
                for (j in 0 until width) acc += s[minOf(s.size - 1, i + j)] * k
                acc
            }
        }
    }
}
