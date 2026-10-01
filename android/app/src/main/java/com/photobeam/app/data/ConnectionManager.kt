package com.photobeam.app.data

import android.content.Context
import android.net.wifi.WifiManager
import android.os.Build
import android.os.PowerManager
import android.util.Log
import com.photobeam.app.protocol.Capability
import com.photobeam.app.protocol.ConnectionState
import com.photobeam.app.protocol.DeviceEndpoint
import com.photobeam.app.protocol.DeviceIdentity
import com.photobeam.app.protocol.PairedDevice
import com.photobeam.app.protocol.PairingPayload
import com.photobeam.app.protocol.PresenceState
import com.photobeam.app.protocol.TrustStatus
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.io.BufferedReader
import java.io.InputStreamReader
import java.net.InetSocketAddress
import java.net.ServerSocket
import java.net.Socket
import java.util.concurrent.ConcurrentHashMap

/**
 * ConnectionManager — Android connection orchestrator.
 * Coordinates pairing, local discovery, and active connection lifecycle with:
 * - Persistent control server socket (port 47474)
 * - Symmetric PING / PONG resilient heartbeat (5s interval, 18s timeout)
 * - Duplicate connection suppression
 * - Exponential backoff reconnection
 * - Background WakeLock and WifiLock management
 */
class ConnectionManager private constructor(private val appContext: Context) {

    private val tag = "ConnectionManager"
    private val scope = CoroutineScope(Dispatchers.IO + Job())

    private val pairingManager = PairingManager.getInstance(appContext)
    private val discoveryService = DiscoveryService.getInstance(appContext)

    private val _pairedDevicesFlow = MutableStateFlow<List<PairedDevice>>(emptyList())
    val pairedDevicesFlow: StateFlow<List<PairedDevice>> = _pairedDevicesFlow.asStateFlow()

    private val activeSessions = ConcurrentHashMap<String, ActiveSession>()
    private val backoffIntervals = ConcurrentHashMap<String, Long>() // device_id -> delay ms
    private val backoffTimers = ConcurrentHashMap<String, Long>()    // device_id -> next_retry_timestamp
    private val connectingDevices = ConcurrentHashMap.newKeySet<String>()

    private val powerManager by lazy { appContext.getSystemService(Context.POWER_SERVICE) as PowerManager }
    private val wifiManager by lazy { appContext.applicationContext.getSystemService(Context.WIFI_SERVICE) as WifiManager }
    private var wakeLock: PowerManager.WakeLock? = null
    private var wifiLock: WifiManager.WifiLock? = null

    private var serverJob: Job? = null
    private var reconnectJob: Job? = null
    private var serverSocket: ServerSocket? = null
    private var isRunning = false

    companion object {
        const val CONTROL_PORT = 47470
        const val CONTROL_USB_PORT = 47471

        @Volatile
        private var instance: ConnectionManager? = null

        fun getInstance(context: Context): ConnectionManager {
            return instance ?: synchronized(this) {
                instance ?: ConnectionManager(context.applicationContext).also { instance = it }
            }
        }
    }

    init {
        refreshDevicesList()
    }

    fun start() {
        if (isRunning) return
        isRunning = true
        discoveryService.start()
        startServer()
        startAutoReconnect()
        refreshDevicesList()
        Log.i(tag, "ConnectionManager started with persistent control server & auto-reconnect")
    }

    fun stop() {
        if (!isRunning) return
        isRunning = false
        discoveryService.stop()
        reconnectJob?.cancel()
        serverJob?.cancel()
        try {
            serverSocket?.close()
        } catch (e: Exception) {}
        serverSocket = null

        for ((_, session) in activeSessions) {
            session.close("manager_stop")
        }
        activeSessions.clear()
        releaseLocks()
        Log.i(tag, "ConnectionManager stopped")
    }

    fun refreshDevicesList() {
        _pairedDevicesFlow.value = pairingManager.getPairedDevices()
    }

    // ── Persistent Control Server (Port 47474) ───────────────────────────────

    private fun startServer() {
        serverJob = scope.launch {
            try {
                val srv = ServerSocket()
                srv.reuseAddress = true
                srv.bind(InetSocketAddress(CONTROL_PORT))
                serverSocket = srv
                Log.i(tag, "[DIAG] [SERVER_STARTED] Control server listening on 0.0.0.0:$CONTROL_PORT")

                while (isActive && isRunning) {
                    val clientSock = try {
                        srv.accept()
                    } catch (e: Exception) {
                        if (!isRunning) break
                        delay(200)
                        continue
                    }

                    scope.launch {
                        handleIncomingConnection(clientSock)
                    }
                }
            } catch (e: Exception) {
                Log.w(tag, "[DIAG] [SERVER_ERROR] Could not start control server on $CONTROL_PORT: ${e.message}")
            }
        }
    }

    private fun handleIncomingConnection(clientSock: Socket) {
        try {
            clientSock.soTimeout = 5000
            val reader = BufferedReader(InputStreamReader(clientSock.inputStream, Charsets.UTF_8))
            val line = reader.readLine() ?: run {
                clientSock.close()
                return
            }

            val msg = JSONObject(line.trim())
            if (msg.optString("type") != "HELLO") {
                clientSock.close()
                return
            }

            val remoteId = msg.optString("device_id")
            if (remoteId.isEmpty()) {
                clientSock.close()
                return
            }

            val paired = pairingManager.getPairedDevice(remoteId)
            if (paired == null || paired.identity.trustStatus != TrustStatus.TRUSTED) {
                Log.w(tag, "[DIAG] [HANDSHAKE_REJECTED] Device $remoteId not trusted or unknown")
                clientSock.close()
                return
            }

            // Duplicate connection suppression
            val existing = activeSessions[remoteId]
            if (existing != null && existing.isRunning && (System.currentTimeMillis() - existing.lastPongTime < 15000L)) {
                Log.i(tag, "[DIAG] [DUPLICATE_SUPPRESSED] Active healthy session already exists for $remoteId, closing incoming socket")
                clientSock.close()
                return
            }

            existing?.close("replaced_by_incoming")

            // Send HELLO_ACK
            val localId = pairingManager.getLocalIdentity()
            val ack = JSONObject().apply {
                put("type", "HELLO_ACK")
                put("device_id", localId.deviceId)
                put("name", localId.name)
                put("version", 1)
                put("ts", System.currentTimeMillis())
            }
            val ackBytes = (ack.toString() + "\n").toByteArray(Charsets.UTF_8)
            clientSock.outputStream.write(ackBytes)
            clientSock.outputStream.flush()

            val remoteIp = clientSock.inetAddress.hostAddress ?: ""
            val transportType = if (remoteIp.startsWith("127.")) "usb" else "wifi"
            val session = ActiveSession(clientSock, remoteId, transportType, scope, ::onSessionClosed, tag)
            activeSessions[remoteId] = session
            session.start()

            acquireLocks()
            pairingManager.updateConnectionState(remoteId, ConnectionState.CONNECTED)
            backoffIntervals[remoteId] = 1000L
            backoffTimers[remoteId] = 0L

            Log.i(tag, "[DIAG] [HANDSHAKE_OK] Accepted connection from ${paired.identity.name} ($remoteId via $transportType)")
            refreshDevicesList()

        } catch (e: Exception) {
            Log.d(tag, "Error handling incoming connection: ${e.message}")
            try { clientSock.close() } catch (_: Exception) {}
        }
    }

    private fun onSessionClosed(session: ActiveSession, reason: String) {
        val deviceId = session.deviceId
        synchronized(activeSessions) {
            val current = activeSessions[deviceId]
            if (current !== session) {
                Log.d(tag, "[DIAG] Stale session closed for $deviceId (reason=$reason), ignoring")
                return
            }
            activeSessions.remove(deviceId)
        }
        Log.i(tag, "[DIAG] [DISCONNECTED] Active session closed for $deviceId (reason=$reason)")

        if (activeSessions.isEmpty()) {
            releaseLocks()
        }

        val dev = pairingManager.getPairedDevice(deviceId)
        if (dev != null && dev.connectionState == ConnectionState.CONNECTED) {
            pairingManager.updateConnectionState(deviceId, ConnectionState.DISCONNECTED)
            val interval = backoffIntervals[deviceId] ?: 1000L
            backoffIntervals[deviceId] = (interval * 2).coerceAtMost(30000L)
            backoffTimers[deviceId] = System.currentTimeMillis() + interval
            refreshDevicesList()
        }
    }

    // ── Pairing Workflow ──────────────────────────────────────────────────────

    fun pairWithPayload(
        payload: PairingPayload,
        onSuccess: (PairedDevice) -> Unit,
        onError: (String) -> Unit
    ) {
        scope.launch {
            try {
                if (payload.isExpired()) {
                    onError("Pairing QR code has expired. Please refresh the QR code.")
                    return@launch
                }

                val peerIdentity = DeviceIdentity(
                    deviceId = payload.rid,
                    name = payload.deviceName,
                    publicKey = payload.devicePublicKey,
                    createdAt = System.currentTimeMillis() / 1000,
                    lastSeen = System.currentTimeMillis() / 1000,
                    trustStatus = TrustStatus.TRUSTED,
                    capabilities = payload.capabilities.mapNotNull {
                        try { Capability.valueOf(it.uppercase()) } catch (e: Exception) { null }
                    }
                )

                val endpoint = DeviceEndpoint(
                    addrs = payload.addrs,
                    port = payload.port,
                    transports = payload.transports,
                    certFp = payload.certFp,
                    updatedAt = System.currentTimeMillis() / 1000
                )

                val pairedDevice = PairedDevice(
                    identity = peerIdentity,
                    endpoint = endpoint,
                    connectionState = ConnectionState.DISCONNECTED,
                    presenceState = PresenceState.DISCOVERED,
                )

                pairingManager.savePairedDevice(pairedDevice)
                refreshDevicesList()
                withContext(Dispatchers.Main) {
                    try { onSuccess(pairedDevice) } catch (e: Exception) { Log.e(tag, "Error in onSuccess", e) }
                }
            } catch (e: Exception) {
                Log.e(tag, "Error pairing device: ${e.message}", e)
                withContext(Dispatchers.Main) {
                    try { onError(e.message ?: "Pairing failed") } catch (ex: Exception) { Log.e(tag, "Error in onError", ex) }
                }
            }
        }
    }

    // ── Connection Lifecycle ──────────────────────────────────────────────────

    fun connectDevice(
        deviceId: String,
        onConnected: (() -> Unit)? = null,
        onFailed: ((String) -> Unit)? = null
    ) {
        scope.launch {
            // Duplicate connection suppression
            val existing = activeSessions[deviceId]
            if (existing != null && existing.isRunning && (System.currentTimeMillis() - existing.lastPongTime < 15000L)) {
                Log.i(tag, "[DIAG] [DUPLICATE_SUPPRESSED] Already connected to $deviceId")
                notifyConnected(onConnected)
                return@launch
            }

            if (!connectingDevices.add(deviceId)) {
                Log.d(tag, "[DIAG] [DUPLICATE_SUPPRESSED] Connection attempt already in flight for $deviceId")
                return@launch
            }

            try {
                val dev = pairingManager.getPairedDevice(deviceId)
                if (dev == null) {
                    notifyFailed(onFailed, "Device not found")
                    return@launch
                }
                if (dev.identity.trustStatus != TrustStatus.TRUSTED) {
                    pairingManager.updateConnectionState(deviceId, ConnectionState.AUTHENTICATION_REQUIRED)
                    refreshDevicesList()
                    notifyFailed(onFailed, "Device trust was revoked. Re-pairing is required.")
                    return@launch
                }

                Log.i(tag, "[DIAG] [CONNECTING] Connecting to device ${dev.identity.name} ($deviceId)...")
                pairingManager.updateConnectionState(deviceId, ConnectionState.CONNECTING)
                pairingManager.recordConnectionAttempt(deviceId)
                refreshDevicesList()

                val endpoint = dev.endpoint
                if (endpoint == null || endpoint.addrs.isEmpty()) {
                    pairingManager.updateConnectionState(deviceId, ConnectionState.DISCONNECTED)
                    refreshDevicesList()
                    notifyFailed(onFailed, "Device endpoint is unknown. Waiting for local discovery.")
                    return@launch
                }

                var connectedSock: Socket? = null
                var transportType = "wifi"

                // 1. Try USB tunnel (127.0.0.1:47471 via ADB reverse) first if configured
                try {
                    val s = Socket()
                    s.tcpNoDelay = true
                    s.keepAlive = true
                    s.connect(InetSocketAddress("127.0.0.1", CONTROL_USB_PORT), 1500)
                    if (performHandshake(s, deviceId)) {
                        connectedSock = s
                        transportType = "usb"
                        Log.i(tag, "[DIAG] [CONNECT_OK] Connected via USB tunnel to $deviceId")
                    } else {
                        s.close()
                    }
                } catch (e: Exception) {
                    Log.d(tag, "USB tunnel connect attempt failed: ${e.message}")
                }

                // 2. Try Wi-Fi addresses if USB not connected
                if (connectedSock == null) {
                    val targetPort = if (endpoint.port != 0 && endpoint.port != 47474) endpoint.port else CONTROL_PORT
                    for (addr in endpoint.addrs) {
                        if (addr.startsWith("127.")) continue
                        try {
                            val s = Socket()
                            s.tcpNoDelay = true
                            s.keepAlive = true
                            s.connect(InetSocketAddress(addr, targetPort), 2500)
                            if (performHandshake(s, deviceId)) {
                                connectedSock = s
                                transportType = "wifi"
                                Log.i(tag, "[DIAG] [CONNECT_OK] Connected via Wi-Fi to $deviceId ($addr:$targetPort)")
                                break
                            } else {
                                s.close()
                            }
                        } catch (e: Exception) {
                            Log.d(tag, "Wi-Fi connect attempt to $addr:$targetPort failed: ${e.message}")
                        }
                    }
                }

                if (connectedSock != null) {
                    val prev = activeSessions.remove(deviceId)
                    prev?.close("replaced_by_outgoing")

                    val session = ActiveSession(connectedSock, deviceId, transportType, scope, ::onSessionClosed, tag)
                    activeSessions[deviceId] = session
                    session.start()

                    acquireLocks()
                    pairingManager.updateConnectionState(deviceId, ConnectionState.CONNECTED)
                    backoffIntervals[deviceId] = 1000L
                    backoffTimers[deviceId] = 0L
                    refreshDevicesList()

                    Log.i(tag, "[DIAG] [HANDSHAKE_OK] Outgoing connection active for ${dev.identity.name}")
                    notifyConnected(onConnected)
                } else {
                    if (isDeviceConnected(deviceId)) {
                        Log.i(tag, "[DIAG] [CONNECT_CONCURRENT] Outgoing attempt failed but active session exists for $deviceId")
                    } else {
                        pairingManager.updateConnectionState(deviceId, ConnectionState.DISCONNECTED)
                        val interval = backoffIntervals[deviceId] ?: 1000L
                        backoffIntervals[deviceId] = (interval * 2).coerceAtMost(30000L)
                        backoffTimers[deviceId] = System.currentTimeMillis() + interval
                        refreshDevicesList()

                        Log.i(tag, "[DIAG] [DISCONNECTED] Could not connect to ${dev.identity.name}, retry in ${interval}ms")
                        notifyFailed(onFailed, "Could not establish connection to device endpoints")
                    }
                }
            } finally {
                connectingDevices.remove(deviceId)
            }
        }
    }

    private suspend fun notifyConnected(cb: (() -> Unit)?) {
        if (cb == null) return
        withContext(Dispatchers.Main) {
            try { cb() } catch (e: Exception) { Log.e(tag, "Error in onConnected callback", e) }
        }
    }

    private suspend fun notifyFailed(cb: ((String) -> Unit)?, msg: String) {
        if (cb == null) return
        withContext(Dispatchers.Main) {
            try { cb(msg) } catch (e: Exception) { Log.e(tag, "Error in onFailed callback", e) }
        }
    }

    private fun performHandshake(sock: Socket, expectedPeerId: String): Boolean {
        return try {
            val localId = pairingManager.getLocalIdentity()
            val hello = JSONObject().apply {
                put("type", "HELLO")
                put("device_id", localId.deviceId)
                put("name", localId.name)
                put("version", 1)
                put("ts", System.currentTimeMillis())
            }
            val helloBytes = (hello.toString() + "\n").toByteArray(Charsets.UTF_8)
            sock.outputStream.write(helloBytes)
            sock.outputStream.flush()

            sock.soTimeout = 3000
            val reader = BufferedReader(InputStreamReader(sock.inputStream, Charsets.UTF_8))
            val line = reader.readLine() ?: return false
            val ack = JSONObject(line.trim())
            ack.optString("type") == "HELLO_ACK" && ack.optString("device_id") == expectedPeerId
        } catch (e: Exception) {
            false
        }
    }

    fun disconnectDevice(deviceId: String) {
        val session = activeSessions.remove(deviceId)
        if (session != null) {
            try {
                val bye = JSONObject().apply {
                    put("type", "DISCONNECT")
                    put("reason", "user_action")
                }
                session.sendMsg(bye)
            } catch (e: Exception) {}
            session.close("user_action")
        }

        if (activeSessions.isEmpty()) {
            releaseLocks()
        }

        pairingManager.updateConnectionState(deviceId, ConnectionState.DISCONNECTED)
        refreshDevicesList()
    }

    fun forgetDevice(deviceId: String) {
        disconnectDevice(deviceId)
        pairingManager.removePairedDevice(deviceId)
        refreshDevicesList()
    }

    fun revokeTrust(deviceId: String) {
        disconnectDevice(deviceId)
        pairingManager.revokeTrust(deviceId)
        refreshDevicesList()
    }

    fun renameDevice(deviceId: String, newName: String) {
        pairingManager.updateDeviceName(deviceId, newName)
        refreshDevicesList()
    }

    fun isDeviceConnected(deviceId: String): Boolean {
        return activeSessions[deviceId]?.isRunning == true
    }

    fun getActiveTransports(deviceId: String): List<String> {
        val s = activeSessions[deviceId]
        return if (s != null && s.isRunning) listOf(s.transportType) else emptyList()
    }

    private fun startAutoReconnect() {
        reconnectJob = scope.launch {
            while (isActive && isRunning) {
                delay(2000L)
                val devices = pairingManager.getPairedDevices()
                val now = System.currentTimeMillis()

                for (dev in devices) {
                    val id = dev.identity.deviceId
                    if (dev.identity.trustStatus != TrustStatus.TRUSTED) continue

                    val session = activeSessions[id]
                    if (session != null && session.isRunning) {
                        if (dev.connectionState != ConnectionState.CONNECTED) {
                            pairingManager.updateConnectionState(id, ConnectionState.CONNECTED)
                            refreshDevicesList()
                        }
                        continue
                    }

                    if (dev.connectionState == ConnectionState.CONNECTED) {
                        if (session == null || !session.isRunning) {
                            disconnectDevice(id)
                        }
                        continue
                    }

                    if (dev.presenceState == PresenceState.DISCOVERED && dev.connectionState == ConnectionState.DISCONNECTED) {
                        val retryTime = backoffTimers[id] ?: 0L
                        if (now >= retryTime && !connectingDevices.contains(id)) {
                            connectDevice(id)
                        }
                    }
                }
            }
        }
    }

    // ── WakeLock & WifiLock Management ───────────────────────────────────────

    private fun acquireLocks() {
        try {
            if (wakeLock == null) {
                wakeLock = powerManager.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "PhotoBeam:ConnectionWakeLock").apply {
                    setReferenceCounted(false)
                }
            }
            if (wakeLock?.isHeld != true) {
                wakeLock?.acquire(30 * 60 * 1000L) // 30 min max safety guard
                Log.d(tag, "[DIAG] [LOCK_ACQUIRED] Partial WakeLock acquired")
            }
        } catch (e: Exception) {
            Log.w(tag, "Failed to acquire WakeLock: ${e.message}")
        }

        try {
            if (wifiLock == null) {
                val mode = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                    WifiManager.WIFI_MODE_FULL_LOW_LATENCY
                } else {
                    WifiManager.WIFI_MODE_FULL_HIGH_PERF
                }
                wifiLock = wifiManager.createWifiLock(mode, "PhotoBeam:ConnectionWifiLock").apply {
                    setReferenceCounted(false)
                }
            }
            if (wifiLock?.isHeld != true) {
                wifiLock?.acquire()
                Log.d(tag, "[DIAG] [LOCK_ACQUIRED] WifiLock acquired")
            }
        } catch (e: Exception) {
            Log.w(tag, "Failed to acquire WifiLock: ${e.message}")
        }
    }

    private fun releaseLocks() {
        try {
            if (wakeLock?.isHeld == true) {
                wakeLock?.release()
                Log.d(tag, "[DIAG] [LOCK_RELEASED] Partial WakeLock released")
            }
        } catch (e: Exception) {}
        try {
            if (wifiLock?.isHeld == true) {
                wifiLock?.release()
                Log.d(tag, "[DIAG] [LOCK_RELEASED] WifiLock released")
            }
        } catch (e: Exception) {}
    }

    // ── ActiveSession Inner Class ─────────────────────────────────────────────

    private class ActiveSession(
        val socket: Socket,
        val deviceId: String,
        val transportType: String,
        val scope: CoroutineScope,
        val onClosed: (ActiveSession, String) -> Unit,
        val tag: String
    ) {
        @Volatile var isRunning = true
        @Volatile var lastPongTime = System.currentTimeMillis()
        @Volatile var missedPongs = 0
        private val sendLock = Any()
        private var readerJob: Job? = null
        private var heartbeatJob: Job? = null

        fun start() {
            try {
                socket.tcpNoDelay = true
                socket.keepAlive = true
                socket.soTimeout = 2000
            } catch (e: Exception) {}

            readerJob = scope.launch(Dispatchers.IO) { readerLoop() }
            heartbeatJob = scope.launch(Dispatchers.IO) { heartbeatLoop() }
        }

        fun sendMsg(msg: JSONObject): Boolean {
            if (!isRunning) return false
            return synchronized(sendLock) {
                try {
                    val bytes = (msg.toString() + "\n").toByteArray(Charsets.UTF_8)
                    socket.outputStream.write(bytes)
                    socket.outputStream.flush()
                    true
                } catch (e: Exception) {
                    Log.w(tag, "[DIAG] [SEND_ERROR] Error sending to $deviceId: ${e.message}")
                    close("send_error")
                    false
                }
            }
        }

        fun close(reason: String = "normal") {
            if (!isRunning) return
            isRunning = false
            readerJob?.cancel()
            heartbeatJob?.cancel()
            try { socket.shutdownInput() } catch (e: Exception) {}
            try { socket.shutdownOutput() } catch (e: Exception) {}
            try { socket.close() } catch (e: Exception) {}
            onClosed(this, reason)
        }

        private suspend fun heartbeatLoop() {
            while (isRunning && scope.isActive) {
                delay(5000L)
                if (!isRunning || !scope.isActive) break
                val now = System.currentTimeMillis()
                if (now - lastPongTime > 18000L) {
                    Log.w(tag, "[DIAG] [HEARTBEAT_TIMEOUT] Missed pongs for >18s from $deviceId, closing socket")
                    close("heartbeat_timeout")
                    break
                }
                missedPongs++
                Log.d(tag, "[DIAG] [PING_SENT] Ping sent to $deviceId (missed=$missedPongs)")
                val ping = JSONObject().apply {
                    put("type", "PING")
                    put("ts", now)
                }
                if (!sendMsg(ping)) break
            }
        }

        private suspend fun readerLoop() {
            val reader = try {
                BufferedReader(InputStreamReader(socket.inputStream, Charsets.UTF_8))
            } catch (e: Exception) {
                close("stream_error")
                return
            }

            while (isRunning && scope.isActive) {
                val line: String? = try {
                    reader.readLine()
                } catch (e: java.net.SocketTimeoutException) {
                    continue
                } catch (e: Exception) {
                    if (isRunning) {
                        Log.i(tag, "[DIAG] [SOCKET_ERROR] Read error from $deviceId: ${e.message}")
                        close("socket_error")
                    }
                    break
                }

                if (line == null) {
                    Log.i(tag, "[DIAG] [SOCKET_CLOSED_REMOTE] Socket closed by remote peer $deviceId")
                    close("remote_closed")
                    break
                }

                val trimmed = line.trim()
                if (trimmed.isEmpty()) continue
                try {
                    val obj = JSONObject(trimmed)
                    handleMsg(obj)
                } catch (e: Exception) {
                    Log.d(tag, "Failed parsing JSON from $deviceId: $trimmed")
                }
            }
        }

        private fun handleMsg(msg: JSONObject) {
            val type = msg.optString("type")
            val now = System.currentTimeMillis()
            when (type) {
                "PING" -> {
                    Log.d(tag, "[DIAG] [PING_RCVD] Received ping from $deviceId")
                    val pong = JSONObject().apply {
                        put("type", "PONG")
                        put("ts", now)
                    }
                    sendMsg(pong)
                    Log.d(tag, "[DIAG] [PONG_SENT] Sent pong to $deviceId")
                }
                "PONG" -> {
                    lastPongTime = now
                    missedPongs = 0
                    Log.d(tag, "[DIAG] [PONG_RCVD] Received pong from $deviceId")
                }
                "DISCONNECT" -> {
                    Log.i(tag, "[DIAG] [DISCONNECTED] Peer requested disconnect $deviceId")
                    close("peer_disconnect")
                }
            }
        }
    }
}
