package org.lifeguard.app.core

import kotlin.math.asin
import kotlin.math.cos
import kotlin.math.roundToInt
import kotlin.math.sin
import kotlin.math.sqrt

object Geo {
    private const val R_M = 6371000.0

    fun distanceMeters(lat1: Double, lon1: Double, lat2: Double, lon2: Double): Double {
        val la1 = Math.toRadians(lat1); val la2 = Math.toRadians(lat2)
        val dLa = la2 - la1; val dLo = Math.toRadians(lon2 - lon1)
        val h = sin(dLa / 2) * sin(dLa / 2) + cos(la1) * cos(la2) * sin(dLo / 2) * sin(dLo / 2)
        return 2 * R_M * asin(sqrt(h.coerceIn(0.0, 1.0)))
    }

    fun formatDistanceHe(km: Double): String = when {
        km < 1.0 -> "${(km * 1000 / 10).roundToInt() * 10} מ'"
        km < 10 -> "%.1f ק\"מ".format(java.util.Locale.US, km)
        else -> "${km.roundToInt()} ק\"מ"
    }

    fun emergencyTypeHe(type: String?): String = when (type) {
        "medical" -> "מצב רפואי"
        "fire" -> "שריפה"
        "earthquake" -> "רעידת אדמה"
        "security" -> "אירוע ביטחוני"
        "physical_danger" -> "סכנה פיזית"
        "road_accident" -> "תאונת דרכים"
        "lost" -> "הלך/ה לאיבוד"
        "missing_person" -> "נעדר/ת"
        "flood_weather" -> "הצפה / מזג אוויר"
        "power_outage" -> "הפסקת חשמל"
        else -> "קריאת חירום"
    }

    fun roleHe(role: String?): String = when (role) {
        "owner" -> "במצוקה"
        "trusted" -> "איש/אשת קשר"
        "volunteer" -> "מתנדב/ת"
        "helper" -> "מתנדב/ת מרחוק"
        else -> ""
    }
}

/** מספרי טלפון ל-SMS ישיר. אותו כלל כמו normalize_phone בשרת: 05X... -> +9725X... */
object PhoneNumbers {
    fun normalize(raw: String?, countryCode: String = "972"): String? {
        var s = (raw ?: "").replace(Regex("[\\s\\-().]"), "")
        if (s.startsWith("00")) s = "+" + s.substring(2)
        val digits = when {
            s.startsWith("+") -> s.substring(1)
            s.startsWith("0") -> countryCode + s.substring(1)
            else -> s
        }
        if (digits.isEmpty() || !digits.all { it in '0'..'9' } || digits.length !in 8..15) return null
        return "+$digits"
    }

    /** טקסט חופשי (שורה/פסיק לכל מספר) -> רשימה תקינה ללא כפילויות, ורשימת השורות שנדחו. */
    fun parseList(text: String): Pair<List<String>, List<String>> {
        val ok = LinkedHashSet<String>(); val bad = ArrayList<String>()
        text.split('\n', ',', ';').map { it.trim() }.filter { it.isNotEmpty() }.forEach {
            val n = normalize(it)
            if (n != null) ok.add(n) else bad.add(it)
        }
        return ok.toList() to bad
    }
}
