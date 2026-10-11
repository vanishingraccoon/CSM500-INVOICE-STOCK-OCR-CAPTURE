package com.invoicecapture.ui.components

import android.app.Activity
import android.net.Uri
import androidx.compose.foundation.layout.Column
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.activity.compose.LocalActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.IntentSenderRequest
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.platform.LocalContext
import androidx.core.content.FileProvider
import androidx.lifecycle.SavedStateHandle
import androidx.lifecycle.ViewModel
import androidx.lifecycle.createSavedStateHandle
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.lifecycle.viewmodel.initializer
import androidx.lifecycle.viewmodel.viewModelFactory
import com.google.mlkit.vision.barcode.common.Barcode
import com.google.mlkit.vision.codescanner.GmsBarcodeScannerOptions
import com.google.mlkit.vision.codescanner.GmsBarcodeScanning
import com.google.mlkit.vision.documentscanner.GmsDocumentScannerOptions
import com.google.mlkit.vision.documentscanner.GmsDocumentScanning
import com.google.mlkit.vision.documentscanner.GmsDocumentScanningResult
import com.invoicecapture.InvoiceCaptureApp
import com.invoicecapture.data.AppContainer
import java.io.File

object ScannerLimits {
    /** One scanner session is one batch; a delivery rarely has more pages than this. */
    const val MAX_PAGES_PER_BATCH = 100

    /** A retake replaces exactly one page (contract §5). */
    const val RETAKE_PAGES = 1
}

/**
 * Uses the basic document scanner, falling back to full-size camera photos when it cannot open.
 * Camera pages keep their order in one batch; a retake captures exactly one page.
 */
@Composable
fun rememberDocumentScanner(
    pageLimit: Int,
    onPages: (List<Uri>) -> Unit,
    onError: (String) -> Unit,
): () -> Unit {
    val activity = LocalActivity.current
    val context = LocalContext.current
    val currentOnPages by rememberUpdatedState(onPages)
    val currentOnError by rememberUpdatedState(onError)
    var cameraMode by rememberSaveable { mutableStateOf(false) }
    var cameraDialog by rememberSaveable { mutableStateOf(false) }
    var pendingPhoto by rememberSaveable { mutableStateOf<String?>(null) }
    var cameraPages by rememberSaveable { mutableStateOf(arrayListOf<String>()) }
    var requesting by remember { mutableStateOf(false) }
    val finishCamera = {
        val pages = cameraPages.map { Uri.fromFile(File(it)) }
        cameraPages = arrayListOf()
        cameraDialog = false
        if (pages.isNotEmpty()) currentOnPages(pages)
    }
    val cancelCamera = {
        cameraPages.forEach { File(it).delete() }
        cameraPages = arrayListOf()
        cameraDialog = false
    }
    val camera = rememberLauncherForActivityResult(ActivityResultContracts.TakePicture()) { saved ->
        val file = pendingPhoto?.let(::File)
        pendingPhoto = null
        if (saved && file != null && file.length() > 0) {
            cameraPages = ArrayList(cameraPages + file.absolutePath)
            if (cameraPages.size >= pageLimit) finishCamera() else cameraDialog = true
        } else {
            file?.delete()
            cameraDialog = true
            if (saved) currentOnError("The camera did not save a photo. Please photograph this page again.")
        }
    }
    val takePhoto = {
        var file: File? = null
        try {
            val directory = File(context.cacheDir, "camera-pages")
            if (!directory.isDirectory && !directory.mkdirs()) error("Photo storage could not be created")
            file = File.createTempFile("invoice-", ".jpg", directory)
            val uri = FileProvider.getUriForFile(context, context.packageName + ".capture-files", file)
            pendingPhoto = file.absolutePath
            cameraDialog = false
            camera.launch(uri)
        } catch (_: Exception) {
            file?.delete()
            pendingPhoto = null
            cameraDialog = true
            currentOnError("The phone camera could not open. Check that the Camera app works, then try again.")
        }
    }
    val launcher = rememberLauncherForActivityResult(ActivityResultContracts.StartIntentSenderForResult()) { result ->
        requesting = false
        if (result.resultCode == Activity.RESULT_OK) {
            val pages = GmsDocumentScanningResult.fromActivityResultIntent(result.data)?.pages.orEmpty().map { it.imageUri }
            if (pages.isNotEmpty()) currentOnPages(pages)
        }
    }
    if (cameraDialog) {
        AlertDialog(
            onDismissRequest = cancelCamera,
            title = { Text(if (cameraPages.isEmpty()) "Use phone camera" else "${cameraPages.size} page(s) photographed") },
            text = {
                Text("Photograph one page at a time with the whole invoice in view. Keep the phone steady and avoid shadows. " +
                    "This camera mode does not crop the page automatically. Add all pages, then tap Done.")
            },
            confirmButton = {
                Column {
                    if (cameraPages.size < pageLimit) {
                        TextButton(onClick = takePhoto) { Text(if (cameraPages.isEmpty()) "Take photo" else "Add page") }
                    }
                    if (cameraPages.isNotEmpty()) {
                        TextButton(onClick = finishCamera) { Text("Done") }
                    }
                }
            },
            dismissButton = { TextButton(onClick = cancelCamera) { Text("Cancel") } },
        )
    }
    return remember(activity, pageLimit, launcher, cameraMode) {
        {
            if (activity == null) {
                currentOnError("The scanner can only open while the app is on screen.")
            } else if (requesting || pendingPhoto != null) {
                // Ignore repeated taps while an external capture screen is opening.
            } else if (cameraMode) {
                cameraDialog = true
            } else {
                requesting = true
                val options = GmsDocumentScannerOptions.Builder()
                    .setScannerMode(GmsDocumentScannerOptions.SCANNER_MODE_BASE)
                    .setResultFormats(GmsDocumentScannerOptions.RESULT_FORMAT_JPEG)
                    .setGalleryImportAllowed(false)
                    .setPageLimit(pageLimit)
                    .build()
                launchDocumentCapture(
                    request = { ready, unavailable ->
                        GmsDocumentScanning.getClient(options).getStartScanIntent(activity)
                            .addOnSuccessListener { ready(it) }
                            .addOnFailureListener { unavailable(it) }
                    },
                    launch = { sender -> launcher.launch(IntentSenderRequest.Builder(sender).build()) },
                    openCamera = {
                        requesting = false
                        cameraMode = true
                        cameraDialog = true
                    },
                )
            }
        }
    }
}

/** Google Code Scanner for the pairing QR code (no camera permission needed). Returns a launcher. */
@Composable
fun rememberQrScanner(onCode: (String) -> Unit, onError: (String) -> Unit): () -> Unit {
    val context = LocalContext.current
    val currentOnCode by rememberUpdatedState(onCode)
    val currentOnError by rememberUpdatedState(onError)
    return remember(context) {
        {
            val options = GmsBarcodeScannerOptions.Builder()
                .setBarcodeFormats(Barcode.FORMAT_QR_CODE)
                .enableAutoZoom()
                .build()
            GmsBarcodeScanning.getClient(context, options)
                .startScan()
                .addOnSuccessListener { barcode ->
                    val raw = barcode.rawValue
                    if (raw != null) currentOnCode(raw) else currentOnError("The code could not be read. Try again, or type it.")
                }
                .addOnFailureListener { e ->
                    currentOnError(
                        "The code scanner could not start (${e.message ?: "unknown error"}). Try again in a minute, " +
                            "or type the laptop address and code below.",
                    )
                }
        }
    }
}

/** A ViewModel built from the app's [AppContainer], with its SavedStateHandle (route arguments included). */
@Composable
inline fun <reified VM : ViewModel> containerViewModel(
    crossinline create: (AppContainer, SavedStateHandle) -> VM,
): VM {
    val container = (LocalContext.current.applicationContext as InvoiceCaptureApp).container
    return viewModel(factory = viewModelFactory { initializer { create(container, createSavedStateHandle()) } })
}
