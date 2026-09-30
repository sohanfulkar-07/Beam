"""
PhotoBeam Windows UI — Home Screen
"""
from __future__ import annotations

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QFrame, QSpacerItem, QSizePolicy,
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont


class HomeScreen(QWidget):
    go_receive = pyqtSignal()
    go_send = pyqtSignal()
    go_history = pyqtSignal()

    def __init__(self):
        super().__init__()
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.setSpacing(0)
        root.setContentsMargins(60, 60, 60, 60)

        # ── Hero card ────────────────────────────────────────────────────────
        hero = QFrame()
        hero.setObjectName("hero")
        hero_layout = QVBoxLayout(hero)
        hero_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hero_layout.setSpacing(12)
        hero_layout.setContentsMargins(48, 48, 48, 48)

        # App icon / logo text
        icon_label = QLabel("⚡")
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_label.setStyleSheet("font-size: 56px;")

        title = QLabel("PhotoBeam")
        title.setObjectName("title")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        subtitle = QLabel("Fast local file transfer — no cloud, no account")
        subtitle.setObjectName("subtitle")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)

        hero_layout.addWidget(icon_label)
        hero_layout.addWidget(title)
        hero_layout.addWidget(subtitle)

        root.addWidget(hero)
        root.addSpacing(32)

        # ── Action cards ──────────────────────────────────────────────────────
        cards_row = QHBoxLayout()
        cards_row.setSpacing(24)

        receive_card = self._make_action_card(
            icon="📥",
            title="Receive",
            description="Generate a QR code.\nLet others send files to this device.",
            button_text="Start Receiving",
            primary=True,
            signal=self.go_receive,
        )
        send_card = self._make_action_card(
            icon="📤",
            title="Send",
            description="Scan the receiver's QR code.\nSelect files and transfer.",
            button_text="Start Sending",
            primary=False,
            signal=self.go_send,
        )

        cards_row.addWidget(receive_card)
        cards_row.addWidget(send_card)
        root.addLayout(cards_row)

        # ── History & Utility ─────────────────────────────────────────────────
        root.addSpacing(20)
        history_btn = QPushButton("📜 View Transfer History")
        history_btn.setObjectName("secondary")
        history_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        history_btn.clicked.connect(self.go_history.emit)
        root.addWidget(history_btn, alignment=Qt.AlignmentFlag.AlignCenter)

        # ── Footer ────────────────────────────────────────────────────────────
        root.addSpacing(16)
        footer = QLabel("Wi-Fi • USB • No internet required")
        footer.setObjectName("info")
        footer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(footer)

    def _make_action_card(self, icon, title, description, button_text, primary, signal) -> QFrame:
        card = QFrame()
        card.setObjectName("card")
        card.setMinimumWidth(280)
        layout = QVBoxLayout(card)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(14)
        layout.setContentsMargins(32, 32, 32, 32)

        icon_lbl = QLabel(icon)
        icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_lbl.setStyleSheet("font-size: 40px;")

        title_lbl = QLabel(title)
        title_lbl.setObjectName("heading")
        title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

        desc_lbl = QLabel(description)
        desc_lbl.setObjectName("subtitle")
        desc_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        desc_lbl.setWordWrap(True)

        btn = QPushButton(button_text)
        btn.setObjectName("primary" if primary else "secondary")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.clicked.connect(signal)

        layout.addWidget(icon_lbl)
        layout.addWidget(title_lbl)
        layout.addWidget(desc_lbl)
        layout.addSpacing(8)
        layout.addWidget(btn, alignment=Qt.AlignmentFlag.AlignCenter)

        return card
