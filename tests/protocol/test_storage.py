"""
Tests: StorageManager — space checks, temp files, preallocate, finalize
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'protocol'))

import pytest
from pathlib import Path
from src.storage import StorageManager, StorageError


def test_create_temp_file(tmp_path):
    dest = tmp_path / "dest"
    sm = StorageManager(dest)
    tmp = sm.create_temp_file("file-id-1", "photo.jpg")
    assert tmp.exists()


def test_write_and_finalize(tmp_path):
    dest = tmp_path / "dest"
    sm = StorageManager(dest)
    data = b"Hello, PhotoBeam chunk data!"
    tmp = sm.create_temp_file("fid", "output.txt")
    sm.preallocate(tmp, len(data))
    sm.write_chunk(tmp, 0, data)
    final = sm.finalize_file(tmp, "output.txt")
    assert final.read_bytes() == data


def test_preallocate_creates_correct_size(tmp_path):
    dest = tmp_path / "dest"
    sm = StorageManager(dest)
    tmp = sm.create_temp_file("fid", "big.bin")
    size = 1024 * 1024  # 1 MB
    sm.preallocate(tmp, size)
    assert tmp.stat().st_size == size


def test_write_multiple_chunks(tmp_path):
    dest = tmp_path / "dest"
    sm = StorageManager(dest)
    chunk_size = 1024
    total = chunk_size * 3
    tmp = sm.create_temp_file("fid", "multi.bin")
    sm.preallocate(tmp, total)

    for i in range(3):
        sm.write_chunk(tmp, i * chunk_size, bytes([i] * chunk_size))

    final = sm.finalize_file(tmp, "multi.bin")
    content = final.read_bytes()
    assert content[:chunk_size] == bytes([0] * chunk_size)
    assert content[chunk_size:2*chunk_size] == bytes([1] * chunk_size)
    assert content[2*chunk_size:] == bytes([2] * chunk_size)


def test_check_space_passes_with_enough(tmp_path):
    dest = tmp_path / "dest"
    sm = StorageManager(dest)
    # 1 byte should always be available
    sm.check_space(1)


def test_check_space_fails_with_too_much(tmp_path):
    dest = tmp_path / "dest"
    sm = StorageManager(dest)
    # Request more than any disk has
    with pytest.raises(StorageError) as exc_info:
        sm.check_space(10 * 1024 ** 4)  # 10 TB
    assert exc_info.value.required > 0
    assert exc_info.value.available >= 0


def test_cleanup_temp_removes_file(tmp_path):
    dest = tmp_path / "dest"
    sm = StorageManager(dest)
    tmp = sm.create_temp_file("fid", "temp.bin")
    assert tmp.exists()
    sm.cleanup_temp(tmp)
    assert not tmp.exists()


def test_cleanup_nonexistent_does_not_raise(tmp_path):
    dest = tmp_path / "dest"
    sm = StorageManager(dest)
    sm.cleanup_temp(dest / "does_not_exist.tmp")  # Should not raise


def test_subdirectory_creation(tmp_path):
    dest = tmp_path / "dest"
    sm = StorageManager(dest)
    tmp = sm.create_temp_file("fid", "vacation/photo.jpg")
    assert tmp.exists()
    final = sm.finalize_file(tmp, "vacation/photo.jpg")
    assert final.exists()
    assert final.parent.name == "vacation"


def test_free_space_returns_int(tmp_path):
    space = StorageManager.free_space(tmp_path)
    assert isinstance(space, int)
    assert space > 0
