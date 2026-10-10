package com.invoicecapture.data.pending

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class CaptureSummaryTest {

    private fun page(ordinal: Int, state: PageState, problems: List<String> = emptyList(), stopped: Boolean = false, error: String? = null) =
        PendingPage(ordinal = ordinal, clientPhotoKey = "k$ordinal", state = state, problems = problems, stopped = stopped, lastError = error)

    @Test
    fun countsChecksAndUploadsAndNamesPoorPagesInOrder() {
        val session = PendingSession(
            key = "s1",
            kind = SessionKind.SCAN,
            createdAtMillis = 0,
            pageCount = 4,
            serverBatchId = "b_1",
            pages = listOf(
                page(3, PageState.READY, listOf("blur", "glare")),
                page(1, PageState.UPLOADED),
                page(2, PageState.UPLOADED, listOf("too_dark")),
                page(4, PageState.SCANNED),
            ),
        )
        val s = CaptureSummary.of(session)
        assertEquals(3, s.checked)
        assertEquals(2, s.uploaded)
        assertEquals(2, s.onPhone)
        assertTrue(s.checking)
        assertFalse(s.waitingForBatch)
        assertEquals(listOf(PoorPage(2, "too dark"), PoorPage(3, "blurred, glare")), s.poorPages)
        assertTrue(s.stuckPages.isEmpty())
    }

    @Test
    fun stoppedAndUnreadablePagesNeedAttentionAndABatchIsAwaited() {
        val session = PendingSession(
            key = "s2",
            kind = SessionKind.SCAN,
            createdAtMillis = 0,
            pageCount = 2,
            batchError = "Can't reach the laptop.",
            pages = listOf(
                page(1, PageState.READY, stopped = true, error = "key reused"),
                page(2, PageState.UNREADABLE),
            ),
        )
        val s = CaptureSummary.of(session)
        assertTrue(s.waitingForBatch)
        assertEquals(listOf(1, 2), s.stuckPages.map { it.ordinal })
        assertEquals("key reused", s.stuckPages[0].message)
        assertFalse(s.everythingUploaded)
    }

    @Test
    fun retakeSessionsNeverWaitForABatch() {
        val session = PendingSession(
            key = "s3", kind = SessionKind.RETAKE, createdAtMillis = 0, pageCount = 1,
            pages = listOf(page(5, PageState.UPLOADED)),
        )
        val s = CaptureSummary.of(session)
        assertFalse(s.waitingForBatch)
        assertTrue(s.everythingUploaded)
        assertEquals("faint print, odd thing", CaptureSummary.wordsFor(listOf("faint_print", "odd_thing")))
    }
}
