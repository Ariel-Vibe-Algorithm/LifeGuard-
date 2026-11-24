package org.lifeguard.app.service

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.media.MediaRecorder
import android.os.Build
import android.os.SystemClock
import android.util.Log
import androidx.core.content.ContextCompat
import org.json.JSONObject
import org.lifeguard.app.LG
import org.lifeguard.app.api.GeoPoint
import org.lifeguard.app.core.AesGcmSealer
import org.lifeguard.app.core.EvidenceLog
import org.lifeguard.app.core.MotionSignal
import org.lifeguard.app.store.EvidenceKey
import java.io.File

/**
 * יומן הראיות של האירוע הנוכחי (שכבת Android דקה מעל EvidenceLog).
 * כל כשל כאן נבלע - תיעוד לעולם לא שובר את שליחת ה-SOS.
 */
object Evidence {
    private const val TAG = "Evidence"
    const val LOCATION_INTERVAL_SECONDS = 15.0

    val log: EvidenceLog by lazy { EvidenceLog(File(LG.app.noBackupFilesDir, "evidence"), AesGcmSealer { EvidenceKey.get() }) }

    private var lastLocLogged = 0.0
    private var lastLocFix = -1.0

    val currentKey: String? get() = LG.store.evidenceKey

    private inline fun safe(what: String, block: () -> Unit) {
        try { block() } catch (e: Exception) { Log.w(TAG, "$what failed: ${e.javaClass.simpleName}") }
    }

    /** תחילת אירוע: key = client_id של ה-SOS (או מזהה האירוע מהשרת כשהשרת פתח אותו, למשל SOS מנסיעה). */
    fun begin(key: String, mode: String, data: JSONObject) = safe("begin") {
        LG.store.evidenceKey = key
        lastLocLogged = 0.0; lastLocFix = -1.0
        log.start(key, mode, data)
    }

    fun record(type: String, data: JSONObject = JSONObject()) {
        val k = currentKey ?: return
        safe(type) { log.append(k, type, data) }
    }

    /** רק אם האירוע הוא האירוע הפעיל שלי */
    fun recordForIncident(incidentId: String?, type: String, data: JSONObject) {
        if (incidentId == null || incidentId != LG.store.activeIncidentId) return
        record(type, data)
    }

    fun linkIncident(incidentId: String) {
        val k = currentKey ?: return
        safe("link") { log.setIncidentId(k, incidentId) }
    }

    /** סוף אירוע. closedAs: resolved | false_alarm | closed_remote. מחזיר את מפתח היומן (למחיקה באישור). */
    fun end(closedAs: String, data: JSONObject = JSONObject()): String? {
        val k = currentKey ?: return null
        safe("end") { log.close(k, closedAs, data) }
        LG.store.evidenceKey = null
        return k
    }

    fun motion(s: MotionSignal, beforeSos: Boolean = false) = record("motion_detection", JSONObject()
        .put("source", s.source).put("confidence", s.confidence).put("at_ms", s.atMs).put("reason", s.reason)
        .put("before_sos", beforeSos))

    fun geo(g: GeoPoint): JSONObject = JSONObject().put("lat", g.lat).put("lon", g.lon).apply {
        g.accuracyM?.let { put("accuracy_m", it) }; g.capturedAt?.let { put("captured_at", it) }
        g.speedMps?.let { put("speed_mps", it) }; put("source", g.source)
    }

    /** מהשירות כל שנייה: מיקום חדש כל 15 שנ' בזמן אירוע פעיל/ממתין. */
    fun locationTick() {
        if (currentKey == null || !IncidentManager.isActive) return
        val fix = LG.lastFix ?: return
        val now = LG.nowSec()
        if (now - lastLocLogged < LOCATION_INTERVAL_SECONDS || (fix.capturedAt ?: 0.0) == lastLocFix) return
        lastLocLogged = now; lastLocFix = fix.capturedAt ?: 0.0
        record("location", geo(fix))
    }
}

/**
 * הקלטת שמע לתיעוד בזמן SOS ידני / חירום מאושר, לפי מדיניות השרת (RecordingGate). קטעים של דקה; כל קטע מוצפן לתוך היומן ונחתם ב-SHA-256,
 * והקובץ הזמני (בתיקייה הפרטית של האפליקציה, לא מגובה) נמחק. הקובץ הזמני קיים לא מוצפן רק בזמן הקטע הנוכחי.
 */
class AudioEvidenceRecorder(private val ctx: Context) {
    private var recorder: MediaRecorder? = null
    private var file: File? = null
    private var startedAt = 0L
    /** שעון קיר (epoch ms) של תחילת הקטע הנוכחי - נשלח עם ההעלאה */
    private var startedWallMs = 0L
    /** "מפגש הקלטה" (מהתחלה ועד עצירה) - לאירועי הביקורת recording_started / recording_stopped */
    private var sessionKey: String? = null
    private var key: String? = null
    private var reportedUnavailableFor: String? = null
    private val tmpDir get() = File(ctx.noBackupFilesDir, "evidence_tmp").apply { mkdirs() }

    val isRecording get() = recorder != null

    fun hasPermission() = ContextCompat.checkSelfPermission(ctx, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED

    /** קטעים שנשארו מקריסה: נחתמים ליומן הנוכחי (מסומנים recovered) או נמחקים. */
    fun recoverLeftovers() {
        val k = Evidence.currentKey
        tmpDir.listFiles()?.forEach { f ->
            if (k != null && f.length() > 0) runCatching {
                Evidence.log.sealFile(k, f, "audio", JSONObject().put("recovered", true)
                    .put("note", "קטע שנקטע (התהליך נעצר) - ייתכן שאינו ניתן לניגון"))
            }
            f.delete()
        }
    }

    /** reasonUnavailable != null -> לא מקליטים ונרשם פעם אחת ביומן למה. */
    @Synchronized
    fun update(want: Boolean, k: String?, reasonUnavailable: String?) {
        if (!want || k == null) { stop(); return }
        if (reasonUnavailable != null) {
            stop()
            if (reportedUnavailableFor != k) {
                reportedUnavailableFor = k
                Evidence.record("audio_unavailable", JSONObject().put("reason", reasonUnavailable))
            }
            return
        }
        if (recorder != null && key != k) stop()
        if (recorder == null) start(k)
        else if (SystemClock.elapsedRealtime() - startedAt >= SEGMENT_MS) { finishSegment(); start(k) }
    }

    private fun start(k: String) {
        val f = File(tmpDir, "seg_${System.currentTimeMillis()}.m4a")
        val r = if (Build.VERSION.SDK_INT >= 31) MediaRecorder(ctx) else @Suppress("DEPRECATION") MediaRecorder()
        try {
            r.setAudioSource(MediaRecorder.AudioSource.MIC)
            r.setOutputFormat(MediaRecorder.OutputFormat.MPEG_4)
            r.setAudioEncoder(MediaRecorder.AudioEncoder.AAC)
            r.setAudioChannels(1)
            r.setAudioSamplingRate(16_000)
            r.setAudioEncodingBitRate(32_000)
            r.setOutputFile(f.absolutePath)
            r.prepare()
            r.start()
            recorder = r; file = f; key = k; startedAt = SystemClock.elapsedRealtime(); startedWallMs = System.currentTimeMillis()
            if (sessionKey != k) {
                sessionKey = k
                PrivacySync.audit("recording_started", "completed", JSONObject().put("discreet", PrivacySync.cachedPolicy?.discreet == true),
                    runCatching { Evidence.log.meta(k)?.incidentId }.getOrNull())
            }
            if (reportedUnavailableFor == k) reportedUnavailableFor = null
            Evidence.record("audio_started", JSONObject().put("segment_seconds", SEGMENT_MS / 1000))
        } catch (e: Exception) {
            runCatching { r.release() }; f.delete()
            if (reportedUnavailableFor != k) {
                reportedUnavailableFor = k
                Evidence.record("audio_unavailable", JSONObject().put("reason", "המיקרופון לא זמין: ${e.javaClass.simpleName}"))
            }
        }
    }

    private fun finishSegment() {
        val r = recorder ?: return
        val f = file; val k = key
        val dur = SystemClock.elapsedRealtime() - startedAt
        recorder = null; file = null
        val ok = runCatching { r.stop() }.isSuccess      // stop() זורק אם לא נאסף כלום
        runCatching { r.release() }
        if (f == null || k == null) return
        if (!ok || f.length() == 0L) { f.delete(); return }
        val startWall = startedWallMs
        runCatching { Evidence.log.sealFile(k, f, "audio", JSONObject().put("duration_ms", dur)) }
            .onSuccess { e ->
                val d = e.dataJson()
                runCatching { PrivacySync.onSegmentSealed(k, d.optString("file"), d.optString("sha256"), d.optLong("bytes"),
                    startWall / 1000.0, (startWall + dur) / 1000.0) }
            }
            .onFailure { f.delete() }
    }

    @Synchronized
    fun stop() {
        if (recorder == null) return
        finishSegment()
        Evidence.record("audio_stopped")
        sessionKey?.let { sk -> PrivacySync.audit("recording_stopped", "completed", JSONObject(),
            runCatching { Evidence.log.meta(sk)?.incidentId }.getOrNull()) }
        sessionKey = null
        key = null
    }

    companion object { const val SEGMENT_MS = 60_000L }
}
