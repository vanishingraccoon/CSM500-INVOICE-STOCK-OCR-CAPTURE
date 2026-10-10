package com.invoicecapture.data.api

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withContext
import kotlinx.serialization.KSerializer
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import okhttp3.Call
import okhttp3.Callback
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import okhttp3.Interceptor
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody
import okhttp3.RequestBody.Companion.asRequestBody
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import com.invoicecapture.data.pairing.PairingStore
import java.io.File
import java.io.IOException
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException

/**
 * The laptop's phone API (contract v1). Every call except [pair] sends the device token as
 * `Authorization: Bearer …`. Nothing here throws for network or HTTP problems: see [ApiResult].
 */
class InvoiceApi(
    private val client: OkHttpClient,
    private val pairingStore: PairingStore,
    private val json: Json = ApiJson,
) {
    private val jsonMedia = "application/json; charset=utf-8".toMediaType()
    private val jpegMedia = "image/jpeg".toMediaType()

    /** §1 `POST /api/v1/pair` — the only call without a token. */
    suspend fun pair(server: String, pairCode: String, deviceName: String): ApiResult<PairResponse> {
        val base = server.toHttpUrlOrNull()
            ?: return ApiResult.HttpError(0, "bad_server", "The laptop address is not valid.")
        val url = base.newBuilder().addPathSegments("api/v1/pair").build()
        val request = Request.Builder()
            .url(url)
            .post(RequestBodies.pair(pairCode, deviceName).toJsonBody())
            .build()
        return execute(request, PairResponse.serializer())
    }

    /** §1 `GET /api/v1/ping`. */
    suspend fun ping(): ApiResult<PingResponse> =
        call(listOf("ping"), PingResponse.serializer(), "GET")

    /** §2 `POST /api/v1/batches` — retry-safe for the same client key. */
    suspend fun createBatch(clientBatchKey: String, pageCount: Int): ApiResult<BatchCreated> =
        call(listOf("batches"), BatchCreated.serializer(), "POST", RequestBodies.createBatch(clientBatchKey, pageCount).toJsonBody())

    /** §2 `PUT /api/v1/batches/{batch_id}/photos/{client_photo_key}` with the raw JPEG. */
    suspend fun uploadPhoto(
        batchId: String,
        clientPhotoKey: String,
        file: File,
        ordinal: Int,
        sha256: String,
        qualityHeader: String?,
        replacesPhotoId: String?,
    ): ApiResult<PhotoReceiptDto> {
        val headers = buildMap {
            put("X-Photo-Ordinal", ordinal.toString())
            put("X-Content-SHA256", sha256)
            if (qualityHeader != null) put("X-Quality", qualityHeader)
            if (replacesPhotoId != null) put("X-Replaces-Photo", replacesPhotoId)
        }
        return call(
            listOf("batches", batchId, "photos", clientPhotoKey),
            PhotoReceiptDto.serializer(),
            "PUT",
            file.asRequestBody(jpegMedia),
            headers = headers,
        )
    }

    /** §2 `POST /api/v1/batches/{batch_id}/complete`. */
    suspend fun completeBatch(batchId: String): ApiResult<StatusResponse> =
        call(listOf("batches", batchId, "complete"), StatusResponse.serializer(), "POST", RequestBodies.empty.toJsonBody())

    /** §3 `GET /api/v1/batches`, newest first. */
    suspend fun listBatches(): ApiResult<BatchListResponse> =
        call(listOf("batches"), BatchListResponse.serializer(), "GET")

    /** §3 `GET /api/v1/batches/{batch_id}` with `If-None-Match` when an ETag is known. */
    suspend fun getBatch(batchId: String, etag: String?): ApiResult<BatchDetailDto> =
        call(
            listOf("batches", batchId),
            BatchDetailDto.serializer(),
            "GET",
            headers = if (etag != null) mapOf("If-None-Match" to etag) else emptyMap(),
        )

    /** §3 `GET /api/v1/invoices/{invoice_id}` — one invoice, in any batch of this shop. */
    suspend fun getInvoice(invoiceId: String): ApiResult<InvoiceDto> =
        call(listOf("invoices", invoiceId), InvoiceDto.serializer(), "GET")

    /** §5 `GET /api/v1/stock/search?q=…&limit=20`. */
    suspend fun searchStock(query: String, limit: Int = 20): ApiResult<StockSearchResponse> =
        call(listOf("stock", "search"), StockSearchResponse.serializer(), "GET", query = mapOf("q" to query, "limit" to "$limit"))

    /** §5 `PUT /api/v1/invoices/{invoice_id}/lines/{index}`. */
    suspend fun updateLine(invoiceId: String, index: Int, body: JsonObject): ApiResult<InvoiceDto> =
        call(listOf("invoices", invoiceId, "lines", index.toString()), InvoiceDto.serializer(), "PUT", body.toJsonBody())

    /** §5 `POST /api/v1/invoices/{invoice_id}/lines`. */
    suspend fun addLine(invoiceId: String, body: JsonObject): ApiResult<InvoiceDto> =
        call(listOf("invoices", invoiceId, "lines"), InvoiceDto.serializer(), "POST", body.toJsonBody())

    /** §5 `DELETE /api/v1/invoices/{invoice_id}/lines/{index}`. */
    suspend fun deleteLine(invoiceId: String, index: Int): ApiResult<InvoiceDto> =
        call(listOf("invoices", invoiceId, "lines", index.toString()), InvoiceDto.serializer(), "DELETE")

    /** §5 `PATCH /api/v1/invoices/{invoice_id}`. */
    suspend fun patchInvoice(invoiceId: String, body: JsonObject): ApiResult<InvoiceDto> =
        call(listOf("invoices", invoiceId), InvoiceDto.serializer(), "PATCH", body.toJsonBody())

    /** §5 `POST /api/v1/invoices/{invoice_id}/delete` (a duplicate). */
    suspend fun deleteInvoice(invoiceId: String): ApiResult<InvoiceDto> =
        call(listOf("invoices", invoiceId, "delete"), InvoiceDto.serializer(), "POST", RequestBodies.empty.toJsonBody())

    /** §5 `POST /api/v1/invoices/{invoice_id}/not_duplicate`. */
    suspend fun notDuplicate(invoiceId: String): ApiResult<InvoiceDto> =
        call(listOf("invoices", invoiceId, "not_duplicate"), InvoiceDto.serializer(), "POST", RequestBodies.empty.toJsonBody())

    /** §7 `POST /api/v1/batches/{batch_id}/submit`. */
    suspend fun submitBatch(batchId: String): ApiResult<SubmitResponse> =
        call(listOf("batches", batchId, "submit"), SubmitResponse.serializer(), "POST", RequestBodies.empty.toJsonBody())

    private suspend fun <T> call(
        segments: List<String>,
        serializer: KSerializer<T>,
        method: String,
        body: RequestBody? = null,
        query: Map<String, String> = emptyMap(),
        headers: Map<String, String> = emptyMap(),
    ): ApiResult<T> {
        val pairing = pairingStore.current() ?: return ApiResult.NotPaired
        val base = pairing.server.toHttpUrlOrNull() ?: return ApiResult.NotPaired
        val url = base.newBuilder().apply {
            addPathSegments("api/v1")
            segments.forEach { addPathSegment(it) }
            query.forEach { (k, v) -> addQueryParameter(k, v) }
        }.build()
        val request = Request.Builder()
            .url(url)
            .header("Authorization", "Bearer ${pairing.token}")
            .apply { headers.forEach { (k, v) -> header(k, v) } }
            .method(method, body)
            .build()
        return execute(request, serializer)
    }

    private suspend fun <T> execute(request: Request, serializer: KSerializer<T>): ApiResult<T> =
        withContext(Dispatchers.IO) {
            try {
                client.newCall(request).await().use { response ->
                    val status = response.code
                    val text = response.body.string()
                    when {
                        status == 304 -> ApiResult.NotModified
                        status in 200..299 -> try {
                            ApiResult.Ok(json.decodeFromString(serializer, text), status, response.header("ETag"))
                        } catch (e: IllegalArgumentException) {
                            ApiResult.HttpError(status, "bad_reply", null)
                        }
                        else -> {
                            val error = try {
                                json.decodeFromString(ErrorDto.serializer(), text)
                            } catch (e: IllegalArgumentException) {
                                null
                            }
                            ApiResult.HttpError(status, error?.error, error?.message)
                        }
                    }
                }
            } catch (e: IOException) {
                ApiResult.NetworkError(e)
            }
        }

    private fun JsonObject.toJsonBody(): RequestBody = toString().toRequestBody(jsonMedia)

    companion object {
        /** `GET /api/v1/photos/{photo_id}/image?w=1080` — a downscaled copy for the screen. */
        fun photoUrl(server: String, photoId: String, width: Int = 1080): String? =
            server.toHttpUrlOrNull()?.newBuilder()
                ?.addPathSegments("api/v1/photos")
                ?.addPathSegment(photoId)
                ?.addPathSegment("image")
                ?.addQueryParameter("w", width.toString())
                ?.build()
                ?.toString()

        /** A portal path such as `/portal/batches/b_1/report.pdf` on the paired laptop. */
        fun portalUrl(server: String, path: String): String? =
            server.toHttpUrlOrNull()?.resolve(path)?.toString()
    }
}

/**
 * Adds the device token to image requests (Coil), but only when they go to the paired laptop,
 * so the token never leaves for any other host.
 */
class DeviceTokenInterceptor(private val pairingStore: PairingStore) : Interceptor {
    override fun intercept(chain: Interceptor.Chain): Response {
        val request = chain.request()
        val pairing = pairingStore.latest ?: return chain.proceed(request)
        val server = pairing.server.toHttpUrlOrNull() ?: return chain.proceed(request)
        val sameServer = request.url.scheme == server.scheme &&
            request.url.host == server.host &&
            request.url.port == server.port
        if (!sameServer) return chain.proceed(request)
        return chain.proceed(request.newBuilder().header("Authorization", "Bearer ${pairing.token}").build())
    }
}

private suspend fun Call.await(): Response = suspendCancellableCoroutine { continuation ->
    continuation.invokeOnCancellation { cancel() }
    enqueue(object : Callback {
        override fun onFailure(call: Call, e: IOException) {
            if (continuation.isActive) continuation.resumeWithException(e)
        }

        override fun onResponse(call: Call, response: Response) {
            if (continuation.isActive) continuation.resume(response) else response.close()
        }
    })
}
