package com.invoicecapture.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.produceState
import androidx.compose.ui.Modifier
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.rememberNavController
import kotlinx.serialization.Serializable
import com.invoicecapture.data.AppContainer
import com.invoicecapture.ui.batch.BatchScreen
import com.invoicecapture.ui.capture.CaptureScreen
import com.invoicecapture.ui.home.HomeScreen
import com.invoicecapture.ui.pairing.PairingScreen
import com.invoicecapture.ui.settings.SettingsScreen
import com.invoicecapture.ui.theme.Industry

@Serializable
object PairRoute

@Serializable
object HomeRoute

@Serializable
object SettingsRoute

/** Progress of one scanner session: quality problems, then uploads. [sessionKey] is the client_batch_key. */
@Serializable
data class CaptureRoute(val sessionKey: String)

@Serializable
data class BatchRoute(val batchId: String)

/** Single-activity navigation. The first screen is Pair until this phone holds a device token. */
@Composable
fun AppRoot(container: AppContainer) {
    val start by produceState<Any?>(initialValue = null) {
        value = if (container.pairingStore.current() != null) HomeRoute else PairRoute
    }
    val startDestination = start
    if (startDestination == null) {
        Box(Modifier.fillMaxSize().background(Industry.Ground))
        return
    }

    val nav = rememberNavController()
    fun startOver(route: Any) = nav.navigate(route) { popUpTo(nav.graph.id) { inclusive = true } }

    NavHost(navController = nav, startDestination = startDestination) {
        composable<PairRoute> {
            PairingScreen(onPaired = { startOver(HomeRoute) })
        }
        composable<HomeRoute> {
            HomeScreen(
                onOpenBatch = { nav.navigate(BatchRoute(it)) },
                onOpenCapture = { nav.navigate(CaptureRoute(it)) },
                onOpenSettings = { nav.navigate(SettingsRoute) },
            )
        }
        composable<SettingsRoute> {
            SettingsScreen(onBack = { nav.popBackStack() }, onUnpaired = { startOver(PairRoute) })
        }
        composable<CaptureRoute> {
            CaptureScreen(
                onBack = { nav.popBackStack() },
                onOpenBatch = { batchId ->
                    nav.navigate(BatchRoute(batchId)) { popUpTo<HomeRoute>() }
                },
            )
        }
        composable<BatchRoute> {
            BatchScreen(onBack = { nav.popBackStack() })
        }
    }
}
