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

from PyQt6.QtCore import Qt, pyqtSignal, pyqtSlot, QTimer
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

import qrcode

if getattr(sys, 'frozen', False):
    _proto = os.path.join(getattr(sys, '_MEIPASS', os.path.dirname(sys.executable)), 'protocol')
else:
    _p = Path(__file__).resolve()
    _proto = str(_p.parent.parent.parent.parent / "protocol")
    if not os.path.exists(_proto):
        _proto = str(_p.parent.parent.parent / "protocol")
if _proto not in sys.path:
    sys.path.append(_proto)

try:
    from src.models import (
        PROTOCOL_VERSION,
        Capability,
        ConnectionState,
        PairingPayload,
        decode_pairing_payload,
        encode_pairing_payload,
    )
except ImportError:
    from models import (
        PROTOCOL_VERSION,
        Capability,
        ConnectionState,
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
        self.setMinimumSize(460, 540)
        self.resize(500, 620)
        self.setModal(True)

        self._closing_soon = False
        self._build_ui()
        self._generate_qr()

        # Connect directly to ConnectionManager Qt signals for immediate main-thread response
        if hasattr(self.connection_manager, "sig_pairing_completed") and self.connection_manager.sig_pairing_completed:
            self.connection_manager.sig_pairing_completed.connect(self._on_pairing_completed_signal)
        if hasattr(self.connection_manager, "sig_device_connected") and self.connection_manager.sig_device_connected:
            self.connection_manager.sig_device_connected.connect(self._on_device_connected_signal)
        if hasattr(self.connection_manager, "sig_device_updated") and self.connection_manager.sig_device_updated:
            self.connection_manager.sig_device_updated.connect(self._on_device_updated_signal)

    def _build_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(16, 16, 16, 16)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("background: transparent;")

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(14)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        title = QLabel("Pair New Device")
        title.setObjectName("dash_heading")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        subtitle = QLabel("Scan this QR code with the PhotoBeam app on your Android device")
        subtitle.setObjectName("muted_text")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setWordWrap(True)

        layout.addWidget(title)
        layout.addWidget(subtitle)

        # QR Frame with crisp white card & quiet zone
        self.qr_label = QLabel()
        self.qr_label.setObjectName("qr_label")
        self.qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.qr_label.setFixedSize(260, 260)
        self.qr_label.setStyleSheet("background: #ffffff; border-radius: 14px; padding: 12px;")
        layout.addWidget(self.qr_label, alignment=Qt.AlignmentFlag.AlignCenter)

        # Instructions / Fallback
        or_label = QLabel("— OR PAIR MANUALLY —")
        or_label.setObjectName("muted_text")
        or_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(or_label)

        manual_row = QHBoxLayout()
        self.code_input = QLineEdit()
        self.code_input.setPlaceholderText("Paste photobeam://pair/... code here")

        pair_btn = QPushButton(" Pair")
        pair_btn.setObjectName("action_primary_sm")
        pair_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        pair_btn.clicked.connect(self._on_manual_pair_clicked)
        manual_row.addWidget(self.code_input)
        manual_row.addWidget(pair_btn)
        layout.addLayout(manual_row)

        self.status_label = QLabel("")
        self.status_label.setObjectName("muted_text")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.status_label)

        # Close button
        close_btn = QPushButton("Close")
        close_btn.setObjectName("secondary")
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(self.close)
        layout.addWidget(close_btn, alignment=Qt.AlignmentFlag.AlignCenter)

        scroll.setWidget(container)
        main_layout.addWidget(scroll)

        # Register external update listener as fallback
        self.connection_manager.add_device_updated_callback(self._on_external_device_updated)

    def closeEvent(self, event):
        self.connection_manager.remove_device_updated_callback(self._on_external_device_updated)
        for sig, slot in [
            (getattr(self.connection_manager, "sig_pairing_completed", None), self._on_pairing_completed_signal),
            (getattr(self.connection_manager, "sig_device_connected", None), self._on_device_connected_signal),
            (getattr(self.connection_manager, "sig_device_updated", None), self._on_device_updated_signal),
        ]:
            if sig:
                try:
                    sig.disconnect(slot)
                except (TypeError, RuntimeError):
                    pass
        super().closeEvent(event)

    @pyqtSlot(object)
    def _on_pairing_completed_signal(self, device):
        self._handle_pairing_success(device)

    @pyqtSlot(str, str)
    def _on_device_connected_signal(self, device_id: str, transport_type: str):
        dev = self.pairing_manager.get_paired_device(device_id)
        self._handle_pairing_success(dev)

    @pyqtSlot(object)
    def _on_device_updated_signal(self, device):
        if device and getattr(device, "connection_state", None) == ConnectionState.CONNECTED:
            self._handle_pairing_success(device)

    def _handle_pairing_success(self, device):
        if self._closing_soon:
            return
        self._closing_soon = True
        name = device.identity.name if (device and hasattr(device, "identity")) else "Device"
        self.status_label.setStyleSheet("color: #10B981; font-weight: 700; font-size: 13px;")
        self.status_label.setText(f"✓ Connected to {name}!")
        if device:
            self.paired_success.emit(device)
        QTimer.singleShot(500, self.accept)

    def _on_external_device_updated(self, device):
        if device and getattr(device, "connection_state", None) == ConnectionState.CONNECTED:
            from PyQt6.QtCore import QMetaObject, Qt
            QMetaObject.invokeMethod(self, "_on_external_safe", Qt.ConnectionType.QueuedConnection)

    @pyqtSlot()
    def _on_external_safe(self):
        devs = self.pairing_manager.get_paired_devices()
        conn = [d for d in devs if d.connection_state == ConnectionState.CONNECTED]
        if conn:
            self._handle_pairing_success(conn[0])

    def _generate_qr(self):
        local_id = self.pairing_manager.get_local_identity()
        addrs = list(self.connection_manager.discovery_service._get_local_ips())
        if "127.0.0.1" not in addrs:
            addrs.append("127.0.0.1")

        nonce = base64.b64encode(os.urandom(32)).decode("ascii")
        token = base64.b64encode(os.urandom(24)).decode("ascii")
        exp = int(time.time()) + 1800  # 30 min expiry

        self.connection_manager.set_active_pairing_token(token, 1800)

        self.current_payload = PairingPayload(
            v=PROTOCOL_VERSION,
            sid=str(os.urandom(16).hex()),
            rid=local_id.device_id,
            addrs=addrs,
            port=47470,
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
            box_size=8,
            border=4,
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
        self.qr_label.setPixmap(
            QPixmap.fromImage(qimg).scaled(
                240, 240, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
            )
        )

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
        self._handle_pairing_success(paired_device)

    def _on_paired_error(self, err_msg):
        self.status_label.setStyleSheet("color: #EF4444; font-weight: 500; font-size: 12px;")
        self.status_label.setText(f"Pairing error: {err_msg}")
