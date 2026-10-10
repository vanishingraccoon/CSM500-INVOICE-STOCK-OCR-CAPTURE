package com.invoicecapture.ui.pairing

import android.content.Context
import android.os.Build
import android.provider.Settings
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.consumeWindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.lifecycle.SavedStateHandle
import androidx.lifecycle.ViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import com.invoicecapture.data.AppContainer
import com.invoicecapture.data.api.ApiResult
import com.invoicecapture.data.api.plainMessage
import com.invoicecapture.domain.pairing.PairingCodes
import com.invoicecapture.domain.pairing.QrParse
import com.invoicecapture.ui.components.AppBar
import com.invoicecapture.ui.components.Kicker
import com.invoicecapture.ui.components.Notice
import com.invoicecapture.ui.components.PrimaryButton
import com.invoicecapture.ui.components.SecondaryButton
import com.invoicecapture.ui.components.containerViewModel
import com.invoicecapture.ui.components.rememberQrScanner
import com.invoicecapture.ui.theme.Industry

object DeviceNames {
    /** The phone's own name (e.g. "Galaxy A06"), else manufacturer and model. */
    fun default(context: Context): String {
        val name = try {
            Settings.Global.getString(context.contentResolver, Settings.Global.DEVICE_NAME)
        } catch (e: SecurityException) {
            null
        }
        if (!name.isNullOrBlank()) return name
        val maker = Build.MANUFACTURER.replaceFirstChar { it.uppercase() }
        return if (Build.MODEL.startsWith(maker, ignoreCase = true)) Build.MODEL else "$maker ${Build.MODEL}"
    }
}

class PairingViewModel(private val container: AppContainer, private val saved: SavedStateHandle) : ViewModel() {

    val deviceName: StateFlow<String> = saved.getStateFlow(KEY_NAME, "")
    val server: StateFlow<String> = saved.getStateFlow(KEY_SERVER, "")
    val code: StateFlow<String> = saved.getStateFlow(KEY_CODE, "")

    private val _busy = MutableStateFlow(false)
    val busy: StateFlow<Boolean> = _busy.asStateFlow()

    private val _error = MutableStateFlow<String?>(null)
    val error: StateFlow<String?> = _error.asStateFlow()

    private val _paired = MutableStateFlow(false)
    val paired: StateFlow<Boolean> = _paired.asStateFlow()

    init {
        viewModelScope.launch {
            if (server.value.isBlank()) {
                container.pairingStore.lastServer.first()?.let { saved[KEY_SERVER] = it }
            }
            if (deviceName.value.isBlank()) {
                container.pairingStore.deviceName.first()?.takeIf { it.isNotBlank() }?.let { saved[KEY_NAME] = it }
            }
        }
    }

    fun offerDefaultDeviceName(name: String) {
        if (deviceName.value.isBlank()) saved[KEY_NAME] = name
    }

    fun onDeviceName(value: String) {
        saved[KEY_NAME] = value.take(60)
    }

    fun onServer(value: String) {
        saved[KEY_SERVER] = value.trim()
    }

    fun onCode(value: String) {
        saved[KEY_CODE] = PairingCodes.normaliseCode(value).take(PairingCodes.LENGTH)
    }

    fun onScanError(message: String) {
        _error.value = message
    }

    fun onQrScanned(raw: String) {
        when (val parsed = PairingCodes.parseQr(raw)) {
            is QrParse.Invalid -> _error.value = parsed.reason
            is QrParse.Ok -> {
                saved[KEY_SERVER] = parsed.qr.server
                saved[KEY_CODE] = parsed.qr.pairCode
                pair(parsed.qr.server, parsed.qr.pairCode)
            }
        }
    }

    fun onPairTyped() {
        val serverUrl = PairingCodes.normaliseServer(server.value)
        if (serverUrl == null) {
            _error.value = "Type the laptop address shown on its Pair page, for example 192.168.1.20:5000."
            return
        }
        val pairCode = PairingCodes.normaliseCode(code.value)
        if (!PairingCodes.isValidCode(pairCode)) {
            _error.value = "The code is the 6 letters and numbers shown on the laptop."
            return
        }
        pair(serverUrl, pairCode)
    }

    private fun pair(serverUrl: String, pairCode: String) {
        if (_busy.value) return
        val name = deviceName.value.trim().ifEmpty { "Phone" }
        _busy.value = true
        _error.value = null
        viewModelScope.launch {
            val result = container.invoices.pair(serverUrl, pairCode, name)
            _busy.value = false
            if (result is ApiResult.Ok) {
                // Photos kept from an earlier pairing can go now.
                container.scheduler.resumeAll(container.pendingStore, includeStopped = false)
                _paired.value = true
            } else {
                _error.value = result.plainMessage()
            }
        }
    }

    private companion object {
        const val KEY_NAME = "device_name"
        const val KEY_SERVER = "server"
        const val KEY_CODE = "code"
    }
}

@Composable
fun PairingScreen(onPaired: () -> Unit) {
    val vm = containerViewModel { container, saved -> PairingViewModel(container, saved) }
    val context = LocalContext.current
    val defaultName = remember { DeviceNames.default(context) }
    LaunchedEffect(Unit) { vm.offerDefaultDeviceName(defaultName) }

    val deviceName by vm.deviceName.collectAsStateWithLifecycle()
    val server by vm.server.collectAsStateWithLifecycle()
    val code by vm.code.collectAsStateWithLifecycle()
    val busy by vm.busy.collectAsStateWithLifecycle()
    val error by vm.error.collectAsStateWithLifecycle()
    val paired by vm.paired.collectAsStateWithLifecycle()
    LaunchedEffect(paired) { if (paired) onPaired() }

    val scanQr = rememberQrScanner(onCode = vm::onQrScanned, onError = vm::onScanError)

    Scaffold(
        topBar = { AppBar(title = "Pair with the laptop", subtitle = "Invoice Capture") },
        containerColor = Industry.Ground,
    ) { padding ->
        Column(
            Modifier
                .fillMaxSize()
                .padding(padding)
                .consumeWindowInsets(padding)
                .imePadding()
                .verticalScroll(rememberScrollState())
                .padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(14.dp),
        ) {
            Text(
                "On the laptop, open the portal's Pair page. It shows a QR code and a 6-character code " +
                    "that works once, for 10 minutes.",
                style = MaterialTheme.typography.bodyMedium,
            )
            OutlinedTextField(
                value = deviceName,
                onValueChange = vm::onDeviceName,
                label = { Text("This phone's name on the laptop") },
                singleLine = true,
                modifier = Modifier.fillMaxWidth(),
                textStyle = MaterialTheme.typography.bodyLarge,
            )
            PrimaryButton("Scan the code on the laptop", onClick = scanQr, enabled = !busy)

            if (busy) PairingProgress()
            error?.let { Notice(title = "Not paired", body = it, edge = Industry.Red) }

            TypedPairing(
                server = server,
                onServerChange = vm::onServer,
                code = code,
                onCodeChange = vm::onCode,
                onPair = vm::onPairTyped,
                busy = busy,
            )
            Text(
                "The phone talks only to this laptop, over the office Wi-Fi.",
                style = MaterialTheme.typography.bodySmall,
                color = Industry.Muted,
            )
        }
    }
}

@Composable
private fun PairingProgress(modifier: Modifier = Modifier) {
    Row(modifier, verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
        CircularProgressIndicator(Modifier.size(24.dp), strokeWidth = 3.dp)
        Text("Pairing…", style = MaterialTheme.typography.bodyMedium)
    }
}

/** The way to pair without the camera: the laptop address and code typed in. */
@Composable
private fun TypedPairing(
    server: String,
    onServerChange: (String) -> Unit,
    code: String,
    onCodeChange: (String) -> Unit,
    onPair: () -> Unit,
    busy: Boolean,
) {
    Kicker("Or type it")
    OutlinedTextField(
        value = server,
        onValueChange = onServerChange,
        label = { Text("Laptop address") },
        placeholder = { Text("192.168.1.20:5000") },
        singleLine = true,
        modifier = Modifier.fillMaxWidth(),
        textStyle = MaterialTheme.typography.bodyLarge,
        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri, imeAction = ImeAction.Next),
    )
    OutlinedTextField(
        value = code,
        onValueChange = onCodeChange,
        label = { Text("Code") },
        placeholder = { Text("K7Q2M9") },
        singleLine = true,
        modifier = Modifier.fillMaxWidth(),
        textStyle = MaterialTheme.typography.bodyLarge,
        keyboardOptions = KeyboardOptions(
            capitalization = KeyboardCapitalization.Characters,
            keyboardType = KeyboardType.Ascii,
            autoCorrectEnabled = false,
            imeAction = ImeAction.Done,
        ),
        keyboardActions = KeyboardActions(onDone = { onPair() }),
    )
    SecondaryButton("Pair", onClick = onPair, modifier = Modifier.fillMaxWidth(), enabled = !busy)
}
