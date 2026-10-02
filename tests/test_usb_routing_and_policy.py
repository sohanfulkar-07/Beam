"""
PhotoBeam — Test USB / Hotspot Routing & PC->Mobile USB Policy Enforcement
"""
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_root = Path(__file__).resolve().parent.parent
_win_dir = _root / "windows" / "photobeam-windows"
_proto_dir = _root / "protocol"
_ui_dir = _win_dir / "ui"

for p in (str(_win_dir), str(_ui_dir), str(_proto_dir), str(_root)):
    if p in sys.path:
        sys.path.remove(p)
    sys.path.insert(0, p)

from protocol_v2 import (
    CONTROL_PORT,
    CONTROL_USB_PORT,
    DATA_PORT,
    DATA_USB_PORT,
    PC_TO_PHONE_CONTROL_FORWARD_PORT,
    PC_TO_PHONE_DATA_FORWARD_PORT,
)
from transport.usb_transport import (
    setup_bidirectional_adb_tunnels,
    setup_adb_forward,
    setup_adb_reverse,
)
from connection_manager import ConnectionManager
from data_receiver import DataReceiver
from send_screen import SendScreen
from PyQt6.QtWidgets import QApplication


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(["--platform", "offscreen"])
    return app


def test_bidirectional_adb_port_forwarding_configuration():
    """Verify setup_bidirectional_adb_tunnels executes the exact forward and reverse port mappings."""
    assert CONTROL_PORT == 47470
    assert CONTROL_USB_PORT == 47471
    assert DATA_PORT == 47474
    assert DATA_USB_PORT == 47475
    assert PC_TO_PHONE_CONTROL_FORWARD_PORT == 47480
    assert PC_TO_PHONE_DATA_FORWARD_PORT == 47484

    with patch("transport.usb_transport.setup_adb_reverse") as mock_rev, \
         patch("transport.usb_transport.setup_adb_forward") as mock_fwd:
        mock_rev.return_value = True
        mock_fwd.return_value = True

        ok = setup_bidirectional_adb_tunnels("mock_adb", device_serial="TEST_DEVICE_123")
        assert ok is True

        # Check reverse calls (Phone -> PC)
        mock_rev.assert_any_call("mock_adb", remote_port=47471, local_port=47470, device_serial="TEST_DEVICE_123")
        mock_rev.assert_any_call("mock_adb", remote_port=47475, local_port=47474, device_serial="TEST_DEVICE_123")
        mock_rev.assert_any_call("mock_adb", remote_port=47478, local_port=47478, device_serial="TEST_DEVICE_123")

        # Check forward calls (PC -> Phone)
        mock_fwd.assert_any_call("mock_adb", local_port=47480, remote_port=47470, device_serial="TEST_DEVICE_123")
        mock_fwd.assert_any_call("mock_adb", local_port=47484, remote_port=47474, device_serial="TEST_DEVICE_123")


def test_is_usb_available_detection():
    """Verify is_usb_available correctly returns True on active USB session or ADB attached device."""
    cm = ConnectionManager()

    # When no active session and no ADB device
    with patch("connection_manager.find_adb", return_value=None):
        assert cm.is_usb_available() is False

    # When ADB device is attached and online
    with patch("connection_manager.find_adb", return_value="mock_adb"), \
         patch("connection_manager.adb_devices", return_value=[{"serial": "XYZ", "state": "device"}]), \
         patch("connection_manager.setup_bidirectional_adb_tunnels", return_value=True):
        assert cm.is_usb_available() is True


def test_pc_send_blocked_when_usb_unavailable(qapp):
    """Verify PC -> Mobile transfer immediately blocks and displays user error dialog when USB is unplugged."""
    cm = ConnectionManager()
    send = SendScreen(connection_manager=cm)

    # Mock file selection
    test_file = Path(tempfile.gettempdir()) / "test_send.bin"
    test_file.write_bytes(b"hello world")
    send._files = [test_file]

    # Force USB unavailable
    with patch.object(cm, "is_usb_available", return_value=False), \
         patch("PyQt6.QtWidgets.QMessageBox.warning") as mock_warn:
        send._start_send()

        # Dialog MUST be displayed with exact required error
        mock_warn.assert_called_once()
        args = mock_warn.call_args[0]
        assert "Cannot send file: USB connection is not available" in args[2]
        # Progress view must NOT have started
        assert send._progress_container.isVisible() is False


def test_pc_send_permitted_when_usb_available(qapp):
    """Verify PC -> Mobile transfer proceeds with DataSender over 127.0.0.1:47484 when USB is active."""
    cm = ConnectionManager()
    send = SendScreen(connection_manager=cm)

    test_file = Path(tempfile.gettempdir()) / "test_send_usb.bin"
    test_file.write_bytes(b"hello usb")
    send._files = [test_file]

    with patch.object(cm, "is_usb_available", return_value=True), \
         patch("send_screen.SenderWorker") as mock_worker_cls, \
         patch("send_screen.QThread") as mock_thread_cls:
        mock_worker = MagicMock()
        mock_worker_cls.return_value = mock_worker
        mock_thread = MagicMock()
        mock_thread_cls.return_value = mock_thread

        send._start_send()

        # SenderWorker must be initialized with port 47484 (PC_TO_PHONE_DATA_FORWARD_PORT) and 127.0.0.1
        mock_worker_cls.assert_called_once_with(
            send._uri,
            send._files,
            is_usb=True,
            peer_ip="127.0.0.1",
            peer_port=47484,
        )
        assert send._progress_container.isHidden() is False


def test_data_receiver_binds_to_wildcard_for_hotspot():
    """Verify PC DataReceiver binds to 0.0.0.0:47474 to accept incoming transfers over Hotspot."""
    with tempfile.TemporaryDirectory() as dst:
        receiver = DataReceiver(download_dir=Path(dst), port=0)
        receiver.start()
        try:
            assert receiver.is_running is True
            assert receiver._server_sock is not None
            # Check host binding
            sock_name = receiver._server_sock.getsockname()
            assert sock_name[0] == "0.0.0.0"
        finally:
            receiver.stop()
