package com.invoicecapture.ui.batch

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.RectangleShape
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import com.invoicecapture.data.api.CandidateDto
import com.invoicecapture.data.api.InvoiceDto
import com.invoicecapture.data.api.LineDto
import com.invoicecapture.domain.batch.HeaderCheck
import com.invoicecapture.domain.batch.HeaderEdits
import com.invoicecapture.domain.batch.LineChoice
import com.invoicecapture.domain.batch.LineEdit
import com.invoicecapture.domain.batch.LineEditCheck
import com.invoicecapture.domain.batch.LineEdits
import com.invoicecapture.domain.batch.LineForm
import com.invoicecapture.domain.batch.Money
import com.invoicecapture.domain.batch.TabOrdering
import com.invoicecapture.domain.util.Decimals
import com.invoicecapture.ui.components.ConfirmDialog
import com.invoicecapture.ui.components.Kicker
import com.invoicecapture.ui.components.Notice
import com.invoicecapture.ui.components.Plate
import com.invoicecapture.ui.components.PrimaryButton
import com.invoicecapture.ui.components.SecondaryButton
import com.invoicecapture.ui.components.TextAction
import com.invoicecapture.ui.components.plate
import com.invoicecapture.ui.theme.Industry

/** Shows the dialog named in [BatchUiState.dialog]. */
@Composable
fun BatchDialogs(state: BatchUiState, vm: BatchViewModel) {
    when (val dialog = state.dialog) {
        null -> Unit
        is BatchDialog.EditLine -> EditLineDialog(dialog, state, vm)
        is BatchDialog.SearchStock -> state.invoice(dialog.invoiceId)?.let { invoice ->
            StockSearchDialog(
                invoice = invoice,
                search = state.search,
                onQuery = vm::onSearchQuery,
                onSearchNow = vm::searchNow,
                onPick = { code, name -> vm.pickStockItem(invoice.invoiceId, code, name) },
                onDismiss = vm::closeDialog,
            )
        }
        is BatchDialog.PickLine -> state.invoice(dialog.invoiceId)?.let { invoice ->
            LinePickerDialog(
                invoice = invoice,
                itemLabel = dialog.itemName ?: dialog.itemCode,
                onLine = { index -> vm.pickLine(invoice.invoiceId, index, dialog.itemCode, dialog.itemName) },
                onDismiss = vm::closeDialog,
            )
        }
        is BatchDialog.EditHeader -> state.invoice(dialog.invoiceId)?.let { invoice ->
            HeaderEditorDialog(
                invoice = invoice,
                busy = state.busy,
                error = state.actionError,
                onSave = { vm.saveHeader(invoice.invoiceId, it) },
                onDismiss = vm::closeDialog,
            )
        }
        is BatchDialog.ConfirmDeleteLine -> ConfirmDialog(
            title = "Remove this line?",
            text = "Only for a line the laptop invented: it is not on the paper invoice.",
            confirmLabel = if (state.busy) "Removing…" else "Remove line",
            onConfirm = { vm.deleteLine(dialog.invoiceId, dialog.index) },
            onDismiss = vm::closeDialog,
            destructive = true,
        )
        is BatchDialog.ConfirmDeleteDuplicate -> ConfirmDialog(
            title = "Delete this duplicate?",
            text = "It is marked deleted. The laptop keeps it for the record, but it never goes into a report.",
            confirmLabel = if (state.busy) "Deleting…" else "Delete duplicate",
            onConfirm = { vm.deleteDuplicate(dialog.invoiceId) },
            onDismiss = vm::closeDialog,
            destructive = true,
        )
        BatchDialog.ConfirmSubmit -> ConfirmDialog(
            title = "Submit this batch?",
            text = "Good and Corrections invoices go into the input report as they stand. Retake and unresolved " +
                "invoices are listed at the end; deleted duplicates are left out.",
            confirmLabel = if (state.busy) "Submitting…" else "Submit",
            onConfirm = vm::submit,
            onDismiss = vm::closeDialog,
        )
        is BatchDialog.Submitted -> ConfirmDialog(
            title = "Report ready on the laptop",
            text = "The batch is submitted. Its input report (a PDF to print, and a CSV) is ready on the laptop" +
                (dialog.pdf?.let { ":\n$it" } ?: ". Open this batch on the portal to print it."),
            confirmLabel = "OK",
            onConfirm = vm::closeDialog,
            onDismiss = vm::closeDialog,
            dismissLabel = null,
        )
    }
}

/** The line editor for [dialog], or closes it when its invoice or line has gone. */
@Composable
private fun EditLineDialog(dialog: BatchDialog.EditLine, state: BatchUiState, vm: BatchViewModel) {
    val invoice = state.invoice(dialog.invoiceId)
    val line = invoice?.let { inv -> dialog.index?.let { i -> inv.lines.firstOrNull { it.index == i } } }
    if (invoice == null || (dialog.index != null && line == null)) {
        // The invoice or line is gone after a refresh; nothing to edit.
        if (state.detail != null) LaunchedEffect(dialog) { vm.closeDialog() }
    } else {
        LineEditorDialog(
            invoice = invoice,
            line = line,
            target = dialog,
            search = state.search,
            busy = state.busy,
            error = state.actionError,
            onQuery = vm::onSearchQuery,
            onSearchNow = vm::searchNow,
            onSave = { edit -> vm.saveLine(invoice.invoiceId, line?.index, edit) },
            onRemove = line?.let { l -> { vm.openDialog(BatchDialog.ConfirmDeleteLine(invoice.invoiceId, l.index)) } },
            onDismiss = vm::closeDialog,
        )
    }
}

/** A full-screen dialog with a title bar and a Close action. */
@Composable
private fun FullScreenDialog(
    title: String,
    subtitle: String?,
    onDismiss: () -> Unit,
    content: @Composable androidx.compose.foundation.layout.ColumnScope.() -> Unit,
) {
    Dialog(onDismissRequest = onDismiss, properties = DialogProperties(usePlatformDefaultWidth = false)) {
        Surface(Modifier.fillMaxSize(), color = Industry.Ground, shape = RectangleShape) {
            Column(Modifier.fillMaxSize()) {
                Row(Modifier.fillMaxWidth().heightIn(min = 56.dp).padding(start = 14.dp), verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f)) {
                        Text(title, style = MaterialTheme.typography.titleMedium, maxLines = 1, overflow = TextOverflow.Ellipsis)
                        if (subtitle != null) {
                            Text(subtitle, style = MaterialTheme.typography.bodySmall, color = Industry.Muted, maxLines = 1, overflow = TextOverflow.Ellipsis)
                        }
                    }
                    TextAction("Close", onDismiss)
                }
                HorizontalDivider(color = Industry.Divider)
                content()
            }
        }
    }
}

/**
 * Change one line, or add one: a stock item from the key (quantity as printed) or free text when
 * the item is not in the key (quantity in the base unit), and the line cost (contract §5).
 */
@Composable
private fun LineEditorDialog(
    invoice: InvoiceDto,
    line: LineDto?,
    target: BatchDialog.EditLine,
    search: StockSearchUi,
    busy: Boolean,
    error: String?,
    onQuery: (String) -> Unit,
    onSearchNow: () -> Unit,
    onSave: (LineEdit) -> Unit,
    onRemove: (() -> Unit)?,
    onDismiss: () -> Unit,
) {
    val formKey = "${invoice.invoiceId}/${target.index}/${target.itemCode}"
    val start = remember(formKey) { initialLineForm(line, target) }
    var itemCode by rememberSaveable(formKey) { mutableStateOf(start.itemCode) }
    var itemName by rememberSaveable(formKey) { mutableStateOf(start.itemName) }
    var freeMode by rememberSaveable(formKey) { mutableStateOf(start.itemCode == null && line?.byManager == true && start.freeText.isNotBlank()) }
    var freeText by rememberSaveable(formKey) { mutableStateOf(start.freeText) }
    var quantity by rememberSaveable(formKey) { mutableStateOf(start.quantity) }
    var unit by rememberSaveable(formKey) { mutableStateOf(start.unit) }
    var cost by rememberSaveable(formKey) { mutableStateOf(start.cost) }
    var problem by rememberSaveable(formKey) { mutableStateOf<String?>(null) }

    // Start the search with the words printed on the invoice.
    val printedWords = line?.printed?.description
    LaunchedEffect(formKey) {
        if (itemCode == null && search.query.isEmpty() && !printedWords.isNullOrBlank()) onQuery(printedWords)
    }

    val title = if (line == null) "Add a line" else "Line ${line.index + 1}"
    FullScreenDialog(title = title, subtitle = invoice.title, onDismiss = onDismiss) {
        Column(
            Modifier.fillMaxWidth().weight(1f).verticalScroll(rememberScrollState()).padding(12.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            if (line != null) PrintedBlock(line)

            Kicker(if (freeMode) "Not in the key" else "Stock item")
            if (!freeMode) {
                StockItemChoice(
                    itemCode = itemCode,
                    itemName = itemName,
                    search = search,
                    onQuery = onQuery,
                    onSearchNow = onSearchNow,
                    candidates = line?.candidates.orEmpty(),
                    onPick = { pickedCode, pickedName ->
                        itemCode = pickedCode
                        itemName = pickedName
                        problem = null
                    },
                    onFreeText = { freeMode = true },
                )
            } else {
                FreeTextChoice(
                    freeText = freeText,
                    onFreeTextChange = { freeText = it },
                    onChooseStockItem = { freeMode = false },
                )
            }

            LineAmountFields(
                freeMode = freeMode,
                quantity = quantity,
                onQuantityChange = { quantity = it },
                unit = unit,
                onUnitChange = { unit = it },
                cost = cost,
                onCostChange = { cost = it },
            )
            (problem ?: error)?.let { Notice("Not saved", it, edge = Industry.Red) }
            if (onRemove != null) {
                SecondaryButton("Remove this line", onRemove, Modifier.fillMaxWidth(), enabled = !busy, color = Industry.Red)
            }
        }
        HorizontalDivider(color = Industry.Divider)
        LineEditorActions(
            busy = busy,
            onCancel = onDismiss,
            onSave = {
                val choice = if (freeMode) LineChoice.FreeText(freeText) else itemCode?.let { LineChoice.StockItem(it, itemName) }
                when (val check = LineEdits.check(choice, quantity, unit, cost)) {
                    is LineEditCheck.Valid -> {
                        problem = null
                        onSave(check.edit)
                    }
                    is LineEditCheck.Invalid -> problem = check.message
                }
            },
        )
    }
}

/** The form's starting values: the line as it stands, or blank, with any stock item already picked. */
private fun initialLineForm(line: LineDto?, target: BatchDialog.EditLine): LineForm {
    val printed = line?.printed
    val base = if (line == null) {
        LineForm.EMPTY
    } else {
        LineForm.initial(
            itemCode = line.item?.code,
            itemName = line.item?.name,
            printedDescription = printed?.description,
            printedQuantity = printed?.quantity,
            printedUnit = printed?.unit,
            unitPrice = printed?.unitPrice,
            lineTotal = printed?.lineTotal,
            enteredFreeText = line.freeText,
            enteredQuantity = line.quantity,
            enteredUnit = line.unit,
            enteredCost = line.cost,
        )
    }
    return if (target.itemCode != null) base.copy(itemCode = target.itemCode, itemName = target.itemName) else base
}

/** The chosen stock item, the search to change it, and the way out to free text. */
@Composable
private fun StockItemChoice(
    itemCode: String?,
    itemName: String?,
    search: StockSearchUi,
    onQuery: (String) -> Unit,
    onSearchNow: () -> Unit,
    candidates: List<CandidateDto>,
    onPick: (code: String, name: String?) -> Unit,
    onFreeText: () -> Unit,
) {
    Text(
        if (itemCode != null) "${itemName ?: itemCode} ($itemCode)" else "No stock item chosen yet",
        style = MaterialTheme.typography.titleMedium,
        color = if (itemCode == null) Industry.Red else Industry.Ink,
    )
    StockSearchSection(
        search = search,
        onQuery = onQuery,
        onSearchNow = onSearchNow,
        candidates = candidates,
        onPick = onPick,
    )
    TextAction("Not in the key? Type it as free text", onClick = onFreeText)
}

/** The item typed as free text, for an item that is not in the key. */
@Composable
private fun FreeTextChoice(freeText: String, onFreeTextChange: (String) -> Unit, onChooseStockItem: () -> Unit) {
    OutlinedTextField(
        value = freeText,
        onValueChange = onFreeTextChange,
        label = { Text("The item as it should appear in the report") },
        modifier = Modifier.fillMaxWidth(),
        textStyle = MaterialTheme.typography.bodyLarge,
    )
    Text(
        "Free-text lines print under \"not in key\". The quantity is in Pilot's base unit.",
        style = MaterialTheme.typography.bodySmall,
        color = Industry.Muted,
    )
    TextAction("Choose a stock item instead", onClick = onChooseStockItem)
}

/** Quantity, unit (stock items only) and line cost. */
@Composable
private fun LineAmountFields(
    freeMode: Boolean,
    quantity: String,
    onQuantityChange: (String) -> Unit,
    unit: String,
    onUnitChange: (String) -> Unit,
    cost: String,
    onCostChange: (String) -> Unit,
) {
    OutlinedTextField(
        value = quantity,
        onValueChange = onQuantityChange,
        label = { Text(if (freeMode) "Quantity in the base unit" else "Quantity as printed on the invoice") },
        singleLine = true,
        modifier = Modifier.fillMaxWidth(),
        textStyle = MaterialTheme.typography.bodyLarge,
        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal, imeAction = ImeAction.Next),
    )
    if (!freeMode) {
        OutlinedTextField(
            value = unit,
            onValueChange = onUnitChange,
            label = { Text("Unit as printed (optional)") },
            singleLine = true,
            modifier = Modifier.fillMaxWidth(),
            textStyle = MaterialTheme.typography.bodyLarge,
        )
    }
    OutlinedTextField(
        value = cost,
        onValueChange = onCostChange,
        label = { Text("Line cost excl. VAT, in rands") },
        singleLine = true,
        modifier = Modifier.fillMaxWidth(),
        textStyle = MaterialTheme.typography.bodyLarge,
        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal, imeAction = ImeAction.Done),
    )
}

@Composable
private fun LineEditorActions(busy: Boolean, onCancel: () -> Unit, onSave: () -> Unit) {
    Row(Modifier.fillMaxWidth().padding(12.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        SecondaryButton("Cancel", onCancel, Modifier.weight(1f))
        PrimaryButton(
            text = if (busy) "Saving…" else "Save",
            onClick = onSave,
            modifier = Modifier.weight(1f),
            enabled = !busy,
        )
    }
}

@Composable
private fun PrintedBlock(line: LineDto) {
    val printed = line.printed
    Plate(Modifier.fillMaxWidth(), background = if (line.star) Industry.StarWash else Color.Transparent) {
        Kicker(if (line.star) "★ Printed on the invoice · needs you" else "Printed on the invoice")
        Text(printed?.description ?: "(nothing printed was read)", style = MaterialTheme.typography.bodyLarge)
        Text(
            listOfNotNull(
                "qty ${Money.formatQuantity(printed?.quantity)}",
                printed?.unit?.let { "unit $it" },
                printed?.pack?.let { "pack $it" },
                "price ${Money.format(printed?.unitPrice)}",
                "total ${Money.format(printed?.lineTotal)}",
            ).joinToString(" · "),
            style = MaterialTheme.typography.bodySmall,
        )
        line.reason?.let { Text("Laptop: $it", style = MaterialTheme.typography.bodySmall, color = Industry.Muted) }
        line.refusalText?.let { Text("Cannot convert: $it", style = MaterialTheme.typography.bodySmall, color = Industry.Red) }
        line.flags.forEach { flag ->
            Text("⚑ ${flag.reason ?: flag.kind ?: "flagged"}", style = MaterialTheme.typography.bodySmall, color = Industry.Red)
        }
    }
}

/** Search box, the laptop's suggestions and the results; tapping a result picks it. */
@Composable
private fun StockSearchSection(
    search: StockSearchUi,
    onQuery: (String) -> Unit,
    onSearchNow: () -> Unit,
    candidates: List<CandidateDto>,
    onPick: (code: String, name: String?) -> Unit,
) {
    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            OutlinedTextField(
                value = search.query,
                onValueChange = onQuery,
                label = { Text("Search this shop's stock key") },
                singleLine = true,
                modifier = Modifier.weight(1f),
                textStyle = MaterialTheme.typography.bodyLarge,
                keyboardOptions = KeyboardOptions(imeAction = ImeAction.Search),
                keyboardActions = KeyboardActions(onSearch = { onSearchNow() }),
            )
            SecondaryButton("Search", onSearchNow)
        }
        if (search.searching) Text("Searching…", style = MaterialTheme.typography.bodySmall, color = Industry.Muted)
        search.message?.let { Text(it, style = MaterialTheme.typography.bodySmall, color = Industry.Red) }
        if (search.items.isEmpty() && candidates.isNotEmpty()) {
            Kicker("Suggested by the laptop")
            candidates.forEach { c ->
                ResultRow(
                    title = c.name ?: c.code,
                    detail = "code ${c.code}" + (c.score?.let { " · score ${Decimals.format(it, 0)}" } ?: ""),
                    onClick = { onPick(c.code, c.name) },
                )
            }
        }
        search.items.forEach { item ->
            ResultRow(
                title = item.name ?: item.code,
                detail = listOfNotNull("code ${item.code}", item.baseUnit?.let { "unit $it" }, item.category).joinToString(" · "),
                onClick = { onPick(item.code, item.name) },
            )
        }
    }
}

@Composable
private fun ResultRow(title: String, detail: String, onClick: () -> Unit) {
    Column(
        Modifier
            .fillMaxWidth()
            .heightIn(min = 52.dp)
            .background(Industry.Ground)
            .plate(marks = false, border = Industry.Rule)
            .clickable(onClick = onClick)
            .padding(horizontal = 10.dp, vertical = 6.dp),
        verticalArrangement = Arrangement.Center,
    ) {
        Text(title, style = MaterialTheme.typography.bodyMedium, maxLines = 2, overflow = TextOverflow.Ellipsis)
        Text(detail, style = MaterialTheme.typography.bodySmall, color = Industry.Muted, maxLines = 1, overflow = TextOverflow.Ellipsis)
    }
}

/** The panel's search: find any stock item, then choose the line it belongs to (so unflagged lines can be fixed too). */
@Composable
private fun StockSearchDialog(
    invoice: InvoiceDto,
    search: StockSearchUi,
    onQuery: (String) -> Unit,
    onSearchNow: () -> Unit,
    onPick: (code: String, name: String?) -> Unit,
    onDismiss: () -> Unit,
) {
    FullScreenDialog(title = "Search stock", subtitle = invoice.title, onDismiss = onDismiss) {
        Column(
            Modifier.fillMaxWidth().verticalScroll(rememberScrollState()).padding(12.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Text(
                "Find an item in this shop's key, then choose which line it is for, or add it as a new line.",
                style = MaterialTheme.typography.bodySmall,
            )
            StockSearchSection(search, onQuery, onSearchNow, candidates = emptyList(), onPick = onPick)
        }
    }
}

@Composable
private fun LinePickerDialog(invoice: InvoiceDto, itemLabel: String, onLine: (Int?) -> Unit, onDismiss: () -> Unit) {
    val lines = remember(invoice.lines) { TabOrdering.starredFirst(invoice.lines, { it.star }, { it.index }) }
    FullScreenDialog(title = "Use \"$itemLabel\" for…", subtitle = invoice.title, onDismiss = onDismiss) {
        LazyColumn(
            Modifier.fillMaxSize(),
            contentPadding = PaddingValues(12.dp),
            verticalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            item(key = "new") { SecondaryButton("Add it as a new line", { onLine(null) }, Modifier.fillMaxWidth()) }
            items(lines, key = { it.index }) { line ->
                ResultRow(
                    title = (if (line.star) "★ " else "") + (line.printed?.description ?: "Line ${line.index + 1}"),
                    detail = line.item?.let { "now: ${it.name ?: it.code}" } ?: "not matched to a stock item",
                    onClick = { onLine(line.index) },
                )
            }
        }
    }
}

/** Supplier, invoice number and date (`PATCH /invoices/{id}`). */
@Composable
private fun HeaderEditorDialog(
    invoice: InvoiceDto,
    busy: Boolean,
    error: String?,
    onSave: (HeaderCheck.Valid) -> Unit,
    onDismiss: () -> Unit,
) {
    var supplier by rememberSaveable(invoice.invoiceId) { mutableStateOf(invoice.supplier ?: invoice.supplierName.orEmpty()) }
    var number by rememberSaveable(invoice.invoiceId) { mutableStateOf(invoice.documentNo.orEmpty()) }
    var date by rememberSaveable(invoice.invoiceId) { mutableStateOf(HeaderEdits.displayDate(invoice.date)) }
    var problem by rememberSaveable(invoice.invoiceId) { mutableStateOf<String?>(null) }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Invoice details", style = MaterialTheme.typography.titleLarge) },
        text = {
            Column(Modifier.verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                // The laptop's key is typed here; the Pilot name beside it says which supplier that is.
                OutlinedTextField(
                    value = supplier,
                    onValueChange = { supplier = it },
                    label = { Text("Supplier (as the laptop knows it)") },
                    supportingText = invoice.supplierName?.takeIf { it.isNotBlank() }?.let { name ->
                        { Text("The laptop calls this supplier \"$name\".") }
                    },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                )
                OutlinedTextField(
                    value = number,
                    onValueChange = { number = it },
                    label = { Text("Invoice number") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                )
                OutlinedTextField(
                    value = date,
                    onValueChange = { date = it },
                    label = { Text("Date, e.g. 31/08/2026") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                )
                (problem ?: error)?.let { Text(it, style = MaterialTheme.typography.bodySmall, color = Industry.Red) }
            }
        },
        confirmButton = {
            TextAction(
                if (busy) "Saving…" else "Save",
                onClick = {
                    when (val check = HeaderEdits.check(supplier, number, date)) {
                        is HeaderCheck.Valid -> {
                            problem = null
                            onSave(check)
                        }
                        is HeaderCheck.Invalid -> problem = check.message
                    }
                },
                enabled = !busy,
            )
        },
        dismissButton = { TextAction("Cancel", onDismiss) },
        shape = RectangleShape,
        containerColor = Industry.Ground,
    )
}
