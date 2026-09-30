"""
Tests: QR Payload encode/decode
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'protocol'))

import time
import pytest
from src.models import QRPayload, PROTOCOL_VERSION
from src.qr_payload import encode_qr_payload, decode_qr_payload, URI_SCHEME


def make_payload(**kwargs) -> QRPayload:
    defaults = dict(
        v=PROTOCOL_VERSION,
        sid="550e8400-e29b-41d4-a716-446655440000",
        rid="rid-uuid",
        addrs=["192.168.1.5"],
        port=47474,
        transports=["wifi"],
        token="a" * 64,
        exp=int(time.time()) + 300,
        cert_fp="sha256:abcdef",
    )
    defaults.update(kwargs)
    return QRPayload(**defaults)


def test_encode_starts_with_scheme():
    p = make_payload()
    uri = encode_qr_payload(p)
    assert uri.startswith(URI_SCHEME)


def test_encode_decode_roundtrip():
    p = make_payload()
    uri = encode_qr_payload(p)
    p2 = decode_qr_payload(uri)
    assert p2.sid == p.sid
    assert p2.rid == p.rid
    assert p2.addrs == p.addrs
    assert p2.port == p.port
    assert p2.token == p.token
    assert p2.exp == p.exp
    assert p2.cert_fp == p.cert_fp
    assert p2.transports == p.transports
    assert p2.v == p.v


def test_decode_invalid_scheme():
    with pytest.raises(ValueError, match="Not a PhotoBeam URI"):
        decode_qr_payload("https://example.com/not-photobeam")


def test_decode_corrupted_base64():
    with pytest.raises(ValueError):
        decode_qr_payload(URI_SCHEME + "!!not_valid_base64!!")


def test_decode_missing_field():
    import base64, json
    payload = {"v": 1, "sid": "x"}  # missing many fields
    b64 = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    with pytest.raises(ValueError, match="missing fields"):
        decode_qr_payload(URI_SCHEME + b64)


def test_decode_future_version():
    import base64, json
    payload = {
        "v": 999, "sid": "x", "rid": "y",
        "addrs": [], "port": 1, "transports": [],
        "token": "t", "exp": 0, "cert_fp": ""
    }
    b64 = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    with pytest.raises(ValueError, match="Unsupported protocol version"):
        decode_qr_payload(URI_SCHEME + b64)


def test_payload_expiry():
    p_expired = make_payload(exp=int(time.time()) - 1)
    assert p_expired.is_expired()

    p_valid = make_payload(exp=int(time.time()) + 300)
    assert not p_valid.is_expired()


def test_multiple_addresses():
    p = make_payload(addrs=["192.168.1.5", "10.0.0.1", "172.16.0.1"])
    uri = encode_qr_payload(p)
    p2 = decode_qr_payload(uri)
    assert p2.addrs == ["192.168.1.5", "10.0.0.1", "172.16.0.1"]


def test_wifi_and_usb_transports():
    p = make_payload(transports=["wifi", "usb"])
    uri = encode_qr_payload(p)
    p2 = decode_qr_payload(uri)
    assert "wifi" in p2.transports
    assert "usb" in p2.transports


def test_decode_pairing_uri_as_qr_payload():
    from src.models import PairingPayload, encode_pairing_payload
    pp = PairingPayload(
        v=PROTOCOL_VERSION,
        sid="session-pair-123",
        rid="receiver-id-456",
        addrs=["192.168.1.50", "127.0.0.1"],
        port=47474,
        transports=["wifi", "usb"],
        token="pair-token-xyz",
        exp=int(time.time()) + 1800,
        cert_fp="sha256:fp123",
        device_name="TestLaptop",
        device_public_key="pubkey-abc",
        capabilities=["file_transfer"],
        pairing_nonce="nonce-123",
    )
    uri = encode_pairing_payload(pp)
    assert uri.startswith("photobeam://pair/")
    decoded = decode_qr_payload(uri)
    assert decoded.sid == "session-pair-123"
    assert decoded.rid == "receiver-id-456"
    assert decoded.addrs == ["192.168.1.50", "127.0.0.1"]
    assert decoded.port == 47474
    assert decoded.token == "pair-token-xyz"
    assert not decoded.is_expired()


def test_decode_pairing_uri_expired():
    from src.models import PairingPayload, encode_pairing_payload
    pp = PairingPayload(
        v=PROTOCOL_VERSION,
        sid="session-expired",
        rid="receiver-id-456",
        addrs=["127.0.0.1"],
        port=47474,
        transports=["wifi"],
        token="token",
        exp=int(time.time()) - 100,  # Expired
        cert_fp="",
        device_name="Laptop",
        device_public_key="key",
        capabilities=[],
        pairing_nonce="nonce",
    )
    uri = encode_pairing_payload(pp)
    decoded = decode_qr_payload(uri)
    assert decoded.is_expired()
