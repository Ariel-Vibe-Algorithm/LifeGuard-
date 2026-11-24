package org.lifeguard.app.service

import android.app.Service
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import android.os.PowerManager
import android.os.SystemClock
import androidx.core.app.ServiceCompat
import androidx.core.content.ContextCompat
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeoutOrNull
import org.lifeguard.app.LG
import org.lifeguard.app.R
import org.lifeguard.app.UiEvent
import org.lifeguard.app.alert.Notifier
import org.lifeguard.app.api.ApiException
import org.lifeguard.app.core.MotionSignal
import org.lifeguard.app.core.RecordingGate
import org.lifeguard.app.core.WellnessMonitor
import org.lifeguard.app.hazard.HazardRuntime
import java.io.IOException
import java.util.concurrent.atomic.AtomicBoolean
import org.lifeguard.app.fcm.FcmManager

/**
 * שירות חזית "LifeGuard מחובר":
 * - long-poll להתראות (/v1/notifications?wait=25), אישור קבלה (ACK) לכל התראה, והפעלת מכונת הצלצול
 * - outbox של SOS, מיקום חי באירוע (15 שנ'), מיקום ליווי (60 שנ'), נוכחות גסה (5 דק')
 * - זיהוי נפילה / עצירה פתאומית / אין קליטה בליווי -> "הכל בסדר?"
 * - "זיהוי סכנה ברקע" (רק אם הופעל במפורש בהגדרות): HazardListener על אותו שירות (סוג microphone) - לא מקליט שני
 * ממשיך לרוץ כשסוגרים את האפליקציה; מופעל מחדש אחרי אתחול (BootReceiver).
 */
class LifeGuardService : Service() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val kick = Channel<Unit>(Channel.CONFLATED)
    /** מעיר רק את לולאת ה-poll מה-backoff (kick משותף ל-tick ול-poll, ואחד מהם "גונב" אותו) */
    private val pollKick = Channel<Unit>(Channel.CONFLATED)
    /** השכמת FCM ביטלה את ה-long-poll - ה-IOException שיגיע הוא מכוון */
    private val wakeRequested = AtomicBoolean(false)
    private lateinit var location: LocationTracker
    private lateinit var fall: MotionSensor
    private lateinit var audio: AudioEvidenceRecorder
    /** זיהוי סכנה ברקע (מיקרופון -> YAMNet במכשיר). מושהה כשמקליט הראיות של ה-SOS צריך את המיקרופון. */
    private lateinit var hazard: HazardListener
    private var hazardRetryAt = 0L
    /** השירות רץ כרגע עם סוג microphone (API 30+) */
    private var micForeground = false
    private var micBlockedReason: String? = null
    @Volatile private var started = false
    private var lastPresence = 0.0
    private var lastStatusText: String? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        running = true
        instance = this
        location = LocationTracker(this)
        fall = MotionSensor(this)
        audio = AudioEvidenceRecorder(this)
        hazard = HazardListener(this, onWindow = { w, now -> HazardRuntime.monitor.process(w, now) },
            onDigitalSilence = { silent ->
                HazardRuntime.listenStatus = if (silent) HazardRuntime.STATUS_SILENT_MIC else HazardRuntime.STATUS_ACTIVE
            })
        runCatching { audio.recoverLeftovers() }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        // startCommand מגיע בדרך כלל מהאפליקציה הפתוחה (למשל הפעלת הזיהוי בהגדרות) - הזמן שבו אנדרואיד מתיר
        // לשירות לקבל את סוג ה-microphone. ננסה מיד (בלי להמתין לניסיון החוזר).
        hazardRetryAt = 0L
        startInForeground(keepMic = (micForeground && (audio.isRecording || hazard.isRunning)) || hazardWanted())
        if (!LG.store.isLoggedIn) { stopSelf(); return START_NOT_STICKY }
        if (intent?.action == ACTION_STOP) { stopSelf(); return START_NOT_STICKY }
        if (!started) {
            started = true
            scope.launch { pollLoop() }
            scope.launch { tickLoop() }
            scope.launch { IncidentManager.verifyActive(); EscortManager.sync() }
        }
        applySensors()
        if (intent?.action == ACTION_WAKE) wakeNow(intent.getLongExtra(EXTRA_WAKELOCK_MS, 0L))
        kick.trySend(Unit)
        pollKick.trySend(Unit)
        return START_STICKY
    }

    /**
     * השכמה מ-FCM: חיבור ה-long-poll אולי מת בזמן Doze (ה-poll היה נתקע עד ה-timeout) -> מבטלים אותו והלולאה מושכת
     * מיד. wake lock קצר (עם timeout) כדי שהמעבד לא יירדם לפני שההתראה מטופלת. לא מציגים כלום מההודעה עצמה.
     */
    fun wakeNow(wakeLockMs: Long) {
        if (wakeLockMs > 0) runCatching {
            getSystemService(PowerManager::class.java)?.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "lifeguard:fcm_wake")
                ?.apply { setReferenceCounted(false) }?.acquire(wakeLockMs)
        }
        if (started) {
            wakeRequested.set(true)
            runCatching { LG.api().cancelPoll() }
        }
        kick.trySend(Unit)
        pollKick.trySend(Unit)
    }

    override fun onDestroy() {
        running = false
        if (instance === this) instance = null
        scope.cancel()
        location.stop()
        fall.setEnabled(false)
        runCatching { audio.stop() }
        runCatching { hazard.stop() }
        super.onDestroy()
    }

    /** keepMic: להשאיר את סוג ה-microphone (הקלטה / זיהוי סכנה פעילים) כשהשירות מקבל startCommand נוסף. */
    private fun startInForeground(keepMic: Boolean = micForeground && (audio.isRecording || hazard.isRunning)) {
        val n = Notifier.serviceNotification(this, lastStatusText ?: getString(R.string.service_connected_text))
        val mic = if (keepMic && Build.VERSION.SDK_INT >= 30) ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE else 0
        micForeground = false
        if (Build.VERSION.SDK_INT >= 34) {
            val withLocation = hasLocationPermission(this)
            try {
                ServiceCompat.startForeground(this, Notifier.ID_SERVICE, n,
                    ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE or mic or
                        (if (withLocation) ServiceInfo.FOREGROUND_SERVICE_TYPE_LOCATION else 0))
                micForeground = mic != 0
            } catch (e: Exception) {
                // מיקום/מיקרופון ברקע אסורים כרגע (למשל הופעל מאתחול בלי הרשאת רקע) - ממשיכים בלי
                // (קודם בלי המיקרופון בלבד, כדי לא לאבד גם את סוג המיקום)
                val ok = mic != 0 && withLocation && runCatching {
                    ServiceCompat.startForeground(this, Notifier.ID_SERVICE, n,
                        ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE or ServiceInfo.FOREGROUND_SERVICE_TYPE_LOCATION)
                }.isSuccess
                if (!ok) ServiceCompat.startForeground(this, Notifier.ID_SERVICE, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE)
            }
        } else if (Build.VERSION.SDK_INT >= 29) {
            val type = (if (hasLocationPermission(this)) ServiceInfo.FOREGROUND_SERVICE_TYPE_LOCATION else 0) or mic
            runCatching { ServiceCompat.startForeground(this, Notifier.ID_SERVICE, n, type); micForeground = mic != 0 }
                .onFailure { ServiceCompat.startForeground(this, Notifier.ID_SERVICE, n, 0) }
        } else {
            startForeground(Notifier.ID_SERVICE, n)
        }
    }

    /**
     * תיעוד שמע בזמן SOS: דורש הגדרה + הרשאת מיקרופון + (API 30+) שירות חזית מסוג microphone.
     * אנדרואיד חוסם מיקרופון לשירות שלא הופעל כשהאפליקציה בחזית (למשל SOS מ"הכל בסדר?" כשהמסך כבוי) -
     * אז לא מקליטים ונרשם ביומן למה.
     */
    private fun updateAudio() {
        // רק SOS ידני / חירום מאושר + מדיניות השרת (record_on_emergency או discreet). אין שום מסלול אחר למיקרופון.
        val decision = RecordingGate.decide(IncidentManager.isActive && Evidence.currentKey != null,
            IncidentManager.emergencyConfirmed(), PrivacySync.cachedPolicy, audio.hasPermission())
        val want = decision == RecordingGate.Decision.RECORD || decision == RecordingGate.Decision.NO_PERMISSION
        if (!want) {
            if (audio.isRecording) audio.stop()
            micBlockedReason = null
            updateHazard()
            if (micForeground && !hazard.isRunning) startInForeground(keepMic = false)   // מורידים את סוג ה-microphone
            return
        }
        // תיעוד שמע ב-SOS: מקליט הראיות לוקח את המיקרופון. הזיהוי מושהה (ממילא אין חלונות סכנה בזמן SOS פעיל).
        if (hazard.isRunning) {
            hazard.stop()
            HazardRuntime.listenStatus = HazardRuntime.STATUS_PAUSED_SOS
        }
        val reason = when {
            !audio.hasPermission() -> "אין הרשאת מיקרופון"
            else -> ensureMicForeground()
        }
        audio.update(true, Evidence.currentKey, reason)
    }

    /** null = מותר להקליט; אחרת הסיבה. */
    private fun ensureMicForeground(): String? {
        if (Build.VERSION.SDK_INT < 30 || micForeground) return null
        micBlockedReason?.let { return it }
        val e = tryMicForeground() ?: return null
        return ("אנדרואיד לא מאפשר מיקרופון לשירות ברקע כרגע (${e.javaClass.simpleName}). " +
            "ההקלטה מתאפשרת כשה-SOS מופעל מתוך האפליקציה הפתוחה").also { micBlockedReason = it }
    }

    /** מוסיף לשירות את סוג ה-microphone. null = הצליח; אחרת החריגה (אנדרואיד 14+: מותר רק כשהאפליקציה בחזית). */
    private fun tryMicForeground(): Exception? {
        if (Build.VERSION.SDK_INT < 30 || micForeground) return null
        val n = Notifier.serviceNotification(this, lastStatusText ?: getString(R.string.service_connected_text))
        val base = ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE.takeIf { Build.VERSION.SDK_INT >= 34 } ?: 0
        val loc = if (hasLocationPermission(this)) ServiceInfo.FOREGROUND_SERVICE_TYPE_LOCATION else 0
        return try {
            ServiceCompat.startForeground(this, Notifier.ID_SERVICE, n, base or loc or ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE)
            micForeground = true
            null
        } catch (e: Exception) {
            runCatching { ServiceCompat.startForeground(this, Notifier.ID_SERVICE, n, base or loc) }
                .onFailure { runCatching { ServiceCompat.startForeground(this, Notifier.ID_SERVICE, n, base) } }
            e
        }
    }

    // ------------------------------------------------------------ זיהוי סכנה ברקע
    /** הופעל במפורש בהגדרות ("זיהוי סכנה ברקע", ברירת מחדל כבוי) + יש הרשאת מיקרופון */
    private fun hazardWanted(): Boolean =
        LG.store.isLoggedIn && runCatching { HazardRuntime.prefs().background }.getOrDefault(false) && hazard.hasPermission()

    /**
     * מפעיל / עוצר את המאזין לפי ההגדרה, ההרשאה, זמינות המודל ומה שאנדרואיד מתיר. כל מצב נאמר בגלוי בהגדרות
     * (HazardRuntime.listenStatus) - לא "פעיל" כשבפועל לא.
     */
    private fun updateHazard() {
        val enabled = runCatching { HazardRuntime.prefs().background }.getOrDefault(false)
        val status: String? = when {
            !enabled -> HazardRuntime.STATUS_OFF
            !LG.store.isLoggedIn -> HazardRuntime.STATUS_NOT_LOGGED_IN
            !hazard.hasPermission() -> HazardRuntime.STATUS_NO_PERMISSION
            else -> HazardRuntime.engineUnavailableReason(this)
        }
        if (status != null) {
            if (hazard.isRunning) hazard.stop()
            HazardRuntime.listenStatus = status
            return
        }
        if (hazard.isRunning) return
        val now = SystemClock.elapsedRealtime()
        if (now < hazardRetryAt) return
        tryMicForeground()?.let { e ->
            hazardRetryAt = now + 60_000
            HazardRuntime.listenStatus = "הזיהוי ברקע ממתין: אנדרואיד מתיר מיקרופון לשירות רק אחרי פתיחת האפליקציה " +
                "(${e.javaClass.simpleName}). פתחו את LifeGuard פעם אחת."
            return
        }
        val err = hazard.start()
        if (err != null) {
            hazardRetryAt = now + 60_000
            HazardRuntime.listenStatus = "הזיהוי ברקע לא פעיל: $err"
            return
        }
        HazardRuntime.listenStatus = HazardRuntime.STATUS_ACTIVE
    }

    private fun applySensors() {
        val needGps = IncidentManager.isActive || EscortManager.activeId != null || LG.store.motionMonitoring
        location.update(highAccuracy = needGps)
        fall.setEnabled(LG.store.fallDetection)
    }

    // ------------------------------------------------------------ long-poll
    private suspend fun pollLoop() {
        var backoff = 1L
        while (scope.isActive) {
            if (!LG.store.isLoggedIn) { stopSelf(); return }
            try {
                val api = LG.api()
                // התקנה חדשה / התחברות: לא "משחזרים" היסטוריה - רק מה שעוד לא התקבל באף מכשיר
                NotificationHandler.initCursorIfNeeded(this, api)
                val list = api.poll(LG.ring.lastId())
                wakeRequested.set(false)
                LG.connectivity.onSuccess(LG.nowSec())
                NotificationHandler.handle(this, list, backlog = false)
                NotificationHandler.saveCursor()
                backoff = 1
            } catch (e: ApiException) {
                if (e.isAuth) { onAuthLost(); return }
                LG.connectivity.onFailure(LG.nowSec())
                delay(backoff * 1000); backoff = minOf(backoff * 2, 30)
            } catch (e: IOException) {
                // השכמת FCM ביטלה את ה-long-poll בכוונה (חיבור שאולי מת ב-Doze) -> מושכים מחדש מיד, לא נחשב כשל
                if (wakeRequested.getAndSet(false)) { backoff = 1; continue }
                LG.connectivity.onFailure(LG.nowSec())
                withTimeoutOrNull(backoff * 1000) { pollKick.receive() }
                backoff = minOf(backoff * 2, 30)
            } catch (e: IllegalStateException) {
                delay(5000)   // כתובת שרת לא תקינה
            }
        }
    }

    // ------------------------------------------------------------ tick
    private suspend fun tickLoop() {
        var n = 0L
        while (scope.isActive) {
            withTimeoutOrNull(1000) { kick.receive() }
            n++
            try {
                LG.wellness.tick()
                expireEscortCheck()
                IncidentManager.processOutbox(this)
                IncidentManager.sendLiveLocation()
                Evidence.locationTick()
                updateAudio()
                EscortManager.tick()
                if (n % 10 == 0L) {
                    IncidentManager.processPendingResolve()
                    IncidentManager.processPendingConfirm()
                    NotificationHandler.flushAcks()
                    val online = LG.isOnline() && LG.networkAvailable(this)
                    PrivacySync.tick(this, online)
                    FcmManager.tick(online)                // רישום טוקן ההשכמה שממתין (backoff)
                    if (LG.wellness.onConnectivity(online, watching = EscortManager.activeId != null)) {
                        // "אין קליטה" נשאל לבד (התנהגות קיימת בליווי) - ונשמר גם כאות להערכת הסיכון
                        LG.motion.addExternal(MotionSignal("no_signal", 1.0, System.currentTimeMillis(),
                            "אין קשר לשרת ${(WellnessMonitor.SIGNAL_LOSS_SECONDS / 60).toInt()} דק' בזמן ליווי"))
                    }
                    applySensors()
                    updateStatus(online)
                }
                maybePresence()
            } catch (e: ApiException) {
                if (e.isAuth) { onAuthLost(); return }
            } catch (_: IOException) {
            } catch (_: IllegalStateException) {
            }
        }
    }

    /** עבר respond_by: השרת מטפל בהסלמה; כאן רק מנקים את המסך והצפצוף. */
    private fun expireEscortCheck() {
        val c = LG.escortCheck ?: return
        val by = c.respondBy ?: return
        if (LG.api().serverNow() < by + 5) return
        LG.escortCheck = null
        LG.alarm.stopBeepIfAny()
        Notifier.cancel(this, Notifier.ID_ESCORT_CHECK)
        LG.emit(UiEvent.StateChanged)
    }

    /** נוכחות גסה כל 5 דקות - כדי שהשרת יצלצל קודם לאיש הקשר הקרוב. */
    private fun maybePresence() {
        val now = LG.nowSec()
        if (now - lastPresence < PRESENCE_SECONDS) return
        val fix = LG.lastFix ?: location.lastKnown()?.toGeo() ?: return
        lastPresence = now
        try {
            LG.api().putPresence(fix.copy(isPrecise = false))
            LG.connectivity.onSuccess(LG.nowSec())
        } catch (_: IOException) {
            lastPresence = now - PRESENCE_SECONDS + 30   // ננסה שוב בעוד 30 שנ'
        }
    }

    private fun updateStatus(online: Boolean) {
        val text = when {
            !online -> getString(R.string.service_offline_text)
            // במצוקה שקטה ההתראה הקבועה לא משתנה - שום דבר מבהיל על המסך
            IncidentManager.isActive && IncidentManager.activeMode == "loud" -> getString(R.string.service_sos_active)
            else -> getString(R.string.service_connected_text)
        }
        if (text != lastStatusText) {
            lastStatusText = text
            Notifier.updateService(this, text)
            LG.emit(UiEvent.StateChanged)
        }
    }

    private fun onAuthLost() {
        FcmManager.onLogout()
        LG.store.clearSession()
        LG.emit(UiEvent.LoggedOut)
        stopSelf()
    }

    companion object {
        const val ACTION_STOP = "org.lifeguard.app.STOP"
        const val ACTION_WAKE = "org.lifeguard.app.WAKE"
        const val EXTRA_WAKELOCK_MS = "wakelock_ms"
        const val PRESENCE_SECONDS = 300.0

        /** השירות חי בתהליך הזה (לבדיקת המוכנות) */
        @Volatile var running = false
            private set

        /** השירות הפעיל בתהליך (להשכמת FCM בלי IPC) */
        @Volatile var instance: LifeGuardService? = null
            private set

        /**
         * הפעלה מהשכמת FCM. מחזיר את החריגה אם אנדרואיד סירב (ForegroundServiceStartNotAllowedException ב-12+ -
         * יורשת IllegalStateException), כדי שהקורא יעבור למשיכה חד-פעמית דרך WorkManager.
         */
        fun startForWake(ctx: Context, wakeLockMs: Long): Exception? {
            if (!LG.store.isLoggedIn) return null
            return try {
                ContextCompat.startForegroundService(ctx, Intent(ctx, LifeGuardService::class.java).setAction(ACTION_WAKE)
                    .putExtra(EXTRA_WAKELOCK_MS, wakeLockMs))
                null
            } catch (e: IllegalStateException) {
                e
            } catch (e: SecurityException) {
                e
            }
        }

        fun start(ctx: Context, kick: Boolean = false) {
            if (!LG.store.isLoggedIn) return
            runCatching { ContextCompat.startForegroundService(ctx, Intent(ctx, LifeGuardService::class.java)) }
        }

        fun stop(ctx: Context) {
            ctx.stopService(Intent(ctx, LifeGuardService::class.java))
        }
    }
}

/** הפעלה מחדש אחרי אתחול / עדכון אפליקציה. */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        when (intent.action) {
            Intent.ACTION_BOOT_COMPLETED, Intent.ACTION_MY_PACKAGE_REPLACED ->
                LifeGuardService.start(context)
        }
    }
}
