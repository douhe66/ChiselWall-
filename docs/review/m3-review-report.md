# M3 验收与评审报告（凿壁 ChiselWall）

> 评审：reviewer（代码审阅/单测/live 补验）+ lead（执行补跑，reviewer 侧 DSH 运行时故障后接手）
> 依据：docs/review/m3-acceptance-checklist.md、design-v1 §5/§6/§9/§12/§13、调研 03
> 结论：**通过**（M3 驱动 30/30、单测 194/194、M2 回归 78/78、live 真实数据验证、UI 检查通过）

## 1. 执行概况

| 项 | 内容 |
|---|---|
| 代码基线 | src/connectors/siyuan.py、yuanbao.py、directory.py、src/indexer/*、src/node/agent.py、src/api/server.py、src/ui/*（v0.3.0） |
| 单元测试 | **194/194 全绿**（含 siyuan 21 项、yuanbao 18 项、分组 4 项、open 14 项等） |
| M3 验收驱动 | tests/fixtures/acceptance/m3_test.py → **30/30 PASS**（SC 12 / GR 7 / YB 精简 5 / 其余 6） |
| M2 回归 | run_acceptance.py → **78/78 PASS**（独立实例、DB 位于 .run 内以覆盖红线②） |
| live 真实数据 | 用户真实思源工作空间（E:\思源笔记\笔记，236 篇 .sy）全部索引、搜索命中（reviewer 与 lead 均已验证） |
| UI 检查 | v0.3.0 页脚、笔记分组面板、挂载类型徽标、排除双列表+构成、文档浏览、搜索 route 徽标「主索引/兜底命中」、**Vue 本地内置无 CDN**（index.html/app.js 无外链） |
| 三红线 | ① 只读（sha256 前后一致）② db 不在挂载目录（400 拒绝，回归覆盖）③ 资源控制（单线程/低优先级/空闲调度，继承 M2） |

## 2. 验收中发现并修复的问题

| # | 级别 | 发现 | 修复 |
|---|---|---|---|
| F1 | P0 | SC-02：思源 inline-memo 内容（TextMarkInlineMemoContent）未入索引 | siyuan.py `_extract_inline` 支持 inline-memo（与 TextMarkTextContent 合并） |
| F2 | P0 | YB-01/04：通用对话导出结构（conversations/messages/role/content|speech）不被识别 | yuanbao.py 双结构识别（元宝 convs + 通用 conversations），title 回退 conversations[0].title，speech 回退 |
| F3 | P0 | YB-07：语法错误 JSON 被归为「不支持格式」而非失败列表 | yuanbao.py 新增 classify_json（invalid/not_chat/chat）；directory.py 对 invalid 不排除，交 read_doc 抛 ParseError → 失败列表 |
| F4 | P1 | SC-10：思源挂载下嵌套 assets/ 图片被 watchdog 事件记录为文档行 | siyuan.meta_of 仅接受 .sy（非 .sy 抛 ParseError）；indexer.index_file / scan_mount reconciliation 捕获后忽略 |

另修正：m3_test.py/run_acceptance.py 的端口与实例 DB 位置适配（见 §4）。

## 3. 各验收组结果

- **SC 思源解析（12/12）**：根结构/ID=文件名、TextMark 全类型（含 inline-math/inline-memo）、三类列表与 base64/码点、代码块、表格、Spec3/4/5/6、伪块 Type 判别、BOM、密文/截断/语法错误失败隔离、assets 不索引；
- **GR 分组矩阵（7/7）**：建删组、归组多组、按组筛选统计、整组批量排除=真实规则（deny-union）、删组规则仍在、规则可单删恢复可见、规则持久化；
- **YB 元宝/通用 JSON（5/5 精简）**：标准对话命中+title、speech 回退、反例不误识别、坏 JSON 失败隔离、孪生去重；YB 其余细分项由 test_yuanbao 18 项单测覆盖；
- **RG 回归（78/78）**：M2 全量验收驱动（挂载/排除/失败隔离/增量/资源/UI/token/红线）；
- **UI（10 项）**：类型徽标、双列表、route 展示、离线资源、toast、统计观感均通过浏览器实测。

## 4. 环境注意（不阻断）

- Windows 端口保留区（8160–8759）会拦 8317/8371 绑定（WinError 10013）——验收实例改用 18317/18318，用户实例改用 18371；m3_test/run_acceptance 的 BASE 已适配；
- 红线② 检查依赖 DB 位于被挂载目录内：M2 回归实例的 DB 放在 .run 内才可复现 400 拒绝（原设计前提）。

## 5. 遗留

- Linux 实机待补（L1，沿用 M2 清单）；
- 「直接打开」可见性问题挂 M6（设计 §14 已记录）；
- 真实 .sy 样本验证已完成（用户工作空间 236 篇）。