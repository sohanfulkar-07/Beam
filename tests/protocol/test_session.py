"""
Tests: Session management — create, validate, expiry, state transitions
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'protocol'))

import time
import pytest
from src.session import SessionManager, Session
from src.models import SessionState


def make_manager() -> SessionManager:
    return SessionManager(device_id="test-device-123")


def test_create_session_returns_session():
    mgr = make_manager()
    s = mgr.create_session(addrs=["192.168.1.1"], port=47474)
    assert s.sid
    assert s.rid == "test-device-123"
    assert len(s.token) == 64  # 32 bytes hex
    assert s.state == SessionState.PENDING
    assert not s.is_expired()


def test_get_session():
    mgr = make_manager()
    s = mgr.create_session(addrs=["192.168.1.1"])
    s2 = mgr.get_session(s.sid)
    assert s2 is not None
    assert s2.sid == s.sid


def test_get_nonexistent_session():
    mgr = make_manager()
    assert mgr.get_session("does-not-exist") is None


def test_validate_hello_ok():
    mgr = make_manager()
    s = mgr.create_session(addrs=["192.168.1.1"])
    ok, err = mgr.validate_hello(s.sid, s.token)
    assert ok
    assert err == ""


def test_validate_hello_wrong_token():
    mgr = make_manager()
    s = mgr.create_session(addrs=["192.168.1.1"])
    ok, err = mgr.validate_hello(s.sid, "wrong_token")
    assert not ok
    assert "invalid_token" in err


def test_validate_hello_unknown_session():
    mgr = make_manager()
    ok, err = mgr.validate_hello("fake-sid", "fake-token")
    assert not ok
    assert "unknown_session" in err


def test_validate_hello_expired():
    mgr = make_manager()
    s = mgr.create_session(addrs=["192.168.1.1"], expiry_seconds=-1)  # already expired
    ok, err = mgr.validate_hello(s.sid, s.token)
    assert not ok
    assert "session_expired" in err


def test_state_transitions():
    mgr = make_manager()
    s = mgr.create_session(addrs=["192.168.1.1"])
    assert s.state == SessionState.PENDING

    mgr.mark_connected(s.sid, sender_id="sender-123")
    assert mgr.get_session(s.sid).state == SessionState.CONNECTED
    assert mgr.get_session(s.sid).sender_id == "sender-123"

    mgr.mark_transferring(s.sid)
    assert mgr.get_session(s.sid).state == SessionState.TRANSFERRING

    mgr.mark_completed(s.sid)
    assert mgr.get_session(s.sid).state == SessionState.COMPLETED


def test_build_qr_payload():
    mgr = make_manager()
    s = mgr.create_session(addrs=["192.168.1.1", "10.0.0.1"], port=47474)
    payload = mgr.build_qr_payload(s)
    assert payload.sid == s.sid
    assert payload.rid == s.rid
    assert payload.token == s.token
    assert "192.168.1.1" in payload.addrs
    assert payload.port == 47474


def test_device_id_persistent():
    mgr = make_manager()
    assert mgr.device_id == "test-device-123"


def test_multiple_sessions_independent():
    mgr = make_manager()
    s1 = mgr.create_session(addrs=["192.168.1.1"])
    s2 = mgr.create_session(addrs=["192.168.1.2"])
    assert s1.sid != s2.sid
    assert s1.token != s2.token
    mgr.mark_completed(s1.sid)
    assert mgr.get_session(s2.sid).state == SessionState.PENDING
