package org.lifeguard.app

import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Test
import org.lifeguard.app.api.ApiClient
import org.lifeguard.app.api.AppNotification
import org.lifeguard.app.api.GeoPoint
import org.lifeguard.app.core.Effect
import org.lifeguard.app.core.RingStateMachine
import java.util.UUID

/**
 * אינטגרציה מול השרת האמיתי (server/lifeguard_server.py). רץ רק כש-LIFEGUARD_IT_URL מוגדר
 * (למשל http://127.0.0.1:8799 על מסד נתונים זמני) - אחרת מדולג.
 */
class RealServerIT {
    private val url = System.getProperty("lifeguard.it.url").orEmpty()
    private val http = OkHttpClient()

    private class User(val name: String, val api: ApiClient, val token: String)

    private fun user(prefix: String): User {
        val name = prefix + UUID.randomUUID().toString().take(8)
        val anon = ApiClient(url, { null })
        anon.register(name, "password123", "שם $prefix")
        val t = anon.login(name, "password123", "jvm-it").token
        return User(name, ApiClient(url, { t }), t)
    }

    private fun raw(u: User, method: String, path: String, body: JSONObject = JSONObject()): JSONObject {
        val req = Request.Builder().url("$url/v1/$path").header("Authorization", "Bearer ${u.token}")
            .method(method, body.toString().toRequestBody("application/json".toMediaType())).build()
        http.newCall(req).execute().use { r ->
            val s = r.body!!.string()
            assertTrue("$path -> ${r.code} $s", r.isSuccessful)
            return JSONObject(s)
        }
    }

    /** poll עד שמגיעה התראה שעונה לתנאי; מאשר קבלה לכל מה שהתקבל (כמו השירות). */
    private fun pollUntil(u: User, cursor: LongArray, ring: RingStateMachine?, pred: (AppNotification) -> Boolean):
        Pair<AppNotification, List<Effect>> {
        val effects = ArrayList<Effect>()
        repeat(10) {
            val list = u.api.poll(cursor[0], 3)
            list.forEach { u.api.ack(it.id) }
            if (list.isNotEmpty()) cursor[0] = list.maxOf { it.id }
            ring?.let { effects += it.onNotifications(list, u.name, emptySet(), u.api.serverNow()) }
            list.firstOrNull(pred)?.let { return it to effects }
        }
        throw AssertionError("notification not received")
    }

    @Test fun full_flow_against_real_server() {
        assumeTrue("LIFEGUARD_IT_URL not set", url.isNotEmpty())
        val owner = user("own"); val contact = user("con")

        // מעגל ביטחון: owner מזמין, contact מאשר
        raw(owner, "POST", "relationships", JSONObject().put("contact", contact.name))
        raw(contact, "POST", "relationships/${owner.name}/respond", JSONObject().put("accept", true))
        val circle = owner.api.circle()
        assertEquals(contact.name, circle.getJSONArray("my_contacts").getJSONObject(0).getString("username"))

        val cc = contact.api.cursor()
        // כמו השירות בהתקנה חדשה: מה שעוד לא התקבל (הזמנה למעגל) מעובד כ-backlog ומאושר
        assertTrue(cc.unreceived.any { it.type == "relationship_invite" })
        cc.unreceived.forEach { contact.api.ack(it.id) }
        val cursor = longArrayOf(cc.lastId)
        contact.api.putPresence(GeoPoint(32.0860, 34.7820, 1500.0, false, "it"))

        // SOS גלוי + אידמפוטנטיות לפי client_id
        val cid = UUID.randomUUID().toString()
        val loc = GeoPoint(32.0853, 34.7818, 8.0, true, "gps")
        val inc = owner.api.createIncident(cid, "loud", location = loc)
        assertEquals(inc.id, owner.api.createIncident(cid, "loud", location = loc).id)
        assertEquals("owner", inc.myRole)
        owner.api.sendIncidentLocation(inc.id, loc.copy(lat = 32.0854))

        // איש הקשר מקבל צלצול
        val ring = RingStateMachine()
        ring.resetCursor(cc.lastId)
        val (newN, eff) = pollUntil(contact, cursor, ring) { it.type == "incident_new" && it.incidentId == inc.id }
        assertTrue("ring flag: ${newN.payload}", newN.payload.optBoolean("ring"))
        assertTrue(eff.any { it is Effect.StartRing && it.incident.id == inc.id })
        assertTrue(eff.any { it is Effect.Ack && it.notificationId == newN.id })
        // ACK נרשם בשרת -> בעל האירוע רואה "delivered"
        val ownerCursor = longArrayOf(0)
        pollUntil(owner, ownerCursor, null) { it.type == "incident_update" && it.payload.optString("event") == "delivered" }

        // קבלה -> ring_stopped
        contact.api.respond(inc.id, "accept")
        val stopEff = pollUntil(contact, cursor, ring) {
            it.type == "incident_update" && it.payload.optString("event") == "ring_stopped"
        }.second
        assertTrue(stopEff.any { it is Effect.StopRing })
        assertFalse(ring.isRinging(inc.id))

        // צ'אט: הודעה מהירה של מגיב, קריאה ואישור קריאה
        val mid = contact.api.sendMessage(inc.id, quick = "on_my_way")
        val ov = owner.api.getIncident(inc.id)
        val m = ov.messages.single { it.id == mid }
        assertEquals("on_my_way", m.quick); assertEquals("trusted", m.role); assertEquals("אני בדרך אליך", m.text)
        assertEquals(setOf("ok", "cant_talk", "call_police", "hiding", "hurry", "injured"), ov.quickReplies.keys)
        owner.api.markRead(inc.id, mid)
        val cv = contact.api.getIncident(inc.id)
        assertTrue(cv.messages.single { it.id == mid }.readBy.contains("שם own"))
        assertEquals(setOf("on_my_way", "near_2min", "called_police", "stay_hidden", "cant_come"), cv.quickReplies.keys)
        owner.api.sendMessage(inc.id, quick = "hurry")
        val (msgN, msgEff) = pollUntil(contact, cursor, ring) { it.type == "incident_update" && it.payload.optString("event") == "message" }
        assertNotNull(msgN.payload.opt("message_id"))
        assertFalse((msgEff.last { it is Effect.ChatMessage } as Effect.ChatMessage).quiet)

        // סגירה -> incident_closed אצל איש הקשר
        owner.api.setIncidentStatus(inc.id, "resolved")
        pollUntil(contact, cursor, ring) { it.type == "incident_closed" && it.incidentId == inc.id }

        // מצוקה שקטה: הודעה מאיש הקשר מגיעה לבעלים עם quiet
        val sil = owner.api.createIncident(UUID.randomUUID().toString(), "silent", location = loc)
        pollUntil(contact, cursor, ring) { it.type == "incident_new" && it.incidentId == sil.id }
        contact.api.respond(sil.id, "accept")
        contact.api.sendMessage(sil.id, text = "אני כאן")
        val q = pollUntil(owner, ownerCursor, null) {
            it.type == "incident_update" && it.payload.optString("event") == "message" && it.incidentId == sil.id
        }.first
        assertTrue("silent owner message must be quiet: ${q.payload}", q.payload.optBoolean("quiet"))
        val ownerRing = RingStateMachine()
        val qe = ownerRing.onNotifications(listOf(q), owner.name, setOf(sil.id), owner.api.serverNow(), backlog = true)
        assertTrue((qe.single { it is Effect.ChatMessage } as Effect.ChatMessage).quiet)
        owner.api.setIncidentStatus(sil.id, "cancelled")

        // ליווי: התחלה, מיקום, הגעה
        val esc = owner.api.startEscort(5, "בית", "loud", loc)
        assertTrue(esc.isActive)
        owner.api.escortLocation(esc.id, loc)
        assertEquals(esc.id, owner.api.myEscorts().single().id)
        assertEquals("arrived", owner.api.escortAction(esc.id, "arrived").status)

        // אחרי שקראנו ואישרנו הכל: ה-cursor לא מחזיר התראות "שלא התקבלו" (ACK נרשם בשרת)
        contact.api.poll(cursor[0], 1).forEach { contact.api.ack(it.id) }
        val left = contact.api.cursor().unreceived
        assertTrue("unreceived: ${left.map { it.type + ":" + it.payload }}", left.isEmpty())
    }
}
