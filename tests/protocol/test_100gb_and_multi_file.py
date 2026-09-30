"""
PhotoBeam Regression & Architecture Tests:
- Requirement 1: Very Large Files (100 GB+) & 64-bit safe offsets/counts
- Requirement 2: Multi-File Batch Transfer, Interleaving, Collisions & Resume
"""
import sys
import os
import uuid
import struct
import hashlib
from pathlib import Path
import pytest

# Protocol import path setup
PROTO = os.path.join(os.path.dirname(__file__), '..', '..', 'protocol')
if PROTO not in sys.path:
    sys.path.insert(0, PROTO)

WINDOWS_SRC = os.path.join(os.path.dirname(__file__), '..', '..', 'windows', 'photobeam-windows')
if WINDOWS_SRC not in sys.path:
    sys.path.insert(0, WINDOWS_SRC)

from src.models import (
    ChunkFrame, TransferInfo, CHUNK_HEADER_SIZE, DEFAULT_CHUNK_SIZE,
    MessageType, PROTOCOL_VERSION
)
from src.chunk import ChunkManager
from src.integrity import IntegrityManager
from src.storage import StorageManager, get_unique_destination
from src.transfer import TransferManager
from src.scheduler import Scheduler
from src.resume import ResumeManager


# ==============================================================================
# REQUIREMENT 1: 100 GB+ File Representation & 64-Bit Safe Offsets
# ==============================================================================

def test_100gb_transfer_info_serialization():
    """Verify TransferInfo correctly represents 100 GB file and serializes without overflow."""
    size_100gb = 100 * 1024 * 1024 * 1024  # 107,374,182,400 bytes (> 4 GB, fits in 64-bit)
    chunk_size = 4 * 1024 * 1024           # 4 MB
    total_chunks = (size_100gb + chunk_size - 1) // chunk_size  # 25,600 chunks

    fid = str(uuid.uuid4())
    info = TransferInfo(
        fid=fid,
        name="Repack_Game_Archive.part1.rar",
        rel_path="Repack_Game_Archive.part1.rar",
        size=size_100gb,
        chunk_size=chunk_size,
        total_chunks=total_chunks,
        sha256="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    )

    d = info.to_dict()
    assert d["size"] == 107374182400
    assert d["total_chunks"] == 25600

    restored = TransferInfo.from_dict(d)
    assert restored.size == size_100gb
    assert restored.total_chunks == 25600
    assert restored.fid == fid


def test_100gb_chunk_frame_binary_framing():
    """Verify ChunkFrame binary header packs and unpacks 64-bit offsets > 4 GB correctly."""
    large_offset = 107_370_000_000  # ~99.99 GB offset
    chunk_data = b"PAYLOAD_" * 512  # 4096 bytes
    checksum = IntegrityManager.chunk_checksum(chunk_data)

    frame = ChunkFrame(
        transfer_id=uuid.uuid4().bytes,
        file_id=uuid.uuid4().bytes,
        chunk_id=25599,
        offset=large_offset,
        data=chunk_data,
        checksum=checksum,
    )

    encoded = frame.encode()
    assert len(encoded) == CHUNK_HEADER_SIZE + len(chunk_data)

    # Inspect binary wire format: offset is at byte 48 (length 8 bytes, big-endian signed 64-bit int)
    unpacked_offset = struct.unpack_from(">Q", encoded, 48)[0]
    assert unpacked_offset == large_offset

    # Decode frame
    decoded = ChunkFrame.decode(encoded)
    assert decoded.offset == large_offset
    assert decoded.chunk_id == 25599
    assert decoded.data == chunk_data
    assert decoded.checksum == checksum


def test_chunk_manager_100gb_calculations():
    """Verify ChunkManager handles 25,600 chunks for 100 GB without overflow or performance degradation."""
    size_100gb = 100 * 1024 * 1024 * 1024
    chunk_size = 4 * 1024 * 1024
    cm = ChunkManager(
        transfer_id=str(uuid.uuid4()),
        file_id=str(uuid.uuid4()),
        file_size=size_100gb,
        chunk_size=chunk_size,
    )

    assert cm.total_chunks == 25600
    assert not cm.is_complete()

    # Record first and last chunk
    cm.record_received(0)
    cm.record_received(25599)
    assert len(cm.received_chunks()) == 2
    assert not cm.is_complete()

    # Verify missing chunks count
    missing = cm.missing_chunks()
    assert len(missing) == 25598
    assert 0 not in missing
    assert 25599 not in missing


def test_pyqt_signals_64bit_overflow_regression():
    """Verify PyQt6 signals with 'qint64' do NOT truncate or wrap 100 GB integers modulo 2^32."""
    from PyQt6.QtWidgets import QApplication
    from ui.send_screen import SenderWorker, format_bytes

    # Ensure QApplication exists for QObject signals
    app = QApplication.instance() or QApplication(sys.argv)

    worker = SenderWorker("photobeam://connect/test", [])

    received_progress = []
    worker.progress_update.connect(lambda name, idx, tot, sent, fsz, ov_sent, ov_tot:
        received_progress.append((name, idx, tot, sent, fsz, ov_sent, ov_tot))
    )

    val_100gb = 107_374_182_400  # 100 GiB
    # Note: 107374182400 % 2^32 == 0! If it were a 32-bit int, it would arrive as 0!
    val_50gb = 53_687_091_200

    worker.progress_update.emit("repack.iso", 1, 1, val_50gb, val_100gb, val_50gb, val_100gb)

    assert len(received_progress) == 1
    _, _, _, sent, fsz, ov_sent, ov_tot = received_progress[0]
    assert fsz == val_100gb, f"Expected 100 GB ({val_100gb}), got {fsz} (32-bit overflow truncation!)"
    assert sent == val_50gb
    assert ov_tot == val_100gb
    assert ov_sent == val_50gb


def test_format_bytes_large_capacities():
    """Verify format_bytes supports GB and TB without error."""
    from ui.send_screen import format_bytes
    assert format_bytes(500) == "500 B"
    assert format_bytes(2048) == "2.0 KB"
    assert format_bytes(50 * 1024 * 1024) == "50.0 MB"
    assert format_bytes(100 * 1024 * 1024 * 1024) == "100.00 GB"
    assert format_bytes(2 * 1024 * 1024 * 1024 * 1024) == "2.00 TB"


# ==============================================================================
# REQUIREMENT 2: Multiple File Selection, Interleaving, Collisions & Resume
# ==============================================================================

def test_multi_file_metadata_preparation(tmp_path):
    """Verify TransferManager prepares batch metadata for all selected files with unique IDs."""
    files = []
    for i in range(5):
        p = tmp_path / f"game.part{i:02d}.rar"
        p.write_bytes(b"DATA" * (1024 * (i + 1)))
        files.append(p)

    xfer = TransferManager(session_id="test-session", scheduler=Scheduler(), resume_manager=ResumeManager())
    infos = xfer.prepare_files(files)

    assert len(infos) == 5
    fids = set()
    for idx, info in enumerate(infos):
        assert info.name == f"game.part{idx:02d}.rar"
        assert info.size == len(files[idx].read_bytes())
        assert info.fid not in fids
        fids.add(info.fid)

    ready_msg = xfer.build_ready_message(infos)
    assert ready_msg["type"] == MessageType.READY
    assert len(ready_msg["transfers"]) == 5


def test_duplicate_filename_collision_resolution(tmp_path):
    """Verify files with duplicate names from different directories are safely disambiguated."""
    dest = tmp_path / "downloads"
    dest.mkdir()

    # Pre-create "readme.txt"
    original = dest / "readme.txt"
    original.write_text("ORIGINAL", encoding="utf-8")

    # Second file arrives
    unique1 = get_unique_destination(dest, "readme.txt")
    assert unique1.name == "readme (1).txt"

    # Third file arrives
    unique1.write_text("SECOND", encoding="utf-8")
    unique2 = get_unique_destination(dest, "readme.txt")
    assert unique2.name == "readme (2).txt"


def test_interleaved_multi_file_chunk_transfer(tmp_path):
    """Verify chunks from multiple files can arrive interleaved without cross-file corruption."""
    dest = tmp_path / "rx"
    dest.mkdir()

    # File A: 8 KB (2 chunks of 4 KB)
    data_a = b"A" * 8192
    sha_a = hashlib.sha256(data_a).hexdigest()
    info_a = TransferInfo(
        fid=str(uuid.uuid4()), name="file_a.bin", rel_path="file_a.bin",
        size=len(data_a), chunk_size=4096, total_chunks=2, sha256=sha_a
    )

    # File B: 8 KB (2 chunks of 4 KB)
    data_b = b"B" * 8192
    sha_b = hashlib.sha256(data_b).hexdigest()
    info_b = TransferInfo(
        fid=str(uuid.uuid4()), name="file_b.bin", rel_path="file_b.bin",
        size=len(data_b), chunk_size=4096, total_chunks=2, sha256=sha_b
    )

    storage = StorageManager(dest)
    xfer = TransferManager(
        session_id="multi-test", scheduler=Scheduler(),
        resume_manager=ResumeManager(), storage_manager=storage
    )
    xfer.setup_receive([info_a, info_b])

    # Interleave chunks: A0, B0, A1, B1
    fa0 = ChunkFrame(
        transfer_id=uuid.uuid4().bytes, file_id=uuid.UUID(info_a.fid).bytes,
        chunk_id=0, offset=0, data=data_a[:4096],
        checksum=IntegrityManager.chunk_checksum(data_a[:4096])
    )
    fb0 = ChunkFrame(
        transfer_id=uuid.uuid4().bytes, file_id=uuid.UUID(info_b.fid).bytes,
        chunk_id=0, offset=0, data=data_b[:4096],
        checksum=IntegrityManager.chunk_checksum(data_b[:4096])
    )
    fa1 = ChunkFrame(
        transfer_id=uuid.uuid4().bytes, file_id=uuid.UUID(info_a.fid).bytes,
        chunk_id=1, offset=4096, data=data_a[4096:],
        checksum=IntegrityManager.chunk_checksum(data_a[4096:])
    )
    fb1 = ChunkFrame(
        transfer_id=uuid.uuid4().bytes, file_id=uuid.UUID(info_b.fid).bytes,
        chunk_id=1, offset=4096, data=data_b[4096:],
        checksum=IntegrityManager.chunk_checksum(data_b[4096:])
    )

    ok, _ = xfer.receive_chunk(fa0); assert ok
    ok, _ = xfer.receive_chunk(fb0); assert ok
    ok, _ = xfer.receive_chunk(fa1); assert ok
    ok, _ = xfer.receive_chunk(fb1); assert ok

    assert xfer.is_file_complete(info_a.fid)
    assert xfer.is_file_complete(info_b.fid)

    ok_fin_a, _ = xfer.finalize_file(info_a.fid); assert ok_fin_a
    ok_fin_b, _ = xfer.finalize_file(info_b.fid); assert ok_fin_b

    assert (dest / "file_a.bin").read_bytes() == data_a
    assert (dest / "file_b.bin").read_bytes() == data_b


def test_partial_batch_resume_skip_completed(tmp_path):
    """Verify that in a multi-file batch, an already completed file is skipped on resume."""
    dest = tmp_path / "rx"
    dest.mkdir()

    data1 = b"COMPLETED_FILE_DATA"
    sha1 = hashlib.sha256(data1).hexdigest()
    file1_dest = dest / "file1.txt"
    file1_dest.write_bytes(data1)  # File 1 is already complete and verified

    info1 = TransferInfo(
        fid=str(uuid.uuid4()), name="file1.txt", rel_path="file1.txt",
        size=len(data1), chunk_size=1024, total_chunks=1, sha256=sha1
    )

    data2 = b"INCOMPLETE_FILE_DATA"
    sha2 = hashlib.sha256(data2).hexdigest()
    info2 = TransferInfo(
        fid=str(uuid.uuid4()), name="file2.txt", rel_path="file2.txt",
        size=len(data2), chunk_size=1024, total_chunks=1, sha256=sha2
    )

    storage = StorageManager(dest)
    xfer = TransferManager(
        session_id="resume-batch", scheduler=Scheduler(),
        resume_manager=ResumeManager(), storage_manager=storage
    )
    xfer.setup_receive([info1, info2])

    # File 1 was pre-existing and verified: it must be immediately marked complete
    assert xfer.is_file_complete(info1.fid)
    assert xfer.is_file_verified(info1.fid)

    # File 2 is not complete yet
    assert not xfer.is_file_complete(info2.fid)


def test_single_file_error_isolation(tmp_path):
    """Verify that if one file fails SHA-256 verification, other files remain verified."""
    dest = tmp_path / "rx"
    dest.mkdir()

    # Good file
    good_data = b"GOOD_INTEGRITY_DATA"
    good_sha = hashlib.sha256(good_data).hexdigest()
    info_good = TransferInfo(
        fid=str(uuid.uuid4()), name="good.bin", rel_path="good.bin",
        size=len(good_data), chunk_size=1024, total_chunks=1, sha256=good_sha
    )

    # Bad file (corrupted during transit)
    bad_data = b"CORRUPTED_TRANSIT"
    expected_bad_sha = "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
    info_bad = TransferInfo(
        fid=str(uuid.uuid4()), name="bad.bin", rel_path="bad.bin",
        size=len(bad_data), chunk_size=1024, total_chunks=1, sha256=expected_bad_sha
    )

    storage = StorageManager(dest)
    xfer = TransferManager(
        session_id="iso-test", scheduler=Scheduler(),
        resume_manager=ResumeManager(), storage_manager=storage
    )
    xfer.setup_receive([info_good, info_bad])

    # Receive good file chunk
    fg = ChunkFrame(
        transfer_id=uuid.uuid4().bytes, file_id=uuid.UUID(info_good.fid).bytes,
        chunk_id=0, offset=0, data=good_data,
        checksum=IntegrityManager.chunk_checksum(good_data)
    )
    xfer.receive_chunk(fg)

    # Receive bad file chunk
    fb = ChunkFrame(
        transfer_id=uuid.uuid4().bytes, file_id=uuid.UUID(info_bad.fid).bytes,
        chunk_id=0, offset=0, data=bad_data,
        checksum=IntegrityManager.chunk_checksum(bad_data)
    )
    xfer.receive_chunk(fb)

    # Finalize both
    ok_good, _ = xfer.finalize_file(info_good.fid)
    ok_bad, err = xfer.finalize_file(info_bad.fid)

    assert ok_good is True
    assert ok_bad is False
    assert err == "hash_mismatch"

    # Good file exists in destination, bad file was not finalized into destination
    assert (dest / "good.bin").exists()
    assert not (dest / "bad.bin").exists()


def test_batch_cancellation_cleanup(tmp_path):
    """Verify cancellation cleans up active handles without leaking resources."""
    dest = tmp_path / "rx"
    dest.mkdir()

    info = TransferInfo(
        fid=str(uuid.uuid4()), name="temp.bin", rel_path="temp.bin",
        size=1024 * 1024, chunk_size=1024, total_chunks=1024,
        sha256="abc"
    )

    storage = StorageManager(dest)
    xfer = TransferManager(
        session_id="cancel-test", scheduler=Scheduler(),
        resume_manager=ResumeManager(), storage_manager=storage
    )
    xfer.setup_receive([info])

    # Receive one chunk so a persistent handle is opened
    frame = ChunkFrame(
        transfer_id=uuid.uuid4().bytes, file_id=uuid.UUID(info.fid).bytes,
        chunk_id=0, offset=0, data=b"X" * 1024,
        checksum=IntegrityManager.chunk_checksum(b"X" * 1024)
    )
    xfer.receive_chunk(frame)

    assert storage.has_open_handles()
    xfer.cancel()
    assert not storage.has_open_handles()
