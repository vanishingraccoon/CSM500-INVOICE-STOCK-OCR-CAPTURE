package com.invoicecapture.ui.batch

import androidx.activity.compose.BackHandler
import androidx.compose.animation.core.animate
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.Orientation
import androidx.compose.foundation.gestures.draggable
import androidx.compose.foundation.gestures.rememberDraggableState
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxScope
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.consumeWindowInsets
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.pager.HorizontalPager
import androidx.compose.foundation.pager.rememberPagerState
import androidx.compose.foundation.selection.selectable
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.VerticalDivider
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.Stable
import androidx.compose.runtime.derivedStateOf
import androidx.compose.runtime.getValue
import androidx.compose.runtime.key
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.runtime.snapshotFlow
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clipToBounds
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.repeatOnLifecycle
import kotlinx.coroutines.launch
import com.invoicecapture.data.api.InvoiceDto
import com.invoicecapture.data.api.LineDto
import com.invoicecapture.data.api.PageDto
import com.invoicecapture.data.pending.CaptureSummary
import com.invoicecapture.domain.batch.BatchRules
import com.invoicecapture.domain.batch.DuplicateActions
import com.invoicecapture.domain.batch.InvoiceTab
import com.invoicecapture.domain.batch.Money
import com.invoicecapture.domain.batch.PagerSelection
import com.invoicecapture.domain.batch.Polling
import com.invoicecapture.domain.batch.SubmitGate
import com.invoicecapture.domain.batch.TabOrdering
import com.invoicecapture.domain.batch.Times
import com.invoicecapture.domain.batch.Totals
import com.invoicecapture.domain.batch.TotalsStatus
import com.invoicecapture.ui.components.AppBar
import com.invoicecapture.ui.components.Figure
import com.invoicecapture.ui.components.Kicker
import com.invoicecapture.ui.components.Notice
import com.invoicecapture.ui.components.Plate
import com.invoicecapture.ui.components.PrimaryButton
import com.invoicecapture.ui.components.ReadingBar
import com.invoicecapture.ui.components.ScannerLimits
import com.invoicecapture.ui.components.SecondaryButton
import com.invoicecapture.ui.components.Tag
import com.invoicecapture.ui.components.TextAction
import com.invoicecapture.ui.components.ZoomableImage
import com.invoicecapture.ui.components.containerViewModel
import com.invoicecapture.ui.components.plate
import com.invoicecapture.ui.components.rememberDocumentScanner
import com.invoicecapture.ui.theme.Industry
import kotlin.math.roundToInt

/** Collapsed height of the invoice panel on a phone: supplier, number, date and totals stay visible. */
private val PANEL_PEEK = 140.dp

/** Wide screens (tablets, unfolded phones) show the panel beside the photo instead. */
private val WIDE = 600.dp

/** How far the phone panel is pulled up: 0 = peek, 1 = open. Shared by every invoice, so it stays open while swiping. */
@Stable
class PanelState {
    var fraction by mutableFloatStateOf(0f)
    val open: Boolean get() = fraction > 0.5f

    suspend fun settle(target: Float) {
        animate(fraction, target, animationSpec = tween(durationMillis = 180)) { value, _ -> fraction = value }
    }
}

@Composable
fun BatchScreen(onBack: () -> Unit) {
    val vm = containerViewModel { container, saved -> BatchViewModel(container, saved) }
    val state by vm.state.collectAsStateWithLifecycle()
    val lifecycleOwner = LocalLifecycleOwner.current
    LaunchedEffect(vm, lifecycleOwner) {
        lifecycleOwner.repeatOnLifecycle(Lifecycle.State.STARTED) { vm.pollWhileVisible() }
    }

    val recapture = rememberDocumentScanner(ScannerLimits.RETAKE_PAGES, onPages = vm::onRetakeScanned, onError = vm::onScannerError)
    val panel = remember { PanelState() }
    val scope = rememberCoroutineScope()
    val panelOpen by remember { derivedStateOf { panel.open } }
    BackHandler(enabled = panelOpen) { scope.launch { panel.settle(0f) } }

    val detail = state.detail
    Scaffold(
        topBar = {
            AppBar(
                title = detail?.createdAt?.let { "Batch ${Times.short(it)}" } ?: "Batch",
                subtitle = "${BatchRules.statusLabel(state.status)} · ${state.batchId}",
                onBack = onBack,
                actions = { TextAction("Refresh", vm::refreshNow) },
            )
        },
        bottomBar = { SubmitBar(state, onSubmit = { vm.openDialog(BatchDialog.ConfirmSubmit) }) },
        containerColor = Industry.Ground,
    ) { padding ->
        Column(Modifier.fillMaxSize().padding(padding).consumeWindowInsets(padding)) {
            TabStrip(state.tab, count = state::count, onSelect = vm::selectTab)
            StatusStrip(state, onDismiss = vm::dismissMessages)
            val invoices = state.invoicesIn(state.tab)
            Box(Modifier.weight(1f).fillMaxWidth().background(if (state.tab.red) Industry.RedWash else Industry.Ground)) {
                when {
                    detail == null -> CentreText(
                        if (state.loading) "Loading the batch…" else "The batch has not loaded yet. It loads by itself when the laptop answers.",
                    )
                    invoices.isEmpty() -> CentreText(emptyText(state))
                    else -> InvoicePager(state, invoices, vm, panel, onRecapture = recapture)
                }
            }
        }
    }
    BatchDialogs(state, vm)
}

private fun emptyText(state: BatchUiState): String = when {
    state.reading -> "Nothing in ${state.tab.label} yet. Invoices appear here as the laptop reads the photos."
    else -> "Nothing in ${state.tab.label}."
}

@Composable
private fun BoxScope.CentreText(text: String) {
    Text(
        text,
        modifier = Modifier.align(Alignment.Center).padding(24.dp),
        style = MaterialTheme.typography.bodyMedium,
        color = Industry.Muted,
        textAlign = TextAlign.Center,
    )
}

/** Good / Corrections / Retake / Duplicates with counts. Corrections is always red. */
@Composable
private fun TabStrip(selected: InvoiceTab, count: (InvoiceTab) -> Int, onSelect: (InvoiceTab) -> Unit) {
    Column(Modifier.fillMaxWidth().background(Industry.Ground)) {
        Row(Modifier.fillMaxWidth()) {
            InvoiceTab.ORDER.forEach { tab ->
                val isSelected = tab == selected
                val background = when {
                    tab.red && isSelected -> Industry.Red
                    tab.red -> Industry.RedWash
                    isSelected -> Industry.Accent100
                    else -> Industry.Ground
                }
                val foreground = when {
                    tab.red && isSelected -> Color.White
                    tab.red -> Industry.Red
                    else -> Industry.Ink
                }
                Box(
                    Modifier
                        .weight(1f)
                        .heightIn(min = 60.dp)
                        .background(background)
                        .selectable(selected = isSelected, role = Role.Tab, onClick = { onSelect(tab) }),
                ) {
                    Column(
                        Modifier.align(Alignment.Center).padding(vertical = 6.dp),
                        horizontalAlignment = Alignment.CenterHorizontally,
                    ) {
                        Text("${count(tab)}", style = MaterialTheme.typography.titleLarge.copy(fontFeatureSettings = "tnum"), color = foreground)
                        Text(
                            tab.label.uppercase(),
                            style = MaterialTheme.typography.labelMedium.copy(fontSize = 11.sp, letterSpacing = 0.5.sp),
                            color = foreground,
                            maxLines = 1,
                        )
                    }
                    Box(
                        Modifier
                            .align(Alignment.BottomCenter)
                            .fillMaxWidth()
                            .height(3.dp)
                            .background(if (!isSelected) Color.Transparent else if (tab.red) Industry.OnRedWash else Industry.Accent),
                    )
                }
            }
        }
        HorizontalDivider(color = Industry.Divider)
    }
}

@Composable
private fun StatusStrip(state: BatchUiState, onDismiss: () -> Unit) {
    val detail = state.detail
    val showProgress = detail != null && Polling.isActive(detail.status)
    val error = state.actionError?.takeIf { state.dialog == null }
    val hasContent = !state.reachable || state.laptopMessage != null || showProgress || state.photosOnPhone > 0 ||
        error != null || state.notice != null
    if (!hasContent) return
    Column(
        Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 6.dp),
        verticalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        if (!state.reachable) {
            Notice(
                "Waiting for the laptop",
                ((state.laptopMessage ?: "") + " Showing what was last loaded; this screen updates by itself.").trim(),
                edge = Industry.Accent,
            )
        } else if (state.laptopMessage != null) {
            Notice("Laptop", state.laptopMessage, edge = Industry.Red)
        }
        if (detail != null && showProgress) {
            val p = detail.progress
            val done = p.read + p.failed
            Text(readingText(p.photos, done, p.waiting, p.reading, state.photosOnPhone), style = MaterialTheme.typography.bodySmall)
            ReadingBar(if (p.photos == 0) 0f else done.toFloat() / p.photos)
        } else if (state.photosOnPhone > 0) {
            Text(photosOnPhoneText(state.photosOnPhone), style = MaterialTheme.typography.bodySmall, color = Industry.Accent700)
        }
        if (error != null) {
            DismissibleNotice("Not done", error, Industry.Red, onDismiss)
        } else {
            state.notice?.let { DismissibleNotice("Done", it, Industry.Accent, onDismiss) }
        }
    }
}

private fun readingText(photos: Int, done: Int, waiting: Int, reading: Int, onPhone: Int): String = buildString {
    append("Reading photos: $done of $photos done")
    if (waiting > 0) append(" · $waiting waiting")
    if (reading > 0) append(" · $reading reading")
    if (onPhone > 0) append(" · ").append(photosOnPhoneText(onPhone))
}

private fun photosOnPhoneText(n: Int): String =
    if (n == 1) "1 photo still uploading from this phone" else "$n photos still uploading from this phone"

@Composable
private fun DismissibleNotice(title: String, body: String, edge: Color, onDismiss: () -> Unit) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        Notice(title, body, Modifier.weight(1f), edge = edge)
        TextAction("OK", onDismiss)
    }
}

@Composable
private fun SubmitBar(state: BatchUiState, onSubmit: () -> Unit) {
    Column(Modifier.fillMaxWidth().background(Industry.Ground).navigationBarsPadding()) {
        HorizontalDivider(color = Industry.Divider)
        Column(Modifier.padding(horizontal = 12.dp, vertical = 8.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            val detail = state.detail
            if (detail != null && state.submitted) {
                Text(
                    "Submitted ${Times.short(detail.submittedAt)}. The input report is ready on the laptop. " +
                        "Retakes still go to the laptop and join the next report.",
                    modifier = Modifier.heightIn(min = 48.dp),
                    style = MaterialTheme.typography.bodyMedium,
                )
            } else {
                val gate = state.gate
                PrimaryButton(
                    text = if (state.busy) "Working…" else "Submit batch",
                    onClick = onSubmit,
                    enabled = gate is SubmitGate.Allowed && !state.busy,
                )
                Text(
                    when (gate) {
                        is SubmitGate.Blocked -> gate.reason
                        SubmitGate.Allowed -> "Good and Corrections invoices go into the report as they stand; Retake invoices are listed as unresolved."
                    },
                    style = MaterialTheme.typography.bodySmall,
                    color = Industry.Muted,
                )
            }
        }
    }
}

/** Swipe between the invoices of one tab. */
@Composable
private fun InvoicePager(
    state: BatchUiState,
    invoices: List<InvoiceDto>,
    vm: BatchViewModel,
    panel: PanelState,
    onRecapture: () -> Unit,
) {
    val tab = state.tab
    key(tab) {
        val ids = invoices.map { it.invoiceId }
        val pagerState = rememberPagerState(initialPage = PagerSelection.indexFor(ids, vm.selectedInvoiceId(tab), 0)) { invoices.size }
        val latestIds by rememberUpdatedState(ids)
        LaunchedEffect(pagerState) {
            snapshotFlow { pagerState.settledPage }.collect { page ->
                latestIds.getOrNull(page)?.let { vm.onInvoiceShown(tab, it) }
            }
        }
        LaunchedEffect(ids) {
            // After an edit the invoice may have left this tab: stay on it, or on the one that took its place.
            val target = PagerSelection.indexFor(ids, vm.selectedInvoiceId(tab), pagerState.currentPage)
            if (target != pagerState.currentPage) pagerState.scrollToPage(target)
        }
        Column(Modifier.fillMaxSize()) {
            Text(
                "Invoice ${pagerState.currentPage + 1} of ${invoices.size}" + (if (invoices.size > 1) " · swipe for the next" else ""),
                modifier = Modifier.padding(horizontal = 12.dp, vertical = 4.dp),
                style = MaterialTheme.typography.bodySmall,
                color = if (tab.red) Industry.OnRedWash else Industry.Muted,
            )
            HorizontalPager(
                state = pagerState,
                modifier = Modifier.weight(1f).fillMaxWidth(),
                key = { index -> invoices.getOrNull(index)?.invoiceId ?: "gone-$index" },
            ) { index ->
                val invoice = invoices.getOrNull(index)
                if (invoice != null) InvoicePage(invoice, state, vm, panel, onRecapture)
            }
        }
    }
}

@Composable
private fun InvoicePage(
    invoice: InvoiceDto,
    state: BatchUiState,
    vm: BatchViewModel,
    panel: PanelState,
    onRecapture: () -> Unit,
) {
    val tab = state.tab
    val panelColor = if (tab.red) Industry.RedWash else Industry.Ground
    val other = invoice.duplicateOfId?.let { state.others[it] }
    if (tab == InvoiceTab.DUPLICATES) {
        LaunchedEffect(invoice.invoiceId, invoice.duplicateOfId, state.reachable) { vm.loadOther(invoice) }
    }
    var showOther by rememberSaveable(invoice.invoiceId) { mutableStateOf(false) }
    val otherInvoice = (other as? OtherInvoice.Found)?.invoice
    val shown = if (showOther && otherInvoice != null) otherInvoice else invoice
    val recapture: ((PageDto) -> Unit)? = if (tab == InvoiceTab.RETAKE && shown === invoice) {
        { page ->
            vm.beginRetake(page.photoId, page.ordinal)
            onRecapture()
        }
    } else {
        null
    }
    val openHeader = { vm.openDialog(BatchDialog.EditHeader(invoice.invoiceId)) }

    BoxWithConstraints(Modifier.fillMaxSize().clipToBounds()) {
        val sidePanelWidth = if (maxWidth >= 900.dp) 420.dp else 360.dp
        if (maxWidth >= WIDE) {
            Row(Modifier.fillMaxSize()) {
                PhotoArea(
                    shown, tab, vm::photoUrl, state.retakesUploading, showOther, otherInvoice != null,
                    onShowOther = { showOther = it }, onRecapture = recapture, modifier = Modifier.weight(1f).fillMaxHeight(),
                )
                VerticalDivider(color = Industry.Divider)
                Column(Modifier.width(sidePanelWidth).fillMaxHeight().background(panelColor)) {
                    Spacer(Modifier.height(8.dp))
                    PanelHeader(invoice, onEdit = openHeader)
                    HorizontalDivider(color = Industry.Rule)
                    PanelBody(invoice, other, state, vm, Modifier.weight(1f))
                }
            }
        } else {
            PhotoArea(
                shown, tab, vm::photoUrl, state.retakesUploading, showOther, otherInvoice != null,
                onShowOther = { showOther = it }, onRecapture = recapture, modifier = Modifier.fillMaxSize().padding(bottom = PANEL_PEEK),
            )
            SlidingPanel(
                panel = panel,
                peek = PANEL_PEEK,
                expanded = maxHeight * 0.86f,
                background = panelColor,
                modifier = Modifier.align(Alignment.BottomCenter),
                header = { PanelHeader(invoice, onEdit = openHeader) },
            ) {
                PanelBody(invoice, other, state, vm, Modifier.weight(1f))
            }
        }
    }
}

/** The panel over the lower part of the photo: drag or tap its top edge to open or close it. */
@Composable
private fun SlidingPanel(
    panel: PanelState,
    peek: Dp,
    expanded: Dp,
    background: Color,
    modifier: Modifier,
    header: @Composable () -> Unit,
    body: @Composable ColumnScope.() -> Unit,
) {
    val scope = rememberCoroutineScope()
    val rangePx = with(LocalDensity.current) { (expanded - peek).toPx() }.coerceAtLeast(1f)
    val open by remember { derivedStateOf { panel.open } }
    val dragState = rememberDraggableState { delta ->
        panel.fraction = (panel.fraction - delta / rangePx).coerceIn(0f, 1f)
    }
    Column(
        modifier
            .fillMaxWidth()
            .height(expanded)
            .offset { IntOffset(0, ((1f - panel.fraction) * rangePx).roundToInt()) }
            .background(background)
            .plate(marks = false),
    ) {
        Column(
            Modifier
                .fillMaxWidth()
                .draggable(
                    state = dragState,
                    orientation = Orientation.Vertical,
                    onDragStopped = { velocity ->
                        val target = when {
                            velocity < -800f -> 1f
                            velocity > 800f -> 0f
                            panel.open -> 1f
                            else -> 0f
                        }
                        scope.launch { panel.settle(target) }
                    },
                )
                .clickable(onClickLabel = if (open) "Hide the details" else "Show the details") {
                    scope.launch { panel.settle(if (panel.open) 0f else 1f) }
                },
        ) {
            Box(Modifier.fillMaxWidth().padding(top = 6.dp, bottom = 2.dp), contentAlignment = Alignment.Center) {
                Box(Modifier.width(40.dp).height(4.dp).background(Industry.Neutral600))
            }
            header()
        }
        body()
    }
}

@Composable
private fun PanelHeader(invoice: InvoiceDto, onEdit: () -> Unit) {
    val totals = Totals.status(invoice.statedTotalExcl, invoice.linesSumExcl, invoice.addsUp)
    Column(Modifier.fillMaxWidth().padding(start = 12.dp, end = 4.dp, bottom = 8.dp), verticalArrangement = Arrangement.spacedBy(2.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text(invoice.title, style = MaterialTheme.typography.titleLarge, maxLines = 1, overflow = TextOverflow.Ellipsis)
                Text(
                    headerLine(invoice),
                    style = MaterialTheme.typography.bodySmall,
                    color = Industry.Muted,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
            }
            TextAction("Edit", onEdit)
        }
        Row(Modifier.padding(end = 8.dp), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            Figure("Stated excl.", Money.format(invoice.statedTotalExcl), Modifier.weight(1f))
            Figure(
                "Lines sum",
                Money.format(invoice.linesSumExcl),
                Modifier.weight(1f),
                valueColor = if (totals is TotalsStatus.DoesNotAddUp) Industry.Red else Industry.Ink,
            )
            Figure("Incl. VAT", Money.format(invoice.statedTotalIncl), Modifier.weight(1f))
        }
    }
}

private fun headerLine(invoice: InvoiceDto): String = listOf(
    invoice.documentNo?.let { "INV $it" } ?: "No invoice number",
    invoice.date ?: "no date",
    pagesLabel(invoice),
).joinToString(" · ")

/** Scan positions, so the manager can find the paper in the pile: "pages 3, 4". */
internal fun pagesLabel(invoice: InvoiceDto): String {
    val ordinals = invoice.pagesInOrder.map { it.ordinal }
    return when (ordinals.size) {
        0 -> "no photo"
        1 -> "page ${ordinals[0]}"
        else -> "pages ${ordinals.joinToString(", ")}"
    }
}

@Composable
private fun PanelBody(invoice: InvoiceDto, other: OtherInvoice?, state: BatchUiState, vm: BatchViewModel, modifier: Modifier) {
    val tab = state.tab
    val lines = remember(invoice.lines) { TabOrdering.starredFirst(invoice.lines, { it.star }, { it.index }) }
    val starred = lines.count { it.star }
    val totals = Totals.status(invoice.statedTotalExcl, invoice.linesSumExcl, invoice.addsUp)
    LazyColumn(
        modifier = modifier.fillMaxWidth(),
        contentPadding = PaddingValues(start = 12.dp, end = 12.dp, top = 4.dp, bottom = 24.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        item(key = "totals") {
            Text(
                Totals.describe(totals),
                style = MaterialTheme.typography.bodySmall,
                color = if (totals is TotalsStatus.DoesNotAddUp) Industry.Red else Industry.Muted,
            )
        }
        if (invoice.tabReasons.isNotEmpty()) {
            item(key = "reasons") { Reasons(tab, invoice.tabReasons) }
        }
        if (tab == InvoiceTab.DUPLICATES) {
            item(key = "duplicate") {
                DuplicateBlock(
                    invoice,
                    other,
                    busy = state.busy,
                    onKeep = { vm.notDuplicate(invoice.invoiceId) },
                    onDelete = { vm.openDialog(BatchDialog.ConfirmDeleteDuplicate(invoice.invoiceId)) },
                )
            }
        }
        if (tab == InvoiceTab.RETAKE) {
            item(key = "retake") {
                Notice(
                    "Retake",
                    "Tap Recapture on the photo to photograph that page again. The new photo replaces the old one and is read again.",
                    edge = Industry.Accent,
                )
            }
        }
        item(key = "actions") {
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                SecondaryButton("Search stock", { vm.openDialog(BatchDialog.SearchStock(invoice.invoiceId)) }, Modifier.weight(1f))
                SecondaryButton("Add a line", { vm.openDialog(BatchDialog.EditLine(invoice.invoiceId)) }, Modifier.weight(1f))
            }
        }
        item(key = "lines-title") {
            Kicker(
                if (starred > 0) "Lines · ★ $starred to fix" else "Lines · tap one to change it",
                color = if (starred > 0 && tab.red) Industry.Red else Industry.Muted,
            )
        }
        if (lines.isEmpty()) {
            item(key = "no-lines") {
                Text("No lines were read on this invoice. Add them, or retake the photo.", style = MaterialTheme.typography.bodyMedium)
            }
        }
        items(lines, key = { "line-${it.index}" }) { line ->
            LineRow(line, onClick = { vm.openDialog(BatchDialog.EditLine(invoice.invoiceId, line.index)) })
        }
    }
}

@Composable
private fun Reasons(tab: InvoiceTab, reasons: List<String>) {
    Column(
        Modifier.fillMaxWidth().plate(marks = false, border = if (tab.red) Industry.Red else Industry.Divider).padding(10.dp),
        verticalArrangement = Arrangement.spacedBy(2.dp),
    ) {
        Kicker("Why it is in ${tab.label}", color = if (tab.red) Industry.Red else Industry.Muted)
        reasons.forEach { Text("• $it", style = MaterialTheme.typography.bodySmall, color = Industry.Ink) }
    }
}

/** One invoice line; starred lines (the ones that need the manager) are highlighted. */
@Composable
private fun LineRow(line: LineDto, onClick: () -> Unit) {
    val printed = line.printed
    Row(
        Modifier
            .fillMaxWidth()
            .heightIn(min = 56.dp)
            .background(if (line.star) Industry.StarWash else Color.Transparent)
            .plate(marks = false, border = if (line.star) Industry.Accent else Industry.Rule)
            .clickable(onClickLabel = "Change this line", onClick = onClick)
            .padding(horizontal = 8.dp, vertical = 6.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Text(if (line.star) "★" else "", Modifier.width(18.dp), style = MaterialTheme.typography.titleMedium, color = Industry.Accent700)
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(1.dp)) {
            Text(
                printed?.description ?: "(nothing printed was read)",
                style = MaterialTheme.typography.bodyMedium,
                fontWeight = FontWeight.Medium,
                maxLines = 2,
                overflow = TextOverflow.Ellipsis,
            )
            val item = line.item
            Text(
                if (item != null) "→ ${item.name ?: item.code} (${item.code})" else "Not matched to a stock item",
                style = MaterialTheme.typography.bodySmall,
                color = if (item == null) Industry.Red else Industry.Muted,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
            Text(quantityLine(line), style = MaterialTheme.typography.bodySmall, color = Industry.Muted, maxLines = 1)
            line.refusalText?.let { Text("Cannot convert: $it", style = MaterialTheme.typography.bodySmall, color = Industry.Red) }
            line.flags.forEach { flag ->
                (flag.reason ?: flag.kind)?.let { Text("⚑ $it", style = MaterialTheme.typography.bodySmall, color = Industry.Red) }
            }
        }
        Column(horizontalAlignment = Alignment.End) {
            Text(Money.format(printed?.lineTotal), style = MaterialTheme.typography.bodyMedium.copy(fontFeatureSettings = "tnum"))
            if (line.byManager) Text("✎ typed", style = MaterialTheme.typography.labelSmall, color = Industry.Muted)
        }
    }
}

private fun quantityLine(line: LineDto): String {
    val printed = line.printed
    return buildString {
        append("Qty ").append(Money.formatQuantity(printed?.quantity))
        printed?.unit?.let { append(' ').append(it) }
        printed?.pack?.let { append(" · pack ").append(it) }
        line.baseQuantity?.let { base ->
            append(" → ").append(Money.formatQuantity(base))
            line.item?.baseUnit?.let { append(' ').append(it) }
        }
    }
}

@Composable
private fun DuplicateBlock(invoice: InvoiceDto, other: OtherInvoice?, busy: Boolean, onKeep: () -> Unit, onDelete: () -> Unit) {
    val buttons = DuplicateActions.buttons(InvoiceTab.DUPLICATES, busy)
    Plate(Modifier.fillMaxWidth()) {
        Kicker("Duplicate check")
        when (other) {
            is OtherInvoice.Found -> CompareTable(invoice, other.invoice)
            is OtherInvoice.Missing -> Text(
                "${other.message} Compare on the laptop if unsure.",
                style = MaterialTheme.typography.bodySmall,
            )
            OtherInvoice.Loading -> Text("Looking for the other invoice…", style = MaterialTheme.typography.bodySmall)
            null -> Text(
                if (invoice.duplicateOfId == null) "The laptop did not say which invoice this one repeats." else "Looking for the other invoice…",
                style = MaterialTheme.typography.bodySmall,
            )
        }
        if (buttons.visible) {
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                SecondaryButton("Not a duplicate", onKeep, Modifier.weight(1f), enabled = buttons.enabled)
                SecondaryButton("Delete duplicate", onDelete, Modifier.weight(1f), enabled = buttons.enabled, color = Industry.Red)
            }
        }
    }
}

@Composable
private fun CompareTable(mine: InvoiceDto, other: InvoiceDto) {
    val rows = listOf(
        Triple("Supplier", mine.title, other.title),
        Triple("Invoice no.", mine.documentNo ?: "—", other.documentNo ?: "—"),
        Triple("Date", mine.date ?: "—", other.date ?: "—"),
        Triple("Stated excl.", Money.format(mine.statedTotalExcl), Money.format(other.statedTotalExcl)),
        Triple("Scan", pagesLabel(mine), pagesLabel(other)),
    )
    Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
        Row {
            Spacer(Modifier.width(92.dp))
            Text("THIS ONE", Modifier.weight(1f), style = MaterialTheme.typography.labelMedium, color = Industry.Muted)
            Text("THE OTHER", Modifier.weight(1f), style = MaterialTheme.typography.labelMedium, color = Industry.Muted)
        }
        rows.forEach { (label, a, b) ->
            Row {
                Text(label, Modifier.width(92.dp), style = MaterialTheme.typography.bodySmall, color = Industry.Muted)
                Text(a, Modifier.weight(1f), style = MaterialTheme.typography.bodySmall)
                Text(b, Modifier.weight(1f), style = MaterialTheme.typography.bodySmall, color = if (a == b) Industry.Ink else Industry.Red)
            }
        }
    }
}

/** The invoice photo(s): pinch-zoom, page badges 1, 2, 3, quality tags, and Recapture in the Retake tab. */
@Composable
private fun PhotoArea(
    invoice: InvoiceDto,
    tab: InvoiceTab,
    photoUrl: (String) -> String?,
    retakesUploading: Set<String>,
    showingOther: Boolean,
    otherAvailable: Boolean,
    onShowOther: (Boolean) -> Unit,
    onRecapture: ((PageDto) -> Unit)?,
    modifier: Modifier,
) {
    val pages = invoice.pagesInOrder
    var selected by rememberSaveable(invoice.invoiceId) { mutableIntStateOf(0) }
    val index = if (pages.isEmpty()) 0 else selected.coerceIn(0, pages.lastIndex)
    val page = pages.getOrNull(index)
    Box(modifier.background(if (tab.red) Industry.RedWash else Industry.Surface)) {
        if (page == null) {
            Text(
                "No photo for this invoice.",
                Modifier.align(Alignment.Center).padding(24.dp),
                style = MaterialTheme.typography.bodyMedium,
                color = Industry.Muted,
            )
        } else {
            ZoomableImage(
                url = photoUrl(page.photoId),
                contentDescription = "${invoice.title}, page ${index + 1} of ${pages.size}",
                modifier = Modifier.fillMaxSize(),
            )
        }
        Column(Modifier.align(Alignment.TopStart).padding(4.dp)) {
            if (tab == InvoiceTab.DUPLICATES) DuplicateSwitch(showingOther, otherAvailable, onShowOther)
            if (pages.size > 1 || page?.quality?.ok == false) {
                Row {
                    pages.forEachIndexed { i, p ->
                        PageBadge(i + 1, selected = i == index, poor = p.quality?.ok == false, onClick = { selected = i })
                    }
                }
            }
        }
        if (page != null) {
            Column(
                Modifier.align(Alignment.BottomStart).fillMaxWidth().padding(8.dp),
                verticalArrangement = Arrangement.spacedBy(6.dp),
            ) {
                val problems = page.quality?.problems.orEmpty()
                if (problems.isNotEmpty()) Tag("Page ${index + 1}: ${CaptureSummary.wordsFor(problems)}", solid = true, color = Industry.Red)
                if (page.photoId in retakesUploading) Tag("New photo uploading", solid = true, color = Industry.Accent700)
                if (onRecapture != null) PrimaryButton("Recapture page ${index + 1}", onClick = { onRecapture(page) })
            }
        }
    }
}

/** A small page number with a full 48 dp touch target; a red frame marks a page with quality problems. */
@Composable
private fun PageBadge(number: Int, selected: Boolean, poor: Boolean, onClick: () -> Unit) {
    Box(
        Modifier.size(48.dp).selectable(selected = selected, role = Role.Tab, onClick = onClick),
        contentAlignment = Alignment.Center,
    ) {
        Box(
            Modifier
                .size(30.dp)
                .background(if (selected) Industry.Accent700 else Industry.Ground)
                .border(if (poor) 2.dp else 1.dp, if (poor) Industry.Red else Industry.Neutral600),
            contentAlignment = Alignment.Center,
        ) {
            Text("$number", style = MaterialTheme.typography.labelLarge, color = if (selected) Industry.Ground else Industry.Ink)
        }
    }
}

@Composable
private fun DuplicateSwitch(showingOther: Boolean, otherAvailable: Boolean, onChange: (Boolean) -> Unit) {
    Row(Modifier.background(Industry.Ground).plate(marks = false)) {
        SwitchCell("This one", selected = !showingOther, enabled = true) { onChange(false) }
        SwitchCell("The other", selected = showingOther, enabled = otherAvailable) { onChange(true) }
    }
}

@Composable
private fun SwitchCell(label: String, selected: Boolean, enabled: Boolean, onClick: () -> Unit) {
    Box(
        Modifier
            .heightIn(min = 48.dp)
            .background(if (selected) Industry.Accent700 else Color.Transparent)
            .selectable(selected = selected, enabled = enabled, role = Role.Tab, onClick = onClick)
            .padding(horizontal = 14.dp),
        contentAlignment = Alignment.Center,
    ) {
        Text(
            label,
            style = MaterialTheme.typography.labelLarge,
            color = when {
                selected -> Industry.Ground
                enabled -> Industry.Ink
                else -> Industry.Muted
            },
        )
    }
}
