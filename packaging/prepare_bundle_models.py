"""准备打包用 models 目录：只内置向量模型（bge-small-zh），rerank 大模型不打进包。"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "models"
DST = ROOT / "build" / "models-bundle" / "models"


def main() -> int:
    if DST.exists():
        shutil.rmtree(DST)
    DST.mkdir(parents=True)
    emb = SRC / "bge-small-zh-v1.5"
    if not (emb / "onnx" / "model_quantized.onnx").exists():
        print("缺少向量模型：%s" % emb)
        return 1
    shutil.copytree(emb, DST / "bge-small-zh-v1.5")
    rr = SRC / "rerank"
    if rr.is_dir():
        (DST / "rerank").mkdir(exist_ok=True)
        readme = rr / "README.md"
        if readme.exists():
            shutil.copy2(readme, DST / "rerank" / "README.md")
    total = sum(p.stat().st_size for p in DST.rglob("*") if p.is_file())
    print("bundle models: %.1f MB -> %s" % (total / 1024 / 1024, DST))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())