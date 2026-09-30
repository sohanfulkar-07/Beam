"""
PhotoBeam Protocol — Core Data Models

All protocol constants, dataclasses, and enums.
"""
from __future__ import annotations

import struct
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import List, Optional, Dict

# ── Constants ────────────────────────────────────────────────────────────────

PROTOCOL_VERSION: int = 1
CHUNK_MAGIC: bytes = b"PBMC"          # 0x50424D43
DEFAULT_PORT: int = 47474
DEFAULT_USB_PORT: int = 47475          # ADB reverse tunnel port
DEFAULT_CHUNK_SIZE: int = 4 * 1024 * 1024  # 4 MB
QR_EXPIRY_SECONDS: int = 86400        # 24 hours (no short expiration)
CHUNK_HEADER_SIZE: int = 68           # bytes, fixed

# ── Enums ─────────────────────────────────────────────────────────────────────

class TransportType(str, Enum):
    WIFI = "wifi"
    USB  = "usb"


class TrustStatus(str, Enum):
    UNPAIRED          = "unpaired"
    PENDING_APPROVAL  = "pending_approval"
    TRUSTED           = "trusted"
    REVOKED           = "revoked"


class PresenceState(str, Enum):
    UNKNOWN       = "unknown"
    SEARCHING     = "searching"
    DISCOVERED    = "discovered"
    UNAVAILABLE   = "unavailable"


class ConnectionState(str, Enum):
    DISCONNECTED        = "disconnected"
    CONNECTING          = "connecting"
    CONNECTED           = "connected"
    RECONNECTING        = "reconnecting"
    AUTHENTICATION_REQUIRED = "authentication_required"


class Capability(str, Enum):
    FILE_TRANSFER          = "file_transfer"
    SCREEN_MIRROR_SEND     = "screen_mirror_send"
    SCREEN_MIRROR_RECEIVE  = "screen_mirror_receive"
    SECOND_DISPLAY         = "second_display"


class SessionState(str, Enum):
    PENDING      = "pending"    # QR generated, waiting for sender
    CONNECTED    = "connected"  # Sender authenticated
    TRANSFERRING = "transferring"
    PAUSING      = "pausing"    # In process of finishing chunk and flushing state
    PAUSED       = "paused"     # Transfer paused (e.g. transport lost, resumable)
    VERIFYING    = "verifying"  # Verifying state before continuing
    RESUMING     = "resuming"   # Continuing from verified safe position
    COMPLETED    = "completed"
    CANCELLED    = "cancelled"
    EXPIRED      = "expired"
    ERROR        = "error"


class TransferState(str, Enum):
    PENDING      = "pending"
    ACTIVE       = "active"
    PAUSING      = "pausing"
    PAUSED       = "paused"
    VERIFYING    = "verifying"
    RESUMING     = "resuming"
    COMPLETED    = "completed"
    FAILED       = "failed"
    CANCELLED    = "cancelled"


class ChunkState(str, Enum):
    PENDING     = "pending"
    IN_FLIGHT   = "in_flight"
    RECEIVED    = "received"
    FAILED      = "failed"


class MessageType(str, Enum):
    # Sender → Receiver / Bidirectional
    HELLO        = "HELLO"
    READY        = "READY"
    CANCEL       = "CANCEL"
    PING         = "PING"
    RESUME       = "RESUME"
    PAUSE        = "PAUSE"
    CHUNK_SENT   = "CHUNK_SENT"  # used on USB when no separate data channel
    # Receiver → Sender / Bidirectional
    HELLO_ACK    = "HELLO_ACK"
    ACCEPT       = "ACCEPT"
    REJECT       = "REJECT"
    ACK_CHUNK    = "ACK_CHUNK"
    NAK_CHUNK    = "NAK_CHUNK"
    FILE_CHECKSUM = "FILE_CHECKSUM"
    FILE_DONE    = "FILE_DONE"
    FILE_ERROR   = "FILE_ERROR"
    PAUSE_ACK    = "PAUSE_ACK"
    RESUME_ACK   = "RESUME_ACK"
    RESUME_STATE = "RESUME_STATE"
    PONG         = "PONG"
    STORAGE_ERROR = "STORAGE_ERROR"
    ERROR        = "ERROR"


class FrameType(IntEnum):
    DATA        = 0x0001
    RETRANSMIT  = 0x0002


# ── Data Models ──────────────────────────────────────────────────────────────

@dataclass
class QRPayload:
    """Contents encoded into the QR code URI."""
    v: int
    sid: str                    # session UUID
    rid: str                    # receiver device UUID
    addrs: List[str]            # receiver IP addresses
    port: int
    transports: List[str]       # ["wifi"], ["wifi","usb"], etc.
    token: str                  # 32-byte hex auth token
    exp: int                    # Unix timestamp expiry
    cert_fp: str                # "sha256:<hex>"

    def is_expired(self) -> bool:
        return time.time() > self.exp

    def to_dict(self) -> dict:
        return {
            "v": self.v,
            "sid": self.sid,
            "rid": self.rid,
            "addrs": self.addrs,
            "port": self.port,
            "transports": self.transports,
            "token": self.token,
            "exp": self.exp,
            "cert_fp": self.cert_fp,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "QRPayload":
        return cls(
            v=d["v"],
            sid=d["sid"],
            rid=d["rid"],
            addrs=d["addrs"],
            port=d["port"],
            transports=d["transports"],
            token=d["token"],
            exp=d["exp"],
            cert_fp=d["cert_fp"],
        )


@dataclass
class TransferInfo:
    """File metadata sent in the READY message."""
    fid: str              # UUID
    name: str             # basename
    rel_path: str         # relative path (for folder transfers)
    size: int             # bytes
    chunk_size: int
    total_chunks: int
    sha256: str           # hex digest of complete file

    def to_dict(self) -> dict:
        return {
            "fid": self.fid,
            "name": self.name,
            "rel_path": self.rel_path,
            "size": self.size,
            "chunk_size": self.chunk_size,
            "total_chunks": self.total_chunks,
            "sha256": self.sha256,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "TransferInfo":
        return cls(**d)


@dataclass
class ControlMessage:
    """A parsed JSON control message."""
    type: str
    data: dict

    def to_dict(self) -> dict:
        d = {"type": self.type}
        d.update(self.data)
        return d


@dataclass
class ChunkFrame:
    """
    Binary chunk frame.
    Header layout (68 bytes):
      [0:4]   Magic "PBMC"
      [4:6]   Version (uint16 BE)
      [6:8]   Frame type (uint16 BE)
      [8:12]  Payload length (uint32 BE)  = 40 + chunk_len
      [12:28] Transfer ID (16 bytes UUID)
      [28:44] File ID (16 bytes UUID)
      [44:48] Chunk ID (uint32 BE)
      [48:56] Byte offset (uint64 BE)
      [56:60] Chunk data length (uint32 BE)
      [60:68] XXH3-64 checksum (uint64 BE)
      [68:68+chunk_len] data
    """
    transfer_id: bytes   # 16 bytes
    file_id: bytes       # 16 bytes
    chunk_id: int
    offset: int
    data: bytes
    checksum: int        # xxh3_64 of data
    frame_type: int = FrameType.DATA
    version: int = PROTOCOL_VERSION

    def encode(self) -> bytes:
        chunk_len = len(self.data)
        payload_len = 40 + chunk_len  # 16+16+4+8+4+8 - 16 (transfer+file already in fixed area) ... see layout
        # Actually payload_len = total bytes after the 12-byte prefix = 56 + chunk_len
        payload_len = 56 + chunk_len
        header = struct.pack(
            ">4sHHI16s16sIQIQ",
            CHUNK_MAGIC,
            self.version,
            self.frame_type,
            payload_len,
            self.transfer_id,
            self.file_id,
            self.chunk_id,
            self.offset,
            chunk_len,
            self.checksum,
        )
        return header + self.data

    @classmethod
    def decode(cls, buf: bytes) -> "ChunkFrame":
        if len(buf) < CHUNK_HEADER_SIZE:
            raise ValueError(f"Buffer too short: {len(buf)} < {CHUNK_HEADER_SIZE}")
        magic, version, frame_type, payload_len, tid, fid, cid, offset, chunk_len, checksum = struct.unpack_from(
            ">4sHHI16s16sIQIQ", buf, 0
        )
        if magic != CHUNK_MAGIC:
            raise ValueError(f"Bad magic: {magic!r}")
        if version != PROTOCOL_VERSION:
            raise ValueError(f"Unsupported version: {version}")
        data = buf[CHUNK_HEADER_SIZE: CHUNK_HEADER_SIZE + chunk_len]
        if len(data) != chunk_len:
            raise ValueError(f"Truncated chunk data: got {len(data)}, expected {chunk_len}")
        return cls(
            transfer_id=tid,
            file_id=fid,
            chunk_id=cid,
            offset=offset,
            data=data,
            checksum=checksum,
            frame_type=frame_type,
            version=version,
        )

    @property
    def total_size(self) -> int:
        return CHUNK_HEADER_SIZE + len(self.data)


# ── Device Identity & Pairing Models ──────────────────────────────────────────

@dataclass
class DeviceIdentity:
    """Persistent device identity, generated once per installation."""
    device_id: str                    # stable UUID (rid)
    name: str                         # user-editable display name
    public_key: str                   # base64-encoded Ed25519 public key
    created_at: int                   # unix timestamp
    last_seen: int                    # unix timestamp
    trust_status: TrustStatus = TrustStatus.UNPAIRED
    app_version: str = ""
    protocol_version: int = PROTOCOL_VERSION
    capabilities: List[Capability] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "device_id": self.device_id,
            "name": self.name,
            "public_key": self.public_key,
            "created_at": self.created_at,
            "last_seen": self.last_seen,
            "trust_status": self.trust_status.value,
            "app_version": self.app_version,
            "protocol_version": self.protocol_version,
            "capabilities": [c.value for c in self.capabilities],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "DeviceIdentity":
        return cls(
            device_id=d["device_id"],
            name=d["name"],
            public_key=d["public_key"],
            created_at=d["created_at"],
            last_seen=d["last_seen"],
            trust_status=TrustStatus(d.get("trust_status", "unpaired")),
            app_version=d.get("app_version", ""),
            protocol_version=d.get("protocol_version", PROTOCOL_VERSION),
            capabilities=[Capability(c) for c in d.get("capabilities", [])],
        )


@dataclass
class DeviceEndpoint:
    """Last known network endpoint for a device."""
    addrs: List[str]            # IP addresses
    port: int
    transports: List[str]       # ["wifi"], ["wifi","usb"]
    cert_fp: str                # TLS cert fingerprint "sha256:<hex>"
    updated_at: int             # unix timestamp

    def to_dict(self) -> dict:
        return {
            "addrs": self.addrs,
            "port": self.port,
            "transports": self.transports,
            "cert_fp": self.cert_fp,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "DeviceEndpoint":
        return cls(
            addrs=d["addrs"],
            port=d["port"],
            transports=d["transports"],
            cert_fp=d["cert_fp"],
            updated_at=d["updated_at"],
        )


@dataclass
class PairedDevice:
    """A trusted peer device with its identity and connection info."""
    identity: DeviceIdentity
    endpoint: Optional[DeviceEndpoint] = None
    connection_state: ConnectionState = ConnectionState.DISCONNECTED
    presence_state: PresenceState = PresenceState.UNKNOWN
    last_connection_attempt: int = 0
    last_successful_connection: int = 0

    def to_dict(self) -> dict:
        return {
            "identity": self.identity.to_dict(),
            "endpoint": self.endpoint.to_dict() if self.endpoint else None,
            "connection_state": self.connection_state.value,
            "presence_state": self.presence_state.value,
            "last_connection_attempt": self.last_connection_attempt,
            "last_successful_connection": self.last_successful_connection,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "PairedDevice":
        return cls(
            identity=DeviceIdentity.from_dict(d["identity"]),
            endpoint=DeviceEndpoint.from_dict(d["endpoint"]) if d.get("endpoint") else None,
            connection_state=ConnectionState(d.get("connection_state", "disconnected")),
            presence_state=PresenceState(d.get("presence_state", "unknown")),
            last_connection_attempt=d.get("last_connection_attempt", 0),
            last_successful_connection=d.get("last_successful_connection", 0),
        )


@dataclass
class PairingPayload:
    """Extended QR payload for initial pairing (includes device identity)."""
    # Base QR fields
    v: int
    sid: str
    rid: str
    addrs: List[str]
    port: int
    transports: List[str]
    token: str
    exp: int
    cert_fp: str
    # Pairing-specific fields
    device_name: str
    device_public_key: str
    capabilities: List[str]
    pairing_nonce: str        # base64, 32 bytes

    def to_dict(self) -> dict:
        return {
            "v": self.v,
            "sid": self.sid,
            "rid": self.rid,
            "addrs": self.addrs,
            "port": self.port,
            "transports": self.transports,
            "token": self.token,
            "exp": self.exp,
            "cert_fp": self.cert_fp,
            "device_name": self.device_name,
            "device_public_key": self.device_public_key,
            "capabilities": self.capabilities,
            "pairing_nonce": self.pairing_nonce,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "PairingPayload":
        return cls(**d)

    def is_expired(self) -> bool:
        return time.time() > self.exp


def encode_pairing_payload(payload: PairingPayload) -> str:
    """Encode PairingPayload to photobeam://pair/... URI."""
    import base64
    import json
    json_str = json.dumps(payload.to_dict(), separators=(',', ':'))
    b64 = base64.urlsafe_b64encode(json_str.encode("utf-8")).decode("ascii").rstrip("=")
    return f"photobeam://pair/{b64}"


def decode_pairing_payload(uri: str) -> PairingPayload:
    """Decode photobeam://pair/... URI to PairingPayload."""
    import base64
    import json
    prefix = "photobeam://pair/"
    if not uri.startswith(prefix):
        raise ValueError("Not a PhotoBeam pairing URI")
    b64 = uri[len(prefix):]
    # Add padding if needed
    b64 += "=" * ((4 - len(b64) % 4) % 4)
    json_str = base64.urlsafe_b64decode(b64).decode("utf-8")
    d = json.loads(json_str)
    return PairingPayload.from_dict(d)
