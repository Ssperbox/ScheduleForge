"""Dark/light Qt stylesheets built from a small palette dict."""

from __future__ import annotations

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

PALETTES = {
    "dark": {
        "bg": "#101419",
        "panel": "#161c24",
        "panel2": "#1d2530",
        "border": "#2a3543",
        "text": "#e8eef5",
        "muted": "#93a3b4",
        "accent": "#8bd450",
        "accent_text": "#111a08",
        "danger": "#ff7a7a",
        "warn": "#f0b35b",
        "log": "#0b0f13",
        "selection": "#243b22",
    },
    "light": {
        "bg": "#eef1f5",
        "panel": "#ffffff",
        "panel2": "#f6f8fa",
        "border": "#d5dde6",
        "text": "#18212b",
        "muted": "#5a6a7a",
        "accent": "#3f7f22",
        "accent_text": "#ffffff",
        "danger": "#b42318",
        "warn": "#9a6700",
        "log": "#ffffff",
        "selection": "#dcebd2",
    },
    "midnight": {
        "bg": "#0b0d1a",
        "panel": "#131630",
        "panel2": "#1b2042",
        "border": "#2b3266",
        "text": "#e6e9ff",
        "muted": "#9aa2d4",
        "accent": "#7c9cff",
        "accent_text": "#0b0d1a",
        "danger": "#ff7a9a",
        "warn": "#f0c060",
        "log": "#070818",
        "selection": "#252c5e",
    },
    "amber": {
        "bg": "#1a1409",
        "panel": "#241c10",
        "panel2": "#2e2415",
        "border": "#4a3a20",
        "text": "#f7ecd9",
        "muted": "#b8a27e",
        "accent": "#f5a524",
        "accent_text": "#1a1409",
        "danger": "#ff7a6a",
        "warn": "#ffd166",
        "log": "#120e06",
        "selection": "#4a3614",
    },
    "rose": {
        "bg": "#1a0f14",
        "panel": "#241520",
        "panel2": "#2e1b29",
        "border": "#4d2f40",
        "text": "#f9e8ef",
        "muted": "#c09aab",
        "accent": "#f06292",
        "accent_text": "#1a0f14",
        "danger": "#ff8a80",
        "warn": "#ffcc80",
        "log": "#120a0e",
        "selection": "#4a2440",
    },
    "ocean": {
        "bg": "#0a1a1c",
        "panel": "#10262a",
        "panel2": "#163238",
        "border": "#24504f",
        "text": "#e0f5f3",
        "muted": "#8fb8b5",
        "accent": "#2dd4bf",
        "accent_text": "#0a1a1c",
        "danger": "#ff8a8a",
        "warn": "#fbbf24",
        "log": "#061213",
        "selection": "#1b4a4a",
    },
}


def build_stylesheet(theme: str) -> str:
    palette = PALETTES.get(theme, PALETTES["dark"])
    c = palette
    return f"""
    QWidget {{
        background-color: {c['bg']};
        color: {c['text']};
        font-family: "Segoe UI", "Noto Sans", Arial;
        font-size: 10pt;
    }}
    QMainWindow, QDialog {{ background-color: {c['bg']}; }}
    QFrame#Card {{
        background-color: {c['panel']};
        border: 1px solid {c['border']};
        border-radius: 10px;
    }}
    QLabel {{ background: transparent; }}
    QLabel#Title {{ font-size: 20pt; font-weight: 700; }}
    QLabel#Subtitle {{ color: {c['muted']}; font-size: 9.5pt; }}
    QLabel#Muted {{ color: {c['muted']}; font-size: 9pt; }}
    QLabel#SectionTitle {{ font-size: 11pt; font-weight: 600; }}
    QLabel#Warning {{ color: {c['warn']}; font-size: 9pt; }}
    QLabel#BadgeNeutral {{
        background-color: {c['panel2']}; color: {c['muted']};
        border: 1px solid {c['border']}; border-radius: 9px;
        padding: 3px 10px; font-size: 8.5pt; font-weight: 600;
    }}
    QLabel#BadgeOk {{
        background-color: rgba(139, 212, 80, 0.14); color: {c['accent']};
        border: 1px solid rgba(139, 212, 80, 0.35); border-radius: 9px;
        padding: 3px 10px; font-size: 8.5pt; font-weight: 600;
    }}
    QLabel#BadgeWarn {{
        background-color: rgba(240, 179, 91, 0.14); color: {c['warn']};
        border: 1px solid rgba(240, 179, 91, 0.35); border-radius: 9px;
        padding: 3px 10px; font-size: 8.5pt; font-weight: 600;
    }}
    QLabel#BadgeBad {{
        background-color: rgba(255, 122, 122, 0.14); color: {c['danger']};
        border: 1px solid rgba(255, 122, 122, 0.35); border-radius: 9px;
        padding: 3px 10px; font-size: 8.5pt; font-weight: 600;
    }}
    QPushButton, QToolButton {{
        background-color: {c['panel2']};
        border: 1px solid {c['border']};
        border-radius: 7px;
        padding: 6px 13px;
    }}
    QPushButton:hover, QToolButton:hover {{ border-color: {c['accent']}; }}
    QPushButton:pressed, QToolButton:pressed {{ background-color: {c['panel']}; }}
    QPushButton:disabled, QToolButton:disabled {{ color: {c['muted']}; background-color: {c['panel']}; }}
    QPushButton#Primary {{
        background-color: {c['accent']};
        color: {c['accent_text']};
        border: none;
        font-weight: 700;
        padding: 9px 26px;
        font-size: 11pt;
    }}
    QPushButton#Primary:hover {{ background-color: {c['accent']}; }}
    QPushButton#Danger {{ color: {c['danger']}; }}
    QPushButton#Flat {{ background: transparent; border: none; color: {c['muted']}; padding: 4px 8px; }}
    QPushButton#Flat:hover {{ color: {c['text']}; }}
    QTabWidget::pane {{ border: 1px solid {c['border']}; border-radius: 8px; top: -1px; }}
    QTabBar::tab {{
        background: transparent; color: {c['muted']};
        padding: 8px 16px; margin-right: 2px; border-bottom: 2px solid transparent;
    }}
    QTabBar::tab:selected {{ color: {c['text']}; border-bottom: 2px solid {c['accent']}; font-weight: 600; }}
    QTabBar::tab:hover {{ color: {c['text']}; }}
    QLineEdit, QComboBox, QSpinBox {{
        background-color: {c['panel2']};
        border: 1px solid {c['border']};
        border-radius: 6px;
        padding: 6px 8px;
        min-height: 18px;
        selection-background-color: {c['selection']};
        selection-color: {c['text']};
    }}
    QLineEdit:focus, QComboBox:focus {{ border-color: {c['accent']}; }}
    QComboBox::drop-down {{ border: none; width: 20px; }}
    QComboBox QAbstractItemView {{
        background-color: {c['panel2']}; border: 1px solid {c['border']};
        selection-background-color: {c['selection']};
    }}
    QTableWidget {{
        background-color: {c['panel']};
        alternate-background-color: {c['panel2']};
        border: 1px solid {c['border']};
        border-radius: 8px;
        gridline-color: {c['border']};
        selection-background-color: {c['selection']};
        selection-color: {c['text']};
    }}
    QTableWidget::item {{ padding: 5px; }}
    QHeaderView::section {{
        background-color: {c['panel2']};
        color: {c['muted']};
        border: none;
        border-bottom: 1px solid {c['border']};
        padding: 7px 6px;
        font-weight: 600;
    }}
    QListWidget {{
        background-color: {c['panel']}; border: 1px solid {c['border']};
        border-radius: 8px; padding: 4px;
    }}
    QPlainTextEdit#Log {{
        background-color: {c['log']};
        color: {c['text']};
        border: 1px solid {c['border']};
        border-radius: 8px;
        font-family: Consolas, "Cascadia Mono", monospace;
        font-size: 9pt;
        padding: 6px;
    }}
    QProgressBar {{
        background-color: {c['panel2']}; border: 1px solid {c['border']};
        border-radius: 5px; text-align: center; max-height: 10px;
    }}
    QProgressBar::chunk {{ background-color: {c['accent']}; border-radius: 5px; }}
    QStatusBar {{ color: {c['muted']}; }}
    QStatusBar QLabel {{ color: {c['muted']}; }}
    QCheckBox {{ spacing: 7px; }}
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
    QScrollBar::handle:vertical {{ background: {c['border']}; border-radius: 5px; min-height: 24px; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QScrollBar:horizontal {{ background: transparent; height: 10px; }}
    QScrollBar::handle:horizontal {{ background: {c['border']}; border-radius: 5px; min-width: 24px; }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
    QToolTip {{
        background-color: {c['panel2']}; color: {c['text']};
        border: 1px solid {c['border']}; padding: 5px;
    }}
    QMessageBox {{ background-color: {c['panel']}; }}
    QMenu {{ background-color: {c['panel2']}; border: 1px solid {c['border']}; padding: 4px; }}
    QMenu::item {{ padding: 6px 18px; border-radius: 4px; }}
    QMenu::item:selected {{ background-color: {c['selection']}; }}
    QGroupBox {{ border: 1px solid {c['border']}; border-radius: 8px; margin-top: 8px; padding-top: 10px; }}
    QScrollArea#SettingsScroll {{ background: transparent; border: none; }}
    QScrollArea#SettingsScroll > QWidget > QWidget {{ background: transparent; }}
    QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; color: {c['muted']}; }}
    """


def apply_theme(app: QApplication, theme: str) -> None:
    palette = PALETTES.get(theme, PALETTES["dark"])
    app.setStyle("Fusion")
    qt_palette = QPalette()
    qt_palette.setColor(QPalette.ColorRole.Window, QColor(palette["bg"]))
    qt_palette.setColor(QPalette.ColorRole.WindowText, QColor(palette["text"]))
    qt_palette.setColor(QPalette.ColorRole.Base, QColor(palette["panel"]))
    qt_palette.setColor(QPalette.ColorRole.AlternateBase, QColor(palette["panel2"]))
    qt_palette.setColor(QPalette.ColorRole.Text, QColor(palette["text"]))
    qt_palette.setColor(QPalette.ColorRole.Button, QColor(palette["panel2"]))
    qt_palette.setColor(QPalette.ColorRole.ButtonText, QColor(palette["text"]))
    qt_palette.setColor(QPalette.ColorRole.Highlight, QColor(palette["selection"]))
    qt_palette.setColor(QPalette.ColorRole.HighlightedText, QColor(palette["text"]))
    qt_palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(palette["panel2"]))
    qt_palette.setColor(QPalette.ColorRole.ToolTipText, QColor(palette["text"]))
    app.setPalette(qt_palette)
    app.setStyleSheet(build_stylesheet(theme))
