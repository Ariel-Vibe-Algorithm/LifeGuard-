package org.lifeguard.wear

import android.annotation.SuppressLint
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
import android.os.Build
import android.os.Handler
import android.os.HandlerThread
import android.os.IBinder
import android.os.Looper
import android.os.PowerManager
import android.os.SystemClock
import android.util.Log
import androidx.core.app.ServiceCompat
import androidx.core.content.ContextCompat
import androidx.wear.ongoing.OngoingActivity
import androidx.wear.ongoing.Status
import org.lifeguard.wear.logic.WristFallEngine

/**
 * זיהוי נפילה על פרק היד: שירות חזית (type=health) שרץ **רק** כשהמתג "זיהוי נפילה" מופעל (ברירת מחדל: מופעל).
 * חיישן תאוצה + ג'ירוסקופ ב-50Hz -> WristFallEngine (אותו MotionFusion/FallDetector כמו בטלפון) -> "הכל בסדר?".
 *
 * סוללה: מעדיפים חיישנים "מעוררים" (wake-up) עם אצווה של 4 שנ' - המעבד ישן בין האצוות והאירועים לא הולכים לאיבוד.
 * אם אין חיישן מעורר: חיישן רגיל + PARTIAL_WAKE_LOCK כל זמן שהניטור פעיל (אחרת דגימות נזרקות בשינה) - יקר יותר בסוללה,
 * ומוצג כך במסך הראשי. כיבוי המתג = unregister + שחרור ה-wake lock + עצירת השירות.
 */
class FallMonitorService : Service(), SensorEventListener {
    private lateinit var sm: SensorManager
    private var thread: HandlerThread? = null
    private var engine: WristFallEngine? = null
    private var wakeLock: PowerManager.WakeLock? = null
    @Volatile private var epochOffsetMs = 0L
    private var offsetAt = 0L
    private val main = Handler(Looper.getMainLooper())

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        WatchController.init(this)
        sm = getSystemService(SensorManager::class.java)
        if (!goForeground()) { stopSelf(); return }
        running = startSensors()
        if (!running) stopSelf()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (!running || !WatchPrefs.fallEnabled(this)) { stopSelf(); return START_NOT_STICKY }
        return START_STICKY
    }

    private fun goForeground(): Boolean {
        val b = WatchNotifier.monitorBuilder(this)
        val touch = PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
        runCatching {
            OngoingActivity.Builder(applicationContext, WatchNotifier.ID_MONITOR, b)
                .setStaticIcon(R.drawable.ic_shield)
                .setTouchIntent(touch)
                .setStatus(Status.Builder().addTemplate(getString(R.string.monitor_text)).build())
                .build()
                .apply(applicationContext)
        }
        return try {
            ServiceCompat.startForeground(this, WatchNotifier.ID_MONITOR, b.build(),
                if (Build.VERSION.SDK_INT >= 34) ServiceInfo.FOREGROUND_SERVICE_TYPE_HEALTH else 0)
            true
        } catch (e: Exception) {
            // למשל ForegroundServiceStartNotAllowedException (הופעל מהרקע) - לא מעמידים פנים שהניטור פעיל
            Log.w(TAG, "foreground not allowed: ${e.javaClass.simpleName}")
            WatchPrefs.setMonitorMode(this, null)
            false
        }
    }

    @SuppressLint("WakelockTimeout")
    private fun startSensors(): Boolean {
        val accWake = sm.getDefaultSensor(Sensor.TYPE_ACCELEROMETER, true)
        val acc = accWake ?: sm.getDefaultSensor(Sensor.TYPE_ACCELEROMETER)
        if (acc == null) {
            WatchPrefs.setMonitorMode(this, WatchPrefs.MODE_NO_SENSOR)
            return false
        }
        val wakeUp = acc.isWakeUpSensor
        val gyro = (if (wakeUp) sm.getDefaultSensor(Sensor.TYPE_GYROSCOPE, true) else null)
            ?: sm.getDefaultSensor(Sensor.TYPE_GYROSCOPE)
        val t = HandlerThread("lifeguard-fall").also { it.start() }
        thread = t
        val h = Handler(t.looper)
        updateOffset()
        // אצווה רק עם חיישן מעורר (המעבד מתעורר כשה-FIFO מתמלא); חיישן רגיל - wake lock והעברה מיידית
        val latencyUs = if (wakeUp) BATCH_LATENCY_US else 0
        var gyroOn = false
        if (gyro != null) gyroOn = sm.registerListener(this, gyro, SAMPLING_US, latencyUs, h)
        engine = WristFallEngine(gyroAvailable = gyroOn)
        if (!sm.registerListener(this, acc, SAMPLING_US, latencyUs, h)) {
            sm.unregisterListener(this)
            WatchPrefs.setMonitorMode(this, WatchPrefs.MODE_NO_SENSOR)
            return false
        }
        if (!wakeUp) {
            wakeLock = getSystemService(PowerManager::class.java)
                ?.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "LifeGuard:fall-monitor")?.apply { acquire() }
        }
        WatchPrefs.setMonitorMode(this, if (wakeUp) WatchPrefs.MODE_WAKEUP else WatchPrefs.MODE_WAKELOCK)
        Log.i(TAG, "fall monitor on: wakeUp=$wakeUp gyro=$gyroOn")
        return true
    }

    private fun updateOffset() {
        epochOffsetMs = System.currentTimeMillis() - SystemClock.elapsedRealtime()
        offsetAt = SystemClock.elapsedRealtime()
    }

    override fun onSensorChanged(e: SensorEvent) {
        val eng = engine ?: return
        if (SystemClock.elapsedRealtime() - offsetAt > 60_000) updateOffset()
        // SensorEvent.timestamp = elapsedRealtimeNanos (גם באצווה - זמן הדגימה האמיתי) -> epoch, כמו בטלפון
        val t = epochOffsetMs + e.timestamp / 1_000_000L
        val events = when (e.sensor.type) {
            Sensor.TYPE_GYROSCOPE -> eng.onGyro(t, e.values[0], e.values[1], e.values[2])
            Sensor.TYPE_ACCELEROMETER -> eng.onAccel(t, e.values[0], e.values[1], e.values[2])
            else -> emptyList()
        }
        for (ev in events) main.post { WatchController.onFallDetected(this, ev) }
    }

    override fun onAccuracyChanged(sensor: Sensor?, accuracy: Int) {}

    override fun onDestroy() {
        running = false
        runCatching { sm.unregisterListener(this) }
        runCatching { wakeLock?.takeIf { it.isHeld }?.release() }
        wakeLock = null
        thread?.quitSafely()
        engine = null
        if (!WatchPrefs.fallEnabled(this) && WatchPrefs.monitorMode(this) != WatchPrefs.MODE_NO_SENSOR) WatchPrefs.setMonitorMode(this, null)
        super.onDestroy()
    }

    companion object {
        private const val TAG = "LifeGuardWatch"
        /** 50Hz - כמו בטלפון (SENSOR_DELAY_GAME); האלגוריתם והבדיקות מכוילים לקצב הזה */
        const val SAMPLING_US = 20_000
        const val BATCH_LATENCY_US = 4_000_000

        @Volatile var running = false
            private set

        /** מפעיל/עוצר לפי ההגדרה. מהממשק (חזית) או מ-BOOT_COMPLETED (מותר ל-health). */
        fun sync(ctx: Context) {
            val i = Intent(ctx, FallMonitorService::class.java)
            if (WatchPrefs.fallEnabled(ctx)) {
                runCatching { ContextCompat.startForegroundService(ctx, i) }
                    .onFailure { Log.w(TAG, "cannot start fall monitor: ${it.javaClass.simpleName}") }
            } else {
                ctx.stopService(i)
                WatchPrefs.setMonitorMode(ctx, null)
            }
        }
    }
}
