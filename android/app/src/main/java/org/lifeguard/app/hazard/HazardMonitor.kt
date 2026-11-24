package org.lifeguard.app.hazard

/**
 * זיהוי סכנה ברקע: מקטעי קול -> חלונות של 1.5 שנ' -> audio_intel -> AlertManager -> חלון / רישום בלבד.
 * העתק של hazard_monitor.py (Windows), כולל כלל "רצף צעקות" (models/shout_sequence.json) והסיבות ל"לא הוצג".
 *
 * - אין שמירת אודיו. רק מטא-דאטה של הזיהוי עוברת ל-record (יומן ההתראות המוצפן).
 * - לעולם לא מפעיל SOS ולא פונה לאיש: הפלט היחיד הוא onAlert (חלון/התראה) ו-record.
 */
class HazardMonitor(
    private val analyze: (FloatArray) -> HazardReport?,
    private val canShow: () -> Pair<Boolean, String?> = { true to null },
    private val onAlert: (kind: String, alert: HazardAlert) -> Unit = { _, _ -> },
    private val record: (alert: HazardAlert, shown: Boolean, notShownReason: String?) -> Unit = { _, _, _ -> },
    private val prefs: () -> HazardPrefs = { HazardPrefs() },
    val manager: AlertManager = AlertManager(),
    var sequenceRule: ShoutRule? = null,
) {
    data class Decision(val kind: String, val alert: HazardAlert, val show: Boolean, val why: String?)

    private class Soft(val ts: Double, val hit: Boolean, val prob: Double)

    private var soft = ArrayList<Soft>()
    private var seqConf = 0.0
    val stats = linkedMapOf("chunks" to 0L, "analyzed" to 0L, "detections" to 0L)

    /** מקטע אחד (16kHz) -> אפס או יותר החלטות. now - שניות epoch (לכלל הרצף). */
    @Synchronized
    fun process(chunk: FloatArray, now: Double = System.currentTimeMillis() / 1000.0): List<Decision> {
        stats["chunks"] = stats.getValue("chunks") + 1
        val sr = HazardFeatures.SR
        if (chunk.size < MIN_SECONDS * sr) return emptyList()
        val maxN = (MAX_SECONDS * sr).toInt()
        val x = if (chunk.size > maxN) chunk.copyOfRange(chunk.size - maxN, chunk.size) else chunk
        val n = (WINDOW_S * sr).toInt()
        val wins = ArrayList<FloatArray>()
        var i = 0
        while (i < x.size) { wins += x.copyOfRange(i, minOf(x.size, i + n)); i += n }
        if (wins.size > 1 && wins.last().size < n / 2) wins.removeAt(wins.size - 1)
        val reps = ArrayList<HazardReport>()
        for ((j, w) in wins.withIndex()) {
            val r = analyze(w)
            stats["analyzed"] = stats.getValue("analyzed") + 1
            if (r == null || r.quality.status == "unavailable") continue
            reps += r
            noteSoft(r, now - (wins.size - 1 - j) * WINDOW_S)
        }
        if (reps.isEmpty()) return emptyList()
        var rep = reps[0]
        for (r in reps) if (key(r) > key(rep)) rep = r
        val events = mergeEvents(reps)
        var d = rep.distress
        if (reps.any { it.distress?.detected == true }) {
            // Python: max(..., key=lambda d: d.get("score") or -1e9) - ציון 0.0 נחשב "אין" (falsy), בתיקו הראשון
            fun k(x: DistressResult?) = x?.score?.takeIf { it != 0.0 } ?: -1e9
            var best: DistressResult? = reps[0].distress
            for (r in reps) if (k(r.distress) > k(best)) best = r.distress
            d = best
        }
        val dets = ArrayList<Detection>()
        for (e in events) {
            if (HazardAlerts.rank(e.severityPrior) < HazardAlerts.rank("low")) continue     // "מידע" (סירנה) - לא חלון
            dets += Detection(e.category, e.severityPrior ?: "info", e.detectionConfidence ?: 0.0, e.labelHe, explain(e),
                linkedMapOf("score" to e.score, "threshold" to e.threshold, "model" to e.model,
                    "model_version" to rep.modelVersion, "quality" to rep.quality.status, "near_threshold" to e.nearThreshold))
        }
        if (d?.detected == true) {
            dets += Detection("distress_voice", "medium", d.probability ?: 0.0, DISTRESS_LABEL_HE, DISTRESS_EXPLAIN_HE,
                linkedMapOf("score" to d.score, "model_version" to rep.modelVersion))
        }
        if (d?.detected != true && sequenceFired(now)) {
            val rule = sequenceRule!!
            dets += Detection("distress_voice", "medium", seqConf, SEQUENCE_LABEL_HE,
                "${rule.k} קטעי קול דמויי צעקה בתוך שניות ספורות. צעקות שמחה ומשחק נשמעות דומה.",
                linkedMapOf("rule" to rule.version, "model_version" to rep.modelVersion))
        }
        val out = ArrayList<Decision>()
        val p = prefs()
        for (det in dets) {
            stats["detections"] = stats.getValue("detections") + 1
            val (kind, alert) = manager.decide(det)
            if (alert == null) continue                          // שקט אחרי תגובה / הגבלת קצב - לא נרשם שוב
            var show = true
            var why: String? = null
            if (!p.popups) { show = false; why = "popups_disabled" }
            else if (!HazardAlerts.severityAllowed(alert.severity, p)) { show = false; why = "below_min_severity" }
            else {
                val (ok, reason) = canShow()
                if (!ok) { show = false; why = reason }
            }
            if (kind == "new" || kind == "update") {
                runCatching { record(alert, show, why) }
                if (show) onAlert(kind, alert)
            }
            out += Decision(kind, alert, show, why)
        }
        return out
    }

    /** (מספר אירועים, האם מצוקה זוהתה) - כמו מפתח ה-max ב-Python (בתיקו - הראשון). */
    private fun key(r: HazardReport) = r.events.size * 2 + if (r.distress?.detected == true) 1 else 0

    private fun noteSoft(rep: HazardReport, ts: Double) {
        val rule = sequenceRule ?: return
        val d = rep.distress?.score
        val s = rep.categoryScores["scream"]
        val hit = (d != null && d >= rule.tLow) || (s != null && s >= rule.sLow)
        soft.add(Soft(ts, hit, rep.distress?.probability ?: 0.0))
        soft = ArrayList(soft.filter { ts - it.ts <= rule.spanS * 2 }.takeLast(6))
    }

    private fun sequenceFired(now: Double): Boolean {
        val rule = sequenceRule ?: return false
        val hits = soft.filter { it.hit && now - it.ts <= rule.spanS * 2 }
        if (hits.size < rule.k) return false
        seqConf = hits.map { it.prob }.sorted()[hits.size / 2]
        soft = ArrayList()                                       // אותו רצף לא נספר פעמיים
        return true
    }

    companion object {
        const val MIN_SECONDS = 0.5
        const val MAX_SECONDS = 6.0
        const val WINDOW_S = 1.5
        const val DISTRESS_LABEL_HE = "קול מצוקה (צרחה / צעקה / בכי)"
        const val DISTRESS_EXPLAIN_HE = "צליל שדומה לצרחה, צעקה או בכי. צעקות שמחה ומשחק נשמעות דומה."
        const val SEQUENCE_LABEL_HE = "צעקות חוזרות"

        /** לכל קטגוריה - האירוע עם ה-margin הגבוה ביותר מכל החלונות (סדר ההופעה הראשונה נשמר). */
        fun mergeEvents(reps: List<HazardReport>): List<HazardEvent> {
            val best = LinkedHashMap<String, HazardEvent>()
            for (r in reps) for (e in r.events) {
                val cur = best[e.category]
                if (cur == null || (e.margin ?: 0.0) > (cur.margin ?: 0.0)) best[e.category] = e
            }
            return best.values.toList()
        }

        fun explain(e: HazardEvent): String {
            val base = when (e.category) {
                "gunshot" -> "צליל שדומה לירי"; "explosion" -> "צליל שדומה לפיצוץ"
                "glass_break" -> "צליל של שבירת זכוכית"; "alarm" -> "צליל של אזעקה"
                "scream" -> "צליל שדומה לצרחה"; "impact" -> "חבטה חזקה"
                "fireworks" -> "צליל שדומה לזיקוקים"; "siren" -> "סירנה"
                else -> e.labelHe ?: ""
            }
            val note = when (e.category) {
                "gunshot" -> "זיקוקים ותופים יכולים להישמע דומה"; "explosion" -> "זיקוקים ורעם יכולים להישמע דומה"
                "alarm" -> "צופר רכב לפעמים מזוהה כאזעקה"; "scream" -> "צעקות שמחה ומשחק נשמעות דומה"
                else -> null
            }
            val near = if (e.nearThreshold) " (קרוב לסף - לא ודאי)" else ""
            return base + near + (note?.let { ". $it" } ?: "")
        }
    }
}
