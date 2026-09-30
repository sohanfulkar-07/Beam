"""
PhotoBeam Physical Real-Device E2E Transfer Verification
Transfers actual real-world files from Android device to Windows via PhotoBeam protocol.
Physically verifies that file size and SHA-256 match exactly byte-for-byte.
"""
import base64
import json
import os
import subprocess
import sys
import time
from pathlib import Path

# Protocol import path setup
PROTO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "protocol"))
if PROTO not in sys.path:
    sys.path.insert(0, PROTO)

WINDOWS_SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "windows", "photobeam-windows"))
if WINDOWS_SRC not in sys.path:
    sys.path.insert(0, WINDOWS_SRC)

from transport.wifi_transport import WiFiServer, WiFiTransport
from transport.tls_utils import generate_session_cert
from src.models import MessageType, ChunkFrame, TransferInfo, CHUNK_MAGIC, CHUNK_HEADER_SIZE
from src.transfer import TransferManager
from src.scheduler import Scheduler
from src.resume import ResumeManager
from src.storage import StorageManager
from src.integrity import IntegrityManager

ADB_PATH = r"C:\Users\Sohan\AppData\Local\Android\Sdk\platform-tools\adb.exe"
DEVICE_SERIAL = "2f1ce07"
BASE_DIR = "/sdcard/Download"

FILES_TO_TEST = [
    "fg-09.bin",
    "setup.exe",
]

def adb_command(args):
    cmd = [ADB_PATH, "-s", DEVICE_SERIAL] + args
    res = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return res.stdout.strip()

def run_real_device_transfer():
    print("[TEST] Checking device availability...")
    out = adb_command(["get-state"])
    assert out == "device", f"Device not ready: {out}"
    print(f"[TEST] Device {DEVICE_SERIAL} is ready.")

    # 1. Fetch expected hashes from device
    expected_hashes = {}
    expected_sizes = {}
    for fname in FILES_TO_TEST:
        fpath = f"{BASE_DIR}/{fname}"
        h_out = adb_command(["shell", "sha256sum", f"'{fpath}'"])
        sha = h_out.split()[0].strip().lower()
        s_out = adb_command(["shell", "stat", "-c", "%s", f"'{fpath}'"])
        size = int(s_out.strip())
        expected_hashes[fname] = sha
        expected_sizes[fname] = size
        print(f"[TEST] Source on Android: {fname} | Size: {size} bytes | SHA-256: {sha}")

    # 2. Setup destination dir on Windows
    dest_dir = Path(r"C:\Users\Sohan\OneDrive\Documents\Desktop\Beam\tests\scratch_receive")
    dest_dir.mkdir(parents=True, exist_ok=True)
    # Clear previous received files if any
    for fname in FILES_TO_TEST:
        p = dest_dir / fname
        if p.exists():
            p.unlink()

    # 3. Setup TLS server on Windows
    cert_pem, key_pem, fp = generate_session_cert()
    session_id = "test-session-real-" + str(int(time.time()))
    token = "test-token-123"
    port = 47474

    # Clear previous reverses and setup adb reverse for reliable USB connection
    try:
        adb_command(["reverse", "--remove-all"])
    except Exception:
        pass
    adb_command(["reverse", f"tcp:{port}", f"tcp:{port}"])
    adb_command(["reverse", "tcp:47475", f"tcp:{port}"])

    server = WiFiServer(
        port=port,
        cert_pem=cert_pem,
        key_pem=key_pem,
    )
    server.start()
    print(f"[TEST] Server started on port {port}", flush=True)

    # Build QR payload
    qr_dict = {
        "v": 1,
        "sid": session_id,
        "rid": "win-receiver",
        "addrs": ["127.0.0.1"],
        "port": port,
        "transports": ["wifi", "usb"],
        "token": token,
        "cert_fp": fp,
        "exp": int(time.time()) + 3600,
    }
    b64_payload = base64.urlsafe_b64encode(json.dumps(qr_dict).encode("utf-8")).decode("utf-8")
    qr_uri = f"photobeam://connect/{b64_payload}"

    # Build stream URIs for Android
    stream_uris = [f"file://{BASE_DIR}/{fname}" for fname in FILES_TO_TEST]
    uris_arg = ",".join(stream_uris)

    print(f"[TEST] Launching PhotoBeam on Android with {len(FILES_TO_TEST)} files...", flush=True)
    # Dismiss any stray file pickers
    try:
        adb_command(["shell", "am", "force-stop", "com.google.android.documentsui"])
    except Exception:
        pass

    start_out = adb_command([
        "shell", "am", "start",
        "-S",
        "-W",
        "-n", "com.photobeam.app/.MainActivity",
        "--es", "screen", "send",
        "--es", "qr_uri", qr_uri,
        "--esa", "stream_uris", uris_arg,
    ])
    print(f"[TEST] am start output:\n{start_out}", flush=True)
    print("[TEST] App started, waiting for incoming connection...", flush=True)

    client_sock, (client_ip, _) = server.accept(timeout=30.0)
    client_transport = WiFiTransport.from_accepted_socket(client_sock, "wifi-primary")
    print(f"[TEST] Client connected from {client_ip}")

    # Handle HELLO
    hello = client_transport.recv_json(timeout=10.0)
    print(f"[TEST] Received: {hello}")
    assert hello["type"] == MessageType.HELLO
    assert hello["sid"] == session_id
    assert hello["token"] == token

    client_transport.send_json({"type": MessageType.HELLO_ACK, "sid": session_id})

    # Wait for READY
    ready = client_transport.recv_json(timeout=20.0)
    print(f"[TEST] Received READY: {ready}")
    assert ready["type"] == MessageType.READY
    infos = [TransferInfo.from_dict(t) for t in ready.get("transfers", [])]
    assert len(infos) == len(FILES_TO_TEST)

    # Setup transfer manager
    storage = StorageManager(dest_dir)
    resume = ResumeManager()
    scheduler = Scheduler()
    scheduler.add_transport(client_transport)

    xfer = TransferManager(
        session_id=session_id,
        scheduler=scheduler,
        resume_manager=resume,
        storage_manager=storage,
    )
    xfer.setup_receive(infos)

    # Send ACCEPT for all files
    for info in infos:
        client_transport.send_json({"type": MessageType.ACCEPT, "fid": info.fid})

    print("[TEST] Sent ACCEPT for all files. Reading incoming chunk stream...")
    # Receive chunks and control messages
    while not xfer.is_all_files_verified():
        magic = client_transport.recv_exact(4)
        if magic == CHUNK_MAGIC:
            header_rest = client_transport.recv_exact(CHUNK_HEADER_SIZE - 4)
            header = magic + header_rest
            import struct
            chunk_len = struct.unpack_from(">I", header, 56)[0]
            chunk_data = client_transport.recv_exact(chunk_len)
            frame = ChunkFrame.decode(header + chunk_data)
            ok, r_reason = xfer.receive_chunk(frame)
            assert ok, f"Failed chunk receive: {r_reason}"

            import uuid
            fid = str(uuid.UUID(bytes=frame.file_id))
            if xfer.is_file_complete(fid):
                ok2, err = xfer.finalize_file(fid)
                if ok2:
                    csha = xfer._files[fid].info.sha256 or xfer._file_checksums.get(fid, "")
                    client_transport.send_json({"type": MessageType.FILE_DONE, "fid": fid, "sha256": csha})
                    print(f"[TEST] Completed and verified: {xfer._files[fid].info.name}")
        elif magic.startswith(b"{"):
            client_transport._recv_buf[:0] = magic
            msg = client_transport.recv_json(timeout=5.0)
            m_type = msg.get("type")
            if m_type == MessageType.FILE_CHECKSUM:
                cfid = msg.get("fid")
                csha = msg.get("sha256", "")
                print(f"[TEST] Received FILE_CHECKSUM for fid {cfid}: {csha}")
                xfer.set_file_checksum(cfid, csha)
                if xfer.is_file_complete(cfid):
                    ok2, err = xfer.finalize_file(cfid)
                    assert ok2, f"Failed finalization with FILE_CHECKSUM: {err}"
                    client_transport.send_json({"type": MessageType.FILE_DONE, "fid": cfid, "sha256": csha})
                    print(f"[TEST] Completed and verified via FILE_CHECKSUM: {xfer._files[cfid].info.name}")
            elif m_type == MessageType.RESUME:
                rfid = msg.get("fid")
                if rfid and xfer.is_file_complete(rfid):
                    if not xfer.is_file_verified(rfid):
                        xfer.finalize_file(rfid)
                    csha = xfer._files[rfid].info.sha256 or xfer._file_checksums.get(rfid, "")
                    client_transport.send_json({"type": MessageType.FILE_DONE, "fid": rfid, "sha256": csha})

    print("[TEST] All files received and verified by TransferManager!")
    client_transport.disconnect()
    server.stop()

    # 4. Strict physical byte-for-byte SHA-256 and size verification on Windows disk
    print("\n" + "=" * 60)
    print("PHYSICAL BYTE-FOR-BYTE INTEGRITY VERIFICATION REPORT")
    print("=" * 60)
    all_passed = True
    for fname in FILES_TO_TEST:
        received_path = dest_dir / fname
        assert received_path.exists(), f"Destination file missing: {received_path}"
        actual_size = received_path.stat().st_size
        expected_size = expected_sizes[fname]
        actual_sha256 = IntegrityManager.file_hash(received_path).lower()
        expected_sha256 = expected_hashes[fname].lower()

        print(f"\nFile: {fname}")
        print(f"  Source Size:       {expected_size:,} bytes")
        print(f"  Destination Size:  {actual_size:,} bytes")
        print(f"  Size Match:        {'PASS' if actual_size == expected_size else 'FAIL'}")
        print(f"  Source SHA-256:      {expected_sha256}")
        print(f"  Destination SHA-256: {actual_sha256}")
        print(f"  SHA-256 Match:     {'PASS' if actual_sha256 == expected_sha256 else 'FAIL'}")

        assert actual_size == expected_size, f"Size mismatch for {fname}"
        assert actual_sha256 == expected_sha256, f"SHA-256 mismatch for {fname}"

    print("\n" + "=" * 60)
    print("ALL REAL FILES TRANSFERRED BYTE-PERFECTLY AND VERIFIED!")
    print("=" * 60)

if __name__ == "__main__":
    run_real_device_transfer()
