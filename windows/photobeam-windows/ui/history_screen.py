"""
PhotoBeam Windows UI — Transfer History Screen
Displays persistent history of past transfers with status, stats, and clear options.
"""
from __future__ import annotations

import datetime
from pathlib import Path
from typing import Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QFrame, QScrollArea, QSpacerItem, QSizePolicy,
)
from PyQt6.QtCore import Qt, pyqtSignal

try:
    from history_manager import HistoryManager, TransferRecord
except ImportError:
    from ..history_manager import HistoryManager, TransferRecord


def format_bytes(b: int) -> str:
    if b < 1024:
        return f"{b} B"
    elif b < 1024 * 1024:
        return f"{b / 1024:.1f} KB"
    elif b < 1024 * 1024 * 1024:
        return f"{b / (1024 * 1024):.1f} MB"
    else:
        return f"{b / (1024 * 1024 * 1024):.2f} GB"


class HistoryScreen(QWidget):
    go_back = pyqtSignal()

    def __init__(self):
        super().__init__()
        self._history_mgr = HistoryManager.get_instance()
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(40, 20, 40, 40)
        root.setSpacing(16)

        # ── Top bar ───────────────────────────────────────────────────────────
        top = QHBoxLayout()
        back_btn = QPushButton("← Back")
        back_btn.setObjectName("back")
        back_btn.clicked.connect(self.go_back.emit)
        top.addWidget(back_btn)

        top.addStretch()

        clear_btn = QPushButton("🗑 Clear History")
        clear_btn.setObjectName("secondary")
        clear_btn.clicked.connect(self._on_clear)
        top.addWidget(clear_btn)

        root.addLayout(top)

        # ── Header ────────────────────────────────────────────────────────────
        header = QLabel("📜 Transfer History")
        header.setObjectName("heading")
        root.addWidget(header)

        # ── Scroll Area for Records ───────────────────────────────────────────
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._container = QWidget()
        self._container_layout = QVBoxLayout(self._container)
        self._container_layout.setSpacing(12)
        self._container_layout.setContentsMargins(0, 0, 10, 0)
        self._container_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        from PyQt6.QtWidgets import QLayout
        self._container_layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        self._scroll.setWidget(self._container)
        root.addWidget(self._scroll)



    def on_shown(self):
        """Refresh records whenever screen is displayed."""
        self._refresh()

    def _refresh(self):
        # Clear existing items and spacers
        while self._container_layout.count():
            item = self._container_layout.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)
                w.deleteLater()

        records = self._history_mgr.get_records()
        if not records:
            empty = QFrame()
            empty.setObjectName("card")
            empty_layout = QVBoxLayout(empty)
            empty_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty_layout.setContentsMargins(40, 60, 40, 60)

            icon = QLabel("📂")
            icon.setStyleSheet("font-size: 40px;")
            icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty_layout.addWidget(icon)

            lbl = QLabel("No transfer history yet")
            lbl.setObjectName("heading")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty_layout.addWidget(lbl)

            hint = QLabel("Completed and attempted transfers will appear here.")
            hint.setObjectName("info")
            hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty_layout.addWidget(hint)

            self._container_layout.addWidget(empty)
            return

        for record in records:
            card = self._create_record_card(record)
            self._container_layout.addWidget(card)

    def _create_record_card(self, r: TransferRecord) -> QFrame:
        card = QFrame()
        card.setObjectName("card")
        layout = QVBoxLayout(card)
        layout.setSpacing(10)
        layout.setContentsMargins(18, 14, 18, 14)

        # Row 1: Direction + Transport + Status + Timestamp
        row1 = QHBoxLayout()
        row1.setSpacing(10)

        dir_icon = "📤 Sent" if r.direction == "sent" else "📥 Received"
        dir_lbl = QLabel(dir_icon)
        dir_lbl.setStyleSheet("font-size: 14px; font-weight: 700; color: #FFFFFF;")
        row1.addWidget(dir_lbl)

        # Transport badge
        trans_lbl = QLabel(f"📶 {r.transport_type.upper()}" if "wifi" in r.transport_type.lower() else f"🔌 {r.transport_type.upper()}")
        trans_lbl.setObjectName("transport_pill")
        row1.addWidget(trans_lbl)

        row1.addStretch()

        # Status badge
        status_lbl = QLabel()
        if r.status == "completed":
            status_lbl.setText("✓ Completed")
            status_lbl.setObjectName("badge_green")
        elif r.status == "interrupted":
            status_lbl.setText("⚠ Interrupted")
            status_lbl.setObjectName("badge_orange")
        else:
            status_lbl.setText("✕ Failed")
            status_lbl.setObjectName("badge_orange")
        row1.addWidget(status_lbl)

        # Timestamp
        dt = datetime.datetime.fromtimestamp(r.timestamp)
        time_str = dt.strftime("%b %d, %H:%M")
        time_lbl = QLabel(time_str)
        time_lbl.setObjectName("info")
        row1.addWidget(time_lbl)

        layout.addLayout(row1)

        # Row 2: File summary
        files_str = ", ".join(r.files[:3])
        if len(r.files) > 3:
            files_str += f" + {len(r.files) - 3} more"
        file_summary = f"{len(r.files)} file(s): {files_str}"
        files_lbl = QLabel(file_summary)
        files_lbl.setStyleSheet("color: #E2E8F0; font-size: 13px; font-weight: 500;")
        files_lbl.setWordWrap(True)
        layout.addWidget(files_lbl)

        # Row 3: Size, duration, speed, error
        row3 = QHBoxLayout()
        row3.setSpacing(14)
        size_lbl = QLabel(f"Size: {format_bytes(r.total_bytes)}")
        size_lbl.setStyleSheet("color: #94A3B8; font-size: 12px;")
        row3.addWidget(size_lbl)

        if r.duration_sec > 0 and r.status == "completed":
            dur_str = f"{r.duration_sec:.1f}s"
            if r.total_bytes > 0:
                avg_speed = (r.total_bytes / (1024 * 1024)) / r.duration_sec
                dur_str += f" • Speed: {avg_speed:.1f} MB/s"
            dur_lbl = QLabel(f"Duration: {dur_str}")
            dur_lbl.setStyleSheet("color: #38BDF8; font-weight: 600; font-size: 12px;")
            row3.addWidget(dur_lbl)

        if r.error_reason:
            err_lbl = QLabel(f"Reason: {r.error_reason}")
            err_lbl.setStyleSheet("color: #F87171; font-size: 12px;")
            row3.addWidget(err_lbl)

        row3.addStretch()
        layout.addLayout(row3)

        return card


    def _on_clear(self):
        self._history_mgr.clear()
        self._refresh()
