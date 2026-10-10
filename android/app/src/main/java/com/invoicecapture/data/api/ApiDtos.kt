package com.invoicecapture.data.api

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull
import com.invoicecapture.domain.batch.InvoiceTab
import com.invoicecapture.domain.upload.Receipt

/**
 * JSON for the phone ⇄ laptop contract v1 (invoice-pipeline/docs/phone-api.md). Field names
 * follow the contract exactly; unknown fields are ignored so the laptop can add to replies.
 */
val ApiJson: Json = Json {
    ignoreUnknownKeys = true
    coerceInputValues = true
    explicitNulls = false
    encodeDefaults = true
}

// §1 Pairing

@Serializable
data class PairResponse(
    @SerialName("device_id") val deviceId: String,
    val token: String,
    val shop: String,
    @SerialName("shop_name") val shopName: String? = null,
)

@Serializable
data class PingResponse(
    val ok: Boolean = false,
    @SerialName("server_time") val serverTime: String? = null,
    val shop: String? = null,
    val queue: QueueDto? = null,
)

@Serializable
data class QueueDto(val waiting: Int = 0, val reading: Int = 0)

// §2 Batches and uploads

@Serializable
data class BatchCreated(
    @SerialName("batch_id") val batchId: String,
    val status: String? = null,
    @SerialName("created_at") val createdAt: String? = null,
)

/** The receipt for a stored photo. */
@Serializable
data class PhotoReceiptDto(
    @SerialName("photo_id") val photoId: String? = null,
    @SerialName("batch_id") val batchId: String? = null,
    @SerialName("client_photo_key") val clientPhotoKey: String? = null,
    val sha256: String? = null,
    val bytes: Long? = null,
    val state: String? = null,
) {
    fun toReceipt() = Receipt(batchId = batchId, clientPhotoKey = clientPhotoKey, sha256 = sha256, bytes = bytes)
}

@Serializable
data class StatusResponse(val status: String? = null)

// §3 Reading the results

@Serializable
data class BatchListResponse(val batches: List<BatchSummaryDto> = emptyList())

/** A batch in the list. The contract does not spell the summary out, so everything but the id is optional. */
@Serializable
data class BatchSummaryDto(
    @SerialName("batch_id") val batchId: String,
    val status: String? = null,
    @SerialName("created_at") val createdAt: String? = null,
    @SerialName("submitted_at") val submittedAt: String? = null,
    val progress: ProgressDto? = null,
    val counts: CountsDto? = null,
)

@Serializable
data class BatchDetailDto(
    @SerialName("batch_id") val batchId: String,
    val status: String? = null,
    @SerialName("created_at") val createdAt: String? = null,
    @SerialName("submitted_at") val submittedAt: String? = null,
    val progress: ProgressDto = ProgressDto(),
    val counts: CountsDto = CountsDto(),
    val invoices: List<InvoiceDto> = emptyList(),
)

@Serializable
data class ProgressDto(
    val photos: Int = 0,
    val waiting: Int = 0,
    val reading: Int = 0,
    val read: Int = 0,
    val failed: Int = 0,
)

@Serializable
data class CountsDto(
    val good: Int = 0,
    val corrections: Int = 0,
    val retake: Int = 0,
    val duplicates: Int = 0,
) {
    fun forTab(tab: InvoiceTab): Int = when (tab) {
        InvoiceTab.GOOD -> good
        InvoiceTab.CORRECTIONS -> corrections
        InvoiceTab.RETAKE -> retake
        InvoiceTab.DUPLICATES -> duplicates
    }

    fun asMap(): Map<InvoiceTab, Int> = InvoiceTab.ORDER.associateWith { forTab(it) }
}

@Serializable
data class InvoiceDto(
    @SerialName("invoice_id") val invoiceId: String,
    val tab: String? = null,
    @SerialName("tab_reasons") val tabReasons: List<String> = emptyList(),
    val supplier: String? = null,
    @SerialName("supplier_name") val supplierName: String? = null,
    @SerialName("document_no") val documentNo: String? = null,
    val date: String? = null,
    val pages: List<PageDto> = emptyList(),
    @SerialName("stated_total_excl") val statedTotalExcl: Double? = null,
    @SerialName("stated_vat") val statedVat: Double? = null,
    @SerialName("stated_total_incl") val statedTotalIncl: Double? = null,
    @SerialName("lines_sum_excl") val linesSumExcl: Double? = null,
    @SerialName("adds_up") val addsUp: Boolean? = null,
    /** Names the other invoice; kept as raw JSON in case the laptop sends an object. */
    @SerialName("duplicate_of") val duplicateOf: JsonElement? = null,
    val lines: List<LineDto> = emptyList(),
    @SerialName("updated_at") val updatedAt: String? = null,
) {
    val tabEnum: InvoiceTab? get() = InvoiceTab.fromWire(tab)
    val firstOrdinal: Int? get() = pages.minOfOrNull { it.ordinal }
    val pagesInOrder: List<PageDto> get() = pages.sortedWith(compareBy({ it.pageNo }, { it.ordinal }))
    val duplicateOfId: String? get() = duplicateOf.idText("invoice_id")

    /**
     * The supplier as the manager knows it: the Pilot name the laptop sends (`supplier_name`),
     * falling back to the laptop's own key (`supplier`, e.g. `vassco`) when there is no name.
     * Shown wherever the supplier appears; only the editor asks for the key itself.
     */
    val title: String get() = supplierName?.takeIf { it.isNotBlank() } ?: supplier?.takeIf { it.isNotBlank() } ?: "Unknown supplier"
}

@Serializable
data class PageDto(
    @SerialName("photo_id") val photoId: String,
    val ordinal: Int = 0,
    @SerialName("page_no") val pageNo: Int = 0,
    val state: String? = null,
    val quality: PageQualityDto? = null,
)

@Serializable
data class PageQualityDto(val ok: Boolean = true, val problems: List<String> = emptyList())

@Serializable
data class LineDto(
    val index: Int,
    val star: Boolean = false,
    @SerialName("entered_by") val enteredBy: String? = null,
    val printed: PrintedDto? = null,
    val item: ItemDto? = null,
    @SerialName("base_quantity") val baseQuantity: Double? = null,
    /** Why the key could not convert; kept as raw JSON (a string or an object). */
    val refusal: JsonElement? = null,
    val reason: String? = null,
    val flags: List<FlagDto> = emptyList(),
    val candidates: List<CandidateDto> = emptyList(),
    /** Extras the laptop sends beyond contract v1: the line's current entry (the manager's, once typed). Optional. */
    @SerialName("free_text") val freeText: String? = null,
    val quantity: Double? = null,
    val unit: String? = null,
    val cost: Double? = null,
) {
    val refusalText: String? get() = refusal.plainText()
    val byManager: Boolean get() = enteredBy == "manager"
}

@Serializable
data class PrintedDto(
    val description: String? = null,
    val quantity: Double? = null,
    val unit: String? = null,
    val pack: String? = null,
    @SerialName("unit_price") val unitPrice: Double? = null,
    @SerialName("line_total") val lineTotal: Double? = null,
)

@Serializable
data class ItemDto(
    val code: String,
    val name: String? = null,
    @SerialName("base_unit") val baseUnit: String? = null,
)

@Serializable
data class FlagDto(val kind: String? = null, val reason: String? = null)

@Serializable
data class CandidateDto(val code: String, val name: String? = null, val score: Double? = null)

// §5 Manager actions

@Serializable
data class StockSearchResponse(val items: List<StockItemDto> = emptyList())

@Serializable
data class StockItemDto(
    val code: String,
    val name: String? = null,
    @SerialName("base_unit") val baseUnit: String? = null,
    val category: String? = null,
)

// §7 Submit

@Serializable
data class SubmitResponse(val status: String? = null, val report: ReportDto? = null)

@Serializable
data class ReportDto(val pdf: String? = null, val csv: String? = null)

// Errors

@Serializable
data class ErrorDto(val error: String? = null, val message: String? = null)

private fun JsonElement?.plainText(): String? = when (this) {
    null -> null
    is JsonPrimitive -> contentOrNull
    is JsonObject -> listOf("reason", "message", "category")
        .firstNotNullOfOrNull { (this[it] as? JsonPrimitive)?.contentOrNull } ?: toString()
    is JsonArray -> mapNotNull { it.plainText() }.joinToString("; ").ifEmpty { null }
}

private fun JsonElement?.idText(key: String): String? = when (this) {
    null -> null
    is JsonPrimitive -> contentOrNull
    is JsonObject -> (this[key] as? JsonPrimitive)?.contentOrNull
    is JsonArray -> null
}
