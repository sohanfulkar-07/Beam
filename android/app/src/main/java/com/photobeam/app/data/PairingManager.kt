package com.photobeam.app.data

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKeys
import com.photobeam.app.protocol.DeviceIdentity
import com.photobeam.app.protocol.PairedDevice
import com.photobeam.app.protocol.DeviceEndpoint
import com.google.gson.Gson
import com.google.gson.reflect.TypeToken
import java.lang.reflect.Type
import java.security.KeyStore
import java.util.concurrent.ConcurrentHashMap

/**
 * PairingManager — Android persistent storage for device pairings.
 * Uses Android Keystore-backed EncryptedSharedPreferences for secrets
 * and regular SharedPreferences for non-sensitive metadata.
 */
class PairingManager private constructor(context: Context) {

    private val prefsName = "photobeam_pairings"
    private val masterKeyAlias = "photobeam_master_key"

    // Encrypted prefs for sensitive data (private keys, tokens)
    private val encryptedPrefs by lazy {
        val masterKey = MasterKeys.getOrCreate(KeyGenParameterSpec.Builder(
            masterKeyAlias,
            KeyProperties.KEY_ALGORITHM_AES
        ).apply {
            setBlockModes(KeyProperties.BLOCK_MODE_GCM)
            setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
            setKeySize(256)
            setUserAuthenticationRequired(false)
        }.build())

        EncryptedSharedPreferences.create(
            prefsName,
            masterKey,
            context,
            EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
            EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM
        )
    }

    // Regular prefs for non-sensitive metadata (device names, endpoints, capabilities)
    private val regularPrefs by lazy {
        context.getSharedPreferences("${prefsName}_meta", Context.MODE_PRIVATE)
    }

    private val gson = Gson()
    private val pairedDeviceType = object : TypeToken<PairedDevice>() {}.type
    private val deviceIdentityType = object : TypeToken<DeviceIdentity>() {}.type
    private val deviceEndpointType = object : TypeToken<DeviceEndpoint>() {}.type

    // In-memory cache for fast access
    private val cache = ConcurrentHashMap<String, PairedDevice>()

    companion object {
        @Volatile
        private var instance: PairingManager? = null

        fun getInstance(context: Context): PairingManager {
            return instance ?: synchronized(this) {
                instance ?: PairingManager(context.applicationContext).also { instance = it }
            }
        }
    }

    init {
        loadCache()
    }

    private fun loadCache() {
        try {
            val all = regularPrefs.all
            for ((key, value) in all) {
                if (key.startsWith("device_") && value is String) {
                    try {
                        val device = gson.fromJson(value, pairedDeviceType)
                        cache[device.identity.deviceId] = device
                    } catch (e: Exception) {
                        // Skip corrupted entries
                    }
                }
            }
        } catch (e: Exception) {
            // Ignore load errors, start with empty cache
        }
    }

    /**
     * Save or update a paired device.
     * Private identity keys are stored in encrypted prefs.
     * Metadata stored in regular prefs.
     */
    fun savePairedDevice(device: PairedDevice) {
        val key = "device_${device.identity.deviceId}"
        val json = gson.toJson(device)

        regularPrefs.edit().putString(key, json).apply()
        cache[device.identity.deviceId] = device
    }

    /**
     * Get all paired devices.
     */
    fun getPairedDevices(): List<PairedDevice> {
        return cache.values.toList()
            .sortedByDescending { it.lastSuccessfulConnection }
    }

    /**
     * Get a specific paired device by device ID.
     */
    fun getPairedDevice(deviceId: String): PairedDevice? {
        return cache[deviceId]
    }

    /**
     * Update the endpoint (addresses, port, cert fingerprint) for a device.
     */
    fun updateDeviceEndpoint(deviceId: String, endpoint: DeviceEndpoint) {
        cache[deviceId]?.let { existing ->
            val updated = existing.copy(endpoint = endpoint)
            savePairedDevice(updated)
        }
    }

    /**
     * Update connection state for a device.
     */
    fun updateConnectionState(deviceId: String, state: com.photobeam.app.protocol.ConnectionState) {
        cache[deviceId]?.let { existing ->
            val updated = existing.copy(connectionState = state)
            if (state == com.photobeam.app.protocol.ConnectionState.CONNECTED) {
                val now = System.currentTimeMillis() / 1000
                val identity = existing.identity.copy(lastSeen = now)
                savePairedDevice(updated.copy(identity = identity, lastSuccessfulConnection = now))
            } else {
                savePairedDevice(updated)
            }
        }
    }

    /**
     * Update presence state for a device.
     */
    fun updatePresenceState(deviceId: String, state: com.photobeam.app.protocol.PresenceState) {
        cache[deviceId]?.let { existing ->
            savePairedDevice(existing.copy(presenceState = state))
        }
    }

    /**
     * Record a connection attempt.
     */
    fun recordConnectionAttempt(deviceId: String) {
        cache[deviceId]?.let { existing ->
            val now = System.currentTimeMillis() / 1000
            savePairedDevice(existing.copy(lastConnectionAttempt = now))
        }
    }

    /**
     * Remove a paired device (Forget).
     * Does NOT notify the other device.
     */
    fun removePairedDevice(deviceId: String) {
        val key = "device_$deviceId"
        regularPrefs.edit().remove(key).apply()
        // Also remove any stored private key for this device
        encryptedPrefs.edit().remove("private_key_$deviceId").apply()
        cache.remove(deviceId)
    }

    /**
     * Revoke trust for a device.
     * Marks local trust as revoked. Remote revocation requires protocol exchange.
     */
    fun revokeTrust(deviceId: String) {
        cache[deviceId]?.let { existing ->
            val identity = existing.identity.copy(trustStatus = com.photobeam.app.protocol.TrustStatus.REVOKED)
            savePairedDevice(existing.copy(identity = identity))
        }
    }

    /**
     * Check if a device is trusted (mutual trust established).
     */
    fun isTrusted(deviceId: String): Boolean {
        return cache[deviceId]?.identity?.trustStatus == com.photobeam.app.protocol.TrustStatus.TRUSTED
    }

    /**
     * Store a private identity key for this device (used for signing challenges).
     */
    fun storePrivateKey(deviceId: String, privateKeyBase64: String) {
        encryptedPrefs.edit().putString("private_key_$deviceId", privateKeyBase64).apply()
    }

    /**
     * Retrieve the private identity key for this device.
     */
    fun getPrivateKey(deviceId: String): String? {
        return encryptedPrefs.getString("private_key_$deviceId", null)
    }

    /**
     * Generate a new Ed25519 identity key pair for this device.
     * Returns (publicKeyBase64, privateKeyBase64).
     */
    companion object {
        @Suppress("UNUSED_PARAMETER")
        fun generateIdentityKeyPair(): Pair<String, String> {
            // TODO: Implement actual Ed25519 key generation using BouncyCastle or Android Keystore
            // For now, return placeholder - actual implementation needs crypto library
            val keyPair = java.security.KeyPairGenerator.getInstance("Ed25519").apply {
                initialize(256, java.security.SecureRandom())
            }.generateKeyPair()

            val publicKey = android.util.Base64.encodeToString(keyPair.public.encoded, android.util.Base64.NO_WRAP)
            val privateKey = android.util.Base64.encodeToString(keyPair.private.encoded, android.util.Base64.NO_WRAP)
            return Pair(publicKey, privateKey)
        }
    }

    /**
     * Sign a challenge with the device's private identity key.
     */
    fun signChallenge(deviceId: String, challenge: ByteArray): ByteArray? {
        val privateKeyB64 = getPrivateKey(deviceId) ?: return null
        val privateKeyBytes = android.util.Base64.decode(privateKeyB64, android.util.Base64.NO_WRAP)

        // TODO: Implement actual Ed25519 signing using BouncyCastle
        // For now, placeholder
        return ByteArray(64) // Ed25519 signature is 64 bytes
    }

    /**
     * Verify a signature with a device's public identity key.
     */
    companion object {
        @Suppress("UNUSED_PARAMETER")
        fun verifySignature(publicKeyBase64: String, challenge: ByteArray, signature: ByteArray): Boolean {
            // TODO: Implement actual Ed25519 verification using BouncyCastle
            // For now, placeholder
            return true
        }
    }

    /**
     * Clear all pairings (for testing or reset).
     */
    fun clearAll() {
        regularPrefs.edit().clear().apply()
        encryptedPrefs.edit().clear().apply()
        cache.clear()
    }
}