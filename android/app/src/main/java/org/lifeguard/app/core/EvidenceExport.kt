package org.lifeguard.app.core

import org.json.JSONArray
import org.json.JSONObject
import java.io.OutputStream
import java.util.zip.ZipEntry
import java.util.zip.ZipOutputStream

/**
 * ייצוא ראיות ל-zip:
 *  manifest.json   - פרטי הייצוא, תוצאת האימות, מתכון ה-hash, רשימת קבצים
 *  entries.jsonl   - הרשומות המפוענחות (שורה לכל רשומה, כולל רשומת הגישה של הייצוא הזה)
 *  audio/NAME.m4a  - קטעי שמע מפוענחים (אם יש)
 *  server_record.json - הרשומה החתומה מהשרת (Ed25519) אם הייתה רשת
 *  SHA256SUMS      - "<sha256>  <שם>" לכל קובץ (פורמט sha256sum)
 * לפני הקריאה: log.open(key, "export") - הייצוא עצמו נרשם בשרשרת.
 */
object EvidenceExport {
    const val FORMAT = "lifeguard-local-evidence/1"

    data class Result(val files: Map<String, String>, val verify: VerifyResult)

    fun writeZip(
        out: OutputStream, log: EvidenceLog, key: String, entries: List<EvidenceEntry>,
        serverRecord: JSONObject?, serverNote: String?, appVersion: String, nowMs: Long,
    ): Result {
        val verify = log.verify(key)
        val meta = log.meta(key)
        val files = LinkedHashMap<String, ByteArray>()
        files["entries.jsonl"] = entries.joinToString("") { it.toJson().toString() + "\n" }.toByteArray(Charsets.UTF_8)
        for (e in entries.filter { it.type == "audio_segment" }) {
            val name = e.dataJson().optString("file")
            val bytes = runCatching { log.readBlob(key, name) }.getOrNull() ?: continue
            files["audio/" + name.removeSuffix(".bin") + ".m4a"] = bytes
        }
        serverRecord?.let { files["server_record.json"] = it.toString(2).toByteArray(Charsets.UTF_8) }

        val list = JSONArray()
        files.forEach { (n, b) -> list.put(JSONObject().put("name", n).put("sha256", Hashes.sha256(b)).put("bytes", b.size)) }
        val manifest = JSONObject()
            .put("format", FORMAT)
            .put("incident_key", key)
            .put("incident_id", meta?.incidentId ?: JSONObject.NULL)
            .put("mode", meta?.mode ?: JSONObject.NULL)
            .put("closed_as", meta?.closedAs ?: JSONObject.NULL)
            .put("exported_at_ms", nowMs)
            .put("app_version", appVersion)
            .put("entries", entries.size)
            .put("head_hash", entries.lastOrNull()?.hash ?: Hashes.GENESIS)
            .put("chain", JSONObject().put("algorithm", "SHA-256")
                .put("recipe", "hash = sha256(utf8(seq + \"|\" + at + \"|\" + type + \"|\" + data + \"|\" + prev)); data = the stored JSON string verbatim")
                .put("genesis_prev", Hashes.GENESIS))
            .put("verify", JSONObject().put("ok", verify.ok).put("problems", JSONArray(verify.problems)))
            .put("files", list)
            .put("server_record", JSONObject().put("included", serverRecord != null)
                .put("note", serverNote ?: JSONObject.NULL)
                .put("algorithm", serverRecord?.optString("algorithm") ?: JSONObject.NULL)
                .put("sha256", serverRecord?.optString("sha256") ?: JSONObject.NULL))
            .put("notes", JSONArray(listOf(
                "at = epoch milliseconds, device clock (not verified by a third party)",
                "audio files: sha256 of the decrypted content is recorded in the chained entry (type audio_segment)",
                "server_record.json: verify the Ed25519 signature over the canonical JSON with the server public key")))
        files["manifest.json"] = manifest.toString(2).toByteArray(Charsets.UTF_8)
        val sums = files.entries.joinToString("") { (n, b) -> "${Hashes.sha256(b)}  $n\n" }.toByteArray(Charsets.UTF_8)

        ZipOutputStream(out).use { z ->
            for ((n, b) in files) { z.putNextEntry(ZipEntry(n)); z.write(b); z.closeEntry() }
            z.putNextEntry(ZipEntry("SHA256SUMS")); z.write(sums); z.closeEntry()
        }
        return Result(files.mapValues { Hashes.sha256(it.value) }, verify)
    }
}
