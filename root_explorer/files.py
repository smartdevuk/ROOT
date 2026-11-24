"""Paths, directory listings and non-following recursive folder measurements."""

import ipaddress
import mimetypes
import os
import posixpath
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

from gi.repository import Gio, GLib

NOFOLLOW = Gio.FileQueryInfoFlags.NOFOLLOW_SYMLINKS
FOLLOW = Gio.FileQueryInfoFlags.NONE
ATTR = (
    "standard::name,standard::display-name,standard::type,standard::size,"
    "standard::is-hidden,standard::is-symlink,standard::target-uri,"
    "time::modified,time::modified-usec,etag::value,id::file"
)
MEASURE_ATTR = ATTR + ",standard::allocated-size,unix::nlink"
DIR, FILE, LINK = (
    Gio.FileType.DIRECTORY,
    Gio.FileType.REGULAR,
    Gio.FileType.SYMBOLIC_LINK,
)
BROWSE = (Gio.FileType.MOUNTABLE, Gio.FileType.SHORTCUT)


def fmt(value):
    value = max(0, value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB", "PiB"):
        if value < 1024 or unit == "PiB":
            return f"{int(value)} B" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024


def gio_error(exc, code):
    return isinstance(exc, GLib.Error) and exc.matches(Gio.io_error_quark(), code)


def smb_file(host, path):
    if not host or any(c in host for c in "@?#\\ /") or any(ord(c) < 32 for c in host):
        raise ValueError("Enter a server name or IP address, followed by a share name.")
    parts = posixpath.normpath("/" + path.lstrip("/"))
    return Gio.File.new_for_uri("smb://" + host.lower() + quote(parts, safe="/"))


def server(file):
    return urlsplit(file.get_uri()).netloc.casefold() if not file.is_native() else ""


def numeric_host(value):
    if not value:
        return ""
    address = ipaddress.ip_address(value.strip().strip("[]"))
    return f"[{address}]" if address.version == 6 else str(address)


def translate_server(file, source, target):
    if not source or not target or source == target or server(file) != source:
        return file
    url = urlsplit(file.get_uri())
    return smb_file(target, unquote(url.path))


def dns_hint(host, cancel):
    """Look up just the requested NAS name; .local is an optional user-confirmed suggestion."""
    resolver = Gio.Resolver.get_default()
    try:
        result = resolver.lookup_by_name(host, cancel)
        if result:
            return f"Linux resolves {host} to {result[0].to_string()}.", ""
    except GLib.Error as exc:
        if not exc.matches(Gio.resolver_error_quark(), Gio.ResolverError.NOT_FOUND):
            return "", ""
    if "." not in host:
        candidate = host + ".local"
        try:
            addresses = resolver.lookup_by_name(candidate, cancel)
            address = next(
                (a for a in addresses if a.get_family() == Gio.SocketFamily.IPV4),
                addresses[0],
            )
            return (
                f"Linux can resolve {candidate} to {address.to_string()}. Confirm the IP in Connection settings.",
                address.to_string(),
            )
        except (GLib.Error, IndexError):
            pass
    return (
        f"Linux DNS could not find {host}. Enter the NAS IP in Connection settings.",
        "",
    )


def share_root(file):
    url = urlsplit(file.get_uri())
    share = unquote(url.path).strip("/").split("/", 1)[0]
    return smb_file(url.netloc, share)


def resolve(text, current=None):
    if not text:
        raise ValueError("Enter a folder path.")
    if text.startswith(("\\\\", "//")):
        host, _, tail = text.lstrip("\\/").replace("\\", "/").partition("/")
        return smb_file(host, tail)
    if text.lower().startswith("smb://"):
        parsed = urlsplit(text)
        if parsed.query or parsed.fragment:
            raise ValueError("Use a Windows path for names containing # or ?.")
        return smb_file(parsed.netloc, unquote(parsed.path))
    if text.startswith("file://"):
        if urlsplit(text).netloc not in ("", "localhost"):
            raise ValueError("Use a Windows network path to open another computer.")
        return Gio.File.new_for_path(unquote(urlsplit(text).path))
    if "://" in text:
        raise ValueError("Enter a local folder path or an SMB network share.")
    if current and not text.startswith(("/", "~")) and not current.is_native():
        parsed = urlsplit(current.get_uri())
        return smb_file(
            parsed.netloc, posixpath.join(unquote(parsed.path), text.replace("\\", "/"))
        )
    base = current.get_path() if current and current.is_native() else str(Path.home())
    return Gio.File.new_for_path(
        os.path.abspath(os.path.join(base, os.path.expanduser(text)))
    )


def display(file):
    if file.is_native():
        return file.get_path()
    url = urlsplit(file.get_uri())
    path = unquote(url.path).strip("/").replace("/", "\\")
    return "\\\\" + url.netloc + ("\\" + path if path else "")


def leaf(file):
    return file.get_basename() or display(file)


def parent(file):
    if file.is_native():
        return file.get_parent()
    url = urlsplit(file.get_uri())
    tail = unquote(url.path).strip("/")
    return smb_file(url.netloc, posixpath.dirname(tail)) if tail else None


def valid_name(name):
    if not name or name in (".", "..") or "/" in name or "\0" in name:
        raise ValueError("Enter one file or folder name.")
    return name


def child(file, name):
    valid_name(name)
    if not file.is_native() and "\\" in name:
        raise ValueError("A network name cannot contain a backslash.")
    return file.get_child(name)


def icon_kind(name, kind):
    if kind == "Folder":
        return "folder", "#c2a6ff"
    if kind == "Link":
        return "link", "#97d9c6"
    mime = mimetypes.guess_type(name)[0] or ""
    if mime.startswith("video/"):
        return "video", "#bfa4f3"
    if mime.startswith("image/"):
        return "image", "#90cfbc"
    if mime.startswith("audio/"):
        return "music", "#e9b38e"
    if name.lower().endswith((".py", ".js", ".html", ".sh", ".json", ".css", ".php")):
        return "code", "#99c4ea"
    return "file", "#bcaecb"


@dataclass
class Entry:
    file: object
    name: str
    kind: str
    size: int
    modified: int
    hidden: bool
    directory: bool
    target: object
    revision: object = None


def iter_info(folder, cancel=None, attributes=ATTR):
    enum = folder.enumerate_children(attributes, NOFOLLOW, cancel)
    try:
        while True:
            info = enum.next_file(cancel)
            if info is None:
                break
            yield enum.get_child(info), info
    finally:
        enum.close(None)


def symlink(info):
    return info.get_file_type() == LINK or info.get_attribute_boolean(
        "standard::is-symlink"
    )


def listing(folder, cancel=None):
    rows = []
    for file, info in iter_info(folder, cancel):
        t = info.get_file_type()
        target = file
        directory = t == DIR
        if t in BROWSE:
            uri = info.get_attribute_string("standard::target-uri")
            if uri and uri.lower().startswith("smb://"):
                target = resolve(uri)
            directory = True
        if symlink(info) and file.is_native():
            directory = os.path.isdir(file.get_path())
        kind = "Link" if symlink(info) or t == LINK else "Folder" if directory else ""
        if not kind:
            suffix = Path(info.get_name()).suffix[1:].upper()
            kind = (suffix + " file") if suffix else "File"
        rows.append(
            Entry(
                file,
                info.get_display_name(),
                kind,
                info.get_attribute_uint64("standard::size"),
                info.get_attribute_uint64("time::modified"),
                (
                    info.get_attribute_boolean("standard::is-hidden")
                    or info.get_name().startswith(".")
                ),
                directory,
                target,
                (
                    info.get_attribute_uint32("time::modified-usec"),
                    info.get_attribute_string("etag::value"),
                ),
            )
        )
    return rows


def shared_root():
    return next(
        (p for p in (Path("/mnt/chromeos"), Path("/mnt/shared")) if p.is_dir()), None
    )


def places():
    home = Path.home()
    chrome = shared_root()
    result = [("Linux files", home, "home")]
    if chrome:
        for title, subpath, glyph in (
            ("My files", "MyFiles", "folder"),
            ("Downloads", "MyFiles/Downloads", "down"),
            ("Google Drive", "GoogleDrive", "disk"),
            ("Removable media", "removable", "disk"),
        ):
            if (chrome / subpath).is_dir():
                result.append((title, chrome / subpath, glyph))
        result.append(("Shared with Linux", chrome, "workspace"))
    if (
        not any(title == "Downloads" for title, _, _ in result)
        and (home / "Downloads").is_dir()
    ):
        result.append(("Downloads", home / "Downloads", "down"))
    result.append(("File system", Path("/"), "disk"))
    return result


def completion_parts(text, current):
    """Return the parent to enumerate and the unfinished final component."""
    if not text:
        return None, ""
    remote = text.startswith(("\\\\", "//")) or text.lower().startswith("smb://")
    if remote:
        normal = text.replace("\\", "/")
        start = 6 if normal.lower().startswith("smb://") else 2
        if "/" not in normal[start:]:
            return None, ""
    sep = max(
        text.rfind("/"), text.rfind("\\") if remote or not current.is_native() else -1
    )
    if sep < 0:
        return current, text
    prefix, partial = text[: sep + 1], text[sep + 1 :]
    return resolve(prefix, current), partial


def measure(file, cancel, report):
    """Logical file bytes. Symlinks are counted as entries and never followed."""
    info = file.query_info(MEASURE_ATTR, NOFOLLOW, cancel)

    def allocated(meta):
        if meta.has_attribute("standard::allocated-size"):
            return meta.get_attribute_uint64("standard::allocated-size")
        return None

    if info.get_file_type() != DIR or symlink(info):
        return (
            info.get_attribute_uint64("standard::size"),
            1,
            0,
            {},
            [],
            allocated(info),
        )
    total = files = dirs = 0
    breakdown, errors = {}, []
    disk_bytes = 0
    hard_links = set()
    pending = [(file, None)]
    while pending:
        folder, group = pending.pop()
        cancel.set_error_if_cancelled()
        try:
            for entry, item in iter_info(folder, cancel, MEASURE_ATTR):
                is_dir = item.get_file_type() == DIR and not symlink(item)
                key = group if group is not None else (is_dir, item.get_display_name())
                if is_dir:
                    dirs += 1
                    breakdown.setdefault(key, 0)
                    pending.append((entry, key))
                else:
                    size = (
                        item.get_attribute_uint64("standard::size")
                        if item.get_file_type() == FILE
                        else 0
                    )
                    total += size
                    if disk_bytes is not None and item.get_file_type() == FILE:
                        blocks = allocated(item)
                        identity = item.get_attribute_string("id::file")
                        if blocks is None:
                            disk_bytes = None
                        elif not identity or identity not in hard_links:
                            disk_bytes += blocks
                            if (
                                identity
                                and item.get_attribute_uint32("unix::nlink") > 1
                            ):
                                hard_links.add(identity)
                    files += 1
                    breakdown[key] = breakdown.get(key, 0) + size
                report(total, files, dirs)
        except GLib.Error as exc:
            if gio_error(exc, Gio.IOErrorEnum.CANCELLED):
                raise
            errors.append(f"{display(folder)} — {exc.message}")
    return total, files, dirs, breakdown, errors, disk_bytes
