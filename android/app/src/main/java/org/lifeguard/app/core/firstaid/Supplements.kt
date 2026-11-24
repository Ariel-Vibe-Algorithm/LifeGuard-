package org.lifeguard.app.core.firstaid

import org.lifeguard.app.core.firstaid.FirstAidLibrary.Companion.pyTruthy
import org.lifeguard.app.core.firstaid.FirstAidLibrary.Companion.strList
import org.lifeguard.app.core.firstaid.FirstAidText.pyStrip

/**
 * חבילות הרחבה - פורט של first_aid/supplements.py: תוספת בלבד, עם גרסאות ונפילה לגרסה קודמת.
 *
 * - חבילה בתיקייה משלה: content/supplements/<package_id>/<version>/; content/supplements/active.json קובע איזו גרסה
 *   פעילה ומה ההיסטוריה. גרסה פגומה -> הגרסה הקודמת בהיסטוריה; אין - מדלגים. המאגר המקורי ממשיך לעבוד.
 * - הסכמה של חבילה 02 (keywords / recognition / actions / setting / sources כ-URL) מתורגמת למבנה הפנימי **בלי לשנות טקסט**.
 *   שדות חסרים לא "מומצאים": אין קבוצת גיל -> ageGroups ריק ("לא צוין בחבילה").
 * - מזהה שכבר קיים במאגר (או בחבילה קודמת) לא נכנס - נרשם כהתנגשות (ב-[FirstAidLibrary.load]).
 * - supplement_policy.json: מיפוי דחיפות, נהלי בטיחות (לא טיפול רפואי), official_dynamic, גורם מאשר, חפיפות למאגר הבסיס.
 */
object Supplements {
    const val SUPP_DIR = "content/supplements"
    const val ACTIVE_PATH = "$SUPP_DIR/active.json"
    const val POLICY_PATH = "supplement_policy.json"
    const val SUPP_JSON = "first_aid_supplement_he.json"
    const val SUPP_SOURCES_MD = "SOURCES_AND_MEDICAL_REVIEW.md"

    private val REQUIRED_STR = listOf("id", "title", "urgency", "emergency")
    private val REQUIRED_ORDER = listOf("id", "title", "keywords", "urgency", "recognition", "actions", "do_not", "emergency", "sources")
    private val SETTING_SPLIT = Regex("[/,،;]")
    private val SOURCE_LINE = Regex("(?U)^\\s*-\\s*(.+?):\\s*(https?://\\S+)\\s*$")
    private val SAFE_NAME = Regex("^[A-Za-z0-9._-]+$")

    class SuppGuide(val guide: Guide, val sources: List<Source>)
    class LoadedPackage(val packageId: String, val version: String, val meta: Map<String, Any?>, val guides: List<SuppGuide>)

    fun defaultPolicy(): Map<String, Any?> =
        mapOf("urgency_map" to emptyMap<String, Any?>(), "default_review_status" to "pending_medical_review", "packages" to emptyMap<String, Any?>())

    @Suppress("UNCHECKED_CAST")
    fun loadPolicy(src: ContentSource): Map<String, Any?> = try {
        (MiniJson.parse(src.read(POLICY_PATH)) as? Map<String, Any?>) ?: defaultPolicy()
    } catch (e: Exception) {
        defaultPolicy()
    }

    /** -> {"packages": {...}}; פגום/חסר -> ריק */
    @Suppress("UNCHECKED_CAST")
    fun readActive(src: ContentSource, path: String = ACTIVE_PATH): Map<String, Any?> = try {
        val d = MiniJson.parse(src.read(path)) as? Map<String, Any?>
        if (d != null && d["packages"] is Map<*, *>) d else mapOf("packages" to emptyMap<String, Any?>())
    } catch (e: Exception) {
        mapOf("packages" to emptyMap<String, Any?>())
    }

    /** setting מגיע לפעמים כרשימה ולפעמים כמחרוזת "בית/רחוב/טיול" - מאחדים לרשימה. */
    fun normalizeSetting(v: Any?): List<String> {
        val items: List<Any?> = when (v) {
            is String -> v.split(SETTING_SPLIT)
            is List<*> -> v
            else -> return emptyList()
        }
        return items.filterIsInstance<String>().map { pyStrip(it) }.filter { it.isNotEmpty() }
    }

    fun validateRaw(g: Any?): List<String> {
        if (g !is Map<*, *>) return listOf("המדריך אינו אובייקט")
        val probs = ArrayList<String>()
        for (k in REQUIRED_ORDER) {
            val ok = if (k in REQUIRED_STR) g[k] is String else g[k] is List<*>
            if (!ok) probs += "חסר/שגוי $k"
        }
        for (k in listOf("keywords", "recognition", "actions", "do_not", "sources")) {
            val l = g[k] as? List<*> ?: continue
            if (!l.all { it is String && pyStrip(it).isNotEmpty() }) probs += "$k מכיל ערך שאינו טקסט"
        }
        if ((g["actions"] as? List<*>)?.isEmpty() == true) probs += "אין צעדים"
        if (pyStrip(pyStr(g["id"])).isEmpty() || pyStrip(pyStr(g["title"])).isEmpty()) probs += "מזהה/כותרת ריקים"
        return probs
    }

    private fun pyStr(v: Any?): String = v?.toString() ?: "None"

    /** שורות "- Title: URL" -> {url: title}. */
    fun parseSourcesMd(md: String): Map<String, String> {
        val out = LinkedHashMap<String, String>()
        for (line in md.lines()) {
            val m = SOURCE_LINE.find(line) ?: continue
            out[m.groupValues[2]] = pyStrip(m.groupValues[1])
        }
        return out
    }

    /** urllib.parse.urlparse(url).hostname - פענוח טקסט בלבד (המנוע לא מייבא שום ספריית רשת) */
    fun hostOf(url: String): String? {
        val i = url.indexOf("//")
        if (i < 0) return null
        var rest = url.substring(i + 2)
        val end = rest.indexOfAny(charArrayOf('/', '?', '#'))
        if (end >= 0) rest = rest.substring(0, end)
        rest = rest.substringAfterLast('@')
        rest = if (rest.startsWith("[")) rest.substring(1).substringBefore(']') else rest.substringBefore(':')
        return rest.lowercase().ifEmpty { null }
    }

    @Suppress("UNCHECKED_CAST")
    private fun asMap(v: Any?): Map<String, Any?> = (v as? Map<String, Any?>) ?: emptyMap()

    /** מדריך מחבילת הרחבה -> מבנה פנימי (טקסטים כלשונם). */
    fun convert(g: Map<*, *>, meta: Map<String, Any?>, policy: Map<String, Any?>, pkgPolicy: Map<String, Any?>,
                version: String, sourceTitles: Map<String, String>): SuppGuide {
        val umap = asMap(policy["urgency_map"])
        val id = g["id"] as String
        val safety = (pkgPolicy["safety_procedures"] as? List<*>)?.contains(id) == true
        val setting = normalizeSetting(g["setting"])
        val sources = strList(g["sources"]).map { url -> Source(url, sourceTitles[url]?.takeIf { it.isNotEmpty() } ?: hostOf(url) ?: url, url) }
        val notes = g["notes"]
        val recognition = strList(g["recognition"])
        val emergency = g["emergency"] as String
        // מיפוי דחיפות; ערך לא מוכר -> "high" (כמו ברירת המחדל של umap.get(..., "high"))
        val urgency = (umap[g["urgency"] as String] as? String)?.takeIf { it in FirstAidLibrary.URGENCIES } ?: "high"
        val category = pkgPolicy[if (safety) "category_safety" else "category_medical"] as? String ?: "חבילת הרחבה"
        val od = asMap(pkgPolicy["official_dynamic"])[id] as? Map<*, *>
        val guide = Guide(
            id = id, title = g["title"] as String, category = category, urgency = urgency,
            summary = if (recognition.isNotEmpty()) recognition[0] else emergency,
            searchTerms = strList(g["keywords"]) + setting,
            ageGroups = emptyList(),
            callEmergencyNow = emergency,
            whatToDo = strList(g["actions"]), doNot = strList(g["do_not"]), redFlags = emptyList(),
            importantNotes = if (notes is String && pyStrip(notes).isNotEmpty()) listOf(notes) else emptyList(),
            recognition = recognition,
            sourceIds = sources.map { it.id },
            medicalReviewStatus = (g["review_status"] as? String)?.takeIf { it.isNotEmpty() }
                ?: (policy["default_review_status"] as? String) ?: "pending_medical_review",
            contentUpdated = (meta["updated"] as? String) ?: "",
            setting = setting,
            kind = if (safety) "safety" else "medical",
            packageId = meta["package_id"] as? String, packageVersion = version,
            officialDynamic = od?.let { OfficialDynamic(it["authority"]?.toString() ?: "", it["text"]?.toString() ?: "") },
            authorityReview = asMap(pkgPolicy["authority_review"])[id] as? String,
            overlaps = asMap(pkgPolicy["overlaps_with_base"])[id] as? String,
        )
        return SuppGuide(guide, sources)
    }

    class PackageError(message: String) : Exception(message)

    /** -> חבילה טעונה + שגיאות למדריכים שדולגו. זורק אם החבילה לא שמישה כלל. */
    fun loadVersion(src: ContentSource, pkgId: String, version: String, policy: Map<String, Any?>,
                    errors: MutableList<String>): LoadedPackage {
        if (!SAFE_NAME.matches(pkgId) || !SAFE_NAME.matches(version) || pkgId.contains("..") || version.contains("..")) {
            throw PackageError("שם חבילה/גרסה לא תקין")
        }
        val dir = "$SUPP_DIR/$pkgId/$version"
        val data = MiniJson.parse(src.read("$dir/$SUPP_JSON"))
        if (data !is Map<*, *> || data["guides"] !is List<*>) throw PackageError("מבנה חבילה לא תקין (חסר guides)")
        val meta = LinkedHashMap<String, Any?>()
        for ((k, v) in data) if (k != "guides") meta[k as String] = v
        val titles = try {
            parseSourcesMd(src.read("$dir/$SUPP_SOURCES_MD"))
        } catch (e: java.nio.charset.CharacterCodingException) {
            throw e           // קובץ קיים אבל לא UTF-8 תקין - כמו ב-Python: הגרסה לא נטענת
        } catch (e: java.io.IOException) {
            emptyMap()        // אין קובץ מקורות - הכותרת תהיה שם האתר
        }
        val pkgPolicy = asMap(asMap(policy["packages"])[pkgId])
        val out = ArrayList<SuppGuide>()
        val errs = ArrayList<String>()
        val seen = HashSet<String>()
        for (g in data["guides"] as List<*>) {
            var probs = validateRaw(g)
            if (probs.isEmpty() && ((g as Map<*, *>)["id"] as String) in seen) probs = listOf("מזהה כפול בתוך החבילה")
            if (probs.isNotEmpty()) {
                val id = (g as? Map<*, *>)?.get("id") ?: "?"
                errs += "$pkgId/$version: $id: " + probs.joinToString("; ")
                continue
            }
            val m = g as Map<*, *>
            seen += m["id"] as String
            out += convert(m, meta, policy, pkgPolicy, version, titles)
        }
        if (out.isEmpty()) throw PackageError("אין בחבילה אף מדריך תקין")
        errors += errs
        return LoadedPackage(pkgId, version, meta, out)
    }

    /** -> חבילות פעילות (ממוינות לפי מזהה). גרסה פגומה -> הגרסה הקודמת בהיסטוריה. שגיאות נוספות ל-[errors]. */
    fun loadActive(src: ContentSource, errors: MutableList<String>, policy: Map<String, Any?>? = null,
                   active: Map<String, Any?>? = null): List<LoadedPackage> {
        val pol = policy ?: loadPolicy(src)
        val act = active ?: readActive(src)
        val loaded = ArrayList<LoadedPackage>()
        val pkgs = asMap(act["packages"])
        for (pkgId in pkgs.keys.sorted()) {
            val st = pkgs[pkgId] as? Map<*, *>
            if (st == null) { errors += "$pkgId: רשומה לא תקינה ב-active.json"; continue }
            val activeVer = st["active"]
            val history = (st["history"] as? List<*>).orEmpty()
            val candidates = (listOf(activeVer) + history.reversed().filter { it != activeVer })
                .filter { pyTruthy(it) }.filterIsInstance<String>()
            for ((i, ver) in candidates.withIndex()) {
                val pkg = try {
                    loadVersion(src, pkgId, ver, pol, errors)
                } catch (e: Exception) {
                    errors += "$pkgId/$ver לא נטענה (${e.message ?: e.javaClass.simpleName})" + (if (i == 0) " - מנסה גרסה קודמת" else "")
                    continue
                }
                if (i > 0) errors += "$pkgId: נטענה גרסה קודמת $ver במקום ${activeVer ?: "None"}"
                loaded += pkg
                break
            }
        }
        return loaded
    }
}
