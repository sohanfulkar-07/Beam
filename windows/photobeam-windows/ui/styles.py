"""
PhotoBeam Windows UI — Stylesheet (Dark Theme)
"""

STYLESHEET = """
/* ── Global ─────────────────────────────────────────────────── */
QMainWindow, QWidget {
    background-color: #0f1117;
    color: #e2e8f0;
    font-family: "Segoe UI", "Inter", sans-serif;
    font-size: 14px;
}

/* ── Cards / Panels ──────────────────────────────────────────── */
QFrame#card {
    background-color: #1a1f2e;
    border-radius: 16px;
    border: 1px solid #2d3748;
}

QFrame#hero {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 #1a1f2e, stop:1 #0f1117);
    border-radius: 20px;
    border: 1px solid #2d3748;
}

/* ── Title / Labels ──────────────────────────────────────────── */
QLabel#title {
    font-size: 36px;
    font-weight: 700;
    color: #ffffff;
}

QLabel#subtitle {
    font-size: 15px;
    color: #94a3b8;
}

QLabel#heading {
    font-size: 22px;
    font-weight: 600;
    color: #ffffff;
}

QLabel#info {
    font-size: 13px;
    color: #64748b;
}

QLabel#status_ok {
    font-size: 13px;
    color: #4ade80;
}

QLabel#status_err {
    font-size: 13px;
    color: #f87171;
}

QLabel#speed {
    font-size: 20px;
    font-weight: 600;
    color: #60a5fa;
}

/* ── Primary Button ──────────────────────────────────────────── */
QPushButton#primary {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #3b82f6, stop:1 #6366f1);
    color: #ffffff;
    border: none;
    border-radius: 12px;
    padding: 14px 32px;
    font-size: 16px;
    font-weight: 600;
    min-width: 180px;
}
QPushButton#primary:hover {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #60a5fa, stop:1 #818cf8);
}
QPushButton#primary:pressed {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #2563eb, stop:1 #4f46e5);
}
QPushButton#primary:disabled {
    background: #374151;
    color: #6b7280;
}

/* ── Secondary Button ────────────────────────────────────────── */
QPushButton#secondary {
    background-color: #1e293b;
    color: #cbd5e1;
    border: 1px solid #334155;
    border-radius: 10px;
    padding: 10px 24px;
    font-size: 14px;
}
QPushButton#secondary:hover {
    background-color: #273549;
    border-color: #4a6489;
}
QPushButton#secondary:pressed {
    background-color: #1a2436;
}

/* ── Back Button ─────────────────────────────────────────────── */
QPushButton#back {
    background: transparent;
    color: #64748b;
    border: none;
    padding: 4px 8px;
    font-size: 14px;
}
QPushButton#back:hover {
    color: #94a3b8;
}

/* ── Progress Bar ────────────────────────────────────────────── */
QProgressBar {
    background-color: #1e293b;
    border-radius: 6px;
    height: 12px;
    text-align: center;
    color: transparent;
}
QProgressBar::chunk {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #3b82f6, stop:1 #6366f1);
    border-radius: 6px;
}

/* ── Transfer Item ───────────────────────────────────────────── */
QFrame#transfer_item {
    background-color: #1e293b;
    border-radius: 10px;
    border: 1px solid #2d3748;
}

/* ── QR Display ──────────────────────────────────────────────── */
QLabel#qr_label {
    background-color: #ffffff;
    border-radius: 12px;
    padding: 12px;
}

/* ── Scroll Area ─────────────────────────────────────────────── */
QScrollArea {
    background: transparent;
    border: none;
}
QScrollArea > QWidget > QWidget {
    background: transparent;
}
QScrollBar:vertical {
    background: #1e293b;
    width: 8px;
    border-radius: 4px;
}
QScrollBar::handle:vertical {
    background: #334155;
    border-radius: 4px;
    min-height: 20px;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}

/* ── Badges & Status Pills ─────────────────────────────────────── */
QLabel#integrity_badge {
    background-color: rgba(74, 222, 128, 0.15);
    color: #4ade80;
    font-size: 13px;
    font-weight: 600;
    border-radius: 8px;
    padding: 6px 14px;
}

QLabel#transport_pill {
    background-color: rgba(96, 165, 250, 0.15);
    color: #60a5fa;
    font-size: 12px;
    font-weight: 600;
    border-radius: 8px;
    padding: 4px 10px;
}

/* ── Expandable Details ──────────────────────────────────────── */
QPushButton#details_btn {
    background: transparent;
    color: #94a3b8;
    border: none;
    font-size: 12px;
    font-weight: 500;
    padding: 4px 8px;
}
QPushButton#details_btn:hover {
    color: #cbd5e1;
}

QFrame#details_panel {
    background-color: #0b0e14;
    border: 1px solid #1e293b;
    border-radius: 8px;
    padding: 10px;
}

/* ── Status Badges & Pills ───────────────────────────────────── */
QLabel#badge_green {
    background-color: rgba(74, 222, 128, 0.15);
    color: #4ade80;
    font-size: 12px;
    font-weight: 600;
    border-radius: 6px;
    padding: 3px 8px;
}
QLabel#badge_blue {
    background-color: rgba(96, 165, 250, 0.15);
    color: #60a5fa;
    font-size: 12px;
    font-weight: 600;
    border-radius: 6px;
    padding: 3px 8px;
}
QLabel#badge_gray {
    background-color: rgba(148, 163, 184, 0.15);
    color: #94a3b8;
    font-size: 12px;
    font-weight: 600;
    border-radius: 6px;
    padding: 3px 8px;
}
QLabel#badge_orange {
    background-color: rgba(251, 146, 60, 0.15);
    color: #fb923c;
    font-size: 12px;
    font-weight: 600;
    border-radius: 6px;
    padding: 3px 8px;
}
QPushButton#action_sm {
    background-color: #1e293b;
    color: #e2e8f0;
    border: 1px solid #334155;
    border-radius: 8px;
    padding: 6px 14px;
    font-size: 13px;
    font-weight: 500;
}
QPushButton#action_sm:hover {
    background-color: #273549;
    border-color: #4a6489;
}
QPushButton#action_primary_sm {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #3b82f6, stop:1 #6366f1);
    color: #ffffff;
    border: none;
    border-radius: 8px;
    padding: 6px 14px;
    font-size: 13px;
    font-weight: 600;
}
QPushButton#action_primary_sm:hover {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #60a5fa, stop:1 #818cf8);
}
"""
