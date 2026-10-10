package com.invoicecapture.data.api

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.math.BigDecimal
import com.invoicecapture.domain.batch.InvoiceTab
import com.invoicecapture.domain.batch.LineChoice
import com.invoicecapture.domain.batch.LineEdit
import com.invoicecapture.domain.upload.Receipt

/** The example JSON of the contract (docs/phone-api.md), with {line} and {invoice} filled in. */
class DtoParsingTest {

    private val lineJson = """
        {
          "index": 0, "star": false, "entered_by": "system|manager",
          "printed": {"description": "Honey 5L", "quantity": 2, "unit": null, "pack": "5L", "unit_price": 100.00, "line_total": 200.00},
          "item": {"code": "1189", "name": "honey (LITRE)", "base_unit": "l"},
          "base_quantity": 10.0, "refusal": null, "reason": "matched 'honey (LITRE)' by history (score 106)",
          "flags": [{"kind": "price_outlier", "reason": "plain words"}],
          "candidates": [{"code": "1189", "name": "honey (LITRE)", "score": 106.0}]
        }
    """.trimIndent()

    private val invoiceJson = """
        {
          "invoice_id": "i_…", "tab": "good|corrections|retake|duplicates|deleted",
          "tab_reasons": ["line 3: no stock item matched", "does not add up"],
          "supplier": "vassco", "supplier_name": "Vassco", "document_no": "1844378", "date": "2026-08-31",
          "pages": [{"photo_id": "p_…", "ordinal": 3, "page_no": 1, "state": "read", "quality": {"ok": true, "problems": []}}],
          "stated_total_excl": 3000.00, "stated_vat": 450.00, "stated_total_incl": 3450.00,
          "lines_sum_excl": 3000.00, "adds_up": true,
          "duplicate_of": null,
          "lines": [ $lineJson ],
          "updated_at": "..."
        }
    """.trimIndent()

    private val batchJson = """
        {
          "batch_id": "b_…", "status": "uploading|processing|ready|submitted",
          "created_at": "...", "submitted_at": null,
          "progress": {"photos": 14, "waiting": 3, "reading": 2, "read": 9, "failed": 0},
          "counts": {"good": 4, "corrections": 2, "retake": 1, "duplicates": 1},
          "invoices": [ $invoiceJson ]
        }
    """.trimIndent()

    @Test
    fun batchDetailParses() {
        val batch = ApiJson.decodeFromString(BatchDetailDto.serializer(), batchJson)
        assertEquals("b_…", batch.batchId)
        assertNull(batch.submittedAt)
        assertEquals(ProgressDto(photos = 14, waiting = 3, reading = 2, read = 9, failed = 0), batch.progress)
        assertEquals(CountsDto(good = 4, corrections = 2, retake = 1, duplicates = 1), batch.counts)

        val invoice = batch.invoices.single()
        assertEquals("i_…", invoice.invoiceId)
        assertEquals(listOf("line 3: no stock item matched", "does not add up"), invoice.tabReasons)
        assertEquals("vassco", invoice.supplier)
        assertEquals("Vassco", invoice.title)
        assertEquals("1844378", invoice.documentNo)
        assertEquals("2026-08-31", invoice.date)
        assertEquals(3000.00, invoice.statedTotalExcl!!, 0.0)
        assertEquals(450.00, invoice.statedVat!!, 0.0)
        assertEquals(3450.00, invoice.statedTotalIncl!!, 0.0)
        assertEquals(3000.00, invoice.linesSumExcl!!, 0.0)
        assertEquals(true, invoice.addsUp)
        assertNull(invoice.duplicateOfId)
        assertEquals(3, invoice.firstOrdinal)

        val page = invoice.pages.single()
        assertEquals(PageDto("p_…", ordinal = 3, pageNo = 1, state = "read", quality = PageQualityDto(true, emptyList())), page)

        val line = invoice.lines.single()
        assertEquals(0, line.index)
        assertFalse(line.star)
        assertEquals(PrintedDto("Honey 5L", 2.0, null, "5L", 100.00, 200.00), line.printed)
        assertEquals(ItemDto("1189", "honey (LITRE)", "l"), line.item)
        assertEquals(10.0, line.baseQuantity!!, 0.0)
        assertNull(line.refusalText)
        assertEquals("matched 'honey (LITRE)' by history (score 106)", line.reason)
        assertEquals(listOf(FlagDto("price_outlier", "plain words")), line.flags)
        assertEquals(listOf(CandidateDto("1189", "honey (LITRE)", 106.0)), line.candidates)
    }

    /** The Pilot name is what the manager sees; the laptop's key only stands in when there is no name. */
    @Test
    fun theSupplierIsShownByItsNameAndFallsBackToTheLaptopsKey() {
        fun titleOf(json: String) = ApiJson.decodeFromString(InvoiceDto.serializer(), json).title

        assertEquals("Vassco", titleOf("""{"invoice_id": "i_1", "supplier": "vassco", "supplier_name": "Vassco"}"""))
        assertEquals("vassco", titleOf("""{"invoice_id": "i_2", "supplier": "vassco"}"""))
        assertEquals("vassco", titleOf("""{"invoice_id": "i_3", "supplier": "vassco", "supplier_name": null}"""))
        assertEquals("vassco", titleOf("""{"invoice_id": "i_4", "supplier": "vassco", "supplier_name": "  "}"""))
        assertEquals("Unknown supplier", titleOf("""{"invoice_id": "i_5"}"""))
    }

    @Test
    fun realTabValuesMapToTabs() {
        val invoice = ApiJson.decodeFromString(
            InvoiceDto.serializer(),
            """{"invoice_id": "i_1", "tab": "corrections", "duplicate_of": "i_0", "lines": []}""",
        )
        assertEquals(InvoiceTab.CORRECTIONS, invoice.tabEnum)
        assertEquals("i_0", invoice.duplicateOfId)
    }

    @Test
    fun refusalAndDuplicateMayBeObjects() {
        val line = ApiJson.decodeFromString(
            LineDto.serializer(),
            """{"index": 2, "star": true, "refusal": {"category": "no_conversion", "reason": "the key has no case size"}}""",
        )
        assertEquals("the key has no case size", line.refusalText)
        val invoice = ApiJson.decodeFromString(
            InvoiceDto.serializer(),
            """{"invoice_id": "i_2", "duplicate_of": {"invoice_id": "i_1", "batch_id": "b_1"}}""",
        )
        assertEquals("i_1", invoice.duplicateOfId)
    }

    @Test
    fun unknownFieldsAndNullListsAreTolerated() {
        val invoice = ApiJson.decodeFromString(
            InvoiceDto.serializer(),
            """{"invoice_id": "i_3", "tab_reasons": null, "lines": null, "something_new": {"a": 1}}""",
        )
        assertEquals(emptyList<String>(), invoice.tabReasons)
        assertEquals(emptyList<LineDto>(), invoice.lines)
    }

    @Test
    fun pairingRepliesParse() {
        val pair = ApiJson.decodeFromString(
            PairResponse.serializer(),
            """{"device_id": "dev_7", "token": "abcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwx", "shop": "eastgate", "shop_name": "News Cafe Eastgate"}""",
        )
        assertEquals("dev_7", pair.deviceId)
        assertEquals("eastgate", pair.shop)
        assertEquals("News Cafe Eastgate", pair.shopName)

        val ping = ApiJson.decodeFromString(
            PingResponse.serializer(),
            """{"ok": true, "server_time": "2026-09-15T14:02:11", "shop": "eastgate", "queue": {"waiting": 3, "reading": 2}}""",
        )
        assertTrue(ping.ok)
        assertEquals(QueueDto(3, 2), ping.queue)
    }

    @Test
    fun uploadRepliesParse() {
        val created = ApiJson.decodeFromString(
            BatchCreated.serializer(),
            """{"batch_id": "b_1", "status": "uploading", "created_at": "2026-09-15T14:02:11"}""",
        )
        assertEquals("b_1", created.batchId)

        val receipt = ApiJson.decodeFromString(
            PhotoReceiptDto.serializer(),
            """{"photo_id": "p_9", "batch_id": "b_1", "client_photo_key": "3f1c2a4e-8d7b-4c1e-9a55-0b6f7e2d9c10", "sha256": "ab12", "bytes": 812345, "state": "stored"}""",
        )
        assertEquals("p_9", receipt.photoId)
        assertEquals("stored", receipt.state)
        assertEquals(Receipt("b_1", "3f1c2a4e-8d7b-4c1e-9a55-0b6f7e2d9c10", "ab12", 812345), receipt.toReceipt())

        assertEquals("processing", ApiJson.decodeFromString(StatusResponse.serializer(), """{"status": "processing"}""").status)
    }

    @Test
    fun listSearchSubmitAndErrorsParse() {
        val list = ApiJson.decodeFromString(
            BatchListResponse.serializer(),
            """{"batches": [{"batch_id": "b_2", "status": "ready", "counts": {"good": 1}}, {"batch_id": "b_1", "status": "submitted"}]}""",
        )
        assertEquals(listOf("b_2", "b_1"), list.batches.map { it.batchId })
        assertEquals(1, list.batches[0].counts!!.good)

        val search = ApiJson.decodeFromString(
            StockSearchResponse.serializer(),
            """{"items": [{"code": "1189", "name": "honey (LITRE)", "base_unit": "l", "category": "grocery"}]}""",
        )
        assertEquals(StockItemDto("1189", "honey (LITRE)", "l", "grocery"), search.items.single())

        val submit = ApiJson.decodeFromString(
            SubmitResponse.serializer(),
            """{"status": "submitted", "report": {"pdf": "/portal/batches/b_1/report.pdf", "csv": "/portal/batches/b_1/report.csv"}}""",
        )
        assertEquals("/portal/batches/b_1/report.pdf", submit.report!!.pdf)

        val error = ApiJson.decodeFromString(ErrorDto.serializer(), """{"error": "still_reading", "message": "3 photos are still being read"}""")
        assertEquals("still_reading", error.error)
    }

    @Test
    fun requestBodiesMatchTheContractExactly() {
        assertEquals(
            """{"pair_code":"K7Q2M9","device_name":"Galaxy A06 (store 1)"}""",
            RequestBodies.pair("K7Q2M9", "Galaxy A06 (store 1)").toString(),
        )
        assertEquals(
            """{"client_batch_key":"3f1c2a4e-8d7b-4c1e-9a55-0b6f7e2d9c10","page_count":14}""",
            RequestBodies.createBatch("3f1c2a4e-8d7b-4c1e-9a55-0b6f7e2d9c10", 14).toString(),
        )
        assertEquals(
            """{"item_code":"1189","quantity":2,"unit":null,"cost":200.00}""",
            RequestBodies.line(LineEdit(LineChoice.StockItem("1189", null), BigDecimal("2"), null, BigDecimal("200"))).toString(),
        )
        assertEquals(
            """{"item_code":"1189","quantity":2.5,"unit":"case","cost":199.95}""",
            RequestBodies.line(LineEdit(LineChoice.StockItem("1189", null), BigDecimal("2.50"), "case", BigDecimal("199.95"))).toString(),
        )
        assertEquals(
            """{"free_text":"honey 5l tub","quantity":10,"cost":200.00}""",
            RequestBodies.line(LineEdit(LineChoice.FreeText("honey 5l tub"), BigDecimal("10"), null, BigDecimal("200.00"))).toString(),
        )
        assertEquals(
            """{"supplier":"vassco","document_no":"1844378","date":"2026-08-31"}""",
            RequestBodies.patchInvoice("vassco", "1844378", "2026-08-31").toString(),
        )
        assertEquals("{}", RequestBodies.empty.toString())
    }

    @Test
    fun bodiesAreValidJsonThatReadsBackAsNumbers() {
        val body = RequestBodies.line(LineEdit(LineChoice.StockItem("1189", null), BigDecimal("2"), null, BigDecimal("200")))
        val reparsed = ApiJson.parseToJsonElement(body.toString()).toString()
        assertTrue(reparsed, reparsed.contains("\"cost\":200.00") || reparsed.contains("\"cost\":200.0"))
    }
}
