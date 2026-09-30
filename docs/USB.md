# PhotoBeam — USB Transport

## Summary

True USB bulk transfer between Android and Windows requires platform support
that is not trivially available in user-space on Windows without a signed kernel driver.

## Options Investigated

### Option 1: Android Open Accessory (AOA)
- Requires Windows-side USB host accessory driver
- Not available without a custom WinUSB driver + INF file
- Complex setup; requires device-specific driver signing
- **Verdict: Too complex for v1**

### Option 2: libusb + WinUSB
- libusb can talk to Android in AOA mode
- Requires WinUSB as the backend driver
- Requires driver installation on Windows (admin rights)
- **Verdict: Viable for v2 if there is demand**

### Option 3: ADB Tunneling (Developer Mode USB) ✓ CURRENT IMPLEMENTATION
- ADB creates a forwarded TCP socket over the physical USB cable:
  - Windows client -> Android receiver: `adb forward tcp:47475 tcp:47474`
  - Android client -> Windows receiver: `adb reverse tcp:47475 tcp:47474`
- Traffic flows over the physical USB cable via the ADB daemon multiplexer.
- Requires USB debugging enabled on Android and ADB available on Windows.
- Completely optional: if ADB or device is missing, app seamlessly falls back to Wi-Fi.

### Option 4: USB Tethering / RNDIS / NCM (Zero-Driver, Zero-ADB USB Mode)
- Built into all modern Android devices and supported out-of-the-box by Windows.
- Toggling "USB Tethering" on Android creates a virtual network interface on Windows (RNDIS/NCM).
- Gives Android and Windows a dedicated high-speed point-to-point IP subnet (typically `192.168.42.x`).
- PhotoBeam automatically includes all non-loopback IP interfaces in the QR payload.
- Connections routed over the tethered subnet travel directly through the physical USB cable at USB 2.0/3.0 speeds.
- Does NOT require ADB, root, or developer options.

## Current Architecture & Status

1. **Protocol Multi-Path Scheduling**: Fully working and verified. The `Scheduler` dynamically round-robins and weights chunks across multiple transports (`test_transfer_multi_path_simultaneous_transports` passing).
2. **ADB Transport**: Implemented in `windows/photobeam-windows/transport/usb_transport.py` using `adb forward` / `adb reverse`.
3. **USB Tethering**: Discovered automatically by IP subnet scanning in `tls_utils.py`.
4. **Hardware Constraint**: Direct raw USB bulk transfer requires signed kernel drivers (WinUSB) on Windows, replacing standard MTP. USB Tethering and ADB tunneling are the two viable user-space alternatives on stock Android + Windows.

