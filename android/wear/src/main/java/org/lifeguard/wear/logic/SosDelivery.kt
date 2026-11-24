package org.lifeguard.wear.logic

import org.lifeguard.app.wearlink.WearProtocol
import org.lifeguard.app.wearlink.WearProtocol.AckStatus
import org.lifeguard.app.wearlink.WearProtocol.PhoneState

/**
 * מה השעון יודע על ה-SOS שלו - ורק את זה (בלי Android; נבדק ב-JVM):
 * - "נשלח לטלפון" רק אחרי SosAck(queued) מהטלפון. לפני כן: "ממתין לאישור מהטלפון".
 * - "התקבל בשרת" רק אחרי SosStatus(delivered) שהטלפון העביר.
 * - אין טלפון מחובר / אין LifeGuard בטלפון / אין אישור בזמן -> אומרים את זה ומציעים חיוג 100 מהשעון.
 */
class SosDelivery(val ackTimeoutMs: Long = 12_000) {
    enum class NoPhoneReason { NOT_CONNECTED, APP_MISSING }

    sealed class State {
        object Idle : State()
        data class Sending(val requestId: String, val kind: String) : State()
        data class NoPhone(val requestId: String, val kind: String, val reason: NoPhoneReason) : State()
        data class SendFailed(val requestId: String, val kind: String) : State()
        data class AwaitingAck(val requestId: String, val kind: String, val sentAt: Long) : State()
        data class AckTimeout(val requestId: String, val kind: String) : State()
        data class Acked(
            val requestId: String,
            val kind: String,
            val ack: WearProtocol.SosAck,
            /** המצב האחרון מול השרת (delivered / retrying / rejected_low_confidence) */
            val server: WearProtocol.SosStatus? = null,
            /** ה-SMS האחרון (sms_submitted / sms_failed) */
            val sms: WearProtocol.SosStatus? = null,
        ) : State()
    }

    /** offerCall: להציג "התקשר/י 100". canRetry: לשלוח שוב את אותה בקשה (אותו requestId - הטלפון לא יפתח כפול). */
    data class View(val title: String, val detail: String, val offerCall: Boolean, val canRetry: Boolean, val tone: Tone)
    enum class Tone { PROGRESS, OK, WARN, ERROR }

    var state: State = State.Idle
        private set

    val requestId: String? get() = when (val s = state) {
        is State.Sending -> s.requestId
        is State.NoPhone -> s.requestId
        is State.SendFailed -> s.requestId
        is State.AwaitingAck -> s.requestId
        is State.AckTimeout -> s.requestId
        is State.Acked -> s.requestId
        State.Idle -> null
    }

    private val kind: String? get() = when (val s = state) {
        is State.Sending -> s.kind
        is State.NoPhone -> s.kind
        is State.SendFailed -> s.kind
        is State.AwaitingAck -> s.kind
        is State.AckTimeout -> s.kind
        is State.Acked -> s.kind
        State.Idle -> null
    }

    val active get() = state !is State.Idle

    fun begin(requestId: String, kind: String) { state = State.Sending(requestId, kind) }

    fun onNoPhone(requestId: String, reason: NoPhoneReason) {
        if (requestId == this.requestId && state is State.Sending) state = State.NoPhone(requestId, kind!!, reason)
    }

    fun onSendFailed(requestId: String) {
        if (requestId == this.requestId && state is State.Sending) state = State.SendFailed(requestId, kind!!)
    }

    /** MessageClient הצליח למסור לשכבת הקישור - זה עדיין לא אישור מהאפליקציה בטלפון */
    fun onSent(requestId: String, t: Long) {
        if (requestId == this.requestId && state is State.Sending) state = State.AwaitingAck(requestId, kind!!, t)
    }

    /** true אם ה-ack שייך ל-SOS הנוכחי (אחרת מתעלמים - ack ישן) */
    fun onAck(ack: WearProtocol.SosAck): Boolean {
        if (ack.requestId != requestId) return false
        val s = state
        // ack מאוחר אחרי "אין אישור" עדיין נכון - מעדכנים; ack כפול לא מוחק סטטוס שכבר הגיע
        state = if (s is State.Acked) s.copy(ack = ack) else State.Acked(ack.requestId, kind!!, ack)
        return true
    }

    fun onStatus(st: WearProtocol.SosStatus): Boolean {
        if (st.requestId != requestId) return false
        val base = when (val s = state) {
            is State.Acked -> s
            // הטלפון שולח סטטוס רק ל-SOS שנשמר אצלו - סטטוס שהקדים את ה-ack הוא גם אישור
            else -> State.Acked(st.requestId, kind!!, WearProtocol.SosAck(st.requestId, AckStatus.QUEUED, st.clientId))
        }
        state = when (st.state) {
            PhoneState.SMS_SUBMITTED, PhoneState.SMS_FAILED -> base.copy(sms = st)
            // אחרי delivered לא חוזרים ל-"מנסה שוב" (הודעה ישנה שהגיעה באיחור)
            PhoneState.RETRYING -> if (base.server?.state == PhoneState.DELIVERED) base else base.copy(server = st)
            else -> base.copy(server = st)
        }
        return true
    }

    fun tick(t: Long) {
        val s = state
        if (s is State.AwaitingAck && t - s.sentAt >= ackTimeoutMs) state = State.AckTimeout(s.requestId, s.kind)
    }

    /** "נסה/י שוב" - אותו requestId */
    fun retry(): Pair<String, String>? {
        val id = requestId ?: return null
        val k = kind ?: return null
        if (!view().canRetry) return null
        state = State.Sending(id, k)
        return id to k
    }

    fun clear() { state = State.Idle }

    // ------------------------------------------------------------ טקסט למסך
    fun view(): View = when (val s = state) {
        State.Idle -> View("", "", offerCall = false, canRetry = false, tone = Tone.PROGRESS)
        is State.Sending -> View(T_SENDING, "", false, false, Tone.PROGRESS)
        is State.AwaitingAck -> View(T_WAITING_ACK, "", false, false, Tone.PROGRESS)
        is State.NoPhone -> View(if (s.reason == NoPhoneReason.NOT_CONNECTED) T_NO_PHONE else T_NO_APP, T_CALL_FROM_WATCH,
            offerCall = true, canRetry = true, tone = Tone.ERROR)
        is State.SendFailed -> View(T_SEND_FAILED, T_CALL_FROM_WATCH, true, true, Tone.ERROR)
        is State.AckTimeout -> View(T_NO_ACK, T_MAYBE_NOT_RECEIVED + "\n" + T_CALL_FROM_WATCH, true, true, Tone.ERROR)
        is State.Acked -> ackedView(s)
    }

    private fun ackedView(s: State.Acked): View = when (s.ack.status) {
        AckStatus.ALREADY_ACTIVE -> View(T_ALREADY_ACTIVE, T_SEE_PHONE, false, false, Tone.WARN)
        AckStatus.NOT_LOGGED_IN -> View(T_NOT_LOGGED_IN, T_CALL_FROM_WATCH, true, false, Tone.ERROR)
        AckStatus.INVALID -> View(T_INVALID, T_CALL_FROM_WATCH, true, false, Tone.ERROR)
        AckStatus.QUEUED -> {
            val lines = ArrayList<String>()
            var offerCall = false
            var tone = Tone.OK
            var title = T_SENT_TO_PHONE
            val server = s.server
            when (server?.state) {
                null -> lines += T_PHONE_SENDING
                PhoneState.DELIVERED -> lines +=
                    if (server.activation == "unconfirmed") T_DELIVERED_UNCONFIRMED else T_DELIVERED
                PhoneState.RETRYING -> {
                    lines += T_RETRYING.format(server.attempts); offerCall = true; tone = Tone.WARN
                }
                PhoneState.REJECTED_LOW_CONFIDENCE -> {
                    title = T_REJECTED_TITLE; lines += T_REJECTED; tone = Tone.WARN
                }
                else -> {}
            }
            when (s.sms?.state) {
                PhoneState.SMS_SUBMITTED -> lines += T_SMS_SUBMITTED.format(s.sms.smsCount)
                PhoneState.SMS_FAILED -> { lines += T_SMS_FAILED; offerCall = true; if (tone == Tone.OK) tone = Tone.WARN }
                else -> {}
            }
            View(title, lines.joinToString("\n"), offerCall, false, tone)
        }
    }

    companion object {
        const val T_SENDING = "שולח לטלפון…"
        const val T_WAITING_ACK = "ממתין לאישור מהטלפון…"
        const val T_NO_PHONE = "הטלפון לא מחובר"
        const val T_NO_APP = "LifeGuard לא נמצאה בטלפון המחובר"
        const val T_CALL_FROM_WATCH = "התקשר/י 100 מהשעון אם יש לו סים"
        const val T_SEND_FAILED = "השליחה לטלפון נכשלה"
        const val T_NO_ACK = "הטלפון לא אישר קבלה"
        const val T_MAYBE_NOT_RECEIVED = "ייתכן שהקריאה לא התקבלה."
        const val T_ALREADY_ACTIVE = "כבר יש קריאת חירום פעילה בטלפון"
        const val T_SEE_PHONE = "לא נפתחה קריאה נוספת. הפרטים בטלפון."
        const val T_NOT_LOGGED_IN = "LifeGuard בטלפון לא מחוברת לחשבון"
        const val T_INVALID = "הטלפון לא קיבל את הבקשה (גרסאות לא תואמות?)"
        const val T_SENT_TO_PHONE = "נשלח לטלפון"
        const val T_PHONE_SENDING = "הטלפון שולח לשרת LifeGuard…"
        const val T_DELIVERED = "הקריאה התקבלה בשרת LifeGuard"
        const val T_DELIVERED_UNCONFIRMED = "התקבלה בשרת כזיהוי אוטומטי לא מאושר: השרת מתריע לאנשי הקשר; מתנדבים - רק אחרי אישור בטלפון"
        const val T_RETRYING = "הטלפון עדיין מנסה להגיע לשרת (ניסיון %d)"
        const val T_REJECTED_TITLE = "לא נפתחה קריאה"
        const val T_REJECTED = "השרת לא פתח קריאה (זיהוי אוטומטי בביטחון נמוך). צריך/ה עזרה? החזק/י SOS."
        const val T_SMS_SUBMITTED = "אין רשת בטלפון - הטלפון העביר SMS ישיר לשליחה ל-%d מספרים"
        const val T_SMS_FAILED = "שליחת SMS ישיר מהטלפון נכשלה"
    }
}
