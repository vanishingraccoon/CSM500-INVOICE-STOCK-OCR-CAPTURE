package com.invoicecapture.domain.pairing

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.intOrNull
import java.net.URI
import java.net.URISyntaxException

/** The pairing QR code shown by the portal's /pair page (contract §1). */
data class PairingQr(val server: String, val pairCode: String, val shop: String?)

sealed interface QrParse {
    data class Ok(val qr: PairingQr) : QrParse
    data class Invalid(val reason: String) : QrParse
}

object PairingCodes {
    const val ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    const val LENGTH = 6
    const val QR_VERSION = 1

    /** Typed codes: drop spaces and dashes, upper-case. */
    fun normaliseCode(input: String): String =
        input.filterNot { it.isWhitespace() || it == '-' }.uppercase()

    fun isValidCode(code: String): Boolean = code.length == LENGTH && code.all { it in ALPHABET }

    /**
     * A laptop address as the base URL, e.g. "192.168.1.20:5000" → "http://192.168.1.20:5000".
     * Only http/https; no query or fragment; no trailing slash. Null when it is not usable.
     */
    fun normaliseServer(input: String): String? {
        var text = input.trim()
        if (text.isEmpty()) return null
        if (!text.contains("://")) text = "http://$text"
        val uri = try {
            URI(text)
        } catch (e: URISyntaxException) {
            return null
        }
        val scheme = uri.scheme?.lowercase() ?: return null
        if (scheme != "http" && scheme != "https") return null
        val host = uri.host?.lowercase()
        if (host.isNullOrEmpty()) return null
        if (uri.rawQuery != null || uri.rawFragment != null) return null
        val port = when {
            uri.port == -1 -> ""
            uri.port in 1..65535 -> ":${uri.port}"
            else -> return null
        }
        val path = (uri.rawPath ?: "").trimEnd('/')
        return "$scheme://$host$port$path"
    }

    /** Parses and checks the QR JSON: `{"v": 1, "server": "...", "pair_code": "K7Q2M9", "shop": "eastgate"}`. */
    fun parseQr(raw: String): QrParse {
        val obj = try {
            Json.parseToJsonElement(raw.trim()) as? JsonObject
        } catch (e: IllegalArgumentException) {
            null
        } ?: return QrParse.Invalid("This is not a pairing code from the laptop.")

        val version = (obj["v"] as? JsonPrimitive)?.intOrNull
            ?: return QrParse.Invalid("This is not a pairing code from the laptop.")
        if (version > QR_VERSION) {
            return QrParse.Invalid("This code is for a newer version of the app. Update the app, then scan again.")
        }
        if (version < 1) return QrParse.Invalid("This is not a pairing code from the laptop.")

        val serverRaw = obj.string("server") ?: return QrParse.Invalid("The code has no laptop address.")
        val server = normaliseServer(serverRaw)
            ?: return QrParse.Invalid("The laptop address in the code is not usable: $serverRaw")

        val codeRaw = obj.string("pair_code") ?: return QrParse.Invalid("The code has no pairing code.")
        val code = normaliseCode(codeRaw)
        if (!isValidCode(code)) return QrParse.Invalid("The pairing code in the QR code is not valid.")

        return QrParse.Ok(PairingQr(server = server, pairCode = code, shop = obj.string("shop")))
    }

    private fun JsonObject.string(key: String): String? =
        (this[key] as? JsonPrimitive)?.takeIf { it.isString }?.content
}
