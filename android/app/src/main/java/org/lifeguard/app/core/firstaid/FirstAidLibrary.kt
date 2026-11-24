package org.lifeguard.app.core.firstaid

import org.lifeguard.app.core.firstaid.FirstAidText.cpLen
import org.lifeguard.app.core.firstaid.FirstAidText.isPyDigits
import org.lifeguard.app.core.firstaid.FirstAidText.normalize
import org.lifeguard.app.core.firstaid.FirstAidText.pySplit
import org.lifeguard.app.core.firstaid.FirstAidText.pyStrip
import org.lifeguard.app.core.firstaid.FirstAidText.stems
import java.math.BigDecimal
import java.math.RoundingMode

/**
 * מאגר עזרה ראשונה אופליין - פורט של first_aid/library.py (טעינה, אימות, חיפוש, גיל).
 *
 * - **אופליין בלבד**: אין כאן שום ייבוא רשת (נבדק ב-FirstAidScreenTest). התוכן נטען מ-[ContentSource] מקומי.
 * - **התוכן לא נוצר ולא משתנה**: הטקסטים מוצגים כלשונם. אין AI בזמן ריצה.
 * - **אימות**: מדריך פגום מדולג ומדווח ב-[errors]; קובץ פגום לגמרי -> מאגר ריק + שגיאה (101 תמיד זמין). אף פעם לא זורק.
 * - **גיל**: בחנק/החייאה, כשהגיל לא ידוע, המסך שואל לפני הצעדים ([needsAge]).
 */
data class OfficialDynamic(val authority: String, val text: String)

data class Guide(
    val id: String,
    val title: String,
    val category: String,
    val urgency: String,
    val summary: String,
    val searchTerms: List<String>,
    val ageGroups: List<String>,
    val callEmergencyNow: String,
    val whatToDo: List<String>,
    val doNot: List<String>,
    val redFlags: List<String>,
    val importantNotes: List<String>,
    val recognition: List<String>,
    val sourceIds: List<String>,
    val medicalReviewStatus: String,
    val contentUpdated: String,
    val setting: List<String> = emptyList(),
    /** null במאגר הבסיס; "medical" / "safety" בחבילת הרחבה */
    val kind: String? = null,
    val packageId: String? = null,
    val packageVersion: String? = null,
    val officialDynamic: OfficialDynamic? = null,
    val authorityReview: String? = null,
    val overlaps: String? = null,
) {
    val isSafetyProcedure get() = kind == "safety"
}

data class Source(val id: String, val title: String, val url: String?)

data class PackageInfo(val packageId: String, val version: String, val added: Int, val total: Int, val updated: String?)

data class Conflict(val id: String, val packageId: String, val version: String, val reason: String)

data class SearchResult(
    val id: String, val title: String, val urgency: String, val urgencyHe: String, val summary: String,
    /** הציון מעוגל ל-2 ספרות (round(score, 2) של Python - משמש גם לקביעת ambiguous) */
    val score: Double,
    val ageNote: String?,
)

data class SearchResponse(val results: List<SearchResult>, val ambiguous: Boolean, val age: String?, val queryNorm: String)

class FirstAidLibrary(
    val guides: List<Guide>,
    val meta: Map<String, Any?> = emptyMap(),
    val sources: Map<String, Source> = emptyMap(),
    synonyms: Map<String, List<String>> = emptyMap(),
    errors: List<String> = emptyList(),
    val packages: List<PackageInfo> = emptyList(),
    val conflicts: List<Conflict> = emptyList(),
) {
    val errors: List<String> = errors.toList()
    val byId: Map<String, Guide> = LinkedHashMap<String, Guide>().apply { for (g in guides) put(g.id, g) }

    /** ביטוי מנורמל -> מונחים מנורמלים (סדר ההכנסה נשמר - קובע את סדר הסכימה כמו ב-Python) */
    val synonyms: Map<String, List<String>> = LinkedHashMap<String, List<String>>().apply {
        for ((k, v) in synonyms) put(normalize(k), v.map { normalize(it) })
    }

    private class Stem(val s: String, val cps: IntArray) { val len = cps.size }
    private class FieldIndex(val keys: Set<String>, val list: List<Stem>)

    private val index: Map<String, List<FieldIndex>> = LinkedHashMap<String, List<FieldIndex>>().apply {
        for (g in guides) put(g.id, indexGuide(g))
    }
    private val phrases: Map<String, List<String>> = guides.associate { g -> g.id to (listOf(g.title) + g.searchTerms).map { normalize(it) } }

    private fun fieldTexts(g: Guide): List<List<String>> {
        val body = g.whatToDo + g.doNot + g.redFlags + g.importantNotes + g.recognition + listOf(g.callEmergencyNow)
        // אותו סדר כמו FIELD_WEIGHTS: title, search_terms, summary, category, setting, body
        return listOf(listOf(g.title), g.searchTerms, listOf(g.summary), listOf(g.category), g.setting, body)
    }

    private fun indexGuide(g: Guide): List<FieldIndex> = fieldTexts(g).map { texts ->
        val keys = LinkedHashSet<String>()
        for (text in texts) for (t in FirstAidText.tokens(text)) keys += stems(t)
        FieldIndex(keys, keys.map { Stem(it, FirstAidText.codePoints(it)) })
    }

    /** ביטויים מדוברים / ערבית / אנגלית -> מונחי חיפוש נוספים (במשקל מופחת). */
    private fun expand(q: String): List<String> {
        val extra = ArrayList<String>()
        val padded = " $q "
        for ((phrase, terms) in synonyms) if (padded.contains(" $phrase ")) extra += terms
        return extra
    }

    /** איכות התאמה 0..1 של מילת חיפוש מול מילים בשדה. */
    private fun tokenMatch(qs: List<Stem>, field: FieldIndex): Double {
        if (qs.any { it.s in field.keys }) return 1.0
        var best = 0.0
        for (s in field.list) {
            for (q in qs) {
                // התאמת תחילית רק ממילה של 4 אותיות ומעלה: "ירי" לא יתאים ל"ירידה", "חבל" לא ל"חבלה"
                if (q.len >= 4 && (s.s.startsWith(q.s) || q.s.startsWith(s.s)) && minOf(s.len, q.len) >= 4) {
                    best = maxOf(best, 0.8)
                } else if (q.len >= 4 && s.len >= 4) {
                    val d = FirstAidText.editDistance(q.cps, s.cps, 2)
                    if (d <= (if (q.len < 7) 1 else 2)) best = maxOf(best, 0.65)
                }
            }
        }
        return best
    }

    /**
     * חיפוש. תוצאות רק לשאילתה לא ריקה. [age] = "adult" / "child" / "infant" או null (מזוהה מהטקסט אם אפשר).
     */
    fun search(query: String?, age: String? = null, limit: Int = 8): SearchResponse {
        val qn = normalize(query)
        var qTokens = pySplit(qn).filter { cpLen(it) >= 2 }
        if (qTokens.isEmpty()) return SearchResponse(emptyList(), false, age, qn)
        val useAge = age ?: FirstAidText.detectAge(query)
        // מילות גיל ("תינוק", "ילד בן 3") הן מסננת, לא נושא: לא נספרות בניקוד
        val topic = qTokens.filter { t ->
            stems(t).none { it in FirstAidText.AGE_WORDS } && !isPyDigits(t) && t !in AGE_FILLER
        }
        if (topic.isNotEmpty()) qTokens = topic
        val extra = expand(qn).flatMap { pySplit(it) }
        val stemCache = HashMap<String, List<Stem>>()
        fun qStems(t: String) = stemCache.getOrPut(t) { stems(t).map { Stem(it, FirstAidText.codePoints(it)) } }

        class Scored(val score: Double, val urg: Int, val id: String)
        var scored = ArrayList<Scored>()
        for (g in guides) {
            val idx = index.getValue(g.id)
            var total = 0.0
            var strong = false
            for ((weightMul, toks) in listOf(1.0 to qTokens, 0.8 to extra)) {
                for (qt in toks) {
                    val qs = qStems(qt)
                    var best = 0.0
                    for ((fi, fw) in FIELD_WEIGHTS.withIndex()) {
                        val m = tokenMatch(qs, idx[fi])
                        if (m != 0.0) {
                            best = maxOf(best, fw.second * m)
                            if (fw.first != "body" && m >= 0.65) strong = true
                        }
                    }
                    total += best * weightMul
                }
            }
            if (qTokens.size >= 2 && phrases.getValue(g.id).any { p -> cpLen(p) >= 4 && (p.contains(qn) || qn.contains(p)) }) {
                total += 6.0
                strong = true
            }
            if (!strong) continue          // התאמה רק בגוף הטקסט ("אל"/"מים") אינה תוצאה
            var score = total / maxOf(1, qTokens.size) * (1 + URGENCY_BOOST.getValue(g.urgency))
            if (useAge != null && "adult" !in g.ageGroups && (AGE_MATCH[useAge].orEmpty() intersect g.ageGroups.toSet()).isNotEmpty()) {
                score *= AGE_SPECIFIC_BOOST     // מדריך שנכתב במיוחד לגיל הזה
            }
            scored += Scored(score, URGENCIES.indexOf(g.urgency), g.id)
        }
        scored = ArrayList(scored.sortedWith(compareBy<Scored>({ -it.score }, { it.urg })))
        if (scored.isNotEmpty()) {   // התאמות חלשות (פחות מ-35% מהמובילה) לא מוצגות
            val top = scored[0].score
            scored = ArrayList(scored.filter { it.score >= MIN_RELATIVE_SCORE * top })
        }
        var results = scored.take(limit).map { s ->
            val g = byId.getValue(s.id)
            SearchResult(s.id, g.title, g.urgency, URGENCY_HE.getValue(g.urgency), g.summary, pyRound2(s.score), ageNote(s.id, useAge))
        }
        if (useAge != null) {   // בתוך משפחה תלוית-גיל: המדריך של הגיל שנבחר קודם, והאחר לא מוצג
            val keep = HashSet<String>()
            for (fam in AGE_FAMILIES.values) {
                if (results.any { it.id in fam.values }) fam[useAge]?.let { keep += it }
            }
            val famIds = AGE_FAMILIES.values.flatMap { it.values }.toSet()
            results = results.filter { it.id !in famIds || it.id in keep }
        }
        val ambiguous = results.size >= 2 && results[1].score >= 0.8 * results[0].score
        return SearchResponse(results, ambiguous, useAge, qn)
    }

    // ---------- גיל
    fun familyOf(gid: String): String? = AGE_FAMILIES.entries.firstOrNull { gid in it.value.values }?.key

    /** האם לשאול גיל לפני הצגת הצעדים (משפחה שבה הטכניקה שונה לפי גיל, והגיל לא ידוע). */
    fun needsAge(gid: String, age: String?): Boolean = age == null && (familyOf(gid) != null || gid in MIXED_AGE_NOTE)

    /** מדריך + גיל -> המדריך הנכון לגיל (למשל חנק + תינוק -> choking_infant). */
    fun resolve(gid: String, age: String?): String {
        val fam = familyOf(gid) ?: return gid
        return AGE_FAMILIES.getValue(fam)[age] ?: gid
    }

    fun ageNote(gid: String, age: String?): String? {
        val g = byId[gid] ?: return null
        if (age.isNullOrEmpty()) return null
        MIXED_AGE_NOTE[gid]?.get(age)?.let { return it }
        if (g.ageGroups.isEmpty()) return null          // חבילת הרחבה בלי קבוצת גיל - לא מנחשים ("לא צוין")
        if ((AGE_MATCH[age].orEmpty() intersect g.ageGroups.toSet()).isEmpty()) {
            val choice = AGE_CHOICES.firstOrNull { it.first == age }?.second ?: age
            return "המדריך נכתב עבור: ${g.ageGroups.joinToString(", ") { AGE_HE[it] ?: it }}. " +
                "ל$choice - הזעיקו מד״א 101 ופעלו לפי הנחיות המוקד."
        }
        return null
    }

    // ---------- תצוגה
    fun guide(gid: String): Guide? = byId[gid]

    fun sourcesFor(gid: String): List<Source> =
        byId[gid]?.sourceIds?.map { sources[it] ?: Source(it, it, null) }.orEmpty()

    /** מספרי החירום מהתוכן; תמיד יש לפחות mda=101 */
    val emergencyNumbers: Map<String, String> by lazy {
        val m = LinkedHashMap<String, String>()
        (meta["emergency_numbers"] as? Map<*, *>)?.forEach { (k, v) -> if (k is String && v != null) m[k] = v.toString() }
        if (m.isEmpty()) mapOf("mda" to "101") else m
    }

    val mda: String get() = emergencyNumbers["mda"] ?: "101"

    val disclaimer: String
        get() = (meta["disclaimer"] as? String)?.takeIf { it.isNotEmpty() }
            ?: "מידע עזרה ראשונה כללי. אינו תחליף ל-101, לטיפול מקצועי או להכשרה מעשית."

    companion object {
        const val GUIDES_PATH = "content/first_aid_guides_he.json"
        const val SOURCES_PATH = "content/sources.json"
        const val SYNONYMS_PATH = "synonyms.json"

        val URGENCIES = listOf("critical", "high", "medium", "low")
        val URGENCY_HE = mapOf("critical" to "מסכן חיים", "high" to "דחוף", "medium" to "בינוני", "low" to "לא דחוף")
        val URGENCY_BOOST = mapOf("critical" to 0.35, "high" to 0.2, "medium" to 0.05, "low" to 0.0)
        val AGE_GROUPS = listOf("adult", "child", "child_1_plus", "infant_under_1", "pregnant_person", "newborn")
        val AGE_HE = mapOf("adult" to "מבוגר", "child" to "ילד", "child_1_plus" to "ילד מעל גיל שנה",
            "infant_under_1" to "תינוק מתחת לגיל שנה", "pregnant_person" to "הריון", "newborn" to "יילוד")
        /** גיל שנבחר במסך -> תווית (כמו AGE_CHOICES ב-Python; משמש גם בנוסח הערת הגיל) */
        val AGE_CHOICES = listOf("adult" to "מבוגר / נער", "child" to "ילד מעל גיל שנה", "infant" to "תינוק מתחת לגיל שנה")
        val AGE_MATCH = mapOf("adult" to setOf("adult"), "child" to setOf("child", "child_1_plus"), "infant" to setOf("infant_under_1"))
        /** משפחות שבהן הטכניקה שונה לפי גיל: לא מציגים צעדים לפני שיודעים את הגיל */
        val AGE_FAMILIES: Map<String, Map<String, String>> = linkedMapOf(
            "choking" to linkedMapOf("adult" to "choking_adult_child", "child" to "choking_adult_child", "infant" to "choking_infant"),
            "cpr" to linkedMapOf("adult" to "cpr_adult", "child" to "cpr_child_infant", "infant" to "cpr_child_infant"),
        )
        /** מדריך שמכיל בתוכו הנחיות שונות לילד ולתינוק */
        val MIXED_AGE_NOTE = mapOf(
            "cpr_child_infant" to mapOf(
                "child" to "בצעדים יש הנחיות נפרדות - פעלו לפי מה שמסומן \"בילד\".",
                "infant" to "בצעדים יש הנחיות נפרדות - פעלו לפי מה שמסומן \"בתינוק\".",
            ),
        )
        const val MIN_RELATIVE_SCORE = 0.35
        const val AGE_SPECIFIC_BOOST = 1.5
        val FIELD_WEIGHTS = listOf("title" to 5.0, "search_terms" to 4.0, "summary" to 2.0, "category" to 1.5,
            "setting" to 1.0, "body" to 0.7)
        // "חודשים"/"שנים" עם אות סופית לא מופיעים אחרי נרמול - נשמרים כמו במקור
        private val AGE_FILLER = setOf("בנ", "בת", "חודש", "חודשים", "שנה", "שנים")

        private val LIST_OF_STR = listOf("search_terms", "age_groups", "what_to_do", "do_not", "red_flags", "source_ids", "important_notes")
        private val REQUIRED_STR = listOf("id", "title", "category", "urgency", "summary", "call_emergency_now",
            "medical_review_status", "content_updated")
        private val REQUIRED_LIST = listOf("search_terms", "age_groups", "what_to_do", "do_not", "red_flags", "source_ids")
        private val REQUIRED_ORDER = listOf("id", "title", "category", "urgency", "summary", "search_terms", "age_groups",
            "call_emergency_now", "what_to_do", "do_not", "red_flags", "source_ids", "medical_review_status", "content_updated")
        private val DATE = Regex("(?U)^\\d{4}-\\d{2}-\\d{2}\\n?$")

        /** round(x, 2) של Python: עיגול הערך הבינארי המדויק, חצי-לזוגי */
        fun pyRound2(x: Double): Double = BigDecimal(x).setScale(2, RoundingMode.HALF_EVEN).toDouble()

        internal fun pyTruthy(v: Any?): Boolean = when (v) {
            null -> false
            is String -> v.isNotEmpty()
            is List<*> -> v.isNotEmpty()
            is Map<*, *> -> v.isNotEmpty()
            is Boolean -> v
            is Number -> v.toDouble() != 0.0
            else -> true
        }

        internal fun strList(v: Any?): List<String> = (v as? List<*>)?.filterIsInstance<String>().orEmpty()

        /** -> רשימת בעיות (ריקה = תקין). */
        fun validateGuide(g: Any?, knownSources: Set<String>?): List<String> {
            if (g !is Map<*, *>) return listOf("המדריך אינו אובייקט")
            val problems = ArrayList<String>()
            for (k in REQUIRED_ORDER) {
                if (!g.containsKey(k)) problems += "חסר שדה $k"
                else if (k in REQUIRED_STR && g[k] !is String) problems += "שדה $k מסוג שגוי"
                else if (k in REQUIRED_LIST && g[k] !is List<*>) problems += "שדה $k מסוג שגוי"
            }
            for (k in LIST_OF_STR) {
                val l = g[k] as? List<*> ?: continue
                if (!l.all { it is String && pyStrip(it).isNotEmpty() }) problems += "שדה $k מכיל ערך שאינו טקסט"
            }
            if (g["urgency"] !in URGENCIES) problems += "דחיפות לא מוכרת: ${g["urgency"]}"
            (g["age_groups"] as? List<*>)?.let { ag ->
                val bad = ag.filter { it !in AGE_GROUPS }
                if (bad.isNotEmpty() || ag.isEmpty()) problems += "קבוצות גיל לא תקינות: ${if (bad.isEmpty()) "ריק" else bad.toString()}"
            }
            for (k in listOf("what_to_do", "title", "medical_review_status")) if (!pyTruthy(g[k])) problems += "שדה $k ריק"
            if (knownSources != null) (g["source_ids"] as? List<*>)?.let { ids ->
                val missing = ids.filter { it !in knownSources }
                if (missing.isNotEmpty()) problems += "מקורות לא מוכרים: $missing"
            }
            val cu = g["content_updated"]
            if (cu !is String || !DATE.matches(cu)) problems += "content_updated אינו תאריך YYYY-MM-DD"
            return problems
        }

        /** מדריך תקין מהמאגר הבסיסי -> [Guide] (טקסטים כלשונם) */
        fun baseGuide(g: Map<*, *>): Guide {
            val od = g["official_dynamic"] as? Map<*, *>
            return Guide(
                id = g["id"] as String, title = g["title"] as String, category = g["category"] as String,
                urgency = g["urgency"] as String, summary = g["summary"] as String, searchTerms = strList(g["search_terms"]),
                ageGroups = strList(g["age_groups"]), callEmergencyNow = g["call_emergency_now"] as String,
                whatToDo = strList(g["what_to_do"]), doNot = strList(g["do_not"]), redFlags = strList(g["red_flags"]),
                importantNotes = strList(g["important_notes"]), recognition = strList(g["recognition"]),
                sourceIds = strList(g["source_ids"]), medicalReviewStatus = g["medical_review_status"] as String,
                contentUpdated = g["content_updated"] as String, setting = strList(g["setting"]),
                kind = g["kind"] as? String, packageId = g["package_id"] as? String, packageVersion = g["package_version"] as? String,
                officialDynamic = od?.let { OfficialDynamic(it["authority"]?.toString() ?: "", it["text"]?.toString() ?: "") },
                authorityReview = g["authority_review"] as? String, overlaps = g["overlaps"] as? String,
            )
        }

        private fun readJson(src: ContentSource, path: String): Any? = MiniJson.parse(src.read(path))

        private fun describe(e: Throwable): String = e.message ?: e.javaClass.simpleName

        /**
         * טעינה. לא זורקת על תוכן פגום - [errors] מפרט; מדריכים פגומים מדולגים.
         * supplements: חבילות הרחבה פעילות נוספות *אחרי* המאגר המקורי - תוספת בלבד (מזהה קיים לא נדרס).
         */
        fun load(src: ContentSource, supplements: Boolean = true): FirstAidLibrary {
            val errors = ArrayList<String>()
            val meta = LinkedHashMap<String, Any?>()
            var raw: List<Any?> = emptyList()
            try {
                val data = readJson(src, GUIDES_PATH)
                if (data !is Map<*, *> || data["guides"] !is List<*>) throw JsonError("מבנה הקובץ לא תקין (חסר guides)")
                for ((k, v) in data) if (k != "guides") meta[k as String] = v
                raw = data["guides"] as List<*>
            } catch (e: Exception) {
                errors += "לא ניתן לטעון את קובץ המדריכים: ${describe(e)}"
            }
            val sources = LinkedHashMap<String, Source>()
            try {
                val d = readJson(src, SOURCES_PATH) as? Map<*, *> ?: throw JsonError("מבנה לא תקין")
                for (s in (d["sources"] as? List<*>).orEmpty()) {
                    if (s is Map<*, *> && pyTruthy(s["id"])) {
                        val id = s["id"].toString()
                        sources[id] = Source(id, (s["title"] as? String) ?: id, s["url"] as? String)
                    }
                }
            } catch (e: Exception) {
                errors += "קובץ המקורות לא נטען: ${describe(e)}"
            }
            val synonyms = LinkedHashMap<String, List<String>>()
            try {
                val d = readJson(src, SYNONYMS_PATH) as? Map<*, *> ?: throw JsonError("מבנה לא תקין")
                val phrases = d["phrases"] ?: emptyMap<String, Any?>()
                if (phrases !is Map<*, *>) throw JsonError("phrases אינו מילון")
                for ((k, v) in phrases) if (k is String && v is List<*>) synonyms[k] = v.filterIsInstance<String>()
            } catch (e: Exception) {
                errors += "קובץ מילים נרדפות לא נטען: ${describe(e)}"
            }
            val guides = ArrayList<Guide>()
            val seen = HashSet<String>()
            val known = if (sources.isNotEmpty()) sources.keys.toSet() else null
            for (g in raw) {
                var probs = validateGuide(g, known)
                if (probs.isEmpty() && ((g as Map<*, *>)["id"] as String) in seen) probs = listOf("מזהה כפול")
                if (probs.isNotEmpty()) {
                    val id = (g as? Map<*, *>)?.get("id") ?: "?"
                    errors += "$id: " + probs.joinToString("; ")
                    continue
                }
                val guide = baseGuide(g as Map<*, *>)
                seen += guide.id
                guides += guide
            }
            val packages = ArrayList<PackageInfo>()
            val conflicts = ArrayList<Conflict>()
            if (supplements) {
                val loaded = Supplements.loadActive(src, errors)
                val nums = LinkedHashMap<String, String>()
                (meta["emergency_numbers"] as? Map<*, *>)?.forEach { (k, v) -> if (k is String && v != null) nums[k] = v.toString() }
                for (pkg in loaded) {
                    var added = 0
                    for (sg in pkg.guides) {
                        if (sg.guide.id in seen) {     // המקור (או חבילה קודמת) גובר - לא דורסים
                            conflicts += Conflict(sg.guide.id, pkg.packageId, pkg.version,
                                "מזהה קיים - נשמר המדריך הקיים, הגרסה מהחבילה לא נטענה")
                            continue
                        }
                        for (s in sg.sources) sources.putIfAbsent(s.id, s)
                        seen += sg.guide.id
                        guides += sg.guide
                        added++
                    }
                    (pkg.meta["emergency_numbers"] as? Map<*, *>)?.forEach { (k, v) ->
                        if (k is String && v != null) {
                            val key = if (k == "israel_poison_center_rambam") "poison_center" else k
                            nums.putIfAbsent(key, v.toString())
                        }
                    }
                    packages += PackageInfo(pkg.packageId, pkg.version, added, pkg.guides.size, pkg.meta["updated"] as? String)
                }
                if (nums.isNotEmpty()) meta["emergency_numbers"] = nums
            }
            return FirstAidLibrary(guides, meta, sources, synonyms, errors, packages, conflicts)
        }

        /** לא אמור לקרות (load לא זורק) - אבל אם כן: מאגר ריק, 101 עדיין זמין. */
        fun safeLoad(src: ContentSource): FirstAidLibrary = try {
            load(src)
        } catch (e: Throwable) {
            FirstAidLibrary(emptyList(), errors = listOf("שגיאה בטעינת המדריכים: ${describe(e)}"))
        }
    }
}
