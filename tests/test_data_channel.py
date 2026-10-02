import hashlib
import os
import socket
import tempfile
import time
from pathlib import Path

import pytest
import sys

_win_dir = Path(__file__).resolve().parent.parent / "windows" / "photobeam-windows"
if str(_win_dir) not in sys.path:
    sys.path.insert(0, str(_win_dir))

from data_receiver import DataReceiver
from data_sender import DataSender


def test_data_channel_roundtrip():
    """Verify DataSender streams file to DataReceiver with rolling SHA-256 verification and ACK."""
    with tempfile.TemporaryDirectory() as src_dir, tempfile.TemporaryDirectory() as dst_dir:
        # Create a 5 MB test file with pseudo-random content
        src_file = Path(src_dir) / "test_payload.bin"
        chunk = os.urandom(1024 * 1024)  # 1 MB chunk
        expected_hasher = hashlib.sha256()
        with open(src_file, "wb") as f:
            for _ in range(5):
                f.write(chunk)
                expected_hasher.update(chunk)
        expected_sha256 = expected_hasher.hexdigest()

        # Find a free port
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            free_port = s.getsockname()[1]

        receiver = DataReceiver(download_dir=Path(dst_dir), port=free_port)
        receiver.start()

        completed_events = []

        def on_completed(tid, name, size, success, err):
            completed_events.append((tid, name, size, success, err))

        receiver.set_callbacks(completed_cb=on_completed)

        sender = DataSender()
        success = sender.send_file(
            peer_ip="127.0.0.1",
            peer_port=free_port,
            file_path=src_file,
            transfer_id=12345,
        )

        assert success is True
        time.sleep(0.5)

        receiver.stop()

        # Verify receiver saved file
        received_file = Path(dst_dir) / "test_payload.bin"
        assert received_file.is_file()
        assert received_file.stat().st_size == 5 * 1024 * 1024

        with open(received_file, "rb") as f:
            received_sha256 = hashlib.sha256(f.read()).hexdigest()

        assert received_sha256 == expected_sha256
        assert len(completed_events) == 1
        assert completed_events[0][3] is True  # success is True
