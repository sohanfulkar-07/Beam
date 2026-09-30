"""
Unit tests for PhotoBeam human-readable error formatter.
Verifies all 12 required error scenarios produce clear, user-friendly messages
and preserve technical details for the collapsible view.
"""
import sys
import os
from pathlib import Path

# Add windows project path to sys.path
WIN_DIR = Path(__file__).resolve().parent.parent.parent / "windows" / "photobeam-windows"
sys.path.insert(0, str(WIN_DIR))

from ui.error_formatter import format_friendly_error, FriendlyError


def test_invalid_qr():
    err = format_friendly_error("invalid_payload: schema verification failed")
    assert "Invalid QR" in err.title
    assert "not recognized" in err.message
    assert err.technical_details is not None


def test_expired_qr():
    err = format_friendly_error("Session qr_expired after 300 seconds")
    assert "Expired" in err.title
    assert "new QR code" in err.message


def test_connection_refused():
    err = format_friendly_error("WSAECONNREFUSED: Connection refused by target machine")
    assert "Connection Refused" in err.title
    assert "ready" in err.message.lower() or "receive" in err.message.lower()



def test_wifi_disconnected():
    err = format_friendly_error("wifi-primary transport socket closed unexpectedly")
    assert "Wi-Fi" in err.title
    assert "same Wi-Fi" in err.message


def test_usb_disconnected():
    err = format_friendly_error("usb-tunnel disconnected: device offline")
    assert "USB" in err.title
    assert "cable" in err.message


def test_both_transports_disconnected():
    err = format_friendly_error("all transports failed and no route available")
    assert "Lost" in err.title or "Disconnected" in err.title
    assert "Wi-Fi" in err.message and "USB" in err.message


def test_storage_insufficient():
    err = format_friendly_error("not_enough_space: required 5000000000, free 100000000")
    assert "Storage" in err.title or "Space" in err.title
    assert "free up" in err.message


def test_file_changed():
    err = format_friendly_error("source file was modified while paused: mtime changed")
    assert "Changed" in err.title or "Modified" in err.title
    assert "modified" in err.message


def test_resume_failure():
    err = format_friendly_error("unsafe_to_resume: corrupted or missing temporary file")
    assert "Resume" in err.title
    assert "verified" in err.message or "restart" in err.message


def test_corrupted_transfer():
    err = format_friendly_error("Integrity check failed: hash_mismatch on video.mp4")
    assert "Integrity" in err.title or "Corrupt" in err.title
    assert "byte-perfect" in err.message or "did not match" in err.message


def test_transfer_cancelled():
    err = format_friendly_error("Transfer cancelled by user")
    assert "Cancelled" in err.title
    assert "cancelled" in err.message.lower()


def test_unexpected_failure():
    err = format_friendly_error("SSL error: SSLV3_ALERT_BAD_RECORD_MAC: unknown protocol error")
    assert "Connection" in err.title or "Error" in err.title
    assert "unexpected" in err.message.lower() or "interrupted" in err.message.lower()
    assert "SSLV3_ALERT_BAD_RECORD_MAC" in err.technical_details
