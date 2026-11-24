package org.lifeguard.app

import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.json.JSONArray
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Test
import org.lifeguard.app.api.ApiClient
import org.lifeguard.app.api.ApiException
import org.lifeguard.app.api.GeoPoint
import org.lifeguard.app.api.PrivacyHistoryQuery
import org.lifeguard.app.core.ActivationPolicy
import org.lifeguard.app.core.Hashes
import org.lifeguard.app.core.RecordingUploadPolicy
import java.util.Base64

/** חוזה ה-HTTP של הפרטיות / הקלטות / הורה-ילד / הפעלה לפי ביטחון מול MockWebServer. */
class PrivacyApiContractTest {
    private lateinit var server: MockWebServer
    private lateinit var api: ApiClient

    @Before fun setUp() {
        server = MockWebServer(); server.start()
        api = ApiClient(server.url("/").toString().trimEnd('/'), { "tok" })
    }

    @After fun tearDown() { server.shutdown() }

    private fun ok(body: String) = MockResponse().setResponseCode(200).setHeader("Content-Type", "application/json").setBody(body)
    private fun err(code: Int, e: String) = MockResponse().setResponseCode(code).setBody("""{"error":"$e","message":"x"}""")
    private fun body() = server.takeRequest().let { it to JSONObject(it.body.readUtf8().ifEmpty { "{}" }) }

    private val perms = """{"sharing_paused":false,"record_on_emergency":false,"contacts":[],"who_can_see_location_now":[],
        "volunteers_with_access":[],"guardian_links":[],"recording_policy":null,"my_live_location":null}"""

    @Test fun incident_trigger_and_low_confidence_422() {
        server.enqueue(err(422, "low_confidence"))
        try {
            api.createIncident("cid", "loud", risk = JSONObject().put("level", "LOW").put("confidence", 0.2),
                trigger = "wellness_no_response"); fail()
        } catch (e: ApiException) {
            assertEquals(ActivationPolicy.CreateFailure.LowConfidence, ActivationPolicy.classifyCreateError(e.status, e.code))
        }
        val (_, b) = body()
        assertEquals("wellness_no_response", b.getString("trigger"))
        assertEquals(0.2, b.getJSONObject("risk").getDouble("confidence"), 0.0)
        // ידני: trigger "manual" ובלי risk
        server.enqueue(ok("""{"id":"inc_1","status":"active","activation":"manual"}"""))
        assertEquals("manual", api.createIncident("cid2", "loud", trigger = "manual").activation)
        val (_, m) = body()
        assertEquals("manual", m.getString("trigger")); assertFalse(m.has("risk"))
    }

    @Test fun confirm_incident() {
        server.enqueue(ok("""{"id":"inc_1","status":"active","activation":"confirmed_by_user"}"""))
        assertEquals("confirmed_by_user", api.confirmIncident("inc_1").activation)
        val r = server.takeRequest()
        assertEquals("POST", r.method); assertEquals("/v1/incidents/inc_1/confirm", r.path); assertEquals(0L, r.bodySize)
    }

    @Test fun location_permissions_endpoints() {
        repeat(3) { server.enqueue(ok(perms)) }
        api.setLocationMode("Avi", "always")
        api.setRecordingAccess("avi", true)
        api.pauseLocationSharing(true)
        val (r1, b1) = body(); assertEquals("PUT", r1.method); assertEquals("/v1/relationships/avi/location-mode", r1.path)
        assertEquals("always", b1.getString("mode"))
        val (r2, b2) = body(); assertEquals("/v1/relationships/avi/recording-access", r2.path); assertTrue(b2.getBoolean("allowed"))
        val (r3, b3) = body(); assertEquals("POST", r3.method); assertEquals("/v1/me/location-sharing/pause", r3.path)
        assertTrue(b3.getBoolean("paused"))
        try { api.setLocationMode("avi", "sometimes"); fail() } catch (_: IllegalArgumentException) { }
        try { api.setLocationMode("../admin", "off"); fail() } catch (_: IllegalArgumentException) { }
        assertEquals(3, server.requestCount)
    }

    @Test fun live_location_and_contact_location() {
        server.enqueue(ok("""{"stored":false,"reason":"no_always_contacts"}"""))
        val r = api.putLiveLocation(GeoPoint(32.0, 34.0, 15.0, true, "gps", capturedAt = 1700000000.5))
        assertFalse(r.stored); assertEquals("no_always_contacts", r.reason)
        val (req, b) = body()
        assertEquals("PUT", req.method); assertEquals("/v1/me/live-location", req.path)
        assertEquals(setOf("lat", "lon", "accuracy_m", "is_precise", "captured_at"), b.keys().asSequence().toSet())
        server.enqueue(ok("""{"available":true,"lat":1,"lon":2,"accuracy_m":5,"is_precise":true,"captured_at":10,
            "received_at":11,"age_seconds":400,"stale":true,"label":"מיקום ישן (6 דק') - לא חי"}"""))
        val c = api.contactLocation("Dana")
        assertTrue(c.stale); assertEquals(400.0, c.ageSeconds!!, 0.0)
        assertEquals("/v1/contacts/dana/location", server.takeRequest().path)
        server.enqueue(err(403, "not_permitted"))
        try { api.contactLocation("dana"); fail() } catch (e: ApiException) { assertEquals(403, e.status); assertEquals("not_permitted", e.code) }
    }

    @Test fun recording_policy_upload_and_errors() {
        server.enqueue(ok("""{"record_on_emergency":true,"discreet":false,"discreet_guardians":[],"upload":true,
            "recipients":["אבי"],"max_upload_bytes":1200000,"retention_days":30,"notes":[]}"""))
        server.enqueue(ok("""{"record_on_emergency":false,"discreet":false,"upload":false,"recipients":[]}"""))
        assertTrue(api.recordingPolicy().recordOnEmergency)
        assertFalse(api.setRecordOnEmergency(false).recordOnEmergency)
        assertEquals("/v1/me/recording-policy", server.takeRequest().path)
        val (pr, pb) = body(); assertEquals("PUT", pr.method); assertEquals("/v1/me/recording", pr.path)
        assertFalse(pb.getBoolean("record_on_emergency"))

        val data = ByteArray(3000) { (it % 251).toByte() }
        val sha = Hashes.sha256(data)
        server.enqueue(ok("""{"id":"rec_ab12","sha256":"$sha","recipients":["אבי"]}"""))
        val up = api.uploadRecording("inc_1", "seg-abc", data, sha, "audio/mp4", 100.0, 160.0)
        assertEquals("rec_ab12", up.id); assertFalse(up.duplicate)
        val (ur, ub) = body()
        assertEquals("/v1/incidents/inc_1/recordings", ur.path)
        assertEquals(setOf("client_id", "data_b64", "sha256", "mime", "started_at", "ended_at"), ub.keys().asSequence().toSet())
        assertTrue(Base64.getDecoder().decode(ub.getString("data_b64")).contentEquals(data))
        assertEquals(sha, ub.getString("sha256"))
        server.enqueue(ok("""{"id":"rec_ab12","duplicate":true}"""))
        assertTrue(api.uploadRecording("inc_1", "seg-abc", data, sha, "audio/mp4", 100.0, 160.0).duplicate)
        server.takeRequest()
        for ((code, e, expect) in listOf(Triple(409, "not_confirmed", RecordingUploadPolicy.Outcome.Retry("not_confirmed")),
            Triple(403, "recording_disabled", RecordingUploadPolicy.Outcome.Drop("recording_disabled")),
            Triple(409, "too_late", RecordingUploadPolicy.Outcome.Drop("too_late")),
            Triple(413, "too_large", RecordingUploadPolicy.Outcome.Drop("too_large")))) {
            server.enqueue(err(code, e))
            try { api.uploadRecording("inc_1", "seg-x", data, sha, "audio/mp4", 1.0, 2.0); fail() } catch (ex: ApiException) {
                assertEquals(expect, RecordingUploadPolicy.classify(ex.status, ex.code))
            }
            server.takeRequest()
        }
        server.enqueue(ok("""{"recordings":[{"id":"rec_ab12","size":3000,"sha256":"$sha","mime":"audio/mp4","started_at":100,"ended_at":160,"uploaded_at":170}]}"""))
        assertEquals(3000L, api.listRecordings("inc_1").single().size)
        server.enqueue(ok("""{"id":"rec_ab12","mime":"audio/mp4","sha256":"$sha","data_b64":"AAA="}"""))
        assertEquals("AAA=", api.getRecording("inc_1", "rec_ab12").getString("data_b64"))
        server.enqueue(ok("""{"ok":true}"""))
        api.deleteRecording("rec_ab12")
        assertEquals("/v1/incidents/inc_1/recordings", server.takeRequest().path)
        assertEquals("/v1/incidents/inc_1/recordings/rec_ab12", server.takeRequest().path)
        val d = server.takeRequest(); assertEquals("DELETE", d.method); assertEquals("/v1/me/recordings/rec_ab12", d.path)
    }

    @Test fun device_audit_history_permissions_export() {
        server.enqueue(ok("""{"accepted":2}"""))
        val events = JSONArray().put(JSONObject().put("event_id", "a1234567").put("device_ts", 1.0).put("action", "recording_started")
            .put("result", "completed").put("details", JSONObject())).put(JSONObject().put("event_id", "a7654321").put("device_ts", 2.0)
            .put("action", "evidence_exported").put("result", "failed").put("details", JSONObject()))
        assertEquals(2, api.deviceAudit(events))
        val (ar, ab) = body(); assertEquals("POST", ar.method); assertEquals("/v1/me/audit", ar.path)
        assertEquals(2, ab.getJSONArray("events").length())

        server.enqueue(ok("""{"events":[{"event_id":"ev_1","server_ts":5,"action":"location_access","result":"completed",
            "recipient":"avi","recipient_display":"אבי","source":"server","details":{}}],"count":1}"""))
        val h = api.privacyHistory(PrivacyHistoryQuery(from = 10.0, to = 20.5, contact = "Avi", type = "location_access", q = "אבי", limit = 50))
        assertEquals("location_access", h.single().action)
        val hr = server.takeRequest()
        val u = hr.requestUrl!!
        assertEquals("/v1/me/privacy/history", u.encodedPath)
        assertEquals("10.000", u.queryParameter("from")); assertEquals("20.500", u.queryParameter("to"))
        assertEquals("avi", u.queryParameter("contact")); assertEquals("location_access", u.queryParameter("type"))
        assertEquals("אבי", u.queryParameter("q")); assertEquals("50", u.queryParameter("limit"))

        server.enqueue(ok(perms))
        assertFalse(api.privacyPermissions().sharingPaused)
        assertEquals("/v1/me/privacy/permissions", server.takeRequest().path)

        // ייצוא: הטקסט המקורי נשמר כפי שהוא (בלי סריאליזציה מחדש שעלולה לשבור אימות חתימה)
        val raw = """{"record":{"format":"lifeguard-privacy-report-v1","generated_at":1700000000.123456},"sha256":"ab","signature":"cd","public_key":"ef","algorithm":"Ed25519"}"""
        server.enqueue(ok(raw))
        assertEquals(raw, api.privacyExportRaw())
        assertEquals("/v1/me/privacy/export", server.takeRequest().path)
    }

    @Test fun guardian_endpoints() {
        val view = """{"id":"gl_ab12","guardian":"mom","guardian_display":"אמא","child":"kid","child_display":"נועה","my_role":"guardian",
            "status":"pending","child_age":10,"guardian_approved":true,"child_approved":false,"guardian_verified":false,
            "recording_consent":{"guardian":false,"child":false},"discreet_recording_active":false}"""
        server.enqueue(ok("""{"links":[$view]}"""))
        repeat(3) { server.enqueue(ok(view)) }
        server.enqueue(ok("""{"ok":true}"""))
        assertEquals("gl_ab12", api.guardianLinks().single().id)
        assertEquals(10, api.guardianInvite("kid", "guardian", 10).childAge)
        api.guardianApprove("gl_ab12")
        api.guardianRecordingConsent("gl_ab12", true)
        api.guardianRevoke("gl_ab12")
        assertEquals("/v1/me/guardian", server.takeRequest().path)
        val (ir, ib) = body(); assertEquals("/v1/guardian/links", ir.path)
        assertEquals("kid", ib.getString("other")); assertEquals("guardian", ib.getString("i_am")); assertEquals(10, ib.getInt("child_age"))
        assertEquals("/v1/guardian/links/gl_ab12/approve", server.takeRequest().path)
        val (cr, cb) = body(); assertEquals("/v1/guardian/links/gl_ab12/recording-consent", cr.path); assertTrue(cb.getBoolean("consent"))
        assertEquals("/v1/guardian/links/gl_ab12/revoke", server.takeRequest().path)
        try { api.guardianInvite("kid", "parent", null); fail() } catch (_: IllegalArgumentException) { }
    }
}
