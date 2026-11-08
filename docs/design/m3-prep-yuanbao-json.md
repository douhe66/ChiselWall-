# 元宝对话 .json 连接器 · 前期分析（待用户裁决后并入 M3/M4）

> 数据来源：用户已挂载目录 E:\思源笔记\元宝对话\test 中的真实导出文件（012/013/014_*.json）。

## 1. 格式结构（实测）

- 顶层：`{ id, title, agentId, projectId/Name, hasMore, convs: [...] }`；
- `convs[]` 每条一轮对话：`speaker`（human / ai）、`speech`（本轮文本）、`speechesV2[].content[].msg`（富文本片段，text 类型时与 speech 等价）、`displayPrompt` 等；
- 编码 UTF-8；单文件 8KB~54KB 量级；`title` 可作文档标题。

## 2. 连接器设计草图

- 新连接器 `YuanbaoJsonConnector`（复用 Connector 协议）：识别条件=扩展名 .json 且顶层含 `convs` 数组与 `speaker` 字段；不满足的 .json 维持「不支持格式」排除轨道（避免误吃配置类 JSON）；
- 提取：遍历 convs → 每轮输出「speaker + speech」（speech 缺失时回退 speechesV2 content msg 拼接）→ 按轮拼接成正文（可加角色前缀，如「问:/答:」或保留原文）；
- chunk：走现有 Chunker（500-800 字）；标题=title；
- **md 孪生去重**：同目录常见 .md 与 .json 成对（同一对话），规则=两者都在时只索引 .md（信息等价、体积更小），.json 标 excluded(reason=duplicate_md_twin)；仅 .json 存在时正常索引；
- 失败隔离与排除规则沿用现有管线；资源控制不变。

## 3. 待定项

- 是否需要角色前缀/时间戳字段入正文（影响搜索展示，等用户意见）；
- 是否作为默认支持的 .json 形态（还是仅「元宝导出」目录专用开关）。