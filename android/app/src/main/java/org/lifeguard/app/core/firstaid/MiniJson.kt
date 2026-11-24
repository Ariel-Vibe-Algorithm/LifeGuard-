package org.lifeguard.app.core.firstaid

/**
 * מפענח JSON קטן וטהור (בלי תלות ב-Android / org.json): אובייקט -> LinkedHashMap (שומר על סדר המפתחות כמו dict
 * של Python - חשוב לזהות החיפוש: סדר הביטויים ב-synonyms.json קובע את סדר הסכימה), מערך -> List, מספר -> Long/Double.
 * מפתח כפול: הערך האחרון גובר והמיקום נשאר של הראשון (כמו json.load). JSON לא תקין -> [JsonError].
 */
class JsonError(message: String) : IllegalArgumentException(message)

object MiniJson {
    fun parse(text: String): Any? {
        val p = Parser(text)
        p.ws()
        val v = p.value()
        p.ws()
        if (p.i != text.length) throw JsonError("Extra data at offset ${p.i}")
        return v
    }

    private class Parser(val s: String) {
        var i = 0

        fun ws() {
            while (i < s.length && (s[i] == ' ' || s[i] == '\t' || s[i] == '\n' || s[i] == '\r')) i++
        }

        fun fail(msg: String): Nothing = throw JsonError("$msg at offset $i")

        fun value(): Any? {
            if (i >= s.length) fail("Expecting value")
            return when (val c = s[i]) {
                '{' -> obj()
                '[' -> arr()
                '"' -> str()
                't' -> lit("true", true)
                'f' -> lit("false", false)
                'n' -> lit("null", null)
                else -> if (c == '-' || c in '0'..'9') num() else fail("Expecting value")
            }
        }

        fun lit(word: String, v: Any?): Any? {
            if (!s.startsWith(word, i)) fail("Expecting value")
            i += word.length
            return v
        }

        fun obj(): LinkedHashMap<String, Any?> {
            val m = LinkedHashMap<String, Any?>()
            i++ // {
            ws()
            if (i < s.length && s[i] == '}') { i++; return m }
            while (true) {
                ws()
                if (i >= s.length || s[i] != '"') fail("Expecting property name enclosed in double quotes")
                val k = str()
                ws()
                if (i >= s.length || s[i] != ':') fail("Expecting ':' delimiter")
                i++
                ws()
                m[k] = value()
                ws()
                if (i >= s.length) fail("Expecting ',' delimiter")
                when (s[i]) {
                    ',' -> i++
                    '}' -> { i++; return m }
                    else -> fail("Expecting ',' delimiter")
                }
            }
        }

        fun arr(): List<Any?> {
            val out = ArrayList<Any?>()
            i++ // [
            ws()
            if (i < s.length && s[i] == ']') { i++; return out }
            while (true) {
                ws()
                out += value()
                ws()
                if (i >= s.length) fail("Expecting ',' delimiter")
                when (s[i]) {
                    ',' -> i++
                    ']' -> { i++; return out }
                    else -> fail("Expecting ',' delimiter")
                }
            }
        }

        fun str(): String {
            i++ // "
            val sb = StringBuilder()
            while (true) {
                if (i >= s.length) fail("Unterminated string")
                val c = s[i++]
                when {
                    c == '"' -> return sb.toString()
                    c == '\\' -> {
                        if (i >= s.length) fail("Unterminated string")
                        when (val e = s[i++]) {
                            '"' -> sb.append('"'); '\\' -> sb.append('\\'); '/' -> sb.append('/')
                            'b' -> sb.append('\b'); 'f' -> sb.append('\u000C'); 'n' -> sb.append('\n')
                            'r' -> sb.append('\r'); 't' -> sb.append('\t')
                            'u' -> {
                                if (i + 4 > s.length) fail("Invalid \\uXXXX escape")
                                val hex = s.substring(i, i + 4)
                                val code = hex.toIntOrNull(16) ?: fail("Invalid \\uXXXX escape")
                                sb.append(code.toChar()); i += 4
                            }
                            else -> fail("Invalid \\escape: $e")
                        }
                    }
                    c < ' ' -> fail("Invalid control character")
                    else -> sb.append(c)
                }
            }
        }

        fun num(): Any {
            val start = i
            if (s[i] == '-') i++
            if (i >= s.length) fail("Expecting value")
            if (s[i] == '0') i++
            else if (s[i] in '1'..'9') { while (i < s.length && s[i] in '0'..'9') i++ }
            else fail("Expecting value")
            var isFloat = false
            if (i < s.length && s[i] == '.') {
                isFloat = true; i++
                if (i >= s.length || s[i] !in '0'..'9') fail("Invalid number")
                while (i < s.length && s[i] in '0'..'9') i++
            }
            if (i < s.length && (s[i] == 'e' || s[i] == 'E')) {
                isFloat = true; i++
                if (i < s.length && (s[i] == '+' || s[i] == '-')) i++
                if (i >= s.length || s[i] !in '0'..'9') fail("Invalid number")
                while (i < s.length && s[i] in '0'..'9') i++
            }
            val t = s.substring(start, i)
            return if (isFloat) t.toDouble() else (t.toLongOrNull() ?: t.toDouble())
        }
    }
}
