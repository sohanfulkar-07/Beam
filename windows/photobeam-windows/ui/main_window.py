"""
PhotoBeam Windows UI — Main Window (Reference 2 Dashboard Architecture)

Implements Reference 2 Desktop Dashboard:
- Fixed 220px left sidebar with vector icons, vertical alignment, and local machine identity
- Sleek 60px top bar with breadcrumbs, dynamic connection status pill, and primary Pair action
- Seamless workspace stack coordinating Dashboard, Send, Receive, History, and Screen Mirror
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QSize, Qt, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QColor, QFont, QIcon
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QSizePolicy,
    QSpacerItem,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

if getattr(sys, "frozen", False):
    _proto = os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(sys.executable)), "protocol")
else:
    _p = Path(__file__).resolve()
    _proto = str(_p.parent.parent.parent.parent / "protocol")
    if not os.path.exists(_proto):
        _proto = str(_p.parent.parent.parent / "protocol")
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
    from .icons import get_icon, get_pixmap
    from .pairing_dialog import PairingDialog
    from .receive_screen import ReceiveScreen
    from .screen_viewer import ScreenViewer
    from .send_screen import SendScreen
    from .styles import STYLESHEET
except (ImportError, ValueError):
    from history_screen import HistoryScreen
    from home_screen import HomeScreen
    from icons import get_icon, get_pixmap
    from pairing_dialog import PairingDialog
    from receive_screen import ReceiveScreen
    from screen_viewer import ScreenViewer
    from send_screen import SendScreen
    from styles import STYLESHEET


class MainWindow(QMainWindow):
    """PhotoBeam modern desktop dashboard main window."""

    # Emitted from background thread → handled on main Qt thread to open ScreenViewer
    mirror_stream_received = pyqtSignal(str, object)  # (device_id, client_socket)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("PhotoBeam")
        self.setMinimumSize(1000, 680)
        self.resize(1366, 768)

        self.setStyleSheet(STYLESHEET)
        self.connection_manager = ConnectionManager.get_instance()
        self.connection_manager.start()
        self.connection_manager.add_device_updated_callback(self._on_device_updated_bg)

        # Connect to ConnectionManager Qt signals for thread-safe UI updates
        if hasattr(self.connection_manager, "sig_device_connected") and self.connection_manager.sig_device_connected:
            self.connection_manager.sig_device_connected.connect(self._on_sig_device_connected)
        if hasattr(self.connection_manager, "sig_device_disconnected") and self.connection_manager.sig_device_disconnected:
            self.connection_manager.sig_device_disconnected.connect(self._on_sig_device_disconnected)
        if hasattr(self.connection_manager, "sig_device_updated") and self.connection_manager.sig_device_updated:
            self.connection_manager.sig_device_updated.connect(self._on_sig_device_updated)
        if hasattr(self.connection_manager, "sig_connection_state_changed") and self.connection_manager.sig_connection_state_changed:
            self.connection_manager.sig_connection_state_changed.connect(self._on_sig_connection_state_changed)

        # Register mirror stream callback — fires when Android initiates mirroring
        self.connection_manager.set_mirror_stream_callback(self._on_mirror_stream_bg)
        self.mirror_stream_received.connect(self._on_mirror_stream_main)

        # Central container
        central = QWidget()
        self.setCentralWidget(central)
        root_layout = QHBoxLayout(central)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # ── 1. Left Sidebar (Fixed 220px) ─────────────────────────────────────
        self._sidebar = QFrame()
        self._sidebar.setObjectName("sidebar")
        s_layout = QVBoxLayout(self._sidebar)
        s_layout.setContentsMargins(16, 20, 16, 18)
        s_layout.setSpacing(8)

        # Brand header
        brand_row = QHBoxLayout()
        brand_row.setSpacing(10)
        brand_icon_lbl = QLabel()
        brand_icon_lbl.setPixmap(get_pixmap("logo", "#38BDF8", 22))
        brand_row.addWidget(brand_icon_lbl)

        brand_text_box = QVBoxLayout()
        brand_text_box.setSpacing(0)
        brand_name = QLabel("PhotoBeam")
        brand_name.setObjectName("sidebar_brand_title")
        brand_sub = QLabel("PEER-TO-PEER")
        brand_sub.setObjectName("sidebar_brand_sub")
        brand_text_box.addWidget(brand_name)
        brand_text_box.addWidget(brand_sub)
        brand_row.addLayout(brand_text_box)
        brand_row.addStretch()
        s_layout.addLayout(brand_row)

        s_layout.addSpacing(20)

        # Navigation menu
        self._nav_buttons: list[tuple[QPushButton, int, str]] = []

        self._btn_home = self._create_nav_item("Dashboard", "dashboard", 0)
        self._btn_send = self._create_nav_item("Send Files", "send", 2)
        self._btn_receive = self._create_nav_item("Receive Files", "receive", 1)
        self._btn_history = self._create_nav_item("Activity", "activity", 3)

        s_layout.addWidget(self._btn_home)
        s_layout.addWidget(self._btn_send)
        s_layout.addWidget(self._btn_receive)
        s_layout.addWidget(self._btn_history)

        s_layout.addStretch(1)

        # Sidebar Bottom Section: Settings & Local Machine Identity
        self._btn_settings = QPushButton(" Settings")
        self._btn_settings.setIcon(get_icon("settings", "#94A3B8", "#F8FAFC", 16))
        self._btn_settings.setObjectName("nav_btn")
        self._btn_settings.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_settings.clicked.connect(self._on_settings_clicked)
        s_layout.addWidget(self._btn_settings)

        s_layout.addSpacing(6)

        # Local Device Identity Card
        local_card = QFrame()
        local_card.setObjectName("sidebar_identity_card")
        lc_layout = QVBoxLayout(local_card)
        lc_layout.setContentsMargins(10, 10, 10, 10)
        lc_layout.setSpacing(4)

        lc_top = QHBoxLayout()
        lc_top.setSpacing(8)
        laptop_icon = QLabel()
        laptop_icon.setPixmap(get_pixmap("device_laptop", "#38BDF8", 16))
        local_id = self.connection_manager.pairing_manager.get_local_identity()
        self._lbl_local_name = QLabel(local_id.name)
        self._lbl_local_name.setStyleSheet("font-weight: 700; color: #F8FAFC; font-size: 12px;")
        lc_top.addWidget(laptop_icon)
        lc_top.addWidget(self._lbl_local_name)
        lc_top.addStretch()
        lc_layout.addLayout(lc_top)

        self._lbl_local_status = QLabel("● Ready (Discoverable)")
        self._lbl_local_status.setStyleSheet("color: #10B981; font-size: 11px; font-weight: 600; padding-left: 24px;")
        lc_layout.addWidget(self._lbl_local_status)

        s_layout.addWidget(local_card)

        root_layout.addWidget(self._sidebar)

        # ── 2. Right Workspace Area ──────────────────────────────────────────
        main_workspace = QWidget()
        mw_layout = QVBoxLayout(main_workspace)
        mw_layout.setContentsMargins(0, 0, 0, 0)
        mw_layout.setSpacing(0)

        # Top Bar (Fixed 60px height)
        self._topbar = QFrame()
        self._topbar.setObjectName("topbar")
        tb_layout = QHBoxLayout(self._topbar)
        tb_layout.setContentsMargins(32, 0, 32, 0)
        tb_layout.setSpacing(16)

        # Breadcrumbs
        bc_layout = QHBoxLayout()
        bc_layout.setSpacing(6)
        bc_icon = QLabel()
        bc_icon.setPixmap(get_pixmap("logo", "#64748B", 14))
        bc_root = QLabel("PhotoBeam  /")
        bc_root.setObjectName("breadcrumb_root")
        self._bc_active = QLabel("Dashboard")
        self._bc_active.setObjectName("breadcrumb_active")
        bc_layout.addWidget(bc_icon)
        bc_layout.addWidget(bc_root)
        bc_layout.addWidget(self._bc_active)
        tb_layout.addLayout(bc_layout)

        tb_layout.addStretch(1)

        # Connected Device Pill
        self._top_device_pill = QFrame()
        self._top_device_pill.setObjectName("top_device_pill")
        self._top_device_pill.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        tdp_layout = QHBoxLayout(self._top_device_pill)
        tdp_layout.setContentsMargins(12, 6, 12, 6)
        tdp_layout.setSpacing(8)
        self._top_status_dot = QLabel("●")
        self._top_status_dot.setStyleSheet("color: #94A3B8; font-size: 11px;")
        self._top_status_text = QLabel("Standby (Discoverable)")
        self._top_status_text.setStyleSheet("color: #E2E8F0; font-size: 12px; font-weight: 600;")
        tdp_layout.addWidget(self._top_status_dot)
        tdp_layout.addWidget(self._top_status_text)
        tb_layout.addWidget(self._top_device_pill)

        # Top Bar Connect / Disconnect Toggle Button
        self._top_conn_toggle_btn = QPushButton(" Connect")
        self._top_conn_toggle_btn.setObjectName("action_sm")
        self._top_conn_toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._top_conn_toggle_btn.clicked.connect(self._on_top_conn_toggle_clicked)
        tb_layout.addWidget(self._top_conn_toggle_btn)

        # Refresh action button
        refresh_action_btn = QPushButton()
        refresh_action_btn.setObjectName("action_icon_only")
        refresh_action_btn.setIcon(get_icon("refresh", "#94A3B8", "#38BDF8", 16))
        refresh_action_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        refresh_action_btn.setToolTip("Refresh connection and device presence")
        refresh_action_btn.clicked.connect(self._refresh_current_view)
        tb_layout.addWidget(refresh_action_btn)

        # Primary Pair Button
        self._top_pair_btn = QPushButton(" Pair New Device")
        self._top_pair_btn.setIcon(get_icon("plus", "#FFFFFF", "#FFFFFF", 14))
        self._top_pair_btn.setObjectName("primary")
        self._top_pair_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._top_pair_btn.clicked.connect(self._show_pairing_dialog)
        tb_layout.addWidget(self._top_pair_btn)

        mw_layout.addWidget(self._topbar)

        # ── 3. Central Stacked Screens ───────────────────────────────────────
        self._stack = QStackedWidget()
        mw_layout.addWidget(self._stack, 1)

        root_layout.addWidget(main_workspace, 1)

        # Screen instances
        self._home = HomeScreen(self.connection_manager)
        self._receive = ReceiveScreen()
        self._send = SendScreen(self.connection_manager)
        self._history = HistoryScreen()
        self._viewer: ScreenViewer | None = None

        self._stack.addWidget(self._home)     # 0: Dashboard
        self._stack.addWidget(self._receive)  # 1: Receive
        self._stack.addWidget(self._send)     # 2: Send
        self._stack.addWidget(self._history)  # 3: Activity

        # Navigation connections
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

    def _on_mirror_stream_bg(self, device_id: str, client_sock) -> None:
        """Called from ConnectionManager background thread when Android starts mirroring.
        We emit a Qt signal to open the viewer on the main thread."""
        self.mirror_stream_received.emit(device_id, client_sock)

    def _on_mirror_stream_main(self, device_id: str, client_sock) -> None:
        """Opens ScreenViewer with a pre-connected socket from Android (main thread)."""
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
        # Start from already-connected socket (don't start a new listener)
        self._viewer.start_with_socket(client_sock)
        # Bring window to front when mirroring starts
        self.raise_()
        self.activateWindow()

    def _create_nav_item(self, label: str, icon_name: str, target_idx: int) -> QPushButton:
        btn = QPushButton(f" {label}")
        btn.setIcon(get_icon(icon_name, "#94A3B8", "#38BDF8", 16))
        btn.setObjectName("nav_btn")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setProperty("active", "false")
        btn.clicked.connect(lambda: self._navigate_to(target_idx, label))
        self._nav_buttons.append((btn, target_idx, label))
        return btn

    def _navigate_to(self, index: int, label: str):
        if index == 0:
            self._show_home()
        elif index == 1:
            self._show_receive()
        elif index == 2:
            self._show_send()
        elif index == 3:
            self._show_history()

    def _set_active_nav(self, target_idx: int, label: str):
        self._bc_active.setText(label)
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
        if hasattr(self._send, "refresh_connection_status"):
            self._send.refresh_connection_status()

    def _show_history(self):
        self._stack.setCurrentIndex(3)
        self._set_active_nav(3, "Activity")
        if hasattr(self._history, "refresh"):
            self._history.refresh()
        elif hasattr(self._history, "on_shown"):
            self._history.on_shown()

    def _show_send_to_device(self, device_id: str):
        self._show_send()
        if hasattr(self._send, "set_target_device"):
            self._send.set_target_device(device_id)

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

    def _refresh_current_view(self):
        idx = self._stack.currentIndex()
        if idx == 0:
            self._home.refresh_devices()
        elif idx == 3:
            if hasattr(self._history, "refresh"):
                self._history.refresh()
            elif hasattr(self._history, "on_shown"):
                self._history.on_shown()
        self._update_top_status()

    def _on_settings_clicked(self):
        self._show_pairing_dialog()

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

    @pyqtSlot(str, str)
    def _on_sig_device_connected(self, device_id: str, transport_type: str):
        self._sync_all_views()

    @pyqtSlot(str)
    def _on_sig_device_disconnected(self, device_id: str):
        self._sync_all_views()

    @pyqtSlot(object)
    def _on_sig_device_updated(self, dev):
        self._sync_all_views()

    @pyqtSlot(str, object)
    def _on_sig_connection_state_changed(self, device_id: str, state):
        self._sync_all_views()

    @pyqtSlot()
    def _sync_all_views(self):
        self._update_top_status()
        self._home.refresh_devices()
        if hasattr(self, "_send") and hasattr(self._send, "refresh_connection_status"):
            self._send.refresh_connection_status()

    def _on_top_conn_toggle_clicked(self):
        devices = self.connection_manager.pairing_manager.get_paired_devices()
        connected = [d for d in devices if d.connection_state == ConnectionState.CONNECTED]
        if connected:
            self.connection_manager.disconnect_device(connected[0].identity.device_id)
        elif devices:
            self.connection_manager.connect_device(devices[0].identity.device_id)

    def _update_top_status(self):
        devices = self.connection_manager.pairing_manager.get_paired_devices()
        connected = [d for d in devices if d.connection_state == ConnectionState.CONNECTED]
        connecting = [d for d in devices if d.connection_state == ConnectionState.CONNECTING]

        if connected:
            dev = connected[0]
            transports = self.connection_manager.get_active_transports(dev.identity.device_id)
            trans_str = transports[0].upper() if transports else "WI-FI"
            self._top_status_dot.setText("🟢")
            self._top_status_dot.setStyleSheet("color: #10B981; font-size: 11px;")
            self._top_status_text.setText(f"Connected: {dev.identity.name} ({trans_str})")
            if hasattr(self, "_top_conn_toggle_btn"):
                self._top_conn_toggle_btn.setVisible(True)
                self._top_conn_toggle_btn.setEnabled(True)
                self._top_conn_toggle_btn.setText(" Disconnect")
        elif connecting:
            dev = connecting[0]
            self._top_status_dot.setText("🟡")
            self._top_status_dot.setStyleSheet("color: #F59E0B; font-size: 11px;")
            self._top_status_text.setText(f"Connecting to {dev.identity.name}…")
            if hasattr(self, "_top_conn_toggle_btn"):
                self._top_conn_toggle_btn.setVisible(True)
                self._top_conn_toggle_btn.setEnabled(False)
                self._top_conn_toggle_btn.setText(" Connecting…")
        else:
            self._top_status_dot.setText("⚪")
            self._top_status_dot.setStyleSheet("color: #94A3B8; font-size: 11px;")
            self._top_status_text.setText("Disconnected (Scan QR to Connect)")
            if hasattr(self, "_top_conn_toggle_btn"):
                if devices:
                    self._top_conn_toggle_btn.setVisible(True)
                    self._top_conn_toggle_btn.setEnabled(True)
                    self._top_conn_toggle_btn.setText(" Connect")
                else:
                    self._top_conn_toggle_btn.setVisible(False)

        self._top_status_text.adjustSize()
        req_width = max(240, self._top_status_text.sizeHint().width() + 55)
        self._top_device_pill.setMinimumWidth(req_width)
        self._top_device_pill.adjustSize()

    def _on_device_updated_bg(self, dev):
        from PyQt6.QtCore import QMetaObject, Qt
        QMetaObject.invokeMethod(self, "_sync_all_views", Qt.ConnectionType.QueuedConnection)

    def closeEvent(self, event):
        self.connection_manager.remove_device_updated_callback(self._on_device_updated_bg)
        for sig, slot in [
            (getattr(self.connection_manager, "sig_device_connected", None), self._on_sig_device_connected),
            (getattr(self.connection_manager, "sig_device_disconnected", None), self._on_sig_device_disconnected),
            (getattr(self.connection_manager, "sig_device_updated", None), self._on_sig_device_updated),
            (getattr(self.connection_manager, "sig_connection_state_changed", None), self._on_sig_connection_state_changed),
        ]:
            if sig:
                try:
                    sig.disconnect(slot)
                except (TypeError, RuntimeError):
                    pass
        if self._viewer:
            self._viewer.stop()
        self.connection_manager.stop()
        super().closeEvent(event)
