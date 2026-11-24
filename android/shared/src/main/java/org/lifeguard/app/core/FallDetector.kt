package org.lifeguard.app.core

import kotlin.math.abs
import kotlin.math.sqrt

/**
 * זיהוי נפילה מחיישן תאוצה (בלי ML): נפילה חופשית (< ~0.4g) -> תוך שנייה מכה (> ~2.5g) ->
 * אחרי המכה כמעט אין תנועה (הטלפון/האדם שוכב). כל השלבים חייבים להתקיים:
 * - בלי נפילה חופשית לפני המכה = טריקת טלפון על שולחן / קפיצה -> לא נפילה
 * - תנועה רבה אחרי המכה = הטלפון נפל והמשתמש הרים אותו -> "impact" בביטחון נמוך (מתחת לסף)
 *
 * הזנה: onSample(tMillis, x, y, z) ביחידות m/s² (כמו SensorEvent). מחזיר אות ("fall"/"impact") וביטחון, או null.
 */
class FallDetector(
    private val freeFallG: Double = 0.4,
    private val impactG: Double = 2.5,
    private val minFreeFallMs: Long = 60,
    private val impactWindowMs: Long = 1000,
    private val settleDelayMs: Long = 500,
    private val stillWindowMs: Long = 2000,
    private val stillToleranceG: Double = 0.25,
) {
    /**
     * impactAtMs / peakG / freeFallMs / stillRatio: פרטי האירוע (ל-MotionFusion, שמצליב עם ג'ירוסקופ ותנוחה).
     */
    data class Detection(
        val signal: String,
        val confidence: Double,
        val impactAtMs: Long = 0,
        val peakG: Double = 0.0,
        val freeFallMs: Long = 0,
        val stillRatio: Double = 0.0,
    )

    private enum class State { IDLE, FREE_FALL, AWAIT_IMPACT, AWAIT_STILL }

    private var state = State.IDLE
    private var freeFallStart = 0L
    private var freeFallEnd = 0L
    private var impactAt = 0L
    private var peakG = 0.0
    private var stillSamples = 0
    private var movingSamples = 0

    fun reset() { state = State.IDLE; stillSamples = 0; movingSamples = 0; peakG = 0.0 }

    fun onSample(tMillis: Long, x: Float, y: Float, z: Float): Detection? {
        val g = sqrt((x * x + y * y + z * z).toDouble()) / GRAVITY
        when (state) {
            State.IDLE -> if (g < freeFallG) { state = State.FREE_FALL; freeFallStart = tMillis }
            State.FREE_FALL -> if (g >= freeFallG) {
                if (tMillis - freeFallStart >= minFreeFallMs) {
                    state = State.AWAIT_IMPACT; freeFallEnd = tMillis
                    if (g > impactG) startStill(tMillis, g)
                } else state = State.IDLE
            }
            State.AWAIT_IMPACT -> when {
                g > impactG -> startStill(tMillis, g)
                tMillis - freeFallEnd > impactWindowMs -> reset()
            }
            State.AWAIT_STILL -> {
                val sinceImpact = tMillis - impactAt
                if (sinceImpact < settleDelayMs) { peakG = maxOf(peakG, g); return null }
                if (abs(g - 1.0) <= stillToleranceG) stillSamples++ else movingSamples++
                if (sinceImpact >= settleDelayMs + stillWindowMs) {
                    val total = stillSamples + movingSamples
                    val stillRatio = if (total == 0) 0.0 else stillSamples.toDouble() / total
                    val peak = peakG; val at = impactAt; val ff = freeFallEnd - freeFallStart
                    reset()
                    return if (stillRatio >= 0.8) Detection("fall", if (peak > 3.5) 0.95 else 0.9, at, peak, ff, stillRatio)
                    else Detection("impact", 0.5, at, peak, ff, stillRatio)       // מתחת לסף 0.8 - לא שואלים
                }
            }
        }
        return null
    }

    private fun startStill(t: Long, g: Double) {
        state = State.AWAIT_STILL; impactAt = t; peakG = g; stillSamples = 0; movingSamples = 0
    }

    companion object { const val GRAVITY = 9.80665 }
}
