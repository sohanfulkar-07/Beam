"""
PhotoBeam Protocol — Transfer Manager

Orchestrates multi-file, multi-transport transfers.
Sender-side and receiver-side logic.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

from .chunk import ChunkManager
from .integrity import IntegrityManager
from .models import (
    ChunkFrame,
    ControlMessage,
    MessageType,
    TransferInfo,
    TransferState,
)
from .resume import ResumeManager
from .scheduler import Scheduler
from .storage import StorageManager, StorageError
from .transport import Transport


@dataclass
class FileTransferContext:
    info: TransferInfo
    chunk_manager: ChunkManager
    source_path: Optional[Path] = None      # sender side
    tmp_path: Optional[Path] = None          # receiver side
    state: TransferState = TransferState.PENDING
    started_at: float = 0.0
    completed_at: float = 0.0
    bytes_transferred: int = 0


class TransferError(Exception):
    pass


class TransferManager:
    """
    Manages a complete transfer session (multiple files, multiple transports).
    
    Sender usage:
        mgr = TransferManager(session_id, scheduler, resume_manager)
        mgr.prepare_files([path1, path2])
        mgr.send_ready(control_transport)
        mgr.run_sender()  # blocks until done or error
    
    Receiver usage:
        mgr = TransferManager(session_id, scheduler, resume_manager, storage_manager)
        mgr.handle_ready(transfers_info_list)
        mgr.run_receiver(control_transport)  # blocks
    """

    CHUNK_SEND_TIMEOUT = 30.0     # seconds
    MAX_CHUNK_RETRIES = 5
    CONTROL_RECV_TIMEOUT = 60.0

    def __init__(
        self,
        session_id: str,
        scheduler: Scheduler,
        resume_manager: ResumeManager,
        storage_manager: Optional[StorageManager] = None,
        chunk_size: int = 4 * 1024 * 1024,
        on_progress: Optional[Callable] = None,
    ):
        self.session_id = session_id
        self.transfer_id = str(uuid.uuid4())
        self._scheduler = scheduler
        self._resume = resume_manager
        self._storage = storage_manager
        self._chunk_size = chunk_size
        self._on_progress = on_progress

        self._files: Dict[str, FileTransferContext] = {}
        self._file_checksums: Dict[str, str] = {}
        self._lock = threading.RLock()
        self._state = TransferState.PENDING
        self._cancelled = threading.Event()
        self._paused = threading.Event()

    def set_file_checksum(self, fid: str, sha256: str) -> None:
        """Record the expected SHA-256 for a file received via streaming FILE_CHECKSUM."""
        with self._lock:
            self._file_checksums[fid] = sha256
            ctx = self._files.get(fid)
            if ctx:
                ctx.info.sha256 = sha256

    def pause(self) -> None:
        """Safely pause transfer across all files."""
        self._paused.set()
        if self._storage:
            self._storage.close_all()
        with self._lock:
            for ctx in self._files.values():
                if ctx.state == TransferState.ACTIVE:
                    ctx.state = TransferState.PAUSED
            self.persist_all_resume_states()

    def resume_transfer(self) -> None:
        """Clear pause state so transfer can safely continue."""
        self._paused.clear()

    def is_paused(self) -> bool:
        return self._paused.is_set()

    # ── Sender-side preparation ───────────────────────────────────────────────

    def prepare_files(self, paths: List[Path], compute_hash: bool = True) -> List[TransferInfo]:
        """
        Build TransferInfo list without full pre-read if compute_hash=False.
        Returns list of TransferInfo for READY message.
        """
        infos = []
        for path in paths:
            info, chunk_mgr = ChunkManager.compute_transfer_info(
                path, self.transfer_id, self._chunk_size, compute_hash=compute_hash
            )
            ctx = FileTransferContext(
                info=info,
                chunk_manager=chunk_mgr,
                source_path=path,
                state=TransferState.PENDING,
            )
            with self._lock:
                self._files[info.fid] = ctx
            infos.append(info)
        return infos

    def build_ready_message(self, infos: List[TransferInfo]) -> dict:
        return {
            "type": MessageType.READY,
            "transfers": [i.to_dict() for i in infos],
        }

    # ── Sender — send chunks for one file ────────────────────────────────────

    def send_file(
        self,
        fid: str,
        ack_callback: Callable[[str, int], None],  # (fid, chunk_id)
        skip_chunks: Optional[Set[int]] = None,
    ) -> None:
        """
        Stream chunks of a file through available transports.
        If skip_chunks is given, already-received chunks are skipped without sending.
        ack_callback is called when the receiver sends ACK_CHUNK.
        Blocks until all chunks are sent (not necessarily acked) or paused/cancelled.
        """
        ctx = self._files[fid]
        ctx.state = TransferState.ACTIVE
        ctx.started_at = time.monotonic()
        path = ctx.source_path

        # If resuming, credit already received chunks
        if skip_chunks:
            for cid in skip_chunks:
                if cid < len(ctx.chunk_manager._chunks):
                    ctx.chunk_manager.mark_acked(cid)
            ctx.bytes_transferred = sum(
                c.length for c in ctx.chunk_manager._chunks if c.chunk_id in skip_chunks
            )

        hasher = hashlib.sha256()
        for frame in ctx.chunk_manager.iter_chunk_frames(path, skip_chunks=skip_chunks, hasher=hasher):
            if self._cancelled.is_set():
                ctx.state = TransferState.CANCELLED
                return ""
            if self._paused.is_set():
                ctx.state = TransferState.PAUSED
                self._persist_resume_state(ctx)
                return ""

            retries = 0
            while retries < self.MAX_CHUNK_RETRIES:
                if self._cancelled.is_set():
                    ctx.state = TransferState.CANCELLED
                    return ""
                if self._paused.is_set():
                    ctx.state = TransferState.PAUSED
                    self._persist_resume_state(ctx)
                    return ""

                transport = self._scheduler.next_transport()
                if transport is None:
                    time.sleep(0.1)
                    retries += 1
                    continue

                try:
                    encoded = frame.encode()
                    transport.send_all(encoded)
                    ctx.chunk_manager.mark_in_flight(frame.chunk_id, transport.transport_id)
                    ctx.bytes_transferred += len(frame.data)
                    self._scheduler.report_success(transport.transport_id)

                    if self._on_progress:
                        self._on_progress(fid, frame.chunk_id, ctx.bytes_transferred, ctx.info.size)
                    break
                except (OSError, ConnectionError) as e:
                    self._scheduler.report_failure(transport.transport_id)
                    retries += 1
                    time.sleep(0.2 * retries)
            else:
                ctx.state = TransferState.FAILED
                raise TransferError(f"Failed to send chunk {frame.chunk_id} after {self.MAX_CHUNK_RETRIES} retries")

        computed_sha = hasher.hexdigest()
        ctx.info.sha256 = computed_sha
        return computed_sha

    # ── Receiver — handle incoming chunks ────────────────────────────────────

    def setup_receive(self, infos: List[TransferInfo]) -> None:
        """Set up file contexts for receiving."""
        total_size = sum(i.size for i in infos)
        if self._storage:
            self._storage.check_space_for_transfers(total_size)

        for info in infos:
            if info.sha256:
                self._file_checksums[info.fid] = info.sha256

            chunk_mgr = ChunkManager(
                self.transfer_id, info.fid, info.size, info.chunk_size
            )
            # Check if file is already completely received and verified in destination
            if self._storage and info.sha256:
                dest_file = self._storage.dest_dir / info.rel_path
                if dest_file.exists() and dest_file.stat().st_size == info.size:
                    if IntegrityManager.verify_file(dest_file, info.sha256):
                        for cid in range(chunk_mgr.total_chunks):
                            chunk_mgr.record_received(cid)
                        ctx = FileTransferContext(
                            info=info,
                            chunk_manager=chunk_mgr,
                            tmp_path=dest_file,
                            state=TransferState.COMPLETED,
                            bytes_transferred=info.size,
                        )
                        with self._lock:
                            self._files[info.fid] = ctx
                        continue

            # Check for resumable state (exact session/fid or by hash+size across sessions)
            saved = self._resume.load_state(self.session_id, info.fid)
            if not saved and info.sha256:
                saved = self._resume.find_resumable_by_hash(info.sha256, info.size)

            tmp_path = None
            initial_bytes = 0
            if saved and Path(saved.get("dest_tmp_path", "")).exists():
                tmp_path = Path(saved["dest_tmp_path"])
                for cid in saved.get("received_chunks", []):
                    if cid < len(chunk_mgr._chunks):
                        chunk_mgr.record_received(cid)
                        initial_bytes += chunk_mgr._chunks[cid].length
            elif self._storage:
                tmp_path = self._storage.create_temp_file(info.fid, info.rel_path)
                self._storage.preallocate(tmp_path, info.size)

            ctx = FileTransferContext(
                info=info,
                chunk_manager=chunk_mgr,
                tmp_path=tmp_path,
                state=TransferState.PENDING,
                bytes_transferred=initial_bytes,
            )
            with self._lock:
                self._files[info.fid] = ctx

    def receive_chunk(self, frame: ChunkFrame) -> tuple[bool, str]:
        """
        Process a received chunk frame.
        Thread-safe for concurrent multi-path readers.
        Returns (ok, error_reason).
        """
        fid_str = str(uuid.UUID(bytes=frame.file_id))
        with self._lock:
            ctx = self._files.get(fid_str)
            if ctx is None:
                return False, "unknown_file"

            # Verify checksum
            if not IntegrityManager.verify_chunk(frame.data, frame.checksum):
                return False, "bad_checksum"

            # Duplicate check
            is_new = ctx.chunk_manager.record_received(frame.chunk_id)
            if not is_new:
                return True, "duplicate"  # OK, just ignore

            # Write to temp file
            if self._storage and ctx.tmp_path:
                self._storage.write_chunk(ctx.tmp_path, frame.offset, frame.data)

            ctx.bytes_transferred += len(frame.data)

            # Persist resume state every 10 chunks or when paused
            if self._paused.is_set() or len(ctx.chunk_manager.received_chunks()) % 10 == 0:
                self._persist_resume_state(ctx)

            if self._on_progress:
                self._on_progress(fid_str, frame.chunk_id, ctx.bytes_transferred, ctx.info.size)

            return True, ""

    def verify_resume_state(self, fid: str) -> tuple[bool, str]:
        """
        Verify that saved resume state for fid is consistent with destination temp file.
        Returns (ok, reason).
        """
        with self._lock:
            ctx = self._files.get(fid)
            if not ctx:
                return False, "unknown_file"
            if ctx.state == TransferState.COMPLETED:
                return True, ""
            if not ctx.tmp_path or not ctx.tmp_path.exists():
                return False, "temp_file_missing"
            try:
                actual_size = ctx.tmp_path.stat().st_size
                if actual_size < ctx.bytes_transferred:
                    return False, "temp_file_truncated"
            except OSError as e:
                return False, f"file_error_{e}"

            total = ctx.info.total_chunks
            for cid in ctx.chunk_manager.received_chunks():
                if cid < 0 or cid >= total:
                    return False, "invalid_chunk_id"

            return True, ""

    def is_file_complete(self, fid: str) -> bool:
        with self._lock:
            ctx = self._files.get(fid)
            return ctx is not None and (ctx.state == TransferState.COMPLETED or ctx.chunk_manager.is_complete())

    def is_all_files_complete(self) -> bool:
        with self._lock:
            if not self._files:
                return False
            return all((ctx.state == TransferState.COMPLETED or ctx.chunk_manager.is_complete()) for ctx in self._files.values())

    def is_file_verified(self, fid: str) -> bool:
        """Return True only if file has been fully received AND verified (COMPLETED)."""
        with self._lock:
            ctx = self._files.get(fid)
            return ctx is not None and ctx.state == TransferState.COMPLETED

    def is_all_files_verified(self) -> bool:
        """Return True only if all files have been verified byte-for-byte."""
        with self._lock:
            if not self._files:
                return False
            return all(ctx.state == TransferState.COMPLETED for ctx in self._files.values())

    def finalize_file(self, fid: str) -> tuple[bool, str]:
        """
        Verify file hash and move from temp to final destination.
        Thread-safe and idempotent. Releases lock during hashing.
        Returns (ok, error_reason).
        """
        with self._lock:
            ctx = self._files.get(fid)
            if not ctx:
                return False, "unknown_file"

            if ctx.state == TransferState.COMPLETED:
                return True, ""
            if ctx.state == TransferState.VERIFYING:
                return False, "verifying_in_progress"

            if not ctx.chunk_manager.is_complete():
                return False, "chunks_missing"

            expected_sha = ctx.info.sha256 or self._file_checksums.get(fid, "")
            if not expected_sha:
                return False, "checksum_not_received"

            tmp_path = ctx.tmp_path
            rel_path = ctx.info.rel_path
            storage = self._storage
            session_id = self.session_id
            ctx.state = TransferState.VERIFYING

        if storage and tmp_path:
            storage.close_file(tmp_path)
            try:
                ok = IntegrityManager.verify_file(tmp_path, expected_sha)
            except Exception as e:
                with self._lock:
                    ctx.state = TransferState.FAILED
                return False, f"hash_verify_error: {e}"
            if not ok:
                storage.cleanup_temp(tmp_path)
                self._resume.clear_state(session_id, fid)
                with self._lock:
                    ctx.state = TransferState.FAILED
                return False, "hash_mismatch"

            final_path = storage.finalize_file(tmp_path, rel_path, allow_unique=True)
            with self._lock:
                ctx.info.sha256 = expected_sha
                ctx.state = TransferState.COMPLETED
                ctx.completed_at = time.monotonic()
            self._resume.clear_state(session_id, fid)
            return True, ""

        with self._lock:
            ctx.info.sha256 = expected_sha
            ctx.state = TransferState.COMPLETED
        self._resume.clear_state(session_id, fid)
        return True, ""

    def cancel(self) -> None:
        self._cancelled.set()
        if self._storage:
            self._storage.close_all()
        for ctx in self._files.values():
            if ctx.state == TransferState.ACTIVE:
                ctx.state = TransferState.CANCELLED
                if self._storage and ctx.tmp_path:
                    self._storage.cleanup_temp(ctx.tmp_path)


    def is_cancelled(self) -> bool:
        return self._cancelled.is_set()

    def get_missing_chunks(self, fid: str) -> List[int]:
        ctx = self._files.get(fid)
        if ctx:
            return ctx.chunk_manager.missing_chunks()
        return []

    def get_received_chunks(self, fid: str) -> List[int]:
        ctx = self._files.get(fid)
        if ctx:
            return list(ctx.chunk_manager.received_chunks())
        return []

    def persist_all_resume_states(self) -> None:
        with self._lock:
            for ctx in self._files.values():
                if ctx.state != TransferState.COMPLETED and ctx.chunk_manager.received_chunks():
                    self._persist_resume_state(ctx)

    def _persist_resume_state(self, ctx: FileTransferContext) -> None:
        self._resume.save_state(
            session_id=self.session_id,
            file_id=ctx.info.fid,
            file_name=ctx.info.name,
            file_size=ctx.info.size,
            total_chunks=ctx.info.total_chunks,
            received_chunks=ctx.chunk_manager.received_chunks(),
            dest_tmp_path=str(ctx.tmp_path) if ctx.tmp_path else "",
            sha256=ctx.info.sha256,
            chunk_size=ctx.info.chunk_size,
        )

    @property
    def total_bytes_transferred(self) -> int:
        return sum(ctx.bytes_transferred for ctx in self._files.values())

    @property
    def total_bytes_expected(self) -> int:
        return sum(ctx.info.size for ctx in self._files.values())
