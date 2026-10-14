"""Non-destructive regression test for the Pages checkout cleanup selection."""

from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest


class PagesCleanupTest(unittest.TestCase):
    def test_cleanup_selects_only_checkout_children_except_git(self):
        script = (Path(__file__).resolve().parents[1] / "scripts/ci/ci.sh").read_text()
        cleanup = next(line.strip() for line in script.splitlines()
                       if line.strip().startswith('find "$WORK"'))
        selection, action = cleanup.split(" -exec ", 1)
        self.assertEqual(action, "rm -rf {} +")
        # Evaluate the production find predicates, replacing deletion with printing.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "checkout"
            root.mkdir()
            (root / ".git").mkdir()
            (root / ".git" / "config").write_text("preserve")
            (root / "screenshots").mkdir()
            (root / "screenshots" / "old.png").write_bytes(b"old")
            (root / "index.html").write_text("old")
            (root / ".nojekyll").touch()
            args = [str(root) if arg == "$WORK" else arg
                    for arg in shlex.split(selection)]
            result = subprocess.run(args + ["-print"], check=True,
                                    capture_output=True, text=True)
            selected = set(result.stdout.splitlines())
            self.assertNotIn(str(root), selected, "cleanup must not delete the checkout root")
            self.assertNotIn(str(root / ".git"), selected)
            self.assertEqual(selected, {str(root / "screenshots"),
                                        str(root / "index.html"),
                                        str(root / ".nojekyll")})
            self.assertEqual((root / ".git" / "config").read_text(), "preserve")


if __name__ == "__main__":
    unittest.main()
