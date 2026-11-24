"""Run the native browser offscreen against real temporary files."""

import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
from unittest import TestCase, main
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "root_explorer"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QLineEdit, QPushButton
from files import resolve, display
from main import configure, Instance
from service import Service, State
from views import CopyHistory


class Desktop(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = TemporaryDirectory()
        cls.base = Path(cls.temp.name)
        os.environ["XDG_CONFIG_HOME"] = str(cls.base / "config")
        os.environ["XDG_RUNTIME_DIR"] = str(cls.base)
        cls.app = QApplication.instance() or QApplication(["ROOT"])
        configure(cls.app)
        cls.app.setQuitOnLastWindowClosed(False)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        self.case = TemporaryDirectory(dir=self.base)
        self.addCleanup(self.case.cleanup)
        self.root = Path(self.case.name)
        self.a, self.b = self.root / "Archive", self.root / "Exports"
        self.a.mkdir()
        self.b.mkdir()
        (self.a / "Episodes").mkdir()
        (self.a / "notes.txt").write_text("ROOT clipboard test")
        self.s = Service()
        self.w = self.new(self.a)

    def tearDown(self):
        for win in self.s.windows[:]:
            win.close()
        self.app.processEvents()

    def spin(self, predicate, seconds=5):
        end = time.monotonic() + seconds
        while not predicate() and time.monotonic() < end:
            self.app.processEvents()
            QTest.qWait(6)
        self.assertTrue(predicate(), "Desktop operation timed out")

    def new(self, path):
        w = self.s.window(resolve(str(path)))
        self.spin(lambda: not w.loading)
        return w

    def select(self, w, name):
        w.raise_()
        w.activateWindow()
        w.table.setFocus()
        QTest.qWait(20)
        self.app.processEvents()
        for i in range(w.table.proxy.rowCount()):
            idx = w.table.proxy.index(i, 0)
            if idx.data() == name:
                QTest.mouseClick(
                    w.table.viewport(),
                    Qt.LeftButton,
                    pos=w.table.visualRect(idx).center(),
                )
                self.assertEqual([r.name for r in w.table.selected()], [name])
                return
        self.fail("Missing file: " + name)

    def test_clipboard_copy_cut_paste_graph_history(self):
        b = self.new(self.b)
        mirror = self.new(self.a)
        self.select(self.w, "notes.txt")
        uri = resolve(str(self.a / "notes.txt")).get_uri()
        self.w.clip(True)
        self.assertEqual(mirror.table.cut_uris, {uri})
        QApplication.clipboard().setText("another clipboard owner")
        self.assertFalse(self.w.table.cut_uris)
        self.assertFalse(mirror.table.cut_uris)
        self.select(self.w, "notes.txt")
        QTest.keyClick(self.w.table, Qt.Key_C, Qt.ControlModifier)
        b.activateWindow()
        b.table.setFocus()
        self.app.processEvents()
        QTest.keyClick(b.table, Qt.Key_V, Qt.ControlModifier)
        self.spin(
            lambda: len(self.s.store.data["history"]) >= 1
            and not self.s.timer.isActive()
        )
        self.assertEqual((self.b / "notes.txt").read_text(), "ROOT clipboard test")
        self.assertTrue((self.a / "notes.txt").exists())
        self.assertTrue(b.table.isVisible())
        self.assertFalse(b.transfer.monitor.isVisible())
        self.assertIn("Complete", b.transfer.summary.text())
        self.assertFalse(
            any(btn.text() == "Transfers" for btn in b.findChildren(QPushButton))
        )
        self.assertEqual(self.s.series.total, len(b"ROOT clipboard test"))
        self.assertEqual(next(iter(self.s.job.rows.values()))[3], "done")
        self.select(self.w, "notes.txt")
        QTest.keyClick(self.w.table, Qt.Key_X, Qt.ControlModifier)
        b.activateWindow()
        b.table.setFocus()
        self.app.processEvents()
        QTest.keyClick(b.table, Qt.Key_V, Qt.ControlModifier)
        self.spin(
            lambda: not (self.a / "notes.txt").exists() and not self.s.timer.isActive()
        )
        self.assertFalse(mirror.table.cut_uris)
        self.assertTrue((self.b / "notes (copy).txt").exists())
        history = CopyHistory(b)
        key = history.list.currentItem().data(0, Qt.UserRole)["id"]
        history.forget()
        history.close()
        self.assertNotIn(key, [r["id"] for r in self.s.store.data["history"]])
        self.assertTrue((self.b / "notes (copy).txt").exists())

    def test_navigation_properties_shortcuts_and_completion(self):
        self.select(self.w, "Episodes")
        QTest.keyClick(self.w.table, Qt.Key_Return)
        self.spin(
            lambda: not self.w.loading
            and self.w.current.get_path() == str(self.a / "Episodes")
        )
        QTest.keyClick(self.w.table, Qt.Key_Left, Qt.AltModifier)
        self.spin(
            lambda: not self.w.loading and self.w.current.get_path() == str(self.a)
        )
        QTest.keyClick(self.w.table, Qt.Key_Right, Qt.AltModifier)
        self.spin(
            lambda: not self.w.loading
            and self.w.current.get_path() == str(self.a / "Episodes")
        )
        QTest.keyClick(self.w.table, Qt.Key_N, Qt.ControlModifier)
        self.spin(lambda: len(self.s.windows) == 2 and not self.s.windows[-1].loading)
        other = self.s.windows[-1]
        self.assertEqual(other.current.get_path(), str(self.a / "Episodes"))
        self.w.activateWindow()
        self.w.edit_address()
        self.app.processEvents()
        self.w.address.setText(str(self.a / "Ep"))
        self.w.suggest(self.w.address.text())
        self.spin(lambda: self.w.hints.count() > 0)
        self.assertEqual(self.w.hints.item(0).text(), str(self.a / "Episodes"))
        QTest.keyClick(self.w.address, Qt.Key_Tab)
        self.assertEqual(self.w.address.text(), str(self.a / "Episodes") + "/")
        self.w.go(resolve(str(self.a)))
        self.spin(lambda: not self.w.loading)
        self.select(self.w, "notes.txt")

        QTest.keyClick(self.w.table, Qt.Key_Return, Qt.ControlModifier)
        editor = self.w.table.findChild(QLineEdit)
        self.assertIsNotNone(editor)
        self.assertEqual(editor.selectedText(), "notes")
        QTest.keyClicks(editor, "renamed")
        QTest.keyClick(editor, Qt.Key_Return)
        self.spin(lambda: (self.a / "renamed.txt").exists() and not self.w.loading)
        p = self.w.properties(resolve(str(self.a)))
        self.spin(lambda: p.tree.topLevelItemCount() == 2)
        self.assertIn("19 bytes", p.detail.text())
        p.close()
        self.assertEqual(State().data["recents"][0], resolve(str(self.a)).get_uri())

    def test_inline_rename_selection_and_cancel(self):
        for name, stem, is_folder in (
            ("holiday.photo.jpg", "holiday.photo", False),
            ("folder.with.dots", "folder.with.dots", True),
            ("README", "README", False),
            ("camera-📷.jpeg", "camera-📷", False),
        ):
            target = self.a / name
            target.mkdir() if is_folder else target.touch()
            self.w.refresh()
            self.spin(lambda: not self.w.loading)
            self.select(self.w, name)
            QTest.keyClick(self.w.table, Qt.Key_F2)
            index = self.w.table.currentIndex()
            edit = self.w.table.indexWidget(index)
            self.assertEqual(edit.selectedText(), stem)
            QTest.keyClicks(edit, "abandoned")
            QTest.keyClick(edit, Qt.Key_Escape)
            self.assertTrue(target.exists())
            self.assertEqual(index.data(), name)
        (self.a / "other.txt").write_text("original")
        self.w.refresh()
        self.spin(lambda: not self.w.loading)
        self.select(self.w, "notes.txt")
        QTest.keyClick(self.w.table, Qt.Key_F2)
        edit = self.w.table.indexWidget(self.w.table.currentIndex())
        QTest.keyClicks(edit, "other")
        QTest.keyClick(edit, Qt.Key_Return)
        self.spin(lambda: self.w.notice.isVisible() and not self.w.loading)
        self.assertEqual((self.a / "other.txt").read_text(), "original")
        self.assertTrue((self.a / "notes.txt").exists())

    def test_clickable_path_sidebar_capacity_and_terminal_button(self):
        from gi.repository import Gio

        self.s.pin(resolve(str(self.a)))
        node = self.w.tree.roots["PINNED", resolve(str(self.a)).get_uri()]
        node.setExpanded(True)
        self.spin(lambda: node.childCount() == 1)
        item = node.child(0)
        self.assertEqual(item.text(0), "Episodes")
        self.assertEqual(item.childCount(), 0)
        self.w.tree.open_item(item)
        self.spin(lambda: self.w.current.get_path() == str(self.a / "Episodes"))
        parent_btn = next(
            btn
            for file, btn, _ in self.w.crumbs.items
            if file.get_path() == str(self.a)
        )
        self.assertEqual(parent_btn.text(), self.a.name)
        QTest.mouseClick(parent_btn, Qt.LeftButton)
        self.spin(
            lambda: self.w.current.get_path() == str(self.a) and not self.w.loading
        )
        with patch.object(self.w, "launch_terminal") as launcher:
            btn = next(
                btn
                for btn in self.w.findChildren(QPushButton)
                if btn.text() == "Terminal"
            )
            QTest.mouseClick(btn, Qt.LeftButton)
            self.spin(lambda: launcher.called)
            launcher.assert_called_once_with(["folder", str(self.a)])
            root_button = next(
                b
                for b in self.w.findChildren(QPushButton)
                if b.text() == "Root terminal"
            )
            for folder, expected in [
                (self.w.current, str(self.a)),
                (resolve("smb://fileserver/Shared"), str(Path.home())),
                (resolve(str(self.a / "missing")), str(Path.home())),
            ]:
                launcher.reset_mock()
                self.w.attempt = folder
                QTest.mouseClick(root_button, Qt.LeftButton)
                self.spin(lambda: launcher.called)
                launcher.assert_called_once_with(["admin", expected])
            self.w.attempt = None
        info = Gio.FileInfo()
        info.set_attribute_uint64("filesystem::free", 25)
        info.set_attribute_uint64("filesystem::size", 100)
        self.w.storage.update_space(self.w.location, info)
        self.assertEqual(self.w.storage.bar.value(), 750)
        self.assertFalse(self.w.storage.bar.isHidden())
        self.w.storage.update_space(self.w.location, None)
        self.assertTrue(self.w.storage.bar.isHidden())
        self.assertEqual(self.w.storage.value.text(), "Space unavailable")

    def test_archive_actions_and_thumbnail_toggle(self):
        from PyQt5.QtGui import QImage, QColor
        from zipfile import ZipFile
        from archives import ArchiveBrowser

        archive = self.a / "Photos.zip"
        with ZipFile(archive, "w") as z:
            z.writestr("folder/readme.txt", "sample")
        sample = self.a / "sky.png"
        image = QImage(180, 120, QImage.Format_RGB32)
        image.fill(QColor("#88bbff"))
        image.save(str(sample))
        self.w.refresh()
        self.spin(lambda: not self.w.loading)
        if not self.w.thumb_toggle.isChecked():
            self.w.thumb_toggle.click()
        row = next(r for r in self.w.table.files.rows if r.name == "sky.png")
        thumbs = self.w.table.files.thumbnails
        self.spin(lambda: thumbs.get(row) is not None)
        self.assertTrue(State().data["thumbnails"])
        self.assertGreater(self.w.table.iconSize().width(), 60)
        self.select(self.w, "Photos.zip")
        archive_row = self.w.table.selected()[0]
        actions = {a.text(): a for a in self.w.menu(archive_row).actions()}
        self.assertIn("Extract all here", actions)
        self.assertIn("Extract to Photos/", actions)
        self.w.open_selected()
        browser = self.w.findChild(ArchiveBrowser)
        self.spin(lambda: browser.tree.topLevelItemCount() == 1)
        folder = browser.tree.topLevelItem(0)
        folder.setExpanded(True)
        folder.child(0).setSelected(True)
        browser.copy_button.click()
        browser.close()
        other = self.new(self.b)
        other.paste()
        self.spin(
            lambda: (self.b / "readme.txt").exists() and not self.s.timer.isActive()
        )
        self.assertEqual((self.b / "readme.txt").read_text(), "sample")
        actions["Extract to Photos/"].trigger()
        self.spin(
            lambda: (self.a / "Photos/folder/readme.txt").exists()
            and not self.s.timer.isActive()
        )
        self.w.thumb_toggle.click()

    def test_keyring_choice_respects_guest_and_backend_support(self):
        from gi.repository import Gio
        from PyQt5.QtWidgets import QCheckBox

        flags = Gio.AskPasswordFlags
        for supported, guest, remember, expected in (
            (True, False, True, Gio.PasswordSave.PERMANENTLY),
            (True, False, False, Gio.PasswordSave.NEVER),
            (True, True, True, Gio.PasswordSave.NEVER),
            (False, False, False, Gio.PasswordSave.NEVER),
        ):
            options = (
                flags.NEED_USERNAME | flags.NEED_PASSWORD | flags.ANONYMOUS_SUPPORTED
            )
            if supported:
                options |= flags.SAVING_SUPPORTED
            seen = []

            def choose():
                dlg = self.app.activeModalWidget()
                checks = {c.text(): c for c in dlg.findChildren(QCheckBox)}
                store = checks.get("Remember this login in the Linux keyring")
                seen.append(bool(store and store.isChecked() and store.isVisible()))
                if store:
                    store.setChecked(remember)
                checks["Connect as guest"].setChecked(guest)
                dlg.accept()

            op = Gio.MountOperation()
            QTimer.singleShot(0, choose)
            self.s.credentials(
                op,
                self.w,
                resolve("smb://fileserver/Shared"),
                "Sign in",
                "",
                "",
                options,
            )
            self.assertEqual(seen, [supported])
            self.assertEqual(op.get_anonymous(), guest)
            self.assertEqual(op.get_password_save(), expected)

    def test_multi_launch_and_network_mount_retry(self):
        host = Instance(self.s, None)
        self.assertTrue(host.primary)
        try:
            subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).resolve().parents[1] / "root_explorer/main.py"),
                    str(self.b),
                ],
                check=True,
                timeout=6,
            )
            self.spin(
                lambda: len(self.s.windows) == 2 and not self.s.windows[-1].loading
            )
            self.assertEqual(self.s.windows[-1].current.get_path(), str(self.b))
        finally:
            host.close()
        # Exercise async login handoff without a connection to the user's LAN.
        from gi.repository import Gio, GLib

        exc = GLib.Error.new_literal(
            Gio.io_error_quark(), "Not mounted", Gio.IOErrorEnum.NOT_MOUNTED
        )
        with patch("explorer.listing", side_effect=[exc, []]), patch.object(
            self.s, "mount", side_effect=lambda f, w, cb, cancel: cb(None)
        ) as mount:
            self.w.go(resolve(r"\\fileserver\videos"))
            self.spin(lambda: not self.w.loading)
            self.assertEqual(display(self.w.current), r"\\fileserver\videos")
            self.assertEqual(mount.call_count, 1)


if __name__ == "__main__":
    main()
