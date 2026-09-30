package com.photobeam.app.data

import android.content.Context
import android.net.nsd.NsdManager
import android.net.nsd.NsdServiceInfo
import android.net.wifi.WifiManager
import android.os.Build
import android.util.Log
import com.photobeam.app.protocol.DeviceEndpoint
import com.photobeam.app.protocol.PresenceState
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import org.json.JSONArray
import org.json.JSONObject
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetAddress
import java.net.MulticastSocket
import java.net.NetworkInterface
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.Collections
import java.util.concurrent.ConcurrentHashMap

/**
 * DiscoveryService — Local discovery on Android.
 * Integrates Android NsdManager (mDNS) and PhotoBeam Multicast/Broadcast (224.0.0.251:5353)
 * for seamless cross-platform peer discovery between Android and Windows.
 */
class DiscoveryService private constructor(private val context: Context) {

    private val tag = "PhotoBeamDiscovery"
    private val scope = CoroutineScope(Dispatchers.IO + Job())
    private var isRunning = false

    private val nsdManager by lazy { context.getSystemService(Context.NSD_SERVICE) as? NsdManager }
    private val wifiManager by lazy { context.applicationContext.getSystemService(Context.WIFI_SERVICE) as? WifiManager }
    private var multicastLock: WifiManager.MulticastLock? = null

    private var registrationListener: NsdManager.RegistrationListener? = null
    private var discoveryListener: NsdManager.DiscoveryListener? = null

    private var listenerJob: Job? = null
    private var announcerJob: Job? = null

    private val discoveredPeers = ConcurrentHashMap<String, DiscoveredPeerInfo>()

    data class DiscoveredPeerInfo(
        val deviceId: String,
        val name: String,
        val endpoint: DeviceEndpoint,
        val lastSeen: Long = System.currentTimeMillis() / 1000,
    )

    companion object {
        private const val SERVICE_TYPE = "_photobeam._tcp."
        private const val MULTICAST_GROUP = "224.0.0.251"
        private const val DISCOVERY_PORT = 5353
        private val MAGIC_HEADER = byteArrayOf(0x50, 0x42, 0x4D, 0x44) // PBMD

        @Volatile
        private var instance: DiscoveryService? = null

        fun getInstance(context: Context): DiscoveryService {
            return instance ?: synchronized(this) {
                instance ?: DiscoveryService(context.applicationContext).also { instance = it }
            }
        }
    }

    fun start() {
        if (isRunning) return
        isRunning = true

        try {
            multicastLock = wifiManager?.createMulticastLock("photobeam_multicast_lock")?.apply {
                setReferenceCounted(true)
                acquire()
            }
        } catch (e: Exception) {
            Log.w(tag, "Could not acquire MulticastLock: ${e.message}")
        }

        startNsd()
        startSocketDiscovery()
        Log.i(tag, "DiscoveryService started")
    }

    fun stop() {
        if (!isRunning) return
        isRunning = false

        stopNsd()
        listenerJob?.cancel()
        announcerJob?.cancel()

        try {
            if (multicastLock?.isHeld == true) {
                multicastLock?.release()
            }
        } catch (e: Exception) {
            Log.w(tag, "Error releasing MulticastLock: ${e.message}")
        }

        Log.i(tag, "DiscoveryService stopped")
    }

    private fun getLocalIpAddresses(): List<String> {
        val ips = mutableListOf<String>()
        try {
            val interfaces = Collections.list(NetworkInterface.getNetworkInterfaces())
            for (intf in interfaces) {
                if (intf.isLoopback || !intf.isUp) continue
                val addrs = Collections.list(intf.inetAddresses)
                for (addr in addrs) {
                    if (!addr.isLoopbackAddress && addr is java.net.Inet4Address) {
                        ips.add(addr.hostAddress)
                    }
                }
            }
        } catch (e: Exception) {
            Log.w(tag, "Error getting IP addresses: ${e.message}")
        }
        if (ips.isEmpty()) ips.add("127.0.0.1")
        return ips
    }

    // ── NSD Implementation ───────────────────────────────────────────────────

    private fun startNsd() {
        val pairingManager = PairingManager.getInstance(context)
        val localIdentity = pairingManager.getLocalIdentity()

        val serviceInfo = NsdServiceInfo().apply {
            serviceName = "PhotoBeam-${localIdentity.name}"
            serviceType = SERVICE_TYPE
            port = 47474
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.LOLLIPOP) {
                setAttribute("id", localIdentity.deviceId)
                setAttribute("name", localIdentity.name)
            }
        }

        registrationListener = object : NsdManager.RegistrationListener {
            override fun onServiceRegistered(info: NsdServiceInfo) {
                Log.d(tag, "NSD Service registered: ${info.serviceName}")
            }
            override fun onRegistrationFailed(info: NsdServiceInfo, code: Int) {
                Log.w(tag, "NSD registration failed: code=$code")
            }
            override fun onServiceUnregistered(info: NsdServiceInfo) {}
            override fun onUnregistrationFailed(info: NsdServiceInfo, code: Int) {}
        }

        discoveryListener = object : NsdManager.DiscoveryListener {
            override fun onDiscoveryStarted(regType: String) {
                Log.d(tag, "NSD Discovery started")
            }
            override fun onServiceFound(info: NsdServiceInfo) {
                if (info.serviceType.contains("photobeam")) {
                    nsdManager?.resolveService(info, object : NsdManager.ResolveListener {
                        override fun onResolveFailed(serviceInfo: NsdServiceInfo, code: Int) {
                            Log.w(tag, "NSD resolve failed: code=$code")
                        }
                        override fun onServiceResolved(serviceInfo: NsdServiceInfo) {
                            val host = serviceInfo.host?.hostAddress ?: return
                            val port = serviceInfo.port
                            val devId = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.LOLLIPOP) {
                                serviceInfo.attributes["id"]?.let { String(it, Charsets.UTF_8) } ?: serviceInfo.serviceName
                            } else serviceInfo.serviceName

                            handlePeerFound(
                                deviceId = devId,
                                name = serviceInfo.serviceName,
                                addrs = listOf(host),
                                port = port,
                                transports = listOf("wifi"),
                                certFp = ""
                            )
                        }
                    })
                }
            }
            override fun onServiceLost(info: NsdServiceInfo) {
                Log.d(tag, "NSD Service lost: ${info.serviceName}")
            }
            override fun onDiscoveryStopped(serviceType: String) {}
            override fun onStartDiscoveryFailed(serviceType: String, errorCode: Int) {}
            override fun onStopDiscoveryFailed(serviceType: String, errorCode: Int) {}
        }

        try {
            nsdManager?.registerService(serviceInfo, NsdManager.PROTOCOL_DNS_SD, registrationListener)
            nsdManager?.discoverServices(SERVICE_TYPE, NsdManager.PROTOCOL_DNS_SD, discoveryListener)
        } catch (e: Exception) {
            Log.w(tag, "NSD initialization failed: ${e.message}")
        }
    }

    private fun stopNsd() {
        try {
            registrationListener?.let { nsdManager?.unregisterService(it) }
            discoveryListener?.let { nsdManager?.stopServiceDiscovery(it) }
        } catch (e: Exception) {
            Log.w(tag, "Error stopping NSD: ${e.message}")
        }
    }

    // ── UDP Multicast / Broadcast Implementation ─────────────────────────────

    private fun buildAdvertisementPacket(): ByteArray {
        val pm = PairingManager.getInstance(context)
        val id = pm.getLocalIdentity()
        val json = JSONObject().apply {
            put("v", 1)
            put("id", id.deviceId)
            put("name", id.name)
            put("port", 47474)
            put("transports", JSONArray(listOf("wifi")))
            put("cert_fp", "")
            put("addrs", JSONArray(getLocalIpAddresses()))
            put("time", System.currentTimeMillis() / 1000)
        }
        val body = json.toString().toByteArray(Charsets.UTF_8)
        val buf = ByteBuffer.allocate(MAGIC_HEADER.size + 2 + body.size)
        buf.order(ByteOrder.BIG_ENDIAN)
        buf.put(MAGIC_HEADER)
        buf.putShort(body.size.toShort())
        buf.put(body)
        return buf.array()
    }

    private fun startSocketDiscovery() {
        // Announcer loop
        announcerJob = scope.launch {
            var burst = 0
            while (isActive && isRunning) {
                sendAnnouncement()
                burst++
                val delayMs = if (burst < 3) 1000L else 10000L
                kotlinx.coroutines.delay(delayMs)
            }
        }

        // Listener loop
        listenerJob = scope.launch {
            var socket: MulticastSocket? = null
            try {
                socket = MulticastSocket(DISCOVERY_PORT).apply {
                    reuseAddress = true
                    soTimeout = 2000
                    try {
                        val group = InetAddress.getByName(MULTICAST_GROUP)
                        joinGroup(group)
                    } catch (e: Exception) {
                        Log.w(tag, "Could not join multicast group: ${e.message}")
                    }
                }

                val buffer = ByteArray(4096)
                while (isActive && isRunning) {
                    try {
                        val packet = DatagramPacket(buffer, buffer.size)
                        socket.receive(packet)
                        parseAndHandlePacket(packet)
                    } catch (e: java.net.SocketTimeoutException) {
                        // Periodic check
                    } catch (e: Exception) {
                        if (!isRunning) break
                        kotlinx.coroutines.delay(500)
                    }
                }
            } catch (e: Exception) {
                Log.w(tag, "Socket discovery listener error: ${e.message}")
            } finally {
                try {
                    socket?.close()
                } catch (e: Exception) {}
            }
        }
    }

    private fun sendAnnouncement() {
        try {
            val packetData = buildAdvertisementPacket()
            val socket = DatagramSocket()
            socket.broadcast = true

            // Send to multicast
            try {
                val groupAddr = InetAddress.getByName(MULTICAST_GROUP)
                val mPacket = DatagramPacket(packetData, packetData.size, groupAddr, DISCOVERY_PORT)
                socket.send(mPacket)
            } catch (e: Exception) {}

            // Send to subnet broadcast
            try {
                val bcastAddr = InetAddress.getByName("255.255.255.255")
                val bPacket = DatagramPacket(packetData, packetData.size, bcastAddr, DISCOVERY_PORT)
                socket.send(bPacket)
            } catch (e: Exception) {}

            socket.close()
        } catch (e: Exception) {
            Log.d(tag, "Failed sending announcement: ${e.message}")
        }
    }

    private fun parseAndHandlePacket(packet: DatagramPacket) {
        val data = packet.data
        val len = packet.length
        if (len < MAGIC_HEADER.size + 2) return

        for (i in MAGIC_HEADER.indices) {
            if (data[i] != MAGIC_HEADER[i]) return
        }

        val buf = ByteBuffer.wrap(data, MAGIC_HEADER.size, 2).order(ByteOrder.BIG_ENDIAN)
        val bodyLen = buf.short.toInt() and 0xFFFF
        if (len < MAGIC_HEADER.size + 2 + bodyLen) return

        try {
            val jsonStr = String(data, MAGIC_HEADER.size + 2, bodyLen, Charsets.UTF_8)
            val obj = JSONObject(jsonStr)
            val peerId = obj.getString("id")

            val pm = PairingManager.getInstance(context)
            if (peerId == pm.getLocalIdentity().deviceId) return // Ignore self

            val name = obj.optString("name", "Unknown Peer")
            val port = obj.optInt("port", 47474)
            val certFp = obj.optString("cert_fp", "")

            val addrs = mutableListOf<String>()
            val addrsArray = obj.optJSONArray("addrs")
            if (addrsArray != null) {
                for (i in 0 until addrsArray.length()) {
                    addrs.add(addrsArray.getString(i))
                }
            }
            val senderIp = packet.address.hostAddress
            if (senderIp != null && !senderIp.startsWith("127.") && !addrs.contains(senderIp)) {
                addrs.add(0, senderIp)
            }

            val transports = mutableListOf<String>()
            val tArray = obj.optJSONArray("transports")
            if (tArray != null) {
                for (i in 0 until tArray.length()) {
                    transports.add(tArray.getString(i))
                }
            } else transports.add("wifi")

            handlePeerFound(peerId, name, addrs, port, transports, certFp)
        } catch (e: Exception) {
            Log.d(tag, "Failed parsing packet: ${e.message}")
        }
    }

    private fun handlePeerFound(
        deviceId: String,
        name: String,
        addrs: List<String>,
        port: Int,
        transports: List<String>,
        certFp: String,
    ) {
        val endpoint = DeviceEndpoint(
            addrs = addrs,
            port = port,
            transports = transports,
            certFp = certFp,
            updatedAt = System.currentTimeMillis() / 1000
        )

        discoveredPeers[deviceId] = DiscoveredPeerInfo(
            deviceId = deviceId,
            name = name,
            endpoint = endpoint,
        )

        val pm = PairingManager.getInstance(context)
        // If it's a known paired device, update its presence and endpoint
        if (pm.getPairedDevice(deviceId) != null) {
            pm.updateDeviceEndpoint(deviceId, endpoint)
            pm.updatePresenceState(deviceId, PresenceState.DISCOVERED)
        }
    }

    fun getDiscoveredPeers(): List<DiscoveredPeerInfo> = discoveredPeers.values.toList()

    fun getPeer(deviceId: String): DiscoveredPeerInfo? = discoveredPeers[deviceId]
}
