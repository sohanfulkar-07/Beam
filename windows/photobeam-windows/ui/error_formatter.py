"""
PhotoBeam Windows UI — Human-Readable Error Formatter

Translates raw protocol errors, socket exceptions, and technical codes
into clear, actionable messages for end users with optional technical details.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class FriendlyError:
    title: str
    message: str
    technical_details: Optional[str] = None


def format_friendly_error(raw: str) -> FriendlyError:
    lower = raw.lower()

    # 1. Invalid QR
    if any(k in lower for k in ["invalid qr", "invalid_token", "invalid_payload", "malformed", "not a photobeam link"]):
        return FriendlyError(
            title="Invalid QR Code",
            message="The provided code or link is not recognized as a valid PhotoBeam session. Please scan or copy a fresh QR code from the receiving device.",
            technical_details=raw,
        )

    # 2. Expired QR
    if any(k in lower for k in ["qr_expired", "expired"]):
        return FriendlyError(
            title="QR Code Expired",
            message="This transfer session has expired for security. Please generate a new QR code on the receiver and try again.",
            technical_details=raw,
        )

    # 3. Connection Refused
    if any(k in lower for k in ["connection refused", "econnrefused", "connectexception", "wsaeconnrefused", "could not reach receiver"]):
        return FriendlyError(
            title="Connection Refused",
            message="Could not reach the receiving device. Ensure both devices are connected to the same Wi-Fi network or hotspot and PhotoBeam is open and ready to receive.",
            technical_details=raw,
        )

    # 4. Storage Insufficient
    if any(k in lower for k in ["not_enough_space", "storage full", "enospc", "not enough storage"]):
        return FriendlyError(
            title="Not Enough Storage Space",
            message="The receiver does not have enough free disk space to store these files. Please free up storage space and try again.",
            technical_details=raw,
        )

    # 5. File Changed
    if any(k in lower for k in ["source file was modified", "file changed", "modified or deleted while paused"]):
        return FriendlyError(
            title="Source File Changed",
            message="A source file was modified, moved, or deleted while paused. The transfer must be restarted to maintain byte-perfect integrity.",
            technical_details=raw,
        )

    # 6. Resume Failure
    if any(k in lower for k in ["invalid_resume_state", "corrupted or missing temporary file", "cannot safely resume", "unsafe_to_resume"]):
        return FriendlyError(
            title="Cannot Resume Transfer",
            message="The saved transfer state could not be verified. You can restart the transfer from the beginning.",
            technical_details=raw,
        )

    # 7. Corrupted Transfer / Checksum Mismatch
    if any(k in lower for k in ["hash_mismatch", "sha-256 mismatch", "bad_checksum", "integrity check failed", "corruption"]):
        return FriendlyError(
            title="Transfer Integrity Error",
            message="The transferred file did not match the original file (SHA-256 checksum mismatch). The corrupted file was safely discarded.",
            technical_details=raw,
        )

    # 8. Transfer Cancelled
    if any(k in lower for k in ["user_rejected", "declined by user", "cancelled by user", "cancelled by sender", "cancelled", "cancel requested"]):
        return FriendlyError(
            title="Transfer Cancelled",
            message="The transfer was cancelled. Any incomplete files have been safely cleaned up.",
            technical_details=None,
        )

    # 9. USB Disconnected
    if any(k in lower for k in ["usb disconnected", "usb-tunnel disconnected", "usb connection lost"]):
        return FriendlyError(
            title="USB Disconnected",
            message="The USB connection was unplugged or disconnected. Check the USB cable or continue over Wi-Fi if available.",
            technical_details=raw,
        )

    # 10. Wi-Fi Disconnected
    if any(k in lower for k in ["wifi disconnected", "wifi-primary transport socket closed", "network unreachable", "enetunreach"]):
        return FriendlyError(
            title="Wi-Fi Disconnected",
            message="The Wi-Fi connection was interrupted. Please ensure both devices remain connected to the same Wi-Fi network.",
            technical_details=raw,
        )

    # 11. Authentication or Handshake Error
    if any(k in lower for k in ["authentication failed", "handshake error", "auth failed", "auth rejected", "invalid_state", "expected hello"]):
        return FriendlyError(
            title="Authentication Failed",
            message="Could not securely authenticate with the sending device. Please generate a fresh QR code on the laptop and scan again.",
            technical_details=raw,
        )

    # 12. Sender Disconnected / Connection Closed
    if any(k in lower for k in ["sender disconnected", "connection closed", "forcibly closed", "10054", "disconnected while selecting", "disconnected before sending"]):
        return FriendlyError(
            title="Sender Disconnected",
            message="The sending device disconnected. Make sure PhotoBeam is still open on your mobile device and try scanning again.",
            technical_details=raw,
        )

    # 13. General Connection Loss
    if any(k in lower for k in ["all transports failed", "no available transports", "socket closed", "broken pipe", "connection reset", "timed out", "timeout", "not connected"]):
        return FriendlyError(
            title="Connection Lost",
            message="Lost connection across active transports (Wi-Fi and USB). Please verify device connectivity and try again.",
            technical_details=raw,
        )

    # 14. Unexpected Failure Fallback
    return FriendlyError(
        title="Unexpected Connection Error",
        message="An unexpected error occurred during transfer. Please check connection and try again.",
        technical_details=raw,
    )

