# 03 思源 .sy / 中文检索工程 / NATS / AI 接入调研

> 调研目标：为 M3（SiyuanConnector）、M2/M3（中文检索质量）、M4（relay+随手问）、M5（AI 入口）四个里程碑补充工程级备料。承接 01（分布式架构，NATS 选型已定）与 02（本地索引与 AI，FTS5+jieba 方案已定），本文只深入"怎么做"的细节，不复述选型论证。
> 方法：web_search / 浏览器抓取官方文档与源码原文（原始抓取存 docs/research/_raw/）。调研时间：2026-11。
> 面向读者：实现 M2–M5 的工程师；可直接照抄的 SQL/配置/代码要点均附来源。

## 0. 结论速览

- **.sy 直读可行且是官方认可场景**：.sy 是 Lute AST 序列化的 UTF-8 JSON，无外部 schema，Python 直接 json.loads + 递归遍历即可提取纯文本；官方规范（SY-FORMAT.zh-CN.md）已完整公开字段语义、Spec 版本规则与 base64/IAL 约定。官方还内置了无需运行服务的 CLI（`siyuan export md`）可作转换兜底。加密笔记本磁盘为密文，必须跳过或走内核 API。
- **中文检索有两条现成工程路线**：hermes-agent 的 cjk_unicode61 加载式分词器（CJK run 重发射为重叠 bigram，2 字词精确子串命中）与 dsh-knowledge-sqlite 的 trigram+查询扩展（实测 unicode61 基线 0%，trigram 65% 人工集，L1 扩展 65–82%）。本项目「jieba 影子列为主 + trigram 兜底」与两者正交互补：jieba 词级主索引保精度，trigram 表兜底任意子串，查询路由与自写 snippet 直接借鉴 hermes/dsh 的实现。
- **NATS 集成要点**：nats-py 请求-回复天然三结局（按时答复/超时/NoRespondersError）；广播聚合不能用单 `request()`（只收第一个回复），要用 subscribe + 按 qid 计数收口；token 鉴权与 TLS 配置样例已备齐。
- **AI 接入**：OpenAI 兼容客户端 + base_url/model/key 三配置即覆盖 DeepSeek/Ollama/内网模型；注意 DeepSeek 当前模型已是 1M 上下文（deepseek-flash / deepseek-v4-pro），设计文档里的 deepseek-chat 64K 是旧信息；Ollama /v1/chat 与 /v1/embeddings 均可用。

## 1. 思源笔记 .sy 格式（M3 备料）

### 1.1 官方规范结构（SY-FORMAT.zh-CN.md 要点）

来源：https://github.com/siyuan-note/siyuan/blob/master/docs/SY-FORMAT.zh-CN.md

- **本质**：明文 .sy = Lute AST 树序列化的 JSON。根节点恒为 `NodeDocument`，正文是递归嵌套的 `Children` 数组；**不存在单独维护的 JSON Schema**，Lute 的 Go 结构体（ast.Node / ListData）是事实来源。
- **顶层结构**：

```json
{
  "ID": "20200825162036-4dx365o",   // 文档块 ID，等于去掉 .sy 的文件名
  "Spec": "2",                      // 格式版本，见 §1.2
  "Type": "NodeDocument",
  "Properties": { "icon": "1f4f0", "id": "20200825162036-4dx365o",
                  "title": "排版元素", "type": "doc", "updated": "20260616224229" },
  "Children": [ ... ]
}
```

- **通用字段语义**（每个节点适用）：`Type`（所有节点必有，类型判别）、`ID`（规范块节点必需，22 字符）、`Data`（文本/HTML/markdown 原文，**可省略，不能假设必有**）、`Properties`（IAL，map[string]string）、`Children`（容器节点）、类型专属字段（HeadingLevel、ListData、TextMarkType 等）。
- **块判定规则**：**Type 决定节点是否为块**（ast.Node.IsBlock() 是权威判断），不能凭 ID 有无判断；历史遗留文件可能在非块节点上带 ID。
- **块类型（§5）**：NodeParagraph、NodeHeading（HeadingLevel 1–6）、NodeList（**用 ListData.Typ 区分无序/有序/任务列表**，直接子节点只能是 NodeListItem）、NodeListItem、NodeBlockquote、NodeCallout（GFM Alert）、NodeCodeBlock（CodeBlockInfo 是 base64）、NodeSuperBlock、NodeTabs/NodeTabItem、NodeMindmap/NodeMindmapItem、NodeTable/NodeTableCell、NodeAttributeView 等。
- **内联节点（§6）**：**加粗/斜体/链接/行内代码/块引用几乎全部用 `NodeTextMark`**（TextMarkType 取值 text/strong/em/u/s/mark/kbd/code/tag/a/block-ref/inline-math/inline-memo 等），而非 NodeStrong/NodeEmphasis/NodeLink 旧类型；纯文本是 `NodeText`（Data 为空字符串时因 omitempty 省略——空 TextMark 不代表空文本）。图片/音视频/挂件是 NodeImage/NodeAudio/NodeVideo/NodeWidget 等独立节点。
- **base64 编码约定（§7）**：Go `[]byte` 字段（ListData.Marker、CodeBlockInfo、CodeBlockOpenFence）序列化为 **base64 字符串**（如 `"Kg=="`=`*`、`"Z28="`=`go`）；Go `byte` 字段（BulletChar、Delimiter、TaskListItemMarker）序列化为 **int ASCII 码点**（`42`=`*`、`32`=空格、`88`=`X`）；`Data`/TextMarkTextContent 等内容字符串保持**原文不编码**。
- **Properties（IAL，§8）**：文档级规范必写 id/title/type（恒 "doc"）/updated；块级必写 id（等于节点 ID）/updated；权威键是小写 `id`（遗留大写 ID 需清理）。**文档标题取 `Properties.title`**。
- **编码**：UTF-8 无 BOM（读取器容忍开头 BOM）；**加密笔记本的 .sy 在磁盘是密文，不能当 JSON 解析**，只能在解锁后走专用 API。

### 1.2 Spec 版本差异与兼容规则

来源：同上 SY-FORMAT.zh-CN.md（规范写入基准段落）

| Spec | 触发条件 | 对读取器的影响 |
|---|---|---|
| "2" | 普通文档（基准，大多数文件） | 全字段按 §1.1 |
| "3" | 包含页签的文档 | 新增 NodeTabs 结构 |
| "4" | 表格单元格富文本 | 单元格内容可为块树 |
| "5" | 包含思维导图节点 | NodeMindmap 结构 |
| "6" | 文档标题包含 ASCII `/` | 结构化文档寻址 |

- **升级规则**：写入时至少采用所用功能要求的最高版本，**功能移除后不降低版本**；兼容读取器（dataparser.ParseJSON）可**升级旧版或缺少 Spec 的数据**——它填充缺失空段落、为缺 ID 块分配 ID、升级旧 Spec，是"兼容读取器"而非严格 schema 校验器。
- 对 M3 的推论：**读端策略 = 最低支持 Spec 2，遇更高版本照常解析，未知 Type/字段一律忽略并继续递归 Children**；不要因 Spec 版本拒绝整个文档。

### 1.3 工作区布局（WORKSPACE.zh-CN.md 要点）

来源：https://github.com/siyuan-note/siyuan/blob/master/docs/WORKSPACE.zh-CN.md

- 核心：`data/<boxID>/<docID>.sy`——**文件名即文档 ID，目录结构即文档层级**；子文档放在同名目录 `<docID>/` 下，可任意深度嵌套。笔记本配置在 `data/<boxID>/.siyuan/conf.json`。
- **判定合法工作区**：`conf/conf.json` 存在且含 `kernelVersion` 字段；运行时锁 `.lock` 防止两个内核同开一个工作区。
- 需要**排除**的目录：`data/.siyuan/`（工作区级隐藏配置，含 syncignore/searchignore）、`assets/`（资源文件）、`templates/`、`widgets/`、`plugins/`、`storage/`；`repo/`、`history/`、`corrupted/` 也不属于索引范围。
- `temp/` 下的 siyuan.db / blocktree.db 是**可重建索引**——不要读取、不要依赖（作者也警告直接改盘后需触发重建索引）。
- 加密笔记本：内容密文落盘，目录为 `data/<boxID>/assets/` 存本地资源；**磁盘上不可直读**。

### 1.4 官方解析/导出途径（四个层级）

- **Lute**（Go 编辑引擎，AST 事实来源）：https://github.com/88250/lute
- **protyle**（官方 TypeScript 解析渲染器）：https://github.com/88250/protyle —— "思源笔记数据解析器和渲染器"（deps.dev 描述）
- **Go 解析包**：github.com/siyuan-note/parser（"SiYuan file format parsers"）与 siyuan-note/dataparser：https://socket.dev/go/package/github.com/siyuan-note/parser 、https://githublb.vercel.app/repo/siyuan-note/dataparser
- **官方内置 CLI（重要，无需运行服务）**：官方 README（https://github.com/siyuan-note/siyuan）"Command-line Interface" 一节：

```bash
siyuan notebook list -w ~/SiYuan          # 列笔记本
siyuan search "keyword" -w ~/SiYuan -f json   # 全文搜索（含资产文件 --asset）
siyuan export md --id <block-id> -w ~/SiYuan  # 单文档导出 Markdown
```

  CLI 二进制为 `<install-dir>/resources/kernel/SiYuan-Kernel`，Windows 安装器自动加入 PATH（Store 版注册 `siyuan.exe` 别名）；**不支持加密笔记本操作**。`-f json` 输出脚本友好格式。
- **内核 HTTP API**（运行时增强）：/api/export/exportMdContent 等，需思源运行 + `Authorization: Token {token}`（127.0.0.1:6806）；官方建议插件"不要自己打包 Lute，通过 API 遥控内核"（链滴讨论：https://ld246.com/article/1768708383832/comment/1768708576867 ）。

官方对"何时直接读写 .sy"的定性（SY-FORMAT §0.5）：**修改**优先走 API/MCP/CLI（否则两套索引不一致）；**只读的内容统计、分析、自定义导出/格式转换**是直接读盘的正规适用场景——这正是 ChiselWall 的用法。

### 1.5 社区解析/导出实现

- **cli-anything-siyuan**（Python，走 HTTP API 而非 .sy 解析）：notebook/document/block/search/export md 子命令封装：https://raw.githubusercontent.com/HKUDS/CLI-Anything/main/siyuan/agent-harness/cli_anything/siyuan/skills/SKILL.md
- **cocoloop 思源技能**（Python SDK + CLI，同样走官方 HTTP API）：https://hub.cocoloop.cn/skills/45677
- **结论**：**未发现成熟、活跃的第三方纯 .sy JSON 解析库**（社区普遍走内核 API 或官方 CLI 绕开解析）；M3 的纯 Python 直读需要自写 JSON 树遍历（工作量可控，见 §1.6），或调用官方 CLI 做转换兜底。

### 1.6 M3 SiyuanConnector 实现要点（离线直读）

- **list_docs()**：遍历 `data/<boxID>/**/*.sy`，排除 data/.siyuan、assets、templates、widgets、storage、repo、history；doc_id 取 `<workspace>/<boxID>/<相对路径去掉.sy>`；title 取根节点 `Properties.title`；mtime/size 用文件系统值。合法工作区校验：conf/conf.json 含 kernelVersion。
- **read_doc(doc_id)**：json.loads → 递归遍历 Children，按 Type 提取纯文本：
  - NodeText → Data；NodeTextMark → TextMarkTextContent（inline-math 取 TextMarkInlineMathContent）；
  - NodeCodeBlock → 子节点 NodeCodeBlockCode.Data（CodeBlockInfo 解码出语言标注）；
  - NodeImage → Properties.title 或 alt；NodeHeading → 前置 # 层级；NodeListItem → 前缀列表符号；
  - 未知 Type 节点：跳过自身、仍递归 Children（兼容未来 spec）。
- **chunk 切分天然按块**：思源 .sy AST 本身就是块级结构，按块边界切 chunk（呼应 02 §5.3"思源按块"），块文本拼接后按设计 §5 的 500–800 字窗口二次合并。
- **加密/异常处理**：文件不以 `{` 开头或 JSON 解析失败 → 判定为加密或损坏，跳过并记入失败列表（设计 §6 要求"解析失败跳过并记录，不阻断"）；读取瞬时损坏（与内核并行写入竞争）可重试一次。
- **可选增强通道（v1 后）**：思源运行时经 127.0.0.1:6806 内核 API 取实时块/全文；官方 CLI `siyuan export md --id <docID> -w <workspace>` 作为 .sy→文本的兜底转换器（注意 CLI 不支持加密笔记本）。
- **绝不写盘**：连接器对 .sy 只读（产品原则"零迁移"）。

### 1.7 版本兼容策略

1. 读器按 §1.1 字段语义实现"容错遍历"：Data/Children 缺失按空处理；块判定不需要严格 IsBlock()（我们只提取文本，不需要区分块/内联的权威语义）；
2. Spec 版本：≥"2" 一律尝试解析，未知版本不拒绝；未知 Type/字段忽略、递归 Children，保证升级后旧解析器仍能提取已知节点文本；
3. 灰度自检：内置一个"解析健康度"统计（成功提取文本的节点比例、未知 Type 计数），上线前用真实工作空间样本核对（设计 §14 风险项"需提供几份真实工作空间样本"的落地工具）；
4. 官方规范文档每季度跟进 diff（SY-FORMAT.zh-CN.md 在 master 持续更新），新 Type 加入白名单或降级为"跳过"。

### 1.8 风险清单

| 风险 | 影响 | 缓解 |
|---|---|---|
| spec 演进新增节点类型 | 未知节点文本提取不全/遗漏 | 未知 Type 递归 Children + 健康度统计 + 跟进官方规范 |
| 加密笔记本磁盘密文 | 完全不可读 | 跳过 + UI 提示"解锁后经内核 API 可索引"（v1 不做） |
| 与思源内核并行读写 | 读到中间态 JSON | 解析失败重试一次；仍失败跳过并记录 |
| 无第三方 Python 解析库 | 解析器自维护 | 自写树遍历工作量小（§1.6）；官方 CLI/API 兜底 |
| Spec 6 标题含 /、页签、思维导图等特殊结构 | 文本顺序/完整性差异 | 按 Type 分支处理；健康度抽样验收 |
| temp/ 索引库被误当数据源 | 重复/脏数据 | 只扫 data/，排除清单见 §1.3 |

## 2. 中文全文检索工程实现（M2/M3 检索质量备料）

### 2.1 hermes-agent：fts5_cjk 加载式分词器（CJK bigram）

来源：https://github.com/Adkid-Zephyr/hermes-agent/blob/main/native/fts5_cjk/fts5_cjk.c 、https://github.com/RohiRIK/hermes-agent/blob/add-microsoft-graph-security-catalog/hermes_state_fts.py 、https://raw.githubusercontent.com/NousResearch/hermes-agent/88b9d35ee05bc35783e68456a9c0f40ab151c6a9/hermes_state_search.py

- **问题**：unicode61 把连续 CJK 当 **1 个 token**，2 字词查询永远无法命中；原生 trigram 要求每词 ≥3 字符，**2 字中文/韩文词退化为 LIKE 全表扫**——hermes 在 6.8GB messages 表实测 3–6s/查询，是其 session_search 延迟第一来源。
- **方案**：约 250 行无依赖 C 扩展 `cjk_unicode61`，**包装 unicode61**：每个 token 内的最大 CJK run 重新发射为**重叠字符 bigram**（Lucene CJKAnalyzer 语义），run 长度=1 的孤立字发射 unigram，非 CJK 段原样透传。FTS5 把同一查询词连续发射的 token 变成 phrase → 查询词 bigram 序列 = **索引速度下的精确子串语义，低至 2 字词**。
- **构建/加载**：`gcc -shared -fPIC -O2 fts5_cjk.c -o libfts5_cjk.so`；`conn.load_extension(path)`；用法 `CREATE VIRTUAL TABLE t USING fts5(c, tokenize='cjk_unicode61')`，额外参数透传给 unicode61。
- **DDL（external content + 触发器同步）**：

```sql
CREATE VIEW messages_fts_cjk_src AS
  SELECT id, role, content, tool_name, tool_calls FROM messages WHERE role <> 'tool';
CREATE VIRTUAL TABLE messages_fts_cjk USING fts5(
  content, tool_name, tool_calls,
  content='messages_fts_cjk_src', content_rowid='id',
  tokenize='cjk_unicode61'
);
-- AFTER INSERT/UPDATE/DELETE 触发器维护（删除用特殊命令）：
INSERT INTO messages_fts_cjk(messages_fts_cjk, rowid, content, ...) VALUES ('delete', old.id, ...);
```

  渐进回填：state_meta 里 high_water/progress 两个标记 + 触发器 WHEN 条件，未索引区间用 `INSERT ... SELECT ... WHERE m.id > ? AND m.id <= ? AND NOT EXISTS (SELECT 1 FROM <fts>_docsize d WHERE d.id = m.id)` 分批灌入；tokenizer 缺失时**只禁用该表**（其余 FTS 正常）。
- **查询路由（_search_cjk）**：cjk 表（排除含孤立 1 字 CJK run 的查询，bigram 表只有 run≥2 才覆盖）→ trigram 表（每 token ≥3 CJK 字符才合格）→ LIKE 子串兜底（每词一个 `%term%` 子句）。
- **查询净化（_sanitize_fts5_query，可直接照抄）**：FTS5 特殊字符清单 `+{}():"^@/#&|~[]<>,;!?$=\\'\`；先保护成对引号短语再清洗；连字符/点号词（chat-send、P2.2）加引号防被当语法。
- **snippet**：FTS 路由用 `snippet(fts, -1, '>>>', '<<<', '...', 40)`；LIKE 路由用 `substr(m.content, max(1, instr(m.content, ?) - 40), 120)`（锚定首个命中词）。另有 OR-relaxed 重试（AND 全词无命中时降级 OR）与回填期 unindexed-gap 的 LIKE 补充。

### 2.2 dsh-knowledge-sqlite：trigram + L1 查询扩展（带实测数据）

来源：https://www.npmjs.com/package/dsh-knowledge-sqlite 、https://cdn.jsdelivr.net/npm/dsh-knowledge-sqlite@0.1.10/README.zh.md 、lib/store.js、lib/search.js（jsdelivr 镜像）

- **DDL**：`CREATE VIRTUAL TABLE items_fts_base USING fts5(content, tokenize='trigram')`（另有 items_fts_rich 索引富化字段）。trigram 分词器内建（SQLite ≥3.34），无需编译扩展——与 hermes 的 C 扩展互为"零编译"与"2 字词精确"的取舍。
- **grams 生成**：文档侧按 token 内 3-gram（非字母数字切分、ASCII 小写，即 FTS5 trigram 语义）；查询侧去空白标点后**整串** 3-gram 去重。
- **MATCH 构造（matchOf）**：所有 gram 全 OR 化，**quoted 单 gram 即子串语义**：`"知识" OR "识管" OR "管理"`；长查询超过 maxQueryTrigrams 时按 **idf 降序截断**（保留判别力高的稀有 gram，原型实测顺序截断 human A 65%→41%）。
- **检索 SQL**：

```sql
SELECT f.rowid, bm25(items_fts_base) AS score
FROM items_fts_base f JOIN items i ON i.rowid = f.rowid
WHERE f.items_fts_base MATCH ?          -- gram OR 化查询
  AND i.deleted = 0
  AND (i.ttl_seconds IS NULL OR ...)    -- TTL
  AND (scope/workspace 隔离条件)
ORDER BY score LIMIT ?;
```

- **snippet 替代（snippetOf，可直接借鉴）**：**不用 FTS5 snippet()**，自写：在原文里找最优匹配 gram 的首次位置，向前 12、向后 42 字符窗口截取，首尾加省略号（`…`）；另有 provenanceOf 判断命中来自原文还是富化字段。
- **L1 查询扩展**：每次查询一次小模型调用生成查询变体；按归一化查询缓存（persistent expansion_cache 表）；超时/失败**降级为纯词法检索**（带 degraded:'lexical' 元数据，绝不当工具错误）。
- **实测（该包评测套件）**：unicode61 基线 0%（无 CJK 子串能力）；L0 纯 trigram 改写难题集 7%、真实人工查询 65%；**L1 默认（trigram+查询扩展）21–64%（难题集）/ 65%（人工集，reasoner 变体 82%）**；L2 写入时富化 50%/88%。验收门槛 recall@1 难题集 ≥30%、人工集 ≥50%。

### 2.3 对本项目的落地细节：jieba 影子列 + trigram 兜底（M2/M3 可直接实现）

设计 §5 已定：主索引 unicode61 + content_jieba 影子列，查询分词组合短语，bm25 排序，可选 trigram 表兜底。结合 hermes/dsh 的工程实践，落地骨架：

**① 入库（jieba 切词写影子列）**

```sql
CREATE VIRTUAL TABLE chunks_fts USING fts5(
  text,                    -- unicode61 主列（英文/数字）
  content_jieba,           -- jieba 影子列（词空格连接）
  tokenize='unicode61'
);
-- 写入时（Python 侧）：
-- text = chunk 原文；content_jieba = ' '.join(jieba.lcut_for_search(text))
INSERT INTO chunks_fts(rowid, text, content_jieba) VALUES (?, ?, ?);
```

**② 查询（同源分词 → 短语组合）**

```python
words = jieba.lcut_for_search(query)          # 检索模式：细粒度召回
match = ' '.join(f'"{w}"' for w in words if w.strip())   # 词序列=短语（相邻出现）
rows = db.execute(
  "SELECT rowid, bm25(chunks_fts) FROM chunks_fts WHERE content_jieba MATCH ? ORDER BY bm25(chunks_fts) LIMIT ?",
  (match, topk))
```

  `lcut_for_search`（搜索引擎模式）比 `lcut` 细粒度更高、召回更好；词列必须**入库与查询同分词函数**，否则 phrase 不匹配。

  **实测修正（builder M2-2 验证，2026-11-06）**：FTS5 中多个独立引号串 `"a" "b" "c"` 是 **AND 语义**（词可散落命中，非相邻）；要表达相邻短语必须写成**单个字符串** `"a b c"`。因此 jieba 短语查询须把全部 token 拼进一个引号串，不能逐词各包引号。实现见 src/indexer/jieba_util.py。

**③ trigram 兜底表（按空间预算可选，设计 §5）**

```sql
CREATE VIRTUAL TABLE chunks_trigram USING fts5(text, tokenize='trigram');
```

  路由规则（借鉴 hermes _has_short_cjk_token / _trigram_eligible_tokens）：查询含 **<3 字的 CJK 词**（双字词、单字）或需要任意子串（代码、缩写）时走 trigram 表：gram OR 化（dsh matchOf 思路）或直接 `"词" OR "词" ` 子串语义；查询词全 ≥3 字且主索引零命中时也可兜底重试（hermes"零命中才重试，保住主索引排名"的做法）。

**④ snippet 自写（设计 §5 已定：不依赖 FTS5 snippet()）**：命中后定位首个命中词在 chunk 原文的位置，前后各取约 150 字、首尾省略号（设计口径）；dsh snippetOf 的"最优 gram 定位 ±窗口 + …"与 hermes 的 instr/substr 锚定实现都可参考。BM25 排序不变。

**⑤ 词典更新触发重索引**（§16 预留项）：影子列内容依赖分词结果，词典变了必须重切全部影子列；索引管线已有"按 rowid 增删改"能力，重索引 = 全量重灌 chunks_fts/chunks_trigram。

**⑥ 查询净化**：照抄 hermes _FTS5_SPECIAL_CHARS 清单做 MATCH 转义，防止用户查询含 `"`、`-`、`:` 等被 FTS5 当语法。

### 2.4 jieba 自定义词典加载

来源：https://github.com/fxsjy/jieba 、https://osgeo.cn/python-tutorial/jieba-user-dict.html

- **词典文件格式**：每行一词，`词语 [词频] [词性]` 空格分隔，顺序不可颠倒；**UTF-8**；词频省略时 jieba 自动计算"保证能分出该词"的词频；词频必须为整数。
- **加载**：`jieba.load_userdict(file_name)`（路径或文件对象），在**首次分词前**调用；动态加词 `jieba.add_word(word, freq=None, tag=None)`，删词 `jieba.del_word(word)`。
- **ChiselWall 落地**：部门词典（术语/黑话）作为配置文件随 agent 分发（如 `~/.chiselwall/userdict.txt`），agent 启动时 load_userdict；修改词典后触发 §2.3⑤ 重索引。注意 jieba 默认词典首次加载有 I/O 与内存开销，worker 常驻进程只需加载一次。

## 3. NATS 集成（M4 备料）

### 3.1 nats-py 客户端要点（官方示例归纳）

来源：https://raw.githubusercontent.com/nats-io/nats.py/refs/tags/v2.13.1/README.md 、https://examples.nats.io/examples/messaging/request-reply/python/ 、https://pypi.org/project/nats-py/

- 安装 `pip install nats-py`（asyncio，Python ≥3.8，v2.13 当前主线）；另有 nats-server 包用于测试环境管理 server 实例。
- 基本用法：`nc = await nats.connect("nats://host:4222")`；`sub = await nc.subscribe("foo", cb=handler)`；`await nc.publish("foo", b'Hello')`；响应方 `await msg.respond(reply_bytes)`（msg.reply 是收件箱 subject）；queue group：`nc.subscribe("help", "workers", cb)`（组内负载均衡，对应设计 §4"queue group 防重复"）。
- **请求-回复**：`rep = await nc.request("greet.joe", b'', timeout=0.5)`；异常 `from nats.errors import TimeoutError, NoRespondersError`；优雅退出 `await nc.drain()`。
- **广播问答的关键差异（务必注意）**：`request()` 底层只收**第一个**回复就返回——ChiselWall 的广播聚合（多节点各回一条）**不能**用单次 request()，应：publish 到 `chisel.query.broadcast`，同时 `subscribe("chisel.answer.<me>.<qid>", cb=collector)` 收集到 deadline 收口（设计 §4 语义不变，这是它的 nats-py 落法）。fetch 单文档可用 request()（点对点）。
- 重连：nats-py 自带重连（max_reconnect_attempts 等参数），对端断开后订阅需重新建立——注意消息处理幂等。

### 3.2 token / 用户鉴权

来源：https://docs.nats.io/running-a-nats-service/configuration/securing_nats/auth_intro/tokens 、https://docs.nats.io/using-nats/developer/connecting/token.md

- **服务器端两种形态**：
  - 单一共享 token：`authorization { token: "shared-secret-rotate-me" }`——服务器级秘密，**不能每用户区分**（设计 §11 要求每用户独立 token，故不用此形态做主鉴权）；
  - **每用户**（推荐，贴合设计）：`authorization { users: [ {user: "alice", password: "..."} ] }`；密码建议 **bcrypt 哈希**（明文启动会打 `[WRN] Plaintext passwords detected` 告警）；单用户时可省数组。`timeout` 控制认证时限（默认 2s）。
- **按 subject 授权（permissions）**：users 条目内 `permissions: { publish: [...], subscribe: [...] }`，另有 `default_permissions`；NATS 语法用单引号/双引号均可（`publish = "chisel.query.broadcast"` 与数组形式都合法）。来源：https://docs.nats.io/running-a-nats-service/configuration/securing_nats/authorization
- **客户端**：`await nats.connect(servers=["nats://host:4222"], token="mytoken")` 或 `user=... password=...` 参数；URL 内嵌凭证亦可。
- 安全提示（官方）：未知用户与错误密码返回同一个 Authorization Violation（不泄露哪半错）；凭证在 connect 时一次性提供，连接生命周期内固定。

### 3.3 TLS（内网自签场景）

来源：https://docs.nats.io/running-a-nats-service/configuration/securing_nats/tls

- **证书生成（官方步骤）**：自签 CA + 服务端证书，SAN 同时覆盖 DNS 与 127.0.0.1：

```bash
openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes   -keyout ca-key.pem -out ca.pem -subj "/CN=ChiselWall CA" -days 3650
openssl req -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes   -keyout server-key.pem -out server.csr -subj "/CN=nats.chisel.local"
openssl x509 -req -in server.csr -CA ca.pem -CAkey ca-key.pem   -CAcreateserial -out server-cert.pem -days 3650   -extfile <(printf "subjectAltName=DNS:nats.chisel.local,IP:127.0.0.1")
```

- **服务端配置**：顶层 `tls { cert_file, key_file, ca_file, timeout: 2 }`（只保护 client 连接；websocket/leafnode/cluster 各有独立 tls 子块，未配则明文）；`nats-server -t -c nats.conf` 先校验配置再启动。
- **客户端（nats-py）**：URL 用 `tls://` 自动建默认 ssl 上下文；**自签 CA 需显式传入**：`ssl_ctx = ssl.create_default_context(purpose=ssl.Purpose.SERVER_AUTH); ssl_ctx.load_verify_locations('ca.pem'); await nats.connect("tls://127.0.0.1:4443", tls=ssl_ctx, tls_hostname="nats.chisel.local")`。
- 与设计 §14 呼应：CA 证书随安装包内置或首启写入信任，客户端统一加载。

### 3.4 超时与 no-responders 语义（广播问答的三种结局）

来源：https://examples.nats.io/examples/messaging/request-reply/python/ 、https://docs.nats.io/learn/resilient-clients/request-reply-resilience（01 已引）、https://www.netdata.cloud/guides/nats/nats-no-responders-available/

- **三结局**：按时答复 / TimeoutError / **NoRespondersError**（服务器在主题无任何订阅者时**立即**返回 503，避免干等到超时）。
- 判别价值：**no-responders = 此刻没有应答者**（全员掉线/主题拼错）；**timeout = 有应答者但没按时回**（慢、卡），不是死亡证明。广播场景：publish 到无订阅者主题**不报错**（pub/sub 语义），只有 request() 才收到 no-responders——所以"在线人数"要靠 presence 心跳表（设计 §4 已有），而不是靠 no-responders。
- 主题大小写敏感（foo..bar 不匹配任何东西）；排查工具 /varz subscriptions。
- 重试必须幂等 + 退避（01 §2.3 已调研）。

### 3.5 nats-server 部署与配置样例（Windows / Linux）

来源：https://github.com/nats-io/nats-server/releases 、https://docs.nats.io/running-a-nats-service/configuration/securing_nats/tls 、authorization 文档同上

- **获取**：GitHub Releases 官方多平台二进制（Windows zip 含 nats-server.exe、Linux tar.gz 单文件）；解压即用，无依赖。
- **Windows**：`nats-server.exe -c nats.conf`（可前台/注册 Windows 服务）；防火墙放行 4222。
- **Linux**：`nats-server -c /etc/nats/nats.conf`，官方建议 systemd 托管（常开机器）。
- **ChiselWall 参考 nats.conf**（核心模式、不开 JetStream，按设计 §4/§11 汇总）：

```
# chiselwall-relay.conf —— 中心只路由、不持久化
listen: "0.0.0.0:4222"
server_name: chisel-relay
# 不开 JetStream（核心模式，知识不落盘）
authorization {
  timeout: 2
  users = [
    { user: "alice", password: "$2a$11$..."   # bcrypt 哈希
      permissions: {
        publish:   ["chisel.presence.alice", "chisel.query.broadcast",
                    "chisel.query.*.broadcast", "chisel.answer.*.*", "chisel.fetch.*"]
        subscribe: ["chisel.presence.>", "chisel.query.broadcast",
                    "chisel.query.*.broadcast", "chisel.answer.alice.>", "chisel.fetch.*"]
      } }
    # ... 每用户一条（也可用 $include 分文件管理）
  ]
}
# 内网自签 TLS（可选，证书见 §3.3）
# tls { cert_file: "server-cert.pem"  key_file: "server-key.pem"  ca_file: "ca.pem"  timeout: 2 }
```

  注意：设计 §11 的"每用户只能发布 query、订阅自己的 answer"用 permissions 逐用户配置即可实现；subject 里的用户名用变量模板由部署脚本生成（v1 无 JetStream、无 leafnode 需求）。**双机热备（§16）** = 两台 nats-server 加 cluster 块组集群，客户端配置多地址。

## 4. OpenAI 兼容接入（M5 备料）

### 4.1 OpenAI 兼容 chat 接口对接任意 base_url（内网模型）

来源：https://docs.vllm.ai/en/latest/examples/basic/online_serving/ 、https://platform.openai.com/docs/api-reference/chat/create（惯例）

- **接口形态**：POST `{base_url}/chat/completions`（OpenAI 客户端会自动补 /v1 前缀；如 vLLM 的 base_url 直接给 `http://host:8000/v1`）；请求体核心：`model`、`messages`（role=system/user/assistant）、`max_tokens`、`temperature`、`stream`（true 时 SSE、`data: [DONE]` 结尾）。
- **openai Python SDK 接内网**（vLLM 官方示例同款写法）：`client = OpenAI(api_key="EMPTY", base_url="http://localhost:8000/v1")` —— **api_key 可占位**，任意 OpenAI 兼容端点（vLLM/SGLang/自建网关）同样生效。设计 §15.3"内网模型只需配置 base_url + model + key"由此落为三配置项，无需改代码。
- **接入检查清单**（对内网模型验收）：/v1/models 是否可列；chat/completions 是否支持 stream、max_tokens；有无 /v1/embeddings（决定向量语义搜索能否走内网，设计 §15.3 待确认项）；是否有 reasoning/thinking 参数（影响提示词策略）。

### 4.2 Ollama：/v1/chat 与 /v1/embeddings

来源：https://docs.ollama.com/openai 、https://github.com/ollama/ollama（/v1 兼容端点自 0.1.24 起提供，见 ollama/pull/5470 相关讨论）

- **OpenAI 兼容入口**：`base_url="http://localhost:11434/v1"`，`api_key` **必填但本地被忽略**（惯例 `"ollama"`）；Python：`OpenAI(base_url='http://localhost:11434/v1/', api_key='ollama')`。
- **/v1/chat/completions 支持字段**：model、messages（文本/图片 base64/URL/多部件）、frequency_penalty、presence_penalty、response_format（JSON 模式）、seed、stop、stream、stream_options、include_usage、temperature、top_p、max_tokens、tools/tool_choice、logit_bias、user、n、reasoning_effort（思考模型；用 /api/show 查模型支持值）；/v1/models 列模型（owned_by 默认 "library"）。
- **/v1/embeddings 支持字段**：model、input（**string 或 array**）、encoding format、dimensions、user。原生端点 /api/embed 亦可用（02 已引）。embedding 模型如 `nomic-embed-text`、`bge-m3` 需先 ollama pull。
- 用途：设计 §10 的节点侧本地 LLM（预留）与语义搜索备选 embedding 服务（§15.3）都走同一 OpenAI 兼容客户端，一个实现两种接入。

### 4.3 DeepSeek API：参数与上下文长度（当前值）

来源：https://api-docs.deepseek.com/zh-cn/api/create-chat-completion 、https://api-docs.deepseek.com/zh-cn/quick_start/pricing

- **端点与模型**：base_url `https://api.deepseek.com`（OpenAI 兼容；另有 Anthropic 格式 `/anthropic`）。当前在售模型：`deepseek-flash`（DeepSeek-V4.1-Flash-1017）、`deepseek-v4-pro`（DeepSeek-V4-Pro-0813）。
- **上下文与输出**：**上下文长度 1M**；输出最大 384K；`max_tokens` 取值范围 1–393216，未设置时默认（非思考模式 8K、思考模式 64K、reasoning_effort=max 时 128K）。
- **思考模式**：`thinking: {type: "enabled"|"disabled"}` 或 `reasoning_effort: none|low|high|max`（默认 high）；输出含 `reasoning_content`（CoT，不计入 64K 上下文说法已随新模型更新——以当前 1M 上下文为准）。
- **其余参数**：stream + stream_options.include_usage（末尾块带全请求 usage）、response_format {type:"json_object"}（需提示词配合，识别失败会被空格替换）、stop（≤16 个）、temperature/top_p 等标准字段；JSON 模式与 finish_reason=length 截断语义见原文。
- **对设计的影响（需同步）**：设计 §10/§14 引用 deepseek-chat 64K 为旧型号信息；**L4 的 8K token 预算在 1M 上下文下宽裕**，可上调预算或保持保守默认均可。**DeepSeek 无 embedding 端点**（设计 §10 已规避，本地 ONNX 或内网 /embeddings）。

### 4.4 与设计 §10/§15.3 的映射

- provider 抽象：单 OpenAI 兼容客户端（openai SDK），base_url/model/api_key 三配置；DeepSeek=官方 base_url、Ollama=localhost:11434/v1、内网模型=内网 base_url——**同代码全覆盖**。
- 向量语义搜索（可选开关）：本地 ONNX（02 §2.5）为主；内网提供 /v1/embeddings 时切内网服务（配置项即可）。
- 节点侧本地 AI（预留）：Ollama 本地 LLM，客户端不变，model 换成本地模型名。

## 5. 来源清单

**思源 .sy**
- 官方 SY-FORMAT.zh-CN.md（格式规范全文）：https://github.com/siyuan-note/siyuan/blob/master/docs/SY-FORMAT.zh-CN.md
- 官方 WORKSPACE.zh-CN.md（工作区布局）：https://github.com/siyuan-note/siyuan/blob/master/docs/WORKSPACE.zh-CN.md
- siyuan README（内置 CLI）：https://github.com/siyuan-note/siyuan
- Lute：https://github.com/88250/lute ；protyle：https://github.com/88250/protyle
- Go 解析包：https://socket.dev/go/package/github.com/siyuan-note/parser ；https://githublb.vercel.app/repo/siyuan-note/dataparser
- .sy 权威指南解读文：https://blog.gitcode.com/501ee5573e68b1d2e09b0679f7b18c40.html
- DeepWiki 导出/导入与数据模型：https://deepwiki.com/siyuan-note/siyuan/3.5-export-and-import 、https://deepwiki.com/siyuan-note/siyuan/2.2-data-model-and-storage
- 社区工具 cli-anything-siyuan：https://raw.githubusercontent.com/HKUDS/CLI-Anything/main/siyuan/agent-harness/cli_anything/siyuan/skills/SKILL.md ；cocoloop 技能：https://hub.cocoloop.cn/skills/45677
- 链滴讨论（.sy=JSON、导出 API 路线）：https://ld246.com/article/1662720201124 、https://ld246.com/article/1768708383832/comment/1768708576867

**中文检索**
- hermes-agent fts5_cjk.c：https://github.com/Adkid-Zephyr/hermes-agent/blob/main/native/fts5_cjk/fts5_cjk.c
- hermes_state_fts.py（DDL/触发器/回填）：https://github.com/RohiRIK/hermes-agent/blob/add-microsoft-graph-security-catalog/hermes_state_fts.py
- hermes_state_search.py（查询路由/净化/snippet）：https://raw.githubusercontent.com/NousResearch/hermes-agent/88b9d35ee05bc35783e68456a9c0f40ab151c6a9/hermes_state_search.py
- hermes CJK 问题 issue：https://github.com/NousResearch/hermes-agent/issues/14829
- dsh-knowledge-sqlite：https://www.npmjs.com/package/dsh-knowledge-sqlite ；README.zh：https://cdn.jsdelivr.net/npm/dsh-knowledge-sqlite@0.1.10/README.zh.md ；lib/store.js、lib/search.js：https://cdn.jsdelivr.net/npm/dsh-knowledge-sqlite@0.1.10/
- jieba：https://github.com/fxsjy/jieba ；自定义词典教程：https://osgeo.cn/python-tutorial/jieba-user-dict.html
- SQLite FTS5（承接 02）：https://sqlite.org/fts5.html

**NATS**
- nats-py README：https://raw.githubusercontent.com/nats-io/nats.py/refs/tags/v2.13.1/README.md ；PyPI：https://pypi.org/project/nats-py/
- Request-Reply Python 官方示例：https://examples.nats.io/examples/messaging/request-reply/python/
- 认证基础（token/users）：https://docs.nats.io/running-a-nats-service/configuration/securing_nats/auth_intro/tokens
- 授权（permissions）：https://docs.nats.io/running-a-nats-service/configuration/securing_nats/authorization
- TLS：https://docs.nats.io/running-a-nats-service/configuration/securing_nats/tls
- token 客户端连接：https://docs.nats.io/using-nats/developer/connecting/token.md
- no-responders 排查：https://www.netdata.cloud/guides/nats/nats-no-responders-available/
- 发行版下载：https://github.com/nats-io/nats-server/releases

**AI 接入**
- DeepSeek Chat Completions：https://api-docs.deepseek.com/zh-cn/api/create-chat-completion ；模型与价格（1M 上下文）：https://api-docs.deepseek.com/zh-cn/quick_start/pricing
- Ollama OpenAI 兼容：https://docs.ollama.com/openai ；v1 端点引入讨论：https://github.com/ollama/ollama/pull/5470
- vLLM OpenAI 客户端示例（base_url 任意指向）：https://docs.vllm.ai/en/latest/examples/basic/online_serving/

## 附：原始抓取归档

本次调研的官方文档/源码原文（浏览器抓取）存于 docs/research/_raw/：sy-format-zh-cn.md、siyuan-workspace.md、siyuan-readme.md、hermes-fts5-cjk.c、hermes_state_fts.py、hermes_state_search.py、dsh-knowledge-readme-zh.md、dsh-store.js、dsh-search.js、nats-py-readme.md、nats-request-reply-python.txt、nats-token-auth.txt、nats-tls.txt、ollama-openai-docs.txt、deepseek-chat-completion.txt、deepseek-pricing.txt。
