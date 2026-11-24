import gzip
import io
from pathlib import Path
import sys
import tarfile
from tempfile import TemporaryDirectory
from unittest import TestCase
from zipfile import ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "root_explorer"))
from gi.repository import Gio
from archives import ExtractJob, index_archive
from files import resolve


class Archives(TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.out = self.base / "dest"
        self.out.mkdir()

    def extract(self, path, **options):
        job = ExtractJob(resolve(str(path)), resolve(str(self.out)), **options)
        job.run()
        return job

    def test_zip_browsing_selection_conflicts_and_changed_file(self):
        file = self.base / "two.part.zip"
        with ZipFile(file, "w") as z:
            z.writestr("album/vacation/photo.txt", b"picture")
            z.writestr("album/empty/", "")
            z.writestr("extra", "more")
        listing, skipped, stamp = index_archive(resolve(str(file)), Gio.Cancellable())
        self.assertTrue(listing[()]["album"][0])
        self.assertEqual(listing[("album", "vacation")]["photo.txt"], (False, 7))
        self.assertEqual(skipped, 0)
        job = self.extract(file, selection=[("album", "vacation")], stamp=stamp)
        self.assertEqual(job.status, "Complete", job.errors)
        self.assertEqual((self.out / "vacation/photo.txt").read_bytes(), b"picture")
        self.assertFalse((self.out / "extra").exists())
        for suffix in ("", " (copy)"):
            job = self.extract(file, named=True)
            self.assertEqual(job.status, "Complete", job.errors)
            self.assertTrue((self.out / ("two.part" + suffix) / "album/empty").is_dir())
        with ZipFile(file, "a") as z:
            z.writestr("another", "new")
        self.assertTrue(
            self.extract(file, selection=[("extra",)], stamp=stamp).failures
        )
        self.assertFalse((self.out / "extra").exists())

    def test_tar_and_loose_compressed_files(self):
        path = self.base / "package.tar.xz"
        with tarfile.open(path, "w:xz") as t:
            data = b"testing"
            item = tarfile.TarInfo("nested/example.txt")
            item.size = len(data)
            t.addfile(item, io.BytesIO(data))
        job = self.extract(path, named=True)
        self.assertEqual(job.status, "Complete", job.errors)
        self.assertEqual((self.out / "package/nested/example.txt").read_bytes(), data)
        gz = self.base / "notes.txt.gz"
        gz.write_bytes(gzip.compress(b"hello"))
        job = self.extract(gz)
        self.assertEqual(job.status, "Complete", job.errors)
        self.assertEqual((self.out / "notes.txt").read_bytes(), b"hello")

    def test_archive_paths_links_and_existing_targets(self):
        path = self.base / "mixed.tar"
        with tarfile.open(path, "w") as t:
            for name in ("../escaped", "/unexpected", "good/file.txt"):
                item = tarfile.TarInfo(name)
                item.size = 4
                t.addfile(item, io.BytesIO(b"data"))
            link = tarfile.TarInfo("outside")
            link.type = tarfile.SYMTYPE
            link.linkname = "../"
            t.addfile(link)
        (self.out / "good").symlink_to(self.base, target_is_directory=True)
        result = self.extract(path)
        self.assertTrue(result.failures)
        self.assertTrue((self.out / "good (copy)/file.txt").exists())
        self.assertFalse((self.base / "escaped").exists())
        self.assertFalse((self.base / "file.txt").exists())
        self.assertFalse((self.out / "outside").is_symlink())
