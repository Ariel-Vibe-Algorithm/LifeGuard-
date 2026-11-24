package org.lifeguard.app

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import org.lifeguard.app.api.ContactLocation
import org.lifeguard.app.api.GeoPoint
import org.lifeguard.app.api.PrivacyPermissions
import org.lifeguard.app.core.ContactLocationText
import org.lifeguard.app.core.LiveLocationPolicy
import org.lifeguard.app.core.LocationModes
import java.util.TimeZone

/** משימות 2-3: העלאת מיקום חי ("always") ותצוגת מיקום של איש קשר עם סימון מיקום ישן. */
class LocationSharingTest {
    private val now = 1_700_000_000.0
    private fun input(
        always: Int = 1, paused: Boolean = false, fixAge: Double? = 10.0, lastUpload: Double = now - 61,
        lastFixAt: Double? = null, saver: Boolean = false, pct: Int? = 80, charging: Boolean = false, refused: Double = 0.0,
    ) = LiveLocationPolicy.Input(now, always, paused, fixAge?.let { GeoPoint(32.0, 34.0, 20.0, true, capturedAt = now - it) },
        lastUpload, lastFixAt, saver, pct, charging, refused)

    @Test fun uploads_only_with_always_contacts_and_not_paused() {
        assertTrue(LiveLocationPolicy.decide(input()).upload)
        assertEquals("no_always_contacts", LiveLocationPolicy.decide(input(always = 0)).reason)
        assertEquals("paused", LiveLocationPolicy.decide(input(paused = true)).reason)
        assertEquals("refused_recently", LiveLocationPolicy.decide(input(refused = now + 10)).reason)
        assertEquals("no_fix", LiveLocationPolicy.decide(input(fixAge = null)).reason)
    }

    @Test fun never_marks_stale_data_as_live() {
        // fix ישן -> לא נשלח (השרת היה מסמן אותו "חי" לפי זמן הקבלה)
        assertEquals("fix_too_old", LiveLocationPolicy.decide(input(fixAge = 121.0)).reason)
        // אותו fix שכבר נשלח -> לא נשלח שוב (היה מרענן את received_at)
        assertEquals("same_fix", LiveLocationPolicy.decide(input(fixAge = 30.0, lastFixAt = now - 30)).reason)
        assertTrue(LiveLocationPolicy.decide(input(fixAge = 5.0, lastFixAt = now - 70)).upload)
    }

    @Test fun battery_aware_interval() {
        assertEquals("interval", LiveLocationPolicy.decide(input(lastUpload = now - 30)).reason)
        // חיסכון סוללה / סוללה חלשה בלי טעינה -> כל 5 דק'
        assertEquals("interval", LiveLocationPolicy.decide(input(lastUpload = now - 120, saver = true)).reason)
        assertEquals("interval", LiveLocationPolicy.decide(input(lastUpload = now - 120, pct = 15)).reason)
        assertTrue(LiveLocationPolicy.decide(input(lastUpload = now - 120, pct = 15, charging = true)).upload)
        assertTrue(LiveLocationPolicy.decide(input(lastUpload = now - 301, saver = true)).upload)
    }

    @Test fun contact_location_live_vs_stale_labels() {
        val tz = TimeZone.getTimeZone("UTC")
        val live = ContactLocation.parse(JSONObject("""{"available":true,"lat":32.1,"lon":34.8,"accuracy_m":12.4,
            "is_precise":true,"captured_at":$now,"received_at":$now,"age_seconds":40,"stale":false,"label":"חי"}"""))
        val v = ContactLocationText.describe(live, "דנה", now, now + 10, tz)
        assertTrue(v.live)
        assertTrue(v.headline.startsWith("מיקום חי"))
        assertTrue(v.lines.any { it.contains("12 מ'") })
        assertTrue(v.lines.any { it.startsWith("התקבל בשרת") } && v.lines.any { it.startsWith("נמדד במכשיר") })
        // אותה תשובה, אבל המסך פתוח עוד 5 דק' בלי רענון -> כבר לא חי
        assertFalse(ContactLocationText.describe(live, "דנה", now, now + 300, tz).live)
        // השרת אומר stale -> לא חי, עם אזהרה מפורשת
        val stale = ContactLocation.parse(JSONObject("""{"available":true,"lat":32.1,"lon":34.8,"accuracy_m":null,
            "is_precise":false,"captured_at":null,"received_at":${now - 900},"age_seconds":900,"stale":true}"""))
        val s = ContactLocationText.describe(stale, "דנה", now, now, tz)
        assertFalse(s.live)
        assertTrue(s.headline.contains("לא חי"))
        assertTrue(s.headline.contains("15 דק'"))
        assertTrue(s.lines.any { it.contains("עלול לא לשקף") })
        // שדה stale חסר -> לא מניחים שחי
        assertTrue(ContactLocation.parse(JSONObject("""{"available":true,"lat":1,"lon":2}""")).stale)
        val none = ContactLocation.parse(JSONObject("""{"available":false,"reason":"אין עדיין מיקום משותף מהמכשיר"}"""))
        assertFalse(ContactLocationText.describe(none, "דנה", now, now, tz).live)
    }

    @Test fun permissions_parse_contract() {
        val p = PrivacyPermissions.parse(JSONObject("""{"sharing_paused":true,"record_on_emergency":false,
            "contacts":[{"username":"avi","display_name":"אבי","location_mode":"always","recording_access":1,
              "effective_now":"paused","last_change":{"server_ts":5.5,"actor":"me"}},
             {"username":"bat","display_name":"בת","location_mode":"off","recording_access":false,"effective_now":"off","last_change":null}],
            "who_can_see_location_now":[],"volunteers_with_access":[{"display_name":"מתנדב","username":"v1","incident_id":"inc_1"}],
            "guardian_links":[],"recording_policy":{"record_on_emergency":false,"discreet":false,"upload":true,"recipients":["אבי"]},
            "my_live_location":{"captured_at":10,"received_at":11}}"""))
        assertTrue(p.sharingPaused)
        assertEquals(1, p.alwaysContacts)
        assertEquals("paused", p.contacts[0].effectiveNow)
        assertTrue(p.contacts[0].recordingAccess)
        assertEquals(5.5, p.contacts[0].lastChange!!.serverTs!!, 0.0)
        assertEquals(null, p.contacts[1].lastChange)
        assertEquals("inc_1", p.volunteersWithAccess.single().incidentId)
        assertTrue(p.recordingPolicy!!.upload)
        assertTrue(p.hasMyLiveLocation); assertEquals(11.0, p.myLiveReceivedAt!!, 0.0)
        assertFalse(PrivacyPermissions.parse(JSONObject("""{"my_live_location":null}""")).hasMyLiveLocation)
        assertEquals(listOf("always", "emergency", "off"), LocationModes.ALL)
        assertTrue(LocationModes.ALL.all { LocationModes.LABEL_HE.containsKey(it) && LocationModes.EXPLAIN_HE.containsKey(it) })
    }
}
