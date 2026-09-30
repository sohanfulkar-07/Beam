"""
PhotoBeam Protocol — Chunk Manager

Splits files into chunks for sending.
Tracks chunk state (pending/in-flight/received/failed) on both sides.
"""
from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import Dict, Iterator, List, Optional, Set

from .models import (
    ChunkFrame,
    ChunkState,
    DEFAULT_CHUNK_SIZE,
    FrameType,
    TransferInfo,
)
from .integrity import IntegrityManager


@dataclass
class ChunkDescriptor:
    """Describes one chunk without holding data in memory."""
    chunk_id: int
    offset: int
    length: int
    state: ChunkState = ChunkState.PENDING
    transport_id: Optional[str] = None   # which transport is carrying it
    checksum: int = 0                    # filled when data is read
    retries: int = 0


class ChunkManager:
    """
    Sender-side: splits file into chunks, yields ChunkFrames on demand.
    Receiver-side: tracks received chunks, detects missing/duplicate.
    Thread-safe.
    """

    def __init__(
        self,
        transfer_id: str,
        file_id: str,
        file_size: int,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
    ):
        self.transfer_id_bytes = uuid.UUID(transfer_id).bytes
        self.file_id_bytes = uuid.UUID(file_id).bytes
        self.file_size = file_size
        self.chunk_size = chunk_size
        self.total_chunks = max(1, math.ceil(file_size / chunk_size)) if file_size > 0 else 1

        self._chunks: List[ChunkDescriptor] = [
            ChunkDescriptor(
                chunk_id=i,
                offset=i * chunk_size,
                length=min(chunk_size, file_size - i * chunk_size) if file_size > 0 else 0,
            )
            for i in range(self.total_chunks)
        ]
        self._received: Set[int] = set()
        self._lock = Lock()

    # ── Sender-side ───────────────────────────────────────────────────────────

    def iter_chunk_frames(
        self,
        path: Path,
        skip_chunks: Optional[Set[int]] = None,
        hasher: Optional[Any] = None,
    ) -> Iterator[ChunkFrame]:
        """
        Read file and yield ChunkFrames in order.
        Streams; never loads full file into memory.
        If hasher is provided, updates hasher with file bytes as read.
        If skip_chunks is specified, already-received chunks are skipped from yielding.
        """
        skip = skip_chunks if skip_chunks is not None else set()
        with path.open("rb") as f:
            for desc in self._chunks:
                f.seek(desc.offset)
                data = f.read(desc.length)
                if hasher is not None:
                    hasher.update(data)
                if desc.chunk_id in skip or desc.state == ChunkState.RECEIVED:
                    continue
                checksum = IntegrityManager.chunk_checksum(data)
                desc.checksum = checksum
                yield ChunkFrame(
                    transfer_id=self.transfer_id_bytes,
                    file_id=self.file_id_bytes,
                    chunk_id=desc.chunk_id,
                    offset=desc.offset,
                    data=data,
                    checksum=checksum,
                )

    def get_chunk_frame(self, path: Path, chunk_id: int) -> ChunkFrame:
        """Read and return a single chunk frame (for retransmit)."""
        desc = self._chunks[chunk_id]
        with path.open("rb") as f:
            f.seek(desc.offset)
            data = f.read(desc.length)
        checksum = IntegrityManager.chunk_checksum(data)
        return ChunkFrame(
            transfer_id=self.transfer_id_bytes,
            file_id=self.file_id_bytes,
            chunk_id=chunk_id,
            offset=desc.offset,
            data=data,
            checksum=checksum,
            frame_type=FrameType.RETRANSMIT,
        )

    def mark_in_flight(self, chunk_id: int, transport_id: str) -> None:
        with self._lock:
            self._chunks[chunk_id].state = ChunkState.IN_FLIGHT
            self._chunks[chunk_id].transport_id = transport_id

    def mark_acked(self, chunk_id: int) -> None:
        with self._lock:
            self._chunks[chunk_id].state = ChunkState.RECEIVED
            self._received.add(chunk_id)

    def mark_failed(self, chunk_id: int) -> None:
        with self._lock:
            desc = self._chunks[chunk_id]
            desc.state = ChunkState.FAILED
            desc.retries += 1

    def pending_chunks(self) -> List[int]:
        """Return list of chunk IDs not yet successfully sent."""
        with self._lock:
            return [
                c.chunk_id for c in self._chunks
                if c.state in (ChunkState.PENDING, ChunkState.FAILED)
            ]

    def in_flight_chunks(self) -> List[int]:
        with self._lock:
            return [c.chunk_id for c in self._chunks if c.state == ChunkState.IN_FLIGHT]

    def is_complete(self) -> bool:
        with self._lock:
            return len(self._received) == self.total_chunks

    # ── Receiver-side ─────────────────────────────────────────────────────────

    def record_received(self, chunk_id: int) -> bool:
        """Mark a chunk as received. Returns False if duplicate."""
        with self._lock:
            if chunk_id in self._received:
                return False  # duplicate
            self._received.add(chunk_id)
            self._chunks[chunk_id].state = ChunkState.RECEIVED
            return True

    def missing_chunks(self) -> List[int]:
        """Return list of chunk IDs not yet received."""
        with self._lock:
            return [i for i in range(self.total_chunks) if i not in self._received]

    def received_chunks(self) -> List[int]:
        with self._lock:
            return sorted(self._received)

    @staticmethod
    def compute_transfer_info(
        path: Path,
        transfer_id: str,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        compute_hash: bool = True,
    ) -> tuple["TransferInfo", "ChunkManager"]:
        """Build TransferInfo + ChunkManager for a file. If compute_hash is False, sha256 is computed while streaming."""
        from .models import TransferInfo
        sha256 = IntegrityManager.file_hash(path) if compute_hash else ""
        size = path.stat().st_size
        fid = str(uuid.uuid4())
        total_chunks = max(1, math.ceil(size / chunk_size)) if size > 0 else 1
        info = TransferInfo(
            fid=fid,
            name=path.name,
            rel_path=str(path.name),
            size=size,
            chunk_size=chunk_size,
            total_chunks=total_chunks,
            sha256=sha256,
        )
        mgr = ChunkManager(transfer_id, fid, size, chunk_size)
        return info, mgr
