package com.photobeam.app.protocol

import org.json.JSONObject
import java.io.InputStream
import java.io.OutputStream
import java.nio.ByteBuffer
import java.nio.ByteOrder

object ProtocolV2 {
    const val MAGIC_PBEA: Int = 0x50424541 // "PBEA" in ASCII big-endian
    val MAGIC_BYTES: ByteArray = byteArrayOf('P'.code.toByte(), 'B'.code.toByte(), 'E'.code.toByte(), 'A'.code.toByte())

    const val CONTROL_PORT: Int = 47470
    const val CONTROL_USB_PORT: Int = 47471
    const val DATA_PORT: Int = 47474
    const val DATA_USB_PORT: Int = 47475

    // PC -> Phone Forwarding Ports (Windows localhost -> Android listener)
    const val PC_TO_PHONE_CONTROL_FORWARD_PORT: Int = 47480
    const val PC_TO_PHONE_DATA_FORWARD_PORT: Int = 47484

    const val CHUNK_BUFFER_SIZE: Int = 4 * 1024 * 1024 // 4 MB

    fun readExact(inputStream: InputStream, n: Int): ByteArray? {
        val buf = ByteArray(n)
        var total = 0
        while (total < n) {
            val r = try {
                inputStream.read(buf, total, n - total)
            } catch (e: java.net.SocketTimeoutException) {
                if (total == 0) throw e else continue
            } catch (e: java.io.InterruptedIOException) {
                if (total == 0) throw e else continue
            }
            if (r == -1) return null
            total += r
        }
        return buf
    }

    fun sendFramedMsg(outputStream: OutputStream, msg: JSONObject): Boolean {
        return try {
            val body = msg.toString().toByteArray(Charsets.UTF_8)
            val header = ByteBuffer.allocate(4).order(ByteOrder.BIG_ENDIAN).putInt(body.size).array()
            outputStream.write(header)
            outputStream.write(body)
            outputStream.flush()
            true
        } catch (e: Exception) {
            false
        }
    }

    fun recvFramedMsg(inputStream: InputStream): JSONObject? {
        val lenBytes = readExact(inputStream, 4) ?: return null
        val len = ByteBuffer.wrap(lenBytes).order(ByteOrder.BIG_ENDIAN).int
        if (len <= 0 || len > 10 * 1024 * 1024) return null
        val payloadBytes = readExact(inputStream, len) ?: return null
        return try {
            JSONObject(String(payloadBytes, Charsets.UTF_8))
        } catch (e: Exception) {
            null
        }
    }

    fun packDataHeader(transferId: Long, fileSize: Long, filename: String): ByteArray {
        val nameBytes = filename.toByteArray(Charsets.UTF_8)
        val buf = ByteBuffer.allocate(4 + 8 + 8 + 2 + nameBytes.size).order(ByteOrder.BIG_ENDIAN)
        buf.put(MAGIC_BYTES)
        buf.putLong(transferId)
        buf.putLong(fileSize)
        buf.putShort(nameBytes.size.toShort())
        buf.put(nameBytes)
        return buf.array()
    }

    fun unpackDataHeader(inputStream: InputStream): Triple<Long, Long, String>? {
        val fixed = readExact(inputStream, 4 + 8 + 8 + 2) ?: return null
        val buf = ByteBuffer.wrap(fixed).order(ByteOrder.BIG_ENDIAN)
        val magic = buf.int
        if (magic != MAGIC_PBEA) return null
        val transferId = buf.long
        val fileSize = buf.long
        val nameLen = buf.short.toInt() and 0xFFFF
        val nameBytes = readExact(inputStream, nameLen) ?: return null
        val filename = String(nameBytes, Charsets.UTF_8)
        return Triple(transferId, fileSize, filename)
    }
}
