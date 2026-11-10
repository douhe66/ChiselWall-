# AI 自定义网关接入（v0.9.6）

## 模式

- 标准 OpenAI：沿用原有 base_url 规范化与 /v1 兼容；
- 自定义网关：base_url 可直接填接口，也可写模板，如 ${host}/inf/v1、http://bai.com/${path}、${host}/${path}。
  网关模式不自动追加 /v1，只在末尾追加 /chat/completions（若已是完整接口则不重复追加）。

## 占位符

- 自动解析任意 ${name} 占位符；
- 每个占位符配置一个本地文件；
- 每次请求读取文件（UTF-8、去首尾空白、拒绝空内容/换行/超大），替换后校验 http(s)；
- 相对路径相对 ~/.chiselwall（设置 CHISELWALL_CONFIG_DIR 可改）；
- 文件缺失/为空时请求与测试连接都给出明确错误，不静默回退。

## 请求头

- 两种模式都可配；
- 默认 Content-Type: application/json；有 api_key 时自动 Bearer；
- 自定义 Authorization 显式优先，Content-Type 不允许覆盖；
- GET 配置只返回名称和是否已设置，值留空保存表示保留，删除行表示删除；
- 应用于 chat / chat_stream / ping，不改变 embedding、rerank 配置。

## 实现

- AIConfig 增加 mode、placeholder_files、headers；
- LLMClient 统一走 request_urls() + request_headers()，chat/stream/ping 共用；
- 设置页接入方式下拉、占位符文件自动字段、请求头增删；
- 测试：tests/test_ai_gateway.py；全量 313 项通过。