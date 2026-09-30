package com.photobeam.app.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.photobeam.app.data.TransferHistoryManager
import com.photobeam.app.data.TransferRecord
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
            .background(Background),
    ) {
        Column(
            modifier = Modifier
                .fillMaxSize()
                .statusBarsPadding()
                .padding(24.dp),
        ) {
            // ── Top Bar ───────────────────────────────────────────────────────
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                TextButton(onClick = onBack) {
                    Text("← Back", color = OnSurfaceVariant)
                }

                if (records.isNotEmpty()) {
                    TextButton(
                        onClick = {
                            historyMgr.clear()
                            records = emptyList()
                        }
                    ) {
                        Text("Clear History", color = Error)
                    }
                }
            }

            Spacer(Modifier.height(12.dp))

            Text(
                "📜 Transfer History",
                fontSize = 24.sp,
                fontWeight = FontWeight.Bold,
                color = OnBackground,
            )

            Spacer(Modifier.height(16.dp))

            if (records.isEmpty()) {
                Card(
                    modifier = Modifier.fillMaxWidth(),
                    shape = RoundedCornerShape(20.dp),
                    colors = CardDefaults.cardColors(containerColor = Surface),
                ) {
                    Column(
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(40.dp),
                        horizontalAlignment = Alignment.CenterHorizontally,
                        verticalArrangement = Arrangement.spacedBy(12.dp),
                    ) {
                        Text("📂", fontSize = 48.sp)
                        Text(
                            "No transfer history yet",
                            fontSize = 16.sp,
                            fontWeight = FontWeight.SemiBold,
                            color = OnBackground,
                        )
                        Text(
                            "Completed and attempted transfers will appear here.",
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

    Card(
        modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(16.dp),
        colors = CardDefaults.cardColors(containerColor = Surface),
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            // Row 1: Direction + Transport + Status + Date
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(6.dp),
                ) {
                    val dirIcon = if (record.direction == "sent") "📤 Sent" else "📥 Received"
                    Text(
                        text = dirIcon,
                        fontWeight = FontWeight.SemiBold,
                        fontSize = 15.sp,
                        color = OnBackground,
                    )
                    Text(
                        text = "[${record.transportType}]",
                        fontSize = 12.sp,
                        color = OnSurfaceVariant,
                    )
                }

                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    val (statusText, statusColor) = when (record.status) {
                        "completed" -> "✓ Completed" to Secondary
                        "interrupted" -> "⚠ Interrupted" to androidx.compose.ui.graphics.Color(0xFFFBBF24)
                        else -> "✕ Failed" to Error
                    }
                    Text(
                        text = statusText,
                        color = statusColor,
                        fontWeight = FontWeight.SemiBold,
                        fontSize = 13.sp,
                    )
                    Text(
                        text = dateStr,
                        color = OnSurfaceVariant,
                        fontSize = 11.sp,
                    )
                }
            }

            // Row 2: File summary
            val filesStr = record.files.take(3).joinToString(", ") +
                    if (record.files.size > 3) " + ${record.files.size - 3} more" else ""
            Text(
                text = "${record.files.size} file(s): $filesStr",
                color = OnSurface,
                fontSize = 13.sp,
                maxLines = 2,
                overflow = TextOverflow.Ellipsis,
            )

            // Row 3: Size, duration, speed, error
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                    Text(
                        text = "Size: ${formatBytes(record.totalBytes)}",
                        color = OnSurfaceVariant,
                        fontSize = 12.sp,
                    )
                    if (record.durationSec > 0 && record.status == "completed") {
                        val durStr = String.format(Locale.US, "%.1fs", record.durationSec)
                        val speedStr = if (record.totalBytes > 0) {
                            val mbPerSec = (record.totalBytes / (1024.0 * 1024.0)) / record.durationSec
                            " (" + String.format(Locale.US, "%.1f MB/s", mbPerSec) + ")"
                        } else ""
                        Text(
                            text = "Duration: $durStr$speedStr",
                            color = OnSurfaceVariant,
                            fontSize = 12.sp,
                        )
                    }
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
}
