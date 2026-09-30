package com.photobeam.app.transport

import java.net.InetAddress
import java.net.NetworkInterface
import java.security.KeyPairGenerator
import java.security.KeyStore
import java.security.MessageDigest
import java.security.SecureRandom
import java.security.cert.X509Certificate
import java.util.Date
import javax.net.ssl.KeyManagerFactory
import javax.net.ssl.SSLContext
import javax.net.ssl.SSLServerSocket
import javax.net.ssl.SSLSocket

/**
 * TLS Server utilities for Android receiver.
 * Generates ephemeral self-signed cert using BouncyCastle.
 */
object TlsUtils {

    data class SessionCert(
        val certDer: ByteArray,
        val fingerprint: String,   // "sha256:<hex>"
        val sslContext: SSLContext,
    )

    /**
     * Generate ephemeral RSA-2048 self-signed cert.
     * Returns SessionCert with DER bytes, fingerprint, and configured SSLContext.
     */
    fun generateSessionCert(): SessionCert {
        val keyPair = KeyPairGenerator.getInstance("RSA").apply {
            initialize(2048, SecureRandom())
        }.generateKeyPair()

        // Use BouncyCastle to generate the cert
        val cert = generateSelfSignedCert(keyPair)
        val certDer = cert.encoded

        val fp = "sha256:" + MessageDigest.getInstance("SHA-256").digest(certDer)
            .joinToString("") { "%02x".format(it) }

        val ks = KeyStore.getInstance(KeyStore.getDefaultType()).apply {
            load(null, null)
            setKeyEntry("key", keyPair.private, "photobeam".toCharArray(), arrayOf(cert))
        }

        val kmf = KeyManagerFactory.getInstance(KeyManagerFactory.getDefaultAlgorithm()).apply {
            init(ks, "photobeam".toCharArray())
        }

        val ctx = SSLContext.getInstance("TLSv1.3").apply {
            init(kmf.keyManagers, null, SecureRandom())
        }

        return SessionCert(certDer = certDer, fingerprint = fp, sslContext = ctx)
    }

    private fun generateSelfSignedCert(keyPair: java.security.KeyPair): X509Certificate {
        // Use BouncyCastle
        val bcProvider = org.bouncycastle.jce.provider.BouncyCastleProvider()
        val name = org.bouncycastle.asn1.x500.X500Name("CN=photobeam")
        val now = Date()
        val expires = Date(now.time + 24L * 60 * 60 * 1000) // 1 day

        val certGen = org.bouncycastle.cert.jcajce.JcaX509v3CertificateBuilder(
            name,
            java.math.BigInteger.valueOf(SecureRandom().nextLong().and(Long.MAX_VALUE)),
            now,
            expires,
            name,
            keyPair.public,
        )

        val signer = org.bouncycastle.operator.jcajce.JcaContentSignerBuilder("SHA256WithRSA")
            .setProvider(bcProvider)
            .build(keyPair.private)

        return org.bouncycastle.cert.jcajce.JcaX509CertificateConverter()
            .setProvider(bcProvider)
            .getCertificate(certGen.build(signer))
    }

    fun getLocalAddresses(): List<String> {
        val result = mutableListOf<String>()
        NetworkInterface.getNetworkInterfaces()?.toList()?.forEach { iface ->
            if (iface.isLoopback || !iface.isUp) return@forEach
            iface.inetAddresses.toList().forEach { addr ->
                if (addr is java.net.Inet4Address && !addr.isLoopbackAddress) {
                    result.add(addr.hostAddress ?: return@forEach)
                }
            }
        }
        return result.ifEmpty { listOf("127.0.0.1") }
    }
}

/**
 * TLS Server — Android receiver side.
 */
class TlsServer(private val ctx: SSLContext, private val port: Int) {
    private var serverSocket: SSLServerSocket? = null

    fun start() {
        val factory = ctx.serverSocketFactory
        serverSocket = (factory.createServerSocket(port) as SSLServerSocket).also {
            it.enabledProtocols = arrayOf("TLSv1.3")
        }
    }

    fun accept(timeoutMs: Int = 300_000): SSLSocket {
        val ss = serverSocket ?: error("Server not started")
        ss.soTimeout = timeoutMs
        return ss.accept() as SSLSocket
    }

    fun stop() {
        try { serverSocket?.close() } catch (_: Exception) {}
        serverSocket = null
    }
}
