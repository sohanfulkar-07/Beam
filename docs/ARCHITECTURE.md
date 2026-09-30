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
- No credentials stored unencrypted on disk
- Persistent identity keys protected via Windows DPAPI (CryptProtectData) and Android Keystore
- Forward secrecy via ephemeral session keys, authentication via persistent Ed25519 identity key signatures

## Persistent Device Identity & Trust

```
[Device Identity]
  ├── id (UUID4)
  ├── display_name (User-editable)
  ├── public_key (Ed25519 raw bytes / Base64)
  ├── trust_status (unpaired | pending_approval | trusted | revoked)
  └── capabilities [file_transfer, screen_mirror_send, screen_mirror_receive, second_display]
```

- **Windows Storage**: Encrypted with DPAPI (`CryptProtectData`/`CryptUnprotectData` via `crypt32.dll`), persisted to `%APPDATA%\PhotoBeam\pairings.json`.
- **Android Storage**: Hardware-backed Android Keystore for key generation and cryptographic signing, with `EncryptedSharedPreferences` / DPAPI equivalent for metadata.
- **Forget vs Revoke**:
  - `Forget`: Removes the pairing record from local device.
  - `Revoke`: Explicitly revokes trust status for the remote device, transitioning state to `REVOKED` and requiring explicit re-pairing before any future communication.

## Connection Management & Lifecycle

Connection state is managed independently from presence, trust, and transfer progress:
- **Trust State**: `UNPAIRED`, `PENDING_APPROVAL`, `TRUSTED`, `REVOKED`
- **Presence State**: `UNKNOWN`, `SEARCHING`, `DISCOVERED`, `UNAVAILABLE`
- **Connection State**: `DISCONNECTED`, `CONNECTING`, `CONNECTED`, `RECONNECTING`, `AUTHENTICATION_REQUIRED`
- **Transport Health**: Each transport (`WIFI`, `USB`) independently reports `ACTIVE`, `DEGRADED`, or `FAILED`.
- **Auto-Reconnect**: Exponential backoff with jitter (1s base, 30s ceiling, max 10 attempts) triggered upon presence detection of a paired device.

## Local Discovery Architecture

1. **Primary Protocol**: Multicast DNS (RFC 6762) over UDP `224.0.0.251:5353`.
   - Service name: `_photobeam._tcp.local.`
2. **Fallback Protocol**: Local subnet UDP broadcast to port `47475`.
3. **Wire Framing (`PBMD`)**:
   - Magic: `0x50424D44` (ASCII "PBMD")
   - Payload: JSON advertisement containing device UUID, name, addresses, port, active transports, and supported capabilities.
4. **Security Boundary**: Discovery broadcast is unauthenticated. Receiving a discovery packet moves presence to `DISCOVERED`, but connection requires full Ed25519 mutual authentication before reaching `CONNECTED`.

## Screen Mirroring & Viewing Architecture

```
[Android Screen Capture] (MediaProjection + VirtualDisplay + ImageReader)
  ├── Low-latency JPEG/H.264 packetizer
  └── Backpressure frame dropping (queues bounded to 2 frames)
        │
        ▼ (PBMS Binary Stream over TLS)
[Windows Screen Viewer] (PyQt6 QWidget + QPainter)
  ├── Frame decoder & latency tracker
  ├── Metrics overlay (FPS, latency, bitrate)
  └── Drag-and-Drop file drop target
```

- **Binary Frame Format (`PBMS`)**:
  - `[4B Magic: 0x50424D53]` `[2B Version: 1]` `[2B FrameType]`
  - `[4B FrameID]` `[8B Timestamp]` `[2B Width]` `[2B Height]` `[4B PayloadLength]`
  - `[PayloadLength bytes: encoded frame]`
- **Drag-and-Drop Integration**: Files dragged and dropped onto the Windows screen viewer widget are intercepted via `QDropEvent`, verified for filesystem validity, and handed directly to `TransferManager` to transfer across the active high-speed channel.

## Second Display Mode (Extended Desktop)

- **Architecture Reality**: An extended desktop monitor in Windows requires a true Windows Display Driver Model (WDDM) Indirect Display Driver (IddCx).
- **Security Constraints**: Kernel-mode or UMDF drivers require Microsoft WHQL / EV (Extended Validation) code signing certificates and administrator installation privileges.
- **PhotoBeam Implementation**:
  - Full driver specifications, architecture, and installation pipeline documented in `docs/SECOND_DISPLAY.md`.
  - Application UI honestly reports second-display mode as an experimental/prototype feature requiring virtual display driver components, rather than faking an extended desktop.

## Multi-path Scheduling

Scheduler runs every 500ms measurement window.
Weights chunks proportionally to each transport's measured throughput.
If a transport fails: its pending-but-unconfirmed chunks are rescheduled.
Chunk is only marked "done" after ACK from receiver.

