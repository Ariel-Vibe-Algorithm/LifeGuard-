package org.lifeguard.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Test
import org.lifeguard.app.api.ApiClient
import org.lifeguard.app.api.GeoPoint
import org.lifeguard.app.api.RideRequest
import org.lifeguard.app.api.Vehicle
import org.lifeguard.app.core.MotionSignal
import org.lifeguard.app.core.RiskScorer
import java.util.UUID

/**
 * שלבים 1-3 מול השרת האמיתי (לא MockWebServer): /me/setup, risk עם ביטחון מהטלפון,
 * רשומת ראיות חתומה, נסיעה בטוחה (start/location/checkin/sos), מצב לאומי.
 * רץ רק עם LIFEGUARD_IT_URL (כמו RealServerIT).
 */
class RealServerRoadmapIT {
    private val url = System.getProperty("lifeguard.it.url").orEmpty()

    private fun api(prefix: String): ApiClient {
        val name = prefix + UUID.randomUUID().toString().take(8)
        val anon = ApiClient(url, { null })
        anon.register(name, "password123", "שם $prefix")
        val t = anon.login(name, "password123", "jvm-it").token
        return ApiClient(url, { t })
    }

    @Test
    fun roadmapEndpointsAgainstRealServer() {
        assumeTrue("LIFEGUARD_IT_URL not set", url.isNotEmpty())
        val me = api("rm")
        val loc = GeoPoint(32.0700, 34.7800, 8.0, true)

        // 1.1 מוכנות מצד השרת
        val setup = me.meSetup()
        assertEquals(0, setup.contactsAccepted)

        // 1.3 + 1.4: SOS עם הערכה מהחיישנים -> השרת שומר ביטחון; רשומת ראיות חתומה
        val now = System.currentTimeMillis()
        val risk = RiskScorer.assess(listOf(
            MotionSignal("fall", 0.9, now - 5000, "נפילה"), MotionSignal("struggle", 0.7, now, "תנועה חריגה"))).toJson()
        val clientId = "it-" + UUID.randomUUID().toString().take(8)
        val inc = me.createIncident(clientId, "loud", location = loc, risk = risk)
        val ev = me.evidenceByClient(clientId)
        assertEquals("Ed25519", ev.getString("algorithm"))
        val rec = ev.getJSONObject("record")
        assertEquals(inc.id, rec.getJSONObject("incident").getString("id"))
        assertTrue(rec.getJSONObject("risk").optDouble("confidence", -1.0) in 0.0..1.0)
        assertTrue(rec.getJSONObject("risk").getJSONArray("signals").length() >= 1)
        assertEquals(64 * 2, ev.getString("signature").length)
        assertEquals(inc.id, me.incidentEvidence(inc.id).getJSONObject("record").getJSONObject("incident").getString("id"))
        me.setIncidentStatus(inc.id, "cancelled")

        // 2: נסיעה בטוחה
        val ride = me.startEscort(25, "עבודה", "silent", loc,
            RideRequest("taxi", GeoPoint(32.1100, 34.7800), Vehicle("12-345-67", "טויוטה לבנה", null)))
        assertTrue(ride.isRide && ride.isSilent)
        assertEquals("taxi", ride.rideType)
        assertEquals("12-345-67", ride.vehicle?.plate)
        assertNotNull(ride.routeSource)
        me.escortLocation(ride.id, GeoPoint(32.0720, 34.7800, 10.0, true, speedMps = 9.0))
        val v = me.getEscort(ride.id)
        assertTrue((v.progress?.remainingM ?: 0.0) > 3000)
        assertEquals(null, me.escortCheckin(ride.id, "ok"))          // ok -> view, לא אירוע
        val sos = me.escortSos(ride.id, "silent")
        assertNotNull(sos)
        assertEquals("silent", sos!!.mode)

        // 3.4 מצב לאומי: בלי מקור מורשה -> not_configured, לא "אין התרעות"
        val nat = me.nationalStatus(32.0853, 34.7818)
        assertEquals("not_configured", nat.alertsStatus)
    }
}
