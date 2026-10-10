package com.invoicecapture.domain.batch

import java.math.BigDecimal
import java.math.RoundingMode
import java.text.DecimalFormat
import java.text.DecimalFormatSymbols
import java.util.Locale

/** Rands and quantities: what the manager types, and how the app shows them. */
object Money {
    private val PLAIN = Regex("""\d+(\.\d+)?""")

    /** "R 3,450.00"; "—" when missing. */
    fun format(amount: Double?): String {
        if (amount == null || !amount.isFinite()) return "—"
        val value = BigDecimal.valueOf(amount).setScale(2, RoundingMode.HALF_UP)
        val text = DecimalFormat("#,##0.00", DecimalFormatSymbols(Locale.ROOT)).format(value.abs())
        return (if (value.signum() < 0) "-R " else "R ") + text
    }

    /** "2", "2.5", "—". */
    fun formatQuantity(quantity: Double?): String {
        if (quantity == null || !quantity.isFinite()) return "—"
        return plainNumber(BigDecimal.valueOf(quantity))
    }

    /** Accepts "200", "200.5", "1 200,50", "R3,450.00", "-12.00". Null when it is not a number. */
    fun parseAmount(text: String): BigDecimal? {
        var t = text.trim()
        val negative = t.startsWith("-")
        t = t.removePrefix("-").trim().removePrefix("R").removePrefix("r").trim()
        return parseNumber(t)?.let { if (negative) it.negate() else it }
    }

    /** A quantity as printed: "2", "2.5", "0,75". */
    fun parseQuantity(text: String): BigDecimal? {
        val t = text.trim()
        val negative = t.startsWith("-")
        return parseNumber(t.removePrefix("-").trim())?.let { if (negative) it.negate() else it }
    }

    /** Money on the wire: two decimals, e.g. "200.00". */
    fun twoDecimals(value: BigDecimal): String = value.setScale(2, RoundingMode.HALF_UP).toPlainString()

    /** Quantities on the wire: no trailing zeros, never exponent notation, e.g. "2", "2.5", "100". */
    fun plainNumber(value: BigDecimal): String {
        val stripped = value.stripTrailingZeros()
        return if (stripped.signum() == 0) "0" else stripped.toPlainString()
    }

    private fun parseNumber(raw: String): BigDecimal? {
        var t = raw.replace(" ", "").replace(" ", "")
        if (t.isEmpty()) return null
        val commas = t.count { it == ',' }
        t = when {
            commas > 0 && t.contains('.') -> t.replace(",", "") // 3,450.00
            commas == 1 && t.substringAfter(',').length <= 2 -> t.replace(',', '.') // 200,50
            else -> t.replace(",", "") // 1,200
        }
        if (!PLAIN.matches(t)) return null
        return BigDecimal(t)
    }
}

sealed interface LineChoice {
    /** A stock item from this shop's key. */
    data class StockItem(val code: String, val name: String?) : LineChoice

    /** Not in the key: the words as the manager types them. */
    data class FreeText(val text: String) : LineChoice
}

/** A checked manager entry, ready for `PUT/POST …/lines` (contract §5). */
data class LineEdit(
    val choice: LineChoice,
    /** With a stock item: as printed on the invoice. With free text: in Pilot's base unit. */
    val quantity: BigDecimal,
    val unit: String?,
    /** Line cost in rands, excluding VAT. */
    val cost: BigDecimal,
)

sealed interface LineEditCheck {
    data class Valid(val edit: LineEdit) : LineEditCheck
    data class Invalid(val message: String) : LineEditCheck
}

object LineEdits {
    fun check(choice: LineChoice?, quantityText: String, unitText: String, costText: String): LineEditCheck {
        val checkedChoice = when (choice) {
            null -> return LineEditCheck.Invalid("Choose a stock item, or type the item as free text.")
            is LineChoice.FreeText -> {
                if (choice.text.isBlank()) return LineEditCheck.Invalid("Type the item as it should appear in the report.")
                choice.copy(text = choice.text.trim())
            }
            is LineChoice.StockItem -> choice
        }
        val quantity = Money.parseQuantity(quantityText)
            ?: return LineEditCheck.Invalid("Enter the quantity as a number.")
        if (quantity.signum() < 0) return LineEditCheck.Invalid("The quantity cannot be negative.")
        val cost = Money.parseAmount(costText)
            ?: return LineEditCheck.Invalid("Enter the cost as an amount in rands, e.g. 200.00.")
        val unit = if (checkedChoice is LineChoice.StockItem) unitText.trim().ifEmpty { null } else null
        return LineEditCheck.Valid(LineEdit(checkedChoice, quantity, unit, cost))
    }
}
