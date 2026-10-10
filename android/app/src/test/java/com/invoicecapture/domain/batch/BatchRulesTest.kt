package com.invoicecapture.domain.batch

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File
import java.math.BigDecimal
import com.invoicecapture.domain.util.Decimals
import com.invoicecapture.domain.util.Hashing

class BatchRulesTest {

    @Test
    fun pollsEveryThreeSecondsWhileReadingAndThirtyOtherwise() {
        assertEquals(3_000L, Polling.intervalMillis("uploading", 0))
        assertEquals(3_000L, Polling.intervalMillis("processing", 0))
        assertEquals(30_000L, Polling.intervalMillis("ready", 0))
        assertEquals(30_000L, Polling.intervalMillis("submitted", 0))
        assertEquals(3_000L, Polling.intervalMillis(null, 0))
        assertEquals(10_000L, Polling.intervalMillis("processing", 2))
    }

    @Test
    fun submitWaitsForReadingAndForThisPhonesUploads() {
        assertEquals(SubmitGate.Allowed, BatchRules.submitGate("ready", 0, 0, 0))
        assertTrue(BatchRules.submitGate("processing", 3, 2, 0) is SubmitGate.Blocked)
        assertTrue((BatchRules.submitGate("processing", 3, 2, 0) as SubmitGate.Blocked).reason.contains("5 still to read"))
        assertTrue(BatchRules.submitGate("uploading", 0, 0, 2) is SubmitGate.Blocked)
        assertTrue(BatchRules.submitGate("submitted", 0, 0, 0) is SubmitGate.Blocked)
    }

    @Test
    fun moneyIsShownInRands() {
        assertEquals("R 3,450.00", Money.format(3450.0))
        assertEquals("R 0.50", Money.format(0.5))
        assertEquals("-R 12.00", Money.format(-12.0))
        assertEquals("—", Money.format(null))
    }

    @Test
    fun typedAmountsAreParsed() {
        assertEquals(BigDecimal("200"), Money.parseAmount("200"))
        assertEquals(BigDecimal("1200.50"), Money.parseAmount("1 200,50"))
        assertEquals(BigDecimal("3450.00"), Money.parseAmount("R3,450.00"))
        assertEquals(BigDecimal("1200"), Money.parseAmount("1,200"))
        assertEquals(BigDecimal("-12.00"), Money.parseAmount("-12.00"))
        assertNull(Money.parseAmount("abc"))
        assertNull(Money.parseAmount("1e5"))
        assertNull(Money.parseAmount(""))
    }

    @Test
    fun wireNumbersKeepTheContractFormat() {
        assertEquals("200.00", Money.twoDecimals(BigDecimal("200")))
        assertEquals("0.13", Money.twoDecimals(BigDecimal("0.125")))
        assertEquals("2", Money.plainNumber(BigDecimal("2.000")))
        assertEquals("100", Money.plainNumber(BigDecimal("100")))
        assertEquals("2.5", Money.plainNumber(BigDecimal("2.50")))
        assertEquals("0", Money.plainNumber(BigDecimal("0.00")))
        assertEquals("10", Money.formatQuantity(10.0))
        assertEquals("0.75", Money.formatQuantity(0.75))
    }

    @Test
    fun decimalsRoundAndTrim() {
        assertEquals("142.3", Decimals.format(142.34, 1))
        assertEquals("171.0", Decimals.format(171.0, 1))
        assertEquals("0.004", Decimals.format(0.004, 4, minDecimals = 1))
        assertEquals("0.0", Decimals.format(0.0, 4, minDecimals = 1))
    }

    @Test
    fun timesAreShortened() {
        assertEquals("15 Sep 14:02", Times.short("2026-09-15T14:02:11"))
        assertEquals("15 Sep 14:02", Times.short("2026-09-15 14:02:11.123"))
        assertEquals("not a time", Times.short("not a time"))
        assertEquals("", Times.short(null))
        assertTrue(Times.isIsoDate("2026-08-31"))
        assertFalse(Times.isIsoDate("31/08/2026"))
    }

    @Test
    fun sha256IsLowerCaseHex() {
        val expected = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        assertEquals(expected, Hashing.sha256Hex("abc".toByteArray()))
        val file = File.createTempFile("sha", ".bin")
        try {
            file.writeBytes("abc".toByteArray())
            assertEquals(expected, Hashing.sha256Hex(file))
        } finally {
            file.delete()
        }
    }

    @Test
    fun lineEditsAreChecked() {
        val item = LineChoice.StockItem("1189", "honey (LITRE)")
        assertTrue(LineEdits.check(null, "2", "", "200") is LineEditCheck.Invalid)
        assertTrue(LineEdits.check(LineChoice.FreeText("  "), "2", "", "200") is LineEditCheck.Invalid)
        assertTrue(LineEdits.check(item, "two", "", "200") is LineEditCheck.Invalid)
        assertTrue(LineEdits.check(item, "-1", "", "200") is LineEditCheck.Invalid)
        assertTrue(LineEdits.check(item, "2", "", "") is LineEditCheck.Invalid)

        val valid = LineEdits.check(item, "2", " case ", "200,00") as LineEditCheck.Valid
        assertEquals(LineEdit(item, BigDecimal("2"), "case", BigDecimal("200.00")), valid.edit)

        val free = LineEdits.check(LineChoice.FreeText(" honey 5l tub "), "10", "ignored", "200") as LineEditCheck.Valid
        assertEquals(LineEdit(LineChoice.FreeText("honey 5l tub"), BigDecimal("10"), null, BigDecimal("200")), free.edit)
    }
}
