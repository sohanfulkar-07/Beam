package com.photobeam.app.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.photobeam.app.ui.theme.*

@Composable
fun HomeScreen(
    onNavigateReceive: () -> Unit,
    onNavigateSend: () -> Unit,
    onNavigateHistory: () -> Unit = {},
) {
    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(Background),
        contentAlignment = Alignment.Center,
    ) {
        Column(
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.spacedBy(24.dp),
            modifier = Modifier.padding(24.dp),
        ) {
            // ── Hero ──────────────────────────────────────────────────────────
            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .clip(RoundedCornerShape(24.dp))
                    .background(Surface)
                    .padding(32.dp),
                contentAlignment = Alignment.Center,
            ) {
                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    Text("⚡", fontSize = 56.sp)
                    Spacer(Modifier.height(8.dp))
                    Text(
                        "PhotoBeam",
                        fontSize = 32.sp,
                        fontWeight = FontWeight.Bold,
                        color = OnBackground,
                    )
                    Spacer(Modifier.height(4.dp))
                    Text(
                        "Fast local file transfer — no cloud, no account",
                        fontSize = 14.sp,
                        color = OnSurfaceVariant,
                    )
                }
            }

            // ── Action row ────────────────────────────────────────────────────
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(16.dp),
            ) {
                ActionCard(
                    modifier = Modifier.weight(1f),
                    icon = "📥",
                    title = "Receive",
                    description = "Generate a QR code and receive files from nearby devices",
                    buttonText = "Start Receiving",
                    isPrimary = true,
                    onClick = onNavigateReceive,
                )
                ActionCard(
                    modifier = Modifier.weight(1f),
                    icon = "📤",
                    title = "Send",
                    description = "Scan the receiver's QR code and send files",
                    buttonText = "Start Sending",
                    isPrimary = false,
                    onClick = onNavigateSend,
                )
            }

            // ── History Button ────────────────────────────────────────────────
            OutlinedButton(
                onClick = onNavigateHistory,
                modifier = Modifier.fillMaxWidth(0.85f),
                shape = RoundedCornerShape(12.dp),
                colors = ButtonDefaults.outlinedButtonColors(contentColor = OnSurface),
                border = ButtonDefaults.outlinedButtonBorder(true).copy(
                    brush = androidx.compose.ui.graphics.SolidColor(Outline)
                ),
            ) {
                Text("📜 View Transfer History", fontSize = 14.sp, fontWeight = FontWeight.Medium)
            }

            Text(
                "Wi-Fi • USB • No internet required",
                fontSize = 12.sp,
                color = OnSurfaceVariant.copy(alpha = 0.6f),
            )
        }
    }
}

@Composable
private fun ActionCard(
    modifier: Modifier = Modifier,
    icon: String,
    title: String,
    description: String,
    buttonText: String,
    isPrimary: Boolean,
    onClick: () -> Unit,
) {
    Card(
        modifier = modifier,
        shape = RoundedCornerShape(20.dp),
        colors = CardDefaults.cardColors(containerColor = Surface),
    ) {
        Column(
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.spacedBy(12.dp),
            modifier = Modifier.padding(24.dp),
        ) {
            Text(icon, fontSize = 36.sp)
            Text(title, fontSize = 20.sp, fontWeight = FontWeight.SemiBold, color = OnBackground)
            Text(description, fontSize = 13.sp, color = OnSurfaceVariant,
                textAlign = androidx.compose.ui.text.style.TextAlign.Center)
            Spacer(Modifier.height(4.dp))
            Button(
                onClick = onClick,
                modifier = Modifier.fillMaxWidth(),
                colors = if (isPrimary)
                    ButtonDefaults.buttonColors(containerColor = Primary)
                else
                    ButtonDefaults.buttonColors(containerColor = SurfaceVariant),
                shape = RoundedCornerShape(12.dp),
            ) {
                Text(buttonText, color = OnBackground)
            }
        }
    }
}
