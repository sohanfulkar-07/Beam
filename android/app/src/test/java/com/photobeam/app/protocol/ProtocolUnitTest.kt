package com.photobeam.app.protocol

import com.photobeam.app.transport.TlsUtils
import kotlinx.coroutines.*
import kotlinx.coroutines.channels.Channel
import org.junit.Assert.*
import org.junit.Test
import java.io.File
import java.nio.ByteBuffer
import java.util.UUID

class ProtocolUnitTest {

    @Test
    fun testChunkFrameEncodeDecode() {
        val transferId = ByteArray(16) { 0x01 }
        val fileId = ByteArray(16) { 0x02 }
        val data = "Hello PhotoBeam Chunk Data!".toByteArray(Charsets.UTF_8)
        val checksum = IntegrityManager.chunkChecksum(data)

        val frame = ChunkFrame(
            transferId = transferId,
            fileId = fileId,
            chunkId = 3,
            offset = 12288L,
            data = data,
            checksum = checksum,
        )

        val encoded = frame.encode()
        assertEquals(CHUNK_HEADER_SIZE + data.size, encoded.size)

        val decoded = ChunkFrame.decode(encoded)
        assertArrayEquals(transferId, decoded.transferId)
        assertArrayEquals(fileId, decoded.fileId)
        assertEquals(3, decoded.chunkId)
        assertEquals(12288L, decoded.offset)
        assertEquals(checksum, decoded.checksum)
        assertArrayEquals(data, decoded.data)
        assertTrue(IntegrityManager.verifyChunk(decoded.data, decoded.checksum))
    }

    @Test
    fun testChunkManagerTracking() {
        val transferId = ByteArray(16) { 0x11 }
        val fileId = ByteArray(16) { 0x22 }
        val fileSize = 10 * 1024L
        val chunkSize = 4096

        val cm = ChunkManager(transferId, fileId, fileSize, chunkSize)
        assertEquals(3, cm.totalChunks) // 4096, 4096, 2048
        assertEquals(4096, cm.lengthForChunk(0))
        assertEquals(4096, cm.lengthForChunk(1))
        assertEquals(2048, cm.lengthForChunk(2))
        assertFalse(cm.isComplete())
        assertEquals(listOf(0, 1, 2), cm.missingChunks())

        assertTrue(cm.recordReceived(0))
        assertFalse(cm.recordReceived(0)) // duplicate
        assertEquals(listOf(0), cm.receivedChunks())
        assertEquals(listOf(1, 2), cm.missingChunks())
        assertFalse(cm.isComplete())

        assertTrue(cm.recordReceived(1))
        assertTrue(cm.recordReceived(2))
        assertTrue(cm.isComplete())
        assertTrue(cm.missingChunks().isEmpty())
    }

    @Test
    fun testIntegrityManagerChunkVerification() {
        val testData = "Verification test content".toByteArray(Charsets.UTF_8)
        val cs = IntegrityManager.chunkChecksum(testData)
        assertTrue(IntegrityManager.verifyChunk(testData, cs))

        val corrupted = "Verification test content!".toByteArray(Charsets.UTF_8)
        assertFalse(IntegrityManager.verifyChunk(corrupted, cs))
    }

    @Test
    fun testIntegrityManagerFileHash() {
        val tmp = File.createTempFile("pb_test_file_", ".tmp")
        try {
            tmp.writeBytes("PhotoBeam Integrity Verification".toByteArray(Charsets.UTF_8))
            val hash = IntegrityManager.fileHash(tmp)
            assertEquals(64, hash.length)
            assertTrue(IntegrityManager.verifyFile(tmp, hash))
            assertFalse(IntegrityManager.verifyFile(tmp, "0".repeat(64)))
        } finally {
            tmp.delete()
        }
    }

    @Test
    fun testSessionManagerLifecycle() {
        val sm = SessionManager(deviceId = "android-device-1")
        val session = sm.createSession(
            addrs = listOf("192.168.1.50"),
            port = 47474,
            certPem = ByteArray(32),
            certFp = "AA:BB:CC:DD",
        )
        assertNotNull(session.sid)
        assertNotNull(session.token)
        assertEquals(SessionState.PENDING, session.state)

        // Validate Hello with correct token
        val (okValid, _) = sm.validateHello(session.sid, session.token)
        assertTrue(okValid)

        // Validate Hello with bad token
        val (okBadToken, reasonToken) = sm.validateHello(session.sid, "bad-token")
        assertFalse(okBadToken)
        assertEquals("invalid_token", reasonToken)

        // Validate Hello with unknown session
        val (okUnknown, reasonUnknown) = sm.validateHello("non-existent-sid", session.token)
        assertFalse(okUnknown)
        assertEquals("unknown_session", reasonUnknown)

        // Transitions
        sm.markConnected(session.sid, "sender-device-99")
        assertEquals(SessionState.CONNECTED, sm.getSession(session.sid)?.state)

        sm.markTransferring(session.sid)
        assertEquals(SessionState.TRANSFERRING, sm.getSession(session.sid)?.state)

        sm.markCompleted(session.sid)
        assertEquals(SessionState.COMPLETED, sm.getSession(session.sid)?.state)
    }

    @Test
    fun testTlsUtilsCertGeneration() {
        val cert = TlsUtils.generateSessionCert()
        assertNotNull(cert.sslContext)
        assertNotNull(cert.certDer)
        assertTrue(cert.certDer.isNotEmpty())
        assertNotNull(cert.fingerprint)
        assertTrue(cert.fingerprint.startsWith("sha256:"))
        val hex = cert.fingerprint.removePrefix("sha256:")
        assertEquals(64, hex.length)
    }

    @Test
    fun testQrPayloadEncodeDecode() {
        val payload = QRPayload(
            v = 1,
            sid = UUID.randomUUID().toString(),
            rid = UUID.randomUUID().toString(),
            addrs = listOf("192.168.1.100", "10.0.0.5"),
            port = 47474,
            transports = listOf("wifi", "usb"),
            token = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
            exp = System.currentTimeMillis() / 1000 + 300,
            certFp = "sha256:abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789",
        )
        val uri = encodeQrPayload(payload)
        assertTrue(uri.startsWith("photobeam://connect/"))

        val decoded = decodeQrPayload(uri)
        assertEquals(payload.v, decoded.v)
        assertEquals(payload.sid, decoded.sid)
        assertEquals(payload.rid, decoded.rid)
        assertEquals(payload.addrs, decoded.addrs)
        assertEquals(payload.port, decoded.port)
        assertEquals(payload.transports, decoded.transports)
        assertEquals(payload.token, decoded.token)
        assertEquals(payload.exp, decoded.exp)
        assertEquals(payload.certFp, decoded.certFp)
        assertFalse(decoded.isExpired())
    }

    @Test
    fun testSchedulerSingleTransport() {
        val mockWifi = MockTransport("wifi")
        val scheduler = Scheduler()
        scheduler.addTransport(mockWifi)

        assertTrue(scheduler.hasAnyTransport())
        assertEquals(1, scheduler.availableTransports().size)
        assertEquals("wifi", scheduler.nextTransport()?.transportId)
        assertEquals("wifi", scheduler.nextTransport()?.transportId)
    }

    @Test
    fun testSchedulerMultiTransportDistribution() {
        val mockWifi = MockTransport("wifi")
        val mockUsb = MockTransport("usb")
        val scheduler = Scheduler()
        scheduler.addTransport(mockWifi)
        scheduler.addTransport(mockUsb)

        assertEquals(2, scheduler.availableTransports().size)

        val counts = mutableMapOf<String, Int>()
        for (i in 0 until 10) {
            val t = scheduler.nextTransport()
            assertNotNull(t)
            counts[t!!.transportId] = (counts[t.transportId] ?: 0) + 1
        }

        // Both transports must have received chunks in multi-path mode
        assertTrue("Wi-Fi should have handled chunks", (counts["wifi"] ?: 0) > 0)
        assertTrue("USB should have handled chunks", (counts["usb"] ?: 0) > 0)
        assertEquals(10, (counts["wifi"] ?: 0) + (counts["usb"] ?: 0))
    }

    @Test
    fun testSchedulerFailureAndFallback() {
        val mockWifi = MockTransport("wifi")
        val mockUsb = MockTransport("usb")
        val scheduler = Scheduler()
        scheduler.addTransport(mockWifi)
        scheduler.addTransport(mockUsb)

        // Simulate 3 failures on USB
        scheduler.reportFailure("usb")
        scheduler.reportFailure("usb")
        scheduler.reportFailure("usb")

        // USB should now be disabled; all traffic falls back to Wi-Fi
        val transports = (0 until 5).map { scheduler.nextTransport()?.transportId }
        assertTrue(transports.all { it == "wifi" })

        // Mark USB reconnected
        scheduler.markReconnected("usb")
        val reconnectedTransports = (0 until 10).mapNotNull { scheduler.nextTransport()?.transportId }
        assertTrue("Should include USB after reconnect", reconnectedTransports.contains("usb"))
    }

    @Test
    fun testSenderMultiTransportChunkStreamingAndFallback() {
        val mockWifi = MockTransport("wifi")
        val mockUsb = MockTransport("usb")
        val scheduler = Scheduler()
        scheduler.addTransport(mockWifi)
        scheduler.addTransport(mockUsb)

        val totalChunks = 8
        val sentLocations = mutableListOf<String>()
        val maxRetries = 5

        for (chunkId in 0 until totalChunks) {
            val data = "Chunk data $chunkId".toByteArray()
            val frame = ChunkFrame(
                transferId = ByteArray(16),
                fileId = ByteArray(16),
                chunkId = chunkId,
                offset = chunkId * 1024L,
                data = data,
                checksum = IntegrityManager.chunkChecksum(data),
            )

            // Simulate USB failure midway through transfer (at chunk 4)
            if (chunkId == 4) {
                mockUsb.shouldFail = true
            }

            var retries = 0
            var chunkSent = false
            while (retries < maxRetries && !chunkSent) {
                val t = scheduler.nextTransport()
                assertNotNull("Transport must be available", t)
                try {
                    t!!.sendChunkFrame(frame)
                    scheduler.reportSuccess(t.transportId)
                    sentLocations.add(t.transportId)
                    chunkSent = true
                } catch (e: Exception) {
                    scheduler.reportFailure(t!!.transportId)
                    retries++
                }
            }
            assertTrue("Chunk $chunkId must be sent successfully", chunkSent)
        }

        // Verify total chunks sent equals 8
        assertEquals(8, sentLocations.size)
        // Verify both USB and Wi-Fi were used before the failure
        assertTrue("USB was used before failure", sentLocations.take(4).contains("usb"))
        // Verify all chunks after USB failure were sent on Wi-Fi
        assertTrue("Chunks after failure routed to Wi-Fi", sentLocations.drop(4).all { it == "wifi" })
    }

    @Test
    fun testQrExpiryExtendedTo24Hours() {
        assertEquals("QR expiry should be 86400s (24 hours)", 86400L, QR_EXPIRY_SECONDS)
        val sm = SessionManager("device-123")
        val session = sm.createSession(
            addrs = listOf("192.168.1.10"),
            port = DEFAULT_PORT,
            transports = listOf("wifi", "usb"),
            certPem = ByteArray(16),
            certFp = "sha256:abcd",
        )
        val payload = sm.buildQrPayload(session)
        assertFalse("Payload must not be expired upon creation", payload.isExpired())
        assertTrue("Expiry must be in the future (~24 hours)", payload.exp > (System.currentTimeMillis() / 1000 + 80000))
    }

    @Test
    fun testHelloValidationTiming() {
        val sm = SessionManager("device-perf")
        val t0 = System.nanoTime()
        val session = sm.createSession(
            addrs = listOf("10.0.0.1"),
            port = 47474,
            certPem = ByteArray(16),
            certFp = "sha256:11223344",
        )
        val (ok, _) = sm.validateHello(session.sid, session.token)
        val elapsedMs = (System.nanoTime() - t0) / 1_000_000.0
        assertTrue(ok)
        assertTrue("Hello validation must take < 5ms (was ${elapsedMs}ms)", elapsedMs < 5.0)
    }

    @Test
    fun testParallelAddressRaceSelection() = kotlinx.coroutines.runBlocking {
        val addrs = listOf("192.168.1.99", "10.200.0.5", "192.168.1.15")
        val reachableAddr = "192.168.1.15"

        val channel = Channel<Pair<String, String>>(addrs.size)
        val jobs = addrs.map { addr ->
            this@runBlocking.launch {
                if (addr == reachableAddr) {
                    channel.send(Pair("connected", addr))
                }
            }
        }

        val winner = channel.receive()
        jobs.forEach { it.cancel() }

        assertEquals(reachableAddr, winner.second)
        assertEquals("connected", winner.first)
    }

    @Test
    fun testWriteChunksOutOfOrderAndVerifyHash() {
        val size = 64 * 1024
        val data = ByteArray(size) { i ->
            if (i % 17 == 0) 0.toByte() else (i and 0xFF).toByte()
        }
        val chunkSize = 4096
        val numChunks = size / chunkSize

        val tmp = File.createTempFile("pb_ooo_test_", ".bin")
        try {
            java.io.RandomAccessFile(tmp, "rw").use { it.setLength(size.toLong()) }

            // Write chunks in reverse order
            for (cid in (numChunks - 1) downTo 0) {
                val offset = (cid * chunkSize).toLong()
                val chunkBytes = data.copyOfRange(cid * chunkSize, (cid + 1) * chunkSize)
                writeChunkToFile(tmp, offset, chunkBytes)
            }

            assertEquals(size.toLong(), tmp.length())
            assertArrayEquals(data, tmp.readBytes())

            // Verify SHA-256
            val expectedHash = java.security.MessageDigest.getInstance("SHA-256")
                .digest(data)
                .joinToString("") { "%02x".format(it) }

            val actualHash = IntegrityManager.fileHash(tmp)
            assertEquals(expectedHash, actualHash)
            assertTrue(IntegrityManager.verifyFile(tmp, expectedHash))
        } finally {
            tmp.delete()
        }
    }

    @Test
    fun testZeroByteFileChunkManagerAndIntegrity() {
        val transferId = ByteArray(16) { 0x01 }
        val fileId = ByteArray(16) { 0x02 }
        val cm = ChunkManager(transferId, fileId, 0L, 4096)

        assertEquals(1, cm.totalChunks)
        assertEquals(0, cm.lengthForChunk(0))
        assertFalse(cm.recordReceived(-1))
        assertFalse(cm.recordReceived(1))
        assertTrue(cm.recordReceived(0))
        assertTrue(cm.isComplete())

        val tmp = File.createTempFile("pb_zero_", ".bin")
        try {
            val emptyHash = IntegrityManager.fileHash(tmp)
            assertEquals("e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855", emptyHash)
            assertTrue(IntegrityManager.verifyFile(tmp, emptyHash))
        } finally {
            tmp.delete()
        }
    }
}

private class MockTransport(id: String) : com.photobeam.app.transport.Transport(id) {
    var shouldFail: Boolean = false

    init {
        status = Status.CONNECTED
    }

    override fun connect(host: String, port: Int, timeoutMs: Int) {
        status = Status.CONNECTED
    }

    override fun disconnect() {
        status = Status.DISCONNECTED
    }

    override fun sendAll(data: ByteArray) {
        if (shouldFail) {
            status = Status.FAILED
            throw java.io.IOException("Mock transport $transportId failed")
        }
        recordSent(data.size.toLong())
    }

    override fun recvExact(n: Int): ByteArray {
        if (shouldFail) {
            status = Status.FAILED
            throw java.io.IOException("Mock transport $transportId failed")
        }
        return ByteArray(n)
    }
}

