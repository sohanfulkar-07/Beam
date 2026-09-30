package com.photobeam.app.ui.screens

import android.graphics.Bitmap
import android.os.Handler
import android.os.Looper
import android.widget.Toast
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
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
import com.photobeam.app.data.PairingManager
import com.photobeam.app.protocol.PROTOCOL_VERSION
import com.photobeam.app.protocol.PairingPayload
import com.photobeam.app.protocol.decodePairingPayload
import com.photobeam.app.protocol.encodePairingPayload
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

    var selectedTab by remember { mutableStateOf(0) } // 0 = Scan, 1 = Show My QR
    var uiState by remember { mutableStateOf<PairUiState>(PairUiState.Ready) }

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
            .background(Background)
            .statusBarsPadding()
            .navigationBarsPadding(),
    ) {
        // Top app bar
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 16.dp, vertical = 12.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            IconButton(onClick = onBack) {
                Text("←", fontSize = 24.sp, color = OnBackground)
            }
            Text(
                "Pair New Device",
                fontSize = 20.sp,
                fontWeight = FontWeight.Bold,
                color = OnBackground,
                modifier = Modifier.padding(start = 8.dp)
            )
        }

        // Tab Selector (Scan vs Show QR)
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 24.dp, vertical = 8.dp)
                .clip(RoundedCornerShape(12.dp))
                .background(Surface),
        ) {
            Button(
                onClick = { selectedTab = 0 },
                modifier = Modifier.weight(1f),
                colors = ButtonDefaults.buttonColors(
                    containerColor = if (selectedTab == 0) Primary else Color.Transparent,
                    contentColor = if (selectedTab == 0) Color.White else OnSurfaceVariant
                ),
                shape = RoundedCornerShape(12.dp),
            ) {
                Text("📷 Scan QR", fontWeight = FontWeight.SemiBold)
            }
            Button(
                onClick = { selectedTab = 1 },
                modifier = Modifier.weight(1f),
                colors = ButtonDefaults.buttonColors(
                    containerColor = if (selectedTab == 1) Primary else Color.Transparent,
                    contentColor = if (selectedTab == 1) Color.White else OnSurfaceVariant
                ),
                shape = RoundedCornerShape(12.dp),
            ) {
                Text("📱 Show My QR", fontWeight = FontWeight.SemiBold)
            }
        }

        Spacer(Modifier.height(12.dp))

        if (selectedTab == 0) {
            // Scanner view
            Box(
                modifier = Modifier
                    .weight(1f)
                    .fillMaxWidth()
                    .padding(horizontal = 24.dp)
                    .clip(RoundedCornerShape(20.dp))
            ) {
                val isScanning = uiState is PairUiState.Ready

                QrScannerView(
                    modifier = Modifier.fillMaxSize(),
                    isScanningActive = isScanning,
                    promptText = when (uiState) {
                        is PairUiState.Ready -> "Point camera at the PhotoBeam QR code on your PC"
                        is PairUiState.Connecting -> "QR detected! Verifying challenge..."
                        is PairUiState.Success -> "Pairing complete!"
                        is PairUiState.Error -> "Pairing failed"
                    },
                    onQrScanned = handleQrUri
                )

                // State Feedback Overlay
                when (val s = uiState) {
                    is PairUiState.Connecting -> {
                        Surface(
                            modifier = Modifier
                                .align(Alignment.Center)
                                .padding(24.dp),
                            shape = RoundedCornerShape(20.dp),
                            color = Color(0xFF1E293B).copy(alpha = 0.95f),
                            shadowElevation = 8.dp,
                        ) {
                            Column(
                                modifier = Modifier.padding(24.dp),
                                horizontalAlignment = Alignment.CenterHorizontally,
                                verticalArrangement = Arrangement.spacedBy(14.dp),
                            ) {
                                CircularProgressIndicator(color = Primary, modifier = Modifier.size(44.dp))
                                Text("Connecting to PC...", fontWeight = FontWeight.Bold, color = Color.White, fontSize = 16.sp)
                                Text(s.message, color = Color.LightGray, fontSize = 13.sp, textAlign = TextAlign.Center)
                            }
                        }
                    }

                    is PairUiState.Success -> {
                        Surface(
                            modifier = Modifier
                                .align(Alignment.Center)
                                .padding(24.dp),
                            shape = RoundedCornerShape(20.dp),
                            color = Color(0xFF0F291E).copy(alpha = 0.95f),
                            shadowElevation = 8.dp,
                        ) {
                            Column(
                                modifier = Modifier.padding(24.dp),
                                horizontalAlignment = Alignment.CenterHorizontally,
                                verticalArrangement = Arrangement.spacedBy(10.dp),
                            ) {
                                Text("✅", fontSize = 40.sp)
                                Text("Pairing Successful!", fontWeight = FontWeight.Bold, color = Color(0xFF2ED573), fontSize = 18.sp)
                                Text("Connected to ${s.deviceName}", color = Color.White, fontSize = 14.sp)
                            }
                        }
                    }

                    is PairUiState.Error -> {
                        Surface(
                            modifier = Modifier
                                .align(Alignment.Center)
                                .padding(24.dp),
                            shape = RoundedCornerShape(20.dp),
                            color = Color(0xFF2B161B).copy(alpha = 0.96f),
                            shadowElevation = 8.dp,
                        ) {
                            Column(
                                modifier = Modifier.padding(24.dp),
                                horizontalAlignment = Alignment.CenterHorizontally,
                                verticalArrangement = Arrangement.spacedBy(14.dp),
                            ) {
                                Text("⚠️", fontSize = 36.sp)
                                Text("Pairing Failed", fontWeight = FontWeight.Bold, color = Color(0xFFFF4757), fontSize = 17.sp)
                                Text(s.message, color = Color.LightGray, fontSize = 13.sp, textAlign = TextAlign.Center)
                                Spacer(Modifier.height(4.dp))
                                Button(
                                    onClick = { uiState = PairUiState.Ready },
                                    colors = ButtonDefaults.buttonColors(containerColor = Primary),
                                    shape = RoundedCornerShape(12.dp),
                                ) {
                                    Text("🔄 Scan Again / Retry", color = OnBackground, fontWeight = FontWeight.Bold)
                                }
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
                val payload = PairingPayload(
                    v = PROTOCOL_VERSION,
                    sid = UUID.randomUUID().toString(),
                    rid = localIdentity.deviceId,
                    addrs = listOf("127.0.0.1"),
                    port = 47474,
                    transports = listOf("wifi", "usb"),
                    token = UUID.randomUUID().toString(),
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
                    .padding(24.dp),
                horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = Arrangement.Center,
            ) {
                Card(
                    modifier = Modifier.padding(16.dp),
                    shape = RoundedCornerShape(24.dp),
                    colors = CardDefaults.cardColors(containerColor = Surface),
                ) {
                    Column(
                        modifier = Modifier.padding(24.dp),
                        horizontalAlignment = Alignment.CenterHorizontally,
                    ) {
                        qrBitmap?.let { bmp ->
                            Image(
                                bitmap = bmp.asImageBitmap(),
                                contentDescription = "Local Pairing QR",
                                modifier = Modifier
                                    .size(240.dp)
                                    .clip(RoundedCornerShape(16.dp))
                                    .background(Color.White)
                                    .padding(12.dp)
                            )
                        }
                        Spacer(Modifier.height(16.dp))
                        Text(
                            localIdentity.name,
                            fontWeight = FontWeight.Bold,
                            fontSize = 18.sp,
                            color = OnBackground,
                        )
                        Text(
                            "Scan with PhotoBeam on another device",
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
