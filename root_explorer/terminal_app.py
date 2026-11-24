#!/usr/bin/python3
"""ROOT's tabbed VTE terminal. Local shells and OpenSSH share the same PTY UI."""
import os
from pathlib import Path
from functools import lru_cache
import pwd
import re
import sys

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Vte", "2.91")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango, Vte
from ssh_sessions import Sessions, profile, ssh_command

try:
    import cairo

    gi.require_foreign("cairo")
except (ImportError, ValueError):
    cairo = None

CSS = b"""
window.root-terminal, .root-body, notebook, notebook > stack {background:#100d16;color:#f5f1ff;}
headerbar.root-titlebar, box.toolbar {background-image:none;background:#181320;border-bottom:1px solid #44364e;}
label.brand {color:#d7c0ff;font: bold 18px monospace;}
label.dim {color:#bcafc8;}
button.root-control {background-image:none;background-color:#241e2e;color:#f5f1ff;border:1px solid #4b3d57;border-radius:5px;text-shadow:none;box-shadow:none;}
button.root-control:hover {background-color:#372a44;}
button.primary {background-color:#c2a6ff;color:#211332;}
vte-terminal {padding:7px 9px;}
box.tab-strip, notebook > header {background:#17121e;border:0;}
notebook > header tab {background:#17121e;color:#bbafc7;padding:5px 8px;border:0;}
notebook > header tab:checked {background:#32273e;color:#e7d8ff;box-shadow:inset 0 -2px #bda0ec;}
button.tab-action {min-width:20px;min-height:22px;padding:0 5px;}
button.close-tab {padding:0 3px;border:0;min-width:16px;min-height:14px;background:transparent;}
entry, treeview, scrolledwindow {background:#15111c;color:#f5f1ff;border-color:#483b54;}
treeview:selected {background:#49355a;color:#fff;}
"""
PALETTE = (
    "#201a28",
    "#e38e9f",
    "#90d9b6",
    "#e4c386",
    "#9faee8",
    "#b69be2",
    "#83c9c5",
    "#d8cfe2",
    "#766a82",
    "#f4a6b6",
    "#a6f0cb",
    "#eed49b",
    "#b6c4ff",
    "#dabbff",
    "#a2e6df",
    "#f5f1ff",
)


def tone(value):
    color = Gdk.RGBA()
    color.parse(value)
    return color


def label(text, style=None):
    out = Gtk.Label(label=text, xalign=0)
    if style:
        out.get_style_context().add_class(style)
    return out


def button(text, action, style=None):
    out = Gtk.Button.new_with_label(text)
    out.get_style_context().add_class("root-control")
    if style:
        out.get_style_context().add_class(style)
    out.connect("clicked", lambda _: action())
    return out


def notice(parent, text, question=False):
    dlg = Gtk.MessageDialog(
        transient_for=parent,
        modal=True,
        message_type=Gtk.MessageType.QUESTION if question else Gtk.MessageType.INFO,
        buttons=Gtk.ButtonsType.NONE,
        text=text,
    )
    if question:
        dlg.add_buttons(
            "Cancel", Gtk.ResponseType.CANCEL, "Continue", Gtk.ResponseType.OK
        )
        dlg.set_default_response(Gtk.ResponseType.CANCEL)
    else:
        dlg.add_button("Close", Gtk.ResponseType.CLOSE)
    result = dlg.run()
    dlg.destroy()
    return result == Gtk.ResponseType.OK


class SessionPicker(Gtk.Stack):
    def __init__(self, window, store):
        super().__init__()
        self.window = window
        self.store = store
        self.get_style_context().add_class("root-body")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin=18)
        self.add_named(box, "list")
        box.pack_start(label("Saved SSH sessions", "brand"), False, False, 0)
        self.model = Gtk.ListStore(str, str, str)
        self.tree = Gtk.TreeView(model=self.model)
        for i, title in enumerate(("Session", "Destination")):
            column = Gtk.TreeViewColumn(title, Gtk.CellRendererText(), text=i + 1)
            column.set_expand(True)
            self.tree.append_column(column)
        scroll = Gtk.ScrolledWindow()
        scroll.add(self.tree)
        box.pack_start(scroll, True, True, 0)
        self.empty = label("Add a server to open an interactive SSH tab.", "dim")
        box.pack_start(self.empty, False, False, 0)
        row = Gtk.Box(spacing=7)
        row.pack_start(button("New session", lambda: self.edit(None)), False, False, 0)
        self.edit_btn = button("Edit", lambda: self.edit(self.selected()))
        self.remove_btn = button("Remove", self.remove_session)
        row.pack_start(self.edit_btn, False, False, 0)
        row.pack_start(self.remove_btn, False, False, 0)
        self.connect_btn = button("Connect", self.connect_selected, "primary")
        row.pack_end(self.connect_btn, False, False, 0)
        row.pack_end(button("Close", self.window.finish_sessions), False, False, 0)
        box.pack_start(row, False, False, 0)
        self.tree.get_selection().connect("changed", lambda *_: self.sync())
        self.tree.connect("row-activated", lambda *_: self.connect_selected())
        self.show_all()
        self.reload()

    def connect_selected(self):
        record = self.selected()
        if record:
            self.window.finish_sessions(record)

    def selected(self):
        m, it = self.tree.get_selection().get_selected()
        if it is not None:
            return next((s for s in self.store.rows if s["id"] == m[it][0]), None)
        return None

    def sync(self):
        selected = self.selected() is not None
        for control in (
            self.edit_btn,
            self.remove_btn,
            self.connect_btn,
        ):
            control.set_sensitive(selected)
        self.empty.set_visible(not self.store.rows)

    def reload(self, select_id=None):
        self.model.clear()
        for s in self.store.rows:
            target = (
                (s["user"] + "@" if s["user"] else "")
                + s["host"]
                + (" · " + s["port"] if s["port"] else "")
            )
            it = self.model.append([s["id"], s["name"], target])
            if s["id"] == select_id:
                self.tree.get_selection().select_iter(it)
        if self.model.get_iter_first() and not self.selected():
            self.tree.get_selection().select_iter(self.model.get_iter_first())
        self.sync()

    def remove_session(self):
        old = self.selected()
        if old and notice(
            self.window, "Remove the saved session “" + old["name"] + "”?", True
        ):
            try:
                self.store.remove(old)
            except OSError as e:
                notice(self.window, str(e))
            self.reload()

    def edit(self, old):
        pane = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin=18)
        pane.pack_start(
            label("Edit SSH session" if old else "New SSH session", "brand"),
            False,
            False,
            0,
        )
        grid = Gtk.Grid(column_spacing=12, row_spacing=10)
        pane.pack_start(grid, False, False, 0)
        fields = {}
        for n, (key, title, hint) in enumerate(
            (
                ("name", "Session name", ""),
                ("host", "Host", "Hostname, IP or SSH config alias"),
                ("user", "Username", "Optional"),
                ("port", "Port", "SSH config or 22"),
                ("key", "OpenSSH private key", "SSH agent or default keys"),
            )
        ):
            grid.attach(label(title), 0, n, 1, 1)
            entry = Gtk.Entry(width_chars=30)
            entry.set_placeholder_text(hint)
            entry.set_text((old or {}).get(key, ""))
            entry.set_hexpand(True)
            entry.connect("activate", lambda *_: save())
            grid.attach(entry, 1, n, 1, 1)
            fields[key] = entry

        def browse():
            chooser = Gtk.FileChooserDialog(
                title="Choose an OpenSSH private key",
                transient_for=self.window,
                action=Gtk.FileChooserAction.OPEN,
            )
            chooser.add_buttons(
                "Cancel", Gtk.ResponseType.CANCEL, "Choose", Gtk.ResponseType.OK
            )
            chooser.set_show_hidden(True)
            if (Path.home() / ".ssh").is_dir():
                chooser.set_current_folder(str(Path.home() / ".ssh"))
            if chooser.run() == Gtk.ResponseType.OK:
                fields["key"].set_text(chooser.get_filename())
            chooser.destroy()

        grid.attach(button("Browse…", browse), 2, 4, 1, 1)
        err = label("", "dim")
        grid.attach(err, 0, 5, 3, 1)

        def back():
            self.set_visible_child_name("list")
            self.remove_page(pane)

        def save():
            try:
                item = profile(
                    **{k: e.get_text() for k, e in fields.items()},
                    identity=old["id"] if old else None,
                )
                self.store.put(item)
            except (ValueError, OSError) as exc:
                err.set_text(str(exc))
                return
            back()
            self.reload(item["id"])

        actions = Gtk.Box(spacing=8)
        actions.pack_end(button("Save session", save, "primary"), False, False, 0)
        actions.pack_end(button("Cancel", back), False, False, 0)
        pane.pack_start(actions, False, False, 0)
        self.add_named(pane, "edit")
        pane.show_all()
        self.set_visible_child_name("edit")
        fields["name"].grab_focus()

    def remove_page(self, pane):
        Gtk.Container.remove(self, pane)
        pane.destroy()


@lru_cache(maxsize=1)
def watermark():
    if cairo is not None:
        try:
            return cairo.ImageSurface.create_from_png(
                str(Path(__file__).with_name("sd-mark.png"))
            )
        except (OSError, cairo.Error):
            pass
    return None


def paint_background(term, cr):
    cr.set_source_rgb(16 / 255, 13 / 255, 22 / 255)
    cr.paint()
    width, height = term.get_allocated_width(), term.get_allocated_height()
    art = watermark()
    scale = min(220, width * 0.42, height * 0.5) / art.get_width()
    cr.save()
    cr.translate(
        width - art.get_width() * scale - 12, height - art.get_height() * scale - 12
    )
    cr.scale(scale, scale)
    cr.set_source_surface(art, 0, 0)
    cr.paint_with_alpha(0.09)
    cr.restore()
    return False


class Tab(Gtk.Box):
    def __init__(self, window, directory, session=None, admin=False):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.window = window
        self.directory = directory
        self.session = session
        self.admin = admin
        self.pid = None
        self.closed = False
        self.spawn_cancel = Gio.Cancellable()
        self.term = Vte.Terminal()
        self.term.set_colors(
            tone("#f5f1ff"), tone("#100d16"), [tone(v) for v in PALETTE]
        )
        if hasattr(self.term, "set_clear_background") and watermark() is not None:
            self.term.set_clear_background(False)
            self.term.connect("draw", paint_background)
        self.term.set_color_cursor(tone("#98e1cd"))
        self.term.set_color_highlight(tone("#523866"))
        self.term.set_font(Pango.FontDescription("monospace 11"))
        self.term.set_scrollback_lines(12000)
        self.term.set_scroll_on_keystroke(True)
        self.term.set_scroll_on_output(False)
        self.term.set_mouse_autohide(True)
        self.term.set_audible_bell(False)
        area = Gtk.Box()
        area.pack_start(self.term, True, True, 0)
        scrollbar = Gtk.Scrollbar(
            orientation=Gtk.Orientation.VERTICAL, adjustment=self.term.get_vadjustment()
        )
        area.pack_start(scrollbar, False, False, 0)
        self.content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.content.pack_start(area, True, True, 0)
        self.status = Gtk.Box(spacing=8, margin=5)
        self.status.set_no_show_all(True)
        self.message = label("", "dim")
        self.status.pack_start(self.message, True, True, 0)
        self.status.pack_end(
            button("Reconnect" if session else "Restart shell", self.spawn),
            False,
            False,
            0,
        )
        self.content.pack_end(self.status, False, False, 0)
        self.term.connect("child-exited", self.exited)
        self.header = Gtk.Box(spacing=5)
        self.tab_name = label(
            "Administrator"
            if admin
            else session["name"] if session else (Path(directory).name or "/")
        )
        self.tab_name.set_width_chars(min(22, max(4, len(self.tab_name.get_text()))))
        self.tab_name.set_max_width_chars(22)
        self.tab_name.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        self.header.pack_start(self.tab_name, False, False, 0)
        self.header.pack_start(
            button("×", lambda: window.close_tab(self), "close-tab"), False, False, 0
        )
        self.header.set_tooltip_text(session["host"] if session else directory)
        if not session and not admin:
            self.term.connect("window-title-changed", self.title_changed)
        self.header.show_all()

    def title_changed(self, *_):
        title = self.term.get_window_title()
        if title:
            self.tab_name.set_text(title[:160])
            self.tab_name.set_width_chars(min(22, max(4, len(title))))
            self.window.book.measure_tabs()

    def current_directory(self):
        if self.pid and not self.session:
            try:
                path = os.readlink(f"/proc/{self.pid}/cwd")
                if os.path.isdir(path):
                    return path
            except OSError:
                pass
        return self.directory

    def has_running_command(self):
        if self.pid is None:
            return False
        if self.session or self.admin:
            return True
        try:
            return os.tcgetpgrp(self.term.get_pty().get_fd()) != self.pid
        except (OSError, AttributeError):
            return False

    def report(self, text):
        self.message.set_text(text)
        self.status.set_no_show_all(False)
        self.status.show_all()

    def spawn(self):
        self.status.hide()
        self.status.set_no_show_all(True)
        try:
            if self.session:
                argv = ssh_command(self.session)
            elif self.admin:
                argv = ["/usr/bin/sudo", "-H", "-s"]
            else:
                shell = (
                    os.environ.get("SHELL")
                    or pwd.getpwuid(os.getuid()).pw_shell
                    or "/bin/sh"
                )
                argv = (
                    [shell]
                    if os.path.isabs(shell) and os.access(shell, os.X_OK)
                    else ["/bin/sh"]
                )
            self.term.set_input_enabled(True)
            self.term.spawn_async(
                Vte.PtyFlags.DEFAULT,
                self.directory,
                argv,
                None,
                GLib.SpawnFlags.DEFAULT,
                None,
                None,
                -1,
                self.spawn_cancel,
                self.spawned,
                None,
            )
        except (OSError, ValueError, GLib.Error) as e:
            self.report(str(e))

    def spawned(self, term, pid, error, *_):
        if self.closed:
            return
        if error:
            if self.directory != str(Path.home()) and (
                error.matches(GLib.spawn_error_quark(), GLib.SpawnError.CHDIR)
                or not os.path.isdir(self.directory)
                or not os.access(self.directory, os.X_OK)
            ):
                self.directory = str(Path.home())
                if not self.admin and not self.session:
                    self.tab_name.set_text(Path.home().name)
                self.spawn()
            else:
                self.report(error.message)
        else:
            self.pid = pid

    def exited(self, _, status):
        self.pid = None
        if self.closed:
            return
        if not self.session and status == 0:
            GLib.idle_add(self.window.discard, self)
            return
        self.term.set_input_enabled(False)
        code = os.waitstatus_to_exitcode(status)
        self.report(f"Session closed · exit status {code}")

    def shutdown(self):
        self.closed = True
        self.spawn_cancel.cancel()


class TabBook(Gtk.Notebook):
    all_tabs_width = 0

    def measure_tabs(self):
        # GTK's scrollable notebook normally requests only one tab and two arrows.
        # Measure the complete strip with GTK's actual tab theme and font first.
        self.set_scrollable(False)
        self.all_tabs_width = Gtk.Notebook.do_get_preferred_width(self)[1] + 6
        self.set_scrollable(True)
        self.queue_resize()

    def do_get_preferred_width(self):
        minimum, natural = Gtk.Notebook.do_get_preferred_width(self)
        return minimum, max(natural, self.all_tabs_width)


class TerminalWindow(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="ROOT Terminal")
        self.set_default_size(940, 595)
        self.picker = None
        self.set_icon_name("uk.systemadmin.RootExplorer")
        main = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.add(main)
        self.get_style_context().add_class("root-terminal")
        top = Gtk.HeaderBar(show_close_button=False, has_subtitle=False)
        top.get_style_context().add_class("root-titlebar")
        self.set_titlebar(top)
        top.pack_start(label("ROOT / TERMINAL", "brand"))

        def title_control(symbol, name, action):
            control = button(symbol, action)
            control.set_tooltip_text(name)
            control.get_accessible().set_name(name)
            top.pack_end(control)

        title_control("×", "Close window", self.close)
        title_control("□", "Maximise or restore window", self.toggle_maximise)
        title_control("−", "Minimise window", self.iconify)
        self.ssh_button = button("SSH sessions", self.sessions)
        top.pack_end(self.ssh_button)
        self.pages = Gtk.Stack()
        main.pack_start(self.pages, True, True, 0)
        self.terminal_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.pages.add_named(self.terminal_page, "tabs")
        strip = Gtk.Box(spacing=5)
        strip.get_style_context().add_class("tab-strip")
        self.terminal_page.pack_start(strip, False, False, 0)
        # Keep GTK's draggable, scrollable tabs. Their terminal panes fill the stack below.
        self.book = TabBook(scrollable=True, show_border=False)
        strip.pack_start(self.book, False, True, 0)
        self.shells = Gtk.Stack()
        self.terminal_page.pack_start(self.shells, True, True, 0)
        self.tab_actions = Gtk.Box(spacing=2)
        self.plus = button("+", self.new_local, "tab-action")
        self.plus.set_tooltip_text("New tab (Ctrl+Shift+T)")
        self.plus.get_accessible().set_name("New tab")
        self.tab_actions.pack_start(self.plus, False, False, 0)
        self.options = Gtk.MenuButton(label="▾")
        self.options.get_style_context().add_class("root-control")
        self.options.get_style_context().add_class("tab-action")
        self.options.set_tooltip_text("New window and administrator tab")
        self.options.get_accessible().set_name("Terminal options")
        menu = Gtk.Menu()
        for text, fn in (
            ("New window", self.new_window),
            ("Administrator tab", self.new_admin),
        ):
            entry = Gtk.MenuItem(label=text)
            entry.connect("activate", lambda _, action=fn: action())
            menu.append(entry)
        menu.show_all()
        self.options.set_popup(menu)
        self.tab_actions.pack_start(self.options, False, False, 0)
        self.tab_actions.set_valign(Gtk.Align.CENTER)
        strip.pack_start(self.tab_actions, False, False, 0)
        self.book.connect_after("switch-page", self.switch_tab)
        self.connect("key-press-event", self.key)
        self.connect("delete-event", self.closing)
        self.show_all()

    def toggle_maximise(self):
        if self.is_maximized():
            self.unmaximize()
        else:
            self.maximize()

    def switch_tab(self, book, page, num):
        self.shells.set_visible_child(page.content)
        GLib.idle_add(self.focus_tab, page)

    def new_window(self):
        tab = self.active()
        window = TerminalWindow(self.get_application())
        window.add_tab(tab.current_directory() if tab else str(Path.home()))
        window.present()

    def new_admin(self):
        tab = self.active()
        self.add_tab(tab.current_directory() if tab else str(Path.home()), admin=True)

    def active(self):
        return self.book.get_nth_page(self.book.get_current_page())

    def focus_tab(self, tab):
        if tab and not tab.closed and self.active() is tab:
            tab.term.grab_focus()
        return False

    def add_tab(self, directory, session=None, admin=False):
        self.pages.set_visible_child_name("tabs")
        if self.picker:
            self.ssh_button.set_sensitive(True)
        tab = Tab(self, directory, session, admin)
        self.shells.add(tab.content)
        tab.content.show_all()
        index = self.book.append_page(tab, tab.header)
        self.book.set_tab_reorderable(tab, True)
        tab.show_all()
        self.book.measure_tabs()
        self.book.set_current_page(index)
        tab.spawn()
        tab.term.grab_focus()
        return tab

    def new_local(self):
        tab = self.active()
        self.add_tab(tab.current_directory() if tab else str(Path.home()))

    def sessions(self):
        if self.picker is not None:
            self.pages.set_visible_child(self.picker)
            self.ssh_button.set_sensitive(False)
            return
        try:
            store = Sessions()
        except (ValueError, OSError) as exc:
            notice(self, f"Could not open saved sessions. {exc}")
            return
        self.picker = SessionPicker(self, store)
        self.pages.add_named(self.picker, "sessions")
        self.pages.set_visible_child(self.picker)
        self.ssh_button.set_sensitive(False)

    def finish_sessions(self, session=None):
        picker = self.picker
        self.picker = None
        self.pages.set_visible_child(self.terminal_page)
        if picker:
            self.pages.remove(picker)
            picker.destroy()
        self.ssh_button.set_sensitive(True)
        if session:
            self.add_tab(str(Path.home()), session)
        elif self.active():
            self.focus_tab(self.active())
        else:
            self.destroy()

    def discard(self, tab):
        if tab.closed:
            return False
        tab.shutdown()
        pos = self.book.page_num(tab)
        if pos >= 0:
            self.book.remove_page(pos)
            self.book.measure_tabs()
        tab.content.destroy()
        tab.destroy()
        if not self.picker and not self.book.get_n_pages():
            self.destroy()
        return False

    def close_tab(self, tab):
        if tab.has_running_command() and not notice(
            self, "Close this running terminal session?", True
        ):
            return
        self.discard(tab)

    def closing(self, *_):
        tabs = self.book.get_children()
        if any(t.has_running_command() for t in tabs) and not notice(
            self, "Close this window and its running terminal sessions?", True
        ):
            return True
        for tab in tabs:
            tab.shutdown()
        return False

    def key(self, _, event):
        state = event.state & Gtk.accelerator_get_default_mod_mask()
        key = Gdk.keyval_to_lower(event.keyval)
        ctrl, shift = Gdk.ModifierType.CONTROL_MASK, Gdk.ModifierType.SHIFT_MASK
        tab = self.active()
        if self.picker is not None and self.pages.get_visible_child() is self.picker:
            if key == Gdk.KEY_Escape:
                if self.picker.get_visible_child_name() == "edit":
                    pane = self.picker.get_visible_child()
                    self.picker.set_visible_child_name("list")
                    self.picker.remove_page(pane)
                else:
                    self.finish_sessions()
                return True
            return False
        if not tab:
            return False
        if state == ctrl | shift:
            if key == Gdk.KEY_t:
                self.new_local()
            elif key == Gdk.KEY_n:
                self.new_window()
            elif key == Gdk.KEY_w:
                self.close_tab(tab)
            elif key == Gdk.KEY_c:
                tab.term.copy_clipboard_format(Vte.Format.TEXT)
            elif key == Gdk.KEY_v:
                tab.term.paste_clipboard()
            else:
                return False
            return True
        if state == ctrl and key in (Gdk.KEY_Page_Up, Gdk.KEY_Page_Down):
            offset = 1 if key == Gdk.KEY_Page_Down else -1
            self.book.set_current_page(
                (self.book.get_current_page() + offset) % self.book.get_n_pages()
            )
            return True
        return False


def launch_directory(path):
    # Read the kernel mount table without issuing a stat request to an offline NAS.
    if not os.path.isabs(path):
        return str(Path.home())
    target = os.path.normpath(path)
    deepest, kind = 0, ""
    try:
        with open(
            "/proc/self/mountinfo", encoding="utf-8", errors="surrogateescape"
        ) as mounts:
            for line in mounts:
                fields, _, fs = line.partition(" - ")
                mount = re.sub(
                    r"\\([0-7]{3})", lambda m: chr(int(m[1], 8)), fields.split()[4]
                )
                if len(mount) >= deepest and (
                    mount == target or target.startswith(mount.rstrip("/") + "/")
                ):
                    deepest, kind = len(mount), fs.split()[0]
    except OSError:
        pass
    if kind in {
        "cifs",
        "smb3",
        "smbfs",
        "nfs",
        "nfs4",
        "fuse.sshfs",
        "fuse.gvfsd-fuse",
    }:
        return str(Path.home())
    return target


class TerminalApp(Gtk.Application):
    def __init__(self):
        super().__init__(
            application_id="uk.systemadmin.RootExplorer.Terminal",
            flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE,
        )

    def do_startup(self):
        Gtk.Application.do_startup(self)
        GLib.set_application_name("ROOT Terminal")
        Gtk.Settings.get_default().set_property(
            "gtk-application-prefer-dark-theme", True
        )
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    def do_command_line(self, line):
        args = line.get_arguments()[1:]
        win = self.get_active_window()
        launched = args[-1:] == ["--from-explorer"]
        if launched:
            args.pop()
        try:
            win = win or TerminalWindow(self)
            if args == ["sessions"]:
                win.sessions()
                if win.picker is None and not win.book.get_n_pages():
                    win.destroy()
                    return 1
            else:
                mode, folder = (
                    args
                    if len(args) == 2 and args[0] in ("folder", "admin")
                    else ("folder", str(Path.home()))
                )
                win.add_tab(launch_directory(folder), admin=mode == "admin")
            win.deiconify()
            win.present()
            if launched and not line.get_is_remote():
                print("ROOT_WINDOW_READY", flush=True)
        except Exception as exc:
            reason = f"Terminal startup failed. {exc}\n"
            if hasattr(line, "printerr_literal"):
                line.printerr_literal(reason)
            else:
                print(reason, file=sys.stderr, flush=True)
            if win and not win.book.get_n_pages():
                win.destroy()
            return 1
        return 0


if __name__ == "__main__":
    sys.exit(TerminalApp().run(sys.argv))
