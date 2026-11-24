package org.lifeguard.app.core

import org.json.JSONArray
import org.json.JSONObject
import java.util.Locale

/**
 * אות שזוהה במכשיר. source: fall | impact | struggle | sudden_stop | no_signal (אותם ערכים כמו SIGNAL_SOURCES בשרת).
 * atMs = epoch במילישניות (שעון המכשיר).
 */
data class MotionSignal(val source: String, val confidence: Double, val atMs: Long, val reason: String)

enum class RiskLevel { LOW, MEDIUM, HIGH }

/**
 * הערכת סיכון לשליחה ב-POST /v1/incidents ("risk"). severity (level/score) וביטחון (confidence) הם צירים נפרדים:
 * score = כמה חמור המצב אם הזיהוי נכון (סקאלה 0..8 כמו SCORE_SCALE בשרת), confidence = כמה סביר שהזיהוי נכון (0..1).
 */
data class RiskAssessment(
    val level: RiskLevel,
    val score: Double,
    val confidence: Double,
    val reasons: List<String>,
    val signals: List<MotionSignal>,
) {
    val severity get() = level

    /** {"level","score","confidence","reasons":[...],"signals":[{"source","confidence","at"}]} */
    fun toJson(): JSONObject {
        val sig = JSONArray()
        for (s in signals.take(RiskScorer.MAX_SIGNALS)) {
            sig.put(JSONObject().put("source", s.source).put("confidence", round3(s.confidence)).put("at", s.atMs / 1000.0))
        }
        return JSONObject().put("level", level.name).put("score", round3(score)).put("confidence", round3(confidence))
            .put("reasons", JSONArray(reasons.take(RiskScorer.MAX_REASONS))).put("signals", sig)
    }

    /** טקסט קצר למסך "הכל בסדר?" */
    fun promptTextHe(): String {
        val names = signals.map { RiskScorer.SOURCE_HE[it.source] ?: it.source }.distinct()
        return "זוהו: " + names.joinToString(" + ")
    }

    private fun round3(d: Double) = String.format(Locale.US, "%.3f", d).toDouble()
}

object RiskScorer {
    const val MAX_SCORE = 8.0
    const val MAX_REASONS = 8
    const val MAX_SIGNALS = 8
    const val HIGH_FROM = 5.0
    const val MEDIUM_FROM = 2.5

    /** משקל חומרה לכל סוג אות (כפול הביטחון שלו) */
    val WEIGHTS = mapOf("fall" to 3.0, "impact" to 2.0, "struggle" to 3.0, "sudden_stop" to 2.0, "no_signal" to 1.5,
        "no_response" to 1.0)

    val SOURCE_HE = mapOf("fall" to "נפילה", "impact" to "מכה חזקה", "struggle" to "תנועה אלימה/מאבק",
        "sudden_stop" to "עצירה פתאומית", "no_signal" to "אין קליטה", "no_response" to "אין תגובה לשאלה")

    /** why: null | "no_answer" | "help_requested" (תוצאת "הכל בסדר?") */
    fun assess(signals: List<MotionSignal>, why: String? = null, extraReasons: List<String> = emptyList()): RiskAssessment {
        // לכל סוג אות - המופע בביטחון הגבוה ביותר
        val best = signals.groupBy { it.source }.mapValues { (_, v) -> v.maxBy { it.confidence } }
        var score = best.values.sumOf { (WEIGHTS[it.source] ?: 1.0) * it.confidence.coerceIn(0.0, 1.0) }
        val kinds = best.keys
        if (kinds.size >= 2) score += 1.0
        // צירופים שמחזקים זה את זה: תאונה (עצירה + מכה), תקיפה (מאבק + מכה / נפילה)
        if ("impact" in kinds && "sudden_stop" in kinds) score += 1.0
        if ("struggle" in kinds && ("impact" in kinds || "fall" in kinds)) score += 1.0
        val reasons = ArrayList<String>()
        best.values.sortedByDescending { it.confidence }.forEach { reasons += it.reason }
        reasons += extraReasons
        // ביטחון משולב (noisy-OR, מוגבל - האותות לא בלתי תלויים לגמרי)
        var confidence = 1.0 - best.values.fold(1.0) { acc, s -> acc * (1.0 - s.confidence.coerceIn(0.0, 1.0)) }
        confidence = confidence.coerceAtMost(0.95)
        when (why) {
            "no_answer" -> { score += 1.5; reasons += "לא נענתה השאלה \"הכל בסדר?\"" }
            "help_requested" -> {
                score += 2.0; reasons += "המשתמש/ת ביקש/ה עזרה"
                confidence = maxOf(confidence, 0.95)     // אישור אנושי לזיהוי
            }
        }
        score = score.coerceIn(0.0, MAX_SCORE)
        val level = when {
            score >= HIGH_FROM -> RiskLevel.HIGH
            score >= MEDIUM_FROM -> RiskLevel.MEDIUM
            else -> RiskLevel.LOW
        }
        return RiskAssessment(level, score, confidence, reasons.distinct().take(MAX_REASONS),
            signals.sortedBy { it.atMs })
    }
}
