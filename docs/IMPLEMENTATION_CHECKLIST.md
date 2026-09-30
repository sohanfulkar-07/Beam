# PhotoBeam — Implementation Checklist

## Baseline (2026-09-30)
- All 160 Python tests pass
- Android unit tests pass (10/10)
- Windows executable builds (5.5 MB)
- Android debug APK (38 MB) and release APK (28 MB) build
- Verified physical transfers up to 57.66 GB multi-file batch

---

## Milestone 1: Persistent Device Identity & Pairing

### 1.1 Shared Protocol Models
- [ ] Add `DeviceIdentity` dataclass to `protocol/src/models.py`
- [ ] Add `PairedDevice`, `DeviceEndpoint`, `ConnectionState` enums
- [ ] Add `TrustStatus` enum (unpaired, pending_approval, trusted, revoked)
- [ ] Add `PresenceState` enum (unknown, searching, discovered, unavailable)
- [ ] Add `Capability` enum (file_transfer, screen_mirror_send, screen_mirror_receive, second_display)
- [ ] Mirror models to Android `Models.kt`
- [ ] Add serialization/deserialization helpers

### 1.2 Android Pairing Storage
- [ ] Create `PairingManager.kt` using Android Keystore for secrets
- [ ] Use `EncryptedSharedPreferences` or `DataStore` for metadata
- [ ] Implement: `savePairedDevice`, `getPairedDevices`, `updateDeviceEndpoint`, `removePairedDevice`, `revokeTrust`
- [ ] Handle missing/corrupted storage gracefully
- [ ] No logging of private keys or sensitive data

### 1.3 Windows Pairing Storage
- [ ] Create `pairing_manager.py` using DPAPI (`cryptography.hazmat.primitives.kdf.pbkdf2` + Windows Credential Manager)
- [ ] Encrypted JSON storage in `%APPDATA%\PhotoBeam\pairings.json`
- [ ] Same API as Android
- [ ] Handle missing/corrupted/inaccessible storage

### 1.4 Cryptographic Pairing Protocol
- [ ] Design protocol: Ed25519 identity keys per device
- [ ] QR contains: ephemeral session cert + device_id + device_name + capabilities + pairing_nonce
- [ ] Scanner verifies cert fingerprint, sends HELLO with pairing challenge signed by its identity key
- [ ] Receiver verifies challenge signature, shows approval dialog with device name
- [ ] On approval: both persist `DeviceIdentity` with `trusted=true`, exchange long-term public keys
- [ ] Subsequent connections: mutual TLS with pinned long-term certs, no user prompt
- [ ] Certificate rotation: re-pairing required if identity key changes

---

## Milestone 2: Discovery & Connection Management

### 2.1 mDNS/Bonjour Discovery (Android)
- [ ] Create `DiscoveryService.kt` using `NsdManager`
- [ ] Advertise `_photobeam._tcp.local.` with port, device_id, name, transports
- [ ] Discover and resolve services
- [ ] Match discovered device_ids to paired devices
- [ ] Lifecycle-aware: foreground continuous, background periodic (30s on / 2min off)
- [ ] Handle network interface changes

### 2.2 mDNS/Bonjour Discovery (Windows)
- [ ] Create `discovery.py` using `zeroconf` library
- [ ] Same service type and metadata
- [ ] Cache with TTL, match to paired devices

### 2.3 Connection Manager (Android)
- [ ] Create `ConnectionManager.kt`
- [ ] State machine per device: DISCONNECTED → SEARCHING → PAIRED → CONNECTING → CONNECTED → RECONNECTING
- [ ] Methods: `connect(deviceId)`, `autoReconnect()`, `disconnect(deviceId)`, `forgetDevice(deviceId)`
- [ ] Handles discovery events, network changes, laptop sleep/wake
- [ ] Authenticates before marking CONNECTED
- [ ] Clean resource management

### 2.4 Connection Manager (Windows)
- [ ] Create `connection_manager.py` with same API
- [ ] Integrate with existing `SessionManager` and transports

---

## Milestone 3: Unified Connect UI

### 3.1 Android HomeScreen Redesign
- [ ] Replace Send/Receive cards with single CONNECT button
- [ ] Paired devices section with connection state badges
- [ ] Device cards show: name, transport icons, connection state, feature pills
- [ ] Expandable device detail → File Transfer, Screen Mirror, Second Display
- [ ] Long-press menu: Rename, Forget, Revoke Trust
- [ ] Add New Device button → QR scanner

### 3.2 Windows HomeScreen Redesign
- [ ] Mirror Android design in PyQt6
- [ ] CONNECT button, paired device list, feature actions

### 3.3 ConnectScreen (Android)
- [ ] QR scanner (reuse `QrScannerView`)
- [ ] Shows "Scanning for PhotoBeam QR..."
- [ ] On scan: initiates pairing protocol
- [ ] Shows approval dialog on receiver
- [ ] Success → returns to Home with new device listed

### 3.4 ConnectScreen (Windows)
- [ ] QR display for pairing (when acting as receiver)
- [ ] "Waiting for device to scan..."
- [ ] Approval dialog for new devices
- [ ] Manual QR input fallback

### 3.5 DeviceDetailScreen (Android & Windows)
- [ ] Expanded device view
- [ ] Connection status, transports, last seen
- [ ] Feature buttons: File Transfer, Screen Mirror, Second Display
- [ ] Device actions: Rename, Forget, Revoke Trust

---

## Milestone 4: USB Transport (Android)

### 4.1 Android UsbTransport
- [ ] Implement `UsbTransport.kt` using `UsbManager` + `UsbDeviceConnection`
- [ ] Bulk endpoints for data transfer
- [ ] Fallback to ADB if available
- [ ] Integrate with `Scheduler` for multi-path
- [ ] Show USB badge only when transport verified active

---

## Milestone 5: Regression Testing

### 5.1 Automated Tests
- [ ] All existing 160 Python tests pass
- [ ] Android unit tests pass
- [ ] Add pairing/discovery unit tests

### 5.2 Physical Device Tests (Xiaomi Pad 6 + Windows / OnePlus Nord CE5 + Windows)
- [ ] First pairing: phone scans laptop QR → approve → both save
- [ ] App restart: paired device appears without QR scan
- [ ] Network change: auto-reconnect works
- [ ] Laptop sleep/wake: reconnects
- [ ] Multiple paired devices (phone + tablet)
- [ ] Forget device: removed from both, requires re-pair
- [ ] Revoke trust: shows AUTH_REQUIRED, requires re-approval
- [ ] File transfer: 2 GB via paired device → SHA-256 match
- [ ] Wi-Fi only, USB only, simultaneous paths
- [ ] Failover both directions

---

## Milestone 6: Screen Mirroring (Android → Windows)

### 6.1 Android Screen Capture
- [ ] MediaProjection permission flow
- [ ] VirtualDisplay + Surface for capture
- [ ] Hardware encoder (MediaCodec) for H.264/HEVC
- [ ] Stream over existing TLS connection (separate channel)

### 6.2 Windows Viewer
- [ ] FFmpeg/VideoDecoder for H.264/HEVC
- [ ] PyQt6 widget for live display
- [ ] Quality selector (Low/Balanced/High)
- [ ] Stats overlay: fps, latency, bitrate

### 6.3 Stream Management
- [ ] Pause/stop cleanly
- [ ] Rotation handling
- [ ] Permission revocation handling
- [ ] Network loss → reconnect stream
- [ ] Resource cleanup

---

## Milestone 7: Screen Viewing (Windows → Android)

### 7.1 Windows Capture
- [ ] Windows Graphics Capture API (WinRT) or Desktop Duplication
- [ ] Hardware encoder (NVENC/QuickSync/AMF)
- [ ] Stream to Android

### 7.2 Android Viewer
- [ ] MediaCodec decoder
- [ ] SurfaceView/TextureView playback
- [ ] Full-screen, rotation support

---

## Milestone 8: Second Display Investigation

### 8.1 Research
- [ ] Windows virtual display driver requirements
- [ ] Spacedesk/Deskreen/Universal Display Driver analysis
- [ ] Driver signing, admin rights, installation process
- [ ] What can be done in-app vs separate driver

### 8.2 Implementation Decision
- [ ] If feasible: implement driver + app integration
- [ ] If not: document limitation, label experimental

---

## Milestone 9: Drag-and-Drop File Transfer

### 9.1 Windows Drop Target
- [ ] Drop zone on mirrored screen view
- [ ] File picker fallback
- [ ] Uses existing TransferManager

### 9.2 Android Receive
- [ ] Handle incoming files via existing protocol
- [ ] Show progress in mirroring view

---

## Milestone 10: Documentation & Release

### 10.1 Documentation Updates
- [ ] Architecture doc with new components
- [ ] Pairing protocol specification
- [ ] Discovery and reconnection behavior
- [ ] Supported transports and limitations
- [ ] Screen streaming architecture
- [ ] Second-display prerequisites
- [ ] Permissions and firewall guide
- [ ] Build/install instructions
- [ ] Troubleshooting
- [ ] Test matrix and results
- [ ] Known issues

### 10.2 Release Packaging
- [ ] Android release APK with ProGuard
- [ ] Windows standalone executable
- [ ] Version bump
- [ ] Changelog

---

## Current Status

| Milestone | Status | Notes |
|-----------|--------|-------|
| Baseline | ✅ Complete | All tests pass, builds work |
| 1: Identity & Pairing | 🔄 In Progress | Starting with shared models |
| 2: Discovery & Connection | ⏳ Pending | |
| 3: Unified Connect UI | ⏳ Pending | |
| 4: Android USB Transport | ⏳ Pending | |
| 5: Regression Testing | ⏳ Pending | |
| 6: Screen Mirroring | ⏳ Pending | |
| 7: Screen Viewing | ⏳ Pending | |
| 8: Second Display | ⏳ Pending | |
| 9: Drag-and-Drop | ⏳ Pending | |
| 10: Docs & Release | ⏳ Pending | |

---

## Commit Log
(To be filled as work progresses)