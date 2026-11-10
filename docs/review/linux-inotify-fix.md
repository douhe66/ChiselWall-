# Linux inotify 队列溢出修复（v0.9.4）

## 现象

思源工作区挂载后，进程给根目录注册 2433 个 inotify watch（另一个库 437 个），
repo/、history/、conf/、temp/ 等编辑器内部目录频繁变动，内核队列 16384 溢出；
watchdog 在 on_queue_overflow 中重新 os.walk 整棵树、重建全部 watch，
又制造/积压事件，形成正反馈，单核 CPU 87%、日志刷屏。

## 关键结论：handler ignore 挡不住 watch 注册

watchdog 6 的递归 inotify 实现在 InotifyWatchGroup._add_all_callbacks 中用
os.walk 遍历整棵子树并逐个 add_callback；PatternMatchingEventHandler 的
ignore_patterns/ignore_directories 只过滤“事件到达之后”，不会阻止 watch 注册。
所以不能只给 handler 加 ignore。

## 修复

1. Connector 新增 watch_root / watch_allowed：
   - 普通目录：root；
   - 单文件：父目录；
   - 思源：只返回 data/。
   - watch_allowed 对顶层目录应用 ExclusionEngine（.git/node_modules/.obsidian/隐藏目录等）；
     思源额外排除 data/assets、data/.siyuan 等。
2. WatchService.add_mount 改为：
   - watch_root 只做非递归 watch（捕获新建/改名顶层目录）；
   - 只对允许的顶层子目录做 recursive watch；
   - 新建/移入的顶层目录动态补 watch，删除/移出时移除记录。
3. 效果：
   - 思源不再监视 repo/history/conf/temp；只监视 data/ 下 notebook 目录；
   - 溢出时只可能重建某个 notebook 子树，不再全工作区重建；
   - 普通目录不会为 .git、node_modules、.obsidian 等注册 watch。
4. 日志新增：
   watch mount <id> -> <root> (root non-recursive, N child watches)
   用于 Linux 上确认 watch 数。

## 验证

- 单测 tests/test_watch_scope.py、tests/test_watch_schedule.py；
- 全量 301 项通过；
- Linux 现场建议：
  - sysctl fs.inotify.max_user_watches fs.inotify.max_queued_events
  - 通过 /proc/<pid>/fdinfo 统计 inotify wd 数量，确认不再包含 repo/history/conf/temp；
  - 观察日志是否还有 "Inotify queue overflowed"；正常应显著减少或消失。

## 注意

- 临时调大 fs.inotify.max_queued_events 只能延后溢出，不能替代本修复。
- 递归 watch 的某个顶层目录内部如果还有用户未排除的构建产物目录，仍会被监视；
  建议把 node_modules/build/dist 等放在顶层并加入排除规则。
- 若未来需要“任意深度过滤”，需要自定义 inotify emitter 或轮询后端，当前不做。
