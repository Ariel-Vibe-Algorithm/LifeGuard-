package org.lifeguard.app

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.BeforeClass
import org.junit.Test
import org.lifeguard.app.hazard.AlertManager
import org.lifeguard.app.hazard.AudioIntel
import org.lifeguard.app.hazard.DistressResult
import org.lifeguard.app.hazard.HazardAlert
import org.lifeguard.app.hazard.HazardAlerts
import org.lifeguard.app.hazard.HazardEvent
import org.lifeguard.app.hazard.HazardFeatures
import org.lifeguard.app.hazard.HazardModels
import org.lifeguard.app.hazard.HazardMonitor
import org.lifeguard.app.hazard.HazardPrefs
import org.lifeguard.app.hazard.HazardReport
import org.lifeguard.app.hazard.Quality
import org.lifeguard.app.hazard.YamnetOutput
import java.io.File
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.Base64
import java.util.SimpleTimeZone
import java.util.TimeZone
import kotlin.math.abs
import kotlin.math.max

/**
 * תאימות Kotlin == Python למנוע הסכנה. הקלט: android/app/src/test/resources/hazard_parity.json שנוצר ע"י
 * tools/build_android_hazard_assets.py מקוד ה-Python של האפליקציה (hazard_features, audio_intel, acoustic_ai_module,
 * hazard_monitor, hazard_alerts) + אותם נכסי מודל שנארזים ב-APK (assets/hazard).
 *
 * YAMNet עצמו לא רץ כאן (רק במכשיר): פלטי YAMNet *אמיתיים* של הגלים הסינתטיים (שחושבו ב-Python) משמשים קלט קבוע,
 * וכל השאר (מאפיינים ספקטרליים, ראשים, החלטות v1/v2, ראש המצוקה, כלל הרצף, מנהל ההתראות) מושווה.
 */
class HazardParityTest {
    companion object {
        lateinit var p: JSONObject
        lateinit var models: HazardModels
        lateinit var intel: AudioIntel

        @BeforeClass @JvmStatic fun load() {
            p = JSONObject(HazardParityTest::class.java.getResourceAsStream("/hazard_parity.json")!!.readBytes().toString(Charsets.UTF_8))
            val dir = File(System.getProperty("lifeguard.hazard.assets") ?: "src/main/assets/hazard")
            models = HazardModels.parse(File(dir, "hazard_models.json").readText(), File(dir, "hazard_models.bin").readBytes())
            intel = AudioIntel(models)
        }

        fun floats(b64: String): FloatArray {
            val fb = ByteBuffer.wrap(Base64.getDecoder().decode(b64)).order(ByteOrder.LITTLE_ENDIAN).asFloatBuffer()
            return FloatArray(fb.remaining()).also { fb.get(it) }
        }

        fun rows(flat: FloatArray, n: Int): Array<FloatArray> {
            val d = flat.size / n
            return Array(n) { flat.copyOfRange(it * d, (it + 1) * d) }
        }

        fun close(exp: Double, act: Double, rel: Double = 1e-3, absTol: Double = 1e-3) =
            abs(exp - act) <= absTol + rel * abs(exp)
    }

    private fun cases(): List<JSONObject> = (0 until p.getJSONArray("cases").length()).map { p.getJSONArray("cases").getJSONObject(it) }

    private fun wave(c: JSONObject): FloatArray {
        val ref = c.optString("wave_ref")
        val src = if (ref.isNotEmpty()) cases().first { it.getString("name") == ref } else c
        return floats(src.getString("wave"))
    }

    private fun yam(c: JSONObject): YamnetOutput {
        val n = c.getInt("frames")
        return YamnetOutput(rows(floats(c.getString("scores")), n), rows(floats(c.getString("emb")), n))
    }

    @Test fun assets_match_sources_and_cover_all_categories() {
        val src = p.getJSONObject("sources")
        for (k in src.keys()) assertEquals(k, src.getString(k), models.sources[k])
        assertEquals(listOf("gunshot", "explosion", "fireworks", "glass_break", "siren", "alarm", "scream", "impact"), models.v1.map { it.name })
        assertEquals(setOf("explosion", "glass_break", "gunshot"), models.v2.map { it.name }.toSet())
        assertEquals(p.getJSONArray("curated_names").length(), models.curatedIdx.size)
        assertNotNull(models.shoutRule)
        for (c in models.v2) assertEquals(1 + 2 * models.curatedIdx.size + HazardFeatures.N_SPECTRAL, c.s2!!.dim)
    }

    @Test fun constants_and_texts_match_windows() {
        val c = p.getJSONObject("constants")
        assertEquals(c.getDouble("EVENT_GAP_S"), HazardAlerts.EVENT_GAP_S, 0.0)
        assertEquals(c.getDouble("COOLDOWN_DISMISS_S"), HazardAlerts.COOLDOWN_DISMISS_S, 0.0)
        assertEquals(c.getDouble("COOLDOWN_FALSE_S"), HazardAlerts.COOLDOWN_FALSE_S, 0.0)
        assertEquals(c.getDouble("COOLDOWN_ACTION_S"), HazardAlerts.COOLDOWN_ACTION_S, 0.0)
        assertEquals(c.getDouble("MIN_GAP_ANY_S"), HazardAlerts.MIN_GAP_ANY_S, 0.0)
        fun map(o: JSONObject) = o.keys().asSequence().associateWith { o.get(it).toString() }
        assertEquals(map(c.getJSONObject("SEVERITY_RANK")), HazardAlerts.SEVERITY_RANK.mapValues { it.value.toString() })
        assertEquals(map(c.getJSONObject("FAMILY")), HazardAlerts.FAMILY)
        assertEquals(map(c.getJSONObject("FALSE_REASONS")), HazardAlerts.FALSE_REASONS.toMap())
        assertEquals(c.getJSONObject("FALSE_REASONS").keys().asSequence().toSet(), HazardAlerts.FALSE_REASONS.keys)
        assertEquals(map(c.getJSONObject("REASONS_EN")), HazardAlerts.REASONS_EN.toMap())
        assertEquals((0 until c.getJSONArray("RESPONSES").length()).map { c.getJSONArray("RESPONSES").getString(it) }, HazardAlerts.RESPONSES)
        assertEquals(map(c.getJSONObject("MIN_SEVERITY_OPTIONS")), HazardAlerts.MIN_SEVERITY_OPTIONS.toMap())
        assertEquals(map(c.getJSONObject("LABELS_EN")), HazardAlerts.LABELS_EN)
        assertEquals(map(c.getJSONObject("EXPLAIN_EN")), HazardAlerts.EXPLAIN_EN)
        val d = c.getJSONObject("DEFAULTS")
        val k = HazardPrefs()
        assertEquals(d.getBoolean("popups"), k.popups); assertEquals(d.getBoolean("sound"), k.sound)
        assertEquals(d.getBoolean("vibration"), k.vibration); assertEquals(d.getString("min_severity"), k.minSeverity)
        assertEquals(d.getString("language"), k.language)
        assertEquals(false, k.background)                         // אנדרואיד בלבד: כבוי עד הפעלה מפורשת
        val mon = c.getJSONObject("MONITOR")
        assertEquals(mon.getDouble("MIN_SECONDS"), HazardMonitor.MIN_SECONDS, 0.0)
        assertEquals(mon.getDouble("MAX_SECONDS"), HazardMonitor.MAX_SECONDS, 0.0)
        assertEquals(mon.getDouble("WINDOW_S"), HazardMonitor.WINDOW_S, 0.0)
        val ex = c.getJSONObject("explain")
        for (cat in ex.keys()) assertEquals(cat, ex.getString(cat),
            HazardMonitor.explain(HazardEvent(cat, "L", null, null, true, detectionConfidence = null, severityPrior = null,
                margin = null, nearThreshold = false, model = null)))
        assertEquals(c.getString("explain_near"), HazardMonitor.explain(HazardEvent("gunshot", null, null, null, true,
            detectionConfidence = null, severityPrior = null, margin = null, nearThreshold = true, model = null)))
        val old = TimeZone.getDefault()
        try {
            TimeZone.setDefault(SimpleTimeZone(c.getInt("contacts_message_tz_offset_s") * 1000, "parity"))
            val a = HazardAlert("x", "blast", "gunshot", "high", 0.9, "ירי", null, c.getDouble("contacts_message_ts"),
                c.getDouble("contacts_message_ts"), 1)
            val cm = c.getJSONObject("contacts_message")
            assertEquals(cm.getString("he"), HazardAlerts.contactsMessage(a, "he"))
            assertEquals(cm.getString("en"), HazardAlerts.contactsMessage(a, "en"))
        } finally { TimeZone.setDefault(old) }
    }

    @Test fun spectral_features_match_python() {
        var worst = 0.0
        for (c in cases()) {
            val w = HazardFeatures.windows(wave(c))[0].second
            val exp = c.getJSONArray("spectral")
            val act = HazardFeatures.spectralFeatures(w)
            assertEquals(exp.length(), act.size)
            for (i in act.indices) {
                val e = exp.getDouble(i)
                worst = max(worst, abs(e - act[i]) / max(1.0, abs(e)))
                assertTrue("${c.getString("name")} feature $i: python=$e kotlin=${act[i]}", close(e, act[i], 1e-3, 1e-4))
            }
        }
        println("spectral parity: worst relative error $worst")
    }

    @Test fun v2_stage_logits_match_python() {
        for (c in cases()) {
            if (!c.has("frames")) continue
            val y = yam(c)
            val w = HazardFeatures.windows(wave(c))[0].second
            val raw = c.getJSONObject("raw")
            for (cat in models.v2) {
                val r = raw.getJSONObject(cat.name).getJSONObject("v2")
                val l1 = cat.s1.logit(HazardFeatures.stage1Vector(y.emb))
                assertTrue("${c.getString("name")}/${cat.name} l1 ${r.getDouble("l1")} vs $l1", close(r.getDouble("l1"), l1))
                if (cat.twoStage) {
                    val l2 = cat.s2!!.logit(HazardFeatures.verifierVector(l1, y.scores, models.curatedIdx, w))
                    assertTrue("${c.getString("name")}/${cat.name} l2 ${r.getDouble("l2")} vs $l2", close(r.getDouble("l2"), l2))
                }
            }
        }
    }

    @Test fun full_reports_match_audio_intel_analyze() {
        val seen = HashSet<String>()
        for (c in cases()) {
            val name = c.getString("name")
            val x = wave(c)
            val exp = c.getJSONObject("report")
            val y = if (c.has("frames")) yam(c) else null
            var calls = 0
            val rep = intel.analyze(x) { calls++; y ?: throw AssertionError("YAMNet called on $name") }!!
            val q = exp.getJSONObject("quality")
            assertEquals(name, q.getString("status"), rep.quality.status)
            assertEquals(name, q.getString("reason"), rep.quality.reason)
            assertEquals(name, exp.getString("model_version"), rep.modelVersion)
            if (q.getString("status") == "unavailable") { assertEquals(0, calls); assertTrue(rep.events.isEmpty()); continue }
            // אירועים: אותן קטגוריות, אותו סדר, אותם מודלים והחלטות
            val ev = exp.getJSONArray("events")
            assertEquals(name, (0 until ev.length()).map { ev.getJSONObject(it).getString("category") }, rep.events.map { it.category })
            for (i in 0 until ev.length()) { checkRow(name, ev.getJSONObject(i), rep.events[i]); seen += rep.events[i].model!! }
            val top = exp.getJSONArray("top")
            assertEquals(name, (0 until top.length()).map { top.getJSONObject(it).getString("category") }, rep.top.map { it.category })
            for (i in 0 until top.length()) checkRow(name, top.getJSONObject(i), rep.top[i])
            val cs = exp.getJSONObject("category_scores")
            assertEquals(cs.keys().asSequence().toSet(), rep.categoryScores.keys)
            for (k in cs.keys()) assertTrue("$name $k", close(cs.getDouble(k), rep.categoryScores.getValue(k)))
            val d = exp.getJSONObject("distress")
            val rd = rep.distress!!
            assertEquals(name, d.getBoolean("detected"), rd.detected)
            assertTrue("$name distress ${d.getDouble("score")} vs ${rd.score}", close(d.getDouble("score"), rd.score!!))
            assertTrue(name, close(d.getDouble("probability"), rd.probability!!, 1e-3, 2e-3))
            // מקרים "מכוונים" עם כל המסגרות זהות: תיקו מלא ב-argmax (float32 של BLAS בוחר מסגרת שרירותית) - לא משווים זמן
            val tied = y!!.emb.all { it.contentEquals(y.emb[0]) }
            if (!tied) assertEquals(name, d.getDouble("at_seconds"), rd.atSeconds!!, 1e-9)
            val su = exp.getJSONArray("suspected_unvalidated")
            assertEquals(name, su.length(), rep.suspectedUnvalidated.size)
            // ראש המצוקה ישירות (acoustic_ai_module.analyze_embeddings)
            val dd = c.getJSONObject("distress_direct")
            val k = intel.analyzeEmbeddings(y.emb)!!
            assertEquals(dd.getBoolean("detected"), k.detected)
            assertTrue(close(dd.getDouble("score"), k.score!!))
        }
        // הוקטורים מכסים את כל ענפי המנוע
        assertTrue(seen.toString(), seen.containsAll(listOf("head", "zero_shot", "v2_two_stage")))
    }

    private fun checkRow(name: String, e: JSONObject, k: HazardEvent) {
        val cat = e.getString("category")
        val tag = "$name/$cat"
        assertEquals(tag, e.getString("model"), k.model)
        assertEquals(tag, e.getBoolean("detected"), k.detected)
        assertEquals(tag, e.getString("severity_prior"), k.severityPrior)
        assertEquals(tag, e.getString("label_he"), k.labelHe)
        assertEquals(tag, e.getBoolean("near_threshold"), k.nearThreshold)
        assertEquals(tag, e.getDouble("at_seconds"), k.atSeconds, 1e-9)
        assertTrue("$tag score ${e.getDouble("score")} vs ${k.score}", close(e.getDouble("score"), k.score!!))
        assertEquals(tag, e.getDouble("threshold"), k.threshold!!, 1e-9)
        assertTrue("$tag margin", close(e.getDouble("margin"), k.margin!!))
        if (e.isNull("fa_per_hour_at_score")) assertNull(tag, k.faPerHour) else assertEquals(tag, e.getDouble("fa_per_hour_at_score"), k.faPerHour!!, 1e-9)
        // Platt של חלק מהקטגוריות הוא כמעט פונקציית מדרגה (a ~ 1e7): משווים את הביטחון רק כשהציון לא על המדרגה
        val (a, b) = models.v2ByName[cat]?.let { it.plattA to it.plattB }
            ?: models.v1.first { it.name == cat }.let { it.plattA to it.plattB }
        val z = a * k.rawScore!! + b
        if (abs(z) > abs(a) * 1e-3 + 20 || abs(a) < 100) {
            assertTrue("$tag confidence ${e.getDouble("detection_confidence")} vs ${k.detectionConfidence}",
                close(e.getDouble("detection_confidence"), k.detectionConfidence!!, 1e-3, 2e-3))
        }
    }

    // ------------------------------------------------------------------ סקריפט המוניטור (AlertManager + רצף צעקות)
    private fun parseReport(o: JSONObject): HazardReport {
        val ev = o.optJSONArray("events") ?: JSONArray()
        val events = (0 until ev.length()).map { i ->
            val e = ev.getJSONObject(i)
            HazardEvent(e.getString("category"), e.optString("label_he"), e.optDouble("score"), e.optDouble("threshold"), true,
                detectionConfidence = e.optDouble("detection_confidence"), severityPrior = e.getString("severity_prior"),
                margin = e.optDouble("margin"), nearThreshold = e.optBoolean("near_threshold"), model = e.optString("model"))
        }
        val d = if (o.isNull("distress")) null else o.getJSONObject("distress").let {
            DistressResult(if (it.has("score")) it.getDouble("score") else null, it.optBoolean("detected"),
                if (it.has("probability")) it.getDouble("probability") else null)
        }
        val cs = o.optJSONObject("category_scores")
        return HazardReport("audio_intel_v1", o.getString("model_version"), Quality(o.getJSONObject("quality").getString("status"), ""),
            events, events, cs?.keys()?.asSequence()?.associateWith { cs.getDouble(it) } ?: emptyMap(), d)
    }

    private fun prefsOf(o: JSONObject?) = if (o == null) HazardPrefs() else HazardPrefs(o.getBoolean("popups"), o.getBoolean("sound"),
        o.getBoolean("vibration"), o.getString("min_severity"), o.getString("language"))

    @Test fun monitor_script_matches_python_monitor() {
        val m = p.getJSONObject("monitor")
        val steps = m.getJSONArray("steps")
        val expected = m.getJSONArray("expected")
        var now = 0.0
        val queue = ArrayDeque<HazardReport>()
        var prefs = HazardPrefs()
        var can: Pair<Boolean, String?> = true to null
        val shown = ArrayList<JSONObject>()
        val records = ArrayList<JSONObject>()
        val mgr = AlertManager(clock = { now })
        val mon = HazardMonitor(analyze = { queue.removeFirst() }, canShow = { can },
            onAlert = { k, a -> shown += JSONObject().put("kind", k).put("category", a.category).put("severity", a.severity).put("count", a.count) },
            record = { a, s, why -> records += JSONObject().put("category", a.category).put("severity", a.severity).put("count", a.count)
                .put("shown", s).put("not_shown_reason", why ?: JSONObject.NULL) },
            prefs = { prefs }, manager = mgr, sequenceRule = models.shoutRule)
        for (i in 0 until steps.length()) {
            val st = steps.getJSONObject(i)
            val exp = expected.getJSONObject(i)
            now = st.getDouble("now")
            if (st.has("close")) {
                val a = mgr.open[st.getString("family")]
                if (a != null) mgr.close(a, st.getString("close"))
                assertEquals("step $i", exp.getBoolean("closed"), a != null)
                continue
            }
            prefs = prefsOf(st.optJSONObject("prefs"))
            can = st.optJSONArray("can_show")?.let { it.getBoolean(0) to (if (it.isNull(1)) null else it.getString(1)) } ?: (true to null)
            queue.clear()
            val reps = st.getJSONArray("reports")
            for (j in 0 until reps.length()) queue += parseReport(reps.getJSONObject(j))
            val chunk = FloatArray((st.getDouble("seconds") * 16000).toInt()) { 0.01f }
            val out = mon.process(chunk, now)
            assertTrue("step $i: all window reports consumed", queue.isEmpty())
            val eo = exp.getJSONArray("out")
            assertEquals("step $i count", eo.length(), out.size)
            for (j in 0 until eo.length()) {
                val e = eo.getJSONObject(j)
                val k = out[j]
                val tag = "step $i/$j"
                assertEquals(tag, e.getString("kind"), k.kind)
                assertEquals(tag, e.getString("category"), k.alert.category)
                assertEquals(tag, e.getString("severity"), k.alert.severity)
                assertEquals(tag, e.getInt("count"), k.alert.count)
                assertEquals(tag, e.getDouble("confidence"), k.alert.confidence, 1e-9)
                assertEquals(tag, e.optString("label_he"), k.alert.labelHe)
                assertEquals(tag, e.optString("explanation"), k.alert.explanation)
                assertEquals(tag, e.getBoolean("show"), k.show)
                assertEquals(tag, if (e.isNull("why")) null else e.getString("why"), k.why)
                val meta = e.getJSONObject("meta")
                assertEquals(tag, meta.keys().asSequence().toSet(), k.alert.meta.keys)
                for (key in meta.keys()) {
                    val ev = meta.get(key)
                    val kv = k.alert.meta[key]
                    when {
                        ev == JSONObject.NULL -> assertNull("$tag meta $key", kv)
                        ev is Number -> assertEquals("$tag meta $key", ev.toDouble(), (kv as Number).toDouble(), 1e-9)
                        else -> assertEquals("$tag meta $key", ev.toString(), kv.toString())
                    }
                }
            }
        }
        val es = m.getJSONArray("shown")
        assertEquals(es.length(), shown.size)
        for (i in 0 until es.length()) for (key in listOf("kind", "category", "severity", "count"))
            assertEquals("shown $i $key", es.getJSONObject(i).get(key).toString(), shown[i].get(key).toString())
        val er = m.getJSONArray("records")
        assertEquals(er.length(), records.size)
        for (i in 0 until er.length()) for (key in listOf("category", "severity", "count", "shown", "not_shown_reason"))
            assertEquals("record $i $key", er.getJSONObject(i).get(key).toString(), records[i].get(key).toString())
    }
}
