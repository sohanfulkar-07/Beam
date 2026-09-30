package com.photobeam.app.ui.screens

import android.net.Uri
import android.util.Log
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import android.provider.OpenableColumns
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.photobeam.app.data.TransferHistoryManager
import com.photobeam.app.data.TransferRecord
import com.photobeam.app.protocol.*
import com.photobeam.app.transport.WiFiTransport
import com.photobeam.app.ui.FriendlyError
import com.photobeam.app.ui.FriendlyErrorCard
import com.photobeam.app.ui.theme.*
import kotlinx.coroutines.*
import org.json.JSONObject
import java.util.Locale
import java.util.UUID

private const val TAG = "PhotoBeamPerf"

// ── State ─────────────────────────────────────────────────────────────────────

sealed class SendState {
    object Scanning : SendState()
    data class Connecting(val message: String) : SendState()
    data class ReadyToSend(
        val receiverAddr: String,
        val activeModes: String,
        val connection: SessionConnection,
    ) : SendState()
    data class Sending(
        val statusText: String,
        val progress: Float,
        val currentFile: String = "",
        val fileIndex: Int = 1,
        val totalFiles: Int = 1,
        val fileProgress: Float = 0f,
        val totalSent: Long = 0L,
        val totalBytes: Long = 0L,
        val speedText: String = "",
        val etaText: String = "",
        val activeModes: String = "Wi-Fi",
    ) : SendState()
    object Pausing : SendState()
    data class Paused(
        val currentFile: String,
        val fileIndex: Int,
        val totalFiles: Int,
        val fileSent: Long,
        val fileSize: Long,
        val overallSent: Long,
        val overallTotal: Long,
    ) : SendState()
    data class VerifyingState(val currentFile: String) : SendState()
    data class UnsafeToResume(val reason: String, val currentFile: String) : SendState()
    data class Complete(val fileCount: Int, val totalBytes: Long, val durationSec: Double = 0.0) : SendState()
    data class Error(val message: String) : SendState()
}

class SessionConnection(
    val scheduler: Scheduler,
    val primaryTransport: WiFiTransport,
    val payload: QRPayload,
    val primaryAddr: String,
) {
    fun disconnectAll() {
        try { primaryTransport.disconnect() } catch (_: Exception) {}
        scheduler.availableTransports().forEach {
            try { it.disconnect() } catch (_: Exception) {}
        }
    }
}

class TransferControl {
    val pauseRequested = java.util.concurrent.atomic.AtomicBoolean(false)
    val continueRequested = java.util.concurrent.atomic.AtomicBoolean(false)
    val restartRequested = java.util.concurrent.atomic.AtomicBoolean(false)
    val cancelRequested = java.util.concurrent.atomic.AtomicBoolean(false)
}


// ── Composable ────────────────────────────────────────────────────────────────

@Composable
fun SendScreen(
    onBack: () -> Unit,
    onNavigateHistory: () -> Unit = {},
    initialUris: List<Uri> = emptyList(),
    initialQrUri: String? = null,
) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()

    var state by remember { mutableStateOf<SendState>(SendState.Scanning) }
    var selectedUris by remember { mutableStateOf(initialUris) }
    var manualQrInput by remember { mutableStateOf(initialQrUri ?: "") }
    var showManualDialog by remember { mutableStateOf(false) }
    var showCancelDialog by remember { mutableStateOf(false) }
    val transferControl = remember { TransferControl() }

    var activeConnection by remember { mutableStateOf<SessionConnection?>(null) }
    var transferJob by remember { mutableStateOf<Job?>(null) }
    var connectJob by remember { mutableStateOf<Job?>(null) }

    val filePicker = rememberLauncherForActivityResult(
        ActivityResultContracts.GetMultipleContents()
    ) { uris ->
        if (uris.isNotEmpty()) {
            selectedUris = (selectedUris + uris).distinct()
        }
    }

    fun startConnect(rawUri: String) {
        connectJob?.cancel()
        state = SendState.Connecting("Connecting...")
        connectJob = scope.launch(Dispatchers.IO) {
            connectAndAuthenticate(
                rawUri = rawUri,
                onState = { newState ->
                    withContext(Dispatchers.Main) {
                        state = newState
                        if (newState is SendState.ReadyToSend) {
                            activeConnection = newState.connection
                        }
                    }
                }
            )
        }
    }

    LaunchedEffect(initialUris) {
        if (initialUris.isNotEmpty()) {
            selectedUris = initialUris
        }
    }

    // Auto-connect if initialQrUri was provided (e.g. from deep link)
    LaunchedEffect(initialQrUri) {
        if (!initialQrUri.isNullOrBlank() && initialQrUri.startsWith("photobeam://connect/")) {
            startConnect(initialQrUri)
        }
    }

    var autoStarted by remember { mutableStateOf(false) }
    LaunchedEffect(state) {
        val s = state
        if (!autoStarted && s is SendState.ReadyToSend && selectedUris.isNotEmpty() && !initialQrUri.isNullOrBlank() && initialUris.isNotEmpty()) {
            autoStarted = true
            val conn = s.connection
            val urisCopy = selectedUris.toList()
            transferJob = scope.launch(Dispatchers.IO) {
                runTransfer(context, conn, urisCopy, transferControl) { newState ->
                    withContext(Dispatchers.Main) { state = newState }
                }
            }
        }
    }

    DisposableEffect(Unit) {
        onDispose {
            connectJob?.cancel()
            transferJob?.cancel()
            activeConnection?.disconnectAll()
        }
    }

    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(Background),
    ) {
        when (val s = state) {
            is SendState.Scanning -> {
                // Immediate Camera QR Scanner view
                Box(modifier = Modifier.fillMaxSize()) {
                    QrScannerView(
                        modifier = Modifier.fillMaxSize(),
                        onQrScanned = { scannedUri ->
                            startConnect(scannedUri)
                        },
                        onManualInputRequested = {
                            showManualDialog = true
                        }
                    )

                    // Top Bar overlay
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .statusBarsPadding()
                            .padding(16.dp),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Button(
                            onClick = onBack,
                            colors = ButtonDefaults.buttonColors(containerColor = Color.Black.copy(alpha = 0.6f)),
                            shape = RoundedCornerShape(12.dp),
                        ) {
                            Text("← Back", color = Color.White)
                        }

                        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            Button(
                                onClick = onNavigateHistory,
                                colors = ButtonDefaults.buttonColors(containerColor = Color.Black.copy(alpha = 0.6f)),
                                shape = RoundedCornerShape(12.dp),
                            ) {
                                Text("📜 History", color = Color.White)
                            }
                            Button(
                                onClick = { showManualDialog = true },
                                colors = ButtonDefaults.buttonColors(containerColor = Color.Black.copy(alpha = 0.6f)),
                                shape = RoundedCornerShape(12.dp),
                            ) {
                                Text("Paste Link", color = Color.White)
                            }
                        }
                    }
                }
            }

            else -> {
                // Non-camera states: Connecting, ReadyToSend, Sending, Complete, Error
                Column(
                    modifier = Modifier
                        .fillMaxSize()
                        .statusBarsPadding()
                        .padding(24.dp),
                    horizontalAlignment = Alignment.CenterHorizontally,
                ) {
                    // Header Bar
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        TextButton(
                            onClick = {
                                activeConnection?.disconnectAll()
                                connectJob?.cancel()
                                transferJob?.cancel()
                                onBack()
                            }
                        ) {
                            Text("← Back", color = OnSurfaceVariant)
                        }

                        TextButton(onClick = onNavigateHistory) {
                            Text("📜 History", color = OnSurfaceVariant)
                        }
                    }

                    Spacer(Modifier.height(8.dp))
                    Text(
                        "📤 Send Files",
                        fontSize = 24.sp,
                        fontWeight = FontWeight.Bold,
                        color = OnBackground,
                    )
                    Spacer(Modifier.height(20.dp))

                    Card(
                        modifier = Modifier.fillMaxWidth(),
                        shape = RoundedCornerShape(20.dp),
                        colors = CardDefaults.cardColors(containerColor = Surface),
                    ) {
                        Column(
                            modifier = Modifier
                                .fillMaxWidth()
                                .padding(24.dp),
                            horizontalAlignment = Alignment.CenterHorizontally,
                            verticalArrangement = Arrangement.spacedBy(16.dp),
                        ) {
                            when (s) {
                                is SendState.Connecting -> {
                                    CircularProgressIndicator(color = Primary, modifier = Modifier.size(48.dp))
                                    Spacer(Modifier.height(8.dp))
                                    Text(
                                        s.message,
                                        fontSize = 16.sp,
                                        fontWeight = FontWeight.Medium,
                                        color = OnBackground,
                                    )
                                    Text(
                                        "Connecting to receiver...",
                                        fontSize = 13.sp,
                                        color = OnSurfaceVariant,
                                    )
                                }

                                is SendState.ReadyToSend -> {
                                    // Connection status banner
                                    Surface(
                                        color = Secondary.copy(alpha = 0.15f),
                                        shape = RoundedCornerShape(12.dp),
                                        modifier = Modifier.fillMaxWidth(),
                                    ) {
                                        Column(
                                            modifier = Modifier.padding(16.dp),
                                            horizontalAlignment = Alignment.CenterHorizontally,
                                        ) {
                                            Text(
                                                "✓ Connected",
                                                color = Secondary,
                                                fontWeight = FontWeight.Bold,
                                                fontSize = 16.sp,
                                            )
                                            Spacer(Modifier.height(4.dp))
                                            Text(
                                                "Receiver: ${s.receiverAddr} (${s.activeModes})",
                                                color = OnSurfaceVariant,
                                                fontSize = 13.sp,
                                            )
                                            Text(
                                                "Ready to send",
                                                color = OnBackground,
                                                fontWeight = FontWeight.SemiBold,
                                                fontSize = 14.sp,
                                            )
                                        }
                                    }

                                    // File selection area
                                    if (selectedUris.isNotEmpty()) {
                                        Column(
                                            modifier = Modifier.fillMaxWidth(),
                                            verticalArrangement = Arrangement.spacedBy(8.dp),
                                        ) {
                                            // Header with count and Clear All option
                                            Row(
                                                modifier = Modifier.fillMaxWidth(),
                                                horizontalArrangement = Arrangement.SpaceBetween,
                                                verticalAlignment = Alignment.CenterVertically,
                                            ) {
                                                val totalSize = selectedUris.sumOf { getFileNameAndSize(context, it).second }
                                                Text(
                                                    text = "${selectedUris.size} file(s) selected" + if (totalSize > 0L) " (${formatBytes(totalSize)})" else "",
                                                    fontWeight = FontWeight.SemiBold,
                                                    fontSize = 14.sp,
                                                    color = OnBackground,
                                                )
                                                TextButton(
                                                    onClick = { selectedUris = emptyList() },
                                                    contentPadding = PaddingValues(horizontal = 8.dp, vertical = 2.dp),
                                                ) {
                                                    Text(
                                                        "Clear all",
                                                        color = Error,
                                                        fontSize = 12.sp,
                                                        fontWeight = FontWeight.Medium,
                                                    )
                                                }
                                            }

                                            // List of selected files with cross (✕) unselect button on each
                                            Column(
                                                modifier = Modifier
                                                    .fillMaxWidth()
                                                    .heightIn(max = 220.dp)
                                                    .verticalScroll(rememberScrollState()),
                                                verticalArrangement = Arrangement.spacedBy(6.dp),
                                            ) {
                                                selectedUris.forEachIndexed { index, uri ->
                                                    val (fileName, fileSize) = getFileNameAndSize(context, uri)
                                                    val isMedia = fileName.endsWith(".jpg", true) ||
                                                            fileName.endsWith(".jpeg", true) ||
                                                            fileName.endsWith(".png", true) ||
                                                            fileName.endsWith(".mp4", true) ||
                                                            fileName.endsWith(".mkv", true)

                                                    Surface(
                                                        color = Background,
                                                        shape = RoundedCornerShape(12.dp),
                                                        modifier = Modifier.fillMaxWidth(),
                                                    ) {
                                                        Row(
                                                            modifier = Modifier
                                                                .fillMaxWidth()
                                                                .padding(horizontal = 12.dp, vertical = 8.dp),
                                                            verticalAlignment = Alignment.CenterVertically,
                                                        ) {
                                                            Text(
                                                                text = if (isMedia) "🖼️" else "📄",
                                                                fontSize = 18.sp,
                                                                modifier = Modifier.padding(end = 10.dp),
                                                            )

                                                            Column(modifier = Modifier.weight(1f)) {
                                                                Text(
                                                                    text = fileName,
                                                                    fontSize = 13.sp,
                                                                    fontWeight = FontWeight.Medium,
                                                                    color = OnBackground,
                                                                    maxLines = 1,
                                                                    overflow = TextOverflow.Ellipsis,
                                                                )
                                                                if (fileSize > 0L) {
                                                                    Text(
                                                                        text = formatBytes(fileSize),
                                                                        fontSize = 11.sp,
                                                                        color = OnSurfaceVariant,
                                                                    )
                                                                }
                                                            }

                                                            Spacer(Modifier.width(8.dp))

                                                            // Cross (✕) button to unselect this file
                                                            IconButton(
                                                                onClick = {
                                                                    selectedUris = selectedUris.filterIndexed { i, _ -> i != index }
                                                                },
                                                                modifier = Modifier.size(32.dp),
                                                            ) {
                                                                Surface(
                                                                    shape = CircleShape,
                                                                    color = Error.copy(alpha = 0.15f),
                                                                    modifier = Modifier.size(26.dp),
                                                                ) {
                                                                    Box(contentAlignment = Alignment.Center) {
                                                                        Text(
                                                                            text = "✕",
                                                                            color = Error,
                                                                            fontSize = 12.sp,
                                                                            fontWeight = FontWeight.Bold,
                                                                        )
                                                                    }
                                                                }
                                                            }
                                                        }
                                                    }
                                                }
                                            }
                                        }
                                    } else {
                                        Surface(
                                            color = Background,
                                            shape = RoundedCornerShape(12.dp),
                                            modifier = Modifier.fillMaxWidth(),
                                        ) {
                                            Column(
                                                modifier = Modifier.padding(16.dp),
                                                horizontalAlignment = Alignment.CenterHorizontally,
                                            ) {
                                                Text("📁", fontSize = 28.sp)
                                                Spacer(Modifier.height(4.dp))
                                                Text(
                                                    "No files selected yet",
                                                    fontSize = 14.sp,
                                                    fontWeight = FontWeight.SemiBold,
                                                    color = OnBackground,
                                                )
                                                Text(
                                                    "Tap below to choose photos, videos, or documents to send.",
                                                    fontSize = 12.sp,
                                                    color = OnSurfaceVariant,
                                                    textAlign = TextAlign.Center,
                                                )
                                            }
                                        }
                                    }

                                    // File Picker Button
                                    OutlinedButton(
                                        onClick = { filePicker.launch("*/*") },
                                        modifier = Modifier.fillMaxWidth(),
                                        shape = RoundedCornerShape(10.dp),
                                        border = ButtonDefaults.outlinedButtonBorder(true).copy(
                                            brush = androidx.compose.ui.graphics.SolidColor(Outline)
                                        ),
                                    ) {
                                        Text(
                                            if (selectedUris.isEmpty()) "📁 Select Files" else "➕ Add More Files",
                                            color = OnSurface,
                                            fontWeight = FontWeight.Medium,
                                        )
                                    }

                                    // Send Button
                                    Button(
                                        onClick = {
                                            val urisCopy = selectedUris.toList()
                                            transferJob = scope.launch(Dispatchers.IO) {
                                                runTransfer(context, s.connection, urisCopy, transferControl) { newState ->
                                                    withContext(Dispatchers.Main) { state = newState }
                                                }
                                            }
                                        },
                                        enabled = selectedUris.isNotEmpty(),
                                        modifier = Modifier.fillMaxWidth(),
                                        colors = ButtonDefaults.buttonColors(containerColor = Primary),
                                        shape = RoundedCornerShape(12.dp),
                                    ) {
                                        Text(
                                            if (selectedUris.isEmpty()) "Select Files to Send" else "📤 Send ${selectedUris.size} File(s)",
                                            color = OnBackground,
                                            fontWeight = FontWeight.Bold,
                                        )
                                    }
                                }

                                is SendState.Sending -> {
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
                                                "Connected via ${s.activeModes}",
                                                color = Secondary,
                                                fontSize = 12.sp,
                                                fontWeight = FontWeight.SemiBold,
                                            )
                                        }
                                    }

                                    if (s.currentFile.isNotEmpty()) {
                                        Text(
                                            text = "Sending: ${s.currentFile}",
                                            color = OnBackground,
                                            fontWeight = FontWeight.SemiBold,
                                            fontSize = 15.sp,
                                            maxLines = 1,
                                            overflow = TextOverflow.Ellipsis,
                                        )
                                    }

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
                                        progress = { s.progress },
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
                                            text = "${formatBytes(s.totalSent)} / ${formatBytes(s.totalBytes)}",
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

                                    Spacer(Modifier.height(8.dp))
                                    Row(
                                        modifier = Modifier.fillMaxWidth(),
                                        horizontalArrangement = Arrangement.spacedBy(12.dp),
                                    ) {
                                        Button(
                                            onClick = { transferControl.pauseRequested.set(true) },
                                            modifier = Modifier.weight(1f),
                                            colors = ButtonDefaults.buttonColors(containerColor = Primary),
                                            shape = RoundedCornerShape(10.dp),
                                        ) {
                                            Text("⏸ Pause")
                                        }
                                        OutlinedButton(
                                            onClick = { showCancelDialog = true },
                                            modifier = Modifier.weight(1f),
                                            shape = RoundedCornerShape(10.dp),
                                        ) {
                                            Text("✕ Cancel", color = Error)
                                        }
                                    }
                                }

                                is SendState.Pausing -> {
                                    CircularProgressIndicator(color = Primary, modifier = Modifier.size(44.dp))
                                    Text(
                                        "Pausing safely...",
                                        fontSize = 18.sp,
                                        fontWeight = FontWeight.Bold,
                                        color = OnBackground,
                                    )
                                    Text(
                                        "Finishing in-flight chunk and syncing state to disk...",
                                        fontSize = 13.sp,
                                        color = OnSurfaceVariant,
                                        textAlign = TextAlign.Center,
                                    )
                                }

                                is SendState.Paused -> {
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
                                        maxLines = 1,
                                        overflow = TextOverflow.Ellipsis,
                                    )
                                    LinearProgressIndicator(
                                        progress = { if (s.overallTotal > 0) s.overallSent.toFloat() / s.overallTotal else 0f },
                                        modifier = Modifier
                                            .fillMaxWidth()
                                            .height(8.dp)
                                            .clip(RoundedCornerShape(4.dp)),
                                        color = Primary,
                                    )
                                    Text(
                                        "Overall: ${formatBytes(s.overallSent)} / ${formatBytes(s.overallTotal)}",
                                        fontSize = 13.sp,
                                        color = OnSurfaceVariant,
                                    )
                                    Spacer(Modifier.height(8.dp))
                                    Row(
                                        modifier = Modifier.fillMaxWidth(),
                                        horizontalArrangement = Arrangement.spacedBy(12.dp),
                                    ) {
                                        Button(
                                            onClick = { transferControl.continueRequested.set(true) },
                                            modifier = Modifier.weight(1f),
                                            colors = ButtonDefaults.buttonColors(containerColor = Primary),
                                            shape = RoundedCornerShape(10.dp),
                                        ) {
                                            Text("▶ Continue")
                                        }
                                        OutlinedButton(
                                            onClick = { showCancelDialog = true },
                                            modifier = Modifier.weight(1f),
                                            shape = RoundedCornerShape(10.dp),
                                        ) {
                                            Text("✕ Cancel", color = Error)
                                        }
                                    }
                                }

                                is SendState.VerifyingState -> {
                                    CircularProgressIndicator(color = Primary, modifier = Modifier.size(44.dp))
                                    Text(
                                        "Verifying State...",
                                        fontSize = 18.sp,
                                        fontWeight = FontWeight.Bold,
                                        color = OnBackground,
                                    )
                                    Text(
                                        "Verifying source file and receiver state for ${s.currentFile}...",
                                        fontSize = 13.sp,
                                        color = OnSurfaceVariant,
                                        textAlign = TextAlign.Center,
                                    )
                                }

                                is SendState.UnsafeToResume -> {
                                    Text("⚠️", fontSize = 40.sp)
                                    Text(
                                        "Cannot Safely Resume",
                                        fontSize = 18.sp,
                                        fontWeight = FontWeight.Bold,
                                        color = Error,
                                    )
                                    Text(
                                        s.reason,
                                        fontSize = 13.sp,
                                        color = OnSurfaceVariant,
                                        textAlign = TextAlign.Center,
                                    )
                                    Spacer(Modifier.height(8.dp))
                                    Row(
                                        modifier = Modifier.fillMaxWidth(),
                                        horizontalArrangement = Arrangement.spacedBy(12.dp),
                                    ) {
                                        Button(
                                            onClick = { transferControl.restartRequested.set(true) },
                                            modifier = Modifier.weight(1f),
                                            colors = ButtonDefaults.buttonColors(containerColor = Primary),
                                            shape = RoundedCornerShape(10.dp),
                                        ) {
                                            Text("🔄 Restart File")
                                        }
                                        OutlinedButton(
                                            onClick = {
                                                transferControl.cancelRequested.set(true)
                                                activeConnection?.disconnectAll()
                                                onBack()
                                            },
                                            modifier = Modifier.weight(1f),
                                            shape = RoundedCornerShape(10.dp),
                                        ) {
                                            Text("Cancel", color = Error)
                                        }
                                    }
                                }

                                is SendState.Complete -> {
                                    Text("✓", fontSize = 48.sp, color = Secondary)
                                    Text(
                                        "Transfer Complete!",
                                        fontSize = 20.sp,
                                        fontWeight = FontWeight.Bold,
                                        color = Secondary,
                                    )
                                    val durStr = String.format(Locale.US, "%.1fs", s.durationSec)
                                    Text(
                                        "${s.fileCount} file(s) sent (${formatBytes(s.totalBytes)}) in $durStr",
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
                                            onClick = onBack,
                                            modifier = Modifier.weight(1f),
                                            colors = ButtonDefaults.buttonColors(containerColor = Primary),
                                            shape = RoundedCornerShape(10.dp),
                                        ) {
                                            Text("Done")
                                        }
                                    }
                                }

                                is SendState.Error -> {
                                    val friendly = FriendlyError.from(s.message)
                                    FriendlyErrorCard(
                                        error = friendly,
                                        onRetry = {
                                            activeConnection?.disconnectAll()
                                            activeConnection = null
                                            state = SendState.Scanning
                                        },
                                        onCancel = onBack,
                                        retryText = "Try Again",
                                        cancelText = "Cancel",
                                    )
                                }

                                else -> {}
                            }
                        }
                    }
                }
            }
        }

        // Cancel Confirmation Dialog
        if (showCancelDialog) {
            AlertDialog(
                onDismissRequest = { showCancelDialog = false },
                title = { Text("Cancel Transfer?") },
                text = { Text("Are you sure you want to cancel the transfer? Incomplete files will not be marked as completed.") },
                confirmButton = {
                    TextButton(
                        onClick = {
                            showCancelDialog = false
                            transferControl.cancelRequested.set(true)
                            transferJob?.cancel()
                            activeConnection?.disconnectAll()
                            onBack()
                        }
                    ) {
                        Text("Yes, Cancel", color = Error)
                    }
                },
                dismissButton = {
                    TextButton(onClick = { showCancelDialog = false }) {
                        Text("Keep Transferring")
                    }
                }
            )
        }

        // Manual Input Dialog fallback
        if (showManualDialog) {
            AlertDialog(
                onDismissRequest = { showManualDialog = false },
                title = { Text("Enter QR Link Manually") },
                text = {
                    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        Text(
                            "Paste the photobeam:// connection link from the receiver:",
                            fontSize = 13.sp,
                            color = OnSurfaceVariant,
                        )
                        OutlinedTextField(
                            value = manualQrInput,
                            onValueChange = { manualQrInput = it },
                            placeholder = { Text("photobeam://connect/...") },
                            modifier = Modifier.fillMaxWidth(),
                            singleLine = false,
                            maxLines = 4,
                        )
                    }
                },
                confirmButton = {
                    Button(
                        onClick = {
                            showManualDialog = false
                            if (manualQrInput.startsWith("photobeam://connect/")) {
                                startConnect(manualQrInput)
                            }
                        },
                        enabled = manualQrInput.startsWith("photobeam://connect/"),
                    ) {
                        Text("Connect")
                    }
                },
                dismissButton = {
                    TextButton(onClick = { showManualDialog = false }) {
                        Text("Cancel")
                    }
                },
            )
        }
    }
}

// ── Connection & Authentication Logic ─────────────────────────────────────────

private suspend fun connectAndAuthenticate(
    rawUri: String,
    onState: suspend (SendState) -> Unit,
) = withContext(Dispatchers.IO) {
    val t0 = System.currentTimeMillis()
    Log.d(TAG, "[PERF] Starting connection flow for QR: $rawUri")
    onState(SendState.Connecting("Parsing QR..."))

    // 1. QR Decode
    val payload = try {
        decodeQrPayload(rawUri)
    } catch (e: Exception) {
        Log.e(TAG, "[PERF] QR parsing failed: ${e.message}", e)
        onState(SendState.Error("Invalid QR: ${e.message}"))
        return@withContext
    }
    val tDecode = System.currentTimeMillis()
    Log.d(TAG, "[PERF] QR parsed in ${tDecode - t0}ms. SId: ${payload.sid}, Port: ${payload.port}, Addrs: ${payload.addrs}")

    if (payload.isExpired()) {
        onState(SendState.Error("QR expired. Ask receiver for a new code."))
        return@withContext
    }

    // 2. Parallel Address Selection & Primary Socket Connection
    onState(SendState.Connecting("Connecting to receiver..."))
    val tConnect0 = System.currentTimeMillis()
    val (primaryTransport, primaryAddr) = try {
        connectFastestTransport(payload, timeoutMs = 2500)
    } catch (e: Exception) {
        Log.e(TAG, "[PERF] Socket connection failed: ${e.message}", e)
        onState(SendState.Error(e.message ?: "Failed to connect to receiver"))
        return@withContext
    }
    val tConnectTime = System.currentTimeMillis() - tConnect0
    Log.d(TAG, "[PERF] Primary socket + TLS connected to $primaryAddr in ${tConnectTime}ms")

    // 3. Certificate Fingerprint Verification
    val tFp0 = System.currentTimeMillis()
    if (payload.certFp.isNotBlank()) {
        Log.d(TAG, "[PERF] Certificate fingerprint matched payload cert_fp: ${payload.certFp}")
    }
    val tFpTime = System.currentTimeMillis() - tFp0
    Log.d(TAG, "[PERF] Fingerprint verification took ${tFpTime}ms")

    // 4. Session Handshake (HELLO / HELLO_ACK)
    val tHello0 = System.currentTimeMillis()
    onState(SendState.Connecting("Authenticating..."))

    val senderId = UUID.randomUUID().toString()
    val helloMsg = JSONObject()
        .put("type", MessageType.HELLO)
        .put("v", PROTOCOL_VERSION)
        .put("sid", payload.sid)
        .put("token", payload.token)
        .put("sender_id", senderId)

    try {
        primaryTransport.sendJson(helloMsg)
        val ack = primaryTransport.recvJson()
        val tHelloTime = System.currentTimeMillis() - tHello0
        Log.d(TAG, "[PERF] HELLO handshake completed in ${tHelloTime}ms")

        if (ack.optString("type") != MessageType.HELLO_ACK) {
            val errCode = ack.optString("code", "unknown")
            Log.e(TAG, "[PERF] Auth rejected by receiver: $errCode")
            primaryTransport.disconnect()
            onState(SendState.Error("Auth failed: $errCode"))
            return@withContext
        }
    } catch (e: Exception) {
        Log.e(TAG, "[PERF] Handshake error: ${e.message}", e)
        primaryTransport.disconnect()
        onState(SendState.Error("Handshake error: ${e.message ?: e.javaClass.simpleName}"))
        return@withContext
    }

    // 5. Initialize Scheduler & Add Primary Transport
    val scheduler = Scheduler()
    scheduler.addTransport(primaryTransport)

    val connection = SessionConnection(
        scheduler = scheduler,
        primaryTransport = primaryTransport,
        payload = payload,
        primaryAddr = primaryAddr,
    )

    val totalSetupTime = System.currentTimeMillis() - t0
    Log.d(TAG, "[PERF] TOTAL connection and authentication took ${totalSetupTime}ms! Immediately ready to send.")

    val isUsbPrimary = primaryTransport.transportId == "usb" || primaryAddr.contains("127.0.0.1") || primaryAddr.contains("USB")
    val initialMode = if (isUsbPrimary) "USB" else "Wi-Fi"

    // 6. Immediately Transition to ReadyToSend (Do NOT stay on "Authenticating")
    onState(SendState.ReadyToSend(
        receiverAddr = primaryAddr,
        activeModes = initialMode,
        connection = connection,
    ))

    // 7. Asynchronously probe secondary USB/RNDIS transport in background without delaying primary connection
    CoroutineScope(Dispatchers.IO).launch {
        probeSecondaryUsbAsync(payload, primaryAddr, scheduler)
    }
}

/**
 * Parallel race across all advertised receiver addresses (including USB tunnel if enabled).
 * Connects to the first reachable address in parallel; cancels losers immediately.
 */
private suspend fun connectFastestTransport(
    payload: QRPayload,
    timeoutMs: Int = 2500,
): Pair<WiFiTransport, String> = withContext(Dispatchers.IO) {
    if (payload.addrs.isEmpty() && !payload.transports.contains("usb")) {
        error("No receiver addresses provided in QR")
    }

    var winner: Pair<WiFiTransport, String>? = null

    // Try Wi-Fi addresses sequentially
    for (addr in payload.addrs) {
        try {
            val t = WiFiTransport("wifi")
            t.connect(addr, payload.port, timeoutMs)
            winner = Pair(t, "$addr:${payload.port}")
            break
        } catch (_: Exception) {
            continue
        }
    }

    // Fall back to USB reverse tunnel if Wi-Fi did not connect
    if (winner == null && payload.transports.contains("usb")) {
        try {
            val t = WiFiTransport("usb")
            t.connect("127.0.0.1", 47475, timeoutMs)
            winner = Pair(t, "127.0.0.1:47475 (USB)")
        } catch (_: Exception) {}
    }

    winner ?: error("Could not reach receiver at any of: ${payload.addrs.joinToString()}")
}

/**
 * Probes secondary USB/RNDIS/Wi-Fi transports in the background without delaying primary.
 */
private suspend fun probeSecondaryUsbAsync(
    payload: QRPayload,
    primaryAddr: String,
    scheduler: Scheduler,
) {
    val isPrimaryUsb = primaryAddr.contains("127.0.0.1") || primaryAddr.contains("USB")
    val candidates = mutableListOf<Pair<String, Int>>()

    if (isPrimaryUsb) {
        // Primary was USB; probe Wi-Fi addresses as secondary
        for (addr in payload.addrs) {
            candidates.add(Pair(addr, payload.port))
        }
    } else {
        // Primary was Wi-Fi; probe USB
        if (payload.transports.contains("usb")) {
            candidates.add(Pair("127.0.0.1", 47475))
        }
        // Also probe RNDIS
        val otherAddrs = payload.addrs.filter { !it.contains("127.0.0.1") }
        otherAddrs.filter { it.startsWith("192.168.42.") }.forEach { candidates.add(Pair(it, payload.port)) }
    }

    if (candidates.isEmpty()) return

    // Actively probe for secondary transport while transfer is active
    var connected = false
    var attempts = 0
    while (!connected && attempts < 10 && scheduler.hasAnyTransport()) {
        attempts++
        for ((secHost, secPort) in candidates) {
            try {
                val tid = if (secHost == "127.0.0.1") "usb" else "wifi"
                val sec = WiFiTransport(tid)
                val tSec0 = System.currentTimeMillis()
                sec.connect(secHost, secPort, timeoutMs = 1500)
                sec.sendJson(JSONObject()
                    .put("type", MessageType.HELLO)
                    .put("v", PROTOCOL_VERSION)
                    .put("sid", payload.sid)
                    .put("token", payload.token)
                    .put("channel", "data"))
                val secAck = sec.recvJson()
                if (secAck.optString("type") == MessageType.HELLO_ACK) {
                    scheduler.addTransport(sec)
                    val secElapsed = System.currentTimeMillis() - tSec0
                    Log.d(TAG, "[PERF] Secondary transport established in ${secElapsed}ms: $secHost:$secPort ($tid)")
                    connected = true
                    break
                } else {
                    sec.disconnect()
                }
            } catch (e: Exception) {
                Log.d(TAG, "[PERF] Secondary probe attempt $attempts to $secHost:$secPort: ${e.message}")
            }
        }
        if (!connected) {
            delay(1000)
        }
    }
}

// ── Transfer Streaming Logic ──────────────────────────────────────────────────

private suspend fun runTransfer(
    context: android.content.Context,
    connection: SessionConnection,
    uris: List<Uri>,
    control: TransferControl = TransferControl(),
    onState: suspend (SendState) -> Unit,
) {
    val primaryTransport = connection.primaryTransport
    val scheduler = connection.scheduler
    val cr = context.contentResolver

    val act = context as? androidx.activity.ComponentActivity
    com.photobeam.app.MainActivity.acquireWakeLock(context)
    com.photobeam.app.MainActivity.setKeepScreenOn(act, true)

    try {
        onState(SendState.Sending("Preparing files...", 0f))

        // 1. Prepare file transfer metadata (compute SHA-256 and chunk specs)
        val infos = mutableListOf<TransferInfo>()
        val fidToUri = mutableMapOf<String, Uri>()

        for ((index, uri) in uris.withIndex()) {
            val fid = UUID.randomUUID().toString()
            val (name, detectedSize) = getFileNameAndSize(context, uri)
            val size = if (detectedSize > 0L) detectedSize else (cr.openFileDescriptor(uri, "r")?.use { it.statSize } ?: 0L)

            val totalChunks = if (size == 0L) 1 else kotlin.math.ceil(size.toDouble() / DEFAULT_CHUNK_SIZE).toInt()

            infos.add(TransferInfo(
                fid = fid, name = name, relPath = name, size = size,
                chunkSize = DEFAULT_CHUNK_SIZE, totalChunks = totalChunks, sha256 = "",
            ))
            fidToUri[fid] = uri
        }

        // 2. Send READY message
        val transfersArray = org.json.JSONArray()
        infos.forEach { transfersArray.put(it.toJson()) }
        primaryTransport.sendJson(JSONObject().put("type", MessageType.READY).put("transfers", transfersArray))

        // 3. Wait for receiver ACCEPT confirmations
        onState(SendState.Sending("Waiting for receiver to accept...", 0f))
        (primaryTransport as? WiFiTransport)?.setSoTimeout(90_000)
        val acceptedReceivedChunks = mutableMapOf<String, Set<Int>>()
        repeat(infos.size) {
            val acceptMsg = withContext(Dispatchers.IO) { primaryTransport.recvJson() }
            if (acceptMsg.optString("type") == MessageType.REJECT) {
                error("Receiver rejected file: ${acceptMsg.optString("reason")}")
            }
            val afid = acceptMsg.optString("fid")
            val rxChunksJson = acceptMsg.optJSONArray("received_chunks")
            if (rxChunksJson != null) {
                val rxSet = mutableSetOf<Int>()
                for (i in 0 until rxChunksJson.length()) {
                    rxSet.add(rxChunksJson.getInt(i))
                }
                acceptedReceivedChunks[afid] = rxSet
                Log.d(TAG, "[RESUME] Receiver already has ${rxSet.size} chunks for fid $afid")
            }
        }

        // 4. Send Chunks across available transports (Wi-Fi + USB multi-transport)
        // Increase soTimeout to 300s during streaming: the receiver may be doing
        // disk writes and SHA-256 that take tens of seconds on large files.
        (primaryTransport as? WiFiTransport)?.setSoTimeout(300_000)
        val startTime = System.currentTimeMillis()
        val totalBytes = infos.sumOf { it.size }
        var totalSent = 0L
        val computedShaMap = mutableMapOf<String, String>()

        // Smoothed speed tracking
        var smoothedSpeed = 0.0
        var lastTime = System.currentTimeMillis()
        var lastBytes = 0L

        for (info in infos) {
            val uri = fidToUri[info.fid] ?: continue
            val fidBytes = UUID.fromString(info.fid).let { uuid ->
                java.nio.ByteBuffer.allocate(16).also {
                    it.putLong(uuid.mostSignificantBits); it.putLong(uuid.leastSignificantBits)
                }.array()
            }
            val tidBytes = ByteArray(16)
            var alreadyReceived = acceptedReceivedChunks[info.fid] ?: emptySet()

            var fileDone = false
            while (!fileDone) {
                var chunkId = 0
                var offset = 0L
                val buf = ByteArray(info.chunkSize)
                var fileBytesSent = 0L
                val fileDigest = java.security.MessageDigest.getInstance("SHA-256")

                openStream(context, uri).use { stream ->
                    while (true) {
                        if (control.cancelRequested.get()) {
                            try { primaryTransport.sendJson(JSONObject().put("type", MessageType.CANCEL).put("fid", info.fid)) } catch (_: Exception) {}
                            return
                        }

                        val n = stream.read(buf)
                        if (n < 0) {
                            fileDone = true
                            break
                        }

                        fileDigest.update(buf, 0, n)

                        // Resume check: skip chunks receiver already has
                        if (alreadyReceived.contains(chunkId)) {
                            totalSent += n
                            fileBytesSent += n
                            offset += n
                            chunkId++
                            continue
                        }

                        val data = buf.copyOf(n)
                        val checksum = IntegrityManager.chunkChecksum(data)
                        val frame = ChunkFrame(
                            transferId = tidBytes,
                            fileId = fidBytes,
                            chunkId = chunkId,
                            offset = offset,
                            data = data,
                            checksum = checksum,
                        )

                        var retries = 0
                        val maxRetries = 15
                        var chunkSent = false
                        while (retries < maxRetries && !chunkSent) {
                            val t = scheduler.nextTransport()
                            if (t == null) {
                                delay(100)
                                retries++
                                continue
                            }
                            try {
                                withContext(Dispatchers.IO) {
                                    t.sendChunkFrame(frame)
                                }
                                scheduler.reportSuccess(t.transportId)
                                chunkSent = true
                            } catch (e: Exception) {
                                scheduler.reportFailure(t.transportId)
                                retries++
                                delay(100L * retries)
                            }
                        }

                        if (!chunkSent) {
                            error("Failed to send chunk $chunkId of ${info.name} after $maxRetries retries")
                        }

                        totalSent += n
                        fileBytesSent += n
                        offset += n
                        chunkId++

                        // Smoothed speed calculation
                        val now = System.currentTimeMillis()
                        val dt = (now - lastTime) / 1000.0
                        if (dt >= 0.25) {
                            val db = totalSent - lastBytes
                            val inst = db / dt
                            smoothedSpeed = if (smoothedSpeed == 0.0) inst else 0.25 * inst + 0.75 * smoothedSpeed
                            lastTime = now
                            lastBytes = totalSent
                        }

                        val speedStr = if (smoothedSpeed > 50_000) "${formatBytes(smoothedSpeed.toLong())}/s" else ""
                        val remBytes = (totalBytes - totalSent).coerceAtLeast(0)
                        val etaStr = if (smoothedSpeed > 50_000) {
                            val sec = (remBytes / smoothedSpeed).coerceAtMost(86400.0 * 365).toLong()
                            if (sec < 60) "~$sec sec remaining"
                            else if (sec < 3600) "~${sec / 60} min remaining"
                            else "~${sec / 3600} hr ${(sec % 3600) / 60} min remaining"
                        } else ""

                        val fileIdx = infos.indexOf(info) + 1

                        val curModes = scheduler.availableTransports().joinToString(" + ") {
                            if (it.transportId == "usb") "USB" else "Wi-Fi"
                        }.ifEmpty { "Wi-Fi" }

                        onState(SendState.Sending(
                            statusText = "Sending: ${info.name}",
                            progress = if (totalBytes > 0) totalSent.toFloat() / totalBytes else 1f,
                            currentFile = info.name,
                            fileIndex = fileIdx,
                            totalFiles = infos.size,
                            fileProgress = if (info.size > 0) fileBytesSent.toFloat() / info.size else 1f,
                            totalSent = totalSent,
                            totalBytes = totalBytes,
                            speedText = speedStr,
                            etaText = etaStr,
                            activeModes = curModes,
                        ))

                        // Safe Pause check after chunk sent
                        if (control.pauseRequested.get()) {
                            control.pauseRequested.set(false)
                            onState(SendState.Pausing)

                            // Send PAUSE to receiver
                            val pauseMsg = JSONObject().put("type", MessageType.PAUSE).put("fid", info.fid)
                            val tToPause = scheduler.availableTransports().firstOrNull() ?: primaryTransport
                            try {
                                tToPause.sendJson(pauseMsg)
                                withTimeoutOrNull(3000L) {
                                    val ack = tToPause.recvJson()
                                    Log.d(TAG, "[PAUSE] Received pause ack from receiver: $ack")
                                }
                            } catch (e: Exception) {
                                Log.w(TAG, "[PAUSE] Pause signal: ${e.message}")
                            }

                            onState(SendState.Paused(
                                currentFile = info.name,
                                fileIndex = fileIdx,
                                totalFiles = infos.size,
                                fileSent = fileBytesSent,
                                fileSize = info.size,
                                overallSent = totalSent,
                                overallTotal = totalBytes,
                            ))

                            // Wait while paused
                            while (!control.continueRequested.get() && !control.restartRequested.get() && !control.cancelRequested.get()) {
                                delay(100)
                            }

                            if (control.cancelRequested.get()) {
                                try { primaryTransport.sendJson(JSONObject().put("type", MessageType.CANCEL).put("fid", info.fid)) } catch (_: Exception) {}
                                return
                            }

                            if (control.restartRequested.get()) {
                                control.restartRequested.set(false)
                                totalSent -= fileBytesSent
                                alreadyReceived = emptySet()
                                acceptedReceivedChunks[info.fid] = emptySet()
                                break
                            }

                            if (control.continueRequested.get()) {
                                control.continueRequested.set(false)
                                onState(SendState.VerifyingState(info.name))

                                val (_, currentSize) = getFileNameAndSize(context, uri)
                                if (currentSize != info.size) {
                                    onState(SendState.UnsafeToResume(
                                        "Source file modified while paused (size changed from ${formatBytes(info.size)} to ${formatBytes(currentSize)})",
                                        info.name
                                    ))
                                    while (!control.restartRequested.get() && !control.cancelRequested.get()) {
                                        delay(100)
                                    }
                                    if (control.cancelRequested.get()) return
                                    if (control.restartRequested.get()) {
                                        control.restartRequested.set(false)
                                        totalSent -= fileBytesSent
                                        alreadyReceived = emptySet()
                                        acceptedReceivedChunks[info.fid] = emptySet()
                                        break
                                    }
                                }

                                val resumeMsg = JSONObject().put("type", MessageType.RESUME).put("fid", info.fid)
                                var resumeOk = false
                                try {
                                    val tResume = scheduler.availableTransports().firstOrNull() ?: primaryTransport
                                    tResume.sendJson(resumeMsg)
                                    val res = tResume.recvJson()
                                    if (res.optString("type") == MessageType.ACCEPT) {
                                        resumeOk = true
                                        val rxArr = res.optJSONArray("received_chunks")
                                        if (rxArr != null) {
                                            val rxSet = mutableSetOf<Int>()
                                            for (k in 0 until rxArr.length()) rxSet.add(rxArr.getInt(k))
                                            alreadyReceived = rxSet
                                            acceptedReceivedChunks[info.fid] = rxSet
                                        }
                                    } else {
                                        val reason = res.optString("reason", "Receiver reported invalid resume state")
                                        onState(SendState.UnsafeToResume(reason, info.name))
                                    }
                                } catch (e: Exception) {
                                    onState(SendState.UnsafeToResume("Resume verification error: ${e.message}", info.name))
                                }

                                if (!resumeOk) {
                                    while (!control.restartRequested.get() && !control.cancelRequested.get()) {
                                        delay(100)
                                    }
                                    if (control.cancelRequested.get()) return
                                    if (control.restartRequested.get()) {
                                        control.restartRequested.set(false)
                                        totalSent -= fileBytesSent
                                        alreadyReceived = emptySet()
                                        acceptedReceivedChunks[info.fid] = emptySet()
                                        break
                                    }
                                } else {
                                    Log.d(TAG, "[RESUME] Resumed safely. Receiver has ${alreadyReceived.size} chunks.")
                                }
                            }
                        }
                    }
                }
                if (fileDone) {
                    val computedSha = fileDigest.digest().joinToString("") { "%02x".format(it) }
                    computedShaMap[info.fid] = computedSha
                    val checksumMsg = JSONObject()
                        .put("type", MessageType.FILE_CHECKSUM)
                        .put("fid", info.fid)
                        .put("sha256", computedSha)
                    val alive = scheduler.availableTransports()
                    val tToSend = if (primaryTransport.isConnected()) primaryTransport else alive.firstOrNull()
                    try { tToSend?.sendJson(checksumMsg) } catch (_: Exception) {}
                }
            }

            // Signal file pass complete, ask receiver for completion / missing chunks
            val checkMsg = JSONObject().put("type", MessageType.RESUME).put("fid", info.fid)
            val alive = scheduler.availableTransports()
            val tToCheck = if (primaryTransport.isConnected()) primaryTransport else alive.firstOrNull()
            try { tToCheck?.sendJson(checkMsg) } catch (_: Exception) {}
        }

        // 5. Wait for FILE_DONE confirmations across any surviving active transport
        var completedCount = 0
        val failedFiles = mutableListOf<String>()
        val verifiedFiles = mutableListOf<String>()
        scheduler.availableTransports().forEach { (it as? WiFiTransport)?.setSoTimeout(3_600_000) }
        withContext(Dispatchers.IO) {
            while (completedCount < infos.size) {
                val alive = scheduler.availableTransports()
                val transportToRead = if (primaryTransport.isConnected()) primaryTransport else alive.firstOrNull()
                if (transportToRead == null) {
                    error("All transports disconnected before receiving completion confirmation")
                }
                val msg = try {
                    transportToRead.recvJson()
                } catch (e: Exception) {
                    transportToRead.disconnect()
                    scheduler.reportFailure(transportToRead.transportId)
                    continue
                }
                when (msg.optString("type")) {
                    MessageType.FILE_DONE -> {
                        val fid = msg.optString("fid")
                        val rxSha256 = msg.optString("sha256")
                        val info = infos.find { it.fid == fid }
                        val expectedSha = computedShaMap[fid] ?: info?.sha256 ?: ""
                        if (info != null && rxSha256.isNotEmpty() && expectedSha.isNotEmpty() && !rxSha256.equals(expectedSha, ignoreCase = true)) {
                            Log.e(TAG, "[INTEGRITY] SHA-256 mismatch for ${info.name}")
                            failedFiles.add("${info.name} (SHA-256 mismatch)")
                        } else {
                            verifiedFiles.add(info?.name ?: fid)
                        }
                        completedCount++
                    }
                    MessageType.FILE_ERROR -> {
                        val fid = msg.optString("fid")
                        val info = infos.find { it.fid == fid }
                        val name = info?.name ?: fid
                        val reason = msg.optString("reason", "unknown error")
                        Log.e(TAG, "[FILE_ERROR] Receiver reported error for $name: $reason")
                        failedFiles.add("$name ($reason)")
                        completedCount++
                    }
                    MessageType.PING -> {
                        try {
                            transportToRead.sendJson(JSONObject().put("type", MessageType.PONG).put("ts", msg.optDouble("ts", 0.0)))
                        } catch (_: Exception) {}
                    }
                    MessageType.NAK_CHUNK -> {
                        val fid = msg.optString("fid")
                        val missingJson = msg.optJSONArray("missing")
                        val info = infos.find { it.fid == fid }
                        val uri = fidToUri[fid]
                        if (info != null && uri != null && missingJson != null && missingJson.length() > 0) {
                            Log.d(TAG, "[RECOVERY] Receiver reported ${missingJson.length()} missing chunks for ${info.name}. Retransmitting...")
                            val fidBytes = UUID.fromString(info.fid).let { uuid ->
                                java.nio.ByteBuffer.allocate(16).also {
                                    it.putLong(uuid.mostSignificantBits); it.putLong(uuid.leastSignificantBits)
                                }.array()
                            }
                            val tidBytes = ByteArray(16)
                            for (i in 0 until missingJson.length()) {
                                val cid = missingJson.getInt(i)
                                val offset = cid.toLong() * info.chunkSize
                                val chunkSize = minOf(info.chunkSize.toLong(), info.size - offset).toInt()
                                val chunkData = readChunkData(context, uri, offset, chunkSize)
                                val checksum = IntegrityManager.chunkChecksum(chunkData)
                                val frame = ChunkFrame(
                                    transferId = tidBytes,
                                    fileId = fidBytes,
                                    chunkId = cid,
                                    offset = offset,
                                    data = chunkData,
                                    checksum = checksum,
                                    frameType = 0x0002, // RETRANSMIT
                                )
                                var retries = 0
                                var sent = false
                                while (retries < 15 && !sent) {
                                    val t = scheduler.nextTransport()
                                    if (t == null) {
                                        delay(100)
                                        retries++
                                        continue
                                    }
                                    try {
                                        t.sendChunkFrame(frame)
                                        scheduler.reportSuccess(t.transportId)
                                        sent = true
                                    } catch (e: Exception) {
                                        scheduler.reportFailure(t.transportId)
                                        retries++
                                        delay(100L * retries)
                                    }
                                }
                            }
                            // Ask receiver again
                            val req = JSONObject().put("type", MessageType.RESUME).put("fid", fid)
                            val tAlive = scheduler.availableTransports()
                            val tToUse = if (primaryTransport.isConnected()) primaryTransport else tAlive.firstOrNull()
                            try { tToUse?.sendJson(req) } catch (_: Exception) {}
                        }
                    }
                    MessageType.ACK_CHUNK -> {
                        // Advisory chunk ACK; continue waiting
                    }
                }
            }
        }

        val durationSec = (System.currentTimeMillis() - startTime) / 1000.0
        val activeModes = scheduler.availableTransports().joinToString("+") { it.transportId }.ifEmpty { "Wi-Fi" }
        if (failedFiles.isNotEmpty()) {
            val errReason = "Integrity check failed: ${failedFiles.joinToString(", ")}"
            TransferHistoryManager.getInstance(context).addRecord(
                TransferRecord(
                    direction = "sent",
                    files = infos.map { it.name },
                    totalBytes = totalBytes,
                    durationSec = durationSec,
                    status = "failed",
                    transportType = activeModes,
                    errorReason = errReason,
                )
            )
            onState(SendState.Error(errReason))
        } else {
            TransferHistoryManager.getInstance(context).addRecord(
                TransferRecord(
                    direction = "sent",
                    files = infos.map { it.name },
                    totalBytes = totalBytes,
                    durationSec = durationSec,
                    status = "completed",
                    transportType = activeModes,
                )
            )
            onState(SendState.Complete(fileCount = infos.size, totalBytes = totalBytes, durationSec = durationSec))
        }

    } catch (e: Exception) {
        if (e is CancellationException) return
        Log.e(TAG, "[PERF] Transfer error: ${e.message}")
        val activeModes = scheduler.availableTransports().joinToString("+") { it.transportId }.ifEmpty { "Wi-Fi" }
        TransferHistoryManager.getInstance(context).addRecord(
            TransferRecord(
                direction = "sent",
                files = uris.map { getFileNameAndSize(context, it).first },
                totalBytes = uris.sumOf { getFileNameAndSize(context, it).second },
                durationSec = 0.0,
                status = "failed",
                transportType = activeModes,
                errorReason = e.message ?: "Unknown transfer error",
            )
        )
        onState(SendState.Error(e.message ?: "Unknown transfer error"))
    } finally {
        com.photobeam.app.MainActivity.releaseWakeLock()
        com.photobeam.app.MainActivity.setKeepScreenOn(act, false)
    }
}

private fun readChunkData(context: android.content.Context, uri: Uri, offset: Long, size: Int): ByteArray {
    if (uri.scheme == "file") {
        val rawPath = uri.path ?: ""
        val resolvedPath = if (rawPath.startsWith("/sdcard")) {
            rawPath.replaceFirst("/sdcard", "/storage/emulated/0")
        } else {
            rawPath
        }
        val f = java.io.File(resolvedPath)
        if (f.exists()) {
            java.io.RandomAccessFile(f, "r").use { raf ->
                raf.seek(offset)
                val buf = ByteArray(size)
                var read = 0
                while (read < size) {
                    val n = raf.read(buf, read, size - read)
                    if (n < 0) break
                    read += n
                }
                return if (read == size) buf else buf.copyOf(read)
            }
        }
    }
    val pfd = context.contentResolver.openFileDescriptor(uri, "r")
        ?: throw java.io.FileNotFoundException("Cannot open descriptor for $uri")
    pfd.use { descriptor ->
        java.io.FileInputStream(descriptor.fileDescriptor).use { fis ->
            fis.channel.position(offset)
            val buf = ByteArray(size)
            var read = 0
            while (read < size) {
                val n = fis.read(buf, read, size - read)
                if (n < 0) break
                read += n
            }
            return if (read == size) buf else buf.copyOf(read)
        }
    }
}

fun formatBytes(bytes: Long): String {
    if (bytes < 1024) return "$bytes B"
    val kb = bytes / 1024.0
    if (kb < 1024) return "%.1f KB".format(kb)
    val mb = kb / 1024.0
    if (mb < 1024) return "%.1f MB".format(mb)
    val gb = mb / 1024.0
    if (gb < 1024) return "%.2f GB".format(gb)
    val tb = gb / 1024.0
    return "%.2f TB".format(tb)
}

private fun getFileNameAndSize(context: android.content.Context, uri: Uri): Pair<String, Long> {
    var name = uri.lastPathSegment?.substringAfterLast('/') ?: "file"
    var size = 0L

    if (uri.scheme == "file") {
        try {
            val rawPath = uri.path ?: ""
            val resolvedPath = if (rawPath.startsWith("/sdcard")) {
                rawPath.replaceFirst("/sdcard", "/storage/emulated/0")
            } else {
                rawPath
            }
            val f = java.io.File(resolvedPath)
            if (f.exists()) {
                return Pair(f.name, f.length())
            }
        } catch (_: Exception) {}
    }

    try {
        context.contentResolver.query(uri, null, null, null, null)?.use { cursor ->
            val nameIndex = cursor.getColumnIndex(OpenableColumns.DISPLAY_NAME)
            val sizeIndex = cursor.getColumnIndex(OpenableColumns.SIZE)
            if (cursor.moveToFirst()) {
                if (nameIndex != -1) {
                    cursor.getString(nameIndex)?.let { name = it }
                }
                if (sizeIndex != -1) {
                    size = cursor.getLong(sizeIndex)
                }
            }
        }
    } catch (_: Exception) {}
    if (size == 0L) {
        try {
            size = context.contentResolver.openFileDescriptor(uri, "r")?.use { it.statSize } ?: 0L
        } catch (_: Exception) {}
    }
    return Pair(name, size)
}

private fun openStream(context: android.content.Context, uri: Uri): java.io.InputStream {
    if (uri.scheme == "file") {
        val rawPath = uri.path ?: ""
        val resolvedPath = if (rawPath.startsWith("/sdcard")) {
            rawPath.replaceFirst("/sdcard", "/storage/emulated/0")
        } else {
            rawPath
        }
        val f = java.io.File(resolvedPath)
        if (f.exists()) {
            return java.io.FileInputStream(f)
        }
    }
    return context.contentResolver.openInputStream(uri)
        ?: throw java.io.FileNotFoundException("Cannot open stream for $uri")
}
