package org.lifeguard.app

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import org.lifeguard.app.core.AesGcmSealer
import org.lifeguard.app.core.EvidenceExport
import org.lifeguard.app.core.EvidenceLog
import org.lifeguard.app.core.Hashes
import java.io.ByteArrayInputStream
import java.io.ByteArrayOutputStream
import java.io.File
import java.util.Base64
import java.util.zip.ZipInputStream
import javax.crypto.KeyGenerator

class EvidenceLogTest {
    @get:Rule val tmp = TemporaryFolder()

    private val key = KeyGenerator.getInstance("AES").apply { init(256) }.generateKey()
    private val sealer = AesGcmSealer { key }
    private var now = 1_700_000_000_000L
    private fun newLog(root: File = tmp.root) = EvidenceLog(root, sealer) { now++ }

    private fun sample(log: EvidenceLog, k: String = "cid-1") {
        log.start(k, "loud", JSONObject().put("source", "button"))
        log.append(k, "sos_queued", JSONObject().put("client_id", k))
        log.append(k, "location", JSONObject().put("lat", 32.08).put("lon", 34.78).put("accuracy_m", 8))
        log.append(k, "sos_sent", JSONObject().put("incident_id", "inc_1"))
        log.append(k, "chat_sent", JSONObject().put("text", "אני בסדר, מחכה"))
    }

    private fun logFile(k: String = "cid-1") = File(File(tmp.root, k), "log")

    @Test fun chain_links_and_verifies() {
        val log = newLog(); sample(log)
        val r = log.verify("cid-1")
        assertTrue(r.problems.toString(), r.ok)
        assertEquals(5, r.count)
        val entries = log.open("cid-1", "view")
        assertEquals(Hashes.GENESIS, entries[0].prev)
        for (i in 1 until entries.size) {
            assertEquals(entries[i - 1].hash, entries[i].prev)
            assertEquals(i.toLong(), entries[i].seq)
            assertEquals(entries[i].computeHash(), entries[i].hash)
        }
        assertEquals("incident_start", entries[0].type)
        assertEquals("אני בסדר, מחכה", entries[4].dataJson().getString("text"))
    }

    @Test fun encrypted_at_rest_no_plaintext_on_disk() {
        val log = newLog(); sample(log)
        val raw = logFile().readText() + File(File(tmp.root, "cid-1"), "meta").readText()
        assertFalse(raw.contains("34.78")); assertFalse(raw.contains("sos_sent")); assertFalse(raw.contains("inc_1"))
        // מפתח אחר לא מפענח (כמו מכשיר אחר / גיבוי)
        val other = KeyGenerator.getInstance("AES").apply { init(256) }.generateKey()
        val r = EvidenceLog(tmp.root, AesGcmSealer { other }).verify("cid-1")
        assertFalse(r.ok)
    }

    @Test fun every_open_and_export_is_appended_to_the_chain() {
        val log = newLog(); sample(log)
        val v = log.open("cid-1", "view")
        assertEquals("access", v.last().type); assertEquals("view", v.last().dataJson().getString("action"))
        val e = log.open("cid-1", "export")
        assertEquals(7, e.size)
        assertEquals(listOf("view", "export"), e.filter { it.type == "access" }.map { it.dataJson().getString("action") })
        assertTrue(log.verify("cid-1").ok)
    }

    @Test fun modified_entry_is_detected() {
        val log = newLog(); sample(log)
        // מפענחים שורה, משנים את המיקום, ומצפינים מחדש עם אותו מפתח (תוקף שיש לו גישה למפתח)
        val lines = logFile().readLines().toMutableList()
        val o = JSONObject(String(sealer.open(Base64.getDecoder().decode(lines[2]))))
        o.put("data", JSONObject(o.getString("data")).put("lat", 31.0).toString())
        lines[2] = Base64.getEncoder().encodeToString(sealer.seal(o.toString().toByteArray()))
        logFile().writeText(lines.joinToString("\n") + "\n")
        val r = log.verify("cid-1")
        assertFalse(r.ok)
        assertTrue(r.problems.toString(), r.problems.any { it.contains("שורה 3") && it.contains("hash") })
    }

    @Test fun deleted_or_reordered_or_corrupted_lines_are_detected() {
        val log = newLog(); sample(log)
        val orig = logFile().readLines()

        logFile().writeText((orig.take(2) + orig.drop(3)).joinToString("\n") + "\n")     // מחיקה באמצע
        assertTrue(log.verify("cid-1").problems.any { it.contains("מספר רץ") })

        logFile().writeText(listOf(orig[0], orig[2], orig[1], orig[3], orig[4]).joinToString("\n") + "\n")  // החלפת סדר
        assertFalse(log.verify("cid-1").ok)

        val bad = orig.toMutableList(); bad[1] = bad[1].replaceRange(20, 24, "AAAA")       // שינוי ciphertext
        logFile().writeText(bad.joinToString("\n") + "\n")
        assertTrue(log.verify("cid-1").problems.any { it.contains("פענוח נכשל") })

        logFile().writeText(orig.take(3).joinToString("\n") + "\n")                       // קטיעת הסוף
        val r = log.verify("cid-1")
        assertTrue(r.problems.toString(), r.problems.any { it.contains("קטיעה") })

        logFile().writeText(orig.joinToString("\n") + "\n")
        assertTrue(log.verify("cid-1").ok)
    }

    @Test fun sealed_audio_segment_hash_is_chained_and_tamper_detected() {
        val log = newLog(); sample(log)
        val plain = tmp.newFile("seg.m4a").apply { writeBytes(ByteArray(5000) { (it % 251).toByte() }) }
        val sha = Hashes.sha256(plain.readBytes())
        val e = log.sealFile("cid-1", plain, "audio", JSONObject().put("duration_ms", 60000))
        assertFalse(plain.exists())                                   // המקור הלא מוצפן נמחק
        assertEquals("audio_segment", e.type)
        assertEquals(sha, e.dataJson().getString("sha256"))
        val stored = File(File(tmp.root, "cid-1"), e.dataJson().getString("file"))
        assertNotEquals(sha, Hashes.sha256(stored.readBytes()))       // מוצפן על הדיסק
        assertTrue(log.verify("cid-1").ok)
        // החלפת קובץ השמע בקובץ אחר (מוצפן כראוי) -> מתגלה
        stored.writeBytes(sealer.seal(ByteArray(5000)))
        assertTrue(log.verify("cid-1").problems.any { it.contains("SHA-256") })
    }

    @Test fun export_zip_has_manifest_sums_entries_and_server_record() {
        val log = newLog(); sample(log)
        log.setIncidentId("cid-1", "inc_1")
        val plain = tmp.newFile("seg.m4a").apply { writeBytes(byteArrayOf(1, 2, 3, 4, 5, 6, 7, 8)) }
        log.sealFile("cid-1", plain, "audio")
        val entries = log.open("cid-1", "export")
        val server = JSONObject().put("record", JSONObject().put("format", "x")).put("sha256", "ab").put("signature", "cd")
            .put("public_key", "ef").put("algorithm", "Ed25519")
        val bos = ByteArrayOutputStream()
        val res = EvidenceExport.writeZip(bos, log, "cid-1", entries, server, null, "0.2.0", now)
        assertTrue(res.verify.ok)

        val files = LinkedHashMap<String, ByteArray>()
        ZipInputStream(ByteArrayInputStream(bos.toByteArray())).use { z ->
            while (true) { val e = z.nextEntry ?: break; files[e.name] = z.readBytes() }
        }
        assertEquals(setOf("entries.jsonl", "audio/audio_001.m4a", "server_record.json", "manifest.json", "SHA256SUMS"), files.keys)
        assertTrue(files["audio/audio_001.m4a"]!!.contentEquals(byteArrayOf(1, 2, 3, 4, 5, 6, 7, 8)))
        // SHA256SUMS תואם לכל קובץ
        val sums = String(files["SHA256SUMS"]!!).trim().lines().associate { l -> l.substringAfter("  ") to l.substringBefore("  ") }
        assertEquals(files.keys - "SHA256SUMS", sums.keys)
        for ((n, h) in sums) assertEquals(n, Hashes.sha256(files[n]!!), h)
        val m = JSONObject(String(files["manifest.json"]!!))
        assertEquals("inc_1", m.getString("incident_id"))
        assertEquals(entries.size, m.getInt("entries"))
        assertTrue(m.getJSONObject("verify").getBoolean("ok"))
        assertTrue(m.getJSONObject("server_record").getBoolean("included"))
        assertEquals("Ed25519", JSONObject(String(files["server_record.json"]!!)).getString("algorithm"))
        // השורות המפוענחות ניתנות לאימות עצמאי לפי המתכון במניפסט
        val lines = String(files["entries.jsonl"]!!).trim().lines().map { JSONObject(it) }
        var prev = Hashes.GENESIS
        for (o in lines) {
            assertEquals(prev, o.getString("prev"))
            assertEquals(Hashes.sha256("${o.getLong("seq")}|${o.getLong("at")}|${o.getString("type")}|${o.getString("data")}|$prev"),
                o.getString("hash"))
            prev = o.getString("hash")
        }
        assertEquals("export", JSONObject(lines.last().getString("data")).getString("action"))
    }

    @Test fun close_list_and_delete() {
        val log = newLog(); sample(log, "a"); sample(log, "b")
        log.close("a", "false_alarm")
        assertEquals("false_alarm", log.meta("a")!!.closedAs)
        assertNull(log.meta("b")!!.closedAs)
        assertEquals(setOf("a", "b"), log.list().map { it.key }.toSet())
        assertTrue(log.verify("a").ok)
        assertTrue(log.delete("a"))
        assertEquals(listOf("b"), log.list().map { it.key })
        // מפתח לא חוקי (ניסיון path traversal)
        assertTrue(runCatching { log.append("../x", "t") }.isFailure)
    }
}
