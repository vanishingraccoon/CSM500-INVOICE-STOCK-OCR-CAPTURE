package com.invoicecapture.data

import android.content.Context
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import okhttp3.OkHttpClient
import com.invoicecapture.data.api.DeviceTokenInterceptor
import com.invoicecapture.data.api.InvoiceApi
import com.invoicecapture.data.image.ImageProcessor
import com.invoicecapture.data.pairing.PairingStore
import com.invoicecapture.data.pending.PendingStore
import com.invoicecapture.data.upload.UploadEngine
import com.invoicecapture.data.upload.UploadScheduler
import com.invoicecapture.domain.quality.QualityConfig
import java.io.File
import java.util.concurrent.TimeUnit

/** Hand-made dependency wiring: one of each, created by the Application. */
class AppContainer(context: Context) {
    private val appContext = context.applicationContext

    val appScope = CoroutineScope(SupervisorJob() + Dispatchers.Default)

    val pairingStore = PairingStore(appContext)

    /** One connection pool for API calls and images; short connect timeout on the local network. */
    private val baseClient: OkHttpClient = OkHttpClient.Builder()
        .connectTimeout(8, TimeUnit.SECONDS)
        .readTimeout(30, TimeUnit.SECONDS)
        .writeTimeout(60, TimeUnit.SECONDS)
        .build()

    val api = InvoiceApi(baseClient, pairingStore)

    /** For Coil: adds the device token to requests for the paired laptop only. */
    val imageClient: OkHttpClient = baseClient.newBuilder()
        .addInterceptor(DeviceTokenInterceptor(pairingStore))
        .build()

    val pendingStore = PendingStore(File(appContext.filesDir, "pending"))

    val scheduler = UploadScheduler(appContext)

    val uploadEngine = UploadEngine(api, pendingStore, scheduler, ImageProcessor(QualityConfig.DEFAULT))

    val invoices = InvoiceRepository(api, pairingStore)

    val capture = CaptureRepository(appContext, pendingStore, scheduler, uploadEngine)
}
