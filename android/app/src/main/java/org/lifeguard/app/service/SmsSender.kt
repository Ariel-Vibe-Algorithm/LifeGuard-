package org.lifeguard.app.service

import android.Manifest
import android.app.Activity
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.telephony.SmsManager
import androidx.core.content.ContextCompat
import org.lifeguard.app.LG
import org.lifeguard.app.UiEvent
import org.lifeguard.app.core.SmsState

/** SMS ישיר מהטלפון (כשאין אינטרנט). נשלח ברקע בלי ממשק - מתאים גם למצוקה שקטה. */
object SmsSender {
    const val KIND_HELP = "help"
    const val KIND_SAFE = "safe"
    const val KIND_TEST = "test"
    private var req = 0

    fun hasPermission(ctx: Context) =
        ContextCompat.checkSelfPermission(ctx, Manifest.permission.SEND_SMS) == PackageManager.PERMISSION_GRANTED &&
            ctx.packageManager.hasSystemFeature(PackageManager.FEATURE_TELEPHONY_MESSAGING)

    /** @return מספר ההודעות שנמסרו למערכת לשליחה (0 = לא נשלח כלום) */
    fun send(ctx: Context, numbers: List<String>, text: String, kind: String): Int {
        if (!hasPermission(ctx) || numbers.isEmpty()) return 0
        val sm: SmsManager = if (Build.VERSION.SDK_INT >= 31) ctx.getSystemService(SmsManager::class.java)
        else @Suppress("DEPRECATION") SmsManager.getDefault()
        var n = 0
        for (number in numbers) {
            try {
                val parts = sm.divideMessage(text)
                val sent = ArrayList<PendingIntent>()
                for (i in parts.indices) {
                    val intent = Intent(ctx, SmsResultReceiver::class.java).putExtra("kind", kind)
                    sent += PendingIntent.getBroadcast(ctx, synchronized(this) { ++req }, intent,
                        PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_ONE_SHOT)
                }
                sm.sendMultipartTextMessage(number, null, parts, sent, null)
                n++
            } catch (e: Exception) {
                // מספר לא תקין / אין שירות - ממשיכים לשאר
            }
        }
        return n
    }
}

/** תוצאת שליחת SMS: לפחות חלק אחד הצליח -> SENT; נכשל -> FAILED (ה-outbox ינסה שוב בעוד דקה). */
class SmsResultReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.getStringExtra("kind") != SmsSender.KIND_HELP) return
        val ok = resultCode == Activity.RESULT_OK
        Evidence.record("sms_result", org.json.JSONObject().put("ok", ok).put("result_code", resultCode))
        val store = LG.store
        if (ok) store.sosSmsSent = true
        val p = store.pendingSos ?: return
        val newState = when {
            ok -> SmsState.SENT
            p.smsState == SmsState.SENT -> SmsState.SENT
            else -> SmsState.FAILED
        }
        if (newState != p.smsState) {
            store.pendingSos = p.copy(smsState = newState)
            LG.emit(UiEvent.StateChanged)
        }
    }
}
