# 混合检索（BM25 + 向量）前沿与大厂实践调研

> 调研时间：2026-11-08；目的：定位「文件锁怎么用 → 返回无关代码文件（语义命中）」的根因，并给出可落地的检索质量方案。
> 结论先行：**这是可修的排序/阈值问题，不是向量检索的必然结果**。行业主流不是「BM25 + 向量简单相加」，而是「多路召回 → 融合（RRF/加权）→ 重排（cross-encoder）→ 选择性输出」；纯向量低置信结果必须有独立门槛，不能与 BM25 分数混用阈值。

## 1. 主流架构：两阶段/三阶段检索

当前主流搜索栈（Elastic、Azure、Vespa、Weaviate、Qdrant、以及 Dify/RAGFlow/FastGPT 等 RAG 框架）基本一致：

1. **召回（first stage）**：BM25/全文 + 向量（dense，可选 sparse/多向量），各取 top-N（常见 50–200）；
2. **融合（fusion）**：RRF（rank-based）或加权分数融合（score-based），把两路结果合成一个列表；
3. **重排（second stage / rerank）**：cross-encoder 或语义排序器对 top-N 重新打分，输出 top-k；
4. **后处理**：去重、MMR/多样性、阈值、低置信折叠、按来源过滤。

代表实现：
- **Elasticsearch RRF**：`rank_constant` 默认 **60**，各子检索等权；提供 `rank_window_size` 控制候选集。[1]
- **Azure AI Search**：混合查询 = BM25 + HNSW/eKNN 向量，RRF 合并；再可选**语义排序器**（二次排序 + 查询改写，基于多语言深度模型）。[2][3]
- **Vespa**：多阶段 ranking，把最贵的模型只用在少数候选上（multi-phase ranking），兼顾相关性与算力。[4]
- **Weaviate**：`alpha` 控制向量/关键词权重；融合算法从 `rankedFusion`（按名次）演进到 **`relativeScoreFusion`（分数归一化后加权）**，1.24 起为默认。[5]
- **Qdrant**：`prefetch` 子查询 + RRF/融合，多路召回后统一排序。[6]
- **BGE-M3**：一个模型同时产出 dense / sparse(lexical) / multi-vector(ColBERT) 三种表示，可在同一模型内做混合排序，减少多模型拼接成本。[7]

## 2. 大厂与主流产品的关键选择

| 产品/框架 | 召回 | 融合 | 重排 | 备注 |
|---|---|---|---|---|
| Elasticsearch | BM25 + kNN | RRF（k 默认 60） | 可选（learning-to-rank/外部） | 等权子检索；无参数调优也能用 [1] |
| Azure AI Search | BM25 + HNSW/eKNN | RRF（语义排序器建议 k=50） | 语义排序器（cross-encoder 类） | 查询改写 + 二次排序，显著提升相关性 [2][3] |
| Vespa | 任意检索器 | 多阶段 ranking | 多阶段模型 | 只在候选集上跑重模型 [4] |
| Weaviate | BM25 + 向量 | alpha 加权 + ranked/relativeScoreFusion | 可选 | 默认 relativeScoreFusion（归一化）[5] |
| Qdrant | 多向量/稀疏 | prefetch + RRF | 可选 | 支持嵌套 prefetch [6] |
| Dify（国内常见 RAG） | 关键词 + 向量（hybrid） | 加权分数（weighted score） | rerank 模型 | 去重；代码注释明确「向量阈值不可与融合/重排分数混用」[8] |
| RAGFlow | 多路召回（关键词/向量） | 融合 | rerank 模型 | 支持 query expansion、rerank、多阶段 [9] |
| FastGPT | 关键词/向量/混合 | 加权 | rerank 模型 | 入口先做 query extension + rerank query 生成 [10] |

## 3. 为什么「语义命中」会冒出无关代码文件

行业结论与我们的现象一致：

1. **分数不可比**：BM25 分数（无界）、余弦相似度（0–1）、RRF 融合分数（1/(k+rank)）三者量纲不同。用同一个阈值（我们的 0.45）筛「向量单独命中」必然误伤/漏放；Dify 源码里也专门注明：向量检索阶段的阈值**不能**用于过滤融合/重排后的分数。[8]
2. **BGE 类模型的相似度分布偏高**：BGE 官方说明 v1.5 训练温度低，相似度集中在较高区间；官方 FAQ 明确「两句不相似的话相似度也可能 >0.5」[11]。0.45 的阈值基本等于不过滤。
3. **RRF 不是万能默认**：2026 年的 *The Fusion Tax* 论文系统评估了 7 个 BEIR 数据集、5 个 dense 编码器、RRF 与加权融合：即使最好的融合方法，也会在 **13–48%** 的查询上把相关文档排在「两路中更好的那一路」之后；RRF 在 dense 明显更强时税更重；cross-encoder 只能部分收回。[12]
4. **纯向量召回没有词面约束**：代码文件、日志、表格等「技术词密集」文本容易与任意技术问题产生中等相似度；没有词面重叠的纯向量命中应当降权/限条/折叠，而不是直接进正文结果。

**所以：不是「必然」，而是我们缺了行业标准的三件事——独立阈值/置信分层、重排、融合策略选择。**

## 4. 提升检索质量的主流手段（按投入产出排序）

1. **重排（rerank）**：cross-encoder（bge-reranker、Cohere Rerank、Azure semantic ranker）对 top-20~50 精排，是业界公认「最划算的质量提升」；BGE 官方推荐「embedding 召 top-100 → reranker 取 top-3」。[11] BEIR 也显示 reranking/late-interaction 平均效果最好但算力高。[13]
2. **查询理解**：问句改写、多查询扩展（FastGPT 的 query extension）、HyDE（用 LLM 生成假设文档再检索，零样本提升 dense 召回，但要额外 LLM 调用）[14]。
3. **融合策略**：RRF（稳、免调参）vs 归一化加权（需要校准）；Weaviate 已把默认从 rank-based 切到 relativeScoreFusion，说明「分数归一化 + 加权」在工程上更可控。[5]
4. **选择性融合/路由**：Fusion Tax 建议按语料的 sparse–dense 平衡决定是否融合，甚至 per-query 路由（难但收益明确）。[12]
5. **阈值与置信分层**：纯向量结果单独门槛（模型相关的高分位）、限条数、折叠「低置信语义结果」；不要与 BM25/RRF 分数共用阈值。
6. **分块与上下文**：小分块召回 + 父块/邻块扩展（我们已经做本地全文/邻块），减少「片段太短答不全」。
7. **评估**：固定 golden set（20–50 问），指标 recall@5/10、nDCG@10、MRR；对比 BM25 / hybrid / hybrid+rerank，别只看单条体感。

## 5. 对我们项目的建议方案（分阶段）

### P0（立即，修当前噪声；1 天内）
- 纯向量命中加「置信门槛」：相似度 ≥ **0.62**（BGE v1.5 分布的高分区，而不是 0.45）；
- 纯向量命中要求**词面证据**：标题/分块至少包含一个查询内容词（jieba 词或 2-gram），否则折叠；
- 纯向量命中**最多 3 条**、排在 BM25/hybrid 之后，UI 标为「语义补充」并默认折叠；
- 阈值/条数进配置（vector.json），不硬编码；保留「关闭向量」开关。
- 验收：`文件锁怎么用` 不再出现 app.js；`加锁`、`文件锁怎么用`、`进程启动参数`、`数据库索引` 四条真实查询的前 3 条人工判定合格。

### P1（1–2 周，质量主提升）
- 引入 **cross-encoder rerank**（本地 bge-reranker-base ONNX，或内网 rerank 接口）：hybrid 召 top-20 → rerank → top-5；
- rerank 分数**单独排序**，不与 BM25 混算；RRF 继续做召回融合（k=60 与 Elastic 默认一致）；
- 代价评估：rerank 20 条约几十~几百 ms（CPU），需测 P95；模型体积 +100~300MB（base）或更小模型。

### P2（可选，2–3 周）
- 查询改写（规则 + LLM 可选）：问句去问词已有；多查询扩展参考 FastGPT；
- HyDE 仅作为开关（需 LLM，成本高，默认关）；
- 选择性融合：按语料/查询特征决定是否启用向量（Fusion Tax 思路）。

### P3（架构）
- `VectorBackend` 接口化（metric 声明、模型/维度变更检测、自动重建提示）；
- 规模 >10 万向量再评估 FAISS/HNSW；当前 sqlite-vec 足够（1755 块查询 22–35ms，其中大头是查询向量化）。

### P4（评估体系）
- 建 30 条真实问题的 golden set（含「文件锁怎么用」这类），记录：命中标题、rank、route、人工相关性 0/1/2；
- 每次调参跑 recall@5、nDCG@10、MRR，对比 BM25-only / hybrid / hybrid+rerank；
- 把评估脚本放进 tests/，防止回归。

## 6. 参考来源

1. Elasticsearch — Reciprocal rank fusion: https://www.elastic.co/guide/en/elasticsearch/reference/current/rrf.html
2. Azure AI Search — 混合搜索概述（RRF、k=50 建议）: https://docs.azure.cn/zh-cn/search/hybrid-search-overview
3. Azure AI Search — 语义排名概述（二次排序 + 查询改写）: https://docs.azure.cn/zh-cn/search/semantic-search-overview
4. Vespa — Multi-Phase Ranking: https://vespa.ai/ai-search-platform/architecture/multiphase-ranking/
5. Weaviate — Hybrid search（alpha、rankedFusion/relativeScoreFusion）: https://docs.weaviate.io/weaviate/search/hybrid ; 融合算法博客: https://weaviate.io/blog/hybrid-search-fusion-algorithms
6. Qdrant — Hybrid Queries（prefetch/RRF）: https://qdrant.tech/documentation/concepts/hybrid-queries/
7. BGE-M3 — dense/sparse/multi-vector 与 hybrid ranking: https://bge-model.com/bge/bge_m3.html
8. Dify — retrieval_service.py（HYBRID_SEARCH、weighted score、去重、阈值注释）: https://github.com/langgenius/dify/blob/main/api/core/rag/datasource/retrieval_service.py
9. RAGFlow retrieval tutorial（hybrid、query expansion、rerank）: https://github.com/johnxie/awesome-code-docs/blob/main/tutorials/ragflow-tutorial/04-retrieval-system.md
10. FastGPT — dataset search 入口（query extension + rerank query）: https://github.com/labring/FastGPT/blob/main/packages/service/core/dataset/search/index.ts
11. BGE 模型卡 — 相似度分布与「embedding 召回 + reranker 精排」建议: https://huggingface.co/BAAI/bge-small-zh-v1.5
12. A. Chauhan, A. Agarwal — *The Fusion Tax: A Diagnostic Analysis of When Hybrid Retrieval Hurts*, arXiv:2610.30612: https://arxiv.org/html/2610.30612v1
13. Thakur et al. — *BEIR: A Heterogenous Benchmark for Zero-shot Evaluation of Information Retrieval Models*, arXiv:2104.08663: https://arxiv.org/abs/2104.08663
14. Gao et al. — *Precise Zero-Shot Dense Retrieval without Relevance Labels (HyDE)*, arXiv:2212.10496: https://arxiv.org/abs/2212.10496