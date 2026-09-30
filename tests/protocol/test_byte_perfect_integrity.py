"""
PhotoBeam Protocol — Byte-Perfect Fidelity & Integrity Tests

Guarantees byte-for-byte exactness, original filename/extension preservation,
disk streaming, and strict SHA-256 integrity verification across all file formats
and transport conditions.
"""
from __future__ import annotations

import hashlib
import os
import random
import shutil
import struct
import tempfile
import uuid
import zipfile
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "protocol"))

import pytest

from src.integrity import IntegrityManager
# pyrefly: ignore [missing-import]
from src.models import (
    CHUNK_HEADER_SIZE,
    ChunkFrame,
    FrameType,
    TransferInfo,
    TransferState,
)
from src.resume import ResumeManager
from src.scheduler import Scheduler
from src.storage import StorageManager
from src.transfer import TransferManager
from src.transport import Transport, TransportStatus


class MemoryPipeTransport(Transport):
    """In-memory byte pipe transport for testing."""

    def __init__(self, transport_id: str = "mem-pipe-0"):
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
def transfer_env():
    src_dir = Path(tempfile.mkdtemp(prefix="pb_test_src_"))
    dst_dir = Path(tempfile.mkdtemp(prefix="pb_test_dst_"))
    res_dir = Path(tempfile.mkdtemp(prefix="pb_test_res_"))
    yield src_dir, dst_dir, res_dir
    shutil.rmtree(src_dir, ignore_errors=True)
    shutil.rmtree(dst_dir, ignore_errors=True)
    shutil.rmtree(res_dir, ignore_errors=True)


def _run_transfer_pipeline(src_file: Path, dst_dir: Path, res_dir: Path, chunk_size: int = 16384) -> Path:
    """Helper to run standard transfer pipeline and return received file path."""
    session_id = str(uuid.uuid4())
    pipe = MemoryPipeTransport("pipe-main")
    sched_send = Scheduler()
    sched_send.add_transport(pipe)

    sender = TransferManager(
        session_id=session_id,
        scheduler=sched_send,
        resume_manager=ResumeManager(res_dir),
        chunk_size=chunk_size,
    )
    receiver = TransferManager(
        session_id=session_id,
        scheduler=Scheduler(),
        resume_manager=ResumeManager(res_dir),
        storage_manager=StorageManager(dst_dir),
        chunk_size=chunk_size,
    )

    infos = sender.prepare_files([src_file])
    info = infos[0]
    receiver.setup_receive(infos)

    sender.send_file(info.fid, ack_callback=lambda f, c: None)

    # Deliver all frames
    while pipe.has_data():
        header_bytes = pipe.recv_exact(CHUNK_HEADER_SIZE)
        data_len = int.from_bytes(header_bytes[56:60], "big")
        data_bytes = pipe.recv_exact(data_len)
        frame = ChunkFrame.decode(header_bytes + data_bytes)
        ok, reason = receiver.receive_chunk(frame)
        assert ok, f"Chunk receive failed: {reason}"

    assert receiver.is_file_complete(info.fid)
    ok, err = receiver.finalize_file(info.fid)
    assert ok, f"Finalize failed: {err}"
    assert receiver.is_file_verified(info.fid)

    dst_file = dst_dir / src_file.name
    return dst_file


def _assert_byte_for_byte_identical(src_path: Path, dst_path: Path) -> None:
    """Strictly assert SHA-256, file size, and filename are 100% identical."""
    assert dst_path.exists(), f"Destination file does not exist: {dst_path}"
    assert src_path.name == dst_path.name, f"Filename mismatch: {src_path.name} != {dst_path.name}"
    assert src_path.stat().st_size == dst_path.stat().st_size, (
        f"File size mismatch: {src_path.stat().st_size} != {dst_path.stat().st_size}"
    )

    src_sha256 = IntegrityManager.file_hash(src_path)
    dst_sha256 = IntegrityManager.file_hash(dst_path)
    assert src_sha256.lower() == dst_sha256.lower(), f"SHA-256 mismatch: {src_sha256} != {dst_sha256}"
    assert src_path.read_bytes() == dst_path.read_bytes(), "Raw byte content mismatch!"


# ── Format-Specific Byte-Fidelity Tests ────────────────────────────────────────

def test_small_text_file(transfer_env):
    src_dir, dst_dir, res_dir = transfer_env
    src = src_dir / "notes.txt"
    src.write_text("PhotoBeam byte-for-byte fidelity test.\nHello World! 🚀 12345\n", encoding="utf-8")

    dst = _run_transfer_pipeline(src, dst_dir, res_dir)
    _assert_byte_for_byte_identical(src, dst)


def test_jpg_image(transfer_env):
    src_dir, dst_dir, res_dir = transfer_env
    src = src_dir / "IMG_20260923_184532.jpg"
    # Construct binary payload with valid JPEG header SOI and marker bytes
    jpeg_bytes = b"\xFF\xD8\xFF\xE0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00" + os.urandom(32768) + b"\xFF\xD9"
    src.write_bytes(jpeg_bytes)

    dst = _run_transfer_pipeline(src, dst_dir, res_dir)
    _assert_byte_for_byte_identical(src, dst)


def test_png_image(transfer_env):
    src_dir, dst_dir, res_dir = transfer_env
    src = src_dir / "screenshot.png"
    # Valid PNG header + payload
    png_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + os.urandom(16384) + b"\x00\x00\x00\x00IEND\xaeB`\x82"
    src.write_bytes(png_bytes)

    dst = _run_transfer_pipeline(src, dst_dir, res_dir)
    _assert_byte_for_byte_identical(src, dst)


def test_mp4_video(transfer_env):
    src_dir, dst_dir, res_dir = transfer_env
    src = src_dir / "VID_20260923_190000.mp4"
    # Valid MP4 box structure
    ftyp = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00isommp42"
    mdat_header = struct.pack(">I4s", 65536, b"mdat")
    mp4_bytes = ftyp + mdat_header + os.urandom(65536 - 8)
    src.write_bytes(mp4_bytes)

    dst = _run_transfer_pipeline(src, dst_dir, res_dir, chunk_size=8192)
    _assert_byte_for_byte_identical(src, dst)


def test_pdf_document(transfer_env):
    src_dir, dst_dir, res_dir = transfer_env
    src = src_dir / "manual.pdf"
    pdf_bytes = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\n" + os.urandom(20000) + b"\n%%EOF\n"
    src.write_bytes(pdf_bytes)

    dst = _run_transfer_pipeline(src, dst_dir, res_dir)
    _assert_byte_for_byte_identical(src, dst)


def test_zip_archive(transfer_env):
    src_dir, dst_dir, res_dir = transfer_env
    src = src_dir / "backup.zip"
    with zipfile.ZipFile(src, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("file1.txt", "Content of file 1 inside archive")
        zf.writestr("nested/data.bin", os.urandom(10000))

    dst = _run_transfer_pipeline(src, dst_dir, res_dir)
    _assert_byte_for_byte_identical(src, dst)


def test_large_file_streaming(transfer_env):
    """Verify 12 MB file streams in chunks and is byte-for-byte identical without loading all in RAM."""
    src_dir, dst_dir, res_dir = transfer_env
    src = src_dir / "large_asset.iso"
    # 12 MB
    size = 12 * 1024 * 1024
    chunk_size = 1 * 1024 * 1024  # 1 MB chunks
    with src.open("wb") as f:
        for _ in range(12):
            f.write(os.urandom(1024 * 1024))

    dst = _run_transfer_pipeline(src, dst_dir, res_dir, chunk_size=chunk_size)
    _assert_byte_for_byte_identical(src, dst)


def test_binary_random_data(transfer_env):
    src_dir, dst_dir, res_dir = transfer_env
    src = src_dir / "random.bin"
    src.write_bytes(os.urandom(256 * 1024))

    dst = _run_transfer_pipeline(src, dst_dir, res_dir)
    _assert_byte_for_byte_identical(src, dst)


def test_file_containing_null_bytes(transfer_env):
    src_dir, dst_dir, res_dir = transfer_env
    src = src_dir / "null_padded.dat"
    # Null byte sequences interspersed with arbitrary bytes
    content = b"\x00" * 8192 + b"\xFF\xFE\x00\x00" + os.urandom(4096) + b"\x00" * 16384 + b"END\x00\x00"
    src.write_bytes(content)

    dst = _run_transfer_pipeline(src, dst_dir, res_dir)
    _assert_byte_for_byte_identical(src, dst)


def test_file_with_unusual_characters_in_filename(transfer_env):
    src_dir, dst_dir, res_dir = transfer_env
    filename = "IMG_20260923 (Draft #1) [Final & Verified] v1.2.jpg"
    src = src_dir / filename
    src.write_bytes(b"\xFF\xD8\xFF" + os.urandom(1024) + b"\xFF\xD9")

    dst = _run_transfer_pipeline(src, dst_dir, res_dir)
    _assert_byte_for_byte_identical(src, dst)
    assert dst.name == filename


# ── Transport, Chunking & Out-of-Order Tests ───────────────────────────────────

def test_multi_transport_transfer(transfer_env):
    """Simulate Wi-Fi + USB dual-transport simultaneously carrying interleaved chunks."""
    src_dir, dst_dir, res_dir = transfer_env
    src = src_dir / "dual_transport.mp4"
    src.write_bytes(os.urandom(64 * 1024))

    session_id = str(uuid.uuid4())
    pipe_wifi = MemoryPipeTransport("pipe-wifi")
    pipe_usb = MemoryPipeTransport("pipe-usb")

    sched = Scheduler()
    sched.add_transport(pipe_wifi)
    sched.add_transport(pipe_usb)

    sender = TransferManager(session_id, sched, ResumeManager(res_dir), chunk_size=4096)
    receiver = TransferManager(session_id, Scheduler(), ResumeManager(res_dir), StorageManager(dst_dir), chunk_size=4096)

    infos = sender.prepare_files([src])
    info = infos[0]
    receiver.setup_receive(infos)
    sender.send_file(info.fid, ack_callback=lambda f, c: None)

    assert pipe_wifi.has_data() and pipe_usb.has_data()

    # Read interleaved
    while pipe_wifi.has_data() or pipe_usb.has_data():
        if pipe_wifi.has_data():
            hdr = pipe_wifi.recv_exact(CHUNK_HEADER_SIZE)
            d_len = int.from_bytes(hdr[56:60], "big")
            data = pipe_wifi.recv_exact(d_len)
            ok, _ = receiver.receive_chunk(ChunkFrame.decode(hdr + data))
            assert ok
        if pipe_usb.has_data():
            hdr = pipe_usb.recv_exact(CHUNK_HEADER_SIZE)
            d_len = int.from_bytes(hdr[56:60], "big")
            data = pipe_usb.recv_exact(d_len)
            ok, _ = receiver.receive_chunk(ChunkFrame.decode(hdr + data))
            assert ok

    assert receiver.is_file_complete(info.fid)
    ok, err = receiver.finalize_file(info.fid)
    assert ok, f"Finalize failed: {err}"

    dst = dst_dir / src.name
    _assert_byte_for_byte_identical(src, dst)


def test_out_of_order_chunks(transfer_env):
    """Chunks arrive completely shuffled / out-of-order; receiver must place them at exact offsets."""
    src_dir, dst_dir, res_dir = transfer_env
    src = src_dir / "shuffled.bin"
    content = os.urandom(64 * 1024)
    src.write_bytes(content)

    session_id = str(uuid.uuid4())
    pipe = MemoryPipeTransport("pipe-ooo")
    sched = Scheduler()
    sched.add_transport(pipe)

    sender = TransferManager(session_id, sched, ResumeManager(res_dir), chunk_size=4096)
    receiver = TransferManager(session_id, Scheduler(), ResumeManager(res_dir), StorageManager(dst_dir), chunk_size=4096)

    infos = sender.prepare_files([src])
    receiver.setup_receive(infos)
    sender.send_file(infos[0].fid, ack_callback=lambda f, c: None)

    # Collect all frames
    frames = []
    while pipe.has_data():
        hdr = pipe.recv_exact(CHUNK_HEADER_SIZE)
        d_len = int.from_bytes(hdr[56:60], "big")
        data = pipe.recv_exact(d_len)
        frames.append(ChunkFrame.decode(hdr + data))

    # Shuffle frames
    random.seed(42)
    random.shuffle(frames)

    for frame in frames:
        ok, _ = receiver.receive_chunk(frame)
        assert ok

    assert receiver.is_file_complete(infos[0].fid)
    ok, err = receiver.finalize_file(infos[0].fid)
    assert ok, f"Finalize failed: {err}"

    dst = dst_dir / src.name
    _assert_byte_for_byte_identical(src, dst)


def test_resume_after_interrupted_transfer(transfer_env):
    """Transfer interrupted after receiving subset of chunks, resumed with only missing chunks."""
    src_dir, dst_dir, res_dir = transfer_env
    src = src_dir / "interrupted_video.mp4"
    content = os.urandom(32 * 1024)
    src.write_bytes(content)

    session_id = str(uuid.uuid4())
    pipe = MemoryPipeTransport("pipe-res")
    sched = Scheduler()
    sched.add_transport(pipe)

    resume_mgr = ResumeManager(res_dir)
    storage_mgr = StorageManager(dst_dir)

    sender = TransferManager(session_id, sched, resume_mgr, chunk_size=4096)
    receiver = TransferManager(session_id, Scheduler(), resume_mgr, storage_mgr, chunk_size=4096)

    infos = sender.prepare_files([src])
    fid = infos[0].fid
    receiver.setup_receive(infos)

    frames = list(sender._files[fid].chunk_manager.iter_chunk_frames(src))
    assert len(frames) == 8

    # Deliver first 3 chunks
    for i in range(3):
        ok, _ = receiver.receive_chunk(frames[i])
        assert ok

    # Save resume state and simulate interruption
    receiver._persist_resume_state(receiver._files[fid])
    assert not receiver.is_file_complete(fid)

    # Resumed receiver
    resumed_receiver = TransferManager(session_id, Scheduler(), resume_mgr, storage_mgr, chunk_size=4096)
    resumed_receiver.setup_receive(infos)

    # Verify existing chunks recognized
    missing = resumed_receiver.get_missing_chunks(fid)
    assert missing == [3, 4, 5, 6, 7]

    # Deliver missing chunks
    for cid in missing:
        ok, _ = resumed_receiver.receive_chunk(frames[cid])
        assert ok

    assert resumed_receiver.is_file_complete(fid)
    ok, err = resumed_receiver.finalize_file(fid)
    assert ok, f"Finalize failed: {err}"

    dst = dst_dir / src.name
    _assert_byte_for_byte_identical(src, dst)


def test_missing_chunks_fails_finalization(transfer_env):
    """File cannot be finalized if chunks are missing."""
    src_dir, dst_dir, res_dir = transfer_env
    src = src_dir / "incomplete.bin"
    src.write_bytes(os.urandom(16 * 1024))

    session_id = str(uuid.uuid4())
    sender = TransferManager(session_id, Scheduler(), ResumeManager(res_dir), chunk_size=4096)
    receiver = TransferManager(session_id, Scheduler(), ResumeManager(res_dir), StorageManager(dst_dir), chunk_size=4096)

    infos = sender.prepare_files([src])
    fid = infos[0].fid
    receiver.setup_receive(infos)

    frames = list(sender._files[fid].chunk_manager.iter_chunk_frames(src))
    # Deliver only 2 of 4 chunks
    receiver.receive_chunk(frames[0])
    receiver.receive_chunk(frames[1])

    assert not receiver.is_file_complete(fid)
    assert not receiver.is_file_verified(fid)
    assert receiver.get_missing_chunks(fid) == [2, 3]


def test_corrupted_chunk_rejected(transfer_env):
    """A bit-flipped chunk fails XXH3-64 verification and is rejected."""
    src_dir, dst_dir, res_dir = transfer_env
    src = src_dir / "chunk_check.bin"
    src.write_bytes(b"Good chunk data 1234567890")

    session_id = str(uuid.uuid4())
    sender = TransferManager(session_id, Scheduler(), ResumeManager(res_dir), chunk_size=1024)
    receiver = TransferManager(session_id, Scheduler(), ResumeManager(res_dir), StorageManager(dst_dir), chunk_size=1024)

    infos = sender.prepare_files([src])
    receiver.setup_receive(infos)

    fid_bytes = uuid.UUID(infos[0].fid).bytes
    corrupted_frame = ChunkFrame(
        transfer_id=uuid.uuid4().bytes,
        file_id=fid_bytes,
        chunk_id=0,
        offset=0,
        data=b"Corrupted chunk data!",
        checksum=0x123456789ABCDEF0,  # Invalid checksum
    )

    ok, reason = receiver.receive_chunk(corrupted_frame)
    assert not ok
    assert reason == "bad_checksum"
    assert not receiver.is_file_complete(infos[0].fid)


def test_corrupted_final_file_rejected(transfer_env):
    """If disk file gets corrupted before finalization, finalize_file fails and removes temp file."""
    src_dir, dst_dir, res_dir = transfer_env
    src = src_dir / "tampered.bin"
    src.write_bytes(b"Original valid content " * 100)

    session_id = str(uuid.uuid4())
    sender = TransferManager(session_id, Scheduler(), ResumeManager(res_dir), chunk_size=512)
    storage = StorageManager(dst_dir)
    receiver = TransferManager(session_id, Scheduler(), ResumeManager(res_dir), storage, chunk_size=512)

    infos = sender.prepare_files([src])
    fid = infos[0].fid
    receiver.setup_receive(infos)

    frames = list(sender._files[fid].chunk_manager.iter_chunk_frames(src))
    for f in frames:
        receiver.receive_chunk(f)

    assert receiver.is_file_complete(fid)

    # Tamper with the temp file on disk (simulate bitflip)
    tmp_path = receiver._files[fid].tmp_path
    assert tmp_path.exists()
    with tmp_path.open("r+b") as tf:
        tf.seek(10)
        tf.write(b"TAMPERED_BYTES")

    ok, reason = receiver.finalize_file(fid)
    assert not ok
    assert reason == "hash_mismatch"
    assert not receiver.is_file_verified(fid)
    assert receiver._files[fid].state == TransferState.FAILED
    # Verify temp file cleaned up, final file does NOT exist
    assert not (dst_dir / src.name).exists()
    # Source file must be completely untouched
    assert src.exists()


def test_sha256_mismatch_fails_and_not_accepted(transfer_env):
    """When expected SHA-256 does not match received SHA-256, transfer fails."""
    src_dir, dst_dir, res_dir = transfer_env
    src = src_dir / "mismatch.jpg"
    src.write_bytes(b"File data 1234567890")

    session_id = str(uuid.uuid4())
    sender = TransferManager(session_id, Scheduler(), ResumeManager(res_dir), chunk_size=1024)
    storage = StorageManager(dst_dir)
    receiver = TransferManager(session_id, Scheduler(), ResumeManager(res_dir), storage, chunk_size=1024)

    infos = sender.prepare_files([src])
    # Alter the expected SHA-256 in metadata
    infos[0].sha256 = "0" * 64
    fid = infos[0].fid

    receiver.setup_receive(infos)
    frames = list(sender._files[fid].chunk_manager.iter_chunk_frames(src))
    for f in frames:
        receiver.receive_chunk(f)

    assert receiver.is_file_complete(fid)
    ok, err = receiver.finalize_file(fid)
    assert not ok
    assert err == "hash_mismatch"
    assert not receiver.is_file_verified(fid)
    assert not (dst_dir / src.name).exists()


def test_duplicate_chunk_handled_safely(transfer_env):
    """Duplicate chunks are acknowledged but not duplicated in file length or content."""
    src_dir, dst_dir, res_dir = transfer_env
    src = src_dir / "dup_test.bin"
    content = os.urandom(8192)
    src.write_bytes(content)

    session_id = str(uuid.uuid4())
    sender = TransferManager(session_id, Scheduler(), ResumeManager(res_dir), chunk_size=2048)
    receiver = TransferManager(session_id, Scheduler(), ResumeManager(res_dir), StorageManager(dst_dir), chunk_size=2048)

    infos = sender.prepare_files([src])
    fid = infos[0].fid
    receiver.setup_receive(infos)

    frames = list(sender._files[fid].chunk_manager.iter_chunk_frames(src))
    assert len(frames) == 4

    # Deliver chunk 0
    ok0, r0 = receiver.receive_chunk(frames[0])
    assert ok0 and r0 == ""

    # Deliver chunk 0 AGAIN (duplicate)
    ok0_dup, r0_dup = receiver.receive_chunk(frames[0])
    assert ok0_dup
    assert r0_dup == "duplicate"

    # Deliver remaining chunks
    for i in range(1, 4):
        ok, _ = receiver.receive_chunk(frames[i])
        assert ok

    assert receiver.is_file_complete(fid)
    ok, _ = receiver.finalize_file(fid)
    assert ok

    dst = dst_dir / src.name
    _assert_byte_for_byte_identical(src, dst)


def test_retransmitted_chunk_processed_correctly(transfer_env):
    """Frames with FrameType.RETRANSMIT are accepted and verified byte-perfectly."""
    src_dir, dst_dir, res_dir = transfer_env
    src = src_dir / "retransmit.bin"
    content = os.urandom(8192)
    src.write_bytes(content)

    session_id = str(uuid.uuid4())
    sender = TransferManager(session_id, Scheduler(), ResumeManager(res_dir), chunk_size=4096)
    receiver = TransferManager(session_id, Scheduler(), ResumeManager(res_dir), StorageManager(dst_dir), chunk_size=4096)

    infos = sender.prepare_files([src])
    fid = infos[0].fid
    receiver.setup_receive(infos)

    # Chunk 0 as normal
    frame0 = sender._files[fid].chunk_manager.get_chunk_frame(src, 0)
    # Chunk 1 as RETRANSMIT frame type
    frame1 = sender._files[fid].chunk_manager.get_chunk_frame(src, 1)
    assert frame1.frame_type == FrameType.RETRANSMIT

    ok0, _ = receiver.receive_chunk(frame0)
    ok1, _ = receiver.receive_chunk(frame1)
    assert ok0 and ok1

    assert receiver.is_file_complete(fid)
    ok, _ = receiver.finalize_file(fid)
    assert ok

    dst = dst_dir / src.name
    _assert_byte_for_byte_identical(src, dst)


def test_high_byte_values_all_256_bytes(transfer_env):
    """Verify that all 256 byte values (0x00 to 0xFF) survive byte-for-byte with no signedness or encoding corruption."""
    src_dir, dst_dir, res_dir = transfer_env
    src = src_dir / "all_256_bytes.bin"
    # Repeating 0x00..0xFF across 64 KB
    pattern = bytes(range(256)) * 256
    # Also add blocks of exclusively high-bytes (0x80 to 0xFF)
    high_bytes = bytes(range(128, 256)) * 256
    src.write_bytes(pattern + high_bytes)

    dst = _run_transfer_pipeline(src, dst_dir, res_dir)
    _assert_byte_for_byte_identical(src, dst)


def test_unknown_and_missing_extensions(transfer_env):
    """Verify files with unknown extensions or no extension transfer with 100% fidelity and no special handling."""
    src_dir, dst_dir, res_dir = transfer_env
    files = [
        src_dir / "firmware.unknown",
        src_dir / "data_payload.custom999",
        src_dir / "standalone_binary_no_ext",
        src_dir / "packed.7z.001",
    ]
    for idx, f in enumerate(files):
        f.write_bytes(os.urandom(16384 + idx * 4096))
        dst = _run_transfer_pipeline(f, dst_dir, res_dir)
        _assert_byte_for_byte_identical(f, dst)


def test_mixed_file_types_batch(transfer_env):
    """Verify a batch of mixed file types (.bin, .exe, .iso, .apk, .mp4, .txt, .unknown) transfers simultaneously."""
    src_dir, dst_dir, res_dir = transfer_env
    file_specs = [
        ("setup.exe", b"MZ\x90\x00" + os.urandom(8192)),
        ("archive.bin", os.urandom(32768)),
        ("disk.iso", b"\x00" * 32768 + b"CD001" + os.urandom(16384)),
        ("app.apk", b"PK\x03\x04" + os.urandom(12288)),
        ("movie.mkv", b"\x1A\x45\xDF\xA3" + os.urandom(24576)),
        ("notes.txt", "Arbitrary text data with unicode 🚀 日本語".encode("utf-8")),
        ("blob.arbitrary_extension", os.urandom(10000)),
    ]

    src_paths = []
    for name, content in file_specs:
        p = src_dir / name
        p.write_bytes(content)
        src_paths.append(p)

    session_id = str(uuid.uuid4())
    pipe = MemoryPipeTransport("pipe-batch")
    sched = Scheduler()
    sched.add_transport(pipe)

    sender = TransferManager(session_id, sched, ResumeManager(res_dir), chunk_size=8192)
    receiver = TransferManager(session_id, Scheduler(), ResumeManager(res_dir), StorageManager(dst_dir), chunk_size=8192)

    # Sender prepares files without pre-read (compute_hash=False)
    infos = sender.prepare_files(src_paths, compute_hash=False)
    assert all(info.sha256 == "" for info in infos)
    receiver.setup_receive(infos)

    for info in infos:
        # Stream chunks and compute SHA-256 in stream
        computed_sha = sender.send_file(info.fid, ack_callback=lambda f, c: None)
        assert len(computed_sha) == 64

        # Read frames from pipe
        while pipe.has_data():
            hdr = pipe.recv_exact(CHUNK_HEADER_SIZE)
            d_len = int.from_bytes(hdr[56:60], "big")
            data = pipe.recv_exact(d_len)
            frame = ChunkFrame.decode(hdr + data)
            ok, _ = receiver.receive_chunk(frame)
            assert ok

        # Deliver FILE_CHECKSUM message to receiver
        receiver.set_file_checksum(info.fid, computed_sha)
        assert receiver.is_file_complete(info.fid)
        ok, err = receiver.finalize_file(info.fid)
        assert ok, f"Finalize failed for {info.name}: {err}"
        assert receiver.is_file_verified(info.fid)

        src_path = next(p for p in src_paths if p.name == info.name)
        dst_path = dst_dir / info.name
        _assert_byte_for_byte_identical(src_path, dst_path)


def test_streaming_hash_no_preread_immediate_transfer(transfer_env):
    """Verify that compute_hash=False allows instantaneous transfer start, followed by streaming SHA-256."""
    src_dir, dst_dir, res_dir = transfer_env
    src = src_dir / "instant_stream.bin"
    content = os.urandom(64 * 1024)
    src.write_bytes(content)

    session_id = str(uuid.uuid4())
    pipe = MemoryPipeTransport("pipe-instant")
    sched = Scheduler()
    sched.add_transport(pipe)

    sender = TransferManager(session_id, sched, ResumeManager(res_dir), chunk_size=16384)
    receiver = TransferManager(session_id, Scheduler(), ResumeManager(res_dir), StorageManager(dst_dir), chunk_size=16384)

    # Instant start: compute_hash=False
    infos = sender.prepare_files([src], compute_hash=False)
    info = infos[0]
    assert info.sha256 == ""  # Zero pre-read time!

    receiver.setup_receive([TransferInfo.from_dict(i.to_dict()) for i in infos])

    computed_sha = sender.send_file(info.fid, ack_callback=lambda f, c: None)
    expected_sha = hashlib.sha256(content).hexdigest()
    assert computed_sha == expected_sha

    while pipe.has_data():
        hdr = pipe.recv_exact(CHUNK_HEADER_SIZE)
        d_len = int.from_bytes(hdr[56:60], "big")
        data = pipe.recv_exact(d_len)
        frame = ChunkFrame.decode(hdr + data)
        ok, _ = receiver.receive_chunk(frame)
        assert ok

    # Receiver cannot finalize until checksum is known
    ok_early, err_early = receiver.finalize_file(info.fid)
    assert not ok_early
    assert err_early == "checksum_not_received"

    # Provide FILE_CHECKSUM
    receiver.set_file_checksum(info.fid, computed_sha)
    ok_final, err_final = receiver.finalize_file(info.fid)
    assert ok_final
    assert receiver.is_file_verified(info.fid)

    dst = dst_dir / src.name
    _assert_byte_for_byte_identical(src, dst)


def test_comprehensive_arbitrary_binary_batch_survives_byte_for_byte(transfer_env):
    """
    Comprehensive proof test:
    - Arbitrary binary bytes (including null bytes \\x00 and high bytes 0x80-0xFF)
    - Files with unknown extensions, multiple dots, and no extensions
    - Mixed simulated file types (.bin, .exe, .zip, .iso, .mp4)
    - Source files remain strictly untouched (unmodified mtime, size, sha)
    - Size-agnostic streaming I/O with zero pre-read (sha256 = '')
    - Byte-for-byte exactness: destination size = X, sha256 = Y
    """
    src_dir, dst_dir, res_dir = transfer_env

    # 1. Generate test files covering all requirement specs
    test_files_spec = [
        # (filename, binary_content)
        ("pure_nulls.bin", b"\x00" * (128 * 1024)),
        ("high_bytes.unknown", bytes(range(128, 256)) * 1024),
        ("all_256_bytes_interleaved.dat", bytes(range(256)) * 512),
        ("game_archive.part01.rar", b"RAR_SIMULATED\x00\xFF\xAA\x55" + os.urandom(64 * 1024)),
        ("installer.exe", b"MZ\x90\x00\x03\x00\x00\x00\x04\x00\x00\x00\xff\xff" + os.urandom(32 * 1024)),
        ("video_stream.mkv", b"\x1a\x45\xdf\xa3" + os.urandom(48 * 1024)),
        ("no_extension_arbitrary_binary", os.urandom(50 * 1024)),
        ("spaces and symbols !@#$%(^).bin", b"\x00\x01\x02\xfe\xff" + os.urandom(16 * 1024)),
    ]

    src_paths = []
    source_snapshots = {}

    for fname, data in test_files_spec:
        p = src_dir / fname
        p.write_bytes(data)
        src_paths.append(p)
        # Snapshot source file state before transfer
        source_snapshots[fname] = {
            "size": p.stat().st_size,
            "mtime": p.stat().st_mtime,
            "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": data,
        }

    session_id = str(uuid.uuid4())
    pipe = MemoryPipeTransport("pipe-comp")
    sched = Scheduler()
    sched.add_transport(pipe)

    sender = TransferManager(session_id, sched, ResumeManager(res_dir), chunk_size=16384)
    receiver = TransferManager(session_id, Scheduler(), ResumeManager(res_dir), StorageManager(dst_dir), chunk_size=16384)

    # Streaming I/O: zero pre-reading, transfer begins immediately
    infos = sender.prepare_files(src_paths, compute_hash=False)
    assert len(infos) == len(test_files_spec)
    assert all(info.sha256 == "" for info in infos)

    receiver.setup_receive([TransferInfo.from_dict(i.to_dict()) for i in infos])

    # Transfer each file in the batch
    for info in infos:
        # Sender streams chunks and computes sha256 on the fly
        computed_sha = sender.send_file(info.fid, ack_callback=lambda f, c: None)
        assert computed_sha == source_snapshots[info.name]["sha256"]

        # Transport frames across pipe
        while pipe.has_data():
            hdr = pipe.recv_exact(CHUNK_HEADER_SIZE)
            d_len = int.from_bytes(hdr[56:60], "big")
            data = pipe.recv_exact(d_len)
            frame = ChunkFrame.decode(hdr + data)
            ok, reason = receiver.receive_chunk(frame)
            assert ok, f"Chunk receive failed for {info.name}: {reason}"

        # Receiver receives FILE_CHECKSUM message upon EOF
        receiver.set_file_checksum(info.fid, computed_sha)
        assert receiver.is_file_complete(info.fid)
        ok, err = receiver.finalize_file(info.fid)
        assert ok, f"Finalize failed for {info.name}: {err}"
        assert receiver.is_file_verified(info.fid)

        # Byte-perfect verification on destination
        dst_p = dst_dir / info.name
        _assert_byte_for_byte_identical(src_dir / info.name, dst_p)

    # REQUIREMENT: Source files must remain completely untouched!
    for fname, snap in source_snapshots.items():
        src_p = src_dir / fname
        assert src_p.exists(), f"Source file was deleted or moved: {fname}"
        curr_stat = src_p.stat()
        assert curr_stat.st_size == snap["size"], f"Source file size altered: {fname}"
        assert curr_stat.st_mtime == snap["mtime"], f"Source file mtime altered: {fname}"
        curr_bytes = src_p.read_bytes()
        assert curr_bytes == snap["bytes"], f"Source file contents modified: {fname}"
        assert hashlib.sha256(curr_bytes).hexdigest() == snap["sha256"], f"Source hash changed: {fname}"


def test_out_of_order_chunks_with_streaming_sha256(transfer_env):
    """
    CRITICAL REGRESSION TEST: Out-of-order chunks + Streaming SHA-256.
    Proves that even when chunks arrive out of order (e.g. over multipath Wi-Fi + USB),
    the receiver's final SHA-256 verification is strictly based on reconstructed file offset order,
    matching the original file SHA-256 byte-for-byte.
    """
    src_dir, dst_dir, res_dir = transfer_env
    src = src_dir / "multipath_reconstructed.bin"
    # Create distinct chunk payloads so out-of-order writes without offset seek would corrupt
    raw_parts = [f"CHUNK_OFFSET_{i:04d}_".encode() * 256 for i in range(16)]
    full_content = b"".join(raw_parts)
    src.write_bytes(full_content)
    expected_sha256 = hashlib.sha256(full_content).hexdigest()

    session_id = str(uuid.uuid4())
    pipe = MemoryPipeTransport("pipe-ooo-stream")
    sched = Scheduler()
    sched.add_transport(pipe)

    sender = TransferManager(session_id, sched, ResumeManager(res_dir), chunk_size=len(raw_parts[0]))
    receiver = TransferManager(session_id, Scheduler(), ResumeManager(res_dir), StorageManager(dst_dir), chunk_size=len(raw_parts[0]))

    # Streaming SHA mode: zero pre-reading
    infos = sender.prepare_files([src], compute_hash=False)
    info = infos[0]
    assert info.sha256 == ""

    receiver.setup_receive([TransferInfo.from_dict(i.to_dict()) for i in infos])

    # Sender transmits chunks and computes streaming hash
    computed_sha = sender.send_file(info.fid, ack_callback=lambda f, c: None)
    assert computed_sha == expected_sha256

    # Drain frames from pipe
    frames = []
    while pipe.has_data():
        hdr = pipe.recv_exact(CHUNK_HEADER_SIZE)
        d_len = int.from_bytes(hdr[56:60], "big")
        data = pipe.recv_exact(d_len)
        frames.append(ChunkFrame.decode(hdr + data))

    assert len(frames) == 16

    # Simulate extreme out-of-order multipath delivery: reverse order + interleaving
    shuffled_frames = list(reversed(frames))
    # Chunks are delivered: 15, 14, 13, ..., 1, 0
    for frame in shuffled_frames:
        ok, reason = receiver.receive_chunk(frame)
        assert ok, f"Out of order chunk {frame.chunk_id} rejected: {reason}"

    # Receiver receives FILE_CHECKSUM message
    receiver.set_file_checksum(info.fid, computed_sha)
    assert receiver.is_file_complete(info.fid)

    # Receiver finalizes: reads assembled file from disk in offset order
    ok, err = receiver.finalize_file(info.fid)
    assert ok, f"Finalize failed on out-of-order reconstructed file: {err}"
    assert receiver.is_file_verified(info.fid)

    dst = dst_dir / src.name
    _assert_byte_for_byte_identical(src, dst)



