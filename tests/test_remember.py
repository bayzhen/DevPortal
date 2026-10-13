import base64
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

from dev_portal.pairing import Pairing
from dev_portal.remember import (RememberError, forget_remembered, load_remembered,
                                 save_remembered)


def ssh_key():
    name = b"ssh-ed25519"
    blob = struct.pack(">I", len(name)) + name + struct.pack(">I", 32) + b"a" * 32
    return "ssh-ed25519 " + base64.b64encode(blob).decode("ascii")


def fixture():
    return Pairing("hub.example", 22, "recipient", ssh_key(), "game-dev-ue",
                   "spouse-device", "X" * 64)


class RememberTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "secret" / "pairing.dat"

    def test_only_explicit_save_writes_and_forget_removes(self):
        pairing = fixture()
        self.assertIsNone(load_remembered(self.path))
        self.assertFalse(self.path.exists())
        save_remembered(pairing, self.path)
        self.assertEqual(load_remembered(self.path), pairing)
        data = self.path.read_bytes()
        if sys.platform == "win32":
            self.assertTrue(data.startswith(b"DPW1"))
            self.assertNotIn(pairing.portal_key.encode(), data)
            self.assertNotIn(b"hub.example", data)
        else:
            self.assertTrue(data.startswith(b"DPX1"))
            self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(self.path.parent.stat().st_mode & 0o777, 0o700)
        forget_remembered(self.path)
        self.assertIsNone(load_remembered(self.path))
        forget_remembered(self.path)

    def test_invalid_ciphertext_fails_closed(self):
        save_remembered(fixture(), self.path)
        self.path.write_bytes(b"DPW1broken" if sys.platform == "win32" else b"DPX1broken")
        with self.assertRaises(RememberError) as context:
            load_remembered(self.path)
        self.assertNotIn("X" * 64, str(context.exception))

    def test_unavailable_directory_stat_is_bounded_for_load_and_forget(self):
        with patch.object(Path, "lstat", side_effect=PermissionError("secret filesystem path")):
            for action in (load_remembered, forget_remembered):
                with self.subTest(action=action.__name__), self.assertRaises(RememberError) as caught:
                    action(self.path)
                self.assertNotIn("secret", str(caught.exception))

    def test_unavailable_file_stat_and_save_or_forget_fail_closed(self):
        save_remembered(fixture(), self.path)
        original = Path.lstat
        def deny_file_stat(path):
            if path == self.path:
                raise PermissionError("secret saved file")
            return original(path)
        with patch.object(Path, "lstat", deny_file_stat):
            for action in (load_remembered, forget_remembered, lambda path: save_remembered(fixture(), path)):
                with self.subTest(action=action), self.assertRaises(RememberError) as caught:
                    action(self.path)
                self.assertNotIn("secret", str(caught.exception))
        self.assertTrue(self.path.exists())

    def test_failed_replace_and_unlink_do_not_expose_paths_or_credentials(self):
        with patch("dev_portal.remember.os.replace", side_effect=PermissionError("secret path")):
            with self.assertRaises(RememberError) as caught:
                save_remembered(fixture(), self.path)
            self.assertNotIn("secret", str(caught.exception))
        self.assertFalse(self.path.exists())
        save_remembered(fixture(), self.path)
        with patch.object(Path, "unlink", side_effect=PermissionError("secret path")):
            with self.assertRaises(RememberError) as caught:
                forget_remembered(self.path)
            self.assertNotIn("secret", str(caught.exception))
        self.assertTrue(self.path.exists())

    @unittest.skipIf(sys.platform == "win32", "POSIX owner-only permission test")
    def test_world_readable_and_symlink_storage_rejected(self):
        save_remembered(fixture(), self.path)
        self.path.chmod(0o644)
        with self.assertRaises(RememberError):
            load_remembered(self.path)
        self.path.chmod(0o600)
        other = self.path.parent / "other"
        self.path.rename(other)
        self.path.symlink_to(other)
        with self.assertRaises(RememberError):
            save_remembered(fixture(), self.path)


if __name__ == "__main__":
    unittest.main()
