"""Read-only archive browsing and streamed extraction through the transfer queue."""

from collections import defaultdict
from contextlib import contextmanager
from pathlib import Path, PureWindowsPath
from tempfile import TemporaryDirectory
from time import monotonic
from uuid import uuid4
import json

from gi.repository import Gio
import libarchive
from PyQt5.QtCore import Qt, QMimeData
from PyQt5.QtGui import QKeySequence
from PyQt5.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QMenu,
    QShortcut,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)
from files import ATTR, NOFOLLOW, child, fmt, leaf, resolve
from theme import button, label
from transfers import Job, publish, version

MIME = "application/x-root-archive-selection"
BUNDLES = (
    ".tar.gz",
    ".tar.bz2",
    ".tar.xz",
    ".tar.zst",
    ".tgz",
    ".tbz2",
    ".tbz",
    ".txz",
    ".tzst",
    ".tar",
    ".zip",
    ".7z",
    ".rar",
)
STREAMS = (".gz", ".bz2", ".xz", ".zst")


def extension(name):
    return next(
        (suffix for suffix in BUNDLES + STREAMS if name.lower().endswith(suffix)), ""
    )


def folder_name(file):
    name = leaf(file)
    suffix = extension(name)
    stem = name[: -len(suffix)] if suffix else name
    return stem if stem not in ("", ".", "..") else "Extracted files"


def parts(name):
    if not isinstance(name, str) or "\x00" in name or PureWindowsPath(name).drive:
        raise ValueError("Unsupported archive path")
    name = name.replace("\\", "/")
    names = tuple(n for n in name.split("/") if n not in ("", "."))
    if name.startswith("/") or ".." in names or len(names) > 256:
        raise ValueError("Unsupported archive path")
    return names


@contextmanager
def reader(file, cancel):
    raw = extension(leaf(file)) in STREAMS
    with TemporaryDirectory(prefix="root-archive-") as temp:
        path = file.get_path()
        if not path:
            path = str(Path(temp) / "archive")
            file.copy(Gio.File.new_for_path(path), Gio.FileCopyFlags.NONE, cancel, None)
        cancel.set_error_if_cancelled()
        with libarchive.file_reader(
            path, format_name="raw" if raw else "all"
        ) as archive:
            if raw and not archive.filter_names:
                raise ValueError("The file is not a recognised compressed stream.")
            yield archive, raw


def member(entry, file, raw):
    name = parts(folder_name(file) if raw else entry.pathname)
    if not (entry.isdir or entry.isreg) or entry.islnk or entry.issym:
        raise ValueError("Links and special entries are omitted")
    return name


def identity(file, cancel):
    return list(version(file.query_info(ATTR, NOFOLLOW, cancel)))


def index_archive(file, cancel):
    stamp = identity(file, cancel)
    children = defaultdict(dict)
    skipped = 0
    with reader(file, cancel) as (archive, raw):
        for entry in archive:
            cancel.set_error_if_cancelled()
            try:
                names = member(entry, file, raw)
            except ValueError:
                skipped += 1
                continue
            for i, component in enumerate(names):
                directory = i + 1 < len(names) or entry.isdir
                children[names[:i]][component] = (
                    directory,
                    None if directory else entry.size,
                )
    if stamp != identity(file, cancel):
        raise ValueError("Archive changed while opening. Reopen it.")
    return children, skipped, stamp


class ExtractJob(Job):
    def __init__(self, file, dest, named=False, selection=None, stamp=None):
        super().__init__([file], dest)
        self.file = file
        self.named = named
        self.selection = None if selection is None else set(selection)
        self.stamp = stamp
        self.action = "Extract"

    def write_member(self, entry, target, names):
        key = "/".join(names)
        temp = child(target.get_parent(), ".root-" + uuid4().hex + ".part")
        writer = temp.create(Gio.FileCreateFlags.PRIVATE, self.cancel)
        size = entry.size or 0
        offset = 0
        try:
            try:
                for block in entry.get_blocks(256 * 1024):
                    self.cancel.set_error_if_cancelled()
                    ok, count = writer.write_all(block, self.cancel)
                    if not ok or count != len(block):
                        raise OSError("Incomplete archive write")
                    offset += count
                    with self.lock:
                        self.bytes += count
                    self.set_row(key, names[-1], offset, size, "working")
            finally:
                writer.close(None)
            if entry.size is not None and entry.size != offset:
                raise OSError("Incomplete archive entry")
            self.keep_both(target, False, lambda dest: publish(temp, dest, self.cancel))
        except Exception:
            self.set_row(key, names[-1], offset, size, "error")
            temp.delete(None)
            raise
        self.set_row(key, names[-1], offset, offset, "done")
        with self.lock:
            self.finished += 1

    def run(self):
        self.started = monotonic()
        self.status = "Running"
        skipped = 0
        matched = set()
        try:
            if self.stamp is not None and self.stamp != identity(
                self.file, self.cancel
            ):
                raise ValueError(
                    "Archive changed after copying. Reopen it and select the entries again."
                )
            with reader(self.file, self.cancel) as (archive, raw):
                base = self.dest

                def make_dir(parent, name):
                    return self.keep_both(
                        child(parent, name),
                        True,
                        lambda d: d.make_directory(self.cancel),
                    )

                if self.named:
                    base = make_dir(base, folder_name(self.file))
                dirs = {}
                for entry in archive:
                    self.cancel.set_error_if_cancelled()
                    try:
                        names = member(entry, self.file, raw)
                    except ValueError:
                        skipped += 1
                        continue
                    if not names:
                        continue
                    start = 0
                    if self.selection is not None:
                        root = next(
                            (
                                names[:i]
                                for i in range(1, len(names) + 1)
                                if names[:i] in self.selection
                            ),
                            None,
                        )
                        if root is None:
                            continue
                        matched.add(root)
                        start = len(root) - 1
                    parent = base
                    for i in range(
                        start, len(names) if entry.isdir else len(names) - 1
                    ):
                        prefix = names[: i + 1]
                        if prefix not in dirs:
                            dirs[prefix] = make_dir(parent, names[i])
                            with self.lock:
                                self.finished += 1
                        parent = dirs[prefix]
                    if not entry.isdir:
                        self.write_member(entry, child(parent, names[-1]), names)
            if self.selection and matched != self.selection:
                raise ValueError("Some selected entries are missing.")
            if skipped:
                self.record(
                    self.file,
                    f"Skipped {skipped} links, special entries or unsupported paths.",
                )
        except Exception as exc:
            self.record(self.file, exc)
        finally:
            self.finish()


def from_clipboard(payload, dest):
    record = json.loads(bytes(payload))
    uri = record["archive"]
    selected = record["members"]
    if (
        not isinstance(uri, str)
        or not uri.startswith(("file://", "smb://"))
        or not isinstance(selected, list)
        or not selected
    ):
        raise ValueError("Invalid archive selection.")
    paths = [parts(name) for name in selected]
    if not all(paths):
        raise ValueError("Invalid archive entry.")
    return ExtractJob(resolve(uri), dest, selection=paths, stamp=record["version"])


class ArchiveBrowser(QDialog):
    def __init__(self, window, file):
        super().__init__(window)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowTitle(leaf(file) + " · ROOT")
        self.resize(700, 460)
        self.file = file
        self.cancel = Gio.Cancellable()
        self.children = {}
        self.stamp = None
        layout = QVBoxLayout(self)
        layout.addWidget(label(leaf(file), "h1"))
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Name", "Size"])
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tree.itemExpanded.connect(self.expand)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self.menu)
        layout.addWidget(self.tree)
        self.status = label("Reading archive…", "muted")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        actions = QHBoxLayout()
        self.copy_button = button("Copy selection", self.copy)
        actions.addWidget(self.copy_button)
        actions.addStretch()
        actions.addWidget(button("Close", self.close))
        layout.addLayout(actions)
        self.copy_button.setEnabled(False)
        self.tree.itemSelectionChanged.connect(
            lambda: self.copy_button.setEnabled(bool(self.tree.selectedItems()))
        )
        self.key = QShortcut(QKeySequence.Copy, self, activated=self.copy)
        self.finished.connect(lambda *_: self.cancel.cancel())
        self.destroyed.connect(lambda _, c=self.cancel: c.cancel())

        def done(result, error):
            if self.cancel.is_cancelled():
                return
            if error:
                self.status.setText("Could not open archive. " + str(error))
                return
            self.children, skipped, self.stamp = result
            self.fill(self.tree, ())
            self.status.setText(
                (
                    "Empty archive."
                    if not self.children
                    else "Select files or folders to copy, then paste in a ROOT window."
                )
                + (f"   {skipped} unsupported entries omitted." if skipped else "")
            )

        window.svc.workers.start(
            lambda: index_archive(file, self.cancel), done, self.cancel
        )

    def fill(self, parent, path):
        from views import ICON

        for name, (folder, size) in sorted(
            self.children.get(path, {}).items(),
            key=lambda x: (not x[1][0], x[0].casefold()),
        ):
            node = QTreeWidgetItem(
                parent, [name, fmt(size) if size is not None else "—"]
            )
            node.setIcon(
                0,
                ICON(
                    "folder" if folder else "file", "#c2a6ff" if folder else "#bcaecb"
                ),
            )
            node.setData(0, Qt.UserRole, "/".join((*path, name)))
            if folder and (*path, name) in self.children:
                node.setChildIndicatorPolicy(node.ShowIndicator)

    def expand(self, node):
        if not node.childCount():
            self.fill(node, parts(node.data(0, Qt.UserRole)))

    def menu(self, point):
        item = self.tree.itemAt(point)
        if not item:
            return
        if not item.isSelected():
            self.tree.clearSelection()
            item.setSelected(True)
        m = QMenu(self)
        m.addAction("Copy", self.copy)
        m.exec_(self.tree.viewport().mapToGlobal(point))

    def copy(self):
        paths = {parts(node.data(0, Qt.UserRole)) for node in self.tree.selectedItems()}
        selected = [
            "/".join(p)
            for p in sorted(paths)
            if not any(p[:i] in paths for i in range(1, len(p)))
        ]
        if not selected:
            return
        mime = QMimeData()
        mime.setData(
            MIME,
            json.dumps(
                dict(archive=self.file.get_uri(), members=selected, version=self.stamp)
            ).encode(),
        )
        QApplication.clipboard().setMimeData(mime)
        self.status.setText(
            f"{len(selected)} selected for copying. Paste in a ROOT window."
        )
