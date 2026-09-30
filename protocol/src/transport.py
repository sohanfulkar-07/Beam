"""
PhotoBeam Protocol — Transport Abstraction

Abstract base class for all transports (Wi-Fi, USB).
WiFiTransport and UsbTransport both implement this interface.
"""
from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from threading import Lock
from typing import Deque, Optional, Tuple


class TransportStatus(str, Enum):
    DISCONNECTED = "disconnected"
    CONNECTING   = "connecting"
    CONNECTED    = "connected"
    FAILED       = "failed"


@dataclass
class ThroughputSample:
    timestamp: float
    bytes_transferred: int


class Transport(ABC):
    """
    Abstract transport. Both WiFiTransport and UsbTransport implement this.
    
    Transports are byte-stream oriented (TCP-like).
    The TransferManager and Scheduler use this interface exclusively.
    """

    def __init__(self, transport_id: str):
        self.transport_id = transport_id
        self._status = TransportStatus.DISCONNECTED
        self._samples: Deque[ThroughputSample] = deque(maxlen=20)
        self._bytes_sent: int = 0
        self._bytes_received: int = 0
        self._lock = Lock()

    @property
    def status(self) -> TransportStatus:
        return self._status

    def is_connected(self) -> bool:
        return self._status == TransportStatus.CONNECTED

    @abstractmethod
    def connect(self, host: str, port: int, timeout: float = 10.0) -> None:
        """Connect to remote endpoint. Raises on failure."""
        ...

    @abstractmethod
    def disconnect(self) -> None:
        """Gracefully disconnect."""
        ...

    @abstractmethod
    def send(self, data: bytes) -> int:
        """Send bytes. Returns number of bytes sent. Raises on error."""
        ...

    @abstractmethod
    def recv(self, n: int) -> bytes:
        """Receive exactly n bytes. Raises on error."""
        ...

    def send_all(self, data: bytes) -> None:
        """Send all bytes, retrying as needed."""
        total = len(data)
        sent = 0
        while sent < total:
            n = self.send(data[sent:])
            sent += n
        self._record_sent(total)

    def recv_exact(self, n: int) -> bytes:
        """Receive exactly n bytes, buffering as needed."""
        buf = bytearray()
        while len(buf) < n:
            chunk = self.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("Connection closed while receiving")
            buf.extend(chunk)
        self._record_received(n)
        return bytes(buf)

    def throughput_bps(self, window_seconds: float = 2.0) -> float:
        """
        Estimate current throughput in bytes/second over the last window_seconds.
        Returns 0.0 if not enough data.
        """
        now = time.monotonic()
        cutoff = now - window_seconds
        with self._lock:
            recent = [s for s in self._samples if s.timestamp >= cutoff]
        if len(recent) < 2:
            return 0.0
        total_bytes = sum(s.bytes_transferred for s in recent)
        duration = recent[-1].timestamp - recent[0].timestamp
        if duration <= 0:
            return 0.0
        return total_bytes / duration

    def _record_sent(self, n: int) -> None:
        with self._lock:
            self._bytes_sent += n
            self._samples.append(ThroughputSample(time.monotonic(), n))

    def _record_received(self, n: int) -> None:
        with self._lock:
            self._bytes_received += n
            self._samples.append(ThroughputSample(time.monotonic(), n))

    @property
    def bytes_sent(self) -> int:
        return self._bytes_sent

    @property
    def bytes_received(self) -> int:
        return self._bytes_received
