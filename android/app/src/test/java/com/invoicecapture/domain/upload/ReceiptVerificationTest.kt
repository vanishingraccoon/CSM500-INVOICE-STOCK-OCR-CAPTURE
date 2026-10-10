package com.invoicecapture.domain.upload

import org.junit.Assert.assertEquals
import org.junit.Test

class ReceiptVerificationTest {

    private val sha = "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08"
    private val local = LocalPhoto(
        batchId = "b_20260915_1",
        clientPhotoKey = "3f1c2a4e-8d7b-4c1e-9a55-0b6f7e2d9c10",
        sha256 = sha,
        bytes = 812345,
    )
    private val matching = Receipt(
        batchId = "b_20260915_1",
        clientPhotoKey = "3f1c2a4e-8d7b-4c1e-9a55-0b6f7e2d9c10",
        sha256 = sha,
        bytes = 812345,
    )

    @Test
    fun matchingReceiptMatches() {
        assertEquals(ReceiptCheck.Match, ReceiptVerification.verify(matching, local))
    }

    @Test
    fun hexAndUuidCaseDoesNotMatter() {
        val upper = matching.copy(sha256 = sha.uppercase(), clientPhotoKey = matching.clientPhotoKey!!.uppercase())
        assertEquals(ReceiptCheck.Match, ReceiptVerification.verify(upper, local))
    }

    @Test
    fun differentBatchIdIsAMismatch() {
        assertEquals(
            ReceiptCheck.Mismatch(listOf("batch_id")),
            ReceiptVerification.verify(matching.copy(batchId = "b_other"), local),
        )
    }

    @Test
    fun differentPhotoKeyIsAMismatch() {
        assertEquals(
            ReceiptCheck.Mismatch(listOf("client_photo_key")),
            ReceiptVerification.verify(matching.copy(clientPhotoKey = "00000000-0000-0000-0000-000000000000"), local),
        )
    }

    @Test
    fun differentHashIsAMismatch() {
        assertEquals(
            ReceiptCheck.Mismatch(listOf("sha256")),
            ReceiptVerification.verify(matching.copy(sha256 = sha.replace('9', '8')), local),
        )
    }

    @Test
    fun differentByteCountIsAMismatch() {
        assertEquals(
            ReceiptCheck.Mismatch(listOf("bytes")),
            ReceiptVerification.verify(matching.copy(bytes = 812344), local),
        )
    }

    @Test
    fun missingFieldsAreMismatches() {
        assertEquals(
            ReceiptCheck.Mismatch(listOf("batch_id", "client_photo_key", "sha256", "bytes")),
            ReceiptVerification.verify(Receipt(null, null, null, null), local),
        )
        assertEquals(
            ReceiptCheck.Mismatch(listOf("batch_id", "client_photo_key", "sha256", "bytes")),
            ReceiptVerification.verify(null, local),
        )
    }

    @Test
    fun severalWrongFieldsAreAllListed() {
        assertEquals(
            ReceiptCheck.Mismatch(listOf("sha256", "bytes")),
            ReceiptVerification.verify(matching.copy(sha256 = "00", bytes = 1), local),
        )
    }
}
