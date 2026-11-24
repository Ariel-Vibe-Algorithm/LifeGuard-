package org.lifeguard.wear

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.Handler
import android.os.Looper
import com.google.android.gms.wearable.MessageEvent
import com.google.android.gms.wearable.WearableListenerService
import org.lifeguard.app.wearlink.WearProtocol

/** אישור (SosAck) ומצב מסירה (SosStatus) מהטלפון -> WatchController (main thread) */
class WatchListenerService : WearableListenerService() {
    override fun onMessageReceived(event: MessageEvent) {
        val ctx = applicationContext
        when (event.path) {
            WearProtocol.PATH_SOS_ACK -> WearProtocol.decodeAck(event.data)?.let { ack ->
                Handler(Looper.getMainLooper()).post { WatchController.onAck(ctx, ack) }
            }
            WearProtocol.PATH_SOS_STATUS -> WearProtocol.decodeStatus(event.data)?.let { st ->
                Handler(Looper.getMainLooper()).post { WatchController.onStatus(ctx, st) }
            }
        }
    }
}

/** "אני בסדר" / "צריך/ה עזרה" מתוך התראת הנפילה */
class FallActionReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        WatchController.init(context)
        when (intent.action) {
            ACTION_OK -> WatchController.answerOk()
            ACTION_HELP -> WatchController.answerHelp()
        }
    }

    companion object {
        const val ACTION_OK = "org.lifeguard.wear.action.FALL_OK"
        const val ACTION_HELP = "org.lifeguard.wear.action.FALL_HELP"
    }
}

/** אחרי אתחול / עדכון: זיהוי הנפילה חוזר לפעול אם הוא מופעל בהגדרות */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action == Intent.ACTION_BOOT_COMPLETED || intent.action == Intent.ACTION_MY_PACKAGE_REPLACED) {
            FallMonitorService.sync(context)
        }
    }
}
