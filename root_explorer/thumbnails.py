"""Decode visible picture thumbnails on workers. Qt pixmaps stay on the GUI thread."""

from collections import OrderedDict
from pathlib import Path
from gi.repository import Gio
from PyQt5 import sip
from PyQt5.QtCore import QBuffer, QIODevice, QSize, Qt
from PyQt5.QtGui import QIcon, QImageReader, QPixmap

THUMB_SIZE = QSize(82, 55)
FORMATS = {b"jpeg", b"jpg", b"png", b"gif", b"webp", b"bmp", b"tiff", b"tif"}
EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff"}
NETWORK_LIMIT = 16 * 1024 * 1024


def decode(entry, cancel):
    local = entry.file.get_path()
    if local:
        reader = QImageReader(local)
    else:
        if entry.size > NETWORK_LIMIT:
            return None
        stream = entry.file.read(cancel)
        buf = QBuffer()
        buf.open(QIODevice.ReadWrite)
        try:
            while True:
                part = stream.read_bytes(128 * 1024, cancel).get_data()
                if not part:
                    break
                if buf.size() + len(part) > NETWORK_LIMIT:
                    return None
                buf.write(part)
        finally:
            stream.close(None)
        buf.seek(0)
        reader = QImageReader(buf)
    reader.setDecideFormatFromContent(True)
    if bytes(reader.format()).lower() not in FORMATS:
        return None
    size = reader.size()
    if not size.isValid() or size.width() * size.height() > 80_000_000:
        return None
    reader.setAutoTransform(True)
    reader.setScaledSize(size.boundedTo(size.scaled(THUMB_SIZE, Qt.KeepAspectRatio)))
    cancel.set_error_if_cancelled()
    image = reader.read()
    if not image.isNull():
        return image


class Thumbnails:
    def __init__(self, table, workers):
        self.table = table
        self.workers = workers
        self.cache = OrderedDict()
        self.enabled = False
        self.reset()

    def reset(self):
        if hasattr(self, "cancel"):
            self.cancel.cancel()
        self.cancel = Gio.Cancellable()
        self.pending = set()

    def get(self, entry):
        if (
            not self.enabled
            or entry.directory
            or Path(entry.name).suffix.lower() not in EXTENSIONS
        ):
            return None
        key = (entry.file.get_uri(), entry.size, entry.modified, entry.revision)
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key]
        if key in self.pending or len(self.pending) >= 3:
            return None
        cancel = self.cancel
        self.pending.add(key)

        def done(image, error):
            if cancel.is_cancelled() or sip.isdeleted(self.table):
                return
            self.pending.discard(key)
            self.cache[key] = (
                QIcon(QPixmap.fromImage(image)) if image is not None else None
            )
            if len(self.cache) > 256:
                self.cache.popitem(last=False)
            self.table.viewport().update()

        self.workers.start(lambda: decode(entry, cancel), done, cancel)
        return None
