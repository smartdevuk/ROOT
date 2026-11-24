"""Shared PORT22 theme and ROOT's navigation icons."""

from PyQt5.QtCore import Qt, QPointF, QSize
from PyQt5.QtGui import (
    QColor,
    QIcon,
    QPainter,
    QPen,
    QPixmap,
    QPolygonF,
)
from PyQt5.QtWidgets import QLabel, QPushButton, QFrame

BG = "#0c0b13"
SURFACE = "#181422"
LINE = "#40364f"
TEXT = "#f5f1ff"
MUTED = "#c1baca"
LILAC = "#c2a6ff"
MINT = "#8ee0ca"

STYLES = """
* {color:#f5f1ff; font-family:'Inter','Open Sans','DejaVu Sans',sans-serif; font-size:13px;}
QMainWindow,QDialog,QWidget#page {background:#0c0b13;}
QToolTip {color:#f5f1ff; background:#30263e; border:1px solid #635477;}
QFrame#rail {background:#131019; border-right:1px solid #322a40;}
QFrame#card,QFrame#pane {background:#181422; border:1px solid #393044; border-radius:10px;}
QFrame#bar {background:#15111e; border-top:1px solid #322a40;}
QLabel {background:transparent;}
QLabel#brand {font-family:'DejaVu Sans Mono',monospace; font-size:22px; font-weight:700; letter-spacing:1px;}
QLabel#h1 {font-size:25px; font-weight:700;}
QLabel#h2 {font-size:17px; font-weight:600;}
QLabel#strong {font-size:14px; font-weight:600;}
QLabel#muted {color:#b4acbf;}
QLabel#micro {color:#b9a8cb; font-family:'DejaVu Sans Mono',monospace; font-size:11px;}
QLabel#mint {color:#8ee0ca; font-family:'DejaVu Sans Mono',monospace; font-size:11px;}
QLabel#warning {color:#ffb9bb;}
QPushButton {background:#241e2e; border:1px solid #493e57; border-radius:6px; padding:7px 10px; font-weight:500;}
QPushButton:hover {background:#332a40; border-color:#8d75a9;}
QPushButton:pressed {background:#43334f;}
QPushButton:disabled {color:#827b8b; background:#1c1822; border-color:#302935;}
QPushButton#primary {background:#c2a6ff; color:#211332; border:1px solid #d3bfff; font-weight:700;}
QPushButton#primary:hover {background:#d3bdff;}
QPushButton#primary:disabled {background:#403448; color:#aba0b8; border-color:#514657;}
QPushButton#nav {text-align:left; border:1px solid transparent; background:transparent; padding:10px 12px; color:#c1baca;}
QPushButton#nav:checked {background:#30243f; border:1px solid #57436f; color:#e0ccff;}
QPushButton#nav:hover {background:#251e30;}
QPushButton:focus,QToolButton:focus {border:1px solid #c2a6ff;}
QLineEdit,QSpinBox,QComboBox,QTextEdit,QPlainTextEdit {background:#100e17; color:#f5f1ff; border:1px solid #4a4058; border-radius:5px; padding:6px; selection-background-color:#644a86;}
QLineEdit:focus,QSpinBox:focus,QComboBox:focus {border-color:#c2a6ff;}
QTableView,QListWidget,QScrollArea {background:#14111b; border:0; alternate-background-color:#18141f; outline:0; selection-background-color:#463356; selection-color:#f5f1ff;}
QHeaderView::section {background:#201a29; color:#bcafc9; font-size:11px; font-weight:600; padding:5px; border:0; border-bottom:1px solid #372e42;}
QScrollBar:vertical {background:transparent; width:9px; margin:0;}
QScrollBar:horizontal {background:transparent; height:9px; margin:0;}
QScrollBar::handle {background:#51405f; border-radius:4px; min-height:18px; min-width:18px;}
QScrollBar::add-line,QScrollBar::sub-line {width:0; height:0;}
QMenu {background:#211a2a; border:1px solid #51445f;}
QMenu::item {padding:7px 18px;} QMenu::item:selected {background:#453455;}
QMessageBox {background:#191420;} QCheckBox {spacing:6px;}
QProgressBar {background:#32283e; border:0; border-radius:3px;} QProgressBar::chunk {background:#bd9df8; border-radius:3px;}
QSplitter::handle {background:transparent;}
QDialogButtonBox QPushButton {min-width:65px;}
"""

PATHS = {
    "terminal": [
        [(2, 4), (22, 4), (22, 20), (2, 20), (2, 4)],
        [(6, 8), (10, 12), (6, 16)],
        [(13, 16), (18, 16)],
    ],
    "edit": [
        [(4, 20), (8, 19), (20, 7), (17, 4), (5, 16), (4, 20)],
        [(14, 7), (17, 10)],
    ],
    "server": [
        [(3, 4), (21, 4), (21, 10), (3, 10), (3, 4)],
        [(3, 14), (21, 14), (21, 20), (3, 20), (3, 14)],
        [(6, 7), (7, 7)],
        [(6, 17), (7, 17)],
    ],
    "grid": [
        [(3, 7), (9, 7), (9, 3), (3, 3), (3, 7)],
        [(14, 7), (20, 7), (20, 3), (14, 3), (14, 7)],
        [(3, 13), (9, 13), (9, 20), (3, 20), (3, 13)],
        [(14, 13), (20, 13), (20, 20), (14, 20), (14, 13)],
    ],
    "workspace": [
        [(2, 4), (22, 4), (22, 20), (2, 20), (2, 4)],
        [(12, 4), (12, 20)],
        [(5, 8), (8, 8)],
        [(15, 8), (18, 8)],
    ],
    "queue": [
        [(3, 7), (18, 7)],
        [(14, 3), (18, 7), (14, 11)],
        [(21, 17), (6, 17)],
        [(10, 13), (6, 17), (10, 21)],
    ],
    "key": [
        [(9, 14), (4, 19), (4, 21), (7, 21), (8, 19), (10, 19), (12, 16)],
        [
            (9, 14),
            (8, 10),
            (10, 6),
            (14, 4),
            (18, 5),
            (20, 8),
            (20, 12),
            (17, 15),
            (13, 16),
            (9, 14),
        ],
        [(16, 8), (16, 9)],
    ],
    "folder": [[(2, 6), (9, 6), (11, 9), (21, 9), (21, 19), (2, 19), (2, 6)]],
    "file": [
        [(5, 3), (14, 3), (19, 8), (19, 21), (5, 21), (5, 3)],
        [(14, 3), (14, 8), (19, 8)],
    ],
    "link": [[(3, 18), (10, 11), (16, 11)], [(12, 7), (16, 11), (12, 15)]],
    "up": [[(12, 20), (12, 4)], [(5, 11), (12, 4), (19, 11)]],
    "down": [[(12, 4), (12, 20)], [(5, 13), (12, 20), (19, 13)]],
    "right": [[(3, 12), (20, 12)], [(14, 6), (20, 12), (14, 18)]],
    "plus": [[(12, 4), (12, 20)], [(4, 12), (20, 12)]],
    "settings": [
        [(3, 6), (20, 6)],
        [(3, 17), (20, 17)],
        [(8, 3), (8, 9)],
        [(16, 14), (16, 20)],
    ],
    "refresh": [
        [
            (20, 11),
            (18, 7),
            (14, 5),
            (10, 5),
            (6, 7),
            (4, 11),
            (4, 14),
            (6, 17),
            (10, 19),
            (14, 19),
            (17, 17),
        ],
        [(20, 5), (20, 11), (14, 11)],
    ],
    "home": [[(2, 11), (12, 3), (22, 11)], [(5, 10), (5, 21), (19, 21), (19, 10)]],
    "mark": [
        [(2, 6), (8, 6), (11, 9), (22, 9), (22, 20), (2, 20), (2, 6)],
        [(7, 15), (11, 15), (14, 12)],
        [(11, 15), (15, 17)],
    ],
    "back": [[(19, 12), (5, 12), (11, 6)], [(5, 12), (11, 18)]],
    "window": [
        [(2, 7), (16, 7), (16, 20), (2, 20), (2, 7)],
        [(7, 3), (21, 3), (21, 16)],
    ],
    "pin": [
        [
            (6, 4),
            (17, 4),
            (15, 7),
            (15, 11),
            (19, 15),
            (5, 15),
            (9, 11),
            (9, 7),
            (6, 4),
        ],
        [(12, 15), (12, 21)],
    ],
    "search": [
        [(15, 15), (21, 21)],
        [
            (9, 3),
            (13, 4),
            (16, 7),
            (16, 12),
            (13, 15),
            (8, 16),
            (4, 13),
            (3, 9),
            (4, 6),
            (9, 3),
        ],
    ],
    "disk": [
        [(5, 5), (19, 5), (22, 16), (22, 19), (2, 19), (2, 16), (5, 5)],
        [(3, 15), (21, 15)],
        [(17, 17), (19, 17)],
    ],
    "video": [
        [(3, 5), (21, 5), (21, 19), (3, 19), (3, 5)],
        [(10, 8), (15, 12), (10, 16), (10, 8)],
    ],
    "image": [
        [(3, 4), (21, 4), (21, 20), (3, 20), (3, 4)],
        [(4, 17), (9, 11), (13, 15), (16, 12), (20, 16)],
        [(15, 8), (16, 8)],
    ],
    "music": [
        [(8, 17), (8, 6), (19, 4), (19, 15)],
        [(8, 17), (5, 16), (3, 18), (5, 20), (8, 18)],
        [(19, 15), (16, 14), (14, 16), (16, 18), (19, 16)],
    ],
    "code": [
        [(8, 7), (3, 12), (8, 17)],
        [(16, 7), (21, 12), (16, 17)],
        [(14, 4), (10, 20)],
    ],
    "clock": [
        [
            (12, 2),
            (18, 4),
            (21, 9),
            (21, 15),
            (17, 20),
            (11, 22),
            (5, 19),
            (2, 13),
            (3, 7),
            (7, 3),
            (12, 2),
        ],
        [(12, 6), (12, 12), (16, 14)],
    ],
    "more": [[(4, 12), (5, 12)], [(11.5, 12), (12.5, 12)], [(19, 12), (20, 12)]],
}


def icon(name, color=LILAC, size=24):
    canvas = QPixmap(size * 2, size * 2)
    canvas.fill(Qt.transparent)
    canvas.setDevicePixelRatio(2)
    p = QPainter(canvas)
    p.setRenderHint(QPainter.Antialiasing)
    p.scale(size / 24, size / 24)
    p.setPen(QPen(QColor(color), 1.65, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    for path in PATHS.get(name, PATHS["file"]):
        p.drawPolyline(QPolygonF([QPointF(x, y) for x, y in path]))
    p.end()
    return QIcon(canvas)


def label(txt="", style=""):
    w = QLabel(txt)
    w.setTextFormat(Qt.PlainText)
    w.setObjectName(style)
    return w


def button(txt, action=None, symbol=None, style=""):
    w = QPushButton(txt)
    w.setObjectName(style)
    w.setCursor(Qt.PointingHandCursor)
    if symbol:
        w.setIcon(icon(symbol, "#342045" if style == "primary" else LILAC))
        w.setIconSize(QSize(16, 16))
    if action:
        w.clicked.connect(lambda _=False: action())
    return w


def frame(name="card"):
    w = QFrame()
    w.setObjectName(name)
    return w
