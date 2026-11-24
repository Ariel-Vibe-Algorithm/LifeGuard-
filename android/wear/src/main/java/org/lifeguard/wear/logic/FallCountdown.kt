package org.lifeguard.wear.logic

import org.lifeguard.app.core.MotionSignal
import org.lifeguard.app.core.WellnessMonitor
import org.lifeguard.app.wearlink.WearProtocol

/**
 * "הכל בסדר?" אחרי נפילה בשעון (בלי Android, זמנים במילישניות). אותם כללים כמו WellnessMonitor בטלפון:
 * - נפילה -> שאלה עם ספירה לאחור (30 שנ'). בזמן שאלה פתוחה נפילה נוספת רק מצטרפת לאותות (לא שאלה חדשה).
 * - "אני בסדר" -> שקט COOLDOWN (5 דק', כמו בטלפון) - לא שואלים שוב על אותו אירוע.
 * - "צריך/ה עזרה" -> Escalation(help_requested). אין תשובה עד הדד-ליין -> Escalation(no_answer).
 * - Escalation נושא את ביטחון הגלאי (האות fall החזק ביותר) ואת האותות - הטלפון מחשב מהם risk ו-trigger אוטומטי.
 */
class FallCountdown(
    val answerMs: Long = 30_000,
    val cooldownMs: Long = (WellnessMonitor.COOLDOWN_SECONDS * 1000).toLong(),
) {
    data class Asking(val startedAt: Long, val deadline: Long, val signals: List<MotionSignal>) {
        val fall: MotionSignal get() = signals.filter { it.source == "fall" }.maxByOrNull { it.confidence } ?: signals.first()
    }

    data class Escalation(val why: String, val confidence: Double, val signals: List<MotionSignal>)

    var asking: Asking? = null
        private set
    private var quietUntil = Long.MIN_VALUE

    /** fall + אותות קשורים מ-MotionFusion. true = נפתחה שאלה חדשה (להתחיל רטט ולהציג מסך) */
    fun onFall(t: Long, fall: MotionSignal, related: List<MotionSignal> = emptyList()): Boolean {
        val a = asking
        if (a != null) {
            asking = a.copy(signals = (a.signals + fall + related).distinct())
            return false
        }
        if (t < quietUntil) return false
        asking = Asking(t, t + answerMs, (listOf(fall) + related).distinct())
        return true
    }

    fun secondsLeft(t: Long): Int {
        val a = asking ?: return 0
        val left = a.deadline - t
        return if (left <= 0) 0 else ((left + 999) / 1000).toInt()
    }

    /** "אני בסדר" - true אם הייתה שאלה פתוחה */
    fun answerOk(t: Long): Boolean {
        asking ?: return false
        asking = null
        quietUntil = t + cooldownMs
        return true
    }

    /** "צריך/ה עזרה" */
    fun answerHelp(t: Long): Escalation? = escalate(t, WearProtocol.WHY_HELP_REQUESTED)

    /** לקרוא כל שנייה: תם הזמן בלי תשובה -> Escalation(no_answer) פעם אחת */
    fun tick(t: Long): Escalation? {
        val a = asking ?: return null
        return if (t >= a.deadline) escalate(t, WearProtocol.WHY_NO_ANSWER) else null
    }

    private fun escalate(t: Long, why: String): Escalation? {
        val a = asking ?: return null
        asking = null
        quietUntil = t + cooldownMs     // הקריאה כבר יוצאת; לא לשאול שוב מיד
        return Escalation(why, a.fall.confidence.coerceIn(0.0, 1.0), a.signals)
    }
}
