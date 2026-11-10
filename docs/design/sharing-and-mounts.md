# 共享状态、挂载暂停与组内成员

> v0.9.2。手动共享控制；敏感内容**自动识别未实现**（方案见 sensitive-content-detection.md）。

## 1. 文档共享状态

| 状态 | 本地搜索 | 远端搜索 | 显示 | 预览/定位/打开 |
|---|---|---|---|---|
| shared | ✅ | ✅ | 正常 | ✅ |
| private（仅本机） | ✅ | ❌ | 置灰 + 下拉显示"仅本机" | ✅ |
| hidden（隐藏） | ❌ | ❌ | 置灰 + "隐藏" | ✅ |

- 只改共享标记，不删除文档、不移动文件、不重建索引；
- 所有检索入口共用策略：
  - 本地：`share_state != 'hidden' AND mount.enabled=1`；
  - 远端：`share_state = 'shared' AND mount.enabled=1`；
  - 覆盖 FTS、AND/短语、trigram、LIKE、向量、AI 上下文、远端回答；
- 远端节点永不返回 private/hidden 文档。

API：`PATCH /api/docs/{doc_id}/sharing`，body `{"state":"shared|private|hidden"}`。

## 2. 挂载暂停

- `mounts.enabled`，默认 1；
- 暂停：停止 watcher，不参与本地/远端搜索；文档、索引、向量保留；文档列表置灰；预览/打开可用；
- 启用：重建 connector、启动 watcher、增量扫描；无需全量重建向量；
- API：`PATCH /api/mounts/{mount_id}`，body `{"enabled": false}`。

## 3. 组内成员

- relay `groups_view` 增加 `members`（在线用户昵称）；
- 随手问提问组面板默认只显示"N 人"按钮；点开才展开成员 chips，面板内部滚动，不长期占空间；
- 当前只显示**在线成员**；离线成员需 relay 持久化最近组快照，暂未做。

## 4. 提醒

随手记页面的提示条说明"共享前请确认"，提供手动设置为仅本机/隐藏；不自动替用户判定。

## 5. UI 交互

- 文档行下拉：共享 / 仅本机 / 隐藏；
- 挂载卡：暂停 / 启用；
- AI 回答框右下角可横向拖动加宽（CSS resize，最大 96%）；
- 结果/对话/回答均在框内滚动。
