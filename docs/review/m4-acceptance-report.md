# M4 验收报告（relay + 随手问广播问答）

> 范围：M4（设计 §4/§8/§12/§15.5/§15.6）——relay 只路由、广播问答、增量汇总、在线人数、限流、提问组。
> 结论：**通过**（自建 WebSocket relay；单机三节点 E2E 互搜互答 + 组隔离 + 界面试通；218 项单测全绿）

## 1. 实现概览

| 组件 | 文件 | 说明 |
|---|---|---|
| relay（中心） | src/relay/server.py | WebSocket 路由：在线登记/心跳、问题转发、答案回传、提问限流；不存知识 |
| 节点总线 | src/node/bus.py | RelayClient（重连退避+心跳）、AskService（qid 收口）、NodeBus（应答：本地检索+限流+并发上限） |
| API | src/api/server.py | GET /api/relay、POST /api/ask、GET /api/ask/{qid}；启动/关闭事件管理 relay 连接 |
| UI | src/ui/* | 随手问页：relay 状态/在线人数、组选择、提问、增量结果（节点徽标/route 徽标） |
| 协议文档 | docs/design/m4-relay-protocol.md | 消息格式、路由规则、部署与限流 |

## 2. 验收数据（单机三节点：我/18371、alice/18381、bob/18382，relay 18760）

| 用例 | 结果 |
|---|---|
| relay 在线状态 | online=3，各节点 /api/relay 在线名单一致 |
| 跨节点互搜互答 | 我→"波波专属词条"：expected=2、responded=2，聚合命中 bob 的笔记（nodes=[bob]） |
| 跨节点共享内容 | alice→"凿壁测试词·风"：bob 的 shared-corpus 命中并聚合（agreement 1） |
| 组内广播 | alice→"fcntl 怎么加锁" groups=[数据库组]：仅 bob 应答（targets=1） |
| 组隔离 | alice→ groups=[前端组]：targets=0、无应答、无命中 |
| 增量展示 | 视图 status=collecting→done，responded/expected 实时增长；UI 实测「已响应 1/1 节点 · 在线 2 人 · 用时 422ms」 |
| 限流 | 连续两问（<5s）：relay 返回 rate_limited，提问视图 status=error 并展示原因（不再静默等 5s） |
| 单测 | 218/218 全绿（M4 新增 14 项：relay 路由/限流/替换注销/心跳清扫、聚合去重/近重复/排序、应答限流） |
| UI | 浏览器实测：随手问页在线人数、组选择、提问、结果节点徽标齐全 |

## 3. 验收中发现并修复的问题

| # | 问题 | 修复 |
|---|---|---|
| F1 | /api/ask 返回 422：AskIn 定义在 create_app 内 + `from __future__ import annotations`，FastAPI 无法解析注解、误判为 query 参数 | AskIn 移到模块级（与 MountIn/RuleIn 一致） |
| F2 | relay 限流错误只写日志，提问视图仍等满 5s | AskService 增加 error 状态与 relay error 消息处理；UI 立即提示原因 |

## 3.1 用户复测反馈修复（M4-FIX1）

| # | 反馈 | 根因 | 修复 |
|---|---|---|---|
| F3 | 随手问看不到其他提问组，只有全员组 | 界面只展示本机加入的组；relay 未广播全网已知组 | relay hello_ok/online 增加 `groups`（在线节点组并集）；节点 RelayClient 记录 known_groups；UI 组选择 = 本组 ∪ 全网已知组 |
| F4 | 随手问结果只有一句话 | 原实现只广播给其他节点，本机笔记不参与检索（用户自己的 238 篇思源笔记被忽略） | 提问时本机先本地检索并作为 `@本机` 答案并入结果；expected 计入本机；实测「@我」真实笔记 + 远端「@bob」同时返回 |

实测（用户实例 18371，在线 3）：组列表 [全员组, 数据库组]；问「fcntl」→ 3/3 节点应答，结果含本机真实思源笔记（@我，完整片段）与 bob 的笔记（@bob）。

## 3.2 用户复测反馈修复（M4-FIX2）

| # | 反馈 | 根因 | 修复 |
|---|---|---|---|
| F5 | 不勾选任何组仍能收到所有答案 | 后端把空选择（`groups: []`）当「默认本机组」处理；UI 定时刷新还会把空选择重置为全选 | 后端：显式空选择 → 400「至少选择一个提问组」；UI：未选择时阻止提交并提示；`askGroupsTouched` 标记用户手动调整后不再被定时刷新重置 |
| F6 | 无法测试「不勾某组 → 搜不到该组知识」 | 演示节点都只加入全员组，组间无差异化内容 | 演示环境改为 alice=前端组（Vue 笔记）、bob=数据库组（fcntl 笔记）、我=全员组（真实思源笔记）；组选择只控制广播范围，本机笔记恒参与 |

实测（v0.4.2，UI）：只勾「数据库组」问 fcntl → 应答 2（@我 + @bob），出现 bob-fcntl 与本机文件锁；只勾「前端组」问 Vue → 应答 2（@我 + @alice），仅 alice 的 vue；不勾选任何组 → 提示错误、不发送；后端空选择 → 400。

## 4. 设计变更记录

- 原设计：nats-server 二进制（NATS core 模式）；
- 实现：自建 WebSocket relay（Python/FastAPI）；理由与等价性见 docs/design/m4-relay-protocol.md 开头；设计 §13 M4 与 §16 热备项已同步。

## 5. 遗留（不阻断 M5）

- TLS：内网可先明文，M6 打包时加自签 TLS 选项；
- relay 高可用/跨网 NAT 穿透：推广前处理；
- 结果「查看完整上下文」（跨节点 fetch）：已于 v0.6.1 实现（见 m5-acceptance-report.md §3.6：仅允许取本次搜索返回过的文档，10 分钟有效）；
- 聚合的 AI 综合属 M5（当前为规则聚合：去重/共识/排序）。