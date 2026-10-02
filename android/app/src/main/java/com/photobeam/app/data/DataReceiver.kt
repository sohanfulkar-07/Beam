package com.photobeam.app.data

import android.content.Context
import android.os.Environment
import android.util.Log
import com.photobeam.app.protocol.ProtocolV2
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import java.io.File
import java.io.FileOutputStream
import java.net.InetSocketAddress
import java.net.ServerSocket
import java.net.Socket
import java.security.MessageDigest

/**
 * High-Speed Streaming DataReceiver for Android.
 * Accepts binary transfer streams on port 47474, saving directly to disk
 * with 4 MB constant buffer, rolling SHA-256 verification, and 64-bit offsets.
 */
class DataReceiver(
    private val context: Context,
    private val port: Int = ProtocolV2.DATA_PORT
) {
    private val tag = "PhotoBeam:DataReceiver"
    private val scope = CoroutineScope(Dispatchers.IO + Job())
    private var serverSocket: ServerSocket? = null
    private var listenJob: Job? = null

    @Volatile
    var isRunning = false
        private set

    fun start() {
        if (isRunning) return
        isRunning = true

        var downloadDir = File(
            Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS),
            "PhotoBeam"
        )
        if (!downloadDir.exists() && !downloadDir.mkdirs()) {
            downloadDir = File(context.getExternalFilesDir(Environment.DIRECTORY_DOWNLOADS) ?: context.filesDir, "PhotoBeam").apply { mkdirs() }
        }

        listenJob = scope.launch {
            try {
                val srv = ServerSocket()
                srv.reuseAddress = true
                srv.bind(InetSocketAddress(port))
                serverSocket = srv
                Log.i(tag, "Listening on 0.0.0.0:$port (downloadDir=$downloadDir)")

                while (isActive && isRunning) {
                    val clientSock = try {
                        srv.accept()
                    } catch (e: Exception) {
                        if (!isRunning) break
                        continue
                    }

                    launch {
                        handleClient(clientSock, downloadDir)
                    }
                }
            } catch (e: Exception) {
                Log.e(tag, "Server error on port $port: ${e.message}")
            }
        }
    }

    fun stop() {
        isRunning = false
        listenJob?.cancel()
        try { serverSocket?.close() } catch (_: Exception) {}
        serverSocket = null
        Log.i(tag, "Stopped")
    }

    private fun handleClient(sock: Socket, downloadDir: File) {
        var targetFile: File? = null
        var partFile: File? = null

        try {
            sock.tcpNoDelay = true
            sock.soTimeout = 15000

            // 1. Unpack Header
            val header = ProtocolV2.unpackDataHeader(sock.inputStream)
            if (header == null) {
                Log.w(tag, "Invalid header received")
                sock.close()
                return
            }

            val (transferId, fileSize, rawFilename) = header
            val filename = File(rawFilename).name
            Log.i(tag, "Incoming stream: id=$transferId, file='$filename', size=$fileSize bytes")

            targetFile = File(downloadDir, filename)
            partFile = File(downloadDir, "$filename.part")

            val hasher = MessageDigest.getInstance("SHA-256")
            val buffer = ByteArray(ProtocolV2.CHUNK_BUFFER_SIZE)
            var bytesReceived = 0L

            FileOutputStream(partFile).use { fos ->
                while (bytesReceived < fileSize) {
                    val toRead = minOf(buffer.size.toLong(), fileSize - bytesReceived).toInt()
                    val read = sock.inputStream.read(buffer, 0, toRead)
                    if (read == -1) {
                        throw java.io.EOFException("Unexpected EOF after $bytesReceived/$fileSize bytes")
                    }

                    fos.write(buffer, 0, read)
                    hasher.update(buffer, 0, read)
                    bytesReceived += read
                }
                fos.flush()
            }

            // 2. Read 32-byte SHA-256 Trailer
            val trailer = ProtocolV2.readExact(sock.inputStream, 32)
                ?: throw java.io.IOException("Missing SHA-256 trailer from sender")

            val calculatedDigest = hasher.digest()
            if (!trailer.contentEquals(calculatedDigest)) {
                throw java.security.DigestException("Checksum mismatch on received file '$filename'")
            }

            // 3. Rename .part to target
            if (targetFile.exists()) {
                targetFile.delete()
            }
            if (!partFile.renameTo(targetFile)) {
                partFile.copyTo(targetFile, overwrite = true)
                partFile.delete()
            }

            // 4. Send 1-byte ACK
            try {
                sock.outputStream.write(byteArrayOf(0x06))
                sock.outputStream.flush()
            } catch (_: Exception) {}

            Log.i(tag, "Transfer $transferId completed successfully: '$filename' ($fileSize bytes)")

        } catch (e: Exception) {
            Log.e(tag, "Transfer failed: ${e.message}", e)
            try { partFile?.delete() } catch (_: Exception) {}
        } finally {
            try { sock.close() } catch (_: Exception) {}
        }
    }
}
