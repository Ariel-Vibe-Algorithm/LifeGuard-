package org.lifeguard.wear

import android.content.Context
import android.content.Intent
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import org.lifeguard.app.wearlink.WearProtocol
import org.lifeguard.wear.logic.CancelWindow
import org.lifeguard.wear.logic.FallCountdown
import org.lifeguard.wear.logic.SosDelivery
import org.lifeguard.wear.logic.WristFallEngine
import java.util.UUID
import java.util.concurrent.CopyOnWriteArraySet

/**
 * מצב השעון (תהליך אחד, main thread): חלון הביטול של SOS ידני, "הכל בסדר?" אחרי נפילה, ומצב המסירה לטלפון.
 * הלוגיקה עצמה במכונות המצבים הטהורות (logic/) - כאן רק שעון, רטט, התראות ו-Data Layer.
 */
object WatchController {
    private val main = Handler(Looper.getMainLooper())
    private lateinit var app: Context

    val cancel = CancelWindow()
    val fall = FallCountdown()
    val delivery = SosDelivery()
    /** הבקשה האחרונה - "נסה/י שוב" שולח אותה שוב עם אותו requestId (הטלפון לא יפתח כפול) */
    private var lastRequest: WearProtocol.SosRequest? = null
    /** "אני בסדר" נלחץ - להציג "טוב לשמוע" לרגע */
    var lastFallDismissedAt = 0L
        private set

    private val listeners = CopyOnWriteArraySet<() -> Unit>()
    @Volatile var visibleScreens = 0
    private var ticking = false
    private var shownStatus: SosDelivery.View? = null
    private var shownFallSecond = -1

    fun init(ctx: Context) {
        if (!::app.isInitialized) {
            app = ctx.applicationContext
            WatchNotifier.createChannels(app)
        }
    }

    fun now() = SystemClock.elapsedRealtime()

    /** נשלח ועוד לא ידוע אם הגיע (שולח / ממתין לאישור) */
    val sendInFlight get() = delivery.state is SosDelivery.State.Sending || delivery.state is SosDelivery.State.AwaitingAck

    fun addListener(l: () -> Unit) { listeners += l }
    fun removeListener(l: () -> Unit) { listeners -= l }

    private fun changed() {
        for (l in listeners) l()
        // אין מסך פתוח: המצב מוצג כהתראה (לא "נעלם" בשקט) - רק כשהוא משתנה
        val v = delivery.view()
        if (visibleScreens == 0 && delivery.active && v != shownStatus) WatchNotifier.showStatus(app, v)
        shownStatus = if (delivery.active) v else null
    }

    // ------------------------------------------------------------ SOS ידני
    /** אחרי החזקה של 2 שנ': חלון ביטול, ורק בסופו שליחה */
    fun startCancelWindow(ctx: Context) {
        init(ctx)
        if (cancel.state == CancelWindow.State.COUNTING || sendInFlight) return
        cancel.reset()
        delivery.clear()        // מצב קודם שהסתיים - לא מערבבים עם ה-SOS החדש
        cancel.start(now())
        WakeHold.hold(app, cancel.windowMs + delivery.ackTimeoutMs + 10_000)
        Haptics.heavy(app)
        ensureTicking()
        changed()
    }

    fun cancelSos(): Boolean {
        val ok = cancel.cancel()
        if (ok) { Haptics.click(app); WakeHold.release() }
        changed()
        return ok
    }

    // ------------------------------------------------------------ נפילה
    fun onFallDetected(ctx: Context, ev: WristFallEngine.FallEvent) {
        init(ctx)
        if (!fall.onFall(now(), ev.fall, ev.related)) { changed(); return }
        WakeHold.hold(app, fall.answerMs + delivery.ackTimeoutMs + 15_000)
        Haptics.startAlarm(app)
        WatchNotifier.showFallPrompt(app, fall.secondsLeft(now()))
        // מסך מלא: דרך ה-fullScreenIntent של ההתראה; ניסיון ישיר (מותר רק כשהאפליקציה בחזית)
        runCatching {
            app.startActivity(Intent(app, FallAlertActivity::class.java)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP))
        }
        ensureTicking()
        changed()
    }

    fun answerOk() {
        if (fall.answerOk(now())) {
            lastFallDismissedAt = now()
            Haptics.stop(app)
            WatchNotifier.cancel(app, WatchNotifier.ID_FALL)
            if (!delivery.active) WakeHold.release()
        }
        changed()
    }

    fun answerHelp() {
        fall.answerHelp(now())?.let { escalate(it) } ?: changed()
    }

    private fun escalate(e: FallCountdown.Escalation) {
        Haptics.stop(app)
        WatchNotifier.cancel(app, WatchNotifier.ID_FALL)
        send(WearProtocol.SosRequest(UUID.randomUUID().toString(), WearProtocol.KIND_WATCH_FALL, System.currentTimeMillis(),
            e.why, e.confidence, e.signals))
    }

    // ------------------------------------------------------------ שליחה לטלפון
    private fun send(req: WearProtocol.SosRequest) {
        lastRequest = req
        delivery.begin(req.requestId, req.kind)
        changed()
        transmit(req)
    }

    private fun transmit(req: WearProtocol.SosRequest) {
        WakeHold.hold(app, delivery.ackTimeoutMs + 10_000)
        PhoneLink.send(app, req) { outcome ->
            when (outcome) {
                is PhoneLink.Outcome.Sent -> delivery.onSent(req.requestId, now())
                is PhoneLink.Outcome.NoPhone -> { delivery.onNoPhone(req.requestId, outcome.reason); Haptics.error(app) }
                PhoneLink.Outcome.Failed -> { delivery.onSendFailed(req.requestId); Haptics.error(app) }
            }
            ensureTicking()
            changed()
        }
    }

    fun retry() {
        val req = lastRequest ?: return
        if (req.requestId != delivery.requestId || delivery.retry() == null) return
        changed()
        transmit(req)
    }

    /** מסך הסטטוס נסגר אחרי שהכל הסתיים */
    fun clearDelivery() {
        delivery.clear()
        cancel.reset()
        WatchNotifier.cancel(app, WatchNotifier.ID_STATUS)
        changed()
    }

    fun onAck(ctx: Context, ack: WearProtocol.SosAck) {
        init(ctx)
        if (!delivery.onAck(ack)) return
        if (ack.status == WearProtocol.AckStatus.QUEUED) Haptics.confirm(app) else Haptics.error(app)
        WakeHold.release()
        changed()
    }

    fun onStatus(ctx: Context, st: WearProtocol.SosStatus) {
        init(ctx)
        if (delivery.onStatus(st)) changed()
    }

    // ------------------------------------------------------------ שעון פנימי
    private fun needsTicking() = cancel.state == CancelWindow.State.COUNTING || fall.asking != null ||
        delivery.state is SosDelivery.State.AwaitingAck

    private fun ensureTicking() {
        if (ticking) return
        ticking = true
        main.post(ticker)
    }

    private val ticker = object : Runnable {
        override fun run() {
            val t = now()
            if (cancel.secondChanged(t)) Haptics.tick(app)
            if (cancel.tick(t)) {
                send(WearProtocol.SosRequest(UUID.randomUUID().toString(), WearProtocol.KIND_MANUAL, System.currentTimeMillis()))
            }
            fall.tick(t)?.let { escalate(it) }
            if (fall.asking != null && fall.secondsLeft(t) != shownFallSecond) {
                shownFallSecond = fall.secondsLeft(t)
                WatchNotifier.showFallPrompt(app, shownFallSecond)
            }
            val before = delivery.state
            delivery.tick(t)
            if (before !== delivery.state && delivery.state is SosDelivery.State.AckTimeout) Haptics.error(app)
            changed()
            if (needsTicking()) main.postDelayed(this, 250) else ticking = false
        }
    }
}
