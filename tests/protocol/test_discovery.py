"""
PhotoBeam — Discovery and ConnectionManager Tests

Validates:
1. DiscoveryService advertisement packet generation and parsing.
2. Ignoring self-advertisements and handling multiple addresses.
3. Peer expiration and TTL tracking.
4. ConnectionManager pairing payload processing and device tracking.
5. ConnectionManager callback dispatching.
"""
import base64
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "protocol" / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "windows" / "photobeam-windows"))

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
from discovery import DiscoveredPeer, DiscoveryService, MAGIC_HEADER
from pairing_manager import PairingManager
from connection_manager import ConnectionManager


def test_discovery_packet_encoding_and_parsing():
    ds = DiscoveryService(
        device_id="dev-win-100",
        device_name="Workstation PC",
        port=47474,
        transports=["wifi", "usb"],
        cert_fp="sha256:abcd1234",
    )
    packet = ds._build_advertisement()
    assert packet.startswith(MAGIC_HEADER)

    # Parse using a simulated receiver with a different device_id
    receiver_ds = DiscoveryService(
        device_id="dev-receiver-200",
        device_name="Receiver Test",
    )
    peer = receiver_ds._parse_packet(packet, ("192.168.1.150", 5353))
    assert peer is not None
    assert peer.device_id == "dev-win-100"
    assert peer.name == "Workstation PC"
    assert peer.port == 47474
    assert "wifi" in peer.transports
    assert "192.168.1.150" in peer.addrs


def test_discovery_ignores_self():
    ds = DiscoveryService(
        device_id="same-device-id",
        device_name="Self",
    )
    packet = ds._build_advertisement()
    # Parsing packet from self must return None
    parsed = ds._parse_packet(packet, ("127.0.0.1", 5353))
    assert parsed is None


def test_discovered_peer_expiration():
    peer = DiscoveredPeer(
        device_id="test-peer-id",
        name="Test Device",
        addrs=["192.168.1.20"],
        port=47474,
        transports=["wifi"],
        cert_fp="sha256:fp",
        ttl=1,  # 1 second TTL
    )
    assert not peer.is_expired()
    time.sleep(1.1)
    assert peer.is_expired()


def test_connection_manager_pairing():
    temp_dir = Path(tempfile.mkdtemp())
    try:
        pm = PairingManager(storage_dir=temp_dir)
        cm = ConnectionManager(pairing_manager=pm)

        updated_devices = []
        cm.add_device_updated_callback(lambda d: updated_devices.append(d))

        payload = PairingPayload(
            v=1,
            sid="session-1",
            rid="phone-nord-ce5",
            addrs=["192.168.1.120"],
            port=47474,
            transports=["wifi"],
            token="token123",
            exp=int(time.time()) + 3600,
            cert_fp="sha256:fp123",
            device_name="OnePlus Nord CE5",
            device_public_key="pubkey==",
            capabilities=["file_transfer"],
            pairing_nonce="nonce==",
        )

        completed = threading_event = False
        def _on_success(device):
            nonlocal completed
            completed = True

        cm.pair_with_payload(payload, on_success=_on_success)
        time.sleep(0.3)

        assert completed is True
        paired = pm.get_paired_device("phone-nord-ce5")
        assert paired is not None
        assert paired.identity.name == "OnePlus Nord CE5"
        assert paired.identity.trust_status == TrustStatus.TRUSTED
        assert len(updated_devices) > 0
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
