"""增量索引（设计 §5、调研 02 §5.1~5.2）：watchdog 事件 → 防抖 → 单线程 worker。

链路：
    watchdog（Linux inotify / Windows ReadDirectoryChangesW）
      -> Debouncer 按路径防抖（默认 4s，设计口径 3~5s；编辑器保存风暴合并为一次）
      -> JobQueue 有界队列（同 key 合并、满则丢新，设计 §12 背压）
      -> IndexWorker 单线程串行消费：
           wait_if_busy 空闲调度（CPU 高负载暂停推进）
           -> indexer 处理（mtime/size/hash 校验在 indexer.index_meta）
           -> yield_to_user 逐任务让出
     进程低优先级：worker 启动时 lower_process_priority()（红线③）。

事件语义：
    文件 created/modified/closed/deleted -> 防抖后 index_file（其内部处理不存在即删除）
    文件 moved -> 旧位置删除 + 新位置索引
    目录 created/moved(dest) -> 防抖后 scan_dir 重扫
    目录 deleted/moved(src)  -> 防抖后 remove_dir 摘除该目录下全部文档
不跟随符号链接（与扫描一致）。
"""
from __future__ import annotations

import logging
import queue
import threading
from pathlib import Path
from typing import Callable, Optional

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from connectors.base import Connector
from indexer.db import IndexDB
from indexer.indexer import Indexer
from indexer.resources import CPU_THRESHOLD_DEFAULT, lower_process_priority, wait_if_busy, yield_to_user

log = logging.getLogger("chiselwall.watcher")

DEBOUNCE_DEFAULT: float = 4.0  # 设计：3~5s 防抖
QUEUE_MAXSIZE: int = 10000

# 忽略的噪声事件类型（'opened' 只读打开、'closed_no_write' 未写关闭）
_IGNORED_FILE_EVENTS = frozenset({"opened", "closed_no_write"})


class Debouncer:
    """按 key 防抖：delay 秒内同一 key 的后续触发重置计时器，到期只触发一次。"""

    def __init__(self, delay: float = DEBOUNCE_DEFAULT) -> None:
        self.delay = delay
        self._timers: dict[str, threading.Timer] = {}
        self._lock = threading.Lock()

    def schedule(self, key: str, callback: Callable[[], None]) -> None:
        def fire() -> None:
            with self._lock:
                t = self._timers.pop(key, None)
            if t is not None:
                callback()

        with self._lock:
            old = self._timers.pop(key, None)
            if old is not None:
                old.cancel()
            timer = threading.Timer(self.delay, fire)
            timer.daemon = True
            self._timers[key] = timer
            timer.start()

    def pending(self) -> int:
        with self._lock:
            return len(self._timers)

    def shutdown(self) -> None:
        with self._lock:
            for t in self._timers.values():
                t.cancel()
            self._timers.clear()


class JobQueue:
    """有界队列：同 key 合并（只保留最新一个待处理项），满时丢新（设计 §12 背压）。"""

    def __init__(self, maxsize: int = QUEUE_MAXSIZE) -> None:
        self._q: "queue.Queue[tuple[str, Callable[[], None]]]" = queue.Queue(maxsize)
        self._keys: set[str] = set()
        self._lock = threading.Lock()

    def put(self, key: str, job: Callable[[], None]) -> bool:
        with self._lock:
            if key in self._keys:
                return True  # 已有待处理项：事件合并
            try:
                self._q.put_nowait((key, job))
            except queue.Full:
                return False  # 背压：丢新（启动全量扫描兜底）
            self._keys.add(key)
            return True

    def get(self, timeout: float = 1.0) -> Optional[Callable[[], None]]:
        try:
            key, job = self._q.get(timeout=timeout)
        except queue.Empty:
            return None
        with self._lock:
            self._keys.discard(key)
        return job

    def qsize(self) -> int:
        return self._q.qsize()


class _Handler(FileSystemEventHandler):
    """watchdog 事件回调（回调本身不做 IO/DB 操作，只入防抖队列）。"""

    def __init__(
        self,
        on_file: Callable[[FileSystemEvent], None],
        on_dir: Callable[[FileSystemEvent], None],
    ) -> None:
        super().__init__()
        self._on_file = on_file
        self._on_dir = on_dir

    def on_any_event(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            self._on_dir(event)
        else:
            self._on_file(event)


class WatchService:
    """管理全部挂载的 watchdog 观察器、防抖与事件路由。"""

    def __init__(
        self,
        indexer: Indexer,
        db: IndexDB,
        *,
        debounce: float = DEBOUNCE_DEFAULT,
        cpu_threshold: float = CPU_THRESHOLD_DEFAULT,
        worker_yield: float = 0.05,
    ) -> None:
        self.indexer = indexer
        self.db = db
        self.debouncer = Debouncer(debounce)
        self.jobs = JobQueue()
        self.cpu_threshold = cpu_threshold
        self.worker_yield = worker_yield
        # mount_id -> (connector, observer, handler, watch_root, recursive_child_paths)
        self._mounts: dict[int, tuple[Connector, Observer, _Handler, Path, set[str]]] = {}
        self._lock = threading.Lock()
        self._worker: Optional[IndexWorker] = None

    # ---- 挂载管理 ----
    def add_mount(self, mount_id: int, connector: Connector) -> None:
        """起观察器：只监视连接器声明的根，顶层排除目录不注册 watch。

        关键点：watchdog 的 recursive=True 会遍历整棵子树并注册 inotify watch，
        handler 的 ignore 只在事件到达后过滤，挡不住 watch 注册。思源工作区的
        repo/history/conf/temp 等会因此产生数千 watch；这里改为：
          - 根目录只做非递归 watch（捕获新建/改名顶层目录）；
          - 只对允许的顶层子目录做 recursive watch；
          - 思源的 watch_root 进一步收窄到 data/。
        """
        root = connector.root
        watch_root = connector.watch_root()
        if watch_root is None or not watch_root.is_dir():
            log.warning("watch mount %s skipped: watch root unavailable (%s)", mount_id, root)
            return
        handler = _Handler(
            on_file=lambda ev: self._file_event(mount_id, connector, ev),
            on_dir=lambda ev: self._dir_event(mount_id, connector, ev),
        )
        observer = Observer()
        observer.daemon = True
        # 根目录本身非递归：新增/改名顶层目录能被捕获，但不会把顶层排除目录卷进来
        observer.schedule(handler, str(watch_root), recursive=False)
        children: set[str] = set()
        if not root.is_file():
            try:
                for child in watch_root.iterdir():
                    if child.is_dir() and not child.is_symlink() and connector.watch_allowed(child):
                        observer.schedule(handler, str(child), recursive=True)
                        children.add(str(child.resolve()))
            except OSError:
                log.exception("scan watch children failed: %s", watch_root)
        observer.start()
        with self._lock:
            self._mounts[mount_id] = (connector, observer, handler, watch_root, children)
        log.info("watch mount %s -> %s (root non-recursive, %d child watches)", mount_id, watch_root, len(children))

    def remove_mount(self, mount_id: int) -> None:
        with self._lock:
            item = self._mounts.pop(mount_id, None)
        if item is not None:
            _conn, observer, _handler, _root, _children = item
            observer.stop()
            observer.join(timeout=5)

    def shutdown(self) -> None:
        self.debouncer.shutdown()
        for mount_id in list(self._mounts):
            self.remove_mount(mount_id)

    def start_worker(self) -> "IndexWorker":
        """启动单线程索引 worker（幂等：重复调用返回既有 worker）。"""
        if self._worker is None or not self._worker.is_alive():
            self._worker = IndexWorker(self)
            self._worker.start()
        return self._worker

    def stop_worker(self) -> None:
        if self._worker is not None:
            self._worker.stop()

    def enqueue_scan(self, mount_id: int, connector: Connector) -> bool:
        """入队一次全量扫描（启动校验 / 手动重建 / 规则变更后重扫）。"""
        key = "scan:" + str(mount_id)
        return self.jobs.put(key, lambda: self._do_scan(mount_id, connector))

    # ---- 事件路由 ----
    @staticmethod
    def _rel(connector: Connector, src: str) -> Optional[str]:
        """事件源路径 -> 挂载内相对路径（'/' 分隔）；越界/无关事件返回 None。"""
        root = connector.root
        base = root if root.is_dir() else root.parent
        try:
            rel = Path(src).resolve().relative_to(base.resolve())
        except (ValueError, OSError):
            return None
        rel = connector.norm_rel(str(rel))
        if not root.is_dir() and rel != root.name:
            return None  # 单文件挂载：只关心该文件
        return rel

    def _file_event(self, mount_id: int, connector: Connector, event: FileSystemEvent) -> None:
        kind = event.event_type
        if kind in _IGNORED_FILE_EVENTS:
            return
        if kind == "moved":
            rel_src = self._rel(connector, event.src_path)
            rel_dst = self._rel(connector, event.dest_path)
            if rel_src is not None:
                self._schedule(mount_id, connector, "f", rel_src)
            if rel_dst is not None and rel_dst != rel_src:
                self._schedule(mount_id, connector, "f", rel_dst)
            return
        rel = self._rel(connector, event.src_path)
        if rel is not None:
            self._schedule(mount_id, connector, "f", rel)

    def _dir_event(self, mount_id: int, connector: Connector, event: FileSystemEvent) -> None:
        kind = event.event_type
        if kind == "moved":
            rel_src = self._rel(connector, event.src_path)
            rel_dst = self._rel(connector, event.dest_path)
            self._forget_child_watch(mount_id, event.src_path)
            self._ensure_child_watch(mount_id, connector, event.dest_path)
            if rel_src is not None:
                self._schedule(mount_id, connector, "d", rel_src)
            if rel_dst is not None and rel_dst != rel_src:
                self._schedule(mount_id, connector, "d", rel_dst)
            return
        if kind == "created":
            self._ensure_child_watch(mount_id, connector, event.src_path)
        elif kind == "deleted":
            self._forget_child_watch(mount_id, event.src_path)
        rel = self._rel(connector, event.src_path)
        if rel is not None:
            self._schedule(mount_id, connector, "d", rel)

    def _ensure_child_watch(self, mount_id: int, connector: Connector, src: str) -> None:
        """顶层目录新建/移入时补一个 recursive watch；已存在的树不再重复注册。"""
        if connector.root.is_file():
            return
        try:
            path = Path(src).resolve()
            watch_root = connector.watch_root()
            if watch_root is None or path.parent != watch_root.resolve():
                return
            if not path.is_dir() or not connector.watch_allowed(path):
                return
        except (OSError, ValueError):
            return
        with self._lock:
            item = self._mounts.get(mount_id)
            if item is None:
                return
            _conn, observer, handler, _root, children = item
            key = str(path)
            if key in children:
                return
            try:
                observer.schedule(handler, key, recursive=True)
            except (OSError, RuntimeError):
                log.exception("add watch failed: %s", key)
                return
            children.add(key)
        log.info("watch new child %s -> %s", mount_id, key)

    def _forget_child_watch(self, mount_id: int, src: str) -> None:
        try:
            key = str(Path(src).resolve())
        except OSError:
            return
        with self._lock:
            item = self._mounts.get(mount_id)
            if item is not None:
                item[4].discard(key)

    def _schedule(self, mount_id: int, connector: Connector, prefix: str, rel: str) -> None:
        key = prefix + ":" + str(mount_id) + ":" + rel
        if prefix == "d":
            # 目录事件：防抖后按当前磁盘状态决定重扫或摘除（index_file 自带存在性判断）
            self.debouncer.schedule(key, lambda: self._enqueue(key, lambda: self._do_path(mount_id, connector, rel, is_dir=True)))
        else:
            self.debouncer.schedule(key, lambda: self._enqueue(key, lambda: self._do_path(mount_id, connector, rel, is_dir=False)))

    def _enqueue(self, key: str, job: Callable[[], None]) -> bool:
        return self.jobs.put(key, job)

    # ---- 实际处理（worker 线程执行） ----
    def _do_path(self, mount_id: int, connector: Connector, rel: str, is_dir: bool) -> None:
        try:
            result = self.indexer.index_file(mount_id, connector, rel)
            if result not in ("unchanged", "ignored"):
                log.info("%s %s -> %s", "dir" if is_dir else "file", rel, result)
        except Exception:
            log.exception("index event failed: %s", rel)

    def _do_scan(self, mount_id: int, connector: Connector) -> None:
        try:
            stats = self.indexer.scan_mount(mount_id, connector)
            log.info("scan mount %s done: %s", mount_id, stats)
        except Exception:
            log.exception("scan mount failed: %s", mount_id)


class IndexWorker(threading.Thread):
    """单线程索引 worker：低优先级 + 空闲调度 + 逐任务让出（设计 §12 索引让路）。"""

    def __init__(self, service: WatchService) -> None:
        super().__init__(name="chisel-index-worker", daemon=True)
        self.service = service
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        lower_process_priority()  # 红线③：进程低优先级
        log.info("index worker started (low priority)")
        while not self._stop.is_set():
            job = self.service.jobs.get(timeout=0.5)
            if job is None:
                continue
            wait_if_busy(self.service.cpu_threshold)  # 空闲调度：高负载暂停推进
            try:
                job()
            except Exception:
                log.exception("index job failed")
            yield_to_user(self.service.worker_yield)  # 逐任务让出
        log.info("index worker stopped")
