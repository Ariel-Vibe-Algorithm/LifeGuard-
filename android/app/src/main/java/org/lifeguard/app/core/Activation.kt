package org.lifeguard.app.core

/**
 * הפעלת חירום לפי מקור וביטחון (חוזה השרת, server/privacy.py):
 * - trigger "manual" (ברירת מחדל): SOS ידני - נפתח מיד ובמלואו. **אין שום שלב הערכה/AI לפני השליחה**: ה-SOS
 *   נשמר ב-outbox ונשלח מיד; הביטחון לא נבדק בכלל (לא במכשיר ולא בשרת).
 * - triggers אוטומטיים (wellness_no_response / sensor / acoustic / auto) + risk.confidence:
 *   < 0.4 -> השרת מחזיר 422 low_confidence (אין אירוע; לא שולחים שוב כידני),
 *   0.4..0.7 -> activation "unconfirmed" (אנשי קשר מקבלים התראה, בלי מתנדבים, הבעלים מתבקש/ת לאשר),
 *   >= 0.7 -> "confirmed_high".
 */
object Triggers {
    const val MANUAL = "manual"
    const val WELLNESS_NO_RESPONSE = "wellness_no_response"
    const val SENSOR = "sensor"
    const val ACOUSTIC = "acoustic"
    const val AUTO = "auto"
    val AUTOMATIC = setOf(WELLNESS_NO_RESPONSE, SENSOR, ACOUSTIC, AUTO)

    fun isAutomatic(trigger: String?) = trigger in AUTOMATIC
}

object Activations {
    const val MANUAL = "manual"
    const val UNCONFIRMED = "unconfirmed"
    const val CONFIRMED_HIGH = "confirmed_high"
    const val CONFIRMED_BY_USER = "confirmed_by_user"
    const val SERVER_TIMER = "server_timer"
    val CONFIRMED = setOf(MANUAL, CONFIRMED_HIGH, CONFIRMED_BY_USER, SERVER_TIMER)

    /** שרת ישן בלי השדה (null) = ההתנהגות הקודמת: כל אירוע מלא. */
    fun isConfirmed(activation: String?) = activation == null || activation in CONFIRMED
    fun needsOwnerConfirmation(activation: String?) = activation == UNCONFIRMED
}

object ActivationPolicy {
    const val REJECT_BELOW = 0.4
    const val HIGH_FROM = 0.7

    /** אות "אין תגובה לשאלת הכל בסדר?" - נכנס להערכת הביטחון של SOS אוטומטי. */
    const val NO_RESPONSE_SOURCE = "no_response"
    const val NO_RESPONSE_CONFIDENCE = 0.5
    const val NO_RESPONSE_REASON = "לא נענתה השאלה \"הכל בסדר?\""

    /**
     * trigger ל-SOS שנפתח מ"הכל בסדר?":
     * no_answer -> wellness_no_response; help_requested (לחיצה על "צריך/ה עזרה") -> sensor (הזיהוי מהחיישנים + אישור אנושי,
     * RiskScorer מעלה את הביטחון ל-≥0.95 -> confirmed_high).
     */
    fun triggerForWellness(why: String): String =
        if (why == "no_answer") Triggers.WELLNESS_NO_RESPONSE else Triggers.SENSOR

    /** הערכת הסיכון ל-SOS מ"הכל בסדר?": האותות + (באין תשובה) האות no_response. */
    fun wellnessRisk(signals: List<MotionSignal>, why: String, nowMs: Long): RiskAssessment {
        val all = if (why == "no_answer")
            signals + MotionSignal(NO_RESPONSE_SOURCE, NO_RESPONSE_CONFIDENCE, nowMs, NO_RESPONSE_REASON)
        else signals
        return RiskScorer.assess(all, why)
    }

    /**
     * מה השרת צפוי להחליט (אותם ספים). null = ייחסם (low_confidence).
     * משמש רק במכשיר כשאין קשר לשרת (למשל להחליט אם לשלוח SMS ישיר / להקליט) - השרת הוא מקור האמת.
     */
    fun expectedActivation(trigger: String?, confidence: Double?): String? {
        if (!Triggers.isAutomatic(trigger)) return Activations.MANUAL
        if (confidence == null || confidence.isNaN() || confidence < REJECT_BELOW) return null
        return if (confidence >= HIGH_FROM) Activations.CONFIRMED_HIGH else Activations.UNCONFIRMED
    }

    sealed class CreateFailure {
        /** 422 low_confidence: אין אירוע. רושמים מקומית + אירוע ביקורת; לא מנסים שוב (גם לא כידני). */
        object LowConfidence : CreateFailure()
        /** רשת / 5xx / 429 / 408 / 401 (התחברות מחדש) - ממשיכים לנסות עם אותו client_id */
        object Retry : CreateFailure()
        /** בקשה שגויה אחרת (4xx) - ממשיכים לנסות (SOS לא נזרק לעולם), אבל ה-SMS הישיר ממשיך כרגיל */
        data class Rejected(val status: Int, val code: String) : CreateFailure()
    }

    /** status=null -> שגיאת רשת. */
    fun classifyCreateError(status: Int?, code: String?): CreateFailure = when {
        status == null -> CreateFailure.Retry
        status == 422 && code == "low_confidence" -> CreateFailure.LowConfidence
        status >= 500 || status == 429 || status == 408 || status == 401 -> CreateFailure.Retry
        else -> CreateFailure.Rejected(status, code ?: "http_$status")
    }

    /**
     * האם להקליט עכשיו (בכפוף גם להרשאת RECORD_AUDIO ולמדיניות השרת - ראו RecordingGate):
     * רק בזמן SOS ידני או חירום מאושר. אירוע שעוד לא הגיע לשרת: ידני = מאושר; אוטומטי - לפי הספים.
     */
    fun emergencyConfirmedForRecording(onServer: Boolean, activation: String?, trigger: String?, confidence: Double?): Boolean =
        if (onServer) Activations.isConfirmed(activation)
        else expectedActivation(trigger, confidence) in Activations.CONFIRMED
}
