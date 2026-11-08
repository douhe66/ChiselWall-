"""凿壁 ChiselWall 本地节点入口。

用法：python run.py [--db 路径] [--port 端口] [--debounce 秒] [--cpu-threshold 百分比]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from node.agent import main  # noqa: E402

if __name__ == "__main__":
    main()
