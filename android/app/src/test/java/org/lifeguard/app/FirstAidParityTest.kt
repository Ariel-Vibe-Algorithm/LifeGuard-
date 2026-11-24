package org.lifeguard.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.lifeguard.app.core.firstaid.FileContentSource
import org.lifeguard.app.core.firstaid.FirstAidLibrary
import org.lifeguard.app.core.firstaid.FirstAidText
import org.lifeguard.app.core.firstaid.MiniJson
import java.io.File

/** התוכן האמיתי מ-first_aid/ במאגר (נתיב מ-build.gradle.kts: lifeguard.firstaid.dir) */
object FirstAidContent {
    val dir: File by lazy {
        val p = System.getProperty("lifeguard.firstaid.dir")?.takeIf { it.isNotEmpty() }
        val f = if (p != null) File(p) else File("../../first_aid")
        require(File(f, "content/first_aid_guides_he.json").isFile) { "first_aid content not found at $f" }
        f
    }
    val lib: FirstAidLibrary by lazy { FirstAidLibrary.load(FileContentSource(dir)) }

    fun resource(name: String): String =
        FirstAidContent::class.java.classLoader!!.getResourceAsStream(name)!!.use { String(it.readBytes(), Charsets.UTF_8) }
}

/**
 * תאימות מלאה למימוש ב-Python (first_aid/library.py + supplements.py): first_aid_parity.json נוצר מהמימוש המקורי
 * על אותו תוכן. כל שאילתה: המדריך המוביל, הגיל שזוהה, ambiguous, ורשימת המזהים **לפי הסדר**. וגם פלט הנרמול.
 */
class FirstAidParityTest {
    @Suppress("UNCHECKED_CAST")
    private val fixture = MiniJson.parse(FirstAidContent.resource("first_aid_parity.json")) as Map<String, Any?>
    private val lib = FirstAidContent.lib

    @Test
    fun guideCountMatchesPython() {
        assertEquals((fixture["guides"] as Number).toInt(), lib.guides.size)
        assertEquals(82, lib.guides.size)
        assertEquals(listOf("frostbite", "heat_exhaustion"), lib.conflicts.map { it.id }.sorted())
        assertEquals(1, lib.packages.size)
        val p = lib.packages[0]
        assertEquals(listOf("lifeguard_first_aid_supplement_02", "1.2.0", 43, 45, "2026-11-07"),
            listOf(p.packageId, p.version, p.added, p.total, p.updated))
        assertTrue(lib.errors.toString(), lib.errors.isEmpty())
    }

    @Test
    fun normalizationMatchesPythonExactly() {
        @Suppress("UNCHECKED_CAST")
        val pairs = fixture["normalize"] as Map<String, String>
        assertTrue(pairs.size >= 7)
        for ((input, expected) in pairs) assertEquals("normalize($input)", expected, FirstAidText.normalize(input))
    }

    @Test
    fun everyQueryMatchesPython() {
        @Suppress("UNCHECKED_CAST")
        val cases = fixture["cases"] as List<Map<String, Any?>>
        assertEquals(54, cases.size)
        val mismatches = ArrayList<String>()
        for (c in cases) {
            val q = c["query"] as String
            val r = lib.search(q)
            val ids = r.results.map { it.id }
            val top = ids.firstOrNull()
            val problems = ArrayList<String>()
            if (top != c["top"]) problems += "top=$top expected ${c["top"]}"
            if (r.age != c["age"]) problems += "age=${r.age} expected ${c["age"]}"
            if (r.ambiguous != c["ambiguous"]) problems += "ambiguous=${r.ambiguous} expected ${c["ambiguous"]}"
            if (ids != c["ids"]) problems += "ids=$ids expected ${c["ids"]} scores=${r.results.map { it.score }}"
            if (problems.isNotEmpty()) mismatches += "'$q': " + problems.joinToString("; ")
        }
        assertTrue("Python parity mismatches (${mismatches.size}):\n" + mismatches.joinToString("\n"), mismatches.isEmpty())
    }
}
