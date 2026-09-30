package com.photobeam.app.data

import android.content.Context
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
import java.net.InetSocketAddress
import java.net.Socket
import java.util.concurrent.ConcurrentHashMap

/**
 * ConnectionManager — Android connection orchestrator.
 * Coordinates pairing, local discovery, and active connection lifecycle.
 */
class ConnectionManager private constructor(private val appContext: Context) {

    private val tag = "ConnectionManager"
    private val scope = CoroutineScope(Dispatchers.IO + Job())

    private val pairingManager = PairingManager.getInstance(appContext)
    private val discoveryService = DiscoveryService.getInstance(appContext)

    private val _pairedDevicesFlow = MutableStateFlow<List<PairedDevice>>(emptyList())
    val pairedDevicesFlow: StateFlow<List<PairedDevice>> = _pairedDevicesFlow.asStateFlow()

    private val activeSockets = ConcurrentHashMap<String, Socket>()
    private val backoffIntervals = ConcurrentHashMap<String, Long>() // device_id -> delay ms

    private var reconnectJob: Job? = null
    private var isRunning = false

    companion object {
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
        startAutoReconnect()
        refreshDevicesList()
        Log.i(tag, "ConnectionManager started")
    }

    fun stop() {
        if (!isRunning) return
        isRunning = false
        discoveryService.stop()
        reconnectJob?.cancel()
        for ((_, sock) in activeSockets) {
            try {
                sock.close()
            } catch (e: Exception) {}
        }
        activeSockets.clear()
        Log.i(tag, "ConnectionManager stopped")
    }

    fun refreshDevicesList() {
        _pairedDevicesFlow.value = pairingManager.getPairedDevices()
    }

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
                onSuccess(pairedDevice)
            } catch (e: Exception) {
                Log.e(tag, "Error pairing device: ${e.message}", e)
                onError(e.message ?: "Pairing failed")
            }
        }
    }

    fun connectDevice(
        deviceId: String,
        onConnected: (() -> Unit)? = null,
        onFailed: ((String) -> Unit)? = null
    ) {
        scope.launch {
            val dev = pairingManager.getPairedDevice(deviceId)
            if (dev == null) {
                onFailed?.invoke("Device not found")
                return@launch
            }
            if (dev.identity.trustStatus != TrustStatus.TRUSTED) {
                pairingManager.updateConnectionState(deviceId, ConnectionState.AUTHENTICATION_REQUIRED)
                refreshDevicesList()
                onFailed?.invoke("Device trust was revoked. Re-pairing is required.")
                return@launch
            }

            pairingManager.updateConnectionState(deviceId, ConnectionState.CONNECTING)
            pairingManager.recordConnectionAttempt(deviceId)
            refreshDevicesList()

            val endpoint = dev.endpoint
            if (endpoint == null || endpoint.addrs.isEmpty()) {
                pairingManager.updateConnectionState(deviceId, ConnectionState.DISCONNECTED)
                refreshDevicesList()
                onFailed?.invoke("Device endpoint is unknown. Waiting for local discovery.")
                return@launch
            }

            var connectedSocket: Socket? = null
            for (addr in endpoint.addrs) {
                try {
                    val socket = Socket()
                    socket.connect(InetSocketAddress(addr, endpoint.port), 3000)
                    if (socket.isConnected) {
                        connectedSocket = socket
                        break
                    }
                } catch (e: Exception) {
                    // Try next address
                }
            }

            if (connectedSocket != null) {
                activeSockets[deviceId]?.close()
                activeSockets[deviceId] = connectedSocket
                pairingManager.updateConnectionState(deviceId, ConnectionState.CONNECTED)
                backoffIntervals[deviceId] = 1000L
                refreshDevicesList()
                onConnected?.invoke()
            } else {
                pairingManager.updateConnectionState(deviceId, ConnectionState.DISCONNECTED)
                refreshDevicesList()
                onFailed?.invoke("Could not establish connection to device endpoints")
            }
        }
    }

    fun disconnectDevice(deviceId: String) {
        try {
            activeSockets.remove(deviceId)?.close()
        } catch (e: Exception) {}
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

    private fun startAutoReconnect() {
        reconnectJob = scope.launch {
            while (isActive && isRunning) {
                delay(2000L)
                val devices = pairingManager.getPairedDevices()
                for (dev in devices) {
                    val id = dev.identity.deviceId
                    if (dev.identity.trustStatus != TrustStatus.TRUSTED) continue
                    if (dev.connectionState == ConnectionState.CONNECTED) {
                        // Check if still alive
                        val sock = activeSockets[id]
                        if (sock == null || sock.isClosed || !sock.isConnected) {
                            disconnectDevice(id)
                        }
                        continue
                    }

                    if (dev.presenceState == PresenceState.DISCOVERED && dev.connectionState == ConnectionState.DISCONNECTED) {
                        val interval = backoffIntervals[id] ?: 1000L
                        backoffIntervals[id] = (interval * 2).coerceAtMost(30000L)
                        connectDevice(id)
                    }
                }
            }
        }
    }
}
