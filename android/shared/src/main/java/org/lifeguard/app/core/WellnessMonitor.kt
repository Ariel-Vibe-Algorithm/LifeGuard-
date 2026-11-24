package org.lifeguard.app.core

import kotlin.math.asin
import kotlin.math.cos
import kotlin.math.sin
import kotlin.math.sqrt

/**
 * "הכל בסדר?" - פורט של wellness_check.py (אותם קבועים ואותה לוגיקה).
 * טריגרים: נפילה / מכה (חיישן תאוצה), עצירה פתאומית (רצף מיקומים), אין קליטה בזמן ליווי פעיל.
 * שאלה עם ספירה לאחור; "אני בסדר" -> שקט COOLDOWN; "צריך/ה עזרה" או אין תשובה -> SOS.
 * לא שואלים כשכבר יש אירוע פעיל (או מעקב שקט - שם אסור שיופיע משהו על המסך).
 *
 * בלי threads: הספירה לאחור נבדקת ב-tick() (השירות קורא לו כל שנייה) - כך גם קל לבדוק ב-JVM.
 */
class WellnessMonitor(
    private val clock: () -> Double,
    private val isBusy: () -> Boolean,
    private val listener: Listener,
    val checkinSeconds: Double = CHECKIN_SECONDS,
) {
    interface Listener {
        fun onAsk(reason: String, reasonText: String, deadline: Double)
        /** why: "help_requested" | "no_answer" */
        fun onEscalate(reason: String, why: String)
        fun onDismissed(reason: String) {}
    }

    /** text: טקסט הסבר מותאם (למשל "זוהו: נפילה + מכה חזקה"); null -> REASONS_HE */
    data class Pending(val reason: String, val deadline: Double, val text: String? = null)

    @Volatile var pending: Pending? = null
        private set
    @Volatile var quietUntil = 0.0
        private set
    @Volatile var offlineSince: Double? = null
        private set
    private var fixes = ArrayList<Fix>()

    // ------------------------------------------------------------ טריגרים
    fun onSensor(signal: String, confidence: Double): Boolean {
        val thr = SENSOR_TRIGGERS[signal] ?: return false
        return confidence >= thr && startCheckin(signal)
    }

    fun onLocation(t: Double, lat: Double, lon: Double): Boolean {
        val fire: Boolean
        synchronized(this) {
            fixes = ArrayList(fixes.filter { t - it.t <= 120 } + Fix(t, lat, lon))
            fire = detectAbruptStop(fixes)
            if (fire) fixes = arrayListOf(fixes.last())        // לא לשאול שוב על אותה עצירה
        }
        return fire && startCheckin("abrupt_stop")
    }

    /** watching: האם יש ליווי/טיימר בטיחות פעיל (רק אז היעדר קליטה מדאיג). */
    fun onConnectivity(online: Boolean, watching: Boolean): Boolean {
        val now = clock()
        val fire: Boolean
        synchronized(this) {
            if (online || !watching) { offlineSince = null; return false }
            val since = offlineSince
            if (since == null) { offlineSince = now; return false }
            fire = now - since >= SIGNAL_LOSS_SECONDS
            if (fire) offlineSince = now                         // שאלה אחת לכל חלון
        }
        return fire && startCheckin("signal_loss")
    }

    // ------------------------------------------------------------ שאלה
    fun startCheckin(reason: String, reasonText: String? = null): Boolean {
        val now = clock()
        val p: Pending
        synchronized(this) {
            if (pending != null || now < quietUntil || isBusy()) return false
            p = Pending(reason, now + checkinSeconds, reasonText)
            pending = p
        }
        listener.onAsk(reason, reasonText ?: REASONS_HE[reason] ?: reason, p.deadline)
        return true
    }

    fun answer(ok: Boolean) {
        val reason: String
        synchronized(this) {
            val p = pending ?: return
            reason = p.reason
            pending = null
            if (ok) quietUntil = clock() + COOLDOWN_SECONDS
        }
        if (ok) listener.onDismissed(reason) else listener.onEscalate(reason, "help_requested")
    }

    /** לקרוא כל שנייה: תם הזמן בלי תשובה -> SOS. */
    fun tick(): Boolean {
        val reason: String
        synchronized(this) {
            val p = pending ?: return false
            if (clock() < p.deadline) return false
            reason = p.reason
            pending = null
        }
        listener.onEscalate(reason, "no_answer")
        return true
    }

    data class Fix(val t: Double, val lat: Double, val lon: Double)

    companion object {
        const val CHECKIN_SECONDS = 45.0
        const val COOLDOWN_SECONDS = 5 * 60.0
        const val SIGNAL_LOSS_SECONDS = 5 * 60.0
        val SENSOR_TRIGGERS = mapOf("fall" to 0.7, "impact" to 0.8, "abrupt_stop" to 0.7)
        const val FAST_MPS = 4.0          // ~14 קמ"ש: ריצה או רכב
        const val STILL_MPS = 0.5
        const val STILL_SECONDS = 20.0
        val REASONS_HE = mapOf("fall" to "זוהתה נפילה", "impact" to "זוהתה מכה חזקה", "abrupt_stop" to "עצירה פתאומית",
            "signal_loss" to "אין קליטה כבר כמה דקות", "motion" to "זוהתה תנועה חריגה")

        private fun speed(a: Fix, b: Fix): Double {
            val dt = b.t - a.t
            if (dt <= 0) return 0.0
            val la1 = Math.toRadians(a.lat); val lo1 = Math.toRadians(a.lon)
            val la2 = Math.toRadians(b.lat); val lo2 = Math.toRadians(b.lon)
            val h = sin((la2 - la1) / 2).let { it * it } + cos(la1) * cos(la2) * sin((lo2 - lo1) / 2).let { it * it }
            return 2 * 6371000 * asin(sqrt(h)) / dt
        }

        /**
         * fixes ממוינים לפי זמן. true אם הייתה תנועה מהירה ואחריה עמידה של stillSeconds לפחות,
         * והמעבר קרה תוך window שניות (עצירה *פתאומית*, לא האטה הדרגתית לרמזור).
         */
        fun detectAbruptStop(
            fixes: List<Fix>, fast: Double = FAST_MPS, still: Double = STILL_MPS,
            stillSeconds: Double = STILL_SECONDS, window: Double = 60.0,
        ): Boolean {
            if (fixes.size < 3) return false
            val speeds = (0 until fixes.size - 1).map { fixes[it + 1].t to speed(fixes[it], fixes[it + 1]) }
            val lastFast = speeds.filter { it.second >= fast }.maxOfOrNull { it.first } ?: return false
            val after = speeds.filter { it.first > lastFast }
            if (after.isEmpty() || after.any { it.second > still }) return false
            val stillFrom = after[0].first
            return fixes.last().t - stillFrom >= stillSeconds && stillFrom - lastFast <= window
        }
    }
}
