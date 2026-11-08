# P3 向量后端接口化与模型变更检测

> v0.7.6。目标：向量引擎可替换、度量显式声明、换模型/维度不会继续拿旧向量硬查。

## 1. 接口

`src/ai/vector_backend.py`：

- `VectorBackend`：`upsert / delete_chunks / search / count / close / reset / get_meta / set_meta / similarity`；
- `SqliteVecBackend`：当前默认实现（sqlite-vec vec0，L2 距离，归一化向量相似度 = 1 - d/2）；
- `StoreBackend`：测试/自定义 store 的兼容适配；
- `build_backend(name, db_path, dim, store)`：按 `vector.json.backend` 构建，默认 `sqlite-vec`。

## 2. 元数据与变更检测

同一库里新增 `vector_meta(key, value)`：

| key | 示例 | 说明 |
|---|---|---|
| model | local:bge-small-zh-v1.5:512 | 当前模型标识 |
| dim | 512 | 向量维度 |
| metric | l2 | 度量 |
| backend | sqlite-vec | 引擎 |
| updated_at | 时间戳 | 重建时间 |

- 启动/查询时对比 `model/dim/metric`；不一致 → `stale=True`；
- stale 时**停止向量召回**（关键词仍可用），状态栏提示"请重建"；
- 旧库无元数据：不判 stale（兼容），重建时写入；
- 重建（`/api/vector/rebuild`）先 `reset` 清空旧表（维度变化也能重建），再写新元数据。

## 3. 状态字段

`GET /api/vector/status` 新增：`backend / metric / model_id / stale / meta`；UI 显示后端与"模型变化请重建"。

## 4. 边界

- 目前只有 sqlite-vec 实现；FAISS/pgvector 需实现同一接口；
- 余弦/内积后端需在自身 `similarity()` 里做距离归一化；
- reset 会清空向量表，重建期间向量检索为空（关键词不受影响）。
