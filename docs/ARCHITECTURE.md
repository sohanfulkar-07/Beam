# PhotoBeam — Architecture

## Overview

```
PhotoBeam
    |
+---+---+
|       |
Android  Windows (PyQt6)
|       |
+---+---+
    |
Transfer Protocol (binary + JSON control)
    |
+---+---+
|       |
Wi-Fi   USB
```

## Component Map

```
SessionManager      — creates/validates sessions, tokens, expiry
QRPayload           — encode/decode QR connection info
TransportAbstract   — defines Transport interface
  WiFiTransport     — TCP socket over local LAN or hotspot
  UsbTransport      — ADB reverse tunnel (Android↔Windows) or USB serial
TransferManager     — orchestrates multi-file, multi-transport transfers
ChunkManager        — splits files into chunks, tracks sent/received
Scheduler           — dynamically allocates chunks to transports by throughput
ResumeManager       — persists chunk receipts, enables resume after crash
IntegrityManager    — per-chunk XXH3 + per-file SHA-256
StorageManager      — pre-flight storage check, temp file management
BenchmarkManager    — throughput/latency measurement per transport
```

## Data Flow — Send

```
User selects files
  → TransferManager.prepare_transfer()
      → ChunkManager.split_files()
      → StorageManager.check_destination()
  → SessionManager.create_session()
  → QRPayload.encode() → display QR
  → Receiver scans QR
  → Receiver connects (TLS 1.3) → SessionManager.validate_token()
  → TransferManager.start()
      → Scheduler.next_chunk(transport)
      → Transport.send_chunk(chunk)
      → IntegrityManager.verify_chunk(chunk)
      → ResumeManager.mark_received(chunk_id)
  → IntegrityManager.verify_file(file_id)
  → StorageManager.finalize(tmp → dest)
```

## Transport Interface

Both WiFiTransport and UsbTransport implement the same interface:

```
connect()
disconnect()
send(data: bytes) → int  # bytes sent
recv(n: bytes) → bytes
is_connected() → bool
throughput_bps() → float
transport_id: str
```

## QR Payload (JSON, base64url encoded in URI)

```json
{
  "v": 1,
  "sid": "<uuid4>",
  "rid": "<uuid4>",
  "addrs": ["192.168.1.5", "10.0.0.1"],
  "port": 47474,
  "transports": ["wifi", "usb"],
  "token": "<32-byte hex>",
  "exp": 1700000000,
  "cert_fp": "<sha256 hex of TLS cert>"
}
```

URI scheme: `photobeam://connect/<base64url(json)>`

## Protocol Wire Format

### Control messages: JSON over TLS

```json
{"type": "HELLO", "v": 1, "sid": "...", "token": "..."}
{"type": "READY", "files": [...]}
{"type": "ACK_CHUNK", "fid": "...", "cid": 0}
{"type": "NAK_CHUNK", "fid": "...", "cid": 0}
{"type": "DONE", "fid": "..."}
{"type": "CANCEL"}
{"type": "RESUME_REQUEST", "fid": "...", "received": [0,1,2,4]}
```

### Chunk binary frame

```
[4B magic: 0x50424D43] [4B version: 1] [2B type] [4B payload_len]
[16B transfer_id] [16B file_id] [4B chunk_id] [8B offset] [4B chunk_len]
[8B xxh3_checksum] [chunk_len bytes: data]
```

Total overhead per chunk: 58 bytes.

## Security

- Session token: 32 bytes cryptographically random
- TLS 1.3 with ephemeral self-signed cert per session
- Cert fingerprint included in QR for TOFU pinning
- QR expires after 5 minutes (configurable)
- Session invalidated after transfer complete / cancel / expiry
- No credentials stored on disk

## Multi-path Scheduling

Scheduler runs every 500ms measurement window.
Weights chunks proportionally to each transport's measured throughput.
If a transport fails: its pending-but-unconfirmed chunks are rescheduled.
Chunk is only marked "done" after ACK from receiver.
