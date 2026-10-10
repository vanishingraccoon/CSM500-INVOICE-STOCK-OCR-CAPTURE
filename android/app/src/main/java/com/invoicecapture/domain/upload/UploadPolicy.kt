package com.invoicecapture.domain.upload

sealed interface UploadOutcome {
    /** A matching receipt: the photo is safe on the laptop, so the phone deletes its copy. */
    data object Stored : UploadOutcome

    /** Keep the file and try again with backoff. [reason] is shown in the upload queue. */
    data class RetryLater(val reason: String) : UploadOutcome

    /** Keep the file and stop; shown as an error until the manager taps "Try again". */
    data class Stop(val errorCode: String, val message: String) : UploadOutcome
}

/** What to do after one upload attempt (contract §2). */
object UploadPolicy {
    /** WorkManager exponential backoff: 20 s, 40 s, 80 s, … */
    const val INITIAL_BACKOFF_SECONDS: Long = 20

    /** WorkManager never waits longer than 5 hours between attempts. */
    const val MAX_BACKOFF_SECONDS: Long = 5 * 60 * 60

    /**
     * @param httpStatus null when the laptop could not be reached at all.
     * @param errorCode the `error` field of an error reply, if any.
     * @param receipt the receipt check for a 200/201 reply.
     *
     * Only a 200/201 with a matching receipt deletes the file. `409 key_reused` and
     * `422 hash_mismatch` are surfaced as errors; anything else keeps the file and retries.
     */
    fun decide(httpStatus: Int?, errorCode: String?, receipt: ReceiptCheck?): UploadOutcome = when {
        httpStatus == null -> UploadOutcome.RetryLater("Can't reach the laptop")
        httpStatus == 200 || httpStatus == 201 -> when (receipt) {
            ReceiptCheck.Match -> UploadOutcome.Stored
            is ReceiptCheck.Mismatch ->
                UploadOutcome.RetryLater("The laptop's receipt did not match (${receipt.fields.joinToString()})")
            null -> UploadOutcome.RetryLater("The laptop's reply had no receipt")
        }
        httpStatus == 409 -> UploadOutcome.Stop(
            errorCode ?: "key_reused",
            if (errorCode == "already_replaced") {
                "This page had already been replaced by another photo, so this retake was not stored"
            } else {
                "The laptop already holds a different photo under this page's key"
            },
        )
        httpStatus == 422 -> UploadOutcome.Stop(
            errorCode ?: "hash_mismatch",
            "The photo arrived damaged (its checksum did not match)",
        )
        httpStatus == 401 -> UploadOutcome.RetryLater("The laptop does not recognise this phone - pair it again")
        httpStatus == 403 -> UploadOutcome.RetryLater("This phone was removed on the laptop - pair it again")
        httpStatus == 404 -> UploadOutcome.RetryLater("The laptop does not know this batch")
        httpStatus == 415 -> UploadOutcome.RetryLater("The laptop could not open this photo")
        httpStatus == 423 -> UploadOutcome.RetryLater("The batch is already submitted on the laptop")
        httpStatus >= 500 -> UploadOutcome.RetryLater("The laptop had a problem (HTTP $httpStatus)")
        else -> UploadOutcome.RetryLater("Unexpected reply from the laptop (HTTP $httpStatus)")
    }

    /**
     * Whether a scan session should now tell the laptop that every page is in (contract section 2).
     *
     * [uploadedPages] have receipts; [totalPages] counts the pages still on the phone, discarded ones
     * excluded. A scan whose every page was discarded has neither, and must still be completed, or the
     * batch the laptop already created would sit as "uploading" for ever. A page still on the phone,
     * stuck on an error, keeps the batch open until the manager retries or discards it.
     */
    fun shouldSendComplete(
        isScan: Boolean,
        allSettled: Boolean,
        completeSent: Boolean,
        hasServerBatch: Boolean,
        uploadedPages: Int,
        totalPages: Int,
    ): Boolean = isScan && allSettled && !completeSent && hasServerBatch &&
        (uploadedPages > 0 || totalPages == 0)

    /** The wait before the next attempt after [failedAttempts] failures (1 = first failure). */
    fun backoffSeconds(failedAttempts: Int): Long {
        val n = failedAttempts.coerceAtLeast(1)
        if (n > 20) return MAX_BACKOFF_SECONDS
        return minOf(INITIAL_BACKOFF_SECONDS shl (n - 1), MAX_BACKOFF_SECONDS)
    }
}
