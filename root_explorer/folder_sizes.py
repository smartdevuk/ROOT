"""One cancellable folder-size scan shared by all explorer windows."""

from collections import deque

from gi.repository import Gio, GLib
from PyQt5.QtCore import QObject, QTimer, pyqtSignal

from files import DIR, FILE, NOFOLLOW, iter_info, symlink


def folder_size(root, cancel):
    """Sum logical regular-file bytes, including hidden files. Never descend into links."""
    attrs = "standard::name,standard::type,standard::is-symlink,standard::size"
    identity = ",id::file" if root.is_native() else ""
    root_info = root.query_info("standard::type" + identity, NOFOLLOW, cancel)
    if root_info.get_file_type() != DIR:
        cancel.set_error_if_cancelled()
        raise ValueError("The folder is no longer available.")
    total = 0
    partial = False
    visited = {root_info.get_attribute_string("id::file")} if identity else set()
    todo = [root]
    while todo:
        folder = todo.pop()
        cancel.set_error_if_cancelled()
        try:
            for file, info in iter_info(folder, cancel, attrs + identity):
                if symlink(info):
                    continue
                kind = info.get_file_type()
                if kind == DIR:
                    key = info.get_attribute_string("id::file") if identity else None
                    if key and key in visited:
                        continue
                    if key:
                        visited.add(key)
                    todo.append(file)
                elif kind == FILE:
                    if info.has_attribute("standard::size"):
                        total += info.get_size()
                    else:
                        partial = True
        except GLib.Error:
            cancel.set_error_if_cancelled()
            if folder.equal(root):
                raise
            partial = True
    return total, "Some contents could not be read." if partial else ""


class FolderSizes(QObject):
    changed = pyqtSignal(str)

    def __init__(self, workers):
        super().__init__()
        self.workers = workers
        self.views = {}
        self.results = {}
        self.pending = deque()
        self.active = None
        self.delay = QTimer(self)
        self.delay.setSingleShot(True)
        self.delay.setInterval(250)
        self.delay.timeout.connect(self.advance)

    def watch(self, owner, folders, refresh=False):
        if not folders and owner not in self.views:
            return
        if folders:
            self.views[owner] = folders
        else:
            self.views.pop(owner, None)
        wanted = dict(folders)
        for view in self.views.values():
            wanted.update(view)
        self.results = {
            uri: size
            for uri, size in self.results.items()
            if uri in wanted and not (refresh and uri in folders)
        }
        running = None
        if self.active:
            uri, cancel = self.active
            if uri not in wanted or (refresh and uri in folders):
                cancel.cancel()
            if not cancel.is_cancelled():
                running = uri
        self.pending = deque(
            (uri, file)
            for uri, file in wanted.items()
            if uri != running and uri not in self.results
        )
        if self.pending:
            self.delay.start()
        else:
            self.delay.stop()
        self.changed.emit("")

    def advance(self):
        if self.active or self.delay.isActive() or not self.pending:
            return
        uri, file = self.pending.popleft()
        cancel = Gio.Cancellable()
        self.active = (uri, cancel)

        def done(result, error):
            self.active = None
            if not cancel.is_cancelled():
                self.results[uri] = (None, str(error)) if error else result
                self.changed.emit(uri)
            self.advance()

        # Even a job cancelled before it starts must return here to release the slot.
        self.workers.start(lambda: folder_size(file, cancel), done)
