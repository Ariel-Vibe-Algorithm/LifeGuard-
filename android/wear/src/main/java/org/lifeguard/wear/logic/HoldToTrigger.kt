package org.lifeguard.wear.logic

/**
 * "לחץ והחזק" (בלי Android, זמנים במילישניות): מופעל רק אחרי החזקה רציפה של holdMs (2 שנ') - מונע הפעלה בטעות
 * (שרוול, מגע כף יד). בזמן ההחזקה: אירועי Haptic בכל רבע (רטט קצר = "ממשיך להתמלא"), ובסוף Triggered פעם אחת.
 * שחרור לפני הזמן -> Released (לא קרה כלום). אחרי Triggered הכפתור נעול עד reset() (המסך עובר לחלון הביטול).
 */
class HoldToTrigger(val holdMs: Long = 2_000, private val hapticSteps: Int = 4) {
    sealed class Event {
        /** step = 1..hapticSteps-1 (רבע, חצי, שלושה רבעים) */
        data class Haptic(val step: Int) : Event()
        object Triggered : Event()
        object Released : Event()
    }

    private var pressedAt: Long? = null
    private var stepsFired = 0
    var triggered = false
        private set

    val holding get() = pressedAt != null

    fun press(t: Long) {
        if (triggered || pressedAt != null) return
        pressedAt = t
        stepsFired = 0
    }

    /** 0..1 להצגת טבעת ההתקדמות */
    fun progress(t: Long): Float {
        if (triggered) return 1f
        val p = pressedAt ?: return 0f
        return ((t - p).toFloat() / holdMs).coerceIn(0f, 1f)
    }

    fun tick(t: Long): List<Event> {
        val p = pressedAt ?: return emptyList()
        val elapsed = t - p
        val out = ArrayList<Event>()
        while (stepsFired < hapticSteps - 1 && elapsed >= holdMs * (stepsFired + 1) / hapticSteps) {
            stepsFired++
            out += Event.Haptic(stepsFired)
        }
        if (elapsed >= holdMs) {
            pressedAt = null
            triggered = true
            out += Event.Triggered
        }
        return out
    }

    /** שחרור האצבע (או ביטול המגע ע"י המערכת) */
    fun release(t: Long): List<Event> {
        val out = ArrayList(tick(t))
        if (!triggered && pressedAt != null) {
            pressedAt = null
            out += Event.Released
        }
        return out
    }

    fun reset() {
        pressedAt = null
        triggered = false
        stepsFired = 0
    }
}
