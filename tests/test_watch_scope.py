"""watch 范围：思源只看 data/，普通目录不注册排除目录 watch。"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from connectors.directory import DirectoryConnector
from connectors.siyuan import SiyuanConnector


class WatchScopeTest(unittest.TestCase):
    def test_directory_prunes_builtin_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "notes").mkdir()
            (root / "node_modules").mkdir()
            (root / ".obsidian").mkdir()
            connector = DirectoryConnector(root)
            self.assertEqual(connector.watch_root(), root)
            self.assertTrue(connector.watch_allowed(root))
            self.assertTrue(connector.watch_allowed(root / "notes"))
            self.assertFalse(connector.watch_allowed(root / "node_modules"))
            self.assertFalse(connector.watch_allowed(root / ".obsidian"))

    def test_siyuan_watches_data_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = root / "data"
            (data / "box" / "sub").mkdir(parents=True)
            (data / "assets").mkdir()
            (root / "repo").mkdir()
            (root / "history").mkdir()
            (root / "conf").mkdir()
            (root / "temp").mkdir()
            connector = SiyuanConnector(root)
            self.assertEqual(connector.watch_root(), data)
            self.assertTrue(connector.watch_allowed(data))
            self.assertTrue(connector.watch_allowed(data / "box"))
            self.assertTrue(connector.watch_allowed(data / "box" / "sub"))
            self.assertFalse(connector.watch_allowed(data / "assets"))
            self.assertFalse(connector.watch_allowed(root / "repo"))
            self.assertFalse(connector.watch_allowed(root / "history"))
            self.assertFalse(connector.watch_allowed(root / "conf"))
            self.assertFalse(connector.watch_allowed(root / "temp"))


if __name__ == "__main__":
    unittest.main()
