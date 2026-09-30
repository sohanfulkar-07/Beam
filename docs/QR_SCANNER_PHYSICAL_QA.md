# PhotoBeam — QR Scanner & Physical Pairing QA Report

**Date & Time**: 2026-09-30 22:03:00 +05:30  
**Test Hardware**:
* **Mobile Device**: OnePlus Nord CE 5 (Model `CPH2717`, Android 16 / OxygenOS, ADB ID: `6H99AIUG9DHYXGR8`)
* **Host Laptop**: Lenovo LOQ Windows 11 (`Sohan_LOQ`, 10.0.26200-SP0)
* **Active Transports**: Dual Wi-Fi (LAN `10.78.45.44` / `10.78.45.185`) + USB Tunnel (`adb reverse tcp:47474 tcp:47474`, `127.0.0.1:47475`)

---

## 1. Root Cause Analysis

### Root Cause 1: Scheme Incompatibility & Silent Dropping of Pairing URIs
* **Evidence**:
  In `QrScannerView.kt`, the CameraX ML Kit barcode analyzer had a hardcoded filter:
  ```kotlin
  if (value.startsWith("photobeam://connect/")) {
      onQrScanned(value)
  }
  ```
  However, the Windows pairing dialog generates pairing URIs using the scheme `photobeam://pair/<base64>`. Because the scanner dropped any string not starting with `photobeam://connect/`, pointing the mobile camera at the laptop pairing QR resulted in no callback trigger, leaving the user with a frozen preview reticle and no feedback.
* **Secondary URI Decoding Gap**:
  `decodeQrPayload()` in `protocol/src/qr_payload.py` and Android `Models.kt` only permitted `photobeam://connect/`. `PairScreen.kt` and `SendScreen.kt` lacked unified cross-scheme translation for pairing versus ad-hoc transfer connections.

### Root Cause 2: QR Contrast & Quiet Zone Clipping on Desktop UI
* **Evidence**:
  In `windows/photobeam-windows/ui/pairing_dialog.py`:
  - The QR was generated with default border `border=2` and Low error correction (`ERROR_CORRECT_L`).
  - The `qr_label` was sized to 220x220 without background padding, causing the dark mode theme styling (`#0f172a`) to bleed directly into the QR finder patterns, clipping the required 4-module quiet zone. Mobile phone cameras (CameraX ML Kit) failed corner edge detection under typical LCD screen glare and viewing angles.

### Root Cause 3: Main Thread Looper Exceptions in Android Pairing Callbacks
* **Evidence**:
  In `PairScreen.kt`, callbacks from `connectionManager.pairWithPayload` executed on background coroutine worker threads. `Toast.makeText()` and Compose state updates were invoked directly, throwing `java.lang.RuntimeException: Can't toast on a thread that has not called Looper.prepare()`, crashing the pairing completion handler.

### Root Cause 4: Chunk Buffer Read Truncation in Android Sender
* **Evidence**:
  In `SendScreen.kt` line 1229:
  ```kotlin
  val n = stream.read(buf)
  ```
  `FileInputStream.read(buf)` only returned the initial kernel buffer (64 KB) instead of filling the advertised `chunkSize` (4 MB). As a result, only partial chunks were transmitted, while the receiving `ChunkManager` preallocated the remaining space with zeroes, causing SHA-256 integrity verification failures.

### Root Cause 5: Scoped Storage Permission Denial on Android 16
* **Evidence**:
  `[PERF] Transfer error: /storage/emulated/0/Download/photobeam_e2e_test.bin: open failed: EACCES (Permission denied)`  
  Direct file access via `java.io.File` on Android 16 was blocked by scoped storage policies unless falling back to `context.getExternalFilesDir(null)`.

---

## 2. Files Changed & Technical Fixes

| Component | File | Changes Made |
| :--- | :--- | :--- |
| **Android QR Scanner** | [`QrScannerView.kt`](file:///c:/Users/Sohan/OneDrive/Documents/Desktop/Beam/android/app/src/main/java/com/photobeam/app/ui/screens/QrScannerView.kt) | 1. Configured ML Kit `BarcodeScannerOptions.Builder().setBarcodeFormats(Barcode.FORMAT_QR_CODE)` for high-FPS QR decoding.<br>2. Wrapped `ImageAnalysis.Analyzer` in try-finally ensuring `imageProxy.close()` is never leaked.<br>3. Added `hasTriggered: AtomicBoolean` and `isScanningActive` to suppress duplicate frame triggers.<br>4. Forwarded all detected QR strings to `onQrScanned` to properly trigger user-facing error states on invalid QRs.<br>5. Added explicit Camera permission denied rationale UI with direct "Open App Settings" button. |
| **Android Pairing Screen** | [`PairScreen.kt`](file:///c:/Users/Sohan/OneDrive/Documents/Desktop/Beam/android/app/src/main/java/com/photobeam/app/ui/screens/PairScreen.kt) | 1. Implemented explicit `PairUiState` machine (`Ready`, `Connecting`, `Success`, `Error`).<br>2. Supported both `photobeam://pair/` and `photobeam://connect/` URIs.<br>3. Validated expiration (`payload.isExpired()`), displaying clear expired messages.<br>4. Routed callbacks safely to `Looper.getMainLooper()` via `Handler.post`.<br>5. Added prominent `🔄 Scan Again / Retry` button that resets scanner state cleanly. |
| **Android Sender** | [`SendScreen.kt`](file:///c:/Users/Sohan/OneDrive/Documents/Desktop/Beam/android/app/src/main/java/com/photobeam/app/ui/screens/SendScreen.kt) | 1. Fixed chunk reading loop (`while (n < buf.size) { val r = stream.read(...); n += r }`) ensuring full 4 MB chunk frames.<br>2. Added scoped storage fallback to `context.getExternalFilesDir(null)` in `openStream` and `getFileNameAndSize`.<br>3. Initialized state directly to `Connecting` when `initialQrUri` is present, avoiding unnecessary camera initialization. |
| **Android Protocol** | [`Models.kt`](file:///c:/Users/Sohan/OneDrive/Documents/Desktop/Beam/android/app/src/main/java/com/photobeam/app/protocol/Models.kt) | Updated `decodeQrPayload` to decode both `photobeam://connect/` and `photobeam://pair/` schemes. |
| **Desktop Pairing Dialog** | [`pairing_dialog.py`](file:///c:/Users/Sohan/OneDrive/Documents/Desktop/Beam/windows/photobeam-windows/ui/pairing_dialog.py) | 1. Upgraded QR error correction to `ERROR_CORRECT_M` (15%).<br>2. Configured `box_size=8, border=4` for standard 4-module quiet zone.<br>3. Enlarged `qr_label` to 260x260 with `#ffffff` background card and 10px padding.<br>4. Appended `127.0.0.1` loopback to `addrs` for reliable USB ADB reverse support.<br>5. Added live callback listener to update UI in real-time when pairing completes. |
| **Protocol Core** | [`qr_payload.py`](file:///c:/Users/Sohan/OneDrive/Documents/Desktop/Beam/protocol/src/qr_payload.py) | Added support for decoding `photobeam://pair/` URIs alongside `photobeam://connect/`. |
| **Automated Tests** | [`test_qr_payload.py`](file:///c:/Users/Sohan/OneDrive/Documents/Desktop/Beam/tests/protocol/test_qr_payload.py) | Added regression tests: `test_decode_pairing_uri_as_qr_payload` and `test_decode_pairing_uri_expired`. |
| **Physical QA Suite** | [`test_physical_qr_and_pairing.py`](file:///c:/Users/Sohan/OneDrive/Documents/Desktop/Beam/tests/test_physical_qr_and_pairing.py) | End-to-end automated physical hardware verification suite across ADB and Windows desktop. |

---

## 3. Physical Hardware Test Results

**Target Device**: OnePlus Nord CE 5 (`CPH2717`, Android 16, Serial: `6H99AIUG9DHYXGR8`)  
**Host System**: Windows 11 PC (`Sohan_LOQ`, 10.0.26200-SP0)  
**Execution Timestamp**: 2026-09-30 22:02:40 +05:30

### Test A — Camera & Preview Lifecycle: PASS
* **Camera Permission**: Confirmed `android.permission.CAMERA: granted=true`.
* **Camera2 / CameraX Lifecycle**: Logcat confirms `CameraDevice.onOpened()`, `CameraState{type=OPEN, error=null}`, `Preview ACTIVE`, and `ImageAnalysis ACTIVE`.
* **Live Viewfinder UI**: Confirmed full-screen camera preview with centered glowing reticle and guide text: *"Point camera at the PhotoBeam QR code on your PC"*.
* **Overlay Inspection**: Zero overlapping elements; status bar and navigation bar padding properly applied.

### Test B — Actual Laptop QR Scanning & Pairing Handshake: PASS
* **Laptop QR Generation**: Windows PhotoBeam generated QR with payload:
  * Device Name: `Sohan_LOQ`
  * Device ID: `9497e223-b1df-42fb-8804-24672a8f0aea`
  * Transports: `["wifi", "usb"]` on port `47474`
  * URI: `photobeam://pair/eyJ2IjoxLCJzaWQiOiJlMTZkYTQw...`
* **Detection & Decoded Payload**: Android scanner detected the QR code, decoded the payload, validated the nonce and session token, and initiated socket connection to `127.0.0.1:47474` / `10.78.45.44:47474`.
* **Handshake Completion**: Pairing completed in <1.2s. Both devices recorded each other as `TRUSTED`:
  * Windows `pairing_manager` saved: `OnePlus Nord CE 5 (CPH2717) (android-6h99aiug9dhyxgr8)`.
  * Android UI hierarchy confirmed live display:
    ```xml
    <node text="Trusted Devices" />
    <node text="Sohan_LOQ" />
    <node text="Connected" />
    <node text="📶 WI-FI" />
    <node text="🔌 USB" />
    <node text="📁 Transfer" />
    <node text="🖥️ Mirror" />
    ```

### Test C — Reliability & Error States: PASS
* **C1 — Malformed / Non-PhotoBeam QR**: Tested scanning random external QR (`https://not-photobeam-code.org/random`). App transitioned to `PairUiState.Error("Invalid QR code: Unrecognized format. Please scan a PhotoBeam pairing code.")`. Displayed warning card with `🔄 Scan Again / Retry` button.
* **C2 — Expired QR**: Tested QR with expired timestamp (`exp = time.time() - 3600`). App displayed: *"QR code has expired. Please refresh the QR code on your PC."* with Retry button.
* **C3 — Duplicate Scan Suppression**: Re-scanning identical frames while connection attempt was in progress did not trigger duplicate socket handshakes (`hasTriggered: AtomicBoolean` protection).
* **C4 — Recovery**: Tapping `Scan Again / Retry` reset `uiState` to `Ready`, re-arming the scanner and successfully scanning a valid QR code.

### Test D — Real Bi-Directional File Transfer After Pairing: PASS

#### Direction 1: Android -> Windows
* **File Name**: `photobeam_e2e_test.bin`
* **Transferred Size**: 2,097,152 bytes (2.0 MB)
* **Transport Modes**: Multi-path (Wi-Fi + USB tunnel)
* **Source SHA-256 (Android)**: `cf628613f6d00ec6fd6d1151ee8d2ed5c983a24f4b065c6ff14db9acbb30e721`
* **Destination SHA-256 (Windows)**: `cf628613f6d00ec6fd6d1151ee8d2ed5c983a24f4b065c6ff14db9acbb30e721`
* **Integrity Match**: **EXACT BYTE-FOR-BYTE MATCH (PASS)**
* **Transfer Duration**: ~1.8 seconds (~1.1 MB/s composite)

#### Direction 2: Windows -> Android
* **File Name**: `test_pc_to_phone_qa.bin`
* **Transferred Size**: 2,097,152 bytes (2.0 MB)
* **Transport Modes**: USB tunnel / Reverse socket
* **Source SHA-256 (Windows)**: `df757dc7233957ee9494b6c65098178ccb84b195409f9392eb64fb973f9b22e6`
* **Destination SHA-256 (Android)**: `df757dc7233957ee9494b6c65098178ccb84b195409f9392eb64fb973f9b22e6`
* **Integrity Match**: **EXACT BYTE-FOR-BYTE MATCH (PASS)**

---

## 4. Automated Regression Suite Results

### Python Test Suite (Pytest)
```
============================= test session starts =============================
platform win32 -- Python 3.12.10, pytest-9.1.1, pluggy-1.6.0
rootdir: C:\Users\Sohan\OneDrive\Documents\Desktop\Beam
collected 167 items

tests\protocol\test_100gb_and_multi_file.py ...........                  [  6%]
tests\protocol\test_byte_perfect_integrity.py .........................  [ 21%]
tests\protocol\test_chunk_frame.py ..........                            [ 27%]
tests\protocol\test_chunk_manager.py .............                       [ 35%]
tests\protocol\test_discovery.py ....                                    [ 37%]
tests\protocol\test_integrity.py .........                               [ 43%]
tests\protocol\test_large_file_timeout_regression.py ......              [ 46%]
tests\protocol\test_multi_transport_partial_completion.py ...            [ 48%]
tests\protocol\test_pairing.py .....                                     [ 51%]
tests\protocol\test_qr_payload.py ...........                            [ 58%]
tests\protocol\test_resume.py ......                                     [ 61%]
tests\protocol\test_safe_pause_resume.py .........                       [ 67%]
tests\protocol\test_scheduler.py ..........                              [ 73%]
tests\protocol\test_session.py ...........                               [ 79%]
tests\protocol\test_storage.py ..........                                [ 85%]
tests\protocol\test_transfer_e2e.py ......                               [ 89%]
tests\ui\test_error_formatter.py ............                            [ 96%]
tests\ui\test_receive_screen_states.py ...                               [ 98%]
tests\ui\test_unified_connect_ui.py ...                                  [100%]

============================= 167 passed in 7.97s =============================
```

### Android Release Unit Tests (Gradle)
```
> Task :app:compileReleaseUnitTestKotlin
> Task :app:testReleaseUnitTest
BUILD SUCCESSFUL in 16s
23 actionable tasks: 4 executed, 19 up-to-date
```

### Packaging & Builds
* **Android Release APK**: `android/app/build/outputs/apk/release/app-release.apk` (28.5 MB, assembled & verified installed on physical device).
* **Windows Standalone Executable**: `dist/PhotoBeam/PhotoBeam.exe` (PyInstaller 6.22.3 standalone bundle generated cleanly).

---

## 5. Summary Matrix

| Category | Item | Result |
| :--- | :--- | :--- |
| **Physical Hardware** | OnePlus Nord CE 5 (`CPH2717` / Android 16) | **CONNECTED & AUTHORIZED** |
| **Test A** | Live Camera Preview & CameraX ImageAnalysis | **PASS** |
| **Test B** | Real Laptop QR Scanning & Pairing Handshake | **PASS** |
| **Test C** | Reliability, Malformed/Expired QR, and Retry | **PASS** |
| **Test D1** | Android to Windows Real File Transfer | **PASS (SHA-256 MATCH)** |
| **Test D2** | Windows to Android Real File Transfer | **PASS (SHA-256 MATCH)** |
| **Automated** | 167 Python Protocol & UI Tests | **167 / 167 PASS** |
| **Automated** | Android Release Unit Tests | **PASS** |
| **Build** | Windows Executable & Android APK Builds | **SUCCESS** |
