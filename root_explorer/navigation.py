"""Clickable path components and an asynchronously populated directory tree."""

from functools import lru_cache
from gi.repository import Gio
from PyQt5 import sip
from PyQt5.QtCore import QRect, Qt, pyqtSignal
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QMenu,
    QPushButton,
    QSizePolicy,
    QStyle,
    QStyleOptionButton,
    QTreeWidget,
    QTreeWidgetItem,
)
from files import (
    display,
    leaf,
    listing,
    parent,
    resolve,
    server,
    share_root,
    translate_server,
)
from theme import icon, label


tree_icon = lru_cache(maxsize=16)(icon)


def ancestors(file):
    result = []
    while file:
        result.append(file)
        file = parent(file)
    return list(reversed(result))


class Breadcrumbs(QFrame):
    navigate = pyqtSignal(object)
    edit = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setObjectName("breadcrumbs")
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.setFixedHeight(34)
        self.setAccessibleName("Location path")
        self.bar = QHBoxLayout(self)
        self.bar.setContentsMargins(5, 1, 3, 1)
        self.bar.setSpacing(1)
        self.items = []
        self.more = QPushButton("…")
        self.more.setObjectName("crumb")
        self.more.setToolTip("Earlier folders")
        self.more.setAccessibleName("Earlier folders")
        self.bar.addWidget(self.more)
        self.more.clicked.connect(self.overflow)
        self.bar.addStretch()
        self.edit_button = QPushButton()
        self.edit_button.setObjectName("crumb")
        self.edit_button.setIcon(icon("edit"))
        self.edit_button.setToolTip("Edit path (Ctrl+L)")
        self.edit_button.setAccessibleName("Edit path")
        self.edit_button.clicked.connect(self.edit)
        self.bar.addWidget(self.edit_button)
        self.more.hide()

    def set_location(self, location):
        for _, btn, sep in self.items:
            for widget in (btn, sep):
                self.bar.removeWidget(widget)
                widget.hide()
                widget.deleteLater()
        self.items = []
        for file in ancestors(location):
            name = leaf(file)
            if parent(file) is None:
                name = display(file)
            btn = QPushButton(name.replace("&", "&&"))
            btn.setObjectName("crumb")
            btn.setAccessibleName(name)
            btn.setToolTip(display(file))
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _, f=file: self.navigate.emit(f))
            sep = label("›", "micro")
            sep.setAttribute(Qt.WA_TransparentForMouseEvents)
            self.bar.insertWidget(self.bar.count() - 2, sep)
            self.bar.insertWidget(self.bar.count() - 2, btn)
            self.items.append((file, btn, sep))
        self.fit()

    def fit(self):
        available = self.width() - self.edit_button.sizeHint().width() - 16
        sizes = []
        widths = []
        for _, btn, sep in self.items:
            btn.ensurePolished()
            opt = QStyleOptionButton()
            btn.initStyleOption(opt)
            opt.rect = QRect(0, 0, 240, self.height())
            content = btn.style().subElementRect(QStyle.SE_PushButtonContents, opt, btn)
            inset = opt.rect.width() - content.width() + 8
            fm = btn.fontMetrics()
            name = btn.accessibleName()
            natural = (
                max(fm.horizontalAdvance(name), fm.boundingRect(name).width()) + inset
            )
            widths.append((natural, inset))
            sizes.append(min(206, natural) + sep.sizeHint().width() + 3)
        start = 0
        total = sum(sizes)
        if total > available:
            available -= self.more.sizeHint().width()
            while start < len(sizes) - 1 and total > available:
                total -= sizes[start]
                start += 1
        self.more.setVisible(start > 0)
        for i, (file, btn, sep) in enumerate(self.items):
            name = btn.accessibleName()
            natural, inset = widths[i]
            width = min(natural, 206, max(35, available))
            btn.setFixedWidth(width)
            if width < natural:
                name = btn.fontMetrics().elidedText(
                    name, Qt.ElideMiddle, max(1, width - inset)
                )
            btn.setText(name.replace("&", "&&"))
            btn.setVisible(i >= start)
            sep.setVisible(i >= start and i > 0)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.fit()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.edit.emit()
        else:
            super().mousePressEvent(event)

    def overflow(self):
        menu = QMenu(self)
        for file, btn, _ in self.items:
            if btn.isHidden():
                menu.addAction(
                    display(file).replace("&", "&&"),
                    lambda _, f=file: self.navigate.emit(f),
                )
        menu.exec_(self.more.mapToGlobal(self.more.rect().bottomLeft()))


class DirectoryTree(QTreeWidget):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self.setObjectName("directoryTree")
        self.setAccessibleName("Directory tree")
        self.setHeaderHidden(True)
        self.setIndentation(13)
        self.setUniformRowHeights(True)
        self.setTextElideMode(Qt.ElideMiddle)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setExpandsOnDoubleClick(False)
        self.itemExpanded.connect(self.expand_folder)
        self.itemCollapsed.connect(self.collapse_folder)
        self.itemClicked.connect(self.open_item)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self.menu)
        self.groups = {}
        self.roots = {}
        self.pending = {}
        self.location = None

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Enter, Qt.Key_Return) and self.currentItem():
            self.open_item(self.currentItem())
            return
        super().keyPressEvent(event)

    def open_item(self, item, *_):
        uri = item.data(0, Qt.UserRole)
        if uri:
            self.window.go(resolve(uri))

    def make_item(self, parent_item, title, file, glyph="folder"):
        item = QTreeWidgetItem(parent_item, [title])
        item.setData(0, Qt.UserRole, file.get_uri())
        item.setIcon(0, tree_icon(glyph))
        item.setToolTip(0, display(file))
        item.setChildIndicatorPolicy(QTreeWidgetItem.ShowIndicator)
        return item

    def set_places(self, places, pins, recents):
        shares = {}
        for uri in recents:
            if uri.startswith("smb://"):
                f = share_root(resolve(uri))
                shares.setdefault(f.get_uri(), (display(f), f, "server"))
        groups = {
            "LOCATIONS": [(n, Gio.File.new_for_path(str(p)), g) for n, p, g in places],
            "PINNED": [(leaf(resolve(uri)), resolve(uri), "pin") for uri in pins],
            "NETWORK": list(shares.values()),
        }
        for title, places_in_group in groups.items():
            if title not in self.groups:
                section = QTreeWidgetItem(self, [title])
                section.setFlags(Qt.ItemIsEnabled)
                section.setForeground(0, QColor("#bcabc9"))
                self.groups[title] = section
                section.setExpanded(True)
            section = self.groups[title]
            wanted = set()
            for name, file, glyph in places_in_group:
                key = title, file.get_uri()
                wanted.add(key)
                if key not in self.roots:
                    self.roots[key] = self.make_item(section, name, file, glyph)
            for key in list(self.roots):
                if key[0] == title and key not in wanted:
                    self.remove(self.roots.pop(key))
            section.setHidden(not places_in_group)

    def remove(self, item):
        self.cancel_branch(item)
        item.parent().removeChild(item)

    def cancel_branch(self, item):
        cancel = self.pending.pop(id(item), None)
        if cancel:
            cancel.cancel()
        for i in range(item.childCount()):
            self.cancel_branch(item.child(i))

    def collapse_folder(self, item):
        if item.data(0, Qt.UserRole):
            self.cancel_branch(item)

    def set_children(self, item, logical, actual, rows):
        self.pending.pop(id(item), None)
        hidden = self.window.table.proxy.hidden
        entries = sorted(
            (r for r in rows if r.directory and (hidden or not r.hidden)),
            key=lambda r: r.name.casefold(),
        )
        existing = {
            item.child(i).data(0, Qt.UserRole): item.child(i)
            for i in range(item.childCount())
        }
        for i, row in enumerate(entries):
            target = translate_server(row.target, server(actual), server(logical))
            uri = target.get_uri()
            node = existing.pop(uri, None)
            if node is None:
                node = self.make_item(item, row.name, target)
            if item.child(i) is not node:
                item.takeChild(item.indexOfChild(node))
                item.insertChild(i, node)
        for obsolete in existing.values():
            self.remove(obsolete)
        item.setChildIndicatorPolicy(
            QTreeWidgetItem.ShowIndicator
            if entries
            else QTreeWidgetItem.DontShowIndicatorWhenChildless
        )

    def expand_folder(self, item):
        uri = item.data(0, Qt.UserRole)
        if not uri or id(item) in self.pending:
            return
        logical = resolve(uri)
        if (
            self.window.location.equal(logical)
            and not self.window.loading
            and self.window.history_pos >= 0
        ):
            self.set_children(
                item, logical, self.window.current, self.window.table.files.rows
            )
            return
        actual = self.window.svc.endpoint(logical)
        cancel = Gio.Cancellable()
        self.pending[id(item)] = cancel
        if not item.childCount():
            placeholder = QTreeWidgetItem(item, ["Loading…"])
            placeholder.setFlags(Qt.NoItemFlags)

        def done(rows, err):
            if (
                self.window.closed
                or cancel.is_cancelled()
                or sip.isdeleted(item)
                or self.pending.get(id(item)) is not cancel
            ):
                return
            self.pending.pop(id(item))
            if err:
                for n in reversed(range(item.childCount())):
                    if item.child(n).data(0, Qt.UserRole) is None:
                        item.takeChild(n)
                if not item.childCount():
                    msg = QTreeWidgetItem(item, ["Open folder to retry"])
                    msg.setFlags(Qt.NoItemFlags)
                    msg.setToolTip(0, str(err))
                return
            self.set_children(item, logical, actual, rows)
            if self.location and any(
                f.equal(logical) for f in ancestors(self.location)
            ):
                self.reveal()

        self.window.svc.workers.start(lambda: listing(actual, cancel), done, cancel)

    def sync(self, logical, actual, rows):
        self.location = logical
        self.reveal(rows, actual)

    def reveal(self, rows=None, actual=None):
        if not self.location:
            return
        chain = ancestors(self.location)
        positions = {f.get_uri(): i for i, f in enumerate(chain)}
        candidates = [
            (positions[uri], item)
            for (_, uri), item in self.roots.items()
            if uri in positions
        ]
        if not candidates:
            return
        pos, node = max(candidates, key=lambda p: p[0])
        for file in chain[pos + 1 :]:
            node.setExpanded(True)
            if id(node) in self.pending:
                return
            uri = file.get_uri()
            match = next(
                (
                    node.child(i)
                    for i in range(node.childCount())
                    if node.child(i).data(0, Qt.UserRole) == uri
                ),
                None,
            )
            if match is None:
                return
            node = match
        self.setCurrentItem(node)
        self.scrollToItem(node, QAbstractItemView.EnsureVisible)
        if rows is not None and node.isExpanded():
            self.set_children(node, self.location, actual, rows)

    def menu(self, point):
        node = self.itemAt(point)
        uri = node.data(0, Qt.UserRole) if node else None
        if not uri:
            return
        file = resolve(uri)
        m = QMenu(self)
        m.addAction("Open folder in new window", lambda: self.window.svc.window(file))
        pinned = uri in self.window.svc.store.data["pins"]
        m.addAction(
            "Unpin folder" if pinned else "Pin folder to sidebar",
            lambda: self.window.svc.pin(file),
        )
        m.exec_(self.viewport().mapToGlobal(point))
