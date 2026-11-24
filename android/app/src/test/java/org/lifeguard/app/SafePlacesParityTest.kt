package org.lifeguard.app

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.lifeguard.app.core.SafePlace
import org.lifeguard.app.core.SafePlaceChoice
import org.lifeguard.app.core.SafePlaces
import org.lifeguard.app.core.WorshipPref
import java.io.File

/**
 * תאימות מלאה ל-map_module._choose / is_er_hospital (Python): safe_places_parity.json נוצר ע"י
 * tools/build_safe_places_parity.py על אותה רשימה מובנית (safe_places/safe_places_il.json במאגר).
 * - cases: המסלול האופליין (walk=False, ×1.3) על הרשימה האמיתית
 * - walk_cases: ענף walk=True עם טבלת הליכה דטרמיניסטית (אותה נוסחה כאן ובמחולל)
 * - synthetic: מועמדים מלאכותיים שמפעילים כל ענף (ציבורי קרוב, רשמי רחוק, כפילויות, בי"ח בלי מיון, קשרים)
 * - worship_synthetic: מקום תפילה כמפלט אחרון לפי העדפה (worship_pref.py), כולל ענף walk=True והערת המפלט
 */
class SafePlacesParityTest {
    private val fixture = JSONObject(resource("safe_places_parity.json"))
    private val bundled: List<SafePlace> by lazy { SafePlaces.parseBundled(bundledFile().readText(Charsets.UTF_8)) }

    @Test
    fun constantsMatchPython() {
        val cats = fixture.getJSONArray("categories")
        assertEquals(cats.length(), SafePlaces.CATEGORIES.size)
        SafePlaces.CATEGORIES.entries.forEachIndexed { i, (id, label) ->
            assertEquals(cats.getJSONObject(i).getString("id"), id)
            assertEquals(cats.getJSONObject(i).getString("label"), label)
        }
        assertEquals(strings(fixture.getJSONArray("official_ids")).toSet(), SafePlaces.OFFICIAL_IDS)
        assertEquals(fixture.getDouble("official_preferred_m"), SafePlaces.OFFICIAL_PREFERRED_M, 0.0)
        assertEquals(fixture.getDouble("public_radius_m"), SafePlaces.PUBLIC_RADIUS_M, 0.0)
        assertEquals(fixture.getDouble("walk_factor"), SafePlaces.WALK_FACTOR, 0.0)
        assertEquals(fixture.getDouble("radius_m"), SafePlaces.SEARCH_RADIUS_M, 0.0)
        // מקומות תפילה (worship_pref.py)
        assertEquals(strings(fixture.getJSONArray("worship_ids")).toSet(), SafePlaces.WORSHIP_IDS)
        assertEquals(fixture.getString("worship_default"), WorshipPref.DEFAULT)
        val opts = fixture.getJSONArray("worship_options")
        assertEquals(opts.length(), WorshipPref.OPTIONS.size)
        WorshipPref.OPTIONS.entries.forEachIndexed { i, (id, label) ->
            assertEquals(opts.getJSONObject(i).getString("id"), id)
            assertEquals(opts.getJSONObject(i).getString("label"), label)
        }
        val byPref = fixture.getJSONObject("worship_categories")
        for (k in WorshipPref.OPTIONS.keys) assertEquals(k, strings(byPref.getJSONArray(k)), WorshipPref.categoriesFor(k).toList())
    }

    @Test
    fun worshipFallbackMatchesPython() {
        val cases = fixture.getJSONArray("worship_synthetic")
        assertTrue(cases.length() >= 20)
        val bad = ArrayList<String>()
        var notes = 0
        for (i in 0 until cases.length()) {
            val c = cases.getJSONObject(i)
            val lat = c.getDouble("lat"); val lon = c.getDouble("lon")
            val pref = if (c.isNull("worship")) null else c.getString("worship")
            val walks: ((List<SafePlace>) -> List<Pair<Double, Double>?>?)? =
                if (c.getBoolean("walk")) { places -> fakeWalkTable(lat, lon, places) } else null
            val got = SafePlaces.choose(lat, lon, candidates(c), c.getDouble("radius_m"), pref, walks)
            val id = c.getString("id")
            compare(id, c.opt("expected"), got)?.let { bad += it }
            val e = c.optJSONObject("expected")
            if (e != null && got != null) {
                val en = if (e.isNull("fallback_note")) null else e.getString("fallback_note")
                if (en != got.fallbackNote) bad += "$id: fallback_note $en != ${got.fallbackNote}"
                if (en != null) notes++
            }
        }
        assertTrue(bad.joinToString("\n"), bad.isEmpty())
        assertTrue("expected several fallback-note cases", notes >= 5)
    }

    @Test
    fun fallbackNoteIsShownInSummary() {
        val p = SafePlace("מסגד", 32.0, 34.8, "mosque", "מסגד", source = "osm_overpass")
        val c = SafePlaces.choose(32.001, 34.8, listOf(p), worship = "mosque")!!
        assertEquals(SafePlaces.FALLBACK_NOTE_HE, c.fallbackNote)
        assertTrue(SafePlaces.summaryHe(c).contains(SafePlaces.FALLBACK_NOTE_HE))
        // ברירת המחדל (בית כנסת) לא מציעה מסגד; "none" לא מציע אף מקום תפילה
        assertNull(SafePlaces.choose(32.001, 34.8, listOf(p)))
        assertNull(SafePlaces.choose(32.001, 34.8, listOf(p), worship = "none"))
        // בלי מקום תפילה - בלי הערה
        val police = SafePlace("משטרה", 32.0, 34.8, "police", "תחנת משטרה", source = "bundled_osm")
        val pc = SafePlaces.choose(32.001, 34.8, listOf(police, p), worship = "any")!!
        assertEquals("police", pc.place.type); assertNull(pc.fallbackNote)
        assertFalse(SafePlaces.summaryHe(pc).contains(SafePlaces.FALLBACK_NOTE_HE))
    }

    @Test
    fun worshipPreferenceNormalizes() {
        assertEquals("synagogue", WorshipPref.normalize(null))
        assertEquals("synagogue", WorshipPref.normalize("sector_x"))
        assertEquals(emptySet<String>(), WorshipPref.allowedIds("none"))
        assertEquals(SafePlaces.WORSHIP_IDS, WorshipPref.allowedIds("any"))
        assertEquals(setOf("church"), WorshipPref.allowedIds("church"))
        assertTrue(WorshipPref.EXPLAIN_HE.contains("נשמר רק במכשיר הזה (מוצפן), לא נשלח לשרת ולא לאנשי הקשר."))
    }

    /** ההעדפה לא עוברת לשום קוד רשת / סנכרון / אבחון (כמו test_preference_never_sent_to_server ב-Python) */
    @Test
    fun worshipPreferenceNeverSentToServer() {
        val root = File("src/main/java/org/lifeguard/app")
        require(root.isDirectory) { "run from android/app" }
        val forbidden = listOf("api/ApiClient.kt", "api/Models.kt", "api/PrivacyModels.kt", "service/PrivacySync.kt",
            "core/Diagnostics.kt", "service/DiagnosticsCollector.kt", "core/DeviceAudit.kt", "service/IncidentManager.kt",
            "core/Fcm.kt", "fcm/FcmManager.kt")
        for (rel in forbidden) {
            val src = File(root, rel).readText(Charsets.UTF_8)
            assertFalse(rel, src.contains("worship", ignoreCase = true))
        }
    }

    @Test
    fun bundledListParsesLikePython() {
        assertEquals(fixture.getInt("bundled_usable"), bundled.size)
        assertEquals(322, bundled.size)
        assertTrue(bundled.any { it.source == "bundled_official" && it.type == "fire_station" })
        assertTrue(bundled.any { it.source == "bundled_osm" && it.type == "police" })
    }

    @Test
    fun offlineChoiceMatchesPythonOnRealList() {
        val cases = fixture.getJSONArray("cases")
        assertTrue(cases.length() >= 40)
        val bad = ArrayList<String>()
        for (i in 0 until cases.length()) {
            val c = cases.getJSONObject(i)
            val got = SafePlaces.choose(c.getDouble("lat"), c.getDouble("lon"), bundled)
            compare(c.getString("id"), c.opt("expected"), got)?.let { bad += it }
        }
        assertTrue(bad.joinToString("\n"), bad.isEmpty())
        // לפחות חלק מהנקודות בלי תוצאה (ים/מדבר) - ושם אין "המצאה"
        assertTrue((0 until cases.length()).any { cases.getJSONObject(it).isNull("expected") })
    }

    @Test
    fun walkRankingMatchesPython() {
        val cases = fixture.getJSONArray("walk_cases")
        val bad = ArrayList<String>()
        for (i in 0 until cases.length()) {
            val c = cases.getJSONObject(i)
            val lat = c.getDouble("lat"); val lon = c.getDouble("lon")
            val got = SafePlaces.choose(lat, lon, bundled) { places -> fakeWalkTable(lat, lon, places) }
            compare(c.getString("id"), c.opt("expected"), got)?.let { bad += it }
        }
        assertTrue(bad.joinToString("\n"), bad.isEmpty())
    }

    @Test
    fun syntheticBranchesMatchPython() {
        val cases = fixture.getJSONArray("synthetic")
        val bad = ArrayList<String>()
        for (i in 0 until cases.length()) {
            val c = cases.getJSONObject(i)
            // המחולל מריץ את המקרים האלה עם כל הקטגוריות (config.SAFE_PLACE_CATEGORIES) = "any"
            val got = SafePlaces.choose(c.getDouble("lat"), c.getDouble("lon"), candidates(c), c.getDouble("radius_m"), "any")
            compare(c.getString("id"), c.opt("expected"), got)?.let { bad += it }
        }
        assertTrue(bad.joinToString("\n"), bad.isEmpty())
    }

    @Test
    fun erHospitalRuleMatchesPython() {
        val cases = fixture.getJSONArray("is_er_hospital")
        assertTrue(cases.length() > 50)
        for (i in 0 until cases.length()) {
            val c = cases.getJSONObject(i)
            val name = if (c.isNull("name")) null else c.getString("name")
            val em = if (c.isNull("emergency")) null else c.getString("emergency")
            assertEquals("is_er_hospital($name, $em)", c.getBoolean("expected"), SafePlaces.isErHospital(name, em))
        }
    }

    @Test
    fun noNetworkMeansEstimateIsLabelled() {
        val c = SafePlaces.choose(32.0809, 34.7806, bundled)!!
        assertFalse(c.walkMeasured)
        assertNull(c.walkS)
        val text = SafePlaces.summaryHe(c)
        assertTrue(text, text.contains("אומדן"))
        assertTrue(text, text.contains(c.place.name))
        // מסלול אמיתי - בלי המילה "אומדן"
        val routed = SafePlaces.summaryHe(c, 840.0, 650.0)
        assertFalse(routed.contains("אומדן")); assertTrue(routed.contains("840")); assertTrue(routed.contains("11 דק'"))
    }

    @Test
    fun staleOrImpreciseOriginIsNeverShownAsLive() {
        val now = 1_800_000_000.0
        assertNull(SafePlaces.originNoteHe(now - 30, 12.0, now))
        assertTrue(SafePlaces.originNoteHe(now - 900, 12.0, now)!!.contains("לא מיקום חי"))
        assertTrue(SafePlaces.originNoteHe(now - 10, 1500.0, now)!!.contains("משוער"))
        assertNotNull(SafePlaces.originNoteHe(null, 10.0, now))
    }

    @Test
    fun navigationUrisAreWalkingAndEncoded() {
        val p = SafePlace("תחנת משטרה (מרכז)", 32.07, 34.78, "police", "תחנת משטרה", source = "bundled_osm")
        val uris = SafePlaces.navigationUris(32.08, 34.79, p)
        assertEquals(3, uris.size)
        assertTrue(uris[0], uris[0].startsWith("https://www.google.com/maps/dir/?api=1&origin=32.080000,34.790000") &&
            uris[0].contains("destination=32.070000,34.780000") && uris[0].endsWith("travelmode=walking"))
        assertTrue(uris[1], uris[1].startsWith("geo:32.070000,34.780000?q=32.070000,34.780000(") && !uris[1].contains(" "))
        assertTrue(uris[2], uris[2].contains("fossgis_osrm_foot"))
        // בלי נקודת מוצא - רק יעד
        assertFalse(SafePlaces.navigationUris(null, null, p)[0].contains("origin="))
    }

    // ---------------- עזרים ----------------
    private fun candidates(c: JSONObject): List<SafePlace> = c.getJSONArray("candidates").let { a ->
        (0 until a.length()).map { j ->
            val o = a.getJSONObject(j)
            SafePlace(o.getString("name"), o.getDouble("lat"), o.getDouble("lon"), o.getString("type"),
                o.getString("type_label"), if (o.isNull("emergency")) null else o.getString("emergency"),
                o.getString("source"))
        }
    }

    /** אותה נוסחה כמו fake_walk_table במחולל */
    private fun fakeWalkTable(lat: Double, lon: Double, places: List<SafePlace>): List<Pair<Double, Double>?> =
        places.mapIndexed { i, p ->
            if (i % 4 == 2) null
            else {
                val m = SafePlaces.haversineM(lat, lon, p.lat, p.lon) * (2.6 - ((7 * i) % 5) * 0.4)
                m to m / 1.35
            }
        }

    private fun compare(id: String, expected: Any?, got: SafePlaceChoice?): String? {
        if (expected == null || expected == JSONObject.NULL) return if (got == null) null else "$id: expected none, got ${got.place.name}"
        val e = expected as JSONObject
        if (got == null) return "$id: expected ${e.getString("name")}, got none"
        val diffs = ArrayList<String>()
        if (e.getString("name") != got.place.name) diffs += "name ${e.getString("name")} != ${got.place.name}"
        if (e.getString("type") != got.place.type) diffs += "type"
        if (e.getString("type_label") != got.place.typeLabel) diffs += "type_label"
        if (e.getString("source") != got.place.source) diffs += "source ${e.getString("source")} != ${got.place.source}"
        if (e.getDouble("lat") != got.place.lat || e.getDouble("lon") != got.place.lon) diffs += "coords"
        if (Math.abs(e.getDouble("distance_m") - got.distanceM) > 1e-6) diffs += "distance ${e.getDouble("distance_m")} != ${got.distanceM}"
        if (e.getLong("walk_m") != got.walkM) diffs += "walk_m ${e.getLong("walk_m")} != ${got.walkM}"
        if (e.getBoolean("walk_measured") != got.walkMeasured) diffs += "walk_measured"
        val es = if (e.has("walk_s")) e.getLong("walk_s") else null
        if (es != got.walkS) diffs += "walk_s $es != ${got.walkS}"
        return if (diffs.isEmpty()) null else "$id: " + diffs.joinToString("; ")
    }

    private fun strings(a: JSONArray) = (0 until a.length()).map { a.getString(it) }

    companion object {
        fun resource(name: String): String =
            SafePlacesParityTest::class.java.classLoader!!.getResourceAsStream(name)!!.use { String(it.readBytes(), Charsets.UTF_8) }

        fun bundledFile(): File {
            val p = System.getProperty("lifeguard.safeplaces.file")?.takeIf { it.isNotEmpty() }
            val f = if (p != null) File(p) else File("../../safe_places/safe_places_il.json")
            require(f.isFile) { "safe_places_il.json not found at $f" }
            return f
        }
    }
}
