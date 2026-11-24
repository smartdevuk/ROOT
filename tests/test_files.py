import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from unittest import TestCase, main, mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "root_explorer"))
from gi.repository import Gio
from files import completion_parts, display, listing, measure, parent, resolve
from transfers import Job, relocate


class Files(TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.src, self.dst = self.base / "src", self.base / "out"
        self.src.mkdir()
        self.dst.mkdir()

    def job(self, sources=None, dest=None, move=False):
        obj = Job(
            [resolve(str(p)) for p in sources or [self.src]],
            resolve(str(dest or self.dst)),
            move,
        )
        obj.run()
        return obj

    def test_network_and_completion(self):
        for name in (r"\\fileserver\videos\Box Sets\100% #1?", r"\\fileserver\videos\Café"):
            self.assertEqual(display(resolve(name)), name)
            self.assertEqual(display(resolve(resolve(name).get_uri())), name)
        server, prefix = completion_parts(r"\\fileserver\vid", resolve(str(self.src)))
        self.assertEqual(display(server), r"\\fileserver")
        self.assertEqual(prefix, "vid")
        self.assertEqual(display(parent(resolve(r"\\fileserver\videos"))), r"\\fileserver")
        self.assertIsNone(parent(resolve(r"\\fileserver")))
        with self.assertRaises(ValueError):
            resolve("smb://user:pass@fileserver/videos")
        self.assertEqual(
            display(resolve("folder", resolve(r"\\fileserver\videos"))),
            r"\\fileserver\videos\folder",
        )

    def test_copy_folder_with_links_and_conflicts(self):
        sub = self.src / "nested"
        sub.mkdir()
        (sub / ".hidden").write_bytes(b"abc")
        (sub / "empty").mkdir()
        (sub / "cycle").symlink_to(self.src, target_is_directory=True)
        (self.dst / "src").mkdir()
        result = self.job()
        self.assertEqual(result.status, "Complete", result.errors)
        copy = self.dst / "src (copy)" / "nested"
        self.assertEqual((copy / ".hidden").read_bytes(), b"abc")
        self.assertTrue((copy / "cycle").is_symlink())
        self.assertTrue((copy / "empty").is_dir())
        self.assertEqual(result.renamed, 1)
        self.assertEqual(result.finished, 2)
        self.assertEqual(list(self.dst.rglob("*.part")), [])

    def test_file_conflict_and_same_folder(self):
        p = self.src / "film.mkv"
        p.write_bytes(b"original")
        (self.dst / p.name).write_bytes(b"keep")
        job = self.job([p])
        self.assertEqual(job.status, "Complete", job.errors)
        self.assertEqual((self.dst / p.name).read_bytes(), b"keep")
        self.assertEqual((self.dst / "film (copy).mkv").read_bytes(), b"original")
        self.job([p], self.src)
        self.assertEqual((self.src / "film (copy).mkv").read_bytes(), b"original")
        self.job([p], self.src, True)
        self.assertTrue(p.exists())

    def test_move_and_aliased_recursion(self):
        (self.src / "file").write_text("hello")
        alias = self.base / "alias"
        alias.symlink_to(self.src, target_is_directory=True)
        j = self.job(dest=alias)
        self.assertEqual(j.status, "Finished with errors")
        self.assertEqual(os.listdir(self.src), ["file"])
        j = self.job(move=True)
        self.assertEqual(j.status, "Complete", j.errors)
        self.assertFalse(self.src.exists())
        self.assertEqual((self.dst / "src" / "file").read_text(), "hello")

    def test_cross_volume_move_and_cancel(self):
        p = self.src / "film"
        p.write_bytes(os.urandom(100000))
        with mock.patch.object(Job, "native_move", return_value=False):
            j = self.job(move=True)
        self.assertEqual(j.status, "Complete", j.errors)
        self.assertEqual(j.bytes, 100000)
        self.assertFalse(self.src.exists())
        j = Job([resolve(str(self.dst))], resolve(str(self.base)))
        j.cancel.cancel()
        j.run()
        self.assertEqual(j.status, "Cancelled")
        self.assertTrue((self.dst / "src" / "film").is_file())

    def test_racing_publish_and_failure_keeps_source(self):
        p = self.src / "a"
        p.write_bytes(b"source")

        def collision(a, b, c=None):
            if b.get_basename() == "a" and not (self.dst / "a").exists():
                (self.dst / "a").write_bytes(b"racer")
            return relocate(a, b, c)

        with mock.patch("transfers.relocate", collision):
            j = self.job([p])
        self.assertEqual(j.status, "Complete", j.errors)
        self.assertEqual((self.dst / "a").read_bytes(), b"racer")
        self.assertEqual((self.dst / "a (copy)").read_bytes(), b"source")
        with mock.patch.object(Job, "native_move", return_value=False), mock.patch(
            "transfers.relocate", side_effect=PermissionError("denied")
        ):
            j = self.job([p], move=True)
        self.assertEqual(j.status, "Finished with errors")
        self.assertEqual(p.read_bytes(), b"source")
        self.assertFalse(any(self.dst.glob("*.part")))

    def test_measure_does_not_follow_links(self):
        (self.src / "nested").mkdir()
        (self.src / "nested" / "big").write_bytes(b"x" * 33)
        (self.src / ".tiny").write_bytes(b"12")
        (self.src / "loop").symlink_to(self.src)
        size, files, dirs, groups, errors, allocated = measure(
            resolve(str(self.src)), Gio.Cancellable(), lambda *_: None
        )
        self.assertEqual((size, files, dirs, errors), (35, 3, 1, []))
        self.assertEqual(groups[(True, "nested")], 33)
        self.assertTrue(
            next(r for r in listing(resolve(str(self.src))) if r.name == ".tiny").hidden
        )

    def test_exclusive_publication_on_shared_volumes(self):
        import errno

        p = self.src / "report.txt"
        p.write_bytes(b"new report")
        (self.dst / p.name).write_bytes(b"older report")
        unsupported = OSError(errno.EOPNOTSUPP, "Not supported on this volume")
        with mock.patch("transfers.relocate", side_effect=unsupported), mock.patch(
            "transfers.os.link", side_effect=unsupported
        ):
            job = self.job([p])
        self.assertEqual(job.status, "Complete", job.errors)
        self.assertEqual((self.dst / p.name).read_bytes(), b"older report")
        self.assertEqual((self.dst / "report (copy).txt").read_bytes(), b"new report")

    def test_live_cancellation(self):
        p = self.src / "large"
        p.write_bytes(os.urandom(4 * 1024 * 1024))
        job = Job([resolve(str(p))], resolve(str(self.dst)), True)

        def interrupt(key, name, offset, size, state):
            if state == "working" and offset:
                job.cancel.cancel()

        with mock.patch.object(
            job, "native_move", return_value=False
        ), mock.patch.object(job, "set_row", side_effect=interrupt):
            job.run()
        self.assertEqual(job.status, "Cancelled")
        self.assertTrue(p.exists())
        self.assertEqual(list(self.dst.iterdir()), [])


if __name__ == "__main__":
    main()
