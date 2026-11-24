package org.lifeguard.app

import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Test
import org.lifeguard.app.api.ApiClient
import org.lifeguard.app.api.ApiException
import org.lifeguard.app.api.OsrmWalk
import org.lifeguard.app.core.SafePlace
import org.lifeguard.app.core.SafePlaces

/** נתיבים וגופי בקשה של מתנדבי התמיכה/ההזעקה (server/lifeguard_server.py routes), ולקוח OSRM. */
class HelpersApiAndOsrmTest {
    private lateinit var server: MockWebServer
    private lateinit var api: ApiClient
    private val contract = JSONObject(SafePlacesParityTest.resource("helpers_contract.json")).getJSONObject("views")

    @Before fun setUp() {
        server = MockWebServer(); server.start()
        api = ApiClient(server.url("/").toString().trimEnd('/'), { "tok" })
    }

    @After fun tearDown() { server.shutdown() }

    private fun json(code: Int, body: String) = MockResponse().setResponseCode(code)
        .setHeader("Content-Type", "application/json; charset=utf-8").setBody(body)

    @Test fun volunteer_roles_get_and_put() {
        server.enqueue(json(200, contract.getJSONObject("roles_liaison_unverified").toString()))
        assertEquals(listOf("authority_liaison", "on_scene"), api.volunteerRoles().roles)
        assertEquals("/v1/me/volunteer/roles", server.takeRequest().path)

        server.enqueue(json(200, contract.getJSONObject("roles_liaison_verified").toString()))
        val r = api.setVolunteerRoles(listOf("remote_support", "authority_liaison"), emptyList())
        assertEquals("active", r.effective["authority_liaison"])
        val req = server.takeRequest()
        assertEquals("PUT", req.method); assertEquals("/v1/me/volunteer/roles", req.path)
        val b = JSONObject(req.body.readUtf8())
        assertEquals("remote_support", b.getJSONArray("roles").getString(0))
        assertEquals("he", b.getJSONArray("languages").getString(0))     // ריק -> עברית (כמו השרת)
    }

    @Test fun request_helper_and_respond_paths() {
        server.enqueue(json(200, contract.getJSONObject("owner_request_liaison").toString()))
        val v = api.requestHelper("inc_abc", "authority_liaison", "לא יכולה לדבר")
        assertEquals("notified", v.helpers.single().state)
        var req = server.takeRequest()
        assertEquals("/v1/incidents/inc_abc/request-helper", req.path)
        JSONObject(req.body.readUtf8()).let { assertEquals("authority_liaison", it.getString("kind")); assertEquals("לא יכולה לדבר", it.getString("reason")) }

        server.enqueue(json(200, contract.getJSONObject("liaison_after_action").toString()))
        assertEquals("called_police", api.helperRespond("inc_abc", "called_police").lastAction)
        req = server.takeRequest()
        assertEquals("/v1/incidents/inc_abc/helper-respond", req.path)
        assertEquals("called_police", JSONObject(req.body.readUtf8()).getString("action"))

        server.enqueue(json(409, """{"error":"taken","message":"מישהו/י אחר/ת כבר לקח/ה את הבקשה"}"""))
        try { api.helperRespond("inc_abc", "accept"); fail() } catch (e: ApiException) {
            assertEquals(409, e.status); assertEquals("taken", e.code)
        }
        server.takeRequest()

        server.enqueue(json(200, contract.getJSONObject("owner_liaison_accepted").toString()))
        api.releaseHelper("inc_abc", "Liaison")
        assertEquals("/v1/incidents/inc_abc/helpers/liaison/release", server.takeRequest().path)

        try { api.requestHelper("inc_abc", "on_scene"); fail() } catch (_: IllegalArgumentException) {}
    }

    @Test fun support_request_resumed() {
        server.enqueue(json(200, contract.getJSONObject("support_resumed").toString()))
        val v = api.createSupportRequest("קשה לי", "he")
        assertTrue(v.resumed); assertTrue(v.isSupport)
        val req = server.takeRequest()
        assertEquals("POST", req.method); assertEquals("/v1/support-requests", req.path)
        JSONObject(req.body.readUtf8()).let { assertEquals("קשה לי", it.getString("note")); assertEquals("he", it.getString("language")) }
    }

    // ---------------- OSRM ----------------
    @Test fun osrm_table_and_route_parse_and_send_app_user_agent() {
        val osrm = OsrmWalk(server.url("/routed-foot").toString().trimEnd('/'))
        server.enqueue(json(200, """{"code":"Ok","distances":[[0,850.5,null]],"durations":[[0,610.2,null]]}"""))
        val t = osrm.table(32.0, 34.8, listOf(32.01 to 34.8, 32.02 to 34.81))!!
        assertEquals(850.5 to 610.2, t[0]); assertNull(t[1])
        val req = server.takeRequest()
        assertEquals("/routed-foot/table/v1/foot/34.800000,32.000000;34.800000,32.010000;34.810000,32.020000" +
            "?sources=0&annotations=duration,distance", req.path)
        assertTrue(req.getHeader("User-Agent")!!.startsWith("LifeGuard-Android/"))

        server.enqueue(json(200, """{"code":"Ok","routes":[{"distance":900.0,"duration":700.0}]}"""))
        assertEquals(900.0 to 700.0, osrm.route(32.0, 34.8, 32.01, 34.8))
        assertTrue(server.takeRequest().path!!.startsWith("/routed-foot/route/v1/foot/34.800000,32.000000;34.800000,32.010000"))

        server.enqueue(json(200, """{"code":"NoRoute","routes":[]}"""))
        assertNull(osrm.route(32.0, 34.8, 32.01, 34.8))
        server.enqueue(MockResponse().setResponseCode(503))
        assertNull(osrm.table(32.0, 34.8, listOf(32.01 to 34.8)))
        assertEquals(emptyList<Pair<Double, Double>?>(), osrm.table(32.0, 34.8, emptyList()))
    }

    @Test fun osrm_failure_keeps_offline_estimate() {
        val p = SafePlace("משטרה", 32.01, 34.8, "police", "תחנת משטרה", source = "bundled_osm")
        val est = SafePlaces.choose(32.0, 34.8, listOf(p))!!
        val failed = SafePlaces.choose(32.0, 34.8, listOf(p)) { null }!!
        assertEquals(est, failed)
        val measured = SafePlaces.choose(32.0, 34.8, listOf(p)) { listOf(1500.4 to 1100.6) }!!
        assertTrue(measured.walkMeasured); assertEquals(1500L, measured.walkM); assertEquals(1101L, measured.walkS)
    }
}
