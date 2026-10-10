package com.invoicecapture.ui.components

import android.app.Activity
import android.net.Uri
import androidx.activity.compose.LocalActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.IntentSenderRequest
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.ui.platform.LocalContext
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

object ScannerLimits {
    /** One scanner session is one batch; a delivery rarely has more pages than this. */
    const val MAX_PAGES_PER_BATCH = 100

    /** A retake replaces exactly one page (contract §5). */
    const val RETAKE_PAGES = 1
}

/**
 * Google ML Kit Document Scanner in basic mode: crop and rotate only, no image cleaning or
 * filters (the photos are kept as taken), JPEG pages, no gallery import. Returns a launcher.
 */
@Composable
fun rememberDocumentScanner(
    pageLimit: Int,
    onPages: (List<Uri>) -> Unit,
    onError: (String) -> Unit,
): () -> Unit {
    val activity = LocalActivity.current
    val currentOnPages by rememberUpdatedState(onPages)
    val currentOnError by rememberUpdatedState(onError)
    val launcher = rememberLauncherForActivityResult(ActivityResultContracts.StartIntentSenderForResult()) { result ->
        if (result.resultCode == Activity.RESULT_OK) {
            val pages = GmsDocumentScanningResult.fromActivityResultIntent(result.data)?.pages.orEmpty().map { it.imageUri }
            if (pages.isNotEmpty()) currentOnPages(pages)
        }
    }
    return remember(activity, pageLimit, launcher) {
        {
            if (activity == null) {
                currentOnError("The scanner can only open while the app is on screen.")
            } else {
                val options = GmsDocumentScannerOptions.Builder()
                    .setScannerMode(GmsDocumentScannerOptions.SCANNER_MODE_BASE)
                    .setResultFormats(GmsDocumentScannerOptions.RESULT_FORMAT_JPEG)
                    .setGalleryImportAllowed(false)
                    .setPageLimit(pageLimit)
                    .build()
                GmsDocumentScanning.getClient(options)
                    .getStartScanIntent(activity)
                    .addOnSuccessListener { sender -> launcher.launch(IntentSenderRequest.Builder(sender).build()) }
                    .addOnFailureListener { e ->
                        currentOnError(
                            "The scanner could not start (${e.message ?: "unknown error"}). Google Play services " +
                                "may still be downloading it; try again in a minute.",
                        )
                    }
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
