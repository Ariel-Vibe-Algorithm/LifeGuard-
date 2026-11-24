package org.lifeguard.wear.logic

/**
 * חלון ביטול אחרי ההחזקה (ברירת מחדל 5 שנ'): "שולח בעוד 5... ביטול". רק כשהחלון נגמר בלי ביטול - שולחים לטלפון.
 * fired פעם אחת בלבד; ביטול אחרי השליחה לא אפשרי (מחזיר false - הממשק לא מעמיד פנים שביטל).
 */
class CancelWindow(val windowMs: Long = 5_000) {
    enum class State { IDLE, COUNTING, CANCELLED, FIRED }

    var state = State.IDLE
        private set
    private var startedAt = 0L
    private var lastSecondShown = -1

    fun start(t: Long) {
        if (state == State.COUNTING) return
        state = State.COUNTING
        startedAt = t
        lastSecondShown = secondsLeft(t)
    }

    fun cancel(): Boolean {
        if (state != State.COUNTING) return false
        state = State.CANCELLED
        return true
    }

    /** שניות שנותרו (עיגול למעלה) - 5,4,3,2,1 */
    fun secondsLeft(t: Long): Int {
        if (state != State.COUNTING) return 0
        val left = windowMs - (t - startedAt)
        return if (left <= 0) 0 else ((left + 999) / 1000).toInt()
    }

    /** true אם בדיוק עכשיו עבר שנייה שלמה (לרטט "תקתוק") */
    fun secondChanged(t: Long): Boolean {
        val s = secondsLeft(t)
        if (state != State.COUNTING || s == lastSecondShown) return false
        lastSecondShown = s
        return true
    }

    /** true פעם אחת - כשהחלון נגמר בלי ביטול (= לשלוח עכשיו) */
    fun tick(t: Long): Boolean {
        if (state == State.COUNTING && t - startedAt >= windowMs) {
            state = State.FIRED
            return true
        }
        return false
    }

    fun reset() {
        state = State.IDLE
        lastSecondShown = -1
    }
}
