# M2 测试数据方案（tests/fixtures）

> 文档性质：reviewer 规划的 M2 验收测试语料。与 docs/review/m2-acceptance-checklist.md 配套：清单中每条「搜得到/搜不到」的断言都依赖本方案的 fixture 与标记词。
> 状态：**规划稿**——目录与文件明细在此定案；实际样本文件由 builder 或 reviewer 按 §5 生成脚本产出后落盘。

## 1. 目的与原则

1. **可断言**：每个 fixture 含一个**唯一标记词**（自然文本中不会出现的词），验收断言「检索该词 → 恰好命中该文件 / 0 命中 / 该文件进失败列表」，结果无歧义。
2. **隔离性**：所有排除类 fixture 旁放置含对照标记词的正常文件，验证排除不误伤邻居。
3. **可重现**：二进制格式（docx/xlsx/pdf）无法手写，由生成脚本一次生成并**提交入库**；超大文件不入库、测试前即时生成。
4. **体积克制**：入库 fixture 总量目标 < 500KB（大文件与生成物一律 gitignore）。
5. **声明**：所有「敏感」内容均为**虚构数据**（§7），不含任何真实个人信息。

## 2. 目录结构规划

```text
tests/fixtures/
├── README.md                # fixture 清单登记表（标记词 ↔ 文件 ↔ 预期行为）
├── generate.py              # 幂等生成脚本：生成全部二进制/边界样本（§5）
├── .gitignore               # 忽略 generated/、*.db、__pycache__
├── corpus/                  # 正常语料 —— 全部应可索引、可命中
│   ├── md/                  # 项目周报-2024Q4.md、数据库优化笔记.md、markdown长文.md、代码片段笔记.md
│   ├── txt/                 # 会议纪要-utf8.txt、部署手册.txt
│   ├── docx/                # 接口设计说明.docx、故障复盘.docx
│   ├── xlsx/                # 环境配置清单.xlsx(2 sheet)、值班表.xlsx
│   ├── pdf/                 # 架构评审纪要.pdf、运维手册节选.pdf（均有文本层）
│   ├── csv/                 # 服务器资产.csv
│   ├── vault/               # Obsidian vault：.obsidian/app.json + 笔记A.md + 子目录/笔记B.md
│   ├── paths/               # 路径兼容：中文目录/含空格 文件.md、普通文件.md
│   ├── bm25/                # 排序测试：少.md / 中.md / 多.md（同一关键词 1/3/10 次）
│   ├── phrase/              # 短语测试：相邻.md（“数据库连接池”相邻）与 分离.md（两词不相邻）
│   └── tri/                 # trigram 测试：tri子串.md（含 cwfix_tri_ab）
├── exclude/                 # 排除规则测试（配合 AC-02 矩阵）
│   ├── single_file/         # 被排除-敏感工资表.xlsx + 对照-正常文件.md
│   ├── dir/                 # secret_dir/秘密笔记.md + 对照-正常文件.md
│   ├── glob/                # debug-2024.log、secret-draft.md、drafts/草稿1.md、临时笔记.tmp + 对照-正常文件.md
│   └── sensitive/           # 敏感样例（虚构）：工资-虚构数据.xlsx、密码-虚构示例.md、README.md（声明虚构）
└── edge/                    # 边界样例（配合 AC-04 失败隔离 / AC-02 默认排除）
    ├── empty.md / empty.txt              # 0 字节空文件（入库）
    ├── gbk.txt                           # GBK 编码（生成后入库）
    ├── encrypted.pdf                     # 加密 PDF（生成后入库）
    ├── scanned_no_text.pdf               # 图片型、无文本层（生成后入库）
    ├── corrupted.docx                    # 截断损坏 docx（生成后入库）
    ├── old_format.doc / old_format.xls   # 伪造旧格式（生成后入库；真实样本见 §5.4）
    ├── binary.png / binary.zip           # 小二进制（生成后入库）
    ├── meta.json                        # 不支持格式 -> 排除轨道样本（M2-FIX2 后，AC-08-15）
    ├── .hidden.md                        # 点开头隐藏文件
    ├── ~$幽灵.docx                       # 临时文件（名字匹配 ~$ 规则，内容为普通文本）
    ├── defaults/
    │   ├── project/                      # node_modules/lib/index.js、.git/config、.svn/entries、src/正常代码.md
    │   └── vault/.obsidian/app.json      # 第二个 .obsidian 排除样本
    └── generated/                        # ★ 不入库，generate.py 生成（.gitignore）
        ├── big_over_cap.md              # >5M 字符文本截断测试（≈15MB 字节）
        ├── many_chunks.md                # >2000 chunk 上限测试
        ├── big_binary_50mb.bin / big_binary_21mb.bin  # 大文件默认排除测试（阈值 20MB 可配，21MB 为边界样例）
        └── 🎯emoji文件.md、生僻字𠀀.md   # 非 ASCII 文件名（P2）
```

## 3. 标记词体系

- **中文系列**：`凿壁测试词·X`（X = 风/花/雪/月/星/云/山/林/周/长/码/径/路/编码/部署/对照N…），每个文件唯一，用于「应命中」断言。
- **敏感系列**：`凿壁敏感词·X`（X = 薪/密/志/草/稿/临/隐/幽/模/藏…），仅出现在「应被排除」的文件中，用于「0 命中」断言。
- **ASCII 系列**：`cwfix_<scope>_<n>`（如 cwfix_num_20240101、cwfix_tri_ab、cwfix_bm25、cwfix_big_a/b），用于英文/数字/trigram/截断断言。
- 登记：全部标记词登记在 fixtures/README.md（文件 → 标记词 → 预期：命中/排除/失败列表 → 对应 AC 编号），验收与回归都以此表为唯一事实源。

**标记词分配速查**（正文详情见 §4）：

| 文件 | 标记词 | 预期 |
|---|---|---|
| corpus/md/数据库优化笔记.md | 凿壁测试词·风 | 命中（AC-01-01） |
| corpus/txt/会议纪要-utf8.txt | 凿壁测试词·花 | 命中（AC-01-02） |
| edge/gbk.txt | 凿壁测试词·编码 | 命中（AC-01-03，验证 GBK 解码） |
| corpus/docx/接口设计说明.docx | 凿壁测试词·雪 | 命中（AC-01-04） |
| corpus/xlsx/环境配置清单.xlsx | sheet1 凿壁测试词·月 / sheet2 凿壁测试词·星 | 双 sheet 均命中（AC-01-05） |
| corpus/pdf/架构评审纪要.pdf | 凿壁测试词·云 | 命中（AC-01-06） |
| corpus/csv/服务器资产.csv | 凿壁测试词·山 | 命中（AC-01-07） |
| corpus/vault/笔记A.md | 凿壁测试词·林 | 命中（AC-01-08） |
| corpus/vault/.obsidian/app.json | 凿壁测试词·藏 | **0 命中**（AC-01-08） |
| corpus/md/项目周报-2024Q4.md | 凿壁测试词·周 | 命中（AC-01-09 单文件挂载） |
| corpus/md/代码片段笔记.md | postgres、cwfix_num_20240101、凿壁测试词·码 | 命中（AC-01-10/11） |
| corpus/phrase/相邻.md | 短语「数据库连接池」相邻 | 命中（AC-01-13） |
| corpus/phrase/分离.md | 「数据库」与「连接池」不相邻 | 短语查询不命中（AC-01-13） |
| corpus/tri/tri子串.md | cwfix_tri_ab | 命中（AC-01-14，trigram 若实现） |
| corpus/bm25/少·中·多.md | 同一词 cwfix_bm25 各 1/3/10 次 | 排序 多>中>少（AC-01-15） |
| corpus/md/markdown长文.md | 凿壁测试词·长（文中部） | 命中，片段≈300字带高亮（AC-01-16） |
| exclude/single_file/被排除-敏感工资表.xlsx | 凿壁敏感词·薪 | **0 命中**（AC-02-01） |
| exclude/single_file/对照-正常文件.md | 凿壁测试词·对照一 | 命中（隔离性） |
| exclude/dir/secret_dir/秘密笔记.md | 凿壁敏感词·密 | **0 命中**（AC-02-02） |
| exclude/dir/对照-正常文件.md | 凿壁测试词·对照二 | 命中（隔离性） |
| exclude/glob/debug-2024.log | 凿壁敏感词·志 | **0 命中**（AC-02-03a） |
| exclude/glob/secret-draft.md | 凿壁敏感词·草 | **0 命中**（AC-02-03b） |
| exclude/glob/drafts/草稿1.md | 凿壁敏感词·稿 | **0 命中**（AC-02-03c） |
| exclude/glob/临时笔记.tmp | 凿壁敏感词·临 | **0 命中**（AC-02-06 默认 *.tmp） |
| exclude/glob/对照-正常文件.md | 凿壁测试词·对照三 | 命中（隔离性） |
| exclude/sensitive/工资-虚构数据.xlsx | 凿壁敏感词·薪二（虚构姓名/金额） | 被排除后 **0 命中** |
| exclude/sensitive/密码-虚构示例.md | 凿壁敏感词·码二（虚构密码串） | 被排除后 **0 命中** |
| edge/.hidden.md | 凿壁敏感词·隐 | **0 命中**（AC-02-04 默认隐藏） |
| edge/~$幽灵.docx | 凿壁敏感词·幽 | **0 命中**（AC-02-06 默认 ~$） |
| edge/defaults/project/node_modules/lib/index.js | 凿壁敏感词·模 | **0 命中**（AC-02-05） |
| edge/defaults/project/.git/config | 凿壁敏感词·git | **0 命中**（AC-02-05） |
| edge/defaults/project/.svn/entries | 凿壁敏感词·svn | **0 命中**（AC-02-05） |
| edge/defaults/vault/.obsidian/app.json | 凿壁敏感词·藏二 | **0 命中**（AC-02-05） |
| edge/defaults/project/src/正常代码.md | 凿壁测试词·对照四 | 命中（隔离性） |
| edge/encrypted.pdf | （内含「凿壁测试词·密文」，加密不可读） | 进失败列表（AC-04-03） |
| generated/big_6mb.md | 5MB 前 cwfix_big_a / 5MB 后 cwfix_big_b | A 命中、B 不命中（AC-01-19） |
| generated/many_chunks.md | cwfix_many_head（首块）/ cwfix_many_tail（第 2001 块） | 首块命中、超限块不命中（AC-01-20） |

## 4. fixture 内容设计要点（含生成要求）

### 4.1 corpus（正常语料）
- 每个文件 200 字以上真实感中文内容（周报、笔记、会议纪要体裁），嵌入其标记词 1–2 次；**不得把标记词放在文件名里**（防止按文件名/路径命中造成假阳性——命中必须来自正文索引）。
- markdown长文.md ≥ 3000 字、含多级标题/列表/代码块，标记词放在**文中部**（验证 ±150 字窗口截取而非从头取）。
- 环境配置清单.xlsx 两个 sheet 各含不同标记词；值班表.xlsx 含中文表头。
- 架构评审纪要.pdf 用 reportlab 生成（保证有文本层，pypdf 可抽取）。
- vault 结构必须真实（.obsidian 目录 + 子目录笔记），验证 ObsidianConnector 复用 DirectoryConnector + 默认排除 .obsidian。
- paths/中文目录/含空格 文件.md：文件名本身含中文、空格，路径含中文目录（AC-06-02）。

### 4.2 exclude（排除规则）
- 每个「应排除」文件同时含**敏感系列标记词**与一段虚构敏感内容（如虚构工资表、虚构密码示例），使「排除即完全不可见」的验证语义更接近真实场景。
- 每个排除目录旁必须有对照文件（含对照标记词），保证断言「0 命中」不是「整个挂载没索引」造成的误判。
- glob 样例覆盖三种规则形态：扩展名（*.log）、前缀（secret*）、子目录递归（**/drafts/*）。

### 4.3 edge（边界）
| 文件 | 生成/构造方式 | 验证目标 |
|---|---|---|
| empty.md / empty.txt | 0 字节 | 不报错、无空 chunk（AC-04-06） |
| gbk.txt | Python open(..., encoding="gbk") 写入中文 | charset 检测正确解码（AC-01-03） |
| encrypted.pdf | reportlab canvas 生成 + encrypt 参数设密码 | 跳过+失败列表（AC-04-03） |
| scanned_no_text.pdf | Pillow 画含「此页无文本层」字样的 PNG → reportlab drawImage 输出 PDF | 无文本层跳过，OCR 不做（AC-04-04） |
| corrupted.docx | 正常 docx 字节截断前 60% | 损坏隔离（AC-04-01/02） |
| old_format.doc / .xls | 写入非 OLE 合法字节（伪造） | 仅验证「跳过+提示+不崩溃」；真实样本待同事提供（§5.4） |
| binary.png / binary.zip | Pillow 小图 / zipfile 含 1 个 txt | 不产生乱码命中（AC-04-06） |
| .hidden.md | 普通 md，点开头命名 | 默认排除（AC-02-04）；Windows attrib +h 变体为 P2 补充用例 |
| ~$幽灵.docx | 普通文本文件，文件名带 ~$ 前缀 | 默认排除（AC-02-06） |
| defaults/project/… | node_modules/lib/index.js、.git/config、.svn/entries、src/正常代码.md | 默认工程目录排除 + 对照命中（AC-02-05） |

### 4.4 generated（不入库，generate.py 生成）
- big_over_cap.md：超过 500 万字符（≈15MB 字节）纯文本，5M 字符边界前后各放标记词 cwfix_big_a / cwfix_big_b（截断语义：仅前 5M 字符入库；实现 text_cap_chars 按字符计，故样本按字符构造）。
- many_chunks.md：2001 个段落、每段约 600 字（600<800 保证每段即 1 chunk），总量约 3.6MB（不触发 5MB 截断，先触发 2000 chunk 上限）；首块与第 2001 块各放标记词。
- big_binary_50mb.bin / big_binary_21mb.bin：随机字节。默认排除阈值已定 **20MB（可配置，lead 确认）**：21MB 为阈值上边界样例（应排除），50MB 为常规超限样例（应排除）；阈值改动后重跑本用例验证配置生效。
- emoji/生僻字文件名（P2）：Windows 下用脚本创建，验证索引与「打开所在位置」。

## 5. 生成脚本设计（tests/fixtures/generate.py）

- **形态**：单文件 Python 脚本，**幂等**（重复运行覆盖产出相同文件）；运行一次后二进制产物提交入库，日常测试不再依赖脚本；generated/ 由测试前置步骤按需生成。
- **依赖**：python-docx、openpyxl、reportlab、pypdf、Pillow（均为生成期依赖，不进产品 requirements.txt；在脚本头部注释与 fixtures/README.md 中声明）。
- **职责**：
  1. 生成全部二进制 fixture（docx/xlsx/pdf/csv/加密 pdf/扫描 pdf/损坏 docx/伪造 doc/伪造 xls/小二进制/GBK txt）；
  2. 生成 generated/ 大文件与特殊文件名；
  3. **自检**：生成后逐文件校验标记词存在性（二进制格式用对应解析库读回文本断言），任一缺失即报错——防止「文件生成了但标记词没进去」的静默失效；
  4. 打印 fixtures/README.md 登记表的核对结果。
- **注意事项**：
  - 加密 pdf：reportlab 的 encrypt 参数（或 pypdf writer.encrypt），密码固定为 cwtest123，写入 README 登记表；
  - 损坏 docx：先生成正常 docx 再字节截断，保留 .docx 扩展名；
  - .git/.svn/node_modules 目录在 Windows 上通过 Python os.makedirs 创建（资源管理器限制）；
  - 空文件与纯文本 fixture 直接提交入库（可写、可 git 跟踪）。

### 5.4 需要人工提供的样本（登记为开放项）
- **真实 .doc / .xls 各 1 份**（同事实际文件、脱敏后提供）→ 放入 edge/real/，提升 AC-04-05 证据强度；拿不到时用伪造文件执行并记录该限制。
- **真实思源工作空间样本**：M3 验收用（§14 风险「需要你提供几份真实工作空间样本」），本轮可提前向用户征集。

## 5.5 元宝 JSON 对话语料（M3，验收用例见清单 §10）

目录 tests/fixtures/acceptance/yuanbao/（JSON 文本直接写文件；GBK 用 encoding 写入；BOM 用 utf-8-sig）：

| 文件 | 结构要点 | 标记词 | 对应用例 |
|---|---|---|---|
| 标准对话.json | exportTime+conversations+messages(role/content)，含 thinking 字段消息 | 凿壁元宝词·标准 | YB-01/11/12 |
| 顶层数组.json | 顶层 [{title,messages}] | 凿壁元宝词·数组 | YB-02 |
| 单对话.json | 无 conversations 包裹的单对象 | 凿壁元宝词·单对 | YB-02 |
| speech回退.json | assistant content 为空 + speech 有文本 | 凿壁元宝词·回退 | YB-04 |
| speeches数组.json | speeches:[{...}] 数组形态 | 凿壁元宝词·数组语 | YB-05 |
| gbk.json / bom.json | GBK 编码 / UTF-8 BOM | 凿壁元宝词·编码 | YB-06 |
| 坏json截断.json / 语法错误.json | 截断/缺逗号 | —（进失败列表） | YB-07 |
| 非对话config.json | 普通键值对（反例） | 凿壁元宝词·反例 | YB-03 |
| 数字数组.json | [1,2,3]（反例） | — | YB-03/08 |
| 孪生对话.md | 与「标准对话.json」同对话内容的 md 导出 | 凿壁元宝词·标准（同词） | YB-09/10 |
| 对照对话.json / 对照对话.md | 另一场不同对话 | 凿壁元宝词·对照 | YB-10 |

**真实样本**：builder 提供的脱敏真实元宝导出（官方 App 格式）≥2 份入 fixtures/yuanbao/real/，官方结构的所有字段变体（含 speech 真实形态）以它们为准重跑 YB-01~08。

## 6. fixture ↔ 验收用例映射（速查）

| 验收分组 | 主要 fixture |
|---|---|
| AC-01 格式覆盖 | corpus/md、txt、docx、xlsx、pdf、csv、vault、paths |
| AC-01 引擎正确性 | corpus/phrase、bm25、tri、markdown长文、代码片段笔记 |
| AC-01 chunk/截断 | generated/big_6mb、many_chunks + DB 查询 |
| AC-02 排除矩阵 | exclude/ 全目录 + edge/.hidden.md、~$幽灵.docx、defaults/ |
| AC-04 失败隔离 | edge/encrypted.pdf、scanned_no_text.pdf、corrupted.docx、old_format.*、binary.*、empty.* |
| AC-05 增量索引 | 任一 corpus 文件 + 测试现场新建/修改/删除（增量用例在测试临时目录操作，不污染 fixture） |
| AC-06 跨平台 | corpus/paths/、generated 特殊文件名 |
| AC-03 资源测量 | 全量 corpus + exclude + edge 一次性挂载作为索引负载；必要时复制多份扩大规模（复制目录放测试临时区） |

## 7. 敏感样例声明

- exclude/sensitive/ 与各「应排除」fixture 中的姓名、工号、金额、密码、银行卡等**全部为虚构内容**，仅用于验证「排除即完全不可见」的产品原则；
- fixtures 不包含任何真实个人信息、公司敏感数据；向同事征集真实样本时须**脱敏**（人名/金额/密码替换为虚构值，保留格式特征）方可入库；
- 敏感系列标记词（凿壁敏感词·X）本身无含义，仅是断言锚点。

## 8. 与验收流程的衔接

1. builder 交付 M2 时，reviewer 按 m2-acceptance-checklist.md 执行验收；本方案 fixture 由 reviewer 用 generate.py 产出并落盘（或要求 builder 在交付物中包含已生成 fixture）。
2. 验收通过后，fixture 与清单固化为 M2 回归基线，M3–M6 每轮回归必须保持「应命中仍命中、应排除仍排除」。
3. 每轮新增功能（M3 思源、M4 relay）在 tests/fixtures 下**追加**新目录与标记词登记，不修改既有 fixture 的标记词（保证回归断言稳定）。
