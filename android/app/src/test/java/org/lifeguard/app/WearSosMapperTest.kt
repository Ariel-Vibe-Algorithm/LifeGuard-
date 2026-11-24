package org.lifeguard.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.lifeguard.app.core.ActivationPolicy
import org.lifeguard.app.core.Activations
import org.lifeguard.app.core.MotionSignal
import org.lifeguard.app.core.Triggers
import org.lifeguard.app.wear.WearSosMapper
import org.lifeguard.app.wear.WearSosMapper.Decision
import org.lifeguard.app.wearlink.WearProtocol
import org.lifeguard.app.wearlink.WearProtocol.AckStatus
import org.lifeguard.app.wearlink.WearProtocol.SosRequest

/** בקשה מהשעון -> SOS ידני מלא / טריגר אוטומטי לפי ספי השרת (WearSosMapper). */
class WearSosMapperTest {
    private val now = 1_700_000_100_000L
    private fun manual(id: String = "r1") = SosRequest(id, WearProtocol.KIND_MANUAL, now - 1000)
    private fun fall(why: String, conf: Double, signals: List<MotionSignal> = listOf(
        MotionSignal("fall", conf, now - 35_000, "נפילה: נפילה חופשית 300 מ\"ש, מכה 3.6g"))) =
        SosRequest("r2", WearProtocol.KIND_WATCH_FALL, now - 1000, why, conf, signals)

    @Test fun manual_from_watch_is_exactly_the_phone_button_full_workflow() {
        val d = WearSosMapper.decide(manual(), loggedIn = true, incidentActive = false, nowMs = now) as Decision.Start
        assertEquals("loud", d.mode)
        assertEquals(Triggers.MANUAL, d.trigger)
        assertNull("manual SOS carries no risk/confidence (never evaluated)", d.risk)
        assertEquals("watch:manual:r1", d.source)
        assertEquals(Activations.MANUAL, ActivationPolicy.expectedActivation(d.trigger, null))
    }

    @Test fun fall_without_answer_is_automatic_and_never_manual() {
        val d = WearSosMapper.decide(fall(WearProtocol.WHY_NO_ANSWER, 0.85), true, false, now) as Decision.Start
        assertEquals(Triggers.WELLNESS_NO_RESPONSE, d.trigger)
        assertTrue(Triggers.isAutomatic(d.trigger))
        assertEquals("watch:watch_fall:r2", d.source)
        val risk = d.risk!!
        // אותו חישוב כמו "הכל בסדר?" בטלפון: האותות + no_response
        assertEquals(setOf("fall", "no_response"), risk.signals.map { it.source }.toSet())
        assertTrue(risk.reasons.any { it.startsWith(WearSosMapper.WATCH_REASON_PREFIX) })
        val expected = ActivationPolicy.wellnessRisk(WearSosMapper.watchSignals(fall(WearProtocol.WHY_NO_ANSWER, 0.85)),
            WearProtocol.WHY_NO_ANSWER, now)
        assertEquals(expected.confidence, risk.confidence, 1e-9)
        assertEquals(Activations.CONFIRMED_HIGH, ActivationPolicy.expectedActivation(d.trigger, risk.confidence))
    }

    @Test fun server_thresholds_apply_to_watch_fall_confidence() {
        // ביטחון גלאי נמוך מאוד: fall 0.0 + no_response 0.5 = 0.5 -> unconfirmed (בלי מתנדבים עד אישור הבעלים)
        val mid = WearSosMapper.decide(fall(WearProtocol.WHY_NO_ANSWER, 0.0), true, false, now) as Decision.Start
        assertEquals(0.5, mid.risk!!.confidence, 1e-9)
        assertEquals(Activations.UNCONFIRMED, ActivationPolicy.expectedActivation(mid.trigger, mid.risk!!.confidence))
        // אף שלב לא הופך את זה לידני
        assertTrue(Triggers.isAutomatic(mid.trigger))
    }

    @Test fun help_requested_on_watch_maps_like_phone_wellness_help() {
        val d = WearSosMapper.decide(fall(WearProtocol.WHY_HELP_REQUESTED, 0.6), true, false, now) as Decision.Start
        assertEquals(Triggers.SENSOR, d.trigger)
        assertTrue(d.risk!!.confidence >= 0.95)          // אישור אנושי (RiskScorer) -> confirmed_high בשרת
        assertEquals(setOf("fall"), d.risk!!.signals.map { it.source }.toSet())
    }

    @Test fun unknown_signal_sources_are_dropped_and_fall_signal_always_present() {
        val req = fall(WearProtocol.WHY_NO_ANSWER, 0.7, listOf(
            MotionSignal("watch_fall", 0.9, now, "x"),        // השרת לא מכיר - היה נזרק בשקט
            MotionSignal("no_response", 1.0, now, "fake"),    // רק הטלפון מוסיף no_response
            MotionSignal("impact", 0.6, now, "")))
        val s = WearSosMapper.watchSignals(req)
        assertEquals(listOf("impact", "fall"), s.map { it.source })
        assertEquals(0.7, s.first { it.source == "fall" }.confidence, 0.0)
        assertTrue(s.all { it.reason.startsWith(WearSosMapper.WATCH_REASON_PREFIX) })
        assertEquals("שעון: מכה חזקה", s[0].reason)
    }

    @Test fun never_sends_watch_specific_trigger_to_server() {
        for (why in listOf(WearProtocol.WHY_NO_ANSWER, WearProtocol.WHY_HELP_REQUESTED)) {
            val d = WearSosMapper.decide(fall(why, 0.9), true, false, now) as Decision.Start
            assertTrue(d.trigger in Triggers.AUTOMATIC)
        }
    }

    @Test fun replies_without_starting_when_not_possible() {
        assertEquals(Decision.Reply(AckStatus.INVALID), WearSosMapper.decide(null, true, false, now))
        assertEquals(Decision.Reply(AckStatus.NOT_LOGGED_IN), WearSosMapper.decide(manual(), false, false, now))
        assertEquals(Decision.Reply(AckStatus.ALREADY_ACTIVE), WearSosMapper.decide(manual(), true, true, now))
        assertEquals(Decision.Reply(AckStatus.INVALID),
            WearSosMapper.decide(SosRequest("x", WearProtocol.KIND_WATCH_FALL, now, null, 0.8), true, false, now))
        assertEquals(Decision.Reply(AckStatus.INVALID),
            WearSosMapper.decide(SosRequest("x", "other", now), true, false, now))
    }

    @Test fun decoded_wire_request_maps_end_to_end() {
        val bytes = WearProtocol.encodeRequest(fall(WearProtocol.WHY_NO_ANSWER, 0.8))
        val d = WearSosMapper.decide(WearProtocol.decodeRequest(bytes), true, false, now) as Decision.Start
        assertEquals(Triggers.WELLNESS_NO_RESPONSE, d.trigger)
        // ה-risk שנשלח לשרת: אותות עם מקורות שהשרת מכיר בלבד
        val sources = d.risk!!.toJson().getJSONArray("signals").let { a -> (0 until a.length()).map { a.getJSONObject(it).getString("source") } }
        assertTrue(sources.all { it in setOf("fall", "impact", "struggle", "no_response") })
    }

    @Test fun request_log_dedupes_and_is_bounded() {
        val log = WearSosMapper.RequestLog(max = 2)
        log.put(WearProtocol.SosAck("a", AckStatus.QUEUED, "c1"))
        log.put(WearProtocol.SosAck("b", AckStatus.ALREADY_ACTIVE))
        assertEquals("c1", log.get("a")!!.clientId)
        log.put(WearProtocol.SosAck("c", AckStatus.QUEUED, "c3"))
        assertNull(log.get("a"))
        assertEquals(AckStatus.ALREADY_ACTIVE, log.get("b")!!.status)
    }
}
