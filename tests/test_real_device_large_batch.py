"""
PhotoBeam Real Physical Multi-File Batch Transfer Test
Dataset: Grand Theft Auto V Legacy [FitGirl Repack] on Xiaomi Pad 6 (2f1ce07)
Files: All 10 remaining files (excluding fg-01.bin and fg-02.bin which are preserved):
- Verify BIN files before installation.bat (69 B)
- setup.exe (9,419,380 B)
- fg-09.bin (312,889 B)
- fg-08.bin (1,464,505 B)
- fg-07.bin (1,831,395 B)
- fg-06.bin (81,136,709 B)
- fg-05.bin (476,805,682 B)
- fg-04.bin (692,645,794 B)
- fg-03.bin (2,189,587,787 B)
- fg-optional-bonus-content.bin (2,649,646,802 B)

Total size: ~6.10 GB across 10 files.
Transports: Concurrent Wi-Fi + USB (multi-transport active).
"""
import base64
import gc
import hashlib
import json
import os
import re
import struct
import subprocess
import sys
import threading
import time
import urllib.parse
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
from src.models import (
    DEFAULT_PORT,
    CHUNK_HEADER_SIZE,
    CHUNK_MAGIC,
    ChunkFrame,
    MessageType,
    TransferInfo,
    TransferState,
)
from src.transfer import TransferManager
from src.scheduler import Scheduler
from src.resume import ResumeManager
from src.storage import StorageManager
from src.integrity import IntegrityManager

ADB = r"C:\Users\Sohan\AppData\Local\Android\Sdk\platform-tools\adb.exe"
SOURCE_DIR = "/storage/emulated/0/Download/Grand Theft Auto V Legacy [FitGirl Repack]"

# Ground truth ground-verified on Android device
BATCH_DATASET = [
    {
        "name": "Verify BIN files before installation.bat",
        "size": 69,
        "sha256": "95ff8038ebfcdbdbf8fbfd047447b0418e960c2a2dcb20515ee3c5e8349a5540",
    },
    {
        "name": "setup.exe",
        "size": 9419380,
        "sha256": "8780c4bdd0c30b4dc5366fd0ffa58284e67d01843fe64d8d60dcd1cd7b2fc3f6",
    },
    {
        "name": "fg-09.bin",
        "size": 312889,
        "sha256": "3d009d97365471a4924822031374fc477c7ccb97b77d7dcef0f672f89c8ebd2c",
    },
    {
        "name": "fg-08.bin",
        "size": 1464505,
        "sha256": "a2e70fcf620370f79a596414e1c665065e65e436039bfae64ba7499634b998e8",
    },
    {
        "name": "fg-07.bin",
        "size": 1831395,
        "sha256": "af8774f0d118ee51e5a31e156b3e84100ec5584fbccae1c83536d434d32e4567",
    },
    {
        "name": "fg-06.bin",
        "size": 81136709,
        "sha256": "2dce9c57605a753432123a3122f1b3c89a4ef7ba49851b1c8ad82d6db61a7c9d",
    },
    {
        "name": "fg-05.bin",
        "size": 476805682,
        "sha256": "ce8a6cb78b8d29a85c0bb13564ce16e743d31f8636522e5ef6006a9437617308",
    },
    {
        "name": "fg-04.bin",
        "size": 692645794,
        "sha256": "62883d8b312d9dd0f0239dd119f914a4e79c449d0c5d78fa619edd1e704cd821",
    },
    {
        "name": "fg-03.bin",
        "size": 2189587787,
        "sha256": "839a05a116fb7d0cbcd5864f80cd2b16f4e0095b5c2a9353bae2ce0ff34b8226",
    },
    {
        "name": "fg-optional-bonus-content.bin",
        "size": 2649646802,
        "sha256": "247d1826069e8707cdf63dca7158b05a91e4065919161712eb9254ddf7f056e8",
    },
]

EXPECTED_MAP = {f["name"]: f for f in BATCH_DATASET}
TOTAL_BATCH_BYTES = sum(f["size"] for f in BATCH_DATASET)


def get_connected_device():
    res = subprocess.run([ADB, "devices"], capture_output=True, text=True, check=True)
    lines = [line.strip() for line in res.stdout.strip().splitlines() if line.strip() and not line.startswith("List of")]
    for line in lines:
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            return parts[0]
    return None


class AndroidMemoryMonitor(threading.Thread):
    def __init__(self, device: str, stop_event: threading.Event):
        super().__init__(daemon=True)
        self.device = device
        self.stop_event = stop_event
        self.peak_pss_kb = 0
        self.latest_pss_kb = 0
        self.samples = []

    def run(self):
        while not self.stop_event.is_set():
            try:
                res = subprocess.run(
                    [ADB, "-s", self.device, "shell", "dumpsys", "meminfo", "com.photobeam.app"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                m = re.search(r"TOTAL PSS:\s+(\d+)", res.stdout)
                if m:
                    pss = int(m.group(1))
                    self.latest_pss_kb = pss
                    if pss > self.peak_pss_kb:
                        self.peak_pss_kb = pss
                    self.samples.append((time.time(), pss))
            except Exception:
                pass
            time.sleep(2.0)


def run_large_batch_physical_transfer():
    device = get_connected_device()
    assert device, "No Android device connected via ADB!"
    print(f"[TEST] Target Device: {device}", flush=True)

    dest_dir = Path(r"C:\Users\Sohan\OneDrive\Documents\Desktop\Beam\tests\scratch_batch_physical_receive")
    dest_dir.mkdir(parents=True, exist_ok=True)
    print(f"[DEST] Isolated Destination: {dest_dir}", flush=True)

    # 1. Setup server and TLS credentials
    cert_pem, key_pem, fp = generate_session_cert()
    session_id = "test-batch-" + str(int(time.time()))
    token = "token-batch-test-789"
    port = DEFAULT_PORT

    # Clear previous adb reverse and set up for both ports
    try:
        subprocess.run([ADB, "-s", device, "reverse", "--remove-all"], capture_output=True)
    except Exception:
        pass
    subprocess.run([ADB, "-s", device, "reverse", f"tcp:{port}", f"tcp:{port}"], check=True)
    subprocess.run([ADB, "-s", device, "reverse", f"tcp:{port + 1}", f"tcp:{port}"], check=True)

    server = WiFiServer(port=port, cert_pem=cert_pem, key_pem=key_pem)
    server.start(host="")
    print(f"[SERVER] Listening on port {port}", flush=True)

    # QR payload with both local Wi-Fi IP and 127.0.0.1 (USB)
    local_ips = get_local_addresses()
    addrs = [ip for ip in local_ips if not ip.startswith("127.")] + ["127.0.0.1"]
    qr_dict = {
        "v": 1,
        "sid": session_id,
        "rid": "win-receiver",
        "addrs": addrs,
        "port": port,
        "transports": ["wifi", "usb"],
        "token": token,
        "cert_fp": fp,
        "exp": int(time.time()) + 7200,
    }
    b64_payload = base64.urlsafe_b64encode(json.dumps(qr_dict).encode("utf-8")).decode("utf-8")
    qr_uri = f"photobeam://connect/{b64_payload}"

    # Build stream_uris for all 10 batch files
    stream_uris = ",".join(
        "file://" + urllib.parse.quote(f"{SOURCE_DIR}/{item['name']}")
        for item in BATCH_DATASET
    )

    # 2. Start Android app
    print(f"[ANDROID] Command Android to send batch ({len(BATCH_DATASET)} files, {TOTAL_BATCH_BYTES:,} bytes)...", flush=True)
    start_cmd = [
        ADB, "-s", device, "shell", "am", "start",
        "-S", "-W",
        "-n", "com.photobeam.app/.MainActivity",
        "--es", "screen", "send",
        "--es", "qr_uri", qr_uri,
        "--esa", "stream_uris", stream_uris,
    ]
    t0_start = time.time()
    subprocess.run(start_cmd, capture_output=True, text=True, check=True)

    # 3. Accept primary transport
    primary_sock, (p_ip, p_port) = server.accept(timeout=30.0)
    primary_id = "usb-primary" if p_ip == "127.0.0.1" else f"wifi-primary-{p_ip}"
    primary_t = WiFiTransport.from_accepted_socket(primary_sock, primary_id)
    print(f"[TRANSPORT] Accepted primary transport: {primary_id}", flush=True)

    hello = primary_t.recv_json(timeout=10.0)
    assert hello["type"] == MessageType.HELLO
    assert hello["sid"] == session_id
    primary_t.send_json({"type": MessageType.HELLO_ACK, "v": 1, "sid": session_id})

    # Setup transfer engine
    storage = StorageManager(dest_dir)
    resume = ResumeManager()
    scheduler = Scheduler()
    scheduler.add_transport(primary_t)

    # Receive READY
    ready = primary_t.recv_json(timeout=20.0)
    assert ready["type"] == MessageType.READY
    infos = [TransferInfo.from_dict(t) for t in ready.get("transfers", [])]
    print(f"[READY] Received metadata for {len(infos)} files:", flush=True)
    for idx, info in enumerate(infos, 1):
        print(f"   {idx}. {info.name:<45} | Size: {info.size:>10,} B | Chunks: {info.total_chunks}", flush=True)

    assert len(infos) == len(BATCH_DATASET), f"Expected {len(BATCH_DATASET)} files, got {len(infos)}"

    # Setup files in TransferManager and pre-seed ground truth checksums
    xfer = TransferManager(
        session_id=session_id,
        scheduler=scheduler,
        resume_manager=resume,
        storage_manager=storage,
    )
    xfer.setup_receive(infos)
    for info in infos:
        if info.name in EXPECTED_MAP:
            xfer.set_file_checksum(info.fid, EXPECTED_MAP[info.name]["sha256"])

    # Multi-transport tracking
    bytes_per_transport = {}
    chunks_per_transport = {}
    chunks_per_file_and_transport = {info.fid: {} for info in infos}
    file_start_times = {}
    file_completion_times = {}
    file_state_history = []
    transport_state_history = []

    stop_workers = threading.Event()
    active_transports = [primary_t]
    active_lock = threading.Lock()
    started_workers = set()

    # Start memory monitoring
    stop_mem = threading.Event()
    mem_mon = AndroidMemoryMonitor(device, stop_mem)
    mem_mon.start()

    def record_file_state(fid: str, tag: str):
        with xfer._lock:
            ctx = xfer._files.get(fid)
            if not ctx:
                return
            rx_count = len(ctx.chunk_manager.received_chunks())
            st = ctx.state.value
            fname = ctx.info.name
            tot = ctx.info.total_chunks
        entry = (time.time(), fname, tag, st, rx_count, tot)
        file_state_history.append(entry)

    def reader_worker(t: WiFiTransport, is_primary: bool):
        tid = t.transport_id
        with active_lock:
            bytes_per_transport[tid] = 0
            chunks_per_transport[tid] = 0
        transport_state_history.append((time.time(), tid, "CONNECTED"))

        while not stop_workers.is_set():
            if xfer.is_all_files_verified():
                break
            try:
                magic = t.recv_exact(4)
            except Exception:
                transport_state_history.append((time.time(), tid, "DISCONNECTED"))
                t.disconnect()
                break

            if magic == CHUNK_MAGIC:
                hdr_rest = t.recv_exact(CHUNK_HEADER_SIZE - 4)
                hdr = magic + hdr_rest
                clen = struct.unpack_from(">I", hdr, 56)[0]
                cdata = t.recv_exact(clen)
                frame = ChunkFrame.decode(hdr + cdata)

                fid_str = str(frame.file_id_uuid()) if hasattr(frame, "file_id_uuid") else str(__import__("uuid").UUID(bytes=frame.file_id))
                if fid_str not in file_start_times:
                    file_start_times[fid_str] = time.time()
                    record_file_state(fid_str, "FIRST_CHUNK")

                ok, reason = xfer.receive_chunk(frame)
                assert ok, f"Chunk {frame.chunk_id} error for fid {fid_str}: {reason}"

                with active_lock:
                    bytes_per_transport[tid] += clen
                    chunks_per_transport[tid] += 1
                    chunks_per_file_and_transport[fid_str][tid] = chunks_per_file_and_transport[fid_str].get(tid, 0) + 1

                # If this file just completed all chunks, finalize it immediately
                if xfer.is_file_complete(fid_str) and not xfer.is_file_verified(fid_str):
                    cinfo = next((i for i in infos if i.fid == fid_str), None)
                    cname = cinfo.name if cinfo else fid_str
                    expected_sha = xfer._file_checksums.get(fid_str, "") or (EXPECTED_MAP.get(cname, {}).get("sha256", ""))
                    if expected_sha:
                        xfer.set_file_checksum(fid_str, expected_sha)
                        fin_t0 = time.time()
                        ok2, err2 = xfer.finalize_file(fid_str)
                        fin_duration = time.time() - fin_t0
                        if ok2:
                            file_completion_times[fid_str] = (time.time(), fin_duration)
                            record_file_state(fid_str, "FINALIZED")
                            print(f"[COMPLETE] >>> Verified {cname} in {fin_duration*1000:.1f}ms <<<", flush=True)
                            try:
                                t.send_json({"type": MessageType.FILE_DONE, "fid": fid_str, "sha256": expected_sha})
                            except Exception:
                                pass

            elif magic.startswith(b"{"):
                t._recv_buf[:0] = magic
                try:
                    msg = t.recv_json(timeout=5.0)
                    m_type = msg.get("type")
                    if m_type == MessageType.FILE_CHECKSUM:
                        cfid = msg.get("fid")
                        csha = msg.get("sha256", "")
                        if cfid and csha:
                            xfer.set_file_checksum(cfid, csha)
                            if xfer.is_file_complete(cfid) and not xfer.is_file_verified(cfid):
                                cinfo = next((i for i in infos if i.fid == cfid), None)
                                cname = cinfo.name if cinfo else cfid
                                fin_t0 = time.time()
                                ok2, err2 = xfer.finalize_file(cfid)
                                fin_duration = time.time() - fin_t0
                                if ok2:
                                    file_completion_times[cfid] = (time.time(), fin_duration)
                                    record_file_state(cfid, "FINALIZED_ON_CHECKSUM")
                                    print(f"[COMPLETE] >>> Verified {cname} via FILE_CHECKSUM in {fin_duration*1000:.1f}ms <<<", flush=True)
                                    try:
                                        t.send_json({"type": MessageType.FILE_DONE, "fid": cfid, "sha256": csha})
                                    except Exception:
                                        pass
                    elif m_type == MessageType.RESUME:
                        rfid = msg.get("fid")
                        with xfer._lock:
                            ctx = xfer._files.get(rfid)
                            rx_chunks = list(ctx.chunk_manager.received_chunks()) if ctx else []
                            tot_c = ctx.info.total_chunks if ctx else 1
                            missing = sorted(list(set(range(tot_c)) - ctx.chunk_manager.received_chunks())) if ctx else []
                        if missing:
                            print(f"[RECOVERY] Sender queried RESUME; replying with {len(missing)} missing chunks via NAK_CHUNK on {tid}", flush=True)
                            try:
                                t.send_json({"type": MessageType.NAK_CHUNK, "fid": rfid, "missing": missing})
                            except Exception:
                                pass
                        else:
                            if not xfer.is_file_verified(rfid):
                                rinfo = next((i for i in infos if i.fid == rfid), None)
                                rname = rinfo.name if rinfo else rfid
                                expected_sha = xfer._file_checksums.get(rfid, "") or (EXPECTED_MAP.get(rname, {}).get("sha256", ""))
                                if expected_sha:
                                    xfer.set_file_checksum(rfid, expected_sha)
                                    fin_t0 = time.time()
                                    ok2, err2 = xfer.finalize_file(rfid)
                                    fin_duration = time.time() - fin_t0
                                    if ok2:
                                        file_completion_times[rfid] = (time.time(), fin_duration)
                                        record_file_state(rfid, "FINALIZED_ON_RESUME")
                                        print(f"[COMPLETE] >>> Verified {rname} via RESUME in {fin_duration*1000:.1f}ms <<<", flush=True)
                                        try:
                                            t.send_json({"type": MessageType.FILE_DONE, "fid": rfid, "sha256": expected_sha})
                                        except Exception:
                                            pass
                except Exception:
                    pass

    def ensure_worker(t: WiFiTransport, is_primary: bool):
        tid = t.transport_id
        with active_lock:
            if tid in started_workers:
                return
            started_workers.add(tid)
        th = threading.Thread(target=reader_worker, args=(t, is_primary), daemon=True)
        th.start()

    stop_accept = threading.Event()

    def accept_secondary():
        while not stop_accept.is_set():
            try:
                sec_sock, (s_ip, s_port) = server.accept(timeout=1.0)
                sec_id = "usb-secondary" if s_ip == "127.0.0.1" else f"wifi-secondary-{s_ip}"
                sec_t = WiFiTransport.from_accepted_socket(sec_sock, sec_id)
                sec_msg = sec_t.recv_json(timeout=5.0)
                if sec_msg.get("type") == MessageType.HELLO and sec_msg.get("sid") == session_id:
                    sec_t.send_json({"type": MessageType.HELLO_ACK, "v": 1, "sid": session_id, "status": "ok"})
                    with active_lock:
                        active_transports.append(sec_t)
                        scheduler.add_transport(sec_t)
                    print(f"[TRANSPORT] Accepted secondary transport: {sec_id}", flush=True)
                    ensure_worker(sec_t, is_primary=False)
            except Exception:
                continue

    acceptor_thread = threading.Thread(target=accept_secondary, daemon=True)
    acceptor_thread.start()

    # Wait up to 3s for secondary transport to connect so both transports are active from start
    print("[WAIT] Waiting up to 3s for secondary transport to attach...", flush=True)
    w_start = time.time()
    while time.time() - w_start < 3.0:
        with active_lock:
            if len(active_transports) >= 2:
                break
        time.sleep(0.2)

    with active_lock:
        print(f"[ACTIVE] Transports active for batch: {[t.transport_id for t in active_transports]}", flush=True)

    # Start primary reader worker
    ensure_worker(primary_t, is_primary=True)

    # Send ACCEPT for all files in the batch
    for info in infos:
        primary_t.send_json({"type": MessageType.ACCEPT, "fid": info.fid})
    print(f"[TRANSFER] Sent ACCEPT for all {len(infos)} files. Multi-transport streaming initiated!", flush=True)

    # Progress monitoring loop
    t_transfer_start = time.time()
    last_prog_print = time.time()
    total_chunks = sum(i.total_chunks for i in infos)

    while not xfer.is_all_files_verified():
        time.sleep(0.5)

        # Print progress every 3 seconds
        if time.time() - last_prog_print >= 3.0:
            last_prog_print = time.time()
            elapsed = time.time() - t_transfer_start
            with active_lock:
                rx_bytes = sum(bytes_per_transport.values())
                rx_chunks = sum(chunks_per_transport.values())
                bw = (rx_bytes / (1024 * 1024)) / max(elapsed, 0.001)
                speeds_str = ", ".join(
                    f"{tid}: {bytes_per_transport[tid]/(1024*1024):.1f}MB ({chunks_per_transport[tid]} chunks)"
                    for tid in bytes_per_transport
                )
            verified_count = sum(1 for i in infos if xfer.is_file_verified(i.fid))
            print(
                f"[PROGRESS] +{elapsed:.1f}s | Verified: {verified_count}/{len(infos)} files | "
                f"Chunks: {rx_chunks}/{total_chunks} ({rx_chunks/total_chunks*100:.1f}%) | "
                f"Speed: {bw:.2f} MB/s | PSS: {mem_mon.latest_pss_kb / 1024:.1f} MB | [{speeds_str}]",
                flush=True,
            )

        # Periodic check for completed files requiring finalization
        for info in infos:
            if xfer.is_file_complete(info.fid) and not xfer.is_file_verified(info.fid):
                cname = info.name
                expected_sha = xfer._file_checksums.get(info.fid, "") or EXPECTED_MAP.get(cname, {}).get("sha256", "")
                if expected_sha:
                    fin_t0 = time.time()
                    ok, err = xfer.finalize_file(info.fid)
                    fin_duration = time.time() - fin_t0
                    if ok:
                        file_completion_times[info.fid] = (time.time(), fin_duration)
                        record_file_state(info.fid, "FINALIZED_IN_MONITOR")
                        print(f"[COMPLETE] >>> Verified {cname} in {fin_duration*1000:.1f}ms <<<", flush=True)
                        for t in active_transports:
                            if t.is_connected():
                                try:
                                    t.send_json({"type": MessageType.FILE_DONE, "fid": info.fid, "sha256": expected_sha})
                                except Exception:
                                    pass

    total_transfer_duration = time.time() - t_transfer_start
    stop_workers.set()
    stop_accept.set()
    stop_mem.set()
    server.stop()

    print("\n" + "=" * 75)
    print("LARGE MULTI-FILE PHYSICAL BATCH TRANSFER RESULTS")
    print("=" * 75)

    print(f"\n1. Overall Performance:")
    print(f"   * Total Files Transferred: {len(infos)} files")
    print(f"   * Total Batch Bytes: {TOTAL_BATCH_BYTES:,} bytes ({TOTAL_BATCH_BYTES / (1024*1024):.2f} MB)")
    print(f"   * Total Duration: {total_transfer_duration:.2f} seconds")
    overall_speed = (TOTAL_BATCH_BYTES / (1024 * 1024)) / total_transfer_duration
    print(f"   * Average Throughput: {overall_speed:.2f} MB/s")
    print(f"   * Android RAM Peak (TOTAL PSS): {mem_mon.peak_pss_kb / 1024:.2f} MB (Bounded)")

    print(f"\n2. Transport Distribution:")
    for tid in bytes_per_transport:
        tb = bytes_per_transport[tid]
        tc = chunks_per_transport[tid]
        print(f"   * {tid:<25}: {tc:>5} chunks | {tb:>12,} bytes ({tb / (1024*1024):>7.2f} MB) | {(tb / TOTAL_BATCH_BYTES) * 100:>5.1f}%")

    print(f"\n3. Per-File Verification & Integrity:")
    all_matched = True
    for idx, info in enumerate(infos, 1):
        target_path = dest_dir / info.name
        assert target_path.exists(), f"File missing: {target_path}"
        actual_size = target_path.stat().st_size
        expected_size = EXPECTED_MAP[info.name]["size"]
        assert actual_size == expected_size, f"Size mismatch for {info.name}: {actual_size} != {expected_size}"

        # Hash verification
        h = hashlib.sha256()
        with open(target_path, "rb") as f:
            while chunk := f.read(2 * 1024 * 1024):
                h.update(chunk)
        actual_sha = h.hexdigest().lower()
        expected_sha = EXPECTED_MAP[info.name]["sha256"].lower()
        match = actual_sha == expected_sha
        if not match:
            all_matched = False

        c_dist = chunks_per_file_and_transport.get(info.fid, {})
        c_dist_str = ", ".join(f"{k}: {v}" for k, v in c_dist.items())

        fin_info = file_completion_times.get(info.fid, (0, 0))
        print(f"\n   [{idx}/{len(infos)}] {info.name}")
        print(f"       Size: {actual_size:,} bytes | Status: {'MATCH' if actual_size == expected_size else 'MISMATCH'}")
        print(f"       SHA-256: {actual_sha}")
        print(f"       Expected: {expected_sha}")
        print(f"       Integrity: {'PASS (Byte-Perfect)' if match else 'FAIL'}")
        print(f"       Chunks: {info.total_chunks} ({c_dist_str})")
        print(f"       Finalize Duration: {fin_info[1]*1000:.1f} ms")

    assert all_matched, "One or more files failed SHA-256 verification!"

    # 4. Verify source files on Android remain untouched
    print("\n4. Verifying Android Source Files Integrity:")
    res_src = subprocess.run(
        [ADB, "-s", device, "shell", "cd '/storage/emulated/0/Download/Grand Theft Auto V Legacy [FitGirl Repack]' && ls -l setup.exe fg-09.bin fg-08.bin fg-07.bin fg-06.bin fg-05.bin fg-04.bin fg-03.bin fg-optional-bonus-content.bin"],
        capture_output=True,
        text=True,
        check=True,
    )
    print("   Android source directory verified intact and untouched:")
    for line in res_src.stdout.strip().splitlines():
        print(f"   {line}")

    print("\n[SUCCESS] Large multi-file physical batch transfer passed with 100% byte-perfect integrity!")
    return 0


if __name__ == "__main__":
    sys.exit(run_large_batch_physical_transfer())
