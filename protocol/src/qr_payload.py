"""
PhotoBeam Protocol — QR Payload Encoder/Decoder

Encodes QRPayload → photobeam:// URI
Decodes URI → QRPayload
Generates QR image (PNG bytes or PIL Image)
"""
from __future__ import annotations

import base64
import json
from typing import Optional

from .models import QRPayload

URI_SCHEME = "photobeam://connect/"


def encode_qr_payload(payload: QRPayload) -> str:
    """Encode payload to photobeam:// URI string."""
    json_bytes = json.dumps(payload.to_dict(), separators=(",", ":")).encode("utf-8")
    b64 = base64.urlsafe_b64encode(json_bytes).rstrip(b"=").decode("ascii")
    return URI_SCHEME + b64


def decode_qr_payload(uri: str) -> QRPayload:
    """
    Decode photobeam:// URI to QRPayload.
    Raises ValueError on invalid input.
    """
    if not uri.startswith(URI_SCHEME):
        raise ValueError(f"Not a PhotoBeam URI: {uri!r}")
    b64 = uri[len(URI_SCHEME):]
    # Add padding
    padding = 4 - len(b64) % 4
    if padding != 4:
        b64 += "=" * padding
    try:
        json_bytes = base64.urlsafe_b64decode(b64)
        d = json.loads(json_bytes.decode("utf-8"))
    except Exception as e:
        raise ValueError(f"Failed to decode QR payload: {e}") from e

    required_fields = {"v", "sid", "rid", "addrs", "port", "transports", "token", "exp", "cert_fp"}
    missing = required_fields - set(d.keys())
    if missing:
        raise ValueError(f"QR payload missing fields: {missing}")

    if d["v"] > 1:
        raise ValueError(f"Unsupported protocol version: {d['v']}")

    return QRPayload.from_dict(d)


def generate_qr_image(uri: str, box_size: int = 10, border: int = 4):
    """
    Generate QR code image for the given URI.
    Returns a PIL Image object.
    Raises ImportError if qrcode or PIL not installed.
    """
    try:
        import qrcode
        from PIL import Image
    except ImportError as e:
        raise ImportError("Install: pip install qrcode[pil]") from e

    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=box_size,
        border=border,
    )
    qr.add_data(uri)
    qr.make(fit=True)
    return qr.make_image(fill_color="black", back_color="white")


def generate_qr_png_bytes(uri: str, box_size: int = 10, border: int = 4) -> bytes:
    """Return QR as PNG bytes."""
    import io
    img = generate_qr_image(uri, box_size=box_size, border=border)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
