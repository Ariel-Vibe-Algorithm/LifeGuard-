package org.lifeguard.app.core.firstaid

import java.util.Locale
import java.util.TimeZone

/**
 * לוגיקת המסך "עזרה ראשונה" (טהורה, בלי Android) - פורט של ההתנהגות ב-first_aid_ui.py:
 * - מסך פתיחה: שורת חיפוש אחת והסבר קצר - **בלי רשימה/קטלוג של מצבים**. תוצאות רק אחרי שמקלידים.
 * - כמה התאמות קרובות -> "כמה מצבים מתאימים - בחרו את המתאים. (זו לא אבחנה)".
 * - חנק/החייאה בלי גיל ידוע -> כרטיס בחירת גיל **לפני** כל צעד. גיל שזוהה מהחיפוש נשמר ומוצג בבחירת הגיל.
 * - אין התאמה -> טקסט קבוע + הצעות קטנות ("חוסר הכרה" / "קושי בנשימה" / "דימום חמור").
 * - מדריך: הסעיפים בסדר קבוע ([GuidePage]); הטקסט הרפואי מוצג כלשונו.
 */
class FirstAidScreenModel(val lib: FirstAidLibrary) {
    /** גיל שנבחר במסך (adult / child / infant) או null = לא ידוע */
    @Volatile var age: String? = null
        private set
    @Volatile var query: String = ""
        private set
    /** מזהה המדריך הפתוח (null = לא בתצוגת מדריך) */
    @Volatile var current: String? = null
        private set
    @Volatile var lastResults: SearchResponse? = null
        private set
    @Volatile var view: FirstAidView = landing()
        private set

    private fun landing() = FirstAidView.Landing(LANDING_HINT, LANDING_HINT2,
        if (lib.guides.isEmpty()) LOAD_PROBLEM else null)

    private fun show(v: FirstAidView): FirstAidView { view = v; return v }

    /** טקסט חדש בשורת החיפוש (אחרי debounce) */
    fun onQuery(text: String): FirstAidView {
        query = text
        return runSearch()
    }

    fun runSearch(): FirstAidView {
        current = null
        val q = FirstAidText.pyStrip(query)
        if (q.isEmpty()) return show(landing())
        val res = lib.search(q, age)
        if (res.age != null && age == null) age = res.age      // הגיל זוהה מהחיפוש ("תינוק נחנק") - מוצג ונשמר
        lastResults = res
        return show(resultsView(res))
    }

    fun clear(): FirstAidView {
        query = ""
        current = null
        return show(landing())
    }

    /** כפתור הצעה (במסך "לא נמצא") - ממלא את השורה ומחפש */
    fun suggestion(text: String): FirstAidView = onQuery(text)

    /** שינוי גיל מהבחירה במסך: מדריך פתוח -> נפתח מחדש לפי הגיל (ייתכן מדריך אחר במשפחה); אחרת חיפוש מחדש */
    fun setAge(newAge: String?): FirstAidView {
        age = newAge?.takeIf { it in AGE_KEYS }
        current?.let { return openGuide(it) }
        return if (FirstAidText.pyStrip(query).isNotEmpty()) runSearch() else show(landing())
    }

    fun resultsView(res: SearchResponse): FirstAidView {
        val results = res.results
        if (results.isEmpty()) return FirstAidView.NoMatch(NO_MATCH, SUGGESTIONS)
        val items = ArrayList<ResultItem>()
        val shownFamilies = HashSet<String>()
        for (r in results) {
            val fam = lib.familyOf(r.id)
            if (fam != null && age == null) {
                if (shownFamilies.add(fam)) {
                    items += ResultItem.FamilyChooser(fam, FAMILY_TITLES[fam] ?: fam, FAMILY_QUESTION,
                        FirstAidLibrary.AGE_FAMILIES.getValue(fam).values.first(), FirstAidLibrary.AGE_CHOICES)
                }
                continue
            }
            items += ResultItem.Card(r)
        }
        val status = if (res.ambiguous) AMBIGUOUS else "${results.size} תוצאות"
        return FirstAidView.Results(status, res.ambiguous, items)
    }

    fun chooseAge(newAge: String, gid: String): FirstAidView {
        age = newAge.takeIf { it in AGE_KEYS }
        return openGuide(lib.resolve(gid, age))
    }

    fun openGuide(gid: String): FirstAidView {
        if (lib.guide(gid) == null) return view
        if (lib.needsAge(gid, age)) {
            current = null
            return show(FirstAidView.AgePrompt(gid, lib.guide(gid)!!.title, AGE_PROMPT, FirstAidLibrary.AGE_CHOICES))
        }
        val id = lib.resolve(gid, age)
        val g = lib.guide(id) ?: return view
        current = id
        val canGoBack = lastResults?.results?.isNotEmpty() == true && FirstAidText.pyStrip(query).isNotEmpty()
        return show(FirstAidView.GuideView(guidePage(g, age, canGoBack)))
    }

    /** חזרה (כפתור "חזרה לתוצאות" / Back). null = אין לאן לחזור במסך (לסגור אותו). */
    fun back(): FirstAidView? {
        val lr = lastResults
        val inGuide = view is FirstAidView.GuideView || view is FirstAidView.AgePrompt
        if (inGuide && lr != null && lr.results.isNotEmpty() && FirstAidText.pyStrip(query).isNotEmpty()) {
            current = null
            return show(resultsView(lr))
        }
        return null
    }

    fun guidePage(g: Guide, age: String?, canGoBack: Boolean = false): GuidePage {
        val nums = lib.emergencyNumbers
        val safety = g.isSafetyProcedure
        val sections = ArrayList<Section>()
        sections += Section("what_to_do", "מה עושים עכשיו", g.whatToDo, numbered = true)
        sections += Section("do_not", "מה אסור לעשות", g.doNot, danger = true)
        // חבילת הרחבה: "מתי חירום" הוא שדה emergency (מוצג גם בפס האדום) - כאן כסעיף קבוע
        sections += Section("red_flags", "מתי חייבים להזעיק עזרה", g.redFlags.ifEmpty { listOf(g.callEmergencyNow) })
        if (g.recognition.isNotEmpty()) sections += Section("recognition", "סימנים לזיהוי", g.recognition)
        if (g.importantNotes.isNotEmpty()) sections += Section("important_notes", "חשוב לדעת", g.importantNotes)
        val groups = g.ageGroups.joinToString(", ") { FirstAidLibrary.AGE_HE[it] ?: it }.ifEmpty { "לא צוין בחבילה" }
        val chosen = age?.let { a -> FirstAidLibrary.AGE_CHOICES.firstOrNull { it.first == a }?.second }
        val rel = g.overlaps?.let { lib.guide(it) }
        val poison = (g.category == POISON_CATEGORY || g.id in POISON_IDS)
        val origin = if (g.packageId != null) "חבילת הרחבה ${g.packageId} גרסה ${g.packageVersion}"
        else "מאגר בסיס גרסה ${lib.meta["schema_version"] ?: "?"}"
        return GuidePage(
            guide = g,
            urgencyHe = FirstAidLibrary.URGENCY_HE[g.urgency] ?: g.urgency,
            critical = g.urgency == "critical" || g.urgency == "high",
            emergencyBanner = g.callEmergencyNow,
            safetyLabel = if (safety) SAFETY_LABEL + (g.authorityReview?.let { " · נדרש אישור: $it" } ?: "") else null,
            officialNote = g.officialDynamic?.let { "המקור המחייב: ${it.authority}. ${it.text}" },
            ageLine = "מיועד ל: $groups" + (chosen?.let { "  ·  נבחר: $it" } ?: ""),
            ageNote = lib.ageNote(g.id, age),
            sections = sections,
            settingLine = if (g.setting.isNotEmpty()) "רלוונטי ל: " + g.setting.joinToString(" · ") else null,
            related = rel?.let { it.id to it.title },
            poisonCenter = if (poison) nums["poison_center"]?.let { "המכון הארצי למידע בהרעלות (רמב״ם): $it - 24/7. במצב מסכן חיים: 101." } else null,
            dial = dialButtons(safety),
            reviewStatus = "סטטוס: " + (REVIEW_HE[g.medicalReviewStatus] ?: g.medicalReviewStatus),
            metaLine = "עדכון תוכן: ${g.contentUpdated} · $origin · מזהה: ${g.id}",
            sources = lib.sourcesFor(g.id),
            canGoBack = canGoBack,
        )
    }

    /** כפתורי חיוג: 101 תמיד; משטרה/כבאות בנהלי בטיחות; מוקד הרעלות אם יש מספר */
    fun dialButtons(safety: Boolean): List<DialButton> {
        val nums = lib.emergencyNumbers
        val out = ArrayList<DialButton>()
        out += DialButton("mda", "מד״א ${lib.mda}", lib.mda)
        if (safety) {
            nums["police"]?.let { out += DialButton("police", "משטרה $it", it) }
            nums["fire_and_rescue"]?.let { out += DialButton("fire_and_rescue", "כבאות $it", it) }
        }
        nums["poison_center"]?.let { out += DialButton("poison_center", "הרעלות $it", it) }
        return out
    }

    companion object {
        const val PLACEHOLDER = "מה קרה? חפש מצב חירום או תסמין..."
        const val NO_MATCH = "לא מצאנו מדריך מתאים. במקרה של סכנת חיים התקשרו 101 או בקשו ממישהו להזעיק עזרה."
        val SUGGESTIONS = listOf("חוסר הכרה", "קושי בנשימה", "דימום חמור")
        const val AMBIGUOUS = "כמה מצבים מתאימים - בחרו את המתאים. (זו לא אבחנה)"
        const val LANDING_HINT = "כתבו בשורה למעלה מה קרה - למשל \"נחנק\", \"דימום\", \"לא נושם\", \"כווייה\"."
        const val LANDING_HINT2 = "אפשר לכתוב במילים שלכם, גם עם שגיאות כתיב. לא צריך אינטרנט ולא צריך להתחבר."
        const val LOAD_PROBLEM = "המדריכים לא נטענו במכשיר. במקרה של סכנת חיים התקשרו 101."
        const val FAMILY_QUESTION = "הטיפול שונה לפי גיל. מה גיל הנפגע/ת?"
        const val AGE_PROMPT = "לפני ההוראות: מה גיל הנפגע/ת? הטכניקה שונה בין מבוגר, ילד ותינוק."
        const val SAFETY_LABEL = "נוהל בטיחות - לא טיפול רפואי"
        const val SEARCH_DEBOUNCE_MS = 220L
        /** בחירת הגיל בשורת הצ'יפים (null = לא ידוע) */
        val AGE_CHIPS = listOf(null to "לא ידוע", "adult" to "מבוגר", "child" to "ילד מעל גיל שנה", "infant" to "תינוק מתחת לגיל שנה")
        val AGE_KEYS = setOf("adult", "child", "infant")
        val FAMILY_TITLES = mapOf("choking" to "חנק", "cpr" to "החייאה / לא נושם")
        const val POISON_CATEGORY = "חומרים והרעלות"
        val POISON_IDS = setOf("poison_ingestion", "chemical_exposure")
        val REVIEW_HE = mapOf(
            "REQUIRES_REVIEW_BY_ISRAELI_EMERGENCY_MEDICINE_CLINICIAN_BEFORE_PUBLIC_RELEASE" to
                "טיוטה - טעונה אישור רופא/ה לרפואה דחופה או פרמדיק/ית בישראל לפני פרסום לציבור",
            "pending_medical_review" to "ממתין לבדיקה של גורם רפואי/מקצועי מוסמך בישראל - אינו הנחיה רשמית",
        )
        /** הטלפון לא מתקשר לבד, ובלי קליטה אין הבטחה שהשיחה תצא - אומרים את זה במפורש */
        const val DIAL_NOTE = "החיוג נפתח באפליקציית הטלפון - LifeGuard לא מתקשר בעצמו. בלי קליטה סלולרית השיחה " +
            "עלולה לא לצאת - בקשו ממישהו להזעיק עזרה או חפשו מקום עם קליטה."
        const val OFFLINE_NOTE = "המדריכים שמורים במכשיר - עובדים גם בלי אינטרנט ובלי התחברות"
        const val LINK_NOTE = "(קישור - דורש אינטרנט)"

        /** המיקום האחרון שנשמר במכשיר (בלי פנייה לרשת), עם זמן ודיוק - כדי להקריא למוקד. */
        fun formatLastLocation(lat: Double, lon: Double, isPrecise: Boolean, accuracyM: Double?, capturedAtSec: Double?,
                               nowSec: Double, zone: TimeZone = TimeZone.getDefault()): String {
            val kind = if (isPrecise) "GPS" else "משוער (לא GPS)"
            val sb = StringBuilder("המיקום האחרון שנשמר במכשיר: ")
            sb.append(String.format(Locale.US, "%.5f, %.5f", lat, lon)).append(" · ").append(kind)
            if (accuracyM != null && accuracyM > 0) sb.append(" · דיוק ~${accuracyM.toInt()} מ'")
            if (capturedAtSec != null && capturedAtSec > 0) {
                val cal = java.util.Calendar.getInstance(zone).apply { timeInMillis = (capturedAtSec * 1000).toLong() }
                sb.append(String.format(Locale.US, " · נמדד ב-%02d:%02d", cal.get(java.util.Calendar.HOUR_OF_DAY),
                    cal.get(java.util.Calendar.MINUTE)))
                val mins = ((nowSec - capturedAtSec) / 60).toLong()
                if (mins >= 1) sb.append(if (mins < 120) " (לפני $mins דק')" else " (לפני ${mins / 60} שעות - ייתכן שאינו עדכני)")
            } else sb.append(" · זמן המדידה לא ידוע")
            return sb.toString()
        }
    }
}

sealed class FirstAidView {
    /** מסך פתיחה: בלי רשימת מצבים - רק הסבר קצר (והודעה אם התוכן לא נטען) */
    data class Landing(val hint: String, val hint2: String, val loadProblem: String?) : FirstAidView()
    data class Results(val status: String, val ambiguous: Boolean, val items: List<ResultItem>) : FirstAidView()
    data class NoMatch(val text: String, val suggestions: List<String>) : FirstAidView()
    data class AgePrompt(val guideId: String, val title: String, val question: String,
                         val choices: List<Pair<String, String>>) : FirstAidView()
    data class GuideView(val page: GuidePage) : FirstAidView()
}

sealed class ResultItem {
    data class Card(val result: SearchResult) : ResultItem()
    /** חנק / החייאה בלי גיל ידוע: בוחרים גיל לפני שרואים צעדים */
    data class FamilyChooser(val family: String, val title: String, val question: String, val anyId: String,
                             val choices: List<Pair<String, String>>) : ResultItem()
}

data class Section(val key: String, val title: String, val items: List<String>, val numbered: Boolean = false,
                   val danger: Boolean = false)

data class DialButton(val key: String, val label: String, val number: String)

/** מדריך מוכן לתצוגה - השדות לפי סדר ההצגה במסך */
data class GuidePage(
    val guide: Guide,
    val urgencyHe: String,
    val critical: Boolean,
    val emergencyBanner: String,
    val safetyLabel: String?,
    val officialNote: String?,
    val ageLine: String,
    val ageNote: String?,
    val sections: List<Section>,
    val settingLine: String?,
    val related: Pair<String, String>?,
    val poisonCenter: String?,
    val dial: List<DialButton>,
    val reviewStatus: String,
    val metaLine: String,
    val sources: List<Source>,
    val canGoBack: Boolean,
)
