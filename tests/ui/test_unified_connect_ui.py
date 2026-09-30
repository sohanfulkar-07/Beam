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
