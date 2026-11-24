package org.lifeguard.app.service

import android.Manifest
import android.app.ActivityManager
import android.app.NotificationManager
import android.content.Context
import android.content.pm.PackageManager
import android.hardware.Sensor
import android.hardware.SensorManager
import android.location.LocationManager
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.os.Build
import android.os.PowerManager
import android.os.SystemClock
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import org.lifeguard.app.LG
import org.lifeguard.app.alert.Notifier
import org.lifeguard.app.api.ApiException
import org.lifeguard.app.core.AuthState
import org.lifeguard.app.core.ChannelState
import org.lifeguard.app.core.DiagnosticsSnapshot
import org.lifeguard.app.core.LocationPerm
import org.lifeguard.app.core.ServerProbe
import org.lifeguard.app.core.SetupProbe
import org.lifeguard.app.fcm.FcmManager
import java.io.IOException

/**
 * איסוף תמונת המצב לבדיקת המוכנות (שכבת Android דקה; ההערכה עצמה ב-DiagnosticsEvaluator).
 * בדיקות שרת (health / me / me/setup) נשמרות במטמון כדי שהשורה במסך הראשי לא תפנה לשרת בכל רענון.
 */
object DiagnosticsCollector {
    data class Remote(val server: ServerProbe?, val auth: AuthState, val setup: SetupProbe, val at: Double)

    @Volatile var remote: Remote? = null
        private set

    private fun granted(ctx: Context, p: String) = ContextCompat.checkSelfPermission(ctx, p) == PackageManager.PERMISSION_GRANTED

    /** בדיקות מקומיות בלבד (מהיר, thread ראשי מותר) + תוצאות השרת האחרונות מהמטמון. */
    fun local(ctx: Context): DiagnosticsSnapshot {
        val lm = ctx.getSystemService(LocationManager::class.java)
        val pm = ctx.getSystemService(PowerManager::class.java)
        val am = ctx.getSystemService(ActivityManager::class.java)
        val nm = ctx.getSystemService(NotificationManager::class.java)
        val sm = ctx.getSystemService(SensorManager::class.java)
        val cm = ctx.getSystemService(ConnectivityManager::class.java)
        val caps = runCatching { cm?.getNetworkCapabilities(cm.activeNetwork) }.getOrNull()
        val fix = LG.lastFix
        val r = remote
        val perm = when {
            granted(ctx, Manifest.permission.ACCESS_FINE_LOCATION) -> LocationPerm.FINE
            granted(ctx, Manifest.permission.ACCESS_COARSE_LOCATION) -> LocationPerm.COARSE
            else -> LocationPerm.NONE
        }
        val channels = listOf(Notifier.CH_RING to "צלצול חירום", Notifier.CH_ALERTS to "התראות חירום").map { (id, label) ->
            ChannelState(id, label, runCatching { nm.getNotificationChannel(id)?.importance }.getOrNull())
        }
        return DiagnosticsSnapshot(
            sdkInt = Build.VERSION.SDK_INT,
            nowSec = LG.nowSec(),
            locationPerm = perm,
            gpsEnabled = runCatching { lm.isProviderEnabled(LocationManager.GPS_PROVIDER) }.getOrDefault(false),
            networkLocationEnabled = runCatching { lm.isProviderEnabled(LocationManager.NETWORK_PROVIDER) }.getOrDefault(false),
            lastFixAt = fix?.capturedAt,
            lastFixAccuracyM = fix?.accuracyM,
            backgroundLocation = Build.VERSION.SDK_INT < 29 || granted(ctx, Manifest.permission.ACCESS_BACKGROUND_LOCATION),
            serviceRunning = LifeGuardService.running,
            batteryExempt = pm.isIgnoringBatteryOptimizations(ctx.packageName),
            powerSaveOn = pm.isPowerSaveMode,
            backgroundRestricted = if (Build.VERSION.SDK_INT >= 28) am.isBackgroundRestricted else null,
            networkPresent = caps?.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET) == true,
            networkValidated = caps?.hasCapability(NetworkCapabilities.NET_CAPABILITY_VALIDATED) == true,
            server = r?.server,
            auth = if (LG.store.token == null) AuthState.NO_TOKEN else r?.auth ?: AuthState.UNKNOWN,
            notificationsGranted = Build.VERSION.SDK_INT < 33 || granted(ctx, Manifest.permission.POST_NOTIFICATIONS),
            notificationsEnabled = NotificationManagerCompat.from(ctx).areNotificationsEnabled(),
            channels = channels,
            fullScreenAllowed = Build.VERSION.SDK_INT < 34 || nm.canUseFullScreenIntent(),
            setup = r?.setup ?: SetupProbe.NotChecked,
            smsNumbers = LG.store.emergencyNumbers.size,
            smsPermission = SmsSender.hasPermission(ctx),
            telephony = ctx.packageManager.hasSystemFeature(PackageManager.FEATURE_TELEPHONY_MESSAGING),
            hasAccelerometer = sm?.getDefaultSensor(Sensor.TYPE_ACCELEROMETER) != null,
            hasGyroscope = sm?.getDefaultSensor(Sensor.TYPE_GYROSCOPE) != null,
            motionDetectionEnabled = LG.store.fallDetection,
            audioEvidenceEnabled = PrivacySync.cachedPolicy?.let { it.recordOnEmergency || it.discreet } ?: false,
            micPermission = granted(ctx, Manifest.permission.RECORD_AUDIO),
            fcm = FcmManager.diagState(),
        )
    }

    /** בדיקות שרת - רשת, רק מ-thread רקע. מעדכן את המטמון. */
    fun refreshRemote(): Remote {
        val server: ServerProbe = try {
            val t0 = SystemClock.elapsedRealtime()
            val h = LG.api().health()
            val ms = SystemClock.elapsedRealtime() - t0
            LG.connectivity.onSuccess(LG.nowSec())
            ServerProbe(true, ms, h.optString("status", "ok"), null)
        } catch (e: ApiException) {
            ServerProbe(true, null, "http_${e.status}", e.message)
        } catch (e: IOException) {
            ServerProbe(false, null, null, e.message ?: e.javaClass.simpleName)
        } catch (e: IllegalStateException) {
            ServerProbe(false, null, null, "כתובת שרת לא מותרת")
        }
        val auth = if (LG.store.token == null) AuthState.NO_TOKEN else try {
            LG.api().me(); AuthState.VALID
        } catch (e: ApiException) {
            if (e.isAuth) AuthState.INVALID else AuthState.UNKNOWN
        } catch (e: Exception) {
            AuthState.UNKNOWN
        }
        // השכמה: ניסיון רישום מיידי ("בדוק שוב") לפני שבודקים מה השרת מכיר
        if (auth == AuthState.VALID) runCatching { FcmManager.flushNow(force = true) }
        val setup: SetupProbe = if (auth != AuthState.VALID) SetupProbe.NotChecked else try {
            SetupProbe.Ok(LG.api().meSetup().also { FcmManager.onSetup(it) })
        } catch (e: ApiException) {
            if (e.status == 404) SetupProbe.NotSupported else SetupProbe.Error(e.message ?: "שגיאת שרת")
        } catch (e: Exception) {
            SetupProbe.Error(e.message ?: e.javaClass.simpleName)
        }
        return Remote(server, auth, setup, LG.nowSec()).also { remote = it }
    }
}
