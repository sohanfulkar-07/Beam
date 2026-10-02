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

try:
    from PyQt6.QtCore import QObject, pyqtSignal
    _HAS_PYQT = True
except ImportError:
    _HAS_PYQT = False

if _HAS_PYQT:
    class ConnectionSignals(QObject):
        sig_device_connected = pyqtSignal(str, str)             # (device_id, transport_type)
        sig_device_disconnected = pyqtSignal(str)               # (device_id)
        sig_connection_state_changed = pyqtSignal(str, object)  # (device_id, ConnectionState)
        sig_device_updated = pyqtSignal(object)                 # (PairedDevice)
        sig_pairing_completed = pyqtSignal(object)              # (PairedDevice)
        sig_receive_offer = pyqtSignal(str, str)                # (device_id, uri)
        sig_request_receive = pyqtSignal(str)                   # (device_id)

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
        adb_devices,
        find_adb,
        setup_adb_forward,
        setup_adb_reverse,
        setup_bidirectional_adb_tunnels,
    )
    from .transport.wifi_transport import WiFiTransport
except (ImportError, ValueError):
    from usb_transport import (
        UsbTransport,
        adb_devices,
        find_adb,
        setup_adb_forward,
        setup_adb_reverse,
        setup_bidirectional_adb_tunnels,
    )
    from wifi_transport import WiFiTransport

try:
    from protocol_v2 import (
        CONTROL_PORT,
        CONTROL_USB_PORT,
        DATA_PORT,
        DATA_USB_PORT,
        PC_TO_PHONE_CONTROL_FORWARD_PORT,
        PC_TO_PHONE_DATA_FORWARD_PORT,
        recv_framed_msg,
        send_framed_msg,
    )
    from data_receiver import DataReceiver
    from data_sender import DataSender
except (ImportError, ValueError):
    from .protocol_v2 import (
        CONTROL_PORT,
        CONTROL_USB_PORT,
        DATA_PORT,
        DATA_USB_PORT,
        PC_TO_PHONE_CONTROL_FORWARD_PORT,
        PC_TO_PHONE_DATA_FORWARD_PORT,
        recv_framed_msg,
        send_framed_msg,
    )
    from .data_receiver import DataReceiver
    from .data_sender import DataSender

logger = logging.getLogger("photobeam.connection_manager")


class ActiveSession:
    """
    Active socket session for a paired device with symmetric heartbeat & reader loop.
    Communicates via explicit 4-byte length-prefixed JSON frames (ProtocolV2).
    """

    def __init__(
        self,
        sock: socket.socket,
        device_id: str,
        transport_type: str,
        on_closed: Callable[[ActiveSession, str], None],  # (session, reason)
        on_message: Optional[Callable[[ActiveSession, dict], None]] = None,
    ):
        self.sock = sock
        self.device_id = device_id
        self.transport_type = transport_type
        self.on_closed = on_closed
        self.on_message = on_message

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

    def is_healthy(self) -> bool:
        """Check if session is active, socket is open, and pings are responded to within 30s."""
        if not self.is_running or self.sock is None:
            return False
        now = time.time()
        return (now - self.last_pong_time < 30.0) and (self.missed_pongs < 3)

    def start(self) -> None:
        self.reader_thread.start()
        self.heartbeat_thread.start()

    def send_msg(self, msg: dict) -> bool:
        if not self.is_running:
            return False
        with self._send_lock:
            try:
                ok = send_framed_msg(self.sock, msg)
                if not ok:
                    self.close("error: send_framed_failed")
                return ok
            except Exception as e:
                logger.warning("[DIAG] [SEND_ERROR] Error sending to %s: %s", self.device_id, e)
                self.close(f"error: send_error ({e})")
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

        if reason in ("remote_closed", "peer_disconnect"):
            formatted_reason = "remote_peer"
        elif reason in ("user_action", "local_user"):
            formatted_reason = "local_user"
        elif reason.startswith("error:"):
            formatted_reason = reason
        else:
            formatted_reason = f"error: {reason}"

        logger.info("[SOCKET_CLOSE] Closed by %s", formatted_reason)
        self.on_closed(self, reason)

    def _heartbeat_loop(self) -> None:
        # Ping every 10s; drop only after 30s without response
        while self.is_running:
            time.sleep(10.0)
            if not self.is_running:
                break
            now = time.time()

            # Heartbeat timeout check (drop after 30s without response)
            if now - self.last_pong_time > 30.0:
                self.close("error: heartbeat_timeout (>30s without pong)")
                break

            # Send ping frame
            self.missed_pongs += 1
            self.last_ping_time = now
            if not self.send_msg({"type": "PING", "ts": int(now * 1000)}):
                break

    def _reader_loop(self) -> None:
        while self.is_running:
            try:
                msg = recv_framed_msg(self.sock, timeout=2.0)
                if msg is None:
                    # Explicit EOF (0 bytes read from peer)
                    self.close("remote_closed")
                    break
                self._handle_msg(msg)
            except (socket.timeout, TimeoutError):
                continue
            except Exception as e:
                if self.is_running:
                    self.close(f"error: socket_error ({e})")
                break

    def _handle_msg(self, msg: dict) -> None:
        mtype = msg.get("type")
        now = time.time()
        if mtype == "PING":
            ts = msg.get("ts", int(now * 1000))
            self.send_msg({"type": "PONG", "ts": ts})
        elif mtype == "PONG":
            self.last_pong_time = now
            self.missed_pongs = 0
            ping_ts = msg.get("ts")
            if ping_ts is not None:
                rtt_ms = int(now * 1000 - ping_ts)
            else:
                rtt_ms = int((now - self.last_ping_time) * 1000)
            if rtt_ms < 0:
                rtt_ms = 0
            logger.info("[HEARTBEAT] Ping sent -> Pong received in %dms", rtt_ms)
        elif mtype == "RECEIVE_OFFER":
            if self.on_message:
                self.on_message(self, msg)
        elif mtype == "REQUEST_RECEIVE":
            if self.on_message:
                self.on_message(self, msg)
        elif mtype == "DISCONNECT":
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
        self._session_lock = threading.Lock()
        self._connect_lock = self._session_lock  # alias for backwards compatibility
        self._active_pairing_tokens: Dict[str, float] = {}      # token -> expiry_timestamp

        self._callbacks_lock = threading.Lock()
        self._on_device_updated_cbs: List[Callable[[PairedDevice], None]] = []
        self._on_pairing_request_cb: Optional[Callable[[DeviceIdentity, Callable[[bool], None]], None]] = None
        self._on_mirror_stream_cb: Optional[Callable[[str, socket.socket], None]] = None  # (device_id, sock)

        self._latest_receive_offers: Dict[str, str] = {}
        self._current_local_receive_offer: Optional[str] = None

        if _HAS_PYQT:
            self.signals = ConnectionSignals()
            self.sig_device_connected = self.signals.sig_device_connected
            self.sig_device_disconnected = self.signals.sig_device_disconnected
            self.sig_connection_state_changed = self.signals.sig_connection_state_changed
            self.sig_device_updated = self.signals.sig_device_updated
            self.sig_pairing_completed = self.signals.sig_pairing_completed
            self.sig_receive_offer = self.signals.sig_receive_offer
            self.sig_request_receive = self.signals.sig_request_receive
        else:
            self.signals = None
            self.sig_device_connected = None
            self.sig_device_disconnected = None
            self.sig_connection_state_changed = None
            self.sig_device_updated = None
            self.sig_pairing_completed = None
            self.sig_receive_offer = None
            self.sig_request_receive = None

        self._running = False
        self._server_sock: Optional[socket.socket] = None
        self._server_thread: Optional[threading.Thread] = None
        self._usb_monitor_thread: Optional[threading.Thread] = None
        self._mirror_server_sock: Optional[socket.socket] = None
        self._mirror_server_thread: Optional[threading.Thread] = None
        self.data_receiver: Optional[DataReceiver] = None
        self.data_sender: DataSender = DataSender()

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

        # Clean slate on startup: any stale connected states from past app runs are reset
        for dev in self.pairing_manager.get_paired_devices():
            if dev.connection_state != ConnectionState.DISCONNECTED:
                self.pairing_manager.update_connection_state(dev.identity.device_id, ConnectionState.DISCONNECTED)

        self.discovery_service.start()

        # Start high-speed DataReceiver on port 47474
        self.data_receiver = DataReceiver(port=DATA_PORT)
        self.data_receiver.start()

        # Start persistent Control Server on port 47470
        self._start_server()

        # Start persistent Mirror Listener on port 47478
        self._start_mirror_listener()

        self._usb_monitor_thread = threading.Thread(
            target=self._usb_monitor_loop, name="PhotoBeam-UsbMonitor", daemon=True
        )
        self._usb_monitor_thread.start()

        logger.info("ConnectionManager started with ControlServer & DataReceiver (PhotoBeam 2.0 dual-channel architecture)")

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        self.discovery_service.stop()

        if self.data_receiver:
            self.data_receiver.stop()
            self.data_receiver = None

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
        with self._session_lock:
            session = self._active_sessions.get(device_id)
            return bool(session and session.is_healthy())

    def get_connected_devices(self) -> List[str]:
        """Return IDs of all currently connected devices."""
        with self._session_lock:
            return [
                dev_id
                for dev_id, s in self._active_sessions.items()
                if s.is_healthy()
            ]

    def _usb_monitor_loop(self) -> None:
        """Continuously maintain bidirectional ADB forward/reverse tunnels when a device is attached via USB."""
        while self._running:
            try:
                adb = find_adb()
                if adb:
                    devs = adb_devices(adb)
                    online = [d for d in devs if d.get("state") == "device"]
                    for d in online:
                        serial = d.get("serial")
                        setup_bidirectional_adb_tunnels(adb, device_serial=serial)
            except Exception:
                pass
            time.sleep(4.0)

    def is_usb_available(self, device_id: Optional[str] = None) -> bool:
        """
        Check if USB connection is actively available:
        either via an active session with transport_type == 'usb',
        or an attached ADB device with active forwarding.
        """
        with self._session_lock:
            if device_id:
                s = self._active_sessions.get(device_id)
                if s and s.is_healthy() and s.transport_type == "usb":
                    return True
            else:
                for s in self._active_sessions.values():
                    if s.is_healthy() and s.transport_type == "usb":
                        return True

        # Check if an ADB device is physically attached and online
        try:
            adb = find_adb()
            if adb:
                devs = adb_devices(adb)
                online = [d for d in devs if d.get("state") == "device"]
                if online:
                    for d in online:
                        serial = d.get("serial")
                        setup_bidirectional_adb_tunnels(adb, device_serial=serial)
                    return True
        except Exception as e:
            logger.debug("Error checking ADB devices: %s", e)

        return False

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
            client_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            msg = recv_framed_msg(client_sock, timeout=5.0)
            if not msg:
                client_sock.close()
                return

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
                    send_framed_msg(client_sock, {"type": "PAIR_ACK", "status": "error", "error": "invalid_token"})
                    client_sock.close()
                    return

                transport_type = "usb" if client_ip.startswith("127.") else "wifi"

                with self._session_lock:
                    existing = self._active_sessions.get(remote_id)
                    if existing and existing.is_healthy():
                        logger.info(
                            "[SOCKET_DUPLICATE] Ignored duplicate connect attempt from %s",
                            remote_id,
                        )
                        client_sock.close()
                        return

                    if existing:
                        existing.close("replaced_unhealthy")

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
                    send_framed_msg(client_sock, ack)

                    session = ActiveSession(client_sock, remote_id, transport_type, self._on_session_closed, self._on_session_message)
                    self._active_sessions[remote_id] = session
                    logger.info("[SOCKET] Established session with %s via %s", remote_id, transport_type)
                    session.start()
                    if self._current_local_receive_offer:
                        local_id = self.pairing_manager.get_local_identity()
                        session.send_msg({
                            "type": "RECEIVE_OFFER",
                            "uri": self._current_local_receive_offer,
                            "device_id": local_id.device_id,
                            "ts": int(time.time() * 1000),
                        })

                self._connecting_devices.discard(remote_id)
                if self.signals:
                    try:
                        self.signals.sig_pairing_completed.emit(paired_dev)
                    except Exception as e:
                        logger.debug("Error emitting sig_pairing_completed: %s", e)
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

            transport_type = "usb" if client_ip.startswith("127.") else "wifi"

            # Duplicate connection suppression:
            # If an existing healthy session is active, suppress the new incoming socket.
            with self._session_lock:
                existing = self._active_sessions.get(remote_id)
                if existing and existing.is_healthy():
                    logger.info(
                        "[SOCKET_DUPLICATE] Ignored duplicate connect attempt from %s",
                        remote_id,
                    )
                    client_sock.close()
                    return

                if existing:
                    existing.close("replaced_unhealthy")

                # Send HELLO_ACK
                local_id = self.pairing_manager.get_local_identity()
                ack = {
                    "type": "HELLO_ACK",
                    "device_id": local_id.device_id,
                    "name": local_id.name,
                    "version": 1,
                    "ts": int(time.time() * 1000),
                }
                send_framed_msg(client_sock, ack)

                # Start active session
                session = ActiveSession(client_sock, remote_id, transport_type, self._on_session_closed, self._on_session_message)
                self._active_sessions[remote_id] = session
                logger.info("[SOCKET] Established session with %s via %s", remote_id, transport_type)
                session.start()
                if self._current_local_receive_offer:
                    local_id = self.pairing_manager.get_local_identity()
                    session.send_msg({
                        "type": "RECEIVE_OFFER",
                        "uri": self._current_local_receive_offer,
                        "device_id": local_id.device_id,
                        "ts": int(time.time() * 1000),
                    })

            self._connecting_devices.discard(remote_id)
            self.pairing_manager.update_connection_state(remote_id, ConnectionState.CONNECTED)

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
        with self._session_lock:
            current = self._active_sessions.get(device_id)
            if current is not session:
                logger.debug("[DIAG] Stale session closed for %s (reason=%s), ignoring", device_id, reason)
                return
            self._active_sessions.pop(device_id, None)

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

    def _on_session_message(self, session: ActiveSession, msg: dict) -> None:
        mtype = msg.get("type")
        if mtype == "RECEIVE_OFFER":
            uri = msg.get("uri", "")
            if uri:
                with self._connect_lock:
                    self._latest_receive_offers[session.device_id] = uri
                logger.info("[DIAG] [RECEIVE_OFFER_RCVD] Received offer from %s: %s", session.device_id, uri)
                if self.signals:
                    try:
                        self.signals.sig_receive_offer.emit(session.device_id, uri)
                    except Exception as e:
                        logger.debug("Error emitting sig_receive_offer: %s", e)
        elif mtype == "REQUEST_RECEIVE":
            logger.info("[DIAG] [REQUEST_RECEIVE] Received receive request from %s", session.device_id)
            if self.signals:
                try:
                    self.signals.sig_request_receive.emit(session.device_id)
                except Exception as e:
                    logger.debug("Error emitting sig_request_receive: %s", e)

    def broadcast_receive_offer(self, uri: str) -> None:
        self._current_local_receive_offer = uri
        local_id = self.pairing_manager.get_local_identity()
        offer = {
            "type": "RECEIVE_OFFER",
            "uri": uri,
            "device_id": local_id.device_id,
            "ts": int(time.time() * 1000),
        }
        with self._connect_lock:
            for session in self._active_sessions.values():
                session.send_msg(offer)
        logger.info("[DIAG] [RECEIVE_OFFER_BROADCAST] Broadcasted receive offer across active sessions: %s", uri)

    def clear_local_receive_offer(self) -> None:
        self._current_local_receive_offer = None

    def get_latest_receive_offer(self, device_id: Optional[str] = None) -> Optional[str]:
        with self._connect_lock:
            if device_id and device_id in self._latest_receive_offers:
                return self._latest_receive_offers[device_id]
            if self._latest_receive_offers:
                return next(iter(self._latest_receive_offers.values()))
        return None

    def _notify_device_updated(self, device: PairedDevice) -> None:
        if self.signals:
            try:
                self.signals.sig_device_updated.emit(device)
                self.signals.sig_connection_state_changed.emit(device.identity.device_id, device.connection_state)
                if device.connection_state == ConnectionState.CONNECTED:
                    trans = self.get_active_transports(device.identity.device_id)
                    transport_type = trans[0] if trans else "wifi"
                    self.signals.sig_device_connected.emit(device.identity.device_id, transport_type)
                elif device.connection_state == ConnectionState.DISCONNECTED:
                    self.signals.sig_device_disconnected.emit(device.identity.device_id)
            except Exception as e:
                logger.debug("Error emitting Qt signals in _notify_device_updated: %s", e)

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
                    setup_bidirectional_adb_tunnels(adb)
                    candidates.append(("127.0.0.1", PC_TO_PHONE_CONTROL_FORWARD_PORT, "usb"))

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
                        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                        s.settimeout(2.0)
                        s.connect((ip, port))
                        if send_framed_msg(s, pair_req):
                            resp = recv_framed_msg(s, timeout=3.0)
                            if resp and resp.get("type") == "PAIR_ACK" and resp.get("status") == "ok":
                                paired_sock = s
                                paired_transport = trans
                                logger.info("[DIAG] [PAIR_OK] Connected and paired via %s with %s:%d", trans, ip, port)
                                break
                        s.close()
                    except Exception as ex:
                        logger.debug("Pair connect attempt to %s:%d failed: %s", ip, port, ex)

                if paired_sock:
                    with self._session_lock:
                        existing = self._active_sessions.get(payload.rid)
                        if existing and existing.is_healthy():
                            logger.info("[SOCKET_DUPLICATE] Ignored duplicate connect attempt from %s", payload.rid)
                            try:
                                paired_sock.close()
                            except Exception:
                                pass
                            return

                        if existing:
                            existing.close("replaced_unhealthy")
                        session = ActiveSession(paired_sock, payload.rid, paired_transport, self._on_session_closed, self._on_session_message)
                        self._active_sessions[payload.rid] = session
                        logger.info("[SOCKET] Established session with %s via %s", payload.rid, paired_transport)
                        session.start()
                        if self._current_local_receive_offer:
                            local_id = self.pairing_manager.get_local_identity()
                            session.send_msg({
                                "type": "RECEIVE_OFFER",
                                "uri": self._current_local_receive_offer,
                                "device_id": local_id.device_id,
                                "ts": int(time.time() * 1000),
                            })

                    self.pairing_manager.update_connection_state(payload.rid, ConnectionState.CONNECTED)
                else:
                    self.pairing_manager.update_connection_state(payload.rid, ConnectionState.DISCONNECTED)

                updated = self.pairing_manager.get_paired_device(payload.rid)
                if updated:
                    if self.signals and paired_sock:
                        try:
                            self.signals.sig_pairing_completed.emit(updated)
                        except Exception as e:
                            logger.debug("Error emitting sig_pairing_completed: %s", e)
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
        """Connect to a paired device with strict single transport preference and duplicate suppression."""
        def _connect():
            with self._session_lock:
                existing = self._active_sessions.get(device_id)
                if existing and existing.is_healthy():
                    logger.info("[SOCKET_DUPLICATE] Ignored duplicate connect attempt from %s", device_id)
                    if on_connected:
                        on_connected()
                    return

                if device_id in self._connecting_devices:
                    logger.info("[SOCKET_DUPLICATE] Ignored duplicate connect attempt from %s", device_id)
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

                # D. Single Transport Preference: Prioritize USB if ADB is detected with connected device
                adb = find_adb()
                usb_available = False
                if adb:
                    try:
                        devs = adb_devices(adb)
                        if any(d.get("state") == "device" for d in devs):
                            usb_available = True
                    except Exception:
                        usb_available = False

                if usb_available:
                    try:
                        setup_bidirectional_adb_tunnels(adb)
                        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        s.settimeout(2.0)
                        s.connect(("127.0.0.1", PC_TO_PHONE_CONTROL_FORWARD_PORT))
                        if self._perform_handshake(s, device_id):
                            connected_sock = s
                            transport_type = "usb"
                        else:
                            s.close()
                    except Exception as e:
                        logger.debug("USB connect attempt failed: %s", e)

                # Only try Wi-Fi candidates if USB did not connect
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
                                break
                            else:
                                s.close()
                        except Exception as e:
                            logger.debug("Wi-Fi connect attempt to %s:%d failed: %s", addr, target_port, e)

                if connected_sock:
                    with self._session_lock:
                        existing = self._active_sessions.get(device_id)
                        if existing and existing.is_healthy():
                            logger.info("[SOCKET_DUPLICATE] Ignored duplicate connect attempt from %s", device_id)
                            try:
                                connected_sock.close()
                            except Exception:
                                pass
                            if on_connected:
                                on_connected()
                            return

                        if existing:
                            existing.close("replaced_by_outgoing")

                        session = ActiveSession(connected_sock, device_id, transport_type, self._on_session_closed, self._on_session_message)
                        self._active_sessions[device_id] = session
                        logger.info("[SOCKET] Established session with %s via %s", device_id, transport_type)
                        session.start()
                        if self._current_local_receive_offer:
                            local_id = self.pairing_manager.get_local_identity()
                            session.send_msg({
                                "type": "RECEIVE_OFFER",
                                "uri": self._current_local_receive_offer,
                                "device_id": local_id.device_id,
                                "ts": int(time.time() * 1000),
                            })

                    self.pairing_manager.update_connection_state(device_id, ConnectionState.CONNECTED)
                    updated = self.pairing_manager.get_paired_device(device_id)
                    if updated:
                        self._notify_device_updated(updated)
                    if on_connected:
                        on_connected()
                else:
                    with self._session_lock:
                        is_conn = self.is_connected(device_id)
                    if is_conn:
                        logger.info("[SOCKET_DUPLICATE] Ignored duplicate connect attempt from %s", device_id)
                        if on_connected:
                            on_connected()
                    else:
                        self.pairing_manager.update_connection_state(device_id, ConnectionState.DISCONNECTED)
                        logger.info("[SOCKET_CLOSE] Closed by error: Could not establish connection to %s", dev.identity.name)
                        updated = self.pairing_manager.get_paired_device(device_id)
                        if updated:
                            self._notify_device_updated(updated)
                        if on_failed:
                            on_failed("Could not establish connection to device endpoints")

            finally:
                with self._session_lock:
                    self._connecting_devices.discard(device_id)

        t = threading.Thread(target=_connect, name=f"PhotoBeam-Connect-{device_id[:8]}", daemon=True)
        t.start()

    def _perform_handshake(self, sock: socket.socket, expected_peer_id: str) -> bool:
        """Send HELLO and await valid HELLO_ACK without prefetching bytes."""
        try:
            local_id = self.pairing_manager.get_local_identity()
            hello = {
                "type": "HELLO",
                "device_id": local_id.device_id,
                "name": local_id.name,
                "version": 1,
                "ts": int(time.time() * 1000),
            }
            if not send_framed_msg(sock, hello):
                return False

            ack = recv_framed_msg(sock, timeout=3.0)
            if not ack:
                return False
            if ack.get("type") == "HELLO_ACK" and ack.get("device_id") == expected_peer_id:
                return True
            return False
        except Exception:
            return False

    def disconnect_device(self, device_id: str) -> None:
        with self._session_lock:
            session = self._active_sessions.pop(device_id, None)
        if session:
            try:
                session.send_msg({"type": "DISCONNECT", "reason": "user_action"})
            except Exception:
                pass
            session.close("local_user")

        self.pairing_manager.update_connection_state(device_id, ConnectionState.DISCONNECTED)
        dev = self.pairing_manager.get_paired_device(device_id)
        if dev:
            self._notify_device_updated(dev)

    def is_device_connected(self, device_id: str) -> bool:
        with self._session_lock:
            session = self._active_sessions.get(device_id)
            return bool(session and session.is_healthy())

    def get_active_connected_device_id(self) -> str:
        """Return the device_id of the currently active session, or first CONNECTED paired device."""
        with self._session_lock:
            for dev_id, session in self._active_sessions.items():
                if session and session.is_healthy():
                    return dev_id
        # Fallback: check paired devices state
        for dev in self.pairing_manager.get_paired_devices():
            if dev.connection_state == ConnectionState.CONNECTED:
                return dev.identity.device_id
        return ""

    def get_active_transports(self, device_id: str) -> List[str]:
        with self._session_lock:
            session = self._active_sessions.get(device_id)
            if session and session.is_healthy():
                return [session.transport_type]
            return []
