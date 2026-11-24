package org.lifeguard.app.service

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
import android.location.Location
import android.location.LocationListener
import android.location.LocationManager
import android.os.Build
import android.os.Looper
import androidx.core.content.ContextCompat
import org.lifeguard.app.LG
import org.lifeguard.app.api.GeoPoint
import android.os.SystemClock

fun hasLocationPermission(ctx: Context) =
    ContextCompat.checkSelfPermission(ctx, Manifest.permission.ACCESS_FINE_LOCATION) == PackageManager.PERMISSION_GRANTED ||
        ContextCompat.checkSelfPermission(ctx, Manifest.permission.ACCESS_COARSE_LOCATION) == PackageManager.PERMISSION_GRANTED

fun hasFineLocation(ctx: Context) =
    ContextCompat.checkSelfPermission(ctx, Manifest.permission.ACCESS_FINE_LOCATION) == PackageManager.PERMISSION_GRANTED

fun Location.toGeo(): GeoPoint = GeoPoint(
    lat = latitude, lon = longitude,
    accuracyM = if (hasAccuracy()) accuracy.toDouble() else null,
    isPrecise = provider == LocationManager.GPS_PROVIDER || (hasAccuracy() && accuracy <= 50f),
    source = "android_${provider ?: "unknown"}",
    capturedAt = time / 1000.0,
    speedMps = if (hasSpeed()) speed.toDouble() else null,
)

/**
 * מיקום דרך LocationManager של המערכת (עובד גם בלי שירותי Google).
 * - PASSIVE תמיד (חינם: מיקומים שאפליקציות אחרות ביקשו), NETWORK כל דקה (גס - לנוכחות)
 * - GPS כל 5 שנ' רק כשצריך: אירוע פעיל, ליווי פעיל, או "זיהוי עצירה פתאומית" מופעל בהגדרות
 * מיקומי GPS מוזנים לזיהוי העצירה הפתאומית (WellnessMonitor.onLocation).
 */
class LocationTracker(private val ctx: Context) : LocationListener {
    private val lm = ctx.getSystemService(LocationManager::class.java)
    private var gpsOn = false
    private var baseOn = false

    @Synchronized
    fun update(highAccuracy: Boolean) {
        if (!hasLocationPermission(ctx)) return
        try {
            if (!baseOn) {
                for (p in listOf(LocationManager.PASSIVE_PROVIDER, LocationManager.NETWORK_PROVIDER)) {
                    if (lm.allProviders.contains(p)) {
                        lm.requestLocationUpdates(p, if (p == LocationManager.PASSIVE_PROVIDER) 5_000L else 60_000L,
                            0f, this, Looper.getMainLooper())
                    }
                }
                baseOn = true
                lastKnown()?.let { accept(it) }
            }
            val wantGps = highAccuracy && hasFineLocation(ctx) && lm.allProviders.contains(LocationManager.GPS_PROVIDER)
            if (wantGps && !gpsOn) {
                lm.requestLocationUpdates(LocationManager.GPS_PROVIDER, 5_000L, 0f, this, Looper.getMainLooper())
                gpsOn = true
            } else if (!wantGps && gpsOn) {
                // אין API להסיר ספק אחד - מסירים הכל ונרשמים מחדש לבסיס
                lm.removeUpdates(this); gpsOn = false; baseOn = false
                update(false)
            }
        } catch (e: SecurityException) {
            // ההרשאה בוטלה בזמן ריצה
        }
    }

    @Synchronized
    fun stop() {
        runCatching { lm.removeUpdates(this) }
        gpsOn = false; baseOn = false
    }

    fun lastKnown(): Location? {
        if (!hasLocationPermission(ctx)) return null
        return try {
            lm.allProviders.mapNotNull { runCatching { lm.getLastKnownLocation(it) }.getOrNull() }
                .maxByOrNull { it.time }
        } catch (e: SecurityException) { null }
    }

    override fun onLocationChanged(location: Location) = accept(location)

    private fun accept(l: Location) {
        val g = l.toGeo()
        val prev = LG.lastFix
        // לא מחליפים מיקום GPS טרי במיקום רשת גס
        if (prev == null || (g.capturedAt ?: 0.0) >= (prev.capturedAt ?: 0.0) + (if (g.isPrecise || !prev.isPrecise) 0.0 else 60.0)) {
            LG.lastFix = g
        }
        if (l.provider == LocationManager.GPS_PROVIDER) {
            // עצירה פתאומית היא עכשיו אות אחד במיזוג החיישנים (לבד היא לא שואלת "הכל בסדר?")
            LG.motion.onLocation(l.time / 1000.0, l.latitude, l.longitude)?.let { LG.onMotionPrompt(it) }
        }
    }

    @Deprecated("Deprecated in Java")
    override fun onStatusChanged(provider: String?, status: Int, extras: android.os.Bundle?) {}
    override fun onProviderEnabled(provider: String) {}
    override fun onProviderDisabled(provider: String) {}

    companion object {
        /** מיקום חד-פעמי מהיר ל-SOS (עד timeoutMs), אחרת המיקום האחרון הידוע. */
        fun currentOnce(ctx: Context, timeoutMs: Long = 4000, cb: (GeoPoint?) -> Unit) {
            val lm = ctx.getSystemService(LocationManager::class.java)
            if (!hasLocationPermission(ctx)) { cb(LG.lastFix); return }
            val provider = when {
                hasFineLocation(ctx) && lm.isProviderEnabled(LocationManager.GPS_PROVIDER) -> LocationManager.GPS_PROVIDER
                lm.isProviderEnabled(LocationManager.NETWORK_PROVIDER) -> LocationManager.NETWORK_PROVIDER
                else -> null
            }
            if (provider == null || Build.VERSION.SDK_INT < 30) { cb(LG.lastFix); return }
            var done = false
            val main = android.os.Handler(Looper.getMainLooper())
            val signal = android.os.CancellationSignal()
            main.postDelayed({ if (!done) { done = true; signal.cancel(); cb(LG.lastFix) } }, timeoutMs)
            try {
                lm.getCurrentLocation(provider, signal, ctx.mainExecutor) { loc ->
                    if (!done) {
                        done = true
                        loc?.let { LG.lastFix = it.toGeo() }
                        cb(loc?.toGeo() ?: LG.lastFix)
                    }
                }
            } catch (e: SecurityException) {
                if (!done) { done = true; cb(LG.lastFix) }
            }
        }
    }
}

/**
 * חיישן תאוצה + ג'ירוסקופ (אם קיים) -> MotionFusion -> "הכל בסדר?" רק כשיש ≥2 אותות מאששים.
 * זמני האירועים מומרים ל-epoch (SensorEvent.timestamp הוא זמן מאתחול) כדי שה-"at" באותות יהיה זמן אמיתי.
 */
class MotionSensor(ctx: Context) : SensorEventListener {
    private val sm = ctx.getSystemService(SensorManager::class.java)
    private var on = false
    @Volatile private var epochOffsetMs = 0L
    private var offsetAt = 0L

    @Synchronized
    fun setEnabled(enabled: Boolean) {
        if (enabled == on) return
        if (enabled) {
            val acc = sm.getDefaultSensor(Sensor.TYPE_ACCELEROMETER) ?: return
            val gyro = sm.getDefaultSensor(Sensor.TYPE_GYROSCOPE)
            LG.motion.reset()
            LG.motion.gyroAvailable = gyro != null && sm.registerListener(this, gyro, SensorManager.SENSOR_DELAY_GAME)
            updateOffset()
            on = sm.registerListener(this, acc, SensorManager.SENSOR_DELAY_GAME)
            if (!on) sm.unregisterListener(this)
        } else {
            sm.unregisterListener(this); on = false; LG.motion.reset()
        }
    }

    private fun updateOffset() {
        epochOffsetMs = System.currentTimeMillis() - SystemClock.elapsedRealtime()
        offsetAt = SystemClock.elapsedRealtime()
    }

    override fun onSensorChanged(e: SensorEvent) {
        if (SystemClock.elapsedRealtime() - offsetAt > 60_000) updateOffset()   // שינוי שעון ידני / סנכרון רשת
        val t = epochOffsetMs + e.timestamp / 1_000_000L
        when (e.sensor.type) {
            Sensor.TYPE_GYROSCOPE -> LG.motion.onGyro(t, e.values[0], e.values[1], e.values[2])
            Sensor.TYPE_ACCELEROMETER ->
                LG.motion.onAccel(t, e.values[0], e.values[1], e.values[2])?.let { LG.onMotionPrompt(it) }
        }
    }

    override fun onAccuracyChanged(sensor: Sensor?, accuracy: Int) {}
}
