"""
PhotoBeam Protocol — Resume Manager

Persists chunk receipt state to disk so transfers can be resumed
after crash, sleep, or disconnect.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from threading import Lock
from typing import Dict, List, Optional, Set


class ResumeManager:
    """
    Persists and restores transfer resume state.
    State files live in a configurable directory (default: ~/.photobeam/resume/).
    Each in-progress file transfer has one state file.
    """

    def __init__(self, state_dir: Optional[Path] = None):
        if state_dir is None:
            state_dir = Path.home() / ".photobeam" / "resume"
        self._state_dir = Path(state_dir)
        self._state_dir.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()

    def _state_path(self, session_id: str, file_id: str) -> Path:
        return self._state_dir / f"{session_id}_{file_id}.json"

    def save_state(
        self,
        session_id: str,
        file_id: str,
        file_name: str,
        file_size: int,
        total_chunks: int,
        received_chunks: List[int],
        dest_tmp_path: str,
        sha256: str,
        chunk_size: int,
    ) -> None:
        """Persist current chunk receipt state."""
        state = {
            "session_id": session_id,
            "file_id": file_id,
            "file_name": file_name,
            "file_size": file_size,
            "total_chunks": total_chunks,
            "received_chunks": received_chunks,
            "dest_tmp_path": dest_tmp_path,
            "sha256": sha256,
            "chunk_size": chunk_size,
            "updated_at": time.time(),
        }
        path = self._state_path(session_id, file_id)
        tmp = path.with_suffix(".tmp")
        with self._lock:
            tmp.write_text(json.dumps(state), encoding="utf-8")
            tmp.replace(path)  # atomic on most platforms

    def load_state(self, session_id: str, file_id: str) -> Optional[dict]:
        """Load persisted state. Returns None if not found."""
        path = self._state_path(session_id, file_id)
        with self._lock:
            if not path.exists():
                return None
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, IOError):
                return None

    def clear_state(self, session_id: str, file_id: str) -> None:
        """Delete state after successful transfer."""
        path = self._state_path(session_id, file_id)
        with self._lock:
            path.unlink(missing_ok=True)

    def list_resumable(self, session_id: str) -> List[dict]:
        """Return all resumable states for a session."""
        prefix = f"{session_id}_"
        result = []
        with self._lock:
            for f in self._state_dir.glob(f"{prefix}*.json"):
                try:
                    state = json.loads(f.read_text(encoding="utf-8"))
                    result.append(state)
                except (json.JSONDecodeError, IOError):
                    continue
        return result

    def clear_session(self, session_id: str) -> None:
        """Delete all state files for a session."""
        prefix = f"{session_id}_"
        with self._lock:
            for f in self._state_dir.glob(f"{prefix}*.json"):
                f.unlink(missing_ok=True)

    def find_resumable_by_hash(self, sha256: str, file_size: int) -> Optional[dict]:
        """Find a resumable state matching file hash and size across sessions."""
        with self._lock:
            for f in self._state_dir.glob("*.json"):
                try:
                    state = json.loads(f.read_text(encoding="utf-8"))
                    if state.get("sha256") == sha256 and state.get("file_size") == file_size:
                        tmp = Path(state.get("dest_tmp_path", ""))
                        if tmp.exists():
                            return state
                except Exception:
                    continue
        return None
