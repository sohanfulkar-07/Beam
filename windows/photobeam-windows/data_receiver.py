"""
PhotoBeam 2.0 High-Speed DataReceiver
Ephemeral binary stream receiver listening on port 47474.
Constant O(1) memory footprint (4 MB buffer), 64-bit file sizes (up to 100+ GB),
and streaming rolling SHA-256 verification.
"""
from __future__ import annotations

import hashlib
import logging
import os
import socket
import threading
import time
from pathlib import Path
from typing import Callable, Optional

try:
    from PyQt6.QtCore import QObject, pyqtSignal
    _HAS_PYQT = True
except ImportError:
    _HAS_PYQT = False

from protocol_v2 import (
    DATA_PORT,
    CHUNK_BUFFER_SIZE,
    unpack_data_header,
    recv_exact,
)

logger = logging.getLogger("photobeam.data_receiver")


if _HAS_PYQT:
    class DataReceiverSignals(QObject):
        sig_transfer_started = pyqtSignal(int, str, int)                 # (transfer_id, filename, file_size)
        sig_transfer_progress = pyqtSignal(int, int, int, float)        # (transfer_id, bytes_transferred, total_bytes, speed_mbps)
        sig_transfer_completed = pyqtSignal(int, str, int, bool, str)   # (transfer_id, filename, file_size, success, error)


class DataReceiver:
    def __init__(self, download_dir: Optional[Path] = None, port: int = DATA_PORT):
        self.download_dir = download_dir or (Path.home() / "Downloads" / "PhotoBeam")
        self.port = port
        self.is_running = False
        self._server_sock: Optional[socket.socket] = None
        self._listen_thread: Optional[threading.Thread] = None

        if _HAS_PYQT:
            self.signals = DataReceiverSignals()
            self.sig_transfer_started = self.signals.sig_transfer_started
            self.sig_transfer_progress = self.signals.sig_transfer_progress
            self.sig_transfer_completed = self.signals.sig_transfer_completed
        else:
            self.signals = None
            self.sig_transfer_started = None
            self.sig_transfer_progress = None
            self.sig_transfer_completed = None

        self._callbacks_lock = threading.Lock()
        self._progress_cb: Optional[Callable[[int, int, int, float], None]] = None
        self._completed_cb: Optional[Callable[[int, str, int, bool, str], None]] = None

    def set_callbacks(
        self,
        progress_cb: Optional[Callable[[int, int, int, float], None]] = None,
        completed_cb: Optional[Callable[[int, str, int, bool, str], None]] = None,
    ):
        with self._callbacks_lock:
            self._progress_cb = progress_cb
            self._completed_cb = completed_cb

    def start(self):
        if self.is_running:
            return
        self.is_running = True
        self.download_dir.mkdir(parents=True, exist_ok=True)

        self._server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server_sock.bind(("0.0.0.0", self.port))
        self._server_sock.listen(10)
        self._server_sock.settimeout(1.0)

        self._listen_thread = threading.Thread(
            target=self._listen_loop,
            name="PhotoBeam-DataReceiver",
            daemon=True,
        )
        self._listen_thread.start()
        logger.info("[DATA_RECEIVER] Listening on 0.0.0.0:%d (download_dir=%s)", self.port, self.download_dir)

    def stop(self):
        self.is_running = False
        if self._server_sock:
            try:
                self._server_sock.close()
            except Exception:
                pass
            self._server_sock = None
        if self._listen_thread:
            self._listen_thread.join(timeout=1.0)
            self._listen_thread = None
        logger.info("[DATA_RECEIVER] Stopped")

    def _listen_loop(self):
        while self.is_running:
            try:
                client_sock, client_addr = self._server_sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            except Exception as e:
                if not self.is_running:
                    break
                logger.warning("[DATA_RECEIVER] Accept error: %s", e)
                continue

            # Handle incoming stream in a dedicated thread
            t = threading.Thread(
                target=self._handle_client,
                args=(client_sock, client_addr),
                name=f"DataStream-{client_addr[0]}",
                daemon=True,
            )
            t.start()

    def _handle_client(self, sock: socket.socket, addr):
        transfer_id = 0
        file_size = 0
        filename = "unknown"
        target_path = None
        part_path = None

        try:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            sock.settimeout(15.0)

            # 1. Unpack Header
            header = unpack_data_header(sock)
            if not header:
                logger.warning("[DATA_RECEIVER] Invalid header from %s", addr)
                sock.close()
                return

            transfer_id, file_size, raw_filename = header
            filename = os.path.basename(raw_filename.replace("\\", "/"))
            logger.info(
                "[DATA_RECEIVER] Incoming stream: id=%d, file='%s', size=%d bytes (%.2f MB)",
                transfer_id, filename, file_size, file_size / (1024 * 1024)
            )

            if self.sig_transfer_started:
                self.sig_transfer_started.emit(transfer_id, filename, file_size)

            target_path = self.download_dir / filename
            part_path = self.download_dir / f"{filename}.part"

            hasher = hashlib.sha256()
            bytes_received = 0
            start_time = time.time()
            last_progress_time = start_time

            with open(part_path, "wb") as f:
                while bytes_received < file_size:
                    to_read = min(CHUNK_BUFFER_SIZE, file_size - bytes_received)
                    chunk = sock.recv(to_read)
                    if not chunk:
                        raise ConnectionResetError(f"Unexpected EOF after {bytes_received}/{file_size} bytes")
                    f.write(chunk)
                    hasher.update(chunk)
                    bytes_received += len(chunk)

                    now = time.time()
                    if now - last_progress_time >= 0.1 or bytes_received == file_size:
                        elapsed = now - start_time
                        speed_mbps = (bytes_received / (1024 * 1024)) / elapsed if elapsed > 0 else 0.0
                        if self.sig_transfer_progress:
                            self.sig_transfer_progress.emit(transfer_id, bytes_received, file_size, speed_mbps)
                        with self._callbacks_lock:
                            if self._progress_cb:
                                self._progress_cb(transfer_id, bytes_received, file_size, speed_mbps)
                        last_progress_time = now

            # 2. Read 32-byte SHA-256 Trailer
            trailer = recv_exact(sock, 32)
            if not trailer:
                raise ValueError("Missing SHA-256 trailer from sender")

            expected_digest = trailer.hex().lower()
            calculated_digest = hasher.hexdigest().lower()

            if expected_digest != calculated_digest:
                raise ValueError(
                    f"Checksum mismatch! Expected {expected_digest}, calculated {calculated_digest}"
                )

            # 3. Rename .part to target filename
            if target_path.exists():
                target_path.unlink()
            part_path.rename(target_path)

            # Send 1-byte ACK
            try:
                sock.sendall(b"\x06")
            except Exception:
                pass

            elapsed = max(time.time() - start_time, 0.001)
            final_speed = (file_size / (1024 * 1024)) / elapsed
            logger.info(
                "[DATA_RECEIVER] Transfer %d completed successfully: '%s' (%d bytes, %.2f MB/s, sha256=%s)",
                transfer_id, filename, file_size, final_speed, calculated_digest[:16]
            )

            if self.sig_transfer_completed:
                self.sig_transfer_completed.emit(transfer_id, filename, file_size, True, "")
            with self._callbacks_lock:
                if self._completed_cb:
                    self._completed_cb(transfer_id, filename, file_size, True, "")

        except Exception as e:
            logger.error("[DATA_RECEIVER] Transfer %d failed: %s", transfer_id, e)
            if part_path and part_path.exists():
                try:
                    part_path.unlink()
                except Exception:
                    pass
            if self.sig_transfer_completed:
                self.sig_transfer_completed.emit(transfer_id, filename, file_size, False, str(e))
            with self._callbacks_lock:
                if self._completed_cb:
                    self._completed_cb(transfer_id, filename, file_size, False, str(e))
        finally:
            try:
                sock.close()
            except Exception:
                pass
