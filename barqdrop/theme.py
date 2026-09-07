"""Dark theme for the BarqDrop UI."""

BG = "#0E1116"
PANEL = "#161A22"
PANEL_ALT = "#1D222C"
BORDER = "#262C38"
TEXT = "#E7EAF0"
MUTED = "#8B94A7"
ACCENT = "#FFB020"
ACCENT_DARK = "#D98E12"
GOOD = "#39D98A"
BAD = "#FF6B6B"
INFO = "#5AA9FF"

QSS = """
* { font-family: 'Segoe UI Variable Text', 'Segoe UI', sans-serif; font-size: 13px; }

QMainWindow, QDialog { background: %(BG)s; }
QWidget { color: %(TEXT)s; }

QLabel#Title       { font-size: 21px; font-weight: 700; color: %(TEXT)s; }
QLabel#Subtitle    { color: %(MUTED)s; font-size: 12px; }
QLabel#SectionHead { color: %(MUTED)s; font-size: 11px; font-weight: 700;
                     letter-spacing: 1px; }
QLabel#Bolt        { font-size: 22px; }
QLabel#Muted       { color: %(MUTED)s; }
QLabel#Code        { font-size: 30px; font-weight: 700; color: %(ACCENT)s;
                     letter-spacing: 8px; }

QFrame#Panel {
    background: %(PANEL)s;
    border: 1px solid %(BORDER)s;
    border-radius: 12px;
}
QFrame#Header { background: %(PANEL)s; border-bottom: 1px solid %(BORDER)s; }
QFrame#Divider { background: %(BORDER)s; max-height: 1px; }

QPushButton {
    background: %(PANEL_ALT)s;
    border: 1px solid %(BORDER)s;
    border-radius: 8px;
    padding: 8px 16px;
    color: %(TEXT)s;
}
QPushButton:hover  { background: #252B37; border-color: #343D4C; }
QPushButton:pressed{ background: #2C3340; }
QPushButton:disabled { color: #555F72; background: #171B23; }

QPushButton#Primary {
    background: %(ACCENT)s; color: #201700; border: none; font-weight: 700;
}
QPushButton#Primary:hover   { background: #FFC24B; }
QPushButton#Primary:pressed { background: %(ACCENT_DARK)s; }
QPushButton#Primary:disabled{ background: #4A3F27; color: #8A7C5E; }

QPushButton#Danger { color: %(BAD)s; }
QPushButton#Ghost  { background: transparent; border: none; color: %(MUTED)s; }
QPushButton#Ghost:hover { color: %(TEXT)s; }

QScrollArea { background: transparent; border: none; }
QScrollArea > QWidget { background: transparent; }
QScrollArea > QWidget > QWidget { background: transparent; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: #2E3543; border-radius: 5px; min-height: 30px; }
QScrollBar::handle:vertical:hover { background: #3B4455; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; }
QScrollBar:horizontal { background: transparent; height: 10px; }
QScrollBar::handle:horizontal { background: #2E3543; border-radius: 5px; }

QProgressBar {
    background: #11151C; border: none; border-radius: 5px;
    height: 8px; text-align: center; color: transparent;
}
QProgressBar::chunk { background: %(ACCENT)s; border-radius: 5px; }

QLineEdit, QSpinBox, QComboBox {
    background: #11151C; border: 1px solid %(BORDER)s; border-radius: 8px;
    padding: 7px 10px; selection-background-color: %(ACCENT)s;
    selection-color: #201700;
}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus { border-color: %(ACCENT)s; }
QComboBox QAbstractItemView {
    background: %(PANEL_ALT)s; border: 1px solid %(BORDER)s;
    selection-background-color: %(ACCENT)s; selection-color: #201700;
}

QCheckBox::indicator {
    width: 17px; height: 17px; border-radius: 5px;
    border: 1px solid %(BORDER)s; background: #11151C;
}
QCheckBox::indicator:checked { background: %(ACCENT)s; border-color: %(ACCENT)s; }

QListWidget {
    background: transparent; border: none; outline: none;
}
QListWidget::item { border: none; margin: 0px; padding: 0px; }
QListWidget::item:selected { background: transparent; }

QToolTip {
    background: %(PANEL_ALT)s; color: %(TEXT)s;
    border: 1px solid %(BORDER)s; padding: 6px; border-radius: 6px;
}
""" % {
    "BG": BG, "PANEL": PANEL, "PANEL_ALT": PANEL_ALT, "BORDER": BORDER,
    "TEXT": TEXT, "MUTED": MUTED, "ACCENT": ACCENT, "ACCENT_DARK": ACCENT_DARK,
    "BAD": BAD,
}
