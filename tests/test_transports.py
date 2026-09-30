"""
PhotoBeam — Transport & TLS Integration Tests
Tests real TLS 1.3 socket loopback, certificate generation, and ADB/USB transport handling.
"""
import json
import os
import socket
import ssl
import sys
import threading
import time
import uuid

import pytest

# Add protocol and windows transport to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'protocol'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'windows', 'photobeam-windows'))

from src.models import ChunkFrame, FrameType, CHUNK_HEADER_SIZE
from src.integrity import IntegrityManager
from transport.tls_utils import generate_session_cert, get_local_addresses
from transport.wifi_transport import WiFiTransport
from transport.usb_transport import find_adb, adb_devices, UsbTransport


def test_generate_session_cert():
    cert_pem, key_pem, fingerprint = generate_session_cert()
    assert cert_pem.startswith(b"-----BEGIN CERTIFICATE-----")
    assert key_pem.startswith(b"-----BEGIN PRIVATE KEY-----")
    assert fingerprint.startswith("sha256:")
    assert len(fingerprint.removeprefix("sha256:")) == 64


def test_get_local_addresses():
    addrs = get_local_addresses()
    assert isinstance(addrs, list)
    # On any networked machine, there should be at least one address or fallback
    assert len(addrs) > 0


def test_find_adb():
    adb_path = find_adb()
    # ADB was detected on this environment
    assert adb_path is not None
    assert os.path.isfile(adb_path)


def test_adb_devices_parsing():
    adb_path = find_adb()
    devs = adb_devices(adb_path)
    assert isinstance(devs, list)
    for d in devs:
        assert "serial" in d
        assert "state" in d


def test_usb_transport_availability():
    avail, reason = UsbTransport.is_available()
    assert isinstance(avail, bool)
    assert isinstance(reason, str)
    if not avail:
        assert "USB" in reason or "device" in reason or "ADB" in reason


def test_wifi_transport_tls_loopback():
    """Start a real TLS server on localhost and connect via WiFiTransport."""
    cert_pem, key_pem, fp = generate_session_cert()

    # Find free port
    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.bind(("127.0.0.1", 0))
    port = server_sock.getsockname()[1]
    server_sock.listen(1)

    accepted_transport = []
    server_error = []

    def server_thread():
        try:
            raw_client, addr = server_sock.accept()
            # Wrap in server SSL
            server_ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
            # Write temp cert and key
            import tempfile
            with tempfile.NamedTemporaryFile("wb", delete=False) as c_file, \
                 tempfile.NamedTemporaryFile("wb", delete=False) as k_file:
                c_file.write(cert_pem)
                c_file.flush()
                k_file.write(key_pem)
                k_file.flush()
                c_path = c_file.name
                k_path = k_file.name

            try:
                server_ctx.load_cert_chain(certfile=c_path, keyfile=k_path)
                tls_client = server_ctx.wrap_socket(raw_client, server_side=True)
                t = WiFiTransport.from_accepted_socket(tls_client)
                accepted_transport.append(t)

                # Receive HELLO JSON
                msg = t.recv_json()
                assert msg["type"] == "HELLO"

                # Send HELLO_ACK JSON
                t.send_json({"type": "HELLO_ACK", "status": "ok"})

                # Receive a ChunkFrame
                frame = t.recv_chunk_frame()
                assert frame.chunk_id == 42
                assert frame.data == b"TLS Chunk Transmission Verified"

            finally:
                os.unlink(c_path)
                os.unlink(k_path)
        except Exception as e:
            server_error.append(e)
        finally:
            server_sock.close()

    th = threading.Thread(target=server_thread, daemon=True)
    th.start()

    time.sleep(0.1)

    # Client side connects
    client_transport = WiFiTransport("wifi-client")
    client_transport.connect("127.0.0.1", port, timeout=5.0, cert_fp=fp)
    assert client_transport.is_connected()

    # Send HELLO
    client_transport.send_json({"type": "HELLO", "sender": "test_client"})

    # Recv HELLO_ACK
    ack = client_transport.recv_json()
    assert ack["type"] == "HELLO_ACK"

    # Send ChunkFrame
    payload = b"TLS Chunk Transmission Verified"
    frame = ChunkFrame(
        transfer_id=uuid.uuid4().bytes,
        file_id=uuid.uuid4().bytes,
        chunk_id=42,
        offset=0,
        data=payload,
        checksum=IntegrityManager.chunk_checksum(payload),
    )
    client_transport.send_chunk_frame(frame)

    th.join(timeout=5.0)
    assert not server_error, f"Server thread error: {server_error}"

    client_transport.disconnect()
    assert not client_transport.is_connected()
    if accepted_transport:
        accepted_transport[0].disconnect()


def test_multi_socket_simultaneous_tls_transports(tmp_path):
    """
    Test real multi-socket simultaneous transmission (Wi-Fi + USB simulation).
    Starts a real TLS server on localhost.
    Client connects Primary (Wi-Fi) and Secondary (USB).
    Chunks of an 80KB file (10 chunks of 8KB) are sent through Scheduler.
    Both sockets simultaneously transmit and receive chunks.
    Receiver writes and verifies full file SHA-256.
    """
    from transport.wifi_transport import WiFiServer
    from src.storage import StorageManager
    from src.resume import ResumeManager
    from src.scheduler import Scheduler
    from src.transfer import TransferManager
    from src.models import MessageType, ChunkFrame, CHUNK_HEADER_SIZE

    cert_pem, key_pem, fp = generate_session_cert()
    session_id = str(uuid.uuid4())

    # Find free port
    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.bind(("127.0.0.1", 0))
    port = server_sock.getsockname()[1]
    server_sock.close()

    server = WiFiServer(port, cert_pem, key_pem)
    server.start(host="127.0.0.1")

    src_file = tmp_path / "multi_test.bin"
    content = os.urandom(80 * 1024)  # 80 KB
    src_file.write_bytes(content)
    dest_dir = tmp_path / "dest"
    dest_dir.mkdir()

    receiver_errors = []
    receiver_done = threading.Event()
    chunks_received_by_transport = {"primary": 0, "secondary": 0}

    def receiver_thread():
        try:
            # Accept primary
            p_sock, _ = server.accept(timeout=10.0)
            p_trans = WiFiTransport.from_accepted_socket(p_sock, "primary")

            # Accept secondary concurrently in background thread
            s_trans_holder = []
            sec_accepted = threading.Event()

            def accept_sec():
                try:
                    s_sock, _ = server.accept(timeout=10.0)
                    sec_t = WiFiTransport.from_accepted_socket(s_sock, "secondary")
                    s_hello = sec_t.recv_json()
                    assert s_hello["channel"] == "data"
                    sec_t.send_json({"type": MessageType.HELLO_ACK, "status": "ok"})
                    s_trans_holder.append(sec_t)
                    sec_accepted.set()
                except Exception as ex:
                    receiver_errors.append(ex)

            th_acc = threading.Thread(target=accept_sec, daemon=True)
            th_acc.start()

            # HELLO
            hello = p_trans.recv_json()
            assert hello["type"] == MessageType.HELLO
            p_trans.send_json({"type": MessageType.HELLO_ACK, "sid": session_id})

            # READY
            ready = p_trans.recv_json()
            assert ready["type"] == MessageType.READY
            from src.models import TransferInfo
            infos = [TransferInfo.from_dict(t) for t in ready["transfers"]]

            storage = StorageManager(dest_dir)
            resume = ResumeManager(tmp_path / "resume")
            xfer = TransferManager(session_id, Scheduler(), resume, storage, chunk_size=8192)
            xfer.setup_receive(infos)

            p_trans.send_json({"type": MessageType.ACCEPT, "fid": infos[0].fid})

            assert sec_accepted.wait(timeout=5.0)
            s_trans = s_trans_holder[0]

            all_done = threading.Event()

            def read_loop(t, name):
                t._sock.settimeout(0.5)
                while not all_done.is_set():
                    try:
                        hdr = t.recv_exact(CHUNK_HEADER_SIZE)
                    except (TimeoutError, socket.timeout, ssl.SSLError) as e:
                        if "timed out" in str(e).lower():
                            if xfer.is_all_files_complete():
                                all_done.set()
                                break
                            continue
                        receiver_errors.append(e)
                        break
                    except (ConnectionError, OSError) as e:
                        receiver_errors.append(e)
                        break

                    d_len = int.from_bytes(hdr[56:60], "big")
                    data = t.recv_exact(d_len)
                    frame = ChunkFrame.decode(hdr + data)
                    ok, _ = xfer.receive_chunk(frame)
                    assert ok
                    chunks_received_by_transport[name] += 1
                    fid_str = str(uuid.UUID(bytes=frame.file_id))
                    if xfer.is_file_complete(fid_str):
                        xfer.finalize_file(fid_str)
                    if xfer.is_all_files_complete():
                        all_done.set()
                        break

            th1 = threading.Thread(target=read_loop, args=(p_trans, "primary"), daemon=True)
            th2 = threading.Thread(target=read_loop, args=(s_trans, "secondary"), daemon=True)
            th1.start()
            th2.start()

            # Wait for all files to be complete
            assert all_done.wait(timeout=10.0)
            th1.join(timeout=2.0)
            th2.join(timeout=2.0)

            p_trans._sock.settimeout(10.0)
            p_trans.send_json({"type": MessageType.FILE_DONE, "fid": infos[0].fid})
            p_trans.disconnect()
            s_trans.disconnect()
            receiver_done.set()
        except Exception as e:
            receiver_errors.append(e)
            receiver_done.set()
        finally:
            server.stop()

    r_th = threading.Thread(target=receiver_thread, daemon=True)
    r_th.start()

    time.sleep(0.1)

    # Client connects primary (simulating Wi-Fi)
    c_primary = WiFiTransport("client-wifi")
    c_primary.connect("127.0.0.1", port, timeout=5.0, cert_fp=fp)

    # HELLO
    c_primary.send_json({"type": MessageType.HELLO, "v": 1, "sid": session_id, "token": "tok"})
    ack = c_primary.recv_json()
    assert ack["type"] == MessageType.HELLO_ACK

    # Client connects secondary (simulating USB)
    c_sec = WiFiTransport("client-usb")
    c_sec.connect("127.0.0.1", port, timeout=5.0, cert_fp=fp)
    c_sec.send_json({"type": MessageType.HELLO, "v": 1, "sid": session_id, "token": "tok", "channel": "data"})
    sec_ack = c_sec.recv_json()
    assert sec_ack["type"] == MessageType.HELLO_ACK

    # Prepare transfer with scheduler
    sched = Scheduler()
    sched.add_transport(c_primary)
    sched.add_transport(c_sec)

    sender_xfer = TransferManager(session_id, sched, ResumeManager(tmp_path / "resume_sender"), chunk_size=8192)
    infos = sender_xfer.prepare_files([src_file])

    c_primary.send_json(sender_xfer.build_ready_message(infos))
    acc = c_primary.recv_json()
    assert acc["type"] == MessageType.ACCEPT

    # Send chunks using multi-path scheduler
    sender_xfer.send_file(infos[0].fid, ack_callback=lambda f, c: None)

    # Wait for FILE_DONE
    done_msg = c_primary.recv_json(timeout=10.0)
    assert done_msg["type"] == MessageType.FILE_DONE

    c_primary.disconnect()
    c_sec.disconnect()

    r_th.join(timeout=10.0)
    assert not receiver_errors, f"Receiver errors: {receiver_errors}"
    assert receiver_done.is_set()

    # Verify both transports transmitted chunks
    assert chunks_received_by_transport["primary"] > 0, "Primary transport should have received chunks"
    assert chunks_received_by_transport["secondary"] > 0, "Secondary transport should have received chunks"
    assert chunks_received_by_transport["primary"] + chunks_received_by_transport["secondary"] == 10

    # Verify assembled file matches
    result_file = dest_dir / "multi_test.bin"
    assert result_file.is_file()
    assert result_file.read_bytes() == content

