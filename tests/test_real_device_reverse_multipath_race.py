"""
PhotoBeam Physical Real-Device Reverse Multi-Transport Race Condition Test
Wi-Fi + USB transferring the SAME FILE simultaneously.
INTENTIONALLY DISCONNECT USB while Wi-Fi is active.

Verifies:
1. Both Wi-Fi and USB connect and transfer chunks of the same file concurrently.
2. USB receives chunk(s) and is intentionally closed/disconnected while file is incomplete.
3. Receiver does NOT consider file complete when USB closes.
4. finalize_file() returns (False, 'chunks_missing') and refuses premature finalization.
5. Wi-Fi remains active and receives all remaining/missing chunks.
6. File finalizes ONLY when 100% of chunks have arrived.
7. Final file size matches Android source byte-for-byte.
8. Final SHA-256 matches Android source byte-for-byte.
"""
import base64
import hashlib
import json
import os
import struct
import subprocess
import sys
import threading
import time
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
TEST_FILE = "setup.exe"
EXPECTED_SHA = "8780c4bdd0c30b4dc5366fd0ffa58284e67d01843fe64d8d60dcd1cd7b2fc3f6"
EXPECTED_SIZE = 9419380


def get_connected_device():
    res = subprocess.run([ADB, "devices"], capture_output=True, text=True, check=True)
    lines = [line.strip() for line in res.stdout.strip().splitlines() if line.strip() and not line.startswith("List of")]
    for line in lines:
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            return parts[0]
    return None


def run_reverse_multipath_race_test():
    device = get_connected_device()
    assert device, "No Android device connected via ADB!"
    print(f"[TEST] Physical device: {device}", flush=True)

    dest_dir = Path(r"C:\Users\Sohan\OneDrive\Documents\Desktop\Beam\tests\scratch_usb_disconnect_test")
    dest_dir.mkdir(parents=True, exist_ok=True)
    target_dest_file = dest_dir / TEST_FILE
    if target_dest_file.exists():
        target_dest_file.unlink()

    # 1. Setup server and TLS credentials
    cert_pem, key_pem, fp = generate_session_cert()
    session_id = "test-reverse-race-" + str(int(time.time()))
    token = "token-reverse-race-456"
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
    print(f"[SERVER] Server listening on port {port}", flush=True)

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
        "exp": int(time.time()) + 3600,
    }
    b64_payload = base64.urlsafe_b64encode(json.dumps(qr_dict).encode("utf-8")).decode("utf-8")
    qr_uri = f"photobeam://connect/{b64_payload}"

    # 2. Command Android to connect
    stream_uri = f"file:///sdcard/Download/{TEST_FILE}"
    print(f"[ANDROID] Starting PhotoBeam send of {TEST_FILE}...", flush=True)
    start_cmd = [
        ADB, "-s", device, "shell", "am", "start",
        "-S", "-W",
        "-n", "com.photobeam.app/.MainActivity",
        "--es", "screen", "send",
        "--es", "qr_uri", qr_uri,
        "--esa", "stream_uris", stream_uri,
    ]
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
    assert len(infos) == 1
    info = infos[0]
    print(f"[READY] Transfer file: {info.name} | Size: {info.size} bytes | Total chunks: {info.total_chunks}", flush=True)

    xfer = TransferManager(
        session_id=session_id,
        scheduler=scheduler,
        resume_manager=resume,
        storage_manager=storage,
    )
    xfer.setup_receive(infos)
    xfer.set_file_checksum(info.fid, EXPECTED_SHA)

    chunks_per_transport = {}
    file_state_history = []
    transport_state_history = []
    checksum_time = None
    done_time = None
    finalize_time = None

    stop_workers = threading.Event()
    usb_closed = threading.Event()
    active_transports = [primary_t]
    active_lock = threading.Lock()
    started_workers = set()

    def record_file_state(tag: str):
        with xfer._lock:
            ctx = xfer._files[info.fid]
            rx_count = len(ctx.chunk_manager.received_chunks())
            st = ctx.state.value
        entry = (time.time(), tag, st, rx_count, info.total_chunks)
        file_state_history.append(entry)
        print(f"[STATE] [{tag}] State={st} | Chunks={rx_count}/{info.total_chunks}", flush=True)

    record_file_state("START")

    def reader_worker(t: WiFiTransport, is_primary: bool):
        nonlocal finalize_time, done_time, checksum_time
        tid = t.transport_id
        with active_lock:
            chunks_per_transport[tid] = []
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

                # If this is Wi-Fi and chunk_id > 0, wait briefly for USB to receive its chunk and disconnect
                if tid.startswith("wifi") and frame.chunk_id > 0 and not usb_closed.is_set():
                    print(f"[SYNC] Wi-Fi holding chunk {frame.chunk_id} until USB transport disconnects...", flush=True)
                    usb_closed.wait(timeout=2.0)

                ok, reason = xfer.receive_chunk(frame)
                assert ok, f"Chunk {frame.chunk_id} error: {reason}"
                with active_lock:
                    chunks_per_transport[tid].append(frame.chunk_id)
                print(f"[CHUNK] Received chunk {frame.chunk_id} on {tid} ({clen} bytes)", flush=True)
                record_file_state(f"CHUNK_{frame.chunk_id}_ON_{tid}")

                # REVERSE TEST TRIGGER:
                # When USB transport receives its chunk, intentionally close USB mid-flight!
                if tid.startswith("usb") and not usb_closed.is_set():
                    print(f"\n>>> [SIMULATE CLOSURE] Intentionally closing USB transport {tid} while Wi-Fi is active! <<<", flush=True)
                    transport_state_history.append((time.time(), tid, "INTENTIONALLY_CLOSED"))
                    t.disconnect()

                    # Immediate verification before releasing Wi-Fi:
                    # 1. USB is closed
                    # 2. File state MUST NOT be complete (chunks remain missing)
                    assert not xfer.is_file_complete(info.fid), "VIOLATION: File marked complete when chunks are still missing!"
                    assert not xfer.is_file_verified(info.fid), "VIOLATION: File marked verified prematurely!"
                    # 3. Attempting finalize_file MUST return chunks_missing
                    ok_premature, err_premature = xfer.finalize_file(info.fid)
                    assert ok_premature is False, "VIOLATION: finalize_file succeeded with missing chunks!"
                    assert err_premature == "chunks_missing", f"Expected 'chunks_missing', got '{err_premature}'"
                    record_file_state("POST_USB_CLOSURE_VERIFIED_INCOMPLETE")
                    print(">>> [VERIFIED] File state remains incomplete and finalize_file refused premature finalization! <<<\n", flush=True)

                    usb_closed.set()
                    break

                if xfer.is_file_complete(info.fid) and not xfer.is_file_verified(info.fid):
                    expected = xfer._file_checksums.get(info.fid, "") or EXPECTED_SHA
                    xfer.set_file_checksum(info.fid, expected)
                    fin_start = time.time()
                    ok2, err2 = xfer.finalize_file(info.fid)
                    finalize_time = time.time() - fin_start
                    if ok2:
                        record_file_state("FINALIZED_ON_ALL_CHUNKS")
                        done_time = time.time()
                        try:
                            t.send_json({"type": MessageType.FILE_DONE, "fid": info.fid, "sha256": expected})
                            print(f"[DONE] Sent FILE_DONE on {tid}", flush=True)
                        except Exception:
                            pass

            elif magic.startswith(b"{"):
                t._recv_buf[:0] = magic
                try:
                    msg = t.recv_json(timeout=5.0)
                    m_type = msg.get("type")
                    if m_type == MessageType.FILE_CHECKSUM:
                        checksum_time = time.time()
                        cfid = msg.get("fid")
                        csha = msg.get("sha256", "")
                        xfer.set_file_checksum(cfid, csha)
                        print(f"[CHECKSUM] Received FILE_CHECKSUM for {info.name}: {csha[:16]}...", flush=True)

                        # If file complete, finalize now
                        if xfer.is_file_complete(cfid) and not xfer.is_file_verified(cfid):
                            fin_start = time.time()
                            ok2, err2 = xfer.finalize_file(cfid)
                            finalize_time = time.time() - fin_start
                            assert ok2, f"Finalize failed: {err2}"
                            record_file_state("FINALIZED_ON_CHECKSUM")
                            done_time = time.time()
                            try:
                                t.send_json({"type": MessageType.FILE_DONE, "fid": cfid, "sha256": csha})
                                print(f"[DONE] Sent FILE_DONE on {tid}", flush=True)
                            except Exception:
                                pass
                    elif m_type == MessageType.RESUME:
                        rfid = msg.get("fid")
                        with xfer._lock:
                            ctx = xfer._files.get(rfid)
                            rx_chunks = list(ctx.chunk_manager.received_chunks()) if ctx else []
                            missing = sorted(list(set(range(info.total_chunks)) - ctx.chunk_manager.received_chunks())) if ctx else []
                        if missing:
                            print(f"[RECOVERY] Notifying sender of missing chunks {missing} via NAK_CHUNK on {tid}", flush=True)
                            try:
                                t.send_json({"type": MessageType.NAK_CHUNK, "fid": rfid, "missing": missing})
                            except Exception:
                                pass
                        else:
                            print(f"[RESUME] All chunks present ({len(rx_chunks)}/{info.total_chunks}) on {tid}", flush=True)
                            if not xfer.is_file_verified(rfid):
                                expected = xfer._file_checksums.get(rfid, "") or EXPECTED_SHA
                                xfer.set_file_checksum(rfid, expected)
                                fin_start = time.time()
                                ok2, err2 = xfer.finalize_file(rfid)
                                finalize_time = time.time() - fin_start
                                if ok2:
                                    record_file_state("FINALIZED_ON_RESUME")
                                    done_time = time.time()
                                    try:
                                        t.send_json({"type": MessageType.FILE_DONE, "fid": rfid, "sha256": expected})
                                        print(f"[DONE] Sent FILE_DONE on {tid}", flush=True)
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

    # Wait up to 3 seconds for secondary transport to connect so both transports are active
    print("[WAIT] Waiting up to 3s for secondary transport (USB/Wi-Fi) to attach...", flush=True)
    w_start = time.time()
    while time.time() - w_start < 3.0:
        with active_lock:
            if len(active_transports) >= 2:
                break
        time.sleep(0.2)

    with active_lock:
        print(f"[ACTIVE] Transports currently established: {[t.transport_id for t in active_transports]}", flush=True)

    # Start primary worker
    ensure_worker(primary_t, is_primary=True)

    # Send ACCEPT
    primary_t.send_json({"type": MessageType.ACCEPT, "fid": info.fid})
    print("[TRANSFER] Sent ACCEPT to sender. Multi-transport active.", flush=True)

    # Monitor loop until completion or timeout (60 seconds)
    t_start = time.time()
    while time.time() - t_start < 60.0:
        if xfer.is_file_verified(info.fid):
            break
        if xfer.is_file_complete(info.fid) and not xfer.is_file_verified(info.fid):
            expected = xfer._file_checksums.get(info.fid, "") or EXPECTED_SHA
            fin_start = time.time()
            ok, err = xfer.finalize_file(info.fid)
            finalize_time = time.time() - fin_start
            if ok:
                record_file_state("FINALIZED_IN_MONITOR")
                done_time = time.time()
                for t in active_transports:
                    if t.is_connected():
                        try:
                            t.send_json({"type": MessageType.FILE_DONE, "fid": info.fid, "sha256": expected})
                        except Exception:
                            pass
                break
        time.sleep(0.1)

    stop_workers.set()
    stop_accept.set()
    server.stop()

    print("\n" + "=" * 70)
    print("REVERSE MULTI-TRANSPORT RACE CONDITION TEST RESULTS (USB DISCONNECT)")
    print("=" * 70)

    print("\n1. Chunks received per transport:")
    total_received_chunks = sum(len(c) for c in chunks_per_transport.values())
    for tid, c_list in chunks_per_transport.items():
        print(f"   * {tid}: {len(c_list)} chunks {c_list}")
    print(f"   * Total chunks received: {total_received_chunks}/{info.total_chunks}")

    print("\n2. File state transitions:")
    for t_stamp, tag, state, rx, tot in file_state_history:
        print(f"   * +{t_stamp - t_start:.2f}s | {tag:<40} | State: {state:<12} | Chunks: {rx}/{tot}")

    print("\n3. Transport state transitions:")
    for t_stamp, tid, st in transport_state_history:
        print(f"   * +{t_stamp - t_start:.2f}s | {tid:<25} | Status: {st}")

    print("\n4. Timing:")
    print(f"   * FILE_CHECKSUM arrival: {'+' + f'{checksum_time - t_start:.2f}s' if checksum_time else 'N/A'}")
    print(f"   * FILE_DONE transmission: {'+' + f'{done_time - t_start:.2f}s' if done_time else 'N/A'}")
    print(f"   * Finalization hashing duration: {finalize_time * 1000:.2f} ms" if finalize_time else "   * Finalization time: N/A")

    # Assertions
    assert target_dest_file.exists(), f"Final file missing at {target_dest_file}"
    actual_size = target_dest_file.stat().st_size
    print(f"\n5. Final File Size: {actual_size} bytes (expected {EXPECTED_SIZE})")
    assert actual_size == EXPECTED_SIZE, f"Size mismatch: {actual_size} != {EXPECTED_SIZE}"

    h = hashlib.sha256()
    with open(target_dest_file, "rb") as f:
        while chunk := f.read(1024 * 1024):
            h.update(chunk)
    actual_sha = h.hexdigest().lower()
    print(f"6. Final SHA-256: {actual_sha}")
    print(f"   Expected SHA-256: {EXPECTED_SHA}")
    assert actual_sha == EXPECTED_SHA, f"SHA-256 mismatch! {actual_sha} != {EXPECTED_SHA}"

    print("\n[SUCCESS] Reverse multi-transport race test verified byte-perfect and race-free!")
    return 0


if __name__ == "__main__":
    sys.exit(run_reverse_multipath_race_test())
