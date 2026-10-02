package com.photobeam.app.data

import android.content.Context
import android.net.Uri
import android.provider.OpenableColumns
import android.util.Log
import com.photobeam.app.protocol.ProtocolV2
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.File
import java.io.FileInputStream
import java.io.InputStream
import java.net.InetSocketAddress
import java.net.Socket
import java.security.MessageDigest

/**
 * High-Speed Streaming DataSender for Android.
 * Streams files from ContentResolver or filesystem directly to peer's DataReceiver
 * with 4 MB constant buffer, rolling SHA-256 calculation, and 64-bit offsets.
 */
class DataSender(private val context: Context) {

    private val tag = "PhotoBeam:DataSender"

    data class ProgressUpdate(
        val fileIndex: Int,
        val totalFiles: Int,
        val fileName: String,
        val bytesSent: Long,
        val totalBytes: Long,
        val speedMbps: Double
    )

    suspend fun sendFiles(
        peerIp: String,
        peerPort: Int,
        uris: List<Uri>,
        onProgress: (ProgressUpdate) -> Unit = {},
        onFileComplete: (fileName: String, success: Boolean, error: String?) -> Unit = { _, _, _ -> }
    ): Boolean = withContext(Dispatchers.IO) {
        var allSuccess = true

        for ((index, uri) in uris.withIndex()) {
            val (name, size) = getFileNameAndSize(context, uri)
            val transferId = (System.currentTimeMillis() and 0x7FFFFFFFFFFFFFFF)

            Log.i(tag, "Sending file [${index + 1}/${uris.size}]: '$name' ($size bytes) -> $peerIp:$peerPort")

            val success = sendSingleFile(
                peerIp = peerIp,
                peerPort = peerPort,
                uri = uri,
                transferId = transferId,
                fileName = name,
                fileSize = size,
                fileIndex = index + 1,
                totalFiles = uris.size,
                onProgress = onProgress
            )

            if (success) {
                onFileComplete(name, true, null)
            } else {
                allSuccess = false
                onFileComplete(name, false, "Failed to transfer file")
                break
            }
        }

        allSuccess
    }

    private fun sendSingleFile(
        peerIp: String,
        peerPort: Int,
        uri: Uri,
        transferId: Long,
        fileName: String,
        fileSize: Long,
        fileIndex: Int,
        totalFiles: Int,
        onProgress: (ProgressUpdate) -> Unit
    ): Boolean {
        var socket: Socket? = null
        var inputStream: InputStream? = null

        return try {
            inputStream = openStream(context, uri)
            val actualSize = if (fileSize > 0) fileSize else inputStream.available().toLong()

            socket = Socket()
            socket.tcpNoDelay = true
            socket.soTimeout = 15000
            socket.connect(InetSocketAddress(peerIp, peerPort), 5000)

            // 1. Pack & send binary header
            val headerBytes = ProtocolV2.packDataHeader(transferId, actualSize, fileName)
            socket.outputStream.write(headerBytes)
            socket.outputStream.flush()

            // 2. Stream payload in 4 MB chunks with rolling SHA-256
            val hasher = MessageDigest.getInstance("SHA-256")
            val buffer = ByteArray(ProtocolV2.CHUNK_BUFFER_SIZE)
            var bytesSent = 0L
            val startTime = System.currentTimeMillis()
            var lastProgressTime = startTime

            while (bytesSent < actualSize || (actualSize == 0L && bytesSent == 0L)) {
                if (actualSize == 0L) break
                val toRead = minOf(buffer.size.toLong(), actualSize - bytesSent).toInt()
                val read = inputStream.read(buffer, 0, toRead)
                if (read == -1) break

                hasher.update(buffer, 0, read)
                socket.outputStream.write(buffer, 0, read)
                bytesSent += read

                val now = System.currentTimeMillis()
                if (now - lastProgressTime >= 100 || bytesSent == actualSize) {
                    val elapsedSec = maxOf((now - startTime) / 1000.0, 0.001)
                    val speedMbps = (bytesSent / (1024.0 * 1024.0)) / elapsedSec
                    onProgress(
                        ProgressUpdate(
                            fileIndex = fileIndex,
                            totalFiles = totalFiles,
                            fileName = fileName,
                            bytesSent = bytesSent,
                            totalBytes = actualSize,
                            speedMbps = speedMbps
                        )
                    )
                    lastProgressTime = now
                }
            }

            socket.outputStream.flush()

            // 3. Send 32-byte SHA-256 binary trailer
            val digest = hasher.digest()
            socket.outputStream.write(digest)
            socket.outputStream.flush()

            // 4. Wait for 1-byte ACK (0x06)
            val ack = ProtocolV2.readExact(socket.inputStream, 1)
            if (ack == null || ack[0] != 0x06.toByte()) {
                Log.e(tag, "Receiver failed to ACK transfer for '$fileName' (received: ${ack?.toList()})")
                return false
            }

            Log.i(tag, "Transfer completed successfully for '$fileName' ($bytesSent bytes)")
            true

        } catch (e: Exception) {
            Log.e(tag, "Transfer failed for '$fileName': ${e.message}", e)
            false
        } finally {
            try { inputStream?.close() } catch (_: Exception) {}
            try { socket?.close() } catch (_: Exception) {}
        }
    }

    companion object {
        fun getFileNameAndSize(context: Context, uri: Uri): Pair<String, Long> {
            var name = uri.lastPathSegment?.substringAfterLast('/') ?: "file"
            var size = 0L

            if (uri.scheme == "file") {
                try {
                    val rawPath = uri.path ?: ""
                    val resolvedPath = if (rawPath.startsWith("/sdcard")) {
                        rawPath.replaceFirst("/sdcard", "/storage/emulated/0")
                    } else {
                        rawPath
                    }
                    val f = File(resolvedPath)
                    if (f.exists()) {
                        val len = f.length()
                        if (len > 0) return Pair(f.name, len)
                        val fallback = File(context.getExternalFilesDir(null), f.name)
                        if (fallback.exists()) {
                            return Pair(fallback.name, fallback.length())
                        }
                    }
                } catch (_: Exception) {}
            }

            try {
                context.contentResolver.query(uri, null, null, null, null)?.use { cursor ->
                    val nameIndex = cursor.getColumnIndex(OpenableColumns.DISPLAY_NAME)
                    val sizeIndex = cursor.getColumnIndex(OpenableColumns.SIZE)
                    if (cursor.moveToFirst()) {
                        if (nameIndex != -1) {
                            cursor.getString(nameIndex)?.let { name = it }
                        }
                        if (sizeIndex != -1) {
                            size = cursor.getLong(sizeIndex)
                        }
                    }
                }
            } catch (_: Exception) {}

            if (size == 0L) {
                try {
                    size = context.contentResolver.openFileDescriptor(uri, "r")?.use { it.statSize } ?: 0L
                } catch (_: Exception) {}
            }

            return Pair(name, size)
        }

        fun openStream(context: Context, uri: Uri): InputStream {
            if (uri.scheme == "file") {
                val rawPath = uri.path ?: ""
                val resolvedPath = if (rawPath.startsWith("/sdcard")) {
                    rawPath.replaceFirst("/sdcard", "/storage/emulated/0")
                } else {
                    rawPath
                }
                val f = File(resolvedPath)
                if (f.exists()) {
                    try {
                        return FileInputStream(f)
                    } catch (e: Exception) {
                        val fallback = File(context.getExternalFilesDir(null), f.name)
                        if (fallback.exists()) {
                            return FileInputStream(fallback)
                        }
                    }
                }
            }
            return context.contentResolver.openInputStream(uri)
                ?: throw java.io.FileNotFoundException("Cannot open stream for $uri")
        }
    }
}
