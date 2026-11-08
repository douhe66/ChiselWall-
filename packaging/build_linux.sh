#!/usr/bin/env bash
# 凿壁 ChiselWall · Linux 打包（PyInstaller，免安装目录版）
set -euo pipefail
cd "$(dirname "$0")/.."

python3 packaging/prepare_bundle_models.py

python3 -m PyInstaller --noconfirm --clean --name chiselwall-node \
  --paths src --distpath dist-linux --workpath build-linux \
  --add-data "src/ui:ui" --add-data "build/models-bundle/models:models" \
  --collect-all jieba --collect-all sqlite_vec --collect-all onnxruntime --collect-all tokenizers \
  --collect-submodules uvicorn --collect-submodules websockets \
  packaging/entry_node.py

python3 -m PyInstaller --noconfirm --clean --name chiselwall-relay \
  --paths src --distpath dist-linux --workpath build-linux \
  --collect-submodules uvicorn --collect-submodules websockets \
  packaging/entry_relay.py

echo "完成：dist-linux/chiselwall-node/ 与 dist-linux/chiselwall-relay/"