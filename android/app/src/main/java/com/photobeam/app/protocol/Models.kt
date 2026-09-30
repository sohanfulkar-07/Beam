package com.photobeam.app.protocol

import org.json.JSONArray
import org.json.JSONObject
import java.security.SecureRandom

/**
 * PhotoBeam Protocol Models — Kotlin
 *
 * Mirrors protocol/src/models.py
 */

const val PROTOCOL_VERSION = 1
const val CHUNK_MAGIC = "PBMC"
val CHUNK_MAGIC_BYTES = byteArrayOf(0x50, 0x42, 0x4D, 0x43)
const val DEFAULT_PORT = 47474
const val DEFAULT_USB_PORT = 47475
const val DEFAULT_CHUNK_SIZE = 4 * 1024 * 1024 // 4 MB
const val QR_EXPIRY_SECONDS = 86400L // 24 hours (no short expiration)
const val CHUNK_HEADER_SIZE = 68
const val URI_SCHEME = "photobeam://connect/"

enum class SessionState { PENDING, CONNECTED, TRANSFERRING, PAUSING, PAUSED, VERIFYING, RESUMING, COMPLETED, CANCELLED, EXPIRED, ERROR }
enum class TransferState { PENDING, ACTIVE, PAUSING, PAUSED, VERIFYING, RESUMING, COMPLETED, FAILED, CANCELLED }
enum class ChunkState { PENDING, IN_FLIGHT, RECEIVED, FAILED }
enum class TransportType { WIFI, USB }

enum class TrustStatus { UNPAIRED, PENDING_APPROVAL, TRUSTED, REVOKED }
enum class PresenceState { UNKNOWN, SEARCHING, DISCOVERED, UNAVAILABLE }
enum class ConnectionState { DISCONNECTED, CONNECTING, CONNECTED, RECONNECTING, AUTHENTICATION_REQUIRED }
enum class Capability { FILE_TRANSFER, SCREEN_MIRROR_SEND, SCREEN_MIRROR_RECEIVE, SECOND_DISPLAY }

object MessageType {
    const val HELLO = "HELLO"
    const val HELLO_ACK = "HELLO_ACK"
    const val READY = "READY"
    const val ACCEPT = "ACCEPT"
    const val REJECT = "REJECT"
    const val ACK_CHUNK = "ACK_CHUNK"
    const val NAK_CHUNK = "NAK_CHUNK"
    const val FILE_CHECKSUM = "FILE_CHECKSUM"
    const val FILE_DONE = "FILE_DONE"
    const val FILE_ERROR = "FILE_ERROR"
    const val RESUME_STATE = "RESUME_STATE"
    const val RESUME = "RESUME"
    const val PAUSE = "PAUSE"
    const val PAUSE_ACK = "PAUSE_ACK"
    const val RESUME_ACK = "RESUME_ACK"
    const val CANCEL = "CANCEL"
    const val PING = "PING"
    const val PONG = "PONG"
    const val ERROR = "ERROR"
    const val STORAGE_ERROR = "STORAGE_ERROR"
}

// ── QR Payload ───────────────────────────────────────────────────────────────

data class QRPayload(
    val v: Int,
    val sid: String,
    val rid: String,
    val addrs: List<String>,
    val port: Int,
    val transports: List<String>,
    val token: String,
    val exp: Long,
    val certFp: String,
) {
    fun isExpired(): Boolean = System.currentTimeMillis() / 1000 > exp

    fun toJson(): JSONObject = JSONObject().apply {
        put("v", v)
        put("sid", sid)
        put("rid", rid)
        put("addrs", JSONArray(addrs))
        put("port", port)
        put("transports", JSONArray(transports))
        put("token", token)
        put("exp", exp)
        put("cert_fp", certFp)
    }

    companion object {
        fun fromJson(obj: JSONObject) = QRPayload(
            v = obj.getInt("v"),
            sid = obj.getString("sid"),
            rid = obj.getString("rid"),
            addrs = (0 until obj.getJSONArray("addrs").length()).map { obj.getJSONArray("addrs").getString(it) },
            port = obj.getInt("port"),
            transports = (0 until obj.getJSONArray("transports").length()).map { obj.getJSONArray("transports").getString(it) },
            token = obj.getString("token"),
            exp = obj.getLong("exp"),
            certFp = obj.getString("cert_fp"),
        )
    }
}

// ── QR encode/decode ─────────────────────────────────────────────────────────

fun encodeQrPayload(payload: QRPayload): String {
    val json = payload.toJson().toString()
    val b64 = java.util.Base64.getUrlEncoder().withoutPadding().encodeToString(json.toByteArray(Charsets.UTF_8))
    return URI_SCHEME + b64
}

fun decodeQrPayload(uri: String): QRPayload {
    require(uri.startsWith(URI_SCHEME)) { "Not a PhotoBeam URI: $uri" }
    val b64 = uri.removePrefix(URI_SCHEME)
    val json = String(java.util.Base64.getUrlDecoder().decode(b64), Charsets.UTF_8)
    val obj = JSONObject(json)
    require(obj.getInt("v") <= PROTOCOL_VERSION) { "Unsupported protocol version: ${obj.getInt("v")}" }
    return QRPayload.fromJson(obj)
}

// ── Transfer Info ─────────────────────────────────────────────────────────────

data class TransferInfo(
    val fid: String,
    val name: String,
    val relPath: String,
    val size: Long,
    val chunkSize: Int,
    val totalChunks: Int,
    val sha256: String,
) {
    fun toJson(): JSONObject = JSONObject().apply {
        put("fid", fid)
        put("name", name)
        put("rel_path", relPath)
        put("size", size)
        put("chunk_size", chunkSize)
        put("total_chunks", totalChunks)
        put("sha256", sha256)
    }

    companion object {
        fun fromJson(obj: JSONObject) = TransferInfo(
            fid = obj.getString("fid"),
            name = obj.getString("name"),
            relPath = obj.getString("rel_path"),
            size = obj.getLong("size"),
            chunkSize = obj.getInt("chunk_size"),
            totalChunks = obj.getInt("total_chunks"),
            sha256 = obj.optString("sha256", ""),
        )
    }
}

// ── Chunk Frame ───────────────────────────────────────────────────────────────

data class ChunkFrame(
    val transferId: ByteArray, // 16 bytes
    val fileId: ByteArray,     // 16 bytes
    val chunkId: Int,
    val offset: Long,
    val data: ByteArray,
    val checksum: Long,        // XXH3-64 as unsigned 64-bit
    val frameType: Int = 0x0001,
    val version: Int = PROTOCOL_VERSION,
) {
    fun encode(): ByteArray {
        val chunkLen = data.size
        val payloadLen = 56 + chunkLen  // 16+16+4+8+4+8 = 56, then data
        val buf = java.nio.ByteBuffer.allocate(CHUNK_HEADER_SIZE + chunkLen).apply {
            order(java.nio.ByteOrder.BIG_ENDIAN)
            put(CHUNK_MAGIC_BYTES)               // 4
            putShort(version.toShort())           // 2
            putShort(frameType.toShort())         // 2
            putInt(payloadLen)                    // 4
            put(transferId)                       // 16
            put(fileId)                           // 16
            putInt(chunkId)                       // 4
            putLong(offset)                       // 8
            putInt(chunkLen)                      // 4
            putLong(checksum)                     // 8
            put(data)                             // N
        }
        return buf.array()
    }

    companion object {
        fun decode(buf: ByteArray): ChunkFrame {
            require(buf.size >= CHUNK_HEADER_SIZE) { "Buffer too short: ${buf.size} < $CHUNK_HEADER_SIZE" }
            val bb = java.nio.ByteBuffer.wrap(buf).order(java.nio.ByteOrder.BIG_ENDIAN)
            val magic = ByteArray(4).also { bb.get(it) }
            require(magic.contentEquals(CHUNK_MAGIC_BYTES)) { "Bad magic: ${magic.toHex()}" }
            val version = bb.short.toInt()
            val frameType = bb.short.toInt()
            val payloadLen = bb.int
            val transferId = ByteArray(16).also { bb.get(it) }
            val fileId = ByteArray(16).also { bb.get(it) }
            val chunkId = bb.int
            val offset = bb.long
            val chunkLen = bb.int
            val checksum = bb.long
            require(buf.size >= CHUNK_HEADER_SIZE + chunkLen) {
                "Truncated chunk data: got ${buf.size - CHUNK_HEADER_SIZE}, expected $chunkLen"
            }
            val data = ByteArray(chunkLen).also { bb.get(it) }
            return ChunkFrame(
                transferId = transferId,
                fileId = fileId,
                chunkId = chunkId,
                offset = offset,
                data = data,
                checksum = checksum,
                frameType = frameType,
                version = version,
            )
        }
    }

    override fun equals(other: Any?) = other is ChunkFrame && chunkId == other.chunkId && fileId.contentEquals(other.fileId)
    override fun hashCode() = 31 * chunkId + fileId.contentHashCode()
}

private fun ByteArray.toHex() = joinToString("") { "%02x".format(it) }

// ── Session token generation ──────────────────────────────────────────────────

fun generateSessionToken(): String {
    val bytes = ByteArray(32)
    SecureRandom().nextBytes(bytes)
    return bytes.joinToString("") { "%02x".format(it) }
}

// ── Device Identity & Pairing Models ──────────────────────────────────────────

data class DeviceIdentity(
    val deviceId: String,
    val name: String,
    val publicKey: String,           // base64-encoded Ed25519 public key
    val createdAt: Long,
    val lastSeen: Long,
    val trustStatus: TrustStatus = TrustStatus.UNPAIRED,
    val appVersion: String = "",
    val protocolVersion: Int = PROTOCOL_VERSION,
    val capabilities: List<Capability> = emptyList(),
) {
    fun toJson(): JSONObject = JSONObject().apply {
        put("device_id", deviceId)
        put("name", name)
        put("public_key", publicKey)
        put("created_at", createdAt)
        put("last_seen", lastSeen)
        put("trust_status", trustStatus.name)
        put("app_version", appVersion)
        put("protocol_version", protocolVersion)
        put("capabilities", JSONArray(capabilities.map { it.name }))
    }

    companion object {
        fun fromJson(obj: JSONObject) = DeviceIdentity(
            deviceId = obj.getString("device_id"),
            name = obj.getString("name"),
            publicKey = obj.getString("public_key"),
            createdAt = obj.getLong("created_at"),
            lastSeen = obj.getLong("last_seen"),
            trustStatus = TrustStatus.valueOf(obj.optString("trust_status", "UNPAIRED")),
            appVersion = obj.optString("app_version", ""),
            protocolVersion = obj.optInt("protocol_version", PROTOCOL_VERSION),
            capabilities = (0 until obj.optJSONArray("capabilities")?.length() ?: 0)
                .map { Capability.valueOf(obj.getJSONArray("capabilities").getString(it)) },
        )
    }
}

data class DeviceEndpoint(
    val addrs: List<String>,
    val port: Int,
    val transports: List<String>,
    val certFp: String,
    val updatedAt: Long,
) {
    fun toJson(): JSONObject = JSONObject().apply {
        put("addrs", JSONArray(addrs))
        put("port", port)
        put("transports", JSONArray(transports))
        put("cert_fp", certFp)
        put("updated_at", updatedAt)
    }

    companion object {
        fun fromJson(obj: JSONObject) = DeviceEndpoint(
            addrs = (0 until obj.getJSONArray("addrs").length()).map { obj.getJSONArray("addrs").getString(it) },
            port = obj.getInt("port"),
            transports = (0 until obj.getJSONArray("transports").length()).map { obj.getJSONArray("transports").getString(it) },
            certFp = obj.getString("cert_fp"),
            updatedAt = obj.getLong("updated_at"),
        )
    }
}

data class PairedDevice(
    val identity: DeviceIdentity,
    val endpoint: DeviceEndpoint? = null,
    val connectionState: ConnectionState = ConnectionState.DISCONNECTED,
    val presenceState: PresenceState = PresenceState.UNKNOWN,
    val lastConnectionAttempt: Long = 0,
    val lastSuccessfulConnection: Long = 0,
) {
    fun toJson(): JSONObject = JSONObject().apply {
        put("identity", identity.toJson())
        put("endpoint", endpoint?.toJson())
        put("connection_state", connectionState.name)
        put("presence_state", presenceState.name)
        put("last_connection_attempt", lastConnectionAttempt)
        put("last_successful_connection", lastSuccessfulConnection)
    }

    companion object {
        fun fromJson(obj: JSONObject) = PairedDevice(
            identity = DeviceIdentity.fromJson(obj.getJSONObject("identity")),
            endpoint = obj.optJSONObject("endpoint")?.let { DeviceEndpoint.fromJson(it) },
            connectionState = ConnectionState.valueOf(obj.optString("connection_state", "DISCONNECTED")),
            presenceState = PresenceState.valueOf(obj.optString("presence_state", "UNKNOWN")),
            lastConnectionAttempt = obj.optLong("last_connection_attempt", 0),
            lastSuccessfulConnection = obj.optLong("last_successful_connection", 0),
        )
    }
}

data class PairingPayload(
    // Base QR fields
    val v: Int,
    val sid: String,
    val rid: String,
    val addrs: List<String>,
    val port: Int,
    val transports: List<String>,
    val token: String,
    val exp: Long,
    val certFp: String,
    // Pairing-specific fields
    val deviceName: String,
    val devicePublicKey: String,
    val capabilities: List<String>,
    val pairingNonce: String,        // base64, 32 bytes
) {
    fun isExpired(): Boolean = System.currentTimeMillis() / 1000 > exp

    fun toJson(): JSONObject = JSONObject().apply {
        put("v", v)
        put("sid", sid)
        put("rid", rid)
        put("addrs", JSONArray(addrs))
        put("port", port)
        put("transports", JSONArray(transports))
        put("token", token)
        put("exp", exp)
        put("cert_fp", certFp)
        put("device_name", deviceName)
        put("device_public_key", devicePublicKey)
        put("capabilities", JSONArray(capabilities))
        put("pairing_nonce", pairingNonce)
    }

    companion object {
        fun fromJson(obj: JSONObject) = PairingPayload(
            v = obj.getInt("v"),
            sid = obj.getString("sid"),
            rid = obj.getString("rid"),
            addrs = (0 until obj.getJSONArray("addrs").length()).map { obj.getJSONArray("addrs").getString(it) },
            port = obj.getInt("port"),
            transports = (0 until obj.getJSONArray("transports").length()).map { obj.getJSONArray("transports").getString(it) },
            token = obj.getString("token"),
            exp = obj.getLong("exp"),
            certFp = obj.getString("cert_fp"),
            deviceName = obj.getString("device_name"),
            devicePublicKey = obj.getString("device_public_key"),
            capabilities = (0 until obj.getJSONArray("capabilities").length()).map { obj.getJSONArray("capabilities").getString(it) },
            pairingNonce = obj.getString("pairing_nonce"),
        )
    }
}

const val PAIR_URI_SCHEME = "photobeam://pair/"

fun encodePairingPayload(payload: PairingPayload): String {
    val json = payload.toJson().toString()
    val b64 = java.util.Base64.getUrlEncoder().withoutPadding().encodeToString(json.toByteArray(Charsets.UTF_8))
    return PAIR_URI_SCHEME + b64
}

fun decodePairingPayload(uri: String): PairingPayload {
    require(uri.startsWith(PAIR_URI_SCHEME)) { "Not a PhotoBeam pairing URI: $uri" }
    val b64 = uri.removePrefix(PAIR_URI_SCHEME)
    val json = String(java.util.Base64.getUrlDecoder().decode(b64), Charsets.UTF_8)
    val obj = JSONObject(json)
    require(obj.getInt("v") <= PROTOCOL_VERSION) { "Unsupported protocol version: ${obj.getInt("v")}" }
    return PairingPayload.fromJson(obj)
}
