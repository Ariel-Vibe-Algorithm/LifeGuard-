package org.lifeguard.app.core

import java.net.URI

/**
 * מדיניות כתובת שרת: HTTPS חובה. HTTP מותר רק לשרת מקומי לפיתוח (אמולטור 10.0.2.2 / localhost).
 * אותה רשימה מוגדרת גם ב-res/xml/network_security_config.xml (הגנה כפולה).
 */
object UrlPolicy {
    val LOCAL_HOSTS = setOf("localhost", "127.0.0.1", "10.0.2.2", "::1", "[::1]")

    sealed class Result {
        data class Ok(val baseUrl: String) : Result()
        data class Error(val messageHe: String) : Result()
    }

    fun normalize(input: String?): Result {
        var s = (input ?: "").trim()
        if (s.isEmpty()) return Result.Error("יש להזין כתובת שרת")
        if (!s.contains("://")) {
            val hostGuess = s.substringBefore('/').substringBeforeLast(':').lowercase()
            s = (if (hostGuess in LOCAL_HOSTS) "http://" else "https://") + s
        }
        val uri = try { URI(s) } catch (e: Exception) { return Result.Error("כתובת לא תקינה") }
        val scheme = uri.scheme?.lowercase() ?: return Result.Error("כתובת לא תקינה")
        val host = uri.host?.lowercase() ?: return Result.Error("כתובת לא תקינה")
        if (scheme != "https" && scheme != "http") return Result.Error("רק http/https")
        if (uri.rawUserInfo != null) return Result.Error("אסור לכלול שם משתמש/סיסמה בכתובת")
        if (uri.rawQuery != null || uri.rawFragment != null) return Result.Error("כתובת לא תקינה")
        if (scheme == "http" && host !in LOCAL_HOSTS) {
            return Result.Error("חובה HTTPS (חיבור לא מוצפן מותר רק לשרת מקומי לבדיקות)")
        }
        val port = if (uri.port > 0) ":${uri.port}" else ""
        var path = (uri.rawPath ?: "").trimEnd('/')
        if (path.endsWith("/v1")) path = path.removeSuffix("/v1")
        val hostPart = if (host.contains(':') && !host.startsWith("[")) "[$host]" else host
        return Result.Ok("$scheme://$hostPart$port$path")
    }

    fun isAllowed(url: String): Boolean = normalize(url) is Result.Ok
}
