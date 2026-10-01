"""
PhotoBeam Windows UI — Design System Stylesheet
Faithful to Reference 2 (Polished Desktop Dashboard):
- Deep matte navy background (#0B0F19)
- Fixed left sidebar with refined active states and clean vertical rhythm
- Minimalist top bar with breadcrumbs and live device indicator
- Clear 2-column dashboard layout with consistent card heights, padding, and corner radii
- Subtle blue highlights without excessive or artificial neon glow
"""

STYLESHEET = """
/* ── Global Base ─────────────────────────────────────────────── */
QMainWindow, QDialog, QWidget {
    background-color: #0B0F19;
    color: #F8FAFC;
    font-family: "Segoe UI", "Inter", -apple-system, sans-serif;
    font-size: 13px;
}

QLabel {
    background-color: transparent;
}

QLabel#qr_label {
    background-color: #FFFFFF;
    border-radius: 14px;
    padding: 12px;
}


/* ── Fixed Left Sidebar ──────────────────────────────────────── */
QFrame#sidebar {
    background-color: #080C16;
    border-right: 1px solid #141C2E;
    min-width: 220px;
    max-width: 220px;
}

QLabel#sidebar_brand_title {
    font-size: 17px;
    font-weight: 700;
    color: #FFFFFF;
    letter-spacing: 0.5px;
}

QLabel#sidebar_brand_sub {
    font-size: 10px;
    font-weight: 600;
    color: #38BDF8;
    letter-spacing: 1.2px;
}

QPushButton#nav_btn {
    background-color: transparent;
    color: #94A3B8;
    border: none;
    border-radius: 11px;
    padding: 10px 14px;
    font-size: 13px;
    font-weight: 600;
    text-align: left;
}
QPushButton#nav_btn:hover {
    background-color: #121A2C;
    color: #F8FAFC;
}
QPushButton#nav_btn[active="true"], QPushButton#nav_btn:checked {
    background-color: #18233C;
    color: #38BDF8;
    font-weight: 700;
    border-left: 3px solid #38BDF8;
}

/* Sidebar local identity card */
QFrame#sidebar_identity_card {
    background-color: #0F1626;
    border: 1px solid #18233C;
    border-radius: 12px;
    padding: 10px;
}

/* ── Top Bar ─────────────────────────────────────────────────── */
QFrame#topbar {
    background-color: #0B0F19;
    border-bottom: 1px solid #141C2E;
    min-height: 60px;
    max-height: 60px;
}

QLabel#breadcrumb_root {
    font-size: 13px;
    color: #64748B;
    font-weight: 500;
}
QLabel#breadcrumb_active {
    font-size: 13px;
    color: #38BDF8;
    font-weight: 600;
}

QFrame#top_device_pill {
    background-color: #101726;
    border: 1px solid #1B263E;
    border-radius: 10px;
    padding: 0px;
}

/* ── Dashboard Content Header ────────────────────────────────── */
QLabel#dash_heading {
    font-size: 24px;
    font-weight: 800;
    color: #FFFFFF;
    letter-spacing: -0.3px;
}

QLabel#dash_sub {
    font-size: 13px;
    color: #94A3B8;
}

QPushButton#filter_pill {
    background-color: #111827;
    color: #94A3B8;
    border: 1px solid #1C263B;
    border-radius: 16px;
    padding: 5px 14px;
    font-size: 12px;
    font-weight: 600;
}
QPushButton#filter_pill:hover {
    background-color: #172134;
    color: #F8FAFC;
}
QPushButton#filter_pill[active="true"] {
    background-color: #2563EB;
    color: #FFFFFF;
    border: 1px solid #38BDF8;
}

/* ── Modular Cards ───────────────────────────────────────────── */
QFrame#card {
    background-color: #101625;
    border: 1px solid #1A2438;
    border-radius: 16px;
}

QFrame#active_device_card {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #141D30, stop:1 #0F1626);
    border: 1px solid #22304C;
    border-radius: 16px;
}
QFrame#active_device_card:hover {
    border-color: #2E4166;
}

QFrame#glow_card {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #16243F, stop:0.4 #11192B, stop:1 #0D1322);
    border: 1px solid #1F2D48;
    border-radius: 16px;
}

QFrame#dropzone_card {
    background-color: #0E1422;
    border: 1.5px dashed #202D46;
    border-radius: 16px;
}
QFrame#dropzone_card:hover {
    background-color: #121A2C;
    border-color: #38BDF8;
}

/* ── Typography & Section Headers ────────────────────────────── */
QLabel#card_header_title {
    font-size: 15px;
    font-weight: 700;
    color: #FFFFFF;
}

QLabel#section_heading {
    font-size: 16px;
    font-weight: 700;
    color: #FFFFFF;
}

QLabel#device_title {
    font-size: 15px;
    font-weight: 700;
    color: #FFFFFF;
}

QLabel#muted_text {
    font-size: 12px;
    color: #94A3B8;
}

/* ── Primary Action Button ───────────────────────────────────── */
QPushButton#primary {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #2563EB, stop:1 #3B82F6);
    color: #FFFFFF;
    border: 1px solid rgba(56, 189, 248, 0.25);
    border-radius: 10px;
    padding: 8px 18px;
    font-size: 13px;
    font-weight: 600;
}
QPushButton#primary:hover {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #3B82F6, stop:1 #60A5FA);
    border-color: #38BDF8;
}
QPushButton#primary:pressed {
    background: #1D4ED8;
}
QPushButton#primary:disabled {
    background-color: #172032;
    color: #64748B;
    border-color: #1F2B42;
}

/* ── Secondary / Subtle Button ───────────────────────────────── */
QPushButton#secondary {
    background-color: #151D2D;
    color: #E2E8F0;
    border: 1px solid #222F46;
    border-radius: 9px;
    padding: 7px 14px;
    font-size: 12px;
    font-weight: 600;
}
QPushButton#secondary:hover {
    background-color: #1B263B;
    border-color: #38BDF8;
    color: #FFFFFF;
}
QPushButton#secondary:pressed {
    background-color: #111724;
}

/* ── Small Action Buttons ────────────────────────────────────── */
QPushButton#action_sm {
    background-color: #151D2D;
    color: #E2E8F0;
    border: 1px solid #222F46;
    border-radius: 8px;
    padding: 6px 12px;
    font-size: 12px;
    font-weight: 600;
}
QPushButton#action_sm:hover {
    background-color: #1B263B;
    border-color: #38BDF8;
    color: #FFFFFF;
}

QPushButton#action_primary_sm {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #2563EB, stop:1 #3B82F6);
    color: #FFFFFF;
    border: 1px solid rgba(56, 189, 248, 0.25);
    border-radius: 8px;
    padding: 6px 14px;
    font-size: 12px;
    font-weight: 600;
}
QPushButton#action_primary_sm:hover {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #3B82F6, stop:1 #60A5FA);
}

QPushButton#action_icon_only {
    background-color: #151D2D;
    color: #94A3B8;
    border: 1px solid #222F46;
    border-radius: 8px;
    min-width: 32px;
    max-width: 32px;
    min-height: 32px;
    max-height: 32px;
}
QPushButton#action_icon_only:hover {
    background-color: #1B263B;
    border-color: #38BDF8;
    color: #FFFFFF;
}

/* ── Badges & Status Tags ────────────────────────────────────── */
QLabel#badge_green {
    background-color: rgba(16, 185, 129, 0.12);
    color: #10B981;
    font-size: 11px;
    font-weight: 600;
    border-radius: 6px;
    padding: 3px 8px;
    border: 1px solid rgba(16, 185, 129, 0.25);
}

QLabel#badge_blue {
    background-color: rgba(56, 189, 248, 0.12);
    color: #38BDF8;
    font-size: 11px;
    font-weight: 600;
    border-radius: 6px;
    padding: 3px 8px;
    border: 1px solid rgba(56, 189, 248, 0.25);
}

QLabel#badge_orange {
    background-color: rgba(245, 158, 11, 0.12);
    color: #F59E0B;
    font-size: 11px;
    font-weight: 600;
    border-radius: 6px;
    padding: 3px 8px;
    border: 1px solid rgba(245, 158, 11, 0.25);
}

QLabel#badge_gray {
    background-color: rgba(148, 163, 184, 0.1);
    color: #94A3B8;
    font-size: 11px;
    font-weight: 600;
    border-radius: 6px;
    padding: 3px 8px;
    border: 1px solid rgba(148, 163, 184, 0.2);
}

QLabel#transport_tag {
    background-color: #131B2B;
    color: #94A3B8;
    font-size: 11px;
    font-weight: 600;
    border-radius: 5px;
    padding: 3px 8px;
    border: 1px solid #1E2B42;
}

/* ── Progress Bar ────────────────────────────────────────────── */
QProgressBar {
    background-color: #0E1422;
    border-radius: 6px;
    height: 10px;
    border: 1px solid #1A2438;
    text-align: center;
    color: transparent;
}
QProgressBar::chunk {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #38BDF8, stop:1 #2563EB);
    border-radius: 5px;
}

/* ── Scroll Area & Scrollbars ────────────────────────────────── */
QScrollArea {
    background: transparent;
    border: none;
}
QScrollArea > QWidget > QWidget {
    background: transparent;
}
QScrollBar:vertical {
    background: #090D16;
    width: 6px;
    border-radius: 3px;
    margin: 0;
}
QScrollBar::handle:vertical {
    background: #1F2C46;
    border-radius: 3px;
    min-height: 24px;
}
QScrollBar::handle:vertical:hover {
    background: #38BDF8;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}

/* ── Inputs ──────────────────────────────────────────────────── */
QLineEdit {
    background-color: #0E1422;
    color: #F8FAFC;
    border: 1px solid #1C263B;
    border-radius: 9px;
    padding: 8px 12px;
    font-size: 13px;
}
QLineEdit:focus {
    border: 1px solid #38BDF8;
}
"""
