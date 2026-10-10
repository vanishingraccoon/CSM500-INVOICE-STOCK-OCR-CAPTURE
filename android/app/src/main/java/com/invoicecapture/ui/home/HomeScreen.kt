package com.invoicecapture.ui.home

import android.net.Uri
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyListScope
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.SavedStateHandle
import androidx.lifecycle.ViewModel
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.repeatOnLifecycle
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.receiveAsFlow
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import com.invoicecapture.data.AppContainer
import com.invoicecapture.data.api.ApiResult
import com.invoicecapture.data.api.BatchSummaryDto
import com.invoicecapture.data.api.plainMessage
import com.invoicecapture.data.pairing.Pairing
import com.invoicecapture.data.pending.PendingSession
import com.invoicecapture.data.pending.SessionKind
import com.invoicecapture.domain.batch.BatchRules
import com.invoicecapture.domain.batch.Polling
import com.invoicecapture.domain.batch.Times
import com.invoicecapture.ui.components.AppBar
import com.invoicecapture.ui.components.Kicker
import com.invoicecapture.ui.components.Notice
import com.invoicecapture.ui.components.Plate
import com.invoicecapture.ui.components.PrimaryButton
import com.invoicecapture.ui.components.ReadingBar
import com.invoicecapture.ui.components.ScannerLimits
import com.invoicecapture.ui.components.SecondaryButton
import com.invoicecapture.ui.components.Tag
import com.invoicecapture.ui.components.TextAction
import com.invoicecapture.ui.components.containerViewModel
import com.invoicecapture.ui.components.rememberDocumentScanner
import com.invoicecapture.ui.theme.Industry
import java.io.IOException

data class HomeUiState(
    val pairing: Pairing? = null,
    val batches: List<BatchSummaryDto> = emptyList(),
    val loaded: Boolean = false,
    /** null until the first answer (or failure). */
    val reachable: Boolean? = null,
    val laptopMessage: String? = null,
    val sessions: List<PendingSession> = emptyList(),
    val savingScan: Boolean = false,
    val error: String? = null,
) {
    val photosOnPhone: Int get() = sessions.sumOf { it.pagesOnPhone }
    val problemSessions: List<PendingSession> get() = sessions.filter { it.problemPages.isNotEmpty() }
}

private data class Remote(
    val batches: List<BatchSummaryDto> = emptyList(),
    val loaded: Boolean = false,
    val reachable: Boolean? = null,
    val message: String? = null,
)

private data class Local(val savingScan: Boolean = false, val error: String? = null)

class HomeViewModel(private val container: AppContainer, @Suppress("unused") saved: SavedStateHandle) : ViewModel() {

    private val remote = MutableStateFlow(Remote())
    private val local = MutableStateFlow(Local())
    private val openCapture = Channel<String>(Channel.BUFFERED)

    /** Session keys of scans that were just saved: open their progress screen. */
    val captureOpened: Flow<String> = openCapture.receiveAsFlow()

    val state: StateFlow<HomeUiState> = combine(
        container.pairingStore.pairing,
        container.pendingStore.sessions,
        remote,
        local,
    ) { pairing, sessions, r, l ->
        HomeUiState(
            pairing = pairing,
            batches = r.batches,
            loaded = r.loaded,
            reachable = r.reachable,
            laptopMessage = r.message,
            sessions = sessions,
            savingScan = l.savingScan,
            error = l.error,
        )
    }.stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), HomeUiState())

    init {
        viewModelScope.launch { container.pendingStore.ensureLoaded() }
    }

    /** Runs while the screen is visible: batch list every 15 s, every 10 s while the laptop is away. */
    suspend fun refreshWhileVisible() {
        while (true) {
            refresh()
            delay(if (remote.value.reachable == false) Polling.UNREACHABLE_MILLIS else 15_000L)
        }
    }

    suspend fun refresh() {
        val wasAway = remote.value.reachable == false
        when (val result = container.invoices.batches()) {
            is ApiResult.Ok -> {
                remote.update { it.copy(batches = result.value.batches, loaded = true, reachable = true, message = null) }
                // The laptop is back: uploads waiting out a backoff go now.
                if (wasAway) container.scheduler.resumeAll(container.pendingStore, includeStopped = false)
            }
            is ApiResult.NetworkError -> remote.update { it.copy(loaded = true, reachable = false, message = result.plainMessage()) }
            else -> remote.update { it.copy(loaded = true, reachable = true, message = result.plainMessage()) }
        }
    }

    fun onScanned(uris: List<Uri>) {
        viewModelScope.launch {
            local.update { it.copy(savingScan = true, error = null) }
            try {
                openCapture.send(container.capture.startScan(uris))
            } catch (e: IOException) {
                local.update { it.copy(error = "The scanned pages could not be saved on this phone (${e.message}). Scan again.") }
            } finally {
                local.update { it.copy(savingScan = false) }
            }
        }
    }

    fun onError(message: String) = local.update { it.copy(error = message) }

    fun retryUploads() {
        viewModelScope.launch { container.capture.retryAll() }
    }
}

@Composable
fun HomeScreen(onOpenBatch: (String) -> Unit, onOpenCapture: (String) -> Unit, onOpenSettings: () -> Unit) {
    val vm = containerViewModel { container, saved -> HomeViewModel(container, saved) }
    val state by vm.state.collectAsStateWithLifecycle()
    val lifecycleOwner = LocalLifecycleOwner.current
    LaunchedEffect(vm, lifecycleOwner) {
        lifecycleOwner.repeatOnLifecycle(Lifecycle.State.STARTED) { vm.refreshWhileVisible() }
    }
    LaunchedEffect(vm) { vm.captureOpened.collect { onOpenCapture(it) } }

    val scan = rememberDocumentScanner(ScannerLimits.MAX_PAGES_PER_BATCH, onPages = vm::onScanned, onError = vm::onError)
    val open = state.batches.filter { it.status != "submitted" }
    val submitted = state.batches.filter { it.status == "submitted" }

    Scaffold(
        topBar = {
            AppBar(
                title = "Invoice Capture",
                subtitle = state.pairing?.let { "${it.shopLabel} · ${it.deviceName}" },
                actions = { TextAction("Settings", onOpenSettings) },
            )
        },
        bottomBar = { ScanBottomBar(savingScan = state.savingScan, onScan = scan) },
        containerColor = Industry.Ground,
    ) { padding ->
        LazyColumn(
            modifier = Modifier.fillMaxSize().padding(padding),
            contentPadding = PaddingValues(12.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            state.error?.let { item { Notice("Scan", it, edge = Industry.Red) } }
            if (state.reachable == false) {
                item {
                    Notice(
                        "Waiting for the laptop",
                        "${state.laptopMessage ?: ""} Photos stay on this phone and upload by themselves when the laptop is back.",
                        edge = Industry.Accent,
                    )
                }
            } else if (state.laptopMessage != null) {
                item { Notice("Laptop", state.laptopMessage, edge = Industry.Red) }
            }

            item { QueuePlate(state, onRetry = vm::retryUploads, onOpenCapture = onOpenCapture) }

            batchItems("Current batches", open, state.sessions, onOpenBatch)
            batchItems("Past batches", submitted, state.sessions, onOpenBatch)
            if (state.loaded && state.batches.isEmpty() && state.reachable == true) {
                item {
                    Text(
                        "No batches yet. Tap Scan invoices to photograph a delivery.",
                        style = MaterialTheme.typography.bodyMedium,
                        color = Industry.Muted,
                    )
                }
            }
        }
    }
}

/** Scan invoices, and what one scan means. */
@Composable
private fun ScanBottomBar(savingScan: Boolean, onScan: () -> Unit) {
    Column(Modifier.fillMaxWidth().background(Industry.Ground).navigationBarsPadding()) {
        HorizontalDivider(color = Industry.Divider)
        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            PrimaryButton(
                text = if (savingScan) "Saving the scan…" else "Scan invoices",
                onClick = onScan,
                enabled = !savingScan,
            )
            Text(
                "One scan is one batch: photograph every invoice of the delivery, then save.",
                style = MaterialTheme.typography.bodySmall,
                color = Industry.Muted,
            )
        }
    }
}

/** A heading and one row per batch; nothing when [batches] is empty. */
private fun LazyListScope.batchItems(
    heading: String,
    batches: List<BatchSummaryDto>,
    sessions: List<PendingSession>,
    onOpenBatch: (String) -> Unit,
) {
    if (batches.isNotEmpty()) {
        item { Kicker(heading) }
        items(batches, key = { it.batchId }) { summary ->
            BatchRow(summary, photosOnPhone = photosOnPhoneFor(sessions, summary.batchId), onClick = { onOpenBatch(summary.batchId) })
        }
    }
}

private fun photosOnPhoneFor(sessions: List<PendingSession>, batchId: String): Int =
    sessions.filter { it.serverBatchId == batchId }.sumOf { it.pagesOnPhone }

@Composable
private fun QueuePlate(state: HomeUiState, onRetry: () -> Unit, onOpenCapture: (String) -> Unit) {
    Plate(Modifier.fillMaxWidth()) {
        Kicker("Upload queue")
        val waiting = state.photosOnPhone
        Text(
            when (waiting) {
                0 -> "Every photo is on the laptop."
                1 -> "1 photo waiting to upload."
                else -> "$waiting photos waiting to upload."
            },
            style = MaterialTheme.typography.titleMedium,
        )
        state.sessions.filter { it.kind == SessionKind.SCAN && it.preparedCount < it.pageCount }.forEach { session ->
            Text(
                "Checking a scan: ${session.preparedCount} of ${session.pageCount} pages — tap to see",
                modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp).clickable { onOpenCapture(session.key) }.padding(vertical = 12.dp),
                style = MaterialTheme.typography.bodyMedium,
                color = Industry.Accent700,
            )
        }
        state.sessions.filter { it.kind == SessionKind.SCAN && it.serverBatchId == null && it.pagesOnPhone > 0 }.forEach { session ->
            Text(
                "A scan of ${session.pageCount} pages is waiting for the laptop to create its batch." +
                    (session.batchError?.let { " ($it)" } ?: ""),
                style = MaterialTheme.typography.bodySmall,
            )
        }
        val problems = state.problemSessions.flatMap { s -> s.problemPages.map { s to it } }
        problems.take(4).forEach { (session, page) ->
            // Tapping opens that scan, where the page can be tried again or discarded.
            Text(
                (if (session.kind == SessionKind.RETAKE) "Retake" else "Page ${page.ordinal}") + ": " +
                    (page.lastError ?: "needs attention") + " — tap to see",
                modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp)
                    .clickable { onOpenCapture(session.key) }
                    .padding(vertical = 12.dp),
                style = MaterialTheme.typography.bodySmall,
                color = Industry.Red,
            )
        }
        if (problems.size > 4) Text("…and ${problems.size - 4} more", style = MaterialTheme.typography.bodySmall, color = Industry.Red)
        if (waiting > 0) {
            state.sessions.flatMap { it.pages }.firstOrNull { it.onPhone && !it.stopped && it.lastError != null }?.let {
                Text("Last try: ${it.lastError}", style = MaterialTheme.typography.bodySmall, color = Industry.Muted)
            }
            SecondaryButton(
                if (problems.isNotEmpty()) "Try again" else "Retry now",
                onClick = onRetry,
                modifier = Modifier.fillMaxWidth(),
            )
        }
    }
}

@Composable
private fun BatchRow(summary: BatchSummaryDto, photosOnPhone: Int, onClick: () -> Unit) {
    Plate(Modifier.fillMaxWidth().heightIn(min = 72.dp).clickable(onClickLabel = "Open batch", onClick = onClick)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(
                Times.short(summary.createdAt).ifEmpty { "Batch" },
                style = MaterialTheme.typography.titleMedium,
                modifier = Modifier.weight(1f),
            )
            Tag(BatchRules.statusLabel(summary.status), solid = Polling.isActive(summary.status))
        }
        Text(summary.batchId, style = MaterialTheme.typography.bodySmall.copy(fontFamily = FontFamily.Monospace, fontSize = 12.sp), color = Industry.Muted)
        summary.progress?.let { p ->
            if (Polling.isActive(summary.status) && p.photos > 0) {
                Text("Read ${p.read + p.failed} of ${p.photos} photos", style = MaterialTheme.typography.bodySmall)
                ReadingBar((p.read + p.failed).toFloat() / p.photos)
            }
        }
        summary.counts?.let { c ->
            Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                Text("Good ${c.good}", style = MaterialTheme.typography.bodySmall)
                Text("Corrections ${c.corrections}", style = MaterialTheme.typography.bodySmall, color = if (c.corrections > 0) Industry.Red else Industry.Ink)
                Text("Retake ${c.retake}", style = MaterialTheme.typography.bodySmall)
                Text("Duplicates ${c.duplicates}", style = MaterialTheme.typography.bodySmall)
            }
        }
        if (photosOnPhone > 0) {
            Text(
                if (photosOnPhone == 1) "1 photo still on this phone" else "$photosOnPhone photos still on this phone",
                style = MaterialTheme.typography.bodySmall,
                color = Industry.Accent700,
            )
        }
    }
}
