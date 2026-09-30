# -*- mode: python ; coding: utf-8 -*-
import sys
import os
from pathlib import Path

block_cipher = None

# Absolute paths
REPO_ROOT = Path(r"C:\Users\Sohan\OneDrive\Documents\Desktop\Beam").resolve()
WIN_DIR = REPO_ROOT / "windows" / "photobeam-windows"
PROTO_DIR = REPO_ROOT / "protocol"

datas = [
    (str(PROTO_DIR), "protocol"),
    (str(WIN_DIR / "ui"), "ui"),
    (str(WIN_DIR / "transport"), "transport"),
    (str(WIN_DIR / "connection_manager.py"), "."),
    (str(WIN_DIR / "pairing_manager.py"), "."),
    (str(WIN_DIR / "discovery.py"), "."),
    (str(WIN_DIR / "history_manager.py"), "."),
]


hidden_imports = [
    "PyQt6",
    "PyQt6.QtCore",
    "PyQt6.QtGui",
    "PyQt6.QtWidgets",
    "cryptography",
    "cryptography.hazmat.primitives",
    "cryptography.hazmat.primitives.asymmetric",
    "cryptography.hazmat.primitives.asymmetric.rsa",
    "cryptography.hazmat.primitives.asymmetric.padding",
    "cryptography.hazmat.primitives.hashes",
    "cryptography.hazmat.primitives.serialization",
    "cryptography.x509",
    "cryptography.x509.oid",
    "xxhash",
    "qrcode",
    "qrcode.image.pil",
    "PIL",
    "PIL.Image",
    "history_manager",
    "pairing_manager",
    "discovery",
    "connection_manager",
    "ui.error_formatter",
    "ui.styles",
    "ui.main_window",
    "ui.home_screen",
    "ui.receive_screen",
    "ui.send_screen",
    "ui.history_screen",
    "ui.pairing_dialog",
    "ui.screen_viewer",
    "transport.tls_utils",
    "transport.wifi_transport",
    "transport.usb_transport",
    "src.models",
    "src.session",
    "src.qr_payload",
    "src.scheduler",
    "src.resume",
    "src.storage",
    "src.transfer",
    "src.transport",
    "src.integrity",
]

a = Analysis(
    [str(WIN_DIR / "main.py")],
    pathex=[str(WIN_DIR), str(PROTO_DIR), str(REPO_ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PhotoBeam",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # Windowed GUI application
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="PhotoBeam",
)
