# PhotoBeam UI/UX Improvement Plan

## Objective
Improve user experience and user flow across Android and Windows without destabilizing or modifying the underlying transfer engine, protocol framing, SHA-256 validation, or multi-transport scheduler.

---

## 1. Error UX & Human-Readable Message Architecture
Create dedicated, shared error translation logic for both platforms:
- **Android**: `FriendlyError.kt` with mapper `FriendlyError.from(raw: String)`
- **Windows**: `error_formatter.py` with `format_friendly_error(raw: str) -> FriendlyError`

### Handled Scenarios
1. **Invalid QR**: Clear prompt to re-scan a valid PhotoBeam QR code.
2. **Expired QR**: Explanation that the 24h session expired; prompt to generate a new QR.
3. **Connection refused**: Friendly guidance to verify both devices are on the same Wi-Fi / hotspot.
4. **Wi-Fi disconnected**: Explanation of Wi-Fi loss, failover status to USB if present.
5. **USB disconnected**: Explanation of USB disconnection, failover status to Wi-Fi if present.
6. **Both transports disconnected**: Guidance to reconnect Wi-Fi or USB and restart.
7. **Storage insufficient**: Clear breakdown of required vs available storage space.
8. **File changed**: Explanation that source file was modified/moved while paused; prompt to restart file.
9. **Resume failure**: Explanation that partial resume state could not be verified; prompt to restart transfer.
10. **Corrupted transfer**: Clear notification that checksum validation rejected a corrupted payload; temporary file discarded.
11. **Transfer cancelled**: Non-alarming notification that transfer was cancelled by user.
12. **Unexpected connection failure**: Fallback with friendly description and collapsible technical details.

---

## 2. Android UI Enhancements (`ReceiveScreen.kt`, `SendScreen.kt`)
1. **Receive Flow**:
   - Add explicit transport status badge ("Connected via Wi-Fi", "Connected via USB", "Connected via Wi-Fi + USB").
   - Add **Cancel Button** during active receiving with confirmation dialog.
   - Show speed and ETA cleanly during streaming.
   - Completion view:
     - Clear success checkmark and summary (file count, total bytes, duration).
     - Explicit integrity verification badge: `"✓ SHA-256 byte-perfect integrity verified"`.
     - Action button: `"📁 Open Received Files"` (launches Android Storage/Downloads directory) + `"Done"` and `"View History"`.
   - Error view:
     - Display friendly title and human-readable explanation.
     - Expandable `"Technical details"` section showing raw exception/error string.
2. **Send Flow**:
   - Clear connected-device confirmation banner with transport type.
   - Enhanced file selection list with count, formatted total size, individual `✕` remove buttons, and `"Clear all"`.
   - Active transfer screen with transport badges, progress bars, speed, ETA, pause, and cancel dialog.
   - Completion view with `"✓ SHA-256 byte-perfect integrity verified"`.
   - Error view with friendly message and expandable details.

---

## 3. Windows UI Enhancements (`receive_screen.py`, `send_screen.py`, `styles.py`)
1. **Receive Screen**:
   - Clear transport badge ("Connected via Wi-Fi", "Connected via USB", "Connected via Wi-Fi + USB").
   - Completion view:
     - Explicit integrity verification badge: `"✓ SHA-256 byte-perfect integrity verified"`.
     - Action button: `"📁 Open Folder"` (opens destination directory in Windows File Explorer).
   - Error view with collapsible technical details.
2. **Send Screen**:
   - Transport badge and connection confirmation.
   - Drag & drop feedback, clear file list with individual remove actions.
   - Completion view with integrity verification badge.
   - Error view with collapsible technical details.
3. **Styles**:
   - Stylesheet updates for transport badges, integrity labels, and expandable technical details.

---

## 4. Verification & Testing
1. Run Python protocol test suite (`pytest tests/ -v`).
2. Run Android unit tests (`gradlew testDebugUnitTest`).
3. Build Android debug APK (`gradlew assembleDebug`).
4. Perform physical smoke-test transfer on connected OnePlus Nord CE5 (`6H99AIUG9DHYXGR8`).
5. Confirm 100% byte-for-byte SHA-256 match.
6. Update `docs/PROJECT_STATE.md`.
