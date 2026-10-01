package com.photobeam.app.ui.screens

import android.content.Context
import android.graphics.Bitmap
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.photobeam.app.PhotoBeamApp
import com.photobeam.app.data.TransferHistoryManager
import com.photobeam.app.data.TransferRecord
import com.photobeam.app.protocol.*
import com.photobeam.app.transport.TlsUtils
import com.photobeam.app.transport.WiFiTransport
import com.photobeam.app.ui.FriendlyError
import com.photobeam.app.ui.FriendlyErrorCard
import com.photobeam.app.ui.components.*
import com.photobeam.app.ui.theme.*
import com.google.zxing.BarcodeFormat
import com.google.zxing.qrcode.QRCodeWriter
import kotlinx.coroutines.*
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.util.Locale

@Composable
fun ReceiveScreen(
    onBack: () -> Unit,
    onNavigateHistory: () -> Unit = {},
    autoAccept: Boolean = false,
) {
    val context = LocalContext.current
    var state by remember { mutableStateOf<ReceiveState>(ReceiveState.GeneratingQr) }
    val scope = rememberCoroutineScope()
    var receiverJob by remember { mutableStateOf<Job?>(null) }
    var showCancelDialog by remember { mutableStateOf(false) }
    var currentCancelAction by remember { mutableStateOf<(() -> Unit)?>(null) }

    LaunchedEffect(Unit) {
        receiverJob = scope.launch(Dispatchers.IO) {
            runReceiver(context, autoAccept = autoAccept) { newState ->
                withContext(Dispatchers.Main) { state = newState }
            }
        }
    }

    DisposableEffect(Unit) {
        onDispose { receiverJob?.cancel() }
    }

    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(AppBackgroundBrush),
    ) {
        Column(
            modifier = Modifier
                .fillMaxSize()
                .statusBarsPadding()
                .navigationBarsPadding()
                .padding(horizontal = 20.dp, vertical = 14.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            // ── Top Bar ───────────────────────────────────────────────────────
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Box(
                    modifier = Modifier
                        .size(40.dp)
                        .clip(RoundedCornerShape(12.dp))
                        .background(SurfaceElevated)
                        .border(1.dp, CardBorderSubtle, RoundedCornerShape(12.dp))
                        .clickable(onClick = { receiverJob?.cancel(); onBack() }),
                    contentAlignment = Alignment.Center
                ) {
                    Text("←", fontSize = 20.sp, color = OnBackground)
                }

                TactilePillButton(
                    text = "📜 History",
                    active = false,
                    onClick = onNavigateHistory,
                    minHeight = 36.dp
                )
            }

            Spacer(Modifier.height(14.dp))

            Row(verticalAlignment = Alignment.CenterVertically) {
                TactileGlowRing(size = 38.dp, ringColor = CyanAccent) {
                    Text("📥", fontSize = 18.sp)
                }
                Spacer(Modifier.width(12.dp))
                Column {
                    Text("Receive Files", fontSize = 20.sp, fontWeight = FontWeight.Bold, color = OnBackground)
                    Text("Ready for incoming connection", fontSize = 12.sp, color = OnSurfaceVariant)
                }
            }
            Spacer(Modifier.height(16.dp))

            // ── Main Card ─────────────────────────────────────────────────────
            TactileCard(
                modifier = Modifier.fillMaxWidth(),
                cornerRadius = 22.dp,
                backgroundColor = SurfaceElevated,
                borderColor = CardBorder
            ) {
                Column(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(28.dp),
                    horizontalAlignment = Alignment.CenterHorizontally,
                    verticalArrangement = Arrangement.spacedBy(16.dp),
                ) {
                    when (val s = state) {
                        is ReceiveState.GeneratingQr -> {
                            CircularProgressIndicator(color = Primary)
                            Text("Generating secure session…", color = OnSurfaceVariant)
                        }

                        is ReceiveState.WaitingForSender -> {
                            Surface(
                                color = Secondary.copy(alpha = 0.12f),
                                shape = RoundedCornerShape(8.dp),
                            ) {
                                Row(
                                    modifier = Modifier.padding(horizontal = 10.dp, vertical = 4.dp),
                                    verticalAlignment = Alignment.CenterVertically,
                                    horizontalArrangement = Arrangement.spacedBy(6.dp),
                                ) {
                                    Box(
                                        modifier = Modifier
                                            .size(8.dp)
                                            .clip(CircleShape)
                                            .background(Secondary)
                                    )
                                    Text(
                                        "Ready to receive on Wi-Fi",
                                        color = Secondary,
                                        fontSize = 12.sp,
                                        fontWeight = FontWeight.SemiBold,
                                    )
                                }
                            }

                            s.qrBitmap?.let {
                                Image(
                                    bitmap = it.asImageBitmap(),
                                    contentDescription = "QR Code",
                                    modifier = Modifier
                                        .size(240.dp)
                                        .clip(RoundedCornerShape(12.dp))
                                        .background(androidx.compose.ui.graphics.Color.White)
                                        .padding(8.dp),
                                )
                            }
                            Text(
                                "Waiting for Sender…",
                                fontSize = 16.sp,
                                fontWeight = FontWeight.SemiBold,
                                color = OnBackground,
                            )
                            Text(
                                "Scan this QR code from the sending device or wait for an incoming connection.",
                                fontSize = 13.sp,
                                color = OnSurfaceVariant,
                                textAlign = TextAlign.Center,
                            )
                        }

                        is ReceiveState.Connected -> {
                            Text(
                                "● Connected to sender",
                                fontSize = 16.sp,
                                color = Secondary,
                                fontWeight = FontWeight.Bold,
                            )
                            Text("Waiting for sender to select files…", color = OnSurfaceVariant, fontSize = 13.sp)
                        }

                        is ReceiveState.AwaitingAcceptance -> {
                            Text("📥", fontSize = 40.sp)
                            Text(
                                "Incoming Transfer",
                                fontSize = 20.sp,
                                fontWeight = FontWeight.Bold,
                                color = OnBackground,
                            )
                            Text(
                                "From: ${s.senderAddr}",
                                fontSize = 13.sp,
                                color = OnSurfaceVariant,
                            )
                            Surface(
                                color = Background,
                                shape = RoundedCornerShape(12.dp),
                                modifier = Modifier.fillMaxWidth(),
                            ) {
                                Column(
                                    modifier = Modifier.padding(16.dp),
                                    horizontalAlignment = Alignment.CenterHorizontally,
                                ) {
                                    Text(
                                        "${s.fileCount} file(s)",
                                        fontSize = 18.sp,
                                        fontWeight = FontWeight.Bold,
                                        color = OnBackground,
                                    )
                                    Text(
                                        formatBytes(s.totalBytes),
                                        fontSize = 14.sp,
                                        color = Primary,
                                        fontWeight = FontWeight.SemiBold,
                                    )
                                }
                            }

                            Row(
                                modifier = Modifier.fillMaxWidth(),
                                horizontalArrangement = Arrangement.spacedBy(12.dp),
                            ) {
                                Button(
                                    onClick = s.onAccept,
                                    modifier = Modifier.weight(1f),
                                    colors = ButtonDefaults.buttonColors(containerColor = Primary),
                                    shape = RoundedCornerShape(12.dp),
                                ) {
                                    Text("Accept Transfer", fontWeight = FontWeight.Bold)
                                }
                                OutlinedButton(
                                    onClick = s.onReject,
                                    modifier = Modifier.weight(1f),
                                    shape = RoundedCornerShape(12.dp),
                                ) {
                                    Text("Decline", color = Error)
                                }
                            }
                        }

                        is ReceiveState.Receiving -> {
                            currentCancelAction = s.onCancel

                            // Transport status badge
                            Surface(
                                color = Secondary.copy(alpha = 0.12f),
                                shape = RoundedCornerShape(8.dp),
                            ) {
                                Row(
                                    modifier = Modifier.padding(horizontal = 10.dp, vertical = 4.dp),
                                    verticalAlignment = Alignment.CenterVertically,
                                    horizontalArrangement = Arrangement.spacedBy(6.dp),
                                ) {
                                    Box(
                                        modifier = Modifier
                                            .size(8.dp)
                                            .clip(CircleShape)
                                            .background(Secondary)
                                    )
                                    Text(
                                        "Connected via ${s.transportDesc}",
                                        color = Secondary,
                                        fontSize = 12.sp,
                                        fontWeight = FontWeight.SemiBold,
                                    )
                                }
                            }

                            Text(
                                text = "Receiving: ${s.currentFile}",
                                color = OnBackground,
                                fontWeight = FontWeight.SemiBold,
                                fontSize = 15.sp,
                                maxLines = 1,
                            )

                            // Current file progress bar
                            LinearProgressIndicator(
                                progress = { s.fileProgress },
                                modifier = Modifier
                                    .fillMaxWidth()
                                    .height(6.dp)
                                    .clip(RoundedCornerShape(3.dp)),
                                color = Primary.copy(alpha = 0.7f),
                            )

                            // Overall progress bar
                            LinearProgressIndicator(
                                progress = { s.overallProgress },
                                modifier = Modifier
                                    .fillMaxWidth()
                                    .height(8.dp)
                                    .clip(RoundedCornerShape(4.dp)),
                                color = Primary,
                            )

                            Row(
                                modifier = Modifier.fillMaxWidth(),
                                horizontalArrangement = Arrangement.SpaceBetween,
                            ) {
                                Text(
                                    text = "${formatBytes(s.overallReceived)} / ${formatBytes(s.overallTotal)}",
                                    color = OnSurfaceVariant,
                                    fontSize = 12.sp,
                                )
                                Text(
                                    text = "File ${s.fileIndex} of ${s.totalFiles}",
                                    color = OnSurfaceVariant,
                                    fontSize = 12.sp,
                                )
                            }

                            if (s.speedText.isNotEmpty()) {
                                Text(
                                    text = s.speedText,
                                    color = Primary,
                                    fontSize = 16.sp,
                                    fontWeight = FontWeight.Bold,
                                )
                            }

                            if (s.etaText.isNotEmpty()) {
                                Text(
                                    text = s.etaText,
                                    color = OnSurfaceVariant,
                                    fontSize = 12.sp,
                                )
                            }

                            Spacer(Modifier.height(4.dp))

                            OutlinedButton(
                                onClick = { showCancelDialog = true },
                                modifier = Modifier.fillMaxWidth(0.6f),
                                shape = RoundedCornerShape(10.dp),
                            ) {
                                Text("✕ Cancel Transfer", color = Error)
                            }
                        }

                        is ReceiveState.Pausing -> {
                            CircularProgressIndicator(color = Primary, modifier = Modifier.size(44.dp))
                            Text(
                                "Pausing safely...",
                                fontSize = 18.sp,
                                fontWeight = FontWeight.Bold,
                                color = OnBackground,
                            )
                            Text(
                                "Flushing received chunks and saving resume state to disk...",
                                fontSize = 13.sp,
                                color = OnSurfaceVariant,
                                textAlign = TextAlign.Center,
                            )
                        }

                        is ReceiveState.Paused -> {
                            Text("⏸", fontSize = 40.sp)
                            Text(
                                "Transfer Paused",
                                fontSize = 20.sp,
                                fontWeight = FontWeight.Bold,
                                color = OnBackground,
                            )
                            Text(
                                "File ${s.fileIndex} of ${s.totalFiles}: ${s.currentFile}",
                                fontSize = 14.sp,
                                fontWeight = FontWeight.SemiBold,
                                color = Primary,
                            )
                            LinearProgressIndicator(
                                progress = { if (s.overallTotal > 0) s.overallReceived.toFloat() / s.overallTotal else 0f },
                                modifier = Modifier
                                    .fillMaxWidth()
                                    .height(8.dp)
                                    .clip(RoundedCornerShape(4.dp)),
                                color = Primary,
                            )
                            Text(
                                "Progress: ${formatBytes(s.overallReceived)} / ${formatBytes(s.overallTotal)}",
                                fontSize = 13.sp,
                                color = OnSurfaceVariant,
                            )
                            Text(
                                "Waiting for sender to continue transfer...",
                                fontSize = 12.sp,
                                color = OnSurfaceVariant,
                            )
                        }

                        is ReceiveState.VerifyingState -> {
                            CircularProgressIndicator(color = Primary, modifier = Modifier.size(44.dp))
                            Text(
                                "Verifying State...",
                                fontSize = 18.sp,
                                fontWeight = FontWeight.Bold,
                                color = OnBackground,
                            )
                            Text(
                                "Verifying integrity of temporary chunks for ${s.currentFile}...",
                                fontSize = 13.sp,
                                color = OnSurfaceVariant,
                                textAlign = TextAlign.Center,
                            )
                        }

                        is ReceiveState.Complete -> {
                            Text("✓", fontSize = 48.sp, color = Secondary)
                            Text(
                                "Transfer Complete!",
                                fontSize = 20.sp,
                                fontWeight = FontWeight.Bold,
                                color = Secondary,
                            )
                            val durText = String.format(Locale.US, "%.1fs", s.durationSec)
                            Text(
                                "${s.fileCount} file(s) received (${formatBytes(s.totalBytes)}) in $durText",
                                color = OnSurfaceVariant,
                                fontSize = 13.sp,
                            )

                            // Integrity status badge
                            Surface(
                                color = Secondary.copy(alpha = 0.12f),
                                shape = RoundedCornerShape(8.dp),
                                modifier = Modifier.padding(vertical = 4.dp),
                            ) {
                                Row(
                                    modifier = Modifier.padding(horizontal = 12.dp, vertical = 6.dp),
                                    verticalAlignment = Alignment.CenterVertically,
                                    horizontalArrangement = Arrangement.spacedBy(6.dp),
                                ) {
                                    Text("🛡️", fontSize = 14.sp)
                                    Text(
                                        "SHA-256 byte-perfect integrity verified",
                                        color = Secondary,
                                        fontSize = 12.sp,
                                        fontWeight = FontWeight.SemiBold,
                                    )
                                }
                            }

                            Spacer(Modifier.height(8.dp))

                            // Action button to open files
                            Button(
                                onClick = {
                                    try {
                                        val intent = android.content.Intent(android.app.DownloadManager.ACTION_VIEW_DOWNLOADS).apply {
                                            flags = android.content.Intent.FLAG_ACTIVITY_NEW_TASK
                                        }
                                        context.startActivity(intent)
                                    } catch (_: Exception) {
                                        try {
                                            val intent = android.content.Intent(android.content.Intent.ACTION_GET_CONTENT).apply {
                                                type = "*/*"
                                                flags = android.content.Intent.FLAG_ACTIVITY_NEW_TASK
                                            }
                                            context.startActivity(intent)
                                        } catch (_: Exception) {}
                                    }
                                },
                                modifier = Modifier.fillMaxWidth(),
                                colors = ButtonDefaults.buttonColors(containerColor = Primary),
                                shape = RoundedCornerShape(10.dp),
                            ) {
                                Text("📁 Open Received Files", fontWeight = FontWeight.SemiBold)
                            }

                            Row(
                                modifier = Modifier.fillMaxWidth(),
                                horizontalArrangement = Arrangement.spacedBy(12.dp),
                            ) {
                                OutlinedButton(
                                    onClick = onNavigateHistory,
                                    modifier = Modifier.weight(1f),
                                    shape = RoundedCornerShape(10.dp),
                                ) {
                                    Text("View History")
                                }
                                Button(
                                    onClick = { receiverJob?.cancel(); onBack() },
                                    modifier = Modifier.weight(1f),
                                    colors = ButtonDefaults.buttonColors(containerColor = Primary),
                                    shape = RoundedCornerShape(10.dp),
                                ) {
                                    Text("Done")
                                }
                            }
                        }

                        is ReceiveState.Error -> {
                            val friendly = FriendlyError.from(s.message)
                            FriendlyErrorCard(
                                error = friendly,
                                onRetry = {
                                    receiverJob?.cancel()
                                    receiverJob = scope.launch(Dispatchers.IO) {
                                        runReceiver(context, autoAccept = autoAccept) { newState ->
                                            withContext(Dispatchers.Main) { state = newState }
                                        }
                                    }
                                },
                                onCancel = {
                                    receiverJob?.cancel()
                                    onBack()
                                },
                                retryText = "Restart Receiver",
                                cancelText = "Back",
                            )
                        }
                    }
                }
            }
        }

        // Cancel Confirmation Dialog
        if (showCancelDialog) {
            AlertDialog(
                onDismissRequest = { showCancelDialog = false },
                title = { Text("Cancel Receive?") },
                text = { Text("Are you sure you want to cancel? Incomplete files will not be saved.") },
                confirmButton = {
                    TextButton(
                        onClick = {
                            showCancelDialog = false
                            currentCancelAction?.invoke()
                        }
                    ) {
                        Text("Yes, Cancel", color = Error)
                    }
                },
                dismissButton = {
                    TextButton(onClick = { showCancelDialog = false }) {
                        Text("Keep Receiving")
                    }
                }
            )
        }
    }
}

// ── State ─────────────────────────────────────────────────────────────────────

sealed class ReceiveState {
    object GeneratingQr : ReceiveState()
    data class WaitingForSender(val qrBitmap: Bitmap?, val uri: String) : ReceiveState()
    data class Connected(val senderAddr: String) : ReceiveState()
    data class AwaitingAcceptance(
        val senderAddr: String,
        val fileCount: Int,
        val totalBytes: Long,
        val onAccept: () -> Unit,
        val onReject: () -> Unit,
    ) : ReceiveState()
    data class Receiving(
        val currentFile: String,
        val fileIndex: Int,
        val totalFiles: Int,
        val fileProgress: Float,
        val overallProgress: Float,
        val overallReceived: Long,
        val overallTotal: Long,
        val speedText: String,
        val etaText: String,
        val transportDesc: String = "Wi-Fi",
        val onCancel: () -> Unit = {},
    ) : ReceiveState()
    object Pausing : ReceiveState()
    data class Paused(
        val currentFile: String,
        val fileIndex: Int,
        val totalFiles: Int,
        val fileReceived: Long,
        val fileSize: Long,
        val overallReceived: Long,
        val overallTotal: Long,
    ) : ReceiveState()
    data class VerifyingState(val currentFile: String) : ReceiveState()
    data class Complete(val fileCount: Int, val totalBytes: Long, val durationSec: Double, val destDir: File? = null) : ReceiveState()
    data class Error(val message: String) : ReceiveState()
}

// ── Receiver logic ────────────────────────────────────────────────────────────

private suspend fun runReceiver(
    context: Context,
    autoAccept: Boolean = false,
    onState: suspend (ReceiveState) -> Unit,
) {
    val startTime = System.currentTimeMillis()
    val deviceId = PhotoBeamApp.instance.deviceId
    val sessionMgr = SessionManager(deviceId)
    var transportType = "Wi-Fi"
    val cancelRequested = java.util.concurrent.atomic.AtomicBoolean(false)

    val tlsCert = TlsUtils.generateSessionCert()
    val addrs = TlsUtils.getLocalAddresses()

    val session = sessionMgr.createSession(
        addrs = addrs,
        port = DEFAULT_PORT,
        transports = listOf("wifi", "usb"),
        certPem = tlsCert.certDer,
        certFp = tlsCert.fingerprint,
    )
    val payload = sessionMgr.buildQrPayload(session)
    val uri = encodeQrPayload(payload)
    android.util.Log.i("PhotoBeam", "QR_URI: $uri")

    val qrBitmap = generateQrBitmap(uri, 512)
    onState(ReceiveState.WaitingForSender(qrBitmap, uri))

    val server = com.photobeam.app.transport.TlsServer(tlsCert.sslContext, DEFAULT_PORT)
    server.start()

    val act = context as? androidx.activity.ComponentActivity
    com.photobeam.app.MainActivity.acquireWakeLock(context)
    com.photobeam.app.MainActivity.setKeepScreenOn(act, true)

    try {
        val clientSocket = withContext(Dispatchers.IO) {
            server.accept(timeoutMs = 86_400_000)
        }

        val primaryTransport = WiFiTransport.fromAcceptedSocket(clientSocket)
        val senderAddr = clientSocket.inetAddress.hostAddress ?: "unknown"

        val hello = primaryTransport.recvJson()
        val (ok, reason) = sessionMgr.validateHello(
            hello.getString("sid"),
            hello.getString("token"),
        )
        if (!ok) {
            primaryTransport.sendJson(JSONObject().put("type", MessageType.ERROR).put("code", reason))
            primaryTransport.disconnect()
            server.stop()
            onState(ReceiveState.Error("Auth failed: $reason"))
            return
        }

        sessionMgr.markConnected(session.sid, hello.optString("sender_id", "unknown"))
        primaryTransport.sendJson(JSONObject()
            .put("type", MessageType.HELLO_ACK)
            .put("v", PROTOCOL_VERSION)
            .put("sid", session.sid))

        onState(ReceiveState.Connected(senderAddr))

        // Receive READY message
        val ready = primaryTransport.recvJson()
        val transfersJson = ready.getJSONArray("transfers")
        val infos = (0 until transfersJson.length()).map {
            TransferInfo.fromJson(transfersJson.getJSONObject(it))
        }
        val totalBytes = infos.sumOf { it.size }
        val fileCount = infos.size

        // Storage Check
        val destDir = File(
            android.os.Environment.getExternalStoragePublicDirectory(
                android.os.Environment.DIRECTORY_DOWNLOADS
            ), "PhotoBeam"
        )
        destDir.mkdirs()

        val usable = destDir.usableSpace
        if (usable < totalBytes) {
            for (info in infos) {
                try {
                    primaryTransport.sendJson(JSONObject().put("type", MessageType.REJECT).put("fid", info.fid).put("reason", "not_enough_space"))
                } catch (_: Exception) {}
            }
            primaryTransport.disconnect()
            server.stop()
            onState(ReceiveState.Error(
                "Transfer stopped\n\nNot enough storage space.\nRequired: ${formatBytes(totalBytes)}\nAvailable: ${formatBytes(usable)}"
            ))
            return
        }

        // Prompt user to accept transfer
        val decision = CompletableDeferred<Boolean>()
        if (autoAccept) {
            android.util.Log.i("PhotoBeam", "[DIAG] Auto-accepting transfer due to auto_accept flag")
            decision.complete(true)
        }
        onState(ReceiveState.AwaitingAcceptance(
            senderAddr = senderAddr,
            fileCount = fileCount,
            totalBytes = totalBytes,
            onAccept = { decision.complete(true) },
            onReject = { decision.complete(false) },
        ))

        val accepted = decision.await()
        if (!accepted) {
            for (info in infos) {
                try {
                    primaryTransport.sendJson(JSONObject().put("type", MessageType.REJECT).put("fid", info.fid).put("reason", "user_rejected"))
                } catch (_: Exception) {}
            }
            primaryTransport.disconnect()
            server.stop()
            onState(ReceiveState.Error("Transfer declined by user"))
            return
        }

        val chunkManagers = infos.associate { info ->
            val cm = ChunkManager(
                transferId = java.util.UUID.nameUUIDFromBytes(session.sid.toByteArray()).let { uuid ->
                    val bb = java.nio.ByteBuffer.allocate(16)
                    bb.putLong(uuid.mostSignificantBits)
                    bb.putLong(uuid.leastSignificantBits)
                    bb.array()
                },
                fileId = java.util.UUID.fromString(info.fid).let { uuid ->
                    val bb = java.nio.ByteBuffer.allocate(16)
                    bb.putLong(uuid.mostSignificantBits)
                    bb.putLong(uuid.leastSignificantBits)
                    bb.array()
                },
                fileSize = info.size,
                chunkSize = info.chunkSize,
            )
            info.fid to cm
        }

        val tmpFiles = infos.associate { info ->
            val tmp = File(destDir, ".${info.fid}.pbtemp")
            if (!tmp.exists() || tmp.length() != info.size) {
                if (info.size > 0) {
                    java.io.RandomAccessFile(tmp, "rw").use { it.setLength(info.size) }
                } else {
                    tmp.createNewFile()
                }
            }
            info.fid to tmp
        }

        var totalReceived = 0L

        // Accept files, checking resume state if available
        for (info in infos) {
            val (okResume, rxChunks) = verifyResumeState(destDir, session.sid, info.fid, info.size)
            val acceptMsg = JSONObject().put("type", MessageType.ACCEPT).put("fid", info.fid)
            if (okResume && rxChunks.isNotEmpty()) {
                acceptMsg.put("received_chunks", org.json.JSONArray(rxChunks))
                val cm = chunkManagers[info.fid]
                for (cid in rxChunks) {
                    if (cm?.recordReceived(cid) == true) {
                        totalReceived += minOf(info.chunkSize.toLong(), info.size - cid.toLong() * info.chunkSize)
                    }
                }
            }
            primaryTransport.sendJson(acceptMsg)
        }

        sessionMgr.markTransferring(session.sid)

        var completedFiles = 0
        val completedSet = java.util.Collections.synchronizedSet(mutableSetOf<String>())
        val failedFiles = java.util.Collections.synchronizedList(mutableListOf<String>())
        val verifiedFiles = java.util.Collections.synchronizedList(mutableListOf<String>())
        val fileChecksums = java.util.concurrent.ConcurrentHashMap<String, String>()
        for (info in infos) {
            if (info.sha256.isNotEmpty()) {
                fileChecksums[info.fid] = info.sha256
            }
        }
        val writeLock = Any()
        val completedLock = Any()

        // Smoothed speed tracking
        var smoothedSpeed = 0.0
        var lastTime = System.currentTimeMillis()
        var lastBytes = 0L

        // Keep one RandomAccessFile handle open per file for the duration of the transfer.
        val rafHandles: Map<String, java.io.RandomAccessFile> = infos.associate { info ->
            info.fid to java.io.RandomAccessFile(tmpFiles[info.fid]!!, "rw")
        }

        val activeTransports = java.util.concurrent.CopyOnWriteArrayList<WiFiTransport>()
        activeTransports.add(primaryTransport)

        // Set socket read timeout during active chunk streaming (1 hour for large files)
        primaryTransport.setSoTimeout(3_600_000)

        fun tryFinalizeFile(fid: String) {
            val cm = chunkManagers[fid] ?: return
            val info = infos.find { it.fid == fid } ?: return
            val tmp = tmpFiles[fid] ?: return
            val expectedSha = fileChecksums[fid] ?: return

            var shouldComplete = false
            synchronized(completedLock) {
                if (cm.isComplete() && !completedSet.contains(fid)) {
                    completedSet.add(fid)
                    shouldComplete = true
                }
            }
            if (!shouldComplete) return

            val raf = rafHandles[fid]
            if (raf != null) {
                flushTmpFile(raf)
                try { raf.close() } catch (_: Exception) {}
            }
            val ok2 = IntegrityManager.verifyFile(tmp, expectedSha)
            if (ok2) {
                val finalFile = getUniqueDestinationFile(destDir, info.name)
                val renamed = tmp.renameTo(finalFile)
                if (!renamed) {
                    tmp.copyTo(finalFile, overwrite = true)
                    tmp.delete()
                }
                verifiedFiles.add(info.name)
                val doneMsg = JSONObject()
                    .put("type", MessageType.FILE_DONE)
                    .put("fid", info.fid)
                    .put("sha256", expectedSha)
                var sentDone = false
                for (at in activeTransports) {
                    try {
                        at.sendJson(doneMsg)
                        sentDone = true
                    } catch (_: Exception) {}
                }
                if (!sentDone) {
                    try { primaryTransport.sendJson(doneMsg) } catch (_: Exception) {}
                }
            } else {
                tmp.delete()
                failedFiles.add(info.name)
                val errMsg = JSONObject()
                    .put("type", MessageType.FILE_ERROR)
                    .put("fid", info.fid)
                    .put("reason", "hash_mismatch")
                for (at in activeTransports) {
                    try { at.sendJson(errMsg) } catch (_: Exception) {}
                }
            }
            completedFiles++
        }

        suspend fun readChunksFromTransport(t: WiFiTransport) {
            try {
                while (completedFiles < infos.size) {
                    if (cancelRequested.get()) {
                        try {
                            t.sendJson(JSONObject().put("type", MessageType.CANCEL))
                        } catch (_: Exception) {}
                        break
                    }

                    val magic = try {
                        withContext(Dispatchers.IO) { t.recvExact(4) }
                    } catch (_: Exception) {
                        break
                    }

                if (magic.contentEquals(CHUNK_MAGIC_BYTES)) {
                    val headerRest = try {
                        withContext(Dispatchers.IO) { t.recvExact(CHUNK_HEADER_SIZE - 4) }
                    } catch (_: Exception) { break }
                    val header = magic + headerRest
                    val chunkLen = java.nio.ByteBuffer.wrap(header, 56, 4)
                        .order(java.nio.ByteOrder.BIG_ENDIAN).int
                    val data = try {
                        withContext(Dispatchers.IO) { t.recvExact(chunkLen) }
                    } catch (_: Exception) { break }

                    val frame = ChunkFrame.decode(header + data)
                    val fid = java.util.UUID.nameUUIDFromBytes(frame.fileId).toString()
                    val info = infos.find { it.fid == fid } ?: infos.find {
                        java.util.UUID.fromString(it.fid).let { uuid ->
                            val bb = java.nio.ByteBuffer.allocate(16)
                            bb.putLong(uuid.mostSignificantBits); bb.putLong(uuid.leastSignificantBits)
                            bb.array().contentEquals(frame.fileId)
                        }
                    } ?: continue

                    val cm = chunkManagers[info.fid] ?: continue
                    val tmp = tmpFiles[info.fid] ?: continue

                    if (!IntegrityManager.verifyChunk(frame.data, frame.checksum)) {
                        try {
                            t.sendJson(JSONObject()
                                .put("type", MessageType.NAK_CHUNK)
                                .put("fid", info.fid)
                                .put("cid", frame.chunkId)
                                .put("reason", "bad_checksum"))
                        } catch (_: Exception) {}
                        continue
                    }

                    val isNew = cm.recordReceived(frame.chunkId)
                    if (isNew) {
                        synchronized(writeLock) {
                            // Use the persistent RAF handle — no per-chunk open/close/fsync.
                            val raf = rafHandles[info.fid]
                            if (raf != null) {
                                writeChunkToFile(raf, frame.offset, frame.data)
                            } else {
                                writeChunkToFile(tmp, frame.offset, frame.data)
                            }
                            totalReceived += frame.data.size
                        }
                    }

                    // Chunk received and written to disk without flooding socket buffer
                    // (TCP provides in-order guaranteed delivery; file verified at FILE_DONE)

                    // Speed calculation
                    val now = System.currentTimeMillis()
                    val dt = (now - lastTime) / 1000.0
                    if (dt >= 0.25) {
                        val db = totalReceived - lastBytes
                        val inst = db / dt
                        smoothedSpeed = if (smoothedSpeed == 0.0) inst else 0.25 * inst + 0.75 * smoothedSpeed
                        lastTime = now
                        lastBytes = totalReceived
                    }

                    val speedStr = if (smoothedSpeed > 50_000) "${formatBytes(smoothedSpeed.toLong())}/s" else ""
                    val remBytes = (totalBytes - totalReceived).coerceAtLeast(0)
                    val etaStr = if (smoothedSpeed > 50_000) {
                        val sec = (remBytes / smoothedSpeed).coerceAtMost(86400.0 * 365).toLong()
                        if (sec < 60) "~$sec sec remaining"
                        else if (sec < 3600) "~${sec / 60} min remaining"
                        else "~${sec / 3600} hr ${(sec % 3600) / 60} min remaining"
                    } else ""

                    val fileIdx = infos.indexOf(info) + 1
                    val fileRx = (cm.receivedCount().toLong() * info.chunkSize).coerceAtMost(info.size)

                    onState(ReceiveState.Receiving(
                        currentFile = info.name,
                        fileIndex = fileIdx,
                        totalFiles = fileCount,
                        fileProgress = if (info.size > 0) fileRx.toFloat() / info.size else 1f,
                        overallProgress = if (totalBytes > 0) totalReceived.toFloat() / totalBytes else 1f,
                        overallReceived = totalReceived,
                        overallTotal = totalBytes,
                        speedText = speedStr,
                        etaText = etaStr,
                        transportDesc = transportType,
                        onCancel = { cancelRequested.set(true) },
                    ))

                    if (cm.isComplete()) {
                        tryFinalizeFile(info.fid)
                    }
                } else if (magic[0] == '{'.code.toByte()) {
                    val sb = StringBuilder()
                    sb.append(String(magic, Charsets.UTF_8))
                    while (true) {
                        val b = try {
                            withContext(Dispatchers.IO) { t.recvExact(1) }
                        } catch (_: Exception) { break }
                        if (b[0] == '\n'.code.toByte()) break
                        sb.append(b[0].toInt().toChar())
                    }
                    val jsonMsg = try { JSONObject(sb.toString()) } catch (_: Exception) { null } ?: continue
                    val mType = jsonMsg.optString("type")
                    if (mType == MessageType.FILE_CHECKSUM) {
                        val cfid = jsonMsg.optString("fid")
                        val csha = jsonMsg.optString("sha256")
                        if (cfid.isNotEmpty() && csha.isNotEmpty()) {
                            fileChecksums[cfid] = csha
                            tryFinalizeFile(cfid)
                        }
                    } else if (mType == MessageType.PING) {
                        try {
                            t.sendJson(JSONObject().put("type", MessageType.PONG).put("ts", jsonMsg.optDouble("ts", 0.0)))
                        } catch (_: Exception) {}
                    } else if (mType == MessageType.PAUSE) {
                        val pfid = jsonMsg.optString("fid")
                        val pinfo = infos.find { it.fid == pfid }
                        val pcm = chunkManagers[pfid]
                        if (pinfo != null && pcm != null) {
                            // Fsync at pause — safe state persisted to disk.
                            val pausedRaf = rafHandles[pfid]
                            if (pausedRaf != null) { flushTmpFile(pausedRaf) }
                            saveResumeState(destDir, session.sid, pinfo.fid, pinfo, pcm.receivedChunks())
                            val rxChunksJson = JSONArray(pcm.receivedChunks())
                            try {
                                t.sendJson(JSONObject()
                                    .put("type", MessageType.PAUSE_ACK)
                                    .put("fid", pfid)
                                    .put("received_chunks", rxChunksJson))
                            } catch (_: Exception) {}
                            onState(ReceiveState.Paused(
                                currentFile = pinfo.name,
                                fileIndex = infos.indexOf(pinfo) + 1,
                                totalFiles = fileCount,
                                fileReceived = (pcm.receivedCount().toLong() * pinfo.chunkSize).coerceAtMost(pinfo.size),
                                fileSize = pinfo.size,
                                overallReceived = totalReceived,
                                overallTotal = totalBytes,
                            ))
                        }
                    } else if (mType == MessageType.RESUME) {
                        val rfid = jsonMsg.optString("fid")
                        val rinfo = infos.find { it.fid == rfid }
                        val rcm = chunkManagers[rfid]
                        if (rinfo != null && rcm != null) {
                            if (rcm.isComplete() && completedSet.contains(rfid)) {
                                val shaToSend = fileChecksums[rfid] ?: rinfo.sha256
                                try {
                                    t.sendJson(JSONObject()
                                        .put("type", MessageType.FILE_DONE)
                                        .put("fid", rfid)
                                        .put("sha256", shaToSend))
                                } catch (_: Exception) {}
                            } else if (rcm.isComplete()) {
                                tryFinalizeFile(rfid)
                            } else {
                                onState(ReceiveState.VerifyingState(rinfo.name))
                                val (valid, rxList) = verifyResumeState(destDir, session.sid, rinfo.fid, rinfo.size)
                                if (valid) {
                                    val rxSet = rxList.toSet()
                                    rxSet.forEach { cid -> rcm.recordReceived(cid) }
                                    val missingJson = JSONArray(rcm.missingChunks())
                                    try {
                                        t.sendJson(JSONObject()
                                            .put("type", MessageType.ACCEPT)
                                            .put("fid", rfid)
                                            .put("received_chunks", JSONArray(rxList))
                                            .put("missing", missingJson))
                                    } catch (_: Exception) {}
                                } else {
                                    try {
                                        t.sendJson(JSONObject()
                                            .put("type", MessageType.ERROR)
                                            .put("fid", rfid)
                                            .put("code", "INVALID_RESUME_STATE")
                                            .put("reason", "Corrupted or missing temporary file"))
                                    } catch (_: Exception) {}
                                }
                            }
                        }
                    } else if (mType == MessageType.CANCEL) {
                        onState(ReceiveState.Error("Transfer cancelled by sender"))
                        break
                    }
                }
            }
        } finally {
            activeTransports.remove(t)
        }
    }

    // Secondary connection acceptor (USB tunnel or multi-path)
    val secondaryAcceptJob = CoroutineScope(Dispatchers.IO).launch {
        while (completedFiles < infos.size) {
            try {
                val secSocket = server.accept(timeoutMs = 2000)
                val secTransport = WiFiTransport.fromAcceptedSocket(secSocket, "usb")
                val secHello = secTransport.recvJson()
                if (secHello.optString("sid") == session.sid) {
                    secTransport.sendJson(JSONObject()
                        .put("type", MessageType.HELLO_ACK)
                        .put("v", PROTOCOL_VERSION)
                        .put("sid", session.sid))
                    activeTransports.add(secTransport)
                    transportType = "Wi-Fi + USB"
                    launch(Dispatchers.IO) {
                        readChunksFromTransport(secTransport)
                    }
                }
            } catch (_: Exception) {}
        }
    }

    val primaryJob = CoroutineScope(Dispatchers.IO).launch {
        readChunksFromTransport(primaryTransport)
    }

    // Wait until all files complete or all active transports fail
    while (completedFiles < infos.size) {
        if (activeTransports.isEmpty() && primaryJob.isCompleted) {
            delay(1500)
            if (activeTransports.isEmpty()) break
        }
        delay(100)
    }

    primaryJob.cancel()
    secondaryAcceptJob.cancel()
    server.stop()
    activeTransports.forEach { it.disconnect() }
    // Close any RAF handles that weren't already closed at file completion.
    rafHandles.values.forEach { raf -> try { raf.close() } catch (_: Exception) {} }

        val durationSec = (System.currentTimeMillis() - startTime) / 1000.0

        if (failedFiles.isNotEmpty()) {
            val errReason = "File integrity check failed (SHA-256 mismatch) for: ${failedFiles.joinToString(", ")}"
            TransferHistoryManager.getInstance(context).addRecord(
                TransferRecord(
                    direction = "received",
                    files = infos.map { it.name },
                    totalBytes = totalBytes,
                    durationSec = durationSec,
                    status = "failed",
                    transportType = transportType,
                    errorReason = errReason,
                )
            )
            onState(ReceiveState.Error(errReason))
        } else if (verifiedFiles.size == infos.size) {
            sessionMgr.markCompleted(session.sid)
            TransferHistoryManager.getInstance(context).addRecord(
                TransferRecord(
                    direction = "received",
                    files = infos.map { it.name },
                    totalBytes = totalBytes,
                    durationSec = durationSec,
                    status = "completed",
                    transportType = transportType,
                )
            )
            onState(ReceiveState.Complete(verifiedFiles.size, totalBytes, durationSec, destDir))
        } else {
            val errReason = "Transfer interrupted: received ${verifiedFiles.size} of ${infos.size} files"
            TransferHistoryManager.getInstance(context).addRecord(
                TransferRecord(
                    direction = "received",
                    files = infos.map { it.name },
                    totalBytes = totalBytes,
                    durationSec = durationSec,
                    status = "interrupted",
                    transportType = transportType,
                    errorReason = errReason,
                )
            )
            onState(ReceiveState.Error(errReason))
        }

    } catch (e: Exception) {
        if (e is CancellationException) return
        onState(ReceiveState.Error(e.message ?: "Unknown error"))
    } finally {
        server.stop()
        com.photobeam.app.MainActivity.releaseWakeLock()
        com.photobeam.app.MainActivity.setKeepScreenOn(act, false)
    }
}

private fun writeChunkToFile(file: File, offset: Long, data: ByteArray) {
    java.io.RandomAccessFile(file, "rw").use { raf ->
        raf.seek(offset)
        raf.write(data)
    }
}

private fun generateQrBitmap(content: String, size: Int): Bitmap {
    val writer = QRCodeWriter()
    val matrix = writer.encode(content, BarcodeFormat.QR_CODE, size, size)
    val bmp = Bitmap.createBitmap(size, size, Bitmap.Config.RGB_565)
    for (x in 0 until size) {
        for (y in 0 until size) {
            bmp.setPixel(x, y, if (matrix[x, y]) android.graphics.Color.BLACK else android.graphics.Color.WHITE)
        }
    }
    return bmp
}

private fun getUniqueDestinationFile(destDir: File, filename: String): File {
    val target = File(destDir, filename)
    if (!target.exists()) return target
    val dotIdx = filename.lastIndexOf('.')
    val stem = if (dotIdx > 0) filename.substring(0, dotIdx) else filename
    val ext = if (dotIdx > 0) filename.substring(dotIdx) else ""
    var counter = 1
    while (true) {
        val candidate = File(destDir, "$stem ($counter)$ext")
        if (!candidate.exists()) return candidate
        counter++
    }
}
