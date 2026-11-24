package org.lifeguard.app

import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.lifeguard.app.api.ApiClient
import org.lifeguard.app.api.GeoPoint
import org.lifeguard.app.core.MotionSignal
import org.lifeguard.app.core.PendingSos
import org.lifeguard.app.core.RiskScorer

/** משימה 3: risk נשלח ב-POST /v1/incidents רק ל-SOS מזיהוי; SOS ידני בלי risk. */
class RiskPayloadTest {
    private val at = 1_700_000_000_000L

    private fun riskSos() = PendingSos("cid-1", "loud", 1.0, GeoPoint(32.0, 34.0), source = "wellness_check:motion",
        riskJson = RiskScorer.assess(listOf(MotionSignal("fall", 0.9, at, "נפילה"), MotionSignal("impact", 0.7, at, "מכה")),
            why = "no_answer").toJson().toString())

    @Test fun pending_sos_keeps_risk_through_storage_roundtrip() {
        val p = riskSos()
        val back = PendingSos.parse(JSONObject(p.toJson().toString()))
        assertEquals(p, back)
        assertEquals("HIGH", back.risk()!!.getString("level"))
        // SOS ידני: אין risk
        val manual = PendingSos("cid-2", "loud", 1.0, null)
        assertNull(PendingSos.parse(manual.toJson()).risk())
        assertFalse(manual.toJson().has("risk_json"))
    }

    @Test fun create_incident_sends_risk_only_when_present() {
        val server = MockWebServer(); server.start()
        try {
            val api = ApiClient(server.url("/").toString().trimEnd('/'), { "t" })
            val view = """{"id":"inc_1","mode":"loud","status":"active"}"""
            server.enqueue(MockResponse().setBody(view)); server.enqueue(MockResponse().setBody(view))

            val p = riskSos()
            api.createIncident(p.clientId, p.mode, location = p.location, risk = p.risk())
            val b = JSONObject(server.takeRequest().body.readUtf8())
            val r = b.getJSONObject("risk")
            assertEquals(setOf("level", "score", "confidence", "reasons", "signals"), r.keys().asSequence().toSet())
            assertTrue(r.getString("level") in setOf("LOW", "MEDIUM", "HIGH"))
            assertTrue(r.getDouble("confidence") in 0.0..1.0)
            val sig = r.getJSONArray("signals")
            assertEquals(2, sig.length())
            for (i in 0 until sig.length()) {
                val s = sig.getJSONObject(i)
                assertTrue(s.getString("source") in setOf("fall", "impact", "struggle", "sudden_stop", "no_signal"))
                assertEquals(1_700_000_000.0, s.getDouble("at"), 1e-6)
            }
            assertTrue((0 until r.getJSONArray("reasons").length()).map { r.getJSONArray("reasons").getString(it) }
                .contains("לא נענתה השאלה \"הכל בסדר?\""))

            val manual = PendingSos("cid-2", "loud", 1.0, null)
            api.createIncident(manual.clientId, manual.mode, risk = manual.risk())
            assertFalse(JSONObject(server.takeRequest().body.readUtf8()).has("risk"))
        } finally { server.shutdown() }
    }
}
