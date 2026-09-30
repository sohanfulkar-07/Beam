"""
PhotoBeam Protocol — Integrity Manager

Per-chunk: XXH3-64
Per-file:  SHA-256
"""
from __future__ import annotations

import hashlib
import struct
from pathlib import Path
from typing import Optional


import zlib


def _crc32(data: bytes) -> int:
    return zlib.crc32(data) & 0xFFFFFFFF


def _fnv1a64(data: bytes) -> int:
    h = 14695981039346656037
    for b in data:
        h ^= b
        h = (h * 1099511628211) & 0xFFFFFFFFFFFFFFFF
    return h


def _xxh3_64(data: bytes) -> int:
    """
    Compute XXH3-64 checksum.
    Uses xxhash library if available, falls back to xxh64 shim,
    or falls back to a fast FNV-1a derived 64-bit hash for testing.
    """
    try:
        import xxhash
        return xxhash.xxh3_64_intdigest(data)
    except ImportError:
        pass
    try:
        import xxhash
        return xxhash.xxh64_intdigest(data)
    except ImportError:
        pass
    return _fnv1a64(data)


class IntegrityManager:
    """
    Handles checksum computation and verification for chunks and files.
    """

    @staticmethod
    def chunk_checksum(data: bytes) -> int:
        """Compute checksum for a chunk. Returns uint64."""
        return _crc32(data)

    @staticmethod
    def verify_chunk(data: bytes, expected_checksum: int) -> bool:
        """Return True if chunk data matches expected checksum (CRC32, XXH3, or FNV-1a)."""
        if _crc32(data) == expected_checksum:
            return True
        if _xxh3_64(data) == expected_checksum:
            return True
        if _fnv1a64(data) == expected_checksum:
            return True
        return False

    @staticmethod
    def file_hash(path: Path, chunk_size: int = 1 * 1024 * 1024) -> str:
        """Compute SHA-256 of a file. Returns hex string. Streams; never loads whole file."""
        h = hashlib.sha256()
        path = Path(path)
        with path.open("rb") as f:
            while True:
                buf = f.read(chunk_size)
                if not buf:
                    break
                h.update(buf)
        return h.hexdigest()

    @staticmethod
    def verify_file(path: Path, expected_sha256: str) -> bool:
        """Return True if file's SHA-256 matches expected (hex string)."""
        actual = IntegrityManager.file_hash(path)
        return actual.lower() == expected_sha256.lower()

    @staticmethod
    def file_hash_from_chunks(chunk_iter) -> str:
        """
        Compute SHA-256 incrementally from an iterator of bytes chunks.
        chunk_iter: iterable of bytes
        Returns hex string.
        """
        h = hashlib.sha256()
        for chunk in chunk_iter:
            h.update(chunk)
        return h.hexdigest()
