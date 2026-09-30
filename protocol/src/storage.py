"""
PhotoBeam Protocol — Storage Manager

Pre-flight storage checks, temporary file management, atomic finalization.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import threading
from pathlib import Path
from typing import Optional, Tuple


class StorageError(Exception):
    """Raised when storage is insufficient or a storage operation fails."""
    def __init__(self, message: str, required: int = 0, available: int = 0):
        super().__init__(message)
        self.required = required
        self.available = available


class StorageManager:
    """
    Manages destination directory, free space checks, temp files, and finalization.
    """

    def __init__(self, dest_dir: Path):
        self._dest_dir = Path(dest_dir)
        self._dest_dir.mkdir(parents=True, exist_ok=True)
        self._open_handles: dict = {}
        self._handle_lock = threading.Lock()

    @property
    def dest_dir(self) -> Path:
        return self._dest_dir

    def check_space(self, required_bytes: int, margin_bytes: int = 10 * 1024 * 1024) -> None:
        """
        Check available disk space.
        Raises StorageError if insufficient.
        margin_bytes: extra buffer to avoid leaving disk completely full.
        """
        usage = shutil.disk_usage(self._dest_dir)
        available = usage.free
        needed = required_bytes + margin_bytes
        if available < needed:
            raise StorageError(
                f"Insufficient storage: need {needed:,} bytes, have {available:,} bytes",
                required=needed,
                available=available,
            )

    def check_space_for_transfers(self, total_bytes: int) -> None:
        """Check space for a total transfer size."""
        self.check_space(total_bytes)

    def create_temp_file(self, file_id: str, rel_path: str) -> Path:
        """
        Create a temporary file path for receiving a file.
        Uses .<file_id>.pbtemp suffix to avoid collisions.
        """
        dest_path = self._dest_dir / rel_path
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = dest_path.parent / f".{file_id}.pbtemp"
        # Touch the file to create it
        tmp_path.touch(exist_ok=True)
        return tmp_path

    def has_open_handles(self) -> bool:
        """Check if any file handles are currently held open."""
        with self._handle_lock:
            return any(not h.closed for h in self._open_handles.values())

    def get_unique_destination(self, rel_path: str) -> Path:
        """Return a unique destination path avoiding collisions with duplicate filenames."""
        return get_unique_destination(self._dest_dir, rel_path)

    def finalize_file(self, tmp_path: Path, rel_path: str, allow_unique: bool = True) -> Path:
        """
        Atomically move temp file to its final destination.
        Closes any cached handle before moving.
        If allow_unique is True, resolves name collisions with (1), (2), etc.
        """
        self.close_file(tmp_path)
        final_path = self.get_unique_destination(rel_path) if allow_unique else (self._dest_dir / rel_path)
        final_path.parent.mkdir(parents=True, exist_ok=True)
        # On Windows, os.replace is atomic within the same filesystem
        os.replace(tmp_path, final_path)
        return final_path

    def close_file(self, tmp_path: Path) -> None:
        """Flush and close open file handle for tmp_path."""
        key = str(tmp_path.resolve())
        with self._handle_lock:
            handle = self._open_handles.pop(key, None)
            if handle and not handle.closed:
                try:
                    handle.flush()
                    os.fsync(handle.fileno())
                except OSError:
                    pass
                finally:
                    handle.close()

    def close_all(self) -> None:
        """Flush and close all open file handles."""
        with self._handle_lock:
            for handle in list(self._open_handles.values()):
                if handle and not handle.closed:
                    try:
                        handle.flush()
                    except OSError:
                        pass
                    finally:
                        handle.close()
            self._open_handles.clear()

    def cleanup_temp(self, tmp_path: Path) -> None:
        """Delete temp file if it exists (on error/cancel)."""
        self.close_file(tmp_path)
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass

    def write_chunk(self, tmp_path: Path, offset: int, data: bytes) -> None:
        """Write chunk data at the correct offset using persistent handle for high throughput."""
        key = str(tmp_path.resolve())
        with self._handle_lock:
            handle = self._open_handles.get(key)
            if handle is None or handle.closed:
                handle = open(tmp_path, "r+b")
                self._open_handles[key] = handle
            handle.seek(offset)
            handle.write(data)

    def preallocate(self, tmp_path: Path, size: int) -> None:
        """
        Pre-allocate file to the expected size.
        Avoids fragmentation and gives OS a chance to fail early if space is short.
        Preserves existing data during resume if file is already allocated.
        """
        if tmp_path.is_file() and tmp_path.stat().st_size == size:
            return
        with open(tmp_path, "wb") as f:
            # Seek to end - 1 and write one byte; OS allocates the space
            if size > 0:
                f.seek(size - 1)
                f.write(b"\x00")

    @staticmethod
    def free_space(path: Path) -> int:
        """Return free bytes on the filesystem containing path."""
        return shutil.disk_usage(path).free


def get_unique_destination(dest_dir: Path, rel_path: str) -> Path:
    """Return a unique destination path avoiding collisions with duplicate filenames."""
    dest_dir = Path(dest_dir)
    final_path = dest_dir / rel_path
    if not final_path.exists():
        return final_path
    stem = final_path.stem
    suffix = final_path.suffix
    parent = final_path.parent
    counter = 1
    while (parent / f"{stem} ({counter}){suffix}").exists():
        counter += 1
    return parent / f"{stem} ({counter}){suffix}"

