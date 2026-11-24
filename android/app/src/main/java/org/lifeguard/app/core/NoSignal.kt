package org.lifeguard.app.core

import org.json.JSONObject
import org.lifeguard.app.api.GeoPoint
import org.lifeguard.app.api.dbl
import org.lifeguard.app.api.obj
import org.lifeguard.app.api.str
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.TimeZone
import kotlin.math.min
import kotlin.math.pow

enum class SmsState { NONE, SENDING, SENT, FAILED }

/**
 * SOS שעוד לא אושר בשרת ("outbox"). נשמר מוצפן במכשיר ונשלח שוב עם אותו client_id עד הצלחה
 * (השרת מזהה כפילות לפי client_id - לא ייפתח אירוע כפול).
 */
data class PendingSos(
    val clientId: String,
    val mode: String,
    val createdAt: Double,
    val location: GeoPoint?,
    val source: String = "button",
    val escortId: String? = null,
    val attempts: Int = 0,
    val lastAttemptAt: Double = 0.0,
    val smsState: SmsState = SmsState.NONE,
    val smsAttempts: Int = 0,
    val lastSmsAt: Double = 0.0,
    /** הערכת סיכון (JSON של RiskAssessment.toJson) - רק ל-SOS מ"הכל בסדר?"/חיישנים. SOS ידני: null. */
    val riskJson: String? = null,
    /** manual (כפתור / נסיעה) או טריגר אוטומטי (wellness_no_response / sensor ...) - ראו ActivationPolicy */
    val trigger: String = Triggers.MANUAL,
) {
    val isAutomatic get() = Triggers.isAutomatic(trigger)
    val confidence: Double? get() = risk()?.let { if (it.has("confidence")) it.optDouble("confidence") else null }

    fun toJson(): JSONObject = JSONObject().put("client_id", clientId).put("mode", mode).put("created_at", createdAt)
        .put("source", source).put("attempts", attempts).put("last_attempt_at", lastAttemptAt)
        .put("sms_state", smsState.name).put("sms_attempts", smsAttempts).put("last_sms_at", lastSmsAt)
        .apply {
            location?.let { put("location", it.toJson()) }; escortId?.let { put("escort_id", it) }
            riskJson?.let { put("risk_json", it) }
            put("trigger", trigger)
        }

    fun risk(): JSONObject? = riskJson?.let { runCatching { JSONObject(it) }.getOrNull() }

    companion object {
        fun parse(o: JSONObject) = PendingSos(
            clientId = o.str("client_id") ?: "",
            mode = o.str("mode") ?: "loud",
            createdAt = o.dbl("created_at") ?: 0.0,
            location = GeoPoint.parse(o.obj("location")),
            source = o.str("source") ?: "button",
            escortId = o.str("escort_id"),
            attempts = o.dbl("attempts")?.toInt() ?: 0,
            lastAttemptAt = o.dbl("last_attempt_at") ?: 0.0,
            smsState = runCatching { SmsState.valueOf(o.str("sms_state") ?: "NONE") }.getOrDefault(SmsState.NONE),
            smsAttempts = o.dbl("sms_attempts")?.toInt() ?: 0,
            lastSmsAt = o.dbl("last_sms_at") ?: 0.0,
            riskJson = o.str("risk_json"),
            trigger = o.str("trigger") ?: Triggers.MANUAL,
        )
    }
}

/** מתי לנסות שוב לשלוח SOS לשרת, ומתי לעבור ל-SMS ישיר מהטלפון. */
object NoSignalPolicy {
    /** SOS שלא הגיע לשרת תוך זמן זה -> SMS ישיר לאנשי הקשר */
    const val SMS_AFTER_SECONDS = 20.0
    /** או אחרי כמה כישלונות רשת רצופים */
    const val SMS_AFTER_FAILURES = 3
    const val SMS_RETRY_SECONDS = 60.0
    const val SMS_MAX_ATTEMPTS = 5
    const val MAX_RETRY_DELAY_SECONDS = 15.0

    /** 1, 2, 4, 8, 15, 15... שניות */
    fun retryDelaySeconds(attempts: Int): Double =
        if (attempts <= 0) 0.0 else min(MAX_RETRY_DELAY_SECONDS, 2.0.pow(attempts - 1))

    fun shouldAttemptCreate(p: PendingSos, now: Double): Boolean = now - p.lastAttemptAt >= retryDelaySeconds(p.attempts)

    fun shouldSendSms(p: PendingSos, now: Double, networkAvailable: Boolean, hasNumbers: Boolean, hasPermission: Boolean): Boolean {
        if (!hasNumbers || !hasPermission) return false
        // SOS אוטומטי שהשרת היה דוחה (ביטחון < 0.4) לא נשלח גם ב-SMS ישיר. SOS ידני - תמיד (בלי שום בדיקת ביטחון).
        if (p.isAutomatic && ActivationPolicy.expectedActivation(p.trigger, p.confidence) == null) return false
        return when (p.smsState) {
            SmsState.SENT, SmsState.SENDING -> false
            SmsState.FAILED -> p.smsAttempts < SMS_MAX_ATTEMPTS && now - p.lastSmsAt >= SMS_RETRY_SECONDS
            SmsState.NONE -> !networkAvailable || p.attempts >= SMS_AFTER_FAILURES || now - p.createdAt >= SMS_AFTER_SECONDS
        }
    }
}

/** האם יש קשר עם השרת בפועל (לא רק "יש רשת"): לפי תוצאת הבקשה האחרונה. */
class ConnectivityTracker(private val staleSeconds: Double = 90.0) {
    @Volatile private var lastSuccess: Double? = null
    @Volatile private var lastFailure: Double? = null

    fun onSuccess(now: Double) { lastSuccess = now }
    fun onFailure(now: Double) { lastFailure = now }

    fun isOnline(now: Double): Boolean {
        val s = lastSuccess ?: return false
        val f = lastFailure
        return (f == null || s >= f) && now - s <= staleSeconds
    }
}

/** טקסט ה-SMS הישיר. באנגלית בכוונה: מגיע לכל טלפון, ופחות תווים (Hebrew = UCS-2, 70 תווים להודעה). */
object SmsComposer {
    fun help(name: String, loc: GeoPoint?, timeMillis: Long, tz: TimeZone = TimeZone.getDefault()): String {
        val time = hhmm(timeMillis, tz)
        val where = if (loc != null) "Location: https://maps.google.com/?q=%.6f,%.6f".format(Locale.US, loc.lat, loc.lon)
        else "Location unknown"
        return "LifeGuard: ${clean(name)} needs help. $where ($time)"
    }

    fun safe(name: String, timeMillis: Long, tz: TimeZone = TimeZone.getDefault()): String =
        "LifeGuard: ${clean(name)} is safe now (${hhmm(timeMillis, tz)})"

    private fun clean(name: String) = name.replace(Regex("[\\r\\n]+"), " ").trim().take(40).ifEmpty { "A LifeGuard user" }

    private fun hhmm(t: Long, tz: TimeZone) = SimpleDateFormat("HH:mm", Locale.US).apply { timeZone = tz }.format(Date(t))
}
