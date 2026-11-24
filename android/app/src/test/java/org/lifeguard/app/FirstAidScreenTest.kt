package org.lifeguard.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.lifeguard.app.core.firstaid.FirstAidLibrary
import org.lifeguard.app.core.firstaid.FirstAidScreenModel
import org.lifeguard.app.core.firstaid.FirstAidView
import org.lifeguard.app.core.firstaid.ResultItem
import java.io.File
import java.util.TimeZone

/** לוגיקת המסך (ברמת ה-view-model) + הוכחה שאין רשת בנתיב העזרה הראשונה. */
class FirstAidScreenTest {
    private val lib = FirstAidContent.lib
    private fun model() = FirstAidScreenModel(lib)

    @Test
    fun landingIsSearchFirstWithoutCatalog() {
        val m = model()
        val v = m.view
        assertTrue(v is FirstAidView.Landing)
        v as FirstAidView.Landing
        // אין רשימת מצבים: מסך הפתיחה מכיל רק שני משפטי הסבר, ואף כותרת מדריך לא מופיעה בהם
        assertNull(v.loadProblem)
        for (g in lib.guides) assertFalse(g.title, v.hint.contains(g.title) || v.hint2.contains(g.title))
        assertEquals("מה קרה? חפש מצב חירום או תסמין...", FirstAidScreenModel.PLACEHOLDER)
        // תוצאות רק אחרי הקלדה
        assertTrue(m.onQuery("") is FirstAidView.Landing)
        assertTrue(m.onQuery("   ") is FirstAidView.Landing)
        assertTrue(m.onQuery("דימום") is FirstAidView.Results)
        assertTrue(m.clear() is FirstAidView.Landing)
        assertEquals("", m.query)
        // מאגר ריק -> עדיין מסך חיפוש + הודעה (101 בכפתורי החיוג)
        val empty = FirstAidScreenModel(FirstAidLibrary(emptyList()))
        assertNotNull((empty.view as FirstAidView.Landing).loadProblem)
        assertEquals("101", empty.dialButtons(false).single().number)
    }

    @Test
    fun noMatchShowsFallbackAndSuggestionsThatWork() {
        val m = model()
        val v = m.onQuery("xyzqq") as FirstAidView.NoMatch
        assertEquals("לא מצאנו מדריך מתאים. במקרה של סכנת חיים התקשרו 101 או בקשו ממישהו להזעיק עזרה.", v.text)
        assertEquals(listOf("חוסר הכרה", "קושי בנשימה", "דימום חמור"), v.suggestions)
        for (s in v.suggestions) {
            val r = model().suggestion(s)
            assertTrue("$s -> $r", r is FirstAidView.Results && r.items.isNotEmpty())
        }
        assertTrue(m.onQuery("אני עצוב") is FirstAidView.NoMatch)
    }

    @Test
    fun ambiguousResultsAndFamilyAgeChooserBeforeSteps() {
        val m = model()
        val r = m.onQuery("נחנק") as FirstAidView.Results
        assertTrue(r.ambiguous)
        assertEquals("כמה מצבים מתאימים - בחרו את המתאים. (זו לא אבחנה)", r.status)
        // גיל לא ידוע: כרטיס בחירת גיל אחד במקום שני מדריכי החנק - אין צעדים לפני שבוחרים
        assertEquals(1, r.items.size)
        val fam = r.items[0] as ResultItem.FamilyChooser
        assertEquals("חנק", fam.title)
        assertEquals(listOf("adult", "child", "infant"), fam.choices.map { it.first })
        val g = m.chooseAge("infant", fam.anyId) as FirstAidView.GuideView
        assertEquals("choking_infant", g.page.guide.id)
        assertEquals("infant", m.age)
        // פתיחה ישירה של מדריך במשפחה בלי גיל -> שאלה לפני הצעדים
        val m2 = model()
        m2.onQuery("שבץ")
        assertTrue(m2.openGuide("cpr_adult") is FirstAidView.AgePrompt)
        assertNull(m2.current)
        val cpr = m2.chooseAge("child", "cpr_adult") as FirstAidView.GuideView
        assertEquals("cpr_child_infant", cpr.page.guide.id)
        assertTrue(cpr.page.ageNote!!.contains("בילד"))
    }

    @Test
    fun ageDetectedFromQueryIsKeptAndInfantNeverSeesAdultGuide() {
        val m = model()
        val r = m.onQuery("תינוק נחנק") as FirstAidView.Results
        assertEquals("infant", m.age)
        val ids = r.items.filterIsInstance<ResultItem.Card>().map { it.result.id }
        assertEquals("choking_infant", ids[0])
        assertFalse("choking_adult_child" in ids)
        // מדריך פתוח + שינוי גיל -> המדריך המתאים לגיל
        val g = m.openGuide("choking_infant") as FirstAidView.GuideView
        assertEquals("choking_infant", g.page.guide.id)
        assertEquals("choking_adult_child", (m.setAge("adult") as FirstAidView.GuideView).page.guide.id)
        assertEquals("choking_infant", (m.setAge("infant") as FirstAidView.GuideView).page.guide.id)
        assertTrue(m.setAge(null) is FirstAidView.AgePrompt)        // "לא ידוע" -> שואלים שוב לפני הצעדים
        // תינוק נבחר במסך: אף חיפוש לא מציג את מדריך המבוגר
        val m2 = model()
        m2.setAge("infant")
        for (q in listOf("נחנק", "לא נושם", "החיאה", "choking")) {
            val v = m2.onQuery(q)
            val shown = (v as? FirstAidView.Results)?.items.orEmpty().filterIsInstance<ResultItem.Card>().map { it.result.id }
            assertTrue("$q -> $shown", "choking_adult_child" !in shown && "cpr_adult" !in shown)
        }
    }

    @Test
    fun guidePageOrderAndSafetyFields() {
        val m = model()
        m.onQuery("צבע אדום")
        val p = (m.openGuide("israel_rocket_alert_shelter") as FirstAidView.GuideView).page
        assertTrue(p.safetyLabel!!.startsWith("נוהל בטיחות - לא טיפול רפואי"))
        assertTrue(p.safetyLabel!!.contains("פיקוד העורף"))
        assertTrue(p.officialNote!!.startsWith("המקור המחייב: פיקוד העורף."))
        assertTrue(p.ageLine.contains("לא צוין בחבילה"))
        assertEquals(listOf("what_to_do", "do_not", "red_flags", "recognition"), p.sections.map { it.key }.take(4))
        assertTrue(p.sections[0].numbered)
        assertEquals(p.guide.whatToDo, p.sections[0].items)                 // כלשונו
        assertEquals(listOf(p.guide.callEmergencyNow), p.sections[2].items)
        assertEquals(listOf("mda", "police", "fire_and_rescue", "poison_center"), p.dial.map { it.key })
        assertEquals("101", p.dial[0].number)
        assertTrue(p.metaLine.contains("חבילת הרחבה lifeguard_first_aid_supplement_02 גרסה 1.2.0"))
        assertTrue(p.canGoBack)
        assertTrue(m.back() is FirstAidView.Results)
        assertNull(m.back())

        val poison = model().guidePage(lib.guide("poison_ingestion")!!, null)
        assertTrue(poison.poisonCenter!!.contains("04-7771900"))
        assertNull(poison.safetyLabel)
        assertEquals(listOf("mda", "poison_center"), poison.dial.map { it.key })
        assertTrue(poison.metaLine.contains("מאגר בסיס גרסה 1.0.0"))
        val related = model().guidePage(lib.guide("smoke_inhalation")!!, null)
        assertEquals("burns_thermal", related.related!!.first)
        assertNull(model().guidePage(lib.guide("stroke")!!, null).poisonCenter)
    }

    @Test
    fun everyGuideShowsReviewStatusAndSources() {
        val m = model()
        for (g in lib.guides) {
            val p = m.guidePage(g, null)
            assertTrue(g.id, p.reviewStatus.startsWith("סטטוס: ") && p.reviewStatus.length > 10)
            assertFalse(g.id, p.reviewStatus.contains("REQUIRES_REVIEW") || p.reviewStatus.contains("pending_medical"))
            assertTrue(g.id, p.metaLine.contains(g.contentUpdated) && p.metaLine.contains(g.id))
            assertTrue(g.id, p.sources.isNotEmpty())
            assertEquals("mda", p.dial[0].key)
            assertTrue(g.id, p.emergencyBanner.isNotBlank() && p.sections[0].items.isNotEmpty())
        }
    }

    @Test
    fun lastLocationFormatting() {
        val s = FirstAidScreenModel.formatLastLocation(32.0853, 34.78181, true, 12.7, 1_700_000_000.0, 1_700_000_300.0,
            TimeZone.getTimeZone("UTC"))
        assertEquals("המיקום האחרון שנשמר במכשיר: 32.08530, 34.78181 · GPS · דיוק ~12 מ' · נמדד ב-22:13 (לפני 5 דק')", s)
        val old = FirstAidScreenModel.formatLastLocation(1.0, 2.0, false, null, null, 0.0)
        assertTrue(old.contains("משוער (לא GPS)") && old.contains("זמן המדידה לא ידוע"))
    }

    @Test
    fun searchIsFastEnoughOnJvm() {
        val qs = listOf("נחנק", "יורד לו דם מהאף", "קוצר נשימה", "חום לתינוק", "gas leak", "קיבל מכה בראש")
        qs.forEach { lib.search(it) }      // חימום
        val t0 = System.nanoTime()
        repeat(3) { qs.forEach { lib.search(it) } }
        val perQueryMs = (System.nanoTime() - t0) / 1e6 / (3 * qs.size)
        println("first aid search: %.1f ms/query".format(perQueryMs))
        assertTrue("search too slow: $perQueryMs ms", perQueryMs < 250)
    }

    // ---------------------------------------------------------------- אפס רשת
    private val srcRoot = File("src/main/java/org/lifeguard/app")

    @Test
    fun engineHasNoNetworkOrAndroidDependency() {
        val dir = File(srcRoot, "core/firstaid")
        val files = dir.listFiles { f -> f.name.endsWith(".kt") }.orEmpty()
        assertTrue("engine sources not found in ${dir.absolutePath}", files.size >= 5)
        val forbidden = listOf("java.net", "javax.net", "okhttp", "okio", "android.", "androidx.", "HttpURLConnection",
            "Socket", "URL(", "URI(", "org.lifeguard.app.api", "LG.")
        for (f in files) {
            val text = f.readText()
            val imports = text.lines().filter { it.startsWith("import ") }
            for (imp in imports) {
                val target = imp.removePrefix("import ").trim()
                assertTrue("${f.name}: $imp", target.startsWith("java.io.") || target.startsWith("java.nio.") ||
                    target.startsWith("java.text.") || target.startsWith("java.util.") || target.startsWith("java.math.") ||
                    target.startsWith("kotlin.") || target.startsWith("org.lifeguard.app.core.firstaid."))
            }
            for (bad in forbidden) assertFalse("${f.name} mentions $bad", text.contains(bad))
        }
        // גם בזמן ריצה: מחלקות המנוע לא נטענו עם תלות ב-java.net
        val loaded = listOf(FirstAidLibrary::class.java, FirstAidScreenModel::class.java)
        for (c in loaded) for (m in c.declaredMethods) {
            assertFalse("${c.simpleName}.${m.name}", (m.parameterTypes.toList() + m.returnType).any { it.name.startsWith("java.net.") })
        }
    }

    @Test
    fun screenUsesNoServerLoginOrNetwork() {
        val text = File(srcRoot, "ui/FirstAidActivity.kt").readText()
        for (bad in listOf("LG.api", "ApiClient", "okhttp", "java.net", "HttpURLConnection", "LG.store", "isLoggedIn",
                "SpeechRecognizer", "ACTION_CALL\"", "Intent.ACTION_CALL)", "CALL_PHONE")) {
            assertFalse("FirstAidActivity mentions $bad", text.contains(bad))
        }
        assertTrue(text.contains("Intent.ACTION_DIAL"))
        assertFalse(Regex("ACTION_CALL\\b").containsMatchIn(text))
    }
}
