package com.invoicecapture.ui.capture

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
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
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.lifecycle.SavedStateHandle
import androidx.lifecycle.ViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewModelScope
import androidx.navigation.toRoute
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch
import com.invoicecapture.data.AppContainer
import com.invoicecapture.data.pending.CaptureSummary
import com.invoicecapture.data.pending.StuckPage
import com.invoicecapture.ui.CaptureRoute
import com.invoicecapture.ui.components.AppBar
import com.invoicecapture.ui.components.ConfirmDialog
import com.invoicecapture.ui.components.Kicker
import com.invoicecapture.ui.components.Notice
import com.invoicecapture.ui.components.Plate
import com.invoicecapture.ui.components.PrimaryButton
import com.invoicecapture.ui.components.ReadingBar
import com.invoicecapture.ui.components.SecondaryButton
import com.invoicecapture.ui.components.containerViewModel
import com.invoicecapture.ui.theme.Industry

data class CaptureUiState(val loaded: Boolean = false, val summary: CaptureSummary? = null)

/** Follows one scanner session on the phone: quality checks first, then uploads (WorkManager does the work). */
class CaptureViewModel(private val container: AppContainer, private val saved: SavedStateHandle) : ViewModel() {
    private val key: String = saved.toRoute<CaptureRoute>().sessionKey
    private val loaded = MutableStateFlow(false)

    /** The page the Discard confirmation is about; saved so it survives rotation and process death. */
    private val _discarding = MutableStateFlow(saved.get<String>(KEY_DISCARDING))
    val discarding: StateFlow<String?> = _discarding.asStateFlow()

    val state: StateFlow<CaptureUiState> = combine(container.pendingStore.sessions, loaded) { sessions, isLoaded ->
        CaptureUiState(isLoaded, sessions.firstOrNull { it.key == key }?.let(CaptureSummary::of))
    }.stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), CaptureUiState())

    init {
        viewModelScope.launch {
            container.pendingStore.ensureLoaded()
            loaded.value = true
        }
    }

    fun retry() {
        viewModelScope.launch { container.capture.retryAll() }
    }

    fun askToDiscard(clientPhotoKey: String?) {
        saved[KEY_DISCARDING] = clientPhotoKey
        _discarding.value = clientPhotoKey
    }

    /** Deletes the phone's copy and drops the page, so no later retry can upload it. */
    fun discard(clientPhotoKey: String) {
        askToDiscard(null)
        viewModelScope.launch { container.capture.discardPage(key, clientPhotoKey) }
    }

    private companion object {
        const val KEY_DISCARDING = "discarding"
    }
}

@Composable
fun CaptureScreen(onBack: () -> Unit, onOpenBatch: (String) -> Unit) {
    val vm = containerViewModel { container, saved -> CaptureViewModel(container, saved) }
    val state by vm.state.collectAsStateWithLifecycle()
    val discarding by vm.discarding.collectAsStateWithLifecycle()
    val s = state.summary

    Scaffold(
        topBar = {
            AppBar(
                title = "Scan saved",
                subtitle = s?.let { if (it.pageCount == 1) "1 page" else "${it.pageCount} pages · one batch" },
                onBack = onBack,
            )
        },
        bottomBar = { CaptureBottomBar(s, onOpenBatch) },
        containerColor = Industry.Ground,
    ) { padding ->
        LazyColumn(
            modifier = Modifier.fillMaxSize().padding(padding),
            contentPadding = PaddingValues(12.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            if (s == null) {
                item {
                    Text(
                        if (state.loaded) "This scan has finished uploading. Open its batch from the home screen." else "Loading…",
                        style = MaterialTheme.typography.bodyMedium,
                    )
                }
                return@LazyColumn
            }

            item { CaptureProgress(s, Modifier.fillMaxWidth()) }

            if (s.waitingForBatch) {
                item {
                    Notice(
                        "Waiting for the laptop",
                        (s.batchError?.let { "$it " } ?: "") + "The photos are safe on this phone and upload by themselves when the laptop answers.",
                        edge = Industry.Accent,
                    )
                }
            }

            photoQualityItems(s)
            stuckPageItems(s.stuckPages, onRetry = vm::retry, onDiscard = vm::askToDiscard)

            if (s.discardedLabel != null) {
                item {
                    Notice(
                        "Discarded",
                        "${s.discardedLabel} will not be uploaded. The invoice stays on paper — recapture it if you still need it.",
                        edge = Industry.Muted,
                    )
                }
            }
        }
    }

    discarding?.let { photoKey ->
        DiscardPageConfirm(onConfirm = { vm.discard(photoKey) }, onDismiss = { vm.askToDiscard(null) })
    }
}

/** How many pages are checked and how many are on the laptop. */
@Composable
private fun CaptureProgress(s: CaptureSummary, modifier: Modifier = Modifier) {
    Plate(modifier) {
        Kicker("Checking photos")
        Text("${s.checked} of ${s.pageCount} pages checked", style = MaterialTheme.typography.titleMedium)
        ReadingBar(if (s.pageCount == 0) 0f else s.checked.toFloat() / s.pageCount)
        Kicker("Uploading to the laptop")
        Text("${s.uploaded} of ${s.pageCount} pages on the laptop", style = MaterialTheme.typography.titleMedium)
        ReadingBar(if (s.pageCount == 0) 0f else s.uploaded.toFloat() / s.pageCount)
        if (s.everythingUploaded) {
            Text("Every page is on the laptop and is being read.", style = MaterialTheme.typography.bodySmall)
        }
    }
}

/** The phone's quality verdict: each poor page in words, or that every page passed once checking is done. */
private fun LazyListScope.photoQualityItems(s: CaptureSummary) {
    if (s.poorPages.isNotEmpty()) {
        item {
            Notice(
                "Photo quality",
                "These pages may be hard to read. They still upload; if the laptop cannot read one, it goes to the " +
                    "Retake tab, where you can photograph it again.",
                edge = Industry.Red,
            )
        }
        items(s.poorPages, key = { "poor-${it.ordinal}" }) { page ->
            Text(
                "Page ${page.ordinal}: ${page.words}",
                modifier = Modifier.fillMaxWidth().padding(horizontal = 4.dp),
                style = MaterialTheme.typography.bodyMedium,
                color = Industry.Red,
            )
        }
    } else if (!s.checking) {
        item { Notice("Photo quality", "Every page passed the phone's checks.", edge = Industry.Accent) }
    }
}

/** Pages whose upload stopped, each with Try again and Discard. */
private fun LazyListScope.stuckPageItems(
    pages: List<StuckPage>,
    onRetry: () -> Unit,
    onDiscard: (clientPhotoKey: String) -> Unit,
) {
    if (pages.isNotEmpty()) {
        item { Kicker("Needs attention") }
        items(pages, key = { "stuck-${it.clientPhotoKey}" }) { page ->
            StuckPageRow(
                page = page,
                onRetry = onRetry,
                onDiscard = { onDiscard(page.clientPhotoKey) },
                modifier = Modifier.fillMaxWidth().padding(horizontal = 4.dp),
            )
        }
    }
}

@Composable
private fun StuckPageRow(page: StuckPage, onRetry: () -> Unit, onDiscard: () -> Unit, modifier: Modifier = Modifier) {
    Column(modifier, verticalArrangement = Arrangement.spacedBy(4.dp)) {
        Text(
            "Page ${page.ordinal}: ${page.message}",
            style = MaterialTheme.typography.bodyMedium,
            color = Industry.Red,
        )
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            SecondaryButton("Try again", onClick = onRetry, modifier = Modifier.weight(1f))
            SecondaryButton(
                "Discard",
                onClick = onDiscard,
                modifier = Modifier.weight(1f),
                color = Industry.Red,
            )
        }
    }
}

@Composable
private fun DiscardPageConfirm(onConfirm: () -> Unit, onDismiss: () -> Unit) {
    ConfirmDialog(
        title = "Discard this page?",
        text = "This page will not be uploaded. The invoice stays on paper — recapture it if you still need it.",
        confirmLabel = "Discard page",
        onConfirm = onConfirm,
        onDismiss = onDismiss,
        destructive = true,
    )
}

/** Open the batch once the laptop has made it, and a reminder that uploads carry on without this screen. */
@Composable
private fun CaptureBottomBar(s: CaptureSummary?, onOpenBatch: (String) -> Unit) {
    Column(Modifier.fillMaxWidth().background(Industry.Ground).navigationBarsPadding()) {
        HorizontalDivider(color = Industry.Divider)
        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            val batchId = s?.batchId
            PrimaryButton(
                text = if (batchId != null || s == null) "Open the batch" else "Waiting for the laptop…",
                onClick = { batchId?.let(onOpenBatch) },
                enabled = batchId != null,
            )
            Text(
                "Photos upload in the background. You can leave this screen; nothing is lost if the app closes.",
                style = MaterialTheme.typography.bodySmall,
                color = Industry.Muted,
            )
        }
    }
}
