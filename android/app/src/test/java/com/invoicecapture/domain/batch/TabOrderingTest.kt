package com.invoicecapture.domain.batch

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test
import com.invoicecapture.data.api.CountsDto
import com.invoicecapture.data.api.InvoiceDto
import com.invoicecapture.data.api.LineDto
import com.invoicecapture.data.api.PageDto

class TabOrderingTest {

    private fun invoice(id: String, tab: String, vararg ordinals: Int) =
        InvoiceDto(invoiceId = id, tab = tab, pages = ordinals.map { PageDto(photoId = "p_$it", ordinal = it) })

    private fun group(invoices: List<InvoiceDto>) =
        TabOrdering.group(invoices, { it.tab }, { it.firstOrdinal }, { it.invoiceId })

    @Test
    fun tabsRunGoodCorrectionsRetakeDuplicates() {
        assertEquals(
            listOf("Good", "Corrections", "Retake", "Duplicates"),
            InvoiceTab.ORDER.map { it.label },
        )
        assertEquals(listOf("good", "corrections", "retake", "duplicates"), InvoiceTab.ORDER.map { it.wire })
    }

    @Test
    fun onlyCorrectionsIsRed() {
        assertEquals(listOf(false, true, false, false), InvoiceTab.ORDER.map { it.red })
    }

    @Test
    fun wireNamesMapToTabsAndDeletedIsHidden() {
        assertEquals(InvoiceTab.CORRECTIONS, InvoiceTab.fromWire("corrections"))
        assertEquals(InvoiceTab.DUPLICATES, InvoiceTab.fromWire("duplicates"))
        assertNull(InvoiceTab.fromWire("deleted"))
        assertNull(InvoiceTab.fromWire("something_new"))
        assertNull(InvoiceTab.fromWire(null))
    }

    @Test
    fun invoicesAreGroupedByTabInScanOrder() {
        val grouped = group(
            listOf(
                invoice("i_e", "retake", 5),
                invoice("i_c", "good", 3, 4),
                invoice("i_a", "good", 1),
                invoice("i_b", "deleted", 2),
                invoice("i_d", "corrections", 6),
            ),
        )
        assertEquals(InvoiceTab.ORDER, grouped.keys.toList())
        assertEquals(listOf("i_a", "i_c"), grouped.getValue(InvoiceTab.GOOD).map { it.invoiceId })
        assertEquals(listOf("i_d"), grouped.getValue(InvoiceTab.CORRECTIONS).map { it.invoiceId })
        assertEquals(listOf("i_e"), grouped.getValue(InvoiceTab.RETAKE).map { it.invoiceId })
        assertEquals(emptyList<String>(), grouped.getValue(InvoiceTab.DUPLICATES).map { it.invoiceId })
    }

    @Test
    fun invoicesWithoutPagesComeLastThenById() {
        val grouped = group(
            listOf(invoice("i_z", "good"), invoice("i_y", "good"), invoice("i_x", "good", 9)),
        )
        assertEquals(listOf("i_x", "i_y", "i_z"), grouped.getValue(InvoiceTab.GOOD).map { it.invoiceId })
    }

    @Test
    fun multiPageInvoiceSortsByItsFirstPage() {
        val grouped = group(listOf(invoice("i_late", "good", 2), invoice("i_early", "good", 7, 1)))
        assertEquals(listOf("i_early", "i_late"), grouped.getValue(InvoiceTab.GOOD).map { it.invoiceId })
    }

    @Test
    fun starredLinesComeFirst() {
        val lines = listOf(
            LineDto(index = 0, star = false),
            LineDto(index = 1, star = true),
            LineDto(index = 2, star = false),
            LineDto(index = 3, star = true),
        )
        assertEquals(listOf(1, 3, 0, 2), TabOrdering.starredFirst(lines, { it.star }, { it.index }).map { it.index })
    }

    @Test
    fun theFirstTabNeedingTheManagerOpensFirst() {
        assertEquals(InvoiceTab.GOOD, TabOrdering.defaultTab(CountsDto(good = 4).asMap()))
        assertEquals(InvoiceTab.CORRECTIONS, TabOrdering.defaultTab(CountsDto(good = 4, corrections = 2, retake = 1).asMap()))
        assertEquals(InvoiceTab.RETAKE, TabOrdering.defaultTab(CountsDto(retake = 1, duplicates = 1).asMap()))
        assertEquals(InvoiceTab.DUPLICATES, TabOrdering.defaultTab(CountsDto(duplicates = 1).asMap()))
    }

    @Test
    fun countsAreReadPerTab() {
        val counts = CountsDto(good = 4, corrections = 2, retake = 1, duplicates = 3)
        assertEquals(listOf(4, 2, 1, 3), InvoiceTab.ORDER.map { counts.forTab(it) })
    }
}
