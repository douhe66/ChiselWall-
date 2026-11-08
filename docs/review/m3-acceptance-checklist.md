# M3 验收检查清单（凿壁 ChiselWall）

> 文档性质：reviewer 编写的 M3 可执行验收清单。依据：docs/design/m3-impl-brief.md（§1 SiyuanConnector / §2 完整 UI / §3 验收与回归）、design-v1 §6/§9/§15.6、docs/research/_raw/sy-format-zh-cn.md（.sy 规范）、M2 清单 §9.1/§10。
> M3 交付范围：SiyuanConnector（.sy 直读）、随手记完整 UI（含笔记分组、route 展示、离线资源）、回归基线。
> 判定规则沿用 M2 清单 §0：P0 阻断全过；P1 不通过须记录+修复计划或豁免；P2 跟踪；证据=平台/结果/截图/日志/DB 查询；带 ★ 数值为 reviewer 建议待确认。

## 1. SC 组 —— SiyuanConnector（.sy 直读，M3-1）

通用断言：每个 .sy 样本的标记词「凿壁思源词·X」检索命中且 rel_path 指向该 .sy、title=Properties.title；目标样本生成见 tests/fixtures/acceptance/make_siyuan.py。

| 编号 | 优先级 | 用例 | 期望 |
|---|---|---|---|
| SC-01 | P0 | 根结构：{ID, Spec:"2", Type:"NodeDocument", Properties{id,title,type,updated}, Children}；ID=文件名去 .sy | 识别为文档：title=Properties.title、正文=Children 递归文本、可搜索 |
| SC-02 | P0 | 文本提取：NodeText.Data；**Data 省略**时 NodeTextMark.TextMarkTextContent（strong/em/u/s/mark/code/tag/a/block-ref/inline-memo 全覆盖） | 标记文本全部入库可搜；inline-math 无 TextMarkTextContent 不报错 |
| SC-03 | P0 | 块类型覆盖：NodeParagraph/NodeHeading(1-6)/NodeList(无序 Typ 缺省+有序 Typ:1+任务 Typ:3)/NodeBlockquote/NodeCodeBlock/NodeTable/NodeThematicBreak/NodeHTMLBlock | 各块文本均可搜、不互相串扰 |
| SC-04 | P1 | base64 约定：ListData.Marker（"Kg=="→"*"、"MS4="→"1."）为 base64 串；BulletChar/Delimiter/TaskListItemMarker 为 int 码点 | 解析不误读（DB 断言 chunk 文本合理：列表项文字完整、不出现乱码/错字符）；任务项勾选状态不产生空块 |
| SC-05 | P0 | Spec 兼容：Spec 3（页签）/4（表格富文本）/5（思维导图）/6（标题含 /）样本 | 按版本兼容解析、可搜索；未知节点类型**安全跳过**不崩溃、不误删节点 |
| SC-06 | P0 | Type 判别（规范 §2 核心规则）：伪块样本（带 ID 的非块节点、未知 Type、历史遗留 ID） | 按 Type 判断，不因 ID 误判为块、不删除节点本身、不崩溃 |
| SC-07 | P0 | 加密笔记本：磁盘 .sy 为密文（非 JSON 字节） | 跳过并记录（失败列表或排除轨道、原因明确），不崩溃、不把密文当文本入库 |
| SC-08 | P0 | 失败隔离：坏 .sy（截断 JSON / 语法错误 / 非 JSON 文本）与正常 .sy 同挂载 | 坏样本进失败列表含原因；正常样本全部可搜；整体索引完成 |
| SC-09 | P1 | 编码：UTF-8 无 BOM 与 BOM 前缀样本 | 两者正常解析（BOM 容忍） |
| SC-10 | P0 | 挂载形态：siyuan workspace（data/<box>/<id>.sy 布局）挂载 kind=siyuan | 全部文档枚举；assets/ 资源目录不索引 |
| SC-11 | P1 | 增量：.sy 文件修改/删除 | 修改重建旧词失效、删除后不可搜（AC-05 断言法） |
| SC-12 | P2 | 兜底通道接口：官方 CLI siyuan export md 的接口登记 | 仅接口存在（M3 可不实现） |

**SC 专项注意**：用户真实工作空间脱敏样本到位后（tests/fixtures/acceptance/siyuan/real/），SC-01~08 在真实样本上重跑；真实样本的块类型组合视为最终判定依据。

## 2. GR 组 —— 笔记分组功能矩阵（§15.6 + brief §2）

| 编号 | 优先级 | 用例 | 期望 |
|---|---|---|---|
| GR-01 | P0 | 建组 / 删除组（组名合法性与重复处理按实现文档） | 建组出现在分组列表；删除后组消失 |
| GR-02 | P0 | 目录、单文件归组；一个来源可归多组 | 归组后组内可见该来源全部文档；多组归属同时成立 |
| GR-03 | P0 | 按组筛选与统计 | 组内文档数 = 实际归属数；筛选后仅显示组内文档 |
| GR-04 | P0 | 整组批量排除 → 解除 | 排除后组内文档全部不可见（红线⑤：检索 0 可见、全文 403）；解除后恢复 |
| GR-05 | P1 | 分组持久化 | 重启 agent 后组与归属保留 |
| GR-06 | P1 | 删除组仅解除归属（不删文档/不影响索引）；空组可存在；组内来源被移除挂载后的组统计正确 | 文档不受影响 |
| GR-07a | P0 | **排除语义 deny-union**（lead 裁决）：同一文件被多条规则命中（如 dir 规则 + glob 规则叠加） | 任一条命中即排除；删除其中一条仍排除（其余规则继续命中）；全部删除后恢复可见——不存在优先级冲突 |
| GR-07b | P1 | **组批量排除 = 便捷添加排除规则的批量操作**（组本身不是可见性维度，lead 裁决） | 组批量排除在 exclude_rules 表产生可见规则记录（API/DB 可查）；删除组后规则仍在、文件仍被排除 |
| GR-07c | P1 | 组批量排除产生的规则可单独删除 | 删除对应规则后组内文件恢复可见（无需组存在） |

## 3. UI 组 —— 随手记完整版（M3-2）

| 编号 | 优先级 | 用例 | 期望 |
|---|---|---|---|
| UI-01 | P0 | 侧边栏结构：随手记（默认）/ 随手问 M4 占位 / AI 问答 M5 占位 / 设置 M6 占位 | 导航正确、占位禁用态 |
| UI-02 | P0 | 挂载管理：添加/移除目录与文件；**类型徽标**（directory/obsidian/siyuan/file） | 徽标与挂载类型一致 |
| UI-03 | P0 | **双列表**（观察-1 落地）：默认排除+不支持格式 与 解析失败 分开两个列表 | 加密 .sy/坏 .sy 在失败列表；.json/.log/隐藏文件等在排除列表，各带原因 |
| UI-04 | P0 | **route 展示**（观察-3）：搜索结果区分「主索引命中/兜底命中」 | 主索引与 trigram/LIKE 兜底命中可见不同标识 |
| UI-05 | P0 | **离线资源**（F5 落地）：Vue3 本地内置、无 CDN 依赖 | 浏览器 Network 无外部 jsdelivr 等请求；模拟断网（禁用外网）后 UI 完整可用 |
| UI-06 | P0 | 分组管理 UI：建/删组、归组、按组筛选统计、整组排除 | 与 GR 矩阵行为一致 |
| UI-07 | P1 | 文档列表与只读预览（按挂载/分组浏览），每条带打开所在位置/直接打开 | 预览只读不改文件 |
| UI-08 | P1 | 打开操作 toast（FIX3 回归）：成功显示 how、失败显示 hint | 无「静默无反应」 |
| UI-09 | P2 | 顶栏统计纯文本观感（FIX2 回归） | 无按钮误导 |
| UI-10 | P1 | 索引进度与统计展示（已索引/待处理/失败/排除/挂载） | 数字与 API 一致 |

## 4. RG 组 —— 回归基线（M3-3 必跑）

| 编号 | 优先级 | 用例 | 期望 |
|---|---|---|---|
| RG-01 | P0 | 单测回归 | **161 项全绿**（python -m unittest discover） |
| RG-02 | P0 | 验收驱动回归 | **78 项全绿**（tests/fixtures/acceptance/run_acceptance.py，M2 语料） |
| RG-03 | P0 | AC-08-14 旧库迁移 | 旧 schema 库启动自动迁移、旧数据可检索、set_chunks 正常 |
| RG-04 | P1 | AC-08-15 不支持格式排除轨道 | .json/.log → excluded+unsupported_format+统计+UI 可见 |
| RG-05 | P2 | AC-08-16 UI 统计观感 | 纯文本标签 |
| RG-06 | P1 | AC-08-17 open 健壮性 | ok/how/hint 三字段+回退弹窗+toast（M6 挂起的「无可见窗口」项不在 M3 回归范围） |
| RG-07 | P0 | **YB-01~13 元宝 JSON 连接器** | 合成样本 + real 样本全量执行（清单 §10） |
| RG-08 | P1 | 资源抽查：siyuan workspace 挂载下的索引期/空闲指标 | 复用 AC-03 方法，★ 达标线与 M2 一致 |

## 5. 三红线复查（M3 新增代码）

| 红线 | 检查点 |
|---|---|
| ① 只读 | SiyuanConnector 对 .sy 仅 open('rb')；分组/UI 操作不改动用户笔记文件 |
| ② 索引库位置 | siyuan workspace 挂载时 db 不在其目录内（~/.chiselwall）；挂载拒绝逻辑覆盖 workspace 路径 |
| ③ 资源 | .sy 解析在单线程索引管线内；加密/坏样本快速失败不耗资源 |
| ⑤ 排除即不可见 | 加密笔记本与坏 .sy 内容不可检索、不可全文获取 |

## 6. 证据模板（沿用 M2）

```text
编号 | 平台 | 结果 | 证据(截图/日志/测量JSON/DB查询) | 备注
```

## 7. 待确认项（lead 已裁决更新）

| # | 事项 | 状态 |
|---|---|---|
| 1 | 思源真实工作空间脱敏样本 | ⏳ 用户尚未提供：SC 组先按自建语料执行，样本到位后补跑（lead 再次征集） |
| 2 | GR-07 排除语义 | ✅ 已裁决：**deny-union**（任一条命中即排除，无优先级冲突）；「组批量排除」=按组便捷添加排除规则的批量操作，组非可见性维度（GR-07a/b/c 已按此口径改写，并转告 builder） |
| 3 | M3 资源达标线 | ✅ 沿用 M2 数值（设计 §12，含磁盘 500% 口径） |
