# 凿壁 ChiselWall

> **知识留本机 · 中心只路由**  
> **Knowledge stays on your machine. The relay only routes.**

[中文](#中文) | [English](#english)

---

## 中文

### 这是什么

凿壁（ChiselWall，项目代号「破壁计划」）是一个面向团队/部门的**分布式知识共享工具**：每个人的笔记、文档、代码片段仍然留在自己的电脑上；中心端（relay）只负责在线状态、问题广播和结果回传，**不存储任何知识内容**。

它解决的是"知识在同事电脑里、但彼此不知道"的信息差问题：不用把文件上传到网盘，也不用把笔记迁到中心，就能向在线同事"借光"。

### 核心特性

- **随手记**：挂载本地目录/文件，支持思源（.sy）、Obsidian、Markdown、TXT、DOCX、XLSX、PDF、CSV 等；只建索引，不复制、不改动原文件；支持排除规则（目录/glob/单文件）与增量索引。
- **随手问**：向在线同事广播问题，聚合各自命中的知识；**会话列表/历史持久化**，首问广播，后续追问基于上一轮结果、不重复广播。
- **搜索结果先看**：右侧独立结果栏展示命中原文、来源同事、耗时与片段；支持预览全文、打开所在位置、直接打开本地文件、远端预览。
- **AI 综合**：可选。提问框旁勾选「AI 综合」自动生成；不勾选则先看结果，再点会话级「AI 综合」按钮。回答带引用编号。
- **混合检索**：SQLite FTS5 + jieba 中文分词 + 向量检索（bge-small-zh）+ RRF 融合；弱相关结果单独折叠，不进入 AI 上下文。
- **可选重排（rerank）**：HTTP（Cohere/Jina/TEI 兼容）或本地 ONNX；带分数缓存、空闲自动卸载。
- **可选查询改写**：用已配置的 LLM 把问题改写成 1–3 条检索式；默认关，提示词可在设置页自定义。
- **隐私边界**：中心只路由；只有在线节点参与检索；排除规则为"拒绝并集"；不支持的格式会进入失败列表，不会静默丢失。

### 架构

```text
┌─────────────┐        WebSocket         ┌─────────────┐
│   Node A    │  hello/presence/query    │    Relay    │
│ local index │ ◄──────────────────────► │  (routing)  │
│ local files │      answer/fetch        │ no content  │
└─────────────┘                          └──────┬──────┘
                                                │
┌─────────────┐        WebSocket               │
│   Node B    │ ◄──────────────────────────────┘
│ local index │
└─────────────┘
```

- 每个节点：本地索引 + 本地检索 + 可选向量/rerank/LLM；
- Relay：在线状态、提问组、广播、限流、结果回传、远端预览转发；
- 知识内容不经过中心落盘。

### 快速开始

#### 从源码运行

要求：Python 3.12+，Windows 或 Linux。

```bash
git clone <your-repo-url> chiselwall
cd chiselwall
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux:   source .venv/bin/activate
pip install -r requirements.txt
python run.py
```

默认地址：`http://127.0.0.1:18371`。首次启动会在控制台打印 API token，并自动打开浏览器。

常用参数：

```bash
python run.py --port 18371 --no-browser
python run.py --relay-url ws://<relay-ip>:18760/ws --relay-nick 我的昵称 --relay-token <token>
```

#### 免安装包（Windows）

1. 解压 `chiselwall-0.7.11-win64.zip`；
2. 双击 `启动节点.cmd`；
3. 浏览器打开后粘贴 token；
4. 设置 → Relay 连接，填地址/昵称/token；
5. 随手记 → 挂载笔记目录；设置 → 语义搜索（可选）。

Relay 部署：

```powershell
chiselwall-relay.exe --init
chiselwall-relay.exe --config relay.json admin invite 张三 --group 全员组 --url ws://<内网IP>:18760/ws
chiselwall-relay.exe --config relay.json --host 0.0.0.0 --port 18760
```

### 使用方式

1. **随手记**：添加挂载目录，等待索引完成；可配置排除规则。
2. **随手问**：新建会话 → 选择提问组 → 广播提问；结果先出现在右侧，按需 AI 综合；同一会话继续输入即追问。
3. **AI 会话**：所有会话在随手问左侧；可切换/改名/删除，历史保存在本地 SQLite。
4. **语义搜索**（可选）：设置 → 语义搜索 → 启用 → 重建向量索引；可随时关闭回退关键词检索。
5. **重排/查询改写**（可选）：设置页按需开启；默认关闭。

### 配置与数据

- 配置目录：`~/.chiselwall`（Windows：`%USERPROFILE%\.chiselwall`）
  - `config.json`：本地节点配置
  - `ai.json`：OpenAI 兼容 LLM 配置
  - `vector.json`：向量检索配置
  - `rerank.json`：重排配置
  - `query.json`：查询改写配置与提示词
  - `relay.json`（relay 端）：用户/token/提问组
- 索引库：`chiselwall.db`（SQLite，含 FTS5 / 向量 / 会话表）
- 所有知识文件保持在原位置，不复制到中心。

### 构建

```powershell
# Windows
powershell -ExecutionPolicy Bypass -File packaging/build_windows.ps1
```

```bash
# Linux
bash packaging/build_linux.sh
```

- 构建前确认已准备 `models/bge-small-zh-v1.5`（向量模型，约 23MB）；
- 本地 rerank 大模型（bge-reranker）默认不打包，放到 `models/rerank/<name>/` 即可被设置页识别；
- 打包前先停止正在运行的 `chiselwall-node.exe`，否则 `dist` 会被锁。

### 测试

```bash
python tests/make_fixtures.py
python -m unittest discover -s tests -p "test_*.py"
```

当前：**287 项测试通过**（Windows）。评测脚本：

```bash
python tests/eval_retrieval.py --db ~/.chiselwall/chiselwall.db
python tests/eval_ab.py --golden tests/fixtures/golden/queries_v3.json
```

### 目录

```text
src/
  api/        FastAPI 接口
  node/       本地 agent、relay 客户端、随手问会话
  relay/      中心 relay
  indexer/    连接器/分块/索引/FTS5
  searcher/   混合检索
  ai/         LLM、RAG、embedding、vector、rerank、查询改写、会话存储
  ui/         Vue3 + 原生 HTML/CSS/JS（无构建工具）
packaging/    PyInstaller 构建脚本
docs/         设计/调研/验收/规划
tests/        单元测试与评测
models/       本地向量/重排模型（不提交 Git）
```

### 文档

- 设计基线：[docs/design/design-v1.md](docs/design/design-v1.md)
- 路线图：[docs/design/roadmap-v2.md](docs/design/roadmap-v2.md)
- 检索质量：[docs/design/retrieval-quality.md](docs/design/retrieval-quality.md)
- 重排：[docs/design/p1-rerank-design.md](docs/design/p1-rerank-design.md)
- 查询改写：[docs/design/p2-query-rewrite.md](docs/design/p2-query-rewrite.md)
- 向量后端：[docs/design/p3-vector-backend.md](docs/design/p3-vector-backend.md)
- UI 设计系统：[docs/design/ui-refresh.md](docs/design/ui-refresh.md)
- 验收报告：[docs/review/](docs/review/)
- 未完成事项：[docs/planning/open-items.md](docs/planning/open-items.md)

### 已知限制

- Linux 真机打包/冒烟、同事 Windows 机器内测尚未完成；
- 2 万块向量规模未压测（当前 2030 块约 81s，建索引指标可能不达标）；
- 内网 `/embeddings`、真实 HTTP rerank 服务未接真实端点；
- 随手问追问固定沿用上一轮结果，暂无"重新广播"按钮；
- AI 输出无流式，长上下文受预算截断；
- relay 目前 ws:// + token，生产建议放在 TLS 反向代理后；
- 许可证待定（内部项目）。

### License

内部项目，License 待定。使用/分发前请与项目维护者确认。

---

## English

### What is ChiselWall?

ChiselWall (project codename **Break the Wall**) is a **distributed knowledge-sharing tool** for teams. Everyone's notes and documents stay on their own machines. A lightweight relay only handles presence, question broadcast, and result routing — **it never stores knowledge content**.

It closes the "knowledge is on a colleague's laptop, but nobody knows" gap without uploading files to a central drive or migrating notes into a SaaS silo.

### Key features

- **Local notes**: mount local folders/files; supports SiYuan (.sy), Obsidian, Markdown, TXT, DOCX, XLSX, PDF, CSV; index-only, never copies or modifies originals; exclude rules and incremental indexing.
- **Ask colleagues**: broadcast a question to online peers, aggregate their local matches. **Persistent chat-style sessions**: the first question broadcasts; follow-ups reuse the previous result set without re-broadcasting.
- **Results first**: a dedicated right-hand panel shows matched snippets, source peers, elapsed time, route, and local/remote preview/open actions.
- **AI synthesis**: optional. Check **AI synthesis** next to the ask button to generate automatically; otherwise inspect results first and press the session-level **AI synthesis** button. Answers include citations.
- **Hybrid retrieval**: SQLite FTS5 + jieba + vector search (bge-small-zh) + RRF; weak hits are folded and excluded from AI context.
- **Optional rerank**: HTTP (Cohere/Jina/TEI compatible) or local ONNX; score cache and idle unload.
- **Optional query rewriting**: rewrite a question into 1–3 retrieval queries with your configured LLM; off by default, prompt editable in settings.
- **Privacy by design**: the relay routes only; only online nodes are searched; exclusions are deny-union; unsupported files go to a visible failure list.

### Architecture

```text
Node A (local index/files)  <-- WebSocket -->  Relay (routing only)
Node B (local index/files)  <-- WebSocket -->  Relay
```

The relay handles presence, groups, broadcast, rate limiting, answer routing, and remote-preview forwarding. Knowledge content never lands on the relay.

### Quick start

#### From source

Requires Python 3.12+ (Windows or Linux).

```bash
git clone <your-repo-url> chiselwall
cd chiselwall
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux:   source .venv/bin/activate
pip install -r requirements.txt
python run.py
```

Open `http://127.0.0.1:18371`; the API token is printed on first start and the browser opens automatically.

Useful flags:

```bash
python run.py --port 18371 --no-browser
python run.py --relay-url ws://<relay-ip>:18760/ws --relay-nick <nick> --relay-token <token>
```

#### Windows package

1. Unzip `chiselwall-0.7.11-win64.zip`;
2. Run `启动节点.cmd`;
3. Paste the token in the browser;
4. Settings → Relay: set URL/nick/token;
5. Notes → mount a folder; Settings → Semantic search (optional).

Relay:

```powershell
chiselwall-relay.exe --init
chiselwall-relay.exe --config relay.json admin invite <name> --group 全员组 --url ws://<lan-ip>:18760/ws
chiselwall-relay.exe --config relay.json --host 0.0.0.0 --port 18760
```

### Configuration

- Config directory: `~/.chiselwall` (Windows: `%USERPROFILE%\.chiselwall`)
  - `config.json` — local node
  - `ai.json` — OpenAI-compatible LLM
  - `vector.json` — vector search
  - `rerank.json` — reranker
  - `query.json` — query rewriting + prompt
  - `relay.json` (relay side) — users/tokens/groups
- Index DB: `chiselwall.db` (SQLite; FTS5, vectors, sessions).
- Knowledge files stay where they are.

### Build

```powershell
# Windows
powershell -ExecutionPolicy Bypass -File packaging/build_windows.ps1
```

```bash
# Linux
bash packaging/build_linux.sh
```

- Ensure `models/bge-small-zh-v1.5` exists (vector model, ~23 MB).
- Local rerank models are not bundled by default; put them under `models/rerank/<name>/`.
- Stop any running `chiselwall-node.exe` before packaging (otherwise `dist` is locked).

### Tests

```bash
python tests/make_fixtures.py
python -m unittest discover -s tests -p "test_*.py"
```

Currently **287 tests pass** on Windows. Evaluation:

```bash
python tests/eval_retrieval.py --db ~/.chiselwall/chiselwall.db
python tests/eval_ab.py --golden tests/fixtures/golden/queries_v3.json
```

### Known limitations

- Linux packaging/smoke test and a second Windows machine install test are still pending.
- 20k-chunk scale is not benchmarked (2030 chunks ≈ 81s; build-time target may be missed).
- Real intranet `/embeddings` and HTTP rerank endpoints are not verified.
- Follow-ups reuse the previous result set; no "re-broadcast" button yet.
- AI output is not streamed; long contexts are truncated by budget.
- Relay uses ws:// + token; put it behind TLS in production.
- License: TBD (internal project).

### License

Internal project; license TBD. Contact the maintainers before redistribution.
