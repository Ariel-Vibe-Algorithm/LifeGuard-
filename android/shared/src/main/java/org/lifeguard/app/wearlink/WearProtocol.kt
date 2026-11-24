package org.lifeguard.app.wearlink

import org.json.JSONArray
import org.json.JSONObject
import org.lifeguard.app.core.MotionSignal

/**
 * פרוטוקול Wearable Data Layer (MessageClient) בין השעון (:wear) לטלפון (:app). JSON ב-UTF-8, גרסה v.
 *
 * שעון -> טלפון  [PATH_SOS]        SosRequest: SOS ידני (kind=manual) או נפילה בלי תשובה / "צריך/ה עזרה" (kind=watch_fall).
 * טלפון -> שעון  [PATH_SOS_ACK]    SosAck: תשובה ישירה - האם ה-SOS נשמר ב-outbox של הטלפון (queued) או למה לא.
 * טלפון -> שעון  [PATH_SOS_STATUS] SosStatus: מצב המסירה *האמיתי* מהטלפון (התקבל בשרת / מנסה שוב / SMS / נדחה).
 *
 * השעון לא מציג "נשלח לטלפון" לפני SosAck, ולא מציג "התקבל בשרת" לפני SosStatus(delivered).
 * השעון לא פונה לשרת בעצמו (אין לו חשבון/טוקן) - כל הלוגיקה של האירוע (outbox, SMS, ספי הפעלה) בטלפון.
 */
object WearProtocol {
    const val VERSION = 1

    const val PATH_SOS = "/lifeguard/sos"
    const val PATH_SOS_ACK = "/lifeguard/sos_ack"
    const val PATH_SOS_STATUS = "/lifeguard/sos_status"

    /** יכולות (res/values/wear.xml) - כך השעון מוצא טלפון שמותקנת בו LifeGuard ולא סתם צומת מחובר */
    const val CAPABILITY_PHONE = "lifeguard_phone"
    const val CAPABILITY_WATCH = "lifeguard_watch"

    const val KIND_MANUAL = "manual"
    const val KIND_WATCH_FALL = "watch_fall"
    const val WHY_NO_ANSWER = "no_answer"
    const val WHY_HELP_REQUESTED = "help_requested"

    const val MAX_SIGNALS = 8
    const val MAX_REASON = 200
    const val MAX_REQUEST_ID = 64
    const val MAX_BYTES = 16 * 1024

    /**
     * kind=manual: רק requestId/atMs (SOS ידני = תמיד מלא, בלי הערכה).
     * kind=watch_fall: why + confidence של הגלאי (0..1) + האותות שגרמו לשאלה (MotionFusion בשעון).
     */
    data class SosRequest(
        val requestId: String,
        val kind: String,
        val atMs: Long,
        val why: String? = null,
        val confidence: Double? = null,
        val signals: List<MotionSignal> = emptyList(),
    )

    enum class AckStatus(val wire: String) {
        /** נשמר ב-outbox המוצפן של הטלפון והטלפון מנסה לשלוח (כמו כפתור ה-SOS בטלפון) */
        QUEUED("queued"),
        /** כבר יש קריאה פעילה/ממתינה בטלפון - לא נפתחה חדשה */
        ALREADY_ACTIVE("already_active"),
        /** אין חשבון מחובר באפליקציה בטלפון - אין לאן לשלוח */
        NOT_LOGGED_IN("not_logged_in"),
        /** הבקשה לא תקינה (גרסה/שדות) - לא נפתחה קריאה */
        INVALID("invalid");

        companion object { fun of(s: String?) = entries.firstOrNull { it.wire == s } }
    }

    data class SosAck(val requestId: String, val status: AckStatus, val clientId: String? = null)

    enum class PhoneState(val wire: String) {
        /** השרת פתח אירוע (activation = מה שהשרת החליט: manual / confirmed_high / unconfirmed) */
        DELIVERED("delivered"),
        /** אין עדיין תשובה מהשרת - הטלפון ממשיך לנסות (attempts) */
        RETRYING("retrying"),
        /** הטלפון העביר SMS ישיר לשליחה (smsCount מספרים) - "הועבר לשליחה", לא "נמסר" */
        SMS_SUBMITTED("sms_submitted"),
        SMS_FAILED("sms_failed"),
        /** 422 low_confidence: זיהוי אוטומטי בביטחון נמוך - השרת לא פתח אירוע */
        REJECTED_LOW_CONFIDENCE("rejected_low_confidence");

        companion object { fun of(s: String?) = entries.firstOrNull { it.wire == s } }
    }

    data class SosStatus(
        val requestId: String,
        val clientId: String,
        val state: PhoneState,
        val activation: String? = null,
        val attempts: Int = 0,
        val smsCount: Int = 0,
    )

    // ------------------------------------------------------------ קידוד
    fun encodeRequest(r: SosRequest): ByteArray {
        val o = JSONObject().put("v", VERSION).put("req", r.requestId).put("kind", r.kind).put("at", r.atMs)
        r.why?.let { o.put("why", it) }
        r.confidence?.let { o.put("confidence", it) }
        if (r.signals.isNotEmpty()) {
            val a = JSONArray()
            for (s in r.signals.take(MAX_SIGNALS)) {
                a.put(JSONObject().put("source", s.source).put("confidence", s.confidence).put("at", s.atMs)
                    .put("reason", s.reason.take(MAX_REASON)))
            }
            o.put("signals", a)
        }
        return o.toString().toByteArray(Charsets.UTF_8)
    }

    fun encodeAck(a: SosAck): ByteArray = JSONObject().put("v", VERSION).put("req", a.requestId)
        .put("status", a.status.wire).apply { a.clientId?.let { put("client_id", it) } }
        .toString().toByteArray(Charsets.UTF_8)

    fun encodeStatus(s: SosStatus): ByteArray = JSONObject().put("v", VERSION).put("req", s.requestId)
        .put("client_id", s.clientId).put("state", s.state.wire).put("attempts", s.attempts).put("sms_count", s.smsCount)
        .apply { s.activation?.let { put("activation", it) } }
        .toString().toByteArray(Charsets.UTF_8)

    // ------------------------------------------------------------ פענוח (null = לא תקין; אף פעם לא זורק)
    fun decodeRequest(bytes: ByteArray?): SosRequest? {
        val o = parse(bytes) ?: return null
        val req = requestId(o) ?: return null
        val kind = o.optString("kind", "")
        val at = o.optLong("at", 0L)
        return when (kind) {
            KIND_MANUAL -> SosRequest(req, KIND_MANUAL, at)
            KIND_WATCH_FALL -> {
                val why = o.optString("why", "")
                if (why != WHY_NO_ANSWER && why != WHY_HELP_REQUESTED) return null
                val conf = if (o.has("confidence")) o.optDouble("confidence", Double.NaN) else Double.NaN
                if (conf.isNaN() || conf < 0.0 || conf > 1.0) return null
                val signals = ArrayList<MotionSignal>()
                val arr = o.optJSONArray("signals") ?: JSONArray()
                for (i in 0 until minOf(arr.length(), MAX_SIGNALS)) {
                    val s = arr.optJSONObject(i) ?: continue
                    val source = s.optString("source", "").take(32)
                    val c = s.optDouble("confidence", Double.NaN)
                    if (source.isEmpty() || c.isNaN()) continue
                    signals += MotionSignal(source, c.coerceIn(0.0, 1.0), s.optLong("at", at),
                        s.optString("reason", "").take(MAX_REASON))
                }
                SosRequest(req, KIND_WATCH_FALL, at, why, conf, signals)
            }
            else -> null
        }
    }

    fun decodeAck(bytes: ByteArray?): SosAck? {
        val o = parse(bytes) ?: return null
        val req = requestId(o) ?: return null
        val status = AckStatus.of(o.optString("status", "")) ?: return null
        return SosAck(req, status, o.optString("client_id", "").takeIf { it.isNotEmpty() })
    }

    fun decodeStatus(bytes: ByteArray?): SosStatus? {
        val o = parse(bytes) ?: return null
        val req = requestId(o) ?: return null
        val state = PhoneState.of(o.optString("state", "")) ?: return null
        val cid = o.optString("client_id", "").takeIf { it.isNotEmpty() } ?: return null
        return SosStatus(req, cid, state, o.optString("activation", "").takeIf { it.isNotEmpty() },
            o.optInt("attempts", 0).coerceAtLeast(0), o.optInt("sms_count", 0).coerceAtLeast(0))
    }

    private fun parse(bytes: ByteArray?): JSONObject? {
        if (bytes == null || bytes.isEmpty() || bytes.size > MAX_BYTES) return null
        val o = try { JSONObject(String(bytes, Charsets.UTF_8)) } catch (_: Exception) { return null }
        // גרסה חדשה יותר עם אותם שדות עדיין נקראת; בלי גרסה = לא שלנו
        val v = o.optInt("v", 0)
        return if (v >= 1) o else null
    }

    private fun requestId(o: JSONObject): String? =
        o.optString("req", "").takeIf { it.isNotEmpty() && it.length <= MAX_REQUEST_ID }

    // ------------------------------------------------------------ מקור ה-SOS בטלפון (PendingSos.source)
    /** "watch:<kind>:<requestId>" - כך הטלפון יודע להחזיר סטטוס לשעון גם אחרי שהתהליך שלו הופעל מחדש */
    fun phoneSource(kind: String, requestId: String) = "watch:$kind:$requestId"

    /** (kind, requestId) אם ה-SOS נפתח מהשעון, אחרת null */
    fun parsePhoneSource(source: String?): Pair<String, String>? {
        if (source == null || !source.startsWith("watch:")) return null
        val parts = source.removePrefix("watch:").split(":", limit = 2)
        if (parts.size != 2 || parts[0].isEmpty() || parts[1].isEmpty()) return null
        return parts[0] to parts[1]
    }
}
