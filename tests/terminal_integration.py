"""Exercise ROOT's real VTE, D-Bus tab handoff and optionally a prepared SSH fixture.

Run under a graphical Debian session (or Xvfb) in an isolated test account.
ROOT_TEST_SSH_PORT, ROOT_TEST_SSH_USER and ROOT_TEST_SSH_KEY can name a local
SSH fixture that shares the test's temporary folder. Provision its known host first.
"""

import os
from pathlib import Path
import shlex
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
import threading

source = Path(__file__).resolve().parents[1] / "root_explorer"
if source.is_dir():
    sys.path.insert(0, str(source))
from terminal_app import GLib, Gtk, TerminalApp, TerminalWindow
from ssh_sessions import Sessions, profile


def spin(condition, message):
    deadline = time.monotonic() + 15
    context = GLib.MainContext.default()
    while not condition() and time.monotonic() < deadline:
        while context.pending():
            context.iteration(False)
        time.sleep(0.01)
    assert condition(), message


def command(tab, text):
    tab.term.feed_child((text + "\n").encode())


def via_explorer(directory):
    from PyQt5.QtWidgets import QApplication, QPushButton
    from PyQt5.QtCore import QTimer
    from files import resolve
    from main import configure
    from service import Service

    app = QApplication(["ROOT"])
    configure(app)
    svc = Service()
    window = svc.window(resolve(directory))

    release_workers = threading.Event()

    def ready():
        if window.loading:
            QTimer.singleShot(25, ready)
        else:
            # A terminal click must not queue behind unresponsive filesystem requests.
            for _ in range(6):
                svc.workers.start(lambda: release_workers.wait(10), lambda *_: None)
            next(
                b for b in window.findChildren(QPushButton) if b.text() == "Terminal"
            ).click()
            QTimer.singleShot(1200, window.close)

    QTimer.singleShot(0, ready)
    QTimer.singleShot(8000, app.quit)
    try:
        return app.exec_()
    finally:
        release_workers.set()


def run():
    with TemporaryDirectory(prefix="ROOT working folder '") as directory:
        os.environ.update(
            XDG_CONFIG_HOME=str(Path(directory) / "config"), SHELL="/bin/sh"
        )
        here = Path(directory)
        sub = here / "sub folder"
        sub.mkdir()
        app = TerminalApp()
        assert (
            app.register(None) and not app.get_is_remote()
        ), "Use an isolated session bus"
        win = TerminalWindow(app)
        tab = win.add_tab(directory)
        spin(lambda: tab.pid, "Local shell did not start: " + tab.message.get_text())
        assert not win.plus.is_ancestor(tab.header)
        assert win.tab_actions.get_parent() is win.book.get_parent()
        assert win.shells.get_visible_child() == tab.content
        command(tab, "pwd > first")
        spin(lambda: (here / "first").exists(), "Shell did not run")
        assert (here / "first").read_text().strip() == directory
        command(tab, "cd 'sub folder'")
        spin(
            lambda: tab.current_directory() == str(sub),
            "cd did not update shell's working directory",
        )
        win.plus.clicked()
        second = win.active()
        spin(lambda: second.pid, "Second tab did not start")
        assert tab.header.get_mapped() and second.header.get_mapped()
        assert win.shells.get_visible_child() == second.content
        win.book.set_current_page(0)
        assert win.shells.get_visible_child() == tab.content
        win.book.set_current_page(1)
        command(second, "pwd > second")
        spin(
            lambda: (sub / "second").exists(),
            "Second tab working directory was incorrect",
        )
        command(second, "sleep 10")
        spin(second.has_running_command, "Shell did not enter foreground process")
        second.term.feed_child(b"\x03")
        command(second, "echo resumed > resumed")
        spin(
            lambda: (sub / "resumed").exists(),
            "Ctrl+C did not interrupt foreground process",
        )
        path = Path(sys.modules["terminal_app"].__file__)
        proc = subprocess.Popen([sys.executable, str(path), "folder", directory])
        spin(
            lambda: win.book.get_n_pages() == 3 and proc.poll() is not None,
            "Existing terminal did not accept second invocation",
        )
        assert proc.returncode == 0 and win.active().directory == directory
        command(second, "exit")
        spin(lambda: win.book.get_n_pages() == 2, "Exited local shell left stale tab")
        print(
            "PASS local PTY, folder with spaces and quote, tab after cd, Ctrl+C, D-Bus and shell exit",
            flush=True,
        )
        explorer = subprocess.Popen(
            [sys.executable, __file__, "--via-explorer", str(sub)]
        )
        spin(
            lambda: win.book.get_n_pages() == 3 and explorer.poll() is not None,
            "Explorer toolbar did not open a terminal tab",
        )
        assert explorer.returncode == 0 and win.active().directory == str(sub)
        print("PASS file explorer Terminal button opens current folder", flush=True)
        port = os.environ.get("ROOT_TEST_SSH_PORT")
        if port:
            record = profile(
                "Local fixture",
                "127.0.0.1",
                os.environ["ROOT_TEST_SSH_USER"],
                port,
                os.environ["ROOT_TEST_SSH_KEY"],
            )
            Sessions().put(record)

            win.sessions()
            assert win.pages.get_visible_child() is win.picker
            assert win.picker.selected() == record
            win.picker.edit(record)
            assert win.picker.get_visible_child_name() == "edit"
            assert len([w for w in Gtk.Window.list_toplevels() if w.get_visible()]) == 1
            pane = win.picker.get_visible_child()

            def find_buttons(widget):
                if isinstance(widget, Gtk.Button):
                    yield widget
                if isinstance(widget, Gtk.Container):
                    for child in widget.get_children():
                        yield from find_buttons(child)

            next(
                b for b in find_buttons(pane) if b.get_label() == "Save session"
            ).clicked()
            assert win.picker.get_visible_child_name() == "list"
            win.picker.connect_btn.clicked()
            ssh = win.active()
            assert ssh.session == record
            spin(lambda: ssh.pid, "OpenSSH did not start")
            proof = here / "remote"

            # Wait for authentication before sending commands, including on reconnect.
            def verified_shell():
                end = time.monotonic() + 15
                while not proof.exists() and time.monotonic() < end:
                    command(
                        ssh,
                        '[ -n "$SSH_CONNECTION" ] && echo remote > '
                        + shlex.quote(str(proof)),
                    )
                    short = time.monotonic() + 0.5
                    ctx = GLib.MainContext.default()
                    while time.monotonic() < short:
                        while ctx.pending():
                            ctx.iteration(False)
                        time.sleep(0.01)
                    if ssh.status.get_visible():
                        break
                assert proof.exists(), (
                    "Authenticated session failed: " + ssh.message.get_text()
                )

            verified_shell()
            proof.unlink()
            command(ssh, "exit")
            spin(lambda: ssh.status.get_visible(), "Reconnect control missing")
            ssh.spawn()
            spin(lambda: ssh.pid, "OpenSSH did not restart")
            verified_shell()
            print(
                "PASS saved OpenSSH profile, authenticated remote shell and reconnect",
                flush=True,
            )
        win.new_window()
        other = next(w for w in app.get_windows() if w is not win)
        spin(lambda: other.active().pid, "Separate terminal window failed")
        other.discard(other.active())
        if os.getenv("ROOT_TEST_ADMIN_TAB"):
            admin_launch = subprocess.Popen(
                [sys.executable, str(path), "admin", directory]
            )
            spin(
                lambda: win.active().admin and admin_launch.poll() is not None,
                "Root terminal request did not open an administrator tab",
            )
            admin = win.active()
            assert admin.directory == directory and admin_launch.returncode == 0
            spin(lambda: admin.pid, "sudo did not start")
            proof = here / "admin-id"
            command(admin, "id -u > " + shlex.quote(str(proof)))
            spin(
                lambda: admin.status.get_visible()
                or (proof.exists() and proof.read_text().strip() == "0"),
                "Administrator shell did not respond",
            )
            assert (
                proof.exists() and proof.read_text().strip() == "0"
            ), admin.message.get_text()

            assert admin.tab_name.get_text() == "Administrator"
            command(admin, "exit")
            spin(lambda: admin.closed, "Administrator tab did not close")
            print("PASS labelled administrator tab with sudo", flush=True)
        for page in list(win.book.get_children()):
            win.discard(page)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--via-explorer":
        sys.exit(via_explorer(sys.argv[2]))
    run()
