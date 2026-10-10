package com.invoicecapture.data.pending

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import com.invoicecapture.data.api.ApiJson
import java.io.File
import java.io.FileOutputStream
import java.nio.file.Files
import java.nio.file.StandardCopyOption

@Serializable
enum class SessionKind {
    /** One document-scanner session: a batch on the laptop. */
    SCAN,

    /** One page photographed again for an invoice in the Retake tab. */
    RETAKE,
}

@Serializable
enum class PageState {
    /** The scanner's JPEG is copied to the phone; not checked yet. */
    SCANNED,

    /** Checked and saved as the upload copy; waiting to upload. */
    READY,

    /** The laptop sent a matching receipt; the phone's copy is deleted. */
    UPLOADED,

    /** The scanner's JPEG could not be opened; nothing can be uploaded for this page. */
    UNREADABLE,
}

@Serializable
data class PendingPage(
    /** 1-based position in the scanner result (for a retake: the replaced photo's ordinal). */
    val ordinal: Int,
    val clientPhotoKey: String,
    val state: PageState = PageState.SCANNED,
    /** File names inside the session folder. */
    val sourceFile: String? = null,
    val uploadFile: String? = null,
    val sha256: String? = null,
    val bytes: Long? = null,
    val width: Int? = null,
    val height: Int? = null,
    val sharpness: Double? = null,
    val meanLuma: Double? = null,
    val contrastSpread: Int? = null,
    val glareFraction: Double? = null,
    /** [com.invoicecapture.domain.quality.QualityProblem] codes. */
    val problems: List<String> = emptyList(),
    val replacesPhotoId: String? = null,
    val photoId: String? = null,
    val lastError: String? = null,
    /** A 409/422 stopped the uploads for this page until the manager taps Try again. */
    val stopped: Boolean = false,
    val attempts: Int = 0,
) {
    val onPhone: Boolean get() = state == PageState.SCANNED || state == PageState.READY
}

@Serializable
data class PendingSession(
    /** For a scan this is the `client_batch_key`. */
    val key: String,
    val kind: SessionKind,
    val createdAtMillis: Long,
    val pageCount: Int,
    val serverBatchId: String? = null,
    val completeSent: Boolean = false,
    val batchError: String? = null,
    val pages: List<PendingPage> = emptyList(),
    /** Scan positions the manager discarded: their photos are gone and never upload. */
    val discardedOrdinals: List<Int> = emptyList(),
) {
    val pagesOnPhone: Int get() = pages.count { it.onPhone }
    val preparedCount: Int get() = pages.count { it.state != PageState.SCANNED }
    val uploadedCount: Int get() = pages.count { it.state == PageState.UPLOADED }

    /** Nothing left to upload: every page is uploaded or cannot be. */
    val allSettled: Boolean get() = pages.isNotEmpty() && pages.none { it.onPhone }
    val allUploaded: Boolean get() = pages.isNotEmpty() && pages.all { it.state == PageState.UPLOADED }
    val finished: Boolean get() = allUploaded && (kind == SessionKind.RETAKE || completeSent)
    val problemPages: List<PendingPage> get() = pages.filter { it.stopped || it.state == PageState.UNREADABLE }
}

/**
 * The phone's upload queue on disk: one folder per session under the app's private files
 * directory (not the cache, which Android may clear when storage runs low), holding the photos
 * and a `manifest.json`. Manifests are replaced atomically; all writes go through one lock.
 */
class PendingStore(private val root: File, private val json: Json = ApiJson) {
    private val mutex = Mutex()
    private val _sessions = MutableStateFlow<List<PendingSession>>(emptyList())
    private var loaded = false

    val sessions: StateFlow<List<PendingSession>> = _sessions.asStateFlow()

    fun sessionDir(key: String): File = File(root, key)

    fun file(key: String, name: String): File = File(sessionDir(key), name)

    suspend fun ensureLoaded() = mutex.withLock { loadLocked() }

    suspend fun get(key: String): PendingSession? = mutex.withLock {
        loadLocked()
        _sessions.value.firstOrNull { it.key == key }
    }

    suspend fun all(): List<PendingSession> = mutex.withLock {
        loadLocked()
        _sessions.value
    }

    suspend fun put(session: PendingSession) = mutex.withLock {
        loadLocked()
        writeLocked(session)
    }

    suspend fun update(key: String, transform: (PendingSession) -> PendingSession): PendingSession? = mutex.withLock {
        loadLocked()
        val current = _sessions.value.firstOrNull { it.key == key } ?: return@withLock null
        val next = transform(current)
        if (next != current) writeLocked(next)
        next
    }

    suspend fun updatePage(
        key: String,
        clientPhotoKey: String,
        transform: (PendingPage) -> PendingPage,
    ): PendingSession? = update(key) { session ->
        session.copy(pages = session.pages.map { if (it.clientPhotoKey == clientPhotoKey) transform(it) else it })
    }

    /**
     * Drops one page for good (the manager discarded a page the laptop refused). The page leaves
     * the queue, so no later retry can send it, and the session expects one page fewer.
     */
    suspend fun discardPage(key: String, clientPhotoKey: String): PendingSession? = update(key) { session ->
        val page = session.pages.firstOrNull { it.clientPhotoKey == clientPhotoKey } ?: return@update session
        session.copy(
            pageCount = (session.pageCount - 1).coerceAtLeast(0),
            pages = session.pages.filterNot { it.clientPhotoKey == clientPhotoKey },
            discardedOrdinals = (session.discardedOrdinals + page.ordinal).distinct().sorted(),
        )
    }

    /** Removes a session folder. Only for sessions with nothing left on the phone to upload. */
    suspend fun remove(key: String) = mutex.withLock {
        loadLocked()
        withContext(Dispatchers.IO) { sessionDir(key).deleteRecursively() }
        _sessions.value = _sessions.value.filterNot { it.key == key }
    }

    private suspend fun loadLocked() {
        if (loaded) return
        val list = withContext(Dispatchers.IO) {
            root.mkdirs()
            root.listFiles()
                ?.filter { it.isDirectory }
                ?.mapNotNull { dir -> readManifest(dir) }
                .orEmpty()
        }
        _sessions.value = list.sortedBy { it.createdAtMillis }
        loaded = true
    }

    private fun readManifest(dir: File): PendingSession? {
        val manifest = File(dir, MANIFEST)
        if (!manifest.isFile) return null
        return try {
            json.decodeFromString(PendingSession.serializer(), manifest.readText())
        } catch (e: IllegalArgumentException) {
            null
        }
    }

    private suspend fun writeLocked(session: PendingSession) {
        withContext(Dispatchers.IO) {
            val dir = sessionDir(session.key).apply { mkdirs() }
            val tmp = File(dir, "$MANIFEST.tmp")
            FileOutputStream(tmp).use { out ->
                out.write(json.encodeToString(PendingSession.serializer(), session).toByteArray())
                out.flush()
                out.fd.sync()
            }
            Files.move(
                tmp.toPath(),
                File(dir, MANIFEST).toPath(),
                StandardCopyOption.ATOMIC_MOVE,
                StandardCopyOption.REPLACE_EXISTING,
            )
        }
        _sessions.value = (_sessions.value.filterNot { it.key == session.key } + session).sortedBy { it.createdAtMillis }
    }

    private companion object {
        const val MANIFEST = "manifest.json"
    }
}
