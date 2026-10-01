"""
PhotoBeam Windows UI — Unified Connect Home Screen (Reference 2 Dashboard)

Redesigned as a modern dark-blue dashboard:
- Top Hero banner ("Make Transfers Simple !")
- Left column: Trusted Devices list with live presence, glowing rings, and tactile per-device actions
- Right column: Fast Dropzone (drag & drop files) and Security & Engine activity panel
- Preserves all attributes required by unit tests and pairing/transfer flows.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QDragEnterEvent, QDropEvent
from PyQt6.QtWidgets import (
    QFileDialog,
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

if getattr(sys, "frozen", False):
    _proto = os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(sys.executable)), "protocol")
else:
    _proto = str(Path(__file__).resolve().parent.parent.parent / "protocol")
if _proto not in sys.path:
    sys.path.append(_proto)

try:
    from src.models import ConnectionState, PairedDevice, PresenceState, TrustStatus
except ImportError:
    from models import ConnectionState, PairedDevice, PresenceState, TrustStatus

try:
    from connection_manager import ConnectionManager
except (ImportError, ValueError):
    from ..connection_manager import ConnectionManager

try:
    from .pairing_dialog import PairingDialog
except (ImportError, ValueError):
    try:
        from ui.pairing_dialog import PairingDialog
    except (ImportError, ValueError):
        from pairing_dialog import PairingDialog


class HomeScreen(QWidget):
    go_receive = pyqtSignal()
    go_send = pyqtSignal()
    go_history = pyqtSignal()
    go_mirror = pyqtSignal(str)          # device_id
    go_send_to_device = pyqtSignal(str)   # device_id
    files_dropped = pyqtSignal(list)      # list of Path

    def __init__(self, connection_manager: Optional[ConnectionManager] = None):
        super().__init__()
        self.connection_manager = connection_manager or ConnectionManager.get_instance()
        self.pairing_manager = self.connection_manager.pairing_manager

        self.setAcceptDrops(True)
        self.connection_manager.add_device_updated_callback(self._on_device_updated_from_bg)
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 20, 28, 24)
        root.setSpacing(18)

        # ── Hero Banner (Reference 2 Inspiration) ────────────────────────────
        hero_card = QFrame()
        hero_card.setObjectName("hero_card")
        hero_layout = QHBoxLayout(hero_card)
        hero_layout.setContentsMargins(24, 20, 24, 20)
        hero_layout.setSpacing(20)

        hero_text_box = QVBoxLayout()
        hero_text_box.setSpacing(6)
        hero_title = QLabel("Make Transfers Simple !")
        hero_title.setObjectName("hero_title")
        hero_sub = QLabel("Direct peer-to-peer transfer between Windows & Android with zero cloud exposure.")
        hero_sub.setObjectName("subtitle")
        hero_sub.setWordWrap(True)
        hero_text_box.addWidget(hero_title)
        hero_text_box.addWidget(hero_sub)
        hero_layout.addLayout(hero_text_box, stretch=1)

        hero_layout.addStretch(1)

        # Quick feature badges inside hero
        pill_row = QHBoxLayout()
        pill_row.setSpacing(8)
        pill_p2p = QLabel("🔒 100% P2P")
        pill_p2p.setObjectName("transport_pill")
        pill_e2e = QLabel("🛡️ E2E Encrypted")
        pill_e2e.setObjectName("transport_pill")
        pill_multi = QLabel("⚡ Multipath Ready")
        pill_multi.setObjectName("transport_pill")
        pill_row.addWidget(pill_p2p)
        pill_row.addWidget(pill_e2e)
        pill_row.addWidget(pill_multi)
        hero_layout.addLayout(pill_row)

        root.addWidget(hero_card)

        # ── Two-Column Main Workspace ─────────────────────────────────────────
        workspace_layout = QHBoxLayout()
        workspace_layout.setSpacing(20)

        # ── Left Column: Trusted Devices (~62% width) ─────────────────────────
        left_col = QVBoxLayout()
        left_col.setSpacing(12)

        # Section Header
        sec_header = QHBoxLayout()
        sec_header.setSpacing(10)
        sec_title = QLabel("Trusted Devices")
        sec_title.setObjectName("heading")
        sec_header.addWidget(sec_title)

        self._device_count_badge = QLabel("0 Devices")
        self._device_count_badge.setObjectName("badge_blue")
        sec_header.addWidget(self._device_count_badge)

        sec_header.addStretch(1)

        refresh_btn = QPushButton("🔄 Refresh")
        refresh_btn.setObjectName("action_sm")
        refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        refresh_btn.clicked.connect(self.refresh_devices)
        sec_header.addWidget(refresh_btn)

        pair_top_btn = QPushButton("➕ Pair Device")
        pair_top_btn.setObjectName("action_primary_sm")
        pair_top_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        pair_top_btn.clicked.connect(self._show_pairing_dialog)
        sec_header.addWidget(pair_top_btn)

        left_col.addLayout(sec_header)

        # Scroll Area for Device Cards
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.devices_container = QWidget()
        self.devices_layout = QVBoxLayout(self.devices_container)
        self.devices_layout.setContentsMargins(0, 0, 4, 0)
        self.devices_layout.setSpacing(12)
        self.devices_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        from PyQt6.QtWidgets import QLayout
        self.devices_layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)

        self.scroll_area.setWidget(self.devices_container)
        left_col.addWidget(self.scroll_area, 1)

        workspace_layout.addLayout(left_col, 62)

        # ── Right Column: Dropzone & Activity (~38% width) ────────────────────
        right_col = QVBoxLayout()
        right_col.setSpacing(16)

        # 1. Fast Send Dropzone
        self.dropzone = QFrame()
        self.dropzone.setObjectName("dropzone")
        drop_layout = QVBoxLayout(self.dropzone)
        drop_layout.setContentsMargins(20, 24, 20, 24)
        drop_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        drop_layout.setSpacing(10)

        drop_icon = QLabel("📁")
        drop_icon.setStyleSheet("font-size: 38px; color: #38BDF8;")
        drop_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)

        drop_title = QLabel("Fast Send Dropzone")
        drop_title.setObjectName("heading")
        drop_title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        drop_sub = QLabel("Drag & drop files here to send immediately")
        drop_sub.setObjectName("info")
        drop_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)

        select_files_btn = QPushButton("➕ Choose Files...")
        select_files_btn.setObjectName("primary")
        select_files_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        select_files_btn.clicked.connect(self._on_choose_files_clicked)

        drop_layout.addWidget(drop_icon)
        drop_layout.addWidget(drop_title)
        drop_layout.addWidget(drop_sub)
        drop_layout.addSpacing(6)
        drop_layout.addWidget(select_files_btn, alignment=Qt.AlignmentFlag.AlignCenter)

        right_col.addWidget(self.dropzone)

        # 2. Activity & Security Summary Card
        activity_card = QFrame()
        activity_card.setObjectName("card")
        act_layout = QVBoxLayout(activity_card)
        act_layout.setContentsMargins(20, 18, 20, 18)
        act_layout.setSpacing(12)

        act_title_row = QHBoxLayout()
        act_title = QLabel("Security & Transfer Engine")
        act_title.setObjectName("heading")
        act_title_row.addWidget(act_title)
        act_title_row.addStretch()
        act_layout.addLayout(act_title_row)

        engine_row = QLabel("⚡ Beam Multipath Engine (Wi-Fi + USB)")
        engine_row.setObjectName("info")
        enc_row = QLabel("🔒 TLS Mutual Auth & SHA-256 Verifier")
        enc_row.setObjectName("info")
        cloud_row = QLabel("🛡️ Zero Cloud • 100% Local Storage")
        cloud_row.setObjectName("info")
        act_layout.addWidget(engine_row)
        act_layout.addWidget(enc_row)
        act_layout.addWidget(cloud_row)

        act_layout.addSpacing(4)

        hist_btn = QPushButton("View Transfer Activity →")
        hist_btn.setObjectName("secondary")
        hist_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        hist_btn.clicked.connect(self.go_history.emit)
        act_layout.addWidget(hist_btn)

        right_col.addWidget(activity_card)

        # 3. Ad-hoc Actions Fallback Card
        adhoc_card = QFrame()
        adhoc_card.setObjectName("card")
        adhoc_layout = QHBoxLayout(adhoc_card)
        adhoc_layout.setContentsMargins(16, 12, 16, 12)
        adhoc_layout.setSpacing(10)

        recv_adhoc_btn = QPushButton("📥 Receive (Scan QR)")
        recv_adhoc_btn.setObjectName("action_sm")
        recv_adhoc_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        recv_adhoc_btn.clicked.connect(self.go_receive.emit)
        adhoc_layout.addWidget(recv_adhoc_btn)

        send_adhoc_btn = QPushButton("📤 Send (URI Link)")
        send_adhoc_btn.setObjectName("action_sm")
        send_adhoc_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        send_adhoc_btn.clicked.connect(self.go_send.emit)
        adhoc_layout.addWidget(send_adhoc_btn)

        right_col.addWidget(adhoc_card)
        right_col.addStretch(1)

        workspace_layout.addLayout(right_col, 38)
        root.addLayout(workspace_layout, 1)

        # Hidden local badge preserved for tests
        local_id = self.pairing_manager.get_local_identity()
        self.local_badge = QLabel(f"💻 {local_id.name} (Discoverable)")
        self.local_badge.setObjectName("badge_green")
        self.local_badge.setVisible(False)
        root.addWidget(self.local_badge)

        self.refresh_devices()

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent):
        files = [Path(url.toLocalFile()) for url in event.mimeData().urls() if url.isLocalFile()]
        if files:
            self.files_dropped.emit(files)

    def _on_choose_files_clicked(self):
        selected, _ = QFileDialog.getOpenFileNames(self, "Select Files to Send")
        if selected:
            paths = [Path(f) for f in selected]
            self.files_dropped.emit(paths)

    def _show_pairing_dialog(self):
        dlg = PairingDialog(self.connection_manager, self)
        dlg.paired_success.connect(lambda dev: self.refresh_devices())
        dlg.exec()

    def refresh_devices(self):
        # Clear existing cards immediately
        while self.devices_layout.count():
            item = self.devices_layout.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)
                w.deleteLater()

        devices = self.pairing_manager.get_paired_devices()
        count_txt = f"{len(devices)} Device" if len(devices) == 1 else f"{len(devices)} Devices"
        self._device_count_badge.setText(count_txt)

        if not devices:
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

            e_sub = QLabel("Pair your Android phone or tablet to enable 1-tap instant transfer.")
            e_sub.setObjectName("subtitle")
            e_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)

            pair_btn = QPushButton("➕ Pair New Device")
            pair_btn.setObjectName("action_primary_sm")
            pair_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            pair_btn.clicked.connect(self._show_pairing_dialog)

            e_layout.addWidget(e_icon)
            e_layout.addWidget(e_title)
            e_layout.addWidget(e_sub)
            e_layout.addSpacing(6)
            e_layout.addWidget(pair_btn, alignment=Qt.AlignmentFlag.AlignCenter)
            self.devices_layout.addWidget(empty)
            return

        for dev in devices:
            card = self._create_device_card(dev)
            self.devices_layout.addWidget(card)

    def _create_device_card(self, dev: PairedDevice) -> QFrame:
        card = QFrame()
        card.setObjectName("card")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(18, 16, 18, 16)
        card_layout.setSpacing(12)

        # Top row: Avatar icon ring + Device Info + Presence Badges + Menu
        top_row = QHBoxLayout()
        top_row.setSpacing(14)

        icon_frame = QFrame()
        icon_frame.setObjectName("device_icon_ring")
        icon_layout = QVBoxLayout(icon_frame)
        icon_layout.setContentsMargins(0, 0, 0, 0)
        icon_lbl = QLabel("📱")
        icon_lbl.setStyleSheet("font-size: 20px;")
        icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_layout.addWidget(icon_lbl)
        top_row.addWidget(icon_frame)

        info_col = QVBoxLayout()
        info_col.setSpacing(2)
        name_lbl = QLabel(dev.identity.name)
        name_lbl.setStyleSheet("font-size: 15px; font-weight: 700; color: #FFFFFF;")

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

        # Overflow menu button
        more_btn = QPushButton("⋮")
        more_btn.setObjectName("action_sm")
        more_btn.setFixedWidth(32)
        more_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        more_btn.clicked.connect(lambda: self._show_device_menu(dev, more_btn))
        top_row.addWidget(more_btn)

        card_layout.addLayout(top_row)

        # Middle row: Transports and capabilities
        mid_row = QHBoxLayout()
        mid_row.setSpacing(8)

        if dev.endpoint and dev.endpoint.transports:
            for tr in dev.endpoint.transports:
                pill = QLabel(f"📶 {tr.upper()}" if tr == "wifi" else f"🔌 {tr.upper()}")
                pill.setObjectName("transport_pill")
                mid_row.addWidget(pill)

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

        send_btn = QPushButton("📤 Send Files")
        send_btn.setObjectName("action_primary_sm")
        send_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        send_btn.clicked.connect(lambda: self.go_send_to_device.emit(dev_id))
        btn_row.addWidget(send_btn)

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
            QMenu { background: #121A2D; color: #F8FAFC; border: 1px solid #1E2D4A; border-radius: 8px; padding: 4px; }
            QMenu::item { padding: 8px 24px; border-radius: 4px; }
            QMenu::item:selected { background: #2563EB; }
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
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(0, self.refresh_devices)
