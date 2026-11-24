package org.lifeguard.app

import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Test
import org.lifeguard.app.api.ApiClient
import org.lifeguard.app.api.ApiException
import org.lifeguard.app.api.GeoPoint
import java.io.IOException

class ApiClientTest {
    private lateinit var server: MockWebServer
    private lateinit var api: ApiClient
    private var token: String? = "tok123"

    @Before fun setUp() {
        server = MockWebServer(); server.start()
        api = ApiClient(server.url("/").toString().trimEnd('/'), { token })
    }

    @After fun tearDown() { server.shutdown() }

    private fun json(code: Int, body: String) = MockResponse().setResponseCode(code)
        .setHeader("Content-Type", "application/json; charset=utf-8").setBody(body)

    @Test fun login_parses_token_and_sends_body_without_auth() {
        server.enqueue(json(200, """{"token":"T","username":"dana","display_name":"דנה","expires_at":1}"""))
        token = null
        val r = api.login("Dana", "pw123456", "Pixel")
        assertEquals("T", r.token); assertEquals("dana", r.username); assertEquals("דנה", r.displayName)
        val req = server.takeRequest()
        assertEquals("POST", req.method); assertEquals("/v1/auth/login", req.path)
        assertNull(req.getHeader("Authorization"))
        val b = JSONObject(req.body.readUtf8())
        assertEquals("Dana", b.getString("username")); assertEquals("Pixel", b.getString("device"))
        assertTrue(req.getHeader("Content-Length")!!.toInt() > 0)   // השרת מסרב ל-chunked
    }

    @Test fun server_error_becomes_ApiException_with_hebrew_message() {
        server.enqueue(json(401, """{"error":"bad_credentials","message":"שם משתמש או סיסמה שגויים"}"""))
        try { api.login("a", "b", ""); fail() } catch (e: ApiException) {
            assertEquals(401, e.status); assertEquals("bad_credentials", e.code)
            assertEquals("שם משתמש או סיסמה שגויים", e.message); assertTrue(e.isAuth); assertFalse(e.isRetryable)
        }
        server.enqueue(MockResponse().setResponseCode(503).setBody("oops"))
        try { api.me(); fail() } catch (e: ApiException) { assertEquals(503, e.status); assertTrue(e.isRetryable) }
    }

    @Test fun poll_parses_notifications_and_sends_bearer() {
        server.enqueue(json(200, """{"notifications":[
            {"id":7,"username":"me","incident_id":"inc_1","type":"incident_new","created_at":1700000000.5,"received_at":null,
             "payload":{"id":"inc_1","mode":"loud","status":"active","owner_display":"דנה","ring":true,
                        "location":{"lat":32.1,"lon":34.8,"is_precise":1,"accuracy_m":5}}},
            {"id":8,"username":"me","incident_id":"inc_1","type":"incident_update","created_at":1700000001,
             "payload":{"incident_id":"inc_1","event":"ring_stopped"}}]}"""))
        val list = api.poll(5, 25)
        assertEquals(2, list.size)
        assertEquals(7L, list[0].id); assertEquals("incident_new", list[0].type); assertEquals("inc_1", list[0].incidentId)
        assertEquals(1700000000.5, list[0].createdAt, 0.001)
        assertEquals("ring_stopped", list[1].payload.getString("event"))
        val req = server.takeRequest()
        assertEquals("/v1/notifications?after=5&wait=25", req.path)
        assertEquals("Bearer tok123", req.getHeader("Authorization"))
    }

    @Test fun ack_posts_to_received() {
        server.enqueue(json(200, """{"ok":true}"""))
        api.ack(42)
        val req = server.takeRequest()
        assertEquals("POST", req.method); assertEquals("/v1/notifications/42/received", req.path)
        // השרת לא קורא גוף בנתיב הזה - גוף שנשאר בחיבור keep-alive שובר את הבקשה הבאה (501)
        assertEquals(0L, req.bodySize); assertEquals("0", req.getHeader("Content-Length"))
    }

    @Test fun cursor_parses_last_id_and_unreceived() {
        server.enqueue(json(200, """{"last_id":99,"unreceived":[{"id":90,"type":"info","incident_id":null,
            "payload":{"from_display":"x","text":"hi"},"created_at":1}]}"""))
        val c = api.cursor()
        assertEquals(99L, c.lastId); assertEquals(1, c.unreceived.size); assertNull(c.unreceived[0].incidentId)
        assertEquals("/v1/notifications/cursor", server.takeRequest().path)
    }

    @Test fun create_incident_sends_contract_body_and_parses_view() {
        server.enqueue(json(200, """{"id":"inc_abc","mode":"silent","emergency_type":"unknown","severity":"high",
            "status":"active","owner":"dana","owner_display":"דנה","my_role":"owner","my_state":null,"full_access":true,
            "signal_lost_since":null,"created_at":5,
            "location":{"lat":32.0,"lon":34.0,"accuracy_m":12.5,"source":"gps","is_precise":1,"captured_at":4,"received_at":5},
            "recipients":[{"username":"avi","display_name":"אבי","role":"trusted","state":"notified","response":null}],
            "messages":[{"id":3,"sender":"avi","sender_display":"אבי","role":"trusted","text":"אני בדרך אליך",
                         "quick":"on_my_way","created_at":10,"read_by":["דנה"]}],
            "quick_replies":{"ok":"אני בסדר עכשיו","hurry":"בואו מהר"}}"""))
        val v = api.createIncident("cid-1", "silent", location = GeoPoint(32.0, 34.0, 12.5, true, "gps"))
        assertEquals("inc_abc", v.id); assertTrue(v.isSilent); assertEquals("owner", v.myRole)
        assertTrue(v.location!!.isPrecise); assertEquals(12.5, v.location!!.accuracyM!!, 0.0)
        assertEquals(1, v.responders.size); assertEquals("אבי", v.responders[0].displayName)
        val m = v.messages.single()
        assertEquals("on_my_way", m.quick); assertEquals(listOf("דנה"), m.readBy); assertEquals("trusted", m.role)
        assertEquals(listOf("ok", "hurry"), v.quickReplies.keys.toList())
        assertNull(v.signalLostSince); assertNull(v.ring)
        val b = JSONObject(server.takeRequest().body.readUtf8())
        assertEquals("cid-1", b.getString("client_id")); assertEquals("silent", b.getString("mode"))
        val loc = b.getJSONObject("location")
        assertEquals(32.0, loc.getDouble("lat"), 0.0); assertTrue(loc.getBoolean("is_precise")); assertEquals("gps", loc.getString("source"))
    }

    @Test fun messages_quick_and_text_and_read() {
        server.enqueue(json(200, """{"ok":true,"id":11}"""))
        server.enqueue(json(200, """{"ok":true,"id":12}"""))
        server.enqueue(json(200, """{"ok":true}"""))
        assertEquals(11L, api.sendMessage("inc_1", quick = "hurry"))
        assertEquals(12L, api.sendMessage("inc_1", text = "שלום"))
        api.markRead("inc_1", 12)
        val r1 = server.takeRequest(); assertEquals("/v1/incidents/inc_1/messages", r1.path)
        assertEquals("hurry", JSONObject(r1.body.readUtf8()).getString("quick"))
        assertEquals("שלום", JSONObject(server.takeRequest().body.readUtf8()).getString("text"))
        val r3 = server.takeRequest(); assertEquals("/v1/incidents/inc_1/messages/read", r3.path)
        assertEquals(12, JSONObject(r3.body.readUtf8()).getInt("up_to_id"))
    }

    @Test fun respond_patch_presence_and_escort_paths() {
        repeat(5) { server.enqueue(json(200, """{"ok":true,"id":"x","status":"resolved"}""")) }
        api.respond("inc_1", "accept")
        api.setIncidentStatus("inc_1", "resolved")
        api.putPresence(GeoPoint(1.0, 2.0, 1500.0, false, "network"))
        api.escortLocation("esc_ab", GeoPoint(1.0, 2.0))
        api.escortAction("esc_ab", "arrived")
        val a = server.takeRequest(); assertEquals("/v1/incidents/inc_1/respond", a.path)
        assertEquals("accept", JSONObject(a.body.readUtf8()).getString("action"))
        val p = server.takeRequest(); assertEquals("PATCH", p.method)
        assertEquals("resolved", JSONObject(p.body.readUtf8()).getString("status"))
        val pr = server.takeRequest(); assertEquals("PUT", pr.method); assertEquals("/v1/me/presence", pr.path)
        assertFalse(JSONObject(pr.body.readUtf8()).getBoolean("is_precise"))
        assertEquals("/v1/escorts/esc_ab/location", server.takeRequest().path)
        assertEquals("/v1/escorts/esc_ab/arrived", server.takeRequest().path)
    }

    @Test fun no_token_fails_without_network() {
        token = null
        try { api.inbox(); fail() } catch (e: ApiException) { assertEquals("no_token", e.code) }
        assertEquals(0, server.requestCount)
    }

    @Test fun rejects_path_injection_in_ids() {
        try { api.getIncident("../admin"); fail() } catch (e: IllegalArgumentException) { }
    }

    @Test(expected = IOException::class) fun network_failure_is_IOException() {
        server.shutdown()
        api.cursor()
    }

    @Test fun date_header_updates_clock_skew() {
        val fmt = java.text.SimpleDateFormat("EEE, dd MMM yyyy HH:mm:ss 'GMT'", java.util.Locale.US)
            .apply { timeZone = java.util.TimeZone.getTimeZone("GMT") }
        val inOneHour = fmt.format(java.util.Date(System.currentTimeMillis() + 3600_000))
        server.enqueue(json(200, "{}").setHeader("Date", inOneHour))
        api.me()
        assertEquals(3600.0, api.serverSkewSeconds, 5.0)
    }
}
