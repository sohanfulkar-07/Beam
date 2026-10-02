"""
PhotoBeam 2.0 High-Speed DataSender
Ephemeral binary stream sender connecting to peer on port 47474 (or USB 47475).
Constant O(1) memory footprint (4 MB buffer), 64-bit file sizes (up to 100+ GB),
and streaming rolling SHA-256 verification.
"""
from __future__ import annotations

import hashlib
import logging
import os
import socket
import struct
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
    pack_data_header,
    recv_exact,
)

logger = logging.getLogger("photobeam.data_sender")


if _HAS_PYQT:
    class DataSenderSignals(QObject):
        sig_transfer_started = pyqtSignal(int, str, int)                 # (transfer_id, filename, file_size)
        sig_transfer_progress = pyqtSignal(int, int, int, float)        # (transfer_id, bytes_sent, total_bytes, speed_mbps)
        sig_transfer_completed = pyqtSignal(int, str, int, bool, str)   # (transfer_id, filename, file_size, success, error)


class DataSender:
    """
    Streams a single file to peer's DataReceiver.
    Ephemeral connection: opens socket, writes header, streams 4 MB chunks with rolling SHA-256,
    sends 32-byte digest trailer, waits for 1-byte ACK, then closes.
    """

    def __init__(self):
        if _HAS_PYQT:
            self.signals = DataSenderSignals()
            self.sig_transfer_started = self.signals.sig_transfer_started
            self.sig_transfer_progress = self.signals.sig_transfer_progress
            self.sig_transfer_completed = self.signals.sig_transfer_completed
        else:
            self.signals = None
            self.sig_transfer_started = None
            self.sig_transfer_progress = None
            self.sig_transfer_completed = None

    def send_file(
        self,
        peer_ip: str,
        peer_port: int,
        file_path: Path | str,
        transfer_id: int = 0,
        progress_cb: Optional[Callable[[int, int, int, float], None]] = None,
        completed_cb: Optional[Callable[[int, str, int, bool, str], None]] = None,
    ) -> bool:
        """
        Synchronously send a file to the peer. Run in a background thread if non-blocking operation is required.
        """
        path = Path(file_path).resolve()
        if not path.is_file():
            err = f"File not found: {path}"
            logger.error("[DATA_SENDER] %s", err)
            if self.sig_transfer_completed:
                self.sig_transfer_completed.emit(transfer_id, path.name, 0, False, err)
            if completed_cb:
                completed_cb(transfer_id, path.name, 0, False, err)
            return False

        file_size = path.stat().st_size
        filename = path.name
        if transfer_id == 0:
            transfer_id = int(time.time() * 1000) & 0x7FFFFFFFFFFFFFFF

        logger.info(
            "[DATA_SENDER] Starting transfer %d: '%s' (%d bytes, %.2f MB) -> %s:%d",
            transfer_id, filename, file_size, file_size / (1024 * 1024), peer_ip, peer_port
        )

        if self.sig_transfer_started:
            self.sig_transfer_started.emit(transfer_id, filename, file_size)

        sock: Optional[socket.socket] = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            sock.settimeout(15.0)
            sock.connect((peer_ip, peer_port))

            # 1. Send Header
            header_bytes = pack_data_header(transfer_id, file_size, filename)
            sock.sendall(header_bytes)

            # 2. Stream chunks with rolling SHA-256
            hasher = hashlib.sha256()
            bytes_sent = 0
            start_time = time.time()
            last_progress_time = start_time

            with open(path, "rb") as f:
                while bytes_sent < file_size:
                    to_read = min(CHUNK_BUFFER_SIZE, file_size - bytes_sent)
                    chunk = f.read(to_read)
                    if not chunk:
                        raise EOFError(f"File truncated unexpectedly at {bytes_sent}/{file_size} bytes")

                    hasher.update(chunk)
                    sock.sendall(chunk)
                    bytes_sent += len(chunk)

                    now = time.time()
                    if now - last_progress_time >= 0.1 or bytes_sent == file_size:
                        elapsed = now - start_time
                        speed_mbps = (bytes_sent / (1024 * 1024)) / elapsed if elapsed > 0 else 0.0
                        if self.sig_transfer_progress:
                            self.sig_transfer_progress.emit(transfer_id, bytes_sent, file_size, speed_mbps)
                        if progress_cb:
                            progress_cb(transfer_id, bytes_sent, file_size, speed_mbps)
                        last_progress_time = now

            # 3. Send 32-byte SHA-256 Trailer
            digest = hasher.digest()
            sock.sendall(digest)

            # 4. Wait for 1-byte ACK
            ack = recv_exact(sock, 1)
            if not ack or ack != b"\x06":
                raise ConnectionError(f"Receiver failed to ACK transfer (received {ack!r})")

            elapsed = max(time.time() - start_time, 0.001)
            final_speed = (file_size / (1024 * 1024)) / elapsed
            logger.info(
                "[DATA_SENDER] Transfer %d completed successfully: '%s' (%.2f MB/s, sha256=%s)",
                transfer_id, filename, final_speed, hasher.hexdigest()[:16]
            )

            if self.sig_transfer_completed:
                self.sig_transfer_completed.emit(transfer_id, filename, file_size, True, "")
            if completed_cb:
                completed_cb(transfer_id, filename, file_size, True, "")
            return True

        except Exception as e:
            logger.error("[DATA_SENDER] Transfer %d failed: %s", transfer_id, e)
            if self.sig_transfer_completed:
                self.sig_transfer_completed.emit(transfer_id, filename, file_size, False, str(e))
            if completed_cb:
                completed_cb(transfer_id, filename, file_size, False, str(e))
            return False

        finally:
            if sock:
                try:
                    sock.close()
                except Exception:
                    pass

    def send_file_async(
        self,
        peer_ip: str,
        peer_port: int,
        file_path: Path | str,
        transfer_id: int = 0,
        progress_cb: Optional[Callable[[int, int, int, float], None]] = None,
        completed_cb: Optional[Callable[[int, str, int, bool, str], None]] = None,
    ) -> threading.Thread:
        """Launch send_file in a background thread."""
        t = threading.Thread(
            target=self.send_file,
            args=(peer_ip, peer_port, file_path, transfer_id, progress_cb, completed_cb),
            name=f"DataSender-{Path(file_path).name}",
            daemon=True,
        )
        t.start()
        return t
