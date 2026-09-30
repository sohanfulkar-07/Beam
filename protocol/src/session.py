"""
PhotoBeam Protocol — Session Manager

Creates and validates sessions, tokens, and QR payloads.
"""
from __future__ import annotations

import secrets
import time
import uuid
from dataclasses import dataclass, field
from threading import Lock
from typing import Dict, List, Optional

from .models import (
    QRPayload,
    SessionState,
    TransportType,
    PROTOCOL_VERSION,
    DEFAULT_PORT,
    QR_EXPIRY_SECONDS,
)


@dataclass
class Session:
    sid: str
    rid: str
    token: str            # hex
    created_at: float
    expires_at: float
    state: SessionState = SessionState.PENDING
    cert_pem: Optional[bytes] = None
    cert_fp: str = ""     # "sha256:<hex>"
    addrs: List[str] = field(default_factory=list)
    port: int = DEFAULT_PORT
    transports: List[str] = field(default_factory=lambda: ["wifi"])
    sender_id: Optional[str] = None

    def is_expired(self) -> bool:
        return time.time() > self.expires_at

    def is_valid_token(self, token: str) -> bool:
        return secrets.compare_digest(self.token, token)


class SessionManager:
    """
    Manages the lifecycle of PhotoBeam sessions.
    Thread-safe.
    """

    def __init__(self, device_id: Optional[str] = None):
        self._device_id = device_id or str(uuid.uuid4())
        self._sessions: Dict[str, Session] = {}
        self._lock = Lock()

    @property
    def device_id(self) -> str:
        return self._device_id

    def create_session(
        self,
        addrs: List[str],
        port: int = DEFAULT_PORT,
        transports: Optional[List[str]] = None,
        expiry_seconds: int = QR_EXPIRY_SECONDS,
        cert_pem: Optional[bytes] = None,
        cert_fp: str = "",
    ) -> Session:
        """Create a new session and return it."""
        now = time.time()
        session = Session(
            sid=str(uuid.uuid4()),
            rid=self._device_id,
            token=secrets.token_hex(32),
            created_at=now,
            expires_at=now + expiry_seconds,
            addrs=addrs,
            port=port,
            transports=transports or ["wifi"],
            cert_pem=cert_pem,
            cert_fp=cert_fp,
        )
        with self._lock:
            # Expire old sessions
            self._purge_expired()
            self._sessions[session.sid] = session
        return session

    def get_session(self, sid: str) -> Optional[Session]:
        with self._lock:
            return self._sessions.get(sid)

    def validate_hello(self, sid: str, token: str) -> tuple[bool, str]:
        """
        Validate a HELLO message from sender.
        Returns (ok, error_reason).
        """
        with self._lock:
            session = self._sessions.get(sid)
        if session is None:
            return False, "unknown_session"
        if session.is_expired():
            self._invalidate(sid, SessionState.EXPIRED)
            return False, "session_expired"
        if not session.is_valid_token(token):
            return False, "invalid_token"
        if session.state not in (SessionState.PENDING, SessionState.CONNECTED, SessionState.PAUSED):
            return False, f"invalid_state:{session.state}"
        return True, ""

    def mark_connected(self, sid: str, sender_id: str) -> None:
        with self._lock:
            s = self._sessions.get(sid)
            if s:
                s.state = SessionState.CONNECTED
                s.sender_id = sender_id

    def mark_transferring(self, sid: str) -> None:
        with self._lock:
            s = self._sessions.get(sid)
            if s:
                s.state = SessionState.TRANSFERRING

    def mark_completed(self, sid: str) -> None:
        self._invalidate(sid, SessionState.COMPLETED)

    def mark_cancelled(self, sid: str) -> None:
        self._invalidate(sid, SessionState.CANCELLED)

    def mark_error(self, sid: str) -> None:
        self._invalidate(sid, SessionState.ERROR)

    def build_qr_payload(self, session: Session) -> QRPayload:
        return QRPayload(
            v=PROTOCOL_VERSION,
            sid=session.sid,
            rid=session.rid,
            addrs=session.addrs,
            port=session.port,
            transports=session.transports,
            token=session.token,
            exp=int(session.expires_at),
            cert_fp=session.cert_fp,
        )

    def _invalidate(self, sid: str, state: SessionState) -> None:
        with self._lock:
            s = self._sessions.get(sid)
            if s:
                s.state = state

    def _purge_expired(self) -> None:
        """Remove sessions that have been expired for more than 1 hour."""
        cutoff = time.time() - 3600
        to_remove = [sid for sid, s in self._sessions.items() if s.expires_at < cutoff]
        for sid in to_remove:
            del self._sessions[sid]
