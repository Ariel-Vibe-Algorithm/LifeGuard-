package org.lifeguard.app.service

import android.Manifest
import android.annotation.SuppressLint
import android.content.Context
import android.content.pm.PackageManager
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import androidx.core.content.ContextCompat
import org.lifeguard.app.hazard.EnergyGate
import org.lifeguard.app.hazard.HazardFeatures
import java.util.concurrent.ArrayBlockingQueue
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicLong

/**
 * "זיהוי סכנה ברקע": קורא מהמיקרופון חלונות של 1.5 שנ' (16kHz מונו) ומעביר לניתוח - רק כשהמשתמש/ת הפעיל/ה את זה
 * במפורש. רץ בתוך LifeGuardService (אותו שירות חזית, סוג microphone) - אין שירות/מקליט שני; בזמן SOS עם תיעוד שמע
 * השירות עוצר אותו לפני שמקליט הראיות לוקח את המיקרופון.
 *
 * - האודיו לא נשמר ולא נשלח: כל חלון חי רק בזיכרון עד שנותח.
 * - חיסכון בסוללה: חלון כמעט שקט (EnergyGate) לא מגיע ל-YAMNet; תור באורך 1 - אם הניתוח עסוק, החלון מדולג
 *   (כמו hazard_monitor.feed ב-Windows).
 */
class HazardListener(
    private val ctx: Context,
    private val onWindow: (FloatArray, Double) -> Unit,
    private val onDigitalSilence: (Boolean) -> Unit = {},
) {
    @Volatile private var reader: Thread? = null
    @Volatile private var worker: Thread? = null
    @Volatile private var running = false
    @Volatile private var record: AudioRecord? = null
    private val queue = ArrayBlockingQueue<Pair<FloatArray, Double>>(1)
    val windows = AtomicLong()
    val gated = AtomicLong()
    val skippedBusy = AtomicLong()
    val analyzed = AtomicLong()

    val isRunning get() = running

    fun hasPermission() = ContextCompat.checkSelfPermission(ctx, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED

    /** null = התחיל; אחרת הסיבה (בעברית). */
    @SuppressLint("MissingPermission")
    @Synchronized
    fun start(): String? {
        if (running) return null
        if (!hasPermission()) return "אין הרשאת מיקרופון"
        val sr = HazardFeatures.SR
        val minBuf = AudioRecord.getMinBufferSize(sr, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
        if (minBuf <= 0) return "המיקרופון לא תומך ב-16kHz"
        val rec = try {
            // VOICE_RECOGNITION: בלי AGC / דיכוי רעשים אגרסיבי (שהיו מוחקים ירי ושבירה) - הכי קרוב לאודיו הגולמי
            AudioRecord(MediaRecorder.AudioSource.VOICE_RECOGNITION, sr, AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT, maxOf(minBuf, sr))
        } catch (e: Exception) {
            return "המיקרופון לא זמין (${e.javaClass.simpleName})"
        }
        if (rec.state != AudioRecord.STATE_INITIALIZED) { rec.release(); return "המיקרופון לא זמין (בשימוש?)" }
        try { rec.startRecording() } catch (e: Exception) { rec.release(); return "המיקרופון לא זמין (${e.javaClass.simpleName})" }
        running = true
        record = rec
        queue.clear()
        worker = Thread({
            while (running) {
                val item = try { queue.poll(500, TimeUnit.MILLISECONDS) } catch (_: InterruptedException) { null } ?: continue
                try { onWindow(item.first, item.second); analyzed.incrementAndGet() } catch (_: Throwable) { }
            }
        }, "lifeguard-hazard-analyze").apply { isDaemon = true; priority = Thread.MIN_PRIORITY; start() }
        reader = Thread({ readLoop(rec) }, "lifeguard-hazard-mic").apply { isDaemon = true; start() }
        return null
    }

    private fun readLoop(rec: AudioRecord) {
        val n = (HazardFeatures.WINDOW_S * HazardFeatures.SR).toInt()
        val pcm = ShortArray(n)
        var silentRun = 0
        try {
            while (running) {
                var got = 0
                while (running && got < n) {
                    val r = rec.read(pcm, got, n - got)
                    if (r < 0) { running = false; break }
                    got += r
                }
                if (!running) break
                val x = FloatArray(n) { pcm[it] / 32768f }
                windows.incrementAndGet()
                // מיקרופון חסום (מתג הפרטיות) / תפוס מחזיר אפסים - אומרים את זה במקום "שקט = בטוח"
                if (EnergyGate.isDigitalSilence(x)) {
                    if (++silentRun == 4) onDigitalSilence(true)
                    continue
                } else if (silentRun >= 4) onDigitalSilence(false)
                silentRun = 0
                if (!EnergyGate.passes(x)) { gated.incrementAndGet(); continue }
                if (!queue.offer(x to System.currentTimeMillis() / 1000.0)) skippedBusy.incrementAndGet()
            }
        } finally {
            runCatching { rec.stop() }
            rec.release()
            running = false
        }
    }

    /** עוצר ומחכה שהמיקרופון ישוחרר (עד 2 שנ') - כדי שמקליט הראיות של ה-SOS יוכל לקחת אותו מיד. */
    @Synchronized
    fun stop() {
        running = false
        runCatching { record?.stop() }                 // משחרר read() חוסם
        val r = reader
        worker?.interrupt()
        if (r != null && r !== Thread.currentThread()) runCatching { r.join(2000) }
        reader = null; worker = null; record = null
        queue.clear()
    }
}
