# 分布式知识共享软件 · 开源方案调研报告

> 调研目标：为「中心服务器只做消息分发、知识全部留在个人电脑」的部门内知识共享软件（10–100 人，Windows + Linux，企业内网/VPN）选型开源组件。
> 调研方式：基于官方文档与公开技术文章的检索与阅读（web_search / web_fetch），关键结论均附来源 URL。调研时间：2026-11。

## 摘要（TL;DR）

- 消息总线首选 **NATS**：单二进制、原生 pub/sub + 请求-回复 + 超时/no-responders、leaf node 与 WebSocket 支持；核心 NATS「无持久化、at-most-once」恰好等于「中心只路由、不存知识」；鉴权体系完整（token/NKeys/JWT/mTLS）；自带慢消费者切断保护。
- 备选 **MQTT（Mosquitto/EMQX）**：成熟轻量，但请求-回复是 MQTT 5.0 的约定式模式，不如 NATS 原生。
- 直连数据面备选 **Tailscale + Headscale（自建控制面 + 自建 DERP）** 或 **ZeroTier（自建 controller/roots）**：控制面中心化、数据面直连，适合需要节点间直传大结果的场景；需提前确认企业网络放行 UDP/WireGuard。
- **不推荐全 P2P（YaCy / SSB / IPFS+gossipsub）**：索引/feed 的 gossip 复制与「知识留本地」直接冲突，企业内网 NAT 穿透不可控，运维复杂度高。
- 资源保护：节点侧 **token bucket 限流 + 单查询时间预算 + 自适应并发上限**（参考 Netflix concurrency-limits）；服务端依赖 NATS 慢消费者机制。
- 鉴权：内网最小可用 = **中心 TLS + 每用户 token/密码 + 按 subject 授权**；进阶 = mTLS 或 NKeys；用户级黑名单放应用层。

## 1. 去中心化 / 联邦式搜索与消息系统逐项评估

### 1.1 YaCy（P2P 搜索引擎）

**原理**：Java 实现的完整搜索引擎应用——爬虫、索引、查询引擎、Web 管理界面（默认 localhost:8090）合并在一个进程内；各节点通过内置 P2P 协议互相交换索引分片，组成分布式搜索网络；可在 Web 界面切换为「仅本地索引」的独立模式（intranet search appliance 模式），官方明确其可作内网企业搜索替代 [YaCy GitHub README](https://github.com/yacy/yacy_search_server)。

**成熟度**：老牌项目，仍在维护（截至 2026-10 近期仍有提交，约 4k stars），但工程化一般——「预编译包不定期生成，建议自行编译或使用 Docker」，需 Java 17+ [YaCy GitHub README](https://github.com/yacy/yacy_search_server)、[hysenlabs 项目评估](https://hysenlabs.com/projects/yacy-yacy-search-server)。

**部署复杂度**：高。Java 进程内存占用可观；**默认模式会加入公共 P2P 网络**，必须显式切换模式才适合纯内网 [YaCy GitHub README](https://github.com/yacy/yacy_search_server)。

**是否适合「中心只路由」**：不适合。YaCy 的核心价值恰恰是「索引分片交换」，与本项目「知识留本地」目标相反；其「独立模式 + 本地索引」做法可作本地 agent 的参考，但整体引入过重。

### 1.2 Secure Scuttlebutt (SSB)

**原理**：本地优先的 P2P 社交协议，基于**仅追加（append-only）签名日志 + gossip 复制**；「pubs」是可选中继服务器，为离线用户存消息、提供用户发现；客户端默认下载「关注者及其二度、三度关注者」（默认 3 跳）的完整 feed [arxiv: Bluesky and the AT Protocol §4.1](https://ar5iv.labs.arxiv.org/html/2402.03239)。

**成熟度**：曾有 Manyverse/Patchwork 等应用十余年历史，但生态收缩——主力应用 Manyverse 于 2024 年转向后继协议 PZP，官方自述原因：存储无限增长、无删除能力、单设备身份、消息分叉（fork）等问题难以在 SSB 上修复 [Manyverse: Launch of the PZP protocol](https://www.manyver.se/blog/2024-07-03)。

**部署复杂度**：中高。以 Node.js 生态为主，需维护 gossip 与本地 feed 存储。

**是否适合**：不适合。SSB 的核心机制是把他人 feed 复制到自己机器（3 跳范围），与「知识留本地」冲突；且协议设计上「一旦发布不可删除」，对部门知识共享是硬伤 [arxiv §4.1](https://ar5iv.labs.arxiv.org/html/2402.03239)。

### 1.3 IPFS / libp2p pubsub (gossipsub)

**原理**：IPFS 是内容寻址的开放协议族，本身不是存储服务（内容要靠节点 pin 或 pinning 服务保存）[IPFS: What is IPFS](https://docs.ipfs.tech/concepts/what-is-ipfs/)；libp2p 的 gossipsub 是基于随机主题网格 + gossip 的通用 pubsub 协议，已有 v1.0/v1.1/v1.2 三代规范 [libp2p gossipsub spec](https://github.com/libp2p/specs/blob/master/pubsub/gossipsub/README.md)。

**成熟度**：libp2p 生产级（IPFS、以太坊等使用）；但 NAT 穿透在企业/家庭网络仍是主要痛点——2025 年对 AutoNAT v2 的跨实现审计表明：多数节点位于 NAT 之后，可达性检测与 DHT 模式翻转问题真实存在（翻转 2–12%/Kubo 版本，多数与断线重启相关）[probe-lab AutoNAT 审计报告](https://github.com/probe-lab/autonat-perf-audit/blob/main/docs/final-report.md)。

**部署复杂度**：高。需自己维护 DHT、连接管理、relay 等。

**是否适合**：作为完全去中心的消息层「能用但过重」。10–100 人内网引入 DHT/gossip 得不偿失；若未来确实要去中心，可再评估其 pubsub 组件。

### 1.4 ZeroTier / Tailscale / Headscale 组网

**原理**：三者都是「软件定义覆盖网络」。Tailscale 基于 WireGuard + NAT 穿透；控制面只做 WireGuard 公钥交换、IP 分配、ACL 下发，数据面节点直连 [Headscale README](https://github.com/juanfont/headscale)。Headscale 是 Tailscale 控制服务的开源自建实现，定位单 tailnet（个人或小组织）[同上](https://github.com/juanfont/headscale)。ZeroTier 分两层：控制器（虚拟 L2，完全不处理流量）与 roots（虚拟 L1，直连失败时中继；全链路端到端加密，中继无法解密）[ZeroTier Self Hosting](https://docs.zerotier.com/selfhosting/)。

**成熟度**：商业产品 + 成熟开源实现，广泛用于内网穿透/异地组网；Headscale 官方声明「不支持也不鼓励用反向代理和容器方式运行」[Headscale README](https://github.com/juanfont/headscale)。

**部署复杂度**：中。Headscale 单二进制，但需管理客户端注册与 DERP；内嵌 DERP 服务器默认关闭、需自行启用，官方提醒「只保留自建 DERP 是单点故障」[Headscale DERP 文档](https://headscale.net/development/ref/derp/)。ZeroTier 自建控制器**不提供官方 Web UI，需用 JSON API 管理**；自建 planet（roots+controller）有成熟社区项目（如 zerotier-planet）[ZeroTier Self Hosting](https://docs.zerotier.com/selfhosting/)、[zerotier-planet](https://github.com/Jonnyan404/zerotier-planet)。

**平台与授权**：Headscale 文档确认客户端支持 Windows 与 Linux 等平台 [Headscale: Client and OS support](https://headscale.net/stable/about/clients)；ZeroTier 官方下载页提供 Windows/Linux 等各平台客户端 [ZeroTier Download](https://www.zerotier.com/download/)。授权需注意：ZeroTier 免费个人档仅 10 设备、商用要 Essential 18 美元/月起（超出 10 台每台 2 美元）[ZeroTier Pricing](https://www.zerotier.com/pricing)；Tailscale 免费档限 6 用户（设备不限）、商用 Standard 8 美元/用户/月 [Tailscale Pricing](https://tailscale.com/pricing)；ZeroTier 自建基础设施的许可证官方要求「自行核对」[ZeroTier Self Hosting](https://docs.zerotier.com/selfhosting/)。

**是否适合「中心只路由」**：非常适合作为**组网层**——控制面中心化、数据面直连，天然「中心不碰内容」。但它只解决「怎么连通」，不解决「怎么广播问题、收集答案」，仍需在其上叠加消息协议；企业内网需确认放行 UDP/WireGuard，否则要自建 DERP 兜底 [Headscale DERP 文档](https://headscale.net/development/ref/derp/)。

### 1.5 NATS

**原理**：开源消息系统，轻量 pub/sub + 请求-回复；高性能、低延迟，支持 wildcard 订阅（* 单段、> 多段）、queue group、scatter-gather，JetStream 为可选持久层 [HiveMQ 对 NATS 的介绍](https://www.hivemq.com/blog/building-unified-namespace-why-mqtt-outperforms-nats/)、[NATS Learn 索引](https://docs.nats.io/using-nats/developer/sending/request_reply)。关键特性：**核心 NATS 无队列、无存储、无确认，at-most-once，只投递给发布时刻在线的订阅者**；需要持久化才引入 JetStream [Core NATS Deep Dive](https://docs.nats.io/learn/core-nats.md)。

**成熟度**：生产级，被评价为「high performance and simplicity，适合微服务与实时处理」[HiveMQ 对比文](https://www.hivemq.com/blog/building-unified-namespace-why-mqtt-outperforms-nats/)。

**部署复杂度**：低。单服务器二进制即可；官方 GitHub Releases 提供含 Windows 在内的多平台二进制 [NATS Releases](https://github.com/nats-io/nats-server/releases)。leaf node 支持 hub-and-spoke 拓扑：spoke 主动连 hub（默认端口 7422），配置自带重连间隔、写超时（默认 10s）、TLS、压缩 [NATS leafnodes 配置](https://beta-docs.nats.io/reference/2.12/config/leafnodes/)；原生 WebSocket 监听器（含浏览器客户端、TLS、Origin 校验、cookie 鉴权）[NATS websocket 配置](https://beta-docs.nats.io/reference/2.14/config/websocket/)；还可作为 MQTT broker 兼容 MQTT 客户端 [NATS MQTT 支持](https://docs.nats.io/running-a-nats-service/configuration/mqtt)。

**是否适合**：**最贴合**。核心 NATS 的「无持久化、at-most-once、按兴趣即时路由」就是「中心只路由、不存知识」的现成实现；请求-回复 + 超时 + no-responders 信号直接支撑「问题广播、结果回传」。

### 1.6 MQTT（Mosquitto / EMQX）

**原理**：OASIS 标准 pub/sub，broker 只做主题路由（QoS 0/1/2、retained 消息）；请求-回复在 MQTT 5.0 以 Response Topic + Correlation Data 约定实现，需预订阅响应主题并用唯一 correlation id 匹配 [HiveMQ: MQTT 5 Request-Response](https://www.hivemq.com/blog/mqtt5-essentials-part9-request-response-pattern/)。

**成熟度**：IoT 事实标准。Mosquitto 支持 MQTT 5.0/3.1.1/3.1，默认仅回环监听（2.0 起需显式配置对外监听）[mosquitto(8)](https://mosquitto.org/man/mosquitto-8.html)；鉴权支持无鉴权/用户名密码（password_file）/TLS 证书/PSK [mosquitto.conf(5)](https://mosquitto.org/man/mosquitto-conf-5.html)。厂商观点：MQTT 生态在 IoT/工业集成上比 NATS 强，NATS 胜在云原生服务间通信的简单性 [HiveMQ 对比文](https://www.hivemq.com/blog/building-unified-namespace-why-mqtt-outperforms-nats/)。

**部署复杂度**：低（Mosquitto 单进程）。

**是否适合**：适合，但「问题→答案」要自己维护 response topic、correlation id、超时与去重；NATS 的请求-回复原语对本案更直接。

### 1.7 逐项小结

| 技术 | 原理一句话 | 成熟度 | 部署复杂度 | 适合「中心只路由」 |
|---|---|---|---|---|
| YaCy | P2P 索引分片交换 | 老项目仍维护、发布慢 | 高（Java、内存大） | 否（索引复制与需求冲突） |
| SSB | append-only 日志 + gossip 复制 | 生态收缩，主力转 PZP | 中高 | 否（feed 复制、不可删除） |
| libp2p gossipsub | 主题网格 + gossip | 生产级，NAT 穿透难 | 高 | 勉强可作消息层，过重 |
| ZeroTier/Tailscale/Headscale | 覆盖网络：控制面中心化 + 数据面直连 | 成熟 | 中 | 适合作组网层（本身不是消息系统） |
| NATS | pub/sub + 请求-回复，核心无持久化 | 生产级 | 低 | **最贴合** |
| MQTT | broker 主题路由 + QoS | 非常成熟（IoT 标准） | 低 | 适合（请求-回复靠约定） |

## 2. 「中心只路由、知识留本地」的工程参考模式

### 2.1 三种现成模式

**模式一：NATS leaf node / WebSocket relay。** 中心跑单个 nats-server（核心模式，不开 JetStream），节点 agent 用普通客户端或 leaf node 接入；leaf node 即「spoke 主动连 hub」的跨网段桥接，自带重连间隔、写超时（默认 10s）、TLS [NATS leafnodes 配置](https://beta-docs.nats.io/reference/2.12/config/leafnodes/)。浏览器或受限网络走 WebSocket 监听器（TLS、Origin 校验、服务器 ping 默认 2 分钟）[NATS websocket 配置](https://beta-docs.nats.io/reference/2.14/config/websocket/)。官方文档明确 WebSocket 模式支持「leaf nodes over port 443」[NATS Learn 索引](https://docs.nats.io/using-nats/developer/sending/request_reply)。请求-回复韧性：明确区分「超时」与「无应答者（no responders）」，重试必须幂等 + 退避 [NATS Request-Reply Resilience](https://beta-docs.nats.io/learn/resilient-clients/request-reply-resilience)。

**模式二：signaling server（WebRTC 式）。** 中心只做发现与协商中转：SDP/ICE 信令内容对服务器是黑盒，服务器「不需要理解内容」，数据面点对点直连；信令传输不限 WebSocket（fetch 等皆可）[MDN: Signaling and video calling](https://developer.mozilla.org/en-US/docs/Web/API/WebRTC_API/Signaling_and_video_calling)。适用于「答案很大、想直传」的场景；NAT 不通时仍需 TURN/中继兜底。

**模式三：overlay 控制面（Tailscale/Headscale、ZeroTier）。** 控制面只交换公钥、下发 IP 与 ACL；数据面直连，DERP/roots 作兜底中继且端到端加密 [Headscale DERP 文档](https://headscale.net/development/ref/derp/)、[ZeroTier Self Hosting](https://docs.zerotier.com/selfhosting/)。

### 2.2 在线状态与心跳管理

- WebSocket/TCP 长连接会「假死」（readyState 仍 OPEN 但收不到数据），只能靠应用层心跳超时发现；心跳间隔须小于 NAT 会话老化时间（常见约 60s），实践组合为「25s 发 ping、10s 等 pong」；收到任何消息都刷新最后活跃时间 [阿里云 WebSocket 心跳实践](https://developer.aliyun.com/article/1763056)。
- NATS 侧由服务器 ping-pong 自动探活，无响应的订阅者被直接断开，客户端需自带重连逻辑 [NATS Slow Consumers](https://docs.nats.io/running-a-nats-service/nats_admin/slow_consumers)。
- 中心只需在内存维护「用户→节点在线状态」路由表（元数据，不涉及知识内容本身）。

### 2.3 断线重连与结果超时

- 重连用指数退避 + 随机抖动：第 n 次等待 min(1000·2ⁿ, 30000)ms，避免服务端抖动时大量客户端齐步重连形成「重连风暴」；每次重连前清理旧连接与定时器，保证单连接；重连后按 seq 消息补偿 + 去重，超出补偿窗口则降级为全量刷新 [阿里云 WebSocket 心跳实践](https://developer.aliyun.com/article/1763056)。
- 超时语义：每个查询带时间预算（deadline）。NATS 请求-回复有「三结局」——按时答复 / 超时 / 无应答者；**超时不代表对方死了**（可能只是慢），重试必须幂等；服务端在无任何订阅者时会立即返回 no-responders，避免干等到超时 [NATS Request-Reply Resilience](https://beta-docs.nats.io/learn/resilient-clients/request-reply-resilience)。

## 3. 资源保护机制（防止查询把他人电脑打满）

### 3.1 查询限流（token bucket）

- 原理：以恒定速率 r 补充令牌、桶容量 b 决定最大突发；超限流量三选一：丢弃（policing）、排队整形（shaping）、标记后放行；长期平均速率不超过 r，最大突发时长 T = b/(M−r) [Wikipedia: Token bucket](https://en.wikipedia.org/wiki/Token_bucket)（内容经镜像核对：[EEM-Hub wiki-Token_bucket.md](https://github.com/EEM-Hub/system-design-eem/blob/dd25bdc3e533fd8004179bbfd0260eb3656d0d4a/entries/2026/06/19/wiki-Token_bucket.md)）。
- 落地：每个 agent 进程内实现（按「来源用户」分桶），无需外部存储；限流同时防滥用与 DDoS/爬取式打满 [Redis Glossary: Rate Limiting](https://redis.io/glossary/rate-limiting/)。同时限「接受他人查询」与「本机向外发查询」两个方向。

### 3.2 单查询时间预算

- 每个查询带 deadline，agent 端把预算分配到索引搜索、文档读取、结果组装各阶段；超时立即返回部分结果或空结果，绝不排队等待。
- 与 NATS 的「请求超时 + no-responders」语义对齐，请求方可提前失败而不是挂起 [NATS Request-Reply Resilience](https://beta-docs.nats.io/learn/resilient-clients/request-reply-resilience)。

### 3.3 背压 / 慢消费者

- 系统级范例：NATS 用 ping-pong 检测慢消费者并**直接切断连接**——「保护系统整体优先于迁就单个消费者」；客户端可先自查（pending 超阈值即丢弃消息并回调告警），避免被服务端强断 [NATS Slow Consumers](https://docs.nats.io/running-a-nats-service/nats_admin/slow_consumers)。
- 应用级：有界队列 + 明确丢弃策略（丢新/丢旧），队列满即拒绝新查询，而不是无限排队。

### 3.4 并发上限（自适应并发）

- 固定 RPS 限流会随环境过时；Netflix concurrency-limits 借用 TCP 拥塞控制思想做**自适应并发上限**：Little's Law（上限 = 平均 RPS × 平均时延），Vegas 延迟算法/Gradient2 根据时延自动加减并发上限，超时与拒绝信号触发激进退避 [Netflix/concurrency-limits](https://github.com/Netflix/concurrency-limits)。
- 落地：agent 以「同时在跑的查询数」为硬上限（参考 CPU 核数），并用搜索时延反馈自动调节；OS 层可再加进程级 CPU/内存限制兜底（Linux cgroups、Windows Job Objects 等通用运维手段）。

## 4. 轻量身份与鉴权（内网够用不过度）

### 4.1 各方案对比

- **简单 token / 用户名密码**：NATS 原生支持 user/password、token、bearer JWT 等认证方式 [NATS Security Deep Dive](https://docs.nats.io/learn/security/)；Mosquitto 支持用户名密码（password_file）、无鉴权、TLS 证书、PSK [mosquitto.conf(5)](https://mosquitto.org/man/mosquitto-conf-5.html)。配 TLS 后即可防内网嗅探。
- **mTLS**：双向 X.509，握手阶段即互验身份；但「手工维护 mTLS 是全职工作」，证书签发/轮换/吊销要靠服务网格或 SPIFFE/SPIRE 自动化才可行，需判断何时「mTLS 是过度设计」 [engineering handbook: mTLS and Service-to-Service Authentication](https://github.com/handbook-academy/engineering-handbook/blob/main/content/hld/part-7-security-at-scale/03-mtls-service-auth.md)。NATS 也支持 mTLS 与 NKeys 签名身份 [NATS Security Deep Dive](https://docs.nats.io/learn/security/)。10–100 人内网场景：mTLS 作为进阶选项，不宜作起步门槛。
- **用户级黑名单**：NATS 按 account/user 做发布订阅授权（每用户只允许指定 subject 的 pub/sub，其余一律拒绝）；account 天然是租户隔离边界 [NATS Security Deep Dive](https://docs.nats.io/learn/security/)。应用层再维护 per-user 黑名单（忽略/拒绝某用户的查询与私信），无需中心强一致。

### 4.2 内网推荐组合

TLS（防嗅探）+ 每用户独立 token/密码（NATS 内建，可随 WebSocket cookie 下发 [NATS websocket 配置](https://beta-docs.nats.io/reference/2.14/config/websocket/)）+ 按 subject 授权（只许收发自己与团队主题）+ 应用层黑名单。证书体系成熟后再升级 mTLS；跨部门隔离用 NATS accounts [NATS Security Deep Dive](https://docs.nats.io/learn/security/)。

## 5. 推荐方案矩阵

### 5.1 三方案对比

| 维度 | A. 轻量 relay（NATS 或 FastAPI/WebSocket） | B. 组网直连（ZeroTier / Tailscale+Headscale） | C. 全 P2P（SSB / YaCy / libp2p） |
|---|---|---|---|
| 中心角色 | 只路由消息（NATS 核心无持久化 [Core NATS Deep Dive](https://docs.nats.io/learn/core-nats.md)） | 只做控制面（公钥/IP/ACL [Headscale README](https://github.com/juanfont/headscale)） | 无中心（或仅 pubs 中继） |
| 知识是否复制 | 否，索引与内容全留本地 | 否 | 是，feed/索引 gossip 复制（与需求冲突） |
| 部署复杂度 | 低（单二进制 / 单个 WS 服务） | 中（控制器 + DERP + 客户端注册） | 高（DHT/NAT 穿透/gossip 治理） |
| Windows/Linux | 好（官方多平台二进制） | 好（客户端支持 Windows/Linux [Headscale clients](https://headscale.net/stable/about/clients)） | 一般 |
| 内网适配 | 好（单 TCP 端口即可，WS 可走 443 [NATS websocket 配置](https://beta-docs.nats.io/reference/2.14/config/websocket/)） | 中（需放行 UDP/WireGuard；DERP 兜底 [Headscale DERP 文档](https://headscale.net/development/ref/derp/)） | 差（NAT/DHT 问题，见 [AutoNAT 审计](https://github.com/probe-lab/autonat-perf-audit/blob/main/docs/final-report.md)） |
| 请求-回复 | 原生（含超时/no-responders [NATS Resilience](https://beta-docs.nats.io/learn/resilient-clients/request-reply-resilience)） | 需自建 | 需自建 |
| 资源保护 | 内建慢消费者切断 [NATS Slow Consumers](https://docs.nats.io/running-a-nats-service/nats_admin/slow_consumers) + 自建限流 | 无（自建） | 无（自建） |
| 鉴权 | 内建丰富（token/NKeys/JWT/mTLS [NATS Security](https://docs.nats.io/learn/security/)） | 控制面 ACL（Tailscale ACL 模型） | 各项目自定 |
| 授权成本 | 开源免费（自建） | ZeroTier 商用付费/自建许可证需核对；Tailscale 免费 6 用户、商用 8 美元/人/月 [ZeroTier Pricing](https://www.zerotier.com/pricing) [Tailscale Pricing](https://tailscale.com/pricing) | 开源免费 |
| 适合规模 | 10–100 人，**首选** | 10–100 人（大文件直传加分） | 数百人以上公开网络 |
| 主要风险 | 中心宕机即失联（可集群/双机热备） | 自建 DERP 是单点 [Headscale DERP 文档](https://headscale.net/development/ref/derp/)；ZeroTier 自建无官方 UI [ZeroTier Self Hosting](https://docs.zerotier.com/selfhosting/) | 复制膨胀、不可删除、运维重 |

### 5.2 推荐结论

- **主选 A（NATS）**：中心跑 nats-server（核心模式，不开 JetStream，可选 WebSocket 端口），节点 agent 以 leaf node/普通客户端接入。问题广播 = 发布到团队 subject（wildcard + queue group）；结果回传 = 请求-回复（带超时与 no-responders）；节点资源保护 = token bucket + 单查询时间预算 + 自适应并发上限；鉴权 = TLS + 每用户 token + 按 subject 授权。
- **可选叠加 B**：当需要节点间直传大结果/大文档时，用 Tailscale + Headscale（自建控制面与 DERP）建立直连通道，中心仅经 NATS 交换协商信息——「消息走 NATS、数据面直连」的混合架构。
- **不推荐 C**：理由见 1.1–1.3。

### 5.3 参考架构示意

中心：nats-server（TLS、token 鉴权、内存在线状态表、per-user 授权、可选 WebSocket 端口）——只路由，不落盘任何知识内容。
节点 agent：本地索引（嵌入式全文索引引擎，备选 Tantivy/Bleve/Xapian/Lucene，另文调研，入口参考 [对比文](https://www.pistack.xyz/posts/2026-10-05-embedded-full-text-search-libraries-tantivy-bleve-lucene-xapian/)）+ 查询入口 + 资源保护层（限流/预算/并发）+ 断线重连与心跳。
消息流：提问 → NATS 广播 → 各在线 agent 本地检索 → 结果经请求-回复回传 → 提问端聚合去重（带 per-query deadline）。

## 6. 来源清单

- [YaCy GitHub README](https://github.com/yacy/yacy_search_server)
- [hysenlabs: YaCy 项目评估](https://hysenlabs.com/projects/yacy-yacy-search-server)
- [yacy.net](https://yacy.net/)
- [arxiv 2402.03239: Bluesky and the AT Protocol（§4.1 描述 SSB）](https://ar5iv.labs.arxiv.org/html/2402.03239)
- [Manyverse: Launch of the PZP protocol](https://www.manyver.se/blog/2024-07-03)
- [ssbc/scuttlebutt-protocol-guide](https://github.com/ssbc/scuttlebutt-protocol-guide)
- [libp2p gossipsub spec](https://github.com/libp2p/specs/blob/master/pubsub/gossipsub/README.md)
- [IPFS: What is IPFS](https://docs.ipfs.tech/concepts/what-is-ipfs/)
- [probe-lab: AutoNAT v2 跨实现审计](https://github.com/probe-lab/autonat-perf-audit/blob/main/docs/final-report.md)
- [juanfont/headscale README](https://github.com/juanfont/headscale)
- [Headscale DERP 文档](https://headscale.net/development/ref/derp/)
- [Headscale: Client and OS support](https://headscale.net/stable/about/clients)
- [ZeroTier Self Hosting](https://docs.zerotier.com/selfhosting/)
- [ZeroTier Pricing](https://www.zerotier.com/pricing)
- [ZeroTier Download](https://www.zerotier.com/download/)
- [Jonnyan404/zerotier-planet](https://github.com/Jonnyan404/zerotier-planet)
- [Tailscale Pricing](https://tailscale.com/pricing)
- [Core NATS Deep Dive（at-most-once）](https://docs.nats.io/learn/core-nats.md)
- [NATS leafnodes 配置](https://beta-docs.nats.io/reference/2.12/config/leafnodes/)
- [NATS websocket 配置](https://beta-docs.nats.io/reference/2.14/config/websocket/)
- [NATS Request-Reply Resilience](https://beta-docs.nats.io/learn/resilient-clients/request-reply-resilience)
- [NATS Slow Consumers](https://docs.nats.io/running-a-nats-service/nats_admin/slow_consumers)
- [NATS Security Deep Dive](https://docs.nats.io/learn/security/)
- [NATS MQTT 支持](https://docs.nats.io/running-a-nats-service/configuration/mqtt)
- [NATS GitHub Releases](https://github.com/nats-io/nats-server/releases)
- [mosquitto(8)](https://mosquitto.org/man/mosquitto-8.html)
- [mosquitto.conf(5)](https://mosquitto.org/man/mosquitto-conf-5.html)
- [HiveMQ: MQTT 5 Request-Response](https://www.hivemq.com/blog/mqtt5-essentials-part9-request-response-pattern/)
- [HiveMQ: MQTT 与 NATS 对比（厂商视角）](https://www.hivemq.com/blog/building-unified-namespace-why-mqtt-outperforms-nats/)
- [MDN: WebRTC Signaling](https://developer.mozilla.org/en-US/docs/Web/API/WebRTC_API/Signaling_and_video_calling)
- [阿里云开发者社区：WebSocket 心跳保活、断线重连与消息补偿实践](https://developer.aliyun.com/article/1763056)
- [Wikipedia: Token bucket](https://en.wikipedia.org/wiki/Token_bucket)
- [Redis Glossary: Rate Limiting](https://redis.io/glossary/rate-limiting/)
- [Netflix/concurrency-limits](https://github.com/Netflix/concurrency-limits)
- [engineering handbook: mTLS and Service-to-Service Authentication](https://github.com/handbook-academy/engineering-handbook/blob/main/content/hld/part-7-security-at-scale/03-mtls-service-auth.md)
- [嵌入式全文索引库对比（另文调研入口）](https://www.pistack.xyz/posts/2026-10-05-embedded-full-text-search-libraries-tantivy-bleve-lucene-xapian/)
