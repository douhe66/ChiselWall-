# M4 relay 协议与部署（随手问）

> 设计变更记录：设计 §3/§4 原选 nats-server 二进制；M4 实现改为**自建 WebSocket relay**
> （Python/FastAPI）。原因：① 部门内网部署要单包分发（M6 目标），多引入一个 nats-server
> 二进制会显著增加运维成本；② 本项目协议很小（在线状态/广播/转发/限流），自建 relay 完全掌控；
> ③ 中心只路由、无持久化、at-most-once 的语义与 NATS core 等价，后续如需 NATS 可换实现不改协议。

## 1. 拓扑

```
节点 A（同事电脑） ──WS──┐
节点 B（同事电脑） ──WS──┼── relay（中心，只路由）── 转发问题/答案
节点 C（同事电脑） ──WS──┘     不存任何知识
```

## 2. 消息协议（JSON over WebSocket，`/ws`）

客户端 → relay：

| type | 字段 | 说明 |
|---|---|---|
| hello | user, token, groups | 首条消息鉴权并登记在线 |
| presence | - | 心跳（客户端每 10s） |
| query | qid, text, groups, deadline_ms | 提问广播（relay 限流 1 问/5s） |
| answer | asker, qid, node, hits[], elapsed_ms | 对端应答（relay 转发给 asker） |
| group_create | name, description | 自由建组（relay 持久化 + 广播） |

relay → 客户端：

| type | 字段 | 说明 |
|---|---|---|
| hello_ok | user, online[] | 鉴权成功 + 当前在线名单 |
| online | online[] | 在线名单变化广播 |
| query | qid, asker, text, deadline_ms, groups | 某人的提问（收到后本地检索并 answer） |
| query_ack | qid, targets | 提问者收到：本次广播命中多少在线节点 |
| answer | qid, node, hits[], elapsed_ms | 某节点答案（提问者聚合展示） |
| hello_ok / online | user?, online[], groups[] | groups 为 [{name, description, online}]（全员可见） |
| group_ok | name | 建组成功回执 |
| fetch | to, req_id, doc_id | 请求对端文档全文预览（M4.1） |
| fetch_result | to, req_id, doc_id, text, error | 对端返回全文/错误（relay 原样转发） |
| error | code, qid? | auth_failed / rate_limited / bad_json / message_too_large |

路由规则：提问的 groups 含「全员组」→ 发给除提问者外所有在线节点；否则发给与提问组有交集的节点。
每个节点恒含「全员组」（加入具体组不失去全员组可见性）。

## 3. 部署

1. 中心常开机器上写 relay 配置（用户与 token）：
```json
{ "users": { "张三": "token-zhang", "李四": "token-li" }, "rate": 0.2, "burst": 2 }
```
2. 启动 relay：
```bash
PYTHONPATH=src python -m relay.server --config relay.json --host 0.0.0.0 --port 18760
```
3. 发邀请（给每位同事，token 自动生成；也可在节点设置页填写）：
```bash
python -m relay.admin invite 张三 --group 数据库组 --url ws://<中心IP>:18760/ws
python -m relay.admin list / revoke 张三
```

4. 各同事机器启动节点时带 relay 参数（或留空，之后在设置页填写并保存）：
```bash
python run.py --relay-url ws://<中心IP>:18760/ws --relay-nick 张三 --relay-token token-zhang --relay-group 全员组
```

## 4. 资源保护（设计 §12）

- 提问限流：relay 每用户 token bucket（默认 1 问/5s，burst 2）；
- 应答限流：节点侧每来源 1 问/1s（burst 2）+ 同时在跑应答数 ≤2；
- 单查询预算：提问收口 5s；节点本地检索 800ms 预算（超出由搜索自身收口）；
- 消息大小上限 256KB；中心不落盘。