"""
PhotoBeam — TLS Certificate Generation

Generates ephemeral self-signed certificates per session.
Uses cryptography library.
"""
from __future__ import annotations

import datetime
import hashlib
import ipaddress
import socket


try:
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.backends import default_backend
    CRYPTOGRAPHY_AVAILABLE = True
except ImportError:
    CRYPTOGRAPHY_AVAILABLE = False


def generate_session_cert(
    common_name: str = "photobeam",
    valid_days: int = 1,
) -> tuple[bytes, bytes, str]:
    """
    Generate an ephemeral RSA-2048 self-signed TLS certificate.
    Returns (cert_pem, key_pem, fingerprint_hex_string).
    fingerprint format: "sha256:<hex>"
    
    Raises ImportError if 'cryptography' package not installed.
    """
    if not CRYPTOGRAPHY_AVAILABLE:
        raise ImportError("Install: pip install cryptography")

    key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
        backend=default_backend(),
    )

    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, common_name),
    ])

    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=valid_days))
        .add_extension(
            x509.SubjectAlternativeName([
                x509.DNSName("localhost"),
                x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
            ]),
            critical=False,
        )
        .sign(key, hashes.SHA256(), default_backend())
    )

    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    key_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )

    # Compute fingerprint of DER-encoded cert
    cert_der = cert.public_bytes(serialization.Encoding.DER)
    fp = "sha256:" + hashlib.sha256(cert_der).hexdigest()

    return cert_pem, key_pem, fp


def get_local_addresses() -> list[str]:
    """
    Get all local non-loopback IPv4 addresses.
    Used to populate the QR payload addrs field.
    Includes Wi-Fi, Ethernet, and USB Tethering (RNDIS/NCM) interfaces.
    """
    addrs: list[str] = []

    # On Windows, ipconfig discovers all active network adapters including RNDIS/NCM USB tethering
    import os
    if os.name == "nt":
        try:
            import subprocess
            import re
            output = subprocess.check_output("ipconfig", text=True, timeout=3)
            ips = re.findall(r"IPv4 Address[.\s]+:\s*([0-9.]+)", output)
            for ip in ips:
                if not ip.startswith("127.") and ip not in addrs:
                    addrs.append(ip)
        except Exception:
            pass

    try:
        hostname = socket.gethostname()
        infos = socket.getaddrinfo(hostname, None, socket.AF_INET)
        for info in infos:
            ip = info[4][0]
            if not ip.startswith("127.") and ip not in addrs:
                addrs.append(ip)
    except OSError:
        pass

    # Also try connect trick to determine default outbound IP
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            if ip not in addrs and not ip.startswith("127."):
                addrs.insert(0, ip)
    except OSError:
        pass

    return addrs or ["127.0.0.1"]
