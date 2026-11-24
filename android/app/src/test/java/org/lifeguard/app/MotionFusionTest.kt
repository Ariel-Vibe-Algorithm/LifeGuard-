package org.lifeguard.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.lifeguard.app.core.FallDetector
import org.lifeguard.app.core.MotionFusion
import org.lifeguard.app.core.MotionSignal
import org.lifeguard.app.core.RiskLevel
import org.lifeguard.app.core.RiskScorer
import kotlin.math.PI
import kotlin.math.cos
import kotlin.math.sin
import kotlin.random.Random

/**
 * עקבות סינתטיות (50Hz) - לא הקלטות אמיתיות. בודקות את הלוגיקה, לא את הדיוק בשטח.
 */
class MotionFusionTest {
    private val G = FallDetector.GRAVITY
    private val t0 = 1_700_000_000_000L

    /** בונה עקבה: כל מקטע = משך + פונקציה (זמן בתוך המקטע) -> (תאוצה ב-g, ג'ירו ב-rad/s) */
    private class Trace(val fusion: MotionFusion, startMs: Long, val withGyro: Boolean = true) {
        var t = startMs
        val prompts = ArrayList<MotionFusion.Prompt>()
        val signals = ArrayList<MotionSignal>()
        init { fusion.onSignal = { signals += it } }

        fun seg(ms: Int, f: (Int) -> Pair<DoubleArray, DoubleArray>): Trace {
            var i = 0
            while (i < ms) {
                val (a, g) = f(i)
                if (withGyro) fusion.onGyro(t, g[0].toFloat(), g[1].toFloat(), g[2].toFloat())
                fusion.onAccel(t, (a[0] * 9.80665).toFloat(), (a[1] * 9.80665).toFloat(), (a[2] * 9.80665).toFloat())
                    ?.let { prompts += it }
                t += 20; i += 20
            }
            return this
        }

        fun const(ms: Int, a: DoubleArray, g: DoubleArray = v(0.0, 0.0, 0.0), noise: Double = 0.0, rnd: Random = Random(1)) =
            seg(ms) { Pair(doubleArrayOf(a[0] + n(rnd, noise), a[1] + n(rnd, noise), a[2] + n(rnd, noise)), g) }

        private fun n(r: Random, s: Double) = if (s == 0.0) 0.0 else (r.nextDouble() * 2 - 1) * s
    }

    companion object {
        fun v(x: Double, y: Double, z: Double) = doubleArrayOf(x, y, z)
        /** וקטור בגודל m בכיוון שבין y ל-z בזווית deg */
        fun tilt(m: Double, deg: Double) = v(0.0, m * cos(Math.toRadians(deg)), m * sin(Math.toRadians(deg)))
    }

    /** אדם נופל: הטלפון בכיס (זקוף), נפילה חלקית 0.3g עם סיבוב, מכה, שוכב/ת (שינוי תנוחה 90°), שקט. */
    private fun personFalls(tr: Trace, impactG: Double = 3.8): Trace = tr
        .const(3000, tilt(1.0, 0.0), v(0.1, 0.0, 0.0), noise = 0.03)
        .seg(400) { i -> Pair(tilt(0.3, 90.0 * i / 400), v(4.5, 0.0, 0.0)) }
        .const(60, tilt(impactG, 90.0), v(2.0, 0.0, 0.0))
        .const(3500, tilt(1.0, 90.0), v(0.02, 0.0, 0.0), noise = 0.02)

    @Test fun fall_with_hard_impact_prompts_with_reasons() {
        val tr = personFalls(Trace(MotionFusion(gyroAvailable = true), t0))
        assertEquals(setOf("fall", "impact"), tr.signals.map { it.source }.toSet())
        assertEquals(1, tr.prompts.size)
        val a = tr.prompts[0].assessment
        assertEquals(RiskLevel.HIGH, a.level)
        assertTrue(a.confidence > 0.9)
        val fall = tr.signals.first { it.source == "fall" }
        assertTrue(fall.reason, Regex("שינוי תנוחה (89|90|91)°").containsMatchIn(fall.reason))
        assertTrue(fall.reason.contains("סיבוב"))
        assertTrue(a.reasons.any { it.startsWith("נפילה") })
        val text = a.promptTextHe()
        assertTrue(text, text.startsWith("זוהו: ") && text.contains("נפילה") && text.contains("מכה חזקה"))
    }

    @Test fun soft_fall_alone_is_one_signal_and_does_not_prompt() {
        val tr = personFalls(Trace(MotionFusion(), t0), impactG = 2.7)
        assertEquals(listOf("fall"), tr.signals.map { it.source })
        assertTrue(tr.prompts.isEmpty())
    }

    @Test fun phone_dropped_on_table_is_not_a_fall_or_impact() {
        val tr = Trace(MotionFusion(), t0)
            .const(3000, tilt(1.0, 60.0), v(0.2, 0.1, 0.0), noise = 0.03)    // ביד, מוטה
            .const(450, v(0.0, 0.0, 0.02), v(1.5, 0.3, 0.0))                  // נפילה חופשית אמיתית של הטלפון
            .const(40, v(0.0, 0.0, 6.0))                                     // מכה בשולחן
            .const(100, v(0.0, 0.0, 1.0))
            .const(40, v(0.0, 0.0, 2.2))                                     // קפיצה
            .const(3500, v(0.0, 0.0, 1.0))                                   // מונח על השולחן
        assertTrue(tr.signals.toString(), tr.signals.isEmpty())
        assertTrue(tr.prompts.isEmpty())
        assertTrue(tr.fusion.rejected.contains("fall:device_drop"))
        assertTrue(tr.fusion.rejected.contains("impact:device_drop"))
    }

    @Test fun phone_tossed_on_bed_is_ignored() {
        val tr = Trace(MotionFusion(), t0)
            .const(2000, tilt(1.0, 60.0), noise = 0.03)
            .const(200, tilt(2.0, 60.0), v(1.0, 0.0, 0.0))                   // זריקה
            .const(500, v(0.02, 0.0, 0.03), v(7.0, 2.0, 0.0))                 // באוויר, מסתובב
            .const(150, v(1.2, 0.0, 1.0))                                    // נחיתה רכה (1.6g)
            .const(3500, v(0.7, 0.0, 0.7), noise = 0.01)
        assertTrue(tr.signals.toString(), tr.signals.isEmpty())
        assertTrue(tr.prompts.isEmpty())
    }

    @Test fun running_never_signals() {
        val tr = Trace(MotionFusion(), t0).const(3000, tilt(1.0, 0.0), noise = 0.02)
        // צעד כל 360 מ"ש: שלב ריחוף 0.1g (80 מ"ש), נחיתה 3.4g (40 מ"ש), שאר הזמן ~1.2g; ג'ירו מחזורי
        tr.seg(30_000) { i ->
            val p = i % 360
            val m = when { p < 80 -> 0.1; p < 120 -> 3.4; else -> 1.2 }
            Pair(tilt(m, 10.0), v(2.0 * sin(2 * PI * i / 360.0), 0.5, 0.0))
        }
        assertTrue(tr.signals.toString(), tr.signals.isEmpty())
        assertTrue(tr.prompts.isEmpty())
        assertTrue(tr.fusion.rejected.contains("impact:rhythmic"))
    }

    /** תנועה אלימה לא סדירה: ערכים אקראיים שמוחזקים 40-160 מ"ש, עד maxG, ג'ירו 2-6 rad/s */
    private fun struggle(tr: Trace, ms: Int, maxG: Double = 2.8, seed: Int = 7): Trace {
        val r = Random(seed)
        var hold = 0; var a = tilt(1.0, 0.0); var g = v(0.0, 0.0, 0.0)
        return tr.seg(ms) {
            if (hold <= 0) {
                hold = 40 + r.nextInt(0, 7) * 20
                // דו-אופני: תנועות חדות לסירוגין עם רגעי "ריחוף" - שונות גבוהה, בלי מחזוריות
                val m = if (r.nextBoolean()) 0.2 + r.nextDouble() * 0.6 else 1.8 + r.nextDouble() * (maxG - 1.8)
                val th = r.nextDouble() * PI; val ph = r.nextDouble() * 2 * PI
                a = v(m * sin(th) * cos(ph), m * sin(th) * sin(ph), m * cos(th))
                val w = 2.0 + r.nextDouble() * 4.0
                g = v(w * cos(ph), w * sin(ph), 0.5)
            }
            hold -= 20
            Pair(a, g)
        }
    }

    @Test fun struggle_alone_is_detected_but_does_not_prompt() {
        val tr = struggle(Trace(MotionFusion(), t0).const(2000, tilt(1.0, 0.0), noise = 0.02), 10_000)
        assertEquals(listOf("struggle"), tr.signals.map { it.source })
        assertTrue(tr.prompts.isEmpty())
        assertEquals(0.75, tr.signals[0].confidence, 1e-9)
    }

    @Test fun struggle_then_fall_prompts_high() {
        val tr = struggle(Trace(MotionFusion(), t0).const(2000, tilt(1.0, 0.0), noise = 0.02), 10_000)
        personFalls(tr)
        assertTrue(tr.prompts.isNotEmpty())
        val a = tr.prompts[0].assessment
        assertTrue(a.signals.map { it.source }.contains("struggle"))
        assertEquals(RiskLevel.HIGH, a.level)
    }

    @Test fun gyro_absent_device_lower_confidence_with_reason() {
        val with = personFalls(Trace(MotionFusion(gyroAvailable = true), t0))
        val without = personFalls(Trace(MotionFusion(gyroAvailable = false), t0, withGyro = false))
        val fw = with.signals.first { it.source == "fall" }
        val fo = without.signals.first { it.source == "fall" }
        assertTrue("${fo.confidence} < ${fw.confidence}", fo.confidence < fw.confidence)
        assertFalse(fo.reason.contains("סיבוב"))
        assertEquals(1, without.prompts.size)
        val a = without.prompts[0].assessment
        assertTrue(a.reasons.contains("אין ג'ירוסקופ במכשיר - ביטחון נמוך יותר"))
        assertTrue(a.confidence < with.prompts[0].assessment.confidence)

        // מאבק בלי ג'ירו: מזוהה רק לפי שונות תאוצה, בביטחון נמוך
        val s = struggle(Trace(MotionFusion(gyroAvailable = false), t0, withGyro = false)
            .const(2000, tilt(1.0, 0.0)), 10_000, maxG = 2.9, seed = 3)
        val st = s.signals.firstOrNull { it.source == "struggle" }
        assertNotNull(s.signals.toString(), st)
        assertEquals(0.45, st!!.confidence, 1e-9)
        assertTrue(st.reason.contains("בלי ג'ירוסקופ"))
    }

    @Test fun sudden_stop_alone_no_prompt_but_with_impact_prompts() {
        val f = MotionFusion()
        val lat = 32.0853; val dpm = 1 / 111320.0
        var la = lat
        var prompt: MotionFusion.Prompt? = null
        // נסיעה 10 מ'/שנ' ואז עמידה 25 שנ' (דגימה כל 5 שנ')
        val speeds = listOf(10.0, 10.0, 10.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        var ts = t0 / 1000.0
        f.onLocation(ts, la, 34.78)
        for (s in speeds) { ts += 5; la += s * 5 * dpm; prompt = f.onLocation(ts, la, 34.78) ?: prompt }
        assertNull(prompt)
        assertEquals(listOf("sudden_stop"), f.recentSignals((ts * 1000).toLong()).map { it.source })

        // תאונה: מכה חזקה בלי נפילה חופשית ואחריה שקט, תוך חלון הזמן -> שאלה
        val tr = Trace(f, (ts * 1000).toLong() + 1000)
            .const(3000, tilt(1.0, 0.0), noise = 0.02)
            .const(60, tilt(5.0, 30.0))
            .const(3000, tilt(1.0, 40.0), noise = 0.02)
        assertEquals(1, tr.prompts.size)
        val a = tr.prompts[0].assessment
        assertEquals(setOf("sudden_stop", "impact"), a.signals.map { it.source }.toSet())
        assertEquals(RiskLevel.HIGH, a.level)
    }

    @Test fun signals_outside_window_do_not_combine() {
        val f = MotionFusion()
        assertNull(f.addExternal(MotionSignal("struggle", 0.75, t0, "x")))
        assertNull(f.addExternal(MotionSignal("impact", 0.6, t0 + 91_000, "y")))
        assertNotNull(f.addExternal(MotionSignal("sudden_stop", 0.7, t0 + 100_000, "z")))
    }
}

class RiskScorerTest {
    private val at = 1_700_000_000_000L

    @Test fun payload_contract() {
        val a = RiskScorer.assess(listOf(MotionSignal("fall", 0.95, at, "נפילה"), MotionSignal("impact", 0.7, at + 10, "מכה")))
        val j = a.toJson()
        assertEquals("HIGH", j.getString("level"))
        val score = j.getDouble("score")
        assertTrue(score in 0.0..8.0)
        assertEquals(3.0 * 0.95 + 2.0 * 0.7 + 1.0, score, 1e-6)
        assertEquals(0.95, j.getDouble("confidence"), 1e-9)       // 1-(0.05*0.3) -> מוגבל ל-0.95
        assertEquals(listOf("נפילה", "מכה"), (0 until j.getJSONArray("reasons").length()).map { j.getJSONArray("reasons").getString(it) })
        val s0 = j.getJSONArray("signals").getJSONObject(0)
        assertEquals("fall", s0.getString("source")); assertEquals(0.95, s0.getDouble("confidence"), 1e-9)
        assertEquals(1_700_000_000.0, s0.getDouble("at"), 1e-6)   // epoch בשניות
        assertEquals(setOf("source", "confidence", "at"), s0.keys().asSequence().toSet())
        assertEquals(setOf("level", "score", "confidence", "reasons", "signals"), j.keys().asSequence().toSet())
    }

    @Test fun levels_and_escalation_reasons() {
        val single = RiskScorer.assess(listOf(MotionSignal("no_signal", 1.0, at, "אין קליטה")))
        assertEquals(RiskLevel.LOW, single.level); assertEquals(1.5, single.score, 1e-9)
        val noAnswer = RiskScorer.assess(listOf(MotionSignal("no_signal", 1.0, at, "אין קליטה")), why = "no_answer")
        assertEquals(RiskLevel.MEDIUM, noAnswer.level)
        assertTrue(noAnswer.reasons.contains("לא נענתה השאלה \"הכל בסדר?\""))
        val help = RiskScorer.assess(listOf(MotionSignal("impact", 0.5, at, "מכה")), why = "help_requested")
        assertEquals(0.95, help.confidence, 1e-9)
        assertTrue(help.reasons.contains("המשתמש/ת ביקש/ה עזרה"))
        // אין אותות בכלל (שאלה ישנה) - עדיין הערכה תקינה
        val none = RiskScorer.assess(emptyList(), why = "no_answer")
        assertEquals(0.0, none.confidence, 1e-9); assertEquals(RiskLevel.LOW, none.level)
        // תקרה
        val many = RiskScorer.assess(listOf("fall", "impact", "struggle", "sudden_stop", "no_signal").map {
            MotionSignal(it, 1.0, at, it) }, why = "help_requested")
        assertEquals(8.0, many.score, 1e-9)
    }
}
