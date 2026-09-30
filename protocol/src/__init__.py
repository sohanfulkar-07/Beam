"""
PhotoBeam Protocol Package
Reference implementation in Python.
Used by Windows app and as the canonical protocol definition.
"""
from .models import (
    QRPayload,
    TransferInfo,
    ChunkFrame,
    ControlMessage,
    SessionState,
    TransferState,
    ChunkState,
    TransportType,
    MessageType,
    FrameType,
    PROTOCOL_VERSION,
    CHUNK_MAGIC,
    DEFAULT_PORT,
    DEFAULT_CHUNK_SIZE,
    QR_EXPIRY_SECONDS,
)
from .session import SessionManager
from .qr_payload import encode_qr_payload, decode_qr_payload, generate_qr_image
from .integrity import IntegrityManager
from .chunk import ChunkManager
from .transport import Transport, TransportStatus
from .resume import ResumeManager
from .storage import StorageManager
from .scheduler import Scheduler
from .transfer import TransferManager

__all__ = [
    "QRPayload", "TransferInfo", "ChunkFrame", "ControlMessage",
    "SessionState", "TransferState", "ChunkState",
    "TransportType", "MessageType", "FrameType",
    "PROTOCOL_VERSION", "CHUNK_MAGIC", "DEFAULT_PORT",
    "DEFAULT_CHUNK_SIZE", "QR_EXPIRY_SECONDS",
    "SessionManager",
    "encode_qr_payload", "decode_qr_payload", "generate_qr_image",
    "IntegrityManager",
    "ChunkManager",
    "Transport", "TransportStatus",
    "ResumeManager",
    "StorageManager",
    "Scheduler",
    "TransferManager",
]
