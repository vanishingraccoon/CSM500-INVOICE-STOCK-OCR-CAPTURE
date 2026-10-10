package com.invoicecapture.domain.upload

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class UploadPolicyTest {

    private val match = ReceiptCheck.Match
    private val mismatch = ReceiptCheck.Mismatch(listOf("bytes"))

    @Test
    fun createdOrRetriedWithMatchingReceiptIsStored() {
        assertEquals(UploadOutcome.Stored, UploadPolicy.decide(201, null, match))
        assertEquals(UploadOutcome.Stored, UploadPolicy.decide(200, null, match))
    }

    @Test
    fun successWithoutAMatchingReceiptKeepsTheFileAndRetries() {
        assertTrue(UploadPolicy.decide(201, null, mismatch) is UploadOutcome.RetryLater)
        assertTrue(UploadPolicy.decide(200, null, null) is UploadOutcome.RetryLater)
    }

    @Test
    fun keyReusedIsSurfacedAsAnError() {
        val outcome = UploadPolicy.decide(409, "key_reused", null)
        assertTrue(outcome is UploadOutcome.Stop)
        assertEquals("key_reused", (outcome as UploadOutcome.Stop).errorCode)
    }

    @Test
    fun hashMismatchIsSurfacedAsAnError() {
        val outcome = UploadPolicy.decide(422, "hash_mismatch", null)
        assertTrue(outcome is UploadOutcome.Stop)
        assertEquals("hash_mismatch", (outcome as UploadOutcome.Stop).errorCode)
        assertEquals("hash_mismatch", (UploadPolicy.decide(422, null, null) as UploadOutcome.Stop).errorCode)
    }

    @Test
    fun anAlreadyReplacedRetakeStopsWithItsOwnMessage() {
        val outcome = UploadPolicy.decide(409, "already_replaced", null)
        assertTrue(outcome is UploadOutcome.Stop)
        assertEquals("already_replaced", (outcome as UploadOutcome.Stop).errorCode)
        assertTrue(outcome.message.contains("already been replaced"))
    }

    @Test
    fun everythingElseRetriesWithBackoff() {
        for (status in listOf(null, 401, 403, 404, 408, 415, 423, 429, 500, 502, 503)) {
            val outcome = UploadPolicy.decide(status, null, null)
            assertTrue("HTTP $status gave $outcome", outcome is UploadOutcome.RetryLater)
        }
    }

    @Test
    fun anErrorReplyNeverCountsAsStoredEvenWithAReceipt() {
        assertTrue(UploadPolicy.decide(500, null, match) is UploadOutcome.RetryLater)
        assertTrue(UploadPolicy.decide(409, "key_reused", match) is UploadOutcome.Stop)
    }

    @Test
    fun backoffDoublesFromTwentySeconds() {
        assertEquals(20L, UploadPolicy.backoffSeconds(1))
        assertEquals(40L, UploadPolicy.backoffSeconds(2))
        assertEquals(80L, UploadPolicy.backoffSeconds(3))
        assertEquals(10_240L, UploadPolicy.backoffSeconds(10))
    }

    @Test
    fun backoffIsCappedAtFiveHours() {
        assertEquals(18_000L, UploadPolicy.backoffSeconds(11))
        assertEquals(18_000L, UploadPolicy.backoffSeconds(64))
        assertEquals(20L, UploadPolicy.backoffSeconds(0))
    }

    private fun complete(uploaded: Int, total: Int, hasBatch: Boolean = true, sent: Boolean = false) =
        UploadPolicy.shouldSendComplete(
            isScan = true, allSettled = true, completeSent = sent, hasServerBatch = hasBatch,
            uploadedPages = uploaded, totalPages = total,
        )

    @Test
    fun everyPageOnTheLaptopCompletesTheBatch() {
        assertTrue(complete(uploaded = 3, total = 3))
    }

    @Test
    fun aScanWhoseEveryPageWasDiscardedIsStillCompleted() {
        assertTrue(complete(uploaded = 0, total = 0))
    }

    @Test
    fun nothingIsSentWhenTheLaptopNeverCreatedTheBatch() {
        assertFalse(complete(uploaded = 0, total = 0, hasBatch = false))
    }

    @Test
    fun aPageStillOnThePhoneKeepsTheBatchOpen() {
        assertFalse(complete(uploaded = 0, total = 1))
    }

    @Test
    fun theLaptopIsToldOnlyOnce() {
        assertFalse(complete(uploaded = 2, total = 2, sent = true))
    }
}
