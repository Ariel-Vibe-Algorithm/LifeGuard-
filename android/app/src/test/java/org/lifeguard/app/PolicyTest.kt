package org.lifeguard.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.lifeguard.app.core.Geo
import org.lifeguard.app.core.PhoneNumbers
import org.lifeguard.app.core.QuickReplies
import org.lifeguard.app.core.UrlPolicy

class UrlPolicyTest {
    private fun ok(s: String) = (UrlPolicy.normalize(s) as UrlPolicy.Result.Ok).baseUrl

    @Test fun https_required_for_remote_hosts() {
        assertEquals("https://lg.example.org", ok("https://lg.example.org/"))
        assertEquals("https://lg.example.org", ok("lg.example.org"))                 // ברירת מחדל https
        assertEquals("https://lg.example.org:8443/api", ok("https://lg.example.org:8443/api/v1/"))
        assertTrue(UrlPolicy.normalize("http://lg.example.org") is UrlPolicy.Result.Error)
        assertTrue(UrlPolicy.normalize("http://192.168.1.10:8765") is UrlPolicy.Result.Error)
    }

    @Test fun cleartext_allowed_only_for_local_dev_hosts() {
        assertEquals("http://10.0.2.2:8765", ok("http://10.0.2.2:8765"))
        assertEquals("http://10.0.2.2:8765", ok("10.0.2.2:8765"))
        assertEquals("http://localhost:8765", ok("http://localhost:8765/v1"))
        assertEquals("http://127.0.0.1:8765", ok("http://127.0.0.1:8765"))
    }

    @Test fun rejects_garbage_and_credentials() {
        assertTrue(UrlPolicy.normalize("") is UrlPolicy.Result.Error)
        assertTrue(UrlPolicy.normalize("ftp://x.org") is UrlPolicy.Result.Error)
        assertTrue(UrlPolicy.normalize("https://user:pw@x.org") is UrlPolicy.Result.Error)
        assertTrue(UrlPolicy.normalize("https://x.org/?a=1") is UrlPolicy.Result.Error)
        assertFalse(UrlPolicy.isAllowed("javascript:alert(1)"))
    }
}

class QuickRepliesTest {
    @Test fun owner_and_responder_sets_match_server_contract() {
        assertEquals(listOf("ok", "cant_talk", "call_police", "hiding", "hurry", "injured"), QuickReplies.OWNER.keys.toList())
        assertEquals(listOf("on_my_way", "near_2min", "called_police", "stay_hidden", "cant_come"),
            QuickReplies.RESPONDER.keys.toList())
        assertEquals(QuickReplies.OWNER, QuickReplies.forRole("owner"))
        assertEquals(QuickReplies.RESPONDER, QuickReplies.forRole("trusted"))
        assertEquals(QuickReplies.RESPONDER, QuickReplies.forRole("volunteer"))
    }

    @Test fun server_map_wins_and_validation_by_role() {
        val server = linkedMapOf("ok" to "טקסט מהשרת")
        assertEquals(server, QuickReplies.resolve(server, "owner"))
        assertEquals(QuickReplies.OWNER, QuickReplies.resolve(emptyMap(), "owner"))
        assertTrue(QuickReplies.isValid("hiding", "owner")); assertFalse(QuickReplies.isValid("hiding", "trusted"))
        assertTrue(QuickReplies.isValid("near_2min", "volunteer")); assertFalse(QuickReplies.isValid("near_2min", "owner"))
        assertEquals("תתקשרו למשטרה (100)", QuickReplies.fullText("call_police"))
        assertEquals("on_my_way", QuickReplies.respondActionFor("near_2min"))
        assertNull(QuickReplies.respondActionFor("cant_come"))
        assertEquals("unknown_code", QuickReplies.chipLabel("unknown_code"))
    }
}

class GeoAndPhoneTest {
    @Test fun distance_and_format() {
        val d = Geo.distanceMeters(32.0853, 34.7818, 32.0853 + 1 / 111.32, 34.7818)  // ~1 ק"מ צפונה
        assertEquals(1000.0, d, 5.0)
        assertEquals("350 מ'", Geo.formatDistanceHe(0.348))
        assertEquals("2.5 ק\"מ", Geo.formatDistanceHe(2.46))
        assertEquals("שריפה", Geo.emergencyTypeHe("fire")); assertEquals("קריאת חירום", Geo.emergencyTypeHe(null))
    }

    @Test fun phone_normalization_matches_server() {
        assertEquals("+972521234567", PhoneNumbers.normalize("052-123-4567"))
        assertEquals("+972521234567", PhoneNumbers.normalize("+972 52 123 4567"))
        assertEquals("+972521234567", PhoneNumbers.normalize("00972521234567"))
        assertEquals("+14155550100", PhoneNumbers.normalize("+1 (415) 555-0100"))
        assertNull(PhoneNumbers.normalize("12ab")); assertNull(PhoneNumbers.normalize("+123"))
        val (ok, bad) = PhoneNumbers.parseList("052-1234567\n0521234567, junk\n+14155550100")
        assertEquals(listOf("+972521234567", "+14155550100"), ok); assertEquals(listOf("junk"), bad)
    }
}
