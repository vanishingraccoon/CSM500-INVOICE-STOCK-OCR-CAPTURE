package com.invoicecapture.domain.batch

import java.time.LocalDate
import java.time.LocalDateTime
import java.time.format.DateTimeFormatter
import java.time.format.DateTimeParseException
import java.util.Locale

/** How often the batch screen asks the laptop for news (contract §3). */
object Polling {
    const val ACTIVE_MILLIS = 3_000L
    const val IDLE_MILLIS = 30_000L
    const val UNREACHABLE_MILLIS = 10_000L

    /** Reading is under way (or not known yet). */
    fun isActive(status: String?): Boolean = status == null || status == "uploading" || status == "processing"

    fun intervalMillis(status: String?, consecutiveFailures: Int): Long = when {
        consecutiveFailures > 0 -> UNREACHABLE_MILLIS
        isActive(status) -> ACTIVE_MILLIS
        else -> IDLE_MILLIS
    }
}

sealed interface SubmitGate {
    data object Allowed : SubmitGate
    data class Blocked(val reason: String) : SubmitGate
}

object BatchRules {
    fun statusLabel(status: String?): String = when (status) {
        "uploading" -> "Uploading"
        "processing" -> "Reading"
        "ready" -> "Ready to check"
        "submitted" -> "Submitted"
        null -> "Waiting for the laptop"
        else -> status.replaceFirstChar { it.uppercase() }
    }

    /**
     * Submit is refused by the laptop with `409 still_reading` until waiting + reading is 0
     * (contract §7). The phone also waits for its own photos to finish uploading.
     */
    fun submitGate(status: String?, waiting: Int, reading: Int, photosOnPhone: Int): SubmitGate = when {
        status == "submitted" -> SubmitGate.Blocked("This batch has been submitted.")
        photosOnPhone > 0 -> SubmitGate.Blocked(
            if (photosOnPhone == 1) "1 photo is still uploading from this phone."
            else "$photosOnPhone photos are still uploading from this phone.",
        )
        waiting + reading > 0 -> SubmitGate.Blocked(
            "Submit when every photo has been read (${waiting + reading} still to read).",
        )
        else -> SubmitGate.Allowed
    }
}

object Times {
    private val SHORT = DateTimeFormatter.ofPattern("d MMM HH:mm", Locale.US)

    /** "2026-09-15T14:02:11" → "15 Sep 14:02". Anything unparseable is shown as sent. */
    fun short(iso: String?): String {
        if (iso.isNullOrBlank()) return ""
        return try {
            LocalDateTime.parse(iso.trim().replace(' ', 'T').take(19)).format(SHORT)
        } catch (e: DateTimeParseException) {
            iso
        }
    }

    /** An invoice date as the laptop stores it: YYYY-MM-DD. */
    fun isIsoDate(text: String): Boolean = try {
        LocalDate.parse(text.trim())
        true
    } catch (e: DateTimeParseException) {
        false
    }
}
