import os
import sys
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"

ROOT = Path(__file__).resolve().parent.parent
WIN_DIR = ROOT / "windows" / "photobeam-windows"
PROTO_DIR = ROOT / "protocol"

for p in (str(WIN_DIR), str(PROTO_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)
