package com.invoicecapture.data.pending

import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder

/** Discarding a page the laptop refused (409/422): it leaves the queue for good. */
class PendingStoreDiscardTest {

    @get:Rule
    val folder = TemporaryFolder()

    private fun page(ordinal: Int, state: PageState, stopped: Boolean = false) = PendingPage(
        ordinal = ordinal,
        clientPhotoKey = "k$ordinal",
        state = state,
        uploadFile = if (state == PageState.READY) "page-00$ordinal.jpg" else null,
        stopped = stopped,
        lastError = if (stopped) "The laptop already holds a different photo under this page's key" else null,
    )

    private fun session() = PendingSession(
        key = "s1",
        kind = SessionKind.SCAN,
        createdAtMillis = 0,
        pageCount = 3,
        serverBatchId = "b_1",
        pages = listOf(
            page(1, PageState.UPLOADED),
            page(2, PageState.READY, stopped = true),
            page(3, PageState.UPLOADED),
        ),
    )

    @Test
    fun discardingDropsThePageAndLeavesTheRestOfTheBatchReadyToComplete() = runBlocking {
        val store = PendingStore(folder.newFolder("pending"))
        store.put(session())

        val after = store.discardPage("s1", "k2")!!

        assertNull(after.pages.firstOrNull { it.clientPhotoKey == "k2" })
        assertEquals(listOf(1, 3), after.pages.map { it.ordinal })
        assertEquals(2, after.pageCount)
        assertEquals(listOf(2), after.discardedOrdinals)
        // Nothing of this session is on the phone any more, so `POST …/complete` can still be sent.
        assertEquals(0, after.pagesOnPhone)
        assertTrue(after.allSettled)
        assertTrue(after.allUploaded)
        assertTrue(after.problemPages.isEmpty())
    }

    @Test
    fun aDiscardedPageIsNotQueuedAgainByARetryEvenAfterTheAppRestarts() = runBlocking {
        val root = folder.newFolder("pending")
        PendingStore(root).apply {
            put(session())
            discardPage("s1", "k2")
        }

        // A later "Try again" reads the manifest from disk; only pages still in the session are queued.
        val reopened = PendingStore(root)
        val session = reopened.get("s1")!!
        assertFalse(session.pages.any { it.clientPhotoKey == "k2" })
        assertTrue(session.pages.none { it.state == PageState.READY || it.state == PageState.SCANNED })
        assertEquals(listOf(2), session.discardedOrdinals)
    }

    @Test
    fun theCaptureScreenShowsADiscardedPageAsDiscardedAndNoLongerAsStuck() = runBlocking {
        val store = PendingStore(folder.newFolder("pending"))
        store.put(session())

        val stuck = CaptureSummary.of(store.get("s1")!!)
        assertEquals(listOf(2), stuck.stuckPages.map { it.ordinal })
        assertEquals("k2", stuck.stuckPages.single().clientPhotoKey)
        assertFalse(stuck.everythingUploaded)

        val s = CaptureSummary.of(store.discardPage("s1", "k2")!!)
        assertTrue(s.stuckPages.isEmpty())
        assertEquals(2, s.pageCount)
        assertEquals(2, s.uploaded)
        assertTrue(s.everythingUploaded)
        assertEquals("Page 2", s.discardedLabel)
    }
}
