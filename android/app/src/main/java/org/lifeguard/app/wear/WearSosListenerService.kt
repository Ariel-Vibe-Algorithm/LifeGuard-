package org.lifeguard.app.wear

import android.os.Handler
import android.os.Looper
import android.util.Log
import com.google.android.gms.wearable.MessageEvent
import com.google.android.gms.wearable.WearableListenerService
import org.json.JSONObject
import org.lifeguard.app.LG
import org.lifeguard.app.service.Evidence
import org.lifeguard.app.service.IncidentManager
import org.lifeguard.app.wearlink.WearProtocol
import org.lifeguard.app.wearlink.WearProtocol.AckStatus
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/**
 * SOS / נפילה מהשעון (Wearable Data Layer, נתיב /lifeguard/sos) -> אותו מסלול כמו כפתור ה-SOS בטלפון
 * (IncidentManager.startSos: outbox מוצפן -> שרת, SMS ישיר כשאין רשת). התשובה לשעון (SosAck) נשלחת רק אחרי
 * שה-SOS נשמר ב-outbox; הסטטוס בהמשך - WearRelay.status מתוך IncidentManager.
 */
class WearSosListenerService : WearableListenerService() {

    override fun onMessageReceived(event: MessageEvent) {
        if (event.path != WearProtocol.PATH_SOS) return
        val req = WearProtocol.decodeRequest(event.data)
        val (ack, fresh) = handle(req)
        WearRelay.sendAck(applicationContext, event.sourceNodeId, ack)
        if (fresh) {
            // ייתכן שאנדרואיד לא מרשה להפעיל את שירות החזית מהרקע (Android 12+) - לא מחכים לו: ניסיון שליחה מיידי
            // מכאן (thread רקע של השירות הזה). אותו client_id = השרת לא יפתח אירוע כפול; IncidentManager מונע ריצה כפולה.
            runCatching { IncidentManager.processOutbox(applicationContext) }
                .onFailure { Log.w(TAG, "outbox attempt failed: ${it.javaClass.simpleName}") }
        }
    }

    /** (תשובה, האם נפתח עכשיו SOS חדש) */
    private fun handle(req: WearProtocol.SosRequest?): Pair<WearProtocol.SosAck, Boolean> = synchronized(lock) {
        req?.let { log.get(it.requestId) }?.let { return it to false }      // שליחה חוזרת מהשעון - אותה תשובה
        val requestId = req?.requestId ?: "invalid"
        when (val d = WearSosMapper.decide(req, LG.store.isLoggedIn, IncidentManager.isActive, System.currentTimeMillis())) {
            is WearSosMapper.Decision.Reply -> {
                val ack = WearProtocol.SosAck(requestId, d.status)
                if (req != null) log.put(ack)
                ack to false
            }
            is WearSosMapper.Decision.Start -> {
                onMain { IncidentManager.startSos(applicationContext, d.mode, source = d.source, risk = d.risk, trigger = d.trigger) }
                val p = LG.store.pendingSos
                val ack = if (p != null && p.source == d.source) {
                    Evidence.record("watch_request", JSONObject().put("kind", req!!.kind).put("request_id", req.requestId)
                        .put("trigger", d.trigger).apply {
                            req.why?.let { put("why", it) }
                            req.confidence?.let { put("detector_confidence", it) }
                        })
                    WearProtocol.SosAck(requestId, AckStatus.QUEUED, p.clientId)
                } else {
                    // לא נשמר (למשל אירוע אחר נפתח באותו רגע) - לא מדווחים "נשלח"
                    WearProtocol.SosAck(requestId, if (IncidentManager.isActive) AckStatus.ALREADY_ACTIVE else AckStatus.INVALID)
                }
                log.put(ack)
                ack to (ack.status == AckStatus.QUEUED)
            }
        }
    }

    /** כמו לחיצה על הכפתור בטלפון: startSos רץ ב-main thread (סירנה, מיקום חד-פעמי) */
    private fun onMain(block: () -> Unit) {
        if (Looper.myLooper() == Looper.getMainLooper()) { block(); return }
        val done = CountDownLatch(1)
        Handler(Looper.getMainLooper()).post { try { block() } finally { done.countDown() } }
        done.await(10, TimeUnit.SECONDS)
    }

    companion object {
        private const val TAG = "LifeGuardWear"
        private val lock = Any()
        private val log = WearSosMapper.RequestLog()
    }
}
