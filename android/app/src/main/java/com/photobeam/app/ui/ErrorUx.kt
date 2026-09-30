package com.photobeam.app.ui

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.photobeam.app.ui.theme.*

data class FriendlyError(
    val title: String,
    val message: String,
    val technicalDetails: String? = null,
) {
    companion object {
        fun from(raw: String): FriendlyError {
            val lower = raw.lowercase()
            return when {
                // 1. Invalid QR
                lower.contains("invalid qr") || lower.contains("invalid_token") ||
                lower.contains("invalid_payload") || lower.contains("malformed") ||
                lower.contains("not a photobeam link") -> {
                    FriendlyError(
                        title = "Invalid QR Code",
                        message = "The scanned code is not recognized as a valid PhotoBeam session. Please scan a fresh QR code from the receiving device.",
                        technicalDetails = raw,
                    )
                }

                // 2. Expired QR
                lower.contains("qr_expired") || lower.contains("expired") -> {
                    FriendlyError(
                        title = "QR Code Expired",
                        message = "This transfer session has expired for security. Please generate a new QR code on the receiver and try again.",
                        technicalDetails = raw,
                    )
                }

                // 3. Storage Insufficient
                lower.contains("not_enough_space") || lower.contains("storage full") ||
                lower.contains("enospc") || lower.contains("not enough storage") -> {
                    FriendlyError(
                        title = "Not Enough Storage Space",
                        message = "The receiving device does not have enough free disk space to store these files. Please free up storage space and try again.",
                        technicalDetails = raw,
                    )
                }

                // 4. File Changed
                lower.contains("source file was modified") || lower.contains("file changed") ||
                lower.contains("modified or deleted while paused") -> {
                    FriendlyError(
                        title = "Source File Changed",
                        message = "A source file was modified, moved, or deleted while paused. The transfer must be restarted to maintain byte-perfect integrity.",
                        technicalDetails = raw,
                    )
                }

                // 5. Resume Failure
                lower.contains("invalid_resume_state") || lower.contains("corrupted or missing temporary file") ||
                lower.contains("cannot safely resume") || lower.contains("unsafe_to_resume") -> {
                    FriendlyError(
                        title = "Cannot Resume Transfer",
                        message = "The saved transfer state could not be verified. You can restart the transfer from the beginning.",
                        technicalDetails = raw,
                    )
                }

                // 6. Corrupted Transfer / Checksum Mismatch
                lower.contains("hash_mismatch") || lower.contains("sha-256 mismatch") ||
                lower.contains("bad_checksum") || lower.contains("integrity check failed") ||
                lower.contains("integrity") -> {
                    FriendlyError(
                        title = "File Verification Failed",
                        message = "The transferred file did not match the original file (checksum mismatch). The incomplete file was discarded to protect your storage.",
                        technicalDetails = raw,
                    )
                }

                // 7. Transfer Cancelled
                lower.contains("user_rejected") || lower.contains("declined by user") ||
                lower.contains("cancelled by user") || lower.contains("cancelled by sender") ||
                lower.contains("cancelled") -> {
                    FriendlyError(
                        title = "Transfer Cancelled",
                        message = "The transfer was cancelled. Any incomplete files have been safely cleaned up.",
                        technicalDetails = null,
                    )
                }

                // 8. Connection Refused
                lower.contains("connection refused") || lower.contains("econnrefused") ||
                lower.contains("connectexception") || lower.contains("could not reach receiver") -> {
                    FriendlyError(
                        title = "Unable to Connect",
                        message = "Could not reach the receiving device. Ensure both devices are connected to the same Wi-Fi network or hotspot and PhotoBeam is open on the receiver.",
                        technicalDetails = raw,
                    )
                }

                // 9. Wi-Fi Disconnected
                lower.contains("wifi disconnected") || lower.contains("network unreachable") ||
                lower.contains("enetunreach") || lower.contains("network is unreachable") -> {
                    FriendlyError(
                        title = "Wi-Fi Disconnected",
                        message = "The Wi-Fi connection was interrupted. If USB is active, the transfer will continue; otherwise please reconnect to Wi-Fi.",
                        technicalDetails = raw,
                    )
                }

                // 10. USB Disconnected
                lower.contains("usb disconnected") || lower.contains("usb connection lost") -> {
                    FriendlyError(
                        title = "USB Disconnected",
                        message = "The USB connection was unplugged. The transfer will automatically continue over Wi-Fi if available.",
                        technicalDetails = raw,
                    )
                }

                // 11. Both Transports Disconnected
                lower.contains("all transports failed") || lower.contains("no available transports") ||
                lower.contains("socket closed") || lower.contains("broken pipe") ||
                lower.contains("connection reset") || lower.contains("timed out") ||
                lower.contains("sockettimeoutexception") -> {
                    FriendlyError(
                        title = "Connection Lost",
                        message = "The connection to the other device was lost. Please check your Wi-Fi or USB connection and try again.",
                        technicalDetails = raw,
                    )
                }

                // 12. Unexpected Failure Fallback
                else -> {
                    FriendlyError(
                        title = "Transfer Stopped",
                        message = "An unexpected error occurred during transfer. Please check connection and try again.",
                        technicalDetails = raw,
                    )
                }
            }
        }
    }
}

@Composable
fun FriendlyErrorCard(
    error: FriendlyError,
    onRetry: () -> Unit,
    onCancel: () -> Unit,
    retryText: String = "Try Again",
    cancelText: String = "Cancel",
    modifier: Modifier = Modifier,
) {
    var showDetails by remember { mutableStateOf(false) }

    Column(
        modifier = modifier.fillMaxWidth(),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        Text("❌", fontSize = 44.sp)
        Text(
            text = error.title,
            color = Error,
            fontSize = 20.sp,
            fontWeight = FontWeight.Bold,
            textAlign = TextAlign.Center,
        )
        Text(
            text = error.message,
            color = OnSurfaceVariant,
            fontSize = 14.sp,
            textAlign = TextAlign.Center,
            lineHeight = 20.sp,
        )

        // Expandable technical details section
        if (!error.technicalDetails.isNullOrBlank()) {
            Spacer(Modifier.height(2.dp))
            Surface(
                color = SurfaceVariant.copy(alpha = 0.5f),
                shape = RoundedCornerShape(8.dp),
                modifier = Modifier
                    .fillMaxWidth()
                    .clip(RoundedCornerShape(8.dp))
                    .clickable { showDetails = !showDetails }
                    .padding(horizontal = 12.dp, vertical = 8.dp),
            ) {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Text(
                        text = if (showDetails) "Hide technical details ▴" else "Show technical details ▾",
                        color = OnSurfaceVariant,
                        fontSize = 12.sp,
                        fontWeight = FontWeight.Medium,
                    )
                }
            }

            AnimatedVisibility(visible = showDetails) {
                Surface(
                    color = Background,
                    shape = RoundedCornerShape(8.dp),
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(top = 4.dp),
                ) {
                    Text(
                        text = error.technicalDetails,
                        color = OnSurfaceVariant,
                        fontSize = 11.sp,
                        fontFamily = FontFamily.Monospace,
                        modifier = Modifier.padding(12.dp),
                    )
                }
            }
        }

        Spacer(Modifier.height(8.dp))

        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Button(
                onClick = onRetry,
                modifier = Modifier.weight(1f),
                colors = ButtonDefaults.buttonColors(containerColor = Primary),
                shape = RoundedCornerShape(12.dp),
            ) {
                Text(retryText, color = OnBackground, fontWeight = FontWeight.SemiBold)
            }
            OutlinedButton(
                onClick = onCancel,
                modifier = Modifier.weight(1f),
                shape = RoundedCornerShape(12.dp),
            ) {
                Text(cancelText, color = OnSurfaceVariant)
            }
        }
    }
}
