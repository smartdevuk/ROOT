"""Streaming tree transfers with bounded parallelism and no-clobber publication."""

import ctypes
import errno
import os
import shutil
import stat
import threading
import time
import uuid
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from pathlib import Path
from gi.repository import Gio, GLib
from files import (
    ATTR,
    NOFOLLOW,
    DIR,
    FILE,
    LINK,
    child,
    display,
    gio_error,
    iter_info,
    leaf,
)

NATIVE_ONLY = (
    Gio.FileCopyFlags.NOFOLLOW_SYMLINKS | Gio.FileCopyFlags.NO_FALLBACK_FOR_MOVE
)
libc = ctypes.CDLL(None, use_errno=True)
renameat2 = libc.renameat2
renameat2.argtypes = [
    ctypes.c_int,
    ctypes.c_char_p,
    ctypes.c_int,
    ctypes.c_char_p,
    ctypes.c_uint,
]
renameat2.restype = ctypes.c_int


def relocate(src, dst, cancel=None):
    """A successful call moves exactly one entry without replacing another."""
    if cancel:
        cancel.set_error_if_cancelled()
    if src.is_native() and dst.is_native():
        a, b = map(os.fsencode, (src.get_path(), dst.get_path()))
        if renameat2(-100, a, -100, b, 1):
            err = ctypes.get_errno()
            raise OSError(err, os.strerror(err), os.fsdecode(b))
    else:
        src.move(dst, NATIVE_ONLY, cancel, None)


def publish(temp, target, cancel):
    try:
        relocate(temp, target, cancel)
        return
    except OSError as exc:
        if exc.errno not in (errno.ENOSYS, errno.EOPNOTSUPP, errno.EINVAL):
            raise
        if not (temp.is_native() and target.is_native()):
            raise
    # Some ChromeOS shared volumes do not implement renameat2(RENAME_NOREPLACE).
    # Hard links and exclusive create both preserve a concurrently created name.
    src, dst = temp.get_path(), target.get_path()
    try:
        os.link(src, dst, follow_symlinks=False)
    except OSError as exc:
        if exc.errno not in (
            errno.EXDEV,
            errno.EPERM,
            errno.ENOSYS,
            errno.EOPNOTSUPP,
            errno.EINVAL,
        ):
            raise
    else:
        os.unlink(src)
        return
    mode = os.lstat(src).st_mode
    if stat.S_ISLNK(mode):
        os.symlink(os.readlink(src), dst)
    else:
        with open(src, "rb") as reader:
            fd = os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_EXCL, stat.S_IMODE(mode))
            identity = os.fstat(fd)
            try:
                with os.fdopen(fd, "wb") as writer:
                    while True:
                        cancel.set_error_if_cancelled()
                        block = reader.read(1024 * 1024)
                        if not block:
                            break
                        writer.write(block)
                    writer.flush()
                try:
                    shutil.copystat(src, dst, follow_symlinks=False)
                except OSError as exc:
                    if exc.errno not in (
                        errno.EACCES,
                        errno.EPERM,
                        errno.EOPNOTSUPP,
                        errno.EINVAL,
                    ):
                        raise
            except BaseException:
                try:
                    if os.path.samestat(identity, os.lstat(dst)):
                        os.unlink(dst)
                except FileNotFoundError:
                    pass
                raise
    os.unlink(src)


def occupied(exc):
    return isinstance(exc, FileExistsError) or gio_error(exc, Gio.IOErrorEnum.EXISTS)


def same_or_inside(src, dst):
    if src.is_native() and dst.is_native():
        a, b = map(os.path.realpath, (src.get_path(), dst.get_path()))
        return os.path.commonpath((a, b)) == a
    a, b = [f.get_uri().casefold().rstrip("/") for f in (src, dst)]
    return b == a or b.startswith(a + "/")


def version(info):
    return (
        info.get_size(),
        info.get_attribute_uint64("time::modified"),
        info.get_attribute_uint32("time::modified-usec"),
        info.get_attribute_string("etag::value"),
        info.get_attribute_string("id::file"),
    )


def names(dst, is_folder):
    yield dst
    name = leaf(dst)
    ext = "" if is_folder else Path(name).suffix
    stem = name[: -len(ext)] if ext else name
    n = 1
    while True:
        suffix = " (copy)" if n == 1 else f" (copy {n})"
        yield child(dst.get_parent(), stem + suffix + ext)
        n += 1


class Job:
    def __init__(self, sources, dest, move=False):
        self.id = uuid.uuid4().hex
        self.sources, self.dest, self.move = list(sources), dest, move
        self.action = "Move" if move else "Copy"
        self.title = (
            leaf(self.sources[0]) if len(sources) == 1 else f"{len(sources)} items"
        )
        self.lock = threading.RLock()
        self.cancel = Gio.Cancellable()
        self.rows = OrderedDict()
        self.bytes = self.finished = self.failures = self.renamed = 0
        self.errors = []
        self.status = "Queued"
        self.started = self.ended = 0.0
        self.finished_at = ""
        self._created = set()
        self._created_ids = set()

    def snapshot(self):
        with self.lock:
            return (
                self.status,
                self.bytes,
                self.finished,
                self.failures,
                dict(self.rows),
            )

    def record(self, path, exc):
        if self.cancel.is_cancelled() and gio_error(exc, Gio.IOErrorEnum.CANCELLED):
            return
        with self.lock:
            self.failures += 1
            if len(self.errors) < 100:
                self.errors.append(f"{display(path)} — {exc}")

    def set_row(self, key, name, offset, size, state):
        with self.lock:
            self.rows[key] = (name, offset, size, state)
            while len(self.rows) > 256:
                oldest = next(
                    (k for k, row in self.rows.items() if row[3] != "working"), None
                )
                if oldest is None:
                    break
                self.rows.pop(oldest)

    def keep_both(self, dst, is_dir, op):
        for candidate in names(dst, is_dir):
            self.cancel.set_error_if_cancelled()
            try:
                op(candidate)
            except Exception as exc:
                if occupied(exc):
                    continue
                raise
            if not candidate.equal(dst):
                with self.lock:
                    self.renamed += 1
            return candidate

    def file_transfer(self, src, dst, info):
        size = info.get_size()
        key = src.get_uri()
        name = leaf(src)
        offset = 0
        self.set_row(key, name, 0, size, "working")
        temp = child(dst.get_parent(), ".root-" + uuid.uuid4().hex + ".part")

        def on_progress(done, total, *args):
            nonlocal offset
            with self.lock:
                self.bytes += max(0, done - offset)
                offset = max(offset, done)
                self.set_row(key, name, offset, size, "working")

        cleanup = False
        try:
            if info.get_file_type() not in (FILE, LINK):
                raise ValueError("This file type cannot be copied.")
            try:
                src.copy(
                    temp, Gio.FileCopyFlags.NOFOLLOW_SYMLINKS, self.cancel, on_progress
                )
            except Exception as exc:
                cleanup = not occupied(exc)
                raise
            cleanup = True
            staged = temp.query_info(ATTR, NOFOLLOW, self.cancel)
            if info.get_file_type() == FILE and staged.get_size() != size:
                raise IOError("The destination size differs from the source.")
            if version(info) != version(src.query_info(ATTR, NOFOLLOW, self.cancel)):
                raise IOError("The source changed during the transfer.")
            self.keep_both(
                dst, False, lambda target: publish(temp, target, self.cancel)
            )
            cleanup = False
            if self.move:
                # Re-check after publication. Failure leaves the committed copy in place.
                if version(info) != version(
                    src.query_info(ATTR, NOFOLLOW, self.cancel)
                ):
                    raise IOError("The source changed and was kept.")
                src.delete(self.cancel)
            with self.lock:
                self.finished += 1
            self.set_row(key, name, size, size, "done")
        except Exception as exc:
            self.set_row(
                key,
                name,
                offset,
                size,
                "stopped" if self.cancel.is_cancelled() else "error",
            )
            self.record(src, exc)
        finally:
            # Staging data belongs to this operation, including after cancellation.
            try:
                if cleanup:
                    temp.delete(None)
            except GLib.Error as exc:
                if not gio_error(exc, Gio.IOErrorEnum.NOT_FOUND):
                    self.record(temp, exc)

    def native_move(self, src, dst, is_dir):
        if src.is_native() != dst.is_native():
            return False
        if not src.is_native():
            # Only ask for a server-side rename inside the same share.
            if (
                src.get_uri().casefold().split("/")[:4]
                != dst.get_uri().casefold().split("/")[:4]
            ):
                return False
        try:
            self.keep_both(dst, is_dir, lambda d: relocate(src, d, self.cancel))
        except OSError as exc:
            if exc.errno in (errno.EXDEV, errno.ENOSYS, errno.EOPNOTSUPP, errno.EINVAL):
                return False
            raise
        except GLib.Error as exc:
            if any(
                gio_error(exc, code)
                for code in (
                    Gio.IOErrorEnum.NOT_SUPPORTED,
                    Gio.IOErrorEnum.WOULD_RECURSE,
                )
            ):
                return False
            raise
        with self.lock:
            self.finished += 1
        self.set_row(src.get_uri(), leaf(src), 0, 0, "moved")
        return True

    def run(self):
        self.started = time.monotonic()
        self.status = "Running"
        pending, directories = set(), []

        def schedule(pool, src, dst, info):
            nonlocal pending
            while len(pending) >= 9:
                _, pending = wait(pending, return_when=FIRST_COMPLETED)
                self.cancel.set_error_if_cancelled()
            pending.add(pool.submit(self.file_transfer, src, dst, info))

        def directory(src, dst):
            target = self.keep_both(dst, True, lambda d: d.make_directory(self.cancel))
            self._created.add(target.get_uri().casefold())
            identity = target.query_info(
                "id::file", NOFOLLOW, self.cancel
            ).get_attribute_string("id::file")
            if identity:
                self._created_ids.add(identity)
            if self.move:
                directories.append(src)
            return target

        try:
            with ThreadPoolExecutor(
                max_workers=3, thread_name_prefix="root-copy"
            ) as pool:
                for src in self.sources:
                    self.cancel.set_error_if_cancelled()
                    try:
                        info = src.query_info(ATTR, NOFOLLOW, self.cancel)
                        is_dir = info.get_file_type() == DIR
                        if is_dir and same_or_inside(src, self.dest):
                            raise ValueError(
                                "Choose a destination outside the source folder."
                            )
                        dst = child(self.dest, leaf(src))
                        if self.move and src.equal(dst):
                            continue
                        if self.move and self.native_move(src, dst, is_dir):
                            continue
                        if not is_dir:
                            schedule(pool, src, dst, info)
                            continue
                        stack = [(src, directory(src, dst))]
                        while stack:
                            self.cancel.set_error_if_cancelled()
                            folder, target = stack.pop()
                            try:
                                for item, meta in iter_info(folder, self.cancel):
                                    if item.get_uri().casefold() in self._created or (
                                        meta.get_attribute_string("id::file")
                                        in self._created_ids
                                    ):
                                        raise ValueError(
                                            "The destination appears inside the source tree."
                                        )
                                    item_dst = child(target, meta.get_name())
                                    if meta.get_file_type() == DIR:
                                        stack.append((item, directory(item, item_dst)))
                                    else:
                                        schedule(pool, item, item_dst, meta)
                            except Exception as exc:
                                self.record(folder, exc)
                    except Exception as exc:
                        self.record(src, exc)
                # The pool drains before source directories can be removed.
            if self.move and not self.cancel.is_cancelled():
                for folder in reversed(directories):
                    try:
                        folder.delete(
                            self.cancel
                        )  # Only succeeds on empty directories.
                    except Exception as exc:
                        self.record(folder, exc)
        except Exception as exc:
            self.record(self.dest, exc)
        finally:
            self.finish()

    def finish(self):
        from datetime import datetime

        self.ended = time.monotonic()
        self.finished_at = datetime.now().astimezone().isoformat(timespec="seconds")
        with self.lock:
            self.status = (
                "Cancelled"
                if self.cancel.is_cancelled()
                else "Finished with errors" if self.failures else "Complete"
            )

    def summary(self):
        return dict(
            id=self.id,
            title=self.title,
            action=self.action,
            status=self.status,
            dest=display(self.dest),
            bytes=self.bytes,
            finished=self.finished,
            failures=self.failures,
            errors=self.errors,
            renamed=self.renamed,
            elapsed=max(0, self.ended - self.started),
            date=self.finished_at,
        )
