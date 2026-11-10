"""采集器：cpu_percent 预热、差分速率、单指标失败降级、主机名截断。"""

from types import SimpleNamespace

import psutil
import pytest

from pcmond import collectors
from pcmond.collectors import Collector
from pcmond.rates import MIB

GIB = 1024 * MIB


def test_warmup_then_collect_returns_real_cpu_percent(monkeypatch):
    """psutil.cpu_percent 首次调用返回 0.0；预热后再取必须是第二次的真实值。"""
    avg_values = iter([0.0, 42.0])
    percpu_values = iter([[0.0, 0.0, 0.0, 0.0], [12.0, 34.0, 5.0, 0.0]])
    calls = []

    def fake_cpu_percent(interval=None, percpu=False):
        calls.append((interval, percpu))
        return next(percpu_values) if percpu else next(avg_values)

    monkeypatch.setattr(psutil, "cpu_percent", fake_cpu_percent)
    monkeypatch.setattr(psutil, "cpu_freq", lambda: SimpleNamespace(current=2399.6))
    monkeypatch.setattr(
        psutil,
        "virtual_memory",
        lambda: SimpleNamespace(total=16 * GIB, available=6 * GIB, percent=61.24),
    )

    collector = Collector(gpu_query=lambda: None)
    collector.warmup(now=100.0)
    # 预热必须恰好各调一次（全核 + 每线程），且都是非阻塞 interval=None
    assert calls == [(None, False), (None, True)]

    sample = collector.collect(now=101.0)
    assert sample.cpu_avg == pytest.approx(42.0)
    assert sample.cpu_cores == [12, 34, 5, 0]
    assert sample.cpu_mhz == 2400
    assert sample.mem_pct == pytest.approx(61.2)
    assert sample.mem_used_mib == 10 * 1024
    assert sample.mem_total_mib == 16 * 1024


def test_differential_rates_use_elapsed_time(monkeypatch):
    net_values = iter(
        [
            SimpleNamespace(bytes_recv=1000, bytes_sent=500),
            SimpleNamespace(bytes_recv=1000 + 2 * MIB, bytes_sent=500 + MIB // 2),
        ]
    )
    disk_values = iter(
        [
            SimpleNamespace(read_bytes=100, write_bytes=200),
            SimpleNamespace(read_bytes=100 + 4 * MIB, write_bytes=200 + MIB),
        ]
    )
    monkeypatch.setattr(psutil, "cpu_percent", lambda interval=None, percpu=False: 0.0 if not percpu else [0.0])
    monkeypatch.setattr(psutil, "cpu_freq", lambda: None)
    monkeypatch.setattr(psutil, "virtual_memory", lambda: SimpleNamespace(total=1, available=1, percent=0.0))
    monkeypatch.setattr(psutil, "net_io_counters", lambda: next(net_values))
    monkeypatch.setattr(psutil, "disk_io_counters", lambda: next(disk_values))
    monkeypatch.setattr(collectors.time, "time", lambda: 1_000_000.0)
    monkeypatch.setattr(psutil, "boot_time", lambda: 1_000_000.0 - 1234.7)

    collector = Collector(gpu_query=lambda: None)
    collector.warmup(now=100.0)
    sample = collector.collect(now=101.0)  # 正好 1 秒

    assert sample.net_down == pytest.approx(2.0)
    assert sample.net_up == pytest.approx(0.5)
    assert sample.disk_read == pytest.approx(4.0)
    assert sample.disk_write == pytest.approx(1.0)
    assert sample.uptime_s == 1234


def test_memory_failure_degrades_to_none_without_breaking_frame(monkeypatch):
    def boom():
        raise RuntimeError("虚拟内存读取失败")

    monkeypatch.setattr(psutil, "cpu_percent", lambda interval=None, percpu=False: 1.0 if not percpu else [1.0])
    monkeypatch.setattr(psutil, "cpu_freq", lambda: SimpleNamespace(current=1000.0))
    monkeypatch.setattr(psutil, "virtual_memory", boom)
    monkeypatch.setattr(psutil, "net_io_counters", lambda: None)
    monkeypatch.setattr(psutil, "disk_io_counters", lambda: None)
    monkeypatch.setattr(psutil, "boot_time", lambda: 0.0)

    collector = Collector(gpu_query=lambda: None)
    collector.warmup(now=1.0)
    sample = collector.collect(now=2.0)
    assert sample.mem_pct is None
    assert sample.mem_used_mib is None
    assert sample.mem_total_mib is None
    assert sample.cpu_mhz == 1000
    assert sample.net_down == 0.0


def test_gpu_query_exception_degrades_to_none():
    def boom():
        raise RuntimeError("nvidia-smi 爆炸")

    collector = Collector(gpu_query=boom)
    sample = collector.collect()
    assert sample.gpu is None


def test_hostname_is_truncated_to_15(monkeypatch):
    monkeypatch.setattr(collectors.socket, "gethostname", lambda: "A" * 40)
    collector = Collector(gpu_query=lambda: None)
    assert len(collector.hostname) == 15
