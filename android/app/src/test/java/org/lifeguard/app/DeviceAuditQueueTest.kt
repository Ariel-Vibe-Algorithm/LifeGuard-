package org.lifeguard.app

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import org.lifeguard.app.api.ApiException
import org.lifeguard.app.core.AesGcmSealer
import org.lifeguard.app.core.DeviceAuditQueue
import org.lifeguard.app.core.MemoryTextStore
import org.lifeguard.app.core.SealedFileTextStore
import java.io.File
import java.io.IOException
import javax.crypto.KeyGenerator

/** משימה 7: תור ביקורת לא-מקוון - מוצפן, event_id קבוע (אידמפוטנטי), יוצא רק אחרי אישור השרת. */
class DeviceAuditQueueTest {
    @get:Rule val tmp = TemporaryFolder()
    private val key = KeyGenerator.getInstance("AES").apply { init(256) }.generateKey()
    private var now = 1_700_000_000.0
    private var seq = 0
    private fun queue(store: org.lifeguard.app.core.TextStore = MemoryTextStore()) =
        DeviceAuditQueue(store, clock = { now }, idGen = { "evt_%08d".format(++seq) })

    @Test fun events_have_contract_shape_and_validation() {
        val q = queue()
        val e = q.add("recording_started", details = JSONObject().put("discreet", true), incidentId = "inc_1")
        val j = e.toJson()
        assertEquals(setOf("event_id", "device_ts", "action", "result", "details", "incident_id"), j.keys().asSequence().toSet())
        assertTrue(DeviceAuditQueue.validId(j.getString("event_id")))
        assertEquals(now, j.getDouble("device_ts"), 0.0)
        assertEquals("completed", j.getString("result"))
        assertEquals("true", j.getJSONObject("details").getString("discreet"))   // ערכי details כמחרוזות קצרות
        try { q.add("listen_live"); fail() } catch (_: IllegalArgumentException) { }       // פעולה שלא בחוזה
        try { q.add("recording_started", result = "ok"); fail() } catch (_: IllegalArgumentException) { }
        try { q.add("recording_started", eventId = "short"); fail() } catch (_: IllegalArgumentException) { }
        try { q.add("recording_started", eventId = "bad id with spaces"); fail() } catch (_: IllegalArgumentException) { }
        // אותו event_id פעמיים -> אירוע אחד
        q.add("evidence_exported", eventId = "fixed_id_1")
        q.add("evidence_exported", eventId = "fixed_id_1")
        assertEquals(2, q.pending().size)
        assertTrue(DeviceAuditQueue.ACTIONS.containsAll(listOf("recording_started", "recording_stopped", "recording_stored_offline",
            "recording_upload_failed", "sos_activated_offline", "false_alarm_cancelled", "live_location_paused_offline", "evidence_exported")))
        // device_ts מפורש (פעולה שקרתה קודם בלי קליטה)
        assertEquals(123.0, q.add("live_location_paused_offline", deviceTs = 123.0).deviceTs, 0.0)
    }

    @Test fun flush_removes_only_acknowledged_batches_and_resends_same_ids() {
        val q = queue()
        repeat(120) { q.add("recording_stored_offline", "pending") }
        val sent = ArrayList<JSONArray>()
        var failAt = 2
        val r1 = q.flush(batchSize = 50) { a -> if (sent.size == failAt) throw IOException("offline"); sent += a; a.length() }
        assertEquals(100, r1.sent); assertEquals(20, r1.remaining); assertTrue(r1.error is IOException)
        val firstIdOfRemaining = q.pending().first().eventId
        // קליטה חזרה: אותם מזהים נשלחים (השרת מזהה כפילות לפי event_id)
        failAt = -1
        val again = ArrayList<String>()
        val r2 = q.flush { a -> again += (0 until a.length()).map { a.getJSONObject(it).getString("event_id") }; a.length() }
        assertEquals(20, r2.sent); assertEquals(0, r2.remaining); assertNull(r2.error)
        assertEquals(firstIdOfRemaining, again.first())
        assertTrue(q.pending().isEmpty())
        assertEquals(100, sent.flatMap { a -> (0 until a.length()).map { a.getJSONObject(it).getString("event_id") } }.toSet().size)
    }

    @Test fun permanent_error_drops_batch_auth_error_keeps_it() {
        val q = queue()
        q.add("evidence_exported")
        val r = q.flush(isPermanent = { it is ApiException && it.status == 400 }) { throw ApiException(401, "auth", "x") }
        assertEquals(1, r.remaining)
        val r2 = q.flush(isPermanent = { it is ApiException && it.status == 400 }) { throw ApiException(400, "invalid", "x") }
        assertEquals(0, r2.remaining)
    }

    @Test fun cap_keeps_newest() {
        val q = DeviceAuditQueue(MemoryTextStore(), clock = { now }, idGen = { "evt_%08d".format(++seq) }, maxEvents = 5)
        repeat(8) { q.add("evidence_exported") }
        assertEquals(listOf(4, 5, 6, 7, 8).map { "evt_%08d".format(it) }, q.pending().map { it.eventId })
    }

    @Test fun sealed_file_store_is_encrypted_and_survives_restart() {
        val f = File(tmp.root, "q/device_audit.bin")
        val store = SealedFileTextStore(f, AesGcmSealer { key })
        val q = queue(store)
        q.add("sos_activated_offline", details = JSONObject().put("channel", "sms"), incidentId = "inc_secret")
        assertTrue(f.exists())
        val raw = String(f.readBytes(), Charsets.ISO_8859_1)
        assertFalse(raw.contains("sos_activated_offline")); assertFalse(raw.contains("inc_secret"))
        // "אתחול": תור חדש על אותו קובץ
        val q2 = DeviceAuditQueue(SealedFileTextStore(f, AesGcmSealer { key }))
        assertEquals("inc_secret", q2.pending().single().incidentId)
        // מפתח אחר (למשל שחזור למכשיר אחר) -> ריק, לא קריסה
        val other = KeyGenerator.getInstance("AES").apply { init(256) }.generateKey()
        assertTrue(DeviceAuditQueue(SealedFileTextStore(f, AesGcmSealer { other })).pending().isEmpty())
        // ריקון התור מוחק את הקובץ
        q2.flush { it.length() }
        assertFalse(f.exists())
        assertNotNull(store)
    }
}
