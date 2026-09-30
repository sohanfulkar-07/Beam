# PhotoBeam — Release Packaging & Validation Plan

## 1. Objectives
- Produce an official, standalone, optimized Android release APK (`1.0.0-beta.1`) with ProGuard/R8 shrinking and safe local signing.
- Produce a zero-dependency standalone Windows release package (`1.0.0-beta.1`) using PyInstaller bundling PyQt6 and protocol dependencies.
- Validate execution of both release packages in clean/isolated environments without development dependencies.
- Verify byte-perfect physical file transfer between the clean Windows release executable and the physical Android release APK on the connected OnePlus Nord CE5 (`6H99AIUG9DHYXGR8`).

---

## 2. Versioning Strategy
- **Android `versionName`**: `1.0.0-beta.1`
- **Android `versionCode`**: `2` (incremented from initial development baseline `1`)
- **Windows `applicationVersion`**: `1.0.0-beta.1`

---

## 3. Phase Implementation Steps

### Phase 2: Android Release Build
1. **ProGuard / R8 Rules (`proguard-rules.pro`)**:
   - Preserve PhotoBeam application code (`com.photobeam.app.**`).
   - Preserve CameraX lifecycle & camera2 provider (`androidx.camera.**`).
   - Preserve ML Kit barcode scanning (`com.google.mlkit.vision.barcode.**`, `com.google.android.gms.vision.**`).
   - Preserve ZXing QR generator (`com.google.zxing.**`, `com.journeyapps.barcodescanner.**`).
   - Preserve BouncyCastle cryptographic primitives & TLS (`org.bouncycastle.**`).
   - Preserve Okio streaming buffers (`okio.**`).
   - Retain line numbers and annotations for diagnostics (`SourceFile`, `LineNumberTable`, `*Annotation*`).
2. **Release Signing Configuration**:
   - Generate safe local keystore `android/app/release-local.keystore` (RSA 2048-bit).
   - Configure `android/app/build.gradle.kts` to load signing credentials from project properties with fallback.
   - Ensure keystore and credential files are strictly ignored in `.gitignore`.
3. **Compilation**:
   - Run `.\gradlew.bat assembleRelease`.
   - Verify APK output at `android/app/build/outputs/apk/release/app-release.apk`.
   - Record SHA-256 hash of the generated APK.

### Phase 3: Windows Release Package
1. **PyInstaller Setup**:
   - Create local Python `.venv` with PyInstaller.
   - Create spec configuration `windows/PhotoBeam.spec`.
   - Bundle PyQt6 (Fusion style, Qt6 libraries, SVG/image formats).
   - Bundle `protocol/src` and all application UI modules.
   - Bundle application assets (`windows/photobeam-windows/assets`).
   - Configure hidden imports (`cryptography`, `xxhash`, `qrcode`, `PIL`).
2. **Compilation**:
   - Build standalone distribution `dist/PhotoBeam/PhotoBeam.exe`.
   - Record SHA-256 hash of the generated executable.

### Phase 4: Clean Environment Validation
1. **Windows**:
   - Copy packaged application to an isolated clean directory.
   - Launch `PhotoBeam.exe` with a scrubbed environment where `PYTHONPATH` is cleared and Python binary directory is stripped from `PATH`.
   - Verify GUI starts, loads Fusion dark theme, displays Send/Receive cards, and generates QR code.
2. **Android**:
   - Completely uninstall previous debug APK from OnePlus Nord CE5: `adb uninstall com.photobeam.app`.
   - Install release APK: `adb install -r android/app/build/outputs/apk/release/app-release.apk`.
   - Verify application startup, permissions, CameraX QR scanner, and receiver QR generation.
3. **Cross-Platform Physical Smoke Transfer**:
   - Connect Windows release build to OnePlus Nord CE5 release build over TLS Wi-Fi.
   - Stream real test files.
   - Verify 100% byte-perfect SHA-256 match between Windows disk and Android storage (`/sdcard/Download/PhotoBeam/...`).

### Phase 5: Security & Secret Inspection
- Inspect release artifacts for leaked tokens, private keys, debug logging, or hardcoded IP endpoints.

### Phase 6: Documentation & State Update
- Create comprehensive `docs/RELEASE_BUILD.md`.
- Update `docs/PROJECT_STATE.md`.
