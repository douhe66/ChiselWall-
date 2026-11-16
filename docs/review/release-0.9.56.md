# v0.9.56 发布说明

日期：2026-11-16

## 核心变化

- **Agent-ready 检索底层**：RetrievalKernel / EvidenceSession / ResearchAgent；经典搜索收口到 Kernel。
- **Plan-first**：首轮 QueryPlan，追问带上一轮问答上下文；后续 search 由 AI 自主换词。
- **范围读取**：fetch 支持 preview/full/around、start/next_offset/has_more；按 chunk_seq/char_start 定位命中。
- **AI 自控预算**：preview/full + max_chars；硬上限仍是 max_context_chars，不做隐式平均分摊。
- **回答协议**：工具动作严格 JSON；最终回答 FINAL + 纯 Markdown；非法动作一次修复；无证据引用拒绝。
- **版本校验**：本地/远程搜索带 hash/mtime；fetch 前复核，变化标记 version_changed，删除拒绝。
- **权限撤销复核**：fetch 前复核 share_state/挂载/分组交集；分享/分组变化清理授权缓存。
- **可观测性**：QueryPlan last_status/last_error；rerank 失败熔断 300s。
- **故障处理**：远程版本变化拒绝；partial peers coverage 进入研究上下文。

## 验证

- 全量 356 项测试通过。
- Windows PyInstaller 冻结版冒烟：health 返回 `{"ok":true,"version":"0.9.56"}`。
- 产物：`dist/chiselwall-0.9.56-win64.zip`（约 128MB）。

## 已知剩余

- relay 签名/注入分组上下文（防客户端伪造组）。
- partial peers 真跨设备验证。
- AI 全文读取授权。
- 引用区间审计。
- 旧 /summarize、/followup/stream 端点清理。
- 本地 ONNX rerank 的进程级崩溃隔离。

Linux 包未在 Windows 上构建；需在 Linux 真机执行 `packaging/build_linux.sh`。
