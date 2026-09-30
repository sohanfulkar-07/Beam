# PhotoBeam — Release Build & Distribution Guide

## 1. Release Overview & Versions
- **Release Version**: `1.0.0-beta.1`
- **Android `versionCode`**: `2`
- **Android `versionName`**: `1.0.0-beta.1`
- **Windows `applicationVersion`**: `1.0.0-beta.1`
- **Architecture**: Universal APK (Android API 26+) and Standalone 64-bit Directory Bundle (Windows 10/11)

---

## 2. Release Artifacts & Checksums

| Platform | Artifact Path | Size | SHA-256 Checksum |
| :--- | :--- | :--- | :--- |
| **Android Release APK** | `android/app/build/outputs/apk/release/app-release.apk` | 28.37 MB | `26d63be1a4a056698e7d11e5d8843c320d80557d8763c082d90b2b77e89a59b8` |
| **Windows Executable** | `dist/PhotoBeam/PhotoBeam.exe` | 5.48 MB (bundle: ~156 MB) | `cc20c50acf4c4b0cfd5a164b283ed4c1630cb8e30ad22f9256f4b03eea684661` |

---

## 3. Developer Prerequisites
- **Operating System**: Windows 10/11 (for Windows build) and modern Java runtime
- **Java Development Kit**: JDK 21 LTS (e.g. Eclipse Temurin or OpenJDK)
- **Android SDK**: API 34 & 36 build tools (`36.0.0`)
- **Python**: Python 3.12 (64-bit) with `venv`, `pip`
- **Build Tools**:
  - Android: Gradle 8.12 wrapper (`gradlew.bat`)
  - Windows: PyInstaller (`pip install pyinstaller`)

---

## 4. Android Release Build Instructions

### A. Signing Architecture & Keystore Security
PhotoBeam uses a decoupled signing architecture:
- Secrets are NEVER committed to the Git repository.
- Keystore files (`*.keystore`, `*.jks`) and property files (`release.properties`) are strictly ignored via `.gitignore`.
- Build credentials can be injected via local property files or CI/CD environment variables.

### B. Environment Variables for CI/CD
```bash
export PHOTOBEAM_KEYSTORE_FILE="/path/to/release.keystore"
export PHOTOBEAM_KEYSTORE_PASSWORD="your-secure-keystore-password"
export PHOTOBEAM_KEY_ALIAS="photobeam"
export PHOTOBEAM_KEY_PASSWORD="your-secure-key-password"
```

### C. Local Development Signing Setup
To generate a local development/testing release keystore:
```powershell
cd android
& "C:\Users\Sohan\.jdk\jdk-21.0.12.1+1\bin\keytool.exe" -genkeypair -v `
    -keystore app/release-local.keystore `
    -alias photobeam `
    -keyalg RSA `
    -keysize 2048 `
    -validity 10000 `
    -storepass <YOUR_PASSWORD> `
    -keypass <YOUR_PASSWORD> `
    -dname "CN=PhotoBeam, OU=Development, O=PhotoBeam, L=Local, ST=Local, C=US"
```
Create `android/release.properties` (ignored by git):
```properties
photobeam.keystore.file=release-local.keystore
photobeam.keystore.password=<YOUR_PASSWORD>
photobeam.key.alias=photobeam
photobeam.key.password=<YOUR_PASSWORD>
```

### D. Executing the Build
```powershell
cd android
.\gradlew.bat assembleRelease
```
Output: `android/app/build/outputs/apk/release/app-release.apk`

### E. Verifying APK Signature
```powershell
& "C:\Users\Sohan\AppData\Local\Android\Sdk\build-tools\36.0.0\apksigner.bat" verify --verbose android/app/build/outputs/apk/release/app-release.apk
```
Expected output:
```
Verifies
Verified using v2 scheme (APK Signature Scheme v2): true
Number of signers: 1
```

---

## 5. Windows Release Build Instructions

### A. Virtual Environment Setup
```powershell
python -m venv .venv --system-site-packages
.venv\Scripts\pip install pyinstaller
```

### B. Building with PyInstaller
The repository contains a custom PyInstaller specification file: [`windows/PhotoBeam.spec`](file:///c:/Users/Sohan/OneDrive/Documents/Desktop/Beam/windows/PhotoBeam.spec).
```powershell
.venv\Scripts\pyinstaller.exe windows/PhotoBeam.spec --distpath dist --workpath build/pyinstaller -y
```
Output: `dist/PhotoBeam/PhotoBeam.exe` (with all Qt6 DLLs, Python runtime, and protocol packages in `dist/PhotoBeam/_internal/`).

---

## 6. Clean Installation & Clean Environment Validation

### A. Windows Isolated Environment
The packaged executable requires **zero external dependencies**:
- No Python installation required.
- No PyQt6 installation required.
- No development environment variables required.

To test in an isolated shell:
```powershell
$env:PYTHONPATH=""
$env:PATH="C:\Windows\System32;C:\Windows"
& "dist\PhotoBeam\PhotoBeam.exe"
```

### B. Android Clean Installation
```powershell
# Uninstall any previous debug version
adb uninstall com.photobeam.app

# Install the official signed release APK
adb install -r android/app/build/outputs/apk/release/app-release.apk
```

---

## 7. Transport Requirements & Connectivity

### A. Wi-Fi Transport (Zero External Dependencies)
- Both devices must be on the same local subnet (e.g. Wi-Fi access point or phone mobile hotspot).
- Requires TCP port `47474` (default).
- Uses TLS 1.3 with ephemeral self-signed certificates and TOFU (Trust-On-First-Use) SHA-256 fingerprint verification encoded in the QR code.
- Operates 100% offline without internet connection or cloud servers.

### B. USB Transport (ADB Reverse Tunnel)
- USB transfer is enabled when an Android device is connected via USB cable with USB Debugging enabled.
- Uses `adb.exe` to configure reverse socket port forwarding (`adb reverse tcp:47475 tcp:47474`).
- **ADB Dependency Policy**: PhotoBeam does NOT bundle proprietary or third-party ADB binaries. If ADB is detected on the host system (in standard Android SDK paths or system PATH), USB transport is activated automatically; otherwise, PhotoBeam gracefully operates in Wi-Fi-only mode.

---

## 8. Troubleshooting

| Issue | Root Cause | Solution |
| :--- | :--- | :--- |
| `INSTALL_FAILED_UPDATE_INCOMPATIBLE` | Attempting to install Release APK over Debug APK | Run `adb uninstall com.photobeam.app` first to remove the debug signing certificate. |
| R8 strips required classes | ProGuard rules too aggressive | Verify keep rules in `android/app/proguard-rules.pro` for ML Kit, ZXing, CameraX, and BouncyCastle. |
| Windows app fails to find `protocol` | PyInstaller data missing | Ensure `('protocol', 'protocol')` is present in `datas` in `windows/PhotoBeam.spec`. |
| Connection Refused | Firewall blocking TCP 47474 or different subnet | Allow PhotoBeam through Windows Defender Firewall; verify both devices share the same IP subnet. |

---

## 9. Versioning Procedure for Future Releases
1. Update `versionCode` (integer, e.g. `3`) and `versionName` (semver, e.g. `1.0.0-beta.2`) in `android/app/build.gradle.kts`.
2. Update `app.setApplicationVersion("1.0.0-beta.2")` in `windows/photobeam-windows/main.py`.
3. Update `docs/PROJECT_STATE.md` with new build hashes.
4. Build release artifacts using the procedures documented above.
