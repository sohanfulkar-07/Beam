"""
PhotoBeam Windows UI — Main Window
"""
from __future__ import annotations

import sys
import os

from PyQt6.QtWidgets import (
    QMainWindow, QStackedWidget, QWidget, QVBoxLayout,
    QHBoxLayout, QPushButton, QLabel, QFrame, QApplication,
)
from PyQt6.QtCore import Qt, QSize, pyqtSignal
from PyQt6.QtGui import QIcon, QFont, QPalette, QColor

from .home_screen import HomeScreen
from .receive_screen import ReceiveScreen
from .send_screen import SendScreen
from .history_screen import HistoryScreen
from .styles import STYLESHEET


class MainWindow(QMainWindow):
    """PhotoBeam main application window."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("PhotoBeam")
        self.setMinimumSize(800, 600)
        self.resize(900, 680)

        self.setStyleSheet(STYLESHEET)

        # Central stacked widget for screen navigation
        self._stack = QStackedWidget()
        self.setCentralWidget(self._stack)

        # Screens
        self._home = HomeScreen()
        self._receive = ReceiveScreen()
        self._send = SendScreen()
        self._history = HistoryScreen()

        self._stack.addWidget(self._home)     # index 0
        self._stack.addWidget(self._receive)  # index 1
        self._stack.addWidget(self._send)     # index 2
        self._stack.addWidget(self._history)  # index 3

        # Navigation signals
        self._home.go_receive.connect(self._show_receive)
        self._home.go_send.connect(self._show_send)
        self._home.go_history.connect(self._show_history)
        self._receive.go_back.connect(self._show_home)
        self._receive.go_history.connect(self._show_history)
        self._send.go_back.connect(self._show_home)
        self._send.go_history.connect(self._show_history)
        self._history.go_back.connect(self._show_home)

        self._show_home()

    def _show_home(self):
        self._stack.setCurrentIndex(0)

    def _show_receive(self):
        self._stack.setCurrentIndex(1)
        self._receive.on_shown()

    def _show_send(self):
        self._stack.setCurrentIndex(2)

    def _show_history(self):
        self._stack.setCurrentIndex(3)
        self._history.on_shown()
