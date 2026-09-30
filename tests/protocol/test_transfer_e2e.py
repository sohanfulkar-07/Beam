"""
PhotoBeam Protocol — End-to-End Transfer Tests

Tests complete transfer flow: prepare_files -> send_file -> receive_chunk -> finalize_file
with in-memory transports and real files on disk.
"""
import os
import shutil
import tempfile
import uuid
import sys
from pathlib import Path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'protocol'))

import pytest

from src.models import ChunkFrame, TransferInfo, TransferState, CHUNK_HEADER_SIZE
from src.resume import ResumeManager
from src.scheduler import Scheduler
from src.storage import StorageManager
from src.transfer import TransferError, TransferManager
from src.transport import Transport, TransportStatus


class PipeTransport(Transport):
    """Simple in-memory byte pipe transport for testing."""

    def __init__(self, transport_id: str = "pipe-0"):
        super().__init__(transport_id)
        self._buf = bytearray()
        self._status = TransportStatus.CONNECTED

    def connect(self, host: str, port: int, timeout: float = 10.0) -> None:
        self._status = TransportStatus.CONNECTED

    def disconnect(self) -> None:
        self._status = TransportStatus.DISCONNECTED

    def send(self, data: bytes) -> int:
        self._buf.extend(data)
        return len(data)

    def recv(self, n: int) -> bytes:
        chunk = bytes(self._buf[:n])
        del self._buf[:n]
        return chunk

    def has_data(self) -> bool:
        return len(self._buf) > 0


@pytest.fixture
def temp_dirs():
    src_dir = Path(tempfile.mkdtemp(prefix="pb_test_src_"))
    dst_dir = Path(tempfile.mkdtemp(prefix="pb_test_dst_"))
    resume_dir = Path(tempfile.mkdtemp(prefix="pb_test_res_"))
    yield src_dir, dst_dir, resume_dir
    shutil.rmtree(src_dir, ignore_errors=True)
    shutil.rmtree(dst_dir, ignore_errors=True)
    shutil.rmtree(resume_dir, ignore_errors=True)


def test_transfer_single_file_e2e(temp_dirs):
    src_dir, dst_dir, res_dir = temp_dirs

    # Create source file (64 KB)
    test_content = os.urandom(64 * 1024)
    file_a = src_dir / "sample_image.jpg"
    file_a.write_bytes(test_content)

    session_id = str(uuid.uuid4())
    pipe = PipeTransport("pipe-1")

    sched_send = Scheduler()
    sched_send.add_transport(pipe)
    sched_recv = Scheduler()
    resume_send = ResumeManager(res_dir)
    resume_recv = ResumeManager(res_dir)
    storage_recv = StorageManager(dst_dir)

    # 16 KB chunks -> 4 chunks
    chunk_size = 16 * 1024
    sender = TransferManager(
        session_id=session_id,
        scheduler=sched_send,
        resume_manager=resume_send,
        chunk_size=chunk_size,
    )
    receiver = TransferManager(
        session_id=session_id,
        scheduler=sched_recv,
        resume_manager=resume_recv,
        storage_manager=storage_recv,
        chunk_size=chunk_size,
    )

    # Step 1: Sender prepares files
    infos = sender.prepare_files([file_a])
    assert len(infos) == 1
    info = infos[0]
    assert info.size == 64 * 1024
    assert info.total_chunks == 4

    # Step 2: Receiver sets up receive
    receiver.setup_receive(infos)

    # Step 3: Sender streams chunks into transport
    sender.send_file(info.fid, ack_callback=lambda f, c: None)

    # Step 4: Receiver pulls chunk frames from transport and receives them
    frames_received = 0
    while pipe.has_data():
        header_bytes = pipe.recv_exact(CHUNK_HEADER_SIZE)
        data_len = int.from_bytes(header_bytes[56:60], "big")
        data_bytes = pipe.recv_exact(data_len)
        frame = ChunkFrame.decode(header_bytes + data_bytes)

        ok, reason = receiver.receive_chunk(frame)
        assert ok, f"Chunk receive failed: {reason}"
        frames_received += 1

    assert frames_received == 4
    assert receiver.is_file_complete(info.fid)

    # Step 5: Finalize file
    ok, err = receiver.finalize_file(info.fid)
    assert ok, f"Finalize failed: {err}"

    # Verify received file on disk
    dest_file = dst_dir / "sample_image.jpg"
    assert dest_file.exists()
    assert dest_file.read_bytes() == test_content


def test_transfer_multiple_files_e2e(temp_dirs):
    src_dir, dst_dir, res_dir = temp_dirs

    content1 = b"Hello, World! " * 500
    content2 = os.urandom(32 * 1024)
    file1 = src_dir / "doc.txt"
    file2 = src_dir / "photo.png"
    file1.write_bytes(content1)
    file2.write_bytes(content2)

    session_id = str(uuid.uuid4())
    pipe = PipeTransport("pipe-multi")

    sched_s = Scheduler()
    sched_s.add_transport(pipe)
    sender = TransferManager(session_id, sched_s, ResumeManager(res_dir), chunk_size=8192)
    receiver = TransferManager(session_id, Scheduler(), ResumeManager(res_dir), StorageManager(dst_dir), chunk_size=8192)

    infos = sender.prepare_files([file1, file2])
    assert len(infos) == 2
    receiver.setup_receive(infos)

    for info in infos:
        sender.send_file(info.fid, ack_callback=lambda f, c: None)
        while pipe.has_data():
            header = pipe.recv_exact(CHUNK_HEADER_SIZE)
            data_len = int.from_bytes(header[56:60], "big")
            data = pipe.recv_exact(data_len)
            frame = ChunkFrame.decode(header + data)
            ok, _ = receiver.receive_chunk(frame)
            assert ok

        assert receiver.is_file_complete(info.fid)
        ok, err = receiver.finalize_file(info.fid)
        assert ok, f"Error: {err}"

    assert (dst_dir / "doc.txt").read_bytes() == content1
    assert (dst_dir / "photo.png").read_bytes() == content2


def test_corrupted_chunk_rejected(temp_dirs):
    src_dir, dst_dir, res_dir = temp_dirs
    file1 = src_dir / "test.bin"
    file1.write_bytes(b"A" * 1024)

    session_id = str(uuid.uuid4())
    sender = TransferManager(session_id, Scheduler(), ResumeManager(res_dir), chunk_size=512)
    receiver = TransferManager(session_id, Scheduler(), ResumeManager(res_dir), StorageManager(dst_dir), chunk_size=512)

    infos = sender.prepare_files([file1])
    receiver.setup_receive(infos)

    # Construct frame with bad checksum
    fid_bytes = uuid.UUID(infos[0].fid).bytes
    bad_frame = ChunkFrame(
        transfer_id=uuid.uuid4().bytes,
        file_id=fid_bytes,
        chunk_id=0,
        offset=0,
        data=b"Corrupted data",
        checksum=0xDEADBEEF,  # Wrong checksum
    )

    ok, reason = receiver.receive_chunk(bad_frame)
    assert not ok
    assert reason == "bad_checksum"
    assert not receiver.is_file_complete(infos[0].fid)


def test_out_of_order_chunks_assemble_correctly(temp_dirs):
    src_dir, dst_dir, res_dir = temp_dirs
    content = b"Part0-1234567890" + b"Part1-abcdefghij" + b"Part2-ABCDEFGHIJ"
    file1 = src_dir / "ordered.dat"
    file1.write_bytes(content)

    session_id = str(uuid.uuid4())
    pipe = PipeTransport("pipe-ooo")
    sched_ooo = Scheduler()
    sched_ooo.add_transport(pipe)
    sender = TransferManager(session_id, sched_ooo, ResumeManager(res_dir), chunk_size=16)
    receiver = TransferManager(session_id, Scheduler(), ResumeManager(res_dir), StorageManager(dst_dir), chunk_size=16)

    infos = sender.prepare_files([file1])
    assert infos[0].total_chunks == 3
    receiver.setup_receive(infos)

    sender.send_file(infos[0].fid, ack_callback=lambda f, c: None)

    # Collect all frames
    frames = []
    while pipe.has_data():
        hdr = pipe.recv_exact(CHUNK_HEADER_SIZE)
        d_len = int.from_bytes(hdr[56:60], "big")
        data = pipe.recv_exact(d_len)
        frames.append(ChunkFrame.decode(hdr + data))

    # Deliver in reverse order (chunk 2, chunk 1, chunk 0)
    for frame in reversed(frames):
        ok, _ = receiver.receive_chunk(frame)
        assert ok

    assert receiver.is_file_complete(infos[0].fid)
    ok, _ = receiver.finalize_file(infos[0].fid)
    assert ok
    assert (dst_dir / "ordered.dat").read_bytes() == content


def test_transfer_multi_path_simultaneous_transports(temp_dirs):
    """Verify that multiple transports (e.g. Wi-Fi + USB) simultaneously receive chunks and correctly assemble the file."""
    src_dir, dst_dir, res_dir = temp_dirs
    # 80 KB file, 8 KB chunk size = 10 chunks
    content = os.urandom(80 * 1024)
    file1 = src_dir / "multipath_test.bin"
    file1.write_bytes(content)

    session_id = str(uuid.uuid4())
    pipe_wifi = PipeTransport("pipe-wifi")
    pipe_usb = PipeTransport("pipe-usb")

    sched = Scheduler()
    sched.add_transport(pipe_wifi)
    sched.add_transport(pipe_usb)

    sender = TransferManager(session_id, sched, ResumeManager(res_dir), chunk_size=8 * 1024)
    receiver = TransferManager(session_id, Scheduler(), ResumeManager(res_dir), StorageManager(dst_dir), chunk_size=8 * 1024)

    infos = sender.prepare_files([file1])
    assert infos[0].total_chunks == 10
    receiver.setup_receive(infos)

    sender.send_file(infos[0].fid, ack_callback=lambda f, c: None)

    # Verify both transports transmitted data
    assert pipe_wifi.has_data(), "Wi-Fi transport should have received chunks"
    assert pipe_usb.has_data(), "USB transport should have received chunks"

    # Pull frames from both pipes interleaved
    wifi_frames = 0
    usb_frames = 0
    while pipe_wifi.has_data() or pipe_usb.has_data():
        if pipe_wifi.has_data():
            hdr = pipe_wifi.recv_exact(CHUNK_HEADER_SIZE)
            d_len = int.from_bytes(hdr[56:60], "big")
            data = pipe_wifi.recv_exact(d_len)
            frame = ChunkFrame.decode(hdr + data)
            ok, _ = receiver.receive_chunk(frame)
            assert ok
            wifi_frames += 1

        if pipe_usb.has_data():
            hdr = pipe_usb.recv_exact(CHUNK_HEADER_SIZE)
            d_len = int.from_bytes(hdr[56:60], "big")
            data = pipe_usb.recv_exact(d_len)
            frame = ChunkFrame.decode(hdr + data)
            ok, _ = receiver.receive_chunk(frame)
            assert ok
            usb_frames += 1

    assert wifi_frames > 0 and usb_frames > 0, f"Expected chunks on both transports, got wifi={wifi_frames}, usb={usb_frames}"
    assert wifi_frames + usb_frames == 10
    assert receiver.is_file_complete(infos[0].fid)
    ok, _ = receiver.finalize_file(infos[0].fid)
    assert ok
    assert (dst_dir / "multipath_test.bin").read_bytes() == content


def test_transfer_resume_interrupted_file(temp_dirs):
    """Verify that an interrupted transfer can resume and complete missing chunks without resending everything."""
    src_dir, dst_dir, res_dir = temp_dirs
    # 32 KB file, 8 KB chunk size = 4 chunks
    content = os.urandom(32 * 1024)
    file1 = src_dir / "resumable.bin"
    file1.write_bytes(content)

    session_id = str(uuid.uuid4())
    pipe = PipeTransport("pipe-res")
    sched = Scheduler()
    sched.add_transport(pipe)

    resume_store = ResumeManager(res_dir)
    storage_store = StorageManager(dst_dir)

    sender = TransferManager(session_id, sched, resume_store, chunk_size=8 * 1024)
    receiver = TransferManager(session_id, Scheduler(), resume_store, storage_store, chunk_size=8 * 1024)

    infos = sender.prepare_files([file1])
    fid = infos[0].fid
    assert infos[0].total_chunks == 4
    receiver.setup_receive(infos)

    # Sender generates all 4 frames
    frames = list(sender._files[fid].chunk_manager.iter_chunk_frames(file1))
    assert len(frames) == 4

    # Deliver only first 2 chunks (chunks 0 and 1)
    ok0, _ = receiver.receive_chunk(frames[0])
    ok1, _ = receiver.receive_chunk(frames[1])
    assert ok0 and ok1

    # Receiver persists state before interruption
    receiver._persist_resume_state(receiver._files[fid])
    assert not receiver.is_file_complete(fid)

    # --- SIMULATE DISCONNECT & RESUME ---
    # New receiver instance initialized with same session
    receiver_resumed = TransferManager(session_id, Scheduler(), resume_store, storage_store, chunk_size=8 * 1024)
    receiver_resumed.setup_receive(infos)

    # Verify receiver restored received chunks
    cm = receiver_resumed._files[fid].chunk_manager
    assert cm.received_chunks() == [0, 1]
    assert receiver_resumed.get_missing_chunks(fid) == [2, 3]

    # Sender sends ONLY the missing chunks (2 and 3)
    ok2, _ = receiver_resumed.receive_chunk(frames[2])
    ok3, _ = receiver_resumed.receive_chunk(frames[3])
    assert ok2 and ok3

    # File should now be complete and verified
    assert receiver_resumed.is_file_complete(fid)
    ok, _ = receiver_resumed.finalize_file(fid)
    assert ok
    assert (dst_dir / "resumable.bin").read_bytes() == content


