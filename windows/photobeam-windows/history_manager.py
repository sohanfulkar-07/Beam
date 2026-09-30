"""
PhotoBeam Windows — Transfer History Manager
Persists transfer history records in ~/.photobeam/history.json
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import List, Optional


@dataclass
class TransferRecord:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    direction: str = "sent"          # "sent" or "received"
    files: List[str] = field(default_factory=list)
    total_bytes: int = 0
    timestamp: float = field(default_factory=time.time)
    duration_sec: float = 0.0
    status: str = "completed"        # "completed", "interrupted", "failed"
    transport_type: str = "Wi-Fi"    # "Wi-Fi", "USB", "Wi-Fi + USB"
    error_reason: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> TransferRecord:
        return cls(
            id=data.get("id", str(uuid.uuid4())),
            direction=data.get("direction", "sent"),
            files=data.get("files", []),
            total_bytes=data.get("total_bytes", 0),
            timestamp=data.get("timestamp", time.time()),
            duration_sec=data.get("duration_sec", 0.0),
            status=data.get("status", "completed"),
            transport_type=data.get("transport_type", "Wi-Fi"),
            error_reason=data.get("error_reason", ""),
        )


class HistoryManager:
    """Thread-safe persistent history manager."""

    _instance: Optional[HistoryManager] = None
    _lock = threading.Lock()

    def __init__(self, storage_path: Optional[Path] = None):
        if storage_path is None:
            storage_path = Path.home() / ".photobeam" / "history.json"
        self._path = storage_path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._mutex = threading.Lock()

    @classmethod
    def get_instance(cls) -> HistoryManager:
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def get_records(self) -> List[TransferRecord]:
        with self._mutex:
            if not self._path.exists():
                return []
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    return [TransferRecord.from_dict(d) for d in data]
            except Exception:
                return []

    def add_record(self, record: TransferRecord) -> None:
        with self._mutex:
            records = []
            if self._path.exists():
                try:
                    with open(self._path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        records = [TransferRecord.from_dict(d) for d in data]
                except Exception:
                    records = []
            # Prepend newest record
            records.insert(0, record)
            # Cap at 200 records
            records = records[:200]
            try:
                temp_path = self._path.with_suffix(".tmp")
                with open(temp_path, "w", encoding="utf-8") as f:
                    json.dump([r.to_dict() for r in records], f, indent=2, ensure_ascii=False)
                os.replace(temp_path, self._path)
            except Exception:
                pass

    def clear(self) -> None:
        with self._mutex:
            try:
                if self._path.exists():
                    self._path.unlink()
            except Exception:
                pass
