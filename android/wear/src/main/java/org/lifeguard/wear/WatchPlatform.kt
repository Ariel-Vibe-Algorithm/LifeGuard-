package org.lifeguard.wear

import android.annotation.SuppressLint
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.media.AudioAttributes
import android.net.Uri
import android.os.Build
import android.os.PowerManager
import android.os.VibrationAttributes
import android.os.VibrationEffect
import android.os.Vibrator
import android.os.VibratorManager
import android.widget.Toast
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import org.lifeguard.wear.logic.SosDelivery

/** הגדרות השעון (מקומיות בלבד) */
object WatchPrefs {
    private const val FILE = "lifeguard_watch"
    private const val KEY_FALL = "fall_enabled"
    private const val KEY_MODE = "monitor_mode"

    const val MODE_WAKEUP = "wakeup"
    const val MODE_WAKELOCK = "wakelock"
    const val MODE_NO_SENSOR = "no_sensor"

    private fun p(ctx: Context) = ctx.getSharedPreferences(FILE, Context.MODE_PRIVATE)

    /** ברירת מחדל: מופעל (עם מתג כיבוי במסך הראשי) */
    fun fallEnabled(ctx: Context) = p(ctx).getBoolean(KEY_FALL, true)
    fun setFallEnabled(ctx: Context, v: Boolean) = p(ctx).edit().putBoolean(KEY_FALL, v).apply()

    /** איך השירות דוגם בפועל (להסבר על הסוללה במסך) */
    fun monitorMode(ctx: Context): String? = p(ctx).getString(KEY_MODE, null)
    fun setMonitorMode(ctx: Context, v: String?) = p(ctx).edit().putString(KEY_MODE, v).apply()
}

/** רטט: התקדמות ההחזקה, תקתוק בחלון הביטול, אזעקה אחרי נפילה */
object Haptics {
    private val ALARM_TIMINGS = longArrayOf(0, 900, 250, 900, 250, 900, 900)
    private val ALARM_AMPLITUDES = intArrayOf(0, 255, 0, 255, 0, 255, 0)

    private fun vibrator(ctx: Context): Vibrator? = if (Build.VERSION.SDK_INT >= 31)
        ctx.getSystemService(VibratorManager::class.java)?.defaultVibrator
    else @Suppress("DEPRECATION") ctx.getSystemService(Vibrator::class.java)

    private fun play(ctx: Context, effect: VibrationEffect, alarm: Boolean = false) {
        val v = vibrator(ctx) ?: return
        if (!v.hasVibrator()) return
        runCatching {
            if (alarm && Build.VERSION.SDK_INT >= 33) {
                v.vibrate(effect, VibrationAttributes.createForUsage(VibrationAttributes.USAGE_ALARM))
            } else if (alarm) {
                @Suppress("DEPRECATION")
                v.vibrate(effect, AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_ALARM).build())
            } else v.vibrate(effect)
        }
    }

    fun tick(ctx: Context) = play(ctx, VibrationEffect.createPredefined(VibrationEffect.EFFECT_TICK))
    fun click(ctx: Context) = play(ctx, VibrationEffect.createPredefined(VibrationEffect.EFFECT_CLICK))
    fun heavy(ctx: Context) = play(ctx, VibrationEffect.createPredefined(VibrationEffect.EFFECT_HEAVY_CLICK))
    fun confirm(ctx: Context) = play(ctx, VibrationEffect.createWaveform(longArrayOf(0, 120, 120, 120), -1))
    fun error(ctx: Context) = play(ctx, VibrationEffect.createWaveform(longArrayOf(0, 500, 200, 500), -1))

    /** רטט חזק חוזר עד stop() - "הכל בסדר?" אחרי נפילה */
    fun startAlarm(ctx: Context) {
        val v = vibrator(ctx)
        val effect = if (v != null && v.hasAmplitudeControl())
            VibrationEffect.createWaveform(ALARM_TIMINGS, ALARM_AMPLITUDES, 0)
        else VibrationEffect.createWaveform(ALARM_TIMINGS, 0)
        play(ctx, effect, alarm = true)
    }

    fun stop(ctx: Context) { runCatching { vibrator(ctx)?.cancel() } }
}

/** wake lock קצר עם זמן קצוב: ספירה לאחור / המתנה לאישור כשהמסך כבוי (Handler לא מתקדם בשינה עמוקה) */
object WakeHold {
    private var lock: PowerManager.WakeLock? = null

    @SuppressLint("WakelockTimeout")
    @Synchronized
    fun hold(ctx: Context, ms: Long) {
        val l = lock ?: ctx.getSystemService(PowerManager::class.java)
            ?.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "LifeGuard:watch-sos")?.apply { setReferenceCounted(false) }
            ?.also { lock = it }
        runCatching { l?.acquire(ms) }
    }

    @Synchronized
    fun release() { runCatching { lock?.takeIf { it.isHeld }?.release() } }
}

/** חיוג חירום מהשעון: ACTION_DIAL (בלי הרשאת CALL_PHONE). שעון בלי סים/חייגן -> הודעה ברורה. */
object Dialer {
    fun hasTelephony(ctx: Context) = ctx.packageManager.hasSystemFeature(PackageManager.FEATURE_TELEPHONY)

    fun dial(ctx: Context, number: String) {
        try {
            ctx.startActivity(Intent(Intent.ACTION_DIAL, Uri.parse("tel:$number")).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        } catch (_: ActivityNotFoundException) {
            Toast.makeText(ctx, R.string.no_dialer, Toast.LENGTH_LONG).show()
        } catch (_: SecurityException) {
            Toast.makeText(ctx, R.string.no_dialer, Toast.LENGTH_LONG).show()
        }
    }
}

/** התראות מקומיות בשעון */
object WatchNotifier {
    const val CH_FALL = "fall_v1"
    const val CH_MONITOR = "monitor_v1"
    const val CH_STATUS = "status_v1"
    const val ID_MONITOR = 1
    const val ID_FALL = 2
    const val ID_STATUS = 3

    fun createChannels(ctx: Context) {
        val nm = ctx.getSystemService(NotificationManager::class.java) ?: return
        // ערוץ הנפילה בלי רטט משלו: האזעקה (Haptics.startAlarm) רוטטת עד תשובה
        nm.createNotificationChannel(NotificationChannel(CH_FALL, ctx.getString(R.string.ch_fall), NotificationManager.IMPORTANCE_HIGH)
            .apply { enableVibration(false); setSound(null, null) })
        nm.createNotificationChannel(NotificationChannel(CH_MONITOR, ctx.getString(R.string.ch_monitor), NotificationManager.IMPORTANCE_LOW)
            .apply { enableVibration(false); setSound(null, null); setShowBadge(false) })
        nm.createNotificationChannel(NotificationChannel(CH_STATUS, ctx.getString(R.string.ch_status), NotificationManager.IMPORTANCE_DEFAULT))
    }

    fun canPost(ctx: Context) = Build.VERSION.SDK_INT < 33 ||
        ContextCompat.checkSelfPermission(ctx, android.Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED

    @SuppressLint("MissingPermission")
    private fun post(ctx: Context, id: Int, n: android.app.Notification) {
        if (canPost(ctx)) runCatching { NotificationManagerCompat.from(ctx).notify(id, n) }
    }

    fun cancel(ctx: Context, id: Int) = runCatching { NotificationManagerCompat.from(ctx).cancel(id) }

    private fun activityPi(ctx: Context, req: Int, cls: Class<*>) = PendingIntent.getActivity(ctx, req,
        Intent(ctx, cls).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP),
        PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)

    private fun actionPi(ctx: Context, action: String) = PendingIntent.getBroadcast(ctx, action.hashCode(),
        Intent(ctx, FallActionReceiver::class.java).setAction(action),
        PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)

    fun monitorBuilder(ctx: Context): NotificationCompat.Builder = NotificationCompat.Builder(ctx, CH_MONITOR)
        .setSmallIcon(R.drawable.ic_shield)
        .setContentTitle(ctx.getString(R.string.app_name))
        .setContentText(ctx.getString(R.string.monitor_text))
        .setOngoing(true)
        .setOnlyAlertOnce(true)
        .setCategory(NotificationCompat.CATEGORY_SERVICE)
        .setContentIntent(activityPi(ctx, ID_MONITOR, MainActivity::class.java))

    /** "הכל בסדר?": מסך מלא (אם מותר) + פעולות ישירות בהתראה (אם אין הרשאת מסך מלא) */
    fun showFallPrompt(ctx: Context, secondsLeft: Int) {
        val full = activityPi(ctx, ID_FALL, FallAlertActivity::class.java)
        val n = NotificationCompat.Builder(ctx, CH_FALL)
            .setSmallIcon(R.drawable.ic_shield)
            .setColor(ContextCompat.getColor(ctx, R.color.sos_red))
            .setContentTitle(ctx.getString(R.string.fall_title))
            .setContentText(ctx.getString(R.string.fall_countdown, secondsLeft))
            .setPriority(NotificationCompat.PRIORITY_MAX)
            .setCategory(NotificationCompat.CATEGORY_ALARM)
            .setVisibility(NotificationCompat.VISIBILITY_PUBLIC)
            .setOnlyAlertOnce(true)
            .setAutoCancel(false)
            .setFullScreenIntent(full, true)
            .setContentIntent(full)
            .addAction(R.drawable.ic_shield, ctx.getString(R.string.fall_ok), actionPi(ctx, FallActionReceiver.ACTION_OK))
            .addAction(R.drawable.ic_shield, ctx.getString(R.string.fall_help), actionPi(ctx, FallActionReceiver.ACTION_HELP))
            .build()
        post(ctx, ID_FALL, n)
    }

    /** מצב המסירה כשאין מסך פתוח (למשל אחרי נפילה, או שהמשתמש/ת יצא/ה מהאפליקציה) */
    fun showStatus(ctx: Context, v: SosDelivery.View) {
        if (v.title.isEmpty()) { cancel(ctx, ID_STATUS); return }
        val text = listOf(v.title, v.detail).filter { it.isNotEmpty() }.joinToString("\n")
        val n = NotificationCompat.Builder(ctx, CH_STATUS)
            .setSmallIcon(R.drawable.ic_shield)
            .setContentTitle(v.title)
            .setContentText(v.detail.ifEmpty { v.title })
            .setStyle(NotificationCompat.BigTextStyle().bigText(text))
            .setOnlyAlertOnce(true)
            .setAutoCancel(true)
            .setContentIntent(activityPi(ctx, ID_STATUS, MainActivity::class.java))
            .build()
        post(ctx, ID_STATUS, n)
    }
}
