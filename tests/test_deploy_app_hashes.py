"""Deployment regression tests using the device helpers on a temporary filesystem."""

import contextlib
import hashlib
import importlib.util
import io
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock


TOOLS = Path(__file__).resolve().parents[1] / "tools"
SPEC = importlib.util.spec_from_file_location("deploy_app", TOOLS / "deploy_app.py")
deploy_app = importlib.util.module_from_spec(SPEC)
# Serial transport is not needed: execute the same device helpers locally.
transport = types.ModuleType("sd_format_push")
transport.raw_exec = mock.Mock()
transport.hard_reset_and_wait = mock.Mock()
with mock.patch.dict(sys.modules, {"sd_format_push": transport}):
    SPEC.loader.exec_module(deploy_app)


class DeployHashesTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.local = Path(self.temp.name) / "local"
        self.remote = Path(self.temp.name) / "remote"
        self.local.mkdir()
        self.remote.mkdir()
        self.namespace = {}
        self.commands = []
        self.exec_device(None, deploy_app.DEVICE_HELPERS)
        patcher = mock.patch.object(deploy_app, "raw_exec", self.exec_device)
        patcher.start()
        self.addCleanup(patcher.stop)

    def exec_device(self, ser, code, timeout=30):
        self.commands.append(code)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            exec(code, self.namespace)
        return output.getvalue().strip()

    def tree(self):
        return deploy_app.device_tree(None, (str(self.remote),))

    def push(self, **kwargs):
        expected = {}
        with contextlib.redirect_stdout(io.StringIO()):
            deploy_app.push_tree(None, str(self.local), str(self.remote), "test",
                                 set(), (), expected, self.tree(), **kwargs)
        return expected

    def verify(self, expected):
        with contextlib.redirect_stdout(io.StringIO()):
            return deploy_app.verify(None, expected, (str(self.remote),))

    def test_same_size_edit_is_pushed(self):
        (self.local / "app.py").write_bytes(b"value=2\n")
        (self.remote / "app.py").write_bytes(b"value=1\n")
        expected = self.push()
        self.assertEqual((self.remote / "app.py").read_bytes(), b"value=2\n")
        self.assertTrue(self.verify(expected))

    def test_verification_rejects_same_size_corruption(self):
        (self.local / "app.py").write_bytes(b"value=2\n")
        expected = self.push()
        (self.remote / "app.py").write_bytes(b"value=1\n")
        self.assertFalse(self.verify(expected))
        # A retry must replace a same-size corrupt file too.
        self.assertTrue(self.verify(self.push()))
        self.assertEqual((self.remote / "app.py").read_bytes(), b"value=2\n")

    def test_identical_content_is_skipped_unless_forced(self):
        (self.local / "app.py").write_bytes(b"value=2\n")
        (self.remote / "app.py").write_bytes(b"value=2\n")
        self.commands.clear()
        self.push()
        self.assertFalse(any("_w(" in command for command in self.commands))
        self.commands.clear()
        self.push(force=True)
        self.assertTrue(any("_w(" in command for command in self.commands))

    def test_compiled_bytes_are_hashed_and_stale_source_removed(self):
        (self.local / "app.py").write_bytes(b"value=2\n")
        (self.remote / "app.py").write_bytes(b"value=1\n")
        (self.remote / "app.mpy").write_bytes(b"old-code")
        with mock.patch.object(deploy_app, "_compile_mpy", return_value=(b"new-code", None)):
            expected = self.push(compile_mpy=True, mpy_cross="mock")
        self.assertFalse((self.remote / "app.py").exists())
        self.assertEqual((self.remote / "app.mpy").read_bytes(), b"new-code")
        self.assertEqual(expected[str(self.remote / "app.mpy")],
                         hashlib.sha256(b"new-code").hexdigest())
        self.assertTrue(self.verify(expected))

    def test_recursive_hashes_empty_and_large_binary_files(self):
        nested = self.remote / "nested folder"
        nested.mkdir()
        data = bytes(range(256)) * 100
        (nested / "binary file").write_bytes(data)
        (self.remote / "empty").write_bytes(b"")
        self.assertEqual(self.tree(), {
            str(nested / "binary file"): hashlib.sha256(data).hexdigest(),
            str(self.remote / "empty"): hashlib.sha256(b"").hexdigest(),
        })

    def test_missing_file_fails_verification(self):
        (self.local / "app.py").write_bytes(b"value=2\n")
        expected = self.push()
        (self.remote / "app.py").unlink()
        self.assertFalse(self.verify(expected))


if __name__ == "__main__":
    unittest.main()
