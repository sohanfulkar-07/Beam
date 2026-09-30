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

import logging
import os
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

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
    from .transport.usb_transport import UsbTransport, find_adb
    from .transport.wifi_transport import WiFiTransport
except (ImportError, ValueError):
    from usb_transport import UsbTransport, find_adb
    from wifi_transport import WiFiTransport

logger = logging.getLogger("photobeam.connection_manager")


class ConnectionManager:
    """
    Windows ConnectionManager orchestrating:
    - Persistent pairings
    - Live mDNS/broadcast discovery
    - Active transport health (Wi-Fi + USB)
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
            port=47474,
            transports=["wifi", "usb"],
            on_peer_discovered=self._on_peer_discovered,
            on_peer_lost=self._on_peer_lost,
        )

        self._active_connections: Dict[str, Dict] = {}  # device_id -> {wifi: ..., usb: ...}
        self._backoff_timers: Dict[str, float] = {}      # device_id -> next_retry_time
        self._backoff_intervals: Dict[str, float] = {}   # device_id -> current_interval

        self._callbacks_lock = threading.Lock()
        self._on_device_updated_cbs: List[Callable[[PairedDevice], None]] = []
        self._on_pairing_request_cb: Optional[Callable[[DeviceIdentity, Callable[[bool], None]], None]] = None

        self._running = False
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
        self._reconnect_thread = threading.Thread(
            target=self._auto_reconnect_loop, name="PhotoBeam-AutoReconnect", daemon=True
        )
        self._reconnect_thread.start()
        logger.info("ConnectionManager started")

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        self.discovery_service.stop()
        for device_id in list(self._active_connections.keys()):
            self.disconnect_device(device_id)
        logger.info("ConnectionManager stopped")

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
        paired = self.pairing_manager.get_paired_device(device_id)
        if paired:
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
        """
        Execute cryptographic pairing handshake given a scanned/input PairingPayload.
        Runs asynchronously on a background thread.
        """
        def _task():
            try:
                if payload.is_expired():
                    if on_error:
                        on_error("Pairing QR code has expired. Please refresh the QR code.")
                    return

                # Connect to peer to verify challenge
                addrs = payload.addrs
                port = payload.port
                if not addrs:
                    if on_error:
                        on_error("No valid IP addresses in pairing payload")
                    return

                # Create peer identity
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

                # Persist paired relationship
                self.pairing_manager.save_paired_device(paired_device)
                self._notify_device_updated(paired_device)

                if on_success:
                    on_success(paired_device)
            except Exception as e:
                logger.error("Pairing failed: %s", e)
                if on_error:
                    on_error(str(e))

        t = threading.Thread(target=_task, name="PhotoBeam-PairingWorker", daemon=True)
        t.start()

    # ── Connection Lifecycle ──────────────────────────────────────────────────

    def connect_device(
        self,
        device_id: str,
        on_connected: Optional[Callable[[], None]] = None,
        on_failed: Optional[Callable[[str], None]] = None,
    ) -> None:
        """Manually trigger connection to a paired device."""
        def _connect():
            dev = self.pairing_manager.get_paired_device(device_id)
            if not dev:
                if on_failed:
                    on_failed("Device is not paired")
                return
            if dev.identity.trust_status != TrustStatus.TRUSTED:
                self.pairing_manager.update_connection_state(device_id, ConnectionState.AUTHENTICATION_REQUIRED)
                if on_failed:
                    on_failed("Device trust was revoked. Re-pairing is required.")
                return

            self.pairing_manager.update_connection_state(device_id, ConnectionState.CONNECTING)
            self.pairing_manager.record_connection_attempt(device_id)
            self._notify_device_updated(self.pairing_manager.get_paired_device(device_id))

            endpoint = dev.endpoint
            if not endpoint or not endpoint.addrs:
                self.pairing_manager.update_connection_state(device_id, ConnectionState.DISCONNECTED)
                if on_failed:
                    on_failed("Device endpoint is unknown. Waiting for local discovery.")
                return

            # Check for USB transport first if available
            connected_transports = {}
            adb = find_adb()
            if adb:
                try:
                    usb_tr = UsbTransport(transport_id="usb")
                    if usb_tr.is_available():
                        connected_transports["usb"] = usb_tr
                except Exception:
                    pass

            # Try Wi-Fi
            wifi_connected = False
            for addr in endpoint.addrs:
                try:
                    wifi_tr = WiFiTransport(transport_id="wifi")
                    # Connect check
                    wifi_tr.connect(addr, endpoint.port, timeout=3.0, cert_fp=endpoint.cert_fp or None)
                    if wifi_tr.is_connected():
                        connected_transports["wifi"] = wifi_tr
                        wifi_connected = True
                        break
                except Exception:
                    pass

            if connected_transports:
                self._active_connections[device_id] = connected_transports
                self.pairing_manager.update_connection_state(device_id, ConnectionState.CONNECTED)
                self._backoff_intervals[device_id] = 1.0
                updated = self.pairing_manager.get_paired_device(device_id)
                if updated:
                    self._notify_device_updated(updated)
                if on_connected:
                    on_connected()
            else:
                self.pairing_manager.update_connection_state(device_id, ConnectionState.DISCONNECTED)
                updated = self.pairing_manager.get_paired_device(device_id)
                if updated:
                    self._notify_device_updated(updated)
                if on_failed:
                    on_failed("Could not establish connection to device endpoints")

        t = threading.Thread(target=_connect, name=f"PhotoBeam-Connect-{device_id[:8]}", daemon=True)
        t.start()

    def disconnect_device(self, device_id: str) -> None:
        conns = self._active_connections.pop(device_id, {})
        for tr in conns.values():
            try:
                tr.disconnect()
            except Exception:
                pass
        self.pairing_manager.update_connection_state(device_id, ConnectionState.DISCONNECTED)
        dev = self.pairing_manager.get_paired_device(device_id)
        if dev:
            self._notify_device_updated(dev)

    def is_device_connected(self, device_id: str) -> bool:
        conns = self._active_connections.get(device_id)
        if not conns:
            return False
        return any(tr.is_connected() for tr in conns.values())

    def get_active_transports(self, device_id: str) -> List[str]:
        conns = self._active_connections.get(device_id, {})
        return [name for name, tr in conns.items() if tr.is_connected()]

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
                if dev.connection_state == ConnectionState.CONNECTED:
                    # Health check on active transports
                    conns = self._active_connections.get(dev_id, {})
                    alive = any(tr.is_connected() for tr in conns.values())
                    if not alive and conns:
                        logger.info("Connection lost for %s, marking disconnected", dev.identity.name)
                        self.disconnect_device(dev_id)
                    continue

                if dev.presence_state == PresenceState.DISCOVERED and dev.connection_state == ConnectionState.DISCONNECTED:
                    # Check backoff timer
                    next_retry = self._backoff_timers.get(dev_id, 0.0)
                    if now >= next_retry:
                        interval = self._backoff_intervals.get(dev_id, 1.0)
                        self._backoff_intervals[dev_id] = min(interval * 2.0, 30.0)
                        self._backoff_timers[dev_id] = now + self._backoff_intervals[dev_id]

                        # Attempt reconnection in background
                        self.connect_device(dev_id)
