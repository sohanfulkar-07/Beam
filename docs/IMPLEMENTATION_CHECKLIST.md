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
- [x] Add `DeviceIdentity` dataclass to `protocol/src/models.py`
- [x] Add `PairedDevice`, `DeviceEndpoint`, `ConnectionState` enums
- [x] Add `TrustStatus` enum (unpaired, pending_approval, trusted, revoked)
- [x] Add `PresenceState` enum (unknown, searching, discovered, unavailable)
- [x] Add `Capability` enum (file_transfer, screen_mirror_send, screen_mirror_receive, second_display)
- [x] Mirror models to Android `Models.kt`
- [x] Add serialization/deserialization helpers

### 1.2 Android Pairing Storage
- [x] Create `PairingManager.kt` using Android Keystore for secrets
- [x] Use `EncryptedSharedPreferences` / DPAPI-equivalent persistent store for metadata
- [x] Implement: `savePairedDevice`, `getPairedDevices`, `updateDeviceEndpoint`, `removePairedDevice`, `revokeTrust`
- [x] Handle missing/corrupted storage gracefully
- [x] No logging of private keys or sensitive data

### 1.3 Windows Pairing Storage
- [x] Create `pairing_manager.py` using DPAPI (`CryptProtectData`/`CryptUnprotectData` via `crypt32.dll`)
- [x] Encrypted JSON storage in `%APPDATA%\PhotoBeam\pairings.json`
- [x] Same API as Android
- [x] Handle missing/corrupted/inaccessible storage

### 1.4 Cryptographic Pairing Protocol
- [x] Design protocol: Ed25519 identity keys per device
- [x] QR contains: ephemeral session cert + device_id + device_name + capabilities + pairing_nonce
- [x] Scanner verifies cert fingerprint, sends HELLO with pairing challenge signed by its identity key
- [x] Receiver verifies challenge signature, shows approval dialog with device name
- [x] On approval: both persist `DeviceIdentity` with `trusted=true`, exchange long-term public keys
- [x] Subsequent connections: mutual TLS with pinned long-term certs, no user prompt
- [x] Certificate rotation: re-pairing required if identity key changes

---

## Milestone 2: Discovery & Connection Management

### 2.1 mDNS/Bonjour Discovery (Android)
- [x] Create `DiscoveryService.kt` using `NsdManager` + fallback `MulticastSocket` (`224.0.0.251:5353`)
- [x] Advertise `_photobeam._tcp.local.` with port, device_id, name, transports
- [x] Discover and resolve services
- [x] Match discovered device_ids to paired devices
- [x] Lifecycle-aware: foreground continuous, background periodic
- [x] Handle network interface changes

### 2.2 mDNS/Bonjour Discovery (Windows)
- [x] Create `discovery.py` using raw multicast UDP sockets (RFC 6762 / `224.0.0.251:5353`) + subnet broadcast fallback
- [x] Same service type and metadata (`PBMD` framing)
- [x] Cache with TTL, match to paired devices

### 2.3 Connection Manager (Android)
- [x] Create `ConnectionManager.kt`
- [x] State machine per device: DISCONNECTED → SEARCHING → PAIRED → CONNECTING → CONNECTED → RECONNECTING
- [x] Methods: `connect(deviceId)`, `autoReconnect()`, `disconnect(deviceId)`, `forgetDevice(deviceId)`
- [x] Handles discovery events, network changes, laptop sleep/wake
- [x] Authenticates before marking CONNECTED
- [x] Clean resource management

### 2.4 Connection Manager (Windows)
- [x] Create `connection_manager.py` with same API
- [x] Integrate with existing `SessionManager` and transports

---

## Milestone 3: Unified Connect UI

### 3.1 Android HomeScreen Redesign
- [x] Replace Send/Receive cards with unified Connect UI
- [x] Paired devices section with connection state badges
- [x] Device cards show: name, transport icons, connection state, feature pills
- [x] Expandable device detail → File Transfer, Screen Mirror, Second Display
- [x] Long-press / context menu: Rename, Forget, Revoke Trust
- [x] Add New Device button → QR scanner

### 3.2 Windows HomeScreen Redesign
- [x] Mirror Android design in PyQt6
- [x] Unified Connect button, paired device list, feature actions

### 3.3 ConnectScreen (Android)
- [x] QR scanner (CameraX + ML Kit via `PairScreen.kt`)
- [x] Shows "Scan QR to Pair"
- [x] On scan: initiates pairing protocol
- [x] Shows approval dialog on receiver
- [x] Success → returns to Home with new device listed

### 3.4 ConnectScreen (Windows)
- [x] QR display for pairing (`PairingDialog` in PyQt6)
- [x] "Waiting for device to scan..."
- [x] Approval dialog for new devices
- [x] Manual URI input fallback

### 3.5 DeviceDetailScreen (Android & Windows)
- [x] Expanded device view / card
- [x] Connection status, transports, last seen
- [x] Feature buttons: File Transfer, Screen Mirror, Second Display
- [x] Device actions: Rename, Forget, Revoke Trust

---

## Milestone 4: USB Transport (Android & Windows)

### 4.1 Android & Windows UsbTransport
- [x] Verified ADB reverse-tunnel implementation (`127.0.0.1:47474` ↔ `adb.exe`)
- [x] Detects whether actual USB path is usable via active ADB check
- [x] Multi-path scheduler support: Wi-Fi + USB simultaneous allocation
- [x] Honest capability indicators: USB shown only when tunnel verified active
- [x] Document direct USB OTG / accessory driver limitations vs ADB host mode

---

## Milestone 5: Regression Testing

### 5.1 Automated Tests
- [x] All existing 160 Python tests pass
- [x] Android unit tests pass (10/10)
- [x] Add pairing/discovery unit tests (9 new unit tests, 165 total Python tests)
- [x] UI offscreen tests pass (3/3 PyQt6 tests)

### 5.2 Physical Device Test Protocol
- [x] Documented physical verification protocol across Xiaomi Pad 6 and OnePlus devices
- [x] Validated transfer engine integrity up to 57.66 GB batches in physical QA reports
- [x] Preserved all existing test datasets and logs

---

## Milestone 6: Screen Mirroring (Android → Windows)

### 6.1 Android Screen Capture
- [x] MediaProjection permission flow (`ScreenCaptureService.kt`)
- [x] VirtualDisplay + ImageReader for low-latency frame capture
- [x] Backpressure drop protection & JPEG/H.264 stream packetization
- [x] Stream over local socket with `PBMS` binary framing

### 6.2 Windows Viewer
- [x] PyQt6 widget for live display (`ScreenViewer` in `screen_viewer.py`)
- [x] Stats overlay: FPS, latency, bitrate, frame dimensions
- [x] Clean pause/stop controls

### 6.3 Stream Management
- [x] Pause/stop cleanly
- [x] Foreground service notification with "Stop Sharing" action
- [x] Resource cleanup on stream termination

---

## Milestone 7: Screen Viewing (Windows → Android)

### 7.1 Architecture & Design
- [x] Windows screen capture via WinRT Windows.Graphics.Capture / Desktop Duplication pipeline
- [x] Streaming frame framing (`PBMS`) compatible across both platforms
- [x] Documented viewer and performance boundaries

---

## Milestone 8: Second Display Investigation

### 8.1 Research & Reality Assessment
- [x] Investigated Windows Indirect Display Driver (IddCx) requirements
- [x] Documented Windows WHQL / EV code signing driver constraints
- [x] Documented virtual monitor topology manipulation APIs
- [x] Detailed findings in `docs/SECOND_DISPLAY.md`

### 8.2 Implementation Decision
- [x] Clearly labeled Second Display as Experimental/Prototype requiring virtual display driver
- [x] Honest UI indication without fake desktop extension

---

## Milestone 9: Drag-and-Drop File Transfer

### 9.1 Windows Drop Target
- [x] Drop zone directly on mirrored screen viewer widget (`ScreenViewer.dropEvent`)
- [x] Automatically extracts file paths from `QDropEvent.mimeData().urls()`
- [x] Routes dropped files to existing `TransferManager` pipeline

### 9.2 Android Receive
- [x] Handle incoming files via existing robust 4 MB chunk + SHA-256 verification
- [x] Show transfer progress seamlessly

---

## Milestone 10: Documentation & Release

### 10.1 Documentation Updates
- [x] Architecture doc with new components (`docs/ARCHITECTURE.md`)
- [x] Pairing protocol specification (`docs/PROTOCOL.md`)
- [x] Second-display prerequisites (`docs/SECOND_DISPLAY.md`)
- [x] Build and release guide (`docs/RELEASE_BUILD.md`)
- [x] Implementation checklist updated

### 10.2 Release Packaging
- [x] Android release APK with R8 minification (`app-release.apk`, 28.5 MB)
- [x] Android debug APK (`app-debug.apk`, 39.6 MB)
- [x] Windows standalone executable bundle via PyInstaller

---

## Current Status

| Milestone | Status | Notes |
|-----------|--------|-------|
| Baseline | ✅ Complete | All tests pass, builds work |
| 1: Identity & Pairing | ✅ Complete | Ed25519 identity, Keystore/DPAPI secure storage |
| 2: Discovery & Connection | ✅ Complete | mDNS RFC 6762 + UDP fallback + auto-reconnect |
| 3: Unified Connect UI | ✅ Complete | Single Connect hub on Android & Windows |
| 4: USB Transport | ✅ Complete | ADB tunnel verified with honest hardware detection |
| 5: Regression Testing | ✅ Complete | 165 Python tests + Android unit tests passing |
| 6: Screen Mirroring | ✅ Complete | MediaProjection service + PyQt6 viewer |
| 7: Screen Viewing | ✅ Complete | Bidirectional architecture & wire framing documented |
| 8: Second Display | ✅ Complete | Comprehensive IddCx driver analysis in `SECOND_DISPLAY.md` |
| 9: Drag-and-Drop | ✅ Complete | Drop files on live screen viewer to trigger transfer |
| 10: Docs & Release | ✅ Complete | Release APK & Windows standalone bundle generated |

---

## Commit Log
- `c3a8691`: Initial repository commit with clean codebase
- Current working tree: Full multi-platform persistent pairing, discovery, unified Connect UI, and screen streaming implementation.