"""
PhotoBeam Windows UI — Stylesheet (Neo-Tactile Dark Blue Dashboard)
Inspired by modern polished dark-blue desktop dashboards:
- Dark navy/slate background (#0A0E1A to #101726)
- Sidebar navigation with active gradient pills
- Top bar with breadcrumbs and live status badge
- Modular rounded cards (#121A2D) with 1px border (#1E2D4A) and cyan glows (#00E5FF / #38BDF8)
- Tactile action buttons, dropzones, and styled progress bars
"""

STYLESHEET = """
/* ── Global ─────────────────────────────────────────────────── */
QMainWindow, QWidget {
    background-color: #0A0E1A;
    color: #F8FAFC;
    font-family: "Segoe UI", "Inter", -apple-system, sans-serif;
    font-size: 14px;
}

/* ── Left Sidebar Navigation ─────────────────────────────────── */
QFrame#sidebar {
    background-color: #0B101E;
    border-right: 1px solid #1A253D;
    min-width: 220px;
    max-width: 240px;
}

QLabel#sidebar_logo_text {
    font-size: 18px;
    font-weight: 700;
    color: #F8FAFC;
}

QLabel#sidebar_logo_sub {
    font-size: 11px;
    font-weight: 500;
    color: #38BDF8;
}

QPushButton#nav_btn {
    background: transparent;
    color: #94A3B8;
    border: none;
    border-radius: 12px;
    padding: 10px 16px;
    font-size: 14px;
    font-weight: 600;
    text-align: left;
}
QPushButton#nav_btn:hover {
    background-color: #141C2E;
    color: #F8FAFC;
}
QPushButton#nav_btn:checked, QPushButton#nav_btn[active="true"] {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #2563EB, stop:1 #3B82F6);
    color: #FFFFFF;
    font-weight: 700;
}

/* ── Top Bar ─────────────────────────────────────────────────── */
QFrame#topbar {
    background-color: #0D1322;
    border-bottom: 1px solid #1A253D;
}

QLabel#breadcrumb {
    font-size: 13px;
    color: #64748B;
    font-weight: 500;
}

QLabel#breadcrumb_active {
    font-size: 13px;
    color: #38BDF8;
    font-weight: 600;
}

/* ── Cards / Panels ──────────────────────────────────────────── */
QFrame#card {
    background-color: #121A2D;
    border-radius: 18px;
    border: 1px solid #1E2D4A;
}

QFrame#hero_card {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 #17243C, stop:0.5 #121A2D, stop:1 #0C1220);
    border-radius: 20px;
    border: 1px solid rgba(56, 189, 248, 0.25);
}

QFrame#dropzone {
    background-color: #0E1524;
    border: 2px dashed #2A3F66;
    border-radius: 16px;
}
QFrame#dropzone:hover {
    background-color: #141E34;
    border: 2px dashed #38BDF8;
}

/* ── Title / Labels ──────────────────────────────────────────── */
QLabel#hero_title {
    font-size: 26px;
    font-weight: 800;
    color: #FFFFFF;
}

QLabel#title {
    font-size: 22px;
    font-weight: 700;
    color: #FFFFFF;
}

QLabel#subtitle {
    font-size: 13px;
    color: #94A3B8;
}

QLabel#heading {
    font-size: 17px;
    font-weight: 600;
    color: #F8FAFC;
}

QLabel#info {
    font-size: 13px;
    color: #64748B;
}

QLabel#speed {
    font-size: 22px;
    font-weight: 700;
    color: #00E5FF;
}

/* ── Primary Tactile Button ──────────────────────────────────── */
QPushButton#primary {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #2563EB, stop:1 #3B82F6);
    color: #FFFFFF;
    border: 1px solid rgba(56, 189, 248, 0.3);
    border-radius: 12px;
    padding: 12px 28px;
    font-size: 15px;
    font-weight: 600;
    min-height: 22px;
}
QPushButton#primary:hover {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #3B82F6, stop:1 #60A5FA);
    border-color: #00E5FF;
}
QPushButton#primary:pressed {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #1D4ED8, stop:1 #2563EB);
}
QPushButton#primary:disabled {
    background: #1E283D;
    color: #64748B;
    border: 1px solid #2B3A54;
}

/* ── Secondary Button ────────────────────────────────────────── */
QPushButton#secondary {
    background-color: #172238;
    color: #E2E8F0;
    border: 1px solid #2A3B58;
    border-radius: 10px;
    padding: 9px 20px;
    font-size: 13px;
    font-weight: 600;
}
QPushButton#secondary:hover {
    background-color: #1E2D4A;
    border-color: #38BDF8;
    color: #FFFFFF;
}
QPushButton#secondary:pressed {
    background-color: #121A2D;
}

/* ── Action Buttons ──────────────────────────────────────────── */
QPushButton#action_sm {
    background-color: #172238;
    color: #E2E8F0;
    border: 1px solid #2A3B58;
    border-radius: 8px;
    padding: 6px 14px;
    font-size: 12px;
    font-weight: 600;
}
QPushButton#action_sm:hover {
    background-color: #1E2D4A;
    border-color: #38BDF8;
    color: #FFFFFF;
}

QPushButton#action_primary_sm {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #2563EB, stop:1 #3B82F6);
    color: #FFFFFF;
    border: 1px solid rgba(56, 189, 248, 0.3);
    border-radius: 8px;
    padding: 6px 14px;
    font-size: 12px;
    font-weight: 600;
}
QPushButton#action_primary_sm:hover {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #3B82F6, stop:1 #60A5FA);
}

QPushButton#action_danger_sm {
    background-color: rgba(239, 68, 68, 0.15);
    color: #EF4444;
    border: 1px solid rgba(239, 68, 68, 0.35);
    border-radius: 8px;
    padding: 6px 14px;
    font-size: 12px;
    font-weight: 600;
}
QPushButton#action_danger_sm:hover {
    background-color: rgba(239, 68, 68, 0.25);
    color: #FFFFFF;
}

/* ── Back Button ─────────────────────────────────────────────── */
QPushButton#back {
    background-color: #172238;
    color: #CBD5E1;
    border: 1px solid #2A3B58;
    border-radius: 8px;
    padding: 6px 12px;
    font-size: 13px;
    font-weight: 600;
}
QPushButton#back:hover {
    background-color: #1E2D4A;
    border-color: #38BDF8;
    color: #FFFFFF;
}

/* ── Progress Bar ────────────────────────────────────────────── */
QProgressBar {
    background-color: #0B101E;
    border-radius: 8px;
    height: 12px;
    border: 1px solid #1E2D4A;
    text-align: center;
    color: transparent;
}
QProgressBar::chunk {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #00E5FF, stop:0.5 #38BDF8, stop:1 #2563EB);
    border-radius: 7px;
}

/* ── Transfer Items ──────────────────────────────────────────── */
QFrame#transfer_item {
    background-color: #162035;
    border-radius: 12px;
    border: 1px solid #233352;
}
QFrame#transfer_item:hover {
    border-color: rgba(56, 189, 248, 0.3);
}

/* ── QR Display ──────────────────────────────────────────────── */
QLabel#qr_label {
    background-color: #FFFFFF;
    border-radius: 14px;
    padding: 12px;
}

/* ── Badges & Status Pills ───────────────────────────────────── */
QLabel#badge_green {
    background-color: rgba(16, 185, 129, 0.16);
    color: #10B981;
    font-size: 12px;
    font-weight: 600;
    border-radius: 6px;
    padding: 4px 10px;
    border: 1px solid rgba(16, 185, 129, 0.3);
}
QLabel#badge_blue {
    background-color: rgba(37, 99, 235, 0.16);
    color: #38BDF8;
    font-size: 12px;
    font-weight: 600;
    border-radius: 6px;
    padding: 4px 10px;
    border: 1px solid rgba(56, 189, 248, 0.3);
}
QLabel#badge_gray {
    background-color: rgba(148, 163, 184, 0.15);
    color: #94A3B8;
    font-size: 12px;
    font-weight: 600;
    border-radius: 6px;
    padding: 4px 10px;
}
QLabel#badge_orange {
    background-color: rgba(245, 158, 11, 0.16);
    color: #F59E0B;
    font-size: 12px;
    font-weight: 600;
    border-radius: 6px;
    padding: 4px 10px;
    border: 1px solid rgba(245, 158, 11, 0.3);
}
QLabel#badge_cyan {
    background-color: rgba(0, 229, 255, 0.15);
    color: #00E5FF;
    font-size: 12px;
    font-weight: 600;
    border-radius: 6px;
    padding: 4px 10px;
    border: 1px solid rgba(0, 229, 255, 0.35);
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
    background: #0B101E;
    width: 8px;
    border-radius: 4px;
}
QScrollBar::handle:vertical {
    background: #253552;
    border-radius: 4px;
    min-height: 20px;
}
QScrollBar::handle:vertical:hover {
    background: #38BDF8;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}

/* ── Input Fields ────────────────────────────────────────────── */
QLineEdit {
    background-color: #0B101E;
    color: #F8FAFC;
    border: 1px solid #1E2D4A;
    border-radius: 10px;
    padding: 8px 14px;
    font-size: 13px;
}
QLineEdit:focus {
    border: 1px solid #38BDF8;
}

/* ── Transport & Feature Pills ───────────────────────────────── */
QLabel#transport_pill {
    background-color: rgba(30, 45, 74, 0.6);
    color: #38BDF8;
    font-size: 11px;
    font-weight: 600;
    border-radius: 6px;
    padding: 3px 8px;
    border: 1px solid rgba(56, 189, 248, 0.25);
}

/* ── Sidebar Device Pill ─────────────────────────────────────── */
QFrame#sidebar_device_card {
    background-color: #121A2D;
    border: 1px solid #1E2D4A;
    border-radius: 12px;
    padding: 10px;
}

/* ── Device Icon Container ───────────────────────────────────── */
QFrame#device_icon_ring {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #1E2E4E, stop:1 #131C30);
    border: 1.5px solid rgba(56, 189, 248, 0.4);
    border-radius: 22px;
    min-width: 44px;
    max-width: 44px;
    min-height: 44px;
    max-height: 44px;
}
"""

