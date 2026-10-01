"""
PhotoBeam Windows UI — Main Window (Neo-Tactile Desktop Dashboard)

Implements Reference 2 Dashboard architecture:
- Left sidebar with branding, navigation items, and local identity card
- Top bar with dynamic breadcrumbs, live device presence badge, and quick pair action
- Central workspace with smooth switching between Dashboard, Send, Receive, Activity, and Mirror
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QIcon, QPalette
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpacerItem,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

if getattr(sys, "frozen", False):
    _proto = os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(sys.executable)), "protocol")
else:
    _proto = str(Path(__file__).resolve().parent.parent.parent / "protocol")
if _proto not in sys.path:
    sys.path.append(_proto)

try:
    from src.models import ConnectionState, PresenceState
except ImportError:
    from models import ConnectionState, PresenceState

try:
    from connection_manager import ConnectionManager
except (ImportError, ValueError):
    from ..connection_manager import ConnectionManager

try:
    from .history_screen import HistoryScreen
    from .home_screen import HomeScreen
    from .pairing_dialog import PairingDialog
    from .receive_screen import ReceiveScreen
    from .screen_viewer import ScreenViewer
    from .send_screen import SendScreen
    from .styles import STYLESHEET
except (ImportError, ValueError):
    from history_screen import HistoryScreen
    from home_screen import HomeScreen
    from pairing_dialog import PairingDialog
    from receive_screen import ReceiveScreen
    from screen_viewer import ScreenViewer
    from send_screen import SendScreen
    from styles import STYLESHEET



class MainWindow(QMainWindow):
    """PhotoBeam modern desktop dashboard main window."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("PhotoBeam — Peer-to-Peer Transfer")
        self.setMinimumSize(960, 640)
        self.resize(1120, 750)

        self.setStyleSheet(STYLESHEET)
        self.connection_manager = ConnectionManager.get_instance()
        self.connection_manager.start()
        self.connection_manager.add_device_updated_callback(self._on_device_updated_bg)

        # Main Layout: Left Sidebar + Right Content Workspace
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        self._root_layout = QHBoxLayout(central_widget)
        self._root_layout.setContentsMargins(0, 0, 0, 0)
        self._root_layout.setSpacing(0)

        # ── Left Navigation Sidebar ──────────────────────────────────────────
        self._sidebar = QFrame()
        self._sidebar.setObjectName("sidebar")
        self._sidebar_layout = QVBoxLayout(self._sidebar)
        self._sidebar_layout.setContentsMargins(18, 24, 18, 20)
        self._sidebar_layout.setSpacing(12)

        # Brand header
        brand_row = QHBoxLayout()
        brand_row.setSpacing(12)
        brand_icon = QLabel("⚡")
        brand_icon.setStyleSheet("font-size: 26px; color: #38BDF8;")
        brand_text_box = QVBoxLayout()
        brand_text_box.setSpacing(1)
        brand_name = QLabel("PhotoBeam")
        brand_name.setObjectName("sidebar_logo_text")
        brand_sub = QLabel("PEER-TO-PEER")
        brand_sub.setObjectName("sidebar_logo_sub")
        brand_text_box.addWidget(brand_name)
        brand_text_box.addWidget(brand_sub)
        brand_row.addWidget(brand_icon)
        brand_row.addLayout(brand_text_box)
        brand_row.addStretch()
        self._sidebar_layout.addLayout(brand_row)
        self._sidebar_layout.addSpacing(16)

        # Navigation buttons
        self._nav_buttons: list[tuple[QPushButton, int, str]] = []

        self._btn_home = self._create_nav_button("🏠  Dashboard", 0, "Dashboard")
        self._btn_send = self._create_nav_button("📤  Send Files", 2, "Send Files")
        self._btn_receive = self._create_nav_button("📥  Receive Files", 1, "Receive Files")
        self._btn_history = self._create_nav_button("📜  Activity", 3, "Activity")

        self._sidebar_layout.addWidget(self._btn_home)
        self._sidebar_layout.addWidget(self._btn_send)
        self._sidebar_layout.addWidget(self._btn_receive)
        self._sidebar_layout.addWidget(self._btn_history)

        self._sidebar_layout.addStretch(1)

        # Sidebar Bottom: Local Machine Identity & Pair Action
        local_card = QFrame()
        local_card.setObjectName("sidebar_device_card")
        local_layout = QVBoxLayout(local_card)
        local_layout.setContentsMargins(12, 12, 12, 12)
        local_layout.setSpacing(6)

        local_id = self.connection_manager.pairing_manager.get_local_identity()
        self._sidebar_local_name = QLabel(f"💻 {local_id.name}")
        self._sidebar_local_name.setStyleSheet("font-weight: 700; color: #FFFFFF; font-size: 13px;")
        local_layout.addWidget(self._sidebar_local_name)

        self._sidebar_local_status = QLabel("● Ready (Discoverable)")
        self._sidebar_local_status.setStyleSheet("color: #10B981; font-size: 11px; font-weight: 600;")
        local_layout.addWidget(self._sidebar_local_status)

        pair_quick_btn = QPushButton("➕ Pair Device")
        pair_quick_btn.setObjectName("action_primary_sm")
        pair_quick_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        pair_quick_btn.clicked.connect(self._show_pairing_dialog)
        local_layout.addWidget(pair_quick_btn)

        self._sidebar_layout.addWidget(local_card)

        self._root_layout.addWidget(self._sidebar)

        # ── Right Main Area ──────────────────────────────────────────────────
        self._main_area = QWidget()
        self._main_layout = QVBoxLayout(self._main_area)
        self._main_layout.setContentsMargins(0, 0, 0, 0)
        self._main_layout.setSpacing(0)

        # Top Bar
        self._topbar = QFrame()
        self._topbar.setObjectName("topbar")
        self._topbar_layout = QHBoxLayout(self._topbar)
        self._topbar_layout.setContentsMargins(28, 14, 28, 14)
        self._topbar_layout.setSpacing(16)

        # Breadcrumbs
        breadcrumb_box = QHBoxLayout()
        breadcrumb_box.setSpacing(6)
        bc_root = QLabel("PhotoBeam  /")
        bc_root.setObjectName("breadcrumb")
        self._bc_active = QLabel("Dashboard")
        self._bc_active.setObjectName("breadcrumb_active")
        breadcrumb_box.addWidget(bc_root)
        breadcrumb_box.addWidget(self._bc_active)
        self._topbar_layout.addLayout(breadcrumb_box)

        self._topbar_layout.addStretch(1)

        # Live presence pill
        self._top_status_badge = QLabel("○ Standby")
        self._top_status_badge.setObjectName("badge_gray")
        self._topbar_layout.addWidget(self._top_status_badge)

        # Quick Pair button
        self._top_pair_btn = QPushButton("➕ Pair New Device")
        self._top_pair_btn.setObjectName("action_primary_sm")
        self._top_pair_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._top_pair_btn.clicked.connect(self._show_pairing_dialog)
        self._topbar_layout.addWidget(self._top_pair_btn)

        self._main_layout.addWidget(self._topbar)

        # Central Stacked Widget
        self._stack = QStackedWidget()
        self._main_layout.addWidget(self._stack, 1)

        self._root_layout.addWidget(self._main_area, 1)

        # ── Screens ──────────────────────────────────────────────────────────
        self._home = HomeScreen(self.connection_manager)
        self._receive = ReceiveScreen()
        self._send = SendScreen()
        self._history = HistoryScreen()
        self._viewer: ScreenViewer | None = None

        self._stack.addWidget(self._home)     # index 0
        self._stack.addWidget(self._receive)  # index 1
        self._stack.addWidget(self._send)     # index 2
        self._stack.addWidget(self._history)  # index 3

        # Connect Navigation Signals
        self._home.go_receive.connect(self._show_receive)
        self._home.go_send.connect(self._show_send)
        self._home.go_history.connect(self._show_history)
        self._home.go_mirror.connect(self._show_mirror)
        self._home.go_send_to_device.connect(self._show_send_to_device)

        if hasattr(self._home, "files_dropped"):
            self._home.files_dropped.connect(self._on_files_dropped)

        self._receive.go_back.connect(self._show_home)
        self._receive.go_history.connect(self._show_history)
        self._send.go_back.connect(self._show_home)
        self._send.go_history.connect(self._show_history)
        self._history.go_back.connect(self._show_home)

        self._show_home()
        self._update_top_status()

    def _create_nav_button(self, label: str, target_idx: int, breadcrumb: str) -> QPushButton:
        btn = QPushButton(label)
        btn.setObjectName("nav_btn")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setProperty("active", "false")
        btn.clicked.connect(lambda: self._navigate_to(target_idx, breadcrumb))
        self._nav_buttons.append((btn, target_idx, breadcrumb))
        return btn

    def _navigate_to(self, index: int, breadcrumb: str):
        if index == 0:
            self._show_home()
        elif index == 1:
            self._show_receive()
        elif index == 2:
            self._show_send()
        elif index == 3:
            self._show_history()

    def _set_active_nav(self, target_idx: int, breadcrumb: str):
        self._bc_active.setText(breadcrumb)
        for btn, idx, _ in self._nav_buttons:
            is_active = (idx == target_idx)
            btn.setProperty("active", "true" if is_active else "false")
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    def _show_home(self):
        self._stack.setCurrentIndex(0)
        self._set_active_nav(0, "Dashboard")
        self._home.refresh_devices()
        self._update_top_status()

    def _show_receive(self):
        self._stack.setCurrentIndex(1)
        self._set_active_nav(1, "Receive Files")
        self._receive.on_shown()

    def _show_send(self):
        self._stack.setCurrentIndex(2)
        self._set_active_nav(2, "Send Files")

    def _show_history(self):
        self._stack.setCurrentIndex(3)
        self._set_active_nav(3, "Activity")
        if hasattr(self._history, "refresh"):
            self._history.refresh()
        elif hasattr(self._history, "on_shown"):
            self._history.on_shown()

    def _show_send_to_device(self, device_id: str):
        self._show_send()
        dev = self.connection_manager.pairing_manager.get_paired_device(device_id)
        if dev and dev.endpoint and dev.endpoint.addrs:
            pass

    def _on_files_dropped(self, files: list):
        self._show_send()
        if hasattr(self._send, "set_selected_files"):
            self._send.set_selected_files(files)

    def _show_pairing_dialog(self):
        dlg = PairingDialog(self.connection_manager, self)
        dlg.paired_success.connect(lambda dev: self._on_paired_success())
        dlg.exec()

    def _on_paired_success(self):
        self._show_home()
        self._home.refresh_devices()
        self._update_top_status()

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
        self._set_active_nav(-1, f"Mirror: {dev_name}")
        self._viewer.start_receiver()

    def _on_viewer_files_dropped(self, device_id: str, file_paths: list):
        self._show_send_to_device(device_id)
        if hasattr(self._send, "set_selected_files"):
            self._send.set_selected_files(file_paths)

    def _update_top_status(self):
        devices = self.connection_manager.pairing_manager.get_paired_devices()
        connected = [d for d in devices if d.connection_state == ConnectionState.CONNECTED]
        discovered = [d for d in devices if d.presence_state == PresenceState.DISCOVERED]

        if connected:
            self._top_status_badge.setText(f"🟢 Connected: {connected[0].identity.name}")
            self._top_status_badge.setObjectName("badge_green")
        elif discovered:
            self._top_status_badge.setText(f"🟡 {discovered[0].identity.name} (Online)")
            self._top_status_badge.setObjectName("badge_cyan")
        elif devices:
            self._top_status_badge.setText(f"⚪ {len(devices)} Paired Device(s)")
            self._top_status_badge.setObjectName("badge_gray")
        else:
            self._top_status_badge.setText("○ Standby (Discoverable)")
            self._top_status_badge.setObjectName("badge_gray")

        self._top_status_badge.style().unpolish(self._top_status_badge)
        self._top_status_badge.style().polish(self._top_status_badge)

    def _on_device_updated_bg(self, dev):
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(0, self._update_top_status)

    def closeEvent(self, event):
        if self._viewer:
            self._viewer.stop()
        self.connection_manager.stop()
        super().closeEvent(event)
