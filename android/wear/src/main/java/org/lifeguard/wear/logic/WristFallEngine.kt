package org.lifeguard.wear.logic

import org.lifeguard.app.core.MotionFusion
import org.lifeguard.app.core.MotionSignal
import java.util.PriorityQueue

/**
 * זיהוי נפילה על פרק היד = **אותו קוד** כמו בטלפון (MotionFusion -> FallDetector, מודול :shared), עם שני הבדלים בשעון:
 *
 * 1. סדר דגימות: בשעון החיישנים נרשמים באצווה (maxReportLatency, חיסכון בסוללה), ואצוות התאוצה והג'ירוסקופ יכולות
 *    להגיע בסדר שונה. MotionFusion מניח סדר זמן (בודק את הג'ירו סביב המכה כשהתאוצה מסיימת) - לכן SampleOrderer
 *    מחזיק את הדגימות ומשחרר אותן לפי זמן, רק כשגם הזרם השני כבר עבר את אותו זמן (או אחרי maxLagMs).
 * 2. מתי שואלים "הכל בסדר?": בטלפון - רק ב-≥2 סוגי אותות (טלפון בכיס/תיק חווה הרבה מכות). בשעון - על אות fall
 *    של MotionFusion, שהוא כבר בעצמו צירוף של נפילה חופשית + מכה + (סיבוב או שינוי תנוחה) + שקט אחרי המכה,
 *    ועבר את סינון "המכשיר נפל לבד". אותות impact/struggle לבד לא שואלים (תנועות יד חזקות הן שגרה בשעון).
 *    שאלה היא לא SOS: SOS אוטומטי יוצא רק אחרי 30 שנ' בלי תשובה, והשרת מחליט לפי הביטחון (ספי ההפעלה).
 */
class WristFallEngine(gyroAvailable: Boolean, maxLagMs: Long = 6_000, private val windowMs: Long = 90_000) {
    data class FallEvent(val fall: MotionSignal, val related: List<MotionSignal>)

    val fusion = MotionFusion(gyroAvailable = gyroAvailable)
    private val orderer = SampleOrderer(gyroExpected = gyroAvailable, maxLagMs = maxLagMs)
    private val fresh = ArrayList<MotionSignal>()
    private val recent = ArrayList<MotionSignal>()

    init { fusion.onSignal = { fresh += it } }

    @Synchronized
    fun onAccel(t: Long, x: Float, y: Float, z: Float): List<FallEvent> = feed(orderer.push(SampleOrderer.Sample(false, t, x, y, z)))

    @Synchronized
    fun onGyro(t: Long, x: Float, y: Float, z: Float): List<FallEvent> = feed(orderer.push(SampleOrderer.Sample(true, t, x, y, z)))

    @Synchronized
    fun reset() { fusion.reset(); orderer.clear(); fresh.clear(); recent.clear() }

    private fun feed(samples: List<SampleOrderer.Sample>): List<FallEvent> {
        if (samples.isEmpty()) return emptyList()
        val out = ArrayList<FallEvent>()
        for (s in samples) {
            if (s.gyro) fusion.onGyro(s.t, s.x, s.y, s.z) else fusion.onAccel(s.t, s.x, s.y, s.z)
            if (fresh.isEmpty()) continue
            for (sig in fresh) {
                recent += sig
                recent.removeAll { sig.atMs - it.atMs > windowMs }
                if (sig.source == "fall") out += FallEvent(sig, recent.filter { it !== sig })
            }
            fresh.clear()
        }
        return out
    }
}

/**
 * מיזוג שני זרמי דגימות (תאוצה, ג'ירו) לסדר זמן. בלי ג'ירו - כל דגימה משוחררת מיד.
 * שחרור: כל מה שזמנו ≤ min(הזמן האחרון של תאוצה, של ג'ירו); אם זרם אחד "נתקע" - עד (הזמן המקסימלי - maxLagMs).
 * בשוויון זמן - ג'ירו לפני תאוצה (כמו בטלפון: MotionFusion רואה את הסיבוב לפני שהתאוצה מסיימת אירוע).
 */
class SampleOrderer(private val gyroExpected: Boolean, private val maxLagMs: Long = 6_000) {
    data class Sample(val gyro: Boolean, val t: Long, val x: Float, val y: Float, val z: Float)

    private val buf = PriorityQueue<Sample>(compareBy<Sample>({ it.t }, { if (it.gyro) 0 else 1 }))
    private var lastAccel = Long.MIN_VALUE
    private var lastGyro = Long.MIN_VALUE
    private var released = Long.MIN_VALUE

    fun push(s: Sample): List<Sample> {
        if (s.t < released) return emptyList()      // מאוחר מדי (כבר שוחררו דגימות אחריו) - נזרקת, לא משבשת את הסדר
        buf.add(s)
        if (s.gyro) lastGyro = maxOf(lastGyro, s.t) else lastAccel = maxOf(lastAccel, s.t)
        val newest = maxOf(lastAccel, lastGyro)
        val horizon = if (!gyroExpected) newest else maxOf(minOf(lastAccel, lastGyro), newest - maxLagMs)
        if (buf.isEmpty() || buf.peek()!!.t > horizon) return emptyList()
        val out = ArrayList<Sample>()
        while (buf.isNotEmpty() && buf.peek()!!.t <= horizon) out += buf.poll()!!
        released = out.last().t
        return out
    }

    fun clear() {
        buf.clear(); lastAccel = Long.MIN_VALUE; lastGyro = Long.MIN_VALUE; released = Long.MIN_VALUE
    }
}
