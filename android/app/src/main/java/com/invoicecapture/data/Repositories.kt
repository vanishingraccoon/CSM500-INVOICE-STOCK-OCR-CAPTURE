package com.invoicecapture.data

import android.content.Context
import android.net.Uri
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import com.invoicecapture.data.api.ApiResult
import com.invoicecapture.data.api.BatchDetailDto
import com.invoicecapture.data.api.BatchListResponse
import com.invoicecapture.data.api.InvoiceApi
import com.invoicecapture.data.api.InvoiceDto
import com.invoicecapture.data.api.PairResponse
import com.invoicecapture.data.api.StockSearchResponse
import com.invoicecapture.data.api.SubmitResponse
import com.invoicecapture.data.api.RequestBodies
import com.invoicecapture.data.api.PingResponse
import com.invoicecapture.data.pairing.PairingStore
import com.invoicecapture.data.pending.PendingPage
import com.invoicecapture.data.pending.PendingSession
import com.invoicecapture.data.pending.PendingStore
import com.invoicecapture.data.pending.SessionKind
import com.invoicecapture.data.upload.UploadEngine
import com.invoicecapture.data.upload.UploadScheduler
import com.invoicecapture.domain.batch.DuplicateLookup
import com.invoicecapture.domain.batch.LineEdit
import java.io.File
import java.io.FileOutputStream
import java.io.IOException
import java.util.Locale
import java.util.UUID
import java.util.concurrent.ConcurrentHashMap

/** Batches, invoices and manager actions on the laptop, with an ETag cache per batch. */
class InvoiceRepository(private val api: InvoiceApi, private val pairingStore: PairingStore) {

    private data class Cached(val etag: String?, val detail: BatchDetailDto)

    private val cache = ConcurrentHashMap<String, Cached>()

    suspend fun pair(server: String, pairCode: String, deviceName: String): ApiResult<PairResponse> {
        val result = api.pair(server, pairCode, deviceName)
        if (result is ApiResult.Ok) pairingStore.save(server, result.value, deviceName)
        return result
    }

    suspend fun ping(): ApiResult<PingResponse> = api.ping()

    suspend fun batches(): ApiResult<BatchListResponse> = api.listBatches()

    /** `GET` with `If-None-Match`; a 304 hands back the cached copy as an [ApiResult.Ok]. */
    suspend fun batch(batchId: String): ApiResult<BatchDetailDto> {
        val cached = cache[batchId]
        return when (val result = api.getBatch(batchId, cached?.etag)) {
            is ApiResult.Ok -> {
                cache[batchId] = Cached(result.etag, result.value)
                result
            }
            ApiResult.NotModified -> if (cached != null) {
                ApiResult.Ok(cached.detail, 304, cached.etag)
            } else {
                api.getBatch(batchId, null).also { if (it is ApiResult.Ok) cache[batchId] = Cached(it.etag, it.value) }
            }
            else -> result
        }
    }

    fun cachedBatch(batchId: String): BatchDetailDto? = cache[batchId]?.detail

    /** Shows a changed invoice at once; the ETag is dropped so the next poll fetches new counts. */
    fun applyInvoice(batchId: String, invoice: InvoiceDto): BatchDetailDto? {
        val cached = cache[batchId] ?: return null
        val updated = cached.detail.copy(
            invoices = cached.detail.invoices.map { if (it.invoiceId == invoice.invoiceId) invoice else it },
        )
        cache[batchId] = Cached(null, updated)
        return updated
    }

    suspend fun searchStock(query: String): ApiResult<StockSearchResponse> = api.searchStock(query)

    /** [index] null adds a typed line (`POST`); otherwise the manager's entry replaces that line (`PUT`). */
    suspend fun saveLine(invoiceId: String, index: Int?, edit: LineEdit): ApiResult<InvoiceDto> =
        if (index == null) api.addLine(invoiceId, RequestBodies.line(edit))
        else api.updateLine(invoiceId, index, RequestBodies.line(edit))

    suspend fun deleteLine(invoiceId: String, index: Int): ApiResult<InvoiceDto> = api.deleteLine(invoiceId, index)

    suspend fun editHeader(invoiceId: String, supplier: String, documentNo: String, date: String): ApiResult<InvoiceDto> =
        api.patchInvoice(invoiceId, RequestBodies.patchInvoice(supplier, documentNo, date))

    suspend fun deleteDuplicate(invoiceId: String): ApiResult<InvoiceDto> = api.deleteInvoice(invoiceId)

    suspend fun notDuplicate(invoiceId: String): ApiResult<InvoiceDto> = api.notDuplicate(invoiceId)

    suspend fun submit(batchId: String): ApiResult<SubmitResponse> = api.submitBatch(batchId)

    /** A copy already loaded on this phone, for the invoice a duplicate repeats; no call to the laptop. */
    fun loadedInvoice(invoiceId: String, currentBatchId: String): InvoiceDto? = DuplicateLookup.localCopy(
        currentBatch = cache[currentBatchId]?.detail?.invoices.orEmpty(),
        loadedBefore = cache.filterKeys { it != currentBatchId }.values.flatMap { it.detail.invoices },
        invoiceId = invoiceId,
    ) { it.invoiceId }

    /** §3 `GET /api/v1/invoices/{invoice_id}`: one invoice, in any batch of this shop. */
    suspend fun invoice(invoiceId: String): ApiResult<InvoiceDto> = api.getInvoice(invoiceId)

    fun photoUrl(photoId: String): String? =
        pairingStore.latest?.server?.let { InvoiceApi.photoUrl(it, photoId) }

    fun portalUrl(path: String): String? =
        pairingStore.latest?.server?.let { InvoiceApi.portalUrl(it, path) }
}

/** Starts scan and retake sessions from the document scanner's result. */
class CaptureRepository(
    private val context: Context,
    private val store: PendingStore,
    private val scheduler: UploadScheduler,
    private val engine: UploadEngine,
) {
    /**
     * Copies the scanner's JPEGs into the app's own storage straight away (they sit in a cache
     * that Android may clear), writes the session manifest and queues the preparation work.
     * Returns the session key (the `client_batch_key`).
     */
    suspend fun startScan(uris: List<Uri>): String = withContext(Dispatchers.IO) {
        require(uris.isNotEmpty()) { "no pages" }
        val key = UUID.randomUUID().toString()
        val pages = try {
            uris.mapIndexed { i, uri ->
                val ordinal = i + 1
                val name = String.format(Locale.ROOT, "scan-%03d.jpg", ordinal)
                copy(uri, store.file(key, name))
                PendingPage(ordinal = ordinal, clientPhotoKey = UUID.randomUUID().toString(), sourceFile = name)
            }
        } catch (e: IOException) {
            store.sessionDir(key).deleteRecursively()
            throw e
        }
        store.put(
            PendingSession(
                key = key,
                kind = SessionKind.SCAN,
                createdAtMillis = System.currentTimeMillis(),
                pageCount = pages.size,
                pages = pages,
            ),
        )
        uris.forEach { deleteScannerCopy(it) }
        scheduler.enqueuePrepare(key)
        key
    }

    /** One page again for an invoice in the Retake tab; uploads with `X-Replaces-Photo`. */
    suspend fun startRetake(uri: Uri, batchId: String, replacesPhotoId: String, ordinal: Int): String =
        withContext(Dispatchers.IO) {
            val key = UUID.randomUUID().toString()
            val name = String.format(Locale.ROOT, "scan-%03d.jpg", ordinal)
            try {
                copy(uri, store.file(key, name))
            } catch (e: IOException) {
                store.sessionDir(key).deleteRecursively()
                throw e
            }
            store.put(
                PendingSession(
                    key = key,
                    kind = SessionKind.RETAKE,
                    createdAtMillis = System.currentTimeMillis(),
                    pageCount = 1,
                    serverBatchId = batchId,
                    pages = listOf(
                        PendingPage(
                            ordinal = ordinal,
                            clientPhotoKey = UUID.randomUUID().toString(),
                            sourceFile = name,
                            replacesPhotoId = replacesPhotoId,
                        ),
                    ),
                ),
            )
            deleteScannerCopy(uri)
            scheduler.enqueuePrepare(key)
            key
        }

    suspend fun retryAll() = scheduler.resumeAll(store, includeStopped = true)

    /**
     * The manager gave up on a page the laptop refused (409/422): its work is cancelled, its photo
     * on this phone is deleted and the page leaves the queue, so no later retry can send it. The
     * invoice stays on paper. The rest of the session carries on and can still be completed.
     */
    suspend fun discardPage(sessionKey: String, clientPhotoKey: String) = withContext(Dispatchers.IO) {
        val page = store.get(sessionKey)?.pages?.firstOrNull { it.clientPhotoKey == clientPhotoKey } ?: return@withContext
        scheduler.cancelUpload(clientPhotoKey)
        listOfNotNull(page.sourceFile, page.uploadFile).forEach { store.file(sessionKey, it).delete() }
        store.discardPage(sessionKey, clientPhotoKey)
        engine.afterPageSettled(sessionKey)
    }

    private fun copy(uri: Uri, target: File) {
        target.parentFile?.mkdirs()
        val input = context.contentResolver.openInputStream(uri) ?: throw IOException("The scanner's photo could not be opened")
        input.use { source ->
            FileOutputStream(target).use { out ->
                source.copyTo(out)
                out.flush()
                out.fd.sync()
            }
        }
    }

    /** The scanner writes into this app's cache; tidy it once our own copy is safe. */
    private fun deleteScannerCopy(uri: Uri) {
        if (uri.scheme != "file") return
        val path = uri.path ?: return
        val file = File(path)
        if (file.canonicalPath.startsWith(context.cacheDir.canonicalPath)) file.delete()
    }
}
