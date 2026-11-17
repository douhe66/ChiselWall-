# v0.9.60 发布说明

日期：2026-11-17

## 问答质量

- R-23：qid 失效时 `/api/ask-sessions/{sid}/complete` 返回可读 400，不再 NameError/500。
- R-21：无 AI/QueryPlan 未就绪时不再进入旧 rewrite/probe 分支。
- R-04：answer 发送前按 240KB 上限裁剪，保留命中元数据并显示上下文截断。
- R-18：新增统一 ranking；`score` 统一为越大越好的 RRF，`raw_score` 保留 BM25/相似度，新增 `score_stage`/`score_components`；rerank 只重排，最终 topk 在展示边界截断。
- R-22：远端 ai_text 与本地 context_full 共用 25% 单条配额；新增 8000 token 估算上限；资料标题/路径/节点净化；系统提示明确“资料不是指令”。
- R-27：统一 golden schema，修复 nDCG 理想排序归一；eval_retrieval/eval_ab 共用指标。
- R-15：>8MB 文件改为头/中/尾采样哈希，修复尾部修改漏索引。
- R-09：节点 query 加应答时间盒，超时返回 degraded answer；前端显示超时。

## 稳定性

- R-06：单文档索引改为原子替换；先解析成功，再在单事务内替换 chunk 与状态；失败回滚保留旧内容。
- R-10：relay 每连接发送队列 + writer task；队列满断开慢消费者，避免队头阻塞。
- ONNX Runtime 损坏时，启动用子进程探测；失败则禁用本地 vector/rerank 并回退关键词检索，主进程不再因 DLL access violation 退出。

## 验证

- 全量 376 项测试通过。
- 本地源码服务 `/api/search`、新建会话、无效 qid complete、relay 连接均已冒烟。

## 已知限制

- 当前机器 ONNX Runtime 仍不可用，vector/rerank 为降级状态。
- 冻结版 ONNX 子进程探测尚未覆盖；完整 worker 进程隔离仍未实现。
- R12 ResearchAgent 预算未改（用户决策）。