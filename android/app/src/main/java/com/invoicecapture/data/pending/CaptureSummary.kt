package com.invoicecapture.data.pending

/** One page that the phone's checks flagged, in plain words, e.g. ordinal 3 → "blurred, glare". */
data class PoorPage(val ordinal: Int, val words: String)

/** One page whose upload stopped or cannot happen, with the reason. [clientPhotoKey] names it to discard it. */
data class StuckPage(val ordinal: Int, val clientPhotoKey: String, val message: String)

/** What the capture screen shows for one scanner session: checks, uploads, and pages to look at. */
data class CaptureSummary(
    val kind: SessionKind,
    val pageCount: Int,
    val checked: Int,
    val uploaded: Int,
    val onPhone: Int,
    val batchId: String?,
    val batchError: String?,
    val completeSent: Boolean,
    val poorPages: List<PoorPage>,
    val stuckPages: List<StuckPage>,
    /** Scan positions the manager discarded; their photos are gone and never upload. */
    val discardedOrdinals: List<Int> = emptyList(),
) {
    val checking: Boolean get() = checked < pageCount
    val waitingForBatch: Boolean get() = kind == SessionKind.SCAN && batchId == null
    val everythingUploaded: Boolean get() = pageCount > 0 && uploaded == pageCount

    /** "Page 3" / "Pages 3, 5", for the line that says what was discarded. */
    val discardedLabel: String?
        get() = when (discardedOrdinals.size) {
            0 -> null
            1 -> "Page ${discardedOrdinals[0]}"
            else -> "Pages ${discardedOrdinals.joinToString(", ")}"
        }

    companion object {
        private val WORDS = mapOf(
            "blur" to "blurred",
            "too_dark" to "too dark",
            "washed_out" to "washed out",
            "faint_print" to "faint print",
            "glare" to "glare",
        )

        fun wordsFor(codes: List<String>): String = codes.joinToString(", ") { WORDS[it] ?: it.replace('_', ' ') }

        fun of(session: PendingSession): CaptureSummary {
            val pages = session.pages.sortedBy { it.ordinal }
            return CaptureSummary(
                kind = session.kind,
                pageCount = session.pageCount.coerceAtLeast(pages.size),
                checked = session.preparedCount,
                uploaded = session.uploadedCount,
                onPhone = session.pagesOnPhone,
                batchId = session.serverBatchId,
                batchError = session.batchError,
                completeSent = session.completeSent,
                poorPages = pages.filter { it.problems.isNotEmpty() }.map { PoorPage(it.ordinal, wordsFor(it.problems)) },
                stuckPages = pages.filter { it.stopped || it.state == PageState.UNREADABLE }
                    .map { StuckPage(it.ordinal, it.clientPhotoKey, it.lastError ?: "This page needs attention.") },
                discardedOrdinals = session.discardedOrdinals.sorted(),
            )
        }
    }
}
