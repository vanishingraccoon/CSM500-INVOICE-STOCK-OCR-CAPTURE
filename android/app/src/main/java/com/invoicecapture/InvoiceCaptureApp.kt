package com.invoicecapture

import android.app.Application
import coil3.ImageLoader
import coil3.PlatformContext
import coil3.SingletonImageLoader
import coil3.network.okhttp.OkHttpNetworkFetcherFactory
import kotlinx.coroutines.launch
import com.invoicecapture.data.AppContainer

class InvoiceCaptureApp : Application(), SingletonImageLoader.Factory {

    lateinit var container: AppContainer
        private set

    override fun onCreate() {
        super.onCreate()
        container = AppContainer(this)
        container.appScope.launch { container.pairingStore.keepLatestUpdated() }
        container.appScope.launch {
            container.pairingStore.current()
            container.uploadEngine.pruneFinished()
            // Anything left from last time (app closed, phone restarted) goes again now.
            container.scheduler.resumeAll(container.pendingStore, includeStopped = false)
        }
    }

    /** Coil loads invoice photos through the client that adds the device token for the laptop. */
    override fun newImageLoader(context: PlatformContext): ImageLoader =
        ImageLoader.Builder(context)
            .components { add(OkHttpNetworkFetcherFactory(callFactory = { container.imageClient })) }
            .build()
}
