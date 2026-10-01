"""
PhotoBeam — Wi-Fi Transport (Windows/Python)

TCP socket implementation of the Transport abstract base class.
Both sender and receiver use this; receiver calls accept(), sender calls connect().
"""
from __future__ import annotations

import socket
import ssl
import struct
import tempfile
import time
from pathlib import Path
from typing import Optional

import sys
import os
if getattr(sys, 'frozen', False):
    _proto = os.path.join(getattr(sys, '_MEIPASS', os.path.dirname(sys.executable)), 'protocol')
else:
    _proto = os.path.join(os.path.dirname(__file__), '..', '..', '..', 'protocol')
if _proto not in sys.path:
    sys.path.insert(0, _proto)


from src.transport import Transport, TransportStatus



class WiFiTransport(Transport):
    """
    TCP transport over local Wi-Fi.
    Uses TLS 1.3 for encryption.
    Sender = client side (connect).
    Receiver = server side (accept, then wraps socket).
    """

    RECV_BUFFER_SIZE = 65536  # 64 KB receive buffer

    def __init__(self, transport_id: str = "wifi"):
        super().__init__(transport_id)
        self._sock: Optional[ssl.SSLSocket] = None
        self._raw_sock: Optional[socket.socket] = None
        self._recv_buf = bytearray()

    # ── Client (sender) side ─────────────────────────────────────────────────

    def connect(self, host: str, port: int, timeout: float = 10.0,
                cert_fp: Optional[str] = None) -> None:
        """
        Connect to receiver.
        cert_fp: "sha256:<hex>" — if given, verify server cert fingerprint (TOFU).
        """
        self._status = TransportStatus.CONNECTING
        print(f"[DIAG] [CONNECTION_START] Connecting to {host}:{port} via {self.transport_id}...")
        try:
            raw = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            raw.settimeout(timeout)
            raw.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            raw.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            if hasattr(socket, "SIO_KEEPALIVE_VALS"):
                try:
                    raw.ioctl(socket.SIO_KEEPALIVE_VALS, (1, 10000, 5000))
                except OSError:
                    pass
            elif hasattr(socket, "TCP_KEEPIDLE"):
                try:
                    raw.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 10)
                    raw.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 5)
                except OSError:
                    pass

            # TLS context — we verify the cert fingerprint manually (TOFU)
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE  # We verify fingerprint manually

            tls_sock = ctx.wrap_socket(raw, server_hostname=host)
            tls_sock.connect((host, port))

            # Verify cert fingerprint if provided
            if cert_fp:
                der = tls_sock.getpeercert(binary_form=True)
                import hashlib
                actual_fp = "sha256:" + hashlib.sha256(der).hexdigest()
                if actual_fp.lower() != cert_fp.lower():
                    tls_sock.close()
                    raise ConnectionError(
                        f"TLS cert fingerprint mismatch: expected {cert_fp}, got {actual_fp}"
                    )

            tls_sock.settimeout(None)  # blocking mode after connect
            self._sock = tls_sock
            self._raw_sock = raw
            self._recv_buf.clear()
            self._status = TransportStatus.CONNECTED
            print(f"[DIAG] [CONNECTION_ESTABLISHED] Connected to {host}:{port} via {self.transport_id}")
        except Exception as e:
            self._status = TransportStatus.FAILED
            print(f"[DIAG] [CONNECTION_LOST] Connect failed to {host}:{port}: {e}")
            raise

    # ── Server (receiver) side ───────────────────────────────────────────────

    @classmethod
    def from_accepted_socket(
        cls,
        tls_sock: ssl.SSLSocket,
        transport_id: str = "wifi",
    ) -> "WiFiTransport":
        """Create a WiFiTransport from an already-accepted TLS socket."""
        t = cls(transport_id)
        t._sock = tls_sock
        try:
            tls_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            tls_sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        except OSError:
            pass
        t._status = TransportStatus.CONNECTED
        return t

    # ── Transport interface ───────────────────────────────────────────────────

    def disconnect(self) -> None:
        self._status = TransportStatus.DISCONNECTED
        if self._sock:
            try:
                self._sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        self._recv_buf.clear()
        print(f"[DIAG] [CONNECTION_CLOSE] Transport {self.transport_id} disconnected")

    def send(self, data: bytes) -> int:
        if not self._sock:
            raise ConnectionError("Not connected")
        return self._sock.send(data)

    def recv(self, n: int) -> bytes:
        if not self._sock:
            raise ConnectionError("Not connected")
        if self._recv_buf:
            take = min(n, len(self._recv_buf))
            chunk = bytes(self._recv_buf[:take])
            del self._recv_buf[:take]
            return chunk
        return self._sock.recv(min(n, self.RECV_BUFFER_SIZE))

    def send_all(self, data: bytes) -> None:
        """Override to use sendall for efficiency."""
        if not self._sock:
            raise ConnectionError("Not connected")
        self._sock.sendall(data)
        self._record_sent(len(data))

    def recv_exact(self, n: int) -> bytes:
        """Receive exactly n bytes."""
        buf = bytearray()
        while len(buf) < n:
            chunk = self.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("Connection closed while receiving")
            buf.extend(chunk)
        self._record_received(n)
        return bytes(buf)

    # ── Control message helpers (newline-delimited JSON) ──────────────────────

    def send_json(self, msg: dict) -> None:
        import json
        line = json.dumps(msg, separators=(",", ":")) + "\n"
        self.send_all(line.encode("utf-8"))

    def recv_json(self, timeout: float = 30.0) -> dict:
        """Read one newline-terminated JSON message without discarding stream bytes."""
        import json
        prev_timeout = None
        if self._sock:
            prev_timeout = self._sock.gettimeout()
            self._sock.settimeout(timeout)
        try:
            while b"\n" not in self._recv_buf:
                if not self._sock:
                    raise ConnectionError("Not connected")
                chunk = self._sock.recv(4096)
                if not chunk:
                    raise ConnectionError("Connection closed while receiving JSON")
                self._recv_buf.extend(chunk)
            idx = self._recv_buf.index(b"\n")
            line = bytes(self._recv_buf[:idx])
            del self._recv_buf[:idx + 1]
            return json.loads(line.decode("utf-8"))
        finally:
            if self._sock:
                self._sock.settimeout(prev_timeout)

    def send_chunk_frame(self, frame) -> None:
        """Send a ChunkFrame over the transport."""
        self.send_all(frame.encode())

    def recv_chunk_frame(self, timeout: float = 30.0):
        """Receive a single ChunkFrame."""
        import struct
        try:
            from src.models import CHUNK_HEADER_SIZE, ChunkFrame
        except ImportError:
            from protocol.src.models import CHUNK_HEADER_SIZE, ChunkFrame

        if self._sock:
            self._sock.settimeout(timeout)
        try:
            header = self.recv_exact(CHUNK_HEADER_SIZE)
            chunk_len = struct.unpack_from(">I", header, 56)[0]
            data = self.recv_exact(chunk_len) if chunk_len > 0 else b""
            return ChunkFrame.decode(header + data)
        finally:
            if self._sock:
                self._sock.settimeout(None)


class WiFiServer:
    """
    TLS server that listens for incoming PhotoBeam connections.
    Receiver runs this.
    """

    def __init__(self, port: int, cert_pem: bytes, key_pem: bytes):
        self._port = port
        self._cert_pem = cert_pem
        self._key_pem = key_pem
        self._server_sock: Optional[socket.socket] = None
        self._ctx: Optional[ssl.SSLContext] = None

    def start(self, host: str = "") -> None:
        """Start listening. host="" binds to all interfaces."""
        self._ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        # Load cert/key from PEM bytes using temp files
        import tempfile, os
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pem") as cf:
            cf.write(self._cert_pem)
            cert_file = cf.name
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pem") as kf:
            kf.write(self._key_pem)
            key_file = kf.name
        try:
            self._ctx.load_cert_chain(cert_file, key_file)
        finally:
            os.unlink(cert_file)
            os.unlink(key_file)

        self._ctx.minimum_version = ssl.TLSVersion.TLSv1_3

        raw = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        raw.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        raw.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        raw.bind((host, self._port))
        raw.listen(5)
        self._server_sock = raw

    def accept(self, timeout: float = 60.0) -> tuple[ssl.SSLSocket, tuple]:
        """Accept one valid TLS connection. Retries if a probe or non-TLS connection drops."""
        if not self._server_sock:
            raise RuntimeError("Server not started")
        deadline = time.time() + timeout
        while time.time() < deadline:
            if not self._server_sock:
                raise TimeoutError("Server stopped")
            rem = min(1.0, max(0.1, deadline - time.time()))
            self._server_sock.settimeout(rem)
            try:
                conn, addr = self._server_sock.accept()
            except (TimeoutError, socket.timeout):
                if time.time() >= deadline or not self._server_sock:
                    raise TimeoutError("Accept timed out")
                continue
            except OSError:
                if not self._server_sock:
                    raise TimeoutError("Server stopped")
                raise

            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            conn.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            if hasattr(socket, "SIO_KEEPALIVE_VALS"):
                try:
                    conn.ioctl(socket.SIO_KEEPALIVE_VALS, (1, 10000, 5000))
                except OSError:
                    pass
            try:
                tls_conn = self._ctx.wrap_socket(conn, server_side=True)
                return tls_conn, addr
            except (ssl.SSLError, OSError) as e:
                # Transient client drop or non-TLS probe connection
                try:
                    conn.close()
                except Exception:
                    pass
                continue
        raise TimeoutError("Accept timed out")

    def stop(self) -> None:
        sock = self._server_sock
        self._server_sock = None
        if sock:
            try:
                port = sock.getsockname()[1]
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(0.2)
                s.connect_ex(("127.0.0.1", port))
                s.close()
            except Exception:
                pass
            try:
                sock.close()
            except OSError:
                pass
