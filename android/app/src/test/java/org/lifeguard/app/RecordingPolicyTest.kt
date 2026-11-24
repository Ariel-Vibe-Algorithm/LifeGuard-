package org.lifeguard.app

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import org.lifeguard.app.api.RecordingPolicy
import org.lifeguard.app.core.AesGcmSealer
import org.lifeguard.app.core.RecordingGate
import org.lifeguard.app.core.RecordingGate.Decision
import org.lifeguard.app.core.RecordingUpload
import org.lifeguard.app.core.RecordingUploadPolicy
import org.lifeguard.app.core.RecordingUploadPolicy.Outcome
import org.lifeguard.app.core.RecordingUploadQueue
import org.lifeguard.app.core.SealedFileTextStore
import java.io.File
import javax.crypto.KeyGenerator

/** משימה 4: הקלטה רק בחירום מאושר לפי מדיניות השרת; תור העלאה מוצפן עם ניסיונות חוזרים. */
class RecordingPolicyTest {
    @get:Rule val tmp = TemporaryFolder()

    private fun policy(rec: Boolean = true, discreet: Boolean = false, upload: Boolean = true) =
        RecordingPolicy(rec, discreet, if (discreet) listOf("אמא") else emptyList(), upload, listOf("אבי"), 1_200_000, 30, emptyList())

    @Test fun gate_records_only_in_confirmed_emergency_with_policy_and_permission() {
        assertEquals(Decision.RECORD, RecordingGate.decide(true, true, policy(), true))
        assertEquals(Decision.NO_INCIDENT, RecordingGate.decide(false, true, policy(), true))          // אין הקלטה מחוץ לחירום
        assertEquals(Decision.NOT_CONFIRMED, RecordingGate.decide(true, false, policy(), true))       // unconfirmed
        assertEquals(Decision.POLICY_OFF, RecordingGate.decide(true, true, policy(rec = false), true))
        assertEquals(Decision.POLICY_UNKNOWN, RecordingGate.decide(true, true, null, true))
        assertEquals(Decision.NO_PERMISSION, RecordingGate.decide(true, true, policy(), false))     // לא עוקפים הרשאה
        // ילד/ה עם הקלטה דיסקרטית: מקליט גם בלי record_on_emergency, אבל עדיין דורש הרשאת מיקרופון
        assertEquals(Decision.RECORD, RecordingGate.decide(true, true, policy(rec = false, discreet = true), true))
        assertEquals(Decision.NO_PERMISSION, RecordingGate.decide(true, true, policy(rec = false, discreet = true), false))
        for (d in Decision.values()) if (d != Decision.RECORD && d != Decision.NO_INCIDENT) assertTrue(RecordingGate.reasonHe(d)!!.isNotBlank())
    }

    @Test fun policy_parses_contract_and_roundtrips_cache() {
        val o = JSONObject("""{"record_on_emergency":true,"discreet":true,"discreet_guardians":["אמא"],"upload":true,
            "recipients":["אמא","אבי"],"max_upload_bytes":1200000,"retention_days":30,"notes":["n1","n2"]}""")
        val p = RecordingPolicy.parse(o)
        assertTrue(p.recordOnEmergency && p.discreet && p.upload)
        assertEquals(listOf("אמא", "אבי"), p.recipients)
        assertEquals(1_200_000L, p.maxUploadBytes)
        assertEquals(p, RecordingPolicy.parse(JSONObject(p.toJson().toString())))
        // שדות חסרים -> שמרני (לא מקליטים, לא מעלים)
        val empty = RecordingPolicy.parse(JSONObject())
        assertFalse(empty.recordOnEmergency || empty.discreet || empty.upload)
    }

    @Test fun upload_errors_classified_per_contract() {
        assertEquals(Outcome.Uploaded, RecordingUploadPolicy.classify(200, null))
        assertEquals(Outcome.Retry("network"), RecordingUploadPolicy.classify(null, null))
        assertEquals(Outcome.Retry("not_confirmed"), RecordingUploadPolicy.classify(409, "not_confirmed"))
        assertEquals(Outcome.Drop("too_late"), RecordingUploadPolicy.classify(409, "too_late"))
        assertEquals(Outcome.Drop("recording_disabled"), RecordingUploadPolicy.classify(403, "recording_disabled"))
        assertEquals(Outcome.Drop("too_large"), RecordingUploadPolicy.classify(413, "too_large"))
        assertEquals(Outcome.Drop("hash_mismatch"), RecordingUploadPolicy.classify(400, "hash_mismatch"))
        assertTrue(RecordingUploadPolicy.classify(503, "x") is Outcome.Retry)
        assertTrue(RecordingUploadPolicy.classify(401, "auth") is Outcome.Retry)
        assertEquals(30.0, RecordingUploadPolicy.backoffSeconds(1, "network"), 0.0)
        assertEquals(60.0, RecordingUploadPolicy.backoffSeconds(2, "network"), 0.0)
        assertEquals(900.0, RecordingUploadPolicy.backoffSeconds(20, "network"), 0.0)
        assertEquals(120.0, RecordingUploadPolicy.backoffSeconds(7, "not_confirmed"), 0.0)
    }

    @Test fun encrypted_queue_retry_drop_and_idempotent_ids() {
        val key = KeyGenerator.getInstance("AES").apply { init(256) }.generateKey()
        val f = File(tmp.root, "uploads.bin")
        val q = RecordingUploadQueue(SealedFileTextStore(f, AesGcmSealer { key }))
        val cid = RecordingUpload.clientIdFor("cid-1", "audio_001.bin")
        assertEquals(cid, RecordingUpload.clientIdFor("cid-1", "audio_001.bin"))      // יציב -> אידמפוטנטי בשרת
        assertTrue(cid.length in 4..80)
        val item = RecordingUpload(cid, "cid-1", "audio_001.bin", "ab".repeat(32), 1000, startedAt = 100.0, endedAt = 160.0)
        assertTrue(q.add(item)); assertFalse(q.add(item))
        assertFalse(String(f.readBytes(), Charsets.ISO_8859_1).contains("audio_001"))  // מוצפן
        q.record(cid, Outcome.Retry("network"), 1000.0)
        assertTrue(q.due(1000.0).isEmpty())
        assertEquals(1, q.due(1031.0).size)
        assertEquals(1, q.items().single().attempts)
        q.record(cid, Outcome.Drop("too_late"), 1100.0)
        assertTrue(q.items().isEmpty())
        // 48 שעות אחרי סוף הקטע - פג
        assertFalse(RecordingUploadPolicy.expired(item, 160.0 + 47 * 3600))
        assertTrue(RecordingUploadPolicy.expired(item, 160.0 + 49 * 3600))
    }
}
