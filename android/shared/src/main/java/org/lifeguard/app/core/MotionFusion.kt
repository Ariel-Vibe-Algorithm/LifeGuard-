package org.lifeguard.app.core

import kotlin.math.acos
import kotlin.math.max
import kotlin.math.min
import kotlin.math.roundToInt
import kotlin.math.sqrt

/**
 * מיזוג חיישנים: חיישן תאוצה + ג'ירוסקופ (רשות) + עצירה פתאומית מ-GPS -> אותות, והחלטה מתי לשאול "הכל בסדר?".
 *
 * אותות (MotionSignal.source):
 * - fall: נפילה חופשית -> מכה -> (שיא סיבוב בג'ירוסקופ או שינוי תנוחה ≥60°) -> שקט אחרי המכה. מבוסס על FallDetector.
 * - impact: מכה חזקה (≥3g) שאינה חלק מתנועה מחזורית (ריצה) ואינה טלפון שנפל לבד.
 * - struggle: תנועה חריגה/אלימה מתמשכת - שונות תאוצה גבוהה וגם קצב סיבוב גבוה, לא מחזורית (≠ ריצה), כמה שניות.
 * - sudden_stop: מהירות ≥4 מ'/ש' ואז עמידה 20 שנ' (אותה לוגיקה כמו WellnessMonitor.detectAbruptStop).
 * - no_signal: אין קליטה בזמן ליווי (מוזן מבחוץ; רק להערכת הסיכון).
 *
 * כלל: אות בודד אף פעם לא שולח SOS ואף לא שואל לבד. שאלה דורשת ≥2 סוגי אותות שונים תוך חלון (90 שנ').
 * SOS קורה רק דרך WellnessMonitor: אין תשובה לשאלה או "צריך/ה עזרה".
 *
 * "טלפון שנפל/נזרק": נפילה חופשית *עמוקה* (<0.15g ≥200 מ"ש) = הטלפון עצמו באוויר, לא אדם שנופל איתו בכיס
 * (בנפילת אדם הטלפון בכיס יורד בדרך כלל ל-0.3-0.6g). מגבלה ידועה: נפילה מגובה עם הטלפון ביד עלולה להיות מסווגת כך.
 *
 * בלי threads ובלי Android: זמנים ב-epoch מילישניות; תאוצה ב-m/s², ג'ירוסקופ ב-rad/s (כמו SensorEvent).
 */
class MotionFusion(
    @Volatile var gyroAvailable: Boolean = true,
    private val cfg: Config = Config(),
) {
    data class Config(
        val windowMs: Long = 90_000,
        val minSignalsToPrompt: Int = 2,
        val hardImpactG: Double = 3.0,
        val impactEvalDelayMs: Long = 2_500,
        val rotationPeakRad: Double = 3.0,           // ~170°/שנ'
        val orientationChangeDeg: Double = 60.0,
        val deepFreeFallG: Double = 0.15,
        val deepFreeFallMs: Long = 200,
        val periodicR: Double = 0.5,
        val struggleWindowMs: Long = 3_000,
        val struggleSustainMs: Long = 3_000,
        val struggleAccelStdG: Double = 0.6,
        val struggleAccelStdNoGyroG: Double = 0.75,
        val struggleGyroRad: Double = 2.5,
        val struggleRepeatMs: Long = 30_000,
        val bufferMs: Long = 8_000,
    )

    /** תוצאה כששווה לשאול "הכל בסדר?" */
    data class Prompt(val assessment: RiskAssessment)

    private class S(val t: Long, val x: Double, val y: Double, val z: Double) {
        val mag = sqrt(x * x + y * y + z * z)
    }

    private val fall = FallDetector()
    private val accel = ArrayDeque<S>()
    private val gyro = ArrayDeque<S>()
    private val signals = ArrayList<MotionSignal>()
    private val pendingImpacts = ArrayList<Pair<Long, Double>>()   // (זמן, שיא g)
    private var lastStruggleEval = 0L
    private var struggleSince: Long? = null
    private var lastStruggleSignal = Long.MIN_VALUE / 2
    private var fixes = ArrayList<WellnessMonitor.Fix>()

    /** כל אות חדש (גם אם לא הוביל לשאלה) - לתיעוד. */
    @Volatile var onSignal: ((MotionSignal) -> Unit)? = null

    /** דגימות שנדחו (טלפון שנפל, ריצה...) - לניפוי באגים / בדיקות */
    val rejected = ArrayList<String>()

    // ------------------------------------------------------------ קלט
    @Synchronized
    fun onGyro(tMs: Long, x: Float, y: Float, z: Float) {
        gyro.addLast(S(tMs, x.toDouble(), y.toDouble(), z.toDouble()))
        trim(gyro, tMs)
    }

    @Synchronized
    fun onAccel(tMs: Long, x: Float, y: Float, z: Float): Prompt? {
        val s = S(tMs, x / G, y / G, z / G)
        accel.addLast(s)
        trim(accel, tMs)
        var prompt: Prompt? = null

        // מכה חזקה: מועמדת, נבדקת אחרי 2.5 שנ' (צריך לראות מה קרה אחריה)
        if (s.mag >= cfg.hardImpactG) {
            val last = pendingImpacts.lastOrNull()
            if (last != null && tMs - last.first <= 1000) {
                if (s.mag > last.second) pendingImpacts[pendingImpacts.size - 1] = last.first to s.mag   // קפיצה חוזרת
            } else pendingImpacts += tMs to s.mag
        }
        val due = pendingImpacts.filter { tMs - it.first >= cfg.impactEvalDelayMs }
        if (due.isNotEmpty()) {
            pendingImpacts.removeAll(due.toSet())
            for ((at, peak) in due) evaluateImpact(at, peak)?.let { prompt = add(it) ?: prompt }
        }

        fall.onSample(tMs, x, y, z)?.let { d ->
            if (d.signal == "fall") evaluateFall(d)?.let { prompt = add(it) ?: prompt }
        }

        if (tMs - lastStruggleEval >= 500) {
            lastStruggleEval = tMs
            evaluateStruggle(tMs)?.let { prompt = add(it) ?: prompt }
        }
        return prompt
    }

    /** מיקום GPS (שניות epoch). עצירה פתאומית -> אות sudden_stop. */
    @Synchronized
    fun onLocation(tSec: Double, lat: Double, lon: Double): Prompt? {
        fixes = ArrayList(fixes.filter { tSec - it.t <= 120 } + WellnessMonitor.Fix(tSec, lat, lon))
        if (!WellnessMonitor.detectAbruptStop(fixes)) return null
        fixes = arrayListOf(fixes.last())
        return add(MotionSignal("sudden_stop", 0.7, (tSec * 1000).toLong(),
            "עצירה פתאומית: נסיעה/ריצה מהירה ואז עמידה מלאה 20 שנ'"))
    }

    /** אות חיצוני (no_signal מ-WellnessMonitor וכו'). */
    @Synchronized
    fun addExternal(signal: MotionSignal): Prompt? = add(signal)

    /** האותות בחלון הזמן (להערכת סיכון כשה-SOS נשלח). */
    @Synchronized
    fun recentSignals(nowMs: Long): List<MotionSignal> = signals.filter { nowMs - it.atMs <= cfg.windowMs }

    @Synchronized
    fun reset() {
        fall.reset(); accel.clear(); gyro.clear(); signals.clear(); pendingImpacts.clear(); struggleSince = null
        fixes.clear()
    }

    // ------------------------------------------------------------ החלטה
    private fun add(sig: MotionSignal): Prompt? {
        signals += sig
        signals.removeAll { sig.atMs - it.atMs > cfg.windowMs }
        onSignal?.invoke(sig)
        val kinds = signals.map { it.source }.toSet()
        if (kinds.size < cfg.minSignalsToPrompt) return null
        val a = RiskScorer.assess(signals.toList(), extraReasons = gyroNote(kinds))
        signals.clear()     // אותה קבוצת אותות לא שואלת פעמיים
        return Prompt(a)
    }

    private fun gyroNote(kinds: Set<String>): List<String> =
        if (!gyroAvailable && (kinds.contains("fall") || kinds.contains("struggle")))
            listOf("אין ג'ירוסקופ במכשיר - ביטחון נמוך יותר") else emptyList()

    // ------------------------------------------------------------ נפילה
    private fun evaluateFall(d: FallDetector.Detection): MotionSignal? {
        val impact = d.impactAtMs
        val deep = deepFreeFallMs(impact - 1500, impact)
        if (deep >= cfg.deepFreeFallMs) { rejected += "fall:device_drop"; return null }
        val orient = orientationChangeDeg(impact)
        val rot = if (gyroAvailable) rotationPeak(impact) else null
        val orientOk = orient != null && orient >= cfg.orientationChangeDeg
        val rotOk = rot != null && rot >= cfg.rotationPeakRad
        if (!orientOk && !rotOk) { rejected += "fall:no_rotation_or_posture_change"; return null }
        var c = 0.55
        if (orientOk) c += 0.15
        if (rotOk) c += 0.15
        if (d.peakG > 3.5) c += 0.05
        if (d.stillRatio >= 0.95) c += 0.05
        if (!gyroAvailable) c *= 0.8
        val parts = arrayListOf("נפילה: נפילה חופשית ${d.freeFallMs} מ\"ש", "מכה %.1fg".format(java.util.Locale.US, d.peakG))
        orient?.let { parts += "שינוי תנוחה ${it.roundToInt()}°" }
        rot?.let { parts += "סיבוב ${Math.toDegrees(it).roundToInt()}°/שנ'" }
        parts += "שקט אחרי המכה"
        return MotionSignal("fall", c.coerceAtMost(0.95), impact, parts.joinToString(", "))
    }

    // ------------------------------------------------------------ מכה
    private fun evaluateImpact(at: Long, peak: Double): MotionSignal? {
        if (deepFreeFallMs(at - 1500, at) >= cfg.deepFreeFallMs) { rejected += "impact:device_drop"; return null }
        val after = window(accel, at + 500, at + cfg.impactEvalDelayMs)
        val stillAfter = after.isNotEmpty() && after.count { kotlin.math.abs(it.mag - 1.0) <= 0.25 } >= 0.8 * after.size
        val around = window(accel, at - 3000, at + cfg.impactEvalDelayMs)
        if (!stillAfter && periodicity(around) >= cfg.periodicR) { rejected += "impact:rhythmic"; return null }
        val c = (0.5 + (peak - cfg.hardImpactG) / 6.0).coerceIn(0.5, 0.9)
        return MotionSignal("impact", c, at, "מכה חזקה %.1fg".format(java.util.Locale.US, peak))
    }

    // ------------------------------------------------------------ מאבק
    private fun evaluateStruggle(now: Long): MotionSignal? {
        val w = window(accel, now - cfg.struggleWindowMs, now)
        if (w.size < 30 || w.last().t - w.first().t < cfg.struggleWindowMs * 0.8) { struggleSince = null; return null }
        val mean = w.sumOf { it.mag } / w.size
        val std = sqrt(w.sumOf { (it.mag - mean) * (it.mag - mean) } / w.size)
        val per = periodicity(w)
        val gw = if (gyroAvailable) window(gyro, now - cfg.struggleWindowMs, now) else emptyList()
        val gyroMean = if (gw.isEmpty()) null else gw.sumOf { it.mag } / gw.size
        val active = per < cfg.periodicR && if (gyroMean != null) std >= cfg.struggleAccelStdG && gyroMean >= cfg.struggleGyroRad
        else std >= cfg.struggleAccelStdNoGyroG
        if (!active) { struggleSince = null; return null }
        val since = struggleSince ?: now.also { struggleSince = it }
        if (now - since < cfg.struggleSustainMs || now - lastStruggleSignal < cfg.struggleRepeatMs) return null
        lastStruggleSignal = now
        val seconds = ((now - since + cfg.struggleWindowMs) / 1000.0).roundToInt()
        return if (gyroMean != null) MotionSignal("struggle", 0.75, now,
            "תנועה אלימה לא סדירה ~$seconds שנ' (שונות תאוצה %.1fg, סיבוב %.0f°/שנ')".format(java.util.Locale.US, std,
                Math.toDegrees(gyroMean)))
        else MotionSignal("struggle", 0.45, now,
            "תנועה אלימה לא סדירה ~$seconds שנ' (שונות תאוצה %.1fg; בלי ג'ירוסקופ)".format(java.util.Locale.US, std))
    }

    // ------------------------------------------------------------ מאפיינים
    private fun window(q: ArrayDeque<S>, from: Long, to: Long): List<S> = q.filter { it.t in from..to }

    private fun trim(q: ArrayDeque<S>, now: Long) {
        while (q.isNotEmpty() && now - q.first().t > cfg.bufferMs) q.removeFirst()
    }

    /** הרצף הארוך ביותר (מ"ש) של |a| < 0.15g בטווח. */
    private fun deepFreeFallMs(from: Long, to: Long): Long {
        var best = 0L; var start: Long? = null
        for (s in window(accel, from, to)) {
            if (s.mag < cfg.deepFreeFallG) { if (start == null) start = s.t; best = max(best, s.t - start) }
            else if (start != null) { best = max(best, s.t - start); start = null }
        }
        return best
    }

    /** הזווית בין כיוון הכבידה לפני הנפילה (2.5-1 שנ' לפני המכה) לבין אחריה (1-2.5 שנ' אחרי). */
    private fun orientationChangeDeg(impact: Long): Double? {
        val before = meanVec(window(accel, impact - 2500, impact - 1000)) ?: return null
        val after = meanVec(window(accel, impact + 1000, impact + 2500)) ?: return null
        val nb = norm(before); val na = norm(after)
        if (nb < 0.5 || na < 0.5) return null      // לא מנוחה - כיוון הכבידה לא מוגדר
        val cos = ((before[0] * after[0] + before[1] * after[1] + before[2] * after[2]) / (nb * na)).coerceIn(-1.0, 1.0)
        return Math.toDegrees(acos(cos))
    }

    private fun rotationPeak(impact: Long): Double? {
        val w = window(gyro, impact - 1500, impact + 500)
        return if (w.isEmpty()) null else w.maxOf { it.mag }
    }

    private fun meanVec(w: List<S>): DoubleArray? {
        if (w.size < 5) return null
        return doubleArrayOf(w.sumOf { it.x } / w.size, w.sumOf { it.y } / w.size, w.sumOf { it.z } / w.size)
    }

    private fun norm(v: DoubleArray) = sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])

    /** מחזוריות (ריצה/הליכה): שיא האוטו-קורלציה המנורמלת של |a| בהשהיות 0.25-1.2 שנ'. 0 = אקראי, ~1 = מחזורי. */
    private fun periodicity(w: List<S>): Double {
        val n = w.size
        if (n < 20) return 0.0
        val dt = (w.last().t - w.first().t).toDouble() / (n - 1)
        if (dt <= 0) return 0.0
        val mean = w.sumOf { it.mag } / n
        val d = DoubleArray(n) { w[it].mag - mean }
        val e = d.sumOf { it * it }
        if (e < 1e-6) return 0.0
        val lo = max(1, (250 / dt).roundToInt())
        val hi = min(n / 2, (1200 / dt).roundToInt())
        var best = 0.0
        for (lag in lo..hi) {
            var s = 0.0
            for (i in 0 until n - lag) s += d[i] * d[i + lag]
            best = max(best, s / e * n / (n - lag))
        }
        return best
    }

    companion object { const val G = FallDetector.GRAVITY }
}
