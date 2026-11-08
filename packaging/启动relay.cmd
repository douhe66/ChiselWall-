@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist "relay.json" (
  echo 首次运行：生成 relay.json ...
  "chiselwall-relay\chiselwall-relay.exe" --init
  echo.
  echo 请执行邀请命令后，再启动 relay：
  echo   chiselwall-relay\chiselwall-relay.exe admin invite 张三 --group 全员组 --url ws://本机IP:18760/ws
  pause
  exit /b 0
)
"chiselwall-relay\chiselwall-relay.exe" --config relay.json --host 0.0.0.0 --port 18760
pause