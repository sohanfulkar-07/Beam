"""
PhotoBeam Windows UI — SendScreen State and Connection Badge Tests (Offscreen)
"""
import os
import sys
import tempfile
import time
from pathlib import Path
import pytest

os.environ["QT_QPA_PLATFORM"] = "offscreen"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "protocol" / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "windows" / "photobeam-windows"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "windows" / "photobeam-windows" / "ui"))

from PyQt6.QtWidgets import QApplication
from send_screen import SendScreen
from connection_manager import ConnectionManager
from pairing_manager import PairingManager
from models import DeviceIdentity, DeviceEndpoint, PairedDevice, TrustStatus, ConnectionState, PresenceState


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(["--platform", "offscreen"])
    return app


def test_send_screen_no_devices(qapp):
    temp_dir = Path(tempfile.mkdtemp())
    pm = PairingManager(storage_dir=temp_dir)
    cm = ConnectionManager(pairing_manager=pm)

    send = SendScreen(connection_manager=cm)
    # The badge must NOT be stuck on "Looking for devices…"
    assert "Looking for devices" not in send._conn_badge.text()
    assert "Scan QR to Connect" in send._conn_badge.text()


def test_send_screen_with_connected_device(qapp):
    temp_dir = Path(tempfile.mkdtemp())
    pm = PairingManager(storage_dir=temp_dir)
    cm = ConnectionManager(pairing_manager=pm)

    now = int(time.time())
    # Add a paired device that is CONNECTED
    paired = PairedDevice(
        identity=DeviceIdentity(
            device_id="dev-cph2717",
            name="CPH2717",
            public_key="pubkey==",
            created_at=now,
            last_seen=now,
            trust_status=TrustStatus.TRUSTED,
        ),
        endpoint=DeviceEndpoint(
            addrs=["127.0.0.1"],
            port=47470,
            transports=["usb"],
            cert_fp="sha256:abcd",
            updated_at=now,
        ),
        connection_state=ConnectionState.CONNECTED,
        presence_state=PresenceState.DISCOVERED,
    )
    pm.save_paired_device(paired)

    send = SendScreen(connection_manager=cm)
    send.refresh_connection_status()

    badge_text = send._conn_badge.text()
    assert "Connected to CPH2717" in badge_text
    assert "Looking for devices" not in badge_text
    assert "CPH2717" in send._device_instruction_label.text()


def test_send_screen_set_target_device_and_files(qapp, tmp_path):
    temp_dir = Path(tempfile.mkdtemp())
    pm = PairingManager(storage_dir=temp_dir)
    cm = ConnectionManager(pairing_manager=pm)

    now = int(time.time())
    dev1 = PairedDevice(
        identity=DeviceIdentity(
            device_id="dev-1",
            name="Phone One",
            public_key="k1==",
            created_at=now,
            last_seen=now,
            trust_status=TrustStatus.TRUSTED,
        ),
        endpoint=DeviceEndpoint(
            addrs=["192.168.1.50"],
            port=47470,
            transports=["wifi"],
            cert_fp="sha256:1111",
            updated_at=now,
        ),
        connection_state=ConnectionState.CONNECTED,
    )
    dev2 = PairedDevice(
        identity=DeviceIdentity(
            device_id="dev-2",
            name="Phone Two",
            public_key="k2==",
            created_at=now,
            last_seen=now,
            trust_status=TrustStatus.TRUSTED,
        ),
        endpoint=DeviceEndpoint(
            addrs=["192.168.1.60"],
            port=47470,
            transports=["wifi"],
            cert_fp="sha256:2222",
            updated_at=now,
        ),
        connection_state=ConnectionState.DISCONNECTED,
    )
    pm.save_paired_device(dev1)
    pm.save_paired_device(dev2)

    send = SendScreen(connection_manager=cm)
    send.set_target_device("dev-2")

    assert "Scan QR to Connect" in send._conn_badge.text()

    # Test file selection
    f1 = tmp_path / "photo1.jpg"
    f1.write_bytes(b"12345")
    f2 = tmp_path / "doc.pdf"
    f2.write_bytes(b"67890")

    send.set_selected_files([f1, f2])
    assert len(send._files) == 2
    assert "2 file(s) selected" in send._files_summary_label.text()


def test_send_screen_uri_verified(qapp):
    temp_dir = Path(tempfile.mkdtemp())
    pm = PairingManager(storage_dir=temp_dir)
    cm = ConnectionManager(pairing_manager=pm)

    send = SendScreen(connection_manager=cm)
    send.set_receiver_uri("photobeam://connect/eyJuYW1lIjoidGVzdCJ9")

    assert "Receiver Link Verified" in send._conn_badge.text()
