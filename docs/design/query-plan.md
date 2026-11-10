# 发送前检索计划（QueryPlan，v0.9.7）

## 目标

在问题广播前生成多词检索计划，改善语义表达，同时不牺牲召回：

- 组内同义词 OR；
- 不同概念组之间采用“软 AND”覆盖度排序，不做硬过滤；
- 原问题始终保留，作为候选与兜底；
- AI 未开启/未配置/失败时直接原问题检索，不增加延迟。

## 数据

```json
{"clauses":[{"terms":["文件锁","flock","fcntl"]},{"terms":["Linux"]}],"paraphrases":["Linux 文件锁用法"]}
```

- clauses：概念组；组内 OR。
- paraphrases：完整问句，进入向量召回。
- expression：UI 展示如 (文件锁 OR flock) AND (Linux)。

## 时序

1. 用户发送问题；
2. QueryRewriter.plan() 调 LLM，超时/非法返回 None；
3. 本机与远端都使用 evaluate_plan()：
   - 原问题、每个 term、每条 paraphrase 分别召回；
   - OR 合并候选；
   - 按覆盖 clause 数排序，覆盖全的优先；
   - 不丢弃低覆盖文档，不做交集硬过滤；
4. 广播带原问题 text + plan；relay 只转发；
5. 计划为空/失败：原问题普通检索。

## 回退

- AI 未配置或查询计划关闭：不调用 LLM，原问题。
- LLM 超时、异常、JSON 非法：原问题。
- 远端没有计划：按 text 原问题。
- 计划检索没有结果：原问题。

## 配置

- enabled：发送前拆词；
- max_queries：最多概念组（1-4）；
- timeout_s：发送前等待计划的上限；
- system_prompt：新 JSON 计划提示词；旧版默认逐行提示词自动迁移。

## 测试

- tests/test_query_plan.py：解析、泛词过滤、软 AND 候选保留、plan 先于广播、无计划回退；
- tests/test_bus.py / test_query_rewrite.py 回归。