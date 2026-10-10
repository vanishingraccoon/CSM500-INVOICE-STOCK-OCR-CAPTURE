package com.invoicecapture.domain.upload

/** What the phone knows about the photo file it sent. */
data class LocalPhoto(
    val batchId: String,
    val clientPhotoKey: String,
    val sha256: String,
    val bytes: Long,
)

/** The fields of the laptop's receipt that decide whether the phone may delete its copy. */
data class Receipt(
    val batchId: String?,
    val clientPhotoKey: String?,
    val sha256: String?,
    val bytes: Long?,
)

sealed interface ReceiptCheck {
    data object Match : ReceiptCheck

    /** [fields] uses the contract's JSON names, e.g. `sha256`. */
    data class Mismatch(val fields: List<String>) : ReceiptCheck
}

object ReceiptVerification {
    private val ALL_FIELDS = listOf("batch_id", "client_photo_key", "sha256", "bytes")

    /**
     * Contract §2: the phone deletes its copy only after a 200/201 receipt whose `batch_id`,
     * `client_photo_key`, `sha256` and `bytes` all match the local file. The key (a UUID) and the
     * hex hash are compared without regard to letter case; the batch id exactly.
     */
    fun verify(receipt: Receipt?, local: LocalPhoto): ReceiptCheck {
        if (receipt == null) return ReceiptCheck.Mismatch(ALL_FIELDS)
        val wrong = buildList {
            if (receipt.batchId != local.batchId) add("batch_id")
            if (receipt.clientPhotoKey == null || !receipt.clientPhotoKey.equals(local.clientPhotoKey, ignoreCase = true)) {
                add("client_photo_key")
            }
            if (receipt.sha256 == null || !receipt.sha256.equals(local.sha256, ignoreCase = true)) add("sha256")
            if (receipt.bytes != local.bytes) add("bytes")
        }
        return if (wrong.isEmpty()) ReceiptCheck.Match else ReceiptCheck.Mismatch(wrong)
    }
}
