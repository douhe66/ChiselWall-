# 凿壁 ChiselWall · Windows 打包（PyInstaller，免安装文件夹版）
# 用法：pwsh -File packaging\build_windows.ps1
param(
  [string]$Python = "python",
  [string]$OutDir = "dist"
)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

Write-Host "==> 准备打包模型（仅向量模型）"
& $Python packaging/prepare_bundle_models.py
if ($LASTEXITCODE -ne 0) { throw "模型准备失败" }

Write-Host "==> 打包节点 chiselwall-node"
& $Python -m PyInstaller --noconfirm --clean --name chiselwall-node `
  --paths src --distpath $OutDir --workpath build `
  --add-data "src/ui;ui" --add-data "build/models-bundle/models;models" `
  --collect-all jieba --collect-all sqlite_vec --collect-all onnxruntime --collect-all tokenizers `
  --collect-submodules uvicorn --collect-submodules websockets `
  packaging/entry_node.py
if ($LASTEXITCODE -ne 0) { throw "节点打包失败" }

Write-Host "==> 打包 relay chiselwall-relay"
& $Python -m PyInstaller --noconfirm --clean --name chiselwall-relay `
  --paths src --distpath $OutDir --workpath build `
  --collect-submodules uvicorn --collect-submodules websockets `
  packaging/entry_relay.py
if ($LASTEXITCODE -ne 0) { throw "relay 打包失败" }

Write-Host "==> 完成：$OutDir\chiselwall-node\chiselwall-node.exe / $OutDir\chiselwall-relay\chiselwall-relay.exe"