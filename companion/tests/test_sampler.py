"""采集线程：queue(maxsize=2) 丢旧帧、不阻塞、异常不杀线程。"""

import time

from pcmond.collectors import RawSample
from pcmond.sampler import Sampler


class StubCollector:
    """不碰 psutil 的假采集器，让测试可控。"""

    def __init__(self, fail_first: bool = False) -> None:
        self.warmed = False
        self.calls = 0
        self.fail_first = fail_first

    def warmup(self, now=None) -> None:
        self.warmed = True

    def collect(self, now=None) -> RawSample:
        self.calls += 1
        if self.fail_first and self.calls == 1:
            raise RuntimeError("模拟采集失败")
        return RawSample(
            hostname="TEST",
            uptime_s=1,
            cpu_avg=1.0,
            cpu_mhz=100,
            cpu_cores=[1],
            mem_pct=1.0,
            mem_used_mib=1,
            mem_total_mib=2,
        )


def wait_until(predicate, timeout: float = 3.0, interval: float = 0.01) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def test_latest_is_none_before_first_frame():
    sampler = Sampler(interval_s=1.0, collector=StubCollector())
    assert sampler.latest() is None
    assert sampler.age_s() is None


def test_queue_keeps_only_two_newest_frames():
    sampler = Sampler(interval_s=1.0, collector=StubCollector())
    for _ in range(3):
        sampler.poll_once()
    assert sampler.queue.qsize() == 2  # maxsize=2，满后丢最旧
    remaining = []
    while not sampler.queue.empty():
        remaining.append(sampler.queue.get_nowait()["seq"])
    assert remaining == [2, 3]
    assert sampler.latest()["seq"] == 3


def test_thread_produces_frames_and_stops():
    stub = StubCollector()
    sampler = Sampler(interval_s=0.05, collector=stub)
    sampler.start()
    try:
        assert wait_until(lambda: sampler.latest() is not None)
        assert stub.warmed
        assert sampler.latest()["seq"] >= 1
        assert sampler.age_s() is not None
    finally:
        sampler.stop()
    assert sampler._thread is not None and not sampler._thread.is_alive()


def test_collection_error_does_not_kill_thread():
    stub = StubCollector(fail_first=True)
    sampler = Sampler(interval_s=0.03, collector=stub)
    sampler.start()  # 首帧故意失败，start 不能抛
    try:
        assert wait_until(lambda: stub.calls >= 3)
        assert wait_until(lambda: sampler.latest() is not None)
    finally:
        sampler.stop()
    assert sampler._thread is not None and not sampler._thread.is_alive()


def test_interval_has_lower_bound():
    sampler = Sampler(interval_s=0.001, collector=StubCollector())
    assert sampler.interval_s == 0.2
