package com.photobeam.app.protocol

import java.security.MessageDigest
import java.security.SecureRandom
import java.time.Instant

/**
 * SessionManager — Android Kotlin
 * Mirrors protocol/src/session.py
 */

data class Session(
    val sid: String,
    val rid: String,
    val token: String,
    val createdAt: Long,
    val expiresAt: Long,
    var state: SessionState = SessionState.PENDING,
    val certPem: ByteArray? = null,
    val certFp: String = "",
    val addrs: List<String> = emptyList(),
    val port: Int = DEFAULT_PORT,
    val transports: List<String> = listOf("wifi"),
    var senderId: String? = null,
) {
    fun isExpired(): Boolean = Instant.now().epochSecond > expiresAt

    fun isValidToken(token: String): Boolean {
        // Constant-time comparison
        val a = this.token.toByteArray()
        val b = token.toByteArray()
        if (a.size != b.size) return false
        var diff = 0
        for (i in a.indices) diff = diff or (a[i].toInt() xor b[i].toInt())
        return diff == 0
    }
}

class SessionManager(val deviceId: String) {

    private val sessions = mutableMapOf<String, Session>()

    @Synchronized
    fun createSession(
        addrs: List<String>,
        port: Int = DEFAULT_PORT,
        transports: List<String> = listOf("wifi"),
        expirySeconds: Long = QR_EXPIRY_SECONDS,
        certPem: ByteArray? = null,
        certFp: String = "",
    ): Session {
        purgeExpired()
        val now = Instant.now().epochSecond
        val session = Session(
            sid = java.util.UUID.randomUUID().toString(),
            rid = deviceId,
            token = generateSessionToken(),
            createdAt = now,
            expiresAt = now + expirySeconds,
            addrs = addrs,
            port = port,
            transports = transports,
            certPem = certPem,
            certFp = certFp,
        )
        sessions[session.sid] = session
        return session
    }

    @Synchronized
    fun getSession(sid: String): Session? = sessions[sid]

    @Synchronized
    fun validateHello(sid: String, token: String): Pair<Boolean, String> {
        val session = sessions[sid] ?: return Pair(false, "unknown_session")
        if (session.isExpired()) {
            session.state = SessionState.EXPIRED
            return Pair(false, "session_expired")
        }
        if (!session.isValidToken(token)) return Pair(false, "invalid_token")
        if (session.state !in listOf(SessionState.PENDING, SessionState.PAUSED)) {
            return Pair(false, "invalid_state:${session.state}")
        }
        return Pair(true, "")
    }

    @Synchronized
    fun markConnected(sid: String, senderId: String) {
        sessions[sid]?.let { it.state = SessionState.CONNECTED; it.senderId = senderId }
    }

    @Synchronized
    fun markTransferring(sid: String) {
        sessions[sid]?.state = SessionState.TRANSFERRING
    }

    @Synchronized
    fun markCompleted(sid: String) {
        sessions[sid]?.state = SessionState.COMPLETED
    }

    @Synchronized
    fun markCancelled(sid: String) {
        sessions[sid]?.state = SessionState.CANCELLED
    }

    fun buildQrPayload(session: Session): QRPayload = QRPayload(
        v = PROTOCOL_VERSION,
        sid = session.sid,
        rid = session.rid,
        addrs = session.addrs,
        port = session.port,
        transports = session.transports,
        token = session.token,
        exp = session.expiresAt,
        certFp = session.certFp,
    )

    private fun purgeExpired() {
        val cutoff = Instant.now().epochSecond - 3600
        sessions.entries.removeIf { it.value.expiresAt < cutoff }
    }
}
