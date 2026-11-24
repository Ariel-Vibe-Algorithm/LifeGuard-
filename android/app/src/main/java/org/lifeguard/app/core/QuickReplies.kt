package org.lifeguard.app.core

/**
 * הודעות מהירות (לחיצה אחת). הקוד נשלח לשרת והשרת קובע את הטקסט; הטקסטים כאן זהים לשרת
 * (server/lifeguard_server.py QUICK_OWNER / QUICK_RESPONDER) ומשמשים לתצוגה כשהשרת לא שלח quick_replies.
 */
object QuickReplies {
    val OWNER: Map<String, String> = linkedMapOf(
        "ok" to "אני בסדר עכשיו",
        "cant_talk" to "אני לא יכול/ה לדבר - רק הודעות",
        "call_police" to "תתקשרו למשטרה (100)",
        "hiding" to "אני מסתתר/ת",
        "hurry" to "בואו מהר",
        "injured" to "אני פצוע/ה",
    )
    val RESPONDER: Map<String, String> = linkedMapOf(
        "on_my_way" to "אני בדרך אליך",
        "near_2min" to "אני במרחק כ-2 דקות",
        "called_police" to "התקשרתי למשטרה",
        "stay_hidden" to "תישאר/י במקום מוסתר",
        "cant_come" to "אני לא יכול/ה להגיע, מעדכן/ת אחרים",
    )

    /** תווית קצרה לשבב (chip) */
    private val SHORT: Map<String, String> = mapOf(
        "ok" to "אני בסדר", "cant_talk" to "לא יכול/ה לדבר", "call_police" to "משטרה!", "hiding" to "מסתתר/ת",
        "hurry" to "בואו מהר", "injured" to "פצוע/ה",
        "on_my_way" to "בדרך", "near_2min" to "עוד 2 דק'", "called_police" to "התקשרתי למשטרה",
        "stay_hidden" to "הישאר/י מוסתר/ת", "cant_come" to "לא יכול/ה להגיע",
    )

    fun forRole(role: String?): Map<String, String> = if (role == "owner") OWNER else RESPONDER

    /** מעדיפים את הרשימה מהשרת (מקור האמת); אם לא נשלחה - הרשימה המקומית לפי תפקיד. */
    fun resolve(serverMap: Map<String, String>?, role: String?): Map<String, String> =
        if (!serverMap.isNullOrEmpty()) serverMap else forRole(role)

    fun isValid(code: String, role: String?): Boolean = code in forRole(role)

    fun fullText(code: String): String? = OWNER[code] ?: RESPONDER[code]

    fun chipLabel(code: String, serverText: String? = null): String = SHORT[code] ?: serverText ?: code

    /** פעולת respond שמתאימה להודעה מהירה של מגיב (כדי שהסטטוס בשרת יתעדכן גם הוא). */
    fun respondActionFor(code: String): String? = when (code) {
        "on_my_way", "near_2min" -> "on_my_way"
        else -> null
    }
}
