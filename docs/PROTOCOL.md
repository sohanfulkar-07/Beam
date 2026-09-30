# PhotoBeam — Wire Protocol Specification v1

## URI Scheme

```
photobeam://connect/<base64url(payload_json)>
```

Base64url = RFC 4648 §5, no padding.

## QR Payload JSON

```json
{
  "v": 1,
  "sid": "550e8400-e29b-41d4-a716-446655440000",
  "rid": "receiver-uuid",
  "addrs": ["192.168.1.5", "10.0.0.1"],
  "port": 47474,
  "transports": ["wifi"],
  "token": "aabbccdd...32bytes_hex",
  "exp": 1700000000,
  "cert_fp": "sha256:aabb..."
}
```

Fields:
- `v` — protocol version (int). Current: 1.
- `sid` — session UUID.
- `rid` — receiver device UUID (persistent, randomly generated on install).
- `addrs` — list of receiver's local IP addresses to try.
- `port` — TCP port receiver is listening on.
- `transports` — transport types available: `"wifi"`, `"usb"`.
- `token` — 32-byte random hex session auth token.
- `exp` — Unix timestamp expiry (default: now + 300s).
- `cert_fp` — SHA-256 fingerprint of receiver's ephemeral TLS cert, format: `sha256:<hex>`.

## Control Channel (JSON over TLS 1.3)

Each message is a UTF-8 JSON object terminated by `\n`.

### Sender → Receiver

```
HELLO   {"type":"HELLO","v":1,"sid":"...","token":"...","sender_id":"..."}
READY   {"type":"READY","transfers":[<TransferInfo>,...]}
CHUNK_SENT  {"type":"CHUNK_SENT","fid":"...","cid":0}  (for USB where framing differs)
CANCEL  {"type":"CANCEL","sid":"..."}
PING    {"type":"PING","ts":1700000000.123}
RESUME  {"type":"RESUME","sid":"..."}
```

### Receiver → Sender

```
HELLO_ACK   {"type":"HELLO_ACK","v":1,"sid":"..."}
ACCEPT      {"type":"ACCEPT","fid":"..."}
REJECT      {"type":"REJECT","fid":"...","reason":"..."}
ACK_CHUNK   {"type":"ACK_CHUNK","fid":"...","cid":0}
NAK_CHUNK   {"type":"NAK_CHUNK","fid":"...","cid":0,"reason":"bad_checksum"}
FILE_DONE   {"type":"FILE_DONE","fid":"...","sha256":"<hex>"}
FILE_ERROR  {"type":"FILE_ERROR","fid":"...","reason":"hash_mismatch"}
RESUME_STATE {"type":"RESUME_STATE","fid":"...","received":[0,1,2,4]}
PONG        {"type":"PONG","ts":1700000000.123,"server_ts":1700000000.200}
STORAGE_ERROR {"type":"STORAGE_ERROR","required":120000000000,"available":84000000000}
```

### TransferInfo object

```json
{
  "fid": "uuid4",
  "name": "photo.jpg",
  "rel_path": "vacation/photo.jpg",
  "size": 10485760,
  "chunk_size": 4194304,
  "total_chunks": 3,
  "sha256": "hex"
}
```

## Chunk Binary Frame

```
Offset  Size  Field
0       4     Magic: 0x50424D43 ("PBMC")
4       2     Version: 1 (uint16 BE)
6       2     Type: 0x0001 = DATA, 0x0002 = RETRANSMIT (uint16 BE)
8       4     Payload length (uint32 BE) — length of everything after this header
12      16    Transfer ID (UUID bytes)
28      16    File ID (UUID bytes)
44      4     Chunk ID (uint32 BE)
48      8     Byte offset in file (uint64 BE)
56      4     Chunk data length (uint32 BE)
60      8     XXH3-64 checksum of chunk data (uint64 BE)
68      N     Chunk data (raw bytes)
```

Total fixed header: 68 bytes.

## Handshake Sequence

```
Receiver                         Sender
   |                                |
   |<-- TLS connect (cert pinned) --|
   |                                |
   |--> HELLO_ACK ----------------->|
   |<-- HELLO ----------------------|
   |                                |
   |--> ACCEPT/REJECT files ------->|  (user approval on receiver)
   |<-- READY ----------------------|
   |                                |
   |<-- [binary chunks] ------------|  (may arrive on multiple transports)
   |--> ACK_CHUNK / NAK_CHUNK ----->|  (per chunk)
   |                                |
   |--> FILE_DONE ----------------->|
   |                                |
```

Wait — correction: Sender connects TO receiver (receiver is server).

```
Receiver (server)                Sender (client)
   |                                |
   | listen on :47474               |
   |<-- TLS connect ----------------|
   |<-- HELLO ----------------------|
   |--> HELLO_ACK ----------------->|
   |<-- READY ----------------------|
   |--> ACCEPT (all files) -------->|
   |                                |
   |<-- [binary chunks stream] -----|
   |--> ACK_CHUNK ----------------->|  (back on control channel)
   |                                |
   |--> FILE_DONE ----------------->|
```

## Version Compatibility

If sender sends `v > receiver_version`: receiver replies HELLO_ACK with `{"type":"HELLO_ACK","v":1,"error":"version_mismatch","max_v":1}` and closes.

If sender sends `v < 1`: reject.

## Chunk Ordering

Chunks may arrive out of order (especially on multi-path).
Receiver buffers and writes each chunk at its correct offset.
Receiver tracks received chunk IDs in a bitset.

## Resume Protocol

1. Sender reconnects with same `sid` and `token`.
2. Sends `RESUME` message.
3. Receiver replies `RESUME_STATE` for each in-progress file.
4. Sender resends only missing chunks.
5. If session expired: receiver sends `{"type":"ERROR","code":"session_expired"}`.

---

## Persistent Cryptographic Pairing Protocol

PhotoBeam devices establish persistent mutual trust using Ed25519 asymmetric cryptography.

### Pairing Flow

```
Device A (Displays QR)                     Device B (Scans QR)
      |                                           |
      |--- Displays QR (ephemeral token, -------->| (Scans QR)
      |    device_id, Ed25519 public key,         |
      |    pairing_nonce, addrs, port)            |
      |                                           |
      |<-- TLS 1.3 Handshake (TOFU cert pinning)--|
      |                                           |
      |<-- PAIR_REQUEST --------------------------|
      |    {device_id, name, public_key, nonce}   |
      |                                           |
      |--- User Prompt: "Trust Device B?" --------|
      |    [Approve / Decline]                    |
      |                                           |
      |--- PAIR_CONFIRM ------------------------->|
      |    {trusted: true, device_id, name}       |
      |                                           |
      | [Both persist PairedDevice in Keystore/   |
      |  DPAPI encrypted storage]                 |
```

### QR Pairing Payload Schema
```json
{
  "v": 1,
  "sid": "<session-uuid>",
  "rid": "<receiver-device-uuid>",
  "name": "<user-friendly-device-name>",
  "pk": "<ed25519-public-key-base64>",
  "nonce": "<16-byte-hex-nonce>",
  "caps": ["file_transfer", "screen_mirror_send", "screen_mirror_receive", "second_display"],
  "addrs": ["192.168.1.15"],
  "port": 47474,
  "transports": ["wifi", "usb"],
  "token": "<32-byte-hex-session-token>",
  "exp": 1700000300,
  "cert_fp": "sha256:<tls-fingerprint-hex>"
}
```

---

## Local Discovery Wire Protocol (`PBMD`)

For local network presence detection across mDNS (multicast `224.0.0.251:5353`) and subnet broadcast (UDP `47475`).

### Binary Framing
```
Offset  Size  Field
0       4     Magic: 0x50424D44 ("PBMD")
4       2     Version: 1 (uint16 BE)
6       2     Reserved / Flags (uint16 BE)
8       4     JSON payload length (uint32 BE)
12      N     JSON advertisement payload (UTF-8)
```

### JSON Advertisement
```json
{
  "device_id": "4a2b1c3d-...",
  "device_name": "Sohan's PC",
  "addrs": ["192.168.1.100"],
  "port": 47474,
  "transports": ["wifi", "usb"],
  "capabilities": ["file_transfer", "screen_mirror_receive"],
  "protocol_version": 1
}
```

---

## Screen Streaming Binary Protocol (`PBMS`)

Low-latency screen mirroring channel multiplexed over authenticated TLS socket.

### Binary Framing
```
Offset  Size  Field
0       4     Magic: 0x50424D53 ("PBMS")
4       2     Version: 1 (uint16 BE)
6       2     Frame type: 0x0001 = KEYFRAME, 0x0002 = DELTA (uint16 BE)
8       4     Frame sequence ID (uint32 BE)
12      8     Timestamp milliseconds (uint64 BE)
20      2     Frame width in pixels (uint16 BE)
22      2     Frame height in pixels (uint16 BE)
24      4     Payload length in bytes (uint32 BE)
28      N     Compressed video frame data (JPEG / H.264 NALUs)
```
Total frame header: 28 bytes. Bounded queue backpressure guarantees latency is bounded under network jitter.

