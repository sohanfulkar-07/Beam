"""
Multi-Transport Same-File Partial Completion Regression Tests

Verifies that when multiple transports (e.g. Wi-Fi and USB) transfer the SAME file:
1. A transport finishing or closing its assigned chunk subset NEVER marks the file as complete.
2. The file remains incomplete and unfinalized while chunks remain missing.
3. FILE_CHECKSUM cannot cause premature finalization if chunks from another transport are missing.
4. If Transport A closes while Transport B still has missing chunks, the transfer continues.
5. Only when 100% of chunks across all transports arrive does the file become COMPLETE.
6. Byte-for-byte size and SHA-256 match perfectly upon finalization.
"""
import hashlib
import os
import sys
from pathlib import Path
import pytest

PROTO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "protocol"))
if PROTO not in sys.path:
    sys.path.insert(0, PROTO)

from src.chunk import ChunkManager
from src.integrity import IntegrityManager
from src.models import (
    CHUNK_HEADER_SIZE,
    CHUNK_MAGIC,
    ChunkFrame,
    FrameType,
    MessageType,
    TransferInfo,
    TransferState,
)
from src.resume import ResumeManager
from src.scheduler import Scheduler
from src.storage import StorageManager
from src.transfer import TransferManager
from src.transport import Transport


class DummyTransport(Transport):
    """In-memory mock transport for testing chunk delivery and closure."""

    def __init__(self, transport_id: str):
        super().__init__(transport_id)
        self._connected = True
        self.sent_data = []

    def is_connected(self) -> bool:
        return self._connected

    def connect(self, host: str = "127.0.0.1", port: int = 0, timeout: float = 10.0) -> None:
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False

    def send(self, data: bytes) -> int:
        if not self._connected:
            raise ConnectionError(f"Transport {self.transport_id} is disconnected")
        self.sent_data.append(data)
        return len(data)

    def recv(self, n: int) -> bytes:
        return b""


def test_multi_transport_same_file_partial_completion(tmp_path):
    dest_dir = tmp_path / "dest"
    dest_dir.mkdir(parents=True, exist_ok=True)

    # 1. Create a 640 KB test payload (10 chunks of 64 KB each)
    chunk_size = 64 * 1024
    num_chunks = 10
    file_bytes = b"".join(os.urandom(chunk_size) for _ in range(num_chunks))
    file_size = len(file_bytes)
    file_sha256 = hashlib.sha256(file_bytes).hexdigest()

    fid = "11111111-2222-3333-4444-555555555555"
    info = TransferInfo(
        fid=fid,
        name="multi_path_test.bin",
        rel_path="multi_path_test.bin",
        size=file_size,
        total_chunks=num_chunks,
        chunk_size=chunk_size,
        sha256=file_sha256,
    )

    storage = StorageManager(dest_dir)
    resume = ResumeManager()
    scheduler = Scheduler()

    transport_wifi = DummyTransport("wifi-0")
    transport_usb = DummyTransport("usb-0")
    scheduler.add_transport(transport_wifi)
    scheduler.add_transport(transport_usb)

    receiver = TransferManager(
        session_id="multi-test-session",
        scheduler=scheduler,
        resume_manager=resume,
        storage_manager=storage,
    )
    receiver.setup_receive([info])

    # Build chunk frames
    chunk_frames = []
    fid_bytes = bytes.fromhex(fid.replace("-", ""))
    for cid in range(num_chunks):
        offset = cid * chunk_size
        cdata = file_bytes[offset : offset + chunk_size]
        csum = IntegrityManager.chunk_checksum(cdata)
        frame = ChunkFrame(
            transfer_id=b"TID_" + b"0" * 12,
            file_id=fid_bytes,
            chunk_id=cid,
            offset=offset,
            data=cdata,
            checksum=csum,
            frame_type=FrameType.DATA,
        )
        chunk_frames.append(frame)

    # 2. Wi-Fi receives even chunks: 0, 2, 4, 6, 8
    for cid in [0, 2, 4, 6, 8]:
        ok, reason = receiver.receive_chunk(chunk_frames[cid])
        assert ok is True
        assert reason == ""

    # Check state: only 5 of 10 chunks received
    ctx = receiver._files[fid]
    assert len(ctx.chunk_manager.received_chunks()) == 5
    assert not ctx.chunk_manager.is_complete()
    assert not receiver.is_file_complete(fid)
    assert not receiver.is_file_verified(fid)
    assert ctx.state == TransferState.PENDING or ctx.state == TransferState.ACTIVE

    # 3. Wi-Fi finishes all of its currently assigned chunks and transport closes
    transport_wifi.disconnect()
    assert not transport_wifi.is_connected()
    assert transport_usb.is_connected()

    # CRITICAL: Wi-Fi closing must NEVER cause the file to be considered complete
    assert not ctx.chunk_manager.is_complete()
    assert not receiver.is_file_complete(fid)
    assert not receiver.is_file_verified(fid)

    # 4. Attempt premature finalization (e.g. if FILE_CHECKSUM arrives early)
    ok_fin, fin_err = receiver.finalize_file(fid)
    assert ok_fin is False
    assert fin_err == "chunks_missing"
    # Verify file was NOT finalized or corrupted/deleted
    assert ctx.state != TransferState.COMPLETED
    assert ctx.state != TransferState.FAILED
    assert ctx.tmp_path.exists()
    assert not (dest_dir / "multi_path_test.bin").exists()

    # 5. USB is STILL active and delivers chunks 1, 3, 5
    for cid in [1, 3, 5]:
        ok, reason = receiver.receive_chunk(chunk_frames[cid])
        assert ok is True

    # 8 of 10 chunks received: still not complete
    assert len(ctx.chunk_manager.received_chunks()) == 8
    assert not receiver.is_file_complete(fid)

    # 6. USB delivers remaining chunks 7, 9
    for cid in [7, 9]:
        ok, reason = receiver.receive_chunk(chunk_frames[cid])
        assert ok is True

    # 7. Now all 10 chunks are received
    assert len(ctx.chunk_manager.received_chunks()) == 10
    assert ctx.chunk_manager.is_complete()
    assert receiver.is_file_complete(fid)

    # 8. Finalize file now that all chunks across both transports are present
    ok_fin2, fin_err2 = receiver.finalize_file(fid)
    assert ok_fin2 is True
    assert fin_err2 == ""
    assert receiver.is_file_verified(fid)
    assert ctx.state == TransferState.COMPLETED

    final_path = dest_dir / "multi_path_test.bin"
    assert final_path.exists()
    assert final_path.stat().st_size == file_size

    with open(final_path, "rb") as f:
        actual_sha = hashlib.sha256(f.read()).hexdigest()
    assert actual_sha == file_sha256


def test_file_checksum_arriving_on_closed_transport_does_not_finalize(tmp_path):
    """
    Scenario:
    - Transport A receives its chunk subset and sender sends FILE_CHECKSUM on Transport A.
    - Chunks are still in-flight on Transport B.
    - Confirm FILE_CHECKSUM does not prematurely finalize or fail the transfer.
    """
    dest_dir = tmp_path / "dest2"
    dest_dir.mkdir(parents=True, exist_ok=True)

    chunk_size = 32 * 1024
    num_chunks = 4
    file_bytes = b"".join(os.urandom(chunk_size) for _ in range(num_chunks))
    file_size = len(file_bytes)
    file_sha256 = hashlib.sha256(file_bytes).hexdigest()

    fid = "22222222-3333-4444-5555-666666666666"
    info = TransferInfo(
        fid=fid,
        name="test_checksum_timing.bin",
        rel_path="test_checksum_timing.bin",
        size=file_size,
        total_chunks=num_chunks,
        chunk_size=chunk_size,
        sha256="",  # Post-transfer checksum mode
    )

    storage = StorageManager(dest_dir)
    resume = ResumeManager()
    scheduler = Scheduler()

    t_a = DummyTransport("transport-a")
    t_b = DummyTransport("transport-b")
    scheduler.add_transport(t_a)
    scheduler.add_transport(t_b)

    receiver = TransferManager(
        session_id="multi-test-checksum",
        scheduler=scheduler,
        resume_manager=resume,
        storage_manager=storage,
    )
    receiver.setup_receive([info])

    fid_bytes = bytes.fromhex(fid.replace("-", ""))
    frames = [
        ChunkFrame(
            transfer_id=b"TID_" + b"0" * 12,
            file_id=fid_bytes,
            chunk_id=i,
            offset=i * chunk_size,
            data=file_bytes[i * chunk_size : (i + 1) * chunk_size],
            checksum=IntegrityManager.chunk_checksum(file_bytes[i * chunk_size : (i + 1) * chunk_size]),
            frame_type=FrameType.DATA,
        )
        for i in range(num_chunks)
    ]

    # Transport A delivers chunks 0 and 1
    receiver.receive_chunk(frames[0])
    receiver.receive_chunk(frames[1])

    # Transport A closes
    t_a.disconnect()

    # Sender sends FILE_CHECKSUM message
    receiver.set_file_checksum(fid, file_sha256)

    # File must not be complete yet
    assert not receiver.is_file_complete(fid)

    # Attempting to finalize returns chunks_missing without corrupting state
    ok, err = receiver.finalize_file(fid)
    assert not ok
    assert err == "chunks_missing"
    assert receiver._files[fid].state != TransferState.FAILED
    assert receiver._files[fid].state != TransferState.COMPLETED

    # Transport B delivers remaining chunks 2 and 3
    receiver.receive_chunk(frames[2])
    receiver.receive_chunk(frames[3])

    # Now all chunks are present and checksum is known
    assert receiver.is_file_complete(fid)
    ok_fin, err_fin = receiver.finalize_file(fid)
    assert ok_fin is True
    assert err_fin == ""
    assert receiver.is_file_verified(fid)

    final_path = dest_dir / "test_checksum_timing.bin"
    assert final_path.exists()
    assert final_path.stat().st_size == file_size
    with open(final_path, "rb") as f:
        assert hashlib.sha256(f.read()).hexdigest() == file_sha256


def test_usb_closes_first_wifi_completes_second(tmp_path):
    """
    Symmetric test: USB receives its subset and closes; Wi-Fi continues and finishes the file.
    """
    dest_dir = tmp_path / "dest_usb_first"
    dest_dir.mkdir(parents=True, exist_ok=True)

    chunk_size = 64 * 1024
    num_chunks = 6
    file_bytes = b"".join(os.urandom(chunk_size) for _ in range(num_chunks))
    file_size = len(file_bytes)
    file_sha256 = hashlib.sha256(file_bytes).hexdigest()

    fid = "33333333-4444-5555-6666-777777777777"
    info = TransferInfo(
        fid=fid,
        name="usb_first_test.bin",
        rel_path="usb_first_test.bin",
        size=file_size,
        total_chunks=num_chunks,
        chunk_size=chunk_size,
        sha256=file_sha256,
    )

    storage = StorageManager(dest_dir)
    resume = ResumeManager()
    scheduler = Scheduler()

    t_wifi = DummyTransport("wifi-main")
    t_usb = DummyTransport("usb-main")
    scheduler.add_transport(t_wifi)
    scheduler.add_transport(t_usb)

    receiver = TransferManager(
        session_id="session-usb-first",
        scheduler=scheduler,
        resume_manager=resume,
        storage_manager=storage,
    )
    receiver.setup_receive([info])

    fid_bytes = bytes.fromhex(fid.replace("-", ""))
    frames = [
        ChunkFrame(
            transfer_id=b"TID_" + b"0" * 12,
            file_id=fid_bytes,
            chunk_id=i,
            offset=i * chunk_size,
            data=file_bytes[i * chunk_size : (i + 1) * chunk_size],
            checksum=IntegrityManager.chunk_checksum(file_bytes[i * chunk_size : (i + 1) * chunk_size]),
            frame_type=FrameType.DATA,
        )
        for i in range(num_chunks)
    ]

    # USB receives chunks 1, 3, 5
    for cid in [1, 3, 5]:
        ok, _ = receiver.receive_chunk(frames[cid])
        assert ok is True

    # USB closes
    t_usb.disconnect()
    assert not t_usb.is_connected()
    assert t_wifi.is_connected()

    # File must NOT be complete or verified
    assert not receiver.is_file_complete(fid)
    assert not receiver.is_file_verified(fid)

    # Wi-Fi is still transferring and receives chunks 0, 2
    receiver.receive_chunk(frames[0])
    receiver.receive_chunk(frames[2])
    assert not receiver.is_file_complete(fid)

    # Wi-Fi delivers the final chunk 4
    receiver.receive_chunk(frames[4])
    assert receiver.is_file_complete(fid)

    # Only now does finalization succeed
    ok_fin, err = receiver.finalize_file(fid)
    assert ok_fin is True
    assert err == ""
    assert receiver.is_file_verified(fid)

    final_path = dest_dir / "usb_first_test.bin"
    assert final_path.exists()
    assert final_path.stat().st_size == file_size
    with open(final_path, "rb") as f:
        assert hashlib.sha256(f.read()).hexdigest() == file_sha256

