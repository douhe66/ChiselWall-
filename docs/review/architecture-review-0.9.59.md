# ChiselWall 架构与代码评审报告（v0.9.59）

> 评审日期：2026-11-17 ｜ 评审对象：`v0.9.59`（HEAD `bb15aea`）｜ 性质：**只读审查，未修改任何源码**
> 依据：`需求.txt`、`docs/design/design-v1.md`、`docs/design/roadmap-v2.md`、`docs/design/m4-relay-protocol.md`、`docs/planning/open-items.md`、README
> 用途：本文件是**可勾选的整改清单**。每条问题一个 `R-xx` 编号，带位置、现状、影响、建议与验收口径；维护者修复后勾选，本文件即为整改台账。

## 0. 一句话结论

方案主干成立（中心只路由、本地索引、广播检索、提问端聚合 + RAG），工程完成度高于一般内部项目；但**「分布式」只做到了消息转发，没做到权限与信任闭环**，**「知识不出本机」在实现里被 AI 上下文需求放宽**，且最近一轮 `ResearchAgent` 演进脱离了设计基线。建议**不推翻重写**，按 §5.1 的 8 条 P0 收口后再进入内测。

## 1. 两个维度的判断

### 1.1 方案设计

**成立的部分**

1. relay 语义选型与需求严格对齐：无持久化、at-most-once、只投递给在线连接，中心确实不落任何知识内容；
2. 放弃 NATS 改自建 WebSocket relay 的取舍（单包分发、免运维）是合理的，协议也确实只用了很小的一个子集；
3. 隐私防线是多点、可复查的：排除规则 deny-union、三态共享（shared/private/hidden）、挂载暂停、远端预览白名单 + 每次复审共享状态 + hash 版本校验 + 读取时再复核排除（红线⑤）；
4. 检索分层思路正确：FTS5 + jieba 主索引 → 短语 → 降级 → trigram/LIKE 兜底，strong/weak 分层避免弱相关污染 AI 上下文；
5. 可选能力全部「失败回退、默认关闭」（向量、rerank、查询计划、AI），不会因缺依赖导致主流程不可用。

**不成立 / 需要补的部分**

1. **信任模型没有闭环**：分组在协议里是「广播域」，在 fetch 里又被当权限判断；relay 用客户端自报的 groups 路由，从不校验提问者是否属于该组。answer 消息无身份绑定、无限流。
2. **隐私承诺与实现不一致**：设计 §7 是「约 300 字片段 + 点击后 fetch」，实现里 answer 主动附带约 12k 字的 `ai_text`，AI 综合还会自动远端取全文。
3. **资源保护的承诺只落地一半**：`answer_budget_ms` 配置后从未使用；自建 relay 丢了 NATS 的慢消费者切断与背压，没有等价实现；`sweep()` 定义了但从未调用。
4. **最新一轮 AI 演进脱离设计**：UI 的「AI 综合」实际走 `ResearchAgent`（最多 6 次 LLM、每轮重新广播），而 `design-v1.md` §10/§15.5 与 `roadmap-v2.md` 仍是「一次提问全局一次 LLM」的单轮 RAG；该能力只出现在 `docs/review/release-0.9.56.md` 里。

### 1.2 代码框架

**好的部分**：依赖方向基本单向（`connectors → indexer → searcher/retrieval → ai → node → api/ui`）；`RelayHub`、`IndexDB`、纯函数层被刻意做成可单测；注释里大量引用设计条款，可读性高；357 项后端测试与 golden 评测不是摆设。

**结构性问题**：`NodeAgent` 是 1904 行的上帝对象（挂载/排除/分组/relay 生命周期/AI 配置/会话/research 编排/向量/重排/Windows 注册表打开文件全在一起），API 层直接 `agent.*` 没有 service 接口；`SearchHit.score` 在不同阶段分别是 bm25 / RRF / 相似度 / rerank 分，四套融合公式互相覆盖；工程化缺位（无 pyproject、CI、ruff、mypy、依赖锁定，requirements 漏声明可选依赖）。

## 2. 评审范围与方法

| 项 | 内容 |
|---|---|
| 通读文档 | 需求、README、design-v1、roadmap-v2、m4-relay-protocol、open-items、ui-refresh、retrieval-quality、sharing-and-mounts、ai-* 等 |
| 逐文件读源码 | `src/relay/{server,admin}.py`、`src/node/{bus,agent}.py`、`src/api/server.py`、`src/retrieval/{kernel,research,session}.py`、`src/ai/{llm,rag}.py`、`src/searcher/search.py`（主路径）、`src/indexer/{db,indexer,watcher}.py`（关键段）、`src/connectors/base.py`、`src/ui/app.js`（渲染与轮询）、`tests/eval_*.py` |
| 独立复核 | 两路子代理分别逐文件复核「索引器/连接器」与「检索/AI/研究式追问」，其结论由主审抽查关键行号后采纳 |
| 实测 | 按 README 新建 venv 安装 `requirements.txt` 后运行 `python -m unittest discover -s tests -p "test_*.py"` |
| 实测结果 | **Ran 357 tests，350 通过，7 error**，7 项全部为 `ModuleNotFoundError: No module named 'sqlite_vec'`（见 R-08） |
| 局限 | 未做 Linux 真机、真实内网 embedding/rerank、2 万块规模压测、浏览器端自动化；相关结论标注「未验证」 |

## 3. 需求覆盖对照

| 需求 | 实现 | 评价 |
|---|---|---|
| 分布式：中心不存知识 | relay 只有在线表/组注册表，转发完即弃 | ✅ 成立，这条守住了 |
| 系统兼容：Windows + Linux | 双平台代码路径 + PyInstaller 脚本；Windows 打包已由维护者完成 | ⚠️ Linux 真机与打包仍未验证（open-items P0） |
| 笔记兼容性 | 目录/Obsidian/思源 `.sy` 自写解析/元宝；Notion、有道、链接预留 | ✅ 超预期；但 Connector 协议不完整（R-17） |
| 随手记 | 挂载、排除、三态共享、暂停恢复、预览、打开位置、笔记分组 | ✅ 超出需求 |
| 随手问 | 组广播、deadline 聚合、会话/追问、在线人数、增量展示 | ✅ 主干成立；鉴权有洞（R-01/R-02） |
| AI 入口 | OpenAI 兼容 + 自定义网关 + 向量 + 重排 + 查询计划 + 研究式追问 | ✅ 能力完整；⚠️ 偏离设计且预算失控（R-12/R-22） |
| 资源保护（不占满同事电脑） | 限流、消息上限、索引让路、deadline 设计值 | ⚠️ 多项只在文档（R-09/R-10） |
| 预留接口（链接/Notion/有道） | §16 清单齐全，mount kind 与 Connector 有铺垫 | ✅ 但「插拔」成本被低估（R-17） |

## 4. 建议保留、不建议改动的设计

1. 中心只路由、答案不落盘——这是产品差异点，任何"为提速加缓存"的方案都不应破坏它；
2. 远端预览白名单（`_allowed`，10 分钟 + 每次复审共享状态 + 分组 + hash 版本）——是全仓最扎实的隐私实现；
3. 三态共享 + 挂载暂停 + 排除规则 deny-union 三套过滤同时生效；
4. 本地检索的 tier（strong/weak）分层与「weak 默认折叠、不进 AI 上下文」；
5. 所有可选能力「默认关、失败回退」，AI 未配置时退化到纯搜索；
6. `RelayHub` / `IndexDB` 这类纯逻辑与 IO 分离、可直接单测的写法；
7. 设计 → 里程碑 → 验收报告 → open-items 的文档闭环，请继续保持（本报告即挂在这条链上）。

## 5. 整改清单（勾选使用）

**勾选约定**：每条问题下有两行勾选框——`已修复` 表示代码改动完成，`已回归` 表示有测试/手工验证证据。修复时请在同一行末尾补版本号或 PR，例如 `- [x] 已修复（v0.9.60）`。全部 P0 勾完之前不建议对同事开放内测。

### 5.1 P0 — 发布/内测前必须完成

#### R-01 relay 分组鉴权缺失

- **位置**：`src/relay/server.py:205`（`route_query`）、`:360`（`query` 取客户端自报 groups）；`src/node/bus.py:719`（fetch 用 doc_groups 当权限）
- **现状**：`route_query` 只判断「目标节点是否属于请求里声明的组」，不校验**提问者**是否属于该组；hello 时自报的 groups 仅用于登记，路由时被完全忽略。分组在协议文档里是「广播域」，在 fetch 里又被当 ACL 使用。
- **影响**：任何持 token 的人都可向任意组广播，跨组拿到本不该看的知识；组语义在两处代码里自相矛盾，后续无法演进成真正的权限。
- **建议**：`route_query` 改为以服务端登记的 groups 与请求 groups 取交集为准（提问者不在的组直接拒绝或忽略，并回 error）；若短期只把组当打扰控制，则删掉 fetch 里基于 doc_groups 的权限判断，避免给出虚假的权限承诺。
- **验收**：新增单测——用户 A 只属「平台组」，向「数据库组」提问时 `query_ack.targets == 0` 且收到 error；A 向本组提问仍正常。
- [ ] 已修复　- [ ] 已回归（版本/PR：________）

#### R-02 answer 无身份绑定、无限流、无结构校验

- **位置**：`src/relay/server.py:378`（`answer` 原样转发）；`src/node/bus.py:112`（`note_answer` 无上限）、`:168`（聚合）
- **现状**：`answer` 里的 `node` 由应答方自报，relay 不覆写、不校验、不限流；`hits` 条数与总大小也没有上限。
- **影响**：可冒充他人来源（破坏「来源同事」这一信任展示）；单个节点可刷 answer 把提问端聚合 CPU/内存打满（近重复去重是 O(n²) 字符串比较）。
- **建议**：relay 转发前覆写 `node = 认证用户`；按 `(qid, node)` 去重；为 answer 增加每用户令牌桶；`hits` 限条（如 ≤20）与总字节上限，超限截断而非整条丢弃。
- **验收**：单测覆盖「伪造 node 被覆写」「同一 node 对同一 qid 只计一次」「answer 洪泛被限流」。
- [ ] 已修复　- [ ] 已回归（版本/PR：________）

#### R-03 隐私边界：answer 主动外发约 12k 字上下文

- **位置**：`src/node/bus.py:664`（`select_local_window(..., max_chars=12000)`）、`:678`（随 answer 发出）；设计基线 `docs/design/design-v1.md:122`
- **现状**：每次应答都会为每条命中附带 `ai_text`（命中块及相邻块，最多 12k 字）；AI 综合时 `src/node/agent.py:980` 还会主动向对端 fetch 全文。
- **影响**：与设计 §7「命中片段约 300 字 + 出处，点击才取全文」不一致；一次提问即可把多位同事笔记的大段原文传到提问者机器与 relay 内存中，产品最核心的「知识不出本机」承诺被明显放宽。
- **建议**：answer 默认只带片段（≤400 字）；把「AI 需要更完整上下文」显式化为 `query.allow_context` 协商或由提问者在 AI 综合时逐步 fetch；设置页提供「AI 全文读取」总开关（open-items 已列此项）。
- **验收**：关闭开关时 answer 报文不含 `ai_text`，单条 hit 的体积有硬上限；开启时日志可审计（谁、哪篇、多长）。
- [ ] 已修复　- [ ] 已回归（版本/PR：________）

#### R-04 超长 answer 撞 relay 256KB 上限后被静默丢弃

- **位置**：`src/relay/server.py:26`（`MAX_MESSAGE_BYTES = 256KB`）、`:304`（超限只回 error）；`src/node/bus.py:667`（单条可到 12k 字，8 条约 96k 字 ≈ 288KB）
- **现状**：relay 收到超限消息时给**应答方**回 `message_too_large` 后不转发；提问端只表现为「这个节点没回」，无任何错误可见。
- **影响**：长文档命中时整条答案丢失，且丢失原因不可观测（会误导用户判断"某同事没在线/没资料"）。
- **建议**：发送前按 UTF-8 字节计算总长并截断 `ai_text`（保留片段与出处）；relay 对超限 answer 回一个带 qid 的 error，让提问端能显示「某节点结果过大已丢弃」。
- **验收**：构造 8 条长文档命中的端到端测试，提问端能看到 8 条来源（或明确的丢弃提示），无静默丢失。
- [ ] 已修复　- [ ] 已回归（版本/PR：________）

#### R-05 在线状态不清理 + 吊销 token 不踢线

- **位置**：`src/relay/server.py:139`（`sweep()` 定义了但全仓无调用）、`:88`（`heartbeat_timeout=45s`）；`src/relay/admin.py:89`（`revoke` 只改配置文件）
- **现状**：连接靠 WebSocket 断开事件清理，半开连接（拔网线/休眠）不会被心跳超时清理；被吊销的用户只要不断线就继续可用。
- **影响**：在线名单出现「鬼在线」，「已响应 x/n」失真；吊销在安全事件下无效。
- **建议**：启动周期性 `sweep` 任务（超时即注销并广播）；`revoke` 后主动关闭该用户当前连接（relay 侧可提供管理通道或重启时清理）。
- **验收**：模拟半开连接 45s 后在线列表移除；revoke 后该连接被断开且重连被拒。
- [ ] 已修复　- [ ] 已回归（版本/PR：________）

#### R-06 单文档重索引无事务边界，崩溃后文档静默消失

- **位置**：`src/indexer/indexer.py:180`（先 upsert 为 pending）、`:200`（写 chunk）、`:207`（最后置 indexed）；`src/indexer/db.py:495`（先删旧块并 commit）、`:515`（每 200 块 commit）；`src/indexer/db.py:296` 的 `tx()` **全仓 0 调用**
- **现状**：一次文档重索引跨多次提交；进程在中间被 kill / 崩溃后，该文档停留在 `pending` + 空或半截 chunk，而检索只查 `status='indexed'`。
- **影响**：升级/断电/任务管理器结束进程后，用户会看到「文档还在，但搜不到」，且没有任何失败提示。
- **建议**：单文档索引用 `db.tx()` 包成一个事务（chunk 分批提交可保留，但状态切换与首尾 commit 要原子）；`NodeAgent.stop()` 里 join 索引 worker 再 close db。
- **验收**：新增测试——在写 chunk 中途抛异常/强制中断后，文档仍是旧的 indexed 内容（或明确的 error 状态），不会变成 pending。
- [ ] 已修复　- [ ] 已回归（版本/PR：________）

#### R-07 LIKE 查询未转义

- **位置**：`src/indexer/db.py:452`（`delete_docs_under` 用 `rel_path LIKE prefix + "/%"`）；同类前缀匹配还出现在 `doc_group_names`：`src/indexer/db.py:559`（`d.rel_path LIKE gm.rel_path || '/%'`）
- **现状**：`_`、`%` 在 LIKE 里是通配符且未加 ESCAPE。
- **影响**：两处后果不同——删除路径上，目录名 `project_a` 的 `project_a/%` 会匹配到 `projectxa/...`，可能误删兄弟目录的索引记录（用户侧表现为文档突然搜不到）；分组路径上，成员前缀含 `_` 时会把兄弟目录的文档算进该组，导致「整组排除」误伤或分组统计失真。
- **建议**：统一 `LIKE ? ESCAPE '\'` 并对 `\`、`%`、`_` 转义；抽一个 `like_prefix()` 工具函数，所有前缀匹配都走它。
- **验收**：单测覆盖含 `_`、`%` 的目录名，断言只删目标前缀下的文档。
- [ ] 已修复　- [ ] 已回归（版本/PR：________）

#### R-08 依赖声明不完整，按 README 干净安装后测试不绿

- **位置**：`requirements.txt`（缺 `sqlite-vec`、`onnxruntime`、`numpy`）；`README.md` 测试段（宣称 357 项通过）；相关代码 `src/ai/vector_backend.py:94` → `src/ai/vectorstore.py:25`（构造即 `import sqlite_vec`）
- **现状**：实测按 README 建 venv 装完依赖后，`test_vector`/`test_vector_backend` 共 7 项 error，全部是 `No module named 'sqlite_vec'`；打包脚本却用 `--collect-all sqlite_vec onnxruntime tokenizers`（`chiselwall-node.spec:12`、`packaging/build_windows.ps1:18`），说明这些是隐性必需依赖。
- **影响**：新同事照文档装完测试不绿、向量功能不可用；打包环境与开发环境不一致，问题只在打包后才暴露。
- **建议**：`pyproject.toml` 声明 extras（`vector = ["sqlite-vec", "onnxruntime", "numpy"]`），requirements 拆 `requirements-core.txt` + `requirements-optional.txt`；README 与 CI 明确「最小安装」和「完整安装」两种；测试对可选依赖做 `skipUnless` 而不是 error。
- **验收**：全新环境 `pip install -r requirements-core.txt && python -m unittest` 全绿（vector 相关测试 skip）；完整安装后全绿、vector 测试真实执行。
- [ ] 已修复　- [ ] 已回归（版本/PR：________）

### 5.2 P1 — 内测期间随版本修复

#### R-09 节点侧 800ms 应答预算未实现

- **位置**：`src/node/bus.py:37`（`answer_budget_ms`）、`:410`（`_answer_deadline_s` 赋值后全仓无使用）；`src/node/agent.py:63`、`:1864` 一路透传
- **现状**：节点检索 + `ai_text` 组装没有任何时间盒，只有提问端 5s 收口。
- **影响**：大文档/慢磁盘的节点会做完整工作但答案被丢弃，白耗 CPU；与设计 §12「到点回部分结果，绝不排队」不符。
- **建议**：`_handle_query` 用 `asyncio.timeout()` 包住检索与上下文组装，超时回「已命中但上下文精简」的结果或空 hits。
- **验收**：注入慢 searcher（sleep 2s），断言在 ≤800ms 内发出 answer。
- [ ] 已修复　- [ ] 已回归（版本/PR：________）

#### R-10 relay 无每连接发送队列/慢消费者处理

- **位置**：`src/relay/server.py:425`（`broadcast_online` 顺序 await 每个 client）、`:228`（`deliver` 直接 `await send`）
- **现状**：所有广播/转发都在当前连接的处理协程里串行 await 目标连接；没有 per-connection 队列，也没有「队列满断开」。
- **影响**：一个卡住的客户端会拖慢其他人的在线广播与答案转发（队头阻塞）；自建 relay 后丢掉了设计 §3 里「NATS 自带慢消费者切断」这一条保障，却没有等价实现。
- **建议**：每个连接一个 `asyncio.Queue` + 独立 writer 任务（队列上限如 256，满则断开并记录）；广播用 `gather` 投递到队列即可。
- **验收**：压测 100 连接、其中一个不读 socket，其余连接的消息延迟不受影响。
- [ ] 已修复　- [ ] 已回归（版本/PR：________）

#### R-11 `research_id` 可绕过提问限流且桶不回收

- **位置**：`src/relay/server.py:216`（新 `research_id` → 新桶，`burst=max(4, burst+2)`）、`:97`（`research_buckets` 永不清理）
- **现状**：每次请求带不同的 `research_id` 就能拿到新的 4 次额度；字典随研究会话无限增长。
- **影响**：限流形同虚设；长期运行内存缓慢泄漏。
- **建议**：研究额度计入主桶（加权扣费），或按 `(user)` 维护固定并发研究数；桶加 TTL 清理。
- **验收**：同用户 10 个不同 research_id 连续提问，被限流；`research_buckets` 在空闲 TTL 后被清空。
- [ ] 已修复　- [ ] 已回归（版本/PR：________）

#### R-12 ResearchAgent 未进入设计基线且预算失控

- **位置**：`src/retrieval/research.py:127`（最多 4 轮，deadline 只在轮首检查）、`:161`/`:170`/`:314`（每次 `client.chat` 默认 120s 超时）、`src/retrieval/kernel.py:40`（每轮 search 都重新广播）；入口 `src/api/server.py:700`；UI `src/ui/app.js:364`
- **现状**：UI 的「AI 综合」实际走研究式多轮 Agent；最坏 4 轮 + 1 次修复 + 1 次强制收口 = 6 次 LLM 调用，每次可塞 24k 字上下文；每轮 search 都是一次全组广播；60s deadline 不覆盖单次 LLM 调用。
- **影响**：一次点击「AI 综合」可能持续十几分钟、并反复打扰全组同事的电脑；与设计 §10/§15.5「全局一次 LLM、低打扰」直接冲突；该能力在任何设计文档中都没有描述（仅 `release-0.9.56.md` 提及）。
- **建议**：①把 research 写进 `design-v1` 或独立设计文档（轮次、预算、广播次数、降级策略）；②总 deadline 下传到每次调用（`asyncio.timeout`）；③每次用户动作最多 1 次额外广播；④加全局并发闸门；⑤若短期无法约束，先在设置页降级为可选。
- **验收**：一次点击最多 2 次广播、总时长有硬上限（如 45s）、超时能返回部分结论；设计文档与实现一致。
- [ ] 已修复　- [ ] 已回归（版本/PR：________）

#### R-13 迁移无版本号，mounts 重建丢列

- **位置**：`src/indexer/db.py:197`（`_migrate` 逐列 `PRAGMA table_info` 探测）、`:222`（重建 `mounts_new` 只复制 4 列）、`:239`（后续回填）
- **现状**：没有 `PRAGMA user_version`；重建表时未复制的列回落到默认值；迁移前无备份。
- **影响**：老库升级后「已暂停的挂载」会静默恢复索引、「敏感忽略位」丢失；SQLite < 3.35 时 `DROP COLUMN` 直接失败。
- **建议**：引入 `user_version` + 顺序迁移脚本；每次迁移前 `VACUUM INTO` 一份 `.bak`；重建表时显式拷贝全部列。
- **验收**：构造 v0.7 老库升级测试，断言 `enabled`/`sensitivity_ignored` 保持原值。
- [ ] 已修复　- [ ] 已回归（版本/PR：________）

#### R-14 排除/删除只清 chunk，敏感摘录与向量残留（红线⑤）

- **位置**：`src/indexer/db.py:462`（`_delete_chunks_locked` 只删 `chunks` + 两张 FTS）、`:119`（`sensitivity_hits` 表，`:123` 的 `excerpt` 存原文摘录）；向量孤儿见 `docs/planning/open-items.md`
- **现状**：文档被排除或删除后，敏感扫描命中的 `excerpt` 原文仍在库中；`vec_chunks` 只增不删（检索 JOIN 会过滤，但占空间、占 KNN 名额）。
- **影响**：与「排除即完全不可见」的承诺存在偏差——本机库里仍留有被排除文档的原文片段。
- **建议**：`clear_chunks` 同时清 `sensitivity_hits`（或标记失效）；删除文档/挂载时同步删 `vec_chunks`；补一条「排除后全库搜不到该文档任何文本」的测试。
- **验收**：排除含敏感词文档后，直接 SQL 查询无该文档 excerpt/向量残留。
- [ ] 已修复　- [ ] 已回归（版本/PR：________）

#### R-15 大文件哈希只采样头部 1MB，可能永久陈旧

- **位置**：`src/indexer/indexer.py:26`（阈值 8MB / 采样 1MB）、`:174`（mtime+size 相同直接 unchanged）、`:176`（疑似变更才算哈希）
- **现状**：>8MB 文件只哈希前 1MB；mtime 未变时连哈希都不算（快通道）。
- **影响**：同长度、改尾部的文件（日志、导出文档常见）会被永久判为 unchanged，检索结果与文件内容长期不一致。
- **建议**：大文件改为「头 1MB + 尾 1MB + 中间定点采样」的组合哈希；或对超大文件按块哈希并与上次比较。
- **验收**：构造 10MB 文件、改尾部后保留 mtime，断言索引被更新。
- [ ] 已修复　- [ ] 已回归（版本/PR：________）

#### R-16 Debouncer 每路径一个线程，首次同步无上限

- **位置**：`src/indexer/watcher.py:45`（每个 key 一个 `threading.Timer`，无数量上限）；对比 `JobQueue` 有界（`:80`）
- **现状**：防抖层没有容量控制；首次全量扫描/大目录移动时可能同时存在上万个 Timer 线程。
- **影响**：索引期内存与线程数尖峰，与设计 §12「峰值内存 ≤1GB」冲突，低配置机器可能卡顿。
- **建议**：改成单调度线程 + 最小堆（或复用 JobQueue 的有界语义），同一 key 只保留最新到期时间。
- **验收**：一次 2 万文件事件后，线程数保持常数（如 <50）。
- [ ] 已修复　- [ ] 已回归（版本/PR：________）

#### R-17 Connector 协议与实际调用不一致

- **位置**：`src/connectors/base.py:98`（`list_docs(self)` 无参数）、`:87`（ABC 未声明 `meta_of`）；实际调用 `src/indexer/indexer.py:122`（`list_docs(under=...)`）、`:90`/`:113`（`meta_of`）
- **现状**：ABC 只是「事实上的鸭子类型」，实现者若严格照 ABC 写，会在运行时 `TypeError`/`AttributeError`；`mounts.kind` 的 CHECK 约束（`db.py:39`）也需迁移才能加新来源。
- **影响**：真实成本被低估——加一个链接连接器不只是「实现一个类」，还要改 indexer 的存在性判定（`abs_path.exists()`）、哈希（`open(path,"rb")`）、迁移 CHECK 约束。
- **建议**：把「枚举 / 变更签名 / 是否存在 / 读取」抽成显式协议方法（如 `iter(under)`、`stamp(rel)`、`exists(rel)`），indexer 只与协议交互；`kind` 改为可扩展的文本列。
- **验收**：新增一个 `LinkConnector` 桩（只实现协议、不改 indexer）能跑通索引与排除流程。
- [ ] 已修复　- [ ] 已回归（版本/PR：________）

#### R-18 打分语义四套并存，rerank 还会截断调用方 topk

- **位置**：`src/searcher/search.py:279`（meta RRF + 覆盖度）、`:319`（向量 RRF）、`src/ai/query_plan.py:80`（计划覆盖度）、`src/node/bus.py:188`（跨节点共识）；`src/node/agent.py:1466`（rerank 后 `out[:cfg.top_k]`）
- **现状**：`SearchHit.score` 在不同阶段分别是 bm25（越小越好）/ RRF（越大越好）/ 相似度 / rerank 分；`/api/search?topk=50` 会被 rerank 的 `top_k` 截到 5。
- **影响**：任何检索调优都无法归因（改一处被另一处覆盖）；API 的 topk 语义不可信；评测数字与线上行为不一致。
- **建议**：抽独立 `ranking` 模块，统一 `RankedHit{score, stage, components}`；融合只在提分阶段做，rerank 只重排不截断（截断交给最终展示层）。
- **验收**：所有入口（`/api/search`、随手问、AI 综合、research）走同一个融合函数；`topk` 在端到端测试中不被中间层改写。
- [ ] 已修复　- [ ] 已回归（版本/PR：________）

#### R-19 前端 markdown 渲染可注入 HTML 属性（XSS面）

- **位置**：`src/ui/app.js:1001`（转义只处理 `& < >`，未处理引号）、`:1023`（链接正则 `https?:[^)\s]+` 允许 `"`）；token 存 `localStorage`（`:119`、`:1636`）
- **现状**：笔记正文/远端片段里的 `[x](https://a"onmouseover="...)` 可闭合属性并注入事件处理器；`v-html` 直接渲染。
- **影响**：任意被索引文档（或恶意节点回传的 snippet）可在提问者页面执行脚本，进而读取 localStorage 里的 API token、调用本机 API。
- **建议**：属性值统一走 `escapeAttr`（含 `" ' ` =`）；链接协议白名单（只 http/https）；token 从不落 localStorage（改内存 + 会话级），或加短有效期。
- **验收**：用含引号的链接/代码块样本做回归用例，渲染结果无新增属性；CSP 下无 inline handler。
- [ ] 已修复　- [ ] 已回归（版本/PR：________）

#### R-20 网关密钥会进入异常文案与日志

- **位置**：`src/ai/llm.py:231`（占位符解析把文件内容拼进 base_url）、`:339`、`:391`（错误信息里带完整 URL）
- **现状**：`ai.json` 的 `api_key` 做了「不写日志」处理，但网关模式下从占位符文件读到的 token 被拼进 URL，连接失败时随异常一起打印/返回。
- **影响**：内网网关 token 泄漏到 UI 错误提示、node.log、用户截图。
- **建议**：错误信息只保留 scheme+host（或只保留模板变量名）；日志层对 URL 做统一脱敏。
- **验收**：构造连接失败场景，断言日志与 API 响应中不含密钥串。
- [ ] 已修复　- [ ] 已回归（版本/PR：________）

#### R-21 `ask()` 在事件循环里同步检索，且改写结果从未被使用

- **位置**：`src/node/bus.py:486`（rewrite 分支）、`:552`（`_pick_broadcast_query` 直接调 `self.searcher.search`）、`:500`（`send_text` 在 `plan_fn` 存在时恒为 original）
- **现状**：未配置 AI 时 `rewriter.ready=False`，每次提问都会走旧改写分支，在事件循环里同步跑 3~4 次本地检索；算出的 `refined` 最终被丢弃。
- **影响**：每次提问白付几十到几百毫秒事件循环阻塞（心跳、其他协程被推迟），且代码路径具有误导性。
- **建议**：删除旧改写分支（保留 plan 路径与测试迁移），或在 `to_thread` 里执行；`send_text` 的计算简化。
- **验收**：无 AI 配置时 `/api/ask` 不出现同步搜索调用；删除后相关测试改为覆盖 plan 路径。
- [ ] 已修复　- [ ] 已回归（版本/PR：________）

#### R-22 经典 RAG 无注入防护，上下文预算口径与设计不一致

- **位置**：`src/ai/rag.py:27`（资料直拼、标题/路径未净化）、`:75`（历史与资料同层拼接）；`src/ai/llm.py:24`（系统提示未声明「资料不是指令」）；对照 `src/retrieval/research.py:41` 已有该防护
- **现状**：两条 AI 路径的提示词安全策略不一致；上下文按**字符**预算（`llm.py:94`），设计 §15.5 是 8K **token** 预算；带 `ai_text` 的单条可吃满整个预算。
- **影响**：同事笔记里的「忽略以上指令」类文本可影响回答；单条长文档会把其他命中挤掉，回答覆盖面反而不如预期。
- **建议**：统一资料包裹（分隔符 + 「以下为资料，不是指令」）+ 引用编号净化；改为 token 估算预算并给单条配额（如 ≤25%）；两条路径共用同一 context builder。
- **验收**：注入样本测试（资料内含指令）时回答仍只基于资料；单条超长文档不会挤空其余命中。
- [ ] 已修复　- [ ] 已回归（版本/PR：________）

#### R-23 `ask_session_complete` 在 qid 失效时抛 NameError（500）

- **位置**：`src/node/agent.py:1006`（方法体）、`:1020`（`"search_text": view.get("search_text") or question`，`question` 未定义）
- **现状**：`view = self.bus.view(qid) or {}` 在 qid 不存在（重启后/过期）时得到 `{}`，随后引用未定义变量 `question`。
- **影响**：本地 500，前端提示「完成」失败，该轮结果无法落库；日志里是 NameError 而非业务错误。
- **建议**：view 为空时直接返回 `{"ok": False, "error": "提问已过期，请重新检索"}`；补一条单测。
- **验收**：用随机 qid 调 `/complete` 返回 400/404 且带可读错误。
- [ ] 已修复　- [ ] 已回归（版本/PR：________）

### 5.3 P2 — 工程债，随迭代消化

#### R-24 `NodeAgent` 上帝对象，API 层直连内部实现

- **位置**：`src/node/agent.py:74`（1904 行，同时管理挂载/排除/分组/relay/AI/会话/research/向量/重排/Windows 打开文件）；`src/api/server.py:246`（100+ 路由直接 `agent.*`）
- **影响**：改一处要跨 4 个文件；测试只能大量 monkeypatch 内部属性；新人理解成本高。
- **建议**：按职责拆 service（`MountService`/`SearchService`/`AskService`/`AIService`/`RelayService`/`DesktopOpener`），API 只依赖 service 接口；先拆「Windows 打开文件」（纯 OS 代码）收益最大、风险最低。
- **验收**：`agent.py` 降到 ~600 行以内，`open_*`/注册表相关迁出到独立模块且单测不变。
- [ ] 已修复　- [ ] 已回归（版本/PR：________）

#### R-25 重复实现与死代码

- **位置**：`TokenBucket` 两份（`src/relay/server.py:29`、`src/node/bus.py:59`）；`_match_jieba`/`_match_or` 无调用（`src/searcher/search.py:453`、`:464`）；`_merge_expanded`/`_rrf_merge` 只被测试调用（`src/node/agent.py:1521`、`:1538`）；旧 `/api/ai/sessions` 与 `ai/sessions.py`（open-items 已列）
- **建议**：抽 `common/ratelimit.py`；删死代码与其测试（测试改测行为）；旧端点物理删除。
- **验收**：`vulture`/`ruff --select F401,F811` 无未使用告警；行数净减少。
- [ ] 已修复　- [ ] 已回归（版本/PR：________）

#### R-26 工程化缺位（CI / 类型 / 依赖锁定）

- **位置**：仓库根无 `pyproject.toml`、`.github/`、`ruff.toml`、`mypy.ini`；`requirements.txt` 只有 `>=`
- **建议**：加 `pyproject.toml`（ruff + mypy 渐进式 + pytest）、GitHub Actions 双平台矩阵（Windows/Linux，最小安装跑单测）、`uv lock` 或 `pip-tools` 锁版本。
- **验收**：PR 上 CI 必过；本地 `ruff check`、`mypy src` 可运行（可分模块放宽）。
- [ ] 已修复　- [ ] 已回归（版本/PR：________）

#### R-27 评测脚本与 golden 数据不可复现

- **位置**：`tests/eval_retrieval.py:41` 读 `item["expect"]`，而 `tests/fixtures/golden/queries_v3.json` 用的是 `expect_title_like`（只有 `tests/eval_ab.py:131` 读它）→ 按 README 的命令跑 queries_v3 恒为 0 分；`:45` 的 nDCG 分母固定为 `dcg([1])`，实际是 DCG@10；期望值是本机私有库标题（如 `"013_%"`）
- **建议**：统一 golden schema（`expect_doc_ids` / `expect_title_like` 二选一并在两个脚本里都支持）；用公开可生成的 fixture 库产出期望；nDCG 用理想排序做分母；评测脚本纳入 CI 的 smoke 运行。
- **验收**：`python tests/eval_retrieval.py --golden tests/fixtures/golden/queries_v3.json` 能产出有意义分数；两人环境结果一致。
- [ ] 已修复　- [ ] 已回归（版本/PR：________）

#### R-28 relay 缺可观测性

- **位置**：`src/relay/server.py:285`（只有 `/health` 返回在线数）；无指标、无请求级日志聚合
- **建议**：暴露 `/metrics`（在线数、query 数、answer 数、限流数、超时数、P95 转发延迟）；日志加 qid/user 结构化字段。
- **验收**：内测期间能从指标判断「广播是否到达」「谁在超时」。
- [ ] 已修复　- [ ] 已回归（版本/PR：________）

#### R-29 文档与版本号漂移

- **位置**：`README.md`（包名仍写 `chiselwall-0.7.11-win64.zip`）；`docs/review/m6-packaging-report.md`（版本停在 v0.6.4）；`design-v1.md` 未记录 ResearchAgent；`.gitignore` 忽略 `*.spec` 但 `chiselwall-node.spec`/`chiselwall-relay.spec` 已入库
- **建议**：发布时同步版本号；把 ResearchAgent 补进设计文档或明确标为实验特性；`*.spec` 从 gitignore 移除。
- **验收**：README/报告版本号与 `APP_VERSION` 一致；设计文档能覆盖 UI 的实际主路径。
- [ ] 已修复　- [ ] 已回归（版本/PR：________）

#### R-30 前端无自动化测试，SSE 为死路径

- **位置**：`tests/` 全为后端；`src/api/server.py:693`、`:718` 的 SSE 端点未被 UI 使用（`app.js` 只走 `/research`）；`src/api/server.py:40` 无断连检测
- **建议**：关键流程（会话、结果栏、打开文件、引用跳转）补 Playwright 冒烟；SSE 端点要么接线要么删除。
- **验收**：CI 里有 3~5 条 UI 冒烟用例；无未使用端点。
- [ ] 已修复　- [ ] 已回归（版本/PR：________）

## 6. 建议实施顺序

| 批次 | 内容 | 理由 | 预估 |
|---|---|---|---|
| A（内测前） | R-01、R-02、R-05 | 分布式核心的安全与在线状态正确性，改动小、风险可控 | 1–2 天 |
| B（内测前） | R-03、R-04、R-08 | 隐私边界与「长文档答案丢失」，直接影响用户信任；依赖声明决定新机器能否跑起来 | 1–2 天 |
| C（内测前） | R-06、R-07、R-13、R-14 | 数据一致性与红线⑤，出问题表现为「文档莫名搜不到/删不干净」 | 2 天 |
| D（内测中） | R-12、R-09、R-10 | 先把 AI 综合的预算与打扰收住，再做节点侧时间盒与 relay 背压 | 2–3 天 |
| E（内测中） | R-17、R-18、R-23、R-19、R-20 | 扩展性/可维护性与安全小项 | 3 天 |
| F（迭代） | R-24 ~ R-30 | 工程债，按 R-24 → R-26 → R-25 → R-27 → R-28/29/30 的顺序 | 持续 |

## 7. 未验证事项（不要在未验证前下结论）

| 项 | 说明 |
|---|---|
| FTS5 中文行为 | 未在真实大库上验证 unicode61/jieba 影子列的实际召回与短语匹配边界 |
| Linux | 真机打包、inotify、`xdg-open`/DBus 路径均未运行 |
| 真实内网 AI | `/embeddings`、Cohere/Jina/TEI 兼容 rerank 只接过桩 |
| 规模 | 2 万块向量、1000 在线节点的 relay 压测均未做（open-items 已列） |
| 浏览器端 | XSS 结论来自代码分析，未做浏览器实测；UI 流程无自动化 |

## 8. 附录：复现与证据

```powershell
# 干净环境复现测试状态（结果：357 ran / 350 ok / 7 error: No module named 'sqlite_vec'）
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"
```

- 评测脚本 schema 不一致的快速验证：`tests/eval_retrieval.py:41` 读 `expect`，`tests/eval_ab.py:131` 读 `expect_title_like`，而 `tests/fixtures/golden/queries_v3.json` 只有后者。
- 相关设计条款：`docs/design/design-v1.md` §3（NATS→自建 relay 取舍）、§7（片段+出处）、§12（资源保护）、§15.5（聚合漏斗/上下文预算）；`docs/design/m4-relay-protocol.md`（消息协议与限流）。

---

**评审签署**：只读审查，未改动源码；本文件由维护者维护勾选状态。
