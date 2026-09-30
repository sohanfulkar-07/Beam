"""
Tests: Integrity — chunk checksums and file SHA-256
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'protocol'))

import hashlib
import tempfile
from pathlib import Path
import pytest
from src.integrity import IntegrityManager


def test_chunk_checksum_consistent():
    data = b"photobeam test data"
    c1 = IntegrityManager.chunk_checksum(data)
    c2 = IntegrityManager.chunk_checksum(data)
    assert c1 == c2


def test_chunk_checksum_different_data():
    c1 = IntegrityManager.chunk_checksum(b"data1")
    c2 = IntegrityManager.chunk_checksum(b"data2")
    assert c1 != c2


def test_verify_chunk_ok():
    data = b"chunk content here"
    cs = IntegrityManager.chunk_checksum(data)
    assert IntegrityManager.verify_chunk(data, cs)


def test_verify_chunk_corrupted():
    data = b"chunk content here"
    cs = IntegrityManager.chunk_checksum(data)
    corrupted = bytearray(data)
    corrupted[0] ^= 0xFF
    assert not IntegrityManager.verify_chunk(bytes(corrupted), cs)


def test_file_hash_matches_sha256(tmp_path):
    p = tmp_path / "test.bin"
    content = b"Hello PhotoBeam! " * 10000
    p.write_bytes(content)
    expected = hashlib.sha256(content).hexdigest()
    actual = IntegrityManager.file_hash(p)
    assert actual == expected


def test_verify_file_ok(tmp_path):
    p = tmp_path / "test.bin"
    content = b"file content " * 5000
    p.write_bytes(content)
    sha = hashlib.sha256(content).hexdigest()
    assert IntegrityManager.verify_file(p, sha)


def test_verify_file_tampered(tmp_path):
    p = tmp_path / "test.bin"
    content = b"original content"
    p.write_bytes(content)
    sha = hashlib.sha256(content).hexdigest()
    # Tamper
    p.write_bytes(b"tampered content!!")
    assert not IntegrityManager.verify_file(p, sha)


def test_file_hash_from_chunks():
    parts = [b"part1", b"part2", b"part3"]
    full = b"".join(parts)
    expected = hashlib.sha256(full).hexdigest()
    actual = IntegrityManager.file_hash_from_chunks(iter(parts))
    assert actual == expected


def test_large_file_hash(tmp_path):
    # 10 MB file — verify streaming doesn't OOM
    p = tmp_path / "large.bin"
    content = b"X" * (10 * 1024 * 1024)
    p.write_bytes(content)
    h = IntegrityManager.file_hash(p, chunk_size=1024 * 1024)
    expected = hashlib.sha256(content).hexdigest()
    assert h == expected
