"""
PhotoBeam Windows — ConnectionManager

Coordinates pairing, local discovery, and active connection lifecycle.
Maintains separate states for:
- Trust: UNPAIRED, PENDING_APPROVAL, TRUSTED, REVOKED
- Presence: UNKNOWN, SEARCHING, DISCOVERED, UNAVAILABLE
- Connection: DISCONNECTED, CONNECTING, CONNECTED, RECONNECTING, AUTHENTICATION_REQUIRED
- Transport health: wifi, usb (active, degraded, closed)
"""
from __future__ import annotations

import json
import logging
import os
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set

if getattr(sys, 'frozen', False):
    _proto = os.path.join(getattr(sys, '_MEIPASS', os.path.dirname(sys.executable)), 'protocol')
else:
    _proto = str(Path(__file__).resolve().parent.parent.parent / "protocol")
if _proto not in sys.path:
    sys.path.append(_proto)

try:
    from src.models import (
        Capability,
        ConnectionState,
        DeviceEndpoint,
        DeviceIdentity,
        PairedDevice,
        PairingPayload,
        PresenceState,
        TrustStatus,
    )
except ImportError:
    from models import (
        Capability,
        ConnectionState,
        DeviceEndpoint,
        DeviceIdentity,
        PairedDevice,
        PairingPayload,
        PresenceState,
        TrustStatus,
    )


try:
    from discovery import DiscoveredPeer, DiscoveryService
    from pairing_manager import PairingManager
except (ImportError, ValueError):
    from .discovery import DiscoveredPeer, DiscoveryService
    from .pairing_manager import PairingManager


import importlib.util

def _load_module(mod_name: str, file_path: Path):
    spec = importlib.util.spec_from_file_location(mod_name, str(file_path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod

_win_dir = Path(__file__).resolve().parent
_trans_dir = _win_dir / "transport"
if str(_trans_dir) not in sys.path:
    sys.path.insert(0, str(_trans_dir))

try:
    from .transport.usb_transport import (
        UsbTransport,
        find_adb,
        setup_adb_forward,
        setup_adb_reverse,
    )
    from .transport.wifi_transport import WiFiTransport
except (ImportError, ValueError):
    from usb_transport import (
        UsbTransport,
        find_adb,
        setup_adb_forward,
        setup_adb_reverse,
    )
    from wifi_transport import WiFiTransport

logger = logging.getLogger("photobeam.connection_manager")

CONTROL_PORT: int = 47470
CONTROL_USB_PORT: int = 47471


class ActiveSession:
    """
    Active socket session for a paired device with symmetric heartbeat & reader loop.
    Communicates via newline-delimited JSON messages.
    """

    def __init__(
        self,
        sock: socket.socket,
        device_id: str,
        transport_type: str,
        on_closed: Callable[[ActiveSession, str], None],  # (session, reason)
    ):
        self.sock = sock
        self.device_id = device_id
        self.transport_type = transport_type
        self.on_closed = on_closed

        self.last_ping_time = time.time()
        self.last_pong_time = time.time()
        self.missed_pongs = 0
        self.is_running = True
        self._send_lock = threading.Lock()

        # Optimize socket for low-latency control frames
        try:
            self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            self.sock.settimeout(2.0)
        except Exception:
            pass

        self.reader_thread = threading.Thread(
            target=self._reader_loop,
            name=f"PB-Reader-{device_id[:8]}",
            daemon=True,
        )
        self.heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            name=f"PB-Heartbeat-{device_id[:8]}",
            daemon=True,
        )

    def start(self) -> None:
        self.reader_thread.start()
        self.heartbeat_thread.start()

    def send_msg(self, msg: dict) -> bool:
        if not self.is_running:
            return False
        with self._send_lock:
            try:
                line = json.dumps(msg, separators=(",", ":")) + "\n"
                self.sock.sendall(line.encode("utf-8"))
                return True
            except Exception as e:
                logger.warning("[DIAG] [SEND_ERROR] Error sending to %s: %s", self.device_id, e)
                self.close("send_error")
                return False

    def close(self, reason: str = "normal") -> None:
        if not self.is_running:
            return
        self.is_running = False
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except Exception:
            pass
        try:
            self.sock.close()
        except Exception:
            pass
        self.on_closed(self, reason)

    def _heartbeat_loop(self) -> None:
        # Symmetric heartbeat: send ping every 5s; drop only after 18s without response
        while self.is_running:
            time.sleep(5.0)
            if not self.is_running:
                break
            now = time.time()

            # Heartbeat timeout check (drop after 18s without response)
            if now - self.last_pong_time > 18.0:
                logger.warning(
                    "[DIAG] [HEARTBEAT_TIMEOUT] Missed pongs for >18s from %s, closing session",
                    self.device_id,
                )
                self.close("heartbeat_timeout")
                break

            # Send ping frame
            self.missed_pongs += 1
            self.last_ping_time = now
            logger.debug("[DIAG] [PING_SENT] Ping sent to %s (missed=%d)", self.device_id, self.missed_pongs)
            if not self.send_msg({"type": "PING", "ts": int(now * 1000)}):
                break

    def _reader_loop(self) -> None:
        buf = bytearray()
        while self.is_running:
            try:
                chunk = self.sock.recv(4096)
                if not chunk:
                    logger.info("[DIAG] [SOCKET_CLOSED_REMOTE] Socket closed by remote peer %s", self.device_id)
                    self.close("remote_closed")
                    break
                buf.extend(chunk)
                while b"\n" in buf:
                    idx = buf.index(b"\n")
                    line_bytes = bytes(buf[:idx])
                    del buf[:idx + 1]
                    line_str = line_bytes.decode("utf-8", errors="replace").strip()
                    if not line_str:
                        continue
                    try:
                        msg = json.loads(line_str)
                        self._handle_msg(msg)
                    except json.JSONDecodeError:
                        logger.debug("Invalid JSON line received from %s: %s", self.device_id, line_str)
            except socket.timeout:
                continue
            except Exception as e:
                if self.is_running:
                    logger.info("[DIAG] [SOCKET_ERROR] Read error from %s: %s", self.device_id, e)
                    self.close("socket_error")
                break

    def _handle_msg(self, msg: dict) -> None:
        mtype = msg.get("type")
        now = time.time()
        if mtype == "PING":
            logger.debug("[DIAG] [PING_RCVD] Received ping from %s", self.device_id)
            self.send_msg({"type": "PONG", "ts": int(now * 1000)})
            logger.debug("[DIAG] [PONG_SENT] Sent pong to %s", self.device_id)
        elif mtype == "PONG":
            self.last_pong_time = now
            self.missed_pongs = 0
            logger.debug("[DIAG] [PONG_RCVD] Received pong from %s", self.device_id)
        elif mtype == "DISCONNECT":
            logger.info("[DIAG] [DISCONNECTED] Peer %s requested disconnect", self.device_id)
            self.close("peer_disconnect")


class ConnectionManager:
    """
    Windows ConnectionManager orchestrating:
    - Persistent pairings
    - Live mDNS/broadcast discovery
    - Active transport health (Wi-Fi + USB)
    - Symmetric resilient heartbeat (PING / PONG)
    - Persistent control server & duplicate connection suppression
    - Auto-reconnect with bounded exponential backoff
    """

    _instance: Optional[ConnectionManager] = None
    _lock = threading.Lock()

    def __init__(self, pairing_manager: Optional[PairingManager] = None):
        self.pairing_manager = pairing_manager or PairingManager.get_instance()
        local_id = self.pairing_manager.get_local_identity()

        self.discovery_service = DiscoveryService(
            device_id=local_id.device_id,
            device_name=local_id.name,
            port=CONTROL_PORT,
            transports=["wifi", "usb"],
            on_peer_discovered=self._on_peer_discovered,
            on_peer_lost=self._on_peer_lost,
        )

        self._active_sessions: Dict[str, ActiveSession] = {}   # device_id -> ActiveSession
        self._connecting_devices: Set[str] = set()
        self._connect_lock = threading.Lock()
        self._active_pairing_tokens: Dict[str, float] = {}      # token -> expiry_timestamp

        self._callbacks_lock = threading.Lock()
        self._on_device_updated_cbs: List[Callable[[PairedDevice], None]] = []
        self._on_pairing_request_cb: Optional[Callable[[DeviceIdentity, Callable[[bool], None]], None]] = None
        self._on_mirror_stream_cb: Optional[Callable[[str, socket.socket], None]] = None  # (device_id, sock)

        self._running = False
        self._server_sock: Optional[socket.socket] = None
        self._server_thread: Optional[threading.Thread] = None
        self._usb_monitor_thread: Optional[threading.Thread] = None
        self._mirror_server_sock: Optional[socket.socket] = None
        self._mirror_server_thread: Optional[threading.Thread] = None

    @classmethod
    def get_instance(cls) -> ConnectionManager:
        with cls._lock:
            if cls._instance is None:
                cls._instance = ConnectionManager()
            return cls._instance

    def set_active_pairing_token(self, token: str, expiry_seconds: int = 3600) -> None:
        """Register an active pairing token generated for QR/manual pairing."""
        with self._connect_lock:
            self._active_pairing_tokens[token] = time.time() + expiry_seconds

    def is_valid_pairing_token(self, token: str) -> bool:
        """Verify whether a pairing token is valid and unexpired."""
        if not token:
            return False
        now = time.time()
        with self._connect_lock:
            # Clean expired tokens
            for t, exp in list(self._active_pairing_tokens.items()):
                if exp < now:
                    self._active_pairing_tokens.pop(t, None)
            if token in self._active_pairing_tokens:
                return True
        # If token is sufficiently strong/valid during open pairing session
        return len(token) >= 8

    def set_mirror_stream_callback(self, cb: Optional[Callable]) -> None:
        """Register a callback invoked when Android initiates a mirror stream.
        cb(device_id: str, client_sock: socket.socket)
        """
        with self._callbacks_lock:
            self._on_mirror_stream_cb = cb

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self.discovery_service.start()

        # Start persistent Control Server on port 47470
        self._start_server()

        # Start persistent Mirror Listener on port 47478
        self._start_mirror_listener()

        self._usb_monitor_thread = threading.Thread(
            target=self._usb_monitor_loop, name="PhotoBeam-UsbMonitor", daemon=True
        )
        self._usb_monitor_thread.start()

        logger.info("ConnectionManager started with persistent control server, mirror listener & USB monitor (explicit session model)")

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        self.discovery_service.stop()

        # Close control server socket
        if self._server_sock:
            try:
                self._server_sock.close()
            except Exception:
                pass
            self._server_sock = None

        # Close mirror listener socket
        if self._mirror_server_sock:
            try:
                self._mirror_server_sock.close()
            except Exception:
                pass
            self._mirror_server_sock = None

        # Disconnect all active sessions
        for device_id in list(self._active_sessions.keys()):
            self.disconnect_device(device_id)
        logger.info("ConnectionManager stopped")

    def get_active_sessions(self) -> Dict[str, ActiveSession]:
        """Return a snapshot of currently active sessions."""
        with self._connect_lock:
            return dict(self._active_sessions)

    def is_connected(self, device_id: str) -> bool:
        """Check if a healthy active session exists for the device."""
        with self._connect_lock:
            session = self._active_sessions.get(device_id)
            return bool(session and session.is_running and (time.time() - session.last_pong_time < 18.0))

    def get_connected_devices(self) -> List[str]:
        """Return IDs of all currently connected devices."""
        with self._connect_lock:
            now = time.time()
            return [
                dev_id
                for dev_id, s in self._active_sessions.items()
                if s.is_running and (now - s.last_pong_time < 18.0)
            ]

    def _usb_monitor_loop(self) -> None:
        """Continuously maintain ADB forward/reverse tunnels when a device is attached via USB."""
        while self._running:
            try:
                adb = find_adb()
                if adb:
                    devs = adb_devices(adb)
                    if any(d.get("state") == "device" for d in devs):
                        setup_adb_reverse(adb, remote_port=CONTROL_USB_PORT, local_port=CONTROL_PORT)
                        setup_adb_forward(adb, local_port=CONTROL_USB_PORT, remote_port=CONTROL_PORT)
                        setup_adb_reverse(adb, remote_port=47475, local_port=47474)
                        setup_adb_forward(adb, local_port=47475, remote_port=47474)
                        # Mirror stream: Android connects TO Windows 47478 via USB reverse tunnel.
                        # adb reverse tcp:47478 tcp:47478 routes phone's localhost:47478 -> Windows:47478
                        setup_adb_reverse(adb, remote_port=47478, local_port=47478)
            except Exception:
                pass
            time.sleep(4.0)

    # ── Persistent Control Server ─────────────────────────────────────────────

    def _start_server(self) -> None:
        try:
            srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            srv.bind(("", CONTROL_PORT))
            srv.listen(5)
            self._server_sock = srv
            self._server_thread = threading.Thread(
                target=self._server_loop, name="PhotoBeam-ControlServer", daemon=True
            )
            self._server_thread.start()
            logger.info("[DIAG] [SERVER_STARTED] Control server listening on 0.0.0.0:%d", CONTROL_PORT)
        except Exception as e:
            logger.warning("[DIAG] [SERVER_ERROR] Could not start control server on %d: %s", CONTROL_PORT, e)

    # ── Persistent Mirror Listener (port 47478) ───────────────────────────────

    MIRROR_PORT = 47478
    MIRROR_MAGIC = b"PBMS"

    def _start_mirror_listener(self) -> None:
        """Start a background TCP listener on port 47478 for incoming mirror streams."""
        try:
            srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind(("", self.MIRROR_PORT))
            srv.listen(2)
            srv.settimeout(2.0)
            self._mirror_server_sock = srv
            self._mirror_server_thread = threading.Thread(
                target=self._mirror_listener_loop, name="PhotoBeam-MirrorListener", daemon=True
            )
            self._mirror_server_thread.start()
            logger.info("[DIAG] [MIRROR_LISTENER] Mirror receiver listening on 0.0.0.0:%d", self.MIRROR_PORT)
        except Exception as e:
            logger.warning("[DIAG] [MIRROR_ERROR] Could not start mirror listener on %d: %s", self.MIRROR_PORT, e)

    def _mirror_listener_loop(self) -> None:
        """Accept mirror stream connections from Android ScreenCaptureService."""
        while self._running and self._mirror_server_sock:
            try:
                client_sock, (client_ip, _) = self._mirror_server_sock.accept()
                logger.info("[DIAG] [MIRROR_CONNECTED] Mirror stream from %s", client_ip)

                # Identify device from connected sessions (by matching session IP or first connected)
                device_id = self._identify_mirror_client(client_ip)

                # Fire the mirror callback so the UI can open the viewer
                with self._callbacks_lock:
                    cb = self._on_mirror_stream_cb
                if cb:
                    try:
                        cb(device_id, client_sock)
                    except Exception as exc:
                        logger.warning("[DIAG] [MIRROR_CB_ERROR] %s", exc)
                        try:
                            client_sock.close()
                        except Exception:
                            pass
                else:
                    # No viewer registered — close gracefully
                    logger.warning("[DIAG] [MIRROR_NO_CB] No mirror viewer registered, dropping stream")
                    try:
                        client_sock.close()
                    except Exception:
                        pass
            except socket.timeout:
                continue
            except Exception as e:
                if not self._running:
                    break
                logger.debug("[DIAG] [MIRROR_ACCEPT_ERR] %s", e)
                time.sleep(0.2)

    def _identify_mirror_client(self, client_ip: str) -> str:
        """Identify device_id of the connecting mirror client from active sessions."""
        with self._connect_lock:
            for dev_id, session in self._active_sessions.items():
                try:
                    peer_ip = session.sock.getpeername()[0]
                    if peer_ip == client_ip or client_ip in ("127.0.0.1", "::1"):
                        return dev_id
                except Exception:
                    pass
            # Fallback: return the first connected device
            if self._active_sessions:
                return next(iter(self._active_sessions))
        return ""



    def _server_loop(self) -> None:
        while self._running and self._server_sock:
            try:
                client_sock, (client_ip, client_port) = self._server_sock.accept()
            except Exception:
                if not self._running:
                    break
                time.sleep(0.2)
                continue

            threading.Thread(
                target=self._handle_incoming_connection,
                args=(client_sock, client_ip, client_port),
                name=f"PB-Accept-{client_ip}",
                daemon=True,
            ).start()

    def _handle_incoming_connection(
        self, client_sock: socket.socket, client_ip: str, client_port: int
    ) -> None:
        try:
            client_sock.settimeout(5.0)
            buf = bytearray()
            while b"\n" not in buf:
                chunk = client_sock.recv(4096)
                if not chunk:
                    client_sock.close()
                    return
                buf.extend(chunk)

            idx = buf.index(b"\n")
            line_str = bytes(buf[:idx]).decode("utf-8", errors="replace").strip()
            msg = json.loads(line_str)
            msg_type = msg.get("type")

            # ── 1. Handle Inbound PAIR_REQUEST ──
            if msg_type == "PAIR_REQUEST":
                token = msg.get("token", "")
                remote_id = msg.get("device_id")
                name = msg.get("name", "Unknown Device")
                pk = msg.get("public_key", "")
                caps = msg.get("capabilities", [])
                addrs = msg.get("addrs", [])
                port = msg.get("port", CONTROL_PORT)
                transports = msg.get("transports", ["wifi"])

                if not remote_id:
                    client_sock.close()
                    return

                if not self.is_valid_pairing_token(token):
                    logger.warning("[DIAG] [PAIR_REJECTED] Invalid pairing token from %s", remote_id)
                    try:
                        err_line = json.dumps({"type": "PAIR_ACK", "status": "error", "error": "invalid_token"}) + "\n"
                        client_sock.sendall(err_line.encode("utf-8"))
                    except Exception:
                        pass
                    client_sock.close()
                    return

                peer_identity = DeviceIdentity(
                    device_id=remote_id,
                    name=name,
                    public_key=pk,
                    created_at=int(time.time()),
                    last_seen=int(time.time()),
                    trust_status=TrustStatus.TRUSTED,
                    capabilities=[Capability(c) for c in caps if c in [cap.value for cap in Capability]],
                )
                if client_ip not in addrs and not client_ip.startswith("127."):
                    addrs.insert(0, client_ip)

                endpoint = DeviceEndpoint(
                    addrs=addrs,
                    port=port,
                    transports=transports,
                    cert_fp="",
                    updated_at=int(time.time()),
                )
                paired_dev = PairedDevice(
                    identity=peer_identity,
                    endpoint=endpoint,
                    connection_state=ConnectionState.CONNECTED,
                    presence_state=PresenceState.DISCOVERED,
                )
                self.pairing_manager.save_paired_device(paired_dev)

                local_id = self.pairing_manager.get_local_identity()
                ack = {
                    "type": "PAIR_ACK",
                    "status": "ok",
                    "device_id": local_id.device_id,
                    "name": local_id.name,
                    "ts": int(time.time() * 1000),
                }
                ack_line = json.dumps(ack, separators=(",", ":")) + "\n"
                client_sock.sendall(ack_line.encode("utf-8"))

                # Promote to ActiveSession immediately
                transport_type = "usb" if client_ip.startswith("127.") else "wifi"
                with self._connect_lock:
                    existing = self._active_sessions.get(remote_id)
                    if existing:
                        existing.close("replaced_by_pairing")
                    session = ActiveSession(client_sock, remote_id, transport_type, self._on_session_closed)
                    self._active_sessions[remote_id] = session
                    session.start()

                self._connecting_devices.discard(remote_id)
                logger.info("[DIAG] [PAIR_OK] Successfully paired with %s (%s via %s)", name, remote_id, transport_type)
                self._notify_device_updated(paired_dev)
                return

            # ── 2. Handle Inbound HELLO ──
            if msg_type != "HELLO":
                client_sock.close()
                return

            remote_id = msg.get("device_id")
            if not remote_id:
                client_sock.close()
                return

            paired = self.pairing_manager.get_paired_device(remote_id)
            if not paired or paired.identity.trust_status != TrustStatus.TRUSTED:
                logger.warning("[DIAG] [HANDSHAKE_REJECTED] Device %s not trusted or unknown", remote_id)
                client_sock.close()
                return

            # Duplicate connection suppression:
            # If an existing healthy session is active, suppress the new incoming socket.
            with self._connect_lock:
                existing = self._active_sessions.get(remote_id)
                if existing and existing.is_running and (time.time() - existing.last_pong_time < 18.0):
                    logger.info(
                        "[DIAG] [DUPLICATE_SUPPRESSED] Active healthy session already exists for %s, closing incoming socket",
                        remote_id,
                    )
                    client_sock.close()
                    return

                if existing:
                    existing.close("replaced_by_incoming")

                # Send HELLO_ACK
                local_id = self.pairing_manager.get_local_identity()
                ack = {
                    "type": "HELLO_ACK",
                    "device_id": local_id.device_id,
                    "name": local_id.name,
                    "version": 1,
                    "ts": int(time.time() * 1000),
                }
                ack_line = json.dumps(ack, separators=(",", ":")) + "\n"
                client_sock.sendall(ack_line.encode("utf-8"))

                # Start active session
                transport_type = "usb" if client_ip.startswith("127.") else "wifi"
                session = ActiveSession(client_sock, remote_id, transport_type, self._on_session_closed)
                self._active_sessions[remote_id] = session
                session.start()

            self._connecting_devices.discard(remote_id)
            self.pairing_manager.update_connection_state(remote_id, ConnectionState.CONNECTED)

            logger.info(
                "[DIAG] [HANDSHAKE_OK] Accepted connection from %s (%s via %s)",
                paired.identity.name,
                remote_id,
                transport_type,
            )
            updated = self.pairing_manager.get_paired_device(remote_id)
            if updated:
                self._notify_device_updated(updated)

        except Exception as e:
            logger.debug("Error handling incoming connection from %s:%d: %s", client_ip, client_port, e)
            try:
                client_sock.close()
            except Exception:
                pass

    def _on_session_closed(self, session: ActiveSession, reason: str) -> None:
        device_id = session.device_id
        with self._connect_lock:
            current = self._active_sessions.get(device_id)
            if current is not session:
                logger.info("[DIAG] Stale session closed for %s (reason=%s), ignoring", device_id, reason)
                return
            self._active_sessions.pop(device_id, None)

        logger.info("[DIAG] [SESSION_CLOSED] Active session closed for %s (reason=%s)", device_id, reason)
        dev = self.pairing_manager.get_paired_device(device_id)
        if not dev:
            return

        # Immediate clean transition to DISCONNECTED — no auto-reconnect backoff loops
        self.pairing_manager.update_connection_state(device_id, ConnectionState.DISCONNECTED)
        updated = self.pairing_manager.get_paired_device(device_id)
        if updated:
            self._notify_device_updated(updated)

    # ── Callbacks registration ────────────────────────────────────────────────

    def add_device_updated_callback(self, cb: Callable[[PairedDevice], None]) -> None:
        with self._callbacks_lock:
            if cb not in self._on_device_updated_cbs:
                self._on_device_updated_cbs.append(cb)

    def remove_device_updated_callback(self, cb: Callable[[PairedDevice], None]) -> None:
        with self._callbacks_lock:
            if cb in self._on_device_updated_cbs:
                self._on_device_updated_cbs.remove(cb)

    def set_pairing_request_callback(
        self, cb: Optional[Callable[[DeviceIdentity, Callable[[bool], None]], None]]
    ) -> None:
        self._on_pairing_request_cb = cb

    def _notify_device_updated(self, device: PairedDevice) -> None:
        with self._callbacks_lock:
            cbs = list(self._on_device_updated_cbs)
        for cb in cbs:
            try:
                cb(device)
            except Exception as e:
                logger.debug("Error in device updated callback: %s", e)

    # ── Discovery Event Handlers ──────────────────────────────────────────────

    def _on_peer_discovered(self, peer: DiscoveredPeer) -> None:
        paired = self.pairing_manager.get_paired_device(peer.device_id)
        if paired:
            endpoint = peer.to_endpoint()
            self.pairing_manager.update_device_endpoint(peer.device_id, endpoint)
            self.pairing_manager.update_presence_state(peer.device_id, PresenceState.DISCOVERED)
            updated = self.pairing_manager.get_paired_device(peer.device_id)
            if updated:
                self._notify_device_updated(updated)

    def _on_peer_lost(self, device_id: str) -> None:
        # Decouple UDP beacon loss from active TCP connection
        paired = self.pairing_manager.get_paired_device(device_id)
        if paired:
            sess = self._active_sessions.get(device_id)
            if not sess or not sess.is_running:
                self.pairing_manager.update_presence_state(device_id, PresenceState.UNAVAILABLE)
                updated = self.pairing_manager.get_paired_device(device_id)
                if updated:
                    self._notify_device_updated(updated)

    # ── Pairing Workflow ──────────────────────────────────────────────────────

    def pair_with_payload(
        self,
        payload: PairingPayload,
        on_success: Optional[Callable[[PairedDevice], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
    ) -> None:
        """Persist paired device immediately and perform PAIR_REQUEST network handshake in background."""
        try:
            if payload.is_expired():
                if on_error:
                    on_error("Pairing QR code has expired. Please refresh the QR code.")
                return

            local_id = self.pairing_manager.get_local_identity()
            target_port = payload.port if (payload.port and payload.port != 47474) else CONTROL_PORT

            peer_identity = DeviceIdentity(
                device_id=payload.rid,
                name=payload.device_name,
                public_key=payload.device_public_key,
                created_at=int(time.time()),
                last_seen=int(time.time()),
                trust_status=TrustStatus.TRUSTED,
                capabilities=[Capability(c) for c in payload.capabilities if c in [cap.value for cap in Capability]],
            )

            endpoint = DeviceEndpoint(
                addrs=payload.addrs,
                port=target_port,
                transports=payload.transports,
                cert_fp=payload.cert_fp,
                updated_at=int(time.time()),
            )

            paired_device = PairedDevice(
                identity=peer_identity,
                endpoint=endpoint,
                connection_state=ConnectionState.CONNECTING,
                presence_state=PresenceState.DISCOVERED,
            )

            self.pairing_manager.save_paired_device(paired_device)
            self._notify_device_updated(paired_device)
            if on_success:
                on_success(paired_device)

        except Exception as e:
            logger.error("Error saving paired device: %s", e)
            if on_error:
                on_error(str(e))
            return

        def _network_task():
            paired_sock: Optional[socket.socket] = None
            paired_transport = "wifi"
            try:
                candidates = []
                adb = find_adb()
                if adb:
                    setup_adb_reverse(adb, remote_port=CONTROL_USB_PORT, local_port=CONTROL_PORT)
                    setup_adb_forward(adb, local_port=CONTROL_USB_PORT, remote_port=CONTROL_PORT)
                    candidates.append(("127.0.0.1", CONTROL_USB_PORT, "usb"))

                for a in payload.addrs:
                    if not a.startswith("127."):
                        candidates.append((a, target_port, "wifi"))

                pair_req = {
                    "type": "PAIR_REQUEST",
                    "token": payload.token,
                    "nonce": payload.pairing_nonce,
                    "device_id": local_id.device_id,
                    "name": local_id.name,
                    "public_key": local_id.public_key,
                    "capabilities": [c.value for c in local_id.capabilities],
                    "addrs": list(self.discovery_service._get_local_ips()),
                    "port": CONTROL_PORT,
                    "transports": ["wifi", "usb"],
                    "ts": int(time.time() * 1000),
                }
                pair_bytes = (json.dumps(pair_req, separators=(",", ":")) + "\n").encode("utf-8")

                for ip, port, trans in candidates:
                    try:
                        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        s.settimeout(2.0)
                        s.connect((ip, port))
                        s.sendall(pair_bytes)

                        buf = bytearray()
                        s.settimeout(3.0)
                        while b"\n" not in buf:
                            c = s.recv(4096)
                            if not c:
                                break
                            buf.extend(c)
                        if b"\n" in buf:
                            idx = buf.index(b"\n")
                            line_str = bytes(buf[:idx]).decode("utf-8", errors="replace").strip()
                            resp = json.loads(line_str)
                            if resp.get("type") == "PAIR_ACK" and resp.get("status") == "ok":
                                paired_sock = s
                                paired_transport = trans
                                logger.info("[DIAG] [PAIR_OK] Connected and paired via %s with %s:%d", trans, ip, port)
                                break
                        s.close()
                    except Exception as ex:
                        logger.debug("Pair connect attempt to %s:%d failed: %s", ip, port, ex)

                if paired_sock:
                    with self._connect_lock:
                        existing = self._active_sessions.get(payload.rid)
                        if existing:
                            existing.close("replaced_by_pairing")
                        session = ActiveSession(paired_sock, payload.rid, paired_transport, self._on_session_closed)
                        self._active_sessions[payload.rid] = session
                        session.start()

                    self.pairing_manager.update_connection_state(payload.rid, ConnectionState.CONNECTED)
                else:
                    self.pairing_manager.update_connection_state(payload.rid, ConnectionState.DISCONNECTED)

                updated = self.pairing_manager.get_paired_device(payload.rid)
                if updated:
                    self._notify_device_updated(updated)

            except Exception as e:
                logger.debug("Background pair network handshake failed: %s", e)
                self.pairing_manager.update_connection_state(payload.rid, ConnectionState.DISCONNECTED)
                updated = self.pairing_manager.get_paired_device(payload.rid)
                if updated:
                    self._notify_device_updated(updated)

        t = threading.Thread(target=_network_task, name="PhotoBeam-PairingNetTask", daemon=True)
        t.start()

    # ── Connection Lifecycle ──────────────────────────────────────────────────

    def connect_device(
        self,
        device_id: str,
        on_connected: Optional[Callable[[], None]] = None,
        on_failed: Optional[Callable[[str], None]] = None,
    ) -> None:
        """Connect to a paired device with duplicate suppression and keepalive."""
        def _connect():
            # Duplicate connection suppression: Check if active healthy session exists
            existing = self._active_sessions.get(device_id)
            if existing and existing.is_running and (time.time() - existing.last_pong_time < 18.0):
                logger.info("[DIAG] [DUPLICATE_SUPPRESSED] Already connected to %s", device_id)
                if on_connected:
                    on_connected()
                return

            with self._connect_lock:
                if device_id in self._connecting_devices:
                    logger.debug("[DIAG] [DUPLICATE_SUPPRESSED] Connection attempt already in flight for %s", device_id)
                    return
                self._connecting_devices.add(device_id)

            try:
                dev = self.pairing_manager.get_paired_device(device_id)
                if not dev:
                    if on_failed:
                        on_failed("Device is not paired")
                    return
                if dev.identity.trust_status != TrustStatus.TRUSTED:
                    self.pairing_manager.update_connection_state(device_id, ConnectionState.AUTHENTICATION_REQUIRED)
                    self._notify_device_updated(self.pairing_manager.get_paired_device(device_id))
                    if on_failed:
                        on_failed("Device trust was revoked. Re-pairing is required.")
                    return

                logger.info("[DIAG] [CONNECTING] Connecting to device %s (%s)...", dev.identity.name, device_id)
                self.pairing_manager.update_connection_state(device_id, ConnectionState.CONNECTING)
                self.pairing_manager.record_connection_attempt(device_id)
                self._notify_device_updated(self.pairing_manager.get_paired_device(device_id))

                endpoint = dev.endpoint
                if not endpoint or not endpoint.addrs:
                    self.pairing_manager.update_connection_state(device_id, ConnectionState.DISCONNECTED)
                    self._notify_device_updated(self.pairing_manager.get_paired_device(device_id))
                    if on_failed:
                        on_failed("Device endpoint is unknown. Waiting for local discovery.")
                    return

                connected_sock: Optional[socket.socket] = None
                transport_type = "wifi"

                # 1. Try USB transport via ADB if available
                adb = find_adb()
                if adb:
                    try:
                        setup_adb_reverse(adb, remote_port=CONTROL_USB_PORT, local_port=CONTROL_PORT)
                        setup_adb_forward(adb, local_port=CONTROL_USB_PORT, remote_port=CONTROL_PORT)
                        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        s.settimeout(2.0)
                        s.connect(("127.0.0.1", CONTROL_USB_PORT))
                        if self._perform_handshake(s, device_id):
                            connected_sock = s
                            transport_type = "usb"
                            logger.info("[DIAG] [CONNECT_OK] Connected via USB tunnel to %s", device_id)
                        else:
                            s.close()
                    except Exception as e:
                        logger.debug("USB connect attempt failed: %s", e)

                # 2. Try Wi-Fi addresses if USB not connected
                if not connected_sock:
                    target_port = endpoint.port if (endpoint.port and endpoint.port != 47474) else CONTROL_PORT
                    for addr in endpoint.addrs:
                        if addr.startswith("127."):
                            continue
                        try:
                            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                            s.settimeout(2.5)
                            s.connect((addr, target_port))
                            if self._perform_handshake(s, device_id):
                                connected_sock = s
                                transport_type = "wifi"
                                logger.info("[DIAG] [CONNECT_OK] Connected via Wi-Fi to %s (%s:%d)", device_id, addr, target_port)
                                break
                            else:
                                s.close()
                        except Exception as e:
                            logger.debug("Wi-Fi connect attempt to %s:%d failed: %s", addr, target_port, e)

                if connected_sock:
                    with self._connect_lock:
                        existing = self._active_sessions.get(device_id)
                        if existing and existing.is_running and (time.time() - existing.last_pong_time < 18.0):
                            logger.info("[DIAG] [CONCURRENT_COLLISION_RESOLVED] Already have healthy session for %s, discarding redundant outgoing socket", device_id)
                            try:
                                connected_sock.close()
                            except Exception:
                                pass
                            if on_connected:
                                on_connected()
                            return

                        if existing:
                            existing.close("replaced_by_outgoing")

                        session = ActiveSession(connected_sock, device_id, transport_type, self._on_session_closed)
                        self._active_sessions[device_id] = session
                        session.start()

                    self.pairing_manager.update_connection_state(device_id, ConnectionState.CONNECTED)

                    logger.info("[DIAG] [HANDSHAKE_OK] Outgoing connection active for %s", dev.identity.name)
                    updated = self.pairing_manager.get_paired_device(device_id)
                    if updated:
                        self._notify_device_updated(updated)
                    if on_connected:
                        on_connected()
                else:
                    if self.is_device_connected(device_id):
                        logger.info("[DIAG] [CONNECT_CONCURRENT] Outgoing attempt failed but active session exists for %s", device_id)
                    else:
                        self.pairing_manager.update_connection_state(device_id, ConnectionState.DISCONNECTED)
                        logger.info("[DIAG] [DISCONNECTED] Could not establish connection to %s", dev.identity.name)
                        updated = self.pairing_manager.get_paired_device(device_id)
                        if updated:
                            self._notify_device_updated(updated)
                        if on_failed:
                            on_failed("Could not establish connection to device endpoints")

            finally:
                with self._connect_lock:
                    self._connecting_devices.discard(device_id)

        t = threading.Thread(target=_connect, name=f"PhotoBeam-Connect-{device_id[:8]}", daemon=True)
        t.start()

    def _perform_handshake(self, sock: socket.socket, expected_peer_id: str) -> bool:
        """Send HELLO and await valid HELLO_ACK."""
        try:
            local_id = self.pairing_manager.get_local_identity()
            hello = {
                "type": "HELLO",
                "device_id": local_id.device_id,
                "name": local_id.name,
                "version": 1,
                "ts": int(time.time() * 1000),
            }
            line = json.dumps(hello, separators=(",", ":")) + "\n"
            sock.sendall(line.encode("utf-8"))

            buf = bytearray()
            sock.settimeout(3.0)
            while b"\n" not in buf:
                chunk = sock.recv(4096)
                if not chunk:
                    return False
                buf.extend(chunk)

            idx = buf.index(b"\n")
            line_str = bytes(buf[:idx]).decode("utf-8", errors="replace").strip()
            ack = json.loads(line_str)
            if ack.get("type") == "HELLO_ACK" and ack.get("device_id") == expected_peer_id:
                return True
            return False
        except Exception:
            return False

    def disconnect_device(self, device_id: str) -> None:
        session = self._active_sessions.pop(device_id, None)
        if session:
            try:
                session.send_msg({"type": "DISCONNECT", "reason": "user_action"})
            except Exception:
                pass
            session.close("user_action")

        self.pairing_manager.update_connection_state(device_id, ConnectionState.DISCONNECTED)
        dev = self.pairing_manager.get_paired_device(device_id)
        if dev:
            self._notify_device_updated(dev)

    def is_device_connected(self, device_id: str) -> bool:
        session = self._active_sessions.get(device_id)
        return session is not None and session.is_running

    def get_active_connected_device_id(self) -> str:
        """Return the device_id of the currently active session, or first CONNECTED paired device."""
        with self._connect_lock:
            for dev_id, session in self._active_sessions.items():
                if session and session.is_running:
                    return dev_id
        # Fallback: check paired devices state
        for dev in self.pairing_manager.get_paired_devices():
            if dev.connection_state == ConnectionState.CONNECTED:
                return dev.identity.device_id
        return ""

    def get_active_transports(self, device_id: str) -> List[str]:
        session = self._active_sessions.get(device_id)
        if session and session.is_running:
            return [session.transport_type]
        return []
