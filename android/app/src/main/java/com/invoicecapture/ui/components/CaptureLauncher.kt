package com.invoicecapture.ui.components

/** Starts the document intent or offers the phone camera when it is unavailable. */
internal fun <T> launchDocumentCapture(
    request: ((T) -> Unit, (Exception) -> Unit) -> Unit,
    launch: (T) -> Unit,
    openCamera: () -> Unit,
) {
    var settled = false
    val unavailable: (Exception) -> Unit = {
        if (!settled) {
            settled = true
            openCamera()
        }
    }
    try {
        request({ intent ->
            if (!settled) {
                settled = true
                try { launch(intent) }
                catch (_: Exception) { openCamera() }
            }
        }, unavailable)
    } catch (error: Exception) { unavailable(error) }
}
