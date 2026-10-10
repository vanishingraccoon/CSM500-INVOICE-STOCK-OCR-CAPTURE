package com.invoicecapture.domain.util

import java.io.File
import java.math.BigDecimal
import java.math.RoundingMode
import java.security.MessageDigest

object Decimals {
    /**
     * [value] rounded half-up to [maxDecimals] places, keeping at least [minDecimals] places
     * (trailing zeros beyond that are dropped). Never uses exponent notation. NaN/∞ print as 0.
     */
    fun format(value: Double, maxDecimals: Int, minDecimals: Int = maxDecimals): String {
        val safe = if (value.isFinite()) value else 0.0
        var text = BigDecimal.valueOf(safe).setScale(maxDecimals, RoundingMode.HALF_UP).toPlainString()
        if (maxDecimals > minDecimals && text.contains('.')) {
            val minEnd = text.indexOf('.') + 1 + minDecimals
            var end = text.length
            while (end > minEnd && text[end - 1] == '0') end--
            text = text.substring(0, end)
            if (text.endsWith('.')) text = text.dropLast(1)
        }
        return text
    }
}

object Hashing {
    private const val HEX = "0123456789abcdef"

    /** Lower-case hex SHA-256, as sent in `X-Content-SHA256`. */
    fun sha256Hex(bytes: ByteArray): String = MessageDigest.getInstance("SHA-256").digest(bytes).toHex()

    fun sha256Hex(file: File): String {
        val digest = MessageDigest.getInstance("SHA-256")
        file.inputStream().use { input ->
            val buffer = ByteArray(64 * 1024)
            while (true) {
                val n = input.read(buffer)
                if (n < 0) break
                digest.update(buffer, 0, n)
            }
        }
        return digest.digest().toHex()
    }

    private fun ByteArray.toHex(): String {
        val out = StringBuilder(size * 2)
        for (b in this) {
            val v = b.toInt() and 0xFF
            out.append(HEX[v ushr 4]).append(HEX[v and 0x0F])
        }
        return out.toString()
    }
}
