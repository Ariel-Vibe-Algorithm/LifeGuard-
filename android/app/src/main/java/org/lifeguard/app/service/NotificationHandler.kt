package org.lifeguard.app.service

import android.content.Context
import android.content.Intent
import org.lifeguard.app.LG
import org.lifeguard.app.R
import org.lifeguard.app.UiEvent
import org.lifeguard.app.alert.Notifier
import org.lifeguard.app.api.ApiClient
import org.lifeguard.app.api.ApiException
import org.lifeguard.app.api.AppNotification
import org.lifeguard.app.api.str
import org.lifeguard.app.core.Effect
import java.io.IOException
import java.util.concurrent.ConcurrentLinkedQueue

/**
 * טיפול בהתראות מהשרת (מכונת הצלצול -> צלצול / התראות / מסכים) ואישורי קבלה.
 * משותף לשירות החזית (long-poll) ולמשיכה החד-פעמית אחרי השכמת FCM (WakePollWorker) - כשאנדרואיד לא נותן להפעיל
 * את השירות. כפילויות לא אפשריות: RingStateMachine מסנן התראות שכבר נראו (לפי מזהה, מסונכרן).
 */
object NotificationHandler {
    private val pendingAcks = ConcurrentLinkedQueue<Long>()

    /** התקנה חדשה / התחברות: לא "משחזרים" היסטוריה - רק מה שעוד לא התקבל באף מכשיר. */
    @Synchronized
    fun initCursorIfNeeded(ctx: Context, api: ApiClient) {
        if (LG.store.lastNotificationId >= 0) return
        val c = api.cursor()
        LG.ring.resetCursor(c.lastId)
        LG.store.lastNotificationId = c.lastId
        handle(ctx, c.unreceived, backlog = true)
    }

    /** משיכה חד-פעמית בלי המתנה (wait=0) - מההשכמה כשהשירות לא רץ. זורק IOException / ApiException. */
    fun pollOnce(ctx: Context) {
        val api = LG.api()
        initCursorIfNeeded(ctx, api)
        val list = api.poll(LG.ring.lastId(), waitSeconds = 0)
        LG.connectivity.onSuccess(LG.nowSec())
        handle(ctx, list, backlog = false)
        saveCursor()
    }

    @Synchronized
    fun saveCursor() {
        val id = LG.ring.lastId()
        if (id > LG.store.lastNotificationId) LG.store.lastNotificationId = id
    }

    fun handle(ctx: Context, list: List<AppNotification>, backlog: Boolean) {
        if (list.isEmpty()) return
        val me = LG.store.username ?: return
        val ownSilent = if (LG.store.activeIncidentMode == "silent") setOfNotNull(LG.store.activeIncidentId) else emptySet()
        // התראות על האירוע שלי (אישורי קבלה, תגובות מגיבים, סגירה) -> יומן הראיות. רק שדות מזהים מינימליים.
        for (n in list) Evidence.recordForIncident(n.incidentId, "server_notification", org.json.JSONObject()
            .put("notification_id", n.id).put("type", n.type).put("server_time", n.createdAt).apply {
                for (k in listOf("event", "by", "action", "from", "reason", "message_id")) n.payload.str(k)?.let { put(k, it) }
            })
        val effects = LG.ring.onNotifications(list, me, ownSilent, LG.api().serverNow(), backlog)
        for (e in effects) apply(ctx, e)
        flushAcks()
    }

    private fun apply(ctx: Context, e: Effect) {
        when (e) {
            is Effect.Ack -> pendingAcks.add(e.notificationId)
            is Effect.StartRing -> {
                LG.alarm.startRing()
                Notifier.showRing(ctx, e.incident)
                LG.emit(UiEvent.RingChanged(e.incident.id, true))
                // מסך מלא גם כשהמסך דלוק (fullScreenIntent מציג רק heads-up אם הטלפון בשימוש)
                runCatching { ctx.startActivity(Notifier.alertIntent(ctx, e.incident, true)) }
            }
            is Effect.StopRing -> stopRingUi(ctx, e.incidentId)
            is Effect.IncidentAlert -> {
                val note = if (e.incident.ring == false && !e.incident.isHelper) ctx.getString(R.string.alert_someone_handling) else null
                Notifier.showIncidentAlert(ctx, e.incident, e.loud, note)
                LG.emit(UiEvent.IncidentUpdated(e.incident.id, "new"))
            }
            is Effect.ChatMessage -> {
                if (LG.visibleChatIncident != e.incidentId) Notifier.showChat(ctx, e.incidentId, e.from, e.quiet)
                LG.emit(UiEvent.IncidentUpdated(e.incidentId, "message"))
            }
            is Effect.IncidentChanged -> {
                LG.emit(UiEvent.IncidentUpdated(e.incidentId, e.event))
                if (!e.quiet && e.text != null && LG.visibleChatIncident != e.incidentId) {
                    Notifier.showInfo(ctx, "LifeGuard", e.text, quiet = false, incidentId = e.incidentId)
                }
            }
            is Effect.IncidentClosed -> {
                Notifier.cancel(ctx, Notifier.incidentNotifId(e.incidentId))
                Notifier.showInfo(ctx, ctx.getString(R.string.alert_closed), e.text ?: ctx.getString(R.string.alert_closed), quiet = false)
                LG.emit(UiEvent.IncidentClosed(e.incidentId))
            }
            is Effect.EscortOverdue -> {
                // ליווי שקט: טקסט ניטרלי מהשרת, בלי צליל
                Notifier.showInfo(ctx, if (e.silent) "LifeGuard" else ctx.getString(R.string.escort_title), e.text,
                    quiet = e.silent, loud = !e.silent)
            }
            is Effect.Info -> {
                Notifier.showInfo(ctx, e.title, e.text, e.quiet)
                if (e.type == "guardian_update") LG.emit(UiEvent.StateChanged)   // רענון מסך הורה-ילד אם פתוח
            }
            is Effect.EscortCheck -> {
                LG.escortCheck = e
                Evidence.record("escort_check", org.json.JSONObject().put("escort_id", e.escortId).put("kind", e.kind))
                if (!e.silent) LG.alarm.startBeep()           // נסיעה שקטה: בלי צליל
                Notifier.showEscortCheck(ctx, e.text, e.silent)
                LG.emit(UiEvent.StateChanged)
                runCatching {
                    ctx.startActivity(org.lifeguard.app.ui.WellnessCheckActivity.escortIntent(ctx)
                        .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP))
                }
            }
        }
    }

    private fun stopRingUi(ctx: Context, incidentId: String) {
        if (LG.ring.ringingIncidents.isEmpty() && LG.alarm.mode == org.lifeguard.app.alert.AlarmPlayer.Mode.RING) LG.alarm.stop()
        Notifier.cancel(ctx, Notifier.incidentNotifId(incidentId))
        LG.emit(UiEvent.RingChanged(incidentId, false))
    }

    fun flushAcks() {
        while (true) {
            val id = pendingAcks.peek() ?: return
            try {
                LG.api().ack(id)
                pendingAcks.remove(id)
            } catch (e: ApiException) {
                if (e.isRetryable) return
                pendingAcks.remove(id)        // 404 וכו' - אין טעם לנסות שוב
            } catch (e: IOException) {
                return                        // ננסה שוב בסבב הבא
            }
        }
    }
}
