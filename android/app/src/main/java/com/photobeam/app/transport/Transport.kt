package com.photobeam.app.transport

import com.photobeam.app.protocol.CHUNK_HEADER_SIZE
import com.photobeam.app.protocol.ChunkFrame
import org.json.JSONObject
import java.io.BufferedReader
import java.io.InputStreamReader
import java.io.PrintWriter
import java.net.Socket
import java.util.concurrent.atomic.AtomicLong
import javax.net.ssl.SSLSocket

/**
 * Transport abstraction — Android
 * Matches the Python Transport interface.
 */
abstract class Transport(val transportId: String) {
    enum class Status { DISCONNECTED, CONNECTING, CONNECTED, FAILED }

    var status: Status = Status.DISCONNECTED
        protected set

    val bytesSent = AtomicLong(0)
    val bytesReceived = AtomicLong(0)

    private val throughputSamples = ArrayDeque<Pair<Long, Long>>() // (nanoTime, bytes)
    private val samplesLock = Any()

    fun isConnected() = status == Status.CONNECTED

    abstract fun connect(host: String, port: Int, timeoutMs: Int = 10_000)
    abstract fun disconnect()
    abstract fun sendAll(data: ByteArray)
    abstract fun recvExact(n: Int): ByteArray

    fun sendJson(msg: JSONObject) {
        val bytes = (msg.toString() + "\n").toByteArray(Charsets.UTF_8)
        sendAll(bytes)
    }

    fun recvJson(): JSONObject {
        // Read until newline
        val sb = StringBuilder()
        val buf = ByteArray(1)
        while (true) {
            val b = recvExact(1)
            if (b[0] == '\n'.code.toByte()) break
            sb.append(b[0].toInt().toChar())
        }
        return JSONObject(sb.toString())
    }

    fun sendChunkFrame(frame: ChunkFrame) {
        val encoded = frame.encode()
        sendAll(encoded)
        recordSent(encoded.size.toLong())
    }

    fun recvChunkFrame(): ChunkFrame {
        val header = recvExact(CHUNK_HEADER_SIZE)
        val chunkLen = java.nio.ByteBuffer.wrap(header, 56, 4)
            .order(java.nio.ByteOrder.BIG_ENDIAN).int
        val data = recvExact(chunkLen)
        recordReceived((CHUNK_HEADER_SIZE + chunkLen).toLong())
        return ChunkFrame.decode(header + data)
    }

    fun throughputBps(windowMs: Long = 2000): Double {
        val now = System.nanoTime()
        val cutoff = now - windowMs * 1_000_000
        synchronized(samplesLock) {
            throughputSamples.removeAll { it.first < cutoff }
            if (throughputSamples.size < 2) return 0.0
            val total = throughputSamples.sumOf { it.second }
            val duration = (throughputSamples.last().first - throughputSamples.first().first) / 1_000_000_000.0
            return if (duration > 0) total / duration else 0.0
        }
    }

    protected fun recordSent(n: Long) {
        bytesSent.addAndGet(n)
        addSample(n)
    }

    protected fun recordReceived(n: Long) {
        bytesReceived.addAndGet(n)
    }

    private fun addSample(n: Long) {
        synchronized(samplesLock) {
            throughputSamples.addLast(Pair(System.nanoTime(), n))
            if (throughputSamples.size > 50) throughputSamples.removeFirst()
        }
    }
}

/**
 * Wi-Fi TCP Transport — Android
 */
class WiFiTransport(transportId: String = "wifi") : Transport(transportId) {
    private var socket: SSLSocket? = null
    private var outputStream: java.io.OutputStream? = null
    private var inputStream: java.io.InputStream? = null

    override fun connect(host: String, port: Int, timeoutMs: Int) {
        status = Status.CONNECTING
        android.util.Log.d("PhotoBeam.Transport", "[DIAG] [CONNECTION_START] Connecting to $host:$port via $transportId...")
        try {
            // TLS context — no cert verification (TOFU via fingerprint check externally)
            val ctx = javax.net.ssl.SSLContext.getInstance("TLSv1.3")
            val tm = arrayOf<javax.net.ssl.TrustManager>(object : javax.net.ssl.X509TrustManager {
                override fun checkClientTrusted(chain: Array<out java.security.cert.X509Certificate>?, authType: String?) {}
                override fun checkServerTrusted(chain: Array<out java.security.cert.X509Certificate>?, authType: String?) {}
                override fun getAcceptedIssuers(): Array<java.security.cert.X509Certificate> = emptyArray()
            })
            ctx.init(null, tm, java.security.SecureRandom())
            val factory = ctx.socketFactory
            val s = factory.createSocket() as SSLSocket
            s.tcpNoDelay = true
            s.keepAlive = true
            s.soTimeout = 60_000 // 60s adaptive timeout for handshake/control
            s.connect(java.net.InetSocketAddress(host, port), timeoutMs)
            socket = s
            outputStream = s.outputStream
            inputStream = s.inputStream
            status = Status.CONNECTED
            android.util.Log.d("PhotoBeam.Transport", "[DIAG] [CONNECTION_ESTABLISHED] Connected to $host:$port via $transportId")
        } catch (e: Exception) {
            status = Status.FAILED
            android.util.Log.e("PhotoBeam.Transport", "[DIAG] [CONNECTION_LOST] Connect failed to $host:$port: ${e.message}")
            throw e
        }
    }

    fun setSoTimeout(timeoutMs: Int) {
        try { socket?.soTimeout = timeoutMs } catch (_: Exception) {}
    }

    override fun disconnect() {
        status = Status.DISCONNECTED
        try { socket?.close() } catch (_: Exception) {}
        socket = null
        outputStream = null
        inputStream = null
        android.util.Log.d("PhotoBeam.Transport", "[DIAG] [CONNECTION_CLOSE] Transport $transportId disconnected")
    }

    override fun sendAll(data: ByteArray) {
        try {
            outputStream?.write(data) ?: throw IllegalStateException("Not connected")
            outputStream?.flush()
            recordSent(data.size.toLong())
        } catch (e: Exception) {
            status = Status.FAILED
            android.util.Log.e("PhotoBeam.Transport", "[DIAG] [CONNECTION_LOST] sendAll failed: ${e.message}")
            throw e
        }
    }

    override fun recvExact(n: Int): ByteArray {
        val buf = ByteArray(n)
        val stream = inputStream ?: throw IllegalStateException("Not connected")
        var read = 0
        try {
            while (read < n) {
                val r = stream.read(buf, read, n - read)
                if (r < 0) {
                    status = Status.DISCONNECTED
                    throw java.io.EOFException("Connection closed while receiving")
                }
                read += r
            }
            recordReceived(n.toLong())
            return buf
        } catch (e: Exception) {
            status = Status.FAILED
            android.util.Log.e("PhotoBeam.Transport", "[DIAG] [CONNECTION_LOST] recvExact($n) failed: ${e.message}")
            throw e
        }
    }

    companion object {
        /**
         * Create from an already-accepted SSLSocket (receiver/server side).
         */
        fun fromAcceptedSocket(socket: SSLSocket, transportId: String = "wifi"): WiFiTransport {
            val t = WiFiTransport(transportId)
            try {
                socket.tcpNoDelay = true
                socket.keepAlive = true
                socket.soTimeout = 3_600_000
            } catch (_: Exception) {}
            t.socket = socket
            t.outputStream = socket.outputStream
            t.inputStream = socket.inputStream
            t.status = Status.CONNECTED
            android.util.Log.d("PhotoBeam.Transport", "[DIAG] [CONNECTION_ESTABLISHED] Accepted socket wrapped via $transportId")
            return t
        }
    }
}
