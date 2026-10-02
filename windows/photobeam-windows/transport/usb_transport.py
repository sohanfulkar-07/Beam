"""
PhotoBeam — USB Transport (Windows/Python)

Uses ADB reverse port forwarding to tunnel TCP over USB cable.
Detects ADB, sets up reverse tunnel, connects like a second TCP transport.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from typing import Optional

import sys
if getattr(sys, 'frozen', False):
    _proto = os.path.join(getattr(sys, '_MEIPASS', os.path.dirname(sys.executable)), 'protocol')
else:
    _proto = os.path.join(os.path.dirname(__file__), '..', '..', '..', 'protocol')
if _proto not in sys.path:
    sys.path.insert(0, _proto)


from src.transport import Transport, TransportStatus

try:
    from .wifi_transport import WiFiTransport
except (ImportError, ValueError):
    from wifi_transport import WiFiTransport  # USB transport is TCP over ADB tunnel

# Known ADB locations on Windows
ADB_SEARCH_PATHS = [
    r"C:\Users\Sohan\AppData\Local\Android\Sdk\platform-tools\adb.exe",
    r"C:\Program Files\Android\android-sdk\platform-tools\adb.exe",
    r"C:\Android\platform-tools\adb.exe",
]

CONTROL_PORT = 47470
CONTROL_USB_PORT = 47471
DATA_PORT = 47474
DATA_USB_PORT = 47475
MIRROR_PORT = 47478

PC_TO_PHONE_CONTROL_FORWARD_PORT = 47480
PC_TO_PHONE_DATA_FORWARD_PORT = 47484

USB_TUNNEL_PORT = DATA_USB_PORT  # 47475 (Android -> PC reverse data)


def find_adb() -> Optional[str]:
    """Find ADB executable. Returns path or None."""
    # Check PATH first
    adb_on_path = shutil.which("adb")
    if adb_on_path:
        return adb_on_path
    for p in ADB_SEARCH_PATHS:
        if os.path.isfile(p):
            return p
    return None


def adb_devices(adb: str) -> list[dict]:
    """
    Run 'adb devices' and return list of connected devices.
    Each dict: {'serial': str, 'state': str}
    """
    try:
        result = subprocess.run(
            [adb, "devices"],
            capture_output=True, text=True, timeout=10
        )
        lines = result.stdout.strip().splitlines()
        devices = []
        for line in lines[1:]:  # Skip "List of devices attached"
            parts = line.strip().split("\t")
            if len(parts) == 2:
                devices.append({"serial": parts[0], "state": parts[1]})
        return devices
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []


def setup_adb_forward(adb: str, local_port: int, remote_port: int, device_serial: Optional[str] = None) -> bool:
    """
    Run 'adb forward tcp:LOCAL tcp:REMOTE'.
    Forwards connection from PC (localhost:local_port) across USB to Android (localhost:remote_port).
    Used when Windows is SENDER/client connecting to Android RECEIVER/server.
    """
    cmd = [adb]
    if device_serial:
        cmd += ["-s", device_serial]
    cmd += ["forward", f"tcp:{local_port}", f"tcp:{remote_port}"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def teardown_adb_forward(adb: str, local_port: int, device_serial: Optional[str] = None) -> None:
    """Remove ADB forward tunnel."""
    cmd = [adb]
    if device_serial:
        cmd += ["-s", device_serial]
    cmd += ["forward", "--remove", f"tcp:{local_port}"]
    try:
        subprocess.run(cmd, capture_output=True, timeout=5)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass


def setup_adb_reverse(adb: str, remote_port: int = USB_TUNNEL_PORT, local_port: int = USB_TUNNEL_PORT,
                      device_serial: Optional[str] = None) -> bool:
    """
    Run 'adb reverse tcp:REMOTE tcp:LOCAL' to create reverse USB tunnel.
    Forwards connection from Android (localhost:remote_port) across USB to PC (localhost:local_port).
    Used when Windows is RECEIVER/server accepting connection from Android SENDER/client.
    """
    cmd = [adb]
    if device_serial:
        cmd += ["-s", device_serial]
    cmd += ["reverse", f"tcp:{remote_port}", f"tcp:{local_port}"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def teardown_adb_reverse(adb: str, remote_port: int = USB_TUNNEL_PORT, device_serial: Optional[str] = None) -> None:
    """Remove the ADB reverse tunnel."""
    cmd = [adb]
    if device_serial:
        cmd += ["-s", device_serial]
    cmd += ["reverse", "--remove", f"tcp:{remote_port}"]
    try:
        subprocess.run(cmd, capture_output=True, timeout=5)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass


def setup_bidirectional_adb_tunnels(adb: str, device_serial: Optional[str] = None) -> bool:
    """
    Configure both directions upon detecting a connected device via ADB:
    # Phone -> PC (Reverse)
    adb -s <device_id> reverse tcp:47471 tcp:47470   # Control channel
    adb -s <device_id> reverse tcp:47475 tcp:47474   # Data receiver on PC
    adb -s <device_id> reverse tcp:47478 tcp:47478   # Screen mirror

    # PC -> Phone (Forward)
    adb -s <device_id> forward tcp:47480 tcp:47470   # Control listener on Phone
    adb -s <device_id> forward tcp:47484 tcp:47474   # Data receiver on Phone
    """
    ok_rev_ctrl = setup_adb_reverse(adb, remote_port=CONTROL_USB_PORT, local_port=CONTROL_PORT, device_serial=device_serial)
    ok_rev_data = setup_adb_reverse(adb, remote_port=DATA_USB_PORT, local_port=DATA_PORT, device_serial=device_serial)
    setup_adb_reverse(adb, remote_port=MIRROR_PORT, local_port=MIRROR_PORT, device_serial=device_serial)

    ok_fwd_ctrl = setup_adb_forward(adb, local_port=PC_TO_PHONE_CONTROL_FORWARD_PORT, remote_port=CONTROL_PORT, device_serial=device_serial)
    ok_fwd_data = setup_adb_forward(adb, local_port=PC_TO_PHONE_DATA_FORWARD_PORT, remote_port=DATA_PORT, device_serial=device_serial)

    return all([ok_rev_ctrl, ok_rev_data, ok_fwd_ctrl, ok_fwd_data])


class UsbTransport(Transport):
    """
    USB transport implemented via ADB tunnel (forward or reverse).
    
    When connected, data flows over the physical USB cable via ADB.
    This is real USB utilization — not Wi-Fi.
    
    See docs/USB.md for full explanation of this design decision.
    """

    def __init__(self, transport_id: str = "usb"):
        super().__init__(transport_id)
        self._wifi_transport: Optional[WiFiTransport] = None
        self._adb_path: Optional[str] = None
        self._device_serial: Optional[str] = None
        self._tunnel_port: int = USB_TUNNEL_PORT

    @staticmethod
    def is_available() -> tuple[bool, str]:
        """
        Check if USB transport can be set up.
        Returns (available, reason_if_not).
        """
        adb = find_adb()
        if not adb:
            return False, "ADB not found. Install Android SDK platform-tools."
        devices = adb_devices(adb)
        online = [d for d in devices if d["state"] == "device"]
        if not online:
            if devices:
                states = ", ".join(f"{d['serial']}:{d['state']}" for d in devices)
                return False, f"ADB devices not ready: {states}. Enable USB debugging."
            return False, "No Android devices connected via USB."
        return True, ""

    def connect(self, host: str = "127.0.0.1", port: int = PC_TO_PHONE_DATA_FORWARD_PORT,
                timeout: float = 10.0, target_port: int = DATA_PORT, **kwargs) -> None:
        """
        Set up ADB forward tunnel to receiver's port, then connect TCP to localhost tunnel.
        Forward: PC localhost:port -> Android localhost:target_port.
        """
        self._status = TransportStatus.CONNECTING
        adb = find_adb()
        if not adb:
            self._status = TransportStatus.FAILED
            raise ConnectionError("ADB not found")
        self._adb_path = adb

        devices = adb_devices(adb)
        online = [d for d in devices if d["state"] == "device"]
        if not online:
            self._status = TransportStatus.FAILED
            raise ConnectionError("No Android device connected via USB with debugging enabled")

        self._device_serial = online[0]["serial"]
        self._tunnel_port = port
        ok = setup_adb_forward(adb, port, target_port, self._device_serial)
        if not ok:
            self._status = TransportStatus.FAILED
            raise ConnectionError("Failed to set up ADB forward tunnel")

        # Small delay to let tunnel establish
        time.sleep(0.3)

        # Connect via TCP to the local ADB tunnel endpoint
        self._wifi_transport = WiFiTransport(self.transport_id)
        try:
            self._wifi_transport.connect(
                "127.0.0.1", port, timeout=timeout, cert_fp=kwargs.get("cert_fp")
            )
            self._status = TransportStatus.CONNECTED
        except Exception as e:
            teardown_adb_forward(adb, port, self._device_serial)
            self._status = TransportStatus.FAILED
            raise ConnectionError(f"Failed to connect through USB tunnel: {e}") from e

    def disconnect(self) -> None:
        self._status = TransportStatus.DISCONNECTED
        if self._wifi_transport:
            self._wifi_transport.disconnect()
            self._wifi_transport = None
        if self._adb_path and self._device_serial:
            teardown_adb_forward(self._adb_path, self._tunnel_port, self._device_serial)
            teardown_adb_reverse(self._adb_path, self._tunnel_port, self._device_serial)

    def send(self, data: bytes) -> int:
        if not self._wifi_transport:
            raise ConnectionError("Not connected")
        return self._wifi_transport.send(data)

    def recv(self, n: int) -> bytes:
        if not self._wifi_transport:
            raise ConnectionError("Not connected")
        return self._wifi_transport.recv(n)

    def send_all(self, data: bytes) -> None:
        if not self._wifi_transport:
            raise ConnectionError("Not connected")
        self._wifi_transport.send_all(data)
        self._record_sent(len(data))

    def recv_exact(self, n: int) -> bytes:
        if not self._wifi_transport:
            raise ConnectionError("Not connected")
        data = self._wifi_transport.recv_exact(n)
        self._record_received(n)
        return data

    def send_json(self, msg: dict) -> None:
        if not self._wifi_transport:
            raise ConnectionError("Not connected")
        self._wifi_transport.send_json(msg)

    def recv_json(self, timeout: float = 30.0) -> dict:
        if not self._wifi_transport:
            raise ConnectionError("Not connected")
        return self._wifi_transport.recv_json(timeout)

    def send_chunk_frame(self, frame) -> None:
        if not self._wifi_transport:
            raise ConnectionError("Not connected")
        self._wifi_transport.send_chunk_frame(frame)

    def recv_chunk_frame(self, timeout: float = 30.0):
        if not self._wifi_transport:
            raise ConnectionError("Not connected")
        return self._wifi_transport.recv_chunk_frame(timeout)

