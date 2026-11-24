"""File list, properties, previews, and live transfer views."""

from datetime import datetime
from functools import lru_cache
from pathlib import Path
import time

from gi.repository import Gio
from PyQt5.QtCore import (
    Qt,
    QAbstractTableModel,
    QSortFilterProxyModel,
    QModelIndex,
    QMimeData,
    QSize,
    QUrl,
    pyqtSignal,
)
from PyQt5.QtGui import QPixmap, QDrag, QImageReader
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLineEdit,
    QHeaderView,
    QProgressBar,
    QSizePolicy,
    QMessageBox,
    QPlainTextEdit,
    QStyledItemDelegate,
    QTableView,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)
from files import display, fmt, icon_kind, leaf, measure
from theme import button, icon, label
from thumbnails import Thumbnails, THUMB_SIZE
from telemetry import FileLanes, Monitor

ICON = lru_cache(maxsize=64)(icon)


def timestamp(seconds):
    if not seconds:
        return "—"
    try:
        return datetime.fromtimestamp(seconds).strftime("%d %b %Y  %H:%M")
    except (OverflowError, OSError, ValueError):
        return "—"


class FileModel(QAbstractTableModel):
    columns = ("Name", "Size", "Type", "Modified")
    rename_requested = pyqtSignal(object, str)

    def __init__(self, parent):
        super().__init__(parent)
        self.rows = []
        self.thumbnails = None

    def replace(self, rows):
        if self.thumbnails:
            self.thumbnails.reset()
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return len(self.columns)

    def headerData(self, section, orientation, role):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return self.columns[section]

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return
        row = self.rows[index.row()]
        col = index.column()
        if role == Qt.EditRole and col == 0:
            return leaf(row.file)
        if role == Qt.DisplayRole:
            if col == 0:
                return row.name
            if col == 1:
                return "—" if row.directory else fmt(row.size)
            if col == 2:
                return row.kind
            return timestamp(row.modified)
        if role == Qt.DecorationRole and col == 0:
            thumb = self.thumbnails.get(row) if self.thumbnails else None
            return thumb or ICON(*icon_kind(row.name, row.kind))
        if role == Qt.TextAlignmentRole and col == 1:
            return int(Qt.AlignVCenter | Qt.AlignRight)
        if role == Qt.ToolTipRole:
            return display(row.file)

    def flags(self, index):
        flags = super().flags(index) | Qt.ItemIsDragEnabled
        if index.isValid() and index.column() == 0:
            flags |= Qt.ItemIsEditable
        return flags

    def setData(self, index, value, role=Qt.EditRole):
        if role != Qt.EditRole or not index.isValid() or index.column() != 0:
            return False
        row = self.rows[index.row()]
        if value != leaf(row.file):
            self.rename_requested.emit(row, value)
        return True


class Filter(QSortFilterProxyModel):
    def __init__(self, model):
        super().__init__(model)
        self.setSourceModel(model)
        self.needle = ""
        self.hidden = False

    def filterAcceptsRow(self, n, parent):
        row = self.sourceModel().rows[n]
        return (self.hidden or not row.hidden) and self.needle in row.name.casefold()

    def lessThan(self, a, b):
        left, right = [self.sourceModel().rows[idx.row()] for idx in (a, b)]
        if left.directory != right.directory:
            return (
                left.directory
                if self.sortOrder() == Qt.AscendingOrder
                else right.directory
            )
        col = a.column()
        if col == 0:
            return left.name.casefold() < right.name.casefold()
        attr = ("name", "size", "kind", "modified")[col]
        return (getattr(left, attr), left.name.casefold()) < (
            getattr(right, attr),
            right.name.casefold(),
        )


class FileDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        table = self.parent()
        if table.cut_uris and table.entry(index).file.get_uri() in table.cut_uris:
            painter.save()
            painter.setOpacity(0.5)
            super().paint(painter, option, index)
            painter.restore()
        else:
            super().paint(painter, option, index)


class FileTable(QTableView):
    dropped = pyqtSignal(object, object, bool)

    def __init__(self):
        super().__init__()
        self.cut_uris = set()
        self.setItemDelegate(FileDelegate(self))
        self.files = FileModel(self)
        self.proxy = Filter(self.files)
        self.setModel(self.proxy)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setAlternatingRowColors(True)
        self.setShowGrid(False)
        self.setWordWrap(False)
        self.setIconSize(QSize(20, 20))
        self.verticalHeader().hide()
        self.verticalHeader().setDefaultSectionSize(34)
        header = self.horizontalHeader()
        header.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        for col, width in ((1, 85), (2, 112), (3, 150)):
            self.setColumnWidth(col, width)
        self.setSortingEnabled(True)
        self.sortByColumn(0, Qt.AscendingOrder)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDrop)
        self.setAccessibleName("Files and folders")

    def set_thumbnails(self, enabled, workers):
        if self.files.thumbnails is None:
            self.files.thumbnails = Thumbnails(self, workers)
        thumbs = self.files.thumbnails
        if thumbs.enabled == enabled:
            return
        thumbs.reset()
        thumbs.enabled = enabled
        self.setIconSize(THUMB_SIZE if enabled else QSize(20, 20))
        self.verticalHeader().setDefaultSectionSize(64 if enabled else 34)
        self.viewport().update()

    def entry(self, index):
        if index.isValid():
            return self.files.rows[self.proxy.mapToSource(index).row()]

    def selected(self):
        return [self.entry(index) for index in self.selectionModel().selectedRows()]

    def rename_selected(self):
        indices = self.selectionModel().selectedRows()
        if len(indices) != 1 or self.state() == QAbstractItemView.EditingState:
            return
        idx = indices[0]
        row = self.entry(idx)
        self.setCurrentIndex(idx)
        self.edit(idx)
        editor = self.indexWidget(idx)
        if isinstance(editor, QLineEdit):
            name = leaf(row.file)
            suffix = "" if row.directory else Path(name).suffix
            stem = name[: -len(suffix)] if suffix else name
            # QLineEdit uses UTF-16 offsets, including for emoji in file names.
            editor.setSelection(
                0, len(stem.encode("utf-16-le", errors="surrogatepass")) // 2
            )
            editor.setAccessibleName("Rename file")

    def startDrag(self, _):
        mime = QMimeData()
        mime.setUrls(
            [QUrl.fromEncoded(row.file.get_uri().encode()) for row in self.selected()]
        )
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec_(Qt.CopyAction | Qt.MoveAction, Qt.CopyAction)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.setDropAction(
                Qt.MoveAction
                if event.keyboardModifiers() & Qt.ShiftModifier
                else Qt.CopyAction
            )
            event.accept()

    def dropEvent(self, event):
        row = self.entry(self.indexAt(event.pos()))
        move = bool(event.keyboardModifiers() & Qt.ShiftModifier)
        paths = [
            bytes(u.toEncoded()).decode()
            for u in event.mimeData().urls()
            if u.scheme() in ("file", "smb")
        ]
        if paths:
            self.dropped.emit(
                paths, row.target if row and row.directory else None, move
            )
            # ROOT owns source removal. External drag sources must not delete a second time.
            event.setDropAction(Qt.CopyAction)
            event.accept()


class Properties(QDialog):
    def __init__(self, window, file):
        super().__init__(window)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowTitle("Properties")
        self.resize(590, 440)
        self.cancel = Gio.Cancellable()
        self.finished.connect(lambda _: self.cancel.cancel())
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.addWidget(label(leaf(file), "h1"))
        path = label(display(file), "micro")
        path.setWordWrap(True)
        path.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(path)
        self.summary = label("Calculating size…", "h2")
        layout.addWidget(self.summary)
        self.detail = label("", "muted")
        layout.addWidget(self.detail)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Contents", "Size", "Share"])
        self.tree.setRootIsDecorated(False)
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tree.setColumnWidth(1, 96)
        self.tree.setColumnWidth(2, 65)
        layout.addWidget(self.tree, 1)
        self.errors = label("", "warning")
        self.errors.setWordWrap(True)
        layout.addWidget(self.errors)
        box = QDialogButtonBox(QDialogButtonBox.Close)
        box.rejected.connect(self.reject)
        layout.addWidget(box)
        last = 0.0

        def progress(size, count, dirs):
            nonlocal last
            now = time.monotonic()
            if now - last > 0.18:
                last = now
                window.svc.workers.returned.emit(lambda: self.counts(size, count, dirs))

        window.svc.workers.start(
            lambda: measure(file, self.cancel, progress),
            self.measurement_ready,
            self.cancel,
        )

    def counts(self, size, files, folders):
        if not self.cancel.is_cancelled():
            self.summary.setText(fmt(size))
            self.detail.setText(
                f"{size:,} bytes   ·   {files:,} files   ·   {folders:,} folders"
            )

    def measurement_ready(self, result, error):
        if self.cancel.is_cancelled():
            return
        if error:
            self.summary.setText("Size unavailable")
            self.errors.setText(str(error))
            return
        size, files, dirs, groups, errors, allocated = result
        self.counts(size, files, dirs)
        if allocated is not None:
            self.detail.setText(
                self.detail.text() + f"   ·   {fmt(allocated)} allocated"
            )
        for (is_dir, name), amount in sorted(groups.items(), key=lambda pair: -pair[1]):
            item = QTreeWidgetItem(
                [name, fmt(amount), f"{amount/size:.1%}" if size else "0%"]
            )
            item.setIcon(0, ICON("folder" if is_dir else "file"))
            item.setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)
            self.tree.addTopLevelItem(item)
        self.tree.setVisible(bool(groups))
        if errors:
            self.errors.setText(
                f"Partial total · {len(errors)} locations could not be read."
            )
            self.errors.setToolTip("\n".join(errors[:20]))


def preview(window, row):
    if row.directory:
        return window.properties(row.file)
    dlg = QDialog(window)
    dlg.setWindowTitle("Preview")
    dlg.setAttribute(Qt.WA_DeleteOnClose)
    dlg.resize(590, 420)
    layout = QVBoxLayout(dlg)
    layout.addWidget(label(row.name, "h2"))
    layout.addWidget(
        label(
            f"{row.kind}   ·   {fmt(row.size)}   ·   {timestamp(row.modified)}", "muted"
        )
    )
    holder = label("Loading preview…", "muted")
    holder.setAlignment(Qt.AlignCenter)
    layout.addWidget(holder, 1)
    cancel = Gio.Cancellable()
    dlg.finished.connect(lambda _: cancel.cancel())

    def read():
        stream = row.file.read(cancel)
        try:
            return stream.read_bytes(min(row.size, 4 * 1024 * 1024), cancel).get_data()
        finally:
            stream.close(None)

    def done(data, error):
        if cancel.is_cancelled():
            return
        if error:
            holder.setText(str(error))
            return
        if icon_kind(row.name, row.kind)[0] == "image":
            from PyQt5.QtCore import QBuffer, QByteArray, QIODevice

            buffer = QBuffer()
            buffer.setData(QByteArray(data))
            buffer.open(QIODevice.ReadOnly)
            reader = QImageReader(buffer)
            size = reader.size()
            if size.isValid() and size.width() * size.height() <= 60_000_000:
                reader.setScaledSize(size.scaled(700, 550, Qt.KeepAspectRatio))
                image = reader.read()
                if not image.isNull():
                    holder.setPixmap(QPixmap.fromImage(image))
                    return
        try:
            if b"\0" in data[:4096]:
                raise ValueError()
            import codecs

            sample = data[:65536]
            content = codecs.getincrementaldecoder("utf-8")().decode(
                sample, final=False
            )
        except (ValueError, UnicodeError):
            holder.setText("No preview available for this file type.")
            return
        editor = QPlainTextEdit(content)
        editor.setReadOnly(True)
        layout.replaceWidget(holder, editor)
        holder.hide()

    window.svc.workers.start(read, done, cancel)
    dlg.show()
    return dlg


class StorageMeter(QWidget):
    def __init__(self):
        super().__init__()
        box = QVBoxLayout(self)
        box.setContentsMargins(7, 8, 7, 3)
        box.setSpacing(5)
        self.caption = label("Storage", "micro")
        self.value = label("", "strong")
        self.bar = QProgressBar()
        self.bar.setObjectName("capacity")
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(6)
        self.bar.setAccessibleName("Storage used")
        box.addWidget(self.caption)
        box.addWidget(self.value)
        box.addWidget(self.bar)

    def update_space(self, file, info=None, pending=False):
        self.caption.setText("NAS STORAGE" if not file.is_native() else "STORAGE")
        self.value.setText("Checking space…" if pending else "Space unavailable")
        self.bar.hide()
        self.setToolTip(display(file))
        if not info or not info.has_attribute("filesystem::free"):
            return
        free = info.get_attribute_uint64("filesystem::free")
        self.value.setText(fmt(free) + " free")
        total = info.get_attribute_uint64("filesystem::size")
        if not total and info.has_attribute("filesystem::used"):
            total = free + info.get_attribute_uint64("filesystem::used")
        if 0 < total and free <= total:
            used = total - free
            self.bar.setValue(round(1000 * used / total))
            self.bar.show()
            summary = f"{fmt(used)} used · {fmt(free)} free of {fmt(total)}"
            self.setToolTip(display(file) + "\n" + summary)
            self.bar.setAccessibleDescription(summary)
            self.value.setAccessibleDescription(summary)


class TransferStrip(QWidget):
    """Live copy information in the folder view. Completed jobs leave one summary row."""

    def __init__(self, window):
        super().__init__()
        self.svc = window.svc
        self.last_id = None
        self.dismissed = None
        self.collapsed = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)
        line = QHBoxLayout()
        self.summary = label("", "micro")
        self.summary.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        line.addWidget(self.summary, 1)
        self.details = button("Details", self.show_details)
        line.addWidget(self.details)
        self.cancel_button = button("Cancel", lambda: self.svc.job.cancel.cancel())
        line.addWidget(self.cancel_button)
        self.fold = button("", self.toggle)
        line.addWidget(self.fold)
        layout.addLayout(line)
        self.visual = QWidget()
        row = QHBoxLayout(self.visual)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.monitor = Monitor(self.svc.series)
        self.lanes = FileLanes()
        row.addWidget(self.monitor, 3)
        row.addWidget(self.lanes, 2)
        layout.addWidget(self.visual)
        self.render()

    def show_details(self):
        job = self.svc.job
        if not job:
            return
        record = job.summary()
        box = QMessageBox(
            QMessageBox.Information,
            job.title,
            f"{record['action']} · {record['status']}\n{record['dest']}\n"
            f"{record['finished']:,} items finished · {fmt(record['bytes'])}",
            parent=self,
        )
        box.setTextFormat(Qt.PlainText)
        if record["errors"]:
            box.setDetailedText("\n".join(record["errors"]))
        box.exec_()

    def toggle(self):
        job = self.svc.job
        if not job:
            return
        if job.status in ("Running", "Queued"):
            self.collapsed = not self.collapsed
        else:
            self.dismissed = job.id
        self.render()

    def render(self):
        job = self.svc.job
        if not job or self.dismissed == job.id:
            self.hide()
            return
        if self.last_id != job.id:
            self.last_id = job.id
            self.collapsed = False
        status, amount, count, failures, rows = job.snapshot()
        live = status in ("Running", "Queued")
        self.show()
        suffix = f" · {len(self.svc.pending)} queued" if self.svc.pending else ""
        text = f"{job.action} · {status} · {count:,} finished · {fmt(amount)}{suffix}"
        self.summary.setText(text)
        self.summary.setToolTip(job.title + " → " + display(job.dest))
        self.cancel_button.setVisible(live)
        self.details.setVisible(not live)
        self.fold.setText(
            ("Expand" if self.collapsed else "Collapse") if live else "Dismiss"
        )
        self.visual.setVisible(live and not self.collapsed)
        if live and not self.collapsed:
            self.monitor.display(job.action, status, True)
            self.lanes.set_files(rows, time.monotonic(), True)


class CopyHistory(QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.svc = window.svc
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowTitle("Copy and move history")
        self.resize(550, 360)
        layout = QVBoxLayout(self)
        self.list = QTreeWidget()
        self.list.setHeaderLabels(["Item", "Action", "Status"])
        self.list.setRootIsDecorated(False)
        self.list.header().setSectionResizeMode(0, QHeaderView.Stretch)
        layout.addWidget(self.list)
        self.details = label("", "muted")
        self.details.setWordWrap(True)
        self.details.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.details)
        self.remove = button("Remove from history", self.forget)
        buttons = QHBoxLayout()
        buttons.addWidget(self.remove)
        buttons.addStretch()
        buttons.addWidget(button("Close", self.accept))
        layout.addLayout(buttons)
        self.list.currentItemChanged.connect(self.select)
        self.svc.settled.connect(self.reload)
        self.svc.changed.connect(self.reload)
        self.reload()

    def reload(self):
        self.list.clear()
        for job in self.svc.store.data["history"]:
            row = QTreeWidgetItem(
                self.list, [job["title"], job["action"], job["status"]]
            )
            row.setData(0, Qt.UserRole, job)
        self.list.setCurrentItem(self.list.topLevelItem(0))
        self.select()

    def select(self, *_):
        item = self.list.currentItem()
        self.remove.setEnabled(bool(item))
        if not item:
            self.details.setText("No completed copies or moves.")
            return
        record = item.data(0, Qt.UserRole)
        self.details.setText(
            record["dest"]
            + "\n"
            + record["date"]
            + " · "
            + fmt(record["bytes"])
            + "\n"
            + "\n".join(record["errors"][:3])
        )
        self.details.setToolTip("\n".join(record["errors"]))

    def forget(self):
        if self.list.currentItem():
            self.svc.forget(self.list.currentItem().data(0, Qt.UserRole)["id"])
