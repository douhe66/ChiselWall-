# M6 打包分发 · 进展报告（Windows 已可交付）

> 版本：v0.6.4　范围：设计 §13 M6（打包、安装/运维文档、内测）
> 状态：Windows 免安装包完成并自测通过；Linux 脚本就绪（真机待测）；同事机器内测待做。

## 1. 产物

| 产物 | 路径 | 体积 |
|---|---|---|
| 节点（免安装目录版） | dist/chiselwall-node/chiselwall-node.exe | 124.5MB |
| relay（中心） | dist/chiselwall-relay/chiselwall-relay.exe | 34.1MB |
| 分发包 | dist/chiselwall-0.6.4-win64.zip | 75.5MB |
| 使用说明 | dist/使用说明.txt | — |
| 启动脚本 | dist/启动节点.cmd、dist/启动relay.cmd | — |
| Linux 打包脚本 | packaging/build_linux.sh | — |

## 2. 自测结果（本机 Windows）

| 项目 | 结果 |
|---|---|
| 启动 | 双击/命令行启动到 /api/health 可访问：**约 0.85s**（含索引加载） |
| 空闲内存 | RSS **约 133MB**（31 篇文档索引后；红线 ≤200MB） |
| UI | 首页/静态资源正常（版本 0.6.4）；启动后自动打开浏览器 |
| 索引与检索 | 挂载目录 → 索引 31 篇 → 中文搜索命中正常（jieba 打包含词典） |
| 打开/预览 | 本地文件预览、打开链路沿用 M2/M3 实现 |
| relay 打包 | --init 生成配置、admin invite 发邀请、/health 正常（online=1） |
| 节点↔relay | 打包节点连打包 relay：connected=true，在线名单正常 |
| 异常用例 | 扫描件/加密 PDF/损坏 docx 按预期进入失败列表（非打包问题） |

## 3. 使用方式（管理员）

```powershell
# 1) 初始化 relay 配置
chiselwall-relay.exe --init
# 2) 给同事发邀请（token 自动生成）
chiselwall-relay.exe --config relay.json admin invite 张三 --group 全员组 --url ws://<内网IP>:18760/ws
# 3) 启动 relay
chiselwall-relay.exe --config relay.json --host 0.0.0.0 --port 18760
```

同事：解压 → 双击「启动节点.cmd」→ 浏览器打开 → 粘贴 token → 挂载笔记 → 设置里填 relay 邀请信息。

## 3.1 向量检索打包集成（v0.7.2）

| 项目 | 结果 |
|---|---|
| 包内新增 | onnxruntime 1.20.1、sqlite-vec 0.1.9、tokenizers、bge-small-zh 向量模型（23.4MB） |
| 节点包体积 | 124.5MB → **214.3MB**；分发包 zip **122MB** |
| 冻结版向量重建 | 31 篇 / 2030 块，**81.3 秒**（≈40ms/块），成功 |
| 冻结版检索 | 「凿壁测试词」→ hybrid 命中，正常 |
| 冻结版内存 | 向量重建后 RSS **219.5MB**（约 200MB 基线 + 向量运行时） |

打包踩坑与修复：
1. **frozen 下 sqlite3 报 `unable to open database file`**：`ORDER BY` 触发临时文件，打包环境临时目录异常；索引库统一 `PRAGMA temp_store=MEMORY` 解决；
2. **ONNX 内存暴涨（942MB）**：embedding session 关闭 CPU arena + 批量 8 + 单条截断 1500 字 → 降到 219MB；
3. PyInstaller 需 `--collect-all sqlite_vec --collect-all onnxruntime --collect-all tokenizers`，模型用 `--add-data "build/models-bundle/models;models"`；
4. 打包前必须停掉正在运行的 chiselwall-node.exe，否则 `dist` 目录被锁、构建会失败。

## 3.2 最新包（v0.7.11，含 M8）

| 项目 | 结果 |
|---|---|
| 版本 | 0.7.11（P0.5/P1/P2/P3 + 随手问会话） |
| 节点包 | 214.3MB（含向量依赖/模型） |
| relay 包 | 34.6MB |
| 分发包 zip | **122.4MB**（`dist/chiselwall-0.7.11-win64.zip`） |
| 冻结版冒烟 | health=0.7.11；vector backend=sqlite-vec；首页含「AI 综合」；随手问会话代码已打包 |

包内新增随手问会话：左侧会话列表/历史持久化、首问广播、后续追问不重播、AI 综合勾选、右侧可调结果栏。

## 4. 已知限制 / 待办

- **Linux**：build_linux.sh 已就绪，需在 Linux 机器上跑一次冒烟（打包/启动/搜索）；
- **同事机器内测**：需在另一台 Windows 上实测（可能会遇到 SmartScreen 提示，需点「更多信息→仍要运行」）；
- **首启引导**：当前为「控制台显示 token + 浏览器自动打开 + 页面 token 弹窗」；可后续做更完整的首启向导；
- **单文件 exe**：当前为免安装文件夹版（推荐）；如需单文件可用 --onefile 另打；
- **签名**：未做代码签名，内网分发可接受。

## 5. 下一步

1. Linux 冒烟 + 同事机器内测（收集问题清单）；
2. 按 roadmap-v2 进入 **M7 向量混合检索**（内网 /embeddings 优先，本地 ONNX 备选）；
3. 之后 **M8 多轮追问 + AI 会话**。
