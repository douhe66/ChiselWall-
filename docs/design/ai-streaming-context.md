# AI 综合：流式输出、上下文预算与逐轮引用

> v0.9.1。解决"长回答 4–14 秒无反馈、长文档被 5000 字截断、citations 只保留最近一轮"。

## 1. 流式输出

- 新增 `LLMClient.chat_stream()`：按 OpenAI 兼容 SSE 解析 `data: {...}` 增量，支持 `/v1` 与不带 `/v1` 的 base_url；
- 新增 `POST /api/ask-sessions/{sid}/summarize/stream`、`/followup/stream`：
  - SSE 事件：`start`（citations/context_chars/model）、`delta`（文本增量）、`done`（完整文本/引用/会话 turns）、`error`；
  - 生成完成后服务端立即把 assistant turn（含 citations）落库，刷新/切会话不丢；
- 前端 `streamSSE()` 边收边渲染，会话里显示"回答 · 生成中"气泡与光标；
- 若模型端点不支持 stream：在**尚未输出任何 token** 时自动回退普通 `chat()`，不会直接失败。

实测（deepseek-flash，文件锁问题）：首个 delta **约 2.2s**，总时长 4.6–7.8s；不再等整段生成完才显示。

## 2. 上下文预算

默认值（旧配置 8000/2000 自动升级）：

| 配置 | 默认 | 说明 |
|---|---|---|
| max_context_chars | 24000 | 全文/多文档总字符预算 |
| top_n | 24 | 最多片段数 |
| max_tokens | 4000 | 回答上限（流式输出，长答案不再截断） |
| timeout_s | 120 | 长回答超时 |

长文档策略（`enrich_local_context`）：

1. 文档 ≤ 12000 字：**全文**送入；
2. 更长：**开头 1800 字（背景/目录）+ 命中块上下文窗口 + 结尾 700 字**，中间用"……（中间省略）……"标记；
3. `build_context` 按总预算逐条裁剪；单条超预算时硬截断到预算，避免整体超限；
4. 设置页 AI 配置新增"上下文预算 / 片段数 / 回答上限"可调。

## 3. 逐轮 citations

- 每次 assistant turn 的 payload 保存 `citations`、`context_chars`、`elapsed_ms`、`model`；
- 前端每轮回答下都渲染该轮引用徽标，不再只有最近一轮；
- `ask_session_summarize` / `followup` / 两个 stream 端点均走同一落库逻辑。

## 4. 验收

- 单测：`tests/test_streaming.py`（流式解析、事件、fallback 结构、长文档 head+window+tail、citations 落库）；
- 全量：**293 项通过**；
- 前端实测：点击 AI 综合后 2.5s 出现 streaming 气泡、最终回答与引用落库；SSE done 返回 turns。

## 5. 边界

- 超长文档仍无法完整塞进单次上下文，会保留头/命中窗口/尾并显式标记省略；
- 模型端点若返回非 OpenAI SSE 格式，会走 fallback（无流式）；
- 前端流式渲染目前逐 token 更新；后续可按帧节流。
