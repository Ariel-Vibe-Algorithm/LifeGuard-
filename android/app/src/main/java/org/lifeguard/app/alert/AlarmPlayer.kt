package org.lifeguard.app.alert

import android.content.Context
import android.media.AudioAttributes
import android.media.AudioManager
import android.media.MediaPlayer
import android.media.RingtoneManager
import android.media.ToneGenerator
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.os.VibrationAttributes
import android.os.VibrationEffect
import android.os.Vibrator
import android.os.VibratorManager

/**
 * אזעקה מקומית: צליל בלולאה בערוץ ALARM (נשמע גם במצב שקט/רטט, היכן שהמערכת מאפשרת) + רטט.
 * RING = צלצול קריאה נכנסת, SIREN = סירנה בטלפון של מי שבמצוקה (קריאה גלויה), BEEP = "הכל בסדר?".
 */
class AlarmPlayer(private val ctx: Context) {
    enum class Mode { NONE, RING, SIREN, BEEP }

    @Volatile var mode = Mode.NONE
        private set
    private var player: MediaPlayer? = null
    private var tone: ToneGenerator? = null
    private val main = Handler(Looper.getMainLooper())
    private var savedVolume: Int? = null
    private val audio = ctx.getSystemService(AudioManager::class.java)

    private val alarmAttrs = AudioAttributes.Builder()
        .setUsage(AudioAttributes.USAGE_ALARM)
        .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION)
        .build()

    fun startRing() = start(Mode.RING)
    fun startSiren() = start(Mode.SIREN)
    fun startBeep() = start(Mode.BEEP)

    /** עוצר רק צפצוף "הכל בסדר?" (לא צלצול/סירנה) */
    fun stopBeepIfAny() { if (mode == Mode.BEEP) stop() }

    @Synchronized
    private fun start(m: Mode) {
        if (mode == m) return
        stopInternal()
        mode = m
        raiseVolume()
        if (m == Mode.BEEP) startBeepLoop() else startLoopingSound()
        vibrate(if (m == Mode.BEEP) longArrayOf(0, 300, 700) else longArrayOf(0, 800, 400))
    }

    @Synchronized
    fun stop() {
        stopInternal()
        mode = Mode.NONE
    }

    private fun stopInternal() {
        main.removeCallbacksAndMessages(null)
        runCatching { player?.stop() }; runCatching { player?.release() }; player = null
        runCatching { tone?.release() }; tone = null
        vibrator()?.cancel()
        restoreVolume()
    }

    private fun startLoopingSound() {
        val uri = RingtoneManager.getDefaultUri(RingtoneManager.TYPE_ALARM)
            ?: RingtoneManager.getDefaultUri(RingtoneManager.TYPE_RINGTONE)
        val mp = MediaPlayer()
        try {
            mp.setAudioAttributes(alarmAttrs)
            mp.setDataSource(ctx, uri ?: throw IllegalStateException("no alarm sound"))
            mp.isLooping = true
            mp.prepare()
            mp.start()
            player = mp
        } catch (e: Exception) {
            runCatching { mp.release() }
            startBeepLoop()      // אין צליל אזעקה במכשיר (נפוץ באמולטור) - צפצוף בערוץ האזעקה
        }
    }

    private fun startBeepLoop() {
        tone = runCatching { ToneGenerator(AudioManager.STREAM_ALARM, ToneGenerator.MAX_VOLUME) }.getOrNull()
        val r = object : Runnable {
            override fun run() {
                tone?.startTone(ToneGenerator.TONE_CDMA_EMERGENCY_RINGBACK, 600)
                main.postDelayed(this, 1000)
            }
        }
        main.post(r)
    }

    /** מגביר את עוצמת ערוץ האזעקה לזמן הצלצול ומחזיר אחר כך. */
    private fun raiseVolume() {
        runCatching {
            if (savedVolume == null) savedVolume = audio.getStreamVolume(AudioManager.STREAM_ALARM)
            audio.setStreamVolume(AudioManager.STREAM_ALARM, audio.getStreamMaxVolume(AudioManager.STREAM_ALARM), 0)
        }
    }

    private fun restoreVolume() {
        savedVolume?.let { v -> runCatching { audio.setStreamVolume(AudioManager.STREAM_ALARM, v, 0) } }
        savedVolume = null
    }

    private fun vibrator(): Vibrator? = if (Build.VERSION.SDK_INT >= 31)
        ctx.getSystemService(VibratorManager::class.java)?.defaultVibrator
    else @Suppress("DEPRECATION") ctx.getSystemService(Vibrator::class.java)

    private fun vibrate(pattern: LongArray) {
        val v = vibrator() ?: return
        if (!v.hasVibrator()) return
        val effect = VibrationEffect.createWaveform(pattern, 0)
        if (Build.VERSION.SDK_INT >= 33) {
            v.vibrate(effect, VibrationAttributes.createForUsage(VibrationAttributes.USAGE_ALARM))
        } else {
            @Suppress("DEPRECATION") v.vibrate(effect, alarmAttrs)
        }
    }
}
