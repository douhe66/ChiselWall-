# M7 向量混合检索 · 进展报告

> 版本 v0.7.0；状态：核心链路完成（本地 ONNX 模型 + sqlite-vec + BM25/向量 RRF 融合 + 设置页开关），真实笔记实测通过；打包与质量调优待收尾。

## 1. 实现

| 组件 | 文件 | 说明 |
|---|---|---|
| Embedding 提供方 | src/ai/embedding.py | 本地 bge-small-zh-v1.5 ONNX int8（CLS pooling + L2 归一化，查询前缀）、OpenAI 兼容 /embeddings、配置持久化 vector.json |
| 向量存储 | src/ai/vectorstore.py | sqlite-vec vec0 表（chunk_id, FLOAT[512]），独立连接 + busy_timeout |
| 向量索引 | src/ai/vector_index.py | 文档分块向量化、重建全部、查询 KNN；失败不影响关键词检索 |
| 混合检索 | src/searcher/search.py | BM25 + 向量 RRF 融合（BM25 权重 1.0 / 向量 0.7，向量单独召回要求相似度 ≥0.45），route=hybrid/vector |
| 索引钩子 | src/indexer/indexer.py | on_indexed 回调：新索引/更新文档时自动向量化 |
| API | src/api/server.py | GET/POST /api/vector/config、GET /api/vector/status、POST /api/vector/rebuild |
| UI | src/ui/* | 设置页「语义搜索」面板（开关/提供方/模型路径/内网端点/保存/重建/状态）；结果徽标「语义命中 / 混合命中」 |

## 2. 实测（用户真实思源笔记，241 篇 / 1755 块）

| 项目 | 结果 |
|---|---|
| 本地模型体积 | 22.9MB（int8 ONNX）+ tokenizer 0.42MB |
| 建向量 | 241 篇 / 1755 块，用时 41.7s（≈24ms/块，一次性；之后增量） |
| 查询延迟 | 22–35ms（热态；首次含模型加载约 470ms） |
| 质量示例 | 「加锁」→《文件锁》混合命中第 1；「文件锁怎么用」→《文件锁》语义命中第 1；「数据库索引优化」→《数据库索引》第 1 |
| 内存 | 开启向量后 RSS ≈ 202MB（基线约 130MB；模型+ORT 约 70MB）→ 超出 200MB 红线，需要在文档/设置中标注，默认关 |
| 测试 | 253 项全绿（M7 新增 4 项：向量存取/重建/混合路由/关闭回退） |

## 3. 已知问题 / 待收尾

- **打包**：需把 onnxruntime、sqlite-vec 扩展、models/ 目录打进安装包（PyInstaller --collect-all onnxruntime --collect-all sqlite_vec --add-data "models;models"），包体预计 +60–80MB；
- **质量调优**：向量单独召回的噪声仍需按语料调阈值/权重；建议用 20 条真实问题做一次对比评估（roadmap M7 验收口径）；
- **内存**：开启向量后 RSS 约 200–230MB；可接受但需在设置页提示；后续可做空闲卸载模型；
- **内网 /embeddings**：代码已支持（provider=openai），尚无真实内网端点可测；
- 向量表未在文档删除时清理（检索时 JOIN chunks 已过滤，无脏结果；后续补清理）。

## 4. 使用方式

设置 → 语义搜索 → 勾选启用（本地模型路径留空即用打包/项目内 models 目录）→ 保存 → 重建向量索引。