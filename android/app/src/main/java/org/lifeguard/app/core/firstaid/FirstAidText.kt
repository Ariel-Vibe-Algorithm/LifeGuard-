package org.lifeguard.app.core.firstaid

import java.text.Normalizer
import java.util.Locale

/**
 * נרמול עברית, צורות בסיס, מרחק עריכה וזיהוי גיל - פורט מדויק של first_aid/library.py (normalize / stems /
 * _edit_distance / tokens / detect_age). אורכים נמדדים בנקודות קוד (כמו len() של Python), לא ביחידות UTF-16.
 * בדיקת התאימות: FirstAidParityTest מול first_aid_parity.json שנוצר מהמימוש ב-Python.
 */
object FirstAidText {
    private val NIQQUD = Regex("[֑-ׇ]")
    private val ARABIC_DIACRITICS = Regex("[ً-ٰٟ]")
    private val FINALS = mapOf('ך' to 'כ', 'ם' to 'מ', 'ן' to 'נ', 'ף' to 'פ', 'ץ' to 'צ')
    private val VAV_RUN = Regex("וו+")
    private val YOD_RUN = Regex("יי+")
    private val ARABIC_FOLD = listOf("أ" to "ا", "إ" to "ا", "آ" to "ا", "ة" to "ه", "ى" to "ي")
    private val PREFIXES2 = listOf("וה", "שה", "כש", "מה", "לה", "בה", "וב", "ול", "ומ", "וש", "שב", "של", "שמ")
    private val PREFIXES1 = listOf("ו", "ה", "ב", "ל", "מ", "ש", "כ")
    private val SUFFIXES = listOf("ים", "ות", "ה", "ת", "י")

    /** str.isspace() של Python (כולל \x1c-\x1f, \x85 ורווחים מסוג Zs) */
    fun isPySpace(cp: Int): Boolean = when (cp) {
        0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x1C, 0x1D, 0x1E, 0x1F, 0x20, 0x85, 0xA0, 0x1680, 0x2028, 0x2029, 0x202F,
        0x205F, 0x3000 -> true
        in 0x2000..0x200A -> true
        else -> false
    }

    /** \w של re ב-Python (str.isalnum() או קו תחתון) */
    fun isPyWord(cp: Int): Boolean {
        if (cp == '_'.code || Character.isLetter(cp) || Character.isDigit(cp)) return true
        val t = Character.getType(cp)
        return t == Character.LETTER_NUMBER.toInt() || t == Character.OTHER_NUMBER.toInt()
    }

    fun cpLen(s: String): Int = s.codePointCount(0, s.length)

    /** str.strip() של Python */
    fun pyStrip(s: String): String {
        var a = 0
        var b = s.length
        while (a < b && isPySpace(s.codePointAt(a))) a += Character.charCount(s.codePointAt(a))
        while (b > a && isPySpace(s.codePointBefore(b))) b -= Character.charCount(s.codePointBefore(b))
        return s.substring(a, b)
    }

    /** str.split() בלי ארגומנט */
    fun pySplit(s: String): List<String> {
        val out = ArrayList<String>()
        val cur = StringBuilder()
        var i = 0
        while (i < s.length) {
            val cp = s.codePointAt(i)
            if (isPySpace(cp)) { if (cur.isNotEmpty()) { out += cur.toString(); cur.setLength(0) } }
            else cur.appendCodePoint(cp)
            i += Character.charCount(cp)
        }
        if (cur.isNotEmpty()) out += cur.toString()
        return out
    }

    /** str.isdigit() (לא ריק וכל התווים ספרות) */
    fun isPyDigits(s: String): Boolean = s.isNotEmpty() && s.codePoints().allMatch { Character.isDigit(it) }

    fun normalize(text: String?): String {
        var t = Normalizer.normalize(text ?: "", Normalizer.Form.NFKC).lowercase(Locale.ROOT)
        t = NIQQUD.replace(t, "")
        t = ARABIC_DIACRITICS.replace(t, "").replace("ـ", "")
        t = t.replace("״", "").replace("׳", "").replace("\"", "").replace("'", "")
        // [^\w\s] -> רווח
        val sb = StringBuilder(t.length)
        var i = 0
        while (i < t.length) {
            val cp = t.codePointAt(i)
            if (isPyWord(cp) || isPySpace(cp)) sb.appendCodePoint(cp) else sb.append(' ')
            i += Character.charCount(cp)
        }
        t = sb.toString().replace('_', ' ')
        t = buildString(t.length) { for (c in t) append(FINALS[c] ?: c) }
        t = VAV_RUN.replace(t, "ו")            // כתיב מלא/חסר: כווייה / כוויה / כויה
        t = YOD_RUN.replace(t, "י")
        for ((a, b) in ARABIC_FOLD) t = t.replace(a, b)
        return pySplit(t).joinToString(" ")    // re.sub(r"\s+", " ", t).strip()
    }

    fun isHebrew(tok: String): Boolean = tok.any { it in 'א'..'ת' }

    /** צורות בסיס אפשריות למילה: בלי אותיות שימוש בתחילה ובלי סיומות רבים/נקבה. */
    fun stems(tok: String): Set<String> {
        val out = LinkedHashSet<String>()
        out += tok
        val n = cpLen(tok)
        if (!isHebrew(tok)) {
            if (n > 4 && tok.endsWith("s")) out += tok.dropLast(1)
            if (tok.startsWith("ال") && n > 4) out += tok.substring(2)
            return out
        }
        val bases = LinkedHashSet<String>()
        bases += tok
        for (p in PREFIXES2) if (tok.startsWith(p) && n - 2 >= 3) bases += tok.substring(2)
        for (p in PREFIXES1) if (tok.startsWith(p) && n - 1 >= 3) bases += tok.substring(1)
        for (b in bases) {
            out += b
            val bn = cpLen(b)
            for (s in SUFFIXES) if (b.endsWith(s) && bn - s.length >= 3) out += b.substring(0, b.length - s.length)
        }
        return out
    }

    fun codePoints(s: String): IntArray = s.codePoints().toArray()

    /** מרחק Levenshtein עם יציאה מוקדמת מעל limit (כמו _edit_distance). */
    fun editDistance(a: IntArray, b: IntArray, limit: Int = 2): Int {
        if (Math.abs(a.size - b.size) > limit) return limit + 1
        var prev = IntArray(b.size + 1) { it }
        for (i in 1..a.size) {
            val cur = IntArray(b.size + 1)
            cur[0] = i
            var best = cur[0]
            val ca = a[i - 1]
            for (j in 1..b.size) {
                cur[j] = minOf(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (if (ca != b[j - 1]) 1 else 0))
                best = minOf(best, cur[j])
            }
            if (best > limit) return limit + 1
            prev = cur
        }
        return prev[b.size]
    }

    fun editDistance(a: String, b: String, limit: Int = 2) = editDistance(codePoints(a), codePoints(b), limit)

    fun tokens(text: String?): List<String> = pySplit(normalize(text)).filter { cpLen(it) >= 2 || isPyDigits(it) }

    // ------------------------------------------------------------ גיל
    private val INFANT = listOf("תינוק", "תינוקת", "תינוקות", "בייבי", "baby", "infant", "رضيع", "رضيعه")
    private val CHILD = listOf("ילד", "ילדה", "ילדים", "פעוט", "פעוטה", "child", "kid", "toddler", "طفل", "طفله", "ولد")
    private val ADULT = listOf("מבוגר", "מבוגרת", "אישה", "גבר", "אבא", "אמא", "סבא", "סבתא", "בעלי", "אשתי", "adult", "man",
        "woman", "رجل", "امراه")
    val AGE_WORDS: Set<String> = (INFANT + CHILD + ADULT).map { normalize(it) }.toSet()
    private val INFANT_N = INFANT.map { normalize(it) }
    private val CHILD_N = CHILD.map { normalize(it) }
    private val ADULT_N = ADULT.map { normalize(it) }
    // (?U): \b ו-\d לפי Unicode, כמו re של Python על str
    private val MONTHS = Regex("(?U)\\b\\d{1,2} ?חודש")
    private val BEN_N = Regex("(?U)\\b(?:בנ|בת) (\\d{1,2})\\b")

    /** גיל מתוך החיפוש ("תינוק נחנק" / "ילד בן 3" / "בן 4 חודשים"). null = לא ידוע (לא מנחשים). */
    fun detectAge(query: String?): String? {
        val q = normalize(query)
        val st = HashSet<String>()
        for (t in pySplit(q)) st += stems(t)
        // "חודשים" (עם ם סופית) לא מופיע אחרי נרמול - נשמר כמו במקור לשם תאימות
        if (MONTHS.containsMatchIn(q) || q.contains("חודשים")) return "infant"
        if (INFANT_N.any { it in st }) return "infant"
        val m = BEN_N.find(q)
        if (m != null) {
            val n = m.groupValues[1].toInt()
            return if (n in 1..8) "child" else null
        }
        if (CHILD_N.any { it in st }) return "child"
        if (ADULT_N.any { it in st }) return "adult"
        return null
    }
}
