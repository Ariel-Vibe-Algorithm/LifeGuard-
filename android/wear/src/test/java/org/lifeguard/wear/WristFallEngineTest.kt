package org.lifeguard.wear

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.lifeguard.app.core.FallDetector
import org.lifeguard.app.core.MotionFusion
import org.lifeguard.app.core.MotionSignal
import org.lifeguard.wear.logic.SampleOrderer
import org.lifeguard.wear.logic.WristFallEngine
import kotlin.math.cos
import kotlin.math.sin
import kotlin.random.Random

/**
 * עקבות סינתטיות (50Hz, כמו MotionFusionTest בטלפון) - בודקות את החיווט והסדר, לא דיוק בשטח (לא נבדק על שעון אמיתי).
 * הזיהוי עצמו הוא אותו קוד של הטלפון (מודול :shared), לכן "שוויון" כאן = שה-WristFallEngine לא משנה את התוצאה.
 */
class WristFallEngineTest {
    private val t0 = 1_700_000_000_000L
    private val g = FallDetector.GRAVITY

    private data class S(val gyro: Boolean, val t: Long, val v: DoubleArray)

    private fun tilt(m: Double, deg: Double) = doubleArrayOf(0.0, m * cos(Math.toRadians(deg)), m * sin(Math.toRadians(deg)))

    /** אדם נופל: זרוע זקופה, נפילה חלקית 0.3g עם סיבוב, מכה, שוכב/ת (שינוי תנוחה 90°), שקט */
    private fun personFalls(start: Long, impactG: Double = 3.8): List<S> {
        val out = ArrayList<S>(); var t = start; val rnd = Random(1)
        fun seg(ms: Int, f: (Int) -> Pair<DoubleArray, DoubleArray>) {
            var i = 0
            while (i < ms) { val (a, gy) = f(i); out += S(true, t, gy); out += S(false, t, a); t += 20; i += 20 }
        }
        fun n(s: Double) = (rnd.nextDouble() * 2 - 1) * s
        seg(3000) { Pair(tilt(1.0, 0.0).map { it + n(0.03) }.toDoubleArray(), doubleArrayOf(0.1, 0.0, 0.0)) }
        seg(400) { i -> Pair(tilt(0.3, 90.0 * i / 400), doubleArrayOf(4.5, 0.0, 0.0)) }
        seg(60) { Pair(tilt(impactG, 90.0), doubleArrayOf(2.0, 0.0, 0.0)) }
        seg(3500) { Pair(tilt(1.0, 90.0).map { it + n(0.02) }.toDoubleArray(), doubleArrayOf(0.02, 0.0, 0.0)) }
        return out
    }

    /** הליכה עם נדנוד יד: מחזורי, בלי נפילה חופשית */
    private fun armSwing(start: Long, ms: Int): List<S> {
        val out = ArrayList<S>(); var t = start
        for (i in 0 until ms step 20) {
            val ph = 2 * Math.PI * i / 1000.0
            out += S(true, t, doubleArrayOf(1.5 * sin(ph), 0.0, 0.0))
            out += S(false, t, doubleArrayOf(0.3 * sin(ph), 1.0 + 0.6 * sin(2 * ph), 0.2))
            t += 20
        }
        return out
    }

    private fun feed(e: WristFallEngine, samples: List<S>): List<WristFallEngine.FallEvent> {
        val out = ArrayList<WristFallEngine.FallEvent>()
        for (s in samples) {
            val (x, y, z) = s.v.map { it.toFloat() }
            out += if (s.gyro) e.onGyro(s.t, x, y, z)
            else e.onAccel(s.t, (s.v[0] * g).toFloat(), (s.v[1] * g).toFloat(), (s.v[2] * g).toFloat())
        }
        // סוף העקבה: קצת שקט כדי לשחרר את מה שבמאגר
        var t = samples.last().t
        repeat(400) { t += 20; out += e.onGyro(t, 0f, 0f, 0f); out += e.onAccel(t, 0f, (g).toFloat(), 0f) }
        return out
    }

    private fun direct(samples: List<S>): List<MotionSignal> {
        val f = MotionFusion(gyroAvailable = true)
        val sig = ArrayList<MotionSignal>(); f.onSignal = { sig += it }
        for (s in samples) {
            if (s.gyro) f.onGyro(s.t, s.v[0].toFloat(), s.v[1].toFloat(), s.v[2].toFloat())
            else f.onAccel(s.t, (s.v[0] * g).toFloat(), (s.v[1] * g).toFloat(), (s.v[2] * g).toFloat())
        }
        return sig
    }

    @Test fun wrist_fall_produces_one_fall_event_same_as_phone_fusion() {
        val trace = personFalls(t0)
        val ev = feed(WristFallEngine(gyroAvailable = true), trace)
        assertEquals(1, ev.size)
        val phoneFall = direct(trace).first { it.source == "fall" }
        assertEquals(phoneFall, ev[0].fall)                    // שוויון לקוד הטלפון
        assertTrue(ev[0].fall.confidence >= 0.7)
        assertTrue(ev[0].fall.reason.contains("סיבוב"))
    }

    @Test fun batched_out_of_order_streams_still_see_the_rotation() {
        // אצוות של 4 שנ': קודם כל התאוצה של האצווה, אחר כך כל הג'ירו (כמו FIFO נפרדים בחומרה)
        val trace = personFalls(t0)
        val batched = trace.chunked(400).flatMap { chunk -> chunk.filter { !it.gyro } + chunk.filter { it.gyro } }
        val ev = feed(WristFallEngine(gyroAvailable = true), batched)
        assertEquals(1, ev.size)
        assertEquals(direct(trace).first { it.source == "fall" }, ev[0].fall)
    }

    @Test fun arm_swing_while_walking_does_not_prompt() {
        val ev = feed(WristFallEngine(gyroAvailable = true), armSwing(t0, 20_000))
        assertEquals(0, ev.size)
    }

    @Test fun without_gyro_fall_still_detected_by_posture_with_lower_confidence() {
        val trace = personFalls(t0).filter { !it.gyro }
        val ev = feed(WristFallEngine(gyroAvailable = false), trace)
        assertEquals(1, ev.size)
        assertTrue(ev[0].fall.confidence < 0.7)
        assertTrue(ev[0].fall.reason.contains("שינוי תנוחה"))
    }

    @Test fun orderer_releases_in_time_order_and_drops_too_late_samples() {
        val o = SampleOrderer(gyroExpected = true, maxLagMs = 1000)
        assertEquals(emptyList<SampleOrderer.Sample>(), o.push(SampleOrderer.Sample(false, 100, 0f, 0f, 0f)))
        assertEquals(emptyList<SampleOrderer.Sample>(), o.push(SampleOrderer.Sample(false, 120, 0f, 0f, 0f)))
        val r = o.push(SampleOrderer.Sample(true, 110, 0f, 0f, 0f))
        assertEquals(listOf(100L, 110L), r.map { it.t })
        // ג'ירו נתקע: אחרי maxLag התאוצה משתחררת בכל זאת
        val r2 = o.push(SampleOrderer.Sample(false, 1200, 0f, 0f, 0f))
        assertEquals(listOf(120L), r2.map { it.t })
        assertEquals(emptyList<SampleOrderer.Sample>(), o.push(SampleOrderer.Sample(true, 50, 0f, 0f, 0f)))
        // שוויון זמן: ג'ירו לפני תאוצה
        val o2 = SampleOrderer(gyroExpected = true)
        o2.push(SampleOrderer.Sample(false, 10, 0f, 0f, 0f))
        assertEquals(listOf(true, false), o2.push(SampleOrderer.Sample(true, 10, 0f, 0f, 0f)).map { it.gyro })
    }
}
