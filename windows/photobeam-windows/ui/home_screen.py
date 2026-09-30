"""
PhotoBeam Windows UI — Unified Connect Home Screen

Replaces separate Send/Receive entry points with a unified Connect hub:
- Prominent Pair / Connect action
- Live Paired Devices list with real-time presence & connection states
- Per-device actions: Send Files, Screen Mirror, Device Management (Rename, Forget, Revoke)
- Fast fallback for ad-hoc Send / Receive
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpacerItem,
    QVBoxLayout,
    QWidget,
)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "protocol" / "src"))
from models import ConnectionState, PairedDevice, PresenceState, TrustStatus

try:
    from ..connection_manager import ConnectionManager
    from .pairing_dialog import PairingDialog
except (ImportError, ValueError):
    from connection_manager import ConnectionManager
    from pairing_dialog import PairingDialog


class HomeScreen(QWidget):
    go_receive = pyqtSignal()
    go_send = pyqtSignal()
    go_history = pyqtSignal()
    go_mirror = pyqtSignal(str)          # device_id
    go_send_to_device = pyqtSignal(str)   # device_id

    def __init__(self, connection_manager: Optional[ConnectionManager] = None):
        super().__init__()
        self.connection_manager = connection_manager or ConnectionManager.get_instance()
        self.pairing_manager = self.connection_manager.pairing_manager

        self.connection_manager.add_device_updated_callback(self._on_device_updated_from_bg)
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(40, 32, 40, 32)
        root.setSpacing(20)

        # ── Header bar ────────────────────────────────────────────────────────
        header = QHBoxLayout()
        header.setSpacing(16)

        logo_title = QHBoxLayout()
        icon = QLabel("⚡")
        icon.setStyleSheet("font-size: 28px;")
        title = QLabel("PhotoBeam")
        title.setObjectName("heading")
        logo_title.addWidget(icon)
        logo_title.addWidget(title)
        header.addLayout(logo_title)

        header.addStretch(1)

        # Local device status badge
        local_id = self.pairing_manager.get_local_identity()
        self.local_badge = QLabel(f"💻 {local_id.name} (Discoverable)")
        self.local_badge.setObjectName("badge_green")
        header.addWidget(self.local_badge)

        # Pair New Device Button
        pair_btn = QPushButton("➕ Pair New Device")
        pair_btn.setObjectName("action_primary_sm")
        pair_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        pair_btn.clicked.connect(self._show_pairing_dialog)
        header.addWidget(pair_btn)

        # History Button
        hist_btn = QPushButton("📜 History")
        hist_btn.setObjectName("action_sm")
        hist_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        hist_btn.clicked.connect(self.go_history.emit)
        header.addWidget(hist_btn)

        root.addLayout(header)

        # ── Section Title: Paired Devices ──────────────────────────────────────
        section_hdr = QHBoxLayout()
        sec_title = QLabel("Trusted Devices")
        sec_title.setStyleSheet("font-size: 16px; font-weight: 600; color: #cbd5e1;")
        section_hdr.addWidget(sec_title)
        section_hdr.addStretch(1)

        refresh_btn = QPushButton("🔄 Refresh")
        refresh_btn.setObjectName("details_btn")
        refresh_btn.clicked.connect(self.refresh_devices)
        section_hdr.addWidget(refresh_btn)
        root.addLayout(section_hdr)

        # ── Scrollable Paired Devices Area ─────────────────────────────────────
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.devices_container = QWidget()
        self.devices_layout = QVBoxLayout(self.devices_container)
        self.devices_layout.setContentsMargins(0, 0, 0, 0)
        self.devices_layout.setSpacing(12)
        self.devices_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        self.scroll_area.setWidget(self.devices_container)
        root.addWidget(self.scroll_area, 1)

        # ── Bottom Quick Action Bar ───────────────────────────────────────────
        bottom_bar = QFrame()
        bottom_bar.setObjectName("card")
        b_layout = QHBoxLayout(bottom_bar)
        b_layout.setContentsMargins(20, 14, 20, 14)
        b_layout.setSpacing(16)

        info_lbl = QLabel("Quick Actions (Ad-Hoc):")
        info_lbl.setObjectName("info")
        b_layout.addWidget(info_lbl)

        send_adhoc_btn = QPushButton("📤 Send Files...")
        send_adhoc_btn.setObjectName("action_sm")
        send_adhoc_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        send_adhoc_btn.clicked.connect(self.go_send.emit)
        b_layout.addWidget(send_adhoc_btn)

        recv_adhoc_btn = QPushButton("📥 Receive Files (QR)")
        recv_adhoc_btn.setObjectName("action_sm")
        recv_adhoc_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        recv_adhoc_btn.clicked.connect(self.go_receive.emit)
        b_layout.addWidget(recv_adhoc_btn)

        b_layout.addStretch(1)

        footer_txt = QLabel("Wi-Fi • USB • Local-Only • Zero Cloud")
        footer_txt.setObjectName("info")
        b_layout.addWidget(footer_txt)

        root.addWidget(bottom_bar)

        self.refresh_devices()

    def _show_pairing_dialog(self):
        dlg = PairingDialog(self.connection_manager, self)
        dlg.paired_success.connect(lambda dev: self.refresh_devices())
        dlg.exec()

    def refresh_devices(self):
        # Clear existing cards
        while self.devices_layout.count():
            item = self.devices_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        devices = self.pairing_manager.get_paired_devices()
        if not devices:
            # Empty state
            empty = QFrame()
            empty.setObjectName("card")
            e_layout = QVBoxLayout(empty)
            e_layout.setContentsMargins(32, 40, 32, 40)
            e_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            e_layout.setSpacing(12)

            e_icon = QLabel("📱")
            e_icon.setStyleSheet("font-size: 40px;")
            e_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
            e_title = QLabel("No Paired Devices Yet")
            e_title.setObjectName("heading")
            e_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
            e_sub = QLabel("Click '➕ Pair New Device' above to scan a QR code and connect your phone or tablet.")
            e_sub.setObjectName("subtitle")
            e_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)

            e_layout.addWidget(e_icon)
            e_layout.addWidget(e_title)
            e_layout.addWidget(e_sub)
            self.devices_layout.addWidget(empty)
            return

        for dev in devices:
            card = self._create_device_card(dev)
            self.devices_layout.addWidget(card)

    def _create_device_card(self, dev: PairedDevice) -> QFrame:
        card = QFrame()
        card.setObjectName("card")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(20, 16, 20, 16)
        card_layout.setSpacing(10)

        # Top row: Device Info + Badges
        top_row = QHBoxLayout()
        top_row.setSpacing(12)

        icon_lbl = QLabel("📱")
        icon_lbl.setStyleSheet("font-size: 24px;")
        top_row.addWidget(icon_lbl)

        info_col = QVBoxLayout()
        name_lbl = QLabel(dev.identity.name)
        name_lbl.setStyleSheet("font-size: 16px; font-weight: 600; color: #fff;")

        # Subtitle: Last seen or endpoint
        sub_text = "Never connected"
        if dev.identity.last_seen > 0:
            diff = int(time.time() - dev.identity.last_seen)
            if diff < 60:
                sub_text = "Active now"
            elif diff < 3600:
                sub_text = f"Active {diff // 60}m ago"
            else:
                sub_text = f"Active {diff // 3600}h ago"
        sub_lbl = QLabel(sub_text)
        sub_lbl.setObjectName("info")

        info_col.addWidget(name_lbl)
        info_col.addWidget(sub_lbl)
        top_row.addLayout(info_col)

        top_row.addStretch(1)

        # Presence badge
        if dev.presence_state == PresenceState.DISCOVERED:
            pres_badge = QLabel("🟢 Online")
            pres_badge.setObjectName("badge_green")
        elif dev.presence_state == PresenceState.SEARCHING:
            pres_badge = QLabel("🟡 Searching...")
            pres_badge.setObjectName("badge_orange")
        else:
            pres_badge = QLabel("⚪ Offline")
            pres_badge.setObjectName("badge_gray")
        top_row.addWidget(pres_badge)

        # Connection badge
        if dev.connection_state == ConnectionState.CONNECTED:
            conn_badge = QLabel("Connected")
            conn_badge.setObjectName("badge_green")
        elif dev.connection_state == ConnectionState.CONNECTING:
            conn_badge = QLabel("Connecting...")
            conn_badge.setObjectName("badge_blue")
        elif dev.connection_state == ConnectionState.AUTHENTICATION_REQUIRED:
            conn_badge = QLabel("Auth Required")
            conn_badge.setObjectName("badge_orange")
        else:
            conn_badge = QLabel("Disconnected")
            conn_badge.setObjectName("badge_gray")
        top_row.addWidget(conn_badge)

        # More menu button
        more_btn = QPushButton("⋮")
        more_btn.setObjectName("action_sm")
        more_btn.setFixedWidth(32)
        more_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        more_btn.clicked.connect(lambda: self._show_device_menu(dev, more_btn))
        top_row.addWidget(more_btn)

        card_layout.addLayout(top_row)

        # Middle row: Capability & Transport pills
        mid_row = QHBoxLayout()
        mid_row.setSpacing(8)

        # Transports
        if dev.endpoint and dev.endpoint.transports:
            for tr in dev.endpoint.transports:
                pill = QLabel(f"📶 {tr.upper()}" if tr == "wifi" else f"🔌 {tr.upper()}")
                pill.setObjectName("transport_pill")
                mid_row.addWidget(pill)

        # Features
        mid_row.addWidget(QLabel("•"))
        ft_pill = QLabel("📁 File Transfer")
        ft_pill.setObjectName("transport_pill")
        mid_row.addWidget(ft_pill)

        mirror_pill = QLabel("🖥️ Screen Mirror")
        mirror_pill.setObjectName("transport_pill")
        mid_row.addWidget(mirror_pill)

        mid_row.addStretch(1)
        card_layout.addLayout(mid_row)

        # Bottom row: Action Buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        # Connect / Disconnect button
        is_conn = dev.connection_state == ConnectionState.CONNECTED
        conn_btn = QPushButton("Disconnect" if is_conn else "Connect")
        conn_btn.setObjectName("action_sm")
        conn_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        dev_id = dev.identity.device_id
        if is_conn:
            conn_btn.clicked.connect(lambda: self.connection_manager.disconnect_device(dev_id))
        else:
            conn_btn.clicked.connect(lambda: self.connection_manager.connect_device(dev_id))
        btn_row.addWidget(conn_btn)

        # Send Files to device
        send_btn = QPushButton("📤 Send Files")
        send_btn.setObjectName("action_primary_sm")
        send_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        send_btn.clicked.connect(lambda: self.go_send_to_device.emit(dev_id))
        btn_row.addWidget(send_btn)

        # Screen Mirror
        mirror_btn = QPushButton("🖥️ Mirror Screen")
        mirror_btn.setObjectName("action_sm")
        mirror_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        mirror_btn.clicked.connect(lambda: self.go_mirror.emit(dev_id))
        btn_row.addWidget(mirror_btn)

        btn_row.addStretch(1)
        card_layout.addLayout(btn_row)

        return card

    def _show_device_menu(self, dev: PairedDevice, anchor: QWidget):
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu { background: #1e293b; color: #fff; border: 1px solid #334155; border-radius: 8px; padding: 4px; }
            QMenu::item { padding: 8px 24px; border-radius: 4px; }
            QMenu::item:selected { background: #3b82f6; }
        """)

        dev_id = dev.identity.device_id

        rename_act = menu.addAction("✏️ Rename Device")
        rename_act.triggered.connect(lambda: self._rename_device(dev_id, dev.identity.name))

        forget_act = menu.addAction("🗑️ Forget Device")
        forget_act.triggered.connect(lambda: self._forget_device(dev_id, dev.identity.name))

        revoke_act = menu.addAction("🚫 Revoke Trust")
        revoke_act.triggered.connect(lambda: self._revoke_device(dev_id, dev.identity.name))

        menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))

    def _rename_device(self, device_id: str, old_name: str):
        new_name, ok = QInputDialog.getText(self, "Rename Device", "Enter new name:", text=old_name)
        if ok and new_name.strip():
            self.pairing_manager.update_device_name(device_id, new_name.strip())
            self.refresh_devices()

    def _forget_device(self, device_id: str, name: str):
        ans = QMessageBox.question(
            self,
            "Forget Device",
            f"Are you sure you want to forget '{name}'?\nTo connect again, you will need to re-pair.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if ans == QMessageBox.StandardButton.Yes:
            self.connection_manager.disconnect_device(device_id)
            self.pairing_manager.remove_paired_device(device_id)
            self.refresh_devices()

    def _revoke_device(self, device_id: str, name: str):
        ans = QMessageBox.warning(
            self,
            "Revoke Trust",
            f"Revoke trust for '{name}'?\nThis device will no longer be allowed to connect or transfer files.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if ans == QMessageBox.StandardButton.Yes:
            self.connection_manager.disconnect_device(device_id)
            self.pairing_manager.revoke_trust(device_id)
            self.refresh_devices()

    def _on_device_updated_from_bg(self, dev: PairedDevice):
        # Refresh UI safely on main Qt thread
        from PyQt6.QtCore import QMetaObject, Q_ARG
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(0, self.refresh_devices)
