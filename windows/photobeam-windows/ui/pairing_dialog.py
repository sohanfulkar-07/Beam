"""
PhotoBeam Windows UI — Pairing Dialog

Provides QR code display for initial device pairing,
manual pairing code input fallback, and device approval dialog.
"""
from __future__ import annotations

import base64
import os
import sys
import time
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

import qrcode

if getattr(sys, 'frozen', False):
    _proto = os.path.join(getattr(sys, '_MEIPASS', os.path.dirname(sys.executable)), 'protocol')
else:
    _proto = str(Path(__file__).resolve().parent.parent.parent / "protocol")
if _proto not in sys.path:
    sys.path.append(_proto)

try:
    from src.models import (
        PROTOCOL_VERSION,
        Capability,
        PairingPayload,
        decode_pairing_payload,
        encode_pairing_payload,
    )
except ImportError:
    from models import (
        PROTOCOL_VERSION,
        Capability,
        PairingPayload,
        decode_pairing_payload,
        encode_pairing_payload,
    )



class PairingDialog(QDialog):
    """Pairing modal showing QR code and manual entry fallback."""

    paired_success = pyqtSignal(object)  # emits PairedDevice

    def __init__(self, connection_manager, parent=None):
        super().__init__(parent)
        self.connection_manager = connection_manager
        self.pairing_manager = connection_manager.pairing_manager
        self.setWindowTitle("Pair New Device — PhotoBeam")
        self.setFixedSize(520, 640)
        self.setModal(True)

        self._build_ui()
        self._generate_qr()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 28, 32, 28)
        layout.setSpacing(16)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        title = QLabel("Pair New Device")
        title.setObjectName("heading")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        subtitle = QLabel("Scan this QR code with the PhotoBeam app on your Android device")
        subtitle.setObjectName("subtitle")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setWordWrap(True)

        layout.addWidget(title)
        layout.addWidget(subtitle)

        # QR Frame
        self.qr_label = QLabel()
        self.qr_label.setObjectName("qr_label")
        self.qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.qr_label.setFixedSize(260, 260)
        layout.addWidget(self.qr_label, alignment=Qt.AlignmentFlag.AlignCenter)

        # Instructions / Fallback
        or_label = QLabel("— OR PAIR MANUALLY —")
        or_label.setObjectName("info")
        or_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(or_label)

        manual_row = QHBoxLayout()
        self.code_input = QLineEdit()
        self.code_input.setPlaceholderText("Paste photobeam://pair/... code here")
        self.code_input.setStyleSheet(
            "background: #1e293b; border: 1px solid #334155; border-radius: 8px; padding: 8px; color: #fff;"
        )

        pair_btn = QPushButton("Pair")
        pair_btn.setObjectName("action_primary_sm")
        pair_btn.clicked.connect(self._on_manual_pair_clicked)
        manual_row.addWidget(self.code_input)
        manual_row.addWidget(pair_btn)
        layout.addLayout(manual_row)

        self.status_label = QLabel("")
        self.status_label.setObjectName("info")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.status_label)

        # Close button
        close_btn = QPushButton("Close")
        close_btn.setObjectName("secondary")
        close_btn.clicked.connect(self.close)
        layout.addWidget(close_btn, alignment=Qt.AlignmentFlag.AlignCenter)

    def _generate_qr(self):
        local_id = self.pairing_manager.get_local_identity()
        addrs = self.connection_manager.discovery_service._get_local_ips()

        nonce = base64.b64encode(os.urandom(32)).decode("ascii")
        token = base64.b64encode(os.urandom(24)).decode("ascii")
        exp = int(time.time()) + 1800  # 30 min expiry

        self.current_payload = PairingPayload(
            v=PROTOCOL_VERSION,
            sid=str(os.urandom(16).hex()),
            rid=local_id.device_id,
            addrs=addrs,
            port=47474,
            transports=["wifi", "usb"],
            token=token,
            exp=exp,
            cert_fp="",
            device_name=local_id.name,
            device_public_key=local_id.public_key,
            capabilities=[c.value for c in local_id.capabilities],
            pairing_nonce=nonce,
        )

        uri = encode_pairing_payload(self.current_payload)
        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=6,
            border=2,
        )
        qr.add_data(uri)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white").convert("RGBA")

        qimg = QImage(
            img.tobytes("raw", "RGBA"),
            img.size[0],
            img.size[1],
            QImage.Format.Format_RGBA8888,
        )
        self.qr_label.setPixmap(QPixmap.fromImage(qimg).scaled(240, 240, Qt.AspectRatioMode.KeepAspectRatio))

    def _on_manual_pair_clicked(self):
        text = self.code_input.text().strip()
        if not text:
            return
        try:
            payload = decode_pairing_payload(text)
            self.status_label.setText("Processing pairing...")
            self.connection_manager.pair_with_payload(
                payload,
                on_success=self._on_paired_success,
                on_error=self._on_paired_error,
            )
        except Exception as e:
            self.status_label.setText(f"Invalid code: {e}")

    def _on_paired_success(self, paired_device):
        self.status_label.setText(f"Successfully paired with {paired_device.identity.name}!")
        self.paired_success.emit(paired_device)
        self.accept()

    def _on_paired_error(self, err_msg):
        self.status_label.setText(f"Pairing error: {err_msg}")
