package com.invoicecapture.domain.batch

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.math.BigDecimal

class BatchScreenLogicTest {

    @Test
    fun pagerKeepsTheSameInvoiceOrTheOneThatTookItsPlace() {
        val ids = listOf("i_1", "i_2", "i_3")
        assertEquals(2, PagerSelection.indexFor(ids, "i_3", 0))
        // i_2 was fixed and left the Corrections tab: show the invoice now at its position.
        assertEquals(1, PagerSelection.indexFor(listOf("i_1", "i_3"), "i_2", 1))
        // The last invoice left: step back to the new last one.
        assertEquals(1, PagerSelection.indexFor(listOf("i_1", "i_2"), "i_3", 2))
        assertEquals(0, PagerSelection.indexFor(emptyList(), "i_1", 4))
        assertEquals(0, PagerSelection.indexFor(ids, null, -3))
    }

    @Test
    fun headerEditsNeedSupplierNumberAndARealDate() {
        assertEquals(
            HeaderCheck.Valid("vassco", "1844378", "2026-08-31"),
            HeaderEdits.check(" vassco ", " 1844378 ", "31/08/2026"),
        )
        assertTrue(HeaderEdits.check("", "1", "2026-08-31") is HeaderCheck.Invalid)
        assertTrue(HeaderEdits.check("vassco", "  ", "2026-08-31") is HeaderCheck.Invalid)
        assertTrue(HeaderEdits.check("vassco", "1", "31/02/2026") is HeaderCheck.Invalid)
        assertTrue(HeaderEdits.check("vassco", "1", "yesterday") is HeaderCheck.Invalid)
    }

    @Test
    fun typedDatesBecomeIsoDates() {
        assertEquals("2026-08-31", HeaderEdits.isoDate("2026-08-31"))
        assertEquals("2026-08-01", HeaderEdits.isoDate("2026/8/1"))
        assertEquals("2026-08-31", HeaderEdits.isoDate("31.08.2026"))
        assertEquals("2026-08-03", HeaderEdits.isoDate("3-8-2026"))
        assertNull(HeaderEdits.isoDate("2026-13-01"))
        assertNull(HeaderEdits.isoDate("31/08/26"))
    }

    @Test
    fun lineFormIsPrefilledFromThePrintedLine() {
        val matched = LineForm.initial("1189", "honey (LITRE)", "Honey 5L", 2.0, null, 100.0, 200.0)
        assertEquals(LineChoice.StockItem("1189", "honey (LITRE)"), matched.choice)
        assertEquals("2", matched.quantity)
        assertEquals("200.00", matched.cost)
        assertEquals("", matched.freeText)

        val unmatched = LineForm.initial(null, null, " Honey 5L tub ", 1.5, "tub", 80.0, null)
        assertEquals(LineChoice.FreeText("Honey 5L tub"), unmatched.choice)
        assertEquals("1.5", unmatched.quantity)
        assertEquals("tub", unmatched.unit)
        assertEquals("120.00", unmatched.cost)

        val empty = LineForm.initial(null, null, null, null, null, null, null)
        assertNull(empty.choice)
        assertEquals("", empty.cost)
        // The prefilled texts pass the editor's own check.
        assertTrue(LineEdits.check(matched.choice, matched.quantity, matched.unit, matched.cost) is LineEditCheck.Valid)
    }

    @Test
    fun totalsFollowTheLaptopAndShowTheDifference() {
        assertEquals(TotalsStatus.AddsUp, Totals.status(3000.0, 3000.0, true))
        assertEquals(TotalsStatus.DoesNotAddUp(BigDecimal("-50.00")), Totals.status(3000.0, 2950.0, false))
        assertEquals(TotalsStatus.AddsUp, Totals.status(10.0, 10.0, null))
        assertEquals(TotalsStatus.Unknown, Totals.status(null, 10.0, false))
        assertTrue(Totals.describe(TotalsStatus.DoesNotAddUp(BigDecimal("-50.00"))).contains("R 50.00 less"))
        assertTrue(Totals.describe(TotalsStatus.DoesNotAddUp(BigDecimal("12.50"))).contains("R 12.50 more"))
    }

    @Test
    fun duplicateActionsOnlyInTheDuplicatesTabAndNotWhileBusy() {
        assertEquals(DuplicateButtons(visible = true, enabled = true), DuplicateActions.buttons(InvoiceTab.DUPLICATES, busy = false))
        assertEquals(DuplicateButtons(visible = true, enabled = false), DuplicateActions.buttons(InvoiceTab.DUPLICATES, busy = true))
        assertFalse(DuplicateActions.buttons(InvoiceTab.GOOD, busy = false).visible)
        assertFalse(DuplicateActions.buttons(null, busy = false).enabled)
        assertTrue(DuplicateActions.needsConfirmation(DuplicateAction.DELETE))
        assertFalse(DuplicateActions.needsConfirmation(DuplicateAction.NOT_DUPLICATE))
    }

    @Test
    fun messagesSayWhereTheInvoiceWent() {
        assertEquals("Kept as a separate invoice. It is now in Corrections.", DuplicateActions.doneMessage(DuplicateAction.NOT_DUPLICATE, "corrections"))
        assertTrue(DuplicateActions.doneMessage(DuplicateAction.DELETE, "deleted").contains("never goes into a report"))
        assertEquals("Saved. It is now in Good.", DuplicateActions.movedMessage(InvoiceTab.CORRECTIONS, "good"))
        assertEquals("Saved.", DuplicateActions.movedMessage(InvoiceTab.CORRECTIONS, "corrections"))
    }

    @Test
    fun theLinesCurrentEntryWinsOverThePrintedValues() {
        val typed = LineForm.initial(
            null, null, "Honey 5L", 2.0, null, 100.0, 200.0,
            enteredFreeText = "honey 5l tub", enteredQuantity = 10.0, enteredCost = 180.0,
        )
        assertEquals(LineChoice.FreeText("honey 5l tub"), typed.choice)
        assertEquals("10", typed.quantity)
        assertEquals("180.00", typed.cost)
    }

    @Test
    fun regroupedInvoicesAreFoundAgainByTheirPhotos() {
        assertTrue(Regrouping.needsRefetch(409, "invoice_regrouped"))
        assertTrue(Regrouping.needsRefetch(409, "already_replaced"))
        assertFalse(Regrouping.needsRefetch(409, "key_reused"))
        assertFalse(Regrouping.needsRefetch(422, "invoice_regrouped"))
        assertFalse(Regrouping.needsRefetch(409, null))
        val invoices = listOf("i_7" to listOf("p_1"), "i_8" to listOf("p_2", "p_3"), "i_9" to listOf("p_4"))
        assertEquals("i_8", Regrouping.refind(invoices, setOf("p_1", "p_2", "p_3")) { it.second }?.first)
        assertEquals("i_7", Regrouping.refind(invoices, setOf("p_1", "p_4")) { it.second }?.first)
        assertNull(Regrouping.refind(invoices, setOf("p_9")) { it.second })
        assertNull(Regrouping.refind(invoices, emptySet()) { it.second })
    }
}
