"""
PhotoBeam — Cryptographic Pairing and Persistent Identity Tests

Validates:
1. DeviceIdentity, PairedDevice, DeviceEndpoint serialization roundtrips.
2. Ed25519 key generation, signing, and verification.
3. Rejecting forged/tampered signatures.
4. Windows PairingManager caching, persistence, forget, and revoke behavior.
5. PairingPayload QR encoding and decoding roundtrips.
"""
import base64
import os
import shutil
import sys
import tempfile
import time
import uuid
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "protocol" / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "windows" / "photobeam-windows"))

from models import (
    PROTOCOL_VERSION,
    Capability,
    ConnectionState,
    DeviceEndpoint,
    DeviceIdentity,
    PairedDevice,
    PairingPayload,
    PresenceState,
    TrustStatus,
    decode_pairing_payload,
    encode_pairing_payload,
)
from pairing_manager import PairingManager


def test_device_identity_serialization():
    identity = DeviceIdentity(
        device_id="test-id-1234",
        name="Pixel 9 Pro",
        public_key="abcd1234publickey==",
        created_at=1700000000,
        last_seen=1700001000,
        trust_status=TrustStatus.TRUSTED,
        app_version="1.0.0",
        protocol_version=PROTOCOL_VERSION,
        capabilities=[Capability.FILE_TRANSFER, Capability.SCREEN_MIRROR_SEND],
    )
    d = identity.to_dict()
    restored = DeviceIdentity.from_dict(d)

    assert restored.device_id == "test-id-1234"
    assert restored.name == "Pixel 9 Pro"
    assert restored.public_key == "abcd1234publickey=="
    assert restored.trust_status == TrustStatus.TRUSTED
    assert Capability.FILE_TRANSFER in restored.capabilities
    assert Capability.SCREEN_MIRROR_SEND in restored.capabilities


def test_device_endpoint_serialization():
    endpoint = DeviceEndpoint(
        addrs=["192.168.1.100", "10.0.0.5"],
        port=47474,
        transports=["wifi", "usb"],
        cert_fp="sha256:aabbccddeeff",
        updated_at=1700000500,
    )
    d = endpoint.to_dict()
    restored = DeviceEndpoint.from_dict(d)

    assert restored.addrs == ["192.168.1.100", "10.0.0.5"]
    assert restored.port == 47474
    assert restored.transports == ["wifi", "usb"]
    assert restored.cert_fp == "sha256:aabbccddeeff"


def test_pairing_payload_qr_encode_decode():
    payload = PairingPayload(
        v=1,
        sid="session-uuid-1111",
        rid="receiver-uuid-2222",
        addrs=["192.168.1.50"],
        port=47474,
        transports=["wifi"],
        token="0123456789abcdef0123456789abcdef",
        exp=int(time.time()) + 3600,
        cert_fp="sha256:112233445566",
        device_name="Windows Workstation",
        device_public_key="pubkey_example_base64==",
        capabilities=["file_transfer", "screen_mirror_receive"],
        pairing_nonce=base64.b64encode(os.urandom(32)).decode("ascii"),
    )
    uri = encode_pairing_payload(payload)
    assert uri.startswith("photobeam://pair/")

    decoded = decode_pairing_payload(uri)
    assert decoded.v == 1
    assert decoded.sid == payload.sid
    assert decoded.rid == payload.rid
    assert decoded.device_name == "Windows Workstation"
    assert decoded.pairing_nonce == payload.pairing_nonce
    assert not decoded.is_expired()


def test_ed25519_sign_and_verify():
    pub_b64, priv_bytes = PairingManager.generate_keypair()
    assert len(pub_b64) > 0
    assert len(priv_bytes) == 32

    challenge = os.urandom(64)

    from cryptography.hazmat.primitives.asymmetric import ed25519
    priv_key = ed25519.Ed25519PrivateKey.from_private_bytes(priv_bytes)
    sig = priv_key.sign(challenge)

    assert PairingManager.verify_signature(pub_b64, challenge, sig) is True

    # Tampered challenge must fail
    tampered_challenge = bytearray(challenge)
    tampered_challenge[0] ^= 0xFF
    assert PairingManager.verify_signature(pub_b64, bytes(tampered_challenge), sig) is False

    # Tampered signature must fail
    tampered_sig = bytearray(sig)
    tampered_sig[0] ^= 0xFF
    assert PairingManager.verify_signature(pub_b64, challenge, bytes(tampered_sig)) is False


def test_pairing_manager_persistence():
    temp_dir = Path(tempfile.mkdtemp())
    try:
        pm = PairingManager(storage_dir=temp_dir)
        identity = pm.get_local_identity()
        assert identity.device_id is not None
        assert len(identity.public_key) > 0
        assert pm.get_local_private_key() is not None

        # Sign challenge with local manager
        challenge = b"photobeam-pairing-nonce-challenge-12345"
        sig = pm.sign_challenge(challenge)
        assert PairingManager.verify_signature(identity.public_key, challenge, sig) is True

        # Pair a peer device
        peer_identity = DeviceIdentity(
            device_id="peer-phone-9999",
            name="OnePlus Nord CE5",
            public_key="peerpubkey123==",
            created_at=int(time.time()),
            last_seen=int(time.time()),
            trust_status=TrustStatus.TRUSTED,
            capabilities=[Capability.FILE_TRANSFER],
        )
        peer_endpoint = DeviceEndpoint(
            addrs=["192.168.1.105"],
            port=47474,
            transports=["wifi"],
            cert_fp="sha256:peerfp",
            updated_at=int(time.time()),
        )
        peer_device = PairedDevice(
            identity=peer_identity,
            endpoint=peer_endpoint,
            connection_state=ConnectionState.DISCONNECTED,
            presence_state=PresenceState.DISCOVERED,
        )
        pm.save_paired_device(peer_device)

        assert pm.is_trusted("peer-phone-9999") is True
        paired_list = pm.get_paired_devices()
        assert len(paired_list) == 1
        assert paired_list[0].identity.name == "OnePlus Nord CE5"

        # Update presence and connection state
        pm.update_connection_state("peer-phone-9999", ConnectionState.CONNECTED)
        assert pm.get_paired_device("peer-phone-9999").connection_state == ConnectionState.CONNECTED

        # Restart manager from same directory to verify persistence
        pm2 = PairingManager(storage_dir=temp_dir)
        assert pm2.get_local_identity().device_id == identity.device_id
        assert pm2.is_trusted("peer-phone-9999") is True
        assert len(pm2.get_paired_devices()) == 1

        # Test Revoke
        pm2.revoke_trust("peer-phone-9999")
        assert pm2.is_trusted("peer-phone-9999") is False
        assert pm2.get_paired_device("peer-phone-9999").identity.trust_status == TrustStatus.REVOKED

        # Test Forget (remove)
        pm2.remove_paired_device("peer-phone-9999")
        assert pm2.get_paired_device("peer-phone-9999") is None
        assert len(pm2.get_paired_devices()) == 0

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
