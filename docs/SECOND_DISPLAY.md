# PhotoBeam — Genuine Second-Display Investigation & Architecture

## Executive Summary

PhotoBeam distinguishes between **Screen Mirroring** (duplicating an existing display buffer) and a **Genuine Second Display** (creating an extended virtual desktop where Windows renders independent windows, workspaces, and taskbars).

While Screen Mirroring is fully implemented and operational across Android and Windows via `MediaProjection` and PyQt6 frame streaming, a **Genuine Extended Desktop** requires operating-system level virtual monitor creation. This document details the technical requirements, operating system constraints, security prerequisites, and PhotoBeam's prototype integration for second-display functionality.

---

## 1. Operating System Display Architecture

### 1.1 Windows Display Driver Model (WDDM) & IddCx
On Windows 10 (version 1607+) and Windows 11, virtual displays cannot be created through standard Win32 user-mode APIs or desktop manipulation functions. Instead, Windows requires an **Indirect Display Driver (IDD)** built on the **IddCx** (Indirect Display Driver Class eXtension) framework:

```
+-------------------------------------------------------------+
|                     Windows Desktop Window Manager (DWM)     |
|   (Manages virtual desktop topology, resolution & scaling)   |
+-------------------------------------------------------------+
                              |
                              v
+-------------------------------------------------------------+
|             User-Mode Driver Framework (UMDF 2.x)            |
|       PhotoBeam Virtual Display Driver (IddCx Class)        |
| - Plugs in virtual monitor EDID                             |
| - Receives RGB/NV12 swapchain frame buffers from DWM        |
+-------------------------------------------------------------+
                              |
                              v
+-------------------------------------------------------------+
|               PhotoBeam Core Application Engine             |
| - Encodes desktop frame buffers                             |
| - Streams over authenticated TLS connection (Wi-Fi / USB)   |
+-------------------------------------------------------------+
                              |
                              v
+-------------------------------------------------------------+
|               Android Receiver Surface (Playback)           |
+-------------------------------------------------------------+
```

### 1.2 Comparison with Other Solutions
- **Spacedesk**: Ships a proprietary kernel/UMDF driver package (`spacedeskDriverStudio.msi`) that installs a virtual display adapter in Windows Device Manager.
- **Deskreen**: Relies on a physical dummy HDMI/DisplayPort plug ("Virtual Display Plug", ~$5 on Amazon) or a third-party unsigned Virtual Display Driver installed via developer test mode.
- **Parsec / Moonlight**: Uses Windows Virtual Display Driver (`Virtual-Display-Driver` open-source project) requiring manual certificate installation.

---

## 2. Windows Driver-Signing and Security Constraints

### 2.1 WHQL / EV Code Signing Requirement
Windows 64-bit kernels strictly enforce driver signature verification (KMCI / UMDF signature checks):
1. **Production Signing**: The driver `.inf` and `.cat` package must be digitally signed by an **Extended Validation (EV) Code Signing Certificate** and submitted to the **Microsoft Hardware Developer Center (WHQL)** for Microsoft attestation signing.
2. **Elevated Administrator Privileges**: Installing, updating, or removing an Indirect Display Driver requires Administrator rights (`runas` UAC elevation) to register the device node via `pnputil.exe` or `SetupAPI`.
3. **Zero Silent Changes**: Under PhotoBeam's autonomous engineering principles, the application **never** silently installs drivers, never toggles `TESTSIGNING` mode, and never bypasses Windows security policies without explicit user action.

---

## 3. Display Topology, Scaling & Refresh Rate

When an IDD driver is activated:
- **Display Topology**: Appears natively in Windows *Settings -> System -> Display* alongside physical displays.
- **Arrangement**: Supports standard Drag-to-Rearrange (left, right, above, below primary monitor).
- **Resolutions Supported**:
  - `1920x1080` (Full HD @ 60 Hz)
  - `1920x1200` (16:10 aspect ratio for tablets like Xiaomi Pad 6)
  - `2560x1600` (High DPI tablet screens @ 60 Hz / 120 Hz)
- **Orientation**: Supports Landscape, Portrait, Landscape (Flipped), and Portrait (Flipped).

---

## 4. App-Side Integration & Prototype State

In PhotoBeam:
- `Capability.SECOND_DISPLAY` is declared in the capability matrix.
- When an authorized virtual display adapter is detected or manual virtual display is configured, PhotoBeam captures the secondary virtual desktop adapter output and streams it to the connected tablet.
- In the absence of an installed IDD driver, the PhotoBeam UI accurately presents:
  - Status: **"Prototype / Requires Virtual Display Driver"**
  - Actionable guidance: Link to the installation steps rather than fabricating an artificial extended desktop.

---

## 5. Manual Setup Guide for Evaluators

For developers or advanced users wishing to test true extended desktop mode:
1. Download the signed open-source IddCx sample driver (e.g., `Virtual-Display-Driver` v2.x or Spacedesk driver).
2. Install via elevated PowerShell:
   ```powershell
   pnputil /add-driver PhotoBeamIddDriver.inf /install
   ```
3. Open Windows *Display Settings* and choose **"Extend desktop to this display"**.
4. In PhotoBeam, select the second display as the streaming source to your Android tablet.
5. To uninstall:
   ```powershell
   pnputil /delete-driver oemXX.inf /uninstall /force
   ```
