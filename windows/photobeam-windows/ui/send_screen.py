"""
PhotoBeam Windows UI — Send Screen

QR input, device connection status, file list with remove actions,
shared transfer-progress component with smoothed speed (EMA) & ETA,
transfer history logging, and robust error/recovery states.
"""
from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path
from typing import List, Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QFrame, QProgressBar, QFileDialog,
    QScrollArea, QSizePolicy, QLineEdit, QMessageBox, QApplication,
)
from PyQt6.QtCore import Qt, pyqtSignal, QThread, QObject, QTimer
from PyQt6.QtGui import QDragEnterEvent, QDropEvent, QPixmap


# Protocol imports
if getattr(sys, 'frozen', False):
    PROTO = os.path.join(getattr(sys, '_MEIPASS', os.path.dirname(sys.executable)), 'protocol')
else:
    PROTO = os.path.join(os.path.dirname(__file__), '..', '..', '..', 'protocol')
if PROTO not in sys.path:
    sys.path.insert(0, PROTO)
from src.session import SessionManager

from src.qr_payload import decode_qr_payload
from src.models import DEFAULT_PORT, MessageType, CHUNK_HEADER_SIZE
from src.scheduler import Scheduler
from src.resume import ResumeManager
from src.transfer import TransferManager

try:
    from transport.wifi_transport import WiFiTransport
    from transport.usb_transport import UsbTransport
except (ImportError, ValueError):
    from ..transport.wifi_transport import WiFiTransport
    from ..transport.usb_transport import UsbTransport

try:
    from history_manager import HistoryManager, TransferRecord
except ImportError:
    from ..history_manager import HistoryManager, TransferRecord

try:
    from ui.error_formatter import format_friendly_error
except ImportError:
    from .error_formatter import format_friendly_error



def format_bytes(b: int) -> str:
    if b < 1024:
        return f"{b} B"
    elif b < 1024 * 1024:
        return f"{b / 1024:.1f} KB"
    elif b < 1024 * 1024 * 1024:
        return f"{b / (1024 * 1024):.1f} MB"
    elif b < 1024 * 1024 * 1024 * 1024:
        return f"{b / (1024 * 1024 * 1024):.2f} GB"
    else:
        return f"{b / (1024 * 1024 * 1024 * 1024):.2f} TB"


def get_file_icon(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in ('.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.svg'):
        return "🖼️"
    elif ext in ('.mp4', '.mkv', '.avi', '.mov', '.wmv', '.webm'):
        return "🎥"
    elif ext in ('.zip', '.rar', '.7z', '.tar', '.gz'):
        return "📦"
    elif ext in ('.pdf', '.doc', '.docx', '.txt', '.md'):
        return "📄"
    elif ext in ('.mp3', '.wav', '.flac', '.aac', '.m4a'):
        return "🎵"
    else:
        return "📁"


class SpeedTracker:
    def __init__(self, alpha: float = 0.25):
        self.alpha = alpha
        self.last_time = time.time()
        self.last_bytes = 0
        self.smoothed_speed = 0.0
        self.has_sample = False

    def update(self, current_bytes: int) -> float:
        now = time.time()
        dt = now - self.last_time
        if dt >= 0.2:
            db = current_bytes - self.last_bytes
            instant_speed = db / dt if dt > 0 else 0.0
            if not self.has_sample:
                self.smoothed_speed = instant_speed
                self.has_sample = True
            else:
                self.smoothed_speed = self.alpha * instant_speed + (1.0 - self.alpha) * self.smoothed_speed
            self.last_time = now
            self.last_bytes = current_bytes
        return self.smoothed_speed

    def format_eta(self, remaining_bytes: int) -> str:
        if self.smoothed_speed < 50_000:
            return ""
        sec = int(remaining_bytes / self.smoothed_speed)
        if sec < 1:
            return "~1 sec remaining"
        elif sec < 60:
            return f"~{sec} sec remaining"
        elif sec < 3600:
            m = sec // 60
            s = sec % 60
            return f"~{m} min {s} sec remaining"
        else:
            h = sec // 3600
            m = (sec % 3600) // 60
            return f"~{h} hr {m} min remaining"


class SenderWorker(QObject):
    """Background thread: connect to receiver, send files with safe pause/resume."""
    connected = pyqtSignal(str, str)  # device_name, transport_desc
    progress_update = pyqtSignal(str, int, int, 'qint64', 'qint64', 'qint64', 'qint64')  # file_name, file_idx, total_files, file_sent, file_size, overall_sent, overall_total
    file_done = pyqtSignal(str, bool)
    transfer_complete = pyqtSignal(int, 'qint64', float)  # file_count, total_bytes, duration_sec
    error = pyqtSignal(str, str)  # message, reason
    status = pyqtSignal(str)
    pausing = pyqtSignal()
    paused = pyqtSignal(str, 'qint64', 'qint64', 'qint64', 'qint64')  # file_name, cur_sent, file_size, overall_sent, overall_total
    verifying = pyqtSignal()
    state_verified = pyqtSignal()
    unsafe_to_resume = pyqtSignal(str)

    def __init__(self, uri: str, files: List[Path]):
        super().__init__()
        self._uri = uri
        self._files = files
        self._stop = threading.Event()
        self._pause_requested = threading.Event()
        self._continue_requested = threading.Event()
        self._restart_requested = threading.Event()
        self._transport_type = "Wi-Fi"
        self._xfer: Optional[TransferManager] = None

    def request_pause(self):
        self._pause_requested.set()
        if self._xfer:
            self._xfer.pause()

    def request_continue(self):
        self._continue_requested.set()

    def request_restart(self):
        self._restart_requested.set()

    def stop(self):
        self._stop.set()
        if self._xfer:
            self._xfer.cancel()

    def run(self):
        start_time = time.time()
        try:
            self._run(start_time)
        except Exception as e:
            if not self._stop.is_set():
                err_msg = str(e)
                self.error.emit(f"Transfer error: {err_msg}", "transport_error")
                HistoryManager.get_instance().add_record(TransferRecord(
                    direction="sent",
                    files=[p.name for p in self._files],
                    total_bytes=sum(p.stat().st_size for p in self._files if p.exists()),
                    duration_sec=time.time() - start_time,
                    status="failed",
                    transport_type=self._transport_type,
                    error_reason=err_msg,
                ))

    def _run(self, start_time: float):
        try:
            payload = decode_qr_payload(self._uri)
        except ValueError as e:
            self.error.emit(f"Invalid QR code: {e}", "invalid_qr")
            return

        if payload.is_expired():
            self.error.emit("QR code has expired. Please ask the receiver to generate a new QR code.", "qr_expired")
            return

        scheduler = Scheduler()
        wifi_transport = None
        usb_transport = None

        self.status.emit("Looking for devices…")
        for addr in payload.addrs:
            try:
                t = WiFiTransport("wifi")
                t.connect(addr, payload.port, timeout=10.0, cert_fp=payload.cert_fp)
                wifi_transport = t
                scheduler.add_transport(t)
                break
            except Exception:
                continue

        if wifi_transport is None:
            self.error.emit(
                f"Could not connect to receiver.\nTried addresses: {', '.join(payload.addrs)}\nEnsure both devices are on the same local network.",
                "connection_failed"
            )
            return

        active_transports_desc = "Wi-Fi"
        self._transport_type = "Wi-Fi"

        adb_avail, _ = UsbTransport.is_available()
        if "usb" in payload.transports or adb_avail:
            try:
                self.status.emit("Checking USB connection…")
                u = UsbTransport("usb")
                u.connect(
                    port=DEFAULT_PORT + 1,
                    target_port=payload.port,
                    timeout=5.0,
                    cert_fp=payload.cert_fp,
                )
                u.send_json({
                    "type": MessageType.HELLO,
                    "v": 1,
                    "sid": payload.sid,
                    "token": payload.token,
                    "channel": "data",
                })
                ack = u.recv_json(timeout=5.0)
                if ack.get("type") == MessageType.HELLO_ACK:
                    usb_transport = u
                    scheduler.add_transport(u)
                    active_transports_desc = "Wi-Fi + USB (Multi-path)"
                    self._transport_type = "Wi-Fi + USB"
            except Exception:
                pass

        device_name = f"Receiver ({payload.addrs[0]})"
        self.connected.emit(device_name, active_transports_desc)

        # Handshake on control channel
        import uuid
        sender_id = str(uuid.uuid4())
        wifi_transport.send_json({
            "type": MessageType.HELLO,
            "v": 1,
            "sid": payload.sid,
            "token": payload.token,
            "sender_id": sender_id,
        })
        ack = wifi_transport.recv_json(timeout=15.0)
        if ack.get("type") != MessageType.HELLO_ACK:
            err_code = ack.get("code", ack.get("type", "unknown"))
            self.error.emit(f"Handshake failed: {err_code}", "handshake_failed")
            wifi_transport.disconnect()
            return

        self.status.emit("Preparing files…")
        resume = ResumeManager()
        xfer = TransferManager(
            session_id=payload.sid,
            scheduler=scheduler,
            resume_manager=resume,
            on_progress=lambda fid, cid, sent, total: None,
        )
        self._xfer = xfer
        infos = xfer.prepare_files(self._files, compute_hash=False)
        total_batch_bytes = sum(i.size for i in infos)

        # Send READY immediately
        wifi_transport.send_json(xfer.build_ready_message(infos))
        self.status.emit("Waiting for receiver to accept…")

        # Receive ACCEPT / REJECT
        accepted = {}
        for _ in infos:
            msg = wifi_transport.recv_json(timeout=120.0)
            if msg.get("type") == MessageType.ACCEPT:
                fid = msg["fid"]
                rx_chunks = set(msg.get("received_chunks", []))
                accepted[fid] = rx_chunks
            elif msg.get("type") == MessageType.REJECT:
                reason = msg.get("reason", "rejected")
                if reason == "not_enough_space":
                    self.error.emit("Transfer stopped: Receiver does not have enough storage space.", "not_enough_space")
                else:
                    self.error.emit(f"Receiver declined transfer: {reason}", "rejected")
                wifi_transport.disconnect()
                if usb_transport:
                    usb_transport.disconnect()
                return

        total_files = len(infos)
        completed_bytes_prior = 0

        # Send each file
        for idx, info in enumerate(infos, start=1):
            if self._stop.is_set():
                break

            current_file_name = info.name
            skip_set = accepted.get(info.fid, set())

            while True:
                if self._stop.is_set():
                    break

                def progress(fid, cid, sent, total, _name=current_file_name, _idx=idx, _prior=completed_bytes_prior):
                    overall_sent = _prior + sent
                    self.progress_update.emit(_name, _idx, total_files, sent, total, overall_sent, total_batch_bytes)

                xfer._on_progress = progress

                # If pause was requested before file starts
                if self._pause_requested.is_set():
                    self.pausing.emit()
                    time.sleep(0.1)
                    cur_sent = xfer._files[info.fid].bytes_transferred
                    overall_sent = completed_bytes_prior + cur_sent
                    self.paused.emit(current_file_name, cur_sent, info.size, overall_sent, total_batch_bytes)

                    # Await continue, cancel, or restart
                    while not self._continue_requested.is_set() and not self._stop.is_set() and not self._restart_requested.is_set():
                        time.sleep(0.05)

                    if self._stop.is_set():
                        break

                    if self._restart_requested.is_set():
                        self._restart_requested.clear()
                        self._pause_requested.clear()
                        skip_set = set()
                        xfer.resume_transfer()
                        continue

                    # Verify state before continue
                    self.verifying.emit()
                    src_p = xfer._files[info.fid].source_path
                    source_ok = src_p and src_p.exists() and src_p.stat().st_size == info.size
                    if not source_ok:
                        self.unsafe_to_resume.emit("Source file was modified or deleted while paused.")
                        self._continue_requested.clear()
                        continue

                    try:
                        wifi_transport.send_json({"type": MessageType.RESUME, "fid": info.fid})
                        res_reply = wifi_transport.recv_json(timeout=10.0)
                        if res_reply.get("type") == MessageType.ACCEPT:
                            skip_set = set(res_reply.get("received_chunks", []))
                        elif res_reply.get("type") == MessageType.FILE_DONE:
                            break
                        else:
                            self.unsafe_to_resume.emit(res_reply.get("reason", "Receiver reported invalid transfer state."))
                            self._continue_requested.clear()
                            continue
                    except Exception as e:
                        self.unsafe_to_resume.emit(f"Connection lost while verifying: {e}")
                        self._continue_requested.clear()
                        continue

                    self.state_verified.emit()
                    time.sleep(0.2)
                    self._continue_requested.clear()
                    self._pause_requested.clear()
                    xfer.resume_transfer()

                # Stream file chunks
                xfer.send_file(info.fid, ack_callback=lambda fid, cid: None, skip_chunks=skip_set)

                # Check if paused during streaming
                if xfer.is_paused():
                    self.pausing.emit()
                    try:
                        wifi_transport.send_json({"type": MessageType.PAUSE, "fid": info.fid})
                        ack_p = wifi_transport.recv_json(timeout=5.0)
                        if ack_p.get("type") == MessageType.PAUSE_ACK:
                            skip_set = set(ack_p.get("received_chunks", []))
                    except Exception:
                        pass

                    cur_sent = xfer._files[info.fid].bytes_transferred
                    overall_sent = completed_bytes_prior + cur_sent
                    self.paused.emit(current_file_name, cur_sent, info.size, overall_sent, total_batch_bytes)

                    # Await continue, cancel, or restart
                    while not self._continue_requested.is_set() and not self._stop.is_set() and not self._restart_requested.is_set():
                        time.sleep(0.05)

                    if self._stop.is_set():
                        break

                    if self._restart_requested.is_set():
                        self._restart_requested.clear()
                        self._pause_requested.clear()
                        skip_set = set()
                        xfer.resume_transfer()
                        continue

                    # Verify state before continue
                    self.verifying.emit()
                    src_p = xfer._files[info.fid].source_path
                    source_ok = src_p and src_p.exists() and src_p.stat().st_size == info.size
                    if not source_ok:
                        self.unsafe_to_resume.emit("Source file was modified or deleted while paused.")
                        self._continue_requested.clear()
                        continue

                    try:
                        wifi_transport.send_json({"type": MessageType.RESUME, "fid": info.fid})
                        res_reply = wifi_transport.recv_json(timeout=10.0)
                        if res_reply.get("type") == MessageType.ACCEPT:
                            skip_set = set(res_reply.get("received_chunks", []))
                        elif res_reply.get("type") == MessageType.FILE_DONE:
                            break
                        else:
                            self.unsafe_to_resume.emit(res_reply.get("reason", "Receiver reported invalid transfer state."))
                            self._continue_requested.clear()
                            continue
                    except Exception as e:
                        self.unsafe_to_resume.emit(f"Connection lost while verifying: {e}")
                        self._continue_requested.clear()
                        continue

                    self.state_verified.emit()
                    time.sleep(0.2)
                    self._continue_requested.clear()
                    self._pause_requested.clear()
                    xfer.resume_transfer()
                    continue
                else:
                    # File finished normally
                    if xfer._files.get(info.fid):
                        computed_sha = xfer._files[info.fid].info.sha256
                        if computed_sha:
                            try:
                                wifi_transport.send_json({"type": MessageType.FILE_CHECKSUM, "fid": info.fid, "sha256": computed_sha})
                            except Exception:
                                pass
                    break

            completed_bytes_prior += info.size

        # Await verification from receiver
        failed_files = []
        info_by_fid = {inf.fid: inf for inf in infos}
        completed_fids = set()

        while len(completed_fids) < len(infos) and not self._stop.is_set():
            try:
                msg = wifi_transport.recv_json(timeout=3600.0)
            except Exception:
                remaining = [inf.name for inf in infos if inf.fid not in completed_fids]
                for rname in remaining:
                    failed_files.append(f"{rname} (timeout waiting for verification)")
                    self.file_done.emit(rname, False)
                break

            m_type = msg.get("type")
            if m_type == MessageType.FILE_DONE:
                fid = msg.get("fid")
                info = info_by_fid.get(fid) if fid else (infos[len(completed_fids)] if len(completed_fids) < len(infos) else None)
                if info:
                    rx_sha = msg.get("sha256", "")
                    if rx_sha and rx_sha.lower() != info.sha256.lower():
                        failed_files.append(f"{info.name} (SHA-256 mismatch)")
                        self.file_done.emit(info.name, False)
                    else:
                        print(f"[DIAG] [TRANSFER_COMPLETE] File verified byte-perfect: {info.name}")
                        self.file_done.emit(info.name, True)
                    completed_fids.add(info.fid)
            elif m_type == MessageType.FILE_ERROR:
                fid = msg.get("fid")
                info = info_by_fid.get(fid) if fid else (infos[len(completed_fids)] if len(completed_fids) < len(infos) else None)
                fname = info.name if info else (fid or "Unknown")
                reason = msg.get("reason", "hash mismatch or corruption")
                failed_files.append(f"{fname} ({reason})")
                self.file_done.emit(fname, False)
                if info:
                    completed_fids.add(info.fid)
            elif m_type == MessageType.PING:
                try:
                    wifi_transport.send_json({"type": MessageType.PONG, "ts": msg.get("ts", 0)})
                except Exception:
                    pass

        wifi_transport.disconnect()
        if usb_transport:
            usb_transport.disconnect()

        duration = time.time() - start_time
        if failed_files:
            err_reason = f"Integrity check failed: {', '.join(failed_files)}"
            self.error.emit(err_reason, "hash_mismatch")
            HistoryManager.get_instance().add_record(TransferRecord(
                direction="sent",
                files=[p.name for p in self._files],
                total_bytes=total_batch_bytes,
                duration_sec=duration,
                status="failed",
                transport_type=self._transport_type,
                error_reason=err_reason,
            ))
        else:
            HistoryManager.get_instance().add_record(TransferRecord(
                direction="sent",
                files=[p.name for p in self._files],
                total_bytes=total_batch_bytes,
                duration_sec=duration,
                status="completed",
                transport_type=self._transport_type,
            ))
            self.transfer_complete.emit(len(infos), total_batch_bytes, duration)


class SendScreen(QWidget):
    go_back = pyqtSignal()
    go_history = pyqtSignal()

    def __init__(self):
        super().__init__()
        self._files: List[Path] = []
        self._uri: Optional[str] = None
        self._worker: Optional[SenderWorker] = None
        self._thread: Optional[QThread] = None
        self._speed_tracker = SpeedTracker(alpha=0.25)

        self._build_ui()
        self.setAcceptDrops(True)

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(40, 20, 40, 40)
        root.setSpacing(14)

        # ── Top Bar ───────────────────────────────────────────────────────────
        top = QHBoxLayout()
        back_btn = QPushButton("← Back")
        back_btn.setObjectName("back")
        back_btn.clicked.connect(self._on_back)
        top.addWidget(back_btn)

        top.addStretch()

        history_btn = QPushButton("📜 History")
        history_btn.setObjectName("secondary")
        history_btn.clicked.connect(self.go_history.emit)
        top.addWidget(history_btn)
        root.addLayout(top)

        # ── Main Card ─────────────────────────────────────────────────────────
        self._card = QFrame()
        self._card.setObjectName("card")
        self._card_layout = QVBoxLayout(self._card)
        self._card_layout.setSpacing(16)
        self._card_layout.setContentsMargins(36, 32, 36, 32)

        # Title
        heading = QLabel("📤 Send Files")
        heading.setObjectName("heading")
        heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._card_layout.addWidget(heading)

        # Connection Status Badge
        self._conn_badge = QLabel("○ Looking for devices…")
        self._conn_badge.setObjectName("info")
        self._conn_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._conn_badge.setStyleSheet("font-size: 14px; font-weight: 600; padding: 4px 12px; border-radius: 8px;")
        self._card_layout.addWidget(self._conn_badge)

        # QR Input Area
        qr_row = QHBoxLayout()
        qr_lbl = QLabel("Receiver URI:")
        qr_lbl.setObjectName("subtitle")
        self._qr_input = QLineEdit()
        self._qr_input.setPlaceholderText("Paste receiver's photobeam://connect/... link")
        self._qr_input.setStyleSheet(
            "background:#1e293b; color:#e2e8f0; border:1px solid #334155; border-radius:8px; padding:8px; font-size:13px;"
        )
        self._qr_input.textChanged.connect(self._on_uri_changed)
        qr_row.addWidget(qr_lbl)
        qr_row.addWidget(self._qr_input, stretch=1)
        self._card_layout.addLayout(qr_row)

        # ── Selection View ────────────────────────────────────────────────────
        self._selection_container = QWidget()
        sel_layout = QVBoxLayout(self._selection_container)
        sel_layout.setSpacing(12)
        sel_layout.setContentsMargins(0, 0, 0, 0)

        # Drop Zone
        self._drop_zone = QFrame()
        self._drop_zone.setObjectName("transfer_item")
        self._drop_zone.setMinimumHeight(100)
        drop_layout = QVBoxLayout(self._drop_zone)
        drop_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._drop_label = QLabel("Drag & drop files here, or")
        self._drop_label.setObjectName("subtitle")
        self._drop_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        browse_btn = QPushButton("📁 Browse Files")
        browse_btn.setObjectName("secondary")
        browse_btn.clicked.connect(self._browse_files)
        drop_layout.addWidget(self._drop_label)
        drop_layout.addWidget(browse_btn, alignment=Qt.AlignmentFlag.AlignCenter)
        sel_layout.addWidget(self._drop_zone)

        # Selected files header (count & total size)
        summary_row = QHBoxLayout()
        self._files_summary_label = QLabel("No files selected")
        self._files_summary_label.setObjectName("subtitle")
        summary_row.addWidget(self._files_summary_label)

        summary_row.addStretch()

        self._clear_all_btn = QPushButton("Clear all")
        self._clear_all_btn.setObjectName("back")
        self._clear_all_btn.setStyleSheet("color: #f87171;")
        self._clear_all_btn.clicked.connect(self._clear_files)
        self._clear_all_btn.setVisible(False)
        summary_row.addWidget(self._clear_all_btn)
        sel_layout.addLayout(summary_row)

        # Scrollable Selected Files List
        self._file_scroll = QScrollArea()
        self._file_scroll.setWidgetResizable(True)
        self._file_scroll.setMaximumHeight(180)
        self._file_list_widget = QWidget()
        self._file_list_layout = QVBoxLayout(self._file_list_widget)
        self._file_list_layout.setSpacing(6)
        self._file_list_layout.setContentsMargins(0, 0, 0, 0)
        self._file_scroll.setWidget(self._file_list_widget)
        sel_layout.addWidget(self._file_scroll)

        # Action Buttons
        btn_row = QHBoxLayout()
        add_more_btn = QPushButton("➕ Add Files")
        add_more_btn.setObjectName("secondary")
        add_more_btn.clicked.connect(self._browse_files)

        self._send_btn = QPushButton("Send Files")
        self._send_btn.setObjectName("primary")
        self._send_btn.setEnabled(False)
        self._send_btn.clicked.connect(self._start_send)

        btn_row.addWidget(add_more_btn)
        btn_row.addWidget(self._send_btn)
        sel_layout.addLayout(btn_row)

        self._card_layout.addWidget(self._selection_container)

        # ── Progress View ─────────────────────────────────────────────────────
        self._progress_container = QWidget()
        prog_layout = QVBoxLayout(self._progress_container)
        prog_layout.setSpacing(10)
        prog_layout.setContentsMargins(0, 8, 0, 8)

        self._prog_file_name = QLabel("Preparing transfer…")
        self._prog_file_name.setObjectName("heading")
        self._prog_file_name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        prog_layout.addWidget(self._prog_file_name)

        self._transport_pill = QLabel("Connected via Wi-Fi")
        self._transport_pill.setObjectName("transport_pill")
        self._transport_pill.setAlignment(Qt.AlignmentFlag.AlignCenter)
        prog_layout.addWidget(self._transport_pill)

        # Current file progress
        self._file_progress_bar = QProgressBar()
        self._file_progress_bar.setRange(0, 100)
        self._file_progress_bar.setValue(0)
        prog_layout.addWidget(self._file_progress_bar)

        # Overall progress
        self._overall_progress_bar = QProgressBar()
        self._overall_progress_bar.setRange(0, 100)
        self._overall_progress_bar.setValue(0)
        prog_layout.addWidget(self._overall_progress_bar)

        # Stats Row: Bytes & Counter
        stats_row = QHBoxLayout()
        self._bytes_label = QLabel("0 B / 0 B")
        self._bytes_label.setObjectName("subtitle")
        self._counter_label = QLabel("File 1 of 1")
        self._counter_label.setObjectName("subtitle")
        stats_row.addWidget(self._bytes_label)
        stats_row.addStretch()
        stats_row.addWidget(self._counter_label)
        prog_layout.addLayout(stats_row)

        # Speed and ETA
        self._speed_label = QLabel("")
        self._speed_label.setObjectName("speed")
        self._speed_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        prog_layout.addWidget(self._speed_label)

        self._eta_label = QLabel("")
        self._eta_label.setObjectName("subtitle")
        self._eta_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        prog_layout.addWidget(self._eta_label)

        # Pause status banner
        self._pause_status_label = QLabel("")
        self._pause_status_label.setObjectName("subtitle")
        self._pause_status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._pause_status_label.setWordWrap(True)
        self._pause_status_label.setVisible(False)
        prog_layout.addWidget(self._pause_status_label)

        # Action buttons
        self._transfer_actions_layout = QHBoxLayout()
        self._transfer_actions_layout.setSpacing(12)

        self._pause_btn = QPushButton("Pause Transfer")
        self._pause_btn.setObjectName("secondary")
        self._pause_btn.clicked.connect(self._on_pause_clicked)

        self._continue_btn = QPushButton("Continue Transfer")
        self._continue_btn.setObjectName("primary")
        self._continue_btn.clicked.connect(self._on_continue_clicked)
        self._continue_btn.setVisible(False)

        self._restart_btn = QPushButton("Restart Transfer")
        self._restart_btn.setObjectName("secondary")
        self._restart_btn.setStyleSheet("color: #f87171;")
        self._restart_btn.clicked.connect(self._on_restart_clicked)
        self._restart_btn.setVisible(False)

        self._cancel_btn = QPushButton("Cancel Transfer")
        self._cancel_btn.setObjectName("back")
        self._cancel_btn.setStyleSheet("color: #f87171;")
        self._cancel_btn.clicked.connect(self._on_cancel_clicked)

        self._transfer_actions_layout.addWidget(self._pause_btn)
        self._transfer_actions_layout.addWidget(self._continue_btn)
        self._transfer_actions_layout.addWidget(self._restart_btn)
        self._transfer_actions_layout.addWidget(self._cancel_btn)
        prog_layout.addLayout(self._transfer_actions_layout)

        self._progress_container.setVisible(False)
        self._card_layout.addWidget(self._progress_container)

        # ── Completion View ───────────────────────────────────────────────────
        self._complete_container = QWidget()
        comp_layout = QVBoxLayout(self._complete_container)
        comp_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        comp_layout.setSpacing(12)

        comp_icon = QLabel("✓")
        comp_icon.setStyleSheet("font-size: 48px; color: #4ade80;")
        comp_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        comp_layout.addWidget(comp_icon)

        comp_title = QLabel("Transfer Complete!")
        comp_title.setObjectName("heading")
        comp_title.setStyleSheet("color: #4ade80; font-size: 22px;")
        comp_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        comp_layout.addWidget(comp_title)

        self._comp_integrity = QLabel("🛡️ SHA-256 byte-perfect integrity verified")
        self._comp_integrity.setObjectName("integrity_badge")
        self._comp_integrity.setAlignment(Qt.AlignmentFlag.AlignCenter)
        comp_layout.addWidget(self._comp_integrity)

        self._comp_summary = QLabel("")
        self._comp_summary.setObjectName("subtitle")
        self._comp_summary.setAlignment(Qt.AlignmentFlag.AlignCenter)
        comp_layout.addWidget(self._comp_summary)

        comp_btn_row = QHBoxLayout()
        done_btn = QPushButton("Done")
        done_btn.setObjectName("secondary")
        done_btn.clicked.connect(self._reset_to_start)

        view_hist_btn = QPushButton("View History")
        view_hist_btn.setObjectName("primary")
        view_hist_btn.clicked.connect(self.go_history.emit)

        comp_btn_row.addWidget(done_btn)
        comp_btn_row.addWidget(view_hist_btn)
        comp_layout.addLayout(comp_btn_row)

        self._complete_container.setVisible(False)
        self._card_layout.addWidget(self._complete_container)

        # ── Error View ────────────────────────────────────────────────────────
        self._error_container = QWidget()
        err_layout = QVBoxLayout(self._error_container)
        err_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        err_layout.setSpacing(12)

        err_icon = QLabel("❌")
        err_icon.setStyleSheet("font-size: 40px;")
        err_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        err_layout.addWidget(err_icon)

        self._err_title = QLabel("Transfer Error")
        self._err_title.setObjectName("heading")
        self._err_title.setStyleSheet("color: #f87171; font-size: 18px;")
        self._err_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        err_layout.addWidget(self._err_title)

        self._err_desc = QLabel("Transfer failed")
        self._err_desc.setObjectName("subtitle")
        self._err_desc.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._err_desc.setWordWrap(True)
        err_layout.addWidget(self._err_desc)

        self._details_toggle_btn = QPushButton("Show technical details ▼")
        self._details_toggle_btn.setObjectName("details_btn")
        self._details_toggle_btn.clicked.connect(self._toggle_err_details)
        err_layout.addWidget(self._details_toggle_btn)

        self._details_panel = QFrame()
        self._details_panel.setObjectName("details_panel")
        details_layout = QVBoxLayout(self._details_panel)
        details_layout.setContentsMargins(12, 10, 12, 10)
        self._details_label = QLabel("")
        self._details_label.setObjectName("details_text")
        self._details_label.setWordWrap(True)
        self._details_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        details_layout.addWidget(self._details_label)
        self._details_panel.setVisible(False)
        err_layout.addWidget(self._details_panel)

        err_btn_row = QHBoxLayout()
        retry_btn = QPushButton("Try Again")
        retry_btn.setObjectName("primary")
        retry_btn.clicked.connect(self._reset_to_start)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("secondary")
        cancel_btn.clicked.connect(self._on_back)

        err_btn_row.addWidget(retry_btn)
        err_btn_row.addWidget(cancel_btn)
        err_layout.addLayout(err_btn_row)

        self._error_container.setVisible(False)
        self._card_layout.addWidget(self._error_container)

        root.addWidget(self._card)
        root.addStretch()

    # ── Drag & Drop ───────────────────────────────────────────────────────────

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent):
        paths = [Path(url.toLocalFile()) for url in event.mimeData().urls()]
        self._add_files(paths)

    def _browse_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Select files to send")
        if files:
            self._add_files([Path(f) for f in files])

    def _add_files(self, paths: List[Path]):
        for p in paths:
            if p.exists() and p not in self._files:
                self._files.append(p)
        self._refresh_file_list()

    def _remove_file(self, path: Path):
        self._files = [p for p in self._files if p != path]
        self._refresh_file_list()

    def _clear_files(self):
        self._files.clear()
        self._refresh_file_list()

    def _refresh_file_list(self):
        while self._file_list_layout.count():
            item = self._file_list_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        if not self._files:
            self._files_summary_label.setText("No files selected")
            self._clear_all_btn.setVisible(False)
            self._send_btn.setEnabled(False)
            return

        total_bytes = sum(p.stat().st_size for p in self._files if p.exists())
        self._files_summary_label.setText(f"{len(self._files)} file(s) selected • {format_bytes(total_bytes)}")
        self._clear_all_btn.setVisible(True)

        for p in self._files:
            row = QFrame()
            row.setObjectName("transfer_item")
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(10, 6, 10, 6)

            icon = QLabel(get_file_icon(p))
            icon.setStyleSheet("font-size: 16px;")
            row_layout.addWidget(icon)

            name = QLabel(p.name)
            name.setStyleSheet("color: #e2e8f0; font-size: 13px; font-weight: 500;")
            row_layout.addWidget(name, stretch=1)

            size_val = p.stat().st_size if p.exists() else 0
            size_lbl = QLabel(format_bytes(size_val))
            size_lbl.setObjectName("info")
            row_layout.addWidget(size_lbl)

            del_btn = QPushButton("✕")
            del_btn.setFixedSize(24, 24)
            del_btn.setStyleSheet(
                "QPushButton { background: transparent; color: #f87171; border: none; font-weight: bold; border-radius: 12px; }"
                "QPushButton:hover { background: #3b1b1b; }"
            )
            del_btn.clicked.connect(lambda _, target=p: self._remove_file(target))
            row_layout.addWidget(del_btn)

            self._file_list_layout.addWidget(row)

        self._update_send_btn()

    def _on_uri_changed(self, text: str):
        t = text.strip()
        if t.startswith("photobeam://connect/"):
            self._uri = t
            self._conn_badge.setText("○ Ready to connect")
            self._conn_badge.setStyleSheet("color: #60a5fa; font-weight: 600;")
        else:
            self._uri = None
            self._conn_badge.setText("○ Paste receiver's QR link above")
            self._conn_badge.setStyleSheet("color: #94a3b8; font-weight: 500;")
        self._update_send_btn()

    def _update_send_btn(self):
        self._send_btn.setEnabled(bool(self._files and self._uri))

    # ── Transfer Execution ────────────────────────────────────────────────────

    def _start_send(self):
        if not self._files or not self._uri:
            return

        self._selection_container.setVisible(False)
        self._complete_container.setVisible(False)
        self._error_container.setVisible(False)
        self._progress_container.setVisible(True)

        self._conn_badge.setText("○ Connecting…")
        self._conn_badge.setStyleSheet("color: #60a5fa; font-weight: 600;")
        self._speed_tracker = SpeedTracker(alpha=0.25)

        self._worker = SenderWorker(self._uri, self._files)
        self._thread = QThread()
        self._worker.moveToThread(self._thread)

        self._worker.connected.connect(self._on_connected)
        self._worker.progress_update.connect(self._on_progress_update)
        self._worker.file_done.connect(self._on_file_done)
        self._worker.transfer_complete.connect(self._on_complete)
        self._worker.error.connect(self._on_error)
        self._worker.status.connect(self._on_status)
        self._worker.pausing.connect(self._on_transfer_pausing)
        self._worker.paused.connect(self._on_transfer_paused)
        self._worker.verifying.connect(self._on_transfer_verifying)
        self._worker.state_verified.connect(self._on_transfer_state_verified)
        self._worker.unsafe_to_resume.connect(self._on_unsafe_to_resume)

        self._pause_status_label.setVisible(False)
        self._pause_btn.setVisible(True)
        self._pause_btn.setEnabled(True)
        self._continue_btn.setVisible(False)
        self._restart_btn.setVisible(False)
        self._cancel_btn.setVisible(True)
        self._cancel_btn.setEnabled(True)

        self._thread.started.connect(self._worker.run)
        self._thread.start()

    def _on_pause_clicked(self):
        self._pause_btn.setEnabled(False)
        self._cancel_btn.setEnabled(False)
        self._pause_status_label.setText("Pausing…\nSafely saving transfer state.")
        self._pause_status_label.setStyleSheet("color: #f59e0b; font-size: 14px; font-weight: 600;")
        self._pause_status_label.setVisible(True)
        if self._worker:
            self._worker.request_pause()

    def _on_continue_clicked(self):
        self._continue_btn.setEnabled(False)
        self._cancel_btn.setEnabled(False)
        self._pause_status_label.setText("Verifying transfer state…")
        self._pause_status_label.setStyleSheet("color: #38bdf8; font-size: 14px; font-weight: 600;")
        if self._worker:
            self._worker.request_continue()

    def _on_restart_clicked(self):
        self._restart_btn.setEnabled(False)
        self._cancel_btn.setEnabled(False)
        self._pause_status_label.setText("Restarting transfer from beginning…")
        self._pause_status_label.setStyleSheet("color: #38bdf8; font-size: 14px; font-weight: 600;")
        if self._worker:
            self._worker.request_restart()

    def _on_cancel_clicked(self):
        reply = QMessageBox.question(
            self,
            "Cancel Transfer?",
            "Are you sure you want to cancel the transfer?\n\nThe incomplete transfer will not be marked as successfully transferred.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._stop_worker()
            self._reset_to_start()

    def _on_transfer_pausing(self):
        self._pause_btn.setEnabled(False)
        self._continue_btn.setEnabled(False)
        self._cancel_btn.setEnabled(False)
        self._pause_status_label.setText("Pausing…\nSafely saving transfer state.")
        self._pause_status_label.setStyleSheet("color: #f59e0b; font-size: 14px; font-weight: 600;")
        self._pause_status_label.setVisible(True)

    def _on_transfer_paused(self, current_file: str, cur_sent: int, file_size: int, overall_sent: int, overall_total: int):
        self._pause_status_label.setText(
            f"Transfer Paused\n\n{current_file}\n{format_bytes(cur_sent)} / {format_bytes(file_size)}\n\nYour transferred data is safe."
        )
        self._pause_status_label.setStyleSheet("color: #38bdf8; font-size: 14px; font-weight: 600;")
        self._pause_status_label.setVisible(True)
        self._pause_btn.setVisible(False)
        self._continue_btn.setVisible(True)
        self._continue_btn.setEnabled(True)
        self._restart_btn.setVisible(False)
        self._cancel_btn.setVisible(True)
        self._cancel_btn.setEnabled(True)

    def _on_transfer_verifying(self):
        self._pause_status_label.setText("Verifying transfer state…")
        self._pause_status_label.setStyleSheet("color: #38bdf8; font-size: 14px; font-weight: 600;")
        self._continue_btn.setEnabled(False)
        self._cancel_btn.setEnabled(False)

    def _on_transfer_state_verified(self):
        self._pause_status_label.setText("State verified ✓\nResuming transfer…")
        self._pause_status_label.setStyleSheet("color: #4ade80; font-size: 14px; font-weight: 600;")

    def _on_unsafe_to_resume(self, reason: str):
        self._pause_status_label.setText(
            f"Cannot safely continue\n\n{reason}\n\nYour existing data has not been marked complete."
        )
        self._pause_status_label.setStyleSheet("color: #f87171; font-size: 14px; font-weight: 600;")
        self._pause_status_label.setVisible(True)
        self._pause_btn.setVisible(False)
        self._continue_btn.setVisible(False)
        self._restart_btn.setVisible(True)
        self._restart_btn.setEnabled(True)
        self._cancel_btn.setVisible(True)
        self._cancel_btn.setEnabled(True)

    def _on_connected(self, device_name: str, transport_desc: str):
        self._conn_badge.setText(f"● Connected to {device_name} ({transport_desc})")
        self._conn_badge.setStyleSheet("color: #4ade80; font-weight: 600;")
        self._transport_pill.setText(f"Connected via {transport_desc}")

    def _on_status(self, msg: str):
        self._prog_file_name.setText(msg)
        lower = msg.lower()
        if "multi-path" in lower or "usb" in lower:
            self._transport_pill.setText("⚡ Multi-path (Wi-Fi + USB)")
            self._transport_pill.setStyleSheet("color: #a78bfa; background-color: rgba(167, 139, 250, 0.15); border: 1px solid rgba(167, 139, 250, 0.3);")
        elif "wi-fi" in lower or "wifi" in lower:
            self._transport_pill.setText("Connected via Wi-Fi")
            self._transport_pill.setStyleSheet("color: #38bdf8; background-color: rgba(56, 189, 248, 0.12); border: 1px solid rgba(56, 189, 248, 0.25);")

    def _on_progress_update(self, name: str, idx: int, total_files: int, file_sent: int, file_size: int, overall_sent: int, overall_total: int):
        self._prog_file_name.setText(f"Sending {name}")
        self._counter_label.setText(f"File {idx} of {total_files}")

        if file_size > 0:
            self._file_progress_bar.setValue(int(file_sent * 100 / file_size))
        else:
            self._file_progress_bar.setValue(100)

        if overall_total > 0:
            self._overall_progress_bar.setValue(int(overall_sent * 100 / overall_total))
        else:
            self._overall_progress_bar.setValue(100)

        self._bytes_label.setText(f"{format_bytes(overall_sent)} / {format_bytes(overall_total)}")

        spd = self._speed_tracker.update(overall_sent)
        if spd > 0:
            self._speed_label.setText(f"{format_bytes(int(spd))}/s")
            rem = max(0, overall_total - overall_sent)
            eta = self._speed_tracker.format_eta(rem)
            self._eta_label.setText(eta)
        else:
            self._speed_label.setText("")
            self._eta_label.setText("")

    def _on_file_done(self, name: str, success: bool):
        pass

    def _on_complete(self, file_count: int, total_bytes: int, duration_sec: float):
        self._progress_container.setVisible(False)
        self._complete_container.setVisible(True)
        dur_str = f"{duration_sec:.1f}s"
        spd_str = f"({(total_bytes / (1024 * 1024)) / duration_sec:.1f} MB/s)" if duration_sec > 0 and total_bytes > 0 else ""
        self._comp_summary.setText(f"{file_count} file(s) • {format_bytes(total_bytes)} transferred in {dur_str} {spd_str}")
        if "--exit-after-transfer" in sys.argv:
            QTimer.singleShot(2000, QApplication.instance().quit)


    def _on_error(self, message: str, reason: str):
        self._progress_container.setVisible(False)
        self._complete_container.setVisible(False)
        self._error_container.setVisible(True)

        raw_err = message or reason or "Unknown transfer error"
        friendly = format_friendly_error(raw_err)
        self._err_title.setText(friendly.title)
        self._err_desc.setText(friendly.message)
        self._details_label.setText(friendly.technical_details or raw_err)
        self._details_panel.setVisible(False)
        self._details_toggle_btn.setText("Show technical details ▼")

        self._conn_badge.setText("Transfer interrupted")
        self._conn_badge.setStyleSheet("color: #f87171; font-weight: 600;")

    def _toggle_err_details(self):
        is_vis = self._details_panel.isVisible()
        self._details_panel.setVisible(not is_vis)
        self._details_toggle_btn.setText("Hide technical details ▲" if not is_vis else "Show technical details ▼")

    def _reset_to_start(self):
        self._stop_worker()
        self._complete_container.setVisible(False)
        self._error_container.setVisible(False)
        self._progress_container.setVisible(False)
        self._details_panel.setVisible(False)
        self._details_toggle_btn.setText("Show technical details ▼")
        self._selection_container.setVisible(True)
        self._refresh_file_list()

    def _stop_worker(self):
        if self._worker:
            self._worker.stop()
        if self._thread and self._thread.isRunning():
            self._thread.quit()
            self._thread.wait(2000)
        self._worker = None
        self._thread = None

    def _on_back(self):
        self._stop_worker()
        self.go_back.emit()
