"""
Tests: ChunkManager — splitting, tracking, missing/duplicate detection
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'protocol'))

import tempfile
import uuid
from pathlib import Path
import pytest
from src.chunk import ChunkManager
from src.models import ChunkState


TRANSFER_ID = str(uuid.uuid4())
FILE_ID = str(uuid.uuid4())


def make_test_file(size: int, tmp_path: Path) -> Path:
    p = tmp_path / "testfile.bin"
    pattern = bytes(range(256))
    full_reps, remainder = divmod(size, 256)
    p.write_bytes(pattern * full_reps + pattern[:remainder])
    return p


def test_total_chunks_exact():
    mgr = ChunkManager(TRANSFER_ID, FILE_ID, file_size=8 * 1024 * 1024, chunk_size=4 * 1024 * 1024)
    assert mgr.total_chunks == 2


def test_total_chunks_partial():
    mgr = ChunkManager(TRANSFER_ID, FILE_ID, file_size=5 * 1024 * 1024, chunk_size=4 * 1024 * 1024)
    assert mgr.total_chunks == 2


def test_total_chunks_smaller_than_chunk():
    mgr = ChunkManager(TRANSFER_ID, FILE_ID, file_size=1024, chunk_size=4 * 1024 * 1024)
    assert mgr.total_chunks == 1


def test_total_chunks_zero_file():
    mgr = ChunkManager(TRANSFER_ID, FILE_ID, file_size=0, chunk_size=4 * 1024 * 1024)
    assert mgr.total_chunks == 1


def test_iter_chunk_frames_covers_full_file(tmp_path):
    size = 10 * 1024 * 1024 + 123  # not aligned
    p = make_test_file(size, tmp_path)
    chunk_size = 4 * 1024 * 1024
    mgr = ChunkManager(TRANSFER_ID, FILE_ID, file_size=size, chunk_size=chunk_size)

    frames = list(mgr.iter_chunk_frames(p))
    assert len(frames) == mgr.total_chunks

    # All data concatenated should equal original file
    all_data = b"".join(f.data for f in frames)
    assert all_data == p.read_bytes()


def test_iter_chunk_frames_offsets(tmp_path):
    size = 12 * 1024 * 1024
    p = make_test_file(size, tmp_path)
    chunk_size = 4 * 1024 * 1024
    mgr = ChunkManager(TRANSFER_ID, FILE_ID, file_size=size, chunk_size=chunk_size)

    frames = list(mgr.iter_chunk_frames(p))
    assert frames[0].offset == 0
    assert frames[1].offset == chunk_size
    assert frames[2].offset == 2 * chunk_size


def test_pending_chunks_all_initially():
    mgr = ChunkManager(TRANSFER_ID, FILE_ID, file_size=8 * 1024 * 1024, chunk_size=4 * 1024 * 1024)
    pending = mgr.pending_chunks()
    assert set(pending) == {0, 1}


def test_mark_acked_removes_from_pending():
    mgr = ChunkManager(TRANSFER_ID, FILE_ID, file_size=8 * 1024 * 1024, chunk_size=4 * 1024 * 1024)
    mgr.mark_in_flight(0, "wifi")
    mgr.mark_acked(0)
    pending = mgr.pending_chunks()
    assert 0 not in pending


def test_is_complete_after_all_acked():
    mgr = ChunkManager(TRANSFER_ID, FILE_ID, file_size=8 * 1024 * 1024, chunk_size=4 * 1024 * 1024)
    assert not mgr.is_complete()
    mgr.mark_acked(0)
    mgr.mark_acked(1)
    assert mgr.is_complete()


def test_receiver_missing_chunks():
    mgr = ChunkManager(TRANSFER_ID, FILE_ID, file_size=12 * 1024 * 1024, chunk_size=4 * 1024 * 1024)
    mgr.record_received(0)
    mgr.record_received(2)
    missing = mgr.missing_chunks()
    assert missing == [1]


def test_receiver_duplicate_chunk():
    mgr = ChunkManager(TRANSFER_ID, FILE_ID, file_size=8 * 1024 * 1024, chunk_size=4 * 1024 * 1024)
    assert mgr.record_received(0) is True
    assert mgr.record_received(0) is False  # duplicate


def test_get_chunk_frame_for_retransmit(tmp_path):
    size = 4 * 1024 * 1024
    p = make_test_file(size, tmp_path)
    mgr = ChunkManager(TRANSFER_ID, FILE_ID, file_size=size, chunk_size=size)
    frame = mgr.get_chunk_frame(p, 0)
    assert frame.chunk_id == 0
    assert len(frame.data) == size


def test_compute_transfer_info(tmp_path):
    p = make_test_file(5 * 1024 * 1024, tmp_path)
    info, mgr = ChunkManager.compute_transfer_info(p, TRANSFER_ID, chunk_size=4 * 1024 * 1024)
    assert info.name == p.name
    assert info.size == p.stat().st_size
    assert len(info.sha256) == 64  # hex sha256
    assert info.total_chunks == 2
    assert mgr.total_chunks == 2
