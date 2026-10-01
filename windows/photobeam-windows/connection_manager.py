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
            self.sock.settimeout(1.0)
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
        # Ping interval 5s, timeout after 18s of missed pongs (~3 missed pongs)
        while self.is_running:
            time.sleep(5.0)
            if not self.is_running:
                break
            now = time.time()

            # Heartbeat timeout check
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
        self._backoff_timers: Dict[str, float] = {}             # device_id -> next_retry_time
        self._backoff_intervals: Dict[str, float] = {}          # device_id -> current_interval
        self._connecting_devices: Set[str] = set()
        self._connect_lock = threading.Lock()

        self._callbacks_lock = threading.Lock()
        self._on_device_updated_cbs: List[Callable[[PairedDevice], None]] = []
        self._on_pairing_request_cb: Optional[Callable[[DeviceIdentity, Callable[[bool], None]], None]] = None

        self._running = False
        self._server_sock: Optional[socket.socket] = None
        self._server_thread: Optional[threading.Thread] = None
        self._reconnect_thread: Optional[threading.Thread] = None

    @classmethod
    def get_instance(cls) -> ConnectionManager:
        with cls._lock:
            if cls._instance is None:
                cls._instance = ConnectionManager()
            return cls._instance

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self.discovery_service.start()

        # Start persistent Control Server on port 47474
        self._start_server()

        self._reconnect_thread = threading.Thread(
            target=self._auto_reconnect_loop, name="PhotoBeam-AutoReconnect", daemon=True
        )
        self._reconnect_thread.start()
        logger.info("ConnectionManager started with persistent control server & auto-reconnect")

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        self.discovery_service.stop()

        # Close server socket
        if self._server_sock:
            try:
                self._server_sock.close()
            except Exception:
                pass
            self._server_sock = None

        # Disconnect all active sessions
        for device_id in list(self._active_sessions.keys()):
            self.disconnect_device(device_id)
        logger.info("ConnectionManager stopped")

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

            if msg.get("type") != "HELLO":
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
            existing = self._active_sessions.get(remote_id)
            if existing and existing.is_running and (time.time() - existing.last_pong_time < 15.0):
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

            self.pairing_manager.update_connection_state(remote_id, ConnectionState.CONNECTED)
            self._backoff_intervals[remote_id] = 1.0
            self._backoff_timers[remote_id] = 0.0

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

        logger.info("[DIAG] [DISCONNECTED] Active session closed for %s (reason=%s)", device_id, reason)
        dev = self.pairing_manager.get_paired_device(device_id)
        if dev and dev.connection_state == ConnectionState.CONNECTED:
            self.pairing_manager.update_connection_state(device_id, ConnectionState.DISCONNECTED)
            interval = self._backoff_intervals.get(device_id, 1.0)
            self._backoff_intervals[device_id] = min(interval * 2.0, 30.0)
            self._backoff_timers[device_id] = time.time() + interval
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
        def _task():
            try:
                if payload.is_expired():
                    if on_error:
                        on_error("Pairing QR code has expired. Please refresh the QR code.")
                    return

                addrs = payload.addrs
                if not addrs:
                    if on_error:
                        on_error("No valid IP addresses in pairing payload")
                    return

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
                    port=payload.port,
                    transports=payload.transports,
                    cert_fp=payload.cert_fp,
                    updated_at=int(time.time()),
                )

                paired_device = PairedDevice(
                    identity=peer_identity,
                    endpoint=endpoint,
                    connection_state=ConnectionState.DISCONNECTED,
                    presence_state=PresenceState.DISCOVERED,
                )

                self.pairing_manager.save_paired_device(paired_device)
                self._notify_device_updated(paired_device)

                if on_success:
                    on_success(paired_device)
            except Exception as e:
                logger.error("Error pairing device: %s", e)
                if on_error:
                    on_error(str(e))

        t = threading.Thread(target=_task, name="PhotoBeam-PairingTask", daemon=True)
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
            if existing and existing.is_running and (time.time() - existing.last_pong_time < 15.0):
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
                    # Close previous session if any
                    prev = self._active_sessions.pop(device_id, None)
                    if prev:
                        prev.close("replaced_by_outgoing")

                    session = ActiveSession(connected_sock, device_id, transport_type, self._on_session_closed)
                    self._active_sessions[device_id] = session
                    session.start()

                    self.pairing_manager.update_connection_state(device_id, ConnectionState.CONNECTED)
                    self._backoff_intervals[device_id] = 1.0
                    self._backoff_timers[device_id] = 0.0

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
                        interval = self._backoff_intervals.get(device_id, 1.0)
                        self._backoff_intervals[device_id] = min(interval * 2.0, 30.0)
                        self._backoff_timers[device_id] = time.time() + interval

                        logger.info("[DIAG] [DISCONNECTED] Could not establish connection to %s, retry in %.1fs", dev.identity.name, interval)
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

    def get_active_transports(self, device_id: str) -> List[str]:
        session = self._active_sessions.get(device_id)
        if session and session.is_running:
            return [session.transport_type]
        return []

    # ── Auto-Reconnect Loop ───────────────────────────────────────────────────

    def _auto_reconnect_loop(self) -> None:
        while self._running:
            time.sleep(2.0)
            now = time.time()
            paired_devices = self.pairing_manager.get_paired_devices()

            for dev in paired_devices:
                dev_id = dev.identity.device_id
                if dev.identity.trust_status != TrustStatus.TRUSTED:
                    continue

                session = self._active_sessions.get(dev_id)
                if session and session.is_running:
                    # Active session is healthy; ensure connection_state reflects CONNECTED
                    if dev.connection_state != ConnectionState.CONNECTED:
                        self.pairing_manager.update_connection_state(dev_id, ConnectionState.CONNECTED)
                        updated = self.pairing_manager.get_paired_device(dev_id)
                        if updated:
                            self._notify_device_updated(updated)
                    continue

                if dev.connection_state == ConnectionState.CONNECTED:
                    # Health check on active session
                    if not session or not session.is_running:
                        logger.info("Connection lost for %s, marking disconnected", dev.identity.name)
                        self.disconnect_device(dev_id)
                    continue

                if dev.presence_state == PresenceState.DISCOVERED and dev.connection_state == ConnectionState.DISCONNECTED:
                    next_retry = self._backoff_timers.get(dev_id, 0.0)
                    if now >= next_retry and dev_id not in self._connecting_devices:
                        self.connect_device(dev_id)
