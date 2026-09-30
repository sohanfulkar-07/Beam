# PhotoBeam — Build Guide

## Prerequisites

| Component | Requirement |
|-----------|------------|
| Android app | Java 11+, Android SDK API 34+, Android Studio (recommended) |
| Windows app | Python 3.10+, pip |
| Protocol tests | Python 3.10+, pip |

Java 25 and Python 3.12 are confirmed available on this machine.

---

## Protocol / Windows App Dependencies

```bash
cd windows/photobeam-windows
pip install -r requirements.txt
```

Or install manually:
```bash
pip install PyQt6 qrcode[pil] pyobjc-core xxhash cryptography pillow pyzbar opencv-python
```

On Windows, `pyzbar` requires: https://github.com/NuGet/Home/releases (zbar DLL)
Alternative: use `opencv-python` + ZBar bundled.

---

## Run Windows App

```bash
cd windows/photobeam-windows
python main.py
```

---

## Protocol Tests

```bash
cd tests/protocol
pip install pytest xxhash cryptography
pytest -v
```

---

## Android App

### From command line (Gradle wrapper)

```bash
cd android
./gradlew assembleDebug        # build APK
./gradlew installDebug         # install to connected device
./gradlew test                 # unit tests
./gradlew connectedAndroidTest # instrumented tests
```

First build downloads Gradle (~100 MB) automatically.

### From Android Studio

Open `android/` folder in Android Studio. Sync Gradle, run.

---

## ADB (for USB transport)

ADB location: `C:\Users\Sohan\AppData\Local\Android\Sdk\platform-tools\adb.exe`

Add to PATH or PhotoBeam will find it automatically via known location.

Enable USB transport:
1. Android: Settings → Developer Options → USB Debugging → ON
2. Connect phone via USB cable
3. Accept RSA key fingerprint on phone
4. `adb devices` should show device
5. PhotoBeam detects ADB automatically

---

## Clean Build

```bash
# Windows app
cd windows/photobeam-windows
find . -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null

# Android
cd android
./gradlew clean
```
