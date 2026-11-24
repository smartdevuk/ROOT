#!/usr/bin/python3
"""ROOT desktop entry point. Subsequent launches open another independent window."""
import json
import os
from pathlib import Path
import sys
import tempfile
import uuid
from PyQt5.QtCore import QFileSystemWatcher, QLockFile, Qt, QTimer
from PyQt5.QtGui import QColor, QIcon, QPalette
from PyQt5.QtWidgets import QApplication
from files import resolve
from service import Service
from theme import BG, LILAC, STYLES, icon

EXPLORER_STYLE = """
QTreeWidget#directoryTree {background:#131019;border:0;outline:0;}
QTreeWidget#directoryTree::item {height:26px;padding:1px 3px;border-radius:4px;}
QTreeWidget#directoryTree::item:selected {background:#3a2b4a;color:#ebdbff;}
QFrame#breadcrumbs {background:#100e17;border:1px solid #4a4058;border-radius:5px;}
QPushButton#crumb {background:transparent;color:#decff1;border:1px solid transparent;padding:3px 8px;}
QPushButton#crumb:hover,QPushButton#crumb:focus {background:#392a48;border:1px solid #72568b;}
QProgressBar#capacity {background:#352b40;border-radius:3px;}
QProgressBar#capacity::chunk {background:#92dcc4;border-radius:3px;}
QLineEdit#address {color:#e0d4eb;font-family:'DejaVu Sans Mono',monospace;font-size:12px;padding:7px 10px;}
QLineEdit#filter {padding:4px 6px;background:#17121f;}
QTableView::item {padding:0 6px;border:0;}
QTableView::item:selected {background:#443353;color:#fff;}
QTableView::item:hover:!selected {background:#231b2c;}
QHeaderView::section {padding:7px 8px;}
QTreeWidget {background:#15111d;alternate-background-color:#1e1825;border:1px solid #3a3047;}
QTreeWidget::item {padding:4px;}
QListWidget#hints {background:#211a2b;border:1px solid #6a527e;border-radius:5px;}
QListWidget#hints::item {height:28px;padding-left:8px;}
QListWidget::item {padding:4px;}
"""


def configure(app):
    app.setApplicationName("ROOT")
    app.setDesktopFileName("uk.systemadmin.RootExplorer")
    app.setOrganizationName("ROOT Explorer")
    app.setStyle("Fusion")
    pal = QPalette()
    for key, color in (
        (QPalette.Window, BG),
        (QPalette.WindowText, "#f5f1ff"),
        (QPalette.Base, "#14111b"),
        (QPalette.AlternateBase, "#191520"),
        (QPalette.Text, "#f5f1ff"),
        (QPalette.Button, "#241e2e"),
        (QPalette.ButtonText, "#f5f1ff"),
        (QPalette.Highlight, "#49355a"),
        (QPalette.HighlightedText, "#ffffff"),
        (QPalette.PlaceholderText, "#afa2bc"),
    ):
        pal.setColor(key, QColor(color))
    app.setPalette(pal)
    app.setStyleSheet(STYLES + EXPLORER_STYLE)
    appicon = Path(__file__).resolve().parent / "root.png"
    app.setWindowIcon(
        QIcon(str(appicon)) if appicon.exists() else icon("mark", LILAC, 64)
    )


class Instance:
    def __init__(self, svc, where):
        self.svc = svc
        runtime = Path(os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir())
        self.inbox = runtime / f"root-explorer-{os.getuid()}"
        self.inbox.mkdir(mode=0o700, exist_ok=True)
        private = self.inbox.lstat()
        if (
            self.inbox.is_symlink()
            or private.st_uid != os.getuid()
            or private.st_mode & 0o077
        ):
            raise RuntimeError("ROOT needs a private runtime directory.")
        self.lock = QLockFile(str(self.inbox / "instance.lock"))
        self.lock.setStaleLockTime(0)
        self.primary = self.lock.tryLock(0)
        if not self.primary:
            name = uuid.uuid4().hex
            temp = self.inbox / (name + ".tmp")
            temp.write_text(json.dumps(where.get_uri() if where else None))
            temp.rename(self.inbox / (name + ".open"))
            return
        self.watcher = QFileSystemWatcher([str(self.inbox)])
        self.watcher.directoryChanged.connect(self.accept)
        QTimer.singleShot(0, self.accept)

    def accept(self, *_):
        for path in self.inbox.glob("*.open"):
            try:
                uri = json.loads(path.read_text())
                win = self.svc.window(resolve(uri) if isinstance(uri, str) else None)
                win.raise_()
                win.activateWindow()
            except (OSError, ValueError, TypeError):
                pass
            finally:
                path.unlink(missing_ok=True)

    def close(self):
        self.lock.unlock()


def main():
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps)
    app = QApplication(sys.argv)
    configure(app)
    args = app.arguments()[1:]
    target = resolve(args[0], resolve(os.getcwd())) if args else None
    svc = Service()
    instance = Instance(svc, target)
    if not instance.primary:
        return 0
    svc.window(target)
    app.aboutToQuit.connect(instance.close)
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
