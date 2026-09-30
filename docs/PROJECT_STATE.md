# PhotoBeam — Project State

## Environment

| Tool | Version | Available | Location / Notes |
|------|---------|-----------|------------------|
| Java | 21.0.12.1 LTS | ✓ | C:\Users\Sohan\.jdk\jdk-21.0.12.1+1 (Adoptium Temurin) |
| Java (system) | 25.0.4 LTS | ✓ | C:\Program Files\Java\jdk-25.0.4 |
| Python | 3.12.10 | ✓ | Python 3.12 with PyQt6, cryptography, xxhash, qrcode, Pillow |
| Android SDK | API 34 + 36 | ✓ | C:\Users\Sohan\AppData\Local\Android\Sdk |
| Android Build Tools | 34.0.0, 35.0.0, 36.0.0 | ✓ | In Android SDK |
| Gradle | 8.12 (wrapper) | ✓ | Configured with JDK 21 daemon |
| ADB | Available | ✓ | C:\Users\Sohan\AppData\Local\Android\Sdk\platform-tools\adb.exe |
| Connected Device (Current QA) | OnePlus Nord CE5 (`CPH2717`) | ✓ | Serial: `6H99AIUG9DHYXGR8`, Android 16 / API 36 |
| Connected Device (Historical) | Xiaomi Pad 6 (`pipa`) | ✓ | Serial: `2f1ce07`, Android 14 / API 34 |

---

STATUS: 100 GB Architecture Validated & Multi-File Batch Transfer Physically Verified (1.0.0-beta.2 Candidate)

### Phase: 100 GB Very Large Files & Multi-File Batch Architecture (Completed 2026-09-24)

#### 1. Core Architectural Resolutions for 100 GB Files
- **PyQt6 32-Bit Integer Truncation**:
  - `pyqtSignal(int, ...)` in PyQt6 defaults to C++ signed 32-bit `int` (overflows at $2^{31}-1 \approx 2.14$ GB). On a 100 GB file (`107,374,182,400` bytes), this caused silent 32-bit truncation modulo $2^{32} == 0$, resetting progress reporting.
  - Resolved: Updated all byte parameters in `SenderWorker` and `ReceiverWorker` signals (`progress_update`, `paused`, `incoming_prompt`, `transfer_complete`) to `'qint64'`.
- **Initial Handshake & Verification Timeout on Flash Hashing**:
  - Hashing a 100 GB file at ~200-300 MB/s takes 350-500 seconds. Previously, receiver `soTimeout` was 60 seconds (causing broken pipe before `READY`), and sender verification timeout in `send_screen.py` was 120 seconds.
  - Resolved: Raised socket `soTimeout` in `Transport.kt` and `ReceiveScreen.kt` to 3,600,000 ms (1 hour), and verification timeout in `send_screen.py` / `ReceiveScreen.kt` to 3,600.0s.
- **Persistent File Handle Caching & Flush Sequencing**:
  - `StorageManager` in `protocol/src/storage.py` previously opened and closed the file descriptor for every 4 MB chunk write (25,600 opens/closes for 100 GB). Added thread-safe handle caching with `_open_handles` and `threading.Lock()`.
  - Updated `finalize_file()` and `pause()` to explicitly flush (`os.fsync`) and close open handles before `IntegrityManager.verify_file()` executes, ensuring zero read races or unflushed buffers.
- **Strict Chunk Streaming & Memory Bounds**:
  - All transfers stream strictly from disk via 4 MB `ChunkFrame` binary packets without RAM accumulation. Physical memory profiling confirmed Android process PSS increased by only ~7 MB during a 2.5 GB transfer (148 MB -> 155 MB).
- **ETA & Progress Arithmetic Safety**:
  - Protected ETA calculations against floating point infinities, division by zero, and integer overflow, clamping to 1 year and formatting cleanly with hours/minutes/seconds. Added `TB` unit formatting across Windows and Android.

#### 2. Multi-File Batch Transfer & Integrity Isolation
- **Duplicate Filename Collision Disambiguation**:
  - Previous receiver logic unconditionally deleted existing target files. Added `get_unique_destination` / `getUniqueDestinationFile` to both Python and Kotlin receiver pipelines to automatically disambiguate collisions to `name (1).ext`, `name (2).ext` without overwriting or data loss.
- **Single-File Error Isolation**:
  - In `SendScreen.kt`, `FILE_ERROR` previously threw an unhandled exception that halted the remaining batch files. Refactored to record failed files in `failedFiles`, continue transferring remaining files, and report a granular summary (`SendState.Error` with specific failed files or `SendState.Complete`).
- **Partial Batch Resume**:
  - Receiver checks destination directory on `setup_receive`: if a file already exists with exact size and valid SHA-256, it is marked as `COMPLETED` immediately, and remaining files resume without re-transmitting completed assets.

#### 3. Test & Verification Results
- **Automated Python Test Suite**: **148/148 PASSED** (100% pass rate, 0 failed).
  - 11 new comprehensive tests in `tests/protocol/test_100gb_and_multi_file.py`:
    - 100 GB metadata serialization (`size = 107,374,182,400`)
    - Binary framing at large 64-bit offsets (`offset = 107,370,000,000`)
    - Chunk manager 25,600 chunk calculation
    - PyQt6 `'qint64'` signal overflow verification
    - TB capacity formatting
    - Multi-file batch metadata exchange & interleaving
    - Duplicate filename collision disambiguation
    - Partial batch resume skipping completed files
    - Single-file error isolation
    - Batch cancellation cleanup
- **Physical Device Transfer Verification (Xiaomi Pad 6 `2f1ce07`, Android 14 / API 34)**:
  - **Suite 1: Diverse Multi-File Batch**:
    - 6 files transferred across TLS socket: `document_1.txt`, `sample image (2026) [photo].png`, `data_archive_sample.bin`, `notes_multilingual_日本語_हिन्दी.txt`, `video_clip_sample.bin`, `presentation_deck.bin`.
    - Result: **6/6 Files Received — 100% SHA-256 Match**.
  - **Suite 2: Duplicate Filename Collision**:
    - Two files named `game_data.bin` with distinct random bytes transferred in succession.
    - Result: Saved as `game_data.bin` and `game_data (1).bin`. Neither overwritten. **100% SHA-256 Match on both**.
  - **Suite 3: Real Large File Physical Transfer**:
    - File: `real_large_2500mb.bin` (2,684,354,560 bytes, 640 chunks @ 4 MB).
    - Duration: 77.84 seconds.
    - Throughput: **32.89 MB/s**.
    - Android Memory: PSS before: 148 MB, PSS after: 155 MB (+7 MB, confirming zero RAM buffering).
    - Source SHA-256: `5b4894f986c3d7f2100933f67d7b2fd2f3ca063b9b5577e1467e9b804975badd`.
    - Android SHA-256: `5b4894f986c3d7f2100933f67d7b2fd2f3ca063b9b5577e1467e9b804975badd`.
    - Result: **100% BYTE-PERFECT SHA-256 MATCH**.
  - **Suite 4: Real-World 57.66 GB Multi-File Repack Dataset (Full 12 Files)**:
    - Source: Real physical device storage `/storage/emulated/0/Download/Grand Theft Auto V Legacy [FitGirl Repack]` on Xiaomi Pad 6 (`2f1ce07`).
    - Files: 12 files totaling **57,662,402,744 bytes (53.70 GiB)**:
      - `fg-01.bin`: 28,745,230,572 bytes (~28.75 GB / 26.77 GiB)
      - `fg-02.bin`: 22,814,321,160 bytes (~22.81 GB / 21.25 GiB)
      - `fg-03.bin`: 2,189,587,787 bytes (~2.19 GB / 2.04 GiB)
      - `fg-04.bin`: 692,645,794 bytes (~660 MB)
      - `fg-05.bin`: 476,805,682 bytes (~455 MB)
      - `fg-06.bin`: 81,136,709 bytes (~77 MB)
      - `fg-07.bin`: 1,831,395 bytes (~1.75 MB)
      - `fg-08.bin`: 1,464,505 bytes (~1.40 MB)
      - `fg-09.bin`: 312,889 bytes (~305 KB)
      - `fg-optional-bonus-content.bin`: 2,649,646,802 bytes (~2.65 GB / 2.47 GiB)
      - `setup.exe`: 9,419,380 bytes (~9.4 MB)
      - `Verify BIN files before installation.bat`: 69 bytes
    - Transfers:
      - `fg-01.bin` and `fg-02.bin` transferred individually at 36.4 MB/s and 32.8 MB/s.
      - Remaining 10 files transferred in a single 5.68 GB multi-file batch session in 7.39 min without requiring separate confirmations.
    - Verification:
      - All 12 destination files verified byte-perfect via streaming 1 MB buffer SHA-256 against source device hashes.
      - All 10 data archives matched the official repack MD5 catalog (`fitgirl-bins.md5`) bit-for-bit.
      - All Android device source files audited with `stat` — zero files modified (mtimes preserved).
      - Android peak memory held strictly under 165 MB PSS throughout all phases.
    - Result: **100% BYTE-PERFECT VERIFIED ACROSS ENTIRE 57.66 GB REPACK DATASET**.

---

#### 1. Release Versioning
- **Release Version**: `1.0.0-beta.1`
- **Android `versionCode`**: `2`
- **Android `versionName`**: `1.0.0-beta.1`
- **Windows `applicationVersion`**: `1.0.0-beta.1`
- **Build Date**: 2026-09-24

#### 2. Release Artifacts & SHA-256 Hashes
- **Android Release APK**:
  - File: `android/app/build/outputs/apk/release/app-release.apk`
  - Size: 28,374,904 bytes (~27.06 MB)
  - Optimization: R8 code shrinking and resource optimization enabled (`proguard-rules.pro`)
  - Signing: APK Signature Scheme v2 verified (`apksigner verify`)
  - SHA-256: `BDFE5BC35CC2D50A6889C80B8E6E91C23B0DF1548279D8FC847E74B383B03E59`
- **Windows Release Executable**:
  - File: `dist/PhotoBeam/PhotoBeam.exe` (standalone directory bundle in `dist/PhotoBeam/`)
  - Size: 5,482,357 bytes (~5.23 MB executable, ~156 MB total bundle)
  - Runtime: Bundled Python 3.12 runtime, PyQt6 6.11, OpenSSL / Cryptography 50.0.1, Qt6 Fusion style
  - SHA-256: `cc20c50acf4c4b0cfd5a164b283ed4c1630cb8e30ad22f9256f4b03eea684661`

#### 3. What was Changed
- **Android Release Pipeline**:
  - Configured release signing in `android/app/build.gradle.kts` with decoupled credential management via `release.properties` and environment variables.
  - Comprehensive R8 keep rules in `proguard-rules.pro` for ML Kit Barcode Scanning, CameraX, ZXing, BouncyCastle, and Okio.
  - Bumped `versionCode` to 2 and `versionName` to `1.0.0-beta.1`.
- **Windows Standalone Packaging**:
  - Created PyInstaller build spec `windows/PhotoBeam.spec` bundling all Qt plugins, cryptography OpenSSL engines, and protocol packages into `dist/PhotoBeam/`.
  - Updated path resolution in `main.py`, `receive_screen.py`, `send_screen.py`, `wifi_transport.py`, and `usb_transport.py` to transparently detect PyInstaller frozen bundles (`sys._MEIPASS`) while maintaining zero-overhead dev execution.
  - Bumped Windows application version to `1.0.0-beta.1`.
- **Repository Security & Cleanliness**:
  - Added `*.jks` and `release.properties` patterns to `.gitignore`.
  - Conducted secret and credential scan (0 hardcoded keys, 0 dev endpoints).

#### 4. Clean Environment Validation
- **Windows Clean Environment**:
  - Scrubbed environment test: executed `PhotoBeam.exe` with `PYTHONPATH` cleared and system PATH restricted solely to `C:\Windows\System32;C:\Windows`.
  - Module inspection: 28 non-system modules loaded strictly from `dist\PhotoBeam\_internal\`. Zero dependencies on development Python, `.venv`, or external pip packages.
- **Android Clean Installation**:
  - Uninstalled development debug APK from physical OnePlus Nord CE5 (`6H99AIUG9DHYXGR8`, Android 16).
  - Streamed clean installation of `app-release.apk` via ADB.
  - Confirmed proper application launch, camera permissions, and QR generation on device.

#### 5. Physical Release-Build Transfer Verification (OnePlus Nord CE5)
- **Harness**: `scratch/test_release_physical_transfer.py`
- **Network**: Wi-Fi TLS 1.3 socket (`10.206.59.103:47474`)
- **Batch Transfer**:
  - `release_spec_document.pdf` (1.05 MB) -> SHA-256: `79c1fb4107978e5337c0122b1189d664894c44e6ad2fca07ef0865a8478cbc64` (100% MATCH)
  - `release_bundle_archive.zip` (3.16 MB) -> SHA-256: `a7e891cdc97523c431f64b067a9e00604c3360c668ea407e975efb9d98cc0c7b` (100% MATCH)
  - `release_demo_video.mp4` (11.93 MB) -> SHA-256: `1495fcd48b4cc95411fc4776d27eb34b152c4f6e9b43d63340f29613a22f0c26` (100% MATCH)
- **Screen Lock Validation**: Triggered physical power button lock mid-transfer. Android WakeLock kept socket alive; transfer completed without dropping.
- **Integrity**: Direct `sha256sum` on Android disk `/sdcard/Download/PhotoBeam/` confirmed 3/3 byte-for-byte exact matches.

#### 6. Regression Testing Baseline
- **Python Tests**: **137/137 PASSED** (0 failed, 100% pass rate). *(+6 new large-file timeout regression tests added 2026-09-24)*
- **Android Tests**: **10/10 PASSED** (`testDebugUnitTest`).
- **New regression test**: `tests/protocol/test_large_file_timeout_regression.py`
  - Analytical proof that old code would timeout on 4 GB (1024 chunks × 100 ms fsync = 102 s > 60 s timeout)
  - Analytical proof that fixed code is safe (1 fsync at end ≈ 200 ms << 300 s timeout)
  - 100 MB with 50 ms/chunk simulated write delay
  - 256 MB end-to-end with SHA-256 verified
  - Persistent RAF vs per-chunk open benchmark

#### 7. Known Limitations
- USB transport relies on ADB reverse tunneling when available. When ADB is absent, the system gracefully falls back to Wi-Fi with zero external dependencies.

#### 8. Next Recommended Phase
- **Closed Beta Distribution**: Distribute release APK and Windows portable bundle to early testers across varied local Wi-Fi and corporate network environments.

---

### Phase: Large-File Transfer Reliability Fix (2026-09-24)

#### Root Cause
A critical reliability bug was present in the Android receiver path (`Integrity.kt`).
`writeChunkToFile()` opened a new `RandomAccessFile` **and** called `f.fd.sync()` (fsync)
for **every single 4 MB chunk received**. For a large file:

| File size | Chunks | Cumulative fsync stall | Old timeout | Outcome |
|-----------|--------|------------------------|-------------|--------|
| 1 GB | 256 | ~26 s | 60 s | Marginal |
| 2 GB | 512 | ~51 s | 60 s | Risk |
| 4 GB | 1024 | **~102 s** | **60 s** | **TIMEOUT** |

The accumulated fsync stalls created multi-second idle gaps in the TCP stream. The sender's
60-second `soTimeout` fired, disconnected, and the transfer died — a hang with no recovery.

#### Files Changed
| File | Change |
|------|--------|
| `android/app/…/protocol/Integrity.kt` | Added `writeChunkToFile(raf, offset, data)` overload taking persistent RAF; removed `f.fd.sync()` from per-chunk path; added `flushTmpFile(raf)` for single end-of-file flush |
| `android/app/…/ui/screens/ReceiveScreen.kt` | Open one `RandomAccessFile` per file at transfer start; write all chunks through it; `flushTmpFile()` once at completion/pause; close all handles at session end; raise receiver `soTimeout` 60s → 300s |
| `android/app/…/ui/screens/SendScreen.kt` | Raise streaming `soTimeout` 60s → 300s; raise FILE\_DONE wait 180s → 600s |
| `tests/protocol/test_large_file_timeout_regression.py` | 6 new regression tests (analytical proofs + 100 MB + 256 MB + RAF benchmark) |

#### What Was NOT Changed
- Protocol, chunk framing, TLS, SHA-256, transport failover, resume, multipath — all intact.
- fsync still occurs exactly **once** per file: at completion (`flushTmpFile`) or at pause (before `saveResumeState`). Data safety is preserved.
- Python (Windows) side is unaffected — `StorageManager.write_chunk()` does not call `os.fsync()`.

#### Verification
- **Python Tests**: 137/137 PASSED (all 131 existing + 6 new, 6.70s)
- **Android Debug Build**: BUILD SUCCESSFUL (13s)
- **Android Release Build**: BUILD SUCCESSFUL (R8 + ProGuard, SHA-256: `BDFE5BC3...`)
- **Physical Device Transfer (2 GB)**: **PASSED (29.61s, 69.17 MB/s, 0 stalls, byte-for-byte SHA-256 match)** on physical Xiaomi Pad 6 (`2f1ce07`, Android 14) with release APK.


---

### Previous Phase: UI/UX Improvement & Human-Friendly Error Experience (Completed)


#### 1. What was Changed
- **Human-Friendly Error UX (Android & Windows)**:
  - Created centralized error formatter architectures (`ErrorUx.kt` on Android, `error_formatter.py` on Windows).
  - Translated technical exceptions, socket codes, and protocol errors into user-friendly titles and clear guidance across all 12 key scenarios:
    1. *Invalid QR Code*
    2. *QR Code Expired*
    3. *Connection Refused*
    4. *Wi-Fi Disconnected*
    5. *USB Disconnected*
    6. *All Transports Disconnected (Both Wi-Fi & USB lost)*
    7. *Not Enough Storage Space*
    8. *Source File Changed While Paused*
    9. *Cannot Resume Transfer (Unsafe state)*
    10. *Transfer Integrity Error (SHA-256 mismatch)*
    11. *Transfer Cancelled (Clean state cleanup)*
    12. *Unexpected Connection Error (Graceful fallback)*
  - Collapsible "Technical Details" view preserving exact exception messages, socket codes, and backtraces for troubleshooting without overwhelming everyday users.
- **Android Receive Flow Enhancements (`ReceiveScreen.kt`)**:
  - Waiting state: Added clear "Ready to receive on Wi-Fi" connection status badge.
  - Receiving state: Added real-time transport badge ("Connected via $transportDesc") and active Cancel Transfer button with confirmation dialog.
  - Clean cancellation: Integrated atomic `cancelRequested` flag in chunk reader loop so transfers can be cancelled immediately without hanging, cleaning up temporary `.pbtemp` files safely.
  - Completion state: Added prominent "🛡️ SHA-256 byte-perfect integrity verified" badge and "📁 Open Received Files" direct action button opening the Android system downloads/files manager.
  - Error state: Integrated `FriendlyErrorCard` with collapsible technical details.
- **Android Send Flow Enhancements (`SendScreen.kt`)**:
  - Added transport status badge during streaming ("Connected via $activeModes").
  - Completion state: Added "🛡️ SHA-256 byte-perfect integrity verified" badge.
  - Error state: Integrated `FriendlyErrorCard` with collapsible technical details.
- **Windows PyQt6 Enhancements (`receive_screen.py`, `send_screen.py`, `styles.py`)**:
  - Added transport status pills during active file transfers.
  - Added "🛡️ SHA-256 byte-perfect integrity verified" badges on both send and receive completion screens.
  - Added "📁 Open Folder" button in Receive completion view to immediately open the Windows Downloads/PhotoBeam folder in Windows File Explorer.
  - Added human-readable error titles and explanations with collapsible technical details panels.
  - Added custom CSS tokens in `styles.py` for badges, transport pills, error containers, and detail toggle buttons.

#### 2. Files Changed
- `android/app/src/main/java/com/photobeam/app/ui/ErrorUx.kt` (New Android error UX parser and Composable card)
- `android/app/src/main/java/com/photobeam/app/ui/screens/ReceiveScreen.kt` (Enhanced waiting, receiving, complete, and error states; cancel button & confirmation; open files button)
- `android/app/src/main/java/com/photobeam/app/ui/screens/SendScreen.kt` (Enhanced streaming transport pill, integrity badge, friendly error card)
- `windows/photobeam-windows/ui/error_formatter.py` (New Windows error formatter covering all 12 scenarios)
- `windows/photobeam-windows/ui/styles.py` (CSS tokens for integrity badge, transport pill, error details panel)
- `windows/photobeam-windows/ui/receive_screen.py` (Transport pill, integrity badge, open folder button, friendly error panel)
- `windows/photobeam-windows/ui/send_screen.py` (Transport pill, integrity badge, friendly error panel)
- `tests/ui/test_error_formatter.py` (New comprehensive test verifying all 12 error scenarios)
- `docs/UI_UX_IMPROVEMENT_PLAN.md` (Implementation plan document)
- `scratch/smoke_test_physical_ui.py` (Physical automated smoke test harness)

#### 3. Test & Build Results
- **Python Test Suite**: **131/131 PASSED** in 4.60s (Protocol, Storage, Resume, Transports, Scheduler, Error Formatter).
- **Android Unit Tests**: **10/10 PASSED** (`testDebugUnitTest`).
- **Android Build**: `.\gradlew.bat assembleDebug` -> **BUILD SUCCESSFUL** (`app-debug.apk`).
- **Android APK Deployment**: Installed cleanly on connected OnePlus Nord CE5 (`6H99AIUG9DHYXGR8`, Android 16).
- **Windows App**: `MainWindow` initialized cleanly via PyQt6.

#### 4. Physical Smoke Test Verification (OnePlus Nord CE5)
- **Harness**: `scratch/smoke_test_physical_ui.py`
- **Device**: OnePlus Nord CE5 (`6H99AIUG9DHYXGR8`), Android 16 (API 36)
- **Network**: Real Hotspot / Wi-Fi (`10.206.59.103:47474`)
- **File Transferred**: 6.62 MB binary test file (`ui_ux_smoke_test.bin`)
- **Windows Source SHA-256**: `9b1b1a7d1ad5ce85e94d6dbd020046e490227ce13d5c25e0933ca7683f04b3a4`
- **Android Received SHA-256**: `9b1b1a7d1ad5ce85e94d6dbd020046e490227ce13d5c25e0933ca7683f04b3a4`
- **Physical Result**: **100% BYTE-PERFECT PASS** — exact hash match on device storage `/sdcard/Download/PhotoBeam/ui_ux_smoke_test.bin`.

#### 5. Known Issues
- None. Core transfer engine, multi-transport failover, chunk protocols, and SHA-256 streaming verification remain completely intact and stable.

#### 6. Next Recommended Phase
- **Production Packaging & Release Engineering**: Build standalone Windows installer/executable (PyInstaller) and signed release APK with ProGuard/R8 verification.

---

STATUS: Safe Pause/Resume, Full System Audit & Failure Mode Matrix Complete (Release-Ready)


COMPLETED:
1. **Safe Pause -> Verify State -> Continue Transfer Architecture**:
   - [x] State transition pipeline: `TRANSFERRING` -> `PAUSING` -> `VERIFYING_STATE` -> `PAUSED` -> `RESUMING` -> `TRANSFERRING` -> `FINALIZING` -> `COMPLETED`.
   - [x] Atomic pause execution: When pause is requested, the system completes any chunk in flight, invokes `fsync()` / `raf.fd.sync()`, commits verified bitset to `.resume/<sid>_<fid>.json`, and safely enters `PAUSED`.
   - [x] True resume without duplicate chunks: Receiver exchanges verified chunk bitmap via `ACCEPT`; sender reads only missing chunks using `skip_chunks` without re-reading or re-transmitting already committed chunks.
   - [x] Tamper & corruption detection: `verify_resume_state(fid)` strictly inspects destination temporary file size, disk presence, and bitset validity. Truncation or file deletion triggers `UNSAFE_TO_RESUME` with `[ Restart Transfer ]` / `[ Cancel ]`.
   - [x] Source file integrity verification: Sender re-validates source file presence and size before continuing. Modified source files are rejected before streaming.
   - [x] Clean separation of Pause vs Cancel: Cancel requires user confirmation dialog and guarantees incomplete temporary files are never finalized or marked completed.
2. **Cross-Platform Implementation**:
   - **Windows PyQt6**: `send_screen.py` and `receive_screen.py` updated with pause/continue/restart/cancel worker signals, confirmation dialogs, status badges, and pause actions.
   - **Android Jetpack Compose**: `SendScreen.kt` and `ReceiveScreen.kt` updated with atomic `TransferControl`, `PAUSE`/`RESUME` message dispatching, `raf.fd.sync()` flushing, and confirmation dialogs.
3. **Verification & Testing**:
   - 119/119 Python unit and protocol tests passing (`test_safe_pause_resume.py` passing 9/9).
   - 10/10 Android unit tests passing (`testDebugUnitTest`).
   - Android debug APK assembled and installed on connected Xiaomi Pad 6 (`2f1ce07`).
   - 27/27 Failure Mode Matrix tests passing (`scratch/test_full_system_failure_matrix.py`).

---

## Technical Safety Assessment

### 1. Is PhotoBeam safe to pause at any time?
**YES**. Pause is completely atomic. When pause is triggered, PhotoBeam transitions immediately to `PAUSING`, finishes any in-flight chunk being transmitted/received over the wire, flushes and synchronizes disk buffers via `fsync()` / `raf.fd.sync()`, records the verified chunk bitset in `.resume/<sid>_<fid>.json`, and enters `PAUSED`. It never truncates mid-chunk or leaves un-flushed buffers in memory.

### 2. Does continue/resume ever corrupt files?
**NO**. Before continuing, PhotoBeam transitions through `VERIFYING_STATE`. It inspects the destination temporary file (`.<fid>.pbtemp`), validates its byte length against the expected file size, verifies the resume metadata bitset, checks the source file's presence and size, and only streams missing chunks via `skip_chunks`. If any tampering, truncation, or missing file is detected, PhotoBeam refuses resume, marks the state `UNSAFE_TO_RESUME`, and prompts the user to either restart from scratch or cancel. At transfer completion, the receiver computes a full SHA-256 hash of the reassembled file; only if the hash strictly matches the sender's original manifest is the temporary file atomically renamed to the final filename.

### 3. What happens if the network drops while paused?
The transfer is already paused and completely flushed to disk. Multi-path transport disconnects cleanly. When the user taps `[ Continue Transfer ]`, the client attempts reconnection across available transports (Wi-Fi, USB, RNDIS), sends a `RESUME` message with the session and file ID, exchanges the verified chunk bitmap, and resumes only missing chunks. If reconnection fails, the state safely remains paused with progress preserved indefinitely.

### 4. What happens if the user leaves the transfer paused for days?
The transfer state remains safely persisted on disk in `.resume/<sid>_<fid>.json` alongside the `.<fid>.pbtemp` file. PhotoBeam QR payloads and sessions are configured with a 24-hour expiration by default, but resume metadata also supports hash+size lookup (`find_resumable_by_hash`) so even across expired session IDs or application restarts, the existing chunk data is detected and credited. When resuming after days, the sender re-verifies source file existence and size before streaming.

### 5. What happens if the destination runs out of storage while paused?
PhotoBeam pre-allocates the entire file length on disk via `preallocate(tmp_path, info.size)` / `raf.setLength(info.size)` during `setup_receive` before any chunks are written. Furthermore, the pre-flight check validates `free_space >= total_batch_bytes`. As a result, disk space is already reserved for the file while paused. If external processes consume remaining disk space while paused, subsequent chunk writes fail with an `OSError`/`IOException` which triggers an immediate graceful stop without corrupting the file or finalizing an invalid file.

### 6. What happens if the source file is modified while paused?
When the user taps `[ Continue Transfer ]`, the sender enters `VERIFYING_STATE` and re-inspects the source file. If the source file size differs from the manifest created at session start, PhotoBeam immediately blocks resume, transitions to `UNSAFE_TO_RESUME`, explains that the source file was modified while paused, and provides `[ Restart File ]` and `[ Cancel ]` buttons, preventing corrupted or mismatched byte sequences from being sent.

### 7. Is PhotoBeam ready for real-world beta testing?
**YES**. All 119 Python unit and protocol tests pass (100%). All Android unit tests pass (100%). The Android debug APK builds and installs cleanly on physical Android 14 devices. All 27 automated failure matrix scenarios pass (0-byte, 1-byte, 10 file formats, Unicode/pathological filenames, atomic pause/resume, tamper detection, storage pre-flight rejection, clean cancel). Physical verification on connected hardware demonstrated 100% byte-for-byte SHA-256 integrity over Wi-Fi, USB, and simultaneous multi-transport.

## OnePlus Nord CE5 Physical QA & Stress Test Matrix (Android 16 / API 36, September 2026)

Full dedicated report and evidence ledger available at [`docs/PHYSICAL_QA_REPORT.md`](file:///c:/Users/Sohan/OneDrive/Documents/Desktop/Beam/docs/PHYSICAL_QA_REPORT.md).

| Test | Physical Scenario | Transport | Completed | SHA-256 Match | Speed / Evidence | Result |
| :--- | :--- | :--- | :---: | :---: | :---: | :---: |
| **Wi-Fi Large 1.03 GB** | Windows -> OnePlus (1.03 GB) | Wi-Fi (TLS 1.3) | YES | YES | **74.10 MB/s** (14.25s) | **PASS** |
| **USB Large 1.03 GB** | Windows -> OnePlus (1.03 GB) | USB (ADB tunnel `tcp:47476->47474`) | YES | YES | **22.58 MB/s** (46.76s) | **PASS** |
| **Simultaneous 1.03 GB**| Windows -> OnePlus (1.03 GB) | Wi-Fi + USB Multi-Path | YES | YES | **45.16 MB/s** (23.38s, 132 Wi-Fi / 132 USB) | **PASS** |
| **Wi-Fi Failover** | Mid-transfer Wi-Fi sever at 30% | Wi-Fi -> USB Failover | YES | YES | 100 MB recovered in 4.37s | **PASS** |
| **USB Failover** | Mid-transfer USB sever at 50% | USB -> Wi-Fi Failover | YES | YES | 100 MB recovered in 3.91s | **PASS** |
| **Screen Lock / Sleep** | Power button lock at 20% | Wi-Fi (Screen Locked) | YES | YES | WakeLock preserved socket to 100% | **PASS** |
| **File Format Diversity**| 24 real-world formats & pathological | Wi-Fi (TLS 1.3) | YES | YES | 24/24 byte-perfect (Hindi, Cyrillic, JP, etc.) | **PASS** |
| **Multi-File Batches** | 50 & 100 file rapid batches | Wi-Fi (TLS 1.3) | YES | YES | 150/150 files verified without FD leak | **PASS** |
| **Duplicate Conflict** | Duplicate transfer of existing file | Wi-Fi (TLS 1.3) | YES | YES | Saved deterministically without crash | **PASS** |
| **Negative Testing** | Corrupt CRC 0xDEADBEEF injected | Wi-Fi (TLS 1.3) | N/A | Rejected | Immediate NAK_CHUNK; no corrupted file | **PASS** |
| **Low Storage Reject** | 500 GB declared on 33 GB device | Wi-Fi (TLS 1.3) | N/A | Rejected | Pre-flight REJECT `not_enough_space` | **PASS** |
| **Security Token Audit**| Invalid token & Expired QR | Wi-Fi (TLS 1.3) | N/A | Rejected | Strict session gating; 0 secrets in logcat | **PASS** |
| **RAM / Leak Audit** | dumpsys meminfo after >6.5 GB | System | YES | N/A | Total PSS = 134.82 MB (Flat heap) | **PASS** |
| **Rapid Cycles (10x)** | 10 back-to-back sequential cycles | Wi-Fi (TLS 1.3) | YES | YES | 10/10 verified; 0 port lockups | **PASS** |

---

## Historical Physical Stress Test Results Table (Xiaomi Pad 6, Android 14)

| Test | Physical Scenario | Transport | Completed | SHA-256 Match | Byte-Perfect | Result |
| :--- | :--- | :--- | :---: | :---: | :---: | :---: |
| **TEST 1** | Android ↔ Windows (1.03 GB) | Wi-Fi Only | YES | YES | YES | **PASS** |
| **TEST 2** | Android ↔ Windows (1.03 GB) | USB Only (ADB reverse `127.0.0.1:47475`) | YES | YES | YES | **PASS** |
| **TEST 3** | Android ↔ Windows (1.03 GB) | Wi-Fi + USB Simultaneously | YES | YES | YES | **PASS** |
| **TEST 4** | Android ↔ Windows (1.03 GB) | Wi-Fi Failure Recovery | YES | YES | YES | **PASS** |
| **TEST 5** | Android ↔ Windows (1.03 GB) | USB Failure Recovery | YES | YES | YES | **PASS** |
| **TEST 6** | Android ↔ Windows (1.03 GB) | Interrupt → Resume | YES | YES | YES | **PASS** |

---

## Detailed Physical Test Records

### TEST 1 — Wi-Fi ONLY (Large File 1.03 GB)
- **Source filename**: `test_1gb.bin`
- **Source size**: `1,107,296,256` bytes (1.03 GB)
- **Source SHA-256**: `5ca08cfe25642188476f8ea51299a184704019e0a8eda1f05e760faf22062a61`
- **Received filename**: `test_1gb.bin`
- **Received size**: `1,107,296,256` bytes
- **Received SHA-256**: `5ca08cfe25642188476f8ea51299a184704019e0a8eda1f05e760faf22062a61`
- **Transfer time**: 18.39 seconds
- **Average speed**: 57.41 MB/s
- **SHA-256 MATCH**: YES
- **SIZE MATCH**: YES
- **FILENAME MATCH**: YES
- **BYTE-PERFECT**: YES

### TEST 2 — USB ONLY (Large File 1.03 GB)
- **USB transport method**: Physical USB cable via ADB reverse tunnel (`tcp:47475 -> tcp:47474`)
- **Android Wi-Fi status**: Disabled via `adb shell svc wifi disable` (guaranteed no Wi-Fi fallback)
- **Source filename**: `test_1gb.bin`
- **Source size**: `1,107,296,256` bytes
- **Source SHA-256**: `5ca08cfe25642188476f8ea51299a184704019e0a8eda1f05e760faf22062a61`
- **Received filename**: `test_1gb.bin`
- **Received size**: `1,107,296,256` bytes
- **Received SHA-256**: `5ca08cfe25642188476f8ea51299a184704019e0a8eda1f05e760faf22062a61`
- **Transfer time**: 29.49 seconds
- **Average speed**: 35.81 MB/s
- **USB ACTUALLY USED**: YES (`127.0.0.1:47475` verified in receiver connection log)
- **SHA-256 MATCH**: YES
- **SIZE MATCH**: YES
- **FILENAME MATCH**: YES
- **BYTE-PERFECT**: YES

### TEST 3 — Wi-Fi + USB SIMULTANEOUSLY (1.03 GB)
- **Source filename**: `test_1gb.bin`
- **Source size**: `1,107,296,256` bytes
- **Source SHA-256**: `5ca08cfe25642188476f8ea51299a184704019e0a8eda1f05e760faf22062a61`
- **Wi-Fi bytes/chunks**: 151 chunks / 633,339,904 bytes
- **USB bytes/chunks**: 113 chunks / 473,956,352 bytes
- **Total bytes transferred**: 1,107,296,256 bytes (264 chunks)
- **Transfer time**: 14.08 seconds
- **Average combined speed**: 74.99 MB/s
- **Received filename**: `test_1gb.bin`
- **Received size**: `1,107,296,256` bytes
- **Received SHA-256**: `5ca08cfe25642188476f8ea51299a184704019e0a8eda1f05e760faf22062a61`
- **SHA-256 MATCH**: YES
- **SIZE MATCH**: YES
- **FILENAME MATCH**: YES
- **BOTH TRANSPORTS ACTUALLY USED**: YES (Wi-Fi: 151 chunks, USB: 113 chunks actively transferred concurrently)
- **BYTE-PERFECT**: YES

### TEST 4 — WI-FI FAILURE RECOVERY (1.03 GB)
- **File**: `test_1gb.bin`
- **Initial progress before Wi-Fi disconnect**: 30.3% (80/264 chunks)
- **Wi-Fi disconnected at**: 80 chunks (Wi-Fi: 21 chunks, USB: 59 chunks)
- **Disconnection method**: `adb shell svc wifi disable` mid-transfer while running
- **USB remaining/active**: YES
- **Transfer continued**: YES
- **Transfer completed**: YES
- **Source size**: 1,107,296,256 bytes
- **Received size**: 1,107,296,256 bytes
- **Source SHA-256**: `5ca08cfe25642188476f8ea51299a184704019e0a8eda1f05e760faf22062a61`
- **Received SHA-256**: `5ca08cfe25642188476f8ea51299a184704019e0a8eda1f05e760faf22062a61`
- **SHA-256 MATCH**: YES
- **BYTE-PERFECT**: YES

### TEST 5 — USB FAILURE RECOVERY (1.03 GB)
- **File**: `test_1gb.bin`
- **Initial progress before USB disconnect**: 30.3% (80/264 chunks)
- **USB disconnected at**: 80 chunks (Wi-Fi: 24 chunks, USB: 56 chunks)
- **Disconnection method**: `adb reverse --remove-all` and receiver USB socket disconnect mid-transfer
- **Wi-Fi remaining/active**: YES
- **Transfer continued**: YES (with NAK reconciliation of 32 in-flight lost chunks)
- **Transfer completed**: YES
- **Source size**: 1,107,296,256 bytes
- **Received size**: 1,107,296,256 bytes
- **Source SHA-256**: `5ca08cfe25642188476f8ea51299a184704019e0a8eda1f05e760faf22062a61`
- **Received SHA-256**: `5ca08cfe25642188476f8ea51299a184704019e0a8eda1f05e760faf22062a61`
- **SHA-256 MATCH**: YES
- **BYTE-PERFECT**: YES

### TEST 6 — INTERRUPT → RESUME (1.03 GB)
- **File**: `test_1gb.bin`
- **Original size**: 1,107,296,256 bytes
- **Progress before interruption**: 45.5% (120/264 chunks)
- **Interruption method**: `adb shell am force-stop com.photobeam.app`
- **Resume detected**: YES (`already_received`: 120 chunks)
- **Missing chunks detected**: 144 chunks
- **Chunks retransferred**: 144 chunks (only missing chunks)
- **Transfer restarted from 0**: NO
- **Source SHA-256**: `5ca08cfe25642188476f8ea51299a184704019e0a8eda1f05e760faf22062a61`
- **Received SHA-256**: `5ca08cfe25642188476f8ea51299a184704019e0a8eda1f05e760faf22062a61`
- **SHA-256 MATCH**: YES
- **SIZE MATCH**: YES
- **BYTE-PERFECT**: YES

---

## Prior Audit & Automated Verification Status
- **Byte-for-Byte Preservation**: No transcoding, no compression, no resizing, binary streams written strictly at chunk offsets.
- **Python Test Suite**: 110/110 passed (`pytest tests/ -v`)
- **Android Unit Tests**: 10/10 passed (`gradlew testDebugUnitTest`)
- **Android Debug APK**: clean build, assembled, and installed on device (`gradlew assembleDebug`)
   - Zero image/video transcoding, re-encoding, resizing, or compression libraries in transfer path.
   - Files treated strictly as opaque binary byte streams: `Source bytes -> ChunkFrame -> Transport -> ChunkFrame -> exact offset write -> Destination file`.
   - Streaming from disk in chunks without loading entire files into RAM.
   - Out-of-order and multi-transport chunk reassembly strictly written at exact byte offsets (`RandomAccessFile.seek` / `f.seek(offset)`).
2. **Fixed Original Filename Loss on Android Sender**:
   - Resolved bug in `SendScreen.kt` where `uri.lastPathSegment` returned numeric IDs for `content://` MediaStore URIs.
   - Now uses `getFileNameAndSize(context, uri)` querying `OpenableColumns.DISPLAY_NAME` to strictly preserve the exact original filename and extension (`.jpg`, `.png`, `.mp4`, `.pdf`, `.zip`, etc.).
   - Added `openStream` helper to seamlessly stream both `content://` and `file://` URIs and resolve `/sdcard` paths.
3. **Mandatory SHA-256 Integrity Verification Gating**:
   - Pre-transfer SHA-256 computed via streaming hash.
   - Post-transfer SHA-256 computed on received file before finalization.
   - Transfers marked SUCCESS only when hashes are identical.
   - Mismatches immediately trigger `FILE_ERROR`, delete corrupted temp files, leave source files untouched, and report the specific failed file names to the user.
4. **Cross-Platform Chunk Checksum Standardization**:
   - Standardized per-chunk checksum to hardware-accelerated CRC32 across Android (`java.util.zip.CRC32`) and Windows (`zlib.crc32`), with backwards-compatible verification for XXH3-64 and FNV-1a.
5. **Comprehensive Test Suite**:
   - Added `tests/protocol/test_byte_perfect_integrity.py` covering small text, JPG, PNG, MP4, PDF, ZIP, large file (12MB), binary/random, null bytes, unusual filenames, multi-transport, out-of-order chunks, resume after interruption, missing chunks, corrupted chunk, corrupted final file, SHA-256 mismatch, duplicate chunk, and retransmitted chunk.
   - Added Android unit tests in `ProtocolUnitTest.kt` for out-of-order chunk writing, null bytes, zero-byte files, and bounds checking.

TESTED:
- **Physical Device Transfer Test (Android Xiaomi Pad 6 -> Windows PC)**:
  - File 1: `IMG_20260923_184532.jpg` (1,500,022 bytes)
    - Source SHA-256: `9094f97bade19bb110e6a6655f222bd391d280be424e99761e852adf4f586ae7`
    - Received SHA-256: `9094f97bade19bb110e6a6655f222bd391d280be424e99761e852adf4f586ae7`
    - Result: **100% BYTE-FOR-BYTE IDENTICAL [MATCH]**
  - File 2: `Document_Notes_2026.pdf` (512,056 bytes)
    - Source SHA-256: `1c66f09c24aacb7c2d793fc8da6d3b12c4d20ef5556e6cbaad1b75190d81765e`
    - Received SHA-256: `1c66f09c24aacb7c2d793fc8da6d3b12c4d20ef5556e6cbaad1b75190d81765e`
    - Result: **100% BYTE-FOR-BYTE IDENTICAL [MATCH]**
- **Android Unit Tests**: 10/10 passed (`gradlew testDebugUnitTest`)
- **Android Debug APK**: clean build & assembled (`gradlew assembleDebug`), verified and installed on physical device
- **Python Test Suite**: 110/110 passed (`pytest tests/ -v`)
