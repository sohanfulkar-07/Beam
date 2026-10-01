"""
PhotoBeam Windows UI — Receive Screen

Generates and displays QR code, awaits sender connection,
prompts user to accept incoming transfer batches, verifies storage space,
displays shared progress with smoothed speed (EMA) & ETA,
logs transfer history, and ensures robust integrity verification.
"""
from __future__ import annotations

import os
import shutil
import sys
import threading
import time
from pathlib import Path
from typing import List, Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QFrame, QProgressBar, QFileDialog, QScrollArea,
    QSpacerItem, QSizePolicy, QMessageBox, QApplication,
)
from PyQt6.QtCore import Qt, pyqtSignal, QThread, QObject, QTimer, QUrl
from PyQt6.QtGui import QPixmap, QImage, QDesktopServices

# Protocol imports
if getattr(sys, 'frozen', False):
    PROTO = os.path.join(getattr(sys, '_MEIPASS', os.path.dirname(sys.executable)), 'protocol')
else:
    PROTO = os.path.join(os.path.dirname(__file__), '..', '..', '..', 'protocol')
if PROTO not in sys.path:
    sys.path.insert(0, PROTO)
from src.session import SessionManager

from src.qr_payload import encode_qr_payload, generate_qr_png_bytes
from src.models import DEFAULT_PORT, MessageType, TransferInfo, ChunkFrame, CHUNK_HEADER_SIZE, CHUNK_MAGIC, TransferState

try:
    from transport.tls_utils import generate_session_cert, get_local_addresses
    from transport.wifi_transport import WiFiServer, WiFiTransport
    from transport.usb_transport import (
        UsbTransport, find_adb, setup_adb_reverse, teardown_adb_reverse, USB_TUNNEL_PORT
    )
except (ImportError, ValueError):
    from ..transport.tls_utils import generate_session_cert, get_local_addresses
    from ..transport.wifi_transport import WiFiServer, WiFiTransport
    from ..transport.usb_transport import (
        UsbTransport, find_adb, setup_adb_reverse, teardown_adb_reverse, USB_TUNNEL_PORT
    )

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


class ReceiverWorker(QObject):
    """Background thread: TLS server + file reception."""
    qr_ready = pyqtSignal(bytes, str)
    connected = pyqtSignal(str)
    incoming_prompt = pyqtSignal(int, 'qint64')  # file_count, total_bytes
    progress_update = pyqtSignal(str, int, int, 'qint64', 'qint64', 'qint64', 'qint64')  # file_name, file_idx, total_files, file_rx, file_size, overall_rx, overall_total
    file_done = pyqtSignal(str, bool)
    transfer_complete = pyqtSignal(int, 'qint64', float)  # count, bytes, duration
    error = pyqtSignal(str)
    status = pyqtSignal(str)
    pausing = pyqtSignal()
    paused = pyqtSignal(str, 'qint64', 'qint64', 'qint64', 'qint64')  # file_name, file_rx, file_size, overall_rx, overall_total
    verifying = pyqtSignal()
    state_verified = pyqtSignal()

    def __init__(self, dest_dir: Path, session_mgr: SessionManager):
        super().__init__()
        self._dest_dir = dest_dir
        self._session_mgr = session_mgr
        self._stop = threading.Event()
        self._server: Optional[WiFiServer] = None
        self._user_decision = threading.Event()
        self._user_accepted = False
        self._transport_type = "Wi-Fi"
        self._xfer = None
        self._current_info = None
        self._current_rx = 0
        self._overall_rx = 0
        self._total_batch_bytes = 0
        self._active_transports = []
        self._active_lock = threading.Lock()

    def get_transport_speeds(self) -> dict:
        """Return dict with transport throughput in bps: {'wifi': bps, 'usb': bps, 'total': bps}."""
        speeds = {"wifi": 0.0, "usb": 0.0, "total": 0.0}
        with self._active_lock:
            for t in self._active_transports:
                try:
                    bps = t.throughput_bps()
                except Exception:
                    bps = 0.0
                speeds["total"] += bps
                tid = getattr(t, "transport_id", "").lower()
                if "usb" in tid or "127.0.0.1" in tid:
                    speeds["usb"] += bps
                else:
                    speeds["wifi"] += bps
        return speeds

    def accept_transfer(self):
        self._user_accepted = True
        self._user_decision.set()

    def reject_transfer(self):
        self._user_accepted = False
        self._user_decision.set()

    def request_pause(self):
        if self._xfer:
            self._xfer.pause()
        name = self._current_info.name if self._current_info else "Transfer"
        size = self._current_info.size if self._current_info else 0
        self.paused.emit(name, self._current_rx, size, self._overall_rx, self._total_batch_bytes)

    def request_continue(self):
        if self._xfer:
            self._xfer.resume_transfer()
        self.state_verified.emit()

    def stop(self):
        self._stop.set()
        self._user_decision.set()
        if self._xfer:
            self._xfer.cancel()
        if self._server:
            try:
                self._server.stop()
            except Exception:
                pass

    def run(self):
        start_time = time.time()
        try:
            self._run(start_time)
        except Exception as e:
            if not self._stop.is_set():
                import traceback
                tb = traceback.format_exc()
                try:
                    log_file = Path.home() / ".photobeam" / "last_error.log"
                    log_file.parent.mkdir(parents=True, exist_ok=True)
                    log_file.write_text(f"{str(e)}\n\n{tb}", encoding="utf-8")
                except Exception:
                    pass
                err_msg = str(e)
                self.error.emit(err_msg)

    def _run(self, start_time: float):
        from src.storage import StorageManager
        from src.resume import ResumeManager
        from src.scheduler import Scheduler
        from src.transfer import TransferManager

        self.status.emit("Generating secure session…")
        cert_pem, key_pem, cert_fp = generate_session_cert()
        addrs = get_local_addresses()

        adb_avail, _ = UsbTransport.is_available()
        transports = ["wifi"]
        adb_path = find_adb() if adb_avail else None
        adb_reverse_active = False
        if adb_avail and adb_path:
            if setup_adb_reverse(adb_path, remote_port=DEFAULT_PORT + 1, local_port=DEFAULT_PORT):
                transports.append("usb")
                adb_reverse_active = True

        session = self._session_mgr.create_session(
            addrs=addrs,
            port=DEFAULT_PORT,
            transports=transports,
            cert_pem=cert_pem,
            cert_fp=cert_fp,
        )
        payload = self._session_mgr.build_qr_payload(session)
        uri = encode_qr_payload(payload)

        try:
            from connection_manager import ConnectionManager
            ConnectionManager.get_instance().broadcast_receive_offer(uri)
        except Exception:
            pass

        qr_png = generate_qr_png_bytes(uri, box_size=8)
        self.qr_ready.emit(qr_png, uri)
        self.status.emit("Scan QR code on the sending device")

        server = WiFiServer(DEFAULT_PORT, cert_pem, key_pem)
        self._server = server
        server.start(host="")

        try:
            if self._stop.is_set():
                return
            primary_sock, (sender_ip, sender_port) = server.accept(timeout=86400)
        except (TimeoutError, OSError):
            if self._stop.is_set():
                server.stop()
                return
            self.error.emit("Connection timed out — no sender connected")
            server.stop()
            if adb_reverse_active and adb_path:
                teardown_adb_reverse(adb_path, remote_port=DEFAULT_PORT + 1)
            return

        if self._stop.is_set():
            server.stop()
            if adb_reverse_active and adb_path:
                teardown_adb_reverse(adb_path, remote_port=DEFAULT_PORT + 1)
            return

        self.status.emit("CONNECTING…")
        primary_id = "usb-primary" if sender_ip == "127.0.0.1" else f"wifi-primary-{sender_ip}"
        primary_transport = WiFiTransport.from_accepted_socket(primary_sock, primary_id)
        with self._active_lock:
            self._active_transports = [primary_transport]
        active_lock = self._active_lock
        active_transports = self._active_transports

        hello = primary_transport.recv_json()
        if hello.get("type") != MessageType.HELLO:
            self.error.emit(f"Handshake error: Expected HELLO, got {hello.get('type')}")
            primary_transport.disconnect()
            server.stop()
            if adb_reverse_active and adb_path:
                teardown_adb_reverse(adb_path, remote_port=DEFAULT_PORT + 1)
            return

        ok, reason = self._session_mgr.validate_hello(hello["sid"], hello["token"])
        if not ok:
            primary_transport.send_json({"type": "ERROR", "code": reason})
            primary_transport.disconnect()
            server.stop()
            if adb_reverse_active and adb_path:
                teardown_adb_reverse(adb_path, remote_port=DEFAULT_PORT + 1)
            self.error.emit(f"Authentication failed: {reason}")
            return

        self._session_mgr.mark_connected(hello["sid"], hello.get("sender_id", "unknown"))
        primary_transport.send_json({"type": MessageType.HELLO_ACK, "v": 1, "sid": hello["sid"]})
        self.connected.emit(sender_ip)

        stop_accept = threading.Event()
        xfer_ready = threading.Event()

        def accept_secondary_transports():
            while not stop_accept.is_set() and not self._stop.is_set():
                try:
                    sec_sock, addr = server.accept(timeout=1.0)
                    tid = "usb-secondary" if addr[0] == "127.0.0.1" else f"wifi-secondary-{addr[0]}"
                    sec_t = WiFiTransport.from_accepted_socket(sec_sock, tid)
                    sec_msg = sec_t.recv_json(timeout=5.0)
                    if sec_msg.get("type") == MessageType.HELLO and sec_msg.get("sid") == hello["sid"]:
                        sec_t.send_json({"type": MessageType.HELLO_ACK, "v": 1, "sid": hello["sid"], "status": "ok"})
                        with self._active_lock:
                            self._active_transports.append(sec_t)
                        self._transport_type = "Wi-Fi + USB"
                        self.status.emit("Multi-path transport active (Wi-Fi + USB)")
                        if xfer_ready.is_set():
                            th = threading.Thread(target=read_from_transport, args=(sec_t,), daemon=True)
                            th.start()
                except Exception:
                    continue

        acceptor_thread = threading.Thread(target=accept_secondary_transports, daemon=True)
        acceptor_thread.start()

        # Receive READY message with batch file metadata (resilient loop handling PING/CANCEL/timeouts)
        ready = None
        while not self._stop.is_set():
            try:
                msg = primary_transport.recv_json(timeout=2.0)
            except TimeoutError:
                continue
            except (ConnectionError, ConnectionResetError, OSError):
                if not self._stop.is_set():
                    self.error.emit("Sender disconnected before sending files.")
                stop_accept.set()
                primary_transport.disconnect()
                server.stop()
                if adb_reverse_active and adb_path:
                    teardown_adb_reverse(adb_path, remote_port=DEFAULT_PORT + 1)
                return

            m_type = msg.get("type")
            if m_type == MessageType.PING:
                try:
                    primary_transport.send_json({"type": MessageType.PONG, "ts": msg.get("ts", 0.0)})
                except Exception:
                    pass
                continue
            elif m_type == MessageType.CANCEL:
                self.error.emit("Transfer cancelled by sender.")
                stop_accept.set()
                primary_transport.disconnect()
                server.stop()
                if adb_reverse_active and adb_path:
                    teardown_adb_reverse(adb_path, remote_port=DEFAULT_PORT + 1)
                return
            elif m_type == MessageType.READY:
                ready = msg
                break
            else:
                self.error.emit(f"Expected READY message, got {m_type}")
                stop_accept.set()
                primary_transport.disconnect()
                server.stop()
                if adb_reverse_active and adb_path:
                    teardown_adb_reverse(adb_path, remote_port=DEFAULT_PORT + 1)
                return

        if self._stop.is_set() or not ready:
            stop_accept.set()
            primary_transport.disconnect()
            server.stop()
            if adb_reverse_active and adb_path:
                teardown_adb_reverse(adb_path, remote_port=DEFAULT_PORT + 1)
            return

        infos = [TransferInfo.from_dict(t) for t in ready.get("transfers", [])]
        total_batch_bytes = sum(i.size for i in infos)
        file_count = len(infos)

        # Storage pre-flight check
        free_bytes = shutil.disk_usage(self._dest_dir).free
        if free_bytes < total_batch_bytes:
            for info in infos:
                try:
                    primary_transport.send_json({"type": MessageType.REJECT, "fid": info.fid, "reason": "not_enough_space"})
                except Exception:
                    pass
            stop_accept.set()
            with self._active_lock:
                for t in self._active_transports:
                    t.disconnect()
            server.stop()
            if adb_reverse_active and adb_path:
                teardown_adb_reverse(adb_path, remote_port=DEFAULT_PORT + 1)

            err_text = (
                f"Transfer stopped\n\nNot enough storage space.\n"
                f"Required: {format_bytes(total_batch_bytes)}\n"
                f"Available: {format_bytes(free_bytes)}"
            )
            self.error.emit(err_text)
            return

        # Prompt user to accept transfer
        self._user_decision.clear()
        self.incoming_prompt.emit(file_count, total_batch_bytes)

        # Wait for user decision (up to 60 seconds)
        got_decision = self._user_decision.wait(timeout=60.0)
        if not got_decision or not self._user_accepted or self._stop.is_set():
            for info in infos:
                try:
                    primary_transport.send_json({"type": MessageType.REJECT, "fid": info.fid, "reason": "user_rejected"})
                except Exception:
                    pass
            stop_accept.set()
            with self._active_lock:
                for t in self._active_transports:
                    t.disconnect()
            server.stop()
            if adb_reverse_active and adb_path:
                teardown_adb_reverse(adb_path, remote_port=DEFAULT_PORT + 1)
            self.status.emit("Transfer declined by user")
            return

        # Setup transfer engine
        storage = StorageManager(self._dest_dir)
        resume = ResumeManager()
        scheduler = Scheduler()
        with self._active_lock:
            for t in self._active_transports:
                scheduler.add_transport(t)

        # Progress tracking variables
        rx_bytes_map = {info.fid: 0 for info in infos}
        progress_lock = threading.Lock()

        def on_mgr_progress(fid, cid, rx, total):
            with progress_lock:
                rx_bytes_map[fid] = rx
                overall_rx = sum(rx_bytes_map.values())
                cur_idx = 1
                cur_info = infos[0]
                for i_idx, inf in enumerate(infos, start=1):
                    if inf.fid == fid:
                        cur_idx = i_idx
                        cur_info = inf
                        break
            self.progress_update.emit(
                cur_info.name, cur_idx, file_count, rx, cur_info.size, overall_rx, total_batch_bytes
            )

        xfer = TransferManager(
            session_id=hello["sid"],
            scheduler=scheduler,
            resume_manager=resume,
            storage_manager=storage,
            on_progress=on_mgr_progress,
        )
        self._xfer = xfer
        self._total_batch_bytes = total_batch_bytes
        xfer.setup_receive(infos)
        sent_done_fids = set()
        xfer_ready.set()

        def read_from_transport(t: WiFiTransport):
            xfer_ready.wait()
            while not self._stop.is_set():
                if xfer.is_all_files_verified():
                    break
                try:
                    magic = t.recv_exact(4)
                except Exception:
                    t.disconnect()
                    break

                if magic == CHUNK_MAGIC:
                    header_rest = t.recv_exact(CHUNK_HEADER_SIZE - 4)
                    header = magic + header_rest
                    import struct
                    chunk_len = struct.unpack_from(">I", header, 56)[0]
                    try:
                        chunk_data = t.recv_exact(chunk_len)
                    except Exception:
                        t.disconnect()
                        break

                    full_frame_bytes = header + chunk_data
                    frame = ChunkFrame.decode(full_frame_bytes)
                    ok, r_reason = xfer.receive_chunk(frame)

                    import uuid
                    fid = str(uuid.UUID(bytes=frame.file_id))
                    info = next((i for i in infos if i.fid == fid), None)
                    if info:
                        self._current_info = info
                        self._current_rx = rx_bytes_map.get(fid, 0)
                        self._overall_rx = sum(rx_bytes_map.values())

                    if not ok:
                        try:
                            t.send_json({"type": MessageType.NAK_CHUNK, "fid": fid, "cid": frame.chunk_id, "reason": r_reason})
                        except Exception:
                            pass

                    if xfer.is_file_complete(fid):
                        ok2, err = xfer.finalize_file(fid)
                        if ok2 and fid not in sent_done_fids:
                            sent_done_fids.add(fid)
                            expected_sha = (info.sha256 if info else "") or xfer._file_checksums.get(fid, "")
                            self.file_done.emit(info.name if info else fid, True)
                            try:
                                print(f"[DIAG] [TRANSFER_COMPLETE] File verified: {info.name if info else fid}")
                                t.send_json({"type": MessageType.FILE_DONE, "fid": fid, "sha256": expected_sha})
                            except Exception:
                                pass
                        elif err not in ("checksum_not_received", "verifying_in_progress", "chunks_missing"):
                            self.file_done.emit(info.name if info else fid, False)
                            try:
                                t.send_json({"type": MessageType.FILE_ERROR, "fid": fid, "reason": err})
                            except Exception:
                                pass
                elif magic.startswith(b"{"):
                    t._recv_buf[:0] = magic
                    try:
                        msg = t.recv_json(timeout=5.0)
                        m_type = msg.get("type")
                        if m_type == MessageType.FILE_CHECKSUM:
                            cfid = msg.get("fid")
                            csha = msg.get("sha256", "")
                            if cfid and csha:
                                xfer.set_file_checksum(cfid, csha)
                                if xfer.is_file_complete(cfid):
                                    ok2, err = xfer.finalize_file(cfid)
                                    cinfo = next((i for i in infos if i.fid == cfid), None)
                                    if ok2 and cfid not in sent_done_fids:
                                        sent_done_fids.add(cfid)
                                        self.file_done.emit(cinfo.name if cinfo else cfid, True)
                                        try:
                                            print(f"[DIAG] [TRANSFER_COMPLETE] File verified via FILE_CHECKSUM: {cinfo.name if cinfo else cfid}")
                                            t.send_json({"type": MessageType.FILE_DONE, "fid": cfid, "sha256": csha})
                                        except Exception:
                                            pass
                                    elif err not in ("checksum_not_received", "verifying_in_progress", "chunks_missing"):
                                        self.file_done.emit(cinfo.name if cinfo else cfid, False)
                                        try:
                                            t.send_json({"type": MessageType.FILE_ERROR, "fid": cfid, "reason": err})
                                        except Exception:
                                            pass
                        elif m_type == MessageType.PING:
                            print(f"[DIAG] [HEARTBEAT_RECEIVED] PING received on {t.transport_id}")
                            try:
                                t.send_json({"type": MessageType.PONG, "ts": msg.get("ts", 0)})
                                print(f"[DIAG] [HEARTBEAT_SENT] PONG sent on {t.transport_id}")
                            except Exception:
                                pass
                        elif m_type == MessageType.PAUSE:
                            pfid = msg.get("fid")
                            xfer.pause()
                            rx_chunks = xfer.get_received_chunks(pfid) if pfid else []
                            t.send_json({
                                "type": MessageType.PAUSE_ACK,
                                "fid": pfid,
                                "received_chunks": rx_chunks,
                            })
                            pinfo = next((i for i in infos if i.fid == pfid), infos[0] if infos else None)
                            pname = pinfo.name if pinfo else "Transfer"
                            psize = pinfo.size if pinfo else 0
                            p_cur_rx = rx_bytes_map.get(pfid, 0)
                            p_overall = sum(rx_bytes_map.values())
                            self.paused.emit(pname, p_cur_rx, psize, p_overall, total_batch_bytes)
                        elif m_type == MessageType.RESUME:
                            rfid = msg.get("fid")
                            if rfid and xfer.is_file_complete(rfid):
                                if xfer.is_file_verified(rfid):
                                    if rfid not in sent_done_fids:
                                        sent_done_fids.add(rfid)
                                        rinfo = next((i for i in infos if i.fid == rfid), None)
                                        expected_sha = (rinfo.sha256 if rinfo else "") or xfer._file_checksums.get(rfid, "")
                                        t.send_json({"type": MessageType.FILE_DONE, "fid": rfid, "sha256": expected_sha})
                                else:
                                    ok2, err = xfer.finalize_file(rfid)
                                    rinfo = next((i for i in infos if i.fid == rfid), None)
                                    if ok2 and rfid not in sent_done_fids:
                                        sent_done_fids.add(rfid)
                                        expected_sha = (rinfo.sha256 if rinfo else "") or xfer._file_checksums.get(rfid, "")
                                        self.file_done.emit(rinfo.name if rinfo else rfid, True)
                                        t.send_json({"type": MessageType.FILE_DONE, "fid": rfid, "sha256": expected_sha})
                                    elif err not in ("checksum_not_received", "verifying_in_progress", "chunks_missing"):
                                        self.file_done.emit(rinfo.name if rinfo else rfid, False)
                                        try:
                                            t.send_json({"type": MessageType.FILE_ERROR, "fid": rfid, "reason": err})
                                        except Exception:
                                            pass
                            elif rfid:
                                ok_state, state_err = xfer.verify_resume_state(rfid)
                                if ok_state:
                                    xfer.resume_transfer()
                                    rx_chunks = xfer.get_received_chunks(rfid)
                                    missing = xfer.get_missing_chunks(rfid)
                                    if missing:
                                        try:
                                            t.send_json({
                                                "type": MessageType.NAK_CHUNK,
                                                "fid": rfid,
                                                "missing": missing,
                                            })
                                        except Exception:
                                            pass
                                    t.send_json({
                                        "type": MessageType.ACCEPT,
                                        "fid": rfid,
                                        "received_chunks": rx_chunks,
                                        "missing": missing,
                                    })
                                    self.state_verified.emit()
                                else:
                                    t.send_json({
                                        "type": MessageType.ERROR,
                                        "fid": rfid,
                                        "reason": f"Corrupted state: {state_err}",
                                    })
                        elif m_type == MessageType.CANCEL:
                            xfer.cancel()
                            self._stop.set()
                    except Exception:
                        pass

        # Start reading from any secondary transports that connected before transfer began
        with self._active_lock:
            for t in self._active_transports:
                if t != primary_transport:
                    th = threading.Thread(target=read_from_transport, args=(t,), daemon=True)
                    th.start()

        # Send ACCEPTs to sender
        for info in infos:
            rx_chunks = xfer.get_received_chunks(info.fid)
            accept_msg = {"type": MessageType.ACCEPT, "fid": info.fid}
            if rx_chunks:
                accept_msg["received_chunks"] = rx_chunks
            primary_transport.send_json(accept_msg)

        self._session_mgr.mark_transferring(hello["sid"])
        self.status.emit(f"Receiving {file_count} file(s)…")

        primary_thread = threading.Thread(target=read_from_transport, args=(primary_transport,), daemon=True)
        primary_thread.start()

        # Wait until transfer completes and all files are verified (or a failure/timeout occurs)
        checksum_wait_start = None
        while not self._stop.is_set():
            if xfer.is_all_files_verified():
                break
            if any(ctx.state == TransferState.FAILED for ctx in xfer._files.values()):
                break
            if any(ctx.state == TransferState.VERIFYING for ctx in xfer._files.values()):
                time.sleep(0.05)
                continue
            with self._active_lock:
                any_connected = any(t.is_connected() for t in self._active_transports)
            if not any_connected:
                # If all chunks are already received, do not abort just because socket closed -
                # allow post-transfer verification to finish or wait for checksum
                if not xfer.is_all_files_complete():
                    time.sleep(0.5)
                    with self._active_lock:
                        any_connected = any(t.is_connected() for t in self._active_transports)
                    if not any_connected:
                        break
            # If all chunks received, allow up to 300s for post-transfer hashing and checksum arrival
            if xfer.is_all_files_complete():
                if checksum_wait_start is None:
                    checksum_wait_start = time.time()
                elif time.time() - checksum_wait_start > 300.0:
                    break
            time.sleep(0.05)

        stop_accept.set()
        server.stop()

        if not xfer.is_all_files_complete():
            xfer.persist_all_resume_states()

        failed_files = []
        with self._active_lock:
            alive_transports = [t for t in self._active_transports if t.is_connected()]

        for info in infos:
            expected_sha = xfer._file_checksums.get(info.fid, "") or info.sha256 or (xfer._files[info.fid].info.sha256 if info.fid in xfer._files else "")
            if xfer.is_file_verified(info.fid):
                if info.fid not in sent_done_fids:
                    sent_done_fids.add(info.fid)
                    done_msg = {"type": MessageType.FILE_DONE, "fid": info.fid, "sha256": expected_sha}
                    for t in alive_transports:
                        try:
                            t.send_json(done_msg)
                        except Exception:
                            pass
            else:
                failed_files.append(info.name)
                ctx = xfer._files.get(info.fid)
                if ctx and ctx.state == TransferState.FAILED:
                    reason = "hash_mismatch"
                elif not xfer.is_file_complete(info.fid):
                    reason = "incomplete_chunks"
                elif not expected_sha:
                    reason = "checksum_not_received"
                else:
                    reason = "verification_failed"
                err_msg = {"type": MessageType.FILE_ERROR, "fid": info.fid, "reason": reason}
                for t in alive_transports:
                    try:
                        t.send_json(err_msg)
                    except Exception:
                        pass

        with self._active_lock:
            for t in self._active_transports:
                try:
                    t.disconnect()
                except Exception:
                    pass

        if adb_reverse_active and adb_path:
            teardown_adb_reverse(adb_path, remote_port=DEFAULT_PORT + 1)

        duration = time.time() - start_time
        if failed_files:
            with self._active_lock:
                any_connected = any(t.is_connected() for t in self._active_transports)
            if not any_connected and not xfer.is_all_files_complete():
                err_reason = f"Connection lost during transfer: {', '.join(failed_files)}"
            else:
                err_reason = f"Integrity check failed: {', '.join(failed_files)}"
            self.error.emit(err_reason)
            HistoryManager.get_instance().add_record(TransferRecord(
                direction="received",
                files=[i.name for i in infos],
                total_bytes=total_batch_bytes,
                duration_sec=duration,
                status="failed",
                transport_type=self._transport_type,
                error_reason=err_reason,
            ))
        else:
            self._session_mgr.mark_completed(hello["sid"])
            HistoryManager.get_instance().add_record(TransferRecord(
                direction="received",
                files=[i.name for i in infos],
                total_bytes=total_batch_bytes,
                duration_sec=duration,
                status="completed",
                transport_type=self._transport_type,
            ))
            self.transfer_complete.emit(len(infos), total_batch_bytes, duration)


class ReceiveScreen(QWidget):
    go_back = pyqtSignal()
    go_history = pyqtSignal()

    def __init__(self):
        super().__init__()
        self._worker: Optional[ReceiverWorker] = None
        self._thread: Optional[QThread] = None
        self._dest_dir = Path.home() / "Downloads" / "PhotoBeam"
        self._session_mgr = SessionManager()
        self._speed_tracker = SpeedTracker(alpha=0.25)

        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(36, 24, 36, 32)
        root.setSpacing(18)

        # ── Top Bar ───────────────────────────────────────────────────────────
        top = QHBoxLayout()
        back_btn = QPushButton(" Back to Dashboard")
        back_btn.setObjectName("secondary")
        back_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        back_btn.clicked.connect(self._on_back)
        top.addWidget(back_btn)

        top.addStretch()

        dest_btn = QPushButton(" Destination")
        dest_btn.setObjectName("secondary")
        dest_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        dest_btn.clicked.connect(self._pick_dest)
        top.addWidget(dest_btn)

        hist_btn = QPushButton(" Activity")
        hist_btn.setObjectName("secondary")
        hist_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        hist_btn.clicked.connect(self.go_history.emit)
        top.addWidget(hist_btn)

        root.addLayout(top)

        # ── Main Card ─────────────────────────────────────────────────────────
        self._card = QFrame()
        self._card.setObjectName("card")
        self._card_layout = QVBoxLayout(self._card)
        self._card_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._card_layout.setSpacing(16)
        self._card_layout.setContentsMargins(32, 28, 32, 28)

        # Title
        heading = QLabel("Receive Files")
        heading.setObjectName("dash_heading")
        heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._card_layout.addWidget(heading)

        # Connection status badge
        self._conn_badge = QLabel("Waiting for incoming transfer…")
        self._conn_badge.setObjectName("badge_gray")
        self._conn_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._card_layout.addWidget(self._conn_badge, alignment=Qt.AlignmentFlag.AlignCenter)

        # ── QR Waiting View ───────────────────────────────────────────────────
        self._qr_container = QWidget()
        qr_layout = QVBoxLayout(self._qr_container)
        qr_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        qr_layout.setSpacing(12)

        self._qr_label = QLabel()
        self._qr_label.setObjectName("qr_label")
        self._qr_label.setFixedSize(260, 260)
        self._qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._qr_label.setText("Generating QR…")
        qr_layout.addWidget(self._qr_label, alignment=Qt.AlignmentFlag.AlignCenter)

        self._inst_label = QLabel("Point your mobile camera at this QR code to connect")
        self._inst_label.setObjectName("muted_text")
        self._inst_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        qr_layout.addWidget(self._inst_label)

        self._card_layout.addWidget(self._qr_container)

        # ── Incoming Prompt View (Accept / Reject) ─────────────────────────────
        self._prompt_container = QWidget()
        prompt_layout = QVBoxLayout(self._prompt_container)
        prompt_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        prompt_layout.setSpacing(14)

        try:
            from .icons import get_pixmap
        except (ImportError, ValueError):
            from icons import get_pixmap

        prompt_icon = QLabel()
        prompt_icon.setPixmap(get_pixmap("receive", "#38BDF8", 36))
        prompt_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        prompt_layout.addWidget(prompt_icon)

        prompt_title = QLabel("Incoming Transfer")
        prompt_title.setObjectName("section_heading")
        prompt_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        prompt_layout.addWidget(prompt_title)


        self._prompt_details = QLabel("3 files • 2.84 GB")
        self._prompt_details.setObjectName("subtitle")
        self._prompt_details.setAlignment(Qt.AlignmentFlag.AlignCenter)
        prompt_layout.addWidget(self._prompt_details)

        prompt_btn_row = QHBoxLayout()
        accept_btn = QPushButton("Accept Transfer")
        accept_btn.setObjectName("primary")
        accept_btn.clicked.connect(self._on_accept)

        reject_btn = QPushButton("Decline")
        reject_btn.setObjectName("secondary")
        reject_btn.clicked.connect(self._on_reject)

        prompt_btn_row.addWidget(accept_btn)
        prompt_btn_row.addWidget(reject_btn)
        prompt_layout.addLayout(prompt_btn_row)

        self._prompt_container.setVisible(False)
        self._card_layout.addWidget(self._prompt_container)

        # ── Progress View ─────────────────────────────────────────────────────
        self._progress_container = QWidget()
        prog_layout = QVBoxLayout(self._progress_container)
        prog_layout.setSpacing(10)

        self._prog_file_name = QLabel("Receiving files…")
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

        stats_row = QHBoxLayout()
        self._bytes_label = QLabel("0 B / 0 B")
        self._bytes_label.setObjectName("subtitle")
        self._counter_label = QLabel("File 1 of 1")
        self._counter_label.setObjectName("subtitle")
        stats_row.addWidget(self._bytes_label)
        stats_row.addStretch()
        stats_row.addWidget(self._counter_label)
        prog_layout.addLayout(stats_row)

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

        self._cancel_btn = QPushButton("Cancel Transfer")
        self._cancel_btn.setObjectName("back")
        self._cancel_btn.setStyleSheet("color: #f87171;")
        self._cancel_btn.clicked.connect(self._on_cancel_clicked)

        self._transfer_actions_layout.addWidget(self._pause_btn)
        self._transfer_actions_layout.addWidget(self._continue_btn)
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
        open_folder_btn = QPushButton("📁 Open Folder")
        open_folder_btn.setObjectName("secondary")
        open_folder_btn.clicked.connect(self._open_dest_folder)

        done_btn = QPushButton("Done")
        done_btn.setObjectName("secondary")
        done_btn.clicked.connect(self._reset_to_start)

        view_hist_btn = QPushButton("View History")
        view_hist_btn.setObjectName("primary")
        view_hist_btn.clicked.connect(self.go_history.emit)

        comp_btn_row.addWidget(open_folder_btn)
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

        self._err_desc = QLabel("Transfer stopped")
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
        retry_btn = QPushButton("Restart Receiver")
        retry_btn.setObjectName("primary")
        retry_btn.clicked.connect(self._reset_to_start)

        cancel_btn = QPushButton("Back")
        cancel_btn.setObjectName("secondary")
        cancel_btn.clicked.connect(self._on_back)

        err_btn_row.addWidget(retry_btn)
        err_btn_row.addWidget(cancel_btn)
        err_layout.addLayout(err_btn_row)

        self._error_container.setVisible(False)
        self._card_layout.addWidget(self._error_container)

        # Destination info label
        self._dest_info = QLabel(f"Saving to: {self._dest_dir}")
        self._dest_info.setObjectName("info")
        self._dest_info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._card_layout.addWidget(self._dest_info)

        root.addWidget(self._card)
        root.addStretch()

    def on_shown(self):
        self._reset_to_start()

    def _reset_to_start(self):
        self._stop_worker()
        self._qr_container.setVisible(True)
        self._prompt_container.setVisible(False)
        self._progress_container.setVisible(False)
        self._complete_container.setVisible(False)
        self._error_container.setVisible(False)
        self._details_panel.setVisible(False)
        self._details_toggle_btn.setText("Show technical details ▼")
        self._inst_label.setText("Point your mobile camera at this QR code to connect")
        self._inst_label.setStyleSheet("")
        self._conn_badge.setText("● READY — WAITING FOR DEVICE")
        self._conn_badge.setStyleSheet("color: #94a3b8; font-size: 14px; font-weight: 600;")
        self._start_worker()

    def _start_worker(self):
        self._dest_dir.mkdir(parents=True, exist_ok=True)
        self._speed_tracker = SpeedTracker(alpha=0.25)
        self._worker = ReceiverWorker(self._dest_dir, self._session_mgr)
        self._thread = QThread()
        self._worker.moveToThread(self._thread)

        self._worker.qr_ready.connect(self._on_qr_ready)
        self._worker.connected.connect(self._on_connected)
        self._worker.incoming_prompt.connect(self._on_incoming_prompt)
        self._worker.progress_update.connect(self._on_progress_update)
        self._worker.file_done.connect(self._on_file_done)
        self._worker.transfer_complete.connect(self._on_complete)
        self._worker.error.connect(self._on_error)
        self._worker.status.connect(self._on_status)
        self._worker.pausing.connect(self._on_transfer_pausing)
        self._worker.paused.connect(self._on_transfer_paused)
        self._worker.verifying.connect(self._on_transfer_verifying)
        self._worker.state_verified.connect(self._on_transfer_state_verified)

        self._pause_status_label.setVisible(False)
        self._pause_btn.setVisible(True)
        self._pause_btn.setEnabled(True)
        self._continue_btn.setVisible(False)
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

    def _on_transfer_paused(self, current_file: str, cur_rx: int, file_size: int, overall_rx: int, overall_total: int):
        self._pause_status_label.setText(
            f"Transfer Paused\n\n{current_file}\n{format_bytes(cur_rx)} / {format_bytes(file_size)}\n\nYour transferred data is safe."
        )
        self._pause_status_label.setStyleSheet("color: #38bdf8; font-size: 14px; font-weight: 600;")
        self._pause_status_label.setVisible(True)
        self._pause_btn.setVisible(False)
        self._continue_btn.setVisible(True)
        self._continue_btn.setEnabled(True)
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

    def _stop_worker(self):
        try:
            from connection_manager import ConnectionManager
            ConnectionManager.get_instance().clear_local_receive_offer()
        except Exception:
            pass
        if self._worker:
            self._worker.stop()
        if self._thread and self._thread.isRunning():
            self._thread.quit()
            self._thread.wait(2000)
        self._worker = None
        self._thread = None

    def _on_qr_ready(self, png_bytes: bytes, uri: str):
        img = QImage.fromData(png_bytes, "PNG")
        pix = QPixmap.fromImage(img).scaled(
            256, 256,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._qr_label.setPixmap(pix)
        self._conn_badge.setText("● READY — WAITING FOR DEVICE")
        self._conn_badge.setStyleSheet("color: #94a3b8; font-size: 14px; font-weight: 600;")
        try:
            qr_file = Path.home() / ".photobeam" / "current_qr_uri.txt"
            qr_file.parent.mkdir(parents=True, exist_ok=True)
            qr_file.write_text(uri, encoding="utf-8")
        except Exception:
            pass

    def _on_connected(self, addr: str):
        self._conn_badge.setText(f"● CONNECTED ({addr})")
        self._conn_badge.setStyleSheet("color: #4ade80; font-size: 14px; font-weight: 600;")
        self._inst_label.setText("✓ Connected! Waiting for transfer to start…")
        self._inst_label.setStyleSheet("color: #38bdf8; font-weight: 500;")

    def _on_incoming_prompt(self, file_count: int, total_bytes: int):
        self._qr_container.setVisible(False)
        self._prompt_container.setVisible(True)
        self._prompt_details.setText(f"{file_count} file(s) • {format_bytes(total_bytes)}")
        if "--auto-accept" in sys.argv:
            self._on_accept()

    def _on_accept(self):
        self._prompt_container.setVisible(False)
        self._progress_container.setVisible(True)
        if self._worker:
            self._worker.accept_transfer()

    def _on_reject(self):
        self._prompt_container.setVisible(False)
        if self._worker:
            self._worker.reject_transfer()
        self._reset_to_start()

    def _on_progress_update(self, name: str, idx: int, total_files: int, file_rx: int, file_size: int, overall_rx: int, overall_total: int):
        self._conn_badge.setText("● TRANSFERRING")
        self._conn_badge.setStyleSheet("color: #38bdf8; font-size: 14px; font-weight: 600;")

        self._prog_file_name.setText(f"Receiving {name}")
        self._counter_label.setText(f"File {idx} of {total_files}")

        if file_size > 0:
            self._file_progress_bar.setValue(int(file_rx * 100 / file_size))
        else:
            self._file_progress_bar.setValue(100)

        if overall_total > 0:
            self._overall_progress_bar.setValue(int(overall_rx * 100 / overall_total))
        else:
            self._overall_progress_bar.setValue(100)

        self._bytes_label.setText(f"{format_bytes(overall_rx)} / {format_bytes(overall_total)}")

        spd = self._speed_tracker.update(overall_rx)
        transport_speeds = self._worker.get_transport_speeds() if self._worker else {}
        wifi_bps = transport_speeds.get("wifi", 0.0)
        usb_bps = transport_speeds.get("usb", 0.0)

        display_spd = spd if spd > 0 else transport_speeds.get("total", 0.0)
        if display_spd > 0:
            speed_text = f"{format_bytes(int(display_spd))}/s"
            breakdown = []
            if wifi_bps > 1024:
                breakdown.append(f"Wi-Fi: {format_bytes(int(wifi_bps))}/s")
            if usb_bps > 1024:
                breakdown.append(f"USB: {format_bytes(int(usb_bps))}/s")
            if breakdown and len(breakdown) > 1:
                speed_text += f" ({' • '.join(breakdown)})"
            self._speed_label.setText(speed_text)

            rem = max(0, overall_total - overall_rx)
            eta = self._speed_tracker.format_eta(rem)
            self._eta_label.setText(eta)
        else:
            self._speed_label.setText("")
            self._eta_label.setText("")

    def _on_file_done(self, name: str, success: bool):
        pass

    def _on_complete(self, file_count: int, total_bytes: int, duration_sec: float):
        self._conn_badge.setText("● COMPLETED")
        self._conn_badge.setStyleSheet("color: #4ade80; font-size: 14px; font-weight: 600;")
        self._progress_container.setVisible(False)
        self._complete_container.setVisible(True)
        dur_str = f"{duration_sec:.1f}s"
        spd_str = f"({(total_bytes / (1024 * 1024)) / duration_sec:.1f} MB/s)" if duration_sec > 0 and total_bytes > 0 else ""
        self._comp_summary.setText(f"{file_count} file(s) • {format_bytes(total_bytes)} received in {dur_str} {spd_str}")
        if "--exit-after-transfer" in sys.argv:
            QTimer.singleShot(2000, QApplication.instance().quit)

    def _on_status(self, msg: str):
        lower = msg.lower()
        if lower.startswith("connecting"):
            self._conn_badge.setText("● CONNECTING…")
            self._conn_badge.setStyleSheet("color: #facc15; font-size: 14px; font-weight: 600;")
            self._inst_label.setText("Connecting to device…")
            self._inst_label.setStyleSheet("color: #facc15; font-weight: 500;")
        elif lower.startswith("receiving") or lower.startswith("transferring"):
            self._conn_badge.setText("● TRANSFERRING")
            self._conn_badge.setStyleSheet("color: #38bdf8; font-size: 14px; font-weight: 600;")
        elif "completed" in lower:
            self._conn_badge.setText("● COMPLETED")
            self._conn_badge.setStyleSheet("color: #4ade80; font-size: 14px; font-weight: 600;")

        if "multi-path" in lower or "usb" in lower:
            self._transport_pill.setText("⚡ Multi-path (Wi-Fi + USB)")
            self._transport_pill.setStyleSheet("color: #a78bfa; background-color: rgba(167, 139, 250, 0.15); border: 1px solid rgba(167, 139, 250, 0.3);")
        elif "wi-fi" in lower or "wifi" in lower:
            self._transport_pill.setText("Connected via Wi-Fi")
            self._transport_pill.setStyleSheet("color: #38bdf8; background-color: rgba(56, 189, 248, 0.12); border: 1px solid rgba(56, 189, 248, 0.25);")

    def _on_error(self, message: str):
        self._qr_container.setVisible(False)
        self._prompt_container.setVisible(False)
        self._progress_container.setVisible(False)
        self._complete_container.setVisible(False)
        self._error_container.setVisible(True)

        friendly = format_friendly_error(message)
        self._err_title.setText(friendly.title)
        self._err_desc.setText(friendly.message)
        self._details_label.setText(friendly.technical_details or message)
        self._details_panel.setVisible(False)
        self._details_toggle_btn.setText("Show technical details ▼")

        self._conn_badge.setText("● CONNECTION ERROR")
        self._conn_badge.setStyleSheet("color: #f87171; font-size: 14px; font-weight: 600;")

    def _toggle_err_details(self):
        is_vis = self._details_panel.isVisible()
        self._details_panel.setVisible(not is_vis)
        self._details_toggle_btn.setText("Hide technical details ▲" if not is_vis else "Show technical details ▼")

    def _open_dest_folder(self):
        self._dest_dir.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._dest_dir.resolve())))

    def _pick_dest(self):
        d = QFileDialog.getExistingDirectory(self, "Choose destination folder", str(self._dest_dir))
        if d:
            self._dest_dir = Path(d)
            self._dest_info.setText(f"Saving to: {self._dest_dir}")

    def _on_back(self):
        self._stop_worker()
        self.go_back.emit()

