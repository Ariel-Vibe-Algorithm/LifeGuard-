package org.lifeguard.app.core

import org.lifeguard.app.api.GuardianLink

/** טקסטים והחלטות תצוגה למסך הורה-ילד. השרת מחליט אם ההקלטה הדיסקרטית פעילה - כאן רק מסבירים. */
object GuardianText {
    private val USERNAME_RE = Regex("^[\\w\\u0590-\\u05FF.\\-]{3,32}$")

    data class Invite(val other: String, val iAm: String, val childAge: Int?)

    /** -> Invite תקין, או הודעת שגיאה בעברית */
    fun validateInvite(other: String, iAm: String, ageText: String, me: String?): Pair<Invite?, String?> {
        val u = other.trim().lowercase()
        if (!USERNAME_RE.matches(u)) return null to "שם משתמש לא תקין (3-32 תווים)"
        if (me != null && u == me.lowercase()) return null to "אי אפשר לקשר את עצמך"
        if (iAm != "guardian" && iAm != "child") return null to "יש לבחור תפקיד"
        val t = ageText.trim()
        val age = if (t.isEmpty()) null else t.toIntOrNull() ?: return null to "גיל לא תקין"
        if (age != null && age !in 0..17) return null to "פרופיל ילד/ה מיועד לגילאי 0-17"
        return Invite(u, iAm, age) to null
    }

    fun roleHe(l: GuardianLink) = if (l.iAmGuardian) "את/ה ההורה של ${l.childDisplay}" else "${l.guardianDisplay} הוא/היא ההורה שלך"

    fun canApprove(l: GuardianLink) = l.status == "pending" && !l.iApproved
    fun canConsent(l: GuardianLink) = l.isActive

    fun statusLines(l: GuardianLink): List<String> {
        val out = ArrayList<String>()
        out += when {
            l.isActive -> "הקישור פעיל (שני הצדדים אישרו)"
            !l.iApproved -> "ממתין לאישור שלך"
            else -> "ממתין לאישור של ${l.otherDisplay}"
        }
        l.childAge?.let { out += "גיל הילד/ה: $it" }
        if (!l.guardianVerified) out += "ההורה עוד לא אומת (אימות ידני ע\"י צוות LifeGuard) - עד אז אין הקלטה דיסקרטית"
        out += "הסכמה להקלטה: הורה - ${yesNo(l.consentGuardian)} · ילד/ה - ${yesNo(l.consentChild)}"
        out += if (l.discreetRecordingActive)
            "הקלטה דיסקרטית פעילה: בחירום מאושר הטלפון של ${l.childDisplay} מקליט בלי צורך בלחיצה. סמן המיקרופון של הטלפון עדיין מופיע."
        else "הקלטה דיסקרטית לא פעילה" + missing(l).let { if (it.isEmpty()) "" else " - חסר: " + it.joinToString(", ") }
        return out
    }

    fun missing(l: GuardianLink): List<String> = buildList {
        if (!l.isActive) add("אישור של שני הצדדים")
        if (!l.consentGuardian) add("הסכמת ההורה להקלטה")
        if (!l.consentChild) add("הסכמת הילד/ה להקלטה")
        if (!l.guardianVerified) add("אימות ההורה")
    }

    private fun yesNo(b: Boolean) = if (b) "כן" else "לא"

    /** טקסט ההסכמה להקלטה - מותאם גיל לילד/ה, ונפרד מאישור הקישור עצמו. */
    fun consentText(l: GuardianLink, retentionDays: Int = 30): String {
        if (l.iAmGuardian) return "הסכמה להקלטה דיסקרטית של ${l.childDisplay}:\n" +
            "• ההקלטה מתבצעת רק באירוע חירום מאושר של ${l.childDisplay} (SOS ידני או זיהוי שאושר) - לעולם לא סתם.\n" +
            "• אין האזנה חיה ואין שום דרך להפעיל את המיקרופון מרחוק.\n" +
            "• סמן המיקרופון של הטלפון מופיע תמיד בזמן הקלטה.\n" +
            "• כל השמעה של הקלטה נרשמת ביומן הפרטיות של ${l.childDisplay}. ההקלטות נמחקות אחרי $retentionDays יום.\n" +
            "• גם ${l.childDisplay} צריך/ה להסכים בנפרד, וכל אחד מכם יכול לבטל בכל רגע."
        val age = l.childAge
        val g = l.guardianDisplay
        return when {
            age != null && age <= 9 ->
                "אם יקרה משהו מפחיד ותבקש/י עזרה, הטלפון יקליט קולות כדי ש$g יוכל/תוכל לעזור לך.\n" +
                    "• זה קורה רק כשיש מצב חירום.\n" +
                    "• אף אחד לא יכול להדליק את המיקרופון מרחוק או להקשיב לך סתם.\n" +
                    "• כשהטלפון מקליט רואים נקודה קטנה בצבע על המסך.\n" +
                    "• מותר לך להגיד לא, ומותר לך לבטל מתי שתרצה/י."
            age != null && age <= 13 ->
                "אם תהיה במצב חירום (תלחץ/י SOS או שהטלפון יזהה משהו ותאשר/י), הטלפון יקליט שמע וישלח אותו ל$g.\n" +
                    "• רק בחירום - לא בזמן רגיל.\n" +
                    "• אין האזנה חיה, ואף אחד לא יכול להפעיל את המיקרופון מרחוק.\n" +
                    "• בזמן הקלטה מופיע סמן מיקרופון על המסך.\n" +
                    "• אפשר לראות ביומן הפרטיות מתי מישהו קיבל את ההקלטה. ההקלטות נמחקות אחרי $retentionDays יום.\n" +
                    "• זו החלטה שלך: אפשר לסרב, ואפשר לבטל בכל רגע מהמסך הזה."
            else ->
                "הסכמה להקלטה דיסקרטית בחירום עבור $g:\n" +
                    "• בחירום מאושר (SOS ידני, או זיהוי אוטומטי שאושר) הטלפון יקליט שמע גם בלי שתצטרך/י ללחוץ, ויעלה אותו מוצפן.\n" +
                    "• אין האזנה חיה ואין הפעלה מרחוק של המיקרופון - אין לזה שום אפשרות במערכת.\n" +
                    "• סמן המיקרופון של אנדרואיד מוצג תמיד בזמן הקלטה.\n" +
                    "• כל גישה להקלטה נרשמת ביומן הפרטיות שלך; ההקלטות נמחקות אחרי $retentionDays יום, ואת/ה יכול/ה למחוק קודם.\n" +
                    "• ההסכמה נפרדת מהקישור עצמו: אפשר לסרב או לבטל בכל רגע, בלי לבטל את הקישור."
        }
    }

    fun notificationText(event: String?, from: String?): String = when (event) {
        "invite" -> "${from ?: "מישהו"} מבקש/ת לקשר פרופיל הורה-ילד. פתחו את מסך \"הורה וילד\" כדי לאשר או לסרב."
        "approved" -> "קישור הורה-ילד אושר"
        "recording_consent" -> "עודכנה הסכמה להקלטה בקישור הורה-ילד"
        "revoked" -> "קישור הורה-ילד בוטל"
        else -> "עדכון בקישור הורה-ילד"
    }
}
