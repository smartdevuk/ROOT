"""OpenSSH argument construction and durable named sessions."""

import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "root_explorer"))
from ssh_sessions import Sessions, profile, ssh_command


class SavedSessions(TestCase):
    def test_profile_lifecycle_and_openssh_arguments(self):
        with TemporaryDirectory() as directory, patch.dict(
            os.environ, {"XDG_CONFIG_HOME": directory}
        ):
            key = Path(directory) / "key with spaces"
            key.touch()
            store = Sessions()
            item = profile(
                "Example SSH", "demo@ssh.example.test", port="2222", key=str(key)
            )
            store.put(item)
            self.assertEqual(
                ssh_command(Sessions().rows[0]),
                [
                    "/usr/bin/ssh",
                    "-t",
                    "-p",
                    "2222",
                    "-l",
                    "demo",
                    "-i",
                    str(key),
                    "--",
                    "ssh.example.test",
                ],
            )
            self.assertEqual(store.path.stat().st_mode & 0o777, 0o600)
            renamed = profile("Home NAS", "[::1]", identity=item["id"])
            store.put(renamed)
            self.assertEqual(Sessions().rows, [renamed])
            self.assertEqual(ssh_command(renamed), ["/usr/bin/ssh", "-t", "--", "::1"])
            store.remove(renamed)
            self.assertEqual(Sessions().rows, [])

    def test_invalid_settings(self):
        for options in (
            {"host": "-v"},
            {"host": "fileserver", "port": "70000"},
            {"host": "fileserver", "port": "ssh"},
            {"host": "fileserver", "key": "/missing-test-key"},
        ):
            with self.subTest(options=options), self.assertRaises(ValueError):
                profile("Test", **options)
