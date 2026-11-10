# 敏感内容自识别：方案选型（待实现）

> 状态：**未实现，已记录待办**。当前只做了手动共享状态（shared/private/hidden）与提醒，不替用户判断。
> 目标：在本地提示“这篇可能敏感”，由用户决定是否共享；不把敏感内容上传到中心。

## 1. 业界与论文结论

| 来源 | 做法 | 启示 |
|---|---|---|
| Microsoft Purview | 敏感度标签 + 预置/自定义可训练分类器（正负样本训练）+ DLP 策略提示、用户可覆盖 | 自动判定必须可解释、可覆盖；标签要持久 |
| Google Cloud DLP | infoType 检测器、likelihood 置信度、上下文 hotword、自定义正则、排除规则 | 规则 + 上下文 + 置信度 + allowlist，不能只靠关键词 |
| AWS Macie | 托管标识符（PII/凭证/金融）+ 自定义正则/邻近规则 + allow list；抽样巡检 + 定向任务 | 抽样 + 全量结合；规则可配置 |
| Microsoft Presidio | 正则 + NER + 校验和（Luhn 等） | 本地可跑、可解释，适合第一层 |
| GLiNER (NAACL 2024) | 通用 NER，自然语言定义实体类型 | 可识别客户名/项目代号等自定义实体 |
| Piiranha | 多语言 PII Transformer | 中文效果需实测，适合作为 PII 补充 |
| IEEE 敏感文档分类研究 | 文档级敏感分类数据集/模型 | 文档级敏感是独立问题，片段 PII 不等于整篇敏感 |

参考：
- https://learn.microsoft.com/en-us/purview/trainable-classifiers-learn-about
- https://learn.microsoft.com/en-us/purview/sensitivity-labels
- https://cloud.google.com/sensitive-data-protection/docs/concepts-infotypes
- https://docs.aws.amazon.com/macie/latest/user/data-classification.html
- https://microsoft.github.io/presidio/
- https://aclanthology.org/2024.naacl-long.300/
- https://huggingface.co/iiiorg/piiranha-v1-detect-personal-information
- https://ieeexplore.ieee.org/abstract/document/10831852

## 2. 选型（推荐，分阶段）

**本地优先、可解释、人在回路；不自动决定共享（凭证类可安全默认 private）。**

- **L0 元数据/路径**：frontmatter sensitive/private、tags、文件名/路径关键词（合同/客户/薪酬/身份证/secret）。
- **L1 高精度规则（先做）**：
  - 私钥/API key/JWT/连接串/password；
  - 中国身份证（校验位）、手机号、银行卡（Luhn）、护照；
  - 业务词：工资/绩效/股权/并购/客户名单/合同金额/病历/体检/未公开。
- **L2 本地 NER/PII（可选）**：Presidio / GLiNER / Piiranha 的 ONNX 版本；抽样，不跑全量。
- **L3 文档级分类器（可选）**：需要内部标注集；无数据不硬上。
- **L4 LLM 辅助（默认关）**：只发送短片段/命中句到已配置的内网模型，人工确认；LLM 分类不稳定，不能做最终裁决。

融合：
- 高置信凭证/私钥 → confirmed，建议默认 private（通知可改）；
- 中置信 PII/业务词 → suspect，只提示；
- 低置信 → 记录不打扰；
- 保存 score / reasons / matched_excerpt，支持“误报/确实敏感”反馈与 allowlist。

## 3. UX

- 挂载后“敏感巡检”卡片：N 篇疑似、M 篇高风险；不阻塞；
- 文档行徽标“疑似敏感”；点开看命中原因/片段；
- 审核操作：仅本机 / 本机也隐藏 / 仍然共享（确认）/ 误报忽略；
- 首次连接 relay/加组时提示“有 X 篇疑似敏感处于共享状态”；
- **绝不静默删除**；高置信凭证自动 private 必须通知并允许改回。

## 4. 评估

- 100–200 篇中文合成样本（凭证/PII/财务/人事/法务/普通技术）；
- 指标：precision/recall、误报、扫描耗时、内存；
- 优先保证高精度类别（私钥/身份证）低误报；
- 用真实误报反馈校准 allowlist/阈值。

## 5. 实施拆分

1. L0+L1 规则引擎 + 巡检卡片 + 审核抽屉；
2. 高置信凭证自动 private + 通知；
3. allowlist/反馈；
4. 可选本地 NER/文档分类器；
5. 可选内网 LLM 辅助与评测。

**当前决定：先不做自动识别；只做手动 shared/private/hidden + 页面提醒。**
