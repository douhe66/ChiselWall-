# Google 搜索演进调研：从倒排索引到生成式检索

> 调研时间：2026-11-11。Google 当前生产架构未公开；以下区分公开资料与工程推断。

## 结论

1. 生成式 AI 之前，Google 的核心是大规模倒排索引 + 分层排序 + 分布式服务，而不是“理解语义”。
2. Google 并不是 2023 年才开始用 AI：RankBrain（2015）、神经匹配（2018）、BERT（2019）、MUM（2021）已经进入查询理解、召回和排序。
3. AI 时代没有被大模型取代：AI Overviews 明确接入核心网页排序系统，答案要由 top web results 支撑并附链接。
4. AI Mode 的关键是 query fan-out：把复杂问题拆成多个相关检索，并发查多个子主题/数据源，再合并成回答；Gemini 3 进一步增强了这一步。
5. 资源要求极高：Caffeine 索引约 1 亿 GB（约 100 PB），每秒处理数十万网页；生成式检索还需要大规模模型推理。凿壁只能借鉴分层思想，不能复制规模。

## 三个时代

| 阶段 | 时间 | 核心 | 代表 |
|---|---|---|---|
| 关键词/链接时代 | 1998–2012 | 倒排索引 + PageRank + 数百信号 | Brin/Page 论文、Caffeine |
| 神经语义时代 | 2013–2022 | 语义理解 + 神经召回/排序 | Hummingbird、RankBrain、BERT、MUM |
| 生成式检索时代 | 2023– | 检索 + LLM 编排 + grounded generation | AI Overviews、AI Mode、Gemini 3 |

严格说“AI 出现前”应理解为“生成式 AI 前”；Google 在 2015 年后已经大量使用机器学习。

## 生成式 AI 之前：怎么在海量网页中快速找

### 抓取与索引

- 抓取：URL frontier 调度、robots.txt、去重/规范化、增量重抓；解析标题、正文、锚文本、链接。
- 倒排索引：词项 -> posting list（文档 ID、位置、权重）；查询先查词典，再取 posting list 求交/并，不扫全库。
- 分片/副本：索引按文档或词项切到多台机器；查询并行发到 shard，各自返回 top-k，再全局归并。
- 文档库/正排：索引负责“找到”，标题、正文、链接、PageRank 等存在 doc store / Bigtable 类系统，供排序和摘要。
- PageRank：Brin & Page 1998 把链接图作为离线信号，和文本相关性一起排序；它不是唯一信号。
- Caffeine（2010）：把“整层定期重建”改为持续、全局、分块更新；官方数据约 1 亿 GB、每秒数十万页面并行处理，比旧索引新鲜 50%。

参考：Brin & Page 1998（https://snap.stanford.edu/class/cs224w-readings/Brin98Anatomy.pdf）、Google Caffeine（https://developers.google.com/search/blog/2010/06/our-new-search-index-caffeine?hl=en）、IR 教材（https://nlp.stanford.edu/IR-book/）。

### 查询服务

一次查询大致经过：

query -> 拼写纠正/同义词/意图 -> 选择索引分片 -> 各 shard 召回候选（倒排/短语/链接） -> 全局合并 -> 数百信号排序 -> 摘要/去重 -> 缓存返回

工程要点：

- 不是“查整个互联网”，而是查预构建索引；
- 候选召回和最终排序分离，先保证召回，再花算力精排；
- 大量缓存、副本、早期终止（top-k 足够就停）；
- 查询理解（纠错、同义、实体、时效）决定召回上限。

### 排序演进

- PageRank/链接分析：页面重要性；
- Panda/Penguin：内容质量、反垃圾；
- Hummingbird（2013）：从“词匹配”转向意图/语义；
- RankBrain（2015）：首个深度学习排序系统，把词映射到概念；
- 神经匹配（2018）：对 query/page 做更模糊的语义匹配，用于召回；
- BERT（2019）：理解词序和上下文，改善复杂查询；
- MUM（2021）：多语言、多任务、多模态，比 BERT 强约 1000 倍（Google 口径），但当时主要用于特定场景，不是全面替代排序。

Google 官方说 Search 运行着数百个算法和模型，各司其职、组合生效。参考：https://blog.google/products-and-platforms/products/search/how-ai-powers-great-search-results/

## 生成式 AI 之后：Google 怎么搜索

### AI Overviews

- 使用定制语言模型，但接入核心网页排序系统，先找相关高质量网页，再生成摘要；
- 答案要求被 top web results 支撑，并带链接；Google 明确说它不是只靠训练数据生成的聊天机器人；
- 仍会出错：误读 query、误读网页语义、资料不足；Google 公开承认并做了改进。

参考：https://blog.google/products-and-platforms/products/search/ai-overviews-update-may-2024/

### AI Mode 与 query fan-out

- AI Mode 用定制 Gemini 2.0，面向需要多步探索、比较、推理的问题；
- 核心是 query fan-out：把一个复杂问题拆成多个相关检索，并发查多个子主题/多个数据源（网页、Knowledge Graph、购物等），再合并；
- 多步计划：制定计划 -> 执行搜索 -> 根据结果调整计划 -> 生成回答；支持追问；
- Gemini 3（2025-11）进一步升级 fan-out：能发起更多搜索、更好理解意图，并对难题自动路由到更强模型。

参考：https://blog.google/products-and-platforms/products/search/ai-mode-search/、https://blog.google/products-and-platforms/products/search/gemini-3-search-ai-mode/

### 支撑技术

- 稠密向量/embedding + ANN：Google 公开的 ScaNN 用向量压缩/量化做大规模相似检索；
- 神经召回 + 学习排序 + 生成模型组合；
- RAG 思路：先检索证据，再让模型在证据上生成，附引用；
- 大规模模型服务、缓存、TPU/GPU、查询编排与预算控制（具体基础设施未公开）。

参考：https://research.google/blog/announcing-scann-efficient-vector-similarity-search/

## 资源要求

| 层 | Google 公开信息 | 量级含义 |
|---|---|---|
| 索引 | Caffeine 约 1 亿 GB，>10 万页面/秒 | 约 100 PB；全球多数据中心 |
| 服务 | 分片、副本、缓存、早期终止 | 数万级服务器/专用硬件（具体不公开） |
| 排序 | 数百算法/模型 | 海量特征、离线+在线计算 |
| 生成式 AI | Gemini 模型 + 检索编排 + TPU/GPU | 单次生成成本远高于关键词检索 |

结论：Google 的方案对资源要求极高，个人/部门工具无法照搬；但它的“分层”可以在小规模复刻。

## 对凿壁的启发

### 可以借鉴

1. 召回与排序分离：先用 FTS5/向量召回候选，再 rerank；不要用一个模型同时做所有事。
2. 混合检索：BM25/关键词保证精确命中，向量保证同义/换说法；RRF 融合；凿壁已在做。
3. 查询理解：拼写/同义词/概念组；当前 QueryPlan 的“同义词 OR、概念组软 AND”是 Google 查询扩展的轻量版。
4. 有界 query fan-out：AI Mode 并发多检索；凿壁可做 2–4 个子查询，但必须有超时、并发上限、缓存和原问题回退。
5. 段落级索引/摘要：Google 用 passage/snippet；凿壁用 chunk + 邻接窗口，方向正确。
6. 增量索引：Caffeine 持续更新；凿壁用 watchdog + 增量 index，避免全量重建。
7. 分布式分片：Google 索引分片；凿壁每个同事节点天然是一个 shard，relay 查询 fan-out，本地索引不集中，符合隐私边界。
8. grounded AI：AI Overviews 要求 top results 支撑；凿壁 AI 必须“无证据就说不足”，引用逐轮保留。
9. 缓存与预算：热门查询、计划、向量结果缓存；对 fan-out/LLM 设置超时。

### 不应照搬

- 不要做全网爬虫/PageRank 克隆；本地笔记链接图很小，PageRank 收益有限，可作轻量信号而非核心。
- 不要默认上大 embedding/rerank 模型；bge-small + ONNX 更适合本地。
- 不要无界 fan-out 或每次提问都调用大模型；会增加延迟和同事打扰。
- 不要把生成式回答当作搜索结果本身；保留原文、来源、可点击引用。

### 建议路线

P0（已有基础）：保持 FTS5 + jieba + bge-small + RRF；保持 QueryPlan 的软 AND + 原问题兜底；保持 rerank 可选；建立离线评测集（Recall@8、nDCG@8、零结果率、P95 延迟）。

P1（低成本增强）：查询计划缓存 + 有界子查询（2–4 条）；结果聚合从“分数相加”改为 RRF/覆盖度；利用标题、路径、更新时间、同节点共识做轻量排序；AI 回答增加“证据不足”门控。

P2（可选）：笔记间 backlink/引用图作为辅助信号；本地小 cross-encoder rerank（按需加载）；真实用户 A/B：旧单查询 vs QueryPlan vs 软 AND 展开。

## 一句话总结

Google 的强大来自“海量索引 + 分层召回/排序 + 持续更新 + 查询编排 + 证据约束”，不是某个单点算法。凿壁应学这套分层思想，在本地/部门规模用 FTS5、向量、RRF、QueryPlan、rerank 和 grounded AI 做“小而完整”的检索系统，而不是复制 Google 的规模。

## 参考资料

- Brin & Page, 1998: https://snap.stanford.edu/class/cs224w-readings/Brin98Anatomy.pdf
- Caffeine: https://developers.google.com/search/blog/2010/06/our-new-search-index-caffeine?hl=en
- How AI powers great search results: https://blog.google/products-and-platforms/products/search/how-ai-powers-great-search-results/
- AI Overviews: https://blog.google/products-and-platforms/products/search/ai-overviews-update-may-2024/
- AI Mode: https://blog.google/products-and-platforms/products/search/ai-mode-search/
- Gemini 3 in Search: https://blog.google/products-and-platforms/products/search/gemini-3-search-ai-mode/
- ScaNN: https://research.google/blog/announcing-scann-efficient-vector-similarity-search/
- IR book: https://nlp.stanford.edu/IR-book/
