package com.invoicecapture.ui.batch

import android.net.Uri
import androidx.lifecycle.SavedStateHandle
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import androidx.navigation.toRoute
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.serialization.Serializable
import com.invoicecapture.data.AppContainer
import com.invoicecapture.data.api.ApiJson
import com.invoicecapture.data.api.ApiResult
import com.invoicecapture.data.api.BatchDetailDto
import com.invoicecapture.data.api.InvoiceDto
import com.invoicecapture.data.api.StockItemDto
import com.invoicecapture.data.api.errorCode
import com.invoicecapture.data.api.httpStatus
import com.invoicecapture.data.api.plainMessage
import com.invoicecapture.data.pending.PendingSession
import com.invoicecapture.data.pending.SessionKind
import com.invoicecapture.domain.batch.BatchRules
import com.invoicecapture.domain.batch.DuplicateAction
import com.invoicecapture.domain.batch.DuplicateActions
import com.invoicecapture.domain.batch.DuplicateLookup
import com.invoicecapture.domain.batch.HeaderCheck
import com.invoicecapture.domain.batch.InvoiceTab
import com.invoicecapture.domain.batch.LineEdit
import com.invoicecapture.domain.batch.LookupStep
import com.invoicecapture.domain.batch.Polling
import com.invoicecapture.domain.batch.Regrouping
import com.invoicecapture.domain.batch.SubmitGate
import com.invoicecapture.domain.batch.TabOrdering
import com.invoicecapture.ui.BatchRoute
import java.io.IOException

/** The one dialog open on the batch screen; saved as JSON so it survives rotation and process death. */
@Serializable
sealed class BatchDialog {
    /** [index] null adds a line. [itemCode] is a stock item already picked in the panel's search. */
    @Serializable
    data class EditLine(val invoiceId: String, val index: Int? = null, val itemCode: String? = null, val itemName: String? = null) : BatchDialog()

    @Serializable
    data class SearchStock(val invoiceId: String) : BatchDialog()

    @Serializable
    data class PickLine(val invoiceId: String, val itemCode: String, val itemName: String? = null) : BatchDialog()

    @Serializable
    data class EditHeader(val invoiceId: String) : BatchDialog()

    @Serializable
    data class ConfirmDeleteLine(val invoiceId: String, val index: Int) : BatchDialog()

    @Serializable
    data class ConfirmDeleteDuplicate(val invoiceId: String) : BatchDialog()

    @Serializable
    data object ConfirmSubmit : BatchDialog()

    @Serializable
    data class Submitted(val pdf: String? = null, val csv: String? = null) : BatchDialog()
}

/** The invoice a duplicate repeats (contract §4 `duplicate_of`). */
sealed interface OtherInvoice {
    data object Loading : OtherInvoice
    data class Found(val invoice: InvoiceDto) : OtherInvoice

    /** The laptop could not hand it over; [message] says why, in plain words. */
    data class Missing(val message: String) : OtherInvoice
}

data class StockSearchUi(
    val query: String = "",
    val items: List<StockItemDto> = emptyList(),
    val searching: Boolean = false,
    val message: String? = null,
)

data class BatchUiState(
    val batchId: String,
    val detail: BatchDetailDto? = null,
    val loading: Boolean = true,
    val reachable: Boolean = true,
    val laptopMessage: String? = null,
    val tab: InvoiceTab = InvoiceTab.GOOD,
    val tabs: Map<InvoiceTab, List<InvoiceDto>> = emptyMap(),
    val photosOnPhone: Int = 0,
    /** Photo ids whose retake is still on this phone. */
    val retakesUploading: Set<String> = emptySet(),
    val busy: Boolean = false,
    val notice: String? = null,
    val actionError: String? = null,
    val dialog: BatchDialog? = null,
    val others: Map<String, OtherInvoice> = emptyMap(),
    val search: StockSearchUi = StockSearchUi(),
) {
    val status: String? get() = detail?.status
    val submitted: Boolean get() = status == "submitted"

    /** Photos still being read (or nothing known yet). */
    val reading: Boolean get() = detail == null || Polling.isActive(status)

    val gate: SubmitGate
        get() = if (detail == null) {
            SubmitGate.Blocked("Waiting for the laptop.")
        } else {
            BatchRules.submitGate(detail.status, detail.progress.waiting, detail.progress.reading, photosOnPhone)
        }

    fun invoicesIn(tab: InvoiceTab): List<InvoiceDto> = tabs[tab].orEmpty()

    fun count(tab: InvoiceTab): Int = tabs[tab]?.size ?: 0

    fun invoice(id: String): InvoiceDto? = detail?.invoices?.firstOrNull { it.invoiceId == id }
}

class BatchViewModel(private val container: AppContainer, private val saved: SavedStateHandle) : ViewModel() {

    val batchId: String = saved.toRoute<BatchRoute>().batchId

    private val _state = MutableStateFlow(
        BatchUiState(
            batchId = batchId,
            tab = InvoiceTab.fromWire(saved.get<String>(KEY_TAB)) ?: InvoiceTab.GOOD,
            dialog = decodeDialog(saved.get<String>(KEY_DIALOG)),
        ),
    )
    val state: StateFlow<BatchUiState> = _state.asStateFlow()

    private val refreshLock = Mutex()
    private var failures = 0
    private var searchJob: Job? = null

    init {
        container.invoices.cachedBatch(batchId)?.let { showDetail(it) }
        viewModelScope.launch {
            container.pendingStore.ensureLoaded()
            container.pendingStore.sessions.collect { showSessions(it) }
        }
    }

    /** Runs while the screen is visible: every 3 s while uploading or reading, 30 s otherwise (contract §3). */
    suspend fun pollWhileVisible() {
        while (true) {
            refresh()
            delay(Polling.intervalMillis(_state.value.status, failures))
        }
    }

    fun refreshNow() {
        viewModelScope.launch { refresh() }
    }

    /** One `GET /batches/{id}` with the ETag; a 304 keeps what is shown. */
    suspend fun refresh() {
        refreshLock.withLock {
            val wasAway = !_state.value.reachable
            when (val result = container.invoices.batch(batchId)) {
                is ApiResult.Ok -> {
                    failures = 0
                    showDetail(result.value)
                    _state.update { it.copy(loading = false, reachable = true, laptopMessage = null) }
                    // The laptop is back: uploads waiting out a backoff go now.
                    if (wasAway) container.scheduler.resumeAll(container.pendingStore, includeStopped = false)
                }
                is ApiResult.NetworkError -> {
                    failures++
                    _state.update { it.copy(loading = false, reachable = false, laptopMessage = result.plainMessage()) }
                }
                else -> {
                    failures++
                    _state.update { it.copy(loading = false, reachable = true, laptopMessage = result.plainMessage()) }
                }
            }
        }
    }

    private fun showDetail(detail: BatchDetailDto) {
        val groups = TabOrdering.group(detail.invoices, { it.tab }, { it.firstOrdinal }, { it.invoiceId })
        val savedTab = InvoiceTab.fromWire(saved.get<String>(KEY_TAB))
        val tab = when {
            savedTab != null -> savedTab
            // The first load with invoices opens the first tab that needs the manager.
            detail.invoices.isNotEmpty() -> TabOrdering.defaultTab(groups.mapValues { it.value.size }).also { saved[KEY_TAB] = it.wire }
            else -> _state.value.tab
        }
        _state.update { it.copy(detail = detail, tabs = groups, tab = tab) }
    }

    private fun showSessions(sessions: List<PendingSession>) {
        val mine = sessions.filter { it.serverBatchId == batchId }
        _state.update { state ->
            state.copy(
                photosOnPhone = mine.sumOf { it.pagesOnPhone },
                retakesUploading = mine.filter { it.kind == SessionKind.RETAKE }
                    .flatMap { it.pages }
                    .filter { it.onPhone }
                    .mapNotNull { it.replacesPhotoId }
                    .toSet(),
            )
        }
    }

    // Tabs and the invoice shown in each

    fun selectTab(tab: InvoiceTab) {
        saved[KEY_TAB] = tab.wire
        _state.update { it.copy(tab = tab) }
    }

    fun selectedInvoiceId(tab: InvoiceTab): String? = saved.get<String>(selectionKey(tab))

    fun onInvoiceShown(tab: InvoiceTab, invoiceId: String) {
        saved[selectionKey(tab)] = invoiceId
    }

    // Dialogs and messages

    fun openDialog(dialog: BatchDialog) {
        if (dialog is BatchDialog.EditLine || dialog is BatchDialog.SearchStock) clearSearch()
        _state.update { it.copy(actionError = null) }
        setDialog(dialog)
    }

    fun closeDialog() {
        setDialog(null)
        _state.update { it.copy(actionError = null) }
    }

    fun dismissMessages() {
        _state.update { it.copy(notice = null, actionError = null) }
    }

    private fun setDialog(dialog: BatchDialog?) {
        saved[KEY_DIALOG] = dialog?.let { ApiJson.encodeToString(BatchDialog.serializer(), it) }
        _state.update { it.copy(dialog = dialog) }
    }

    // Stock search (contract §5)

    fun onSearchQuery(query: String) {
        _state.update { it.copy(search = it.search.copy(query = query, message = null)) }
        searchJob?.cancel()
        val q = query.trim()
        if (q.length < 2) {
            _state.update { it.copy(search = it.search.copy(items = emptyList(), searching = false)) }
            return
        }
        searchJob = viewModelScope.launch {
            delay(350)
            runSearch(q)
        }
    }

    fun searchNow() {
        val q = _state.value.search.query.trim()
        if (q.isEmpty()) {
            _state.update { it.copy(search = it.search.copy(message = "Type part of the item's name or code.")) }
            return
        }
        searchJob?.cancel()
        searchJob = viewModelScope.launch { runSearch(q) }
    }

    private suspend fun runSearch(q: String) {
        _state.update { it.copy(search = it.search.copy(searching = true, message = null)) }
        when (val result = container.invoices.searchStock(q)) {
            is ApiResult.Ok -> _state.update {
                it.copy(
                    search = it.search.copy(
                        items = result.value.items,
                        searching = false,
                        message = if (result.value.items.isEmpty()) "Nothing in this shop's key matches \"$q\"." else null,
                    ),
                )
            }
            else -> _state.update { it.copy(search = it.search.copy(searching = false, message = result.plainMessage())) }
        }
    }

    private fun clearSearch() {
        searchJob?.cancel()
        _state.update { it.copy(search = StockSearchUi()) }
    }

    /** From the panel's search: an item was chosen; next, which line it is for. */
    fun pickStockItem(invoiceId: String, itemCode: String, itemName: String?) {
        setDialog(BatchDialog.PickLine(invoiceId, itemCode, itemName))
    }

    fun pickLine(invoiceId: String, index: Int?, itemCode: String, itemName: String?) {
        openDialog(BatchDialog.EditLine(invoiceId, index, itemCode, itemName))
    }

    // Manager actions (contract §5); each returns the invoice, which may have moved tab

    fun saveLine(invoiceId: String, index: Int?, edit: LineEdit) =
        invoiceAction(invoiceId, closeOnError = false, call = { container.invoices.saveLine(invoiceId, index, edit) }) { old, invoice ->
            DuplicateActions.movedMessage(old, invoice.tab)
        }

    fun deleteLine(invoiceId: String, index: Int) =
        invoiceAction(invoiceId, closeOnError = true, call = { container.invoices.deleteLine(invoiceId, index) }) { old, invoice ->
            DuplicateActions.movedMessage(old, invoice.tab)
        }

    fun saveHeader(invoiceId: String, header: HeaderCheck.Valid) =
        invoiceAction(
            invoiceId,
            closeOnError = false,
            call = { container.invoices.editHeader(invoiceId, header.supplier, header.documentNo, header.date) },
        ) { old, invoice -> DuplicateActions.movedMessage(old, invoice.tab) }

    fun deleteDuplicate(invoiceId: String) =
        invoiceAction(invoiceId, closeOnError = true, call = { container.invoices.deleteDuplicate(invoiceId) }) { _, invoice ->
            DuplicateActions.doneMessage(DuplicateAction.DELETE, invoice.tab)
        }

    fun notDuplicate(invoiceId: String) =
        invoiceAction(invoiceId, closeOnError = true, call = { container.invoices.notDuplicate(invoiceId) }) { _, invoice ->
            DuplicateActions.doneMessage(DuplicateAction.NOT_DUPLICATE, invoice.tab)
        }

    private fun invoiceAction(
        invoiceId: String,
        closeOnError: Boolean,
        call: suspend () -> ApiResult<InvoiceDto>,
        message: (oldTab: InvoiceTab?, invoice: InvoiceDto) -> String,
    ) {
        if (_state.value.busy) return
        val before = _state.value.invoice(invoiceId)
        val oldTab = before?.tabEnum ?: _state.value.tab
        val oldPhotoIds = before?.pages?.map { it.photoId }?.toSet().orEmpty()
        _state.update { it.copy(busy = true, actionError = null, notice = null) }
        viewModelScope.launch {
            when (val result = call()) {
                is ApiResult.Ok -> {
                    container.invoices.applyInvoice(batchId, result.value)?.let { showDetail(it) }
                    setDialog(null)
                    _state.update { it.copy(busy = false, notice = message(oldTab, result.value)) }
                    refresh()
                }
                else -> {
                    val regrouped = result is ApiResult.HttpError && Regrouping.needsRefetch(result.status, result.error)
                    if (closeOnError || regrouped) setDialog(null)
                    _state.update { it.copy(busy = false, actionError = result.plainMessage()) }
                    if (regrouped) {
                        // The invoice's id changed while the laptop was reading: reload, then show the invoice now holding its photos.
                        refresh()
                        refind(oldPhotoIds)
                    }
                }
            }
        }
    }

    private fun refind(oldPhotoIds: Set<String>) {
        val detail = _state.value.detail ?: return
        val shown = detail.invoices.filter { it.tabEnum != null }
        val match = Regrouping.refind(shown, oldPhotoIds) { invoice -> invoice.pages.map { it.photoId } } ?: return
        val tab = match.tabEnum ?: return
        saved[selectionKey(tab)] = match.invoiceId
        selectTab(tab)
    }

    /** `POST /batches/{id}/submit` (contract §7); refused with 409 while photos are still being read. */
    fun submit() {
        if (_state.value.busy) return
        _state.update { it.copy(busy = true, actionError = null, notice = null) }
        viewModelScope.launch {
            when (val result = container.invoices.submit(batchId)) {
                is ApiResult.Ok -> {
                    val report = result.value.report
                    setDialog(
                        BatchDialog.Submitted(
                            pdf = report?.pdf?.let { container.invoices.portalUrl(it) ?: it },
                            csv = report?.csv?.let { container.invoices.portalUrl(it) ?: it },
                        ),
                    )
                    _state.update { it.copy(busy = false) }
                }
                else -> {
                    setDialog(null)
                    _state.update { it.copy(busy = false, actionError = result.plainMessage()) }
                }
            }
            refresh()
        }
    }

    // Duplicates

    /**
     * The invoice a duplicate repeats: a copy already loaded on the phone, otherwise
     * `GET /invoices/{id}` (contract §3), which reaches batches this phone never opened.
     */
    fun loadOther(invoice: InvoiceDto, allowRefetch: Boolean = true) {
        val otherId = invoice.duplicateOfId ?: return
        val known = _state.value.others[otherId]
        if (known is OtherInvoice.Found || known == OtherInvoice.Loading) return
        val local = container.invoices.loadedInvoice(otherId, batchId)
        if (local != null) {
            showOther(otherId, OtherInvoice.Found(local))
            return
        }
        showOther(otherId, OtherInvoice.Loading)
        viewModelScope.launch {
            val result = container.invoices.invoice(otherId)
            when (DuplicateLookup.decide(result.httpStatus, result.errorCode)) {
                LookupStep.SHOW -> showOther(
                    otherId,
                    (result as? ApiResult.Ok)?.value?.let(OtherInvoice::Found) ?: OtherInvoice.Missing(result.plainMessage()),
                )
                LookupStep.GONE -> showOther(otherId, OtherInvoice.Missing(DuplicateLookup.GONE_MESSAGE))
                LookupStep.NOT_NOW -> showOther(otherId, OtherInvoice.Missing(result.plainMessage()))
                LookupStep.REFETCH_BATCH -> {
                    // The laptop regrouped these pages while reading: load the batch again, then ask once more.
                    _state.update { it.copy(others = it.others - otherId) }
                    refresh()
                    val current = _state.value.invoice(invoice.invoiceId)
                    if (allowRefetch && current != null) {
                        loadOther(current, allowRefetch = false)
                    } else if (current != null) {
                        showOther(otherId, OtherInvoice.Missing(result.plainMessage()))
                    }
                }
            }
        }
    }

    private fun showOther(otherId: String, other: OtherInvoice) {
        _state.update { it.copy(others = it.others + (otherId to other)) }
    }

    // Retake (contract §5): one page, uploaded with X-Replaces-Photo

    fun beginRetake(photoId: String, ordinal: Int) {
        saved[KEY_RETAKE_PHOTO] = photoId
        saved[KEY_RETAKE_ORDINAL] = ordinal
    }

    fun onRetakeScanned(uris: List<Uri>) {
        val photoId = saved.get<String>(KEY_RETAKE_PHOTO) ?: return
        val ordinal = saved.get<Int>(KEY_RETAKE_ORDINAL) ?: 1
        val uri = uris.firstOrNull() ?: return
        saved.remove<String>(KEY_RETAKE_PHOTO)
        viewModelScope.launch {
            try {
                container.capture.startRetake(uri, batchId, photoId, ordinal)
                _state.update { it.copy(notice = "New photo saved. It uploads now, replaces the old one and is read again.") }
            } catch (e: IOException) {
                _state.update { it.copy(actionError = "The new photo could not be saved on this phone (${e.message}). Try again.") }
            }
        }
    }

    fun onScannerError(message: String) {
        _state.update { it.copy(actionError = message) }
    }

    fun photoUrl(photoId: String): String? = container.invoices.photoUrl(photoId)

    private companion object {
        const val KEY_TAB = "tab"
        const val KEY_DIALOG = "dialog"
        const val KEY_RETAKE_PHOTO = "retake_photo"
        const val KEY_RETAKE_ORDINAL = "retake_ordinal"

        fun selectionKey(tab: InvoiceTab) = "selected_${tab.wire}"

        fun decodeDialog(raw: String?): BatchDialog? = raw?.let {
            try {
                ApiJson.decodeFromString(BatchDialog.serializer(), it)
            } catch (e: IllegalArgumentException) {
                null
            }
        }
    }
}
