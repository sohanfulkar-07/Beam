package com.photobeam.app.ui.screens

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.media.projection.MediaProjectionManager
import android.widget.Toast
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.animation.core.*
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.scale
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.photobeam.app.data.ConnectionManager
import com.photobeam.app.mirror.ScreenCaptureService
import com.photobeam.app.protocol.ConnectionState
import com.photobeam.app.ui.components.TactileBadge
import com.photobeam.app.ui.components.TactileCard
import com.photobeam.app.ui.components.TactileGlowRing
import com.photobeam.app.ui.components.TactilePillButton
import com.photobeam.app.ui.theme.*

/**
 * MirrorScreen — UI to initiate screen mirroring to a paired Windows PC.
 *
 * Flow:
 *  1. Shows connected PC info and status.
 *  2. User taps "Start Mirroring" → Android MediaProjection permission dialog appears.
 *  3. On grant → starts ScreenCaptureService with peer host/port.
 *  4. User taps "Stop Mirroring" → stops ScreenCaptureService.
 */
@Composable
fun MirrorScreen(
    deviceId: String,
    onBack: () -> Unit,
) {
    val context = LocalContext.current
    val connectionManager = remember { ConnectionManager.getInstance(context) }

    val pairedDevices by connectionManager.pairedDevicesFlow.collectAsState()
    val device = pairedDevices.firstOrNull { it.identity.deviceId == deviceId }

    var isMirroring by remember { mutableStateOf(false) }
    var mirrorError by remember { mutableStateOf<String?>(null) }

    // Pulse animation for the mirror dot
    val infiniteTransition = rememberInfiniteTransition(label = "pulse")
    val pulseScale by infiniteTransition.animateFloat(
        initialValue = 1f,
        targetValue = 1.15f,
        animationSpec = infiniteRepeatable(
            animation = tween(900, easing = FastOutSlowInEasing),
            repeatMode = RepeatMode.Reverse
        ),
        label = "pulse_scale"
    )

    // MediaProjection launcher
    val projectionLauncher = rememberLauncherForActivityResult(
        contract = ActivityResultContracts.StartActivityForResult()
    ) { result ->
        if (result.resultCode == Activity.RESULT_OK && result.data != null) {
            // Determine peer host — prefer USB tunnel (127.0.0.1) when USB transport is active
            // because the phone may be on mobile data (not the same Wi-Fi as the PC).
            // ADB reverse tcp:47478 -> tcp:47478 routes 127.0.0.1:47478 through the USB cable.
            val transports = device?.endpoint?.transports ?: emptyList()
            val peerHost = if (transports.contains("usb")) {
                "127.0.0.1"  // USB tunnel: ADB reverse routes this to PC port 47478
            } else {
                device?.endpoint?.addrs?.firstOrNull { !it.startsWith("127.") }
                    ?: "127.0.0.1"
            }
            val peerPort = 47478 // Mirror stream port on Windows

            val serviceIntent = Intent(context, ScreenCaptureService::class.java).apply {
                action = ScreenCaptureService.ACTION_START
                putExtra(ScreenCaptureService.EXTRA_RESULT_CODE, result.resultCode)
                putExtra(ScreenCaptureService.EXTRA_RESULT_DATA, result.data)
                putExtra(ScreenCaptureService.EXTRA_PEER_HOST, peerHost)
                putExtra(ScreenCaptureService.EXTRA_PEER_PORT, peerPort)
            }
            context.startForegroundService(serviceIntent)
            isMirroring = true
            mirrorError = null

            Toast.makeText(context, "Screen mirroring started → $peerHost", Toast.LENGTH_SHORT).show()
        } else {
            mirrorError = "Screen capture permission denied. Please try again."
        }
    }

    fun startMirroring() {
        if (device == null) {
            mirrorError = "Device not found."
            return
        }
        if (device.connectionState != ConnectionState.CONNECTED) {
            mirrorError = "Device must be connected before starting mirror."
            return
        }
        mirrorError = null
        val mpm = context.getSystemService(Context.MEDIA_PROJECTION_SERVICE) as MediaProjectionManager
        projectionLauncher.launch(mpm.createScreenCaptureIntent())
    }

    fun stopMirroring() {
        val stopIntent = Intent(context, ScreenCaptureService::class.java).apply {
            action = ScreenCaptureService.ACTION_STOP
        }
        context.startService(stopIntent)
        isMirroring = false
        Toast.makeText(context, "Screen mirroring stopped.", Toast.LENGTH_SHORT).show()
    }

    // Stop mirroring when leaving the screen
    DisposableEffect(Unit) {
        onDispose {
            if (isMirroring) {
                stopMirroring()
            }
        }
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(AppBackgroundBrush)
            .statusBarsPadding()
            .navigationBarsPadding()
            .padding(horizontal = 20.dp, vertical = 14.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp)
    ) {
        // ── Top Bar ────────────────────────────────────────────────────────────
        Row(
            modifier = Modifier.fillMaxWidth(),
            verticalAlignment = Alignment.CenterVertically
        ) {
            TactilePillButton(
                text = "← Back",
                active = false,
                onClick = onBack,
                minHeight = 38.dp
            )
            Spacer(Modifier.width(12.dp))
            Text(
                "Screen Mirror",
                fontSize = 20.sp,
                fontWeight = FontWeight.Bold,
                color = OnBackground
            )
            Spacer(Modifier.weight(1f))
            if (isMirroring) {
                TactileBadge("● Live", Secondary)
            }
        }

        // ── Hero / Status Card ────────────────────────────────────────────────
        TactileCard(
            modifier = Modifier.fillMaxWidth(),
            backgroundColor = SurfaceElevated,
            borderColor = if (isMirroring) Secondary.copy(alpha = 0.6f) else CardBorder,
            elevation = if (isMirroring) 12.dp else 4.dp
        ) {
            Column(
                modifier = Modifier.fillMaxWidth(),
                horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = Arrangement.spacedBy(14.dp)
            ) {
                // Animated glow ring
                val ringColor = if (isMirroring) Secondary else if (device?.connectionState == ConnectionState.CONNECTED) CyanAccent else Outline
                val scaleModifier = if (isMirroring) Modifier.scale(pulseScale) else Modifier

                Box(
                    contentAlignment = Alignment.Center,
                    modifier = scaleModifier
                ) {
                    TactileGlowRing(
                        size = 70.dp,
                        ringColor = ringColor,
                        pulse = isMirroring
                    ) {
                        Text("🖥️", fontSize = 34.sp)
                    }
                }

                Text(
                    text = device?.identity?.name ?: "Unknown PC",
                    fontSize = 18.sp,
                    fontWeight = FontWeight.Bold,
                    color = OnBackground,
                    textAlign = TextAlign.Center
                )

                val connText = when {
                    isMirroring -> "Streaming your screen live"
                    device?.connectionState == ConnectionState.CONNECTED -> "Connected — ready to mirror"
                    device?.connectionState == ConnectionState.CONNECTING -> "Connecting…"
                    else -> "Not connected — connect first"
                }
                Text(
                    text = connText,
                    fontSize = 13.sp,
                    color = if (isMirroring) Secondary else CyanAccent,
                    textAlign = TextAlign.Center
                )

                // Transport info
                val transportStr = device?.endpoint?.transports?.joinToString(" • ") {
                    if (it == "usb") "⚡ USB Tunnel" else "📶 Wi-Fi"
                } ?: ""
                if (transportStr.isNotEmpty()) {
                    Text(transportStr, fontSize = 11.sp, color = OnSurfaceVariant)
                }
            }
        }

        // ── Mirror Info Card ──────────────────────────────────────────────────
        TactileCard(
            modifier = Modifier.fillMaxWidth(),
            backgroundColor = Surface,
            borderColor = CardBorderSubtle
        ) {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(
                    "How it works",
                    fontSize = 14.sp,
                    fontWeight = FontWeight.SemiBold,
                    color = OnBackground
                )
                InfoRow("📡", "Your phone screen streams in real-time to the PC")
                InfoRow("🔒", "Peer-to-peer — no cloud, no internet required")
                InfoRow("📁", "Drag files on the PC viewer to send them here")
                InfoRow("⚡", "720p JPEG stream at ~30 FPS over Wi-Fi or USB")
            }
        }

        // ── Error Display ──────────────────────────────────────────────────────
        if (mirrorError != null) {
            TactileCard(
                modifier = Modifier.fillMaxWidth(),
                backgroundColor = Error.copy(alpha = 0.12f),
                borderColor = Error.copy(alpha = 0.4f)
            ) {
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(10.dp)
                ) {
                    Text("⚠️", fontSize = 18.sp)
                    Text(
                        mirrorError ?: "",
                        fontSize = 13.sp,
                        color = Error,
                        modifier = Modifier.weight(1f)
                    )
                }
            }
        }

        Spacer(Modifier.weight(1f))

        // ── Primary Action Button ─────────────────────────────────────────────
        if (isMirroring) {
            TactilePillButton(
                text = "⏹ Stop Mirroring",
                active = true,
                onClick = { stopMirroring() },
                modifier = Modifier.fillMaxWidth(),
                minHeight = 52.dp
            )
        } else {
            val canStart = device?.connectionState == ConnectionState.CONNECTED
            TactilePillButton(
                text = if (canStart) "▶ Start Mirroring" else "Connect Device First",
                active = canStart,
                onClick = { if (canStart) startMirroring() },
                modifier = Modifier.fillMaxWidth(),
                minHeight = 52.dp
            )
        }

        // Safety note
        Text(
            "Your screen will be captured by Android MediaProjection API and streamed over LAN.",
            fontSize = 11.sp,
            color = OnSurfaceVariant,
            textAlign = TextAlign.Center,
            modifier = Modifier.fillMaxWidth()
        )
    }
}

@Composable
private fun InfoRow(icon: String, text: String) {
    Row(
        verticalAlignment = Alignment.Top,
        horizontalArrangement = Arrangement.spacedBy(8.dp)
    ) {
        Text(icon, fontSize = 14.sp)
        Text(text, fontSize = 12.sp, color = OnSurfaceVariant, modifier = Modifier.weight(1f))
    }
}
