package com.photobeam.app.ui.screens

import android.graphics.Bitmap
import android.os.Handler
import android.os.Looper
import android.widget.Toast
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.google.zxing.BarcodeFormat
import com.google.zxing.qrcode.QRCodeWriter
import com.photobeam.app.data.ConnectionManager
import com.photobeam.app.data.DiscoveryService
import com.photobeam.app.data.PairingManager
import com.photobeam.app.protocol.PROTOCOL_VERSION
import com.photobeam.app.protocol.PairingPayload
import com.photobeam.app.protocol.TrustStatus
import com.photobeam.app.protocol.decodePairingPayload
import com.photobeam.app.protocol.encodePairingPayload
import com.photobeam.app.ui.components.TactileBadge
import com.photobeam.app.ui.components.TactileCard
import com.photobeam.app.ui.components.TactileGlowRing
import com.photobeam.app.ui.components.TactilePillButton
import com.photobeam.app.ui.theme.*
import kotlinx.coroutines.delay
import java.util.UUID

sealed class PairUiState {
    object Ready : PairUiState()
    data class Connecting(val message: String) : PairUiState()
    data class Success(val deviceName: String) : PairUiState()
    data class Error(val message: String) : PairUiState()
}

@Composable
fun PairScreen(
    onBack: () -> Unit,
    onPairingComplete: () -> Unit,
    initialQrUri: String? = null,
) {
    val context = LocalContext.current
    val connectionManager = remember { ConnectionManager.getInstance(context) }
    val pairingManager = remember { PairingManager.getInstance(context) }

    val pairedDevices by connectionManager.pairedDevicesFlow.collectAsState()
    val initialPairedCount = remember { pairedDevices.count { it.identity.trustStatus == TrustStatus.TRUSTED } }

    var selectedTab by remember { mutableStateOf(0) } // 0 = Scan, 1 = Show My QR
    var uiState by remember { mutableStateOf<PairUiState>(PairUiState.Ready) }

    // Auto-detect when remote peer pairs with this device via Show My QR
    LaunchedEffect(pairedDevices) {
        val currentTrusted = pairedDevices.firstOrNull { it.identity.trustStatus == TrustStatus.TRUSTED }
        if (selectedTab == 1 && pairedDevices.count { it.identity.trustStatus == TrustStatus.TRUSTED } > initialPairedCount && currentTrusted != null) {
            uiState = PairUiState.Success(currentTrusted.identity.name)
            Toast.makeText(context, "Successfully paired with ${currentTrusted.identity.name}!", Toast.LENGTH_SHORT).show()
        }
    }

    val mainHandler = remember { Handler(Looper.getMainLooper()) }

    val handleQrUri: (String) -> Unit = remember {
        { rawUri ->
            if (uiState is PairUiState.Ready) {
                if (!rawUri.startsWith("photobeam://pair/") && !rawUri.startsWith("photobeam://connect/")) {
                    uiState = PairUiState.Error("Invalid QR code: Unrecognized format. Please scan a PhotoBeam pairing code.")
                } else {
                    uiState = PairUiState.Connecting("QR Detected! Verifying with PC...")
                    try {
                        val payload = if (rawUri.startsWith("photobeam://pair/")) {
                            decodePairingPayload(rawUri)
                        } else {
                            val qr = com.photobeam.app.protocol.decodeQrPayload(rawUri)
                            PairingPayload(
                                v = qr.v,
                                sid = qr.sid,
                                rid = qr.rid,
                                addrs = qr.addrs,
                                port = qr.port,
                                transports = qr.transports,
                                token = qr.token,
                                exp = qr.exp,
                                certFp = qr.certFp,
                                deviceName = "Windows PC",
                                devicePublicKey = "",
                                capabilities = listOf("file_transfer", "screen_mirror_receive"),
                                pairingNonce = "",
                            )
                        }

                        if (payload.isExpired()) {
                            uiState = PairUiState.Error("QR code has expired. Please refresh the QR code on your PC.")
                        } else {
                            connectionManager.pairWithPayload(
                                payload = payload,
                                onSuccess = { dev ->
                                    mainHandler.post {
                                        uiState = PairUiState.Success(dev.identity.name)
                                        Toast.makeText(context, "Successfully paired with ${dev.identity.name}!", Toast.LENGTH_SHORT).show()
                                    }
                                },
                                onError = { err ->
                                    mainHandler.post {
                                        uiState = PairUiState.Error(err)
                                    }
                                }
                            )
                        }
                    } catch (e: Exception) {
                        uiState = PairUiState.Error("Invalid QR code: ${e.message ?: "Malformed payload"}")
                    }
                }
            }
        }
    }

    LaunchedEffect(initialQrUri) {
        if (!initialQrUri.isNullOrBlank()) {
            handleQrUri(initialQrUri)
        }
    }

    // Auto complete after showing success state
    LaunchedEffect(uiState) {
        if (uiState is PairUiState.Success) {
            delay(1200L)
            onPairingComplete()
        }
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(AppBackgroundBrush)
            .statusBarsPadding()
            .navigationBarsPadding(),
    ) {
        // Top app bar
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 20.dp, vertical = 14.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Box(
                modifier = Modifier
                    .size(40.dp)
                    .clip(RoundedCornerShape(12.dp))
                    .background(SurfaceElevated)
                    .border(1.dp, CardBorderSubtle, RoundedCornerShape(12.dp))
                    .clickable(onClick = onBack),
                contentAlignment = Alignment.Center
            ) {
                Text("←", fontSize = 20.sp, color = OnBackground)
            }
            Spacer(Modifier.width(14.dp))
            Column {
                Text(
                    "Pair New Device",
                    fontSize = 20.sp,
                    fontWeight = FontWeight.Bold,
                    color = OnBackground,
                )
                Text(
                    "Secure local-network link",
                    fontSize = 12.sp,
                    color = CyanAccent,
                )
            }
        }

        // Neo-Tactile Pill Toggle Selector (Inspired by Reference 1: "Defart" / "active")
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 20.dp, vertical = 8.dp),
            horizontalArrangement = Arrangement.spacedBy(12.dp)
        ) {
            TactilePillButton(
                text = "📷 Scan PC QR",
                active = selectedTab == 0,
                onClick = { selectedTab = 0 },
                modifier = Modifier.weight(1f)
            )
            TactilePillButton(
                text = "📱 Show My QR",
                active = selectedTab == 1,
                onClick = { selectedTab = 1 },
                modifier = Modifier.weight(1f)
            )
        }

        Spacer(Modifier.height(8.dp))

        if (selectedTab == 0) {
            // Scanner view
            Box(
                modifier = Modifier
                    .weight(1f)
                    .fillMaxWidth()
                    .padding(horizontal = 20.dp, vertical = 8.dp)
                    .clip(RoundedCornerShape(24.dp))
                    .border(1.5.dp, CardBorder, RoundedCornerShape(24.dp))
            ) {
                val isScanning = uiState is PairUiState.Ready

                QrScannerView(
                    modifier = Modifier.fillMaxSize(),
                    isScanningActive = isScanning,
                    promptText = when (uiState) {
                        is PairUiState.Ready -> "Align PC pairing QR code within frame"
                        is PairUiState.Connecting -> "QR detected! Verifying with PC..."
                        is PairUiState.Success -> "Pairing complete!"
                        is PairUiState.Error -> "Pairing failed"
                    },
                    onQrScanned = handleQrUri
                )

                // State Feedback Overlay
                when (val s = uiState) {
                    is PairUiState.Connecting -> {
                        TactileCard(
                            modifier = Modifier
                                .align(Alignment.Center)
                                .padding(28.dp),
                            backgroundColor = SurfaceElevated.copy(alpha = 0.96f),
                            borderColor = PrimaryGlow
                        ) {
                            Column(
                                modifier = Modifier.fillMaxWidth(),
                                horizontalAlignment = Alignment.CenterHorizontally,
                                verticalArrangement = Arrangement.spacedBy(12.dp)
                            ) {
                                TactileGlowRing(size = 52.dp, ringColor = PrimaryGlow, pulse = true) {
                                    CircularProgressIndicator(color = PrimaryGlow, modifier = Modifier.size(32.dp), strokeWidth = 3.dp)
                                }
                                Text("Connecting to PC...", fontWeight = FontWeight.Bold, color = OnBackground, fontSize = 16.sp)
                                Text(s.message, color = OnSurfaceVariant, fontSize = 13.sp, textAlign = TextAlign.Center)
                            }
                        }
                    }

                    is PairUiState.Success -> {
                        TactileCard(
                            modifier = Modifier
                                .align(Alignment.Center)
                                .padding(28.dp),
                            backgroundColor = SurfaceElevated.copy(alpha = 0.96f),
                            borderColor = Secondary
                        ) {
                            Column(
                                modifier = Modifier.fillMaxWidth(),
                                horizontalAlignment = Alignment.CenterHorizontally,
                                verticalArrangement = Arrangement.spacedBy(10.dp)
                            ) {
                                TactileGlowRing(size = 52.dp, ringColor = Secondary) {
                                    Text("✅", fontSize = 26.sp)
                                }
                                Text("Pairing Successful!", fontWeight = FontWeight.Bold, color = Secondary, fontSize = 18.sp)
                                Text("Connected to ${s.deviceName}", color = OnBackground, fontSize = 14.sp)
                            }
                        }
                    }

                    is PairUiState.Error -> {
                        TactileCard(
                            modifier = Modifier
                                .align(Alignment.Center)
                                .padding(28.dp),
                            backgroundColor = SurfaceElevated.copy(alpha = 0.96f),
                            borderColor = Error
                        ) {
                            Column(
                                modifier = Modifier.fillMaxWidth(),
                                horizontalAlignment = Alignment.CenterHorizontally,
                                verticalArrangement = Arrangement.spacedBy(12.dp)
                            ) {
                                TactileGlowRing(size = 52.dp, ringColor = Error) {
                                    Text("⚠️", fontSize = 24.sp)
                                }
                                Text("Pairing Failed", fontWeight = FontWeight.Bold, color = Error, fontSize = 17.sp)
                                Text(s.message, color = OnSurfaceVariant, fontSize = 13.sp, textAlign = TextAlign.Center)
                                Spacer(Modifier.height(4.dp))
                                TactilePillButton(
                                    text = "🔄 Scan Again / Retry",
                                    active = true,
                                    onClick = { uiState = PairUiState.Ready },
                                    modifier = Modifier.fillMaxWidth()
                                )
                            }
                        }
                    }

                    is PairUiState.Ready -> {
                        // Overlay handled by QrScannerView promptText
                    }
                }
            }
        } else {
            // Show Local Device QR Code
            val localIdentity = remember { pairingManager.getLocalIdentity() }
            val qrBitmap = remember {
                val nonce = java.util.Base64.getUrlEncoder().withoutPadding().encodeToString(ByteArray(32) { 0x07 })
                val discovery = DiscoveryService.getInstance(context)
                val token = UUID.randomUUID().toString()
                connectionManager.setActivePairingToken(token)
                val payload = PairingPayload(
                    v = PROTOCOL_VERSION,
                    sid = UUID.randomUUID().toString(),
                    rid = localIdentity.deviceId,
                    addrs = discovery.getLocalIpAddresses(),
                    port = ConnectionManager.CONTROL_PORT,
                    transports = listOf("wifi", "usb"),
                    token = token,
                    exp = (System.currentTimeMillis() / 1000) + 1800,
                    certFp = "",
                    deviceName = localIdentity.name,
                    devicePublicKey = localIdentity.publicKey,
                    capabilities = localIdentity.capabilities.map { it.name.lowercase() },
                    pairingNonce = nonce,
                )
                val uri = encodePairingPayload(payload)
                generateQrBitmap(uri, 600)
            }

            Column(
                modifier = Modifier
                    .weight(1f)
                    .fillMaxWidth()
                    .padding(20.dp),
                horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = Arrangement.Center,
            ) {
                TactileCard(
                    modifier = Modifier.fillMaxWidth(),
                    cornerRadius = 24.dp,
                    backgroundColor = SurfaceElevated,
                    borderColor = CardBorder
                ) {
                    Column(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalAlignment = Alignment.CenterHorizontally,
                        verticalArrangement = Arrangement.spacedBy(14.dp)
                    ) {
                        qrBitmap?.let { bmp ->
                            Box(
                                modifier = Modifier
                                    .size(240.dp)
                                    .clip(RoundedCornerShape(20.dp))
                                    .background(Color.White)
                                    .padding(14.dp)
                            ) {
                                Image(
                                    bitmap = bmp.asImageBitmap(),
                                    contentDescription = "Local Pairing QR",
                                    modifier = Modifier.fillMaxSize()
                                )
                            }
                        }
                        Text(
                            localIdentity.name,
                            fontWeight = FontWeight.Bold,
                            fontSize = 18.sp,
                            color = OnBackground,
                        )
                        TactileBadge("📱 Ready for Scanning", CyanAccent)
                        Text(
                            "Scan this code using the PhotoBeam desktop client to pair immediately.",
                            fontSize = 12.sp,
                            color = OnSurfaceVariant,
                            textAlign = TextAlign.Center,
                        )
                    }
                }
            }
        }
    }
}

private fun generateQrBitmap(content: String, size: Int): Bitmap? {
    return try {
        val writer = QRCodeWriter()
        val bitMatrix = writer.encode(content, BarcodeFormat.QR_CODE, size, size)
        val bitmap = Bitmap.createBitmap(size, size, Bitmap.Config.RGB_565)
        for (x in 0 until size) {
            for (y in 0 until size) {
                bitmap.setPixel(x, y, if (bitMatrix.get(x, y)) android.graphics.Color.BLACK else android.graphics.Color.WHITE)
            }
        }
        bitmap
    } catch (e: Exception) {
        null
    }
}
