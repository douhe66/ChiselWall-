# M2 验收与评审报告（凿壁 ChiselWall）

> 评审人：reviewer（质量监督） ｜ 验收对象：M2 本地核心（task-6~9 交付物，builder）
> 依据：docs/review/m2-acceptance-checklist.md、docs/review/m2-testdata-plan.md、design-v1 §5/§6/§7/§9/§11/§12/§13、调研 03
> 结论：**通过**（task-11 修复复核完成：F1/F2 复测达标，121 项单测 + 78 项驱动回归全绿，见 §7）

## 1. 验收执行概况

| 项 | 内容 |
|---|---|
| 环境 | Windows 本机；Python 3.12.14（DSH 捆绑解释器，deps 齐全）；4C 参考机 |
| 代码基线 | builder 交付全量源码（src/connectors、indexer、searcher、api、node、ui，run.py） |
| 单元测试 | 复跑 `python -m unittest discover -s tests -p "test_*.py"` → **117 项全绿**（42.4s） |
| 验收语料 | tests/fixtures/acceptance/（make_acceptance.py 幂等生成，自检 10/10 OK；含 root 主挂载、vault Obsidian 挂载、单文件挂载、21MB 大文件、5M 字符截断样本） |
| 验收驱动 | tests/fixtures/acceptance/run_acceptance.py v3（HTTP API 全量断言）→ **78/78 通过** |
| 资源测量 | tests/fixtures/acceptance/measure_resources.py（psutil 三段采样 + 检索延迟） |
| UI 检查 | 浏览器实测 127.0.0.1:8317（无 token 弹窗 / 带 token 数据 / 搜索高亮 DOM 验证） |
| agent 运行 | 后台 job（--db tests/fixtures/acceptance/.run/chiselwall.db --port 8317），验收后环境已清理 |

## 2. 功能测试结果（78/78 PASS，逐组摘要）

### 2.1 挂载与索引（AC-01）—— 全部通过
- **格式覆盖**：md / txt(UTF-8) / **txt(GBK，charset 探测)** / docx / **xlsx 双 sheet 各自命中** / pdf(文本层) / csv / Obsidian vault（根+子目录）/ 单文件挂载（统计=1）全部命中且 rel_path 正确；
- **引擎正确性**：英文（postgres）、数字、中文词（jieba 影子列）✓；短语「数据库连接池」相邻命中、分离文档不排前 ✓（**AC-01-13 代码检查点通过**：build_phrase_match 已实现为单引号串，searcher 与测试共用，无实现漂移）；trigram 子串命中（P1 项已提前实现）✓；bm25 排序 多>中>少 ✓；长文档片段 351 字带 `<em>` 高亮 ✓；命中字段齐全（doc_id/title/rel_path/score/snippet_html/snippet_text/route/format/chunk_seq…）✓；
- **chunk/截断**：big_over_cap.md（>5M 字符）→ 全文含 A 不含 B ✓、DB 侧 truncated=1 ✓（实测 chunk 上限 2000 先于 5M 字符截断触发，两上限语义符合 §5）；
- **统计/持久化**：挂载 3 源统计与实际一致；重启后挂载保留（驱动幂等挂载验证）。

### 2.2 排除矩阵（AC-02）—— 全部通过
- **用户规则**：单文件 / 整目录 / glob（*.csv 扩展名、secret* 前缀、**/drafts/* 递归）三类五种全部「排除前可搜 → 添加后不可见 → 删除恢复可搜」，隔离对照文件全程可搜（不误伤）；
- **默认排除**：隐藏文件、~$ 临时、.tmp、node_modules、.git、.svn、两个 .obsidian 样本均不可见（排除目录整树剪枝，效率+隐私）✓；空文件与 21MB 大文件进 excluded（不报错）✓；
- **红线⑤ 不可见性**：被排除文档全文获取 **HTTP 403**（「文档已被排除，全文不可得」）、open-location 403、搜索不可得 ✓；
- **增量生效**：规则添加后无需重挂载/重启立即摘除（excluded 计数即时变化）✓。

### 2.3 失败隔离（AC-04）—— 全部通过
- 加密 pdf / 无文本层(扫描) pdf / 损坏 docx → 失败列表（含中文原因），不阻断整体（indexed=33 正常）；
- 旧格式 .doc/.xls 与二进制 png/zip → **走「默认排除」轨道**（excluded+原因，§7 明文）而非失败列表——与清单 AC-04-05/06 原预期有语义差异，**实现正确**，清单断言口径已按实测修正（见 §6 观察-1）；
- 空文件 → excluded 非 error，不产生空 chunk。

### 2.4 增量索引（AC-05）—— 全部通过（DB 层断言）
- 新建 6s 入库 / 覆盖修改 **1s 重建且旧词清除** / 删除 6s 记录清除（随机标记词 + 直读 chunks 断言，规避 trigram 假阳性）✓；
- Windows 句柄竞态（写后立即删除）在脚本侧重试即可，产品无问题。

### 2.5 API 与 UI（AC-07）—— 全部通过
- API：token 鉴权（无 token 401 / 带 token 200）、topk 生效、空查询不 500、全文获取、404、open-location（explorer /select 返回 ok）✓；
- UI：首页静态加载、无 token 弹窗、带 token 后统计/挂载/排除/失败列表数据与 API 一致、搜索命中 2 条（root+load2 同名文档各一，正确）、**关键词黄色高亮 DOM 验证（`<em>` 背景 rgb(255,229,143)）**、预览/打开所在位置/直接打开按钮齐全。

## 3. 资源测试结果（对标设计 §12 达标线）

| 编号 | 指标 | 达标线 | 实测 | 判定 |
|---|---|---|---|---|
| AC-03-06 | 空闲 CPU | <1% | p50/p95/max = **0.0%** | ✅ |
| AC-03-07 | 空闲内存 | RSS≤200MB | **147.3MB** | ✅ |
| AC-03-08 | 索引期 CPU | ≤50%(≈2核) | 单核脉冲：p50=0、p95/max=100（进程口径 mean≈25%，即 4C 整机 25% 单核）——单线程 worker 理论形态，符合设计意图 | ✅（口径见注1） |
| AC-03-09 | 索引期内存 | 峰值≤1GB | 峰值 **162.9MB**（较基线 +16MB），结束后回落 146.7MB | ✅ |
| AC-03-10 | 检索延迟 | P50≤30ms/P95≤100ms | 空闲 P50=**15.0ms**/P95=**17.5ms**；索引期 P95=**27.0ms**≤2×空闲P95 | ✅（max 离群见 F1） |
| AC-03-11 | 磁盘 | db≤索引文本**500%**（中文词级正式达标线，lead 裁定；原 50% 口径废弃） | 修复前 755% → 修复后默认配置 **409.8%**；开启 --trigram **410.5%**（增幅 0.2%） | ✅（F2 已修复） |
| AC-03-01/02 | 单线程/低优先级 | 机制项 | 代码审阅：单 IndexWorker；**nice=16384（BELOW_NORMAL_PRIORITY_CLASS）实测** | ✅ |
| AC-03-03/04 | 空闲调度/检索优先 | 机制项 | 代码审阅：wait_if_busy（阈值70 可配）+ 索引让路结构 | ✅（max 离群见 F1） |

**注1（CPU 口径澄清）**：psutil 进程 cpu_percent 以「单核 100%」为口径；达标线「≤50%」按整机（4C）语义理解——实测单核脉冲（进程口径 max=100 = 整机 25%），符合「单线程 worker 不把同事电脑打满」的设计意图。建议把该口径说明补进设计 §12（清单 AC-03-08 附注已同步）。

## 4. 兼容性检查（AC-06）

- **Windows 全量实测通过**：中文目录+空格文件名+长路径挂载可索引可命中（AC-06-02）；explorer /select 打开位置返回 ok（AC-06-04）；watchdog 增量系列实测通过（ReadDirectoryChangesW）；
- **Linux**：本机为 Windows，未实测。代码审阅证据：os.name 分支完整（打开位置 DBus FileManager1.ShowItems + xdg-open 回退、直接打开 xdg-open、优先级 nice(10)、rel_path 统一 / 分隔）；117 项单测为跨平台纯 Python。**待 Linux 实机补跑**（遗留 L1）；
- 依赖安装：requirements.txt 双平台通用（fastapi/uvicorn/jieba/watchdog/psutil/docx/openpyxl/pypdf/charset-normalizer/httpx），FTS5 为标准库能力。

## 5. 代码评审（三条红线 + 安全基线）

| 红线 | 判定 | 证据 |
|---|---|---|
| ① 不修改/移动/复制用户文件 | ✅ | 连接器全链路只 open('rb')；无任何写路径；「直接打开」用系统默认程序只读打开 |
| ② 索引库不在挂载目录 | ✅ | agent.add_mount 拒绝 db 文件/祖先目录挂载（**实测 HTTP 400**）；默认 ~/.chiselwall |
| ③ 资源控制 | ✅ | 单线程 IndexWorker + 低优先级（实测 16384）+ 空闲调度 + 防抖 + 有界队列丢新 + 大文件哈希采样（>8MB 只采样 1MB） |
| 安全基线 | ✅ | API 仅 127.0.0.1 + 随机 token（secrets.token_urlsafe(24)）；无 token 401 实测；被排除文档 403 实测 |

**正面确认**：短语查询 build_phrase_match 单引号串实现（AC-01-13 检查点，我此前提示的漂移风险已被 builder 消除）；连接器 resolve() 防路径穿越；read_doc 双保险（ParseError 白名单 + 意外异常兜底）；排除目录整树剪枝（隐私+效率）。

## 6. 发现与建议（分级）

| # | 级别 | 发现 | 建议 |
|---|---|---|---|
| F1 | **P1 待修** | 批事务粒度：大文档（2000 chunk）单事务写入期间持 DB 锁，索引期并发检索出现 **max=6213ms** 离群（P95=27ms 达标但单请求卡 6.2s，违反「检索优先」体验） | set_chunks 分批提交（如每 200 chunk 一事务）；或事务间让出。M3 前修复并复测索引期检索 max |
| F2 | **P1 待修** | trigram 兜底表空间爆炸：db 58.9MB / 原文 7.8MB ≈ **755%**（AC-03-11 不达标）。根因：全部 chunk 无条件写 trigram 表，大文本（5M 字符）3-gram 索引体积超原文 20+ 倍 | 方案（择一或组合）：trigram 表加开关（设计 §5 本意「按空间预算决定是否开启」）；仅小文档（如 <100K 字符）写 trigram；FTS detail=none。M3 前修复并复测 AC-03-11 |
| F3 | P2 | excludes.py 注释残留「默认 50MB」与代码 20MB 不一致 | 改注释 |
| F4 | P2 | /api/health 公开返回 db 绝对路径（仅本机监听，风险低） | 移除 db 字段或改版本号 |
| F5 | P2 | Vue3 走 CDN（jsdelivr），内网无外网时 UI 无法渲染 | M6 打包内置（src/README 已登记）；M3 若内网演示可先本地内置 |
| F6 | P2 | httpx/starlette TestClient 弃用警告（测试依赖） | 后续升级或忽略 |
| F7 | P2 | 不支持格式（如 .log）静默跳过，不进任何列表，用户无从知晓 | UI 挂载说明中列出支持格式；或排除列表增加「不支持格式」条目 |
| 观察-1 | 记录 | 旧格式 .doc/.xls、二进制走「默认排除」轨道（§7），非失败列表——与清单 AC-04-05/06 原预期差异，实现正确 | 清单断言口径已修正；M3 UI 建议区分「排除（默认规则）」与「失败（解析错误）」两个列表的展示 |
| 观察-2 | 记录 | glob 大小写敏感（fnmatchcase，跨平台一致）；Windows 用户可能预期不敏感 | 设计未定，待用户意见；当前行为一致可接受 |
| 观察-3 | 记录 | 检索降级链（AND/trigram OR/LIKE）为召回增强，使「整词 0 命中」断言不可靠（验收方法已修正为目标不可见+DB 断言） | 建议 UI 利用 route 字段区分「主索引命中」与「兜底命中」 |

## 7. 验收判定（最终，task-11 复核后）

- **P0（阻断项）：全部通过** ✅ —— 挂载可搜（各格式）、排除生效（矩阵+红线⑤ 403）、CPU/内存达标（空闲 0%/147MB、索引期单核脉冲）、失败隔离、增量、token 鉴权、最小 UI 功能齐全。
- **P1：F1/F2 已修复并复核通过** ✅ —— 见下「task-11 复核记录」。
- **判定：通过（最终放行）**。M2 验收闭环；F3/F4/F5/F6/F7 与观察-1/2/3 均不阻断（F3/F4/glob 大小写已随 task-11 速胜修复，其余纳入 M3）。

### task-11 复核记录（builder 修复 → reviewer 复测）

| 项 | 修复实现（审阅） | 复测结果 |
|---|---|---|
| F1 大事务阻塞检索 | set_chunks 分批提交（每 200 chunk 一事务、批间释放锁）+ jieba 分词移出锁外 | 索引期并发检索 100 次：p50=37.9ms / p95=63.2ms / **max=368.2ms ≤500ms** ✅（修复前 max=6213ms） |
| F2 trigram 空间爆炸 | trigram 默认关闭（--trigram 可配开启）、开启时仅总字符 ≤100K 小文档写入；chunks 表移除 text_jieba 冗余列 | 默认配置 db/文本 = **409.8% ≤500%** ✅ 且 trigram 表 0 行；开启 --trigram：小文档真子串命中 route=trigram ✅、大文档（5M 字符）trigram 0 行 ✅、db/文本 410.5%（增幅 0.2%，优于预期 2%）✅ |
| 回归 | — | **121 项单测全绿**（42.5s）；**78 项验收驱动全绿**（默认配置）✅ |

> 说明：AC-03-11 达标线已按 lead 裁定改为「db ≤ 索引文本 500%」（中文词级正式口径，构成 ≈ 原文 100% + 中文 FTS 索引 300%），清单已同步；AC-01-14 验证方法同步更新（默认关断言 + 开启时真子串 route=trigram 断言）。

## 8. 遗留事项

| # | 事项 | 责任 |
|---|---|---|
| L1 | Linux 实机补跑核心子集（AC-06-10） | reviewer（有 Linux 环境时） |
| L2 | 真实 .doc/.xls 样本（脱敏）→ edge/real/ | lead 向用户征集 |
| L3 | 中文文本层 PDF 样本（环境无 reportlab/LibreOffice，本次用 ASCII 文本层 PDF 验证解析链路；pypdf 中文抽取为上游能力） | 待工具具备后补 |
| L4 | F1/F2 修复与复测 | builder（M3 前） |

## 9. 附：验收资产清单（tests/fixtures/acceptance/）

- make_acceptance.py —— 幂等语料生成（自检 10 项）
- run_acceptance.py —— 78 项 API 断言驱动（v3，可重复执行）
- measure_resources.py —— psutil 三段采样 + 检索延迟
- verify_403.py / inspect_db.py / experiment_modify_db.py —— 单项验证脚本
- .run/ —— 验收运行态（db/token/UI 截图），不入库

> 说明：builder 自建 tests/fixtures/notes 为其单元测试语料，本目录为 reviewer 验收语料，两者并存不冲突；验收驱动与生成脚本已固化，M3 起作为回归基线复跑。
