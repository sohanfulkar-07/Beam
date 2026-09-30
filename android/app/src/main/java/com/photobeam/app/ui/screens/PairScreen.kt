package com.photobeam.app.ui.screens

import android.graphics.Bitmap
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
import java.util.UUID

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
    var pairingStatus by remember { mutableStateOf<String?>(null) }
    var isProcessing by remember { mutableStateOf(false) }

    val handleQrUri: (String) -> Unit = remember {
        { rawUri ->
            if (!isProcessing && (rawUri.startsWith("photobeam://pair/") || rawUri.startsWith("photobeam://connect/"))) {
                isProcessing = true
                pairingStatus = "Verifying pairing QR..."
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
                    val mainHandler = android.os.Handler(android.os.Looper.getMainLooper())
                    connectionManager.pairWithPayload(
                        payload = payload,
                        onSuccess = { dev ->
                            mainHandler.post {
                                pairingStatus = "Paired with ${dev.identity.name}!"
                                Toast.makeText(context, "Successfully paired with ${dev.identity.name}!", Toast.LENGTH_SHORT).show()
                                onPairingComplete()
                            }
                        },
                        onError = { err ->
                            mainHandler.post {
                                pairingStatus = "Pairing failed: $err"
                                isProcessing = false
                            }
                        }
                    )
                } catch (e: Exception) {
                    android.os.Handler(android.os.Looper.getMainLooper()).post {
                        pairingStatus = "Invalid QR code: ${e.message}"
                        isProcessing = false
                    }
                }
            }
        }
    }

    LaunchedEffect(initialQrUri) {
        initialQrUri?.let { handleQrUri(it) }
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
                QrScannerView(
                    modifier = Modifier.fillMaxSize(),
                    onQrScanned = handleQrUri
                )

                pairingStatus?.let { status ->
                    Box(
                        modifier = Modifier
                            .align(Alignment.TopCenter)
                            .padding(top = 16.dp)
                            .background(Color.Black.copy(alpha = 0.85f), RoundedCornerShape(12.dp))
                            .padding(horizontal = 20.dp, vertical = 10.dp)
                    ) {
                        Text(status, color = Color.White, fontSize = 14.sp, fontWeight = FontWeight.Medium)
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
                    token = UUID.randomUUID().toString().replace("-", ""),
                    exp = System.currentTimeMillis() / 1000 + 1800,
                    certFp = "",
                    deviceName = localIdentity.name,
                    devicePublicKey = localIdentity.publicKey,
                    capabilities = localIdentity.capabilities.map { it.name.lowercase() },
                    pairingNonce = nonce,
                )
                val uri = encodePairingPayload(payload)
                generateQrBitmap(uri)
            }

            Box(
                modifier = Modifier
                    .weight(1f)
                    .fillMaxWidth()
                    .padding(24.dp),
                contentAlignment = Alignment.Center
            ) {
                Column(
                    horizontalAlignment = Alignment.CenterHorizontally,
                    verticalArrangement = Arrangement.spacedBy(16.dp)
                ) {
                    Text(
                        "Scan this QR code from PhotoBeam on Windows",
                        fontSize = 15.sp,
                        color = OnSurfaceVariant,
                        textAlign = TextAlign.Center
                    )

                    qrBitmap?.let { bmp ->
                        Box(
                            modifier = Modifier
                                .clip(RoundedCornerShape(16.dp))
                                .background(Color.White)
                                .padding(16.dp)
                        ) {
                            Image(
                                bitmap = bmp.asImageBitmap(),
                                contentDescription = "Pairing QR Code",
                                modifier = Modifier.size(240.dp)
                            )
                        }
                    }

                    Text(
                        "Device: ${localIdentity.name}",
                        fontSize = 16.sp,
                        fontWeight = FontWeight.Bold,
                        color = OnBackground
                    )
                }
            }
        }

        Spacer(Modifier.height(16.dp))
    }
}

private fun generateQrBitmap(content: String): Bitmap? {
    return try {
        val size = 512
        val bits = QRCodeWriter().encode(content, BarcodeFormat.QR_CODE, size, size)
        val bmp = Bitmap.createBitmap(size, size, Bitmap.Config.RGB_565)
        for (x in 0 until size) {
            for (y in 0 until size) {
                bmp.setPixel(x, y, if (bits.get(x, y)) android.graphics.Color.BLACK else android.graphics.Color.WHITE)
            }
        }
        bmp
    } catch (e: Exception) {
        null
    }
}
