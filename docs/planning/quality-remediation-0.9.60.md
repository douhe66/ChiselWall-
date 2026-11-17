# 问答质量整改计划（v0.9.59 → v0.9.60）

> 来源：[architecture-review-0.9.59.md](../review/architecture-review-0.9.59.md)
> 原则：问答质量优先；安全/隐私项后置；不改变 QueryPlan 参数；每项先测试后提交。
> 状态：v0.9.60 已实施并提交；Q0 五项 + R27/R15/R09/R18 + P0（R06/R10/R22）+ ONNX 防崩探测；全量回归 376 项。用户决策：R12 不改。

## 1. 优先级结论

架构评审整体可信，但其 P0 顺序偏安全/隐私。按“影响回答正确性、完整性、可解释性”重新排序如下。

### Q0：本轮必须修

| 编号 | 问题 | 影响 | 本轮处理 |
|---|---|---|---|
| R-23 | `ask_session_complete` 在 qid 失效时引用未定义的 `question`，返回 500 | 搜索结果无法落库，用户看到“完成失败” | 是 |
| R-21 | 无 AI/计划未就绪时仍进入旧 rewrite 分支，probe/rewrite 白跑；结果被丢弃 | 每次提问多一次无意义本地检索，后续无 AI 路径仍可能延迟 | 是 |
| R-04 | 单条 answer 可带 8×12k `ai_text`，超过 relay 256KB 后被静默丢弃 | 长文档命中整条答案丢失，提问端误以为同事没结果 | 是 |
| R-18 | rerank 后按 `top_k` 截断；score 的 BM25/RRF/相似度/rerank 语义混用 | `/api/search?topk=50` 被截成 5；排序不可解释，检索调优无法归因 | 部分：先去截断；完整 ranking 抽象后置 |
| R-22 | 单条 `ai_text` 可吃满上下文预算，挤掉其他命中；资料未统一标注“不是指令” | 回答覆盖面下降；资料注入风险 | 部分：先做单条配额；注入防护归安全项后置 |

### Q1：下一批

| 编号 | 问题 | 处理时机 |
|---|---|---|
| R-12 | ResearchAgent 最多 6 次 LLM、每轮重新广播、deadline 不覆盖单次调用 | **不改（用户决策）** |
| R-09 | 节点 800ms 应答预算未实现 | 下一批 |
| R-06 | 单文档重索引无事务，崩溃后文档 pending/空块 | 下一批 |
| R-15 | >8MB 文件只哈希头部 1MB，尾部更新漏索引 | 下一批 |
| R-10 | relay 串行 await 发送，慢消费者队头阻塞 | 下一批 |
| R-27 | 评测脚本 schema/nDCG 不可复现 | 质量调优前置 |
| R-07/R-14/R-05/R-08 | 数据边界、残留、在线状态、依赖声明 | 随版本 |

### 安全/隐私：后置

- R-01 relay 分组鉴权缺失
- R-02 answer 身份绑定/限流
- R-03 answer 主动外发 12k 字
- R-19 前端 XSS
- R-20 网关密钥进日志
- R-22 的提示注入防护部分

### 工程债：后置

R-13、R-16、R-17、R-24、R-25、R-26、R-28、R-29、R-30。

## 2. 本轮验收口径

### R-23
- 随机/过期 qid 调 `/api/ask-sessions/{sid}/complete` 返回 404 或 400，带可读错误；
- 不再抛 `NameError`；
- 有效 qid 行为不变。

### R-21
- 无 AI（`rewriter.ready=False`）时，不调用旧 `rewrite_fn`，不做 probe；
- 有 plan_fn 但未就绪时直接原文检索；
- 保留旧 rewrite 仅用于“没有 plan_fn”的兼容测试。

### R-04
- answer 序列化后不超过 relay 上限（预留余量），保留全部命中元数据；
- 超限时先裁剪/移除 `ai_text`，并记录 `context_truncated`；
- 提问端能显示“部分长上下文已截断”，不静默丢失整条答案。

### R-18
- rerank 只改变顺序，不再按配置 `top_k` 截断；
- 端到端 `/api/search?topk=N` 不被 rerank 中间层改写；
- 保持现有 rerank 候选数限制，避免性能回退。

### R-22
- 单条资料最多占上下文预算的 25%（下限 800 字符）；
- 长资料不会挤掉后续命中；
- 不改变 `max_context_chars` 默认值。

## 3. 实施记录

- [x] R-23：qid 失效返回可读错误，不再 NameError；单测覆盖
- [x] R-21：无 AI 不再进入旧 rewrite/probe 分支；单测覆盖
- [x] R-04：answer 发送前按 240KB 字节上限裁剪；UI 显示截断；单测覆盖
- [x] R-18：rerank 只重排不截断；单测覆盖（完整 score ranking 抽象后置）
- [x] R-22：远端 ai_text 单条 ≤25% 上下文；本地 context_full 保持原语义；单测覆盖（注入防护后置）
- [x] 全量回归：376 tests passed
- [ ] 版本/发布说明

## 5. 第二轮（R27/R15/R09/R18）

- [x] R27：golden schema 统一（expect / expect_title_like / expect_doc_ids）；nDCG 使用理想排序归一；eval_retrieval/eval_ab 共用指标
- [x] R15：>8MB 文件改为头/中/尾各 1MB 采样哈希；新增尾部变更测试
- [x] R09：节点 query 外层 100–800ms 时间盒；超时发送 degraded answer；前端显示“超时”
- [x] R18：新增 searcher/ranking.py；SearchHit 增加 raw_score/score_stage/score_components；lexical/meta/vector/rerank/consensus 统一为越大越好的 RRF；最终 topk 在 API/search 边界截断
- [x] R18 回归：排序测试、聚合 provenance、rerank 不截断、最终 topk 边界

### R18 剩余约定

- `rerank.top_k` 不再参与内部截断；最终条数由调用方 topk 决定。该配置暂时保留兼容，后续从 UI/配置中移除。
- `raw_score` 保留原始 BM25/相似度；`score` 是统一 fused 分。

## 6. 第三轮 P0（R06/R10/R22 + ONNX 防崩）

- [x] ONNX 防崩：启动时子进程探测 `import onnxruntime`；失败则禁用本地 vector/rerank，主进程不导入；vector 状态暴露错误
- [x] R06：先解析后提交；`replace_doc_chunks()` 在单事务内删除旧 chunk、写入新 chunk、更新元数据并置 indexed；失败回滚保留旧内容
- [x] R10：relay 每连接 256 容量发送队列 + writer task；队列满断开慢消费者
- [x] R22：本地 context_full 与远端 ai_text 共用 25% 单条配额；`max_context_tokens=8000` token 估算上限；标题/路径/节点净化；系统提示明确资料不是指令
- [x] 全量回归：376 tests passed

## 4. 非目标

- 不重写 `NodeAgent`；
- 不改变 QueryPlan 的 timeout/max_tokens；
- 不调整 ResearchAgent 参数（R12 明确不改）；
- 不处理安全/隐私项（除非修复过程中发现阻塞性依赖）。
