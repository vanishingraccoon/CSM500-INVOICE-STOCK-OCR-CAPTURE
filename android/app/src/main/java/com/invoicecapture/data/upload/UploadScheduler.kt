package com.invoicecapture.data.upload

import android.content.Context
import androidx.work.BackoffPolicy
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingWorkPolicy
import androidx.work.ListenableWorker
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkInfo
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import androidx.work.workDataOf
import kotlinx.coroutines.flow.first
import com.invoicecapture.InvoiceCaptureApp
import com.invoicecapture.data.pending.PageState
import com.invoicecapture.data.pending.PendingStore
import com.invoicecapture.data.pending.SessionKind
import com.invoicecapture.domain.upload.UploadPolicy
import java.time.Duration

/**
 * Queues the background work. WorkManager keeps it across app close and phone restarts, waits
 * for a network, and retries failed attempts with exponential backoff.
 */
class UploadScheduler(context: Context) {
    private val workManager = WorkManager.getInstance(context.applicationContext)

    private val network = Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build()

    fun enqueuePrepare(sessionKey: String) {
        val request = OneTimeWorkRequestBuilder<PrepareScanWorker>()
            .setInputData(workDataOf(KEY_SESSION to sessionKey))
            .addTag(TAG_PREPARE)
            .build()
        workManager.enqueueUniqueWork("prepare-$sessionKey", ExistingWorkPolicy.KEEP, request)
    }

    fun enqueueUpload(sessionKey: String, clientPhotoKey: String, restart: Boolean = false) {
        val request = OneTimeWorkRequestBuilder<UploadWorker>()
            .setInputData(workDataOf(KEY_SESSION to sessionKey, KEY_PHOTO to clientPhotoKey))
            .setConstraints(network)
            .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, Duration.ofSeconds(UploadPolicy.INITIAL_BACKOFF_SECONDS))
            .addTag(TAG_UPLOAD)
            .build()
        workManager.enqueueUniqueWork(
            uploadName(clientPhotoKey),
            if (restart) ExistingWorkPolicy.REPLACE else ExistingWorkPolicy.KEEP,
            request,
        )
    }

    fun enqueueComplete(sessionKey: String, restart: Boolean = false) {
        val request = OneTimeWorkRequestBuilder<CompleteBatchWorker>()
            .setInputData(workDataOf(KEY_SESSION to sessionKey))
            .setConstraints(network)
            .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, Duration.ofSeconds(UploadPolicy.INITIAL_BACKOFF_SECONDS))
            .addTag(TAG_COMPLETE)
            .build()
        workManager.enqueueUniqueWork(
            completeName(sessionKey),
            if (restart) ExistingWorkPolicy.REPLACE else ExistingWorkPolicy.KEEP,
            request,
        )
    }

    /** Stops the work for one page: the manager discarded it, so nothing is left to upload. */
    fun cancelUpload(clientPhotoKey: String) {
        workManager.cancelUniqueWork(uploadName(clientPhotoKey))
    }

    /**
     * Queues everything still on the phone again. Work waiting out a backoff is restarted so it
     * goes now (used when the laptop becomes reachable, on app start and after pairing). Stopped
     * pages (409/422) are only included when the manager asks.
     */
    suspend fun resumeAll(store: PendingStore, includeStopped: Boolean) {
        for (session in store.all()) {
            if (session.pages.any { it.state == PageState.SCANNED }) enqueuePrepare(session.key)
            for (page in session.pages) {
                if (page.state != PageState.READY) continue
                if (page.stopped && !includeStopped) continue
                if (page.stopped) {
                    store.updatePage(session.key, page.clientPhotoKey) { it.copy(stopped = false, lastError = null) }
                }
                if (!isRunning(uploadName(page.clientPhotoKey))) {
                    enqueueUpload(session.key, page.clientPhotoKey, restart = true)
                }
            }
            if (session.kind == SessionKind.SCAN && session.allSettled && !session.completeSent &&
                session.pages.any { it.state == PageState.UPLOADED } && !isRunning(completeName(session.key))
            ) {
                enqueueComplete(session.key, restart = true)
            }
        }
    }

    private suspend fun isRunning(uniqueName: String): Boolean =
        workManager.getWorkInfosForUniqueWorkFlow(uniqueName).first().any { it.state == WorkInfo.State.RUNNING }

    companion object {
        const val KEY_SESSION = "session"
        const val KEY_PHOTO = "photo"
        const val TAG_PREPARE = "prepare"
        const val TAG_UPLOAD = "upload"
        const val TAG_COMPLETE = "complete"

        fun uploadName(clientPhotoKey: String) = "upload-$clientPhotoKey"
        fun completeName(sessionKey: String) = "complete-$sessionKey"
    }
}

private val CoroutineWorker.engine: UploadEngine
    get() = (applicationContext as InvoiceCaptureApp).container.uploadEngine

private fun StepResult.toWorkResult(): ListenableWorker.Result = when (this) {
    StepResult.Done -> ListenableWorker.Result.success()
    StepResult.Retry -> ListenableWorker.Result.retry()
    StepResult.Failed -> ListenableWorker.Result.failure()
}

/** Checks, downscales and hashes the pages of one scan, then queues their uploads. */
class PrepareScanWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {
    override suspend fun doWork(): Result {
        val key = inputData.getString(UploadScheduler.KEY_SESSION) ?: return Result.failure()
        return engine.prepareSession(key).toWorkResult()
    }
}

/** Uploads one page and deletes the phone's copy only after a matching receipt. */
class UploadWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {
    override suspend fun doWork(): Result {
        val key = inputData.getString(UploadScheduler.KEY_SESSION) ?: return Result.failure()
        val photo = inputData.getString(UploadScheduler.KEY_PHOTO) ?: return Result.failure()
        return engine.uploadPage(key, photo).toWorkResult()
    }
}

/** Tells the laptop that every photo of a scan has arrived. */
class CompleteBatchWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {
    override suspend fun doWork(): Result {
        val key = inputData.getString(UploadScheduler.KEY_SESSION) ?: return Result.failure()
        return engine.completeSession(key).toWorkResult()
    }
}
