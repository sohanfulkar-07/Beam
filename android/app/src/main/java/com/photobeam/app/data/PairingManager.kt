package com.photobeam.app.data

import android.content.Context
import android.content.SharedPreferences
import android.os.Build
import android.util.Base64
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey
import com.photobeam.app.protocol.Capability
import com.photobeam.app.protocol.ConnectionState
import com.photobeam.app.protocol.DeviceEndpoint
import com.photobeam.app.protocol.DeviceIdentity
import com.photobeam.app.protocol.PROTOCOL_VERSION
import com.photobeam.app.protocol.PairedDevice
import com.photobeam.app.protocol.PresenceState
import com.photobeam.app.protocol.TrustStatus
import org.bouncycastle.crypto.generators.Ed25519KeyPairGenerator
import org.bouncycastle.crypto.params.Ed25519KeyGenerationParameters
import org.bouncycastle.crypto.params.Ed25519PrivateKeyParameters
import org.bouncycastle.crypto.params.Ed25519PublicKeyParameters
import org.bouncycastle.crypto.signers.Ed25519Signer
import org.json.JSONObject
import java.security.SecureRandom
import java.util.UUID
import java.util.concurrent.ConcurrentHashMap

/**
 * PairingManager — Android persistent storage and cryptographic identity manager.
 * Uses Android Keystore-backed EncryptedSharedPreferences for secrets (private keys)
 * and regular SharedPreferences for device pairings and endpoints.
 */
class PairingManager private constructor(private val appContext: Context) {

    private val prefsName = "photobeam_pairings_v2"
    private val secretsPrefsName = "photobeam_secrets_v2"

    private val regularPrefs: SharedPreferences by lazy {
        appContext.getSharedPreferences(prefsName, Context.MODE_PRIVATE)
    }

    private val secretsPrefs: SharedPreferences by lazy {
        try {
            val masterKey = MasterKey.Builder(appContext)
                .setKeyScheme(MasterKey.KeyScheme.AES256_GCM)
                .build()

            EncryptedSharedPreferences.create(
                appContext,
                secretsPrefsName,
                masterKey,
                EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
                EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM
            )
        } catch (e: Exception) {
            // Fallback for tests or devices where Keystore initialization fails
            appContext.getSharedPreferences("${secretsPrefsName}_fallback", Context.MODE_PRIVATE)
        }
    }

    private val cache = ConcurrentHashMap<String, PairedDevice>()

    companion object {
        @Volatile
        private var instance: PairingManager? = null

        fun getInstance(context: Context): PairingManager {
            return instance ?: synchronized(this) {
                instance ?: PairingManager(context.applicationContext).also { instance = it }
            }
        }

        /**
         * Generate a new Ed25519 keypair for device identity.
         * Returns (publicKeyBase64, privateKeyBase64).
         */
        fun generateEd25519KeyPair(): Pair<String, String> {
            val generator = Ed25519KeyPairGenerator()
            generator.init(Ed25519KeyGenerationParameters(SecureRandom()))
            val keyPair = generator.generateKeyPair()

            val privKey = keyPair.private as Ed25519PrivateKeyParameters
            val pubKey = keyPair.public as Ed25519PublicKeyParameters

            val privEncoded = Base64.encodeToString(privKey.encoded, Base64.NO_WRAP)
            val pubEncoded = Base64.encodeToString(pubKey.encoded, Base64.NO_WRAP)

            return Pair(pubEncoded, privEncoded)
        }

        /**
         * Sign a challenge payload with an Ed25519 private key.
         */
        fun signChallenge(privateKeyBase64: String, challenge: ByteArray): ByteArray {
            val privBytes = Base64.decode(privateKeyBase64, Base64.NO_WRAP)
            val privKeyParams = Ed25519PrivateKeyParameters(privBytes, 0)
            val signer = Ed25519Signer()
            signer.init(true, privKeyParams)
            signer.update(challenge, 0, challenge.size)
            return signer.generateSignature()
        }

        /**
         * Verify an Ed25519 signature against a challenge and public key.
         */
        fun verifySignature(publicKeyBase64: String, challenge: ByteArray, signature: ByteArray): Boolean {
            return try {
                val pubBytes = Base64.decode(publicKeyBase64, Base64.NO_WRAP)
                val pubKeyParams = Ed25519PublicKeyParameters(pubBytes, 0)
                val verifier = Ed25519Signer()
                verifier.init(false, pubKeyParams)
                verifier.update(challenge, 0, challenge.size)
                verifier.verifySignature(signature)
            } catch (e: Exception) {
                false
            }
        }
    }

    init {
        ensureLocalIdentity()
        loadCache()
    }

    /**
     * Get or create this device's permanent local identity.
     */
    fun getLocalIdentity(): DeviceIdentity {
        val deviceId = regularPrefs.getString("local_device_id", null)
        val name = regularPrefs.getString("local_device_name", null)
        val pubKey = regularPrefs.getString("local_public_key", null)
        val createdAt = regularPrefs.getLong("local_created_at", 0L)

        if (deviceId != null && name != null && pubKey != null && createdAt > 0L) {
            return DeviceIdentity(
                deviceId = deviceId,
                name = name,
                publicKey = pubKey,
                createdAt = createdAt,
                lastSeen = System.currentTimeMillis() / 1000,
                trustStatus = TrustStatus.TRUSTED,
                appVersion = "1.0.0",
                protocolVersion = PROTOCOL_VERSION,
                capabilities = listOf(
                    Capability.FILE_TRANSFER,
                    Capability.SCREEN_MIRROR_SEND,
                    Capability.SCREEN_MIRROR_RECEIVE,
                )
            )
        }

        return ensureLocalIdentity()
    }

    fun setLocalDeviceName(newName: String) {
        regularPrefs.edit().putString("local_device_name", newName).apply()
    }

    fun getLocalPrivateKey(): String? {
        val deviceId = regularPrefs.getString("local_device_id", null) ?: return null
        return secretsPrefs.getString("privkey_$deviceId", null)
    }

    private fun ensureLocalIdentity(): DeviceIdentity {
        val existingId = regularPrefs.getString("local_device_id", null)
        if (existingId != null) {
            val name = regularPrefs.getString("local_device_name", Build.MODEL ?: "Android Device")
            val pubKey = regularPrefs.getString("local_public_key", "") ?: ""
            val createdAt = regularPrefs.getLong("local_created_at", System.currentTimeMillis() / 1000)
            return DeviceIdentity(
                deviceId = existingId,
                name = name ?: "Android Device",
                publicKey = pubKey,
                createdAt = createdAt,
                lastSeen = System.currentTimeMillis() / 1000,
                trustStatus = TrustStatus.TRUSTED,
                appVersion = "1.0.0",
                protocolVersion = PROTOCOL_VERSION,
                capabilities = listOf(
                    Capability.FILE_TRANSFER,
                    Capability.SCREEN_MIRROR_SEND,
                    Capability.SCREEN_MIRROR_RECEIVE,
                )
            )
        }

        // Generate brand new stable identity
        val newDeviceId = UUID.randomUUID().toString()
        val defaultName = Build.MODEL ?: "Android Device"
        val now = System.currentTimeMillis() / 1000
        val (pubKey, privKey) = generateEd25519KeyPair()

        regularPrefs.edit()
            .putString("local_device_id", newDeviceId)
            .putString("local_device_name", defaultName)
            .putString("local_public_key", pubKey)
            .putLong("local_created_at", now)
            .apply()

        secretsPrefs.edit()
            .putString("privkey_$newDeviceId", privKey)
            .apply()

        return DeviceIdentity(
            deviceId = newDeviceId,
            name = defaultName,
            publicKey = pubKey,
            createdAt = now,
            lastSeen = now,
            trustStatus = TrustStatus.TRUSTED,
            appVersion = "1.0.0",
            protocolVersion = PROTOCOL_VERSION,
            capabilities = listOf(
                Capability.FILE_TRANSFER,
                Capability.SCREEN_MIRROR_SEND,
                Capability.SCREEN_MIRROR_RECEIVE,
            )
        )
    }

    private fun loadCache() {
        cache.clear()
        try {
            val all = regularPrefs.all
            for ((key, value) in all) {
                if (key.startsWith("device_") && value is String) {
                    try {
                        val obj = JSONObject(value)
                        val device = PairedDevice.fromJson(obj)
                        cache[device.identity.deviceId] = device
                    } catch (e: Exception) {
                        // Skip corrupted entries safely
                    }
                }
            }
        } catch (e: Exception) {
            // Ignore corrupted storage
        }
    }

    fun savePairedDevice(device: PairedDevice) {
        val key = "device_${device.identity.deviceId}"
        val jsonStr = device.toJson().toString()
        regularPrefs.edit().putString(key, jsonStr).apply()
        cache[device.identity.deviceId] = device
    }

    fun getPairedDevices(): List<PairedDevice> {
        return cache.values.toList().sortedByDescending { it.lastSuccessfulConnection }
    }

    fun getPairedDevice(deviceId: String): PairedDevice? {
        return cache[deviceId]
    }

    fun updateDeviceEndpoint(deviceId: String, endpoint: DeviceEndpoint) {
        cache[deviceId]?.let { existing ->
            val updated = existing.copy(endpoint = endpoint)
            savePairedDevice(updated)
        }
    }

    fun updateDeviceName(deviceId: String, newName: String) {
        cache[deviceId]?.let { existing ->
            val updatedIdentity = existing.identity.copy(name = newName)
            val updated = existing.copy(identity = updatedIdentity)
            savePairedDevice(updated)
        }
    }

    fun updateConnectionState(deviceId: String, state: ConnectionState) {
        cache[deviceId]?.let { existing ->
            val updated = existing.copy(connectionState = state)
            if (state == ConnectionState.CONNECTED) {
                val now = System.currentTimeMillis() / 1000
                val identity = existing.identity.copy(lastSeen = now)
                savePairedDevice(updated.copy(identity = identity, lastSuccessfulConnection = now))
            } else {
                savePairedDevice(updated)
            }
        }
    }

    fun updatePresenceState(deviceId: String, state: PresenceState) {
        cache[deviceId]?.let { existing ->
            savePairedDevice(existing.copy(presenceState = state))
        }
    }

    fun recordConnectionAttempt(deviceId: String) {
        cache[deviceId]?.let { existing ->
            val now = System.currentTimeMillis() / 1000
            savePairedDevice(existing.copy(lastConnectionAttempt = now))
        }
    }

    /**
     * Forget: Removes local relationship only.
     */
    fun removePairedDevice(deviceId: String) {
        val key = "device_$deviceId"
        regularPrefs.edit().remove(key).apply()
        secretsPrefs.edit().remove("token_$deviceId").apply()
        cache.remove(deviceId)
    }

    /**
     * Revoke: Invalidate local trust.
     */
    fun revokeTrust(deviceId: String) {
        cache[deviceId]?.let { existing ->
            val identity = existing.identity.copy(trustStatus = TrustStatus.REVOKED)
            val updated = existing.copy(
                identity = identity,
                connectionState = ConnectionState.DISCONNECTED
            )
            savePairedDevice(updated)
            secretsPrefs.edit().remove("token_$deviceId").apply()
        }
    }

    fun isTrusted(deviceId: String): Boolean {
        return cache[deviceId]?.identity?.trustStatus == TrustStatus.TRUSTED
    }

    fun clearAll() {
        regularPrefs.edit().clear().apply()
        secretsPrefs.edit().clear().apply()
        cache.clear()
        ensureLocalIdentity()
    }
}