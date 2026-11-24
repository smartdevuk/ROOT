"""Saved interactive SSH destinations. Authentication stays with OpenSSH."""

import json
import os
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
import uuid


def profile(name, host, user="", port="", key="", identity=None):
    host, user, port, key = (s.strip() for s in (host, user, str(port), key))
    if "@" in host and not user:
        user, host = host.split("@", 1)
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    if not re.fullmatch(r"[a-zA-Z0-9_:][a-zA-Z0-9_.:%-]*", host):
        raise ValueError("Enter a hostname, IP address or OpenSSH host alias.")
    if user and not re.fullmatch(r"[\w.][\w.@\\-]*", user):
        raise ValueError("Enter a valid SSH username.")
    if port and (
        not port.isascii() or not port.isdecimal() or not 1 <= int(port) <= 65535
    ):
        raise ValueError("Enter a port between 1 and 65535, or leave it blank.")
    if key:
        try:
            path = Path(key).expanduser().absolute()
        except RuntimeError as error:
            raise ValueError("Choose an existing OpenSSH private key file.") from error
        if not path.is_file():
            raise ValueError("Choose an existing OpenSSH private key file.")
        key = str(path)
    return dict(
        id=identity or uuid.uuid4().hex,
        name=name.strip() or host,
        host=host,
        user=user,
        port=port,
        key=key,
    )


def ssh_command(session):
    # Validate saved records on use as well. Pass only individual argv elements.
    s = profile(
        **{k: session.get(k, "") for k in ("name", "host", "user", "port", "key")}
    )
    args = ["/usr/bin/ssh", "-t"]
    if s["port"]:
        args.extend(["-p", s["port"]])
    if s["user"]:
        args.extend(["-l", s["user"]])
    if s["key"]:
        args.extend(["-i", str(Path(s["key"]).expanduser())])
    return args + ["--", s["host"]]


class Sessions:
    def __init__(self):
        self.path = (
            Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
            / "root-explorer"
            / "ssh-sessions.json"
        )
        try:
            self.rows = json.loads(self.path.read_text())
            if not isinstance(self.rows, list) or not all(
                isinstance(r, dict)
                and all(
                    isinstance(r.get(k), str)
                    for k in ("id", "name", "host", "port", "user", "key")
                )
                for r in self.rows
            ):
                raise ValueError("Invalid saved session list")
        except FileNotFoundError:
            self.rows = []

    def save(self, rows):
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with NamedTemporaryFile(
            "w", encoding="utf-8", dir=self.path.parent, delete=False
        ) as out:
            temp = Path(out.name)
            try:
                json.dump(rows, out, ensure_ascii=True, indent=2)
                out.flush()
                os.fsync(out.fileno())
                os.replace(temp, self.path)
            finally:
                temp.unlink(missing_ok=True)
        self.rows = rows

    def put(self, entry):
        self.save(
            sorted(
                [r for r in self.rows if r["id"] != entry["id"]] + [entry],
                key=lambda r: r["name"].casefold(),
            )
        )

    def remove(self, entry):
        self.save([r for r in self.rows if r["id"] != entry["id"]])
