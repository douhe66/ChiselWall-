# P1/P2 2×2 对比评估（rewrite × rerank）

> 目的：回答「改写能否替代本地 rerank / 怎样组合最划算」。
> 环境：用户真实库快照（241 篇），本地 bge-small-zh 向量 + P0.5 检索；改写用 deepseek-flash；本地 rerank 用 bge-reranker-base int8。

## 1. 方法

- 4 组：base（只检索）、rewrite（多查询 + RRF）、rerank（本地精排）、both；
- 指标：recall@5、nDCG@10、MRR、弱相关数、逐查询延迟；
- 两套 golden：
  - **easy**（20 条，关键词与文档高度重合）：base 已 0.95/+0.932，天花板效应，四组几乎无差异；
  - **hard**（14 条，混合 7 条同义/口语化问题 + 7 条精确问题）：用于区分方案。

## 2. 结果（hard set，14 条）

| 组合 | recall@5 | nDCG@10 | MRR | 弱相关/条 | 平均延迟 | P95 延迟 |
|---|---|---|---|---|---|---|
| base | 0.571 | 0.519 | 0.500 | 1.29 | **7ms** | 13ms |
| rewrite | 0.643 (+7.2pp) | 0.564 (+4.5pp) | 0.536 (+3.6pp) | 1.29 | 2402ms | 3468ms |
| rerank | 0.571 (0) | 0.519 (0) | 0.500 (0) | 1.29 | 88ms | 857ms |
| both | **0.643** | **0.590 (+7.1pp)** | **0.571 (+7.1pp)** | 1.29 | 237ms | 1054ms |

关键观察：
- **rewrite 提升召回**：`怎么模拟网络丢包` 从无命中 → 第 2；
- **rerank 单独无增益**：候选本来排序已较好，且很多查询只有 1 条强候选（不足 2 条时跳过精排）；
- **both 最佳**：rewrite 提供候选后，rerank 把「网络丢包」从第 2 提到第 1；nDCG/MRR 均提升；
- rewrite 的延迟几乎全部来自 LLM 调用（约 2.3–3.5s），检索本身仍是毫秒级；
- rerank 加载时内存 +约 540MB（744 vs 200MB），空闲 120s 卸载；HTTP rerank 则零本地内存。

## 3. 推荐组合（写进产品默认与文档）

| 场景 | 建议 | 内存 | 延迟 | 质量 |
|---|---|---|---|---|
| **4C8G / 内存敏感，有 AI**（推荐默认） | **rewrite 开 + rerank 关** | ~200MB | +2.4s/问 | recall +7pp |
| **有内网 rerank 服务** | **rewrite + HTTP rerank** | ~200MB | +2.4s + 一次内网请求 | 最佳（本地零模型） |
| **质量优先、内存宽裕** | rewrite + 本地 rerank | 峰值 ~740MB | +2.4s + 100–500ms | nDCG/MRR 最高 |
| **离线 / 无 AI** | base（或按需本地 rerank） | 200–740MB | 毫秒级 | 本评估中 rerank 单独无增益，默认不建议开 |
| **纯搜索、要求低延迟** | base（或 base + HTTP rerank） | ~200MB | 毫秒级 | 召回略低，交互最快 |

结论：**rewrite 可以替代「必须开本地 rerank」的需求**（内存省 540MB，召回仍提升）；但若要最佳排序，二者组合最好。**HTTP rerank 是质量与内存的最优折中**。

## 4. 复现

```bash
python tests/eval_ab.py --golden tests/fixtures/golden/queries_v3.json
```

脚本只读快照，不修改原索引库。
