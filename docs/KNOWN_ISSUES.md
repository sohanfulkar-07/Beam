# PhotoBeam — Known Issues

## Current

| ID | Severity | Description | Workaround |
|----|----------|-------------|------------|
| KI-001 | INFO | No system Gradle installed | Android project uses Gradle wrapper (gradlew) — auto-downloads on first build |
| KI-002 | INFO | No .NET SDK installed | Windows app uses PyQt6 instead of WPF/MAUI |
| KI-003 | MEDIUM | USB transport on Windows↔Android requires ADB reverse tunnel | True USB bulk transfer not supported without a kernel driver (WinUSB + libusb path documented in USB.md) |
| KI-004 | LOW | ADB not on system PATH | Full path: C:\Users\Sohan\AppData\Local\Android\Sdk\platform-tools\adb.exe |

## USB Limitation Detail (KI-003)

Direct USB bulk transfer between Android and Windows requires:
- Android side: AOA (Android Open Accessory) protocol or custom USB function
- Windows side: WinUSB driver or libusb

Neither is trivially available without a signed kernel driver on Windows.

**Current plan:** Use ADB reverse port forwarding as the USB transport mechanism.
- ADB creates a forwarded TCP socket over the USB cable
- PhotoBeam treats this as a second TCP transport (effectively USB-speed TCP)
- This is real USB hardware utilization — the data travels over the USB cable
- ADB must be installed; PhotoBeam detects ADB presence and enables USB transport accordingly
- On Windows→Windows USB: use named pipe or loopback (not applicable)

This is documented honestly. The abstraction allows future replacement with true AOA/WinUSB when available.

## Resolved

| ID | Description | Resolution |
|----|-------------|------------|
| RES-001 | Kotlin embeddable compiler in Gradle 8.12 incompatible with JDK 25 (`IllegalArgumentException: 25.0.4`) | Installed Eclipse Adoptium Temurin OpenJDK 21 LTS at `C:\Users\Sohan\.jdk\jdk-21.0.12.1+1` and configured `org.gradle.java.home` in `android/gradle.properties`. |
| RES-002 | Duplicate `META-INF/versions/9/OSGI-INF/MANIFEST.MF` across BouncyCastle jars (`bcpkix`, `bcutil`, `bcprov`) | Added `packaging { resources { excludes += "META-INF/versions/**" ... } }` in `android/app/build.gradle.kts`. |
| RES-003 | AGP warning on `compileSdk = 36` | Added `android.suppressUnsupportedCompileSdk=36` to `android/gradle.properties`. |
| RES-004 | Android local JVM unit tests failing on `org.json.JSONObject` not mocked | Added `testImplementation("org.json:json:20240303")` to `android/app/build.gradle.kts`. |
| RES-005 | Deprecated UTC time calculation in `tls_utils.py` and PKCS#8 private key format | Refactored `tls_utils.py` to use `datetime.now(datetime.timezone.utc)` and PKCS#8 encoding. |

