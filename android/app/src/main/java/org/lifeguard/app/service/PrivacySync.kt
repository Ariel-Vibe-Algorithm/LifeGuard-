package org.lifeguard.app.service

import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.os.BatteryManager
import android.os.PowerManager
import android.util.Log
import org.json.JSONObject
import org.lifeguard.app.LG
import org.lifeguard.app.UiEvent
import org.lifeguard.app.api.ApiException
import org.lifeguard.app.api.PrivacyPermissions
import org.lifeguard.app.api.RecordingPolicy
import org.lifeguard.app.core.AesGcmSealer
import org.lifeguard.app.core.DeviceAuditQueue
import org.lifeguard.app.core.Hashes
import org.lifeguard.app.core.LiveLocationPolicy
import org.lifeguard.app.core.RecordingUpload
import org.lifeguard.app.core.RecordingUploadPolicy
import org.lifeguard.app.core.RecordingUploadQueue
import org.lifeguard.app.core.SealedFileTextStore
import org.lifeguard.app.store.QueueKey
import java.io.File
import java.io.IOException

/**
 * שכבת Android דקה מעל הלוגיקה הטהורה (core/): תור ביקורת מוצפן, תור העלאת הקלטות, מדיניות הקלטה ממטמון,
 * העלאת מיקום חי למצב "always", והשהיית שיתוף בלי קליטה. נקרא מהשירות (thread רקע).
 * כל כשל נבלע ונרשם בלוג - לעולם לא שובר SOS.
 */
object PrivacySync {
    private const val TAG = "PrivacySync"
    const val POLICY_REFRESH_SECONDS = 15 * 60.0

    /** התורים נשמרים לפי משתמש (hash של שם המשתמש) - התחברות של משתמש אחר לא שולחת אירועים בשמו של הקודם */
    private var queuesFor: String? = null
    private var auditQ: DeviceAuditQueue? = null
    private var uploadQ: RecordingUploadQueue? = null

    @Synchronized
    private fun ensureQueues(): Pair<DeviceAuditQueue, RecordingUploadQueue>? {
        val u = LG.store.username ?: return null
        if (queuesFor != u || auditQ == null) {
            val dir = File(LG.app.noBackupFilesDir, "queues/" + Hashes.sha256(u.lowercase()).take(16))
            val sealer = AesGcmSealer { QueueKey.get() }
            auditQ = DeviceAuditQueue(SealedFileTextStore(File(dir, "device_audit.bin"), sealer))
            uploadQ = RecordingUploadQueue(SealedFileTextStore(File(dir, "recording_uploads.bin"), sealer))
            queuesFor = u
        }
        return auditQ!! to uploadQ!!
    }

    val audit: DeviceAuditQueue? get() = ensureQueues()?.first
    val uploads: RecordingUploadQueue? get() = ensureQueues()?.second

    /** אירוע ביקורת מהמכשיר (נשלח כשיש קליטה). */
    fun audit(action: String, result: String = "completed", details: JSONObject = JSONObject(), incidentId: String? = null,
              deviceTs: Double? = null) {
        try { audit?.add(action, result, details, incidentId, deviceTs = deviceTs) }
        catch (e: Exception) { Log.w(TAG, "audit $action failed: ${e.javaClass.simpleName}") }
    }

    // ------------------------------------------------------------ מדיניות הקלטה (מטמון)
    val cachedPolicy: RecordingPolicy?
        get() = LG.store.recordingPolicyJson?.let { runCatching { RecordingPolicy.parse(JSONObject(it)) }.getOrNull() }

    fun savePolicy(p: RecordingPolicy) {
        LG.store.recordingPolicyJson = p.toJson().toString()
        LG.store.policyFetchedAt = LG.nowSec()
    }

    fun savePermissions(p: PrivacyPermissions) {
        LG.store.alwaysContacts = p.alwaysContacts
        // השהיה מקומית שעוד לא נשלחה גוברת על מה שהשרת אומר
        if (LG.store.pendingPause == null) LG.store.sharingPaused = p.sharingPaused
        p.recordingPolicy?.let { savePolicy(it) }
        LG.store.liveRefusedUntil = 0.0
    }

    /** מדיניות + הרשאות מהשרת (כל 15 דק', או force). + העברה חד-פעמית של ההגדרה המקומית הישנה. */
    fun refresh(force: Boolean = false) {
        if (!force && LG.nowSec() - LG.store.policyFetchedAt < POLICY_REFRESH_SECONDS) return
        val api = LG.api()
        if (LG.store.audioEvidence) {
            savePolicy(api.setRecordOnEmergency(true))
            LG.store.audioEvidence = false
        }
        savePermissions(api.privacyPermissions())
        savePolicy(api.recordingPolicy())
        LG.emit(UiEvent.StateChanged)
    }

    // ------------------------------------------------------------ השהיית שיתוף
    /** מקומית מיד (מפסיקים להעלות), ואז לשרת. בלי קליטה -> נשלח כשהקליטה חוזרת + אירוע ביקורת. */
    fun setPaused(paused: Boolean): PrivacyPermissions? {
        LG.store.sharingPaused = paused
        LG.store.pendingPause = "$paused|${LG.nowSec()}"
        return try {
            val p = LG.api().pauseLocationSharing(paused)
            LG.store.pendingPause = null
            savePermissions(p)
            p
        } catch (e: IOException) {
            null
        } catch (e: ApiException) {
            if (!e.isRetryable && !e.isAuth) LG.store.pendingPause = null
            throw e
        }
    }

    private fun processPendingPause() {
        val pp = LG.store.pendingPause ?: return
        val parts = pp.split("|")
        val v = parts[0] == "true"
        val at = parts.getOrNull(1)?.toDoubleOrNull() ?: LG.nowSec()
        val p = LG.api().pauseLocationSharing(v)
        LG.store.pendingPause = null
        savePermissions(p)
        if (v) audit("live_location_paused_offline", "completed",
            JSONObject().put("paused_at_device", at).put("delivered_at_device", LG.nowSec()), deviceTs = at)
    }

    // ------------------------------------------------------------ מיקום חי ("always")
    private fun battery(ctx: Context): Triple<Boolean, Int?, Boolean> {
        val pm = ctx.getSystemService(PowerManager::class.java)
        val i: Intent? = runCatching { ctx.registerReceiver(null, IntentFilter(Intent.ACTION_BATTERY_CHANGED)) }.getOrNull()
        val level = i?.getIntExtra(BatteryManager.EXTRA_LEVEL, -1) ?: -1
        val scale = i?.getIntExtra(BatteryManager.EXTRA_SCALE, -1) ?: -1
        val plugged = (i?.getIntExtra(BatteryManager.EXTRA_PLUGGED, 0) ?: 0) != 0
        return Triple(pm?.isPowerSaveMode == true, if (level >= 0 && scale > 0) level * 100 / scale else null, plugged)
    }

    private fun liveLocation(ctx: Context) {
        val s = LG.store
        val (saver, pct, charging) = battery(ctx)
        val fix = LG.lastFix
        val d = LiveLocationPolicy.decide(LiveLocationPolicy.Input(
            now = LG.nowSec(), alwaysContacts = s.alwaysContacts, paused = s.sharingPaused, fix = fix,
            lastUploadAt = s.liveLastUploadAt, lastUploadedFixAt = s.liveLastFixAt.takeIf { it > 0 },
            powerSave = saver, batteryPercent = pct, charging = charging, refusedUntil = s.liveRefusedUntil))
        if (!d.upload || fix == null) return
        s.liveLastUploadAt = LG.nowSec()
        val r = LG.api().putLiveLocation(fix)
        if (r.stored) {
            s.liveLastFixAt = fix.capturedAt ?: 0.0
            s.liveLastAccuracy = fix.accuracyM?.toFloat() ?: -1f
        } else {
            // השרת לא שמר (מושהה / אין אף איש קשר "always") - מפסיקים עד רענון ההרשאות
            s.liveRefusedUntil = LG.nowSec() + LiveLocationPolicy.REFUSAL_BACKOFF_SECONDS
            if (r.reason == "no_always_contacts") s.alwaysContacts = 0
            if (r.reason == "paused" && s.pendingPause == null) s.sharingPaused = true
        }
    }

    // ------------------------------------------------------------ הקלטות
    /** קטע שנחתם ביומן הראיות -> תור העלאה (אם יש נמענים מורשים), ואירוע "נשמר במכשיר" כשאין קליטה/אירוע. */
    fun onSegmentSealed(evidenceKey: String, blob: String, sha256: String, bytes: Long, startedAt: Double, endedAt: Double) {
        val q = uploads ?: return
        val policy = cachedPolicy
        val incidentId = runCatching { Evidence.log.meta(evidenceKey)?.incidentId }.getOrNull()
        if (policy?.upload != true) {
            Evidence.record("recording_local_only", JSONObject().put("file", blob)
                .put("reason", "אין אנשי קשר עם הרשאת הקלטות - הקטע נשמר במכשיר בלבד"))
            return
        }
        val item = RecordingUpload(RecordingUpload.clientIdFor(evidenceKey, blob), evidenceKey, blob, sha256, bytes,
            startedAt = startedAt, endedAt = endedAt, incidentId = incidentId)
        runCatching { q.add(item) }
        if (incidentId == null || !LG.isOnline()) {
            audit("recording_stored_offline", "pending", JSONObject().put("segment", item.clientId).put("bytes", bytes),
                incidentId)
            runCatching { q.update(item.clientId) { it.copy(offlineReported = true) } }
        }
    }

    private fun uploadRecordings() {
        val q = uploads ?: return
        val now = LG.nowSec()
        for (it in q.due(now).take(3)) {
            if (RecordingUploadPolicy.expired(it, now)) { drop(q, it, "too_late"); continue }
            val iid = it.incidentId ?: runCatching { Evidence.log.meta(it.evidenceKey)?.incidentId }.getOrNull()
            if (iid == null) continue                                  // ה-SOS עוד לא הגיע לשרת
            if (it.incidentId == null) q.update(it.clientId) { x -> x.copy(incidentId = iid) }
            val data = try { Evidence.log.readBlob(it.evidenceKey, it.blob) } catch (e: Exception) {
                drop(q, it, "deleted_locally"); continue                // המשתמש/ת מחק/ה את התיעוד המקומי
            }
            val max = cachedPolicy?.maxUploadBytes ?: RecordingPolicy.DEFAULT_MAX_BYTES
            if (data.size > max) { drop(q, it, "too_large"); continue }
            val outcome = try {
                val r = LG.api().uploadRecording(iid, it.clientId, data, it.sha256, it.mime, it.startedAt, it.endedAt)
                Evidence.log.append(it.evidenceKey, "recording_uploaded", JSONObject().put("file", it.blob)
                    .put("server_id", r.id).put("duplicate", r.duplicate).put("sha256", it.sha256)
                    .put("recipients", r.recipients.size))
                RecordingUploadPolicy.Outcome.Uploaded
            } catch (e: ApiException) {
                RecordingUploadPolicy.classify(e.status, e.code)
            } catch (e: IOException) {
                RecordingUploadPolicy.classify(null, null)
            }
            when (outcome) {
                is RecordingUploadPolicy.Outcome.Drop -> drop(q, it, outcome.reason)
                else -> {
                    q.record(it.clientId, outcome, now)
                    if (outcome is RecordingUploadPolicy.Outcome.Retry && outcome.reason == "network") return
                }
            }
        }
    }

    private fun drop(q: RecordingUploadQueue, it: RecordingUpload, reason: String) {
        q.update(it.clientId) { null }
        audit("recording_upload_failed", "failed", JSONObject().put("segment", it.clientId).put("reason", reason),
            it.incidentId)
        runCatching { Evidence.log.append(it.evidenceKey, "recording_upload_failed", JSONObject().put("file", it.blob)
            .put("reason", reason)) }
    }

    private fun flushAudit() {
        val q = audit ?: return
        q.flush(isPermanent = { it is ApiException && it.status == 400 }) { LG.api().deviceAudit(it) }
    }

    /** מהשירות כל 10 שנ'. כל שלב עצמאי. */
    fun tick(ctx: Context, online: Boolean) {
        if (!LG.store.isLoggedIn) return
        fun step(name: String, f: () -> Unit) {
            try { f() } catch (e: ApiException) {
                if (e.isAuth) throw e
                Log.w(TAG, "$name: ${e.status} ${e.code}")
            } catch (e: IOException) {
                LG.connectivity.onFailure(LG.nowSec())
            } catch (e: Exception) {
                Log.w(TAG, "$name: ${e.javaClass.simpleName}")
            }
        }
        if (!online) return
        step("pause") { processPendingPause() }
        step("refresh") { refresh() }
        step("live") { liveLocation(ctx) }
        step("uploads") { uploadRecordings() }
        step("audit") { flushAudit() }
    }
}
