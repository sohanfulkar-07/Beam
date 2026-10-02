import socket
import threading
# pyrefly: ignore [missing-import]
from protocol_v2 import (
    send_framed_msg,
    recv_framed_msg,
    pack_data_header,
    unpack_data_header,
)

def test_control_framing_roundtrip():
    s1, s2 = socket.socketpair()
    try:
        msg = {
            "type": "HELLO",
            "device_id": "test-dev-123",
            "name": "Test Device",
            "version": 1,
            "ts": 1790900000000
        }
        assert send_framed_msg(s1, msg) is True
        rcv = recv_framed_msg(s2, timeout=2.0)
        assert rcv == msg
    finally:
        s1.close()
        s2.close()

def test_data_header_roundtrip_large_file():
    s1, s2 = socket.socketpair()
    try:
        transfer_id = 98765432109876
        file_size = 100 * 1024 * 1024 * 1024  # 100 GB (64-bit int)
        filename = "very_large_100gb_archive.iso"
        
        header = pack_data_header(transfer_id, file_size, filename)
        s1.sendall(header)
        
        unpacked = unpack_data_header(s2)
        assert unpacked is not None
        tid, sz, name = unpacked
        assert tid == transfer_id
        assert sz == file_size
        assert name == filename
    finally:
        s1.close()
        s2.close()
