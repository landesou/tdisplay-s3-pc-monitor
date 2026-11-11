"""采集线程：固定周期产出快照，放进 queue.Queue(maxsize=2)。

队列满了就丢**最旧**一帧，采集绝不阻塞；USB/HTTP 各自取 latest() 的最新快照。
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Any, Callable, Dict, Optional

from .collectors import Collector
from .snapshot import build_snapshot

LOGGER = logging.getLogger(__name__)

WARMUP_SECONDS = 0.2  # 预热后等一小段，让 CPU 占用率有可测窗口
QUEUE_MAXSIZE = 2


class Sampler:
    """采集线程 + 最新快照缓存。"""

    def __init__(
        self,
        interval_s: float = 1.0,
        collector: Optional[Collector] = None,
        dsh_provider: Optional[Callable[[], Optional[Dict[str, Any]]]] = None,
    ) -> None:
        self.interval_s = max(0.2, float(interval_s))
        self.collector = collector if collector is not None else Collector()
        # DSH 统计由独立线程每 5 s 刷新，这里只读缓存，绝不每帧解压日志
        self._dsh_provider = dsh_provider
        self.queue: "queue.Queue[Dict[str, Any]]" = queue.Queue(maxsize=QUEUE_MAXSIZE)
        self._latest: Optional[Dict[str, Any]] = None
        self._latest_monotonic: Optional[float] = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.seq = 0

    # ---- 生命周期 ----

    def start(self) -> None:
        self.collector.warmup()
        self._stop.wait(WARMUP_SECONDS)
        try:
            self.poll_once()  # 立刻产出一帧，HTTP/USB 不用等第一个周期
        except Exception:  # noqa: BLE001
            LOGGER.exception("首帧采集失败，等下一周期")
        self._thread = threading.Thread(target=self._run, name="pcmond-sampler", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)

    def poll_once(self) -> Dict[str, Any]:
        """采一帧并发布（也供 --once/单测直接调用）。"""
        self.seq += 1
        dsh: Optional[Dict[str, Any]] = None
        if self._dsh_provider is not None:
            try:
                dsh = self._dsh_provider()
            except Exception:  # noqa: BLE001 - DSH 挂了也必须照常出 sys 段
                LOGGER.exception("读取 DSH 缓存失败，本帧不带 dsh 字段")
        snapshot = build_snapshot(self.collector.collect(), self.seq, dsh=dsh)
        with self._lock:
            self._latest = snapshot
            self._latest_monotonic = time.monotonic()
        self._publish(snapshot)
        return snapshot

    # ---- 读取 ----

    def latest(self) -> Optional[Dict[str, Any]]:
        """最新一帧（HTTP /api/stats 直接用，不为请求重新采集）。"""
        with self._lock:
            return self._latest

    def age_s(self) -> Optional[float]:
        """最新一帧距今秒数；还没有帧时 None。"""
        with self._lock:
            if self._latest_monotonic is None:
                return None
            return time.monotonic() - self._latest_monotonic

    # ---- 内部 ----

    def _publish(self, snapshot: Dict[str, Any]) -> None:
        try:
            self.queue.put_nowait(snapshot)
        except queue.Full:
            # 消费方慢：丢最旧一帧再放新帧，保证采集线程永不阻塞
            try:
                self.queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self.queue.put_nowait(snapshot)
            except queue.Full:
                LOGGER.debug("快照队列仍满，丢弃 seq=%s", snapshot.get("seq"))

    def _run(self) -> None:
        next_tick = time.monotonic() + self.interval_s
        while not self._stop.is_set():
            delay = next_tick - time.monotonic()
            if delay > 0:
                self._stop.wait(delay)
            if self._stop.is_set():
                break
            try:
                self.poll_once()
            except Exception:  # noqa: BLE001 - 采集失败只跳帧，线程必须活着
                LOGGER.exception("采集线程异常，跳过本帧继续")
            next_tick += self.interval_s
            if next_tick < time.monotonic():
                # 落后超过一个周期：直接对齐到现在，不补帧（补帧只会雪崩）
                next_tick = time.monotonic() + self.interval_s
