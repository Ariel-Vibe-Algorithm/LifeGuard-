package org.lifeguard.app

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.lifeguard.app.api.AppNotification
import org.lifeguard.app.api.GuardianLink
import org.lifeguard.app.api.PrivacyEvent
import org.lifeguard.app.api.PrivacyHistoryQuery
import org.lifeguard.app.core.Effect
import org.lifeguard.app.core.GuardianText
import org.lifeguard.app.core.PrivacyHistoryText
import org.lifeguard.app.core.PrivacyHistoryText.Result
import org.lifeguard.app.core.PrivacyHistoryText.RevokeKind
import org.lifeguard.app.core.RingStateMachine
import java.util.TimeZone

/** משימות 5-6: הורה-ילד ויומן הפרטיות. */
class GuardianAndHistoryTest {
    private fun link(json: String) = GuardianLink.parse(JSONObject(json))

    private val pendingForChild = """{"id":"gl_1","guardian":"mom","guardian_display":"אמא","child":"kid","child_display":"נועה",
        "my_role":"child","status":"pending","child_age":9,"guardian_approved":true,"child_approved":false,
        "guardian_verified":false,"recording_consent":{"guardian":false,"child":false},"discreet_recording_active":false}"""

    @Test fun guardian_status_lines_and_actions() {
        val l = link(pendingForChild)
        assertFalse(l.iAmGuardian); assertEquals("אמא", l.otherDisplay)
        assertTrue(GuardianText.canApprove(l)); assertFalse(GuardianText.canConsent(l))
        val lines = GuardianText.statusLines(l)
        assertEquals("ממתין לאישור שלך", lines[0])
        assertTrue(lines.any { it.startsWith("ההורה עוד לא אומת") })
        assertTrue(lines.last().startsWith("הקלטה דיסקרטית לא פעילה"))
        assertEquals(listOf("אישור של שני הצדדים", "הסכמת ההורה להקלטה", "הסכמת הילד/ה להקלטה", "אימות ההורה"), GuardianText.missing(l))

        val active = link("""{"id":"gl_1","guardian":"mom","guardian_display":"אמא","child":"kid","child_display":"נועה",
            "my_role":"guardian","status":"active","child_age":15,"guardian_approved":true,"child_approved":true,
            "guardian_verified":true,"recording_consent":{"guardian":true,"child":true},"discreet_recording_active":true}""")
        assertTrue(active.iAmGuardian && active.myConsent)
        assertFalse(GuardianText.canApprove(active)); assertTrue(GuardianText.canConsent(active))
        val al = GuardianText.statusLines(active)
        assertEquals("הקישור פעיל (שני הצדדים אישרו)", al[0])
        assertFalse(al.any { it.startsWith("ההורה עוד לא אומת") })
        assertTrue(al.last().contains("סמן המיקרופון"))
        assertTrue(GuardianText.missing(active).isEmpty())
        // ההורה אישר, הילד עוד לא -> מנקודת המבט של ההורה: ממתין לילד/ה
        val waiting = link(pendingForChild.replace("\"my_role\":\"child\"", "\"my_role\":\"guardian\""))
        assertEquals("ממתין לאישור של נועה", GuardianText.statusLines(waiting)[0])
        assertFalse(GuardianText.canApprove(waiting))
    }

    @Test fun consent_text_is_age_appropriate_and_never_promises_live_listening() {
        val young = link(pendingForChild)
        val teen = link(pendingForChild.replace("\"child_age\":9", "\"child_age\":16"))
        val mid = link(pendingForChild.replace("\"child_age\":9", "\"child_age\":12"))
        val guardian = link(pendingForChild.replace("\"my_role\":\"child\"", "\"my_role\":\"guardian\""))
        val texts = listOf(GuardianText.consentText(young), GuardianText.consentText(mid), GuardianText.consentText(teen))
        assertEquals(3, texts.toSet().size)                              // שלושה נוסחים לפי גיל
        assertTrue(texts[0].length < texts[2].length)                     // לצעירים - קצר ופשוט
        for (t in texts) assertTrue(t.contains("לבטל"))                  // תמיד אפשר לבטל
        for (t in texts + GuardianText.consentText(guardian)) assertTrue(t.contains("מרחוק"))   // אין הפעלה מרחוק
        assertTrue(GuardianText.consentText(guardian).contains("נועה"))
        assertTrue(GuardianText.consentText(teen, retentionDays = 30).contains("30 יום"))
    }

    @Test fun invite_validation() {
        assertEquals(GuardianText.Invite("dana", "guardian", 12), GuardianText.validateInvite(" Dana ", "guardian", "12", "me").first)
        assertNull(GuardianText.validateInvite("ab", "guardian", "", "me").first)
        assertNull(GuardianText.validateInvite("me", "child", "", "me").first)
        assertNull(GuardianText.validateInvite("dana", "", "", "me").first)
        assertEquals("פרופיל ילד/ה מיועד לגילאי 0-17", GuardianText.validateInvite("dana", "child", "18", "me").second)
        assertEquals(null, GuardianText.validateInvite("dana", "child", "", "me").first!!.childAge)
        assertNotEquals(null, GuardianText.validateInvite("דנה.כהן", "child", "7", "me").first)
    }

    @Test fun guardian_update_notification_is_handled() {
        val rsm = RingStateMachine()
        val n = AppNotification.parse(JSONObject("""{"id":5,"type":"guardian_update","incident_id":null,
            "payload":{"event":"invite","link_id":"gl_1","from":"אמא"},"created_at":1}"""))
        val effects = rsm.onNotifications(listOf(n), "kid", emptySet(), 2.0)
        assertTrue(effects.any { it is Effect.Ack && it.notificationId == 5L })
        val info = effects.filterIsInstance<Effect.Info>().single()
        assertEquals("guardian_update", info.type); assertFalse(info.quiet); assertTrue(info.text.contains("אמא"))
        val consent = AppNotification.parse(JSONObject("""{"id":6,"type":"guardian_update","payload":{"event":"recording_consent"},"created_at":1}"""))
        assertTrue(rsm.onNotifications(listOf(consent), "kid", emptySet(), 2.0).filterIsInstance<Effect.Info>().single().quiet)
    }

    private fun ev(json: String) = PrivacyEvent.parse(JSONObject(json))

    @Test fun history_location_access_says_delivered_never_viewed() {
        val e = ev("""{"event_id":"ev_1","server_ts":1700000000,"device_ts":null,"actor":"avi","actor_display":"אבי",
            "subject":"me","recipient":"avi","recipient_display":"אבי","action":"location_access","result":"completed",
            "via":"always","permission":"always","incident_id":null,"source":"server","details":{},
            "access_period":{"from":1700000000,"to":1700000600,"requests":11,"seconds":600}}""")
        val d = PrivacyHistoryText.describe(e, "me")
        assertTrue(d.contains("נמסר לאפליקציה של אבי"))
        val all = PrivacyHistoryText.detailLines(e, "me", TimeZone.getTimeZone("UTC")).joinToString("\n")
        assertFalse(all.contains("נצפה")); assertFalse(all.contains("צפה ב")); assertFalse(all.contains("ראה את"))
        assertTrue(all.contains("11 בקשות"))
        val rv = PrivacyHistoryText.revokeFor(e, "me")!!
        assertEquals(RevokeKind.LOCATION, rv.kind); assertEquals("avi", rv.contact)
        val blocked = ev("""{"event_id":"ev_2","server_ts":1,"actor":"avi","actor_display":"אבי","subject":"me",
            "action":"location_access","result":"failed","source":"server","details":{"reason":"not_permitted"}}""")
        assertEquals(Result.FAILED, PrivacyHistoryText.result(blocked))
        assertTrue(PrivacyHistoryText.describe(blocked, "me").contains("נחסמה"))
    }

    @Test fun history_results_times_and_revoke_links() {
        // result_effective גובר על result (מסירה שעוד לא אושרה)
        val n = ev("""{"event_id":"ev_3","server_ts":100,"action":"recording_recipient_notified","result":"pending",
            "result_effective":"completed","recipient":"avi","recipient_display":"אבי","delivery":"נמסר לאפליקציה","details":{}}""")
        assertEquals(Result.COMPLETED, PrivacyHistoryText.result(n))
        assertEquals(RevokeKind.RECORDING_ACCESS, PrivacyHistoryText.revokeFor(n, "me")!!.kind)
        val p = ev("""{"event_id":"ev_4","server_ts":100,"action":"recording_recipient_notified","result":"pending","details":{}}""")
        assertEquals(Result.PENDING, PrivacyHistoryText.result(p))
        // אירוע מהמכשיר: זמן מכשיר וזמן שרת בנפרד
        val dev = ev("""{"event_id":"dev_me_a1","server_ts":1700000100,"device_ts":1700000000,"actor":"me","subject":"me",
            "action":"sos_activated_offline","result":"completed","source":"device","details":{"channel":"sms"}}""")
        val t = PrivacyHistoryText.timeLines(dev, TimeZone.getTimeZone("UTC"))
        assertEquals(2, t.size)
        assertTrue(t[0].startsWith("זמן שרת: 14/11/2023 22:15:00"))
        assertTrue(t[1].startsWith("זמן מכשיר") && t[1].contains("לא מאומת") && t[1].contains("22:13:20"))
        assertEquals(1, PrivacyHistoryText.timeLines(n).size)
        assertEquals(RevokeKind.GUARDIAN, PrivacyHistoryText.revokeFor(ev("""{"event_id":"e","action":"guardian_link_approved","result":"completed","details":{}}"""), "me")!!.kind)
        assertNull(PrivacyHistoryText.revokeFor(dev, "me"))
        // low confidence (נרשם מהמכשיר כ-false_alarm_cancelled עם reason)
        assertTrue(PrivacyHistoryText.describe(ev("""{"event_id":"e2","action":"false_alarm_cancelled","result":"completed",
            "details":{"reason":"low_confidence"}}"""), "me").contains("ביטחון נמוך"))
        // מיון: החדש למעלה לפי זמן שרת
        assertEquals(listOf("dev_me_a1", "ev_3", "ev_4"), PrivacyHistoryText.sorted(listOf(n, dev, p)).map { it.eventId })
        // כל הפעולות המוכרות מתורגמות (לא מוצג השם הגולמי)
        for (a in listOf("emergency_activated", "emergency_confirmed", "emergency_cancelled", "emergency_resolved", "permission_location_mode",
            "permission_recording_access", "permission_sharing_paused", "permission_sharing_resumed", "permission_recording",
            "permission_discreet_recording", "recording_uploaded", "recording_access", "recording_deleted", "privacy_report_exported",
            "guardian_link_requested", "guardian_link_revoked", "guardian_verified", "recording_started", "recording_stopped",
            "recording_stored_offline", "recording_upload_failed", "live_location_paused_offline", "evidence_exported")) {
            assertNotEquals(a, PrivacyHistoryText.describe(ev("""{"event_id":"x","action":"$a","result":"completed","details":{}}"""), "me"))
        }
    }

    @Test fun history_query_and_day_range() {
        val tz = TimeZone.getTimeZone("Asia/Jerusalem")
        val (f, t) = PrivacyHistoryText.dayRange(Triple(2026, 3, 1), Triple(2026, 3, 1), tz)
        assertEquals(86400.0 - 0.001, t!! - f!!, 1e-6)
        val params = PrivacyHistoryQuery(f, t, contact = " Avi ", type = "location_access", q = "  ", limit = 5000).toParams()
        assertEquals("avi", params["contact"]); assertEquals("location_access", params["type"])
        assertFalse(params.containsKey("q")); assertEquals("2000", params["limit"])
        assertEquals(PrivacyHistoryText.TYPE_FILTERS[0].first, "")
        assertTrue(PrivacyQueryHasNoNulls(PrivacyHistoryQuery().toParams()))
    }

    @Suppress("FunctionName") private fun PrivacyQueryHasNoNulls(m: Map<String, String>) = m.keys == setOf("limit")
}
