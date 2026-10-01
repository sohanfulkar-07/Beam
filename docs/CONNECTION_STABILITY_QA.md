# PhotoBeam — Connection Stability & Flapping Fix QA Report

**Date**: 2026-10-01  
**Author**: Antigravity Autonomous Engineering  
**Target Hardware**: 
- **Mobile Device**: OnePlus Nord CE 5 (`CPH2717`, Android 16, Build `W6403V101P00`, Serial: `6H99AIUG9DHYXGR8`, IP: `10.78.45.185`)
- **Desktop Host**: Lenovo LOQ Windows 11 (`Sohan_LOQ`, IP: `10.78.45.44`)
- **Transports Tested**: Local Wi-Fi (`10.78.45.0/24`) and USB ADB Tunnel (`127.0.0.1:47471` / `127.0.0.1:47475`)

---

## 1. Root Cause Diagnosis of Connection Flapping / Immediate Disconnect

Extensive investigation of the network socket lifecycle, coroutines, thread management, and Android/Windows logs uncovered three distinct root causes triggering the disconnect and flapping loop:

### Root Cause 1: Thread Looper Crash in Android `HomeScreen.kt` UI Callbacks
- **Mechanism**: When `ConnectionManager.connectDevice(...)` completed or failed, its completion callbacks invoked `Toast.makeText(context, ...).show()` directly from background IO coroutine dispatchers (`Dispatchers.IO`).
- **Exact Stack Trace Captured**:
  ```text
  java.lang.NullPointerException: Can't toast on a thread that has not called Looper.prepare()
      at android.widget.Toast$TN.<init>(Toast.java:995)
      at android.widget.Toast.<init>(Toast.java:238)
      at android.widget.Toast.makeText(Toast.java:605)
      at com.photobeam.app.ui.screens.HomeScreenKt$HomeScreen$4$3$1$2$1$1$2.invoke(HomeScreen.kt:294)
      at com.photobeam.app.data.ConnectionManager$connectDevice$1.invokeSuspend(ConnectionManager.kt:429)
  ```
- **Consequence**: The unhandled crash immediately terminated the Android app process (`Process com.photobeam.app has died: prcp CRE`). The operating system kernel immediately issued a TCP `FIN`/`RST` to the Windows host (`[SOCKET_CLOSED_REMOTE]`). Windows detected socket termination and marked the device disconnected. When the Android app was relaunched, it re-attempted connection, crashed again, and established a continuous connection flap cycle.

### Root Cause 2: Stale Session Teardown Race Condition in Session Replacement
- **Mechanism**: Both the Windows and Android connection managers maintain active socket sessions (`ActiveSession`). When a device reconnected or switched transports, the manager replaced the previous session by calling `prevSession.close()`.
- **Defect**: The `on_closed` callback was registered without referencing the specific instance being closed. When `prevSession.close()` asynchronously shut down its underlying socket, its teardown handler triggered `_on_session_closed(device_id)` which removed whatever session was *currently* active in `activeSessions[device_id]` and posted a global `DISCONNECTED` state update.
- **Consequence**: Establishing a new connection actively tore down and invalidated itself milliseconds later, creating a flapping loop between `CONNECTED` and `DISCONNECTED`.

### Root Cause 3: Port Collision Between Persistent Control Plane and File Transfer Plane
- **Mechanism**: Both the persistent control/keepalive socket (`ConnectionManager`) and the file transfer TLS server (`ReceiveScreen` / `TlsServer`) defaulted to TCP port `47474`.
- **Defect**: When the mobile app ran the background control server on port 47474, navigating to the Receive screen or receiving a file resulted in port binding collisions or plaintext control sockets intercepting incoming TLS transfer connections. The sender's TLS client received plaintext or an immediate socket closure (`[SSL: UNEXPECTED_EOF_WHILE_READING]`).

---

## 2. Key Code Changes Across Android & Windows

### A. Android Architecture & Threading Fixes
1. **Thread-Safe UI Dispatching in `HomeScreen.kt`**:
   - Replaced direct background Toast invocations with main looper posting:
     ```kotlin
     android.os.Handler(android.os.Looper.getMainLooper()).post {
         android.widget.Toast.makeText(context, "...", android.widget.Toast.LENGTH_SHORT).show()
     }
     ```
2. **Main-Thread Guarantee for Connection Callbacks in `ConnectionManager.kt`**:
   - Wrapped `notifyConnected`, `notifyFailed`, `notifyError`, and `notifySuccess` in `withContext(Dispatchers.Main)` to guarantee that UI observers and state updates never run on unprepared threads.
3. **Power Management & WakeLocks**:
   - Integrated Android `PowerManager.WakeLock` (`PARTIAL_WAKE_LOCK`) and `WifiManager.WifiLock` (`WIFI_MODE_FULL_HIGH_PERF`) in `ConnectionManager.kt`.
   - WakeLock and WifiLock are acquired as soon as a persistent session is established and held continuously during background idle and active transfers to prevent Android 16 battery optimizations from cutting the Wi-Fi interface.
4. **Port Architecture Separation**:
   - Dedicated Control Plane: TCP `47470` (Wi-Fi) / `47471` (USB ADB Reverse Tunnel).
   - Dedicated Data Transfer Plane: TCP `47474` (Wi-Fi) / `47475` (USB ADB Tunnel).
   - Updated `ConnectionManager.kt` and `DiscoveryService.kt` to bind and advertise `47470` for control and reserve `47474` exclusively for `TlsServer` file transfers.

### B. Windows Architecture & Session Management Fixes
1. **Instance-Aware Session Teardown in `connection_manager.py`**:
   - Updated `on_closed` callback signature to `(session: ActiveSession, reason: str) -> None`.
   - In `_on_session_closed`:
     ```python
     current = self._active_sessions.get(session.device_id)
     if current is not session:
         logger.info("Ignoring close for replaced/stale session on %s", session.device_id)
         return
     ```
     This completely eliminates the race condition where closing a superseded socket dropped the new active session.
2. **Symmetric Keepalive & Ping/Pong Protocol**:
   - Standardized ping interval: **5.0 seconds**.
   - Standardized heartbeat timeout: **18.0 seconds** of inactivity without a pong before declaring a drop.
   - Symmetric handling on both Android (`ActiveSession.kt`) and Windows (`connection_manager.py`) ensures neither side drops prematurely during brief network latency spikes.
3. **Exponential Backoff Reconnection & Duplicate Suppression**:
   - Added backoff intervals (2s -> 4s -> 8s -> max 15s) with jitter.
   - When a session is already active or in-flight, duplicate connection requests from background mDNS discovery or user UI clicks are safely ignored.

---

## 3. Physical Hardware QA Results (OnePlus Nord CE 5 & Windows 11 Host)

All tests executed autonomously against the real physical hardware:

### Test 1: 5-Minute Continuous Idle Stability Test
- **Objective**: Verify that connected devices maintain continuous socket connectivity without flapping or dropping for at least 300 seconds.
- **Execution**: `scratch/test_physical_idle_and_transfer.py`
- **Duration**: **300.0 seconds (5 full minutes)**
- **Total Flaps / State Toggles**: **0**
- **Total Disconnects**: **0**
- **Final Session State**: `is_connected = True`
- **Result**: **PASS (100% Stable)**

### Test 2: Foreground / Background Transition Test
- **Objective**: Verify that locking the phone screen or backgrounding the mobile app for 30 seconds does not kill the socket and connection resumes transparently.
- **Execution**: `scratch/test_foreground_background.py`
- **Action**:
  1. Established connection between Windows and OnePlus device.
  2. Backgrounded mobile app via Android `HOME` intent.
  3. Waited 30 seconds while monitoring heartbeat frames (`PING`/`PONG`).
  4. Restored mobile app to foreground via Android `MAIN` intent.
- **Observations**: Android `WifiLock` and `WakeLock` kept the socket alive throughout the 30-second background period. Continuous pings and pongs were exchanged without socket resets.
- **Post-Resume State**: `is_connected = True` seamlessly without user intervention.
- **Result**: **PASS**

### Test 3: Active Bidirectional 50 MB Transfer Under Load
- **Objective**: Stress-test socket stability under sustained high throughput in both directions and verify bit-for-bit SHA-256 integrity.
- **Execution**: `scratch/test_bidirectional_50mb_load.py`
- **Payload**: Exactly 52,428,800 bytes (50 MB) deterministic binary payload.

#### Direction 1: Android -> Windows (50 MB)
- **File Name**: `payload_50mb_android_to_win.bin`
- **Size**: 52,428,800 bytes
- **Throughput**: **59.87 MB/s** (transferred in 0.84 seconds)
- **Source SHA-256**: `f761b5a3c59f70b14c8b94e04a0ecf743b43b48d2d86a0638e7e55f9bb52d528`
- **Received SHA-256**: `f761b5a3c59f70b14c8b94e04a0ecf743b43b48d2d86a0638e7e55f9bb52d528`
- **Checksum Verification**: **MATCH (100% Byte-for-Byte Identical)**
- **Result**: **PASS**

#### Direction 2: Windows -> Android (50 MB)
- **File Name**: `payload_50mb_win_to_android.bin`
- **Size**: 52,428,800 bytes
- **Throughput**: **35.53 MB/s** (13 chunks streamed in 1.41 seconds)
- **Source SHA-256**: `f761b5a3c59f70b14c8b94e04a0ecf743b43b48d2d86a0638e7e55f9bb52d528`
- **Android SHA-256**: `f761b5a3c59f70b14c8b94e04a0ecf743b43b48d2d86a0638e7e55f9bb52d528`
- **Checksum Verification**: **MATCH (100% Byte-for-Byte Identical)**
- **Result**: **PASS**

---

## 4. Automated Regression Test Verification

1. **Python Protocol & Desktop Test Suite**:
   ```powershell
   & .\.venv\Scripts\python.exe -m pytest tests/ -q
   174 passed in 14.49s
   ```
2. **Android Release Unit Test Suite**:
   ```cmd
   gradlew.bat testReleaseUnitTest
   BUILD SUCCESSFUL in 5s (23 actionable tasks: 4 executed, 19 up-to-date)
   ```
3. **Android Release APK Build**:
   ```cmd
   gradlew.bat assembleRelease
   BUILD SUCCESSFUL in 1m 22s (46 actionable tasks: 8 executed, 38 up-to-date)
   ```
   Installed and verified on physical device `6H99AIUG9DHYXGR8`.

Zero regressions across all test suites.
