"""
Live real-device physical test verifying:
1. QR generation and READY/WAITING FOR DEVICE state.
2. Android QR scan and connection -> CONNECTING -> CONNECTED.
3. Transfer start -> TRANSFERRING with progress and speed updating.
4. Completion -> COMPLETED.
5. NO 'Connection error' displayed on the laptop UI.
6. Byte-for-byte file integrity (size + SHA-256).
"""
import hashlib
import os
import subprocess
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Add paths
ROOT = Path(__file__).resolve().parent.parent
WIN_DIR = ROOT / "windows" / "photobeam-windows"
PROTO_DIR = ROOT / "protocol"

for p in (str(WIN_DIR), str(PROTO_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

from PyQt6.QtWidgets import QApplication
# pyrefly: ignore [missing-import]
from ui.receive_screen import ReceiveScreen

ADB = r"C:\Users\Sohan\AppData\Local\Android\Sdk\platform-tools\adb.exe"


def get_connected_device():
    res = subprocess.run([ADB, "devices"], capture_output=True, text=True, check=True)
    lines = [line.strip() for line in res.stdout.strip().splitlines() if line.strip() and not line.startswith("List of")]
    for line in lines:
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            return parts[0]
    return None


def run_test():
    device = get_connected_device()
    if not device:
        print("[ERROR] No Android device connected via ADB!")
        return 1

    print(f"[TEST] Using connected Android device: {device}")

    # Determine which test file exists on Android
    candidate_files = ["photobeam_e2e_test.bin", "LICENSE.md"]
    test_filename = None
    for cand in candidate_files:
        check = subprocess.run([ADB, "-s", device, "shell", f"[ -f '/sdcard/Download/{cand}' ] && echo 1"], capture_output=True, text=True)
        if "1" in check.stdout:
            test_filename = cand
            break

    assert test_filename, f"None of candidates {candidate_files} found on /sdcard/Download/"
    print(f"[TEST] Selected test file on Android: {test_filename}")

    # Query expected sha256 and size from Android
    sha_res = subprocess.run([ADB, "-s", device, "shell", f"sha256sum '/sdcard/Download/{test_filename}'"], capture_output=True, text=True, check=True)
    expected_sha = sha_res.stdout.split()[0].strip().lower()

    size_res = subprocess.run([ADB, "-s", device, "shell", f"stat -c %s '/sdcard/Download/{test_filename}'"], capture_output=True, text=True, check=True)
    expected_size = int(size_res.stdout.strip())
    print(f"[TEST] Source on Android: {test_filename} | Size: {expected_size} | SHA-256: {expected_sha}")

    # Clear previous error log and QR uri
    last_err_file = Path.home() / ".photobeam" / "last_error.log"
    if last_err_file.exists():
        last_err_file.unlink()

    qr_file = Path.home() / ".photobeam" / "current_qr_uri.txt"
    if qr_file.exists():
        qr_file.unlink()

    dest_dir = ROOT / "tests" / "scratch_receive_live"
    dest_dir.mkdir(parents=True, exist_ok=True)
    target_dest_file = dest_dir / test_filename
    if target_dest_file.exists():
        target_dest_file.unlink()

    app = QApplication.instance() or QApplication(sys.argv)

    # Initialize ReceiveScreen with destination
    screen = ReceiveScreen()
    screen._dest_dir = dest_dir
    screen.show()
    screen._reset_to_start()

    observed_states = []
    error_occurred = []

    def record_badge():
        txt = screen._conn_badge.text()
        if not observed_states or observed_states[-1] != txt:
            observed_states.append(txt)
            print(f"[UI BADGE STATE] {txt}")

    screen._worker.error.connect(lambda err: error_occurred.append(err))

    # Pump until QR is ready
    start_wait = time.time()
    qr_uri = ""
    while time.time() - start_wait < 15.0:
        app.processEvents()
        record_badge()
        if qr_file.exists():
            qr_uri = qr_file.read_text(encoding="utf-8").strip()
            if qr_uri:
                break
        time.sleep(0.05)

    assert qr_uri, "QR URI was not generated within 15 seconds"
    print(f"[TEST] QR URI generated successfully: {qr_uri[:40]}...")
    record_badge()
    assert "READY" in screen._conn_badge.text(), f"Expected READY in badge, got: {screen._conn_badge.text()}"

    # Auto accept transfer when prompt arrives
    screen._worker.incoming_prompt.connect(lambda c, b: screen._on_accept())

    # Launch Android sender with the QR URI and stream URI
    stream_uri = f"file:///sdcard/Download/{test_filename}"
    print(f"[TEST] Commanding Android to connect with QR and send {test_filename}...")
    start_cmd = [
        ADB, "-s", device, "shell", "am", "start",
        "-S", "-W",
        "-n", "com.photobeam.app/.MainActivity",
        "--es", "screen", "send",
        "--es", "qr_uri", qr_uri,
        "--esa", "stream_uris", stream_uri,
    ]
    res = subprocess.run(start_cmd, capture_output=True, text=True)
    print(f"[TEST] am start result: {res.stdout.strip()[:120]}")

    # Wait for transfer to progress and complete
    transfer_start = time.time()
    transfer_done = False
    last_speed_log = time.time()

    while time.time() - transfer_start < 45.0:
        app.processEvents()
        record_badge()

        if time.time() - last_speed_log > 1.0:
            last_speed_log = time.time()
            if screen._speed_label.text():
                print(f"[UI PROGRESS] Speed: {screen._speed_label.text()} | Bytes: {screen._bytes_label.text()}")

        if "COMPLETED" in screen._conn_badge.text() and screen._complete_container.isVisible():
            transfer_done = True
            break

        if error_occurred or screen._error_container.isVisible():
            print(f"[ERROR DETECTED] Error container visible! Error message: {error_occurred}")
            break

        time.sleep(0.05)

    print("\n--- RESULTS ---")
    print(f"Observed states: {observed_states}")
    print(f"Error occurred: {error_occurred}")
    print(f"Transfer completed: {transfer_done}")

    assert not error_occurred, f"Connection error occurred during transfer: {error_occurred}"
    assert not screen._error_container.isVisible(), "Error container is visible on ReceiveScreen!"
    assert transfer_done, "Transfer did not complete within timeout!"

    # Verify state progression
    state_names = " -> ".join(observed_states)
    print(f"[STATE PROGRESSION] {state_names}")
    assert any("READY" in s for s in observed_states), "Did not observe READY state"
    assert any("CONNECTED" in s for s in observed_states), "Did not observe CONNECTED state"
    assert any("TRANSFERRING" in s for s in observed_states), "Did not observe TRANSFERRING state"
    assert any("COMPLETED" in s for s in observed_states), "Did not observe COMPLETED state"

    # Verify received file integrity
    assert target_dest_file.exists(), f"Target file was not saved at {target_dest_file}"
    actual_size = target_dest_file.stat().st_size
    print(f"[INTEGRITY] File size: {actual_size} bytes (expected {expected_size})")
    assert actual_size == expected_size, f"Size mismatch: got {actual_size}, expected {expected_size}"

    h = hashlib.sha256()
    with open(target_dest_file, "rb") as f:
        while chunk := f.read(1024 * 1024):
            h.update(chunk)
    actual_sha = h.hexdigest().lower()
    print(f"[INTEGRITY] SHA-256: {actual_sha}")
    print(f"[INTEGRITY] Expected: {expected_sha}")
    assert actual_sha == expected_sha, f"SHA-256 mismatch! Got {actual_sha}, expected {expected_sha}"

    print("\n[SUCCESS] Test passed with ZERO connection errors and byte-perfect file transfer!")
    screen._stop_worker()
    return 0


if __name__ == "__main__":
    sys.exit(run_test())
