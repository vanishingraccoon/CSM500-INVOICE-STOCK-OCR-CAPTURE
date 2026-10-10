package com.invoicecapture.domain.batch

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/** Finding the invoice a duplicate repeats (contract §3 `GET /api/v1/invoices/{invoice_id}`). */
class DuplicateLookupTest {

    /** Invoices stand for themselves here: the id is the whole invoice. */
    private fun copyOf(currentBatch: List<String>, loadedBefore: List<String>, id: String) =
        DuplicateLookup.localCopy(currentBatch, loadedBefore, id) { it }

    @Test
    fun aCopyAlreadyLoadedIsUsedAsItStands() {
        assertEquals("i_2", copyOf(listOf("i_1", "i_2"), emptyList(), "i_2"))
        // Not in this batch, but in one loaded before: still no call to the laptop.
        assertEquals("i_9", copyOf(listOf("i_1"), listOf("i_9"), "i_9"))
    }

    @Test
    fun anInvoiceNoBatchHoldsIsFetchedFromTheLaptop() {
        assertNull(copyOf(listOf("i_1"), listOf("i_9"), "i_77"))
        assertEquals(LookupStep.SHOW, DuplicateLookup.decide(200, null))
    }

    @Test
    fun a404MeansTheInvoiceIsGoneAndA409MeansLoadTheBatchAgain() {
        assertEquals(LookupStep.GONE, DuplicateLookup.decide(404, null))
        assertEquals(LookupStep.REFETCH_BATCH, DuplicateLookup.decide(409, "invoice_regrouped"))
        assertEquals(
            "The invoice this repeats is no longer on the laptop.",
            DuplicateLookup.GONE_MESSAGE,
        )
    }

    @Test
    fun anythingElseIsAskedAgainLater() {
        assertEquals(LookupStep.NOT_NOW, DuplicateLookup.decide(null, null))
        assertEquals(LookupStep.NOT_NOW, DuplicateLookup.decide(401, "unauthorised"))
        assertEquals(LookupStep.NOT_NOW, DuplicateLookup.decide(500, null))
        assertEquals(LookupStep.NOT_NOW, DuplicateLookup.decide(409, "still_reading"))
    }
}
