"""
Unit tests for ReceiveScreen UI state transitions, badges, and speed formatting.
"""
import sys
import os
from pathlib import Path
import pytest
from PyQt6.QtWidgets import QApplication

# Ensure paths
ROOT = Path(__file__).resolve().parent.parent.parent
WIN_DIR = ROOT / "windows" / "photobeam-windows"
PROTO_DIR = ROOT / "protocol"

for p in (str(WIN_DIR), str(PROTO_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

from ui.receive_screen import ReceiveScreen, ReceiverWorker
from src.session import SessionManager


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(["--platform", "offscreen"])
    return app


def test_receive_screen_state_transitions(qapp, tmp_path):
    screen = ReceiveScreen()
    screen._dest_dir = tmp_path
    screen.show()

    # 1. Ready state on shown / reset
    screen._reset_to_start()
    assert "READY" in screen._conn_badge.text()
    assert screen._qr_container.isVisible()
    assert not screen._progress_container.isVisible()
    assert not screen._complete_container.isVisible()
    assert not screen._error_container.isVisible()

    # 2. QR ready
    screen._on_qr_ready(b"fake_png", "photobeam://test")
    assert "READY" in screen._conn_badge.text()

    # 3. Connecting status
    screen._on_status("Connecting to sender (192.168.1.10)…")
    assert "CONNECTING" in screen._conn_badge.text()

    # 4. Connected
    screen._on_connected("192.168.1.10")
    assert "CONNECTED" in screen._conn_badge.text()
    assert "192.168.1.10" in screen._conn_badge.text()

    # 5. Incoming prompt & accept
    screen._on_incoming_prompt(2, 1024 * 1024 * 10)
    assert screen._prompt_container.isVisible()
    assert not screen._qr_container.isVisible()
    screen._on_accept()
    assert screen._progress_container.isVisible()
    assert not screen._prompt_container.isVisible()

    # 6. Progress update -> TRANSFERRING
    screen._on_progress_update("file1.bin", 1, 2, 1024 * 512, 1024 * 1024, 1024 * 512, 1024 * 1024 * 10)
    assert "TRANSFERRING" in screen._conn_badge.text()
    assert "file1.bin" in screen._prog_file_name.text()
    assert "File 1 of 2" in screen._counter_label.text()

    # 7. Complete -> COMPLETED
    screen._on_complete(2, 1024 * 1024 * 10, 5.0)
    assert "COMPLETED" in screen._conn_badge.text()
    assert screen._complete_container.isVisible()
    assert not screen._progress_container.isVisible()

    # 8. Error -> CONNECTION ERROR
    screen._on_error("Connection lost: peer reset")
    assert "CONNECTION ERROR" in screen._conn_badge.text()
    assert screen._error_container.isVisible()
    assert not screen._complete_container.isVisible()

    screen._stop_worker()


def test_receiver_worker_speed_calculation(tmp_path):
    mgr = SessionManager()
    worker = ReceiverWorker(tmp_path, mgr)
    speeds = worker.get_transport_speeds()
    assert speeds == {"wifi": 0.0, "usb": 0.0, "total": 0.0}


def test_genuine_connection_failure_displays_error(qapp, tmp_path):
    screen = ReceiveScreen()
    screen._dest_dir = tmp_path
    screen.show()

    # Simulate genuine network drop
    screen._on_error("Connection lost during transfer: broken pipe")
    assert "CONNECTION ERROR" in screen._conn_badge.text()
    assert screen._error_container.isVisible()
    assert "Connection" in screen._err_title.text()
    screen._stop_worker()

