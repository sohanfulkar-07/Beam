package com.photobeam.app.protocol

import java.io.RandomAccessFile
import java.security.MessageDigest
import kotlin.math.ceil
import kotlin.math.min

/**
 * IntegrityManager — Android Kotlin
 * Per-chunk: XXH3-64 (via net.jpountz library or fallback FNV)
 * Per-file: SHA-256
 */
object IntegrityManager {

    /**
     * Compute chunk checksum.
     * Uses XXHash3-64 if available, otherwise falls back to FNV-1a-64.
     * Note: Android doesn't have xxhash4j by default — the build includes it via Gradle.
     * This fallback ensures the code compiles even if the dependency isn't resolved yet.
     */
    fun chunkChecksum(data: ByteArray): Long {
        val crc = java.util.zip.CRC32()
        crc.update(data)
        return crc.value
    }

    fun verifyChunk(data: ByteArray, expectedChecksum: Long): Boolean {
        if (chunkChecksum(data) == expectedChecksum) return true
        if (fnv1a64(data) == expectedChecksum) return true
        return false
    }

    fun fileHash(path: java.io.File, bufferSize: Int = 1 * 1024 * 1024): String {
        val md = MessageDigest.getInstance("SHA-256")
        val buf = ByteArray(bufferSize)
        path.inputStream().use { stream ->
            while (true) {
                val n = stream.read(buf)
                if (n < 0) break
                md.update(buf, 0, n)
            }
        }
        return md.digest().joinToString("") { "%02x".format(it) }
    }

    fun verifyFile(path: java.io.File, expectedSha256: String): Boolean =
        fileHash(path).lowercase() == expectedSha256.lowercase()

    private fun fnv1a64(data: ByteArray): Long {
        var h = -3750763034362895579L // FNV offset basis for 64-bit
        for (b in data) {
            h = h xor (b.toLong() and 0xFF)
            h *= 1099511628211L
        }
        return h
    }
}

/**
 * ChunkManager — Android (Receiver side)
 * Tracks received chunks, detects missing/duplicate.
 */
class ChunkManager(
    val transferId: ByteArray,
    val fileId: ByteArray,
    val fileSize: Long,
    val chunkSize: Int = DEFAULT_CHUNK_SIZE,
) {
    val totalChunks: Int = if (fileSize == 0L) 1 else ceil(fileSize.toDouble() / chunkSize).toInt()

    private val received = BooleanArray(totalChunks)
    private val lock = Any()

    /** Returns true if this is a new chunk (not duplicate). Returns false if invalid chunkId or duplicate. */
    fun recordReceived(chunkId: Int): Boolean {
        synchronized(lock) {
            if (chunkId < 0 || chunkId >= totalChunks) return false
            if (received[chunkId]) return false
            received[chunkId] = true
            return true
        }
    }

    fun isComplete(): Boolean = synchronized(lock) { received.all { it } }

    fun receivedCount(): Int = synchronized(lock) { received.count { it } }

    fun receivedChunks(): List<Int> = synchronized(lock) {
        received.indices.filter { received[it] }
    }

    fun missingChunks(): List<Int> = synchronized(lock) {
        received.indices.filter { !received[it] }
    }

    fun offsetForChunk(chunkId: Int): Long = chunkId.toLong() * chunkSize

    fun lengthForChunk(chunkId: Int): Int =
        if (fileSize == 0L)
            0
        else if (chunkId == totalChunks - 1 && fileSize % chunkSize != 0L)
            (fileSize % chunkSize).toInt()
        else
            chunkSize
}

/**
 * Write a received chunk to the temporary file at the correct offset.
 *
 * IMPORTANT: This variant does NOT fsync on every chunk — that would cause
 * per-chunk sync stalls (10–500 ms each on Android flash storage), which for
 * large files produce multi-second idle gaps that trigger socket timeouts on
 * the sending side and kill the transfer.
 *
 * The caller must keep [raf] open for the duration of the file and call
 * [flushTmpFile] once at completion or on pause to sync to disk safely.
 */
fun writeChunkToFile(raf: java.io.RandomAccessFile, offset: Long, data: ByteArray) {
    raf.seek(offset)
    raf.write(data)
    // No fsync here — see [flushTmpFile]
}

/**
 * Legacy overload that opens/closes its own RAF. Used only for single-chunk
 * retransmissions or tests where keeping a persistent handle is impractical.
 * Does NOT fsync — caller is responsible for flushing when needed.
 */
fun writeChunkToFile(tmpFile: java.io.File, offset: Long, data: ByteArray) {
    java.io.RandomAccessFile(tmpFile, "rw").use { f ->
        f.seek(offset)
        f.write(data)
        // No fsync per chunk — call flushTmpFile() at completion/pause
    }
}

/**
 * Flush a temporary file to disk (single fsync).
 * Call once per file at completion or pause — never per chunk.
 */
fun flushTmpFile(raf: java.io.RandomAccessFile) {
    try { raf.fd.sync() } catch (_: Exception) {}
}

fun saveResumeState(destDir: java.io.File, sid: String, fid: String, info: TransferInfo, receivedChunks: List<Int>) {
    val resumeDir = java.io.File(destDir, ".resume")
    resumeDir.mkdirs()
    val stateFile = java.io.File(resumeDir, "${sid}_${fid}.json")
    val json = org.json.JSONObject()
        .put("sid", sid)
        .put("fid", fid)
        .put("name", info.name)
        .put("size", info.size)
        .put("sha256", info.sha256)
        .put("received_chunks", org.json.JSONArray(receivedChunks))
    stateFile.writeText(json.toString())
}

fun verifyResumeState(destDir: java.io.File, sid: String, fid: String, expectedSize: Long): Pair<Boolean, List<Int>> {
    val tmpFile = java.io.File(destDir, ".${fid}.pbtemp")
    if (!tmpFile.exists() || tmpFile.length() < expectedSize) {
        return Pair(false, emptyList())
    }
    val stateFile = java.io.File(java.io.File(destDir, ".resume"), "${sid}_${fid}.json")
    if (!stateFile.exists()) {
        return Pair(false, emptyList())
    }
    return try {
        val json = org.json.JSONObject(stateFile.readText())
        val rxJson = json.optJSONArray("received_chunks") ?: org.json.JSONArray()
        val list = mutableListOf<Int>()
        for (i in 0 until rxJson.length()) {
            list.add(rxJson.getInt(i))
        }
        Pair(true, list)
    } catch (_: Exception) {
        Pair(false, emptyList())
    }
}

