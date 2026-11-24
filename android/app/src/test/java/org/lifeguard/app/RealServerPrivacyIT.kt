package org.lifeguard.app

import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Assume.assumeTrue
import org.junit.Test
import org.lifeguard.app.api.ApiClient
import org.lifeguard.app.api.ApiException
import org.lifeguard.app.api.GeoPoint
import org.lifeguard.app.api.PrivacyHistoryQuery
import org.lifeguard.app.core.ActivationPolicy
import org.lifeguard.app.core.Activations
import org.lifeguard.app.core.DeviceAuditQueue
import org.lifeguard.app.core.GuardianText
import org.lifeguard.app.core.Hashes
import org.lifeguard.app.core.MemoryTextStore
import org.lifeguard.app.core.MotionSignal
import org.lifeguard.app.core.PrivacyHistoryText
import org.lifeguard.app.core.RecordingUploadPolicy
import org.lifeguard.app.core.Triggers
import java.util.UUID

/**
 * הפעלה לפי ביטחון, הרשאות מיקום, מיקום חי, הקלטות, יומן ביקורת/פרטיות והורה-ילד - מול השרת האמיתי.
 * רץ רק עם LIFEGUARD_IT_URL (כמו RealServerIT / RealServerRoadmapIT).
 */
class RealServerPrivacyIT {
    private val url = System.getProperty("lifeguard.it.url").orEmpty()
    private val http = OkHttpClient()

    private class User(val name: String, val display: String, val api: ApiClient, val token: String)

    private fun user(prefix: String): User {
        val name = prefix + UUID.randomUUID().toString().take(8)
        val anon = ApiClient(url, { null })
        anon.register(name, "password123", "שם $prefix")
        val t = anon.login(name, "password123", "jvm-it").token
        return User(name, "שם $prefix", ApiClient(url, { t }), t)
    }

    private fun raw(u: User, method: String, path: String, body: JSONObject = JSONObject()) {
        val req = Request.Builder().url("$url/v1/$path").header("Authorization", "Bearer ${u.token}")
            .method(method, body.toString().toRequestBody("application/json".toMediaType())).build()
        http.newCall(req).execute().use { r -> assertTrue("$path -> ${r.code} ${r.body?.string()}", r.isSuccessful) }
    }

    private fun connect(owner: User, contact: User) {
        raw(owner, "POST", "relationships", JSONObject().put("contact", contact.name))
        raw(contact, "POST", "relationships/${owner.name}/respond", JSONObject().put("accept", true))
    }

    private inline fun expectApi(status: Int, code: String?, block: () -> Unit) {
        try { block(); fail("expected $status") } catch (e: ApiException) {
            assertEquals(e.message, status, e.status); code?.let { assertEquals(it, e.code) }
        }
    }

    private fun risk(conf: Double) = JSONObject().put("level", "MEDIUM").put("score", 3.0).put("confidence", conf)
        .put("reasons", JSONArray().put("it")).put("signals", JSONArray())

    @Test fun activationLocationRecordingAuditAgainstRealServer() {
        assumeTrue("LIFEGUARD_IT_URL not set", url.isNotEmpty())
        val owner = user("pv"); val contact = user("pc")
        connect(owner, contact)
        val loc = GeoPoint(32.0700, 34.7800, 8.0, true)

        // ---- הרשאות מיקום + מיקום חי ----
        var perms = owner.api.privacyPermissions()
        assertEquals("emergency", perms.contacts.single().locationMode)         // ברירת מחדל = ההתנהגות הקיימת
        assertFalse(owner.api.putLiveLocation(loc.copy(capturedAt = owner.api.serverNow())).stored)
        perms = owner.api.setLocationMode(contact.name, "always")
        assertEquals(1, perms.alwaysContacts)
        assertTrue(perms.whoCanSeeLocationNow.isNotEmpty())
        assertTrue(owner.api.putLiveLocation(loc.copy(capturedAt = owner.api.serverNow())).stored)
        val cl = contact.api.contactLocation(owner.name)
        assertTrue(cl.available); assertFalse(cl.stale); assertEquals(32.07, cl.lat!!, 1e-6)
        assertNotNull(owner.api.privacyPermissions().myLiveCapturedAt)
        // השהיה: המיקום החי נמחק מיד, איש הקשר מקבל 403, העלאה לא נשמרת
        perms = owner.api.pauseLocationSharing(true)
        assertTrue(perms.sharingPaused); assertEquals("paused", perms.contacts.single().effectiveNow)
        assertFalse(perms.hasMyLiveLocation)
        expectApi(403, "not_permitted") { contact.api.contactLocation(owner.name) }
        assertEquals("paused", owner.api.putLiveLocation(loc.copy(capturedAt = owner.api.serverNow())).reason)
        owner.api.pauseLocationSharing(false)
        owner.api.setLocationMode(contact.name, "off")
        expectApi(403, "not_permitted") { contact.api.contactLocation(owner.name) }

        // ---- הפעלה לפי ביטחון ----
        // הערכה אמיתית מהמכשיר (אות "אין תגובה" כלול) עוברת את אימות השרת
        val now = System.currentTimeMillis()
        val real = ActivationPolicy.wellnessRisk(listOf(MotionSignal("fall", 0.9, now, "נפילה"),
            MotionSignal("impact", 0.8, now, "מכה")), "no_answer", now)
        val hi = owner.api.createIncident("it-" + UUID.randomUUID(), "loud", location = loc, risk = real.toJson(),
            trigger = Triggers.WELLNESS_NO_RESPONSE)
        assertEquals(Activations.CONFIRMED_HIGH, hi.activation)
        owner.api.setIncidentStatus(hi.id, "cancelled")
        expectApi(422, "low_confidence") {
            owner.api.createIncident("it-" + UUID.randomUUID(), "loud", location = loc, risk = risk(0.2), trigger = Triggers.WELLNESS_NO_RESPONSE)
        }
        val mid = owner.api.createIncident("it-" + UUID.randomUUID(), "loud", location = loc, risk = risk(0.5),
            trigger = Triggers.WELLNESS_NO_RESPONSE)
        assertEquals(Activations.UNCONFIRMED, mid.activation)
        assertEquals(Activations.UNCONFIRMED, ActivationPolicy.expectedActivation(Triggers.WELLNESS_NO_RESPONSE, 0.5))
        // איש קשר במצב "off": מקבל את האירוע בלי מיקום
        val seen = contact.api.getIncident(mid.id)
        assertTrue(seen.locationWithheld); assertEquals(null, seen.location)
        // הקלטה לאירוע לא מאושר נדחית (ממשיכים לנסות אחרי אישור)
        owner.api.setRecordingAccess(contact.name, true)
        var policy = owner.api.setRecordOnEmergency(true)
        assertTrue(policy.recordOnEmergency && policy.upload)
        assertTrue(policy.recipients.contains(contact.display))
        val data = ByteArray(2048) { (it * 7).toByte() }
        val sha = Hashes.sha256(data)
        try { owner.api.uploadRecording(mid.id, "seg-mid-1", data, sha, "audio/mp4", 1.0, 2.0); fail() } catch (e: ApiException) {
            assertEquals(RecordingUploadPolicy.Outcome.Retry("not_confirmed"), RecordingUploadPolicy.classify(e.status, e.code))
        }
        assertEquals(Activations.CONFIRMED_BY_USER, owner.api.confirmIncident(mid.id).activation)
        val segId = "seg-" + UUID.randomUUID().toString().take(12)
        val nowS = owner.api.serverNow()
        val up = owner.api.uploadRecording(mid.id, segId, data, sha, "audio/mp4", nowS - 60, nowS)
        assertEquals(sha, up.sha256)
        assertTrue(up.recipients.contains(contact.display))
        assertTrue(owner.api.uploadRecording(mid.id, segId, data, sha, "audio/mp4", nowS - 60, nowS).duplicate)
        val list = contact.api.listRecordings(mid.id)
        assertEquals(up.id, list.single().id)
        assertEquals(java.util.Base64.getEncoder().encodeToString(data), contact.api.getRecording(mid.id, up.id).getString("data_b64"))
        owner.api.deleteRecording(up.id)
        assertTrue(owner.api.listRecordings(mid.id).isEmpty())
        policy = owner.api.setRecordOnEmergency(false)
        assertFalse(policy.recordOnEmergency)
        try { owner.api.uploadRecording(mid.id, "seg-off-1", data, sha, "audio/mp4", 1.0, 2.0); fail() } catch (e: ApiException) {
            assertEquals(RecordingUploadPolicy.Outcome.Drop("recording_disabled"), RecordingUploadPolicy.classify(e.status, e.code))
        }
        owner.api.setIncidentStatus(mid.id, "cancelled")

        // ---- אירועי ביקורת מהמכשיר: אידמפוטנטי לפי event_id ----
        var t = 1_700_000_000.0
        val q = DeviceAuditQueue(MemoryTextStore(), clock = { t++ })
        q.add("sos_activated_offline", details = JSONObject().put("channel", "sms"))
        q.add("false_alarm_cancelled", details = JSONObject().put("reason", "low_confidence"))
        val ids = q.pending().map { it.eventId }
        val batch = JSONArray().apply { q.pending().forEach { put(it.toJson()) } }
        assertEquals(2, owner.api.deviceAudit(batch))
        owner.api.deviceAudit(batch)                                               // שליחה חוזרת אחרי "ניתוק"
        assertEquals(0, q.flush { owner.api.deviceAudit(it) }.remaining)
        val hist = owner.api.privacyHistory()
        val dev = hist.filter { it.source == "device" }
        assertEquals(2, dev.size)                                                  // בלי כפילויות
        assertTrue(dev.all { e -> ids.any { e.eventId.endsWith(it) } })
        assertTrue(dev.all { it.deviceTs != null && it.serverTs != null && it.deviceTs != it.serverTs })
        // מסירת מיקום לאיש הקשר נרשמה (נמסר - לא "נצפה")
        val access = owner.api.privacyHistory(PrivacyHistoryQuery(type = "location_access"))
        assertTrue(access.any { PrivacyHistoryText.result(it) == PrivacyHistoryText.Result.COMPLETED && it.recipient == contact.name })
        assertTrue(access.any { PrivacyHistoryText.result(it) == PrivacyHistoryText.Result.FAILED })
        assertTrue(access.first { it.recipient == contact.name }.let { PrivacyHistoryText.describe(it, owner.name).contains("נמסר לאפליקציה") })
        assertTrue(owner.api.privacyHistory(PrivacyHistoryQuery(contact = contact.name)).isNotEmpty())
        assertTrue(owner.api.privacyHistory(PrivacyHistoryQuery(type = "emergency_")).any { it.action == "emergency_confirmed" })
        val exp = JSONObject(owner.api.privacyExportRaw())
        assertEquals("Ed25519", exp.getString("algorithm"))
        assertEquals(128, exp.getString("signature").length)
        assertEquals("lifeguard-privacy-report-v1", exp.getJSONObject("record").getString("format"))
    }

    @Test fun guardianLinkAgainstRealServer() {
        assumeTrue("LIFEGUARD_IT_URL not set", url.isNotEmpty())
        val parent = user("gp"); val child = user("gc")
        val inv = parent.api.guardianInvite(child.name, "guardian", 12)
        assertEquals("pending", inv.status); assertTrue(inv.iAmGuardian); assertEquals(12, inv.childAge)
        val mine = child.api.guardianLinks().single()
        assertTrue(GuardianText.canApprove(mine))
        val approved = child.api.guardianApprove(mine.id)
        assertTrue(approved.isActive)
        parent.api.guardianRecordingConsent(inv.id, true)
        val both = child.api.guardianRecordingConsent(inv.id, true)
        assertTrue(both.consentGuardian && both.consentChild)
        // בלי אימות הורה ע"י מנהל/ת -> אין הקלטה דיסקרטית (השרת מחליט)
        assertFalse(both.guardianVerified); assertFalse(both.discreetRecordingActive)
        assertTrue(GuardianText.statusLines(both).any { it.startsWith("ההורה עוד לא אומת") })
        assertFalse(child.api.recordingPolicy().discreet)
        assertEquals(1, child.api.privacyPermissions().guardianLinks.size)
        child.api.guardianRevoke(inv.id)
        assertTrue(parent.api.guardianLinks().isEmpty())
        expectApi(404, null) { parent.api.guardianRevoke(inv.id) }
    }
}
