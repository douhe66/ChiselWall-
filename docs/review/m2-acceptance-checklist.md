# M2 验收检查清单（凿壁 ChiselWall）

> 文档性质：reviewer 编写的**可执行验收清单**——把 design-v1 §13 的 M2 验收标准拆成一条条可执行、可判定的测试用例。
> 依据：docs/design/design-v1.md（§5 本地索引、§6 连接器、§7 排除规则、§9 UI、§11 安全、§12 资源保护、§13 验收）、docs/research/02-local-index-and-ai.md（§1 FTS5、§3 解析坑、§5 增量与资源控制）。
> 配套文档：docs/review/m2-testdata-plan.md（测试语料规划，本清单中所有 fixture 均引用该方案的命名）。
> M2 交付范围：目录 + Obsidian 连接器、解析、chunk、FTS5+jieba 索引、排除规则、本地搜索 API + 最小 UI。**不含**：思源连接器（M3）、relay/随手问（M4）、AI（M5）、打包（M6）。

## 0. 判定与执行规则

| 项 | 规则 |
|---|---|
| 优先级 | **P0** = 验收阻断项，必须全部通过；**P1** = 重要项，不通过须记录原因 + 修复计划，或由 reviewer/lead 明确豁免；**P2** = 增强项，记录并跟踪 |
| 平台要求 | P0 用例在 **Windows 全量执行**；Linux 至少执行「挂载/索引/检索/排除/增量」核心子集（见 §7 AC-07-10）；§7 跨平台专项用例双平台各执行一遍 |
| 证据要求 | 每条用例记录：平台 / 结果(通过·失败·N/A) / 证据（截图、日志、测量数据、DB 查询结果）；N/A 须注明原因 |
| 数值达标线 | AC-03-06~11 数值已由 **lead 确认采纳（写入设计 §12）**，与机制项一同作为 M2 达标判据；其余条目均为设计文档明文约束 |
| 判定方法 | 「搜得到/搜不到」一律用 fixture 的**唯一标记词**断言（见 m2-testdata-plan §3），避免自然词歧义 |

验收结论分档：**通过**（P0 全过 + P1 均有结论）；**有条件通过**（P0 全过，个别 P1 挂起并有记录）；**不通过**（任一 P0 失败）。

---

## 1. AC-01 挂载与索引 ——「自己机器挂目录能搜到」

通用断言手法：对每个 fixture 检索其唯一标记词，期望恰好命中该文件（doc_id/rel_path 指向它），且命中结果含约 300 字上下文片段、出处字段齐全。

### 1.1 格式覆盖（每格式 = 1 条用例，P0 全为阻断项）

| 编号 | 格式 | 步骤要点 | 期望 |
|---|---|---|---|
| AC-01-01 | md | 挂载 corpus/md/，检索 `凿壁测试词·风`（数据库优化笔记.md 的标记词） | 命中且 rel_path 正确 |
| AC-01-02 | txt(UTF-8) | 挂载 corpus/txt/，检索「凿壁测试词·花」 | 命中 |
| AC-01-03 | txt(GBK) | 挂载 edge/gbk.txt，检索其标记词「凿壁测试词·编码」 | 命中（验证 charset 检测：GBK 文件被正确解码入库，且不产生乱码 chunk） |
| AC-01-04 | docx | 挂载 corpus/docx/，检索「凿壁测试词·雪」 | 命中，片段来自正文段落 |
| AC-01-05 | xlsx 多 sheet | 挂载 corpus/xlsx/环境配置清单.xlsx（sheet1 标记词「凿壁测试词·月」、sheet2「凿壁测试词·星」），分别检索 | 两个标记词**都**命中同一 doc（验证多 sheet 全部索引） |
| AC-01-06 | pdf | 挂载 corpus/pdf/架构评审纪要.pdf，检索「凿壁测试词·云」 | 命中（有文本层 PDF） |
| AC-01-07 | csv | 挂载 corpus/csv/，检索「凿壁测试词·山」 | 命中 |
| AC-01-08 | Obsidian vault | 挂载 corpus/vault/，检索「凿壁测试词·林」（笔记A.md） | 命中；同时检索 .obsidian/app.json 内的标记词「凿壁测试词·藏」**必须 0 命中**（.obsidian 默认排除） |
| AC-01-09 | 单文件挂载 | 只挂载 corpus/md/项目周报-2024Q4.md（不挂目录），检索其标记词 | 命中，且**只有这一个文件**被索引（统计=1） |

### 1.2 检索引擎正确性（FTS5 + jieba 影子列，§5）

| 编号 | 优先级 | 用例 | 期望 |
|---|---|---|---|
| AC-01-10 | P0 | 英文检索：检索 `postgres`（fixture 代码片段笔记.md 内含） | 命中（unicode61 主列） |
| AC-01-11 | P0 | 数字检索：检索 `cwfix_num_20240101` | 命中（数字 token） |
| AC-01-12 | P0 | 中文词检索：检索 2–4 字真实词（如「数据库」「增量索引」，fixture 含） | 命中且不返回无关文档（jieba 影子列**主索引路径，P0 阻断**；非全文 LIKE 扫库） |
| AC-01-13 | P1 | 短语检索：检索「数据库连接池」（fixture A 相邻出现、fixture B 两词分离出现） | 仅命中 A（或 A 显著排前）；B 不因含两个词而命中——验证「切词组合为 FTS5 短语查询，词序列需相邻」 |
| AC-01-14 | P1 | trigram 兜底（lead 已决：**默认关闭、--trigram 可配开启、仅 <100K 字符小文档写入**）：开启时检索 **9 字符真子串**（如 `cwfix_tri_a`，>8 避开 LIKE 兜底） | 默认配置：trigram 表 0 行，整 token 检索走 route=jieba 主索引；开启配置：小文档真子串命中且 route=trigram、大文档 trigram 0 行、db/文本比增幅 ≤2% 量级（500% 线内） |
| AC-01-15 | P1 | bm25 排序：fixture 三文档含同一关键词 1/3/10 次 | 命中次数多的排序在前 |
| AC-01-16 | P0 | 上下文片段：长文档（markdown长文.md）中检索其标记词 | 返回片段约 300 字、包含关键词前后文字（±150 字窗口）、首尾有省略号、关键词有高亮标记（<em> 或其他实现约定标记）；多个命中取得分最高片段 |
| AC-01-17 | P1 | 片段出处：任何命中结果 | 含 doc_id、title、rel_path、score、snippet 字段，rel_path 为挂载点下的相对路径 |

**AC-01-13 代码级检查点（builder 实测修正，2026-11-06，调研 03 §2.3②）**：FTS5 中多个独立引号串 `"a" "b" "c"` 是 **AND 语义**（词可散落命中），相邻短语必须写成**单个字符串** `"a b c"`。验收时须直接审读 searcher 的 MATCH 构造源码：查询分词结果须拼进一个引号串（如 `'"' + ' '.join(tokens) + '"'`），不得用逐 token 包裹（`" ".join([fts_quote(t) for t in tokens])`）——后者会使 AC-01-13 的 fixture B（词散落）误命中而判失败。当前生产代码 jieba_util.py 仅有逐 token 的 fts_quote，单串拼接逻辑在测试辅助函数 tests/test_indexer.py:phrase() 中，M2-4 的 searcher 必须复用同一写法（建议 builder 将其提升为 jieba_util 的公开函数并被测试直接复用，防止两处实现漂移）。

### 1.3 chunk 策略（§5 明文约束）

| 编号 | 优先级 | 用例 | 期望 |
|---|---|---|---|
| AC-01-18 | P1 | chunk 边界 | 只读查询 chiselwall.db：`SELECT COUNT(*), MIN(LENGTH(text)), MAX(LENGTH(text)) FROM chunks WHERE doc_id=?` —— 单块 500–800 字、相邻块重叠约 50 字（抽查首尾字符比对） |
| AC-01-19 | P1 | 单文件文本上限 5M 字符 | 挂载 generated/big_over_cap.md（5M 字符边界前标记词 cwfix_big_a、边界后 cwfix_big_b）：A 命中、B 不命中、无崩溃 |
| AC-01-20 | P1 | 每文档 chunk 上限 2000 | 挂载 generated/many_chunks.md（>2000 块）：不崩溃、该文档 chunk 数 = 2000（或按实现说明截断），超限部分不索引 |
| AC-01-21 | P2 | 结构切分 | md 标题/段落边界切分合理：命中片段可定位到正确章节（标题内容进入其所在 chunk） |

### 1.4 索引统计与正确性

| 编号 | 优先级 | 用例 | 期望 |
|---|---|---|---|
| AC-01-22 | P0 | 文档统计 | 挂载 corpus 全量后：UI/API 统计「已索引 N 篇」与 fixture 实际文件数一致（排除默认排除项后） |
| AC-01-23 | P0 | 索引库落地 | 挂载并完成索引后，本地存在单文件 chiselwall.db，含 docs/chunks/chunks_fts 表结构（§5），可被 sqlite3 只读打开 |
| AC-01-24 | P1 | 挂载持久化 | 重启 agent 后挂载列表保留，无需重挂载；索引库复用（不重复全量重建——对比重启前后 chunks 计数） |

---

## 2. AC-02 排除规则生效矩阵 ——「排除生效」（§7）

**通用断言手法（每条都执行；M2 实测后修正）**：① 被排除 fixture 的目标 **rel_path 不出现在检索结果中**——注意：检索降级链（jieba 短语→AND→trigram OR→LIKE）会使共享前缀/共享 3-gram 的词互相命中，**「整词 0 命中」在混合语料中不可靠**，以「目标不可见」为准；② 同目录**未被排除**的对照文件标记词可命中（验证排除的隔离性、不误伤）；③ UI/API 统计与排除规则面板状态正确；④ DB 中该文件无 chunk 记录（docs.status=excluded 且 chunks 摘除，红线⑤）。

| 编号 | 排除类型 | fixture | 优先级 |
|---|---|---|---|
| AC-02-01 | **单文件**排除 | exclude/single_file/被排除-敏感工资表.xlsx（标记词「凿壁敏感词·薪」）vs 同目录对照-正常文件.md | P0 |
| AC-02-02 | **整目录**排除 | exclude/dir/secret_dir/（含「凿壁敏感词·密」）vs 同层对照-正常文件.md | P0 |
| AC-02-03a | **glob·扩展名** | 规则 `*.log`：exclude/glob/debug-2024.log | P0 |
| AC-02-03b | **glob·前缀** | 规则 `secret*`：exclude/glob/secret-draft.md | P0 |
| AC-02-03c | **glob·子目录递归** | 规则 `**/drafts/*`：exclude/glob/drafts/草稿1.md | P0 |
| AC-02-04 | **默认·隐藏文件/目录** | edge/.hidden.md、edge/defaults/ 下点开头目录（无需配置任何规则） | P0 |
| AC-02-05 | **默认·工程目录** | edge/defaults/project/ 下 node_modules/、.git/、.svn/；corpus/vault/.obsidian/ | P0 |
| AC-02-06 | **默认·临时文件** | edge/~$幽灵.docx（`~$` 前缀）、exclude/glob/临时笔记.tmp（`*.tmp`） | P0 |
| AC-02-07 | **默认·大文件/二进制** | generated/big_binary_50mb.bin、big_binary_21mb.bin（**阈值 20MB 可配置，lead 已确认**：>20MB 默认排除，21MB 为阈值上边界样例）；edge/binary.png、binary.zip（二进制特征） | P1（阈值须可在设置中修改，改后生效） |
| AC-02-08 | **不可见性**（§7 核心） | 对 AC-02-01~02 的排除对象：①检索不命中；②统计不含；③**本地全文获取 API 也不可得**（M2 若尚无 fetch 接口则检查检索 API 无法通过 doc_id 取到其内容，并登记该点待 M4 复核） | P0 |
| AC-02-09 | **排除后增量生效** | 先挂载+索引完成 → 再对某已索引文件添加排除规则 → 防抖+处理周期后：其标记词 0 命中、统计 -1，且**无需**重挂载/重启 | P0 |
| AC-02-10 | **规则持久化** | 添加若干排除规则 → 重启 agent → 规则仍在且继续生效 | P1 |
| AC-02-11 | **解除排除恢复** | 删除某排除规则 → 对应文件重新可搜（增量重建） | P1 |
| AC-02-12 | **排除优先级语义** | 目录被排除但其下某文件被单独挂载（§7 挂载=添加目录**或单文件**）：单文件挂载源不受目录排除规则影响；反之挂载目录下按规则排除子项——按实现文档核对语义并记录 | P2 |

---

## 3. AC-03 资源占用测量 ——「CPU/内存占用达标」（§12）

### 3.1 psutil 采样脚本思路（验收测量标准做法）

```python
# tests/perf_measure.py 思路（验收时由 reviewer 执行）
import psutil, time, json, sys
AGENT_PID = int(sys.argv[1]); DURATION = float(sys.argv[2])
proc = psutil.Process(AGENT_PID)
proc.cpu_percent(interval=None)          # 预热：首次调用必返回 0.0，必须丢弃
t0, samples = time.time(), []
while time.time() - t0 < DURATION:
    me  = proc.cpu_percent(interval=None)     # 自上次调用以来的均值
    kids = sum((p.cpu_percent(interval=None) or 0.0)
               for p in proc.children(recursive=True))
    r = proc.memory_info()
    samples.append({"t": round(time.time()-t0, 1),
                    "cpu_pct": round(me + kids, 1),     # 进程树合计
                    "rss_mb": round(r.rss/1048576, 1),
                    "vms_mb": round(r.vms/1048576, 1),
                    "threads": proc.num_threads()})
    time.sleep(1)
# 汇总输出 JSON：cpu_p50/p95/max、rss_peak、rss_avg、threads_max → 存入验收记录
```

**三段测量场景**（每段独立运行、分别记录）：① **基线**（空闲 60s，无挂载或索引已完成）；② **索引期**（触发全量索引起，至「已索引 N 篇」稳定为止——fixture 语料需保证索引持续 ≥60s，不足则叠加挂载多份语料）；③ **索引后空闲**（60s）。检索延迟在索引期与空闲期各测一组（100 次关键词检索，记录 P50/P95）。

**采样要点与陷阱**（写进验收记录）：
- 进程树合计（uvicorn/worker 子进程），否则漏计；Windows 下多进程 CPU 之和可 >100%（多核），属正常；
- RSS 即 Windows working set / Linux 驻留内存；psutil 首次 cpu_percent 返回 0.0 需预热丢弃；
- 「空闲调度」验证：索引进行中人工制造系统高负载（如跑满 2 核的循环），观察索引推进明显放缓/暂停、负载解除后恢复；
- 「检索优先」验证：索引进行中连续发检索请求，检索 P95 不明显劣化（见达标线）。

### 3.2 达标线

**机制项（§12 明文，P0 阻断）**：

| 编号 | 检查点 | 期望 |
|---|---|---|
| AC-03-01 | 单线程索引 worker | 索引期 threads 不因文件数增长（无并行索引）；同时最多 1 个文件在解析/写入（日志或线程数证据） |
| AC-03-02 | 进程低优先级 | Windows：psutil nice 值 = 16384（BELOW_NORMAL_PRIORITY_CLASS）；Linux：nice > 0 |
| AC-03-03 | 空闲调度 | 仅在 CPU 低负载/空闲时推进索引队列（§12「索引让路」）；有可观察证据（日志、采样时 CPU 关系） |
| AC-03-04 | 检索优先于索引 | 收到检索请求时索引让路：见 3.1 检索优先测量 |
| AC-03-05 | 批事务 | 索引写入按批提交（非每文件一次 fsync）：日志或 DB 写入行为证据 |

**数值项（lead 已确认采纳并写入设计 §12，作为 M2 达标判据；P1 判定）**：

| 编号 | 指标 | 建议达标线（4C8G 参考机） |
|---|---|---|
| AC-03-06 | 空闲 CPU | 采样均值 < 1% |
| AC-03-07 | 空闲内存 | RSS 相对 agent 启动基线增量 ≤ 200MB（不含可选 embedding/向量模型，M2 默认未开） |
| AC-03-08 | 索引期 CPU | 进程树均值 ≤ 50%（≈2 核），无持续 >30s 的单核满载以上现象（单线程 worker 理论单核 25%） |
| AC-03-09 | 索引期内存 | RSS 峰值增量 ≤ 1GB（含解析临时内存），索引结束后 5 分钟内回落至空闲线 |
| AC-03-10 | 检索延迟 | 本地关键词检索 P50 ≤ 30ms、P95 ≤ 100ms（10 万 chunk 规模内）；索引期检索 P95 ≤ 2× 空闲期 P95 |
| AC-03-11 | 磁盘 | chiselwall.db 体积 ≤ 索引文本总体积的 **500%**（中文词级正式达标线，lead 裁定：构成 ≈ 原文 100% + 中文 FTS 索引 300%；原 50% 口径废弃，contentless 极简索引登记 §16 预留） |

---

## 4. AC-04 解析失败隔离（§6「解析失败跳过并记录」）

| 编号 | 优先级 | 用例 | 期望 |
|---|---|---|---|
| AC-04-01 | P0 | 单文件失败不阻断整体 | 挂载含 edge/corrupted.docx + corpus 正常文件的目录：索引**完成**，正常文件全部可搜 |
| AC-04-02 | P0 | 失败列表 | 失败文件出现在失败列表（UI + API），含路径与失败原因；统计中「失败 N」与列表一致 |
| AC-04-03 | P0 | 加密 PDF | edge/encrypted.pdf：跳过、入失败列表（原因≈需密码/加密）、无崩溃 |
| AC-04-04 | P0 | 无文本层 PDF | edge/scanned_no_text.pdf：跳过、入失败列表（原因≈无文本层/OCR 不做）、无崩溃 |
| AC-04-05 | P1 | 旧格式 | edge/old_format.doc、old_format.xls：跳过并提示（§6「.doc/.xls 旧格式跳过并提示」），不阻塞 |
| AC-04-06 | P1 | 其他损坏 | edge/binary.png、binary.zip：跳过（不产生乱码 chunk、不产生乱码命中）；空文件 edge/empty.md、empty.txt：不报错、不产生空 chunk |
| AC-04-07 | P1 | 失败后恢复 | 修复文件（用正常 docx 覆盖 corrupted.docx）→ 增量索引后该文件从失败列表移除、内容可搜 |
| AC-04-08 | P1 | 失败列表持久化 | 重启 agent 后失败列表保留（未修复的失败项仍在） |

---

## 5. AC-05 增量索引（§5 watchdog + 防抖 + hash 校验）

以下用例在挂载目录上进行，所有「新内容可搜」的等待上限取 **防抖 3–5s + 处理时间 + 30s 余量**。

| 编号 | 优先级 | 用例 | 期望 |
|---|---|---|---|
| AC-05-01 | P0 | 新增文件 | 挂载目录中新建 new_note.md（含新标记词） | 无需任何手动操作，≤40s 内可搜到；统计 +1 |
| AC-05-02 | P0 | 修改文件 | 将某已索引 md 的标记词 A 改为 B | B 可搜、A 不可搜（旧内容不残留） |
| AC-05-03 | P0 | 删除文件 | 删除某已索引文件 | 其标记词不可搜、统计 -1 |
| AC-05-04 | P1 | 重命名/移动 | 文件改名/移入子目录 | 内容标记词仍可搜、rel_path 更新、不出现重复结果（同 doc 只一份） |
| AC-05-05 | P1 | hash 校验防重建 | 用 copy 覆盖同一文件（内容不变、mtime 变） | 不触发重建：chunks 表该文档数据无变化（或日志显示跳过） |
| AC-05-06 | P1 | 启动全量校验 | agent 停止期间手动增删改文件，重启 agent | 索引状态收敛：新增可搜、删除不搜、修改为新内容 |

---

## 6. AC-06 Windows / Linux 兼容检查点（需求特性 2「系统兼容性」）

| 编号 | 优先级 | 检查点 | 期望 |
|---|---|---|---|
| AC-06-01 | P0 | 依赖安装 | 双平台 `pip install -r requirements.txt` 成功；Python 标准库 sqlite3 可创建 FTS5 虚拟表（`CREATE VIRTUAL TABLE t USING fts5(x)`） |
| AC-06-02 | P0 | 路径兼容 | 挂载含**中文路径、空格路径、长路径**的目录（corpus/paths/）：全部可索引、可命中 |
| AC-06-03 | P1 | 大小写 | Windows 上同一文件以不同大小写路径引用：不产生重复索引记录 |
| AC-06-04 | P0 | 打开所在位置·Windows | `explorer /select,"<path>"`：资源管理器打开且**该文件被选中高亮**；路径含中文/空格时同样成功 |
| AC-06-05 | P1 | 打开所在位置·Linux | xdg-open 打开所在目录（或 DBus org.freedesktop.FileManager1 ShowItems 高亮，不强求） |
| AC-06-06 | P1 | watchdog 后端 | AC-05 系列在 Linux 各重跑一遍（inotify 与 ReadDirectoryChangesW 行为一致） |
| AC-06-07 | P1 | 优先级机制 | 双平台验证 AC-03-02（Windows priority class / Linux nice） |
| AC-06-08 | P1 | 路径规范化 | chiselwall.db 中 rel_path 统一用 `/` 分隔、以挂载点为根，双平台格式一致（DB 只读查询断言） |
| AC-06-09 | P2 | 非 ASCII 文件名 | emoji/生僻字文件名（generated/ 下生成）可索引、可「打开所在位置」 |
| AC-06-10 | P0 | 核心子集双平台 | Linux 上至少执行：AC-01-01/02/10/11、AC-02-01~02-05、AC-04-01、AC-05-01~03、AC-07 核心 UI 项 |

---

## 7. AC-07 最小 UI 功能清单（§9，M2 范围 = 挂载/排除/搜索/统计/打开所在位置）

验证方式：浏览器访问 127.0.0.1 页面手工操作 + 截图留证；UI 操作对应的 API 行为可同时核对。

| 编号 | 优先级 | 功能 | 用例 | 期望 |
|---|---|---|---|---|
| AC-07-01 | P0 | 挂载目录 | 添加目录 → 出现在挂载列表 → 索引启动、进度可见 | 挂载后该目录内容逐步可搜 |
| AC-07-02 | P0 | 挂载单文件 | 添加单个文件 | 仅该文件被索引 |
| AC-07-03 | P0 | 移除挂载 | 移除某挂载源 | 其内容从索引清除（标记词不可搜）、列表更新 |
| AC-07-04 | P0 | 排除入口 | 右键/菜单对**单文件**、**目录**添加排除；排除规则面板可添加 **glob** 规则 | 与 AC-02 矩阵行为一致 |
| AC-07-05 | P0 | 排除规则面板 | 列出全部规则（类型/对象）、可删除规则 | 删除后恢复可搜（AC-02-11） |
| AC-07-06 | P0 | 搜索框与结果 | 输入关键词 → 结果列表：标题 + 高亮片段 + 出处（相对路径）+ 得分排序 | 结果与 API 一致 |
| AC-07-07 | P0 | 统计与失败列表 | 已索引 N 篇 / 待处理 / 失败列表（含原因）可见 | 数字与 fixture 实际一致 |
| AC-07-08 | P0 | 打开所在位置 | 对命中结果点击「打开所在位置」 | 文件管理器定位并选中该文件（§9：Windows explorer /select；Linux xdg-open） |
| AC-07-09 | P1 | 直接打开 | 「直接打开」用系统默认程序打开原文件 | 文件可正常打开、**内容未被修改**（对比打开前后 hash） |
| AC-07-10 | P1 | 本地搜索 API | HTTP API（端点命名以实现为准）：`GET /search?q=…&limit=…` | 返回 JSON 数组，每条含 doc_id/title/snippet/score/rel_path；limit 生效；空结果返回空数组；非法参数不 500 |
| AC-07-11 | P1 | 只监听 127.0.0.1 | netstat 确认监听地址 | UI/API 仅 127.0.0.1（§11 安全基线） |
| AC-07-12 | P0 | 本地 API token | §11「随机 token」：未带/带错 token 的请求被拒 | 401/403；**M2 必做（lead 已确认）**，UI 请求须携带 token，不得无鉴权放行 |
| AC-07-13 | P2 | 文档预览（只读） | 点击结果预览文档内容、标注是否参与共享 | 预览只读、不改动原文件（完整版归 M3 验收，M2 有则记、无则 N/A） |

---

## 8. 验收记录模板（每条用例一行）

```text
编号 | 平台 | 结果 | 证据(截图/日志/测量JSON/DB查询) | 备注(失败原因/豁免决定/N-A理由)
```

## 9.1 M3 回归基线增补（M2-FIX2 遗留，lead 指示纳入）

| 编号 | 优先级 | 用例 | 期望 |
|---|---|---|---|
| AC-08-14 | P0 | **旧库迁移回归**（M2-FIX2）：用旧 schema 建库（chunks 带 text_jieba NOT NULL 列 + 少量已索引数据）→ 启动新代码 | 启动时自动 ALTER 迁移（列被移除）、不报 IntegrityError、旧数据可检索、新文件 set_chunks 正常（含大文档分批路径） |
| AC-08-15 | P1 | **不支持格式排除轨道**（F7 落地）：挂载含 .json/.log 等未知扩展名文件的目录 | 文件进入 docs 排除轨道：status=excluded、原因含 unsupported_format 与扩展名；计入 stats.excluded；搜索不可见；UI 可见（挂载行或排除列表提示「N 个不支持格式」） |
| AC-08-16 | P2 | UI 统计项观感（M2-FIX2）：顶栏「已索引/待处理/失败/排除/挂载」为纯文本标签 | 无按钮观感（无 hover 态误导）；页面可点击元素仅真实按钮/链接 |
| AC-08-17 | P1 | **直接打开健壮性**（M2-FIX3）：Windows 下 /api/docs/{id}/open 与 UI「直接打开」 | 返回体含 ok/how/**hint** 字段；.ext 有关联 → os.startfile 正常打开；无关联 → 回退 OpenAs_RunDLL 弹出「打开方式」框且 hint 文案到位（不再静默无反应）；startfile 抛异常同样回退；UI 以 toast 展示成功/失败信息；Linux xdg-open 路径不受影响 |

## 10. 元宝 JSON 对话连接器验收用例（M3，用户已确认立项）

> 基准结构（第三方导出工具样例，lisoleg/yuanbao-exporter）：{ exportTime, conversations: [ { id, title, messages: [ { role, content }, { role:"assistant", content, thinking } ] } ] }。
> **字段语义以 builder 基于用户真实样本的实现文档为准**：以下用例按「结构变体矩阵」设计，验收时与实现文档逐条对照；builder 须提供 1-2 份脱敏真实导出样本入 fixtures（替代/补充以下合成样例）。

| 编号 | 优先级 | 用例 | 期望 |
|---|---|---|---|
| YB-01 | P0 | **结构识别·正例A**：标准对象结构（exportTime+conversations+messages+role/content） | 识别为对话文档：title=对话标题、正文含各消息内容、可检索 |
| YB-02 | P0 | **结构识别·正例B/C**：顶层数组结构（[ {title,messages} ]）与单对话对象（无 conversations 包裹） | 按实现文档支持则正常解析；不支持则明确记录并跳过（不得崩溃） |
| YB-03 | P0 | **结构识别·反例**：普通配置 JSON（键值对）、数字数组、嵌套业务 JSON（如 Obsidian app.json） | 不误识别为对话（标题/结构不产生伪对话 chunk）；处置按实现文档（纯文本索引或跳过），不崩溃 |
| YB-04 | P0 | **speech 回退**：assistant 消息 content 缺失/为空、speech 有文本 | 回退取 speech 文本入库，标记词可搜 |
| YB-05 | P1 | speech 数组形态（speeches:[…]）；content 与 speech 并存；两者皆空 | 并存取 content 优先不重复；数组形态提取拼接（按实现语义）；皆空跳过该消息不产生空 chunk |
| YB-06 | P1 | **编码异常**：UTF-8 BOM / GBK / UTF-16 / 截断 UTF-8 序列 | 前三者正常解码入库；非法字节不崩溃、进失败列表或跳过 |
| YB-07 | P0 | **坏 JSON 隔离**：截断 JSON、语法错误 JSON | ParseError 进失败列表（含原因），不阻断整体索引；空 .json 走空文件默认排除 |
| YB-08 | P1 | JSON 数组混入 null/数字/字符串元素 | 正常元素入库、异常元素跳过（或整体失败按实现语义），不崩溃 |
| YB-09 | P1 | **md 孪生去重**：同一对话的 .json 与 .md 导出同时挂载 | 搜索同一对话标记词：按实现文档的去重策略断言（内容哈希/标题匹配），不出现并排重复片段；**若未实现去重须在报告登记为已知风险** |
| YB-10 | P1 | 孪生去重不误伤：不同对话的 json/md 各自独立命中 | 不去重误伤 |
| YB-11 | P0 | **索引后搜索命中**：对话中独特标记词 | 可搜，rel_path 指向 .json，title=对话标题，route 主索引 |
| YB-12 | P1 | **片段质量**：命中句 ±150 字窗口含对话上下文、<em> 高亮、首尾省略号；多轮长对话 chunk 切分按段落边界；解析文本保留角色信息（如 [我]/[元宝] 前缀，按实现语义） | 片段可读、高亮正确、无串轮乱序 |
| YB-13 | P1 | 增量：json 文件修改/删除 | 修改后重建（旧词失效）、删除后不可搜（沿用 AC-05 断言法） |

## 9. 待确认项（状态跟踪，lead 已裁决更新）

| # | 事项 | 状态 |
|---|---|---|
| 1 | 数值达标线（AC-03-06~11，4C8G 参考机） | ✅ 已采纳，写入设计 §12，作为 M2 达标判据 |
| 2 | trigram 兜底表 M2 是否实现 | ✅ 已决：M2 降为 P1 可选（AC-01-14 未实现记 N/A）；jieba 主索引 P0 必做 |
| 3 | 本地 API token 是否 M2 做 | ✅ 已决：M2 必做，AC-07-12 升为 P0 |
| 4 | 超大文件默认排除阈值 | ✅ 已决：**20MB（可配置）**；文本截断 5MB 不变（AC-01-19、AC-02-07） |
| 5 | .doc/.xls 真实样本与思源工作空间样本 | ⏳ 挂起：lead 将请用户提供，到位后放入 tests/fixtures/edge/real/，AC-04-05 证据随之强化 |
