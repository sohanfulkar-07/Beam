package com.photobeam.app.ui.screens

import android.os.Handler
import android.os.Looper
import android.widget.Toast
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
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

    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(Background)
            .statusBarsPadding()
            .navigationBarsPadding()
            .padding(horizontal = 16.dp, vertical = 12.dp),
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
                Text("⚡", fontSize = 24.sp)
                Spacer(Modifier.width(6.dp))
                Column {
                    Text(
                        "PhotoBeam",
                        fontSize = 19.sp,
                        fontWeight = FontWeight.Bold,
                        color = OnBackground,
                        maxLines = 1
                    )
                    Text(
                        "📱 ${localIdentity.name}",
                        fontSize = 11.sp,
                        color = Secondary,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis
                    )
                }
            }

            Spacer(Modifier.width(6.dp))

            Row(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalAlignment = Alignment.CenterVertically) {
                Button(
                    onClick = onNavigatePair,
                    colors = ButtonDefaults.buttonColors(containerColor = Primary),
                    shape = RoundedCornerShape(10.dp),
                    contentPadding = PaddingValues(horizontal = 10.dp, vertical = 6.dp)
                ) {
                    Text("➕ Pair", fontSize = 12.sp, fontWeight = FontWeight.SemiBold)
                }

                IconButton(
                    onClick = onNavigateHistory,
                    modifier = Modifier.size(36.dp).clip(RoundedCornerShape(10.dp)).background(Surface)
                ) {
                    Text("📜", fontSize = 16.sp)
                }
            }
        }


        // ── Trusted Devices Section ───────────────────────────────────────────
        Text(
            "Trusted Devices",
            fontSize = 16.sp,
            fontWeight = FontWeight.SemiBold,
            color = OnSurfaceVariant,
            modifier = Modifier.padding(top = 4.dp)
        )

        if (pairedDevices.isEmpty()) {
            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .weight(1f)
                    .clip(RoundedCornerShape(16.dp))
                    .background(Surface)
                    .padding(24.dp),
                contentAlignment = Alignment.Center
            ) {
                Column(
                    horizontalAlignment = Alignment.CenterHorizontally,
                    verticalArrangement = Arrangement.spacedBy(12.dp)
                ) {
                    Text("💻", fontSize = 42.sp)
                    Text(
                        "No Paired Devices Yet",
                        fontSize = 18.sp,
                        fontWeight = FontWeight.Bold,
                        color = OnBackground
                    )
                    Text(
                        "Tap 'Pair Device' above to scan your PC's QR code and enable fast 1-tap local connections.",
                        fontSize = 14.sp,
                        color = OnSurfaceVariant,
                        textAlign = androidx.compose.ui.text.style.TextAlign.Center
                    )
                    Spacer(Modifier.height(4.dp))
                    Button(
                        onClick = onNavigatePair,
                        shape = RoundedCornerShape(10.dp)
                    ) {
                        Text("Pair with PC")
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

        // ── Ad-hoc Quick Actions ──────────────────────────────────────────────
        Card(
            modifier = Modifier.fillMaxWidth(),
            shape = RoundedCornerShape(14.dp),
            colors = CardDefaults.cardColors(containerColor = Surface)
        ) {
            Column(modifier = Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                Text("Quick Actions (Ad-Hoc):", fontSize = 12.sp, color = OnSurfaceVariant, fontWeight = FontWeight.Medium)
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.spacedBy(10.dp)
                ) {
                    OutlinedButton(
                        onClick = onNavigateReceive,
                        modifier = Modifier.weight(1f).height(44.dp),
                        shape = RoundedCornerShape(10.dp),
                        contentPadding = PaddingValues(horizontal = 8.dp, vertical = 0.dp),
                        colors = ButtonDefaults.outlinedButtonColors(contentColor = OnBackground)
                    ) {
                        Text("📥 Receive", fontSize = 12.sp, maxLines = 1)
                    }
                    OutlinedButton(
                        onClick = onNavigateSend,
                        modifier = Modifier.weight(1f).height(44.dp),
                        shape = RoundedCornerShape(10.dp),
                        contentPadding = PaddingValues(horizontal = 8.dp, vertical = 0.dp),
                        colors = ButtonDefaults.outlinedButtonColors(contentColor = OnBackground)
                    ) {
                        Text("📤 Send Files", fontSize = 12.sp, maxLines = 1)
                    }
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

    Card(
        modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(14.dp),
        colors = CardDefaults.cardColors(containerColor = Surface),
    ) {
        Column(
            modifier = Modifier.padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp)
        ) {
            // Top Row: Icon + Name + Presence + Menu
            Row(
                modifier = Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically
            ) {
                Text(
                    text = if (device.identity.name.contains("PC", ignoreCase = true) || device.identity.name.contains("Windows", ignoreCase = true)) "💻" else "📱",
                    fontSize = 24.sp
                )
                Spacer(Modifier.width(10.dp))
                Column(modifier = Modifier.weight(1f)) {
                    Text(
                        device.identity.name,
                        fontSize = 16.sp,
                        fontWeight = FontWeight.Bold,
                        color = OnBackground
                    )
                    val statusText = when (device.presenceState) {
                        PresenceState.DISCOVERED -> "🟢 Online"
                        PresenceState.SEARCHING -> "🟡 Searching"
                        else -> "⚪ Offline"
                    }
                    Text(statusText, fontSize = 12.sp, color = OnSurfaceVariant)
                }

                // Connection badge
                val connColor = when (device.connectionState) {
                    ConnectionState.CONNECTED -> Secondary
                    ConnectionState.CONNECTING -> Primary
                    ConnectionState.AUTHENTICATION_REQUIRED -> Error
                    else -> Outline
                }
                Box(
                    modifier = Modifier
                        .clip(RoundedCornerShape(8.dp))
                        .background(connColor.copy(alpha = 0.15f))
                        .padding(horizontal = 10.dp, vertical = 4.dp)
                ) {
                    Text(
                        text = device.connectionState.name.lowercase().replaceFirstChar { it.uppercase() },
                        color = connColor,
                        fontSize = 11.sp,
                        fontWeight = FontWeight.SemiBold
                    )
                }

                Spacer(Modifier.width(4.dp))

                Box {
                    IconButton(onClick = { showMenu = true }) {
                        Text("⋮", fontSize = 20.sp, color = OnSurfaceVariant)
                    }
                    DropdownMenu(
                        expanded = showMenu,
                        onDismissRequest = { showMenu = false },
                        modifier = Modifier.background(Surface)
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
                    val icon = if (tr == "usb") "🔌 USB" else "📶 WI-FI"
                    Box(
                        modifier = Modifier
                            .clip(RoundedCornerShape(6.dp))
                            .background(Primary.copy(alpha = 0.12f))
                            .padding(horizontal = 8.dp, vertical = 2.dp)
                    ) {
                        Text(icon, fontSize = 11.sp, color = Primary, fontWeight = FontWeight.Medium)
                    }
                }
                Box(
                    modifier = Modifier
                        .clip(RoundedCornerShape(6.dp))
                        .background(SurfaceVariant)
                        .padding(horizontal = 8.dp, vertical = 2.dp)
                ) {
                    Text("📁 Transfer", fontSize = 11.sp, color = OnSurfaceVariant)
                }
                Box(
                    modifier = Modifier
                        .clip(RoundedCornerShape(6.dp))
                        .background(SurfaceVariant)
                        .padding(horizontal = 8.dp, vertical = 2.dp)
                ) {
                    Text("🖥️ Mirror", fontSize = 11.sp, color = OnSurfaceVariant)
                }
            }

            // Action Buttons
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(10.dp)
            ) {
                val isConnected = device.connectionState == ConnectionState.CONNECTED
                Button(
                    onClick = onConnectToggle,
                    colors = ButtonDefaults.buttonColors(
                        containerColor = if (isConnected) SurfaceVariant else Primary
                    ),
                    shape = RoundedCornerShape(8.dp),
                    contentPadding = PaddingValues(horizontal = 12.dp, vertical = 6.dp)
                ) {
                    Text(if (isConnected) "Disconnect" else "Connect", fontSize = 12.sp)
                }

                Button(
                    onClick = onSendFiles,
                    colors = ButtonDefaults.buttonColors(containerColor = Secondary),
                    shape = RoundedCornerShape(8.dp),
                    contentPadding = PaddingValues(horizontal = 12.dp, vertical = 6.dp)
                ) {
                    Text("📤 Send Files", fontSize = 12.sp)
                }

                OutlinedButton(
                    onClick = onMirror,
                    shape = RoundedCornerShape(8.dp),
                    contentPadding = PaddingValues(horizontal = 12.dp, vertical = 6.dp)
                ) {
                    Text("🖥️ Mirror", fontSize = 12.sp, color = OnSurface)
                }
            }
        }
    }
}
