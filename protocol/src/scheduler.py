"""
PhotoBeam Protocol — Multi-Path Scheduler

Dynamically allocates chunks to available transports based on measured throughput.
Handles transport failure and reconnection.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from threading import Lock
from typing import Dict, List, Optional, Tuple

from .transport import Transport, TransportStatus


@dataclass
class TransportSlot:
    transport: Transport
    weight: float = 1.0       # proportional to throughput
    last_measured: float = 0.0
    consecutive_failures: int = 0
    enabled: bool = True


class Scheduler:
    """
    Assigns pending chunks to transports proportionally to their throughput.
    
    Algorithm:
    1. Every MEASURE_INTERVAL seconds, sample each transport's throughput.
    2. Compute weight = throughput_i / sum(all_throughputs).
    3. next_transport() returns the transport with the highest deficit
       (weighted round-robin / proportional dispatch).
    4. If a transport fails, disable it until reconnected.
    5. If all transports fail, raise TransportUnavailableError.
    """

    MEASURE_INTERVAL = 0.5    # seconds
    MIN_WEIGHT = 0.05         # minimum weight when transport is slow but alive
    MAX_FAILURES = 3          # consecutive failures before disabling transport

    def __init__(self):
        self._slots: Dict[str, TransportSlot] = {}
        self._counters: Dict[str, float] = {}  # accumulated deficit per transport
        self._lock = Lock()
        self._last_measure = 0.0

    def add_transport(self, transport: Transport) -> None:
        with self._lock:
            self._slots[transport.transport_id] = TransportSlot(transport=transport)
            self._counters[transport.transport_id] = 0.0

    def remove_transport(self, transport_id: str) -> None:
        with self._lock:
            self._slots.pop(transport_id, None)
            self._counters.pop(transport_id, None)

    def report_failure(self, transport_id: str) -> None:
        """Report a chunk send failure on a transport."""
        with self._lock:
            slot = self._slots.get(transport_id)
            if slot:
                slot.consecutive_failures += 1
                if slot.consecutive_failures >= self.MAX_FAILURES:
                    slot.enabled = False

    def report_success(self, transport_id: str) -> None:
        """Report a successful chunk send."""
        with self._lock:
            slot = self._slots.get(transport_id)
            if slot:
                slot.consecutive_failures = 0

    def mark_reconnected(self, transport_id: str) -> None:
        """Re-enable a transport after reconnection."""
        with self._lock:
            slot = self._slots.get(transport_id)
            if slot:
                slot.enabled = True
                slot.consecutive_failures = 0

    def next_transport(self) -> Optional[Transport]:
        """
        Return the best transport for the next chunk.
        Returns None if no transport is available.
        """
        with self._lock:
            self._maybe_remeasure()
            active = [
                (tid, slot)
                for tid, slot in self._slots.items()
                if slot.enabled and slot.transport.is_connected()
            ]
            if not active:
                return None

            if len(active) == 1:
                return active[0][1].transport

            # Weighted round-robin: pick transport with highest counter deficit
            # Counter increments by weight each round, dispatches decrement by 1
            for tid, slot in active:
                self._counters[tid] = self._counters.get(tid, 0.0) + slot.weight

            best_tid = max(active, key=lambda x: self._counters[x[0]])[0]
            self._counters[best_tid] -= 1.0
            return self._slots[best_tid].transport

    def available_transports(self) -> List[Transport]:
        with self._lock:
            return [
                s.transport for s in self._slots.values()
                if s.enabled and s.transport.is_connected()
            ]

    def has_any_transport(self) -> bool:
        with self._lock:
            return any(
                s.enabled and s.transport.is_connected()
                for s in self._slots.values()
            )

    def throughput_summary(self) -> Dict[str, float]:
        """Returns {transport_id: bps} for all connected transports."""
        with self._lock:
            return {
                tid: s.transport.throughput_bps()
                for tid, s in self._slots.items()
                if s.transport.is_connected()
            }

    def _maybe_remeasure(self) -> None:
        """Update weights from measured throughput. Call with lock held."""
        now = time.monotonic()
        if now - self._last_measure < self.MEASURE_INTERVAL:
            return
        self._last_measure = now

        speeds = {}
        unmeasured = []
        for tid, slot in self._slots.items():
            if slot.enabled and slot.transport.is_connected():
                bps = slot.transport.throughput_bps()
                if bps > 0:
                    speeds[tid] = bps
                else:
                    unmeasured.append(tid)

        if not speeds:
            # None have measurements yet, keep current weights
            for tid, slot in self._slots.items():
                slot.weight = 1.0 / max(1, len(self._slots))
            return

        # Assign average speed to unmeasured transports so they can probe and measure
        avg_speed = sum(speeds.values()) / len(speeds)
        for tid in unmeasured:
            speeds[tid] = avg_speed

        total = sum(speeds.values())
        for tid, bps in speeds.items():
            raw_weight = bps / total
            self._slots[tid].weight = max(raw_weight, self.MIN_WEIGHT)
            self._slots[tid].last_measured = now
