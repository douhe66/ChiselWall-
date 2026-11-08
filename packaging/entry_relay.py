"""PyInstaller 入口：中心 relay（chiselwall-relay）。"""
import sys
from pathlib import Path

if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from relay.server import main  # noqa: E402

if __name__ == "__main__":
    main()