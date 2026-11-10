"""watch 注册范围：不把 repo/history/conf/temp/node_modules 注册成 watch。"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from connectors.directory import DirectoryConnector
from connectors.siyuan import SiyuanConnector
from indexer.watcher import WatchService


class FakeObserver:
    def __init__(self):
        self.scheduled = []
        self.daemon = False

    def schedule(self, handler, path, recursive=False, **kwargs):
        self.scheduled.append((str(Path(path).resolve()), bool(recursive)))
        return object()

    def start(self):
        return None

    def stop(self):
        return None

    def join(self, timeout=None):
        return None


class WatchScheduleTest(unittest.TestCase):
    def test_siyuan_only_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = root / "data"
            (data / "box" / "sub").mkdir(parents=True)
            (data / "assets").mkdir()
            for name in ("repo", "history", "conf", "temp"):
                (root / name).mkdir()
            connector = SiyuanConnector(root)
            service = WatchService(indexer=None, db=None)  # type: ignore[arg-type]
            with mock.patch("indexer.watcher.Observer", FakeObserver):
                service.add_mount(1, connector)
            observer = service._mounts[1][1]
            scheduled = observer.scheduled
            paths = [p for p, _r in scheduled]
            self.assertIn(str(data.resolve()), paths)
            self.assertIn(str((data / "box").resolve()), paths)
            self.assertTrue(any(r for p, r in scheduled if p == str((data / "box").resolve())))
            for bad in (root / "repo", root / "history", root / "conf", root / "temp", data / "assets"):
                self.assertNotIn(str(bad.resolve()), paths)

    def test_directory_prunes_top_level(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "notes").mkdir()
            (root / "node_modules").mkdir()
            (root / ".obsidian").mkdir()
            connector = DirectoryConnector(root)
            service = WatchService(indexer=None, db=None)  # type: ignore[arg-type]
            with mock.patch("indexer.watcher.Observer", FakeObserver):
                service.add_mount(2, connector)
            paths = [p for p, _r in service._mounts[2][1].scheduled]
            self.assertIn(str(root.resolve()), paths)
            self.assertIn(str((root / "notes").resolve()), paths)
            self.assertNotIn(str((root / "node_modules").resolve()), paths)
            self.assertNotIn(str((root / ".obsidian").resolve()), paths)


if __name__ == "__main__":
    unittest.main()
