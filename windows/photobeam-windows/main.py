"""
PhotoBeam Windows Application
Entry point — starts the PyQt6 GUI
"""
import sys
import os

# Ensure paths are configured
if getattr(sys, 'frozen', False):
    base_dir = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    proto_dir = os.path.join(base_dir, 'protocol')
    if proto_dir not in sys.path:
        sys.path.append(proto_dir)
    if base_dir not in sys.path:
        sys.path.insert(0, base_dir)
else:
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
    proto_dir = os.path.join(repo_root, 'protocol')
    win_dir = os.path.dirname(os.path.abspath(__file__))
    if proto_dir not in sys.path:
        sys.path.append(proto_dir)
    if win_dir not in sys.path:
        sys.path.insert(0, win_dir)

import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QTimer
from pathlib import Path
from ui.main_window import MainWindow


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("PhotoBeam")
    app.setApplicationVersion("1.0.0-beta.1")
    app.setOrganizationName("PhotoBeam")

    # Apply dark theme by default (will respect system in future)
    app.setStyle("Fusion")

    window = MainWindow()

    if "--screen" in sys.argv:
        idx = sys.argv.index("--screen")
        if idx + 1 < len(sys.argv):
            scr = sys.argv[idx + 1].lower()
            if scr == "receive":
                window._show_receive()
            elif scr == "send":
                window._show_send()

    if "--connect-uri" in sys.argv:
        idx = sys.argv.index("--connect-uri")
        if idx + 1 < len(sys.argv):
            uri = sys.argv[idx + 1]
            window._show_send()
            window._send._qr_input.setText(uri)

    if "--send-files" in sys.argv:
        idx = sys.argv.index("--send-files")
        if idx + 1 < len(sys.argv):
            f_arg = sys.argv[idx + 1]
            paths = [Path(f.strip()) for f in f_arg.split(";") if f.strip()]
            window._send._add_files(paths)
            QTimer.singleShot(1500, window._send._start_send)

    if "--pair" in sys.argv:
        QTimer.singleShot(500, window._show_pairing_dialog)

    window.show()
    sys.exit(app.exec())



if __name__ == "__main__":
    main()
