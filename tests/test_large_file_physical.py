"""
PhotoBeam Physical Real-Device Large-File (28.75 GB) Verification Test
Transfers the actual real-world 28.75 GB file (fg-01.bin) from Xiaomi Pad 6 to Windows host.
Tracks:
- Start time, first-chunk time, total transfer time
- Instant start verification (<100ms negotiation without pre-reading)
- Average speed and continuous throughput
- Stalls (>2.0 seconds)
- Android App Peak RAM/PSS
- Final byte size exactness
- Final SHA-256 byte-for-byte exactness
"""
import base64
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

# Add protocol and windows src to sys.path
PROTO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "protocol"))
if PROTO not in sys.path:
    sys.path.insert(0, PROTO)

WINDOWS_SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "windows", "photobeam-windows"))
if WINDOWS_SRC not in sys.path:
    sys.path.insert(0, WINDOWS_SRC)

from transport.wifi_transport import WiFiServer, WiFiTransport
from transport.tls_utils import generate_session_cert, get_local_addresses
from src.models import MessageType, ChunkFrame, TransferInfo, CHUNK_MAGIC, CHUNK_HEADER_SIZE
from src.transfer import TransferManager
from src.scheduler import Scheduler
from src.resume import ResumeManager
from src.storage import StorageManager
from src.integrity import IntegrityManager

ADB_PATH = r"C:\Users\Sohan\AppData\Local\Android\Sdk\platform-tools\adb.exe"
DEVICE_SERIAL = "2f1ce07"
REAL_FILE_PATH = "/storage/emulated/0/Download/Grand Theft Auto V Legacy [FitGirl Repack]/fg-01.bin"
EXPECTED_SHA256 = "ebac36a9799d0a24150022b3fc102f1bc6a46c7d0dd62964012393e92aabeb58"
DEST_DIR = Path(r"C:\Users\Sohan\OneDrive\Documents\Desktop\Beam\tests\scratch_large_receive")

# Timeouts
HANDSHAKE_TIMEOUT = 30.0      # seconds for HELLO/READY handshake
STREAM_RECV_TIMEOUT = 600.0   # 10 min - generous timeout for any single recv during streaming
                               # (28 GB / ~30 MB/s USB = ~15 min total, each 4MB chunk = ~0.13s)


def adb_command(args, timeout_sec=30):
    cmd = [ADB_PATH, "-s", DEVICE_SERIAL] + args
    res = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=timeout_sec)
    return res.stdout.strip()


def format_bytes(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    elif n < 1024 * 1024:
        return f"{n / 1024:.2f} KB"
    elif n < 1024 * 1024 * 1024:
        return f"{n / (1024 * 1024):.2f} MB"
    else:
        return f"{n / (1024 * 1024 * 1024):.2f} GB"


class MemoryMonitor(threading.Thread):
    def __init__(self, stop_event):
        super().__init__(daemon=True)
        self.stop_event = stop_event
        self.peak_pss_kb = 0
        self.latest_pss_kb = 0

    def run(self):
        while not self.stop_event.is_set():
            try:
                out = adb_command(["shell", "dumpsys", "meminfo", "com.photobeam.app"], timeout_sec=10)
                m = re.search(r"TOTAL PSS:\s+(\d+)", out)
                if m:
                    pss = int(m.group(1))
                    self.latest_pss_kb = pss
                    if pss > self.peak_pss_kb:
                        self.peak_pss_kb = pss
            except Exception:
                pass
            time.sleep(3.0)


def recv_with_timeout(transport, n, timeout=STREAM_RECV_TIMEOUT):
    """Receive exactly n bytes with a proper timeout that persists across the call."""
    sock = transport._sock
    if sock:
        old_timeout = sock.gettimeout()
        sock.settimeout(timeout)
    try:
        return transport.recv_exact(n)
    finally:
        if sock:
            sock.settimeout(old_timeout)


def recv_json_safe(transport, timeout=HANDSHAKE_TIMEOUT):
    """Receive a JSON message while preserving the socket timeout for the streaming phase."""
    sock = transport._sock
    # Save the current timeout
    if sock:
        prev_timeout = sock.gettimeout()
        sock.settimeout(timeout)
    try:
        buf = transport._recv_buf
        while b"\n" not in buf:
            if not transport._sock:
                raise ConnectionError("Not connected")
            chunk = transport._sock.recv(4096)
            if not chunk:
                raise ConnectionError("Connection closed while receiving JSON")
            buf.extend(chunk)
        idx = buf.index(b"\n")
        line = bytes(buf[:idx])
        del buf[:idx + 1]
        return json.loads(line.decode("utf-8"))
    finally:
        # Restore the timeout we want for streaming, NOT None
        if sock:
            sock.settimeout(prev_timeout)


def run_test():
    print("=" * 70, flush=True)
    print("PHOTOBEAM REAL-DEVICE 28.75 GB PHYSICAL TRANSFER TEST", flush=True)
    print("=" * 70, flush=True)

    # 1. Pre-flight Checks
    print("[PRE-FLIGHT] Checking device availability...", flush=True)
    out = adb_command(["get-state"])
    assert out == "device", f"Device not ready: {out}"
    print(f"[PRE-FLIGHT] Android device {DEVICE_SERIAL} is connected.", flush=True)

    # File Stats
    print(f"[PRE-FLIGHT] Inspecting source file on device: {REAL_FILE_PATH}", flush=True)
    size_str = adb_command(["shell", "stat", "-c", "%s", f"'{REAL_FILE_PATH}'"])
    source_size = int(size_str.strip())
    print(f"[PRE-FLIGHT] Exact Source Size: {source_size:,} bytes ({format_bytes(source_size)})", flush=True)
    print(f"[PRE-FLIGHT] Source SHA-256:   {EXPECTED_SHA256}", flush=True)

    # Storage Check
    df_out = adb_command(["shell", "df", "-h", "/storage/emulated/0"])
    print(f"[PRE-FLIGHT] Android Storage:\n{df_out}", flush=True)

    win_free = shutil.disk_usage(DEST_DIR.drive if DEST_DIR.drive else "C:").free
    print(f"[PRE-FLIGHT] Windows Destination Free: {format_bytes(win_free)}", flush=True)
    assert win_free > source_size + (5 * 1024 * 1024 * 1024), "Insufficient space on Windows disk!"

    # Ensure clean destination directory
    DEST_DIR.mkdir(parents=True, exist_ok=True)
    final_dest = DEST_DIR / "fg-01.bin"
    if final_dest.exists():
        print(f"[PRE-FLIGHT] Removing previous destination file ({format_bytes(final_dest.stat().st_size)})...", flush=True)
        final_dest.unlink()

    # 2. Setup Server & Port Forwarding
    cert_pem, key_pem, fp = generate_session_cert()
    session_id = f"real-large-{int(time.time())}"
    token = "large-token-secret"
    port = 47474

    print("[SETUP] Cleaning adb reverse bindings...", flush=True)
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
    print(f"[SETUP] TLS Server listening on port {port}", flush=True)

    # Local Wi-Fi addresses + USB loopback
    local_addrs = get_local_addresses()
    candidate_addrs = ["127.0.0.1"] + [a for a in local_addrs if a != "127.0.0.1"]

    qr_dict = {
        "v": 1,
        "sid": session_id,
        "rid": "win-receiver",
        "addrs": candidate_addrs,
        "port": port,
        "transports": ["wifi", "usb"],
        "token": token,
        "cert_fp": fp,
        "exp": int(time.time()) + 7200,
    }
    b64_payload = base64.urlsafe_b64encode(json.dumps(qr_dict).encode("utf-8")).decode("utf-8")
    qr_uri = f"photobeam://connect/{b64_payload}"

    # 3. Launch PhotoBeam on Android with real file
    print(f"[LAUNCH] Launching PhotoBeam on device with fg-01.bin...", flush=True)
    try:
        adb_command(["shell", "am", "force-stop", "com.photobeam.app"])
    except Exception:
        pass
    time.sleep(1.0)

    import urllib.parse
    encoded_uri = "file://" + urllib.parse.quote(REAL_FILE_PATH)
    t_start = time.time()
    start_res = adb_command([
        "shell", "am", "start",
        "-S",
        "-W",
        "-n", "com.photobeam.app/.MainActivity",
        "--es", "screen", "send",
        "--es", "qr_uri", qr_uri,
        "--esa", "stream_uris", encoded_uri,
    ], timeout_sec=30)
    print(f"[LAUNCH] App launch result:\n{start_res}", flush=True)

    # 4. Accept Connection
    print("[HANDSHAKE] Awaiting incoming TLS connection...", flush=True)
    client_sock, (client_ip, _) = server.accept(timeout=60.0)
    client_transport = WiFiTransport.from_accepted_socket(client_sock, "wifi-primary")
    t_connected = time.time()
    print(f"[HANDSHAKE] Client connected from {client_ip} in {t_connected - t_start:.2f}s", flush=True)

    # Set a generous but finite timeout for the streaming phase
    client_transport._sock.settimeout(STREAM_RECV_TIMEOUT)

    # HELLO / HELLO_ACK — use shorter timeout for handshake
    hello = recv_json_safe(client_transport, timeout=HANDSHAKE_TIMEOUT)
    assert hello["type"] == MessageType.HELLO, f"Expected HELLO, got {hello.get('type')}"
    client_transport.send_json({"type": MessageType.HELLO_ACK, "sid": session_id})
    print(f"[HANDSHAKE] Authenticated session: {session_id}", flush=True)

    # READY message
    ready = recv_json_safe(client_transport, timeout=HANDSHAKE_TIMEOUT)
    t_ready = time.time()
    assert ready["type"] == MessageType.READY, f"Expected READY, got {ready.get('type')}"
    transfers = ready.get("transfers", [])
    assert len(transfers) == 1, f"Expected 1 transfer, got {len(transfers)}"
    info = TransferInfo.from_dict(transfers[0])
    print(f"[NEGOTIATION] Received READY in {t_ready - t_connected:.2f}s", flush=True)
    print(f"[NEGOTIATION] File: {info.name} | Size: {info.size:,} bytes | Total Chunks: {info.total_chunks:,}", flush=True)
    assert info.size == source_size, f"Size mismatch in READY: {info.size} != {source_size}"

    # Setup TransferManager
    storage = StorageManager(DEST_DIR)
    resume = ResumeManager()
    scheduler = Scheduler()
    scheduler.add_transport(client_transport)

    xfer = TransferManager(
        session_id=session_id,
        scheduler=scheduler,
        resume_manager=resume,
        storage_manager=storage,
    )
    xfer.setup_receive([info])

    # Send ACCEPT
    client_transport.send_json({"type": MessageType.ACCEPT, "fid": info.fid})
    print(f"[NEGOTIATION] Sent ACCEPT. Beginning streaming chunk reception...", flush=True)

    # Start memory monitoring thread
    stop_mem_event = threading.Event()
    mem_monitor = MemoryMonitor(stop_mem_event)
    mem_monitor.start()

    # 5. Receive Stream
    # CRITICAL: Ensure the socket has a proper timeout for the streaming phase.
    # recv_json() had a bug where it reset timeout to None in its finally block,
    # which made recv_exact hang forever if the connection dropped.
    client_transport._sock.settimeout(STREAM_RECV_TIMEOUT)

    t_first_chunk = None
    chunks_received = 0
    bytes_received = 0
    last_chunk_time = time.time()
    stalls = []
    received_file_checksum = None

    last_log_time = time.time()
    last_log_bytes = 0

    print(f"[STREAM] Socket timeout set to {STREAM_RECV_TIMEOUT}s for streaming phase", flush=True)
    print(f"[STREAM] Expected {info.total_chunks:,} chunks of ~4 MB each", flush=True)

    try:
        while not xfer.is_all_files_verified():
            # Read first 4 bytes to determine if it's a chunk or JSON control message
            magic = recv_with_timeout(client_transport, 4, timeout=STREAM_RECV_TIMEOUT)
            now = time.time()

            if t_first_chunk is None:
                t_first_chunk = now
                print(f"[TIMING] First chunk arrived at +{t_first_chunk - t_start:.2f}s after launch!", flush=True)

            gap = now - last_chunk_time
            if gap > 2.0:
                stalls.append((now - t_start, gap))
                print(f"[WARN] Stall of {gap:.2f}s detected at +{now - t_start:.1f}s", flush=True)
            last_chunk_time = now

            if magic == CHUNK_MAGIC:
                header_rest = recv_with_timeout(client_transport, CHUNK_HEADER_SIZE - 4)
                header = magic + header_rest
                chunk_len = struct.unpack_from(">I", header, 56)[0]
                chunk_data = recv_with_timeout(client_transport, chunk_len)
                frame = ChunkFrame.decode(header + chunk_data)

                ok, reason = xfer.receive_chunk(frame)
                assert ok, f"Chunk receive failed: {reason}"

                chunks_received += 1
                bytes_received += len(frame.data)

                # Periodic logging (every 10 seconds)
                if now - last_log_time >= 10.0:
                    elapsed_sec = now - t_first_chunk
                    interval_sec = now - last_log_time
                    interval_bytes = bytes_received - last_log_bytes
                    cur_speed_mb = (interval_bytes / (1024 * 1024)) / max(interval_sec, 0.001)
                    avg_speed_mb = (bytes_received / (1024 * 1024)) / max(elapsed_sec, 0.001)
                    pct = (bytes_received / source_size) * 100.0
                    remaining = source_size - bytes_received
                    rate = bytes_received / max(elapsed_sec, 0.001)
                    eta_sec = remaining / max(rate, 1)

                    print(
                        f"[PROGRESS] {pct:5.1f}% | {format_bytes(bytes_received):>9} / {format_bytes(source_size)} "
                        f"| Chunks: {chunks_received:6d}/{info.total_chunks} "
                        f"| Speed: {cur_speed_mb:5.1f} MB/s (Avg: {avg_speed_mb:5.1f} MB/s) "
                        f"| ETA: {eta_sec/60:4.1f}m | PSS: {mem_monitor.latest_pss_kb:6d} kB",
                        flush=True,
                    )
                    last_log_time = now
                    last_log_bytes = bytes_received

                if xfer.is_file_complete(info.fid):
                    if received_file_checksum is not None:
                        print("[FINALIZING] All chunks received + checksum available. Finalizing...", flush=True)
                        ok2, err = xfer.finalize_file(info.fid)
                        assert ok2, f"Finalize failed: {err}"
                        client_transport.send_json({"type": MessageType.FILE_DONE, "fid": info.fid, "sha256": received_file_checksum})
                        print("[FINALIZING] Sent FILE_DONE!", flush=True)

            elif magic.startswith(b"{"):
                # JSON control message — prepend back to receive buffer and parse
                client_transport._recv_buf[:0] = magic
                msg = recv_json_safe(client_transport, timeout=30.0)
                m_type = msg.get("type")
                print(f"[CONTROL] Received control message: {m_type}", flush=True)

                if m_type == MessageType.FILE_CHECKSUM:
                    received_file_checksum = msg.get("sha256", "")
                    print(f"[CHECKSUM] Received FILE_CHECKSUM: {received_file_checksum}", flush=True)
                    xfer.set_file_checksum(info.fid, received_file_checksum)
                    if xfer.is_file_complete(info.fid):
                        print("[FINALIZING] All chunks complete + checksum received! Finalizing file on disk...", flush=True)
                        ok2, err = xfer.finalize_file(info.fid)
                        assert ok2, f"Finalize failed: {err}"
                        client_transport.send_json({"type": MessageType.FILE_DONE, "fid": info.fid, "sha256": received_file_checksum})
                        print("[FINALIZING] Sent FILE_DONE!", flush=True)
                elif m_type == MessageType.RESUME:
                    # Sender asking for completion check — respond with current state
                    if xfer.is_file_complete(info.fid):
                        print("[RESUME] File already complete, ignoring RESUME request", flush=True)
                    else:
                        missing = xfer.get_missing_chunks(info.fid)
                        print(f"[RESUME] {len(missing)} chunks still missing", flush=True)
                        if missing:
                            client_transport.send_json({
                                "type": MessageType.NAK_CHUNK,
                                "fid": info.fid,
                                "missing": missing[:100],  # send first 100 missing
                            })
                elif m_type == MessageType.PING:
                    client_transport.send_json({"type": MessageType.PONG, "ts": msg.get("ts", 0)})
                elif m_type == MessageType.CANCEL:
                    print("[CANCEL] Sender cancelled the transfer!", flush=True)
                    break
                else:
                    print(f"[CONTROL] Unhandled message type: {m_type}", flush=True)
            else:
                print(f"[WARN] Unknown magic bytes: {magic.hex()}", flush=True)

    except (ConnectionError, OSError, TimeoutError) as e:
        print(f"\n[ERROR] Transfer interrupted: {type(e).__name__}: {e}", flush=True)
        print(f"[ERROR] Bytes received so far: {bytes_received:,} / {source_size:,} ({bytes_received/source_size*100:.1f}%)", flush=True)
        print(f"[ERROR] Chunks received: {chunks_received} / {info.total_chunks}", flush=True)

        # Try to get logcat for diagnosis
        try:
            logcat = adb_command(["logcat", "-d", "-t", "50", "-s", "PhotoBeamPerf:*", "PhotoBeam.Transport:*"], timeout_sec=10)
            print(f"[LOGCAT] Recent Android logs:\n{logcat}", flush=True)
        except Exception:
            pass

        raise

    t_end = time.time()
    stop_mem_event.set()
    client_transport.disconnect()
    server.stop()

    total_transfer_time = t_end - (t_first_chunk or t_start)
    avg_speed_mb = (bytes_received / (1024 * 1024)) / max(total_transfer_time, 0.001)

    print("\n" + "=" * 70, flush=True)
    print("PHYSICAL FILE TRANSFER COMPLETED — EXECUTING DISK INTEGRITY CHECK", flush=True)
    print("=" * 70, flush=True)

    assert final_dest.exists(), f"Destination file does not exist: {final_dest}"
    dest_size = final_dest.stat().st_size
    print(f"Destination Path: {final_dest}", flush=True)
    print(f"Source Size:      {source_size:,} bytes", flush=True)
    print(f"Destination Size: {dest_size:,} bytes", flush=True)
    assert dest_size == source_size, f"Size mismatch: {dest_size} != {source_size}"

    print("[INTEGRITY] Computing destination SHA-256 over 28.75 GB file...", flush=True)
    t_hash_start = time.time()
    dest_sha256 = IntegrityManager.file_hash(final_dest)
    t_hash_end = time.time()
    print(f"[INTEGRITY] Hash computed in {t_hash_end - t_hash_start:.1f}s", flush=True)
    print(f"Source SHA-256:      {EXPECTED_SHA256}", flush=True)
    print(f"Destination SHA-256: {dest_sha256}", flush=True)
    assert dest_sha256.lower() == EXPECTED_SHA256.lower(), "SHA-256 MISMATCH!"

    print("\n" + "=" * 70, flush=True)
    print("REAL-DEVICE 28.75 GB PHYSICAL TRANSFER TEST PASSED 100% BYTE-PERFECT!", flush=True)
    print("=" * 70, flush=True)
    print(f"Start Time:           {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(t_start))}", flush=True)
    print(f"First-Chunk Latency:  {t_first_chunk - t_start:.2f}s", flush=True)
    print(f"Total Transfer Time:  {total_transfer_time:.2f}s ({total_transfer_time / 60:.2f} min)", flush=True)
    print(f"Average Speed:        {avg_speed_mb:.2f} MB/s", flush=True)
    print(f"Total Stalls (>2s):   {len(stalls)}", flush=True)
    if stalls:
        for s_time, s_dur in stalls[:20]:
            print(f"  - Stall of {s_dur:.2f}s at +{s_time:.1f}s", flush=True)
        if len(stalls) > 20:
            print(f"  ... and {len(stalls) - 20} more", flush=True)
    print(f"Peak Android RAM/PSS: {mem_monitor.peak_pss_kb:,} kB ({mem_monitor.peak_pss_kb / 1024:.1f} MB)", flush=True)
    print("=" * 70, flush=True)


if __name__ == "__main__":
    run_test()
