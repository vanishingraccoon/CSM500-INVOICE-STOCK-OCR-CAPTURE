package com.invoicecapture.data.api

import kotlinx.serialization.ExperimentalSerializationApi
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.JsonUnquotedLiteral
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import com.invoicecapture.domain.batch.LineChoice
import com.invoicecapture.domain.batch.LineEdit
import com.invoicecapture.domain.batch.Money

/**
 * Request bodies built field by field, so they match the contract exactly: money with two
 * decimals (`200.00`), quantities as plain numbers (`2`), and `"unit": null` when there is none.
 */
@OptIn(ExperimentalSerializationApi::class)
object RequestBodies {

    fun pair(pairCode: String, deviceName: String): JsonObject = buildJsonObject {
        put("pair_code", pairCode)
        put("device_name", deviceName)
    }

    fun createBatch(clientBatchKey: String, pageCount: Int): JsonObject = buildJsonObject {
        put("client_batch_key", clientBatchKey)
        put("page_count", pageCount)
    }

    /** `PUT /invoices/{id}/lines/{index}` and `POST /invoices/{id}/lines`. */
    fun line(edit: LineEdit): JsonObject = buildJsonObject {
        when (val choice = edit.choice) {
            is LineChoice.StockItem -> {
                put("item_code", choice.code)
                put("quantity", JsonUnquotedLiteral(Money.plainNumber(edit.quantity)))
                put("unit", edit.unit?.let { JsonPrimitive(it) } ?: JsonNull)
                put("cost", JsonUnquotedLiteral(Money.twoDecimals(edit.cost)))
            }
            is LineChoice.FreeText -> {
                put("free_text", choice.text)
                put("quantity", JsonUnquotedLiteral(Money.plainNumber(edit.quantity)))
                put("cost", JsonUnquotedLiteral(Money.twoDecimals(edit.cost)))
            }
        }
    }

    /** `PATCH /invoices/{id}`. */
    fun patchInvoice(supplier: String, documentNo: String, date: String): JsonObject = buildJsonObject {
        put("supplier", supplier)
        put("document_no", documentNo)
        put("date", date)
    }

    /** For POSTs that carry no fields (complete, delete, not_duplicate, submit). */
    val empty: JsonObject = JsonObject(emptyMap())
}
