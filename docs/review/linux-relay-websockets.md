# Linux Relay 连不上 / 测试结果不显示：websockets 依赖缺失

## 根因

- `src/node/bus.py` 在 `RelayClient._run()` 协程内部才 `import websockets`；
- `requirements.txt` 没有声明 websockets；
- Linux 干净环境安装后，协程一启动就抛 ImportError，但异常发生在后台 Task 内，
  `start_bus()` 不 await 它，日志/UI 都看不到，表现为 Relay 永远离线；
- 设置页没有 Relay 测试入口，只有一个“未连接”状态，没有失败原因。

## 修改

1. `requirements.txt` 增加 `websockets>=12`；
2. `node/bus.py` 改为可选顶层 import，并记录 `RelayClient.last_error`；
   - 缺失依赖时立即写入明确错误，不再静默死任务；
   - 连接失败/成功时更新/清空错误；
   - `/api/relay`、`/api/relay/config` 返回 `last_error`、`dependency_ok`；
3. 新增 `POST /api/relay/test`：
   - 用表单中未保存的 URL/nick/token/groups 做一次 hello 握手；
   - 不落盘、不改配置；返回在线人数、组数、耗时；
   - 缺 websockets 或握手失败返回可显示的错误；
4. 设置页新增“测试连接”，结果显示在按钮下方；连接错误也显示在状态旁；
5. 顺带修正 `restart_bus()` 使用 local searcher 的问题（应使用 remote searcher）。

## 验证

- `tests/test_relay_probe.py`：缺依赖报错、真实 hello 握手；
- Linux/Windows 安装：`pip install -r requirements.txt`；
- 设置页填写 ws://... 后点“测试连接”，应显示：
  `连接成功：昵称 · 在线 N 人 · M 组 · XXms`。

## 注意

- PyInstaller 构建脚本原本已 `--collect-submodules websockets`，但依赖仍需在构建环境安装。
- 本次修改未提交。
