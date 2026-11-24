"""Exercise ROOT on a writable test share. Give the share's UNC path as argv[1]."""

import os
from pathlib import Path
import sys
import tempfile
import time
import uuid
from gi.repository import Gio
from PyQt5.QtCore import QTimer, Qt
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QDialog, QLineEdit, QDialogButtonBox

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "root_explorer"))
from files import NOFOLLOW, DIR, child, display, iter_info, measure, resolve
from main import configure
from service import Service
from transfers import Job


def wait_for(app, predicate):
    end = time.monotonic() + 45
    while not predicate() and time.monotonic() < end:
        app.processEvents()
        QTest.qWait(20)
    assert predicate(), "Timed out waiting for ROOT"


def run():
    share = resolve(sys.argv[1])
    assert share.get_uri().startswith("smb://"), "Supply a writable SMB test share"
    app = QApplication(["ROOT integration test"])
    configure(app)
    svc = Service()
    friendly = share
    test_ip = os.environ.get("ROOT_TEST_NAS_IP")
    w = svc.window() if test_ip else svc.window(share)
    # Interactive by default, with optional credentials for a disposable test server.
    attempts = 0

    def sign_in():
        nonlocal attempts
        d = app.activeModalWidget()
        if isinstance(d, QDialog) and d.windowTitle() == "Network sign-in":
            attempts += 1
            if attempts > 3:
                d.reject()
                return
            fields = d.findChildren(QLineEdit)
            fields[0].setText(os.environ["ROOT_TEST_USER"])
            fields[-1].setText(os.environ["ROOT_TEST_PASSWORD"])
            d.accept()

    timer = QTimer()
    if "ROOT_TEST_USER" in os.environ and "ROOT_TEST_PASSWORD" in os.environ:
        timer.timeout.connect(sign_in)
        timer.start(80)
    wait_for(app, lambda: not w.loading)
    if test_ip:
        w.activateWindow()
        w.edit_address()
        w.address.setText(display(friendly))
        QTest.keyClick(w.address, Qt.Key_Return)
        wait_for(app, lambda: not w.loading)
        assert w.failed_network and w.failed_network.equal(friendly), w.notice.text()
        print("Initial UNC lookup:", w.notice.text(), flush=True)

        def enter_ip():
            d = app.activeModalWidget()
            assert d.windowTitle() == "Connection settings"
            path, ip = d.findChildren(QLineEdit)
            assert path.text() == display(friendly)
            ip.setText(test_ip)
            d.findChild(QDialogButtonBox).button(QDialogButtonBox.Ok).click()

        QTimer.singleShot(150, enter_ip)
        w.fix_network.click()
        wait_for(app, lambda: not w.loading)
        share = svc.endpoint(friendly)
        assert not share.equal(friendly)
        assert w.location.equal(friendly)
        assert w.address.text() == display(friendly)
        assert svc.store.data["nas_addresses"]
        print("Saved alias", display(friendly), "→", display(share), flush=True)
    assert w.current.equal(share), w.notice.text()
    root = child(share, "ROOT-test-" + uuid.uuid4().hex)
    root.make_directory(None)

    def check(src, dst, move=False):
        job = Job([src], dst, move)
        job.run()
        assert job.status == "Complete", job.errors
        return job

    try:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            from zipfile import ZipFile
            from archives import ExtractJob, index_archive

            archive = base / "Bundle.zip"
            with ZipFile(archive, "w") as z:
                z.writestr("Docs/test.txt", "archive member")
            check(resolve(str(archive)), root)
            remote_archive = child(root, "Bundle.zip")
            contents, _, stamp = index_archive(remote_archive, Gio.Cancellable())
            assert contents[("Docs",)]["test.txt"] == (False, 14)
            extracted = ExtractJob(remote_archive, root, named=True, stamp=stamp)
            extracted.run()
            assert extracted.status == "Complete", extracted.errors
            assert (
                child(child(child(root, "Bundle"), "Docs"), "test.txt").load_contents(
                    None
                )[1]
                == b"archive member"
            )
            print("PASS SMB archive browse and extraction", flush=True)
            src = base / "Example folder"
            src.mkdir()
            (src / "Empty").mkdir()
            payload = os.urandom(48 * 1024)
            (src / "First #1.txt").write_bytes(payload)
            assert check(resolve(str(src)), root).bytes == len(payload)
            remote = child(root, src.name)
            assert measure(remote, Gio.Cancellable(), lambda *_: None)[:3] == (
                len(payload),
                1,
                1,
            )
            logical_root = child(friendly, root.get_basename())
            w.go(logical_root)
            wait_for(app, lambda: not w.loading)
            w.activateWindow()
            w.raise_()
            w.edit_address()
            QTest.qWait(100)
            partial = display(logical_root) + "\\Exa"
            w.address.setText(partial)
            w.suggest(partial)
            wait_for(app, lambda: w.hints.count() > 0)
            assert w.hints.item(0).text() == display(child(logical_root, src.name))
            if test_ip:
                w.open_text(w.hints.item(0).text())
                wait_for(app, lambda: not w.loading)
                assert w.current.equal(remote)
                wait_for(
                    app,
                    lambda: w.tree.currentItem()
                    and w.tree.currentItem().data(0, Qt.UserRole)
                    == w.location.get_uri(),
                )
                name = next(b for f, b, _ in w.crumbs.items if f.equal(logical_root))
                name.click()
                wait_for(app, lambda: not w.loading)
                assert w.current.equal(root)
                # The alias must survive a reopen from persisted settings.
                from service import State

                assert State().data["nas_addresses"] == svc.store.data["nas_addresses"]
            assert check(resolve(str(src)), root).renamed == 1
            assert check(remote, resolve(str(base))).renamed == 1
            assert (
                base / "Example folder (copy)" / "First #1.txt"
            ).read_bytes() == payload
            src2 = child(root, "Example folder (copy)")
            recv = base / "incoming"
            recv.mkdir()
            check(src2, resolve(str(recv)), True)
            assert (
                recv / "Example folder (copy)" / "First #1.txt"
            ).read_bytes() == payload
            assert not src2.query_exists(None)
            newdir = child(root, "Archive")
            newdir.make_directory(None)
            check(remote, newdir, True)
            assert not remote.query_exists(None)
            assert child(newdir, src.name).query_exists(None)
            assert check(resolve(str(src)), newdir).renamed == 1
            print(
                "PASS: authenticated browsing, completion, properties, upload, download, collisions and moves"
            )
    finally:

        def cleanup(file):
            typ = file.query_file_type(NOFOLLOW, None)
            if typ == DIR:
                for item, _ in list(iter_info(file)):
                    cleanup(item)
            file.delete(None)

        cleanup(root)
        w.close()


if __name__ == "__main__":
    run()
