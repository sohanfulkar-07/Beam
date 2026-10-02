package com.photobeam.app.ui.screens

import android.os.Handler
import android.os.Looper
import android.widget.Toast
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.photobeam.app.data.ConnectionManager
import com.photobeam.app.data.PairingManager
import com.photobeam.app.protocol.ConnectionState
import com.photobeam.app.protocol.PairedDevice
import com.photobeam.app.protocol.PresenceState
import com.photobeam.app.protocol.TrustStatus
import com.photobeam.app.ui.components.TactileBadge
import com.photobeam.app.ui.components.TactileCard
import com.photobeam.app.ui.components.TactileGlowRing
import com.photobeam.app.ui.components.TactilePillButton
import com.photobeam.app.ui.theme.*
import android.net.Uri

@Composable
fun HomeScreen(
    onNavigateReceive: () -> Unit,
    onNavigateSend: () -> Unit,
    onNavigateSendWithFiles: (List<Uri>) -> Unit = {},
    onNavigatePair: () -> Unit,
    onNavigateHistory: () -> Unit = {},
    onNavigateMirror: (String) -> Unit = {},
) {
    val context = LocalContext.current
    val connectionManager = remember { ConnectionManager.getInstance(context) }
    val pairingManager = remember { PairingManager.getInstance(context) }

    val pairedDevices by connectionManager.pairedDevicesFlow.collectAsState()
    val localIdentity = remember { pairingManager.getLocalIdentity() }

    LaunchedEffect(Unit) {
        connectionManager.start()
        connectionManager.refreshDevicesList()
    }

    val connectedDevice = pairedDevices.firstOrNull { it.connectionState == ConnectionState.CONNECTED }

    // Keep foreground service alive while a session is active
    LaunchedEffect(connectedDevice) {
        if (connectedDevice != null) {
            com.photobeam.app.service.ConnectionForegroundService.start(
                context,
                "Connected to ${connectedDevice.identity.name}"
            )
        }
    }

    // File picker — launched when "Send Files" is tapped while connected
    val filePicker = rememberLauncherForActivityResult(
        ActivityResultContracts.OpenMultipleDocuments()
    ) { uris ->
        if (uris.isNotEmpty() && connectedDevice != null) {
            onNavigateSendWithFiles(uris)
        }
    }

    // Devices that are NOT the currently connected one (avoid duplication)
    val otherDevices = pairedDevices.filter {
        it.identity.deviceId != connectedDevice?.identity?.deviceId
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(AppBackgroundBrush)
            .statusBarsPadding()
            .navigationBarsPadding()
            .padding(horizontal = 16.dp, vertical = 14.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp)
    ) {
        // ── Top Header ────────────────────────────────────────────────────────
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Row(
                modifier = Modifier.weight(1f, fill = false),
                verticalAlignment = Alignment.CenterVertically
            ) {
                TactileGlowRing(
                    size = 40.dp,
                    ringColor = PrimaryGlow,
                    pulse = connectedDevice != null
                ) {
                    Text("⚡", fontSize = 20.sp)
                }
                Spacer(Modifier.width(12.dp))
                Column {
                    Text(
                        "PhotoBeam",
                        fontSize = 20.sp,
                        fontWeight = FontWeight.Bold,
                        color = OnBackground,
                        maxLines = 1
                    )
                    Text(
                        "📱 ${localIdentity.name}",
                        fontSize = 11.sp,
                        color = CyanAccent,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis
                    )
                }
            }

            Row(
                horizontalArrangement = Arrangement.spacedBy(8.dp),
                verticalAlignment = Alignment.CenterVertically
            ) {
                TactilePillButton(
                    text = "➕ Pair",
                    active = true,
                    onClick = onNavigatePair,
                    minHeight = 38.dp
                )

                Box(
                    modifier = Modifier
                        .size(38.dp)
                        .clip(RoundedCornerShape(12.dp))
                        .background(SurfaceElevated)
                        .border(1.dp, CardBorderSubtle, RoundedCornerShape(12.dp))
                        .clickable(onClick = onNavigateHistory),
                    contentAlignment = Alignment.Center
                ) {
                    Text("📜", fontSize = 16.sp)
                }
            }
        }

        // ── Main Content ──────────────────────────────────────────────────────
        if (connectedDevice != null) {
            // ── Single Active Session Card (Only card shown when connected) ──
            ActiveSessionCard(
                device = connectedDevice,
                onSendFiles = {
                    filePicker.launch(arrayOf("*/*"))
                },
                onDisconnect = {
                    connectionManager.disconnectDevice(connectedDevice.identity.deviceId)
                }
            )
        } else {
            // ── Disconnected Hero Card ────────────────────────────────────────
            TactileCard(
                modifier = Modifier.fillMaxWidth(),
                backgroundColor = SurfaceElevated,
                borderColor = CyanAccent.copy(alpha = 0.45f),
                elevation = 8.dp,
            ) {
                Column(
                    modifier = Modifier.fillMaxWidth(),
                    verticalArrangement = Arrangement.spacedBy(14.dp)
                ) {
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Row(
                            modifier = Modifier.weight(1f, fill = false),
                            verticalAlignment = Alignment.CenterVertically,
                            horizontalArrangement = Arrangement.spacedBy(12.dp)
                        ) {
                            TactileGlowRing(size = 46.dp, ringColor = CyanAccent, pulse = false) {
                                Text("💻", fontSize = 22.sp)
                            }
                            Column {
                                Text(
                                    text = "Connect to PC",
                                    fontSize = 17.sp,
                                    fontWeight = FontWeight.Bold,
                                    color = OnBackground,
                                    maxLines = 1,
                                    overflow = TextOverflow.Ellipsis
                                )
                                Text(
                                    text = "Pair via Wi-Fi or USB cable",
                                    fontSize = 12.sp,
                                    color = OnSurfaceVariant,
                                    maxLines = 1
                                )
                            }
                        }
                        TactileBadge("⚪ Disconnected", OnSurfaceVariant)
                    }

                    TactilePillButton(
                        text = "📷 Scan PC QR Code",
                        active = true,
                        onClick = onNavigatePair,
                        modifier = Modifier.fillMaxWidth(),
                        minHeight = 46.dp
                    )
                }
            }

            // ── Trusted Devices list with Connect & Delete ────────────────────
            val trustedDevices = pairedDevices.filter { it.identity.trustStatus == TrustStatus.TRUSTED }
            if (trustedDevices.isNotEmpty()) {
                TrustedDevicesSection(
                    pairedDevices = trustedDevices,
                    connectionManager = connectionManager,
                    context = context,
                    onNavigateSend = onNavigateSend,
                    onNavigateMirror = onNavigateMirror,
                    showCount = true
                )
            }
        }
    }
}

// ── Active Session Hero Card ──────────────────────────────────────────────────

@Composable
private fun ActiveSessionCard(
    device: PairedDevice,
    onSendFiles: () -> Unit,
    onDisconnect: () -> Unit,
) {
    val isUsb = device.endpoint?.transports?.contains("usb") == true
    val transportStr = if (isUsb) "USB Tunnel" else "Wi-Fi"
    val transportBadgeColor = if (isUsb) CyanAccent else Secondary

    TactileCard(
        modifier = Modifier.fillMaxWidth(),
        backgroundColor = SurfaceElevated,
        borderColor = PrimaryGlow.copy(alpha = 0.55f),
        elevation = 10.dp
    ) {
        // Device info row
        Row(
            modifier = Modifier.fillMaxWidth(),
            verticalAlignment = Alignment.CenterVertically
        ) {
            TactileGlowRing(size = 50.dp, ringColor = Secondary, pulse = true) {
                Text(
                    text = "💻",
                    fontSize = 24.sp
                )
            }
            Spacer(Modifier.width(14.dp))
            Column(modifier = Modifier.weight(1f)) {
                Text(
                    text = device.identity.name,
                    fontSize = 17.sp,
                    fontWeight = FontWeight.Bold,
                    color = OnBackground,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis
                )
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(6.dp),
                    modifier = Modifier.padding(top = 4.dp)
                ) {
                    TactileBadge(transportStr, transportBadgeColor)
                }
            }
            TactileBadge("🟢 Connected", Secondary)
        }

        Spacer(Modifier.height(14.dp))

        // Action buttons — exactly two primary buttons: 📤 Send Files and Disconnect
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(10.dp)
        ) {
            TactilePillButton(
                text = "📤 Send Files",
                active = true,
                onClick = onSendFiles,
                modifier = Modifier.weight(1f),
                minHeight = 44.dp
            )
            TactilePillButton(
                text = "Disconnect",
                active = false,
                onClick = onDisconnect,
                modifier = Modifier.weight(1f),
                minHeight = 44.dp
            )
        }
    }
}

// ── Trusted Devices Section ───────────────────────────────────────────────────

@Composable
private fun TrustedDevicesSection(
    pairedDevices: List<PairedDevice>,
    connectionManager: ConnectionManager,
    context: android.content.Context,
    onNavigateSend: () -> Unit,
    onNavigateMirror: (String) -> Unit,
    showCount: Boolean,
) {
    if (pairedDevices.isEmpty()) return

    Column(modifier = Modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        if (showCount) {
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(top = 2.dp),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Text(
                    "Trusted Devices",
                    fontSize = 16.sp,
                    fontWeight = FontWeight.Bold,
                    color = OnBackground,
                )
                Text(
                    "${pairedDevices.size} paired",
                    fontSize = 12.sp,
                    color = OnSurfaceVariant
                )
            }
        }

        pairedDevices.forEach { device ->
            DeviceCard(
                device = device,
                onConnectToggle = {
                    if (device.connectionState == ConnectionState.CONNECTED) {
                        connectionManager.disconnectDevice(device.identity.deviceId)
                    } else {
                        connectionManager.connectDevice(
                            deviceId = device.identity.deviceId,
                            onConnected = {
                                Handler(Looper.getMainLooper()).post {
                                    Toast.makeText(context, "Connected to ${device.identity.name}", Toast.LENGTH_SHORT).show()
                                }
                            },
                            onFailed = { err ->
                                Handler(Looper.getMainLooper()).post {
                                    Toast.makeText(context, "Connection failed: $err", Toast.LENGTH_SHORT).show()
                                }
                            }
                        )
                    }
                },
                onSendFiles = onNavigateSend,
                onMirror = { onNavigateMirror(device.identity.deviceId) },
                onForget = { connectionManager.forgetDevice(device.identity.deviceId) },
                onRevoke = { connectionManager.revokeTrust(device.identity.deviceId) },
            )
        }
    }
}

// ── Device Card ───────────────────────────────────────────────────────────────

@Composable
private fun DeviceCard(
    device: PairedDevice,
    onConnectToggle: () -> Unit,
    onSendFiles: () -> Unit,
    onMirror: () -> Unit,
    onForget: () -> Unit,
    onRevoke: () -> Unit,
) {
    var showMenu by remember { mutableStateOf(false) }
    val isConnected = device.connectionState == ConnectionState.CONNECTED
    val isConnecting = device.connectionState == ConnectionState.CONNECTING

    TactileCard(
        modifier = Modifier.fillMaxWidth(),
        cornerRadius = 18.dp,
        backgroundColor = if (isConnected) SurfaceElevated else Surface,
        borderColor = if (isConnected) PrimaryGlow.copy(alpha = 0.45f) else CardBorder,
    ) {
        Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
            // Top Row: Icon + Name + Status badge + Menu
            Row(
                modifier = Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically
            ) {
                TactileGlowRing(
                    size = 40.dp,
                    ringColor = if (isConnected) Secondary else if (device.presenceState == PresenceState.DISCOVERED) CyanAccent else Outline
                ) {
                    Text(
                        text = if (device.identity.name.contains("PC", true) || device.identity.name.contains("Windows", true) || device.identity.name.contains("LOQ", true)) "💻" else "📱",
                        fontSize = 18.sp
                    )
                }
                Spacer(Modifier.width(12.dp))
                Column(modifier = Modifier.weight(1f)) {
                    Text(
                        device.identity.name,
                        fontSize = 14.sp,
                        fontWeight = FontWeight.Bold,
                        color = OnBackground,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis
                    )
                    val statusText = when (device.presenceState) {
                        PresenceState.DISCOVERED -> "🟢 Available on LAN"
                        PresenceState.SEARCHING -> "🟡 Searching"
                        else -> "⚪ Offline"
                    }
                    Text(statusText, fontSize = 11.sp, color = OnSurfaceVariant, maxLines = 1)
                }

                val (connColor, connLabel) = when (device.connectionState) {
                    ConnectionState.CONNECTED -> Pair(Secondary, "🟢 Connected")
                    ConnectionState.CONNECTING -> Pair(PrimaryLight, "🟡 Connecting")
                    ConnectionState.AUTHENTICATION_REQUIRED -> Pair(Error, "Auth Req")
                    else -> Pair(OnSurfaceVariant, "⚪ Offline")
                }
                TactileBadge(connLabel, connColor)

                Spacer(Modifier.width(4.dp))

                Box {
                    IconButton(onClick = { showMenu = true }, modifier = Modifier.size(32.dp)) {
                        Text("⋮", fontSize = 18.sp, color = OnSurfaceVariant)
                    }
                    DropdownMenu(
                        expanded = showMenu,
                        onDismissRequest = { showMenu = false },
                        modifier = Modifier.background(SurfaceElevated)
                    ) {
                        DropdownMenuItem(
                            text = { Text("🗑️ Forget Device", color = OnSurface) },
                            onClick = { showMenu = false; onForget() }
                        )
                        DropdownMenuItem(
                            text = { Text("🚫 Revoke Trust", color = Error) },
                            onClick = { showMenu = false; onRevoke() }
                        )
                    }
                }
            }

            // Transport Pills (only when discovered)
            if (device.endpoint?.transports?.isNotEmpty() == true) {
                Row(
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    device.endpoint.transports.forEach { tr ->
                        val icon = if (tr == "usb") "⚡ USB Tunnel" else "📶 Wi-Fi"
                        TactileBadge(icon, PrimaryLight)
                    }
                    TactileBadge("📁 Fast Transfer", CyanAccent)
                }
            }

            // Action Buttons — 3 actions in a clean row with equal distribution
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(8.dp)
            ) {
                val btnText = when {
                    isConnecting -> "Connecting…"
                    isConnected -> "Disconnect"
                    else -> "Connect"
                }
                TactilePillButton(
                    text = btnText,
                    active = !isConnected && !isConnecting,
                    onClick = { if (!isConnecting) onConnectToggle() },
                    modifier = Modifier.weight(1f),
                    minHeight = 36.dp
                )
                TactilePillButton(
                    text = "📤 Send",
                    active = isConnected,
                    onClick = onSendFiles,
                    modifier = Modifier.weight(1f),
                    minHeight = 36.dp
                )
                TactilePillButton(
                    text = "🖥️ Mirror",
                    active = false,
                    onClick = onMirror,
                    modifier = Modifier.weight(1f),
                    minHeight = 36.dp
                )
            }
        }
    }
}
