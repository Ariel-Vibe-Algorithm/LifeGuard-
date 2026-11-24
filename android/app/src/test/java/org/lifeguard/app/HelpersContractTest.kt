package org.lifeguard.app

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.lifeguard.app.api.AppNotification
import org.lifeguard.app.api.HelperInfo
import org.lifeguard.app.api.IncidentView
import org.lifeguard.app.api.VolunteerRoles
import org.lifeguard.app.core.Effect
import org.lifeguard.app.core.Helpers
import org.lifeguard.app.core.RingStateMachine
import org.lifeguard.app.core.Tone

/**
 * חוזה מול השרת האמיתי ומול אפליקציית Windows: helpers_contract.json נוצר ע"י tools/build_helpers_contract.py
 * (LifeGuardService אמיתי על מסד זמני + main.SosWindow.helpers_summary + support_ui.support_status).
 */
class HelpersContractTest {
    private val fx = JSONObject(SafePlacesParityTest.resource("helpers_contract.json"))
    private val views = fx.getJSONObject("views")
    private val c = fx.getJSONObject("constants")

    private fun view(key: String) = IncidentView.parse(views.getJSONObject(key))

    // ---------------- קבועים זהים לשרת / Windows ----------------
    @Test
    fun hotlinesAndConstantsMatchServer() {
        val hot = c.getJSONArray("hotlines")
        assertEquals(hot.length(), Helpers.HOTLINES.size)
        for (i in 0 until hot.length()) {
            val o = hot.getJSONObject(i); val h = Helpers.HOTLINES[i]
            assertEquals(o.getString("name"), h.name)
            assertEquals(o.opt("number").takeIf { it != JSONObject.NULL }, h.number)
            assertEquals(o.optString("url").ifEmpty { null }, h.url)
            assertEquals(o.opt("note").takeIf { it != JSONObject.NULL }, h.note)
        }
        assertEquals(c.getString("hotlines_note"), Helpers.HOTLINES_NOTE)
        assertTrue(Helpers.HOTLINES_NOTE.contains("לאמת לפני הפצה"))
        val acts = c.getJSONArray("liaison_actions")
        assertEquals((0 until acts.length()).map { acts.getJSONArray(it).let { a -> a.getString(0) to a.getString(1) } },
            Helpers.LIAISON_ACTIONS.toList())
        val roleHe = c.getJSONObject("role_he")
        for (k in roleHe.keys()) assertEquals(roleHe.getString(k), Helpers.ROLE_HE[k])
        assertEquals(strings(c.getJSONArray("vol_roles")), Helpers.VOL_ROLES)
        val rows = c.getJSONArray("role_rows")
        assertEquals((0 until rows.length()).map { rows.getJSONArray(it).let { r -> listOf(r.getString(0), r.getString(1), r.getString(2)) } },
            Helpers.ROLE_ROWS.map { listOf(it.role, it.label, it.sub) })
        val langs = c.getJSONArray("lang_rows")
        assertEquals((0 until langs.length()).map { langs.getJSONArray(it).let { r -> r.getString(0) to r.getString(1) } }, Helpers.LANG_ROWS)
        assertTrue(Helpers.LANG_ROWS.all { it.first in strings(c.getJSONArray("langs")) })
        assertEquals(listOf("1202", "1203"), Helpers.HOTLINES.first { it.number == "1202 / 1203" }.dialNumbers)
    }

    // ---------------- המתנדב/ת: פרטיות, קבלה, הזעקה ----------------
    @Test
    fun remoteSupportOfferHidesNameAndLocationAndDoesNotRing() {
        val v = view("support_offer_notification")
        assertTrue(v.isHelper); assertTrue(v.isSupport)
        assertEquals(Helpers.REMOTE_SUPPORT, v.helperKind)
        assertEquals(false, v.ring)
        assertNull(v.owner); assertNull(v.location)
        assertEquals("מישהו צריך לדבר", v.ownerDisplay)
        assertTrue(v.guidance.isNotEmpty()); assertTrue(v.hotlines.isNotEmpty())
        assertEquals("🔒 לשמירה על הפרטיות לא מוצגים שם ומיקום של הפונה", Helpers.locationLine(v).first)
        assertFalse(Helpers.chatAllowed(v)); assertTrue(Helpers.actionButtons(v).isEmpty())
        assertTrue(Helpers.profileLine(v).startsWith("קווי סיוע שאפשר להפנות אליהם: ער\"ן - עזרה ראשונה נפשית 1201"))
        assertEquals("💬 מישהו צריך לדבר", Helpers.title(v))
        assertTrue(Helpers.subtitle(v, "21:05").contains("לא חירום - שיחה בלבד"))
        // הגנה כפולה: גם אם שרת עתידי ישלח מיקום למתנדב/ת תמיכה - לא מוצג ולא ניתן לניווט/העתקה
        val leaked = IncidentView.parse(JSONObject(views.getJSONObject("support_helper_chat").toString())
            .put("location", JSONObject().put("lat", 32.1).put("lon", 34.8)))
        assertNull(Helpers.shareableLocation(leaked))
        assertFalse(Helpers.locationLine(leaked).first.contains("32.1"))
    }

    @Test
    fun remoteSupportChatReplacesOwnerName() {
        val v = view("support_helper_chat")
        assertTrue(Helpers.chatAllowed(v))
        val owner = v.messages.first { it.text == "תודה" }
        assertEquals("הפונה", owner.senderDisplay); assertEquals("", owner.sender)
        assertFalse(views.getJSONObject("support_helper_chat").toString().contains("דנה"))
        assertEquals(listOf("done" to "סיימתי"), Helpers.actionButtons(v))      // אין כפתורי הזעקה בשיחה
    }

    @Test
    fun liaisonSeesNameAndLocationOnlyAfterAccept() {
        val before = view("liaison_before_accept")
        assertNull(before.location); assertNull(before.owner)
        assertEquals("📍 השם והמיקום יוצגו רק אחרי שתקבל/י את הבקשה", Helpers.locationLine(before).first)
        assertEquals("", Helpers.profileLine(before))
        assertNull(Helpers.shareableLocation(before))

        val acc = view("liaison_accepted")
        assertEquals("accepted", acc.myState)
        assertNotNull(acc.location); assertFalse(acc.locationStale)
        assertEquals(32.08, acc.location!!.lat, 1e-9)
        val (line, tone) = Helpers.locationLine(acc)
        assertEquals("📍 32.08000, 34.78000 - למסירה לרשויות", line); assertEquals(Tone.MUTED, tone)
        assertEquals("שם: דנה כהן", Helpers.profileLine(acc))
        assertEquals(Helpers.LIAISON_ACTIONS.keys.toList() + "done", Helpers.actionButtons(acc).map { it.first })
        assertEquals("הזעיק/ה משטרה (100)", Helpers.actionButtons(acc).first().second)
        assertTrue(Helpers.chatAllowed(acc))
        assertEquals("👮 בקשה להזעיק רשויות", Helpers.title(acc))
    }

    @Test
    fun staleLiaisonLocationIsNeverShownAsLive() {
        val v = view("liaison_stale_location")
        assertTrue(v.locationStale)
        val (line, tone) = Helpers.locationLine(v)
        assertTrue(line, line.contains("זה המיקום האחרון הידוע, לא מיקום חי"))
        assertEquals(Tone.ERROR, tone)
        assertEquals("called_police", v.lastAction); assertEquals("הזעיק/ה משטרה (100)", v.lastActionLabel)
    }

    @Test
    fun ringOnlyForAuthorityLiaison() {
        val m = RingStateMachine()
        val now = views.getJSONObject("liaison_offer_notification").getDouble("created_at")
        fun notif(id: Long, key: String) = views.getJSONObject(key).let {
            AppNotification(id, "incident_new", it.getString("id"), it, now)
        }
        val e = m.onNotifications(listOf(notif(1, "liaison_offer_notification"), notif(2, "support_offer_notification")),
            "liaison", emptySet(), now)
        val ring = e.filterIsInstance<Effect.StartRing>().single()
        assertEquals(Helpers.AUTHORITY_LIAISON, ring.incident.helperKind)
        val alert = e.filterIsInstance<Effect.IncidentAlert>().single()
        assertEquals(Helpers.REMOTE_SUPPORT, alert.incident.helperKind)
        assertTrue("שיחת תמיכה: התראה רגילה עם צליל (לא שקטה)", alert.loud)
        // שיחת תמיכה עם ring=true (לא צפוי מהשרת) - עדיין לא מצלצלת
        val bogus = JSONObject(views.getJSONObject("support_offer_notification").toString()).put("id", "inc_x").put("ring", true)
        val e2 = RingStateMachine().onNotifications(listOf(AppNotification(3, "incident_new", "inc_x", bogus, now)), "t", emptySet(), now)
        assertTrue(e2.none { it is Effect.StartRing })
        assertEquals("💬 מישהו צריך לדבר", Helpers.notificationTitle(alert.incident))
        assertEquals("👮 מבקשים ממך להזעיק רשויות", Helpers.notificationTitle(ring.incident))
    }

    @Test
    fun inboxIncludesHelperView() {
        val inbox = views.getJSONArray("inbox_liaison")
        assertTrue((0 until inbox.length()).map { IncidentView.parse(inbox.getJSONObject(it)) }.any { it.isHelper })
    }

    // ---------------- בעל/ת האירוע ----------------
    @Test
    fun ownerSummaryMatchesWindowsExactly() {
        val cases = fx.getJSONArray("helpers_summary")
        for (i in 0 until cases.length()) {
            val k = cases.getJSONObject(i)
            val hs = k.getJSONArray("helpers").let { a -> (0 until a.length()).map { HelperInfo.parse(a.getJSONObject(it)) } }
            val (text, tone) = Helpers.summary(hs, strings(k.getJSONArray("requested")).toSet())
            assertEquals(k.getString("id"), k.getString("text"), text)
            assertEquals(k.getString("id"), k.getString("tone"), tone.name)
        }
    }

    @Test
    fun ownerViewCarriesServerRequestedKinds() {
        val v = view("owner_request_liaison")
        assertEquals(listOf(Helpers.AUTHORITY_LIAISON), v.helperRequests)
        assertEquals("notified", v.helpers.single().state)
        // לעולם לא "איתך" לפני קבלה
        assertFalse(Helpers.summary(v.helpers, v.helperRequests!!.toSet()).first.contains("איתך"))
        val acc = view("owner_liaison_accepted").helpers.single()
        assertEquals("accepted", acc.state); assertEquals("called_police", acc.lastAction)
    }

    @Test
    fun supportStatusMatchesWindowsExactly() {
        val cases = fx.getJSONArray("support_status")
        for (i in 0 until cases.length()) {
            val k = cases.getJSONObject(i)
            val key = k.getString("view")
            val v = if (key.startsWith("inline:")) IncidentView.parse(k.getJSONObject("inline")) else view(key)
            val (text, tone) = Helpers.supportStatus(v)
            assertEquals(key, k.getString("text"), text)
            assertEquals(key, k.getString("tone"), tone.name)
        }
    }

    @Test
    fun supportRequestResumesExistingOpenRequest() {
        val created = view("support_created")
        val resumed = view("support_resumed")
        assertFalse(created.resumed); assertTrue(resumed.resumed)
        assertEquals(created.id, resumed.id)
        assertTrue(created.isSupport); assertEquals("support", created.activation)
        assertEquals(Helpers.HOTLINES.size, resumed.hotlines.size)
        assertEquals(Helpers.HOTLINES_NOTE, resumed.hotlinesNote)
    }

    // ---------------- הגדרות מתנדב/ת ----------------
    @Test
    fun volunteerRolesParseWithEffectiveStatus() {
        val un = VolunteerRoles.parse(views.getJSONObject("roles_liaison_unverified"))
        assertEquals(listOf("authority_liaison", "on_scene"), un.roles)
        assertEquals(listOf("he", "ar"), un.languages)
        assertEquals("✓ פעיל", Helpers.roleStatusText(un.effective["on_scene"]))
        assertTrue(Helpers.roleStatusText(un.effective["authority_liaison"]).startsWith("⏳ ממתין לאימות"))
        assertEquals("", Helpers.roleStatusText(un.effective["remote_support"]))
        assertEquals("active", VolunteerRoles.parse(views.getJSONObject("roles_liaison_verified")).effective["authority_liaison"])
        assertTrue(VolunteerRoles.parse(views.getJSONObject("roles_newbie")).effective["remote_support"]!!.contains("3 ימים"))
        val none = VolunteerRoles.parse(views.getJSONObject("roles_none"))
        assertTrue(none.roles.isEmpty()); assertEquals(listOf("he"), none.languages)
    }

    private fun strings(a: JSONArray) = (0 until a.length()).map { a.getString(it) }
}
