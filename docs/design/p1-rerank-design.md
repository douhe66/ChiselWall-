# P1 重排（rerank）设计：HTTP 服务 + 本地可换模型

> 版本 v0.7.1。目标：提问端对候选做一次精排，提升前几条相关性；**被搜索端不参与**；模型/服务可切换，本地模型换文件不改代码。

## 1. 总体位置

```
提问端：查询 → BM25+向量召回 → P0 收敛（阈值/词面/限条） → rerank 精排 → 展示 / 交给 AI 综合
被搜索端：查询 → BM25+向量召回 → 返回片段（不做 rerank）
```

- 只有**发起搜索的节点**调用 reranker；同事机器只在被搜索时做召回。
- rerank 失败、超时、未配置 → 自动回退 P0 排序，搜索不失败。

## 2. 代码结构

| 文件 | 职责 |
|---|---|
| src/ai/rerank.py | `RerankConfig` / `RerankConfigStore` / `HttpReranker` / `LocalCrossEncoderReranker` / `build_reranker` |
| src/node/agent.py | 加载配置、`rerank_hits()`、本地搜索与提问结果接入、`save_rerank()` |
| src/api/server.py | GET/POST `/api/rerank/config`、POST `/api/rerank/test`；`/api/ask` 聚合结果精排一次并缓存 |
| src/ui/* | 设置页「重排 rerank」面板；结果徽标「已精排」 |

## 3. HTTP 服务协议

配置：`provider=http`，`base_url` 填服务根地址（代码自动拼 `/rerank`），`api_style=auto|cohere|jina|tei`。

| 风格 | 请求体 | 响应解析 |
|---|---|---|
| cohere/jina | `{model, query, documents, top_n}` | `results[].{index,relevance_score}` |
| tei | `{query, texts, truncate}` | `[{index, score}]` |
| auto | 先 cohere 风格，失败再 tei 风格 | 同上 |

失败处理：HTTP 错误/格式不符 → 尝试下一种风格；全部失败 → 抛错，上层回退 P0。

## 4. 本地模型：目录 + 清单（换模型不改代码）

```
models/rerank/<任意名字>/
  model.json       # 必需：描述输入/输出/激活
  model.onnx       # 必需：跨编码器
  tokenizer.json   # 必需：HuggingFace tokenizers 格式
```

`model.json` 示例：
```json
{
  "model_file": "model.onnx",
  "tokenizer": "tokenizer.json",
  "max_length": 512,
  "inputs": ["input_ids", "attention_mask", "token_type_ids"],
  "score": {"output": 0, "select": "first", "activation": "sigmoid"}
}
```

- `inputs`：ONNX 输入名（大多数 cross-encoder 是这三件套；没有 token_type_ids 就删掉）；
- `score.output`：输出序号；`select`：取第一个/最后一个 token；`activation`：sigmoid/softmax/none；
- 换模型 = 新建目录 + 放三个文件 + 设置页选择目录 → 保存即热切换，**无需改代码、无需重建向量索引**。

已知兼容模型族（标准 cross-encoder）：bge-reranker-base/large、jina-reranker、mxbai-rerank、ms-marco-MiniLM 等。
不支持：listwise LLM reranker、非标准输出结构（需要加适配器）。

## 5. 配置项（rerank.json）

| 字段 | 默认 | 说明 |
|---|---|---|
| enabled | false | 总开关 |
| provider | http | http / local |
| base_url / model / api_key | — | HTTP 服务 |
| api_style | auto | cohere / jina / tei / auto |
| model_dir | — | 本地模型目录（空 = models/rerank 下第一个？当前要求显式目录） |
| max_candidates | 20 | 精排候选数 |
| top_k | 5 | 精排后保留条数（也会裁掉多余结果） |
| timeout_s | 10 | HTTP 超时 |

## 6. 性能与资源（4C8G 参考）

| 模式 | 额外内存 | 延迟 | 包体 |
|---|---|---|---|
| HTTP 服务 | ≈0 | 局域网 10–50ms + 服务端 | 0 |
| 本地小模型（int8 ~50–100MB） | +80–150MB 峰值，可按需卸载 | +100–300ms | +50–100MB |
| 本地中模型（int8 ~250–300MB） | +250–350MB 峰值 | +200–500ms | +250–300MB |

建议：优先 HTTP；本地模型只精排 top-10~20，空闲卸载；4C8G 可运行小模型。

## 6.1 实测（本机，真实笔记 241 篇）

| 项目 | 结果 |
|---|---|
| HTTP 适配 | 本地桩服务验证 cohere/jina 与 TEI 两种协议、auto 自动识别 ✓ |
| 本地小模型（MiniLM-L6 int8，22MB） | 链路通；**英文模型，中文排序不可用**（会把《文件锁》排后面），只适合验证 |
| 本地中文模型（bge-reranker-base int8，266MB） | 质量正常：「文件锁怎么用」→《文件锁》第 1（score 5.17）；「加锁」→《文件锁》第 1 |
| 本地延迟 | 首次加载约 1.0–1.4s；预热后 139–392ms（10 候选） |
| 本地内存 | 启用后节点 RSS 约 **741MB**（基线约 130MB，含向量模型约 200MB）→ 4C8G 可跑但偏重，**建议 HTTP 优先** |
| 包体 | 不内置本地模型；用户自带目录。若内置 bge-reranker-base int8 约 +266MB（zip +~120MB） |

> 结论：P1 的 HTTP 模式零包体、零内存，是生产首选；本地模型保留为离线/无服务备选，默认关闭，用户可在设置页选择自己的模型目录。

## 6.2 P1.1：缓存 + 空闲卸载（实测）

- **rerank 分数缓存**：key = query + 候选文本哈希，LRU（默认 256 条）+ 300s TTL；
- **本地模型空闲卸载**：`idle_unload_s` 默认 120s，空闲后释放 ONNX session/tokenizer；
- 实测（bge-reranker-base int8，查询「加锁」）：
  - 第一次 852ms（加载+精排），第二次 21ms，第三次 6ms（缓存命中）；
  - 加载后 RSS 744.6MB → 空闲 8 秒后 **199.2MB**（测试用 idle=3s），内存可回收；
- 结论：本地模型可以「用时加载、闲时卸载」，4C8G 可接受；默认仍建议 HTTP 优先。

## 7. 演进计划

- **P1.1**：本地模型真实烟测（bge-reranker 小模型），校准 4C8G 延迟；
- **P1.2**：rerank 结果缓存（同一 query+候选集），减少重复调用；
- **P2**：查询改写/多查询、HyDE（可选，走已配置 LLM）；
- **P3**：`VectorBackend` 接口化（引擎可换、metric 声明、模型/维度变更检测）；
- 将来若 rerank 服务不可用，可考虑"本地小模型兜底"；默认仍以服务优先。