package org.lifeguard.app.fcm

import android.util.Log
import com.google.firebase.messaging.FirebaseMessagingService
import com.google.firebase.messaging.RemoteMessage
import org.lifeguard.app.LG
import org.lifeguard.app.core.WakeMessage
import org.lifeguard.app.core.WakePolicy
import org.lifeguard.app.service.LifeGuardService

/**
 * קבלת הודעות FCM. ההודעה היא **השכמה בלבד** ({"t":"wake","k":סוג,"u":"1"|"0"}) - אין בה שום תוכן אישי, ושום דבר
 * ממנה לא מוצג. ההשכמה רק מוודאת ששירות החזית רץ ומושכת מיד את /v1/notifications; משם מכונת הצלצול וההתראות
 * הקיימות עושות את שלהן.
 */
class LifeGuardMessagingService : FirebaseMessagingService() {

    override fun onNewToken(token: String) {
        FcmManager.onNewToken(token)
    }

    override fun onMessageReceived(message: RemoteMessage) {
        val msg = WakeMessage.parse(message.data)
        val d = WakePolicy.decide(msg, LG.store.isLoggedIn, LifeGuardService.running)
        // בלוג רק סוג/דחיפות/עדיפות - לא שום ערך אחר מההודעה
        Log.i(TAG, "wake k=${msg?.kind} u=${msg?.urgent} prio=${message.priority}/${message.originalPriority} -> ${d.action} (${d.reason})")
        if (msg == null || d.action == WakePolicy.Action.IGNORE) return
        FcmManager.recordWake()
        dispatch(msg, d)
    }

    /** FCM השמיט הודעות (המכשיר היה מנותק זמן רב) - מושכים כמו בהשכמה רגילה. */
    override fun onDeletedMessages() {
        if (!LG.store.isLoggedIn) return
        val msg = WakeMessage("deleted", urgent = false)
        dispatch(msg, WakePolicy.decide(msg, true, LifeGuardService.running))
    }

    private fun dispatch(msg: WakeMessage, d: WakePolicy.Decision) {
        when (d.action) {
            WakePolicy.Action.KICK_RUNNING_SERVICE -> {
                val svc = LifeGuardService.instance
                if (svc != null) svc.wakeNow(d.wakeLockMs) else startOrFallback(msg, d)   // נעצר בינתיים
            }
            WakePolicy.Action.START_SERVICE -> startOrFallback(msg, d)
            WakePolicy.Action.IGNORE -> Unit
        }
    }

    private fun startOrFallback(msg: WakeMessage, d: WakePolicy.Decision) {
        val err = LifeGuardService.startForWake(this, d.wakeLockMs) ?: return
        // Android 12+: ForegroundServiceStartNotAllowedException (למשל ההודעה ירדה לעדיפות רגילה) -> משיכה חד-פעמית
        Log.w(TAG, "foreground service start not allowed (${err.javaClass.simpleName}) -> one-shot poll via WorkManager")
        runCatching { WakePollWorker.enqueue(this, WakePolicy.fallback(msg) == WakePolicy.Fallback.EXPEDITED_POLL) }
            .onFailure { Log.w(TAG, "WorkManager enqueue failed: ${it.javaClass.simpleName}") }
    }

    companion object {
        private const val TAG = "LifeGuardFcm"
    }
}
