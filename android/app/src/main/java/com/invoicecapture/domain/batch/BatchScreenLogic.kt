package com.invoicecapture.domain.batch

import java.math.BigDecimal
import java.math.RoundingMode
import java.time.LocalDate
import java.time.format.DateTimeParseException

/** Which invoice the pager shows after the list inside a tab changed (a refresh or an edit). */
object PagerSelection {
    /**
     * The same invoice if it is still in the tab; otherwise the invoice that took its place
     * (the old position, clamped), so the manager carries on with the next one. 0 when empty.
     */
    fun indexFor(ids: List<String>, selectedId: String?, previousIndex: Int): Int {
        if (ids.isEmpty()) return 0
        val same = if (selectedId == null) -1 else ids.indexOf(selectedId)
        return if (same >= 0) same else previousIndex.coerceIn(0, ids.lastIndex)
    }
}

sealed interface HeaderCheck {
    data class Valid(val supplier: String, val documentNo: String, val date: String) : HeaderCheck
    data class Invalid(val message: String) : HeaderCheck
}

/** Supplier, invoice number and date as the manager types them (`PATCH /invoices/{id}`, contract §5). */
object HeaderEdits {
    private val DAY_FIRST = Regex("""(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4})""")
    private val YEAR_FIRST = Regex("""(\d{4})[/.\-](\d{1,2})[/.\-](\d{1,2})""")

    fun check(supplier: String, documentNo: String, date: String): HeaderCheck {
        val s = supplier.trim()
        val n = documentNo.trim()
        if (s.isEmpty()) return HeaderCheck.Invalid("Type the supplier.")
        if (n.isEmpty()) return HeaderCheck.Invalid("Type the invoice number as printed.")
        val d = isoDate(date) ?: return HeaderCheck.Invalid("Type the date as printed, e.g. 31/08/2026 or 2026-08-31.")
        return HeaderCheck.Valid(s, n, d)
    }

    /** "2026-08-31", "2026/8/31", "31/08/2026", "31-8-2026" or "31.08.2026" → "2026-08-31". Day first, as printed in South Africa. */
    fun isoDate(text: String): String? {
        val t = text.trim()
        val (year, month, day) = YEAR_FIRST.matchEntire(t)?.destructured?.let { (y, m, d) -> Triple(y, m, d) }
            ?: DAY_FIRST.matchEntire(t)?.destructured?.let { (d, m, y) -> Triple(y, m, d) }
            ?: return null
        return try {
            LocalDate.of(year.toInt(), month.toInt(), day.toInt()).toString()
        } catch (e: java.time.DateTimeException) {
            null
        }
    }

    /** For prefilling: the laptop's ISO date as is, anything else unchanged. */
    fun displayDate(iso: String?): String = iso?.let {
        try {
            LocalDate.parse(it).toString()
        } catch (e: DateTimeParseException) {
            it
        }
    } ?: ""
}

/** The editor's starting values for one line: what the laptop read, as text the manager can change. */
data class LineForm(
    val itemCode: String?,
    val itemName: String?,
    val freeText: String,
    val quantity: String,
    val unit: String,
    val cost: String,
) {
    val choice: LineChoice?
        get() = when {
            itemCode != null -> LineChoice.StockItem(itemCode, itemName)
            freeText.isNotBlank() -> LineChoice.FreeText(freeText)
            else -> null
        }

    companion object {
        val EMPTY = LineForm(null, null, "", "", "", "")

        /**
         * Prefill from the printed line. The cost is the printed line total, or unit price ×
         * quantity when only those were read. A stock item already chosen stays chosen.
         */
        fun initial(
            itemCode: String?,
            itemName: String?,
            printedDescription: String?,
            printedQuantity: Double?,
            printedUnit: String?,
            unitPrice: Double?,
            lineTotal: Double?,
            enteredFreeText: String? = null,
            enteredQuantity: Double? = null,
            enteredUnit: String? = null,
            enteredCost: Double? = null,
        ): LineForm {
            val finiteCost = enteredCost?.takeIf { it.isFinite() }
            val cost = when {
                finiteCost != null -> BigDecimal.valueOf(finiteCost)
                lineTotal != null && lineTotal.isFinite() -> BigDecimal.valueOf(lineTotal)
                unitPrice != null && printedQuantity != null && unitPrice.isFinite() && printedQuantity.isFinite() ->
                    BigDecimal.valueOf(unitPrice).multiply(BigDecimal.valueOf(printedQuantity))
                else -> null
            }
            val quantity = enteredQuantity?.takeIf { it.isFinite() } ?: printedQuantity?.takeIf { it.isFinite() }
            val words = enteredFreeText?.trim()?.takeIf { it.isNotEmpty() } ?: printedDescription?.trim().orEmpty()
            return LineForm(
                itemCode = itemCode,
                itemName = itemName,
                freeText = if (itemCode == null) words else "",
                quantity = quantity?.let { Money.formatQuantity(it) }.orEmpty(),
                unit = (enteredUnit ?: printedUnit)?.trim().orEmpty(),
                cost = cost?.let { Money.twoDecimals(it) }.orEmpty(),
            )
        }
    }
}

/** The laptop can regroup pages into new invoices while it is still reading, so an invoice id can disappear. */
object Regrouping {
    /** 409 codes after which the phone reloads the batch and finds the invoice again. */
    val REFETCH_CODES: Set<String> = setOf("invoice_regrouped", "invoice_deleted", "already_replaced")

    fun needsRefetch(status: Int, error: String?): Boolean = status == 409 && error != null && error in REFETCH_CODES

    /** The invoice now holding most of the old invoice's photos (the first on a tie); null when none does. */
    fun <T> refind(invoices: List<T>, oldPhotoIds: Set<String>, photoIdsOf: (T) -> List<String>): T? {
        if (oldPhotoIds.isEmpty()) return null
        return invoices
            .map { it to photoIdsOf(it).count { id -> id in oldPhotoIds } }
            .filter { it.second > 0 }
            .maxByOrNull { it.second }
            ?.first
    }
}

sealed interface TotalsStatus {
    data object AddsUp : TotalsStatus

    /** [difference] = lines sum − stated total, in rands. */
    data class DoesNotAddUp(val difference: BigDecimal) : TotalsStatus

    /** The stated total or the lines were not read. */
    data object Unknown : TotalsStatus
}

object Totals {
    /** The laptop's `adds_up` decides; the difference is shown when both figures are there. */
    fun status(statedExcl: Double?, linesSumExcl: Double?, addsUp: Boolean?): TotalsStatus {
        val difference = if (statedExcl != null && linesSumExcl != null && statedExcl.isFinite() && linesSumExcl.isFinite()) {
            BigDecimal.valueOf(linesSumExcl).subtract(BigDecimal.valueOf(statedExcl)).setScale(2, RoundingMode.HALF_UP)
        } else {
            null
        }
        return when {
            addsUp == true -> TotalsStatus.AddsUp
            addsUp == false && difference != null -> TotalsStatus.DoesNotAddUp(difference)
            addsUp == null && difference != null && difference.signum() == 0 -> TotalsStatus.AddsUp
            addsUp == null && difference != null -> TotalsStatus.DoesNotAddUp(difference)
            else -> TotalsStatus.Unknown
        }
    }

    fun describe(status: TotalsStatus): String = when (status) {
        TotalsStatus.AddsUp -> "Lines add up to the stated total."
        is TotalsStatus.DoesNotAddUp -> {
            val amount = Money.format(status.difference.abs().toDouble())
            if (status.difference.signum() > 0) "Lines come to $amount more than the stated total."
            else "Lines come to $amount less than the stated total."
        }
        TotalsStatus.Unknown -> "The stated total or the lines could not be compared."
    }
}

/** What the phone does with the laptop's reply to `GET /api/v1/invoices/{id}` (contract §3). */
enum class LookupStep {
    /** The invoice came back: show the comparison. */
    SHOW,

    /** `404`: the laptop no longer has it. */
    GONE,

    /** `409 invoice_regrouped`: the pages moved, so load the batch again and ask once more. */
    REFETCH_BATCH,

    /** The laptop could not answer (unreachable, not paired, a server problem): ask again later. */
    NOT_NOW,
}

/** Finding the invoice a duplicate repeats (contract §3 `GET /api/v1/invoices/{invoice_id}`). */
object DuplicateLookup {
    const val GONE_MESSAGE = "The invoice this repeats is no longer on the laptop."

    /**
     * The copy already on the phone: the batch on screen first, then any batch loaded before.
     * When there is none, the laptop is asked for that one invoice, which may sit in a batch
     * this phone has never loaded.
     */
    fun <T> localCopy(currentBatch: List<T>, loadedBefore: List<T>, invoiceId: String, idOf: (T) -> String): T? =
        currentBatch.firstOrNull { idOf(it) == invoiceId } ?: loadedBefore.firstOrNull { idOf(it) == invoiceId }

    fun decide(httpStatus: Int?, errorCode: String?): LookupStep = when {
        httpStatus == null -> LookupStep.NOT_NOW
        httpStatus in 200..299 -> LookupStep.SHOW
        httpStatus == 404 -> LookupStep.GONE
        Regrouping.needsRefetch(httpStatus, errorCode) -> LookupStep.REFETCH_BATCH
        else -> LookupStep.NOT_NOW
    }
}

enum class DuplicateAction { DELETE, NOT_DUPLICATE }

/** What the Duplicates tab lets the manager do, and what to say afterwards (contract §4, §5). */
data class DuplicateButtons(val visible: Boolean, val enabled: Boolean)

object DuplicateActions {
    fun buttons(tab: InvoiceTab?, busy: Boolean): DuplicateButtons =
        DuplicateButtons(visible = tab == InvoiceTab.DUPLICATES, enabled = tab == InvoiceTab.DUPLICATES && !busy)

    /** Deleting cannot be undone on the phone, so it asks first; keeping does not. */
    fun needsConfirmation(action: DuplicateAction): Boolean = action == DuplicateAction.DELETE

    fun doneMessage(action: DuplicateAction, newTabWire: String?): String = when (action) {
        DuplicateAction.DELETE -> "Duplicate deleted. The laptop keeps it for the record; it never goes into a report."
        DuplicateAction.NOT_DUPLICATE -> "Kept as a separate invoice." + movedTo(newTabWire)
    }

    /** After any change: where the invoice went, when it left the tab being checked. */
    fun movedMessage(oldTab: InvoiceTab?, newTabWire: String?): String {
        val newTab = InvoiceTab.fromWire(newTabWire)
        return if (newTab != null && newTab != oldTab) "Saved." + movedTo(newTabWire) else "Saved."
    }

    private fun movedTo(newTabWire: String?): String =
        InvoiceTab.fromWire(newTabWire)?.let { " It is now in ${it.label}." }.orEmpty()
}
