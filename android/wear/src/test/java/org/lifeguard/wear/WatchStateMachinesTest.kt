package org.lifeguard.wear

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.lifeguard.app.core.MotionSignal
import org.lifeguard.app.wearlink.WearProtocol
import org.lifeguard.wear.logic.CancelWindow
import org.lifeguard.wear.logic.FallCountdown
import org.lifeguard.wear.logic.HoldToTrigger
import org.lifeguard.wear.logic.HoldToTrigger.Event

class HoldToTriggerTest {
    @Test fun full_hold_triggers_once_with_haptic_progress() {
        val h = HoldToTrigger(holdMs = 2000)
        h.press(1000)
        assertEquals(emptyList<Event>(), h.tick(1400))
        assertEquals(listOf(Event.Haptic(1)), h.tick(1500))
        assertEquals(0.5f, h.progress(2000), 0.001f)
        assertEquals(listOf(Event.Haptic(2), Event.Haptic(3)), h.tick(2600))
        assertEquals(listOf(Event.Triggered), h.tick(3000))
        assertTrue(h.triggered)
        assertEquals(emptyList<Event>(), h.tick(3500))          // פעם אחת בלבד
        assertEquals(emptyList<Event>(), h.release(3600))
        assertEquals(1f, h.progress(4000), 0f)
    }

    @Test fun early_release_does_nothing() {
        val h = HoldToTrigger(holdMs = 2000)
        h.press(0)
        h.tick(600)
        val ev = h.release(1999)
        assertEquals(Event.Released, ev.last())
        assertFalse(ev.contains(Event.Triggered))
        assertFalse(h.triggered)
        assertEquals(0f, h.progress(2500), 0f)
    }

    @Test fun release_exactly_at_threshold_triggers_even_without_frame_tick() {
        val h = HoldToTrigger(holdMs = 2000)
        h.press(0)
        val ev = h.release(2000)                                // אין frame בין הלחיצה לשחרור
        assertEquals(listOf(Event.Haptic(1), Event.Haptic(2), Event.Haptic(3), Event.Triggered), ev)
    }

    @Test fun repeated_taps_never_accumulate() {
        val h = HoldToTrigger(holdMs = 2000)
        var t = 0L
        repeat(10) { h.press(t); h.release(t + 900); t += 1000 }
        assertFalse(h.triggered)
    }

    @Test fun locked_after_trigger_until_reset() {
        val h = HoldToTrigger(holdMs = 2000)
        h.press(0); h.tick(2000)
        h.press(5000)
        assertEquals(emptyList<Event>(), h.tick(9000))
        h.reset()
        h.press(10_000)
        assertEquals(Event.Triggered, h.tick(12_000).last())
    }
}

class CancelWindowTest {
    @Test fun fires_once_after_window() {
        val w = CancelWindow(windowMs = 5000)
        w.start(100)
        assertEquals(5, w.secondsLeft(100))
        assertEquals(4, w.secondsLeft(1200))
        assertEquals(1, w.secondsLeft(5099))
        assertFalse(w.tick(5099))
        assertTrue(w.tick(5100))
        assertEquals(CancelWindow.State.FIRED, w.state)
        assertFalse(w.tick(6000))
    }

    @Test fun cancel_inside_window_prevents_send() {
        val w = CancelWindow(windowMs = 5000)
        w.start(0)
        assertTrue(w.cancel())
        assertFalse(w.tick(10_000))
        assertEquals(CancelWindow.State.CANCELLED, w.state)
    }

    @Test fun cancel_after_fire_is_not_pretended() {
        val w = CancelWindow(windowMs = 5000)
        w.start(0)
        assertTrue(w.tick(5000))
        assertFalse(w.cancel())
        assertEquals(CancelWindow.State.FIRED, w.state)
    }

    @Test fun second_ticks_for_haptics() {
        val w = CancelWindow(windowMs = 3000)
        w.start(0)
        assertFalse(w.secondChanged(10))
        assertTrue(w.secondChanged(1001))
        assertFalse(w.secondChanged(1500))
        assertTrue(w.secondChanged(2001))
    }

    @Test fun restart_after_cancel() {
        val w = CancelWindow(windowMs = 1000)
        w.start(0); w.cancel(); w.reset()
        w.start(5000)
        assertTrue(w.tick(6000))
    }
}

class FallCountdownTest {
    private val fall = MotionSignal("fall", 0.8, 1_700_000_000_000L, "נפילה")
    private val impact = MotionSignal("impact", 0.6, 1_700_000_000_100L, "מכה")

    @Test fun no_answer_escalates_after_30s_with_detector_confidence() {
        val f = FallCountdown(answerMs = 30_000)
        assertTrue(f.onFall(0, fall, listOf(impact)))
        assertEquals(30, f.secondsLeft(0))
        assertNull(f.tick(29_999))
        val e = f.tick(30_000)!!
        assertEquals(WearProtocol.WHY_NO_ANSWER, e.why)
        assertEquals(0.8, e.confidence, 0.0)
        assertEquals(listOf(fall, impact), e.signals)
        assertNull(f.asking)
        assertNull(f.tick(31_000))                               // פעם אחת
    }

    @Test fun im_ok_dismisses_and_quiets() {
        val f = FallCountdown(answerMs = 30_000, cooldownMs = 300_000)
        f.onFall(0, fall)
        assertTrue(f.answerOk(5_000))
        assertNull(f.tick(40_000))
        assertFalse(f.onFall(100_000, fall))                    // שקט אחרי "אני בסדר"
        assertTrue(f.onFall(305_001, fall))
    }

    @Test fun help_escalates_immediately() {
        val f = FallCountdown()
        f.onFall(0, fall)
        val e = f.answerHelp(3_000)!!
        assertEquals(WearProtocol.WHY_HELP_REQUESTED, e.why)
        assertNull(f.answerHelp(3_100))
        assertFalse(f.answerOk(3_200))
    }

    @Test fun second_fall_while_asking_joins_and_keeps_deadline() {
        val f = FallCountdown(answerMs = 30_000)
        f.onFall(0, fall.copy(confidence = 0.6))
        assertFalse(f.onFall(10_000, fall.copy(confidence = 0.9, atMs = fall.atMs + 10_000)))
        assertNull(f.tick(29_000))
        val e = f.tick(30_000)!!
        assertEquals(0.9, e.confidence, 0.0)                    // הגלאי החזק ביותר
        assertEquals(2, e.signals.size)
    }

    @Test fun answers_without_prompt_are_ignored() {
        val f = FallCountdown()
        assertFalse(f.answerOk(0))
        assertNull(f.answerHelp(0))
        assertNull(f.tick(1_000_000))
    }

    @Test fun no_reprompt_right_after_escalation() {
        val f = FallCountdown(answerMs = 1000, cooldownMs = 60_000)
        f.onFall(0, fall)
        assertNotNull(f.tick(1000))
        assertFalse(f.onFall(2000, fall))
    }
}
