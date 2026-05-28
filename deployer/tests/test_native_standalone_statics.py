"""Unit tests for _copy_standalone_statics() in deployers/native.py."""
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

DEPLOYER_DIR = Path(__file__).parent.parent
if str(DEPLOYER_DIR) not in sys.path:
    sys.path.insert(0, str(DEPLOYER_DIR))

from deployers.native import _copy_standalone_statics  # noqa: E402


def _make_tree(root: Path) -> None:
    """Create a minimal .next + public layout under root."""
    (root / ".next" / "standalone").mkdir(parents=True)
    (root / ".next" / "static" / "css").mkdir(parents=True)
    (root / ".next" / "static" / "css" / "app.css").write_text("body{}", encoding="utf-8")
    (root / ".next" / "static" / "chunks").mkdir(parents=True)
    (root / ".next" / "static" / "chunks" / "main.js").write_text("// js", encoding="utf-8")
    (root / "public" / "images").mkdir(parents=True)
    (root / "public" / "favicon.ico").write_bytes(b"\x00")


class CopyStandaloneStaticsTests(unittest.TestCase):
    def test_no_standalone_dir_is_noop(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            next_dir = root / ".next"
            next_dir.mkdir()
            (next_dir / "static").mkdir()
            (root / "public").mkdir()
            # standalone/ absent — must not raise, must not create anything
            _copy_standalone_statics(next_dir, root)
            self.assertFalse((next_dir / "standalone").exists())

    def test_copies_static_and_public_into_standalone(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_tree(root)
            _copy_standalone_statics(root / ".next", root)
            dest_static = root / ".next" / "standalone" / ".next" / "static"
            dest_public = root / ".next" / "standalone" / "public"
            self.assertTrue((dest_static / "css" / "app.css").exists())
            self.assertTrue((dest_static / "chunks" / "main.js").exists())
            self.assertTrue((dest_public / "favicon.ico").exists())

    def test_idempotent_second_call_does_not_raise(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_tree(root)
            _copy_standalone_statics(root / ".next", root)
            # Second call must succeed (dirs_exist_ok=True)
            _copy_standalone_statics(root / ".next", root)
            dest_static = root / ".next" / "standalone" / ".next" / "static"
            self.assertTrue((dest_static / "css" / "app.css").exists())

    def test_no_static_dir_skips_static_copies_public(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".next" / "standalone").mkdir(parents=True)
            # No .next/static
            (root / "public").mkdir()
            (root / "public" / "logo.png").write_bytes(b"\xff")
            _copy_standalone_statics(root / ".next", root)
            self.assertFalse((root / ".next" / "standalone" / ".next" / "static").exists())
            self.assertTrue((root / ".next" / "standalone" / "public" / "logo.png").exists())

    def test_no_public_dir_skips_public_copies_static(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".next" / "standalone").mkdir(parents=True)
            (root / ".next" / "static").mkdir()
            (root / ".next" / "static" / "app.css").write_text("*{}", encoding="utf-8")
            # No public/
            _copy_standalone_statics(root / ".next", root)
            self.assertTrue((root / ".next" / "standalone" / ".next" / "static" / "app.css").exists())
            self.assertFalse((root / ".next" / "standalone" / "public").exists())


if __name__ == "__main__":
    unittest.main()
