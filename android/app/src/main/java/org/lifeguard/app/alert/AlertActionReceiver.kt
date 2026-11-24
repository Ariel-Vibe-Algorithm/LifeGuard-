package org.lifeguard.app.alert

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import org.lifeguard.app.LG
import org.lifeguard.app.R
import org.lifeguard.app.UiEvent
import org.lifeguard.app.api.ApiException
import org.lifeguard.app.core.Effect
import java.io.IOException

/**
 * "קבלה" מתוך ההתראה (בעיקר מהשעון - פעולה ב-WearableExtender; הפעולה רצה בטלפון).
 * אותו מסלול כמו כפתור "קבלה" במסך הקריאה: הצלצול נעצר מיד, ואז POST respond(accept) לשרת.
 * "קיבלת את הקריאה" מוצג רק אחרי שהשרת אישר; כישלון -> התראה שהקבלה לא נשלחה ושצריך לפתוח בטלפון.
 */
class AlertActionReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != ACTION_ACCEPT) return
        val incidentId = intent.getStringExtra(EXTRA_INCIDENT_ID) ?: return
        val owner = intent.getStringExtra(EXTRA_OWNER) ?: ""
        val ctx = context.applicationContext
        stopRing(ctx, incidentId)
        val pending = goAsync()
        Thread {
            try {
                LG.api().respond(incidentId, "accept")
                LG.connectivity.onSuccess(LG.nowSec())
                LG.emit(UiEvent.IncidentUpdated(incidentId, "accepted"))
                Notifier.showInfo(ctx, ctx.getString(R.string.watch_accept_ok_title),
                    ctx.getString(R.string.watch_accept_ok_text, owner), quiet = false, incidentId = incidentId)
            } catch (e: Exception) {
                if (e is IOException) LG.connectivity.onFailure(LG.nowSec())
                val closed = e is ApiException && (e.status == 404 || e.status == 409)
                Notifier.showInfo(ctx, ctx.getString(R.string.watch_accept_failed_title),
                    ctx.getString(if (closed) R.string.alert_closed else R.string.watch_accept_failed_text), quiet = false)
            } finally {
                pending.finish()
            }
        }.start()
    }

    private fun stopRing(ctx: Context, incidentId: String) {
        val effects = LG.ring.onLocalResponse(incidentId, "accept")
        if (effects.any { it is Effect.StopRing }) {
            if (LG.ring.ringingIncidents.isEmpty() && LG.alarm.mode == AlarmPlayer.Mode.RING) LG.alarm.stop()
            LG.emit(UiEvent.RingChanged(incidentId, false))
        }
        Notifier.cancel(ctx, Notifier.incidentNotifId(incidentId))
    }

    companion object {
        const val ACTION_ACCEPT = "org.lifeguard.app.action.ACCEPT_ALERT"
        const val EXTRA_INCIDENT_ID = "incident_id"
        const val EXTRA_OWNER = "owner"
    }
}
