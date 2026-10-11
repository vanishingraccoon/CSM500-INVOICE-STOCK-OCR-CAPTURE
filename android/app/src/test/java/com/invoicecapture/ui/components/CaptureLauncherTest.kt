package com.invoicecapture.ui.components

import org.junit.Assert.assertEquals
import org.junit.Test

class CaptureLauncherTest {
    @Test
    fun unsupportedPlayServicesOpensCamera() {
        val opened = mutableListOf<String>()
        launchDocumentCapture<String>(
            request = { _, failure -> failure(IllegalStateException("Feature not available in this version of Google Play services")) },
            launch = { opened.add("scanner") },
            openCamera = { opened.add("camera") },
        )
        assertEquals(listOf("camera"), opened)
    }

    @Test
    fun availableScannerKeepsDocumentFlow() {
        val opened = mutableListOf<String>()
        launchDocumentCapture<String>(
            request = { ready, _ -> ready("document intent") },
            launch = { opened.add(it) },
            openCamera = { opened.add("camera") },
        )
        assertEquals(listOf("document intent"), opened)
    }

    @Test
    fun synchronousRequestFailureOpensCamera() {
        var cameras = 0
        launchDocumentCapture<String>(
            request = { _, _ -> throw IllegalStateException("services unavailable") },
            launch = { error("scanner must not open") },
            openCamera = { cameras++ },
        )
        assertEquals(1, cameras)
    }

    @Test
    fun intentLaunchFailureOpensCameraOnce() {
        var cameras = 0
        launchDocumentCapture<String>(
            request = { ready, failure -> ready("intent"); failure(IllegalStateException("late callback")) },
            launch = { throw IllegalStateException("cannot open scanner") },
            openCamera = { cameras++ },
        )
        assertEquals(1, cameras)
    }
}
