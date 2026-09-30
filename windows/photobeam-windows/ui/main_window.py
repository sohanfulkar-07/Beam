"""
PhotoBeam Windows UI — Main Window
"""
from __future__ import annotations

import os
import sys

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QIcon, QPalette
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)
try:
    from connection_manager import ConnectionManager
except (ImportError, ValueError):
    from ..connection_manager import ConnectionManager
from .history_screen import HistoryScreen
from .home_screen import HomeScreen
from .receive_screen import ReceiveScreen
from .screen_viewer import ScreenViewer
from .send_screen import SendScreen
from .styles import STYLESHEET


class MainWindow(QMainWindow):
    """PhotoBeam main application window."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("PhotoBeam")
        self.setMinimumSize(850, 620)
        self.resize(960, 700)

        self.setStyleSheet(STYLESHEET)
        self.connection_manager = ConnectionManager.get_instance()
        self.connection_manager.start()

        # Central stacked widget for screen navigation
        self._stack = QStackedWidget()
        self.setCentralWidget(self._stack)

        # Screens
        self._home = HomeScreen(self.connection_manager)
        self._receive = ReceiveScreen()
        self._send = SendScreen()
        self._history = HistoryScreen()
        self._viewer: ScreenViewer | None = None

        self._stack.addWidget(self._home)     # index 0
        self._stack.addWidget(self._receive)  # index 1
        self._stack.addWidget(self._send)     # index 2
        self._stack.addWidget(self._history)  # index 3

        # Navigation signals
        self._home.go_receive.connect(self._show_receive)
        self._home.go_send.connect(self._show_send)
        self._home.go_history.connect(self._show_history)
        self._home.go_mirror.connect(self._show_mirror)
        self._home.go_send_to_device.connect(self._show_send_to_device)

        self._receive.go_back.connect(self._show_home)
        self._receive.go_history.connect(self._show_history)
        self._send.go_back.connect(self._show_home)
        self._send.go_history.connect(self._show_history)
        self._history.go_back.connect(self._show_home)

        self._show_home()

    def _show_home(self):
        self._stack.setCurrentIndex(0)
        self._home.refresh_devices()

    def _show_receive(self):
        self._stack.setCurrentIndex(1)
        self._receive.on_shown()

    def _show_send(self):
        self._stack.setCurrentIndex(2)

    def _show_history(self):
        self._stack.setCurrentIndex(3)
        if hasattr(self._history, "refresh"):
            self._history.refresh()


    def _show_send_to_device(self, device_id: str):
        self._stack.setCurrentIndex(2)
        dev = self.connection_manager.pairing_manager.get_paired_device(device_id)
        if dev and dev.endpoint and dev.endpoint.addrs:
            # Pre-fill target endpoint if known
            pass

    def _show_mirror(self, device_id: str):
        dev = self.connection_manager.pairing_manager.get_paired_device(device_id)
        dev_name = dev.identity.name if dev else "Android Device"

        if self._viewer:
            self._viewer.stop()
            self._stack.removeWidget(self._viewer)
            self._viewer.deleteLater()

        self._viewer = ScreenViewer(device_id=device_id, device_name=dev_name)
        self._viewer.go_back.connect(self._show_home)
        self._viewer.files_dropped.connect(self._on_viewer_files_dropped)
        self._stack.addWidget(self._viewer)
        self._stack.setCurrentWidget(self._viewer)
        self._viewer.start_receiver()

    def _on_viewer_files_dropped(self, device_id: str, file_paths: list):
        self._show_send_to_device(device_id)
        # Pre-select dropped files in send screen
        if hasattr(self._send, "set_selected_files"):
            self._send.set_selected_files(file_paths)

    def closeEvent(self, event):
        if self._viewer:
            self._viewer.stop()
        self.connection_manager.stop()
        super().closeEvent(event)
