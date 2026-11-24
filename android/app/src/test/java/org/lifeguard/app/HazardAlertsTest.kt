package org.lifeguard.app

import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import org.lifeguard.app.api.ApiClient
import org.lifeguard.app.api.TrustedNotifyResult
import org.lifeguard.app.core.AesGcmSealer
import org.lifeguard.app.core.MemoryTextStore
import org.lifeguard.app.core.SealedFileTextStore
import org.lifeguard.app.hazard.AlertManager
import org.lifeguard.app.hazard.Detection
import org.lifeguard.app.hazard.DistressResult
import org.lifeguard.app.hazard.EnergyGate
import org.lifeguard.app.hazard.HazardActions
import org.lifeguard.app.hazard.HazardAlert
import org.lifeguard.app.hazard.HazardAlerts
import org.lifeguard.app.hazard.HazardEvent
import org.lifeguard.app.hazard.HazardGate
import org.lifeguard.app.hazard.HazardLog
import org.lifeguard.app.hazard.HazardMonitor
import org.lifeguard.app.hazard.HazardNotConnected
import org.lifeguard.app.hazard.HazardPrefs
import org.lifeguard.app.hazard.HazardPrefsStore
import org.lifeguard.app.hazard.HazardReport
import org.lifeguard.app.hazard.HazardText
import org.lifeguard.app.hazard.Quality
import org.lifeguard.app.hazard.ShoutRule
import org.lifeguard.app.hazard.TrustedNotifier
import org.lifeguard.app.ui.HazardSettingsText
import java.io.File
import javax.crypto.KeyGenerator

/**
 * התראות סכנה אינטראקטיביות באנדרואיד - אותן הבטחות כמו tests/test_hazard_alerts.py ב-Windows:
 * ניהול חכם (קיבוץ / עדכון / שקט / הגבלת קצב / אירועים עצמאיים), העדפות מוצפנות ששורדות הפעלה מחדש, מוניטור שלא
 * מפעיל SOS לעולם, אין חלון במצוקה שקטה / SOS, פעולות רק אחרי אישור, אנשי קשר בלי "נשלח" מדומה, משוב שווא.
 */
class HazardAlertsTest {
    @get:Rule val tmp = TemporaryFolder()

    private var now = 1000.0
    private var seq = 0
    private fun manager() = AlertManager(clock = { now }, idGen = { "hz-%04d".format(++seq) })

    private fun det(cat: String = "gunshot", sev: String = "high", conf: Double = 0.7) =
        Detection(cat, sev, conf, cat, "x", mapOf("score" to 3.0, "model_version" to "v"))

    // ---------------------------------------------------------------- ניהול חכם
    @Test fun grouping_update_cooldown_and_independent_events() {
        val m = manager()
        val (kind, a) = m.decide(det("impact", "low"))
        assertEquals("new", kind)
        now += 30
        assertEquals("update", m.decide(det("glass_break", "medium")).first)        // אותה משפחה, חומרה עלתה
        assertEquals("medium", a!!.severity); assertEquals(2, a.count)
        now += 10
        assertEquals("grouped", m.decide(det("impact", "low")).first)
        now += 25
        val (k2, b) = m.decide(det("gunshot", "high"))                               // משפחה אחרת = אירוע עצמאי חדש
        assertEquals("new", k2); assertNotEquals(a.id, b!!.id)
        m.close(a, "dismiss")
        now += 60
        assertEquals("cooldown", m.decide(det("impact", "low")).first)
        assertEquals("new", m.decide(det("glass_break", "high")).first)              // חומרה גבוהה מהשקט - עוקפת
        m.close(b, "false_alarm")
        now += HazardAlerts.COOLDOWN_DISMISS_S + 1
        assertEquals("cooldown", m.decide(det("gunshot", "high")).first)             // שווא = שקט ארוך יותר
        now += HazardAlerts.COOLDOWN_FALSE_S
        assertEquals("new", m.decide(det("gunshot", "high")).first)
    }

    @Test fun rate_limit_between_independent_popups() {
        val m = manager()
        m.decide(det("gunshot", "high"))
        now += 5
        assertEquals("rate_limited", m.decide(det("alarm", "medium")).first)
        now += HazardAlerts.MIN_GAP_ANY_S
        assertEquals("new", m.decide(det("alarm", "medium")).first)
    }

    @Test fun action_cooldown_and_grouping_window() {
        val m = manager()
        val a = m.decide(det("scream", "medium")).second!!
        now += HazardAlerts.EVENT_GAP_S + 1                                            // מעבר לחלון הקיבוץ - אירוע חדש
        val (k, b) = m.decide(det("distress_voice", "medium"))
        assertEquals("new", k); assertNotEquals(a.id, b!!.id)
        m.close(b, "contacts")                                                         // פעולה -> שקט של 10 דק'
        now += HazardAlerts.COOLDOWN_ACTION_S - 1
        assertEquals("cooldown", m.decide(det("scream", "medium")).first)
        now += 2
        assertEquals("new", m.decide(det("scream", "medium")).first)
    }

    // ---------------------------------------------------------------- העדפות
    private val key = KeyGenerator.getInstance("AES").apply { init(256) }.generateKey()

    @Test fun prefs_default_on_encrypted_and_persist_across_restart() {
        val f = File(tmp.root, "prefs.bin")
        val store = HazardPrefsStore(SealedFileTextStore(f, AesGcmSealer { key }))
        val d = store.load()
        assertTrue(d.popups); assertEquals("medium", d.minSeverity); assertFalse(d.background)   // זיהוי ברקע כבוי
        store.save(HazardPrefs(popups = false, sound = false, vibration = true, minSeverity = "high", language = "en", background = true))
        val raw = String(f.readBytes(), Charsets.ISO_8859_1)
        assertFalse(raw.contains("popups")); assertFalse(raw.contains("min_severity"))     // מוצפן
        val reloaded = HazardPrefsStore(SealedFileTextStore(f, AesGcmSealer { key })).load()  // "הפעלה מחדש"
        assertEquals(HazardPrefs(false, false, true, "high", "en", true), reloaded)
        try { store.save(HazardPrefs(minSeverity = "extreme")); fail() } catch (_: IllegalArgumentException) { }
        try { store.save(HazardPrefs(language = "fr")); fail() } catch (_: IllegalArgumentException) { }
        // ערך לא תקין / מפתח אחר -> ברירות המחדל, לא קריסה
        assertEquals(HazardPrefs(), HazardPrefs.fromJson(JSONObject().put("popups", "yes").put("min_severity", "x")))
        val other = KeyGenerator.getInstance("AES").apply { init(256) }.generateKey()
        assertEquals(HazardPrefs(), HazardPrefsStore(SealedFileTextStore(f, AesGcmSealer { other })).load())
    }

    @Test fun settings_texts_are_honest() {
        assertTrue(HazardSettingsText.ALWAYS_ON.contains("נשארות פעילות תמיד"))
        assertTrue(HazardSettingsText.ALWAYS_ON.contains("הכל בסדר?"))
        assertTrue(HazardSettingsText.BACKGROUND_EXPLAIN.contains("כבוי כברירת מחדל"))
        assertTrue(HazardSettingsText.BACKGROUND_EXPLAIN.contains("לא נשמר ולא נשלח"))
        assertTrue(HazardSettingsText.EXPLAIN.contains("לעולם לא מפעיל SOS לבד"))
        assertEquals(setOf("high", "medium", "low"), HazardAlerts.MIN_SEVERITY_OPTIONS.keys)
        assertTrue(HazardText.he.rtl); assertFalse(HazardText.en.rtl)
        assertEquals("זיהוי של צליל בלבד - לא אישור לסכנה", HazardText.he.notVerified)
        assertTrue(HazardText.en.sos.contains("Activate SOS"))
    }

    // ---------------------------------------------------------------- המוניטור
    private fun ev(cat: String, sev: String) = HazardEvent(cat, cat, 5.0, 4.0, true, detectionConfidence = 0.8,
        severityPrior = sev, margin = 1.0, nearThreshold = false, model = "head")

    private fun report(vararg events: Pair<String, String>, distress: DistressResult? = null, quality: String = "ok",
                       scream: Double? = null) =
        HazardReport("audio_intel_v1", "hazard_audio_v1", Quality(quality, ""), events.map { ev(it.first, it.second) },
            categoryScores = scream?.let { mapOf("scream" to it) } ?: emptyMap(), distress = distress)

    private class Rig(val mon: HazardMonitor, val shown: MutableList<Pair<String, HazardAlert>>, val log: HazardLog)

    private fun rig(rep: HazardReport?, can: Pair<Boolean, String?> = true to null, prefs: HazardPrefs = HazardPrefs(),
                    rule: ShoutRule? = null): Rig {
        val shown = ArrayList<Pair<String, HazardAlert>>()
        val log = HazardLog(MemoryTextStore(), clock = { now })
        val mon = HazardMonitor(analyze = { rep }, canShow = { can }, onAlert = { k, a -> shown += k to a },
            record = { a, s, why -> log.recordAlert(a, s, why) }, prefs = { prefs }, manager = manager(), sequenceRule = rule)
        return Rig(mon, shown, log)
    }

    private fun audio(sec: Double = 1.5) = FloatArray((16000 * sec).toInt()) { (Math.sin(it * 0.3) * 0.05).toFloat() }

    @Test fun monitor_shows_records_without_audio_or_location_and_groups() {
        val r = rig(report("gunshot" to "high", "siren" to "info"))
        val out = r.mon.process(audio(), now)
        assertEquals(listOf("new"), out.map { it.kind })                               // סירנה = מידע, לא חלון
        assertEquals("gunshot", r.shown.single().second.category)
        val rec = r.log.list().single()
        assertTrue(rec.getBoolean("shown")); assertEquals("hazard_audio_v1", rec.getString("model_version"))
        val txt = rec.toString()
        assertFalse(txt.contains("lat")); assertFalse(txt.contains("lon")); assertFalse(txt.contains("audio\""))
        r.mon.process(audio(), now + 1)
        assertEquals(1, r.shown.size)                                                  // אותו אירוע - לא חלון שני
    }

    @Test fun popups_disabled_detection_still_runs_and_is_recorded() {
        val r = rig(report("gunshot" to "high"), prefs = HazardPrefs(popups = false))
        r.mon.process(audio(), now)
        assertTrue(r.shown.isEmpty())
        val rec = r.log.list().single()
        assertFalse(rec.getBoolean("shown")); assertEquals("popups_disabled", rec.getString("not_shown_reason"))
    }

    @Test fun min_severity_silent_tracking_sos_and_digital_silence() {
        val r = rig(report("impact" to "low"), prefs = HazardPrefs(minSeverity = "medium"))
        r.mon.process(audio(), now)
        assertTrue(r.shown.isEmpty()); assertEquals("below_min_severity", r.log.list().single().getString("not_shown_reason"))
        // מצוקה שקטה -> לעולם לא חלון; SOS פעיל -> event_active
        val silent = rig(report("gunshot" to "high"), can = HazardGate.canShow(true, "silent", false, null))
        silent.mon.process(audio(), now)
        assertTrue(silent.shown.isEmpty()); assertEquals("silent_tracking", silent.log.list().single().getString("not_shown_reason"))
        val sos = rig(report("gunshot" to "high"), can = HazardGate.canShow(true, "loud", false, null))
        sos.mon.process(audio(), now)
        assertTrue(sos.shown.isEmpty()); assertEquals("event_active", sos.log.list().single().getString("not_shown_reason"))
        val dig = rig(report("gunshot" to "high", quality = "unavailable"))
        assertTrue(dig.mon.process(audio(), now).isEmpty()); assertTrue(dig.shown.isEmpty())
        val short = rig(report("gunshot" to "high"))
        assertTrue(short.mon.process(audio(0.2), now).isEmpty())                       // קצר מדי
    }

    @Test fun gate_states() {
        assertEquals(true to null, HazardGate.canShow(false, null, false, null))
        assertEquals(false to "silent_tracking", HazardGate.canShow(true, "silent", false, null))
        assertEquals(false to "silent_tracking", HazardGate.canShow(false, null, true, "silent"))   // ליווי שקט
        assertEquals(false to "event_active", HazardGate.canShow(true, "loud", false, null))
        assertEquals(true to null, HazardGate.canShow(false, null, true, "loud"))
    }

    @Test fun distress_voice_from_distress_head() {
        val r = rig(report(distress = DistressResult(7.0, true, 0.9)))
        r.mon.process(audio(), now)
        val a = r.shown.single().second
        assertEquals("distress_voice", a.category); assertTrue(a.explanation!!.contains("שמחה")); assertEquals("medium", a.severity)
    }

    @Test fun shout_sequence_rule_needs_k_soft_windows() {
        val rule = ShoutRule("shout_sequence_v1", 3, 3.0, 0.3, 9.0)
        val soft = report(distress = DistressResult(3.5, false, 0.6))
        val r = rig(soft, rule = rule)
        r.mon.process(audio(), 100.0)
        r.mon.process(audio(), 103.0)
        assertTrue(r.shown.isEmpty())                                                  // שתי "כמעט-צעקות" - עדיין לא
        r.mon.process(audio(), 106.0)
        val a = r.shown.single().second
        assertEquals("צעקות חוזרות", a.labelHe); assertEquals("medium", a.severity); assertEquals("shout_sequence_v1", a.meta["rule"])
        val quiet = rig(report(distress = DistressResult(1.0, false, 0.6)), rule = rule)
        for (t in listOf(0.0, 3.0, 6.0, 9.0)) quiet.mon.process(audio(), t)
        assertTrue(quiet.shown.isEmpty())                                              // מתחת לסף הנמוך - אף פעם
        val far = rig(soft, rule = rule)
        for (t in listOf(0.0, 60.0, 120.0)) far.mon.process(audio(), t)
        assertTrue(far.shown.isEmpty())                                                // מפוזר בזמן - לא רצף
        val one = rig(soft, rule = rule)
        one.mon.process(audio(4.5), 50.0)                                              // מקטע ארוך אחד = 3 חלונות
        assertEquals(1, one.shown.size)
        val scream = rig(report(distress = DistressResult(1.0, false, 0.5), scream = 0.35), rule = rule)
        scream.mon.process(audio(4.5), 50.0)                                           // zero-shot "צרחה" מעל s_low
        assertEquals(1, scream.shown.size)
        val none = rig(soft, rule = null)
        for (t in listOf(0.0, 3.0, 6.0)) none.mon.process(audio(), t)
        assertTrue(none.shown.isEmpty())                                               // בלי קובץ כלל - כבוי
    }

    @Test fun detection_alone_never_triggers_sos_or_contacts() {
        var sos = 0
        var notified = 0
        val log = HazardLog(MemoryTextStore(), clock = { now })
        val m = manager()
        HazardActions(m, log, connected = { true }, notifier = { countingNotifier { notified++ } }, startSos = { sos++ })
        val mon = HazardMonitor(analyze = { report("gunshot" to "high", "explosion" to "high", distress = DistressResult(9.0, true, 0.99)) },
            record = { a, s, w -> log.recordAlert(a, s, w) }, manager = m)
        repeat(20) { mon.process(audio(6.0), now); now += 30 }
        assertEquals(0, sos); assertEquals(0, notified)
        assertTrue(log.list().all { it.isNull("response") })
    }

    @Test fun energy_gate_skips_near_silence_but_not_sharp_sounds() {
        assertFalse(EnergyGate.passes(FloatArray(24000) { 0.0005f }))
        assertTrue(EnergyGate.passes(FloatArray(24000) { if (it % 2 == 0) 0.02f else -0.02f }))
        assertTrue(EnergyGate.passes(FloatArray(24000).also { it[100] = 0.4f }))      // ירייה רחוקה / שבירה קצרה
        assertTrue(EnergyGate.isDigitalSilence(FloatArray(10)))
    }

    // ---------------------------------------------------------------- פעולות
    private fun countingNotifier(onSend: () -> Unit) = object : TrustedNotifier {
        override fun notifyTrusted(text: String): TrustedNotifyResult { onSend(); return TrustedNotifyResult(listOf("mom"), mapOf("mom" to 5L)) }
        override fun notificationStatus(ids: Map<String, Long>) = mapOf("mom" to true)
    }

    private fun recorded(log: HazardLog, m: AlertManager, cat: String = "gunshot"): HazardAlert {
        val a = m.decide(det(cat)).second!!
        log.recordAlert(a, true, null)
        now += 30
        return a
    }

    @Test fun sos_only_after_confirmation_and_through_manual_flow() {
        var sos = 0
        val log = HazardLog(MemoryTextStore(), clock = { now })
        val m = manager()
        val act = HazardActions(m, log, { true }, { countingNotifier {} }, startSos = { sos++ })
        val a = recorded(log, m)
        assertFalse(act.activateSos(a, confirmed = false))
        assertEquals(0, sos); assertNull(a.response); assertTrue(log.list().single().isNull("response"))
        assertTrue(act.activateSos(a, confirmed = true))
        assertEquals(1, sos); assertEquals("sos", log.list().single().getString("response"))
        assertTrue(m.open.isEmpty())                                                   // האירוע נסגר (שקט של פעולה)
    }

    @Test fun contacts_offline_is_an_error_not_a_fake_success() {
        var sent = 0
        val log = HazardLog(MemoryTextStore(), clock = { now })
        val m = manager()
        val act = HazardActions(m, log, connected = { false }, notifier = { countingNotifier { sent++ } }, startSos = {})
        val a = recorded(log, m)
        try { act.notifyContacts(a, "he", confirmed = true); fail() } catch (e: HazardNotConnected) {
            assertTrue(e.message!!.contains("לא מחובר"))
        }
        assertEquals(0, sent); assertTrue(log.list().single().isNull("response"))
        try { act.notifyContacts(a, "he", confirmed = false); fail() } catch (_: IllegalStateException) { }
        assertEquals(emptyMap<String, Boolean>(), act.deliveryStatus(mapOf("mom" to 5L)))
    }

    @Test fun contacts_success_and_delivery_status_against_server() {
        val server = MockWebServer(); server.start()
        try {
            val api = ApiClient(server.url("/").toString().trimEnd('/'), { "tok" })
            val log = HazardLog(MemoryTextStore(), clock = { now })
            val m = manager()
            val act = HazardActions(m, log, connected = { true }, notifier = {
                object : TrustedNotifier {
                    override fun notifyTrusted(text: String) = api.notifyTrusted(text)
                    override fun notificationStatus(ids: Map<String, Long>) = api.notificationStatus(ids)
                }
            }, startSos = {})
            val a = recorded(log, m)
            server.enqueue(MockResponse().setHeader("Content-Type", "application/json")
                .setBody("""{"sent_to":["mom","dad"],"notification_ids":{"mom":5,"dad":6}}"""))
            val res = act.notifyContacts(a, "he", confirmed = true)
            assertEquals(listOf("mom", "dad"), res.sentTo); assertEquals(mapOf("mom" to 5L, "dad" to 6L), res.notificationIds)
            val req = server.takeRequest()
            assertEquals("/v1/trusted/notify", req.path); assertEquals("Bearer tok", req.getHeader("Authorization"))
            val body = JSONObject(req.body.readUtf8())
            assertEquals("info", body.getString("type"))
            assertTrue(body.getString("text").contains("לא אישור לסכנה"))               // "זיהוי AI שלא אומת"
            assertFalse(body.getString("text").contains("http"))                          // בלי מיקום / קישור
            assertEquals("contacts", log.list().single().getString("response"))
            server.enqueue(MockResponse().setHeader("Content-Type", "application/json").setBody("""{"mom":true,"dad":false}"""))
            assertEquals(mapOf("mom" to true, "dad" to false), act.deliveryStatus(res.notificationIds))
            val st = server.takeRequest()
            assertEquals("/v1/notifications/status", st.path)
            assertEquals(5L, JSONObject(st.body.readUtf8()).getJSONObject("ids").getLong("mom"))
            // שגיאת שרת -> חריגה, התגובה לא נרשמת מחדש כ"נשלח"
            val b = recorded(log, m, "alarm")
            server.enqueue(MockResponse().setResponseCode(503).setBody("{}"))
            try { act.notifyContacts(b, "en", confirmed = true); fail() } catch (_: org.lifeguard.app.api.ApiException) { }
            assertTrue(log.list().first { it.getString("id") == b.id }.isNull("response"))
        } finally { server.shutdown() }
    }

    @Test fun call_only_after_confirmation_and_honest_when_no_dialer() {
        val log = HazardLog(MemoryTextStore(), clock = { now })
        val m = manager()
        val act = HazardActions(m, log, { true }, { countingNotifier {} }, startSos = {})
        val a = recorded(log, m)
        val dialed = ArrayList<String>()
        assertNull(act.callEmergency(a, confirmed = false, number = "100") { dialed += it; true })
        assertTrue(dialed.isEmpty()); assertNull(a.response)
        assertEquals(true, act.callEmergency(a, confirmed = true, number = "100") { dialed += it; true })
        assertEquals(listOf("100"), dialed); assertEquals("call", log.list().single().getString("response"))
        val b = recorded(log, m, "alarm")
        assertEquals(false, act.callEmergency(b, confirmed = true, number = "100") { throw RuntimeException("no dialer") })
        assertEquals("100", HazardActions.emergencyNumber())
    }

    // ---------------------------------------------------------------- משוב
    @Test fun false_alarm_is_user_reported_feedback_and_retention_limited() {
        val f = File(tmp.root, "hazard/alerts.bin")
        val log = HazardLog(SealedFileTextStore(f, AesGcmSealer { key }), clock = { now }, maxRecords = 3)
        val m = manager()
        val act = HazardActions(m, log, { true }, { countingNotifier {} }, startSos = {})
        val a = recorded(log, m, "alarm")
        act.respond(a, "false_alarm", "horn")
        val r = log.list().single()
        assertEquals("false_alarm", r.getString("response")); assertEquals("horn", r.getString("reason"))
        assertEquals("user_reported", r.getString("label_strength"))
        assertFalse(String(f.readBytes(), Charsets.ISO_8859_1).contains("false_alarm"))   // מוצפן
        val s = log.feedbackSummary().getValue("alarm")
        assertEquals(1, s.falseAlarm); assertEquals(mapOf("horn" to 1), s.reasons.toMap())
        try { act.respond(a, "false_alarm", "aliens"); fail() } catch (_: IllegalArgumentException) { }
        try { act.respond(a, "nuke"); fail() } catch (_: IllegalArgumentException) { }
        // דחייה בלי סיבה + עוד אירועים -> נשמרות רק 3 הרשומות האחרונות; ישנות מ-90 יום נמחקות
        val b = recorded(log, m, "gunshot"); act.respond(b, "dismiss")
        now += 300; recorded(log, m, "scream"); now += 300; recorded(log, m, "impact")
        assertEquals(3, log.list().size)
        now += HazardLog.RETENTION_DAYS * 86400 + 1
        assertTrue(log.list().isEmpty())
        // "אתחול": אותו קובץ, אותו מפתח
        val log2 = HazardLog(SealedFileTextStore(f, AesGcmSealer { key }), clock = { 0.0 + now - HazardLog.RETENTION_DAYS * 86400 - 1 })
        assertEquals(3, log2.list().size)
    }
}
