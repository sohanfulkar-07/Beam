"""
PhotoBeam Windows UI — Unified Connect & Mirror Tests (Offscreen)
"""
import os
import sys
import tempfile
from pathlib import Path
import pytest

os.environ["QT_QPA_PLATFORM"] = "offscreen"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "protocol" / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "windows" / "photobeam-windows"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "windows" / "photobeam-windows" / "ui"))

from PyQt6.QtWidgets import QApplication
from home_screen import HomeScreen
from pairing_dialog import PairingDialog
from screen_viewer import ScreenViewer
from connection_manager import ConnectionManager
from pairing_manager import PairingManager


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(["--platform", "offscreen"])
    return app


def test_home_screen_initialization(qapp):
    temp_dir = Path(tempfile.mkdtemp())
    pm = PairingManager(storage_dir=temp_dir)
    cm = ConnectionManager(pairing_manager=pm)

    home = HomeScreen(connection_manager=cm)
    assert home.pairing_manager is pm
    assert home.local_badge is not None
    assert home.scroll_area is not None

    # Should render empty state when no devices paired
    home.refresh_devices()
    assert home.devices_layout.count() > 0


def test_pairing_dialog_generation(qapp):
    temp_dir = Path(tempfile.mkdtemp())
    pm = PairingManager(storage_dir=temp_dir)
    cm = ConnectionManager(pairing_manager=pm)

    dlg = PairingDialog(connection_manager=cm)
    assert dlg.qr_label.pixmap() is not None
    assert not dlg.qr_label.pixmap().isNull()
    assert dlg.current_payload is not None
    assert dlg.current_payload.v == 1


def test_screen_viewer_lifecycle(qapp):
    viewer = ScreenViewer(device_id="test-phone", device_name="OnePlus Test")
    assert viewer.device_id == "test-phone"
    assert viewer.device_name == "OnePlus Test"
    assert viewer.acceptDrops() is True
    assert viewer.fps_badge.text() == "0 FPS"

    viewer.stop()


def test_pairing_dialog_auto_dismiss_on_handshake(qapp):
    temp_dir = Path(tempfile.mkdtemp())
    pm = PairingManager(storage_dir=temp_dir)
    cm = ConnectionManager(pairing_manager=pm)
    dlg = PairingDialog(connection_manager=cm)

    try:
        from src.models import (
            DeviceIdentity, DeviceEndpoint, PairedDevice, ConnectionState, PresenceState, TrustStatus
        )
    except ImportError:
        from models import (
            DeviceIdentity, DeviceEndpoint, PairedDevice, ConnectionState, PresenceState, TrustStatus
        )

    mock_dev = PairedDevice(
        identity=DeviceIdentity(
            device_id="phone-123",
            name="OnePlus Nord CE 5",
            public_key="pk123",
            created_at=1000,
            last_seen=1000,
            trust_status=TrustStatus.TRUSTED,
            capabilities=[],
        ),
        endpoint=DeviceEndpoint(addrs=["192.168.1.50"], port=47470, transports=["wifi"], cert_fp="", updated_at=1000),
        connection_state=ConnectionState.CONNECTED,
        presence_state=PresenceState.DISCOVERED,
    )
    pm.save_paired_device(mock_dev)

    # Emit sig_pairing_completed
    cm.sig_pairing_completed.emit(mock_dev)
    qapp.processEvents()

    assert "✓ Connected to OnePlus Nord CE 5!" in dlg.status_label.text()
    assert dlg._closing_soon is True
    dlg.close()


def test_main_window_reactive_ui_sync(qapp):
    temp_dir = Path(tempfile.mkdtemp())
    pm = PairingManager(storage_dir=temp_dir)
    cm = ConnectionManager(pairing_manager=pm)

    try:
        from src.models import (
            DeviceIdentity, DeviceEndpoint, PairedDevice, ConnectionState, PresenceState, TrustStatus
        )
    except ImportError:
        from models import (
            DeviceIdentity, DeviceEndpoint, PairedDevice, ConnectionState, PresenceState, TrustStatus
        )

    from main_window import MainWindow
    win = MainWindow()
    win.connection_manager = cm
    cm.sig_device_connected.connect(win._on_sig_device_connected)
    cm.sig_device_disconnected.connect(win._on_sig_device_disconnected)

    # Initial state: disconnected
    win._update_top_status()
    assert "Disconnected" in win._top_status_text.text()
    assert "⚪" in win._top_status_dot.text()

    # Device connects
    mock_dev = PairedDevice(
        identity=DeviceIdentity(
            device_id="phone-456",
            name="OnePlus Nord CE 5",
            public_key="pk456",
            created_at=1000,
            last_seen=1000,
            trust_status=TrustStatus.TRUSTED,
            capabilities=[],
        ),
        endpoint=DeviceEndpoint(addrs=["192.168.1.60"], port=47470, transports=["wifi"], cert_fp="", updated_at=1000),
        connection_state=ConnectionState.CONNECTED,
        presence_state=PresenceState.DISCOVERED,
    )
    pm.save_paired_device(mock_dev)
    cm.sig_device_connected.emit("phone-456", "wifi")
    qapp.processEvents()

    assert "Connected: OnePlus Nord CE 5" in win._top_status_text.text()
    assert "🟢" in win._top_status_dot.text()

    # Device disconnects symmetrically
    pm.update_connection_state("phone-456", ConnectionState.DISCONNECTED)
    cm.sig_device_disconnected.emit("phone-456")
    qapp.processEvents()

    assert "Disconnected (Scan QR to Connect)" in win._top_status_text.text()
    assert "⚪" in win._top_status_dot.text()

    win.close()


def test_home_screen_device_card_sync(qapp):
    temp_dir = Path(tempfile.mkdtemp())
    pm = PairingManager(storage_dir=temp_dir)
    cm = ConnectionManager(pairing_manager=pm)

    try:
        from src.models import (
            DeviceIdentity, DeviceEndpoint, PairedDevice, ConnectionState, PresenceState, TrustStatus
        )
    except ImportError:
        from models import (
            DeviceIdentity, DeviceEndpoint, PairedDevice, ConnectionState, PresenceState, TrustStatus
        )
    from PyQt6.QtWidgets import QLabel

    home = HomeScreen(connection_manager=cm)

    mock_dev = PairedDevice(
        identity=DeviceIdentity(
            device_id="phone-789",
            name="OnePlus Nord CE 5",
            public_key="pk789",
            created_at=1000,
            last_seen=1000,
            trust_status=TrustStatus.TRUSTED,
            capabilities=[],
        ),
        endpoint=DeviceEndpoint(addrs=["192.168.1.70"], port=47470, transports=["wifi"], cert_fp="", updated_at=1000),
        connection_state=ConnectionState.CONNECTED,
        presence_state=PresenceState.DISCOVERED,
    )
    pm.save_paired_device(mock_dev)
    home.refresh_devices()

    # Check session status reflects connection
    assert "Connected to OnePlus Nord CE 5" in home._lbl_session_status.text()
    assert "🟢" in home._lbl_session_status.text()

    # Check device card badges reflect "● Online / Connected"
    labels = [lbl.text() for lbl in home.findChildren(QLabel)]
    assert any("● Online / Connected" in l for l in labels)

    # Disconnect symmetrically
    pm.update_connection_state("phone-789", ConnectionState.DISCONNECTED)
    cm.sig_device_disconnected.emit("phone-789")
    qapp.processEvents()

    assert "Disconnected (Scan QR to Connect)" in home._lbl_session_status.text()
    assert "⚪" in home._lbl_session_status.text()

    labels = [lbl.text() for lbl in home.findChildren(QLabel)]
    assert any("Offline / Disconnected" in l for l in labels)
    home.close()
