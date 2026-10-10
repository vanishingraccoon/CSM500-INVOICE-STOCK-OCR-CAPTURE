package com.invoicecapture.domain.batch

/** The four tabs of a batch (contract §4). The server decides the tab; the phone only shows it. */
enum class InvoiceTab(val wire: String, val label: String) {
    GOOD("good", "Good"),
    CORRECTIONS("corrections", "Corrections"),
    RETAKE("retake", "Retake"),
    DUPLICATES("duplicates", "Duplicates");

    /** Corrections and its content are shown on a red background. */
    val red: Boolean get() = this == CORRECTIONS

    companion object {
        /** Left to right across the top of the batch screen. */
        val ORDER: List<InvoiceTab> = listOf(GOOD, CORRECTIONS, RETAKE, DUPLICATES)

        /** Deleted duplicates (and any unknown tab) are not shown in any tab. */
        fun fromWire(value: String?): InvoiceTab? = entries.firstOrNull { it.wire == value }
    }
}

object TabOrdering {
    /**
     * Every tab in [InvoiceTab.ORDER], each with its invoices in scan order (first page's
     * ordinal; invoices without pages last; ties by id). Deleted or unknown tabs are left out.
     */
    fun <T> group(
        invoices: List<T>,
        tabOf: (T) -> String?,
        firstOrdinalOf: (T) -> Int?,
        idOf: (T) -> String,
    ): Map<InvoiceTab, List<T>> {
        val sorted = invoices.sortedWith(compareBy<T>({ firstOrdinalOf(it) ?: Int.MAX_VALUE }, { idOf(it) }))
        return InvoiceTab.ORDER.associateWith { tab -> sorted.filter { InvoiceTab.fromWire(tabOf(it)) == tab } }
    }

    /** Starred lines (★, the ones that need the manager) first, each group in line order. */
    fun <T> starredFirst(lines: List<T>, isStarred: (T) -> Boolean, indexOf: (T) -> Int): List<T> =
        lines.sortedWith(compareBy<T>({ if (isStarred(it)) 0 else 1 }, { indexOf(it) }))

    /** The tab to open first: the first one that needs the manager, otherwise Good. */
    fun defaultTab(counts: Map<InvoiceTab, Int>): InvoiceTab =
        listOf(InvoiceTab.CORRECTIONS, InvoiceTab.RETAKE, InvoiceTab.DUPLICATES)
            .firstOrNull { (counts[it] ?: 0) > 0 } ?: InvoiceTab.GOOD
}
