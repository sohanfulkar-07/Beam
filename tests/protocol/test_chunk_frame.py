"""
Tests: ChunkFrame binary serialization + integrity
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'protocol'))

import uuid
import pytest
from src.models import ChunkFrame, CHUNK_MAGIC, PROTOCOL_VERSION, CHUNK_HEADER_SIZE, FrameType
from src.integrity import IntegrityManager


def make_frame(data: bytes = b"hello world", chunk_id: int = 0, offset: int = 0) -> ChunkFrame:
    tid = uuid.uuid4().bytes
    fid = uuid.uuid4().bytes
    checksum = IntegrityManager.chunk_checksum(data)
    return ChunkFrame(
        transfer_id=tid,
        file_id=fid,
        chunk_id=chunk_id,
        offset=offset,
        data=data,
        checksum=checksum,
    )


def test_encode_decode_roundtrip():
    data = b"test chunk data " * 100
    frame = make_frame(data, chunk_id=5, offset=4096)
    encoded = frame.encode()
    decoded = ChunkFrame.decode(encoded)
    assert decoded.chunk_id == 5
    assert decoded.offset == 4096
    assert decoded.data == data
    assert decoded.checksum == frame.checksum
    assert decoded.transfer_id == frame.transfer_id
    assert decoded.file_id == frame.file_id


def test_encoded_size():
    data = b"x" * 1024
    frame = make_frame(data)
    encoded = frame.encode()
    assert len(encoded) == CHUNK_HEADER_SIZE + len(data)
    assert frame.total_size == CHUNK_HEADER_SIZE + len(data)


def test_magic_in_encoded():
    frame = make_frame()
    encoded = frame.encode()
    assert encoded[:4] == CHUNK_MAGIC


def test_bad_magic_raises():
    data = b"x" * 100
    frame = make_frame(data)
    encoded = bytearray(frame.encode())
    encoded[0] = 0xFF  # corrupt magic
    with pytest.raises(ValueError, match="Bad magic"):
        ChunkFrame.decode(bytes(encoded))


def test_truncated_buffer_raises():
    with pytest.raises(ValueError, match="Buffer too short"):
        ChunkFrame.decode(b"too short")


def test_truncated_data_raises():
    data = b"x" * 100
    frame = make_frame(data)
    encoded = frame.encode()
    # Truncate data portion
    with pytest.raises(ValueError, match="Truncated chunk data"):
        ChunkFrame.decode(encoded[:CHUNK_HEADER_SIZE + 10])  # only 10 bytes of data


def test_checksum_verification():
    data = b"important file data"
    checksum = IntegrityManager.chunk_checksum(data)
    assert IntegrityManager.verify_chunk(data, checksum)
    assert not IntegrityManager.verify_chunk(b"tampered data", checksum)


def test_retransmit_frame_type():
    frame = make_frame()
    frame.frame_type = FrameType.RETRANSMIT
    encoded = frame.encode()
    decoded = ChunkFrame.decode(encoded)
    assert decoded.frame_type == FrameType.RETRANSMIT


def test_large_chunk():
    data = b"A" * (4 * 1024 * 1024)  # 4 MB
    frame = make_frame(data, chunk_id=99, offset=99 * 4 * 1024 * 1024)
    encoded = frame.encode()
    decoded = ChunkFrame.decode(encoded)
    assert decoded.data == data
    assert decoded.chunk_id == 99
    assert decoded.offset == 99 * 4 * 1024 * 1024


def test_empty_chunk():
    # Edge case: 0-byte file
    frame = make_frame(b"", chunk_id=0, offset=0)
    encoded = frame.encode()
    decoded = ChunkFrame.decode(encoded)
    assert decoded.data == b""
