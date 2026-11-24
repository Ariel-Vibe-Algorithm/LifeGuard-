package org.lifeguard.app.core

import org.json.JSONObject
import java.math.BigDecimal
import java.math.RoundingMode
import kotlin.math.asin
import kotlin.math.cos
import kotlin.math.sin
import kotlin.math.sqrt

/** מקום בטוח (משטרה / בי"ח עם מיון / כיבוי, או מקום ציבורי פתוח). */
data class SafePlace(
    val name: String,
    val lat: Double,
    val lon: Double,
    /** police | hospital | fire_station | synagogue | mosque | church | community_centre | pharmacy | fuel | supermarket */
    val type: String,
    val typeLabel: String,
    /** תג emergency מ-OSM (yes/no) - רק לבתי חולים */
    val emergency: String? = null,
    /** bundled_official (data.gov.il) | bundled_osm (OpenStreetMap) */
    val source: String,
    val address: String? = null,
)

/**
 * המקום שנבחר. walkM = מרחק הליכה: אמיתי (OSRM, walkMeasured=true) או אומדן (קו אווירי × 1.3).
 * walkS - שניות הליכה רק כשנמדד (OSRM); באומדן null - לא ממציאים זמן.
 */
data class SafePlaceChoice(
    val place: SafePlace,
    val distanceM: Double,
    val walkM: Long,
    val walkMeasured: Boolean,
    val walkS: Long? = null,
    /** מקום תפילה שנבחר כמפלט אחרון (אין רשמי/ציבורי אחר קרוב) - הערה להצגה; אחרת null */
    val fallbackNote: String? = null,
)

/**
 * לאיזה מקום תפילה להפנות בסכנה, רק כשאין מקום בטוח אחר קרוב (worship_pref.py ב-Windows).
 * ההעדפה נשמרת רק במכשיר, מוצפנת (SecureStore.worshipFallback) - לא נשלחת לשרת ולא לאנשי הקשר.
 * ברירת מחדל "synagogue" = ההתנהגות שהייתה לפני השינוי.
 */
object WorshipPref {
    /** מפתח -> תווית בעברית, לפי הסדר (worship_pref.OPTIONS) */
    val OPTIONS: Map<String, String> = linkedMapOf(
        "synagogue" to "בית כנסת",
        "mosque" to "מסגד",
        "church" to "כנסייה",
        "any" to "כל מקום תפילה",
        "none" to "לא להפנות למקומות תפילה",
    )
    const val DEFAULT = "synagogue"
    const val EXPLAIN_HE = "לא חובה. אם בזמן סכנה אין תחנת משטרה, בית חולים, תחנת כיבוי או מקום ציבורי פתוח קרוב - " +
        "לאיזה מקום תפילה להפנות אותך? נשמר רק במכשיר הזה (מוצפן), לא נשלח לשרת ולא לאנשי הקשר."

    fun normalize(pref: String?): String = if (pref != null && pref in OPTIONS) pref else DEFAULT

    /** אילו קטגוריות מקום תפילה מותר להציע לפי ההעדפה (worship_pref.allowed_ids) */
    fun allowedIds(pref: String?): Set<String> = when (val p = normalize(pref)) {
        "none" -> emptySet()
        "any" -> SafePlaces.WORSHIP_IDS
        else -> setOf(p)
    }

    /** הקטגוריות לחיפוש: כל הלא-דתיות + מקומות התפילה שמותרים (worship_pref.categories_for), לפי הסדר */
    fun categoriesFor(pref: String?): Set<String> {
        val ok = allowedIds(pref)
        return SafePlaces.CATEGORIES.keys.filterTo(LinkedHashSet()) { it !in SafePlaces.WORSHIP_IDS || it in ok }
    }
}

/**
 * מקום בטוח קרוב - העתק מדויק של map_module._choose / is_er_hospital (אפליקציית Windows).
 * המסלול האופליין: רשימה ארצית מובנית (safe_places/safe_places_il.json - נארזת ב-assets בבנייה), בלי רשת,
 * הליכה = קו אווירי × 1.3. תאימות ל-Python נבדקת ב-SafePlacesParityTest (safe_places_parity.json).
 * walks (אופציונלי) = טבלת מרחקי הליכה אמיתיים לרשימה הקצרה (OSRM table) - אותו דירוג בדיוק כמו walk=True.
 */
object SafePlaces {
    /** config.SAFE_PLACE_CATEGORIES (מזהה -> תווית), לפי הסדר */
    val CATEGORIES: Map<String, String> = linkedMapOf(
        "police" to "תחנת משטרה",
        "hospital" to "בית חולים",
        "fire_station" to "תחנת כיבוי אש",
        "synagogue" to "בית כנסת",
        "mosque" to "מסגד",
        "church" to "כנסייה",
        "community_centre" to "מתנ\"ס / מרכז קהילתי",
        "pharmacy" to "בית מרקחת",
        "fuel" to "תחנת דלק (פתוחה 24/7 לרוב)",
        "supermarket" to "סופרמרקט",
    )
    val OFFICIAL_IDS = setOf("police", "hospital", "fire_station")
    /** מקומות תפילה (worship=True ב-config.py): עדיפות אחרונה בקרבת מקום, ולפי העדפת המשתמש/ת (WorshipPref) */
    val WORSHIP_IDS = setOf("synagogue", "mosque", "church")
    const val FALLBACK_NOTE_HE = "אין מקום בטוח אחר בקרבת מקום - מקום תפילה לפי ההעדפה שלך"
    const val SEARCH_RADIUS_M = 5000.0            // config.SAFE_PLACE_SEARCH_RADIUS_M
    const val OFFICIAL_PREFERRED_M = 2000.0       // רשמי עד 2 ק"מ עדיף על כל מקום ציבורי
    const val PUBLIC_RADIUS_M = 1200.0            // מקום ציבורי רלוונטי רק אם הוא באמת קרוב
    const val WALK_FACTOR = 1.3                   // בלי מרחק הליכה אמיתי: הליכה ≈ 1.3 × קו אווירי
    const val ASSET_PATH = "safe_places/safe_places_il.json"

    val ER_HOSPITAL_NAMES = listOf("איכילוב", "סוראסקי", "שיבא", "תל השומר", "בילינסון", "ביילינסון", "רבין", "השרון",
        "אסף הרופא", "שמיר", "מאיר", "וולפסון", "שערי צדק", "הדסה", "רמב\"ם", "רמבם", "בני ציון", "כרמל", "העמק",
        "פוריה", "זיו", "הגליל", "נהריה", "הלל יפה", "לניאדו", "קפלן", "ברזילי", "אסותא אשדוד",
        "סורוקה", "יוספטל", "מעייני הישועה", "האנגלי", "סנט ג'וזף", "מקאסד", "נצרת", "הצרפתי",
        "האיטלקי", "בניין המיון", "מיון")
    val NOT_ER = listOf("נפש", "גריאטר", "גמילה", "החלמה", "שיקום", "מרפאה", "מרפאות", "גן ילדים", "מכון", "קופ\"ח",
        "קופת חולים", "מכבי", "כללית", "לאומית", "מאוחדת", "סיעוד", "הוספיס", "פסיכיאטר", "רטורנו", "Eye", "العيون",
        "Smile", "Dental", "שיניים")

    /** "בית חולים" ב-OSM כולל גם מרפאות, בריאות הנפש, גריאטריה... - רק כללי עם מיון הוא מקום בטוח. */
    fun isErHospital(name: String?, emergency: String?): Boolean {
        val n = name ?: ""
        if (emergency == "no" || NOT_ER.any { n.contains(it) }) return false
        return emergency == "yes" || ER_HOSPITAL_NAMES.any { n.contains(it) }
    }

    fun usable(p: SafePlace) = p.type != "hospital" || isErHospital(p.name, p.emergency)

    fun haversineM(lat1: Double, lon1: Double, lat2: Double, lon2: Double): Double {
        val r = 6371000.0
        val phi1 = Math.toRadians(lat1)
        val phi2 = Math.toRadians(lat2)
        val dphi = Math.toRadians(lat2 - lat1)
        val dl = Math.toRadians(lon2 - lon1)
        val a = sin(dphi / 2).let { it * it } + cos(phi1) * cos(phi2) * sin(dl / 2).let { it * it }
        return 2 * r * asin(sqrt(a))
    }

    /** map_module._bundled_places: רק סוגים מוכרים; official -> bundled_official, אחרת bundled_osm. */
    fun parseBundled(json: String): List<SafePlace> {
        val arr = JSONObject(json).optJSONArray("places") ?: return emptyList()
        val out = ArrayList<SafePlace>(arr.length())
        for (i in 0 until arr.length()) {
            val o = arr.optJSONObject(i) ?: continue
            val type = o.optString("type")
            val label = CATEGORIES[type] ?: continue
            if (!o.has("name") || !o.has("lat") || !o.has("lon")) continue
            out += SafePlace(
                name = o.getString("name"), lat = o.getDouble("lat"), lon = o.getDouble("lon"), type = type,
                typeLabel = label, emergency = if (o.isNull("emergency")) null else o.optString("emergency", "").ifEmpty { null },
                source = if (o.optBoolean("official", false)) "bundled_official" else "bundled_osm",
                address = if (o.isNull("address")) null else o.optString("address", "").ifEmpty { null },
            )
        }
        return out
    }

    /**
     * הרשימה הקצרה של _choose: עד 6 רשמיים, עד 4 ציבוריים (<=1200 מ', בלי מקומות תפילה) ועד 3 מקומות תפילה
     * מותרים לפי ההעדפה (<=1200 מ'), כל אחת ממוינת לפי קו אווירי.
     */
    class Shortlist(
        val official: List<Pair<Double, SafePlace>>,
        val public: List<Pair<Double, SafePlace>>,
        val worship: List<Pair<Double, SafePlace>> = emptyList(),
    ) {
        val all get() = official + public + worship
        val isEmpty get() = official.isEmpty() && public.isEmpty() && worship.isEmpty()
    }

    /** worship = העדפת מקום התפילה (WorshipPref.OPTIONS); null = ברירת המחדל (בית כנסת, כמו קודם) */
    fun shortlist(lat: Double, lon: Double, candidates: List<SafePlace>, radiusM: Double = SEARCH_RADIUS_M,
                  worship: String? = null): Shortlist {
        val known = WorshipPref.categoriesFor(worship)
        val worshipIds = WORSHIP_IDS.filterTo(HashSet()) { it in known }
        val seen = HashSet<Triple<String, BigDecimal, BigDecimal>>()
        val scored = ArrayList<Pair<Double, SafePlace>>()
        for (p in candidates) {
            // Python: (type, round(lat, 4), round(lon, 4)) - round של Python = חצי-לזוגי על הערך הבינארי המדויק
            val key = Triple(p.type, BigDecimal(p.lat).setScale(4, RoundingMode.HALF_EVEN),
                BigDecimal(p.lon).setScale(4, RoundingMode.HALF_EVEN))
            if (p.type !in known || key in seen || !usable(p)) continue
            seen += key
            val d = haversineM(lat, lon, p.lat, p.lon)
            if (d <= radiusM) scored += d to p
        }
        // sortedBy יציב - כמו sorted של Python (שוויון נשאר בסדר הקלט)
        val official = scored.filter { it.second.type in OFFICIAL_IDS }.sortedBy { it.first }.take(6)
        val public = scored.filter { it.second.type !in OFFICIAL_IDS && it.second.type !in worshipIds &&
            it.first <= PUBLIC_RADIUS_M }.sortedBy { it.first }.take(4)
        // מקום תפילה (לפי ההעדפה) - נבחר רק כשאין רשמי או ציבורי אחר בקרבת מקום (chooseFrom)
        val worshipRows = scored.filter { it.second.type in worshipIds && it.first <= PUBLIC_RADIUS_M }
            .sortedBy { it.first }.take(3)
        return Shortlist(official, public, worshipRows)
    }

    /**
     * בחירת המקום (map_module._choose): רשמי קרוב (<= 2000 מ' הליכה) -> הקרוב שבהם; אחרת ציבורי קרוב (<= 1200 מ');
     * אחרת מקום תפילה מותר קרוב (<= 1200 מ', עם הערת מפלט); אחרת הרשמי הקרוב ברדיוס; אחרת הציבורי/מקום התפילה הקרוב.
     * worship = העדפת מקום התפילה (null = ברירת המחדל).
     * walks: מרחק/זמן הליכה אמיתיים לכל פריט ב-shortlist.all (באותו סדר; null בפריט = לא ידוע). null = אומדן ×1.3.
     */
    fun choose(lat: Double, lon: Double, candidates: List<SafePlace>, radiusM: Double = SEARCH_RADIUS_M,
               worship: String? = null,
               walks: ((List<SafePlace>) -> List<Pair<Double, Double>?>?)? = null): SafePlaceChoice? =
        chooseFrom(shortlist(lat, lon, candidates, radiusM, worship), walks)

    fun chooseFrom(short: Shortlist, walks: ((List<SafePlace>) -> List<Pair<Double, Double>?>?)? = null): SafePlaceChoice? {
        if (short.isEmpty) return null
        val all = short.all
        val w = walks?.invoke(all.map { it.second })
        class Row(val d: Double, val p: SafePlace, val walkM: Double, val walkS: Double?)
        val rows = all.mapIndexed { i, (d, p) ->
            val wi = if (w != null && i < w.size) w[i] else null
            Row(d, p, wi?.first ?: (d * WALK_FACTOR), wi?.second)
        }
        val nO = short.official.size
        val nP = short.public.size
        val official = rows.take(nO).sortedBy { it.walkM }
        val public = rows.drop(nO).take(nP).sortedBy { it.walkM }
        val worship = rows.drop(nO + nP).sortedBy { it.walkM }
        var fallback = false
        val best = when {
            official.isNotEmpty() && official[0].walkM <= OFFICIAL_PREFERRED_M * WALK_FACTOR -> official[0]
            public.isNotEmpty() && public[0].walkM <= PUBLIC_RADIUS_M * WALK_FACTOR -> public[0]
            worship.isNotEmpty() && worship[0].walkM <= PUBLIC_RADIUS_M * WALK_FACTOR -> { fallback = true; worship[0] }
            official.isNotEmpty() -> official[0]
            else -> { fallback = public.isEmpty(); public.ifEmpty { worship }[0] }
        }
        return SafePlaceChoice(best.p, best.d, pyRound(best.walkM), best.walkS != null, best.walkS?.let { pyRound(it) },
            if (fallback) FALLBACK_NOTE_HE else null)
    }

    /** round() של Python לשלם: חצי-לזוגי */
    fun pyRound(x: Double): Long = Math.rint(x).toLong()

    // ---------------- טקסט למסך (עברית, כמו בחלון ה-SOS ב-Windows) ----------------
    const val STALE_ORIGIN_S = 300.0
    const val IMPRECISE_ORIGIN_M = 500.0

    /**
     * routeM/routeS = מסלול הליכה אמיתי (OSRM) ליעד אם התקבל. בלי מסלול ובלי טבלה - "אומדן" במפורש.
     */
    fun summaryHe(c: SafePlaceChoice, routeM: Double? = null, routeS: Double? = null): String {
        val lines = ArrayList<String>()
        lines += "🏁 מקום בטוח קרוב: ${c.place.name} (${c.place.typeLabel})"
        lines += when {
            routeM != null && routeS != null -> "כ-${pyRound(routeM)} מ' הליכה · ~${minutes(routeS)} דק' (לפי מסלול הליכה)"
            c.walkMeasured -> "כ-${c.walkM} מ' הליכה" + (c.walkS?.let { " · ~${minutes(it.toDouble())} דק'" } ?: "") +
                " (לפי מסלול הליכה)"
            else -> "כ-${c.walkM} מ' הליכה (אומדן: קו אווירי × 1.3 - בלי אינטרנט)"
        }
        c.fallbackNote?.let { lines += "ℹ️ $it" }
        lines += if (c.place.source == "bundled_official") "מקור: רשימה רשמית (data.gov.il)"
        else "מקור: OpenStreetMap - לא רשמי, ייתכן שאינו מעודכן"
        return lines.joinToString("\n")
    }

    private fun minutes(seconds: Double) = maxOf(1L, pyRound(seconds / 60.0))

    /** הערה על נקודת המוצא: מיקום ישן / משוער - לעולם לא מוצג כמיקום חי. null = טרי ומדויק. */
    fun originNoteHe(capturedAt: Double?, accuracyM: Double?, nowSec: Double): String? {
        val notes = ArrayList<String>()
        if (capturedAt == null) notes += "⚠️ זמן המיקום לא ידוע - ייתכן שאינו עדכני"
        else {
            val age = nowSec - capturedAt
            if (age > STALE_ORIGIN_S) notes += "⚠️ מחושב לפי המיקום האחרון הידוע (לפני ${pyRound(age / 60.0)} דק') - לא מיקום חי"
        }
        if (accuracyM != null && accuracyM > IMPRECISE_ORIGIN_M) notes += "⚠️ המיקום משוער (±${pyRound(accuracyM)} מ') - המרחק עלול להיות לא מדויק"
        return notes.takeIf { it.isNotEmpty() }?.joinToString("\n")
    }

    const val NONE_FOUND_HE = "לא נמצא מקום בטוח מוכר ברדיוס 5 ק\"מ (ברשימה המובנית: משטרה, כיבוי, בי\"ח עם מיון). " +
        "בסכנה מיידית: 100"
    const val NO_LOCATION_HE = "📍 אין עדיין מיקום - לא ניתן להציע מקום בטוח קרוב. בסכנה מיידית: 100"

    // ---------------- ניווט: אפליקציית המפות של המשתמש/ת ----------------
    /**
     * קישורי ניווט ברגל, לפי סדר הניסיון:
     * 1. https://www.google.com/maps/dir/?api=1 ... travelmode=walking - נפתח באפליקציית המפות אם מותקנת, אחרת בדפדפן
     * 2. geo: - כל אפליקציית מפות (בלי מצב הליכה - רק היעד)
     * 3. OpenStreetMap (FOSSGIS, הליכה) בדפדפן
     */
    fun navigationUris(fromLat: Double?, fromLon: Double?, place: SafePlace): List<String> {
        fun f(x: Double) = "%.6f".format(java.util.Locale.US, x)
        val dest = "${f(place.lat)},${f(place.lon)}"
        val origin = if (fromLat != null && fromLon != null) "&origin=${f(fromLat)},${f(fromLon)}" else ""
        val label = java.net.URLEncoder.encode(place.name, "UTF-8").replace("+", "%20")
        val osmRoute = if (fromLat != null && fromLon != null) "${f(fromLat)}%2C${f(fromLon)}%3B${f(place.lat)}%2C${f(place.lon)}"
            else "%3B${f(place.lat)}%2C${f(place.lon)}"
        return listOf(
            "https://www.google.com/maps/dir/?api=1$origin&destination=$dest&travelmode=walking",
            "geo:$dest?q=$dest($label)",
            "https://www.openstreetmap.org/directions?engine=fossgis_osrm_foot&route=$osmRoute",
        )
    }
}
