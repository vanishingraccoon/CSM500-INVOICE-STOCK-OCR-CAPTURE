package com.invoicecapture.data.upload

import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.Semaphore
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.sync.withPermit
import com.invoicecapture.data.api.ApiResult
import com.invoicecapture.data.api.InvoiceApi
import com.invoicecapture.data.api.PhotoReceiptDto
import com.invoicecapture.data.api.plainMessage
import com.invoicecapture.data.image.ImageProcessor
import com.invoicecapture.data.pending.PageState
import com.invoicecapture.data.pending.PendingSession
import com.invoicecapture.data.pending.PendingStore
import com.invoicecapture.data.pending.SessionKind
import com.invoicecapture.domain.quality.QualityHeader
import com.invoicecapture.domain.quality.QualityMeasures
import com.invoicecapture.domain.upload.LocalPhoto
import com.invoicecapture.domain.upload.ReceiptVerification
import com.invoicecapture.domain.upload.UploadOutcome
import com.invoicecapture.domain.upload.UploadPolicy
import com.invoicecapture.domain.util.Hashing
import java.io.IOException
import java.util.Locale

enum class StepResult { Done, Retry, Failed }

/** The work behind the three workers: prepare a scan, upload one page, complete a batch. */
class UploadEngine(
    private val api: InvoiceApi,
    private val store: PendingStore,
    private val scheduler: UploadScheduler,
    private val imageProcessor: ImageProcessor,
) {
    /** Budget phones and a laptop on office Wi-Fi: at most two photos travel at once. */
    private val uploadPermits = Semaphore(2)
    private val batchMutex = Mutex()

    /**
     * After a scan: create the batch (if the laptop is reachable), then page by page in order,
     * check quality, save the upload copy, hash it and queue its upload.
     */
    suspend fun prepareSession(key: String): StepResult {
        val session = store.get(key) ?: return StepResult.Done
        if (session.kind == SessionKind.SCAN && session.serverBatchId == null) ensureBatch(key)

        for (page in session.pages.sortedBy { it.ordinal }) {
            if (page.state != PageState.SCANNED) continue
            val source = page.sourceFile?.let { store.file(key, it) }
            if (source == null || !source.exists()) {
                store.updatePage(key, page.clientPhotoKey) {
                    it.copy(state = PageState.UNREADABLE, lastError = "The scanned photo was lost before it was saved. Scan this page again.")
                }
                continue
            }
            val uploadName = String.format(Locale.ROOT, "page-%03d.jpg", page.ordinal)
            val prepared = try {
                imageProcessor.prepare(source, store.file(key, uploadName))
            } catch (e: OutOfMemoryError) {
                return StepResult.Retry
            } catch (e: IOException) {
                store.updatePage(key, page.clientPhotoKey) {
                    it.copy(state = PageState.UNREADABLE, lastError = "This photo could not be opened (${e.message}). Scan this page again.")
                }
                continue
            }
            val m = prepared.report.measures
            store.updatePage(key, page.clientPhotoKey) {
                it.copy(
                    state = PageState.READY,
                    sourceFile = null,
                    uploadFile = uploadName,
                    sha256 = prepared.sha256,
                    bytes = prepared.bytes,
                    width = prepared.width,
                    height = prepared.height,
                    sharpness = m.sharpness,
                    meanLuma = m.meanLuma,
                    contrastSpread = m.contrastSpread,
                    glareFraction = m.glareFraction,
                    problems = prepared.report.problems.map { p -> p.code },
                )
            }
            source.delete()
            scheduler.enqueueUpload(key, page.clientPhotoKey)
        }

        // Pages prepared before the app was stopped may never have been queued.
        store.get(key)?.pages
            ?.filter { it.state == PageState.READY && !it.stopped }
            ?.forEach { scheduler.enqueueUpload(key, it.clientPhotoKey) }
        afterPageSettled(key)
        return StepResult.Done
    }

    /** Creates the laptop batch for a scan session if it has none yet. Retry-safe on the laptop. */
    suspend fun ensureBatch(key: String): String? = batchMutex.withLock {
        val session = store.get(key) ?: return@withLock null
        session.serverBatchId?.let { return@withLock it }
        if (session.kind != SessionKind.SCAN) return@withLock null
        when (val created = api.createBatch(session.key, session.pageCount)) {
            is ApiResult.Ok -> {
                store.update(key) { it.copy(serverBatchId = created.value.batchId, batchError = null) }
                created.value.batchId
            }
            else -> {
                store.update(key) { it.copy(batchError = created.plainMessage()) }
                null
            }
        }
    }

    /** One upload attempt for one page (contract §2). */
    suspend fun uploadPage(key: String, clientPhotoKey: String): StepResult {
        val session = store.get(key) ?: return StepResult.Done
        val page = session.pages.firstOrNull { it.clientPhotoKey == clientPhotoKey } ?: return StepResult.Done
        if (page.state != PageState.READY) return StepResult.Done

        val file = page.uploadFile?.let { store.file(key, it) }
        if (file == null || !file.exists() || page.sha256 == null) {
            store.updatePage(key, clientPhotoKey) {
                it.copy(state = PageState.UNREADABLE, lastError = "The saved photo is missing on this phone. Scan this page again.")
            }
            afterPageSettled(key)
            return StepResult.Failed
        }

        val localSha = Hashing.sha256Hex(file)
        val localBytes = file.length()
        if (!localSha.equals(page.sha256, ignoreCase = true) || (page.bytes != null && page.bytes != localBytes)) {
            store.updatePage(key, clientPhotoKey) {
                it.copy(stopped = true, lastError = "The saved photo changed on this phone since it was checked. Retake this page.")
            }
            return StepResult.Failed
        }

        val batchId = session.serverBatchId ?: ensureBatch(key)
        if (batchId == null) {
            val reason = store.get(key)?.batchError ?: "Waiting for the laptop to create the batch"
            store.updatePage(key, clientPhotoKey) { it.copy(lastError = reason, attempts = it.attempts + 1) }
            return StepResult.Retry
        }

        val quality = qualityHeaderFor(page)
        val result = uploadPermits.withPermit {
            api.uploadPhoto(batchId, clientPhotoKey, file, page.ordinal, localSha, quality, page.replacesPhotoId)
        }

        val status: Int?
        val errorCode: String?
        val receipt: PhotoReceiptDto?
        when (result) {
            is ApiResult.Ok -> {
                status = result.status
                errorCode = null
                receipt = result.value
            }
            is ApiResult.HttpError -> {
                status = result.status
                errorCode = result.error
                receipt = null
            }
            is ApiResult.NetworkError -> {
                status = null
                errorCode = null
                receipt = null
            }
            ApiResult.NotModified -> {
                status = 304
                errorCode = null
                receipt = null
            }
            ApiResult.NotPaired -> {
                store.updatePage(key, clientPhotoKey) {
                    it.copy(lastError = "This phone is not paired. Pair it with the laptop to upload.", attempts = it.attempts + 1)
                }
                return StepResult.Retry
            }
        }

        val check = receipt?.let {
            ReceiptVerification.verify(it.toReceipt(), LocalPhoto(batchId, clientPhotoKey, localSha, localBytes))
        }
        return when (val outcome = UploadPolicy.decide(status, errorCode, check)) {
            UploadOutcome.Stored -> {
                // Record the receipt first, then delete: a crash in between leaves a spare file, never a lost photo.
                store.updatePage(key, clientPhotoKey) {
                    it.copy(state = PageState.UPLOADED, photoId = receipt?.photoId, uploadFile = null, lastError = null, stopped = false)
                }
                file.delete()
                afterPageSettled(key)
                StepResult.Done
            }
            is UploadOutcome.RetryLater -> {
                if (status == 404 && session.kind == SessionKind.SCAN) {
                    // The laptop lost the batch: create it again (same client key) on the next attempt.
                    store.update(key) { it.copy(serverBatchId = null) }
                }
                store.updatePage(key, clientPhotoKey) { it.copy(lastError = outcome.reason, attempts = it.attempts + 1) }
                StepResult.Retry
            }
            is UploadOutcome.Stop -> {
                store.updatePage(key, clientPhotoKey) {
                    it.copy(lastError = outcome.message, stopped = true, attempts = it.attempts + 1)
                }
                StepResult.Failed
            }
        }
    }

    /** `POST …/complete` once every page of a scan is uploaded. */
    suspend fun completeSession(key: String): StepResult {
        val session = store.get(key) ?: return StepResult.Done
        if (session.kind != SessionKind.SCAN || session.completeSent || !session.allSettled) return StepResult.Done
        if (session.pages.none { it.state == PageState.UPLOADED }) return StepResult.Done
        val batchId = session.serverBatchId ?: return StepResult.Retry
        return when (val result = api.completeBatch(batchId)) {
            is ApiResult.Ok -> {
                store.update(key) { it.copy(completeSent = true, batchError = null) }
                StepResult.Done
            }
            is ApiResult.HttpError -> if (result.status == 423) {
                store.update(key) { it.copy(completeSent = true, batchError = null) }
                StepResult.Done
            } else {
                store.update(key) { it.copy(batchError = result.plainMessage()) }
                StepResult.Retry
            }
            else -> {
                store.update(key) { it.copy(batchError = result.plainMessage()) }
                StepResult.Retry
            }
        }
    }

    suspend fun afterPageSettled(key: String) {
        val session = store.get(key) ?: return
        val shouldComplete = UploadPolicy.shouldSendComplete(
            isScan = session.kind == SessionKind.SCAN,
            allSettled = session.allSettled,
            completeSent = session.completeSent,
            hasServerBatch = session.serverBatchId != null,
            uploadedPages = session.pages.count { it.state == PageState.UPLOADED },
            totalPages = session.pages.size,
        )
        if (shouldComplete) {
            scheduler.enqueueComplete(key)
        }
    }

    /** Old sessions whose photos are all on the laptop keep only a small manifest; drop those. */
    suspend fun pruneFinished(olderThanMillis: Long = 3L * 24 * 60 * 60 * 1000) {
        val cutoff = System.currentTimeMillis() - olderThanMillis
        store.all()
            .filter { it.finished && it.createdAtMillis < cutoff }
            .forEach { store.remove(it.key) }
    }

    private fun qualityHeaderFor(page: com.invoicecapture.data.pending.PendingPage): String? {
        val sharpness = page.sharpness ?: return null
        val width = page.width ?: return null
        val height = page.height ?: return null
        return QualityHeader.build(
            QualityMeasures(sharpness, page.meanLuma ?: 0.0, page.contrastSpread ?: 0, page.glareFraction ?: 0.0),
            width,
            height,
        )
    }

    /** Sessions still holding photos for a given laptop batch. */
    fun pagesOnPhoneFor(sessions: List<PendingSession>, batchId: String): Int =
        sessions.filter { it.serverBatchId == batchId }.sumOf { it.pagesOnPhone }
}
