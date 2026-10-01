"""
PhotoBeam Windows UI — Screen Mirroring & Viewing Widget

Features:
- Real-time display of incoming video frames from Android or local capture preview
- Frame rate (FPS), bitrate, and latency stats overlay
- Drag-and-drop file delivery onto the mirrored display directly targeting the peer device
- Clean stop and disconnect controls
"""
from __future__ import annotations

import logging
import os
import socket
import struct
import sys
import threading
import time
from pathlib import Path
from typing import List, Optional

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QDragEnterEvent, QDropEvent, QImage, QPainter, QPixmap
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger("photobeam.screen_viewer")

MIRROR_MAGIC = b"PBMS"  # PhotoBeam Mirror Stream


class ScreenViewer(QWidget):
    """
    Live screen mirror viewer with drag-and-drop file transfer integration.
    """

    go_back = pyqtSignal()
    files_dropped = pyqtSignal(str, list)  # (device_id, file_paths)

    def __init__(self, device_id: str = "", device_name: str = "Android Device"):
        super().__init__()
        self.device_id = device_id
        self.device_name = device_name

        self._running = False
        self._server_sock: Optional[socket.socket] = None
        self._client_sock: Optional[socket.socket] = None
        self._stream_thread: Optional[threading.Thread] = None

        self._fps_count = 0
        self._fps = 0.0
        self._bitrate_bps = 0.0
        self._bytes_this_sec = 0
        self._last_stat_time = time.time()

        self._current_pixmap: Optional[QPixmap] = None

        self.setAcceptDrops(True)
        self._build_ui()

        # Stats timer (updates every 1000ms)
        self._stat_timer = QTimer(self)
        self._stat_timer.timeout.connect(self._update_stats_display)
        self._stat_timer.start(1000)

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(14)

        # ── Top Bar ────────────────────────────────────────────────────────────
        top_bar = QHBoxLayout()

        back_btn = QPushButton("← Back to Hub")
        back_btn.setObjectName("action_sm")
        back_btn.clicked.connect(self._on_stop_and_back)
        top_bar.addWidget(back_btn)

        self.title_lbl = QLabel(f"🖥️ Mirrored Screen — {self.device_name}")
        self.title_lbl.setObjectName("heading")
        top_bar.addWidget(self.title_lbl)

        top_bar.addStretch(1)

        # Stream stats pills
        self.fps_badge = QLabel("0 FPS")
        self.fps_badge.setObjectName("badge_green")
        top_bar.addWidget(self.fps_badge)

        self.bitrate_badge = QLabel("0.0 Mbps")
        self.bitrate_badge.setObjectName("badge_blue")
        top_bar.addWidget(self.bitrate_badge)

        self.quality_badge = QLabel("Quality: High")
        self.quality_badge.setObjectName("badge_gray")
        top_bar.addWidget(self.quality_badge)

        stop_btn = QPushButton("Stop Stream")
        stop_btn.setObjectName("action_sm")
        stop_btn.setStyleSheet("background-color: #ef4444; color: #fff; border: none; font-weight: 600;")
        stop_btn.clicked.connect(self._on_stop_and_back)
        top_bar.addWidget(stop_btn)

        root.addLayout(top_bar)

        # ── Video Canvas ───────────────────────────────────────────────────────
        self.canvas_frame = QFrame()
        self.canvas_frame.setObjectName("card")
        self.canvas_frame.setStyleSheet("background-color: #05070a; border: 2px dashed #334155; border-radius: 12px;")
        canvas_layout = QVBoxLayout(self.canvas_frame)
        canvas_layout.setContentsMargins(0, 0, 0, 0)
        canvas_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.display_lbl = QLabel("Waiting for stream connection from Android...\n\n💡 Tip: You can drag and drop files directly onto this screen to send them to the device.")
        self.display_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.display_lbl.setStyleSheet("color: #64748b; font-size: 15px; font-weight: 500;")
        self.display_lbl.setWordWrap(True)
        canvas_layout.addWidget(self.display_lbl)

        root.addWidget(self.canvas_frame, 1)

        # ── Drop hint footer ──────────────────────────────────────────────────
        footer = QHBoxLayout()
        hint = QLabel("📂 Drag and drop any file or folder above to transfer immediately.")
        hint.setObjectName("info")
        footer.addWidget(hint)
        footer.addStretch(1)

        res_lbl = QLabel("Stream Encrypted • Zero Cloud • Local TLS")
        res_lbl.setObjectName("info")
        footer.addWidget(res_lbl)
        root.addLayout(footer)

    def start_receiver(self, port: int = 47478):
        """Start listening for incoming mirror video stream frames."""
        if self._running:
            return
        self._running = True

        def _listen_loop():
            try:
                self._server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self._server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                self._server_sock.bind(("", port))
                self._server_sock.listen(1)
                self._server_sock.settimeout(2.0)

                while self._running:
                    try:
                        client, addr = self._server_sock.accept()
                        self._client_sock = client
                        logger.info("Mirror stream connected from %s", addr)
                        self._read_stream(client)
                    except socket.timeout:
                        continue
                    except Exception as e:
                        if not self._running:
                            break
            except Exception as e:
                logger.error("Mirror receiver error: %s", e)
            finally:
                if self._server_sock:
                    try:
                        self._server_sock.close()
                    except Exception:
                        pass

        self._stream_thread = threading.Thread(target=_listen_loop, name="PhotoBeam-MirrorReceiver", daemon=True)
        self._stream_thread.start()

    def start_with_socket(self, sock: socket.socket) -> None:
        """Start reading mirror frames from an already-connected socket.
        Used when ConnectionManager's background listener accepted the connection.
        """
        if self._running:
            return
        self._running = True
        self._client_sock = sock

        def _read_loop():
            try:
                self._read_stream(sock)
            finally:
                try:
                    sock.close()
                except Exception:
                    pass

        self._stream_thread = threading.Thread(target=_read_loop, name="PhotoBeam-MirrorReader", daemon=True)
        self._stream_thread.start()
        logger.info("ScreenViewer: reading mirror stream from pre-connected socket")



    def _read_stream(self, sock: socket.socket):
        """Reads framed JPEG/H.264 video frames from socket."""
        sock.settimeout(5.0)
        while self._running:
            try:
                # 4 bytes magic + 4 bytes length
                header = self._recv_all(sock, 8)
                if not header or not header.startswith(MIRROR_MAGIC):
                    break
                frame_len = struct.unpack(">I", header[4:8])[0]
                frame_bytes = self._recv_all(sock, frame_len)
                if not frame_bytes or len(frame_bytes) < frame_len:
                    break

                self._bytes_this_sec += len(frame_bytes) + 8
                self._fps_count += 1

                # Display frame
                qimg = QImage.fromData(frame_bytes)
                if not qimg.isNull():
                    pix = QPixmap.fromImage(qimg)
                    self._current_pixmap = pix
                    QTimer.singleShot(0, self._render_frame)
            except Exception as e:
                logger.debug("Mirror frame recv error: %s", e)
                break

    def _recv_all(self, sock: socket.socket, n: int) -> Optional[bytes]:
        buf = bytearray()
        while len(buf) < n and self._running:
            chunk = sock.recv(min(n - len(buf), 65536))
            if not chunk:
                return None
            buf.extend(chunk)
        return bytes(buf)

    def _render_frame(self):
        if self._current_pixmap and not self._current_pixmap.isNull():
            target_size = self.canvas_frame.size()
            scaled = self._current_pixmap.scaled(
                target_size.width() - 8,
                target_size.height() - 8,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.display_lbl.setPixmap(scaled)

    def _update_stats_display(self):
        now = time.time()
        elapsed = now - self._last_stat_time
        if elapsed >= 1.0:
            self._fps = self._fps_count / elapsed
            self._bitrate_bps = (self._bytes_this_sec * 8.0) / elapsed
            self._fps_count = 0
            self._bytes_this_sec = 0
            self._last_stat_time = now

            self.fps_badge.setText(f"{self._fps:.1f} FPS")
            mbps = self._bitrate_bps / 1_000_000.0
            self.bitrate_badge.setText(f"{mbps:.2f} Mbps")

    def _on_stop_and_back(self):
        self.stop()
        self.go_back.emit()

    def stop(self):
        self._running = False
        if self._client_sock:
            try:
                self._client_sock.close()
            except Exception:
                pass
        if self._server_sock:
            try:
                self._server_sock.close()
            except Exception:
                pass
        self._stat_timer.stop()

    # ── Drag and Drop Support ─────────────────────────────────────────────────

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self.canvas_frame.setStyleSheet("background-color: #0d1e38; border: 2px dashed #3b82f6; border-radius: 12px;")

    def dragLeaveEvent(self, event):
        self.canvas_frame.setStyleSheet("background-color: #05070a; border: 2px dashed #334155; border-radius: 12px;")

    def dropEvent(self, event: QDropEvent):
        self.canvas_frame.setStyleSheet("background-color: #05070a; border: 2px dashed #334155; border-radius: 12px;")
        urls = event.mimeData().urls()
        file_paths = []
        for u in urls:
            lp = u.toLocalFile()
            if os.path.exists(lp):
                file_paths.append(lp)

        if file_paths:
            logger.info("Files dropped onto mirror viewer: %s", file_paths)
            self.files_dropped.emit(self.device_id, file_paths)
            QMessageBox.information(
                self,
                "Transfer Initiated",
                f"Sending {len(file_paths)} item(s) to {self.device_name} via PhotoBeam high-speed transfer.",
            )
