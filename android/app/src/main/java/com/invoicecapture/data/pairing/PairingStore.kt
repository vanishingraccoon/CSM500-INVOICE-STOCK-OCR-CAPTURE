package com.invoicecapture.data.pairing

import android.content.Context
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.emptyPreferences
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.catch
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.map
import com.invoicecapture.data.api.PairResponse
import java.io.IOException

private val Context.pairingDataStore: DataStore<Preferences> by preferencesDataStore(name = "pairing")

/** This phone's pairing with the store laptop. */
data class Pairing(
    val server: String,
    val token: String,
    val shop: String,
    val shopName: String?,
    val deviceId: String?,
    val deviceName: String,
) {
    val shopLabel: String get() = shopName ?: shop
}

/**
 * The laptop address, device token and shop, kept in the app's private DataStore. App backup is
 * off (see the manifest), so the token never leaves the phone except in requests to the laptop.
 */
class PairingStore(context: Context) {
    private val store = context.applicationContext.pairingDataStore

    private val preferences: Flow<Preferences> = store.data.catch { e ->
        if (e is IOException) emit(emptyPreferences()) else throw e
    }

    /** The last pairing read, for code that cannot suspend (the image interceptor). */
    @Volatile
    var latest: Pairing? = null
        private set

    val pairing: Flow<Pairing?> = preferences.map { it.toPairing() }.distinctUntilChanged()

    /** The name this phone gives itself when pairing. */
    val deviceName: Flow<String?> = preferences.map { it[DEVICE_NAME] }.distinctUntilChanged()

    /** The address used last time, to prefill the pairing form after unpairing. */
    val lastServer: Flow<String?> = preferences.map { it[SERVER] }.distinctUntilChanged()

    suspend fun current(): Pairing? = preferences.first().toPairing().also { latest = it }

    /** Keeps [latest] up to date; runs for the life of the app. */
    suspend fun keepLatestUpdated() {
        pairing.collect { latest = it }
    }

    suspend fun save(server: String, response: PairResponse, deviceName: String) {
        store.edit {
            it[SERVER] = server
            it[TOKEN] = response.token
            it[SHOP] = response.shop
            if (response.shopName != null) it[SHOP_NAME] = response.shopName else it.remove(SHOP_NAME)
            it[DEVICE_ID] = response.deviceId
            it[DEVICE_NAME] = deviceName
        }
        current()
    }

    suspend fun updateServer(server: String) {
        store.edit { it[SERVER] = server }
        current()
    }

    suspend fun setDeviceName(name: String) {
        store.edit { it[DEVICE_NAME] = name }
        current()
    }

    /** Forgets the token and shop. The address and device name stay as defaults for pairing again. */
    suspend fun unpair() {
        store.edit {
            it.remove(TOKEN)
            it.remove(DEVICE_ID)
            it.remove(SHOP)
            it.remove(SHOP_NAME)
        }
        latest = null
    }

    private fun Preferences.toPairing(): Pairing? {
        val server = this[SERVER] ?: return null
        val token = this[TOKEN] ?: return null
        return Pairing(
            server = server,
            token = token,
            shop = this[SHOP] ?: "",
            shopName = this[SHOP_NAME],
            deviceId = this[DEVICE_ID],
            deviceName = this[DEVICE_NAME] ?: "",
        )
    }

    private companion object {
        val SERVER = stringPreferencesKey("server")
        val TOKEN = stringPreferencesKey("token")
        val SHOP = stringPreferencesKey("shop")
        val SHOP_NAME = stringPreferencesKey("shop_name")
        val DEVICE_ID = stringPreferencesKey("device_id")
        val DEVICE_NAME = stringPreferencesKey("device_name")
    }
}
