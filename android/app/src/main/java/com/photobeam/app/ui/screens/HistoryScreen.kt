package com.photobeam.app.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.photobeam.app.data.TransferHistoryManager
import com.photobeam.app.data.TransferRecord
import com.photobeam.app.ui.components.TactileBadge
import com.photobeam.app.ui.components.TactileCard
import com.photobeam.app.ui.components.TactileGlowRing
import com.photobeam.app.ui.components.TactilePillButton
import com.photobeam.app.ui.theme.*
import java.text.SimpleDateFormat
import java.util.*

@Composable
fun HistoryScreen(onBack: () -> Unit) {
    val context = LocalContext.current
    val historyMgr = remember { TransferHistoryManager.getInstance(context) }
    var records by remember { mutableStateOf(historyMgr.getRecords()) }

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
                        .clickable(onClick = onBack),
                    contentAlignment = Alignment.Center
                ) {
                    Text("←", fontSize = 20.sp, color = OnBackground)
                }

                if (records.isNotEmpty()) {
                    TactilePillButton(
                        text = "Clear History",
                        active = false,
                        onClick = {
                            historyMgr.clear()
                            records = emptyList()
                        },
                        minHeight = 36.dp
                    )
                }
            }

            Spacer(Modifier.height(16.dp))

            Row(verticalAlignment = Alignment.CenterVertically) {
                TactileGlowRing(size = 36.dp, ringColor = CyanAccent) {
                    Text("📜", fontSize = 18.sp)
                }
                Spacer(Modifier.width(12.dp))
                Column {
                    Text(
                        "Transfer Activity",
                        fontSize = 20.sp,
                        fontWeight = FontWeight.Bold,
                        color = OnBackground,
                    )
                    Text(
                        "${records.size} total recorded transfers",
                        fontSize = 12.sp,
                        color = OnSurfaceVariant,
                    )
                }
            }

            Spacer(Modifier.height(16.dp))

            if (records.isEmpty()) {
                TactileCard(
                    modifier = Modifier.fillMaxWidth(),
                    cornerRadius = 22.dp,
                    backgroundColor = Surface,
                    borderColor = CardBorderSubtle
                ) {
                    Column(
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(32.dp),
                        horizontalAlignment = Alignment.CenterHorizontally,
                        verticalArrangement = Arrangement.spacedBy(14.dp),
                    ) {
                        TactileGlowRing(size = 64.dp, ringColor = CyanAccent) {
                            Text("📂", fontSize = 32.sp)
                        }
                        Text(
                            "No Transfer History Yet",
                            fontSize = 17.sp,
                            fontWeight = FontWeight.Bold,
                            color = OnBackground,
                        )
                        Text(
                            "Completed and active file transfers between this phone and your PC will be recorded here.",
                            fontSize = 13.sp,
                            color = OnSurfaceVariant,
                            textAlign = androidx.compose.ui.text.style.TextAlign.Center,
                        )
                    }
                }
            } else {
                LazyColumn(
                    verticalArrangement = Arrangement.spacedBy(12.dp),
                    modifier = Modifier.fillMaxSize(),
                ) {
                    items(records, key = { it.id }) { record ->
                        HistoryRecordCard(record)
                    }
                }
            }
        }
    }
}

@Composable
private fun HistoryRecordCard(record: TransferRecord) {
    val dateFormat = remember { SimpleDateFormat("MMM dd, HH:mm", Locale.getDefault()) }
    val dateStr = remember(record.timestamp) { dateFormat.format(Date(record.timestamp)) }
    val isSent = record.direction == "sent"

    TactileCard(
        modifier = Modifier.fillMaxWidth(),
        cornerRadius = 18.dp,
        backgroundColor = Surface,
        borderColor = CardBorder
    ) {
        Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
            // Row 1: Direction + Transport + Status + Date
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    TactileBadge(
                        text = if (isSent) "📤 Sent" else "📥 Received",
                        badgeColor = if (isSent) PrimaryLight else CyanAccent
                    )
                    TactileBadge(
                        text = record.transportType.uppercase(),
                        badgeColor = Outline,
                        textColor = OnSurfaceVariant
                    )
                }

                val (statusText, statusColor) = when (record.status) {
                    "completed" -> "✓ Success" to Secondary
                    "interrupted" -> "⚠ Interrupted" to Warning
                    else -> "✕ Failed" to Error
                }
                TactileBadge(statusText, statusColor)
            }

            // Row 2: File summary
            val filesStr = record.files.take(3).joinToString(", ") +
                    if (record.files.size > 3) " + ${record.files.size - 3} more" else ""
            Text(
                text = "${record.files.size} file(s): $filesStr",
                color = OnBackground,
                fontSize = 14.sp,
                fontWeight = FontWeight.SemiBold,
                maxLines = 2,
                overflow = TextOverflow.Ellipsis,
            )

            // Row 3: Size, duration, speed, error
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Row(horizontalArrangement = Arrangement.spacedBy(10.dp), verticalAlignment = Alignment.CenterVertically) {
                    Text(
                        text = formatBytes(record.totalBytes),
                        color = CyanAccent,
                        fontSize = 12.sp,
                        fontWeight = FontWeight.Bold
                    )
                    if (record.durationSec > 0 && record.status == "completed") {
                        val durStr = String.format(Locale.US, "%.1fs", record.durationSec)
                        val speedStr = if (record.totalBytes > 0) {
                            val mbPerSec = (record.totalBytes / (1024.0 * 1024.0)) / record.durationSec
                            " (" + String.format(Locale.US, "%.1f MB/s", mbPerSec) + ")"
                        } else ""
                        Text(
                            text = "•  $durStr$speedStr",
                            color = OnSurfaceVariant,
                            fontSize = 12.sp,
                        )
                    }
                }

                Text(
                    text = dateStr,
                    color = OnSurfaceVariant,
                    fontSize = 11.sp,
                )
            }

            if (record.errorReason.isNotBlank()) {
                Text(
                    text = record.errorReason,
                    color = Error,
                    fontSize = 11.sp,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
            }
        }
    }
}
