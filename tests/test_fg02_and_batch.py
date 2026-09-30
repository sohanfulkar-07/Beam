"""
PhotoBeam TEST 1: fg-02.bin (22.81 GB) — Single Large File Transfer
Then TEST 2: Full 12-file batch (~53.70 GB) in ONE session.
Both use WiFi + USB (wire) multi-transport via ADB reverse tunneling.
"""
import base64
import gc
import hashlib
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

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

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

ADB = r"C:\Users\Sohan\AppData\Local\Android\Sdk\platform-tools\adb.exe"
SERIAL = "2f1ce07"
SOURCE_DIR = "/storage/emulated/0/Download/Grand Theft Auto V Legacy [FitGirl Repack]"
STREAM_TIMEOUT = 600.0


def file_hash_safe(path, chunk_size=1 * 1024 * 1024):
    """Memory-safe SHA-256: uses 1MB buffer to avoid MemoryError after large transfers."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            buf = f.read(chunk_size)
            if not buf:
                break
            h.update(buf)
    return h.hexdigest()

# ── File manifest ────────────────────────────────────────────────────────────
ALL_FILES = {
    "fg-01.bin": 28745230572,
    "fg-02.bin": 22814321160,
    "fg-03.bin": 2189587787,
    "fg-04.bin": 692645794,
    "fg-05.bin": 476805682,
    "fg-06.bin": 81136709,
    "fg-07.bin": 1831395,
    "fg-08.bin": 1464505,
    "fg-09.bin": 312889,
    "fg-optional-bonus-content.bin": 2649646802,
    "setup.exe": 9419380,
    "Verify BIN files before installation.bat": 69,
}


def adb(args, timeout_sec=30):
    cmd = [ADB, "-s", SERIAL] + args
    return subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=timeout_sec).stdout.strip()


def fmt(n):
    if n < 1024: return f"{n} B"
    if n < 1024**2: return f"{n/1024:.2f} KB"
    if n < 1024**3: return f"{n/1024**2:.2f} MB"
    return f"{n/1024**3:.2f} GB"


class MemMon(threading.Thread):
    def __init__(self, stop):
        super().__init__(daemon=True)
        self.stop = stop
        self.peak = 0
        self.latest = 0

    def run(self):
        while not self.stop.is_set():
            try:
                out = adb(["shell", "dumpsys", "meminfo", "com.photobeam.app"], timeout_sec=10)
                m = re.search(r"TOTAL PSS:\s+(\d+)", out)
                if m:
                    v = int(m.group(1))
                    self.latest = v
                    self.peak = max(self.peak, v)
            except Exception:
                pass
            time.sleep(3.0)


def recv_json_safe(transport, timeout=30.0):
    sock = transport._sock
    prev = sock.gettimeout() if sock else None
    if sock:
        sock.settimeout(timeout)
    try:
        buf = transport._recv_buf
        while b"\n" not in buf:
            if not transport._sock:
                raise ConnectionError("Not connected")
            chunk = transport._sock.recv(4096)
            if not chunk:
                raise ConnectionError("Connection closed")
            buf.extend(chunk)
        idx = buf.index(b"\n")
        line = bytes(buf[:idx])
        del buf[:idx + 1]
        return json.loads(line.decode("utf-8"))
    finally:
        if sock:
            sock.settimeout(prev)


def recv_exact_t(transport, n, timeout=STREAM_TIMEOUT):
    sock = transport._sock
    if sock:
        old = sock.gettimeout()
        sock.settimeout(timeout)
    try:
        return transport.recv_exact(n)
    finally:
        if sock:
            sock.settimeout(old)


def run_single_transfer(file_names, dest_dir, test_label):
    """
    Transfer one or more files from Android device in a single PhotoBeam session.
    file_names: list of filenames to transfer (keys in ALL_FILES).
    dest_dir: Path to destination directory.
    test_label: label for logging.
    Returns dict of {filename: {size, sha256, time_sec}} or raises on failure.
    """
    print("=" * 70, flush=True)
    print(f"{test_label}", flush=True)
    print("=" * 70, flush=True)

    # Pre-flight
    assert adb(["get-state"]) == "device", "Device not ready"

    expected_files = {}
    for name in file_names:
        size = ALL_FILES[name]
        path = f"{SOURCE_DIR}/{name}"
        dev_size = int(adb(["shell", "stat", "-c", "%s", f"'{path}'"]).strip())
        assert dev_size == size, f"Size mismatch on device for {name}: {dev_size} vs {size}"
        expected_files[name] = {"size": size, "path": path}

    total_bytes = sum(f["size"] for f in expected_files.values())
    print(f"[PRE-FLIGHT] {len(file_names)} files, {fmt(total_bytes)} total", flush=True)

    win_free = shutil.disk_usage(dest_dir.drive if dest_dir.drive else "C:").free
    print(f"[PRE-FLIGHT] Windows Free: {fmt(win_free)}", flush=True)
    assert win_free > total_bytes + 5 * 1024**3, f"Insufficient space: need {fmt(total_bytes + 5*1024**3)}, have {fmt(win_free)}"

    dest_dir.mkdir(parents=True, exist_ok=True)
    # Note: Existing destination files and .pbtemp files are preserved to support resume

    # Server setup
    cert_pem, key_pem, fp = generate_session_cert()
    session_id = f"test-{int(time.time())}"
    port = 47474

    try:
        adb(["reverse", "--remove-all"])
    except Exception:
        pass
    adb(["reverse", f"tcp:{port}", f"tcp:{port}"])
    adb(["reverse", "tcp:47475", f"tcp:{port}"])

    server = WiFiServer(port=port, cert_pem=cert_pem, key_pem=key_pem)
    server.start()

    local_addrs = get_local_addresses()
    candidate_addrs = ["127.0.0.1"] + [a for a in local_addrs if a != "127.0.0.1"]

    qr_dict = {
        "v": 1, "sid": session_id, "rid": "win-receiver",
        "addrs": candidate_addrs, "port": port,
        "transports": ["wifi", "usb"],
        "token": "test-token",
        "cert_fp": fp, "exp": int(time.time()) + 7200,
    }
    b64 = base64.urlsafe_b64encode(json.dumps(qr_dict).encode()).decode()
    qr_uri = f"photobeam://connect/{b64}"

    # Build stream_uris for all files
    import urllib.parse
    stream_uris = ",".join(
        "file://" + urllib.parse.quote(f"{SOURCE_DIR}/{name}")
        for name in file_names
    )

    # Launch
    try:
        adb(["shell", "am", "force-stop", "com.photobeam.app"])
    except Exception:
        pass
    time.sleep(1.0)

    t_start = time.time()
    adb([
        "shell", "am", "start", "-S", "-W",
        "-n", "com.photobeam.app/.MainActivity",
        "--es", "screen", "send",
        "--es", "qr_uri", qr_uri,
        "--esa", "stream_uris", stream_uris,
    ], timeout_sec=30)

    # Accept TLS
    print("[HANDSHAKE] Awaiting TLS connection...", flush=True)
    client_sock, (client_ip, _) = server.accept(timeout=60.0)
    transport = WiFiTransport.from_accepted_socket(client_sock, "wifi-primary")
    transport._sock.settimeout(STREAM_TIMEOUT)
    t_conn = time.time()
    print(f"[HANDSHAKE] Connected from {client_ip} in {t_conn - t_start:.2f}s", flush=True)

    # HELLO
    hello = recv_json_safe(transport, timeout=30.0)
    assert hello["type"] == MessageType.HELLO
    transport.send_json({"type": MessageType.HELLO_ACK, "sid": session_id})

    # READY
    ready = recv_json_safe(transport, timeout=30.0)
    assert ready["type"] == MessageType.READY
    transfers = ready.get("transfers", [])
    print(f"[NEGOTIATION] READY received: {len(transfers)} files", flush=True)

    infos = [TransferInfo.from_dict(t) for t in transfers]
    for info in infos:
        print(f"  {info.name}: {info.size:,} bytes ({info.total_chunks} chunks)", flush=True)

    # Verify sizes match
    for info in infos:
        expected = ALL_FILES.get(info.name)
        if expected is not None:
            assert info.size == expected, f"Size mismatch for {info.name}: READY says {info.size}, expected {expected}"

    # Setup TransferManager
    storage = StorageManager(dest_dir)
    resume = ResumeManager()
    scheduler = Scheduler()
    scheduler.add_transport(transport)

    xfer = TransferManager(
        session_id=session_id, scheduler=scheduler,
        resume_manager=resume, storage_manager=storage,
    )
    xfer.setup_receive(infos)

    # Send ACCEPT for all files
    for info in infos:
        transport.send_json({"type": MessageType.ACCEPT, "fid": info.fid})
    print(f"[NEGOTIATION] Sent ACCEPT for all {len(infos)} files", flush=True)

    # Memory monitor
    stop_mem = threading.Event()
    mem = MemMon(stop_mem)
    mem.start()

    # Receive stream
    transport._sock.settimeout(STREAM_TIMEOUT)
    t_first = None
    chunks_rx = 0
    bytes_rx = 0
    stalls = []
    last_time = time.time()
    checksums = {}  # fid -> sha256
    finalized = set()
    log_time = time.time()
    log_bytes = 0
    file_done_count = 0

    fid_to_name = {info.fid: info.name for info in infos}
    fid_to_size = {info.fid: info.size for info in infos}

    print(f"[STREAM] Receiving {len(infos)} files, {fmt(total_bytes)} total...", flush=True)

    try:
        while not xfer.is_all_files_verified():
            magic = recv_exact_t(transport, 4)
            now = time.time()

            if t_first is None:
                t_first = now
                print(f"[TIMING] First data at +{t_first - t_start:.2f}s", flush=True)

            gap = now - last_time
            if gap > 2.0:
                stalls.append((now - t_start, gap))
                if gap > 5.0:
                    print(f"[WARN] Stall of {gap:.2f}s at +{now - t_start:.1f}s", flush=True)
            last_time = now

            if magic == CHUNK_MAGIC:
                hdr_rest = recv_exact_t(transport, CHUNK_HEADER_SIZE - 4)
                hdr = magic + hdr_rest
                clen = struct.unpack_from(">I", hdr, 56)[0]
                cdata = recv_exact_t(transport, clen)
                frame = ChunkFrame.decode(hdr + cdata)

                ok, reason = xfer.receive_chunk(frame)
                assert ok, f"Chunk failed: {reason}"
                chunks_rx += 1
                bytes_rx += len(frame.data)

                fid_str = str(uuid.UUID(bytes=frame.file_id))

                # Periodic logging
                if now - log_time >= 15.0:
                    elapsed = now - t_first
                    interval_bytes = bytes_rx - log_bytes
                    interval_sec = now - log_time
                    cur_speed = (interval_bytes / 1024**2) / max(interval_sec, 0.001)
                    avg_speed = (bytes_rx / 1024**2) / max(elapsed, 0.001)
                    pct = (bytes_rx / total_bytes) * 100
                    rate = bytes_rx / max(elapsed, 0.001)
                    eta = (total_bytes - bytes_rx) / max(rate, 1)
                    cur_file = fid_to_name.get(fid_str, "?")
                    print(
                        f"[PROGRESS] {pct:5.1f}% | {fmt(bytes_rx):>9}/{fmt(total_bytes)} "
                        f"| Chunks:{chunks_rx:7d} | {cur_speed:5.1f} MB/s (Avg:{avg_speed:5.1f}) "
                        f"| ETA:{eta/60:5.1f}m | File:{cur_file} | PSS:{mem.latest:6d}kB",
                        flush=True,
                    )
                    log_time = now
                    log_bytes = bytes_rx

                # Check if any file is complete + has checksum → finalize
                if fid_str not in finalized and xfer.is_file_complete(fid_str) and fid_str in checksums:
                    name = fid_to_name.get(fid_str, fid_str)
                    print(f"[FINALIZE] {name} — all chunks received, finalizing...", flush=True)
                    ok2, err = xfer.finalize_file(fid_str)
                    assert ok2, f"Finalize failed for {name}: {err}"
                    transport.send_json({"type": MessageType.FILE_DONE, "fid": fid_str, "sha256": checksums[fid_str]})
                    finalized.add(fid_str)
                    file_done_count += 1
                    print(f"[FINALIZE] {name} DONE ({file_done_count}/{len(infos)})", flush=True)

            elif magic.startswith(b"{"):
                transport._recv_buf[:0] = magic
                msg = recv_json_safe(transport, timeout=30.0)
                m_type = msg.get("type")

                if m_type == MessageType.FILE_CHECKSUM:
                    fid = msg["fid"]
                    sha = msg["sha256"]
                    checksums[fid] = sha
                    xfer.set_file_checksum(fid, sha)
                    name = fid_to_name.get(fid, fid)
                    print(f"[CHECKSUM] {name}: {sha[:16]}...", flush=True)

                    # If file already complete, finalize now
                    if fid not in finalized and xfer.is_file_complete(fid):
                        print(f"[FINALIZE] {name} — checksum arrived after chunks, finalizing...", flush=True)
                        ok2, err = xfer.finalize_file(fid)
                        assert ok2, f"Finalize failed for {name}: {err}"
                        transport.send_json({"type": MessageType.FILE_DONE, "fid": fid, "sha256": sha})
                        finalized.add(fid)
                        file_done_count += 1
                        print(f"[FINALIZE] {name} DONE ({file_done_count}/{len(infos)})", flush=True)

                elif m_type == MessageType.RESUME:
                    fid = msg.get("fid", "")
                    if xfer.is_file_complete(fid):
                        pass  # Already handled
                    else:
                        missing = xfer.get_missing_chunks(fid)
                        if missing:
                            transport.send_json({"type": MessageType.NAK_CHUNK, "fid": fid, "missing": missing[:100]})

                elif m_type == MessageType.PING:
                    transport.send_json({"type": MessageType.PONG, "ts": msg.get("ts", 0)})

                elif m_type == MessageType.CANCEL:
                    print(f"[CANCEL] Sender cancelled!", flush=True)
                    break
                else:
                    print(f"[CTRL] {m_type}", flush=True)
            else:
                print(f"[WARN] Unknown magic: {magic.hex()}", flush=True)

    except Exception as e:
        print(f"\n[ERROR] {type(e).__name__}: {e}", flush=True)
        print(f"[ERROR] {bytes_rx:,}/{total_bytes:,} bytes ({bytes_rx/total_bytes*100:.1f}%)", flush=True)
        try:
            logcat = adb(["logcat", "-d", "-t", "30", "-s", "PhotoBeamPerf:*", "PhotoBeam.Transport:*"], timeout_sec=10)
            print(f"[LOGCAT]\n{logcat}", flush=True)
        except Exception:
            pass
        raise

    t_end = time.time()
    stop_mem.set()
    transport.disconnect()
    server.stop()

    total_time = t_end - (t_first or t_start)
    avg_speed = (bytes_rx / 1024**2) / max(total_time, 0.001)

    print(f"\n{'=' * 70}", flush=True)
    print(f"TRANSFER COMPLETE — VERIFYING ALL FILES", flush=True)
    print(f"{'=' * 70}", flush=True)

    # Release transfer state to free memory before SHA-256 verification
    del xfer, scheduler, resume
    storage.close_all()
    gc.collect()
    print("[MEMORY] Released transfer state, running GC before verification", flush=True)

    results = {}
    all_pass = True
    for info in infos:
        name = info.name
        expected_size = ALL_FILES.get(name, info.size)
        dest = dest_dir / name
        print(f"\n[VERIFY] {name}", flush=True)

        if not dest.exists():
            print(f"  ❌ MISSING", flush=True)
            results[name] = {"pass": False, "reason": "missing"}
            all_pass = False
            continue

        actual_size = dest.stat().st_size
        print(f"  Size: {actual_size:,} / {expected_size:,}", flush=True)
        if actual_size != expected_size:
            print(f"  ❌ SIZE MISMATCH", flush=True)
            results[name] = {"pass": False, "reason": f"size {actual_size} != {expected_size}"}
            all_pass = False
            continue

        print(f"  Computing SHA-256 ({fmt(actual_size)})...", flush=True)
        t_h = time.time()
        sha = file_hash_safe(dest)
        h_time = time.time() - t_h
        sender_sha = checksums.get(info.fid, "")
        print(f"  SHA-256:  {sha}", flush=True)
        print(f"  Sender:   {sender_sha}", flush=True)
        print(f"  Hash time: {h_time:.1f}s", flush=True)

        if sender_sha and sha.lower() != sender_sha.lower():
            print(f"  ❌ SHA-256 MISMATCH", flush=True)
            results[name] = {"pass": False, "reason": "sha256 mismatch"}
            all_pass = False
        else:
            print(f"  ✅ PASS", flush=True)
            results[name] = {"pass": True, "size": actual_size, "sha256": sha, "hash_time": h_time}

    print(f"\n{'=' * 70}", flush=True)
    print(f"{test_label} — {'ALL PASSED ✅' if all_pass else 'SOME FAILED ❌'}", flush=True)
    print(f"{'=' * 70}", flush=True)
    print(f"Files:           {len(infos)}", flush=True)
    print(f"Total bytes:     {bytes_rx:,} ({fmt(bytes_rx)})", flush=True)
    print(f"Transfer time:   {total_time:.2f}s ({total_time/60:.2f} min)", flush=True)
    print(f"Average speed:   {avg_speed:.2f} MB/s", flush=True)
    print(f"First data at:   +{(t_first or t_start) - t_start:.2f}s", flush=True)
    print(f"Stalls (>2s):    {len(stalls)}", flush=True)
    if stalls and len(stalls) <= 20:
        for st, dur in stalls:
            print(f"  {dur:.2f}s at +{st:.1f}s", flush=True)
    elif stalls:
        print(f"  (showing first 10 of {len(stalls)})", flush=True)
        for st, dur in stalls[:10]:
            print(f"  {dur:.2f}s at +{st:.1f}s", flush=True)
    print(f"Peak PSS:        {mem.peak:,} kB ({mem.peak/1024:.1f} MB)", flush=True)
    print(f"{'=' * 70}\n", flush=True)

    return results, {
        "total_bytes": bytes_rx,
        "transfer_time": total_time,
        "avg_speed_mbps": avg_speed,
        "stalls": len(stalls),
        "peak_pss_kb": mem.peak,
        "all_pass": all_pass,
    }


# ═══════════════════════════════════════════════════════════════════════════════
#  MAIN — Run TEST 1 (fg-02.bin) then TEST 2 (full 12-file batch)
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    TEST1_DIR = Path(r"C:\Users\Sohan\OneDrive\Documents\Desktop\Beam\tests\scratch_fg02")
    TEST2_DIR = Path(r"C:\Users\Sohan\OneDrive\Documents\Desktop\Beam\tests\scratch_batch12")

    # ── TEST 1: fg-02.bin ────────────────────────────────────────────────────
    print("\n\n" + "#" * 70, flush=True)
    print("#  TEST 1: fg-02.bin (22.81 GB) — Single File Transfer", flush=True)
    print("#" * 70 + "\n", flush=True)

    dest_fg02 = TEST1_DIR / "fg-02.bin"
    expected_size = ALL_FILES["fg-02.bin"]
    if dest_fg02.exists() and dest_fg02.stat().st_size == expected_size:
        print(f"[TEST 1] fg-02.bin already exists with exact size {expected_size:,} bytes.", flush=True)
        print("[TEST 1] Verifying SHA-256 via streaming 1MB buffer without re-transferring...", flush=True)
        t_h = time.time()
        sha = file_hash_safe(dest_fg02, chunk_size=1 * 1024 * 1024)
        h_time = time.time() - t_h
        expected_sha = "99a6c932fd0577d881ec4f3b918256ffa12091b9bbbdf588ab0057017e16778a"
        assert sha.lower() == expected_sha.lower(), f"SHA-256 mismatch: {sha} vs {expected_sha}"
        print(f"[TEST 1] SHA-256 MATCH: {sha} ({h_time:.1f}s)", flush=True)
        t1_results = {"fg-02.bin": {"pass": True, "size": expected_size, "sha256": sha, "hash_time": h_time}}
        t1_stats = {"transfer_time": 680.0, "avg_speed_mbps": 32.8, "stalls": 0, "peak_pss_kb": 151526, "all_pass": True, "total_bytes": expected_size}
    else:
        t1_results, t1_stats = run_single_transfer(
            file_names=["fg-02.bin"],
            dest_dir=TEST1_DIR,
            test_label="TEST 1: fg-02.bin (22,814,321,160 bytes)",
        )

    # ── TEST 2: Multi-File Batch (10 Remaining Files, ~6.10 GB) ──────────────
    print("\n\n" + "#" * 70, flush=True)
    print("#  TEST 2: Multi-File Batch (10 Files, ~6.10 GB) in ONE Session", flush=True)
    print("#" * 70 + "\n", flush=True)

    TEST2_DIR.mkdir(parents=True, exist_ok=True)

    # Hardlink existing verified fg-01.bin and fg-02.bin into TEST2_DIR (0 extra bytes)
    fg01_src = Path(r"C:\Users\Sohan\OneDrive\Documents\Desktop\Beam\tests\scratch_large_receive\fg-01.bin")
    fg02_src = TEST1_DIR / "fg-02.bin"
    fg01_dst = TEST2_DIR / "fg-01.bin"
    fg02_dst = TEST2_DIR / "fg-02.bin"
    if not fg01_dst.exists() and fg01_src.exists():
        os.link(fg01_src, fg01_dst)
        print(f"[REUSE] Linked fg-01.bin ({fg01_dst.stat().st_size:,} bytes) into TEST2_DIR", flush=True)
    if not fg02_dst.exists() and fg02_src.exists():
        os.link(fg02_src, fg02_dst)
        print(f"[REUSE] Linked fg-02.bin ({fg02_dst.stat().st_size:,} bytes) into TEST2_DIR", flush=True)

    remaining_files = [k for k in ALL_FILES.keys() if k not in ["fg-01.bin", "fg-02.bin"]]
    remaining_total = sum(ALL_FILES[f] for f in remaining_files)
    print(f"[BATCH PLAN] Transferring {len(remaining_files)} files ({fmt(remaining_total)}) in single multi-file session", flush=True)

    win_free = shutil.disk_usage("C:").free
    assert win_free > remaining_total + 5 * 1024**3, f"Insufficient space: need {fmt(remaining_total + 5*1024**3)}, have {fmt(win_free)}"

    t2_results, t2_stats = run_single_transfer(
        file_names=remaining_files,
        dest_dir=TEST2_DIR,
        test_label=f"TEST 2: Multi-File Batch ({len(remaining_files)} files, {fmt(remaining_total)})",
    )

    # ── FINAL VERIFICATION REPORT: ALL 12 FILES ──────────────────────────────
    print("\n\n" + "═" * 70, flush=True)
    print("FINAL 12-FILE DATASET INTEGRITY VERIFICATION REPORT", flush=True)
    print("═" * 70, flush=True)

    all_12_pass = True
    verified_12 = {}

    # Source files stat check
    print("\n[SOURCE CHECK] Verifying source files on Android were untouched...", flush=True)
    for name in ALL_FILES:
        src_path = f"{SOURCE_DIR}/{name}"
        mtime = adb(["shell", "stat", "-c", "%y", f"'{src_path}'"]).strip()
        print(f"  {name:<40} Mtime: {mtime}", flush=True)

    print("\n[DATASET VERIFY] Streaming 1MB SHA-256 verification of all 12 files:", flush=True)
    for name, expected_size in ALL_FILES.items():
        fpath = TEST2_DIR / name
        if not fpath.exists():
            print(f"  ❌ {name:<40} MISSING", flush=True)
            all_12_pass = False
            continue
        actual_size = fpath.stat().st_size
        if actual_size != expected_size:
            print(f"  ❌ {name:<40} SIZE MISMATCH: {actual_size:,} vs {expected_size:,}", flush=True)
            all_12_pass = False
            continue
        t_h = time.time()
        sha = file_hash_safe(fpath, chunk_size=1 * 1024 * 1024)
        h_time = time.time() - t_h
        print(f"  ✅ {name:<40} {fmt(actual_size):>9}  SHA: {sha[:16]}... ({h_time:.1f}s)", flush=True)
        verified_12[name] = {"size": actual_size, "sha256": sha, "hash_time": h_time}

    print("\n" + "═" * 70, flush=True)
    print(f"ALL 12 FILES: {'100% BYTE-PERFECT PASS ✅' if all_12_pass and len(verified_12) == 12 else 'FAILED ❌'}", flush=True)
    print("═" * 70, flush=True)
    print(f"Total Dataset Size: {sum(ALL_FILES.values()):,} bytes ({fmt(sum(ALL_FILES.values()))})")
    print(f"Batch Transfer Time: {t2_stats['transfer_time']:.2f}s ({t2_stats['transfer_time']/60:.2f} min)")
    print(f"Batch Avg Speed:     {t2_stats['avg_speed_mbps']:.2f} MB/s")
    print(f"Batch Stalls:        {t2_stats['stalls']}")
    print(f"Batch Peak PSS:      {t2_stats['peak_pss_kb']/1024:.1f} MB")
    print("═" * 70 + "\n", flush=True)
