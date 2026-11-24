"""Native multi-window file browser for ChromeOS Linux."""

import os
from pathlib import Path

from gi.repository import Gio
from PyQt5.QtCore import (
    Qt,
    QEvent,
    QTimer,
    QProcess,
    QMimeData,
    QUrl,
    QFileSystemWatcher,
    QItemSelectionModel,
)
from PyQt5.QtGui import QDesktopServices, QKeySequence
from PyQt5.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QSplitter,
    QShortcut,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)
from files import (
    child,
    completion_parts,
    display,
    dns_hint,
    fmt,
    gio_error,
    leaf,
    listing,
    parent,
    places,
    resolve,
    numeric_host,
    server,
    share_root,
    translate_server,
)
import archives
from theme import button, frame, icon, label
from transfers import relocate
from views import (
    CopyHistory,
    FileTable,
    Properties,
    StorageMeter,
    TransferStrip,
    preview,
)
from navigation import Breadcrumbs, DirectoryTree

COPY_FORMAT = "x-special/gnome-copied-files"


def clipboard_files(mime):
    if not mime or mime.hasFormat(archives.MIME):
        return False, []
    if mime.hasFormat(COPY_FORMAT):
        lines = bytes(mime.data(COPY_FORMAT)).decode(errors="replace").splitlines()
        if lines and lines[0] in ("cut", "copy"):
            return lines[0] == "cut", lines[1:]
        return False, []
    move = bytes(mime.data("application/x-kde-cutselection")) == b"1"
    return move, [bytes(url.toEncoded()).decode() for url in mime.urls()]


class Window(QMainWindow):
    def __init__(self, service, start=None):
        super().__init__()
        self.svc = service
        self.closed = False
        self.current = Gio.File.new_for_path(str(Path.home()))
        self.backlist, self.history_pos = [], -1
        self.load_cancel = self.hint_cancel = None
        self.loading = False
        self.location = self.current
        self.attempt = None
        self.suggested_ip = None
        self.failed_network = None
        self.unavailable = False
        self.select_after_rename = None
        self.deferred_refresh = False
        self.suggest_serial = 0
        self.hint_cache = {}
        self.hint_chosen = False
        self.pop_allowed = False
        self.setWindowTitle("ROOT")
        self.setWindowIcon(QApplication.windowIcon())
        self.resize(1120, 710)
        self.setMinimumSize(710, 475)
        base = QWidget()
        base.setObjectName("page")
        self.setCentralWidget(base)
        outer = QHBoxLayout(base)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        rail = frame("rail")
        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)
        outer.addWidget(split)
        rail.setMinimumWidth(172)
        rail.setMaximumWidth(520)
        split.addWidget(rail)
        left = QVBoxLayout(rail)
        left.setContentsMargins(12, 20, 12, 14)
        left.setSpacing(7)
        brand = QHBoxLayout()
        brand.setContentsMargins(7, 0, 0, 0)
        mark = label()
        mark.setPixmap(icon("mark", size=31).pixmap(31, 31))
        brand.addWidget(mark)
        brand.addWidget(label("ROOT", "brand"))
        brand.addStretch()
        left.addLayout(brand)
        left.addSpacing(12)
        self.tree = DirectoryTree(self)
        left.addWidget(self.tree, 1)
        left.addWidget(
            button(
                "SSH sessions", lambda: self.launch_terminal(["sessions"]), "key", "nav"
            )
        )
        left.addWidget(button("Keyboard shortcuts", self.shortcuts_help, "key", "nav"))
        self.storage = StorageMeter()
        self.storage.update_space(self.current)
        left.addWidget(self.storage)
        page = QWidget()
        page.setObjectName("page")
        split.addWidget(page)
        split.setStretchFactor(1, 1)
        split.setSizes([240, 880])
        main = QVBoxLayout(page)
        main.setContentsMargins(22, 19, 22, 13)
        main.setSpacing(12)
        header = QHBoxLayout()
        self.title = label("Linux files", "h1")
        self.title.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        header.addWidget(self.title, 1)
        self.newwin = button(
            "New window", lambda: self.svc.window(self.location), "window"
        )
        self.newwin.setToolTip("Open this location in a new window (Ctrl+N)")
        header.addWidget(button("Terminal", self.open_terminal, "terminal"))
        root_terminal = button(
            "Root terminal", lambda: self.open_terminal(admin=True), "terminal"
        )
        root_terminal.setToolTip("Open an administrator shell using sudo")
        header.addWidget(root_terminal)
        header.addWidget(self.newwin)
        header.addWidget(button("New folder", self.new_folder, "plus", "primary"))
        main.addLayout(header)
        nav = QHBoxLayout()
        nav.setSpacing(5)

        def navbutton(name, glyph, fn):
            b = button("", fn, glyph)
            b.setToolTip(name)
            b.setAccessibleName(name)
            b.setFixedSize(34, 33)
            nav.addWidget(b)
            return b

        self.prev_btn = navbutton("Back (Alt+Left)", "back", lambda: self.travel(-1))
        self.next_btn = navbutton(
            "Forward (Alt+Right)", "right", lambda: self.travel(1)
        )
        self.up_btn = navbutton("Parent folder (Alt+Up)", "up", self.up)
        self.address = QLineEdit()
        self.address.setObjectName("address")
        self.address.setAccessibleName("Location")
        self.address.setPlaceholderText(r"Enter a path or \\server\share")
        self.address.installEventFilter(self)
        self.address.textEdited.connect(self.suggest)
        self.pathbar = QStackedWidget()
        self.pathbar.setFixedHeight(34)
        self.pathbar.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.crumbs = Breadcrumbs()
        self.crumbs.navigate.connect(self.go)
        self.crumbs.edit.connect(self.edit_address)
        self.pathbar.addWidget(self.crumbs)
        self.pathbar.addWidget(self.address)
        self.crumbs.set_location(self.current)
        nav.addWidget(self.pathbar, 1)
        navbutton("Refresh (Ctrl+R)", "refresh", self.refresh)
        main.addLayout(nav)
        panel = frame("pane")
        content = QVBoxLayout(panel)
        content.setContentsMargins(10, 9, 10, 7)
        content.setSpacing(7)
        tools = QHBoxLayout()
        self.file_count = label("", "micro")
        tools.addWidget(self.file_count, 1)
        self.filter = QLineEdit()
        self.filter.setObjectName("filter")
        self.filter.setMaximumWidth(232)
        self.filter.setPlaceholderText("Filter this folder…")
        self.filter.setAccessibleName("Filter this folder")
        self.filter.addAction(icon("search", size=16), QLineEdit.LeadingPosition)
        self.filter.textChanged.connect(self.filter_changed)
        tools.addWidget(self.filter)
        self.thumb_toggle = button("", self.toggle_thumbnails, "image")
        self.thumb_toggle.setCheckable(True)
        self.thumb_toggle.setAccessibleName("Image thumbnails")
        self.thumb_toggle.setToolTip("Show image thumbnails")
        tools.addWidget(self.thumb_toggle)
        self.more = button("", self.folder_menu, "more")
        self.more.setToolTip("Folder options")
        self.more.setAccessibleName("Folder options")
        tools.addWidget(self.more)
        content.addLayout(tools)
        self.table = FileTable()
        QApplication.clipboard().dataChanged.connect(self.sync_cut)
        self.sync_cut()
        self.apply_thumbnails()
        self.svc.changed.connect(self.apply_thumbnails)
        self.table.files.rename_requested.connect(self.rename_entry)
        self.table.itemDelegate().closeEditor.connect(self.editor_closed)
        self.table.proxy.hidden = self.svc.store.data["hidden"]
        self.table.doubleClicked.connect(
            lambda idx: self.open_row(self.table.entry(idx))
        )
        self.table.selectionModel().selectionChanged.connect(self.status)
        self.table.dropped.connect(self.drop)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.context)
        self.table.installEventFilter(self)
        content.addWidget(self.table, 1)
        self.empty = label("", "muted")
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setParent(self.table.viewport())
        self.empty.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.table.viewport().installEventFilter(self)
        main.addWidget(panel, 1)
        self.notice = label("", "warning")
        self.notice.setWordWrap(True)
        self.notice.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.notice.hide()
        warnings = QHBoxLayout()
        warnings.addWidget(self.notice, 1)
        self.fix_network = button("Connection settings…", self.network)
        self.fix_network.hide()
        warnings.addWidget(self.fix_network)
        main.addLayout(warnings)
        self.transfer = TransferStrip(self)
        main.addWidget(self.transfer)
        foot = QHBoxLayout()
        self.foot = label("", "micro")
        self.foot.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        foot.addWidget(self.foot, 1)
        main.addLayout(foot)
        self.hints = QListWidget(base)
        self.hints.setObjectName("hints")
        self.hints.setFocusPolicy(Qt.NoFocus)
        self.hints.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.hints.setMouseTracking(True)
        self.hints.itemClicked.connect(lambda it: self.accept_hint(it, True))
        self.hints.hide()
        self.hint_timer = QTimer(self)
        self.hint_timer.setSingleShot(True)
        self.hint_timer.setInterval(140)
        self.hint_timer.timeout.connect(self.lookup)
        self.watch = QFileSystemWatcher(self)
        self.watch.directoryChanged.connect(self.watch_changed)
        self.watch_timer = QTimer(self)
        self.watch_timer.setSingleShot(True)
        self.watch_timer.setInterval(280)
        self.watch_timer.timeout.connect(self.auto_refresh)
        self.svc.changed.connect(self.sidebar)
        self.svc.started.connect(self.transfer.render)
        self.svc.updated.connect(self.transfer.render)
        self.svc.settled.connect(self.auto_refresh)
        self.sidebar()
        self.bind()
        self.go(start or self.current)
        self.table.setFocus()

    def sidebar(self):
        self.tree.set_places(
            places(), self.svc.store.data["pins"], self.svc.store.data["recents"]
        )

    def logical(self, file):
        # The rows and file operations keep the endpoint actually opened by this window.
        # Only present the alias while it still maps to that same endpoint.
        if self.svc.endpoint(self.location).equal(self.current):
            return translate_server(file, server(self.current), server(self.location))
        return file

    def bind(self):
        self.keys = []

        def key(sequence, fn, target=None):
            s = QShortcut(QKeySequence(sequence), target or self)
            if target:
                s.setContext(
                    Qt.WidgetShortcut
                    if target is self.table
                    else Qt.WidgetWithChildrenShortcut
                )
            s.activated.connect(fn)
            self.keys.append(s)

        for seq, fn in {
            "Ctrl+N": lambda: self.svc.window(self.location),
            "Ctrl+L": self.edit_address,
            "Alt+D": self.edit_address,
            "Alt+Left": lambda: self.travel(-1),
            "Alt+Right": lambda: self.travel(1),
            "Alt+Up": self.up,
            "Ctrl+F": self.focus_filter,
            "Ctrl+Shift+N": self.new_folder,
            "Ctrl+.": self.toggle_hidden,
            "Ctrl+H": self.toggle_hidden,
            "F5": self.refresh,
            "Ctrl+R": self.refresh,
            "Ctrl+W": self.close,
        }.items():
            key(seq, fn)
        for seq, fn in {
            "Ctrl+Return": self.rename,
            "Ctrl+Enter": self.rename,
            "F2": self.rename,
            "Ctrl+Shift+Return": self.open_selected_window,
            "Ctrl+Shift+Enter": self.open_selected_window,
            "Alt+Return": self.properties,
            "Return": self.open_selected,
            "Enter": self.open_selected,
            "Ctrl+C": lambda: self.clip(False),
            "Ctrl+X": lambda: self.clip(True),
            "Ctrl+V": self.paste,
            "Delete": self.trash,
            "Alt+Backspace": self.trash,
            "Space": self.preview,
            "Backspace": self.up,
        }.items():
            key(seq, fn, self.table)
        key("Escape", self.clear_filter, self.filter)

    def eventFilter(self, obj, event):
        address = getattr(self, "address", None)
        if obj is address and hasattr(self, "hints"):
            if event.type() == QEvent.FocusOut:
                QTimer.singleShot(0, self.blur_hints)
            if event.type() == QEvent.MouseButtonPress and not address.hasFocus():
                QTimer.singleShot(0, self.focus_suggestions)
            if event.type() == QEvent.KeyPress:
                k = event.key()
                if self.hints.isVisible() and k in (Qt.Key_Up, Qt.Key_Down):
                    self.hint_chosen = True
                    delta = -1 if k == Qt.Key_Up else 1
                    self.hints.setCurrentRow(
                        (self.hints.currentRow() + delta) % self.hints.count()
                    )
                    return True
                if k in (Qt.Key_Tab, Qt.Key_Return, Qt.Key_Enter):
                    item = self.hints.currentItem() if self.hints.isVisible() else None
                    if k == Qt.Key_Tab and self.hints.isVisible() and not item:
                        item = self.hints.item(0)
                    if item and (self.hint_chosen or k == Qt.Key_Tab):
                        self.accept_hint(item, k != Qt.Key_Tab)
                        return True
                    if k in (Qt.Key_Return, Qt.Key_Enter):
                        self.open_text(address.text())
                        return True
                if k == Qt.Key_Escape:
                    self.hide_hints()
                    self.address.setText(display(self.location))
                    self.pathbar.setCurrentWidget(self.crumbs)
                    self.table.setFocus()
                    return True
        table = getattr(self, "table", None)
        if (
            table
            and obj is table.viewport()
            and event.type() == QEvent.Resize
            and hasattr(self, "empty")
        ):
            self.empty.setGeometry(table.viewport().rect())
        return super().eventFilter(obj, event)

    def blur_hints(self):
        if (
            not self.closed
            and not self.address.hasFocus()
            and not self.hints.underMouse()
        ):
            self.hide_hints()
            if not self.loading and not self.failed_network:
                self.pathbar.setCurrentWidget(self.crumbs)

    def hide_hints(self):
        self.pop_allowed = False
        self.hint_timer.stop()
        if self.hint_cancel:
            self.hint_cancel.cancel()
        self.hints.hide()

    def focus_suggestions(self):
        if not self.closed and self.address.hasFocus():
            self.suggest("")

    def edit_address(self):
        self.pathbar.setCurrentWidget(self.address)
        self.address.setFocus()
        self.address.selectAll()
        self.suggest("")

    def suggest(self, text):
        self.pop_allowed = True
        self.suggest_serial += 1
        if self.hint_cancel:
            self.hint_cancel.cancel()
        self.hint_text = text
        self.hint_timer.stop()
        self.present_hints([])
        if text:
            self.hint_timer.start()

    def recent_matches(self, text):
        found = []
        for uri in self.svc.store.data["recents"] + self.svc.store.data["pins"]:
            f = resolve(uri)
            choices = [f]
            if not f.is_native():
                ancestor = parent(f)
                while ancestor:
                    choices.append(ancestor)
                    ancestor = parent(ancestor)
            for item in choices:
                path = display(item)
                match = path.casefold().startswith(text.casefold()) or (
                    not item.is_native()
                    and item.get_uri().casefold().startswith(text.casefold())
                )
                if match and path not in found:
                    found.append(path)
        return found

    def present_hints(self, values):
        if not self.pop_allowed or not self.address.hasFocus() or self.closed:
            return
        recent = self.recent_matches(self.hint_text)[:12]
        allpaths = list(dict.fromkeys(recent + values))[:24]
        self.hints.clear()
        for path in allpaths:
            it = QListWidgetItem(icon("clock" if path in recent else "folder"), path)
            it.setData(Qt.UserRole, path)
            it.setToolTip(path)
            self.hints.addItem(it)
        if not allpaths:
            self.hints.hide()
            return
        point = self.address.mapTo(
            self.centralWidget(), self.address.rect().bottomLeft()
        )
        self.hints.setGeometry(
            point.x(),
            point.y() + 3,
            self.address.width(),
            min(7, len(allpaths)) * 30 + 4,
        )
        self.hint_chosen = False
        self.hints.setCurrentRow(-1)
        self.hints.show()
        self.hints.raise_()

    def lookup(self):
        if not self.pop_allowed:
            return
        serial = self.suggest_serial
        try:
            base, part = completion_parts(self.hint_text, self.location)
        except ValueError:
            return
        if base is None:
            return

        actual = self.svc.endpoint(base)

        def matches(rows):
            return sorted(
                (
                    display(translate_server(r.target, server(actual), server(base)))
                    for r in rows
                    if r.directory
                    and (
                        self.table.proxy.hidden or not r.hidden or part.startswith(".")
                    )
                    and r.name.casefold().startswith(part.casefold())
                ),
                key=str.casefold,
            )

        if base.equal(self.location) and actual.equal(self.current):
            self.present_hints(matches(self.table.files.rows))
            return
        uri = base.get_uri()
        if uri in self.hint_cache:
            self.present_hints(matches(self.hint_cache[uri]))
            return
        cancel = self.hint_cancel = Gio.Cancellable()

        def done(rows, err):
            if self.closed or cancel.is_cancelled() or serial != self.suggest_serial:
                return
            if not err:
                if len(self.hint_cache) > 24:
                    self.hint_cache.clear()
                self.hint_cache[uri] = rows
                self.present_hints(matches(rows))

        self.svc.workers.start(lambda: listing(actual, cancel), done, cancel)

    def accept_hint(self, item, open_now):
        path = item.data(Qt.UserRole)
        if open_now:
            self.open_text(path)
        else:
            path = path.rstrip("\\/") + ("\\" if path.startswith("\\\\") else "/")
            self.address.setText(path)
            self.suggest(path)

    def open_text(self, text):
        self.hide_hints()
        try:
            file = resolve(text, self.location)
        except ValueError as exc:
            return self.message(str(exc))
        self.go(file)

    def go(self, location, pos=None, refresh=False, override=None):
        if self.closed:
            return
        file = self.current if refresh else self.svc.endpoint(location, override)
        self.hide_hints()
        if self.load_cancel:
            self.load_cancel.cancel()
        cancel = self.load_cancel = Gio.Cancellable()
        self.loading = True
        self.attempt = location
        if not refresh:
            self.failed_network = None
            self.suggested_ip = None
            self.fix_network.hide()
            self.address.setText(display(location))
            self.notice.hide()
            self.file_count.setText("OPENING…")

        def fetch(mounted=False):
            def done(rows, err):
                if self.closed or cancel.is_cancelled():
                    return
                if (
                    err
                    and not mounted
                    and not file.is_native()
                    and gio_error(err, Gio.IOErrorEnum.NOT_MOUNTED)
                ):
                    self.svc.mount(
                        file, self, lambda e: failed(e) if e else fetch(True), cancel
                    )
                    return
                if err:
                    return failed(err)
                if refresh and self.table.state() == FileTable.EditingState:
                    self.loading = False
                    self.deferred_refresh = True
                    return
                previous = self.location
                selected = (
                    {r.file.get_uri() for r in self.table.selected()}
                    if refresh
                    else set()
                )
                if self.select_after_rename is not None:
                    folder, target = self.select_after_rename
                    if refresh and folder.equal(file):
                        selected = {target.get_uri()}
                    self.select_after_rename = None
                self.loading = False
                self.unavailable = False
                self.current = file
                self.location = location
                self.attempt = None
                if override is not None:
                    self.svc.remember_address(location, override)
                if not refresh:
                    self.storage.update_space(location, pending=True)

                def show_space(info, error):
                    if not self.closed and not cancel.is_cancelled():
                        self.storage.update_space(location, info)

                self.svc.workers.start(
                    lambda: file.query_filesystem_info(
                        "filesystem::free,filesystem::size,filesystem::used", cancel
                    ),
                    show_space,
                    cancel,
                )
                if not refresh:
                    if pos is not None:
                        self.history_pos = pos
                    elif self.history_pos < 0 or not previous.equal(location):
                        self.backlist[self.history_pos + 1 :] = [location]
                        self.history_pos = len(self.backlist) - 1
                    self.svc.recent(location)
                    self.filter.clear()
                    self.address.setText(display(location))
                    self.crumbs.set_location(location)
                    self.pathbar.setCurrentWidget(self.crumbs)
                self.hint_cache.clear()
                self.table.files.replace(rows)
                if refresh and selected:
                    for i, row in enumerate(rows):
                        if row.file.get_uri() in selected:
                            index = self.table.proxy.mapFromSource(
                                self.table.files.index(i, 0)
                            )
                            if index.isValid():
                                self.table.selectionModel().select(
                                    index,
                                    QItemSelectionModel.Select
                                    | QItemSelectionModel.Rows,
                                )
                self.title.setText(self.display_title(location))
                self.title.setToolTip(display(location))
                self.setWindowTitle(self.title.text() + " · ROOT")
                self.prev_btn.setEnabled(self.history_pos > 0)
                self.next_btn.setEnabled(self.history_pos < len(self.backlist) - 1)
                self.up_btn.setEnabled(parent(file) is not None)
                paths = self.watch.directories()
                local = file.get_path() if file.is_native() else None
                if paths and local not in paths:
                    self.watch.removePaths(paths)
                if local and local not in paths:
                    self.watch.addPath(local)
                self.sidebar()
                self.tree.sync(location, file, rows)
                self.status()
                if not refresh:
                    self.table.setFocus()

            self.svc.workers.start(lambda: listing(file, cancel), done, cancel)

        def failed(err):
            if self.closed or cancel.is_cancelled():
                return
            self.loading = False
            self.status()
            self.attempt = None
            detail = getattr(err, "message", str(err)).rstrip(".")
            self.unavailable = True
            self.failed_network = location if not location.is_native() else None
            self.fix_network.setVisible(self.failed_network is not None)
            self.address.setText(display(location))
            if self.failed_network:
                self.pathbar.setCurrentWidget(self.address)
                if gio_error(err, Gio.IOErrorEnum.PERMISSION_DENIED):
                    tip = " Check your NAS username and access to this share."
                elif (
                    gio_error(err, Gio.IOErrorEnum.NOT_SUPPORTED)
                    and "smb" not in Gio.Vfs.get_default().get_supported_uri_schemes()
                ):
                    tip = " The Linux SMB backend is unavailable. Check that gvfs-backends is installed."
                elif gio_error(err, Gio.IOErrorEnum.NOT_FOUND):
                    tip = " Check the share name and folder path."
                elif gio_error(err, Gio.IOErrorEnum.FAILED_HANDLED) or gio_error(
                    err, Gio.IOErrorEnum.CANCELLED
                ):
                    tip = ""
                else:
                    tip = " Use Connection settings to try the NAS IP address."
            else:
                tip = ""
            reason = f"Could not open {display(location)}. {detail}."
            self.message(reason + tip)
            if self.failed_network and not any(
                gio_error(err, code)
                for code in (
                    Gio.IOErrorEnum.PERMISSION_DENIED,
                    Gio.IOErrorEnum.CANCELLED,
                    Gio.IOErrorEnum.FAILED_HANDLED,
                    Gio.IOErrorEnum.NOT_SUPPORTED,
                )
            ):
                host = server(file)
                try:
                    numeric_host(host)
                except ValueError:

                    def diagnosed(result, error):
                        if (
                            self.closed
                            or cancel.is_cancelled()
                            or not result
                            or not result[0]
                            or self.failed_network is not location
                        ):
                            return
                        explanation, ip = result
                        self.suggested_ip = (server(location), ip)
                        self.message(
                            reason
                            + " "
                            + explanation
                            + (
                                " Check the share name, SMB service and your NAS login."
                                if not ip and explanation.startswith("Linux resolves ")
                                else ""
                            )
                        )

                    self.svc.workers.start(
                        lambda: dns_hint(host, cancel), diagnosed, cancel
                    )

        fetch()

    def display_title(self, file):
        for name, path, _ in places():
            if file.is_native() and str(path) == file.get_path():
                return name
        return leaf(file)

    def watch_changed(self, _):
        if not self.closed:
            self.watch_timer.start()

    def editor_closed(self, *_):
        if self.deferred_refresh:
            self.deferred_refresh = False
            self.watch_timer.start()

    def auto_refresh(self):
        if self.table.state() == FileTable.EditingState:
            self.deferred_refresh = True
            return
        if not self.closed and not self.loading and not self.address.hasFocus():
            self.go(self.location, refresh=True)

    def refresh(self):
        if self.failed_network:
            self.go(self.failed_network)
            return
        self.sidebar()
        self.go(self.location, refresh=True)

    def travel(self, delta):
        pos = self.history_pos + delta
        if 0 <= pos < len(self.backlist):
            self.go(self.backlist[pos], pos)

    def up(self):
        dest = parent(self.location)
        if dest:
            self.go(dest)

    def filter_changed(self, text):
        if not hasattr(self, "table"):
            return
        self.table.proxy.needle = text.casefold()
        self.table.proxy.invalidateFilter()
        self.status()

    def focus_filter(self):
        self.filter.setFocus()
        self.filter.selectAll()

    def clear_filter(self):
        self.filter.clear()
        self.table.setFocus()

    def apply_thumbnails(self):
        enabled = self.svc.store.data["thumbnails"]
        self.thumb_toggle.setChecked(enabled)
        self.table.set_thumbnails(enabled, self.svc.workers)

    def toggle_thumbnails(self):
        store = self.svc.store
        store.data["thumbnails"] = not store.data["thumbnails"]
        store.save()
        self.svc.changed.emit()

    def toggle_hidden(self):
        data = self.svc.store.data
        data["hidden"] = not data["hidden"]
        self.svc.store.save()
        for w in self.svc.windows:
            w.table.proxy.hidden = data["hidden"]
            w.table.proxy.invalidateFilter()
            w.status()

    def status(self, *_):
        if not hasattr(self, "foot"):
            return
        count = self.table.proxy.rowCount()
        self.file_count.setText(f"{count:,} " + ("ITEM" if count == 1 else "ITEMS"))
        self.empty.setText(
            "No matching items" if self.filter.text() else "This folder is empty"
        )
        self.empty.setVisible(count == 0 and not self.loading)
        self.empty.setGeometry(self.table.viewport().rect())
        selected = self.table.selected()
        if selected:
            amount = sum(r.size for r in selected if not r.directory)
            self.foot.setText(
                f"{len(selected):,} selected"
                + (f"   ·   {fmt(amount)}" if amount else "")
            )
        else:
            self.foot.setText(display(self.location))
        self.foot.setToolTip(display(self.location))

    def message(self, text):
        self.notice.setText(text)
        self.notice.show()

    def network(self):
        initial = self.failed_network or self.attempt or self.location
        dlg = QDialog(self)
        dlg.setWindowTitle("Connection settings")
        lay = QFormLayout(dlg)
        path = QLineEdit(display(initial) if not initial.is_native() else "\\\\")
        path.setPlaceholderText(r"\\fileserver\Shared")
        lay.addRow("SMB path", path)
        address = QLineEdit()
        address.setPlaceholderText("Optional")
        lay.addRow("NAS IP address", address)
        hint = label(
            "Use an IP address if Linux cannot find the server by name. ROOT remembers it after the share opens.",
            "muted",
        )
        hint.setWordWrap(True)
        lay.addRow(hint)
        error = label("", "warning")
        error.hide()
        lay.addRow(error)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Connect")
        lay.addRow(buttons)

        def parse():
            raw = path.text().strip()
            if not raw.startswith(("\\\\", "//")) and not raw.lower().startswith(
                "smb://"
            ):
                raw = "\\\\" + raw
            return resolve(raw)

        reset = buttons.addButton(
            "Forget login and reconnect…", QDialogButtonBox.ActionRole
        )
        last_host = [None]

        def populate(*_):
            try:
                host = server(parse())
            except ValueError:
                host = ""
            reset.setEnabled(bool(host))
            if last_host[0] != host:
                guess = (
                    self.suggested_ip[1]
                    if self.suggested_ip and self.suggested_ip[0] == host
                    else ""
                )
                address.setText(
                    self.svc.store.data["nas_addresses"].get(host, guess).strip("[]")
                )
                last_host[0] = host

        def accept(forget=False):
            try:
                target = parse()
                override = numeric_host(address.text().strip())
                if not server(target):
                    raise ValueError("Enter a server or share path.")
            except ValueError:
                error.setText(
                    "Enter an SMB path and a valid IPv4 or IPv6 address, or leave the IP field blank."
                )
                error.show()
                return
            dlg.accept()
            if forget:
                self.change_login(target, override)
            else:
                self.go(target, override=override)

        reset.clicked.connect(lambda: accept(True))
        path.textChanged.connect(populate)
        populate()
        buttons.accepted.connect(accept)
        buttons.rejected.connect(dlg.reject)
        dlg.exec_()
        dlg.deleteLater()

    def change_login(self, target, override=None):
        endpoint = self.svc.endpoint(target, override)
        if not server(endpoint):
            return
        jobs = list(self.svc.pending)
        if self.svc.timer.isActive() and self.svc.job:
            jobs.append(self.svc.job)
        share = share_root(endpoint)
        if any(
            share_root(f).equal(share)
            for job in jobs
            for f in [job.dest, *job.sources]
            if server(f)
        ):
            self.message(
                "Let transfers using this share finish before disconnecting it."
            )
            return
        if (
            QMessageBox.question(
                self,
                "Forget network login",
                f"Disconnect this share and forget saved SMB logins for {server(endpoint)} "
                "in the Linux keyring? Other shares and Linux apps using this server may ask "
                "for a password next time.",
                QMessageBox.Yes | QMessageBox.Cancel,
                QMessageBox.Cancel,
            )
            != QMessageBox.Yes
        ):
            return
        if self.load_cancel:
            self.load_cancel.cancel()
        cancel = self.load_cancel = Gio.Cancellable()
        self.loading = False
        self.message("Disconnecting the share and forgetting its saved login…")

        def finished(error):
            if self.closed or cancel.is_cancelled():
                return
            if error:
                self.message(f"Could not reset the network login. {error}")
            else:
                self.go(target, override=override)

        self.svc.reset_login(endpoint, cancel, finished)

    def launch_terminal(self, args):
        if self.closed:
            return
        executable = str(Path(__file__).with_name("terminal_app.py"))
        ok = QProcess.startDetached("/usr/bin/python3", [executable, *args])
        if not ok:
            self.message("Could not start ROOT Terminal.")

    def open_terminal(self, admin=False):
        folder = self.failed_network or self.attempt or self.current
        home = str(Path.home())
        unavailable = self.unavailable and self.attempt is None

        def working_directory():
            path = folder.get_path() if folder.is_native() and not unavailable else None
            if not path or not os.path.isdir(path) or not os.access(path, os.X_OK):
                return home
            info = folder.query_filesystem_info("filesystem::type", None)
            if info.get_attribute_string("filesystem::type") in {
                "cifs",
                "smb3",
                "smbfs",
                "nfs",
                "nfs4",
                "fuse.sshfs",
                "fuse.gvfsd-fuse",
            }:
                return home
            return path

        self.svc.workers.start(
            working_directory,
            lambda path, error: self.launch_terminal(
                ["admin" if admin else "folder", path or home]
            ),
        )

    def open_selected_window(self):
        rows = self.table.selected()
        if len(rows) == 1 and rows[0].directory:
            self.svc.window(self.logical(rows[0].target))

    def open_selected(self):
        rows = self.table.selected()
        if len(rows) == 1:
            self.open_row(rows[0])

    def open_row(self, row):
        if not row:
            return
        if row.directory:
            self.go(self.logical(row.target))
        elif archives.extension(leaf(row.file)):
            archives.ArchiveBrowser(self, row.file).show()
        else:
            local = row.file.get_path()
            url = (
                QUrl.fromLocalFile(local)
                if local
                else QUrl.fromEncoded(row.file.get_uri().encode())
            )
            if not QDesktopServices.openUrl(url):
                self.message("No application could open " + row.name + ".")

    def properties(self, file=None):
        if file is None:
            rows = self.table.selected()
            file = rows[0].file if rows else self.current
        dialog = Properties(self, file)
        dialog.show()
        return dialog

    def preview(self):
        rows = self.table.selected()
        if len(rows) == 1:
            preview(self, rows[0])

    def modify(self, fn, select=None):
        dest = self.current

        def done(_, err):
            if self.closed:
                return
            if err:
                self.message(str(err))
            else:
                self.notice.hide()
            if self.current.equal(dest):
                if select is not None and not err:
                    self.select_after_rename = dest, select
                self.refresh()

        self.svc.workers.start(fn, done)

    def rename(self):
        self.table.rename_selected()

    def rename_entry(self, row, name):
        import errno

        try:
            target = child(row.file.get_parent(), name)
        except ValueError as exc:
            self.message(str(exc))
            return

        def apply():
            try:
                relocate(row.file, target)
            except OSError as exc:
                if exc.errno not in (errno.ENOSYS, errno.ENOTSUP, errno.EINVAL):
                    raise
                row.file.set_display_name(name, None)

        self.modify(apply, select=target)

    def new_folder(self):
        name, ok = QInputDialog.getText(self, "New folder", "Folder name")
        if not ok:
            return
        base = self.current
        self.modify(lambda: child(base, name).make_directory(None))

    def trash(self):
        rows = self.table.selected()
        if not rows:
            return
        noun = rows[0].name if len(rows) == 1 else f"{len(rows)} items"
        if (
            QMessageBox.question(
                self,
                "Move to Trash",
                f"Move {noun} to Trash?",
                QMessageBox.Yes | QMessageBox.Cancel,
                QMessageBox.Cancel,
            )
            != QMessageBox.Yes
        ):
            return

        def work():
            errors = []
            for row in rows:
                try:
                    row.file.trash(None)
                except Exception as exc:
                    errors.append(f"{row.name} — {exc}")
            if errors:
                raise OSError("\n".join(errors[:8]))

        self.modify(work)

    def clip(self, move):
        rows = self.table.selected()
        if not rows:
            return
        uris = [r.file.get_uri() for r in rows]
        mime = QMimeData()
        mime.setUrls([QUrl.fromEncoded(u.encode()) for u in uris])
        mime.setData(
            COPY_FORMAT, (("cut" if move else "copy") + "\n" + "\n".join(uris)).encode()
        )
        mime.setData("application/x-kde-cutselection", b"1" if move else b"0")
        QApplication.clipboard().setMimeData(mime)
        self.foot.setText(
            f"{len(rows)} selected for " + ("moving" if move else "copying")
        )

    def sync_cut(self):
        move, uris = clipboard_files(QApplication.clipboard().mimeData())
        cut = set(uris) if move else set()
        if cut != self.table.cut_uris:
            self.table.cut_uris = cut
            self.table.viewport().update()

    def paste(self):
        mime = QApplication.clipboard().mimeData()
        if not mime:
            return
        if mime.hasFormat(archives.MIME):
            try:
                self.svc.enqueue_job(
                    archives.from_clipboard(mime.data(archives.MIME), self.current)
                )
            except (ValueError, TypeError, KeyError) as exc:
                self.message(str(exc))
            return
        move, uris = clipboard_files(mime)
        if self.drop(uris, self.current, move) and move:
            QApplication.clipboard().clear()

    def drop(self, uris, dest, move):
        try:
            files = [
                self.svc.endpoint(resolve(u))
                for u in dict.fromkeys(uris)
                if u.startswith(("smb://", "file://"))
            ]
        except ValueError as exc:
            return self.message(str(exc))
        if not files:
            return self.message("The clipboard does not contain files or folders.")
        self.svc.enqueue(files, dest or self.current, move)
        return True

    def menu(self, row=None):
        m = QMenu(self)
        rows = self.table.selected() if row else []
        file = row.file if row else self.current
        if row and len(rows) == 1:
            m.addAction("Open", lambda: self.open_row(row))
            if row.directory:
                m.addAction(
                    "Open folder in new window",
                    lambda: self.svc.window(self.logical(row.target)),
                )
            elif archives.extension(leaf(row.file)):
                for named, title in (
                    (True, "Extract to " + archives.folder_name(file) + "/"),
                    (False, "Extract all here"),
                ):
                    m.addAction(
                        title.replace("&", "&&"),
                        lambda checked=False, wrap=named: self.svc.enqueue_job(
                            archives.ExtractJob(file, file.get_parent(), named=wrap)
                        ),
                    )
            m.addSeparator()
        if rows:
            m.addAction("Cut", lambda: self.clip(True))
            m.addAction("Copy", lambda: self.clip(False))
            if len(rows) == 1:
                m.addAction("Rename…", self.rename)
                m.addAction(
                    "Copy path",
                    lambda: QApplication.clipboard().setText(
                        display(self.logical(file))
                    ),
                )
            m.addAction("Move to Trash…", self.trash)
        else:
            m.addAction("New window here", lambda: self.svc.window(self.location))
            m.addAction("New folder…", self.new_folder)
            m.addAction("Paste", self.paste)
            m.addAction("Refresh", self.refresh)
            m.addAction("Copy and move history…", lambda: CopyHistory(self).show())
            m.addAction("Connection settings…", self.network)
            target = self.failed_network or self.attempt or self.location
            if server(target):
                m.addAction(
                    "Forget network login and reconnect…",
                    lambda: self.change_login(target),
                )
            m.addAction("Access ChromeOS folders…", self.sharing_help)
            t = m.addAction("Show image thumbnails", self.toggle_thumbnails)
            t.setCheckable(True)
            t.setChecked(self.svc.store.data["thumbnails"])
            h = m.addAction("Show hidden files", self.toggle_hidden)
            h.setCheckable(True)
            h.setChecked(self.table.proxy.hidden)
        if not row or (len(rows) == 1 and row.directory):
            m.addSeparator()
            pinfile = self.logical(row.target) if row else self.location
            pinned = pinfile.get_uri() in self.svc.store.data["pins"]
            m.addAction(
                "Unpin folder" if pinned else "Pin folder to sidebar",
                lambda: self.svc.pin(pinfile),
            )
        if len(rows) <= 1:
            m.addAction("Properties…", lambda: self.properties(file))
        return m

    def context(self, point):
        idx = self.table.indexAt(point)
        if idx.isValid() and not self.table.selectionModel().isSelected(idx):
            self.table.selectRow(idx.row())
        self.menu(self.table.entry(idx)).exec_(self.table.viewport().mapToGlobal(point))

    def folder_menu(self):
        self.menu().exec_(self.more.mapToGlobal(self.more.rect().bottomLeft()))

    def sharing_help(self):
        QMessageBox.information(
            self,
            "Access ChromeOS folders",
            "In the ChromeOS Files app, right-click Downloads or another folder and choose "
            "Share with Linux. Then choose Refresh in ROOT. Shared locations appear in the sidebar.",
        )
        self.sidebar()

    def shortcuts_help(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("Keyboard shortcuts")
        lay = QFormLayout(dlg)
        for keys, action in (
            ("Ctrl+N", "New window at current folder"),
            ("Ctrl+Shift+Enter", "Open selected folder in new window"),
            ("Ctrl+L / Alt+D", "Type a path"),
            ("Tab / ↓ / Enter", "Complete / select / open a path"),
            ("Alt+← / Alt+→", "Back / forward"),
            ("Alt+↑", "Parent folder"),
            ("Ctrl+Enter / F2", "Rename selection"),
            ("Ctrl+X / C / V", "Cut / copy / paste"),
            ("Ctrl+Shift+N", "New folder"),
            ("Alt+Enter", "Properties"),
            ("Space", "Preview"),
            ("Ctrl+.", "Show hidden files"),
            ("Ctrl+F", "Filter current folder"),
            ("Delete / Alt+Backspace", "Move selection to Trash"),
            ("Ctrl+W", "Close window"),
        ):
            lay.addRow(label(keys, "mint"), label(action))
        box = QDialogButtonBox(QDialogButtonBox.Close)
        box.rejected.connect(dlg.reject)
        lay.addRow(box)
        dlg.exec_()

    def closeEvent(self, event):
        if len(self.svc.windows) == 1 and (
            self.svc.timer.isActive() or self.svc.pending
        ):
            QMessageBox.information(
                self,
                "Transfer in progress",
                "Keep one ROOT window open while transfers are running. You can cancel the active copy or move at the bottom of the file window.",
            )
            event.ignore()
            return
        self.closed = True
        if self.load_cancel:
            self.load_cancel.cancel()
        self.hide_hints()
        self.watch_timer.stop()
        for dialog in self.findChildren(QDialog):
            dialog.reject()
        if self in self.svc.windows:
            self.svc.windows.remove(self)
        super().closeEvent(event)
        self.deleteLater()
