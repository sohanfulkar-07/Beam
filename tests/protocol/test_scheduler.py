"""
Tests: Scheduler — transport selection, weighting, failure handling
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'protocol'))

import time
import pytest
from unittest.mock import MagicMock, patch
from src.scheduler import Scheduler
from src.transport import Transport, TransportStatus


def make_mock_transport(tid: str, bps: float = 100_000_000, connected: bool = True) -> MagicMock:
    t = MagicMock(spec=Transport)
    t.transport_id = tid
    t.is_connected.return_value = connected
    t.throughput_bps.return_value = bps
    return t


def test_no_transports_returns_none():
    s = Scheduler()
    assert s.next_transport() is None
    assert not s.has_any_transport()


def test_single_transport_always_selected():
    s = Scheduler()
    t = make_mock_transport("wifi")
    s.add_transport(t)
    for _ in range(5):
        assert s.next_transport() is t


def test_two_transports_both_selected():
    s = Scheduler()
    t1 = make_mock_transport("wifi", bps=100_000_000)
    t2 = make_mock_transport("usb", bps=50_000_000)
    s.add_transport(t1)
    s.add_transport(t2)

    selected = [s.next_transport().transport_id for _ in range(30)]
    assert "wifi" in selected
    assert "usb" in selected


def test_faster_transport_selected_more():
    s = Scheduler()
    # Force immediate re-measure
    s._last_measure = 0
    t1 = make_mock_transport("wifi", bps=100_000_000)
    t2 = make_mock_transport("usb", bps=10_000_000)
    s.add_transport(t1)
    s.add_transport(t2)

    selected = [s.next_transport().transport_id for _ in range(100)]
    wifi_count = selected.count("wifi")
    usb_count = selected.count("usb")
    # wifi should be selected roughly 10x more
    assert wifi_count > usb_count * 3


def test_failed_transport_disabled():
    s = Scheduler()
    t = make_mock_transport("wifi")
    s.add_transport(t)
    for _ in range(Scheduler.MAX_FAILURES):
        s.report_failure("wifi")
    assert not s._slots["wifi"].enabled
    assert s.next_transport() is None


def test_reconnected_transport_re_enabled():
    s = Scheduler()
    t = make_mock_transport("wifi")
    s.add_transport(t)
    for _ in range(Scheduler.MAX_FAILURES):
        s.report_failure("wifi")
    assert s.next_transport() is None
    s.mark_reconnected("wifi")
    assert s.next_transport() is t


def test_disconnected_transport_skipped():
    s = Scheduler()
    t1 = make_mock_transport("wifi", connected=True)
    t2 = make_mock_transport("usb", connected=False)
    s.add_transport(t1)
    s.add_transport(t2)
    for _ in range(10):
        assert s.next_transport().transport_id == "wifi"


def test_remove_transport():
    s = Scheduler()
    t = make_mock_transport("wifi")
    s.add_transport(t)
    s.remove_transport("wifi")
    assert s.next_transport() is None


def test_throughput_summary():
    s = Scheduler()
    t1 = make_mock_transport("wifi", bps=100_000_000)
    t2 = make_mock_transport("usb", bps=50_000_000)
    s.add_transport(t1)
    s.add_transport(t2)
    summary = s.throughput_summary()
    assert "wifi" in summary
    assert "usb" in summary


def test_success_clears_failure_count():
    s = Scheduler()
    t = make_mock_transport("wifi")
    s.add_transport(t)
    s.report_failure("wifi")
    s.report_failure("wifi")
    s.report_success("wifi")
    assert s._slots["wifi"].consecutive_failures == 0
    assert s._slots["wifi"].enabled
