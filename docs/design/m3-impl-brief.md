# M3 实施要点（开工即用 · 待用户验收 M2 后解锁）

> 汇总：设计 §6/§9/§15.6、调研 03 §1、reviewer 清单 §9.1/§10。供 builder 开工时直接对照。

## 1. SiyuanConnector（.sy 直读，M3-1）

- .sy = Lute AST 序列化 UTF-8 JSON：根恒为 NodeDocument，正文递归 Children；**Type 决定节点类型**（NodeParagraph/NodeHeading/NodeList/NodeCodeBlock/NodeText/NodeTextMark…），不凭 ID 判断块；
- 文本提取：NodeText.Data + NodeTextMark.TextMarkTextContent（Data 可省略，空 TextMark 不代表空文本）；HeadingLevel 1-6 可作结构切分提示；
- Spec 版本：普通文档 Spec="2"；3=页签、4=表格富文本、5/6 更高特性——按版本兼容解析，不认识的节点类型安全跳过；
- base64 约定：Go []byte 字段（ListData.Marker、CodeBlockInfo）是 base64 字符串；Go byte 字段（BulletChar 等）是 int ASCII 码点；Data/TextMarkTextContent 原文不编码；
- 标题：Properties.title（权威键为小写 id/title/type/updated）；
- 编码 UTF-8 无 BOM（读取容忍 BOM）；**加密笔记本 .sy 磁盘为密文 → 跳过并记录（不可解析）**；
- 无成熟第三方解析库：自写纯 Python 树遍历解析器（单文件 ~200 行内），解析失败进失败列表；
- 兜底通道接口：官方 CLI `siyuan export md --id <id> -w <workspace>`（仅登记接口，M3 可不实现）；
- 测试数据：**需要用户提供一份脱敏真实工作空间样本**（lead 已向用户征集，未到位）；在此之前用调研 03 的官方样例结构自建 fixture。

## 2. 随手记完整 UI（M3-2）

- 侧边栏完整版：随手记（默认页）/ 随手问（M4 占位）/ AI 问答（M5 占位）/ 设置（M6 占位）；
- 挂载管理：添加/移除目录与文件、按连接器显示类型徽标（directory/obsidian/siyuan/单文件）、索引进度与统计；
- **笔记分组**（§15.6）：建组/删除组、目录或文件归组（可多组）、按组筛选统计、整组批量排除；
- 排除面板：单文件/目录/glob 三类规则 + 默认排除说明；**「默认排除/不支持格式」与「解析失败」分两个列表展示**（观察-1）；
- 文档列表与预览：按挂载/分组浏览文档（只读预览），每条带「打开所在位置/直接打开」；
- 搜索结果：route 字段区分「主索引命中/兜底命中」展示（观察-3）；
- **F5：Vue3 改为本地内置资源**（不再依赖 CDN，内网可用），保留无构建工具形态；
- 顶栏统计保持纯文本观感（FIX2），所有打开操作有 toast（FIX3）。

## 3. 验收与回归（M3-3，reviewer）

- 回归基线：AC-08-14~17（迁移/不支持格式轨道/UI 观感/open 健壮性）+ YB-01~13（JSON 连接器）+ 149 项单测 + 78 项验收驱动；
- M3 新增：.sy 解析正确性（多 spec/多块类型样本、加密笔记本跳过、失败隔离）、分组功能矩阵、UI 功能清单（含 route 展示、双列表、离线资源加载）、三红线复查；
- 产出 docs/review/m3-review-report.md。

## 4. 门禁

任务板上 gate=task-12（M2 用户验收，lead 持有）。用户验收通过 → lead complete gate → M3-1/2/3（task-13/14/15）解锁开工。