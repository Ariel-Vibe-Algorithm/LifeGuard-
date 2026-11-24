package org.lifeguard.app.service

import android.content.Context
import org.lifeguard.app.LG
import org.lifeguard.app.UiEvent
import org.lifeguard.app.api.ApiException
import org.lifeguard.app.api.GeoPoint
import org.lifeguard.app.alert.Notifier
import org.lifeguard.app.core.ActivationPolicy
import org.lifeguard.app.core.Activations
import org.lifeguard.app.core.Triggers
import org.lifeguard.app.core.NoSignalPolicy
import org.lifeguard.app.core.PendingSos
import org.lifeguard.app.core.RiskAssessment
import org.lifeguard.app.core.SmsComposer
import org.lifeguard.app.core.SmsState
import org.json.JSONObject
import org.lifeguard.app.wear.WearRelay
import org.lifeguard.app.wearlink.WearProtocol.PhoneState
import java.io.IOException
import java.util.UUID
import java.util.concurrent.atomic.AtomicBoolean

/**
 * ה-SOS של המשתמש/ת (בעל/ת האירוע):
 * - יצירה: נשמר קודם ב-outbox מוצפן (PendingSos) ורק אז נשלח לשרת עם client_id קבוע - כך ניסיון חוזר
 *   אחרי ניתוק לעולם לא פותח אירוע כפול. אין רשת -> SMS ישיר לאנשי הקשר (NoSignalPolicy).
 * - בזמן אירוע פעיל: מיקום כל 15 שניות.
 * - "אני בטוח/ה": סגירה (resolved). בלי רשת - נשמר ונשלח כשהקליטה חוזרת.
 */
object IncidentManager {
    private val lock = Any()
    const val LOCATION_INTERVAL_SECONDS = 15.0
    private var lastLocationSent = 0.0
    private var lastLocationFixTime = 0.0
    /** processOutbox רץ גם מהשירות וגם מיד כשמגיע SOS מהשעון - לא במקביל (SMS כפול) */
    private val outboxBusy = AtomicBoolean(false)

    val isActive get() = LG.store.activeIncidentId != null || LG.store.pendingSos != null
    val activeMode: String? get() = LG.store.pendingSos?.mode ?: LG.store.activeIncidentMode

    fun mutatePending(fn: (PendingSos) -> PendingSos?) = synchronized(lock) {
        LG.store.pendingSos?.let { LG.store.pendingSos = fn(it) }
    }

    /**
     * נקרא מהממשק (או מ"הכל בסדר?" בלי תשובה). mode: loud | silent.
     * risk: רק כשה-SOS נפתח מזיהוי (חיישנים / "הכל בסדר?"). SOS ידני - בלי risk (השרת מעריך בעצמו).
     * trigger: manual (ברירת מחדל - כפתור). SOS ידני **לא** עובר שום שלב הערכה: נשמר ב-outbox ונשלח מיד.
     */
    fun startSos(ctx: Context, mode: String, source: String = "button", risk: RiskAssessment? = null,
                 trigger: String = Triggers.MANUAL) {
        val p: PendingSos
        synchronized(lock) {
            if (isActive) {
                // כבר יש אירוע. "הכל בסדר?" שהסלים בזמן מצוקה שקטה לא הופך אותה לגלויה.
                return
            }
            LG.store.sosSmsSent = false
            p = PendingSos(
                clientId = UUID.randomUUID().toString(), mode = mode, createdAt = LG.nowSec(),
                location = LG.lastFix, source = source, escortId = LG.store.escortId,
                riskJson = risk?.toJson()?.toString(), trigger = trigger,
            )
            LG.store.pendingSos = p
            LG.store.activeIncidentActivation = null
            LG.store.pendingConfirm = false
        }
        // יומן ראיות מקומי: נפתח עם ה-client_id (מזהה האירוע מהשרת יקושר כשיגיע)
        Evidence.begin(p.clientId, mode, JSONObject().put("client_id", p.clientId).put("mode", mode).put("source", source)
            .apply { p.escortId?.let { put("escort_id", it) }; p.location?.let { put("location", Evidence.geo(it)) } })
        Evidence.record("sos_queued", JSONObject().put("client_id", p.clientId).put("trigger", trigger)
            .apply { risk?.let { put("risk", it.toJson()) } })
        risk?.signals?.forEach { Evidence.motion(it, beforeSos = true) }
        if (mode == "loud" && LG.store.localSiren) LG.alarm.startSiren()
        LG.emit(UiEvent.StateChanged)
        LifeGuardService.start(ctx, kick = true)
        // מיקום טרי אם אפשר (עד 4 שנ') - מתעדכן ב-outbox לפני הניסיון הבא / נשלח כעדכון מיקום
        LocationTracker.currentOnce(ctx) { fix ->
            if (fix != null) mutatePending { p -> if (p.location == null) p.copy(location = fix) else p }
            LifeGuardService.start(ctx, kick = true)
        }
    }

    /** מהשירות (thread רקע) / מ-WearSosListenerService: ניסיון שליחה / SMS לפי המדיניות. */
    fun processOutbox(ctx: Context) {
        if (!outboxBusy.compareAndSet(false, true)) return          // ניסיון אחר רץ עכשיו; השירות ינסה שוב בעוד שנייה
        try { processOutboxOnce(ctx) } finally { outboxBusy.set(false) }
    }

    private fun processOutboxOnce(ctx: Context) {
        val p = synchronized(lock) { LG.store.pendingSos } ?: return
        val now = LG.nowSec()
        if (NoSignalPolicy.shouldAttemptCreate(p, now)) {
            try {
                val v = LG.api().createIncident(p.clientId, p.mode, emergencyType = "unknown", severity = "high",
                    location = p.location ?: LG.lastFix, risk = p.risk(), escortId = p.escortId, trigger = p.trigger)
                LG.connectivity.onSuccess(LG.nowSec())
                synchronized(lock) {
                    if (LG.store.pendingSos?.clientId != p.clientId) return     // בוטל בינתיים ("אני בטוח/ה")
                    LG.store.activeIncidentId = v.id
                    LG.store.activeIncidentMode = v.mode
                    LG.store.activeIncidentActivation = v.activation
                    LG.store.pendingSos = null
                    if (p.escortId != null) LG.store.escortId = null   // השרת קישר את הליווי לאירוע
                }
                lastLocationSent = LG.nowSec()
                Evidence.linkIncident(v.id)
                Evidence.record("sos_sent", JSONObject().put("incident_id", v.id).put("attempts", p.attempts + 1)
                    .put("server_status", v.status).put("server_mode", v.mode).put("activation", v.activation ?: "")
                    .put("trigger", p.trigger))
                // SOS מהשעון: מה שהשרת באמת החליט (activation) - השעון מציג "התקבל בשרת" רק עכשיו
                WearRelay.status(ctx, p, PhoneState.DELIVERED, activation = v.activation, attempts = p.attempts + 1)
                if (Activations.needsOwnerConfirmation(v.activation)) {
                    // זיהוי אוטומטי בביטחון בינוני: אנשי הקשר קיבלו התראה, מתנדבים לא. הבעלים מתבקש/ת לבחור.
                    Notifier.showConfirmEmergency(ctx)
                }
                LG.emit(UiEvent.StateChanged)
                return
            } catch (e: ApiException) {
                if (ActivationPolicy.classifyCreateError(e.status, e.code) == ActivationPolicy.CreateFailure.LowConfidence) {
                    onLowConfidence(ctx, p)
                    return
                }
                retryLater(p, now, e)
            } catch (e: Exception) {
                retryLater(p, now, e)
            }
        }
        maybeSendSms(ctx)
    }

    /**
     * 422 low_confidence: השרת לא פתח אירוע (זיהוי אוטומטי בביטחון נמוך). לא שולחים שוב - גם לא כ-SOS ידני.
     * עוצרים סירנה, רושמים ביומן המקומי ובתור הביקורת, ומודיעים בעדינות (אם צריך עזרה - SOS ידני תמיד זמין).
     */
    private fun onLowConfidence(ctx: Context, p: PendingSos) {
        synchronized(lock) {
            if (LG.store.pendingSos?.clientId == p.clientId) LG.store.pendingSos = null
        }
        stopSiren()
        WearRelay.status(ctx, p, PhoneState.REJECTED_LOW_CONFIDENCE)
        Evidence.record("auto_activation_rejected", JSONObject().put("client_id", p.clientId).put("trigger", p.trigger)
            .put("confidence", p.confidence ?: -1.0).put("server", "422 low_confidence"))
        Evidence.end("rejected_low_confidence", JSONObject().put("by", "server"))
        PrivacySync.audit("auto_activation_rejected", "completed", JSONObject().put("reason", "low_confidence")
            .put("trigger", p.trigger).put("confidence", p.confidence ?: "").put("client_id", p.clientId))
        Notifier.showInfo(ctx, "LifeGuard", ctx.getString(org.lifeguard.app.R.string.low_confidence_info), quiet = false)
        LG.emit(UiEvent.StateChanged)
    }

    private fun retryLater(p: PendingSos, now: Double, e: Exception) {
        if (e is IOException || (e is ApiException && e.isRetryable)) LG.connectivity.onFailure(LG.nowSec())
        mutatePending { it.copy(attempts = it.attempts + 1, lastAttemptAt = now) }
        WearRelay.status(LG.app, p, PhoneState.RETRYING, attempts = p.attempts + 1)
        Evidence.record("sos_retry", JSONObject().put("attempt", p.attempts + 1)
            .put("error", if (e is ApiException) "http_${e.status}:${e.code}" else e.javaClass.simpleName))
        LG.emit(UiEvent.StateChanged)
    }

    private fun maybeSendSms(ctx: Context) {
        val p = LG.store.pendingSos ?: return
        val numbers = LG.store.emergencyNumbers
        val now = LG.nowSec()
        if (!NoSignalPolicy.shouldSendSms(p, now, LG.networkAvailable(ctx), numbers.isNotEmpty(), SmsSender.hasPermission(ctx))) return
        val name = LG.store.displayName ?: LG.store.username ?: ""
        val text = SmsComposer.help(name, LG.lastFix ?: p.location, System.currentTimeMillis())
        mutatePending { it.copy(smsState = SmsState.SENDING, smsAttempts = it.smsAttempts + 1, lastSmsAt = now) }
        val sent = SmsSender.send(ctx, numbers, text, SmsSender.KIND_HELP)
        if (sent == 0) mutatePending { it.copy(smsState = SmsState.FAILED) }
        WearRelay.status(ctx, p, if (sent > 0) PhoneState.SMS_SUBMITTED else PhoneState.SMS_FAILED, smsCount = sent)
        // בלי המספרים עצמם (מזעור מידע) - רק כמה נשלחו
        Evidence.record("sms_fallback", JSONObject().put("numbers", numbers.size).put("submitted", sent)
            .put("attempt", p.smsAttempts + 1))
        if (p.smsAttempts == 0) PrivacySync.audit("sos_activated_offline", if (sent > 0) "completed" else "failed",
            JSONObject().put("channel", "sms").put("sms_submitted", sent).put("trigger", p.trigger).put("mode", p.mode)
                .put("client_id", p.clientId), deviceTs = p.createdAt)
        LG.emit(UiEvent.StateChanged)
    }

    /** כל שנייה מהשירות: מיקום חי לאירוע פעיל כל 15 שנ'. */
    fun sendLiveLocation() {
        val id = LG.store.activeIncidentId ?: return
        val fix = LG.lastFix ?: return
        val now = LG.nowSec()
        if (now - lastLocationSent < LOCATION_INTERVAL_SECONDS) return
        lastLocationSent = now
        if ((fix.capturedAt ?: 0.0) == lastLocationFixTime && now - lastLocationFixTime < 120) return   // אין מיקום חדש
        try {
            LG.api().sendIncidentLocation(id, fix)
            lastLocationFixTime = fix.capturedAt ?: 0.0
            LG.connectivity.onSuccess(LG.nowSec())
        } catch (e: ApiException) {
            if (e.status == 409 || e.status == 404) clearActive()   // האירוע נסגר (למשל ממכשיר אחר)
        } catch (e: IOException) {
            LG.connectivity.onFailure(LG.nowSec())
        }
    }

    /**
     * "אני בטוח/ה" / "סיום" (status=resolved) או "אזעקת שווא" (falseAlarm -> status=cancelled בשרת).
     * מחזיר את מפתח יומן הראיות של האירוע (במקרה של אזעקת שווא - לשאול אם למחוק).
     */
    fun resolve(ctx: Context, falseAlarm: Boolean = false): String? {
        stopSiren()
        LG.store.pendingResolveStatus = if (falseAlarm) "cancelled" else "resolved"
        val hadSms: Boolean
        synchronized(lock) {
            hadSms = LG.store.sosSmsSent || LG.store.pendingSos?.smsState == SmsState.SENT
            val pending = LG.store.pendingSos
            val id = LG.store.activeIncidentId
            LG.store.pendingSos = null
            // SOS שעוד לא הגיע לשרת: אולי הגיע ותשובתו אבדה - סוגרים לפי client_id בניסיון הבא
            if (id != null) LG.store.pendingResolveId = id
            else if (pending != null && pending.attempts > 0) LG.store.pendingResolveId = "client:" + pending.clientId +
                "|" + pending.mode + "|" + pending.trigger + "|" + (pending.confidence ?: "")
            LG.store.activeIncidentId = null
            LG.store.activeIncidentMode = null
            LG.store.activeIncidentActivation = null
            LG.store.pendingConfirm = false
            LG.store.sosSmsSent = false
        }
        Notifier.cancel(ctx, Notifier.ID_CONFIRM)
        if (falseAlarm) PrivacySync.audit("false_alarm_cancelled", "completed", JSONObject().put("by", "user"),
            LG.store.pendingResolveId?.takeIf { !it.startsWith("client:") })
        if (hadSms) {
            val name = LG.store.displayName ?: LG.store.username ?: ""
            val n = SmsSender.send(ctx, LG.store.emergencyNumbers, SmsComposer.safe(name, System.currentTimeMillis()), SmsSender.KIND_SAFE)
            Evidence.record("sms_safe", JSONObject().put("submitted", n))
        }
        val evidenceKey = Evidence.end(if (falseAlarm) "false_alarm" else "resolved", JSONObject().put("by", "user"))
        LG.emit(UiEvent.StateChanged)
        LifeGuardService.start(ctx, kick = true)
        return evidenceKey
    }

    /** מהשירות: שליחת הסגירה שנשמרה כשלא הייתה רשת. */
    fun processPendingResolve() {
        val ref = LG.store.pendingResolveId ?: return
        try {
            val id = if (ref.startsWith("client:")) {
                // יצירה חוזרת עם אותו client_id מחזירה את האירוע הקיים אם נוצר (ולא יוצרת חדש... אלא אם לא נוצר בכלל -
                // אז נוצר ומיד נסגר; עדיף על פני אירוע "יתום" פתוח שאנשי הקשר רואים)
                // SOS אוטומטי נשלח עם אותו trigger + ביטחון (לא הופך לידני); 422 = לא נוצר אירוע -> אין מה לסגור
                val parts = ref.removePrefix("client:").split("|")
                val cid = parts[0]; val mode = parts.getOrElse(1) { "loud" }
                val trigger = parts.getOrNull(2)?.takeIf { it.isNotEmpty() } ?: Triggers.MANUAL
                val conf = parts.getOrNull(3)?.toDoubleOrNull()
                try {
                    LG.api().createIncident(cid, mode, location = LG.lastFix, trigger = trigger,
                        risk = if (Triggers.isAutomatic(trigger)) JSONObject().put("confidence", conf ?: 0.0) else null).id
                } catch (e: ApiException) {
                    if (e.status == 422) { LG.store.pendingResolveId = null; return }
                    throw e
                }
            } else ref
            LG.api().setIncidentStatus(id, LG.store.pendingResolveStatus)
            LG.store.pendingResolveId = null
            LG.connectivity.onSuccess(LG.nowSec())
        } catch (e: ApiException) {
            if (!e.isRetryable && !e.isAuth) LG.store.pendingResolveId = null
        } catch (e: IOException) {
            LG.connectivity.onFailure(LG.nowSec())
        }
    }

    /** בהפעלה: האירוע שלי עדיין פעיל בשרת? */
    fun verifyActive() {
        val id = LG.store.activeIncidentId ?: return
        try {
            val v = LG.api().getIncident(id)
            if (!v.isActive) clearActive()
            else if (v.activation != LG.store.activeIncidentActivation) {
                LG.store.activeIncidentActivation = v.activation
                LG.emit(UiEvent.StateChanged)
            }
        } catch (e: ApiException) {
            if (e.status == 404) clearActive()
        } catch (_: IOException) {
        }
    }

    /**
     * "כן, זה חירום" על אירוע unconfirmed -> POST /confirm (מתנדבים נשלחים). בלי רשת: נשמר ונשלח כשהקליטה חוזרת.
     * מחזיר true אם השרת אישר עכשיו.
     */
    fun confirmEmergency(): Boolean {
        val id = LG.store.activeIncidentId ?: return false
        LG.store.pendingConfirm = true
        Evidence.record("owner_confirmed_emergency", JSONObject().put("incident_id", id))
        return processPendingConfirm()
    }

    /** מהשירות / מהממשק: שליחת אישור שממתין. */
    fun processPendingConfirm(): Boolean {
        if (!LG.store.pendingConfirm) return false
        val id = LG.store.activeIncidentId ?: run { LG.store.pendingConfirm = false; return false }
        return try {
            val v = LG.api().confirmIncident(id)
            LG.store.pendingConfirm = false
            LG.store.activeIncidentActivation = v.activation
            LG.connectivity.onSuccess(LG.nowSec())
            Evidence.record("confirm_sent", JSONObject().put("activation", v.activation ?: ""))
            Notifier.cancel(LG.app, Notifier.ID_CONFIRM)
            LG.emit(UiEvent.StateChanged)
            true
        } catch (e: ApiException) {
            if (!e.isRetryable && !e.isAuth) LG.store.pendingConfirm = false
            false
        } catch (e: IOException) {
            LG.connectivity.onFailure(LG.nowSec())
            false
        }
    }

    /** האירוע הפעיל שלי ממתין לאישור הבעלים (זיהוי אוטומטי בביטחון בינוני) */
    val awaitingOwnerConfirmation: Boolean
        get() = LG.store.activeIncidentId != null && !LG.store.pendingConfirm &&
            Activations.needsOwnerConfirmation(LG.store.activeIncidentActivation)

    /** לשירות ההקלטה: האם האירוע הנוכחי ידני / מאושר */
    fun emergencyConfirmed(): Boolean {
        val p = LG.store.pendingSos
        return if (p != null) ActivationPolicy.emergencyConfirmedForRecording(false, null, p.trigger, p.confidence)
        else LG.store.activeIncidentId != null &&
            ActivationPolicy.emergencyConfirmedForRecording(true, LG.store.activeIncidentActivation, null, null)
    }

    private fun clearActive() {
        Evidence.end("closed_remote", JSONObject().put("note", "האירוע נסגר בשרת (ממכשיר אחר / ע\"י השרת)"))
        LG.store.activeIncidentId = null
        LG.store.activeIncidentMode = null
        LG.store.activeIncidentActivation = null
        LG.store.pendingConfirm = false
        Notifier.cancel(LG.app, Notifier.ID_CONFIRM)
        stopSiren()
        LG.emit(UiEvent.StateChanged)
    }

    /** עוצר רק את הסירנה של ה-SOS שלי (לא צלצול של קריאה נכנסת) */
    fun stopSiren() {
        if (LG.alarm.mode == org.lifeguard.app.alert.AlarmPlayer.Mode.SIREN) LG.alarm.stop()
    }

    fun currentLocationOrNull(): GeoPoint? = LG.lastFix

    // ---------------- עזרה מרחוק: "שמישהו יזעיק משטרה בשבילי" / "מישהו לדבר איתו עכשיו" (server/helpers.py) ----------------
    /**
     * בקשה מהשרת למתנדב/ת הזעקה / תמיכה באירוע הפעיל שלי. רק אחרי שהאירוע נוצר בשרת (אחרת IllegalStateException).
     * בקשת הזעקה על אירוע unconfirmed = אישור שזה חירום (השרת מאשר) - מעדכנים את המצב המקומי לפי התשובה.
     * זורק ApiException / IOException. מחזיר את תצוגת הבעלים (כולל "helpers").
     */
    fun requestHelper(kind: String): org.lifeguard.app.api.IncidentView {
        val id = LG.store.activeIncidentId ?: throw IllegalStateException("incident not on server yet")
        val v = LG.api().requestHelper(id, kind)
        LG.connectivity.onSuccess(LG.nowSec())
        synchronized(lock) { LG.store.requestedHelperKinds = id + "|" + (requestedHelperKinds(id) + kind).joinToString(",") }
        Evidence.record("helper_requested", JSONObject().put("kind", kind))
        if (v.activation != null && v.activation != LG.store.activeIncidentActivation) {
            LG.store.activeIncidentActivation = v.activation
            if (!Activations.needsOwnerConfirmation(v.activation)) Notifier.cancel(LG.app, Notifier.ID_CONFIRM)
            LG.emit(UiEvent.StateChanged)
        }
        return v
    }

    /** סוגי העזרה שביקשתי באירוע הזה (מהמכשיר הזה) */
    fun requestedHelperKinds(incidentId: String?): Set<String> {
        val raw = LG.store.requestedHelperKinds ?: return emptySet()
        val (id, kinds) = raw.split("|", limit = 2).let { it[0] to it.getOrElse(1) { "" } }
        return if (incidentId != null && id == incidentId) kinds.split(",").filter { it.isNotEmpty() }.toSet() else emptySet()
    }
}
