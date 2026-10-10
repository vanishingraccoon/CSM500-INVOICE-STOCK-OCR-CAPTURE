package com.invoicecapture.ui.settings

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.consumeWindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.lifecycle.SavedStateHandle
import androidx.lifecycle.ViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch
import com.invoicecapture.data.AppContainer
import com.invoicecapture.data.api.ApiResult
import com.invoicecapture.data.api.plainMessage
import com.invoicecapture.data.pairing.Pairing
import com.invoicecapture.domain.batch.Times
import com.invoicecapture.domain.pairing.PairingCodes
import com.invoicecapture.ui.components.AppBar
import com.invoicecapture.ui.components.ConfirmDialog
import com.invoicecapture.ui.components.Kicker
import com.invoicecapture.ui.components.Notice
import com.invoicecapture.ui.components.Plate
import com.invoicecapture.ui.components.SecondaryButton
import com.invoicecapture.ui.components.containerViewModel
import com.invoicecapture.ui.theme.Industry

class SettingsViewModel(private val container: AppContainer, private val saved: SavedStateHandle) : ViewModel() {

    val pairing: StateFlow<Pairing?> = container.pairingStore.pairing
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), null)

    val photosOnPhone: StateFlow<Int> = container.pendingStore.sessions
        .map { sessions -> sessions.sumOf { it.pagesOnPhone } }
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), 0)

    val server: StateFlow<String> = saved.getStateFlow(KEY_SERVER, "")
    val deviceName: StateFlow<String> = saved.getStateFlow(KEY_NAME, "")

    private val _message = MutableStateFlow<String?>(null)
    val message: StateFlow<String?> = _message.asStateFlow()

    private val _error = MutableStateFlow<String?>(null)
    val error: StateFlow<String?> = _error.asStateFlow()

    private val _unpaired = MutableStateFlow(false)
    val unpaired: StateFlow<Boolean> = _unpaired.asStateFlow()

    init {
        viewModelScope.launch {
            val current = container.pairingStore.current()
            if (!saved.contains(KEY_SERVER)) saved[KEY_SERVER] = current?.server ?: ""
            if (!saved.contains(KEY_NAME)) saved[KEY_NAME] = current?.deviceName ?: ""
        }
    }

    fun onServer(value: String) {
        saved[KEY_SERVER] = value.trim()
    }

    fun onDeviceName(value: String) {
        saved[KEY_NAME] = value.take(60)
    }

    fun saveServer() {
        val url = PairingCodes.normaliseServer(server.value)
        if (url == null) {
            show(error = "Type the laptop address, for example 192.168.1.20:5000.")
            return
        }
        viewModelScope.launch {
            container.pairingStore.updateServer(url)
            saved[KEY_SERVER] = url
            show(message = "Saved. The phone now uses $url.")
            container.scheduler.resumeAll(container.pendingStore, includeStopped = false)
        }
    }

    fun saveDeviceName() {
        val name = deviceName.value.trim()
        if (name.isEmpty()) {
            show(error = "Give this phone a name, for example Galaxy A06 (store 1).")
            return
        }
        viewModelScope.launch {
            container.pairingStore.setDeviceName(name)
            show(message = "Saved. The laptop keeps the name given at pairing; this name is used when you pair again.")
        }
    }

    fun checkConnection() {
        viewModelScope.launch {
            show(message = "Asking the laptop…")
            when (val result = container.invoices.ping()) {
                is ApiResult.Ok -> {
                    val queue = result.value.queue
                    show(
                        message = "The laptop answered (${Times.short(result.value.serverTime)}). " +
                            "Its reading queue for all shops: ${queue?.waiting ?: 0} photos waiting, ${queue?.reading ?: 0} being read.",
                    )
                }
                else -> show(error = result.plainMessage())
            }
        }
    }

    fun unpair() {
        viewModelScope.launch {
            container.pairingStore.unpair()
            _unpaired.value = true
        }
    }

    private fun show(message: String? = null, error: String? = null) {
        _message.value = message
        _error.value = error
    }

    private companion object {
        const val KEY_SERVER = "server"
        const val KEY_NAME = "device_name"
    }
}

@Composable
fun SettingsScreen(onBack: () -> Unit, onUnpaired: () -> Unit) {
    val vm = containerViewModel { container, saved -> SettingsViewModel(container, saved) }
    val pairing by vm.pairing.collectAsStateWithLifecycle()
    val photosOnPhone by vm.photosOnPhone.collectAsStateWithLifecycle()
    val server by vm.server.collectAsStateWithLifecycle()
    val deviceName by vm.deviceName.collectAsStateWithLifecycle()
    val message by vm.message.collectAsStateWithLifecycle()
    val error by vm.error.collectAsStateWithLifecycle()
    val unpaired by vm.unpaired.collectAsStateWithLifecycle()
    LaunchedEffect(unpaired) { if (unpaired) onUnpaired() }

    var confirmUnpair by rememberSaveable { mutableStateOf(false) }
    val context = LocalContext.current
    val version = try {
        context.packageManager.getPackageInfo(context.packageName, 0).versionName
    } catch (e: Exception) {
        null
    }

    Scaffold(topBar = { AppBar(title = "Settings", subtitle = pairing?.shopLabel, onBack = onBack) }, containerColor = Industry.Ground) { padding ->
        Column(
            Modifier
                .fillMaxSize()
                .padding(padding)
                .consumeWindowInsets(padding)
                .imePadding()
                .verticalScroll(rememberScrollState())
                .padding(12.dp)
                .navigationBarsPadding(),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            error?.let { Notice("Settings", it, edge = Industry.Red) }
            message?.let { Notice("Settings", it, edge = Industry.Accent) }

            LaptopPlate(
                server = server,
                onServerChange = vm::onServer,
                onSave = vm::saveServer,
                onCheckConnection = vm::checkConnection,
                modifier = Modifier.fillMaxWidth(),
            )
            ThisPhonePlate(
                deviceName = deviceName,
                onDeviceNameChange = vm::onDeviceName,
                onSave = vm::saveDeviceName,
                pairing = pairing,
                modifier = Modifier.fillMaxWidth(),
            )
            UnpairPlate(onUnpair = { confirmUnpair = true }, modifier = Modifier.fillMaxWidth())

            Text(
                "Connection: plain HTTP to the store laptop over the office Wi-Fi, for this project only. " +
                    "The cloud release will use HTTPS and logins." + (version?.let { "  ·  Version $it" } ?: ""),
                style = MaterialTheme.typography.bodySmall,
                color = Industry.Muted,
            )
        }
    }

    if (confirmUnpair) {
        ConfirmDialog(
            title = "Unpair this phone?",
            text = if (photosOnPhone > 0) {
                "$photosOnPhone photos are still waiting to upload. They stay on this phone and upload after you pair again."
            } else {
                "You will need a new code from the laptop to use this phone again."
            },
            confirmLabel = "Unpair",
            destructive = true,
            onConfirm = {
                confirmUnpair = false
                vm.unpair()
            },
            onDismiss = { confirmUnpair = false },
        )
    }
}

/** The laptop's address, with Save and a connection check. */
@Composable
private fun LaptopPlate(
    server: String,
    onServerChange: (String) -> Unit,
    onSave: () -> Unit,
    onCheckConnection: () -> Unit,
    modifier: Modifier = Modifier,
) {
    Plate(modifier) {
        Kicker("Laptop")
        OutlinedTextField(
            value = server,
            onValueChange = onServerChange,
            label = { Text("Laptop address") },
            singleLine = true,
            modifier = Modifier.fillMaxWidth(),
            textStyle = MaterialTheme.typography.bodyLarge,
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri, imeAction = ImeAction.Done),
        )
        Text(
            "Change this only if the laptop's address changed. The phone stays paired.",
            style = MaterialTheme.typography.bodySmall,
            color = Industry.Muted,
        )
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            SecondaryButton("Save address", onSave, Modifier.weight(1f))
            SecondaryButton("Check connection", onCheckConnection, Modifier.weight(1f))
        }
    }
}

/** This phone's name, and the shop and device id it is paired as. */
@Composable
private fun ThisPhonePlate(
    deviceName: String,
    onDeviceNameChange: (String) -> Unit,
    onSave: () -> Unit,
    pairing: Pairing?,
    modifier: Modifier = Modifier,
) {
    Plate(modifier) {
        Kicker("This phone")
        OutlinedTextField(
            value = deviceName,
            onValueChange = onDeviceNameChange,
            label = { Text("Device name") },
            singleLine = true,
            modifier = Modifier.fillMaxWidth(),
            textStyle = MaterialTheme.typography.bodyLarge,
        )
        SecondaryButton("Save name", onSave, Modifier.fillMaxWidth())
        pairing?.let {
            Text("Shop: ${it.shopLabel}", style = MaterialTheme.typography.bodyMedium)
            it.deviceId?.let { id -> Text("Device id on the laptop: $id", style = MaterialTheme.typography.bodySmall, color = Industry.Muted) }
        }
    }
}

@Composable
private fun UnpairPlate(onUnpair: () -> Unit, modifier: Modifier = Modifier) {
    Plate(modifier) {
        Kicker("Unpair")
        Text(
            "Removes the device token from this phone. To stop the old token working, revoke the phone on the laptop's Devices page too.",
            style = MaterialTheme.typography.bodySmall,
        )
        SecondaryButton("Unpair this phone", onUnpair, Modifier.fillMaxWidth(), color = Industry.Red)
    }
}
