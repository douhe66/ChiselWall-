# 02 本地索引与 AI 问答技术调研

> 调研目标：为 ShareVeryone 每台电脑上的本地 agent 选定索引、检索、笔记解析、AI 接入与资源控制方案（只索引、不复制、不上传原文）。
> 方法：基于公开资料检索，关键结论均附来源 URL。v1 优先 Python、低资源占用（Windows + Linux）。

## 0. 结论速览

- 全文检索：**SQLite FTS5**（jieba 分词影子列为主，可选 trigram 表兜底任意子串），嵌入式、零服务、按 rowid 增量更新简单（§1）。
- 向量检索（可选语义搜索）：**sqlite-vec** 扩展，与 FTS5 同库单文件；模型用 **bge-small-zh-v1.5**（24M 参数 / 512 维）ONNX int8 CPU 推理，约 30MB，默认关闭（§2）。
- 笔记读取：思源直读 `.sy` JSON（或走内核 HTTP API）；Obsidian 直读 Markdown；Notion 走官方 API（游标分页 + 3 req/s 限流）；有道云用 youdaonote-pull 离线导出（非官方，标注风险）（§3）。
- RAG 参考：Khoj（架构最接近，但其服务端依赖 Postgres+pgvector，不适合低资源 agent）；AnythingLLM（默认内嵌 LanceDB + Ollama）；Reor（本地优先，已归档）。AI 接口：DeepSeek API（OpenAI 兼容）+ Ollama（/v1/... 兼容端点）（§4）。
- 增量与资源控制：watchdog 事件监视 + 防抖；低优先级 + 单线程 worker + 限线程推理 + 空闲调度；FTS 索引约原文 30%（trigram 更大），向量 512 维 fp32 每条 2KB（§5）。

## 1. 嵌入式全文检索引擎对比

### 1.1 SQLite FTS5（首选）

- 形态：SQLite 内置全文检索模块（SQLite 3.9 起内置，trigram 分词器需 3.34.0+），虚拟表实现，单文件数据库、进程内查询、无守护进程，Windows/Linux 开箱即用。来源：https://sqlite.org/fts5.html
- 中文/CJK 的核心问题：默认 unicode61 分词器按 Unicode 类别切词，连续中文会被当作一个 token，中文子串/词组查询无法命中。来源：https://github.com/NousResearch/hermes-agent/blob/19dc35cf577a339f1ba6f7106d37704638549ca0/native/fts5_cjk/fts5_cjk.c 、https://www.npmjs.com/package/dsh-knowledge-sqlite
- 中文适配三条路线：
  1. **trigram 分词器**：按 3 字符滑窗建索引，可命中任意 ≥3 字符子串（含中文），但 2 字中文词（最常见查询形态）无法命中，且索引体积显著更大。来源：https://sqlite.org/fts5.html#trigram_tokenizer 、https://docs.rs/intent-engine/0.3.4/intent_engine/search/index.html 、https://github.com/streetwriters/sqlite-better-trigram
  2. **jieba 分词写入影子列**：入库时用 jieba 把正文切词（空格连接）写入专用索引列，查询时同样分词并组合为 FTS5 短语查询（词序列需相邻出现），精度高、体积接近词级索引。来源：https://github.com/fxsjy/jieba 、https://sqlite.org/fts5.html
  3. **组合方案（推荐）**：主索引走 jieba 词，另建一张 trigram 表兜底任意子串（英文代码、缩写、单字/双字）；社区已有同思路实现（trigram + L1 查询扩展）。来源：https://www.npmjs.com/package/dsh-knowledge-sqlite
- 增量更新：FTS5 支持按 rowid 的 UPDATE/DELETE（delete 命令后重新 insert），逐文件增删改即可；external content 表 / contentless 表可避免重复存储原文。来源：https://sqlite.org/fts5.html
- 索引体积实测参考：英文邮件语料下 FTS 数据约占库总量 32%，detail=none 可降至约 7.6%；trigram 索引约比 unicode61 大 2~3 倍，可能超过原文体积。来源：https://sqlite.org/forum/forumpost/ccf1ea60a3 、https://sqlite.org/forum/forumpost/53e724d12dbd2097 、https://github.com/jfolcini/agaric/blob/6f112d12f98f6707bec827f94aba5cee4596e9e8/src-tauri/migrations/0006_fts5_trigram.sql 、https://stackoverflow.com/questions/75368862/lossless-sqlite-fts5-search-of-a-substring/75372780
- 资源占用：查询在调用进程内完成、毫秒级，常驻内存 MB 级；思源笔记自身的全文检索就是 siyuan.db + FTS5，说明该引擎足以支撑笔记级中文检索。来源：https://blog.gitcode.com/501ee5573e68b1d2e09b0679f7b18c40.html

### 1.2 Tantivy（备选，量级升级时考虑）

- Rust 版 Lucene，嵌入式库、无服务，Python 绑定 tantivy-py；第三方评测查询 0.27ms 量级、索引 10,875 docs/s，适合 1M~10M 文档级规模。来源：https://github.com/quickwit-oss/tantivy 、https://research.modelcitizendeveloper.com/survey/1-003/#s4-strategic-viability---recommendations
- 中文：默认分词器不适配中文，需 tantivy-jieba 等 Rust crate；tantivy-py 目前不能直接注册自定义分词器（issue #25），中文方案要深入 Rust 侧封装，Python 工程成本高。来源：https://github.com/quickwit-oss/tantivy-py/issues/25 、https://crates.io/crates/tantivy-jieba
- 段合并（segment merge）会带来建索引期内存/CPU 峰值，对『同事电脑低占用』不友好。
- 结论：v1 不采用；单机文本超过约 10GB 或对检索性能有更高要求时再评估。

### 1.3 Xapian

- C++ 实现、20+ 年成熟库；内置 FLAG_CJK_NGRAM（中文按 unigram+bigram 建索引）开箱即用，索引与查询两侧都要开启该 flag。来源：https://xapian.org/docs/apidoc/html/classXapian_1_1TermGenerator.html 、https://lists.xapian.org/pipermail/xapian-discuss/2018-February/009592.html
- 优点：低资源、增量更新成熟；缺点：API 老旧、生态小、无向量能力。
- 结论：v1 不采用（中文虽省心，但工程体验与 FTS5 差距明显）。

### 1.4 Meilisearch（不采用，留作部门级服务备选）

- Rust 独立 HTTP 服务；自带 charabia 分词器，中文分词质量好（1.15 起进一步优化中文切分）。来源：https://github.com/meilisearch/meilisearch 、https://www.meilisearch.com/docs/capabilities/indexing/advanced/tokenization 、https://github.com/meilisearch/charabia 、https://www.meilisearch.com/blog/meilisearch-1-15
- 资源：官方自托管最低 256MB RAM（数据量大时更多）；索引默认可用系统内存的 2/3，可用 --max-indexing-memory 限制；索引时多线程吃 CPU。来源：https://www.meilisearch.com/docs/resources/self_hosting/overview 、https://meilisearch.com.cn/docs/learn/indexing/ram_multithreading_performance
- 结论：常驻服务不适合『每台同事电脑一个低占用 agent』的形态，且与『中心端只做分发』的架构目标冲突。若未来做部门级统一检索服务可再评估。

### 1.5 对比小结

| 引擎 | 形态 | 中文支持 | 资源占用 | 增量更新 | v1 结论 |
|---|---|---|---|---|---|
| SQLite FTS5 | 嵌入式（C） | jieba 影子列 / trigram 组合 | 极低，进程内 | 按 rowid 增删改，简单 | ✅ 首选 |
| Tantivy | 嵌入式（Rust） | 需 Rust crate（tantivy-jieba） | 段合并有峰值 | 支持 | 备选（10GB+ 场景） |
| Xapian | 嵌入式（C++） | 内置 CJK n-gram | 低 | 成熟 | 不采用 |
| Meilisearch | 独立服务 | charabia 优秀 | 256MB+ 常驻、索引打 CPU | 实时 | 不采用（服务形态不符） |

## 2. 轻量向量数据库对比（语义搜索为可选特性）

### 2.1 sqlite-vec（首选）

- SQLite 纯 C 扩展、零依赖，vec0 虚拟表支持 float/int8/bit 向量与 KNN + 元数据过滤，定位『极小、够快、哪里都能跑』；pip 提供 Windows/Linux/macOS 预编译 wheel。来源：https://github.com/asg017/sqlite-vec 、https://pypi.org/project/sqlite-vec-win-arm64/
- 与 FTS5 同库单文件，共享事务与备份；几十万向量以内性能足够。

### 2.2 LanceDB

- 嵌入式列式存储（Rust 核心、Lance 格式），磁盘友好、内存低，支持向量+全文+过滤混合检索；AnythingLLM 默认内嵌的就是 LanceDB。来源：https://github.com/lancedb/lancedb 、https://docs.anythingllm.com/setup/vector-database-configuration/local/lancedb
- 依赖 Arrow/Lance 生态，比 sqlite-vec 重；百万级向量或强混合检索需求时选用。

### 2.3 Chroma

- Python 优先，PersistentClient 嵌入式或 client-server 双形态；功能全但依赖重（默认 embedding 会拉取 onnx 模型），轻量级方案中资源占用偏高。来源：https://github.com/chroma-core/chroma

### 2.4 FAISS

- C++ 高性能检索库（HNSW/IVF），评测中查询吞吐 866 QPS 远超 DB 类系统；但无内置持久化（需自行序列化）、无并发写、无元数据过滤。来源：https://github.com/facebookresearch/faiss 、https://arxiv.org/html/2608.12812
- 定位是『加速器』而非存储层，本项目不需要。

### 2.5 小体积中文 embedding 模型 + ONNX CPU 推理

- bge-small-zh-v1.5：24M 参数、512 维、最大 512 token、fp32 内存约 0.09GB；int8 量化 ONNX 约 30MB。来源：https://huggingface.co/BAAI/bge-small-zh-v1.5 、https://inference.readthedocs.io/zh-cn/v3.2.0/models/builtin/embedding/bge-small-zh-v1.5.html 、http://mp.weixin.qq.com/s?__biz=Mzg2NzkwNjg4Mg==&mid=2247487648&idx=1&sn=dd95b415cd9a7c60f85ab369dddbc0f1
- gte-small-zh：512 维、模型文件约 0.10GB，同为中文小模型代表。来源：https://huggingface.co/thenlper/gte-small-zh
- 推理方案：fastembed（Qdrant）封装 onnxruntime，无 PyTorch 依赖、支持量化权重、CPU-first；社区实测 BGE-small 量化模型 CPU 吞吐约 200 条/s、单条延迟约 5ms；int8 量化推理常驻内存约 100MB。来源：https://github.com/qdrant/fastembed 、https://pypi.org/project/fastembed/ 、https://github.com/rspamd/rspamd/blob/c08eb7c50d00e49cda1100ac3085f3cc2b280952/contrib/neural-embedding-service/README.md
- 结论：ONNX + CPU 完全可行。建议 int8 量化、限制 onnxruntime intra_op_num_threads=2、批量编码；语义搜索作为可选特性默认关闭（用户手动开启时才加载模型）。

### 2.6 选型小结

v1 采用 sqlite-vec（可选开关），与 FTS5 共用同一 SQLite 文件，不引入任何服务进程。

## 3. 笔记格式读取调研

### 3.1 思源笔记

- 数据目录：工作空间 data/ 下按笔记本存放文档，文档为 `.sy` 文件，格式是 JSON 文档树（块级 AST，带 spec 版本号），可直接 JSON 解析，官方提供格式规范。来源：https://github.com/siyuan-note/siyuan/blob/master/docs/SY-FORMAT.zh-CN.md 、https://github.com/siyuan-note/siyuan/blob/master/docs/WORKSPACE.zh-CN.md
- 内核 HTTP API：本地 127.0.0.1:6806，Header 携带 `Authorization: Token {token}`（设置-鉴权-API token 开启）；接口覆盖文档树/块查询/全文搜索（/api/search/fullTextSearchBlock）等，官方 API.md 维护。来源：https://github.com/siyuan-note/siyuan/blob/master/docs/API.md 、https://github.com/nousresearch/hermes-agent/blob/73bf3ab1b22314ed9dfecbb59242c03742fe72af/website/docs/user-guide/skills/optional/productivity/productivity-siyuan.md
- 结论：优先直读 `.sy` 文件做离线索引（不依赖思源运行、零额外资源）；思源在运行时走 API 作为增强（实时块、双向跳转）。注意 `.sy` 有 spec 版本差异，解析需按版本兼容。

### 3.2 Obsidian

- vault 即普通本地目录，笔记就是 Markdown 纯文本文件（含子目录），直接遍历 .md 索引即可；.obsidian 目录为配置，索引时排除。来源：https://raw.githubusercontent.com/obsidianmd/obsidian-help/refs/heads/master/en/Files%20and%20folders/How%20Obsidian%20stores%20data.md

### 3.3 Notion

- 官方 REST API：Bearer token 认证 + Notion-Version 头；列表接口用 start_cursor 游标分页，page_size 最大 100；限流平均 3 req/s（每 token）；页面块内容需 children 接口递归分页拉取，再转 Markdown 入库。来源：https://developers.notion.com/reference/intro 、https://developers.notion.com/reference/pagination 、https://developers.notion.com/reference/rate-limits 、https://www.stitchflow.com/user-management/notion/api
- 结论：官方 API 可行；注意限流与增量同步（用 last_edited_time 过滤增量），适合『定期拉取导出 → 本地索引』模式。

### 3.4 有道云笔记

- 无官方导出 API。开源方案 youdaonote-pull（DeppWang）：走 Web 端接口批量导出笔记为 .md / .note（html，含图片下载），社区长期使用、有成熟教程。来源：https://github.com/DeppWang/youdaonote-pull 、https://segmentfault.com/a/1190000047552448 、https://modelers.csdn.net/69aa23fd7bbde9200b9fecd2.html
- 风险：非官方接口可能随改版失效；需要账号凭证；仅适合『一次性/定期导出备份』通道，不做实时同步。符合需求中『实现困难可先跳过、留接口』的策略。

### 3.5 docx / xlsx / pdf / txt 解析库与常见坑

- python-docx：解析 .docx（OOXML）；**不支持旧版 .doc 二进制**。来源：https://pypi.org/project/python-docx/ 、https://ask.csdn.net/questions/9540691
- openpyxl：解析 .xlsx；不支持 .xls；公式单元格只能读到缓存值。来源：https://pypi.org/project/openpyxl/
- pypdf：PDF 文本抽取（基础）；pdfplumber：按布局/表格精细抽取。来源：https://pypi.org/project/pypdf/ 、https://pypi.org/project/pdfplumber/
- python-pptx（可选）：.pptx 演示文稿。来源：https://pypi.org/project/python-pptx/
- txt：编码混乱（GBK/UTF-8 等）需 charset-normalizer/chardet 检测。来源：https://pypi.org/project/charset-normalizer/
- 常见坑清单（本期明确不做 OCR）：
  - .doc / .xls 旧格式无法直接解析 → 跳过并提示（留转换接口）；
  - 加密 PDF / 加密 docx 需密码 → 跳过；
  - 扫描件、图片型 PDF 无文本层 → OCR 明确不做；
  - 解析失败一律 try/except 隔离，单文件失败不阻断整体索引。

## 4. 本地知识库 + RAG 开源参考

### 4.1 Khoj（架构最接近本项目的参考）

- 定位：自托管 AI 第二大脑（约 35k star）：索引 org/markdown/pdf/word/notion/github 等来源；Web UI、桌面应用、Obsidian/Emacs 插件、WhatsApp 多界面；后台持续索引 + RAG 聊天。来源：https://github.com/khoj-ai/khoj 、https://docs.khoj.dev/features/all-features/#general
- 架构：内容解析 → chunk → embedding → 向量存储（自托管服务端模式使用 Postgres + pgvector）→ 检索 → LLM 生成。来源：https://railway.com/deploy/khoj-self-hosted-ai-second-brain--khoj-ai-second-brain
- 可借鉴：多格式 indexer 抽象（每种格式一个可插拔索引器）、后台增量索引调度、chat/检索接口分离。
- 教训：① 服务端模式依赖 Postgres+pgvector，对 per-user 低资源 agent 偏重；② 纯向量检索在中文场景有明显盲区（如『违约金』与『违约赔偿』），社区实践是向量 + FTS5 关键词 + 实体图谱多路召回。来源：https://developer.aliyun.com/article/1769943

### 4.2 AnythingLLM

- 一体化 RAG 聊天应用（桌面/Docker）；默认内嵌 LanceDB（向量不出本机），可换多种向量库；原生支持 Ollama 与多种 LLM provider；文档处理管线成熟。来源：https://github.com/Mintplex-Labs/anything-llm 、https://docs.anythingllm.com/setup/vector-database-configuration/local/lancedb
- 可借鉴：LLM/embedding/向量库三层 provider 抽象、文档自动转换处理器。

### 4.3 Reor

- 本地优先 AI 笔记（Electron）：Ollama + Transformers.js + LanceDB，把『人写笔记』与『LLM 检索』作为对等生成器共享同一向量库；项目已归档停维护。来源：https://github.com/reorproject/reor 、https://cloud.tencent.cn/developer/article/2686138
- 可借鉴：人机共享向量库的理念（新写/编辑笔记立即增量进库）；不建议直接复用代码。

### 4.4 AI 接口接入方式

- DeepSeek API：OpenAI 兼容格式，base_url=https://api.deepseek.com，可直接用 openai SDK 或任意 OpenAI 兼容客户端；适合作为云端模型（中心配置或个人配置）。来源：https://api-docs.deepseek.com/zh-cn
- Ollama（本地模型）：OpenAI 兼容端点 http://localhost:11434/v1/chat/completions 与 /v1/embeddings；另有原生 /api/chat、/api/embed。来源：https://github.com/ollama/ollama 、https://github.com/ollama/ollama/pull/5470 、https://docs.ollama.com/api/embed
- 设计建议：RAG 链路统一走 OpenAI 兼容客户端，通过 base_url + model 配置切换 DeepSeek / Ollama / 其他兼容服务，一个实现两种接入。

## 5. 增量索引与资源控制

### 5.1 文件系统监视

- watchdog：Python 事实标准监视库；Linux 用 inotify、Windows 用 ReadDirectoryChangesW、macOS 用 FSEvents（失败回退轮询），事件驱动、开销远低于定时扫描。来源：https://github.com/gorakhargosh/watchdog 、https://deepwiki.com/gorakhargosh/watchdog/3-platform-specific-implementations 、https://python-watchdog.readthedocs.io/
- 工程要点：事件防抖（debounce，如 3~5 秒合并编辑器保存风暴）；以 (mtime, size, hash) 校验真正变更，避免重复索引。

### 5.2 空闲时低优先级索引

- 进程/线程优先级：Linux 下 nice/ionice；Windows 下用 psutil 将进程设为 BELOW_NORMAL_PRIORITY_CLASS。来源：https://pypi.org/project/psutil/
- 索引 worker 单线程串行；ONNX 推理限制 intra_op_num_threads=2；批量提交（每 N 个文件一次事务）；
- 空闲调度：仅在 CPU 负载低于阈值（psutil.cpu_percent 采样）或用户无操作时推进队列；收到『随手问』广播时检索优先级最高、索引让路。

### 5.3 chunk 切分策略

- 中文建议固定窗口 300~800 字 + 100~200 字重叠；优先按结构切分：思源按块（.sy AST 天然块）、Markdown 按标题/段落、docx 按段落、pdf 按页/段。通用递归切分可参考 LangChain 的文本切分器思路。来源：https://docs.langchain.com/oss/python/integrations/splitters/index
- 每 chunk 记录 (文档 id, chunk 序号, 行号/块 id)，保证命中后跳转原文；本地不复制原文。

### 5.4 索引大小估算

- FTS5 词级索引（jieba/unicode61）：约原文 30%（detail=none 可低至约 8%）；trigram：约词级索引 2~3 倍，可能超过原文体积。来源：https://sqlite.org/forum/forumpost/ccf1ea60a3 、https://github.com/jfolcini/agaric/blob/6f112d12f98f6707bec827f94aba5cee4596e9e8/src-tauri/migrations/0006_fts5_trigram.sql 、https://stackoverflow.com/questions/75368862/lossless-sqlite-fts5-search-of-a-substring/75372780
- 案例：3MB Markdown 切块后索引 9.8MB。来源：https://github.com/oomkapwn/enquire-mcp/issues/10
- 向量（按 bge-small-zh 512 维规格推算）：fp32 每条 2KB、int8 每条 0.5KB。若 5GB 文本切约 50 万 chunk：FTS 索引约 1.5~5GB，向量 fp32 约 1GB / int8 约 256MB。
- 结论：几 GB 笔记的索引总量在 GB 级、可接受；用 int8 量化 + FTS detail=none + 不存原文把磁盘占用压到最低。

## 6. v1 技术栈推荐（Python，低资源优先）

| 层 | 选型 | 说明 |
|---|---|---|
| 全文检索 | SQLite FTS5 | jieba 分词影子列为主，可选 trigram 表兜底子串 |
| 向量检索（可选） | sqlite-vec | 与 FTS5 同库；语义搜索默认关闭 |
| embedding | bge-small-zh-v1.5（ONNX int8） | fastembed/onnxruntime，限 2 线程，仅开启时加载 |
| 文件监视 | watchdog | inotify / ReadDirectoryChangesW + 防抖 + hash 校验 |
| 文档解析 | python-docx / openpyxl / pypdf / pdfplumber / python-pptx(可选) / charset-normalizer | 解析失败隔离跳过，OCR 不做 |
| 笔记源 | 思源(.sy 直读+API) / Obsidian(md) / Notion(官方 API 分页) / 有道(导出工具，可选) | 均转统一文本流进索引，不复制原文 |
| AI 接口 | OpenAI 兼容客户端 | DeepSeek API（api.deepseek.com）或 Ollama（localhost:11434）可切换 |
| 资源控制 | 低优先级进程 + 单线程 worker + 限线程推理 + 空闲调度 + 批量提交 | 保证同事电脑不被打满 |

- 不采用：Meilisearch（服务形态不符）、Chroma（重）、FAISS（无持久化）、Tantivy/Xapian（v1 场景过重或体验旧）。
- 规模边界：单机文本 <10GB 时 FTS5 足够；超过再评估 Tantivy。
- 风险提示：有道云导出依赖非官方接口（可能随改版失效）；Notion 需用户自行申请 token；思源 .sy spec 版本需兼容处理。

## 附：参考链接汇总

### 检索引擎与中文分词
- SQLite FTS5 官方文档：https://sqlite.org/fts5.html
- SQLite 官方论坛·FTS 索引体积：https://sqlite.org/forum/forumpost/ccf1ea60a3
- trigram 中文双字匹配限制：https://docs.rs/intent-engine/0.3.4/intent_engine/search/index.html 、https://github.com/streetwriters/sqlite-better-trigram
- trigram + 查询扩展中文实践：https://www.npmjs.com/package/dsh-knowledge-sqlite
- jieba 分词：https://github.com/fxsjy/jieba
- Tantivy：https://github.com/quickwit-oss/tantivy ；tantivy-py 中文分词 issue：https://github.com/quickwit-oss/tantivy-py/issues/25 ；tantivy-jieba：https://crates.io/crates/tantivy-jieba
- Tantivy 评测数据：https://research.modelcitizendeveloper.com/survey/1-003/#s4-strategic-viability---recommendations
- Xapian CJK n-gram：https://xapian.org/docs/apidoc/html/classXapian_1_1TermGenerator.html
- Meilisearch 自托管要求：https://www.meilisearch.com/docs/resources/self_hosting/overview ；内存/多线程：https://meilisearch.com.cn/docs/learn/indexing/ram_multithreading_performance ；charabia：https://github.com/meilisearch/charabia

### 向量检索与 embedding
- sqlite-vec：https://github.com/asg017/sqlite-vec
- LanceDB：https://github.com/lancedb/lancedb ；Chroma：https://github.com/chroma-core/chroma ；FAISS：https://github.com/facebookresearch/faiss
- 向量库横向评测：https://arxiv.org/html/2608.12812
- bge-small-zh-v1.5：https://huggingface.co/BAAI/bge-small-zh-v1.5 ；规格：https://inference.readthedocs.io/zh-cn/v3.2.0/models/builtin/embedding/bge-small-zh-v1.5.html
- gte-small-zh：https://huggingface.co/thenlper/gte-small-zh
- fastembed：https://github.com/qdrant/fastembed ；CPU 实测：http://mp.weixin.qq.com/s?__biz=Mzg2NzkwNjg4Mg==&mid=2247487648&idx=1&sn=dd95b415cd9a7c60f85ab369dddbc0f1

### 笔记源与解析库
- 思源 .sy 格式规范：https://github.com/siyuan-note/siyuan/blob/master/docs/SY-FORMAT.zh-CN.md ；工作区布局：https://github.com/siyuan-note/siyuan/blob/master/docs/WORKSPACE.zh-CN.md ；内核 API：https://github.com/siyuan-note/siyuan/blob/master/docs/API.md
- Obsidian 存储方式：https://raw.githubusercontent.com/obsidianmd/obsidian-help/refs/heads/master/en/Files%20and%20folders/How%20Obsidian%20stores%20data.md
- Notion API：https://developers.notion.com/reference/intro 、分页 https://developers.notion.com/reference/pagination 、限流 https://developers.notion.com/reference/rate-limits
- 有道云导出：https://github.com/DeppWang/youdaonote-pull
- python-docx：https://pypi.org/project/python-docx/ ；openpyxl：https://pypi.org/project/openpyxl/ ；pypdf：https://pypi.org/project/pypdf/ ；pdfplumber：https://pypi.org/project/pdfplumber/ ；python-pptx：https://pypi.org/project/python-pptx/ ；charset-normalizer：https://pypi.org/project/charset-normalizer/

### RAG 参考与 AI 接口
- Khoj：https://github.com/khoj-ai/khoj ；功能总览：https://docs.khoj.dev/features/all-features/#general ；中文场景多路召回分析：https://developer.aliyun.com/article/1769943
- AnythingLLM：https://github.com/Mintplex-Labs/anything-llm ；LanceDB 默认配置：https://docs.anythingllm.com/setup/vector-database-configuration/local/lancedb
- Reor：https://github.com/reorproject/reor ；归档信息：https://cloud.tencent.cn/developer/article/2686138
- DeepSeek API（OpenAI 兼容）：https://api-docs.deepseek.com/zh-cn
- Ollama：https://github.com/ollama/ollama ；/v1/embeddings：https://github.com/ollama/ollama/pull/5470 ；/api/embed：https://docs.ollama.com/api/embed

### 增量与资源控制
- watchdog：https://github.com/gorakhargosh/watchdog ；平台后端：https://deepwiki.com/gorakhargosh/watchdog/3-platform-specific-implementations ；文档：https://python-watchdog.readthedocs.io/
- psutil：https://pypi.org/project/psutil/
- LangChain 文本切分：https://docs.langchain.com/oss/python/integrations/splitters/index
- FTS5 trigram 体积案例：https://github.com/jfolcini/agaric/blob/6f112d12f98f6707bec827f94aba5cee4596e9e8/src-tauri/migrations/0006_fts5_trigram.sql 、https://github.com/oomkapwn/enquire-mcp/issues/10