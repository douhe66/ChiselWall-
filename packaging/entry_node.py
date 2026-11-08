"""PyInstaller 入口：本地节点（chiselwall-node）。"""
import sys
from pathlib import Path

if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from node.agent import main  # noqa: E402

if __name__ == "__main__":
    main()