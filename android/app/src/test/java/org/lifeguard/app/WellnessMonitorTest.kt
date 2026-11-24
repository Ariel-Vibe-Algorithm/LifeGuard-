package org.lifeguard.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.lifeguard.app.core.WellnessMonitor
import org.lifeguard.app.core.WellnessMonitor.Fix

/** אותם מקרים כמו tests/test_wellness_check.py (ללא הבדיקה שתלויה בממשק השולחני). */
class WellnessMonitorTest {
    private val LAT = 32.0853
    private val LON = 34.7818
    private val DEG_PER_M = 1 / 111320.0

    /** רצף מיקומים צפונה לפי מהירויות (מ'/שנ') לכל מקטע. */
    private fun track(speeds: List<Double>, dt: Double = 5.0, t0: Double = 0.0): List<Fix> {
        val fixes = arrayListOf(Fix(t0, LAT, LON)); var lat = LAT; var t = t0
        for (v in speeds) { t += dt; lat += v * dt * DEG_PER_M; fixes += Fix(t, lat, LON) }
        return fixes
    }
    private fun track(vararg s: Number) = track(s.map { it.toDouble() })

    @Test fun detect_abrupt_stop() {
        assertTrue(WellnessMonitor.detectAbruptStop(track(10, 10, 10, 0, 0, 0, 0, 0)))       // רכב -> עמידה 25 שנ'
        assertFalse(WellnessMonitor.detectAbruptStop(track(10, 10, 10, 0, 0)))               // עמידה קצרה מדי
        assertFalse(WellnessMonitor.detectAbruptStop(track(List(10) { 1.3 })))               // הליכה רגילה
        assertFalse(WellnessMonitor.detectAbruptStop(track(10, 10, 0, 0, 1.3, 0, 0, 0)))     // המשיך ללכת
        assertFalse(WellnessMonitor.detectAbruptStop(track(0, 0, 0, 0)))
    }

    private class Rec : WellnessMonitor.Listener {
        val asked = ArrayList<Pair<String, Double>>()
        val triggered = ArrayList<Pair<String, String>>()
        override fun onAsk(reason: String, reasonText: String, deadline: Double) { asked += reasonText to deadline }
        override fun onEscalate(reason: String, why: String) { triggered += reason to why }
    }

    private class Env(checkin: Double = 0.3, busy: String = "IDLE") {
        var now = 1000.0
        var state = busy
        val rec = Rec()
        val m = WellnessMonitor({ now }, { state !in setOf("IDLE", "FEEDBACK") }, rec, checkin)
    }

    @Test fun fall_asks_and_no_answer_triggers_sos() {
        val e = Env()
        e.m.onSensor("fall", 0.4)                       // ביטחון נמוך - לא שואלים
        assertTrue(e.rec.asked.isEmpty())
        e.m.onSensor("fall", 0.9)
        assertEquals("זוהתה נפילה", e.rec.asked[0].first)
        e.m.onSensor("impact", 0.95)                    // כבר יש שאלה פתוחה - לא שואלים שוב
        assertEquals(1, e.rec.asked.size)
        e.m.tick(); assertTrue(e.rec.triggered.isEmpty())   // עוד לא עבר הזמן
        e.now += 0.6
        e.m.tick()
        assertEquals(listOf("fall" to "no_answer"), e.rec.triggered)
    }

    @Test fun im_ok_cancels_and_quiets_further_prompts() {
        val e = Env(checkin = 60.0)
        e.m.onSensor("fall", 0.9)
        e.m.answer(true)                                // "אני בסדר"
        assertTrue(e.rec.triggered.isEmpty()); assertNull(e.m.pending)
        e.m.onSensor("fall", 0.9)
        assertEquals(1, e.rec.asked.size)               // שקט 5 דקות
        e.now += WellnessMonitor.COOLDOWN_SECONDS + 1
        e.m.onSensor("fall", 0.9)
        assertEquals(2, e.rec.asked.size)
        e.m.answer(false)                               // "אני צריך/ה עזרה"
        assertEquals(listOf("fall" to "help_requested"), e.rec.triggered)
        e.now += 100; e.m.tick()
        assertEquals(1, e.rec.triggered.size)           // לא מסלים פעמיים
    }

    @Test fun no_prompt_during_active_event_or_silent_tracking() {
        for (st in listOf("ACTIVE_SOS", "SILENT_TRACKING", "COUNTDOWN")) {
            val e = Env(busy = st)
            e.m.onSensor("fall", 0.99)
            assertTrue(st, e.rec.asked.isEmpty())
        }
    }

    @Test fun abrupt_stop_from_locations_and_signal_loss() {
        val e = Env(checkin = 60.0)
        for (f in track(10, 10, 10, 0, 0, 0, 0, 0)) e.m.onLocation(f.t, f.lat, f.lon)
        assertEquals("עצירה פתאומית", e.rec.asked[0].first)
        e.m.answer(true)

        val e2 = Env(checkin = 60.0); e2.now = 0.0
        e2.m.onConnectivity(false, watching = false)    // בלי ליווי פעיל - לא מדאיג
        e2.now += WellnessMonitor.SIGNAL_LOSS_SECONDS + 1
        e2.m.onConnectivity(false, watching = false)
        assertTrue(e2.rec.asked.isEmpty())
        e2.m.onConnectivity(false, watching = true)
        e2.now += WellnessMonitor.SIGNAL_LOSS_SECONDS - 10
        e2.m.onConnectivity(false, watching = true)
        assertTrue(e2.rec.asked.isEmpty())
        e2.now += 20
        e2.m.onConnectivity(false, watching = true)
        assertEquals("אין קליטה כבר כמה דקות", e2.rec.asked[0].first)
        e2.m.onConnectivity(true, watching = true)      // הקליטה חזרה - מאפס
        assertNull(e2.m.offlineSince)
    }
}
