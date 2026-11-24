package org.lifeguard.app

import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.lifeguard.app.api.ApiClient
import org.lifeguard.app.api.ApiException
import org.lifeguard.app.api.AppNotification
import org.lifeguard.app.api.EscortView
import org.lifeguard.app.api.GeoPoint
import org.lifeguard.app.api.RideRequest
import org.lifeguard.app.api.Vehicle
import org.lifeguard.app.core.Effect
import org.lifeguard.app.core.GeoInput
import org.lifeguard.app.core.RideQueue
import org.lifeguard.app.core.RideText
import org.lifeguard.app.core.RingStateMachine
import java.io.IOException

class GeoInputTest {
    private fun p(s: String) = GeoInput.parse(s)?.let { it.lat to it.lon }

    @Test fun parses_common_map_links_and_pairs() {
        assertEquals(32.0853 to 34.7818, p("32.0853, 34.7818"))
        assertEquals(32.0853 to 34.7818, p("geo:32.0853,34.7818"))
        assertEquals(32.0853 to 34.7818, p("geo:0,0?q=32.0853,34.7818(בית)"))
        assertEquals(32.0853 to 34.7818, p("https://maps.google.com/?q=32.0853,34.7818"))
        assertEquals(32.0853 to 34.7818, p("https://www.google.com/maps/place/X/@32.0853,34.7818,17z"))
        assertEquals(32.0853 to 34.7818, p("https://www.google.com/maps/dir/?api=1&destination=32.0853%2C34.7818"))
        assertEquals(31.7683 to 35.2137, p("https://waze.com/ul?ll=31.7683,35.2137&navigate=yes"))
        assertEquals(31.7683 to 35.2137, p("https://www.openstreetmap.org/#map=17/31.7683/35.2137"))
        assertEquals(31.7683 to 35.2137, p("https://www.openstreetmap.org/?mlat=31.7683&mlon=35.2137#map=17/31.7/35.2"))
    }

    @Test fun rejects_garbage_and_out_of_range() {
        assertNull(p(""))
        assertNull(p("רחוב הרצל 1"))
        assertNull(p("95.0, 34.0"))
        assertNull(p("geo:0,0"))
        assertNull(p("https://goo.gl/maps/abc"))          // קישור מקוצר - אין קואורדינטות בלי רשת
    }
}

class RideQueueTest {
    private fun loc(i: Int) = JSONObject().put("lat", 32.0 + i / 1000.0).put("lon", 34.0)

    @Test fun keeps_order_caps_locations_and_survives_storage() {
        val q = RideQueue()
        repeat(50) { q.addLocation("esc_1", loc(it), it.toDouble()) }
        q.addCheckin("esc_1", "ok", 51.0)
        q.addCheckin("esc_1", "ok", 52.0)      // רק התשובה האחרונה
        assertEquals(RideQueue.MAX_LOCATIONS + 1, q.size)
        assertEquals(32.010, q.items.first().body.getDouble("lat"), 1e-9)   // הישנים נזרקו
        val back = RideQueue.parse(q.toJson())
        assertEquals(q.items.map { it.toJson().toString() }, back.items.map { it.toJson().toString() })
        assertEquals(RideQueue.Op.CHECKIN, back.items.last().kind)
    }

    @Test fun flush_stops_offline_and_drops_closed_rides() {
        val q = RideQueue()
        q.addLocation("esc_1", loc(1), 1.0); q.addCheckin("esc_1", "ok", 2.0); q.addLocation("esc_2", loc(2), 3.0)
        val sent = ArrayList<String>()
        // אין רשת אחרי הראשון -> נעצר ושומר את השאר
        var calls = 0
        assertEquals(RideQueue.Outcome.OFFLINE, q.flush { op -> if (calls++ > 0) throw IOException("down"); sent += op.kind })
        assertEquals(listOf("location"), sent); assertEquals(2, q.size)
        // השרת: הנסיעה esc_1 הסתיימה (409) -> כל הפעולות שלה נזרקות; esc_2 נשלחת
        assertEquals(RideQueue.Outcome.SENT, q.flush { op ->
            if (op.escortId == "esc_1") throw ApiException(409, "closed", "x"); sent += op.escortId })
        assertEquals(0, q.size); assertEquals("esc_2", sent.last())
        // 503 -> ננסה שוב אחר כך
        q.addCheckin("esc_3", "ok", 4.0)
        assertEquals(RideQueue.Outcome.OFFLINE, q.flush { throw ApiException(503, "busy", "x") })
        assertEquals(1, q.size)
        assertEquals(RideQueue.Outcome.IDLE, RideQueue().flush { })
    }
}

class RideApiTest {
    private lateinit var server: MockWebServer
    private lateinit var api: ApiClient

    @Before fun setUp() { server = MockWebServer(); server.start(); api = ApiClient(server.url("/").toString().trimEnd('/'), { "t" }) }
    @After fun tearDown() { server.shutdown() }

    private val rideView = """{"id":"esc_ab12","owner":"dana","destination":"בית","mode":"silent","status":"active",
        "started_at":1000,"deadline":2800,"remaining_seconds":1500,"overdue":false,"signal_lost_since":null,"incident_id":null,
        "kind":"ride","ride_type":"taxi","ride_label":"מונית","vehicle":{"plate":"12-345-67","driver":"משה"},
        "route_source":"osrm","route":[[32.0,34.7],[32.1,34.8]],"destination_point":{"lat":32.1,"lon":34.8},
        "progress":{"remaining_m":2350,"eta_minutes":7},
        "anomaly":{"kind":"stopped","since":1500,"respond_by":1590,"text":"נא לאשר"},
        "watchers":[{"username":"avi","display_name":"אבי"}]}"""

    @Test fun start_ride_sends_contract_fields_and_parses_view() {
        server.enqueue(MockResponse().setBody(rideView))
        val v = api.startEscort(30, "בית", "silent", GeoPoint(32.0, 34.7, 5.0),
            RideRequest("taxi", GeoPoint(32.1, 34.8), Vehicle("12-345-67", " ", "משה".repeat(30))))
        val b = JSONObject(server.takeRequest().body.readUtf8())
        assertEquals("ride", b.getString("kind")); assertEquals("taxi", b.getString("ride_type"))
        assertEquals(30, b.getInt("minutes")); assertEquals("silent", b.getString("mode"))
        assertEquals(32.1, b.getJSONObject("destination_point").getDouble("lat"), 1e-9)
        assertEquals(setOf("lat", "lon"), b.getJSONObject("destination_point").keys().asSequence().toSet())
        val veh = b.getJSONObject("vehicle")
        assertFalse(veh.has("description"))                       // ריק -> לא נשלח
        assertEquals(60, veh.getString("driver").length)          // עד 60 תווים
        assertTrue(v.isRide && v.isSilent)
        assertEquals("מונית", v.rideLabel); assertEquals("osrm", v.routeSource)
        assertEquals(2350.0, v.progress!!.remainingM!!, 1e-9); assertEquals(7.0, v.progress!!.etaMinutes!!, 1e-9)
        assertEquals("stopped", v.anomaly!!.kind); assertEquals(1590.0, v.anomaly!!.respondBy!!, 1e-9)
        assertEquals(listOf("אבי"), v.watchers); assertEquals("12-345-67", v.vehicle!!.plate)
    }

    @Test fun plain_escort_has_no_ride_fields() {
        server.enqueue(MockResponse().setBody("""{"id":"esc_1","status":"active","mode":"loud","deadline":5,"kind":"walk"}"""))
        api.startEscort(20, "", "loud", null)
        val b = JSONObject(server.takeRequest().body.readUtf8())
        assertFalse(b.has("kind")); assertFalse(b.has("ride_type"))
    }

    @Test fun location_with_speed_checkin_and_sos() {
        server.enqueue(MockResponse().setBody("""{"ok":true}"""))
        api.escortLocation("esc_ab12", GeoPoint(32.0, 34.7, accuracyM = 6.0, speedMps = 12.5, capturedAt = 100.0))
        val l = server.takeRequest()
        assertEquals("/v1/escorts/esc_ab12/location", l.path)
        val lb = JSONObject(l.body.readUtf8())
        assertEquals(6.0, lb.getDouble("accuracy_m"), 1e-9); assertEquals(12.5, lb.getDouble("speed_mps"), 1e-9)

        server.enqueue(MockResponse().setBody(rideView.replace("\"anomaly\":{\"kind\":\"stopped\",\"since\":1500,\"respond_by\":1590,\"text\":\"נא לאשר\"}", "\"anomaly\":null")))
        assertNull(api.escortCheckin("esc_ab12", "ok"))           // ok -> תצוגת הנסיעה, לא אירוע
        val c = server.takeRequest()
        assertEquals("/v1/escorts/esc_ab12/checkin", c.path)
        assertEquals("ok", JSONObject(c.body.readUtf8()).getString("status"))

        server.enqueue(MockResponse().setBody("""{"incident":{"id":"inc_9","mode":"silent","status":"active"}}"""))
        val inc = api.escortCheckin("esc_ab12", "help")
        assertEquals("inc_9", inc!!.id); assertTrue(inc.isSilent)
        server.takeRequest()

        server.enqueue(MockResponse().setBody("""{"incident":{"id":"inc_10","mode":"loud","status":"active"}}"""))
        assertEquals("inc_10", api.escortSos("esc_ab12", "loud")!!.id)
        val s = server.takeRequest()
        assertEquals("/v1/escorts/esc_ab12/sos", s.path); assertEquals("loud", JSONObject(s.body.readUtf8()).getString("mode"))

        server.enqueue(MockResponse().setBody(rideView))
        assertEquals("esc_ab12", api.getEscort("esc_ab12").id)
        assertEquals("/v1/escorts/esc_ab12", server.takeRequest().path)
    }

    @Test fun evidence_endpoints() {
        val rec = """{"record":{"format":"x"},"sha256":"aa","signature":"bb","public_key":"cc","algorithm":"Ed25519"}"""
        server.enqueue(MockResponse().setBody(rec)); server.enqueue(MockResponse().setBody(rec))
        assertEquals("Ed25519", api.incidentEvidence("inc_1").getString("algorithm"))
        assertEquals("/v1/incidents/inc_1/evidence", server.takeRequest().path)
        api.evidenceByClient("0b6f1d2e-aaaa-bbbb-cccc-1234567890ab")
        assertEquals("/v1/evidence/by-client/0b6f1d2e-aaaa-bbbb-cccc-1234567890ab", server.takeRequest().path)
    }

    @Test fun status_text() {
        val v = EscortView.parse(JSONObject(rideView))
        val lines = RideText.status(v, 1000.0)
        assertEquals("זמן שנותר: 30:00", lines[0])
        assertTrue(lines.contains("נותרו 2.4 ק\"מ · הגעה משוערת בעוד 7 דק'"))
        assertTrue(lines.contains("עוקבים: אבי"))
        assertEquals("1:01:01", RideText.countdown(3661.0))
        assertEquals("350 מ'", RideText.distance(347.0))
    }
}

class EscortNotificationTest {
    private fun n(id: Long, type: String, payload: String) =
        AppNotification.parse(JSONObject("""{"id":$id,"type":"$type","payload":$payload,"created_at":1}"""))

    @Test fun escort_check_and_watcher_updates() {
        val m = RingStateMachine()
        val fx = m.onNotifications(listOf(
            n(1, "escort_check", """{"escort_id":"esc_1","kind":"deviation","silent":true,"text":"נא לאשר","respond_by":1090.5}"""),
            n(2, "escort_update", """{"escort_id":"esc_2","event":"anomaly","kind":"stopped","text":"דנה עצרה"}"""),
            n(3, "escort_update", """{"escort_id":"esc_2","event":"location"}"""),
            n(4, "escort_update", """{"escort_id":"esc_2","event":"extended","text":"הוארך"}"""),
        ), "me", emptySet(), 10.0)
        val check = fx.filterIsInstance<Effect.EscortCheck>().single()
        assertEquals("esc_1", check.escortId); assertTrue(check.silent); assertEquals(1090.5, check.respondBy!!, 1e-9)
        assertEquals("נא לאשר", check.text)
        val infos = fx.filterIsInstance<Effect.Info>()
        assertEquals(2, infos.size)
        assertFalse(infos.first { it.text == "דנה עצרה" }.quiet)     // חריגה לעוקבים - התראה רגילה
        assertTrue(infos.first { it.text == "הוארך" }.quiet)          // כמו קודם: עדכון שגרתי בשקט
        assertEquals(4, fx.filterIsInstance<Effect.Ack>().size)
        assertNotNull(check)
    }
}

class NationalStatusTest {
    @Test fun parses_not_configured_and_shelters() {
        val server = MockWebServer(); server.start()
        try {
            val api = ApiClient(server.url("/").toString().trimEnd('/'), { "t" })
            server.enqueue(MockResponse().setBody("""{"national_mode":false,"alerts":{"configured":false,"status":"not_configured",
                "text":"התרעות פיקוד העורף לא מחוברות בשרת הזה","official_note":"המקור המחייב"},"my_areas":[],
                "numbers":[{"name":"משטרה","number":"100"},{"name":"מד\"א","number":"101"}],"instructions":{},
                "shelters":[{"lat":32.08,"lon":34.78,"name":"מקלט 12","address":"הרצל 5","distance_m":240,"source":"tlv","source_name":"עיריית ת\"א"}],
                "shelters_note":null,"shelters_coverage":"מאגרים עירוניים רשמיים: תל אביב"}"""))
            val s = api.nationalStatus(32.0853, 34.7818)
            assertEquals("/v1/national/status?lat=32.08530&lon=34.78180", server.takeRequest().path)
            assertEquals("not_configured", s.alertsStatus); assertNull(s.current)
            assertEquals(1, s.shelters.size); assertEquals("עיריית ת\"א", s.shelters[0].source)
            assertEquals(240.0, s.shelters[0].distanceM!!, 1e-9)
            assertEquals(listOf("משטרה" to "100", "מד\"א" to "101"), s.numbers)

            server.enqueue(MockResponse().setBody("""{"alerts":{"status":"ok","current":{"title":"ירי רקטות","kind":"missiles",
                "in_my_areas":true,"instructions":["היכנסו למרחב מוגן"]}},"shelters":[],"shelters_note":"מאגר המקלטים לא נטען"}"""))
            val a = api.nationalStatus()
            assertEquals("/v1/national/status", server.takeRequest().path)
            assertTrue(a.current!!.inMyAreas); assertEquals(listOf("היכנסו למרחב מוגן"), a.current!!.instructions)
            assertEquals("מאגר המקלטים לא נטען", a.sheltersNote)
        } finally { server.shutdown() }
    }
}
