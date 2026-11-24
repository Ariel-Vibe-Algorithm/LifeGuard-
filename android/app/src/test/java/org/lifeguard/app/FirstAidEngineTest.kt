package org.lifeguard.app

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import org.lifeguard.app.core.firstaid.ContentSource
import org.lifeguard.app.core.firstaid.FileContentSource
import org.lifeguard.app.core.firstaid.FirstAidLibrary
import org.lifeguard.app.core.firstaid.FirstAidText
import org.lifeguard.app.core.firstaid.JsonError
import org.lifeguard.app.core.firstaid.MiniJson
import org.lifeguard.app.core.firstaid.Supplements
import java.io.File

/** מנוע העזרה הראשונה: גיל, חבילות הרחבה (מיזוג / נפילה לגרסה קודמת / התנגשויות), תוכן פגום. */
class FirstAidEngineTest {
    @get:Rule val tmp = TemporaryFolder()
    private val lib = FirstAidContent.lib
    private val pkg = "lifeguard_first_aid_supplement_02"

    // ---------------------------------------------------------------- נרמול וגיל
    @Test
    fun hebrewNormalizationAndStems() {
        assertEquals(FirstAidText.normalize("כוויה"), FirstAidText.normalize("כְּוִויָּה"))
        assertEquals("כויה", FirstAidText.normalize("כוויה"))
        assertEquals("מדא", FirstAidText.normalize("מד״א"))
        assertEquals("מדא", FirstAidText.normalize("מד\"א"))
        assertEquals("חומ", FirstAidText.normalize("חום!!"))
        assertTrue("ילד" in FirstAidText.stems("והילדים"))
        assertTrue("נחנק" in FirstAidText.stems("נחנקת"))
        assertEquals(1, FirstAidText.editDistance("החיאה", "החייאה"))
        assertEquals(3, FirstAidText.editDistance("abcdef", "xyzdef"))
        assertEquals("", FirstAidText.normalize(null))
    }

    @Test
    fun ageDetectionDoesNotGuess() {
        assertEquals("infant", FirstAidText.detectAge("תינוק נחנק"))
        assertEquals("infant", FirstAidText.detectAge("בן 4 חודשים לא נושם"))
        assertEquals("child", FirstAidText.detectAge("ילד בן 3 נחנק"))
        assertEquals("adult", FirstAidText.detectAge("אבא שלי לא נושם"))
        assertNull(FirstAidText.detectAge("נחנק"))
        assertNull(FirstAidText.detectAge("בן 40"))
    }

    @Test
    fun ageFamiliesNeverShowAdultGuideForInfant() {
        val adultOnly = setOf("choking_adult_child", "cpr_adult")
        for (q in listOf("נחנק", "חנק", "לא נושם", "לא נושמת", "הפסיק לנשום", "החיאה", "החייאה", "choking", "اختناق",
                "אוכל תקוע", "דום לב", "תינוק נחנק", "תינוק לא נושם")) {
            val ids = lib.search(q, age = "infant").results.map { it.id }
            assertTrue("$q -> $ids", ids.none { it in adultOnly })
        }
        val inf = lib.search("תינוק נחנק").results.map { it.id }
        assertEquals("choking_infant", inf[0])
        assertFalse("choking_adult_child" in inf)
        val adult = lib.search("נחנק", age = "adult").results.map { it.id }
        assertTrue("choking_adult_child" in adult && "choking_infant" !in adult)
        assertEquals("cpr_child_infant", lib.search("תינוק לא נושם").results[0].id)
        assertFalse("cpr_child_infant" in lib.search("מבוגר לא נושם").results.map { it.id })
        // כל מדריך במשפחה + תינוק -> מדריך התינוק; אף פעם לא המבוגר
        for (fam in FirstAidLibrary.AGE_FAMILIES.values) for (gid in fam.values) {
            assertFalse(lib.resolve(gid, "infant") in adultOnly)
            assertTrue(lib.needsAge(gid, null))
            assertFalse(lib.needsAge(gid, "infant"))
        }
        assertEquals("choking_infant", lib.resolve("choking_adult_child", "infant"))
        assertEquals("cpr_adult", lib.resolve("cpr_child_infant", "adult"))
        assertTrue(lib.ageNote("cpr_child_infant", "infant")!!.contains("בתינוק"))
        assertTrue(lib.ageNote("severe_bleeding", "infant")!!.contains("101"))
        assertNull(lib.ageNote("severe_bleeding", "adult"))
        assertNull(lib.ageNote("gas_leak_home", "infant"))          // חבילת הרחבה בלי קבוצת גיל - לא מנחשים
        assertEquals("child_fever", lib.search("חום לתינוק").results[0].id)
    }

    @Test
    fun emptyQueriesReturnNothing() {
        for (q in listOf("", "   ", "!!", "א")) assertTrue(q, lib.search(q).results.isEmpty())
        assertTrue(lib.search("xyzqq").results.isEmpty())
        assertTrue(lib.search("אני עצוב").results.isEmpty())
    }

    // ---------------------------------------------------------------- חבילת הרחבה
    @Test
    fun supplementIsAdditiveVerbatimAndLabelled() {
        val base = FirstAidLibrary.load(FileContentSource(FirstAidContent.dir), supplements = false)
        assertEquals(39, base.guides.size)
        assertEquals(base.guides.size + 43, lib.guides.size)
        for (g in base.guides) assertEquals(g, lib.guide(g.id))           // המאגר המקורי בדיוק כפי שהיה
        // התנגשות: המקור גובר
        assertEquals(setOf("frostbite", "heat_exhaustion"), lib.conflicts.map { it.id }.toSet())
        assertNull(lib.guide("frostbite")!!.packageId)
        assertTrue(lib.guide("frostbite")!!.medicalReviewStatus.startsWith("REQUIRES_REVIEW"))

        val raw = JSONObject(File(FirstAidContent.dir, "content/supplements/$pkg/1.2.0/first_aid_supplement_he.json").readText())
            .getJSONArray("guides")
        for (i in 0 until raw.length()) {
            val r = raw.getJSONObject(i)
            val id = r.getString("id")
            val g = lib.guide(id)!!
            if (id == "frostbite" || id == "heat_exhaustion") continue
            assertEquals(r.getJSONArray("actions").toList(), g.whatToDo)        // טקסט כלשונו
            assertEquals(r.getJSONArray("do_not").toList(), g.doNot)
            assertEquals(r.getString("emergency"), g.callEmergencyNow)
            assertEquals("pending_medical_review", g.medicalReviewStatus)
            assertEquals("1.2.0", g.packageVersion)
            assertTrue(g.ageGroups.isEmpty())
            assertTrue(g.setting.all { it.length > 1 })
            assertTrue(lib.sourcesFor(id).all { it.url != null && it.title.isNotEmpty() })
            val top = lib.search(r.getString("title")).results.take(3).map { it.id }
            assertTrue("${r.getString("title")} -> $top", id in top)
        }
        for (gid in listOf("gas_leak_home", "home_fire_escape", "active_attack_safety", "israel_rocket_alert_shelter", "flash_flood_wadi")) {
            val g = lib.guide(gid)!!
            assertTrue(gid, g.isSafetyProcedure && g.category.contains("לא טיפול רפואי") && g.authorityReview != null)
        }
        assertEquals("medical", lib.guide("scorpion_sting_israel")!!.kind)
        assertEquals("פיקוד העורף", lib.guide("israel_rocket_alert_shelter")!!.officialDynamic!!.authority)
        assertEquals("animal_bite", lib.guide("animal_bite_rabies_israel")!!.overlaps)
        assertTrue(lib.guide("button_battery_ingestion")!!.doNot.joinToString(" ").contains("דבש"))
        assertEquals("100", lib.emergencyNumbers["police"])
        assertEquals("102", lib.emergencyNumbers["fire_and_rescue"])
        assertEquals("04-7771900", lib.emergencyNumbers["poison_center"])
        assertEquals("101", lib.mda)
    }

    private fun JSONArray.toList(): List<String> = (0 until length()).map { getString(it) }

    /** עותק זמני של first_aid/ (התוכן המקורי לא נוגעים בו) */
    private fun copyContent(): File {
        val dest = tmp.newFolder("first_aid")
        File(FirstAidContent.dir, "content").copyRecursively(File(dest, "content"))
        File(FirstAidContent.dir, "synonyms.json").copyTo(File(dest, "synonyms.json"))
        File(FirstAidContent.dir, "supplement_policy.json").copyTo(File(dest, "supplement_policy.json"))
        return dest
    }

    private fun writeActive(root: File, active: String, history: List<String>) {
        File(root, "content/supplements/active.json").writeText(
            JSONObject().put("packages", JSONObject().put(pkg, JSONObject().put("active", active).put("history", JSONArray(history)))).toString())
    }

    @Test
    fun corruptActiveVersionFallsBackAndBaseSurvives() {
        val root = copyContent()
        val bad = File(root, "content/supplements/$pkg/1.2.1").apply { mkdirs() }
        File(bad, Supplements.SUPP_JSON).writeText("{broken")
        writeActive(root, "1.2.1", listOf("1.2.0", "1.2.1"))
        val l1 = FirstAidLibrary.load(FileContentSource(root))
        assertEquals("1.2.0", l1.packages[0].version)
        assertEquals(82, l1.guides.size)
        assertTrue(l1.errors.toString(), l1.errors.any { "1.2.1" in it && "גרסה קודמת" in it })

        File(root, "content/supplements/$pkg/1.2.0").deleteRecursively()       // גם הקודמת חסרה
        val l2 = FirstAidLibrary.load(FileContentSource(root))
        assertEquals(39, l2.guides.size)
        assertTrue(l2.search("חנק").results.isNotEmpty())
        assertTrue(l2.packages.isEmpty())

        File(root, "content/supplements/active.json").writeText("garbage")
        assertEquals(39, FirstAidLibrary.load(FileContentSource(root)).guides.size)
        File(root, "content/supplements/active.json").delete()
        assertEquals(39, FirstAidLibrary.load(FileContentSource(root)).guides.size)
    }

    @Test
    fun newerVersionIsUsedAndDuplicateIdsNeverOverrideBase() {
        val root = copyContent()
        val src = File(root, "content/supplements/$pkg/1.2.0")
        val v13 = File(root, "content/supplements/$pkg/1.3.0")
        src.copyRecursively(v13)
        val data = JSONObject(File(v13, Supplements.SUPP_JSON).readText())
        val guides = data.getJSONArray("guides")
        guides.getJSONObject(0).put("title", "שינוי בגרסה 1.3.0")
        // מזהה שקיים במאגר הבסיס - לא דורס
        guides.put(JSONObject(guides.getJSONObject(1).toString()).put("id", "choking_infant").put("title", "לא אמור להופיע"))
        // מדריך פגום בתוך החבילה - מדולג ומדווח, השאר נטענים
        guides.put(JSONObject().put("id", "broken_one").put("title", "פגום"))
        File(v13, Supplements.SUPP_JSON).writeText(data.toString())
        writeActive(root, "1.3.0", listOf("1.2.0", "1.3.0"))
        val l = FirstAidLibrary.load(FileContentSource(root))
        assertEquals("1.3.0", l.packages[0].version)
        assertEquals("שינוי בגרסה 1.3.0", l.guide(guides.getJSONObject(0).getString("id"))!!.title)
        assertNull(l.guide("choking_infant")!!.packageId)                        // המקור נשאר
        assertFalse(l.guide("choking_infant")!!.title == "לא אמור להופיע")
        assertTrue(l.conflicts.any { it.id == "choking_infant" && it.version == "1.3.0" })
        assertTrue(l.errors.any { "broken_one" in it })
        assertEquals(82, l.guides.size)
        // rollback = active חוזר ל-1.2.0
        writeActive(root, "1.2.0", listOf("1.2.0", "1.3.0"))
        val back = FirstAidLibrary.load(FileContentSource(root))
        assertFalse(back.guide(guides.getJSONObject(0).getString("id"))!!.title == "שינוי בגרסה 1.3.0")
        // גרסה בלי אף מדריך תקין -> לא נטענת, נופלים להיסטוריה
        File(v13, Supplements.SUPP_JSON).writeText(JSONObject().put("guides", JSONArray().put(JSONObject().put("id", "x"))).toString())
        writeActive(root, "1.3.0", listOf("1.2.0", "1.3.0"))
        assertEquals("1.2.0", FirstAidLibrary.load(FileContentSource(root)).packages[0].version)
    }

    @Test
    fun supplementHelpersMatchPython() {
        assertEquals(listOf("בית", "רחוב", "טיול"), Supplements.normalizeSetting("בית/ רחוב ,טיול"))
        assertEquals(listOf("a", "b"), Supplements.normalizeSetting(listOf("a", " ", 3, "b ")))
        assertEquals(emptyList<String>(), Supplements.normalizeSetting(5))
        assertEquals("www.nhs.uk", Supplements.hostOf("https://www.NHS.uk:443/conditions/x?y#z"))
        assertNull(Supplements.hostOf("not a url"))
        assertEquals(mapOf("https://a.org/x" to "Org – thing"), Supplements.parseSourcesMd("intro\n- Org – thing: https://a.org/x \n- no url here"))
        assertEquals(listOf("חסר/שגוי keywords", "חסר/שגוי recognition", "חסר/שגוי actions", "חסר/שגוי do_not",
            "חסר/שגוי emergency", "חסר/שגוי sources"),
            Supplements.validateRaw(mapOf("id" to "a", "title" to "b", "urgency" to "high")))
    }

    // ---------------------------------------------------------------- תוכן פגום
    @Test
    fun malformedGuidesAreSkippedSafely() {
        val root = copyContent()
        val good = JSONObject(File(root, FirstAidLibrary.GUIDES_PATH).readText())
        val gs = good.getJSONArray("guides")
        val g0 = gs.getJSONObject(0)
        val bad = JSONArray()
            .put(g0).put(JSONObject(g0.toString())).put(JSONObject().put("id", "x")).put("junk")
            .put(JSONObject(gs.getJSONObject(2).toString()).put("urgency", "soon"))
            .put(JSONObject(gs.getJSONObject(3).toString()).put("age_groups", JSONArray().put("martian")))
            .put(gs.getJSONObject(4))
        good.put("guides", bad)
        File(root, FirstAidLibrary.GUIDES_PATH).writeText(good.toString())
        val l = FirstAidLibrary.load(FileContentSource(root), supplements = false)
        assertEquals(setOf(g0.getString("id"), gs.getJSONObject(4).getString("id")), l.guides.map { it.id }.toSet())
        assertEquals(l.errors.toString(), 5, l.errors.size)
        assertTrue(l.errors.any { "כפול" in it })

        File(root, FirstAidLibrary.GUIDES_PATH).writeText("{not json")
        val broken = FirstAidLibrary.load(FileContentSource(root), supplements = false)
        assertTrue(broken.guides.isEmpty() && broken.errors.isNotEmpty())
        assertTrue(broken.search("חנק").results.isEmpty())
        assertEquals("101", broken.emergencyNumbers["mda"])
    }

    @Test
    fun missingOrUnreadableContentNeverCrashes() {
        val empty = FirstAidLibrary.load(FileContentSource(tmp.newFolder("nothing")))
        assertTrue(empty.guides.isEmpty())
        assertEquals(3, empty.errors.size)                      // מדריכים, מקורות, מילים נרדפות
        assertEquals("101", empty.mda)
        assertTrue(empty.disclaimer.contains("101"))
        assertTrue(empty.search("חנק").results.isEmpty())

        val root = copyContent()
        File(root, FirstAidLibrary.GUIDES_PATH).writeBytes(byteArrayOf(0x7B, 0xC3.toByte(), 0x28, 0x7D))   // UTF-8 לא תקין
        File(root, FirstAidLibrary.SYNONYMS_PATH).writeText("[1,2]")
        File(root, Supplements.POLICY_PATH).writeText("{\"packages\": 5}")
        val l = FirstAidLibrary.load(FileContentSource(root))
        // הבסיס לא נטען; החבילה עדיין נטענת (מדיניות פגומה -> ברירות מחדל); אין קריסה
        assertTrue(l.errors.any { "קובץ המדריכים" in it })
        assertEquals(45, l.guides.size)
        assertEquals("101", l.mda)

        // מקור שזורק שגיאה לא צפויה
        val throwing = object : ContentSource { override fun read(path: String): String = throw IllegalStateException("boom") }
        val t = FirstAidLibrary.safeLoad(throwing)
        assertTrue(t.guides.isEmpty() && t.errors.isNotEmpty())
    }

    @Test
    fun miniJsonKeepsOrderAndRejectsGarbage() {
        @Suppress("UNCHECKED_CAST")
        val m = MiniJson.parse("""{"b":1,"a":[true,null,"x\u05d0\n"],"c":{"z":1.5,"b":2},"b":3}""") as Map<String, Any?>
        assertEquals(listOf("b", "a", "c"), m.keys.toList())
        assertEquals(3L, m["b"])
        assertEquals(listOf(true, null, "xא\n"), m["a"])
        for (bad in listOf("", "{", "{\"a\":}", "[1,]", "tru", "{\"a\":1} x", "\"\\q\"", "01")) {
            var threw = false
            try { MiniJson.parse(bad) } catch (e: JsonError) { threw = true }
            assertTrue(bad, threw)
        }
        assertNotNull(MiniJson.parse(" [ ] "))
    }
}
