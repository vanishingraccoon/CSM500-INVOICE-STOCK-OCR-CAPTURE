package com.invoicecapture.data.api

/** The outcome of one call to the laptop. */
sealed interface ApiResult<out T> {
    data class Ok<T>(val value: T, val status: Int, val etag: String? = null) : ApiResult<T>

    /** `304` for a batch whose ETag has not changed. */
    data object NotModified : ApiResult<Nothing>

    data class HttpError(val status: Int, val error: String?, val message: String?) : ApiResult<Nothing>

    /** The laptop could not be reached (no Wi-Fi, laptop off, wrong address, timeout). */
    data class NetworkError(val cause: Throwable) : ApiResult<Nothing>

    /** No device token on this phone. */
    data object NotPaired : ApiResult<Nothing>
}

val ApiResult<*>.isUnreachable: Boolean get() = this is ApiResult.NetworkError

/** The HTTP status, or null when the laptop was never reached. */
val ApiResult<*>.httpStatus: Int?
    get() = when (this) {
        is ApiResult.Ok -> status
        ApiResult.NotModified -> 304
        is ApiResult.HttpError -> status
        is ApiResult.NetworkError, ApiResult.NotPaired -> null
    }

/** The `error` field of an error reply, if any. */
val ApiResult<*>.errorCode: String? get() = (this as? ApiResult.HttpError)?.error

val ApiResult<*>.needsPairing: Boolean
    get() = this is ApiResult.NotPaired || (this is ApiResult.HttpError && (status == 401 || status == 403))

/** Plain words for the manager. */
fun ApiResult<*>.plainMessage(): String = when (this) {
    is ApiResult.Ok -> ""
    ApiResult.NotModified -> ""
    ApiResult.NotPaired -> "This phone is not paired with the laptop."
    is ApiResult.NetworkError ->
        "Can't reach the laptop. Check this phone is on the office Wi-Fi and the laptop is switched on."
    is ApiResult.HttpError -> ErrorMessages.forCode(error, status, message)
}

object ErrorMessages {
    fun forCode(error: String?, status: Int, message: String?): String = when (error) {
        "unauthorised" -> "The laptop no longer recognises this phone. Pair it again in Settings."
        "device_revoked" -> "This phone was removed on the laptop. Pair it again in Settings."
        "pair_code_invalid" -> "That code is wrong, expired or already used. Show a new code on the laptop and try again."
        "still_reading" -> "Photos are still being read. Submit when reading has finished."
        "batch_submitted" -> "This batch has already been submitted."
        "key_reused" -> "The laptop already has a different photo under this key."
        "hash_mismatch" -> "The photo arrived damaged. Try again."
        "unsupported_image" -> "The laptop could not open this photo."
        "bad_reply" -> "The laptop's reply could not be read."
        "invoice_regrouped" -> "The laptop regrouped these pages while reading, so the batch was reloaded. Check the invoice and make the change again."
        "invoice_deleted" -> "That invoice was deleted on the laptop. The batch was reloaded."
        "already_replaced" -> "That page was already replaced by another photo. The batch was reloaded."
        "unknown_item" -> "That stock item is not in this shop's key. Search again and pick an item from the list."
        "unknown_supplier" -> "The laptop does not know that supplier. Type the supplier as the laptop knows it."
        "key_unavailable" -> "The laptop's stock list is unavailable. Try again in a moment."
        else -> message?.takeIf { it.isNotBlank() } ?: when {
            error != null && error.startsWith("invalid_") ->
                "The laptop did not accept what was typed (${error.removePrefix("invalid_").replace('_', ' ')}). Check it and try again."
            status == 401 -> "The laptop no longer recognises this phone. Pair it again in Settings."
            status == 403 -> "This phone was removed on the laptop. Pair it again in Settings."
            status == 404 -> "The laptop could not find that. Refresh and try again."
            status in 500..599 -> "The laptop had a problem (HTTP $status). Try again in a moment."
            else -> "The laptop refused this (HTTP $status)."
        }
    }
}
