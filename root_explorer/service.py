"""Shared desktop state, async work, SMB authentication and the transfer queue."""

import json
import os
from pathlib import Path
import queue
import threading
import time
from collections import deque
from urllib.parse import urlsplit

import gi

gi.require_version("Secret", "1")
from gi.repository import Secret

from gi.repository import Gio, GLib
from PyQt5.QtCore import QObject, Qt, QTimer, pyqtSignal, QSaveFile, QIODevice
from PyQt5.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
)
from files import display, gio_error, server, translate_server
from theme import label
from telemetry import Throughput
from transfers import Job


class Workers(QObject):
    returned = pyqtSignal(object)

    def __init__(self):
        super().__init__()
        self.returned.connect(lambda f: f(), Qt.QueuedConnection)
        self.tasks = queue.Queue()
        for _ in range(6):
            threading.Thread(target=self.work, daemon=True, name="root-browser").start()

    def work(self):
        while True:
            fn, done, cancel = self.tasks.get()
            if cancel and cancel.is_cancelled():
                continue
            try:
                result, error = fn(), None
            except Exception as exc:
                result, error = None, exc
            self.returned.emit(lambda r=result, e=error, d=done: d(r, e))

    def start(self, fn, done, cancel=None):
        self.tasks.put((fn, done, cancel))


class State:
    def __init__(self):
        folder = (
            Path(os.getenv("XDG_CONFIG_HOME", Path.home() / ".config"))
            / "root-explorer"
        )
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path = folder / "settings.json"
        try:
            data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        self.data = {
            k: data[k] if isinstance(data.get(k), type(default)) else default
            for k, default in dict(
                recents=[],
                pins=[],
                history=[],
                hidden=False,
                nas_addresses={},
                thumbnails=False,
            ).items()
        }

    def save(self):
        stream = QSaveFile(str(self.path))
        if not stream.open(QIODevice.WriteOnly):
            return
        stream.setPermissions(QSaveFile.ReadOwner | QSaveFile.WriteOwner)
        stream.write(json.dumps(self.data, ensure_ascii=True).encode())
        stream.commit()


class Service(QObject):
    changed = pyqtSignal()
    updated = pyqtSignal()
    started = pyqtSignal()
    settled = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.workers = Workers()
        self.store = State()
        self.windows = []
        self.pending = deque()
        self.job = None
        self.series = Throughput()
        self.series.running = False
        self.mounts = {}
        self.pump = QTimer(self)
        self.pump.setInterval(25)
        self.pump.timeout.connect(self.glib_tick)
        self.timer = QTimer(self)
        self.timer.setInterval(180)
        self.timer.timeout.connect(self.tick)

    def window(self, file=None):
        from explorer import Window

        w = Window(self, file)
        self.windows.append(w)
        w.show()
        return w

    def endpoint(self, logical, override=None):
        host = server(logical)
        address = (
            self.store.data["nas_addresses"].get(host, "")
            if override is None
            else override
        )
        return translate_server(logical, host, address)

    def remember_address(self, logical, address):
        addresses = self.store.data["nas_addresses"]
        if address:
            addresses[server(logical)] = address
        else:
            addresses.pop(server(logical), None)
        self.store.save()

    def recent(self, file):
        uri = file.get_uri()
        old = self.store.data["recents"]
        self.store.data["recents"] = ([uri] + [r for r in old if r != uri])[:60]
        self.store.save()

    def pin(self, file):
        uri = file.get_uri()
        items = self.store.data["pins"]
        if uri in items:
            items.remove(uri)
        else:
            items.append(uri)
        self.store.save()
        self.changed.emit()

    def enqueue(self, files, dest, move=False):
        self.enqueue_job(Job(files, dest, move))

    def enqueue_job(self, job):
        self.pending.append(job)
        if not self.timer.isActive():
            self.next_job()
        self.updated.emit()

    def next_job(self):
        if not self.pending:
            self.timer.stop()
            return
        self.job = self.pending.popleft()
        self.series.reset(time.monotonic())
        threading.Thread(target=self.job.run, daemon=True, name="root-transfer").start()
        self.timer.start()
        self.started.emit()

    def tick(self):
        job = self.job
        status, amount, _, _, rows = job.snapshot()
        done = status not in ("Queued", "Running")
        self.series.sample(
            job.ended if done else time.monotonic(),
            amount,
            {k: v for k, v in rows.items() if v[3] == "working"},
            done,
        )
        if done:
            history = self.store.data["history"]
            history.insert(0, job.summary())
            del history[150:]
            self.store.save()
        self.updated.emit()
        if done:
            self.settled.emit()
            self.next_job()

    def forget(self, key):
        data = self.store.data
        data["history"] = [r for r in data["history"] if r["id"] != key]
        if self.job and self.job.id == key and not self.timer.isActive():
            self.job = None
        self.store.save()
        self.updated.emit()
        self.changed.emit()

    def mount(self, file, window, callback, cancel):
        op = Gio.MountOperation()

        def ask(op, msg, user, domain, flags):
            if cancel.is_cancelled() or window.closed:
                op.reply(Gio.MountOperationResult.ABORTED)
            else:
                self.credentials(op, window, file, msg, user, domain, flags)

        op.connect("ask-password", ask)
        self.mounts[id(op)] = op

        def finished(source, result, *_):
            try:
                source.mount_enclosing_volume_finish(result)
                error = None
            except GLib.Error as exc:
                error = None if gio_error(exc, Gio.IOErrorEnum.ALREADY_MOUNTED) else exc
            self.mounts.pop(id(op), None)
            if not self.mounts:
                self.pump.stop()
            if not cancel.is_cancelled():
                callback(error)

        file.mount_enclosing_volume(Gio.MountMountFlags.NONE, op, cancel, finished)
        self.pump.start()

    def reset_login(self, file, cancel, callback):
        endpoint = urlsplit(file.get_uri())

        def erase():
            schema = Secret.get_schema(Secret.SchemaType.COMPAT_NETWORK)
            attrs = {"protocol": "smb", "server": endpoint.hostname}
            service = Secret.Service.get_sync(Secret.ServiceFlags.NONE, cancel)
            items = service.search_sync(schema, attrs, Secret.SearchFlags.ALL, cancel)
            for item in items:
                fields = item.get_attributes()
                if fields.get("port", "0") == str(
                    endpoint.port or 0
                ) and not fields.get("object"):
                    item.delete_sync(cancel)

        def forget():
            self.workers.start(erase, lambda _, error: callback(error), cancel)

        def lookup():
            try:
                return file.find_enclosing_mount(cancel)
            except GLib.Error as exc:
                if gio_error(exc, Gio.IOErrorEnum.NOT_MOUNTED) or gio_error(
                    exc, Gio.IOErrorEnum.NOT_FOUND
                ):
                    return None
                raise

        def found(mount, error):
            if cancel.is_cancelled():
                return
            if error:
                callback(error)
            elif mount is None:
                forget()
            else:
                op = Gio.MountOperation()
                self.mounts[id(op)] = op

                def unmounted(source, result, *_):
                    self.mounts.pop(id(op), None)
                    if not self.mounts:
                        self.pump.stop()
                    try:
                        source.unmount_with_operation_finish(result)
                    except GLib.Error as exc:
                        if not gio_error(exc, Gio.IOErrorEnum.NOT_MOUNTED):
                            callback(exc)
                            return
                    if not cancel.is_cancelled():
                        forget()

                mount.unmount_with_operation(
                    Gio.MountUnmountFlags.NONE, op, cancel, unmounted
                )
                self.pump.start()

        self.workers.start(lookup, found, cancel)

    def glib_tick(self):
        loop = GLib.MainContext.default()
        for _ in range(12):
            if not loop.pending():
                break
            loop.iteration(False)

    def credentials(self, op, window, file, message, user, domain, flags):
        parent = window if window in self.windows else QApplication.activeWindow()
        dialog = QDialog(parent)
        dialog.setWindowTitle("Network sign-in")
        form = QFormLayout(dialog)
        form.addRow(label(display(file), "strong"))
        form.addRow(label(message.split("\n")[0], "muted"))
        login, group, password = (
            QLineEdit(user or ""),
            QLineEdit(domain or ""),
            QLineEdit(),
        )
        password.setEchoMode(QLineEdit.Password)
        opts = Gio.AskPasswordFlags
        if flags & opts.NEED_USERNAME:
            form.addRow("Username", login)
        if flags & opts.NEED_DOMAIN:
            form.addRow("Domain", group)
        if flags & opts.NEED_PASSWORD:
            form.addRow("Password", password)
        guest = QCheckBox("Connect as guest")
        if flags & opts.ANONYMOUS_SUPPORTED:
            form.addRow(guest)
            guest.toggled.connect(
                lambda b: [edit.setDisabled(b) for edit in (login, group, password)]
            )
        remember = QCheckBox("Remember this login in the Linux keyring")
        if flags & opts.SAVING_SUPPORTED:
            form.addRow(remember)
            remember.setChecked(True)
            guest.toggled.connect(remember.setDisabled)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec_() != QDialog.Accepted:
            op.reply(Gio.MountOperationResult.ABORTED)
            return
        op.set_anonymous(guest.isChecked())
        if not guest.isChecked():
            op.set_username(login.text())
            op.set_domain(group.text())
            op.set_password(password.text())
        save = remember.isChecked() and not guest.isChecked()
        op.set_password_save(
            Gio.PasswordSave.PERMANENTLY if save else Gio.PasswordSave.NEVER
        )
        op.reply(Gio.MountOperationResult.HANDLED)
