"""
PhotoBeam 2.0 Core Wire Protocol & Framing
Strict separation of Control and Data channels with explicit length-prefixed framing.
No delimiter parsing, no regex scanning, constant O(1) memory footprint.
"""
from __future__ import annotations

import json
import socket
import struct
from typing import Optional, Tuple

MAGIC_PBEA: bytes = b"PBEA"  # 0x50424541
CONTROL_PORT: int = 47470
CONTROL_USB_PORT: int = 47471
DATA_PORT: int = 47474
DATA_USB_PORT: int = 47475

# PC -> Phone Forwarding Ports (Windows localhost -> Android listener)
PC_TO_PHONE_CONTROL_FORWARD_PORT: int = 47480
PC_TO_PHONE_DATA_FORWARD_PORT: int = 47484

CHUNK_BUFFER_SIZE: int = 4 * 1024 * 1024  # 4 MB streaming buffer


def recv_exact(sock: socket.socket, n: int) -> Optional[bytes]:
    """
    Read exactly n bytes from a socket.
    Returns None if peer closes connection (clean EOF: 0 bytes received).
    Raises socket.timeout if socket timeout expires before any/all bytes arrive.
    """
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(min(n - len(buf), 65536))
        if not chunk:
            return None
        buf.extend(chunk)
    return bytes(buf)


def send_framed_msg(sock: socket.socket, msg: dict) -> bool:
    """Send an explicit length-prefixed JSON message (4-byte big-endian uint32 + UTF-8 body)."""
    try:
        body = json.dumps(msg, separators=(",", ":")).encode("utf-8")
        header = struct.pack(">I", len(body))
        sock.sendall(header + body)
        return True
    except Exception:
        return False


def recv_framed_msg(sock: socket.socket, timeout: Optional[float] = None) -> Optional[dict]:
    """
    Receive an explicit length-prefixed JSON message.
    Returns parsed dict, or None on EOF (connection closed cleanly by peer).
    Raises (socket.timeout, TimeoutError) if socket times out while waiting.
    """
    if timeout is not None:
        sock.settimeout(timeout)
    len_bytes = recv_exact(sock, 4)
    if len_bytes is None:
        return None
    (msg_len,) = struct.unpack(">I", len_bytes)
    if msg_len <= 0 or msg_len > 10 * 1024 * 1024:  # 10 MB sanity ceiling for control JSON
        raise ValueError(f"Invalid message length: {msg_len}")
    payload_bytes = recv_exact(sock, msg_len)
    if payload_bytes is None:
        return None
    return json.loads(payload_bytes.decode("utf-8"))


def pack_data_header(transfer_id: int, file_size: int, filename: str) -> bytes:
    """
    Pack binary transfer stream header:
    - 4-byte Magic (0x50424541 = 'PBEA')
    - 8-byte Transfer ID (uint64)
    - 8-byte Total File Size (uint64)
    - 2-byte Filename Length (uint16)
    - N-byte Filename (UTF-8)
    """
    name_bytes = filename.encode("utf-8")
    header_fixed = struct.pack(">4sQQH", MAGIC_PBEA, transfer_id, file_size, len(name_bytes))
    return header_fixed + name_bytes


def unpack_data_header(sock: socket.socket) -> Optional[Tuple[int, int, str]]:
    """
    Read and unpack binary transfer stream header from socket.
    Returns (transfer_id, file_size, filename) or None on failure/EOF.
    """
    fixed = recv_exact(sock, 4 + 8 + 8 + 2)
    if not fixed:
        return None
    magic, transfer_id, file_size, name_len = struct.unpack(">4sQQH", fixed)
    if magic != MAGIC_PBEA:
        return None
    name_bytes = recv_exact(sock, name_len)
    if not name_bytes:
        return None
    filename = name_bytes.decode("utf-8", errors="replace")
    return transfer_id, file_size, filename
