package com.photobeam.app.ui.screens

import android.os.Handler
import android.os.Looper
import android.widget.Toast
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

@Composable
fun HomeScreen(
    onNavigateReceive: () -> Unit,
    onNavigateSend: () -> Unit,
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

    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(AppBackgroundBrush)
            .statusBarsPadding()
            .navigationBarsPadding()
            .padding(horizontal = 20.dp, vertical = 14.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp)
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

        // ── Active Session Hero Card (Connected) OR Explicit "Scan PC QR" Card (Disconnected) ──
        if (connectedDevice != null) {
            TactileCard(
                modifier = Modifier.fillMaxWidth(),
                backgroundColor = SurfaceElevated,
                borderColor = PrimaryGlow.copy(alpha = 0.5f),
                elevation = 10.dp
            ) {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    TactileGlowRing(
                        size = 50.dp,
                        ringColor = Secondary,
                        pulse = true
                    ) {
                        Text(
                            text = if (connectedDevice.identity.name.contains("PC", true) || connectedDevice.identity.name.contains("Windows", true)) "💻" else "📱",
                            fontSize = 24.sp
                        )
                    }

                    Spacer(Modifier.width(14.dp))

                    Column(modifier = Modifier.weight(1f)) {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Text(
                                text = connectedDevice.identity.name,
                                fontSize = 17.sp,
                                fontWeight = FontWeight.Bold,
                                color = OnBackground,
                                maxLines = 1,
                                overflow = TextOverflow.Ellipsis
                            )
                            Spacer(Modifier.width(8.dp))
                            TactileBadge("🟢 Connected", Secondary)
                        }

                        val transportStr = connectedDevice.endpoint?.transports?.joinToString(" • ") {
                            if (it == "usb") "⚡ USB Tunnel" else "📶 Wi-Fi"
                        } ?: "📶 Connected"
                        Text(
                            text = transportStr,
                            fontSize = 12.sp,
                            color = CyanAccent,
                            modifier = Modifier.padding(top = 2.dp)
                        )
                    }
                }

                Spacer(Modifier.height(14.dp))

                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.spacedBy(10.dp)
                ) {
                    TactilePillButton(
                        text = "📤 Send Files",
                        active = true,
                        onClick = onNavigateSend,
                        modifier = Modifier.weight(1f),
                        minHeight = 40.dp
                    )
                    TactilePillButton(
                        text = "Disconnect",
                        active = false,
                        onClick = {
                            connectionManager.disconnectDevice(connectedDevice.identity.deviceId)
                        },
                        modifier = Modifier.weight(0.9f),
                        minHeight = 40.dp
                    )
                }
            }
        } else {
            TactileCard(
                modifier = Modifier.fillMaxWidth(),
                backgroundColor = SurfaceElevated,
                borderColor = CyanAccent.copy(alpha = 0.35f),
                elevation = 6.dp,
                onClick = onNavigatePair
            ) {
                Column(
                    modifier = Modifier.fillMaxWidth(),
                    verticalArrangement = Arrangement.spacedBy(10.dp)
                ) {
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Row(
                            modifier = Modifier.weight(1f, fill = false),
                            verticalAlignment = Alignment.CenterVertically,
                            horizontalArrangement = Arrangement.spacedBy(10.dp)
                        ) {
                            TactileGlowRing(
                                size = 40.dp,
                                ringColor = CyanAccent,
                                pulse = false
                            ) {
                                Text("📷", fontSize = 20.sp)
                            }
                            Column {
                                Text(
                                    text = "Scan PC QR to Connect",
                                    fontSize = 15.sp,
                                    fontWeight = FontWeight.Bold,
                                    color = OnBackground,
                                    maxLines = 1,
                                    overflow = TextOverflow.Ellipsis
                                )
                                Text(
                                    text = "Point camera at PC to connect",
                                    fontSize = 11.sp,
                                    color = OnSurfaceVariant,
                                    maxLines = 1
                                )
                            }
                        }

                        Spacer(Modifier.width(8.dp))
                        TactileBadge("⚪ Disconnected", OnSurfaceVariant)
                    }

                    TactilePillButton(
                        text = "📷 Scan PC QR Code",
                        active = true,
                        onClick = onNavigatePair,
                        modifier = Modifier.fillMaxWidth(),
                        minHeight = 42.dp
                    )
                }
            }
        }

        // ── Quick Actions Grid (Send & Receive) ────────────────────────────────
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(12.dp)
        ) {
            // Send Action Card
            TactileCard(
                modifier = Modifier.weight(1f),
                cornerRadius = 20.dp,
                backgroundColor = Surface,
                borderColor = CardBorder,
                onClick = onNavigateSend
            ) {
                Column(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalAlignment = Alignment.CenterHorizontally,
                    verticalArrangement = Arrangement.spacedBy(6.dp)
                ) {
                    TactileGlowRing(size = 46.dp, ringColor = PrimaryLight) {
                        Text("📤", fontSize = 20.sp)
                    }
                    Text(
                        "Send Files",
                        fontSize = 15.sp,
                        fontWeight = FontWeight.Bold,
                        color = OnBackground
                    )
                    Text(
                        "Select or scan QR",
                        fontSize = 11.sp,
                        color = OnSurfaceVariant
                    )
                }
            }

            // Receive Action Card
            TactileCard(
                modifier = Modifier.weight(1f),
                cornerRadius = 20.dp,
                backgroundColor = Surface,
                borderColor = CardBorder,
                onClick = onNavigateReceive
            ) {
                Column(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalAlignment = Alignment.CenterHorizontally,
                    verticalArrangement = Arrangement.spacedBy(6.dp)
                ) {
                    TactileGlowRing(size = 46.dp, ringColor = CyanAccent) {
                        Text("📥", fontSize = 20.sp)
                    }
                    Text(
                        "Receive",
                        fontSize = 15.sp,
                        fontWeight = FontWeight.Bold,
                        color = OnBackground
                    )
                    Text(
                        "Ready for incoming",
                        fontSize = 11.sp,
                        color = OnSurfaceVariant
                    )
                }
            }
        }

        // ── Paired Devices Section ────────────────────────────────────────────
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(top = 4.dp),
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

        if (pairedDevices.isEmpty()) {
            TactileCard(
                modifier = Modifier
                    .fillMaxWidth()
                    .weight(1f),
                cornerRadius = 22.dp,
                backgroundColor = Surface,
                borderColor = CardBorderSubtle,
            ) {
                Box(
                    modifier = Modifier.fillMaxSize(),
                    contentAlignment = Alignment.Center
                ) {
                    Column(
                        horizontalAlignment = Alignment.CenterHorizontally,
                        verticalArrangement = Arrangement.spacedBy(12.dp),
                        modifier = Modifier.padding(16.dp)
                    ) {
                        TactileGlowRing(size = 64.dp, ringColor = CyanAccent) {
                            Text("💻", fontSize = 32.sp)
                        }
                        Text(
                            "No Paired Devices Yet",
                            fontSize = 17.sp,
                            fontWeight = FontWeight.Bold,
                            color = OnBackground
                        )
                        Text(
                            "Tap 'Pair' above to scan your PC's QR code and unlock instant 1-tap transfers.",
                            fontSize = 13.sp,
                            color = OnSurfaceVariant,
                            textAlign = androidx.compose.ui.text.style.TextAlign.Center
                        )
                        Spacer(Modifier.height(4.dp))
                        TactilePillButton(
                            text = "Pair with PC",
                            active = true,
                            onClick = onNavigatePair,
                            minHeight = 42.dp
                        )
                    }
                }
            }
        } else {
            LazyColumn(
                modifier = Modifier.weight(1f),
                verticalArrangement = Arrangement.spacedBy(12.dp)
            ) {
                items(pairedDevices, key = { it.identity.deviceId }) { device ->
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
    }
}

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

    TactileCard(
        modifier = Modifier.fillMaxWidth(),
        cornerRadius = 18.dp,
        backgroundColor = if (isConnected) SurfaceElevated else Surface,
        borderColor = if (isConnected) PrimaryGlow.copy(alpha = 0.45f) else CardBorder,
    ) {
        Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
            // Top Row: Icon + Name + Presence + Menu
            Row(
                modifier = Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically
            ) {
                TactileGlowRing(
                    size = 40.dp,
                    ringColor = if (isConnected) Secondary else if (device.presenceState == PresenceState.DISCOVERED) CyanAccent else Outline
                ) {
                    Text(
                        text = if (device.identity.name.contains("PC", true) || device.identity.name.contains("Windows", true)) "💻" else "📱",
                        fontSize = 18.sp
                    )
                }

                Spacer(Modifier.width(12.dp))

                Column(modifier = Modifier.weight(1f)) {
                    Text(
                        device.identity.name,
                        fontSize = 15.sp,
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
                    Text(statusText, fontSize = 11.sp, color = OnSurfaceVariant)
                }

                // Connection badge
                val (connColor, connLabel) = when (device.connectionState) {
                    ConnectionState.CONNECTED -> Pair(Secondary, "🟢 Connected")
                    ConnectionState.CONNECTING -> Pair(PrimaryLight, "🟡 Connecting")
                    ConnectionState.AUTHENTICATION_REQUIRED -> Pair(Error, "Auth Req")
                    else -> Pair(OnSurfaceVariant, "⚪ Disconnected")
                }
                TactileBadge(connLabel, connColor)

                Spacer(Modifier.width(4.dp))

                Box {
                    IconButton(
                        onClick = { showMenu = true },
                        modifier = Modifier.size(32.dp)
                    ) {
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

            // Transport and Capability Pills
            Row(
                horizontalArrangement = Arrangement.spacedBy(8.dp),
                verticalAlignment = Alignment.CenterVertically
            ) {
                device.endpoint?.transports?.forEach { tr ->
                    val icon = if (tr == "usb") "⚡ USB Tunnel" else "📶 Wi-Fi"
                    TactileBadge(icon, PrimaryLight)
                }
                TactileBadge("📁 Fast Transfer", CyanAccent)
            }

            // Action Buttons
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(8.dp)
            ) {
                TactilePillButton(
                    text = if (isConnected) "Disconnect" else "Connect",
                    active = !isConnected,
                    onClick = onConnectToggle,
                    modifier = Modifier.weight(1f),
                    minHeight = 36.dp
                )

                TactilePillButton(
                    text = "📤 Send",
                    active = isConnected,
                    onClick = onSendFiles,
                    modifier = Modifier.weight(0.9f),
                    minHeight = 36.dp
                )

                TactilePillButton(
                    text = "🖥️ Mirror",
                    active = false,
                    onClick = onMirror,
                    modifier = Modifier.weight(0.9f),
                    minHeight = 36.dp
                )
            }
        }
    }
}
