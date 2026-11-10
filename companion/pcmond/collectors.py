"""采集器：CPU / 内存 / 磁盘 / 网络 / GPU / 开机时长 / 主机名。

每条指标单独 try/except：某项读不到只降级为 None/0，绝不让整帧失败。
有状态的部分（累计计数器差分）需要先 warmup() 建立基准。
"""

from __future__ import annotations

import logging
import socket
import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional

import psutil

from . import gpu as gpu_collector
from .rates import CounterRate

LOGGER = logging.getLogger(__name__)

MIB = 1024 * 1024
HOST_MAX_CHARS = 15


@dataclass
class RawSample:
    """一帧的原始采集值（未做 JSON 取整，取整在 snapshot.py 统一做）。"""

    hostname: str
    uptime_s: int
    cpu_avg: Optional[float]
    cpu_mhz: Optional[int]
    cpu_cores: List[int] = field(default_factory=list)
    mem_pct: Optional[float] = None
    mem_used_mib: Optional[int] = None
    mem_total_mib: Optional[int] = None
    net_down: float = 0.0
    net_up: float = 0.0
    disk_read: float = 0.0
    disk_write: float = 0.0
    gpu: Optional[gpu_collector.GpuSample] = None


def _hostname() -> str:
    try:
        return socket.gethostname().split(".")[0][:HOST_MAX_CHARS]
    except OSError as exc:  # 极端情况下 gethostname 也会失败
        LOGGER.warning("读取主机名失败：%s", exc)
        return ""


class Collector:
    """有状态采集器；gpu_query 可注入，方便单测不真的起 nvidia-smi。"""

    def __init__(self, gpu_query: Optional[Callable[[], Optional[gpu_collector.GpuSample]]] = None) -> None:
        self._gpu_query = gpu_query or gpu_collector.query_gpu
        self._net_down = CounterRate()
        self._net_up = CounterRate()
        self._disk_read = CounterRate()
        self._disk_write = CounterRate()
        self.hostname = _hostname()
        self._warmed = False

    def warmup(self, now: Optional[float] = None) -> None:
        """psutil.cpu_percent(interval=None) **首次调用必然返回 0.0**（没有时间基准），
        所以先空跑一次建立基准，之后的 collect() 才拿得到真实占用率；
        顺便给网络/磁盘累计计数器建基准，避免首帧把开机以来的量算成尖峰。
        """
        now = time.monotonic() if now is None else float(now)
        try:
            psutil.cpu_percent(interval=None)  # 预热全核平均
            psutil.cpu_percent(interval=None, percpu=True)  # 预热每线程（同一个坑）
            net_io = psutil.net_io_counters()
            if net_io is not None:
                self._net_down.seed(net_io.bytes_recv, now)
                self._net_up.seed(net_io.bytes_sent, now)
            disk_io = psutil.disk_io_counters()
            if disk_io is not None:
                self._disk_read.seed(disk_io.read_bytes, now)
                self._disk_write.seed(disk_io.write_bytes, now)
        except Exception:  # noqa: BLE001 - 预热失败也不该让进程起不来
            LOGGER.exception("采集器预热失败，首帧 CPU 占用率可能为 0")
        self._warmed = True

    def collect(self, now: Optional[float] = None) -> RawSample:
        """采一帧。now 只给测试用（让差分时刻可控），默认取 monotonic。"""
        now = time.monotonic() if now is None else float(now)
        cpu_avg, cpu_cores = self._cpu_percent()
        mem_pct, mem_used, mem_total = self._memory()
        net_down, net_up = self._net_rates(now)
        disk_read, disk_write = self._disk_rates(now)
        return RawSample(
            hostname=self.hostname,
            uptime_s=self._uptime(),
            cpu_avg=cpu_avg,
            cpu_mhz=self._cpu_mhz(),
            cpu_cores=cpu_cores,
            mem_pct=mem_pct,
            mem_used_mib=mem_used,
            mem_total_mib=mem_total,
            net_down=net_down,
            net_up=net_up,
            disk_read=disk_read,
            disk_write=disk_write,
            gpu=self._gpu(),
        )

    # ---- 单项采集：每项失败只降级 ----

    def _cpu_percent(self) -> tuple[Optional[float], List[int]]:
        try:
            if not self._warmed:
                LOGGER.warning("Collector 未经 warmup 就采集，CPU 占用率可能是 0")
            avg = psutil.cpu_percent(interval=None)
            cores = list(psutil.cpu_percent(interval=None, percpu=True) or [])
            return float(avg), [int(round(value)) for value in cores]
        except Exception:  # noqa: BLE001
            LOGGER.exception("CPU 占用率采集失败")
            return None, []

    def _cpu_mhz(self) -> Optional[int]:
        try:
            freq = psutil.cpu_freq()
            if freq is not None and freq.current:
                return int(round(freq.current))
        except Exception:  # noqa: BLE001
            LOGGER.debug("CPU 频率读取失败", exc_info=True)
        return None

    def _memory(self) -> tuple[Optional[float], Optional[int], Optional[int]]:
        try:
            vm = psutil.virtual_memory()
            used = int(vm.total - vm.available)
            return round(float(vm.percent), 1), int(round(used / MIB)), int(round(vm.total / MIB))
        except Exception:  # noqa: BLE001
            LOGGER.exception("内存采集失败")
            return None, None, None

    def _net_rates(self, now: float) -> tuple[float, float]:
        try:
            io = psutil.net_io_counters()
            if io is None:
                return 0.0, 0.0
            return self._net_down.update(io.bytes_recv, now), self._net_up.update(io.bytes_sent, now)
        except Exception:  # noqa: BLE001
            LOGGER.exception("网络速率采集失败")
            return 0.0, 0.0

    def _disk_rates(self, now: float) -> tuple[float, float]:
        try:
            io = psutil.disk_io_counters()
            if io is None:
                return 0.0, 0.0
            return self._disk_read.update(io.read_bytes, now), self._disk_write.update(io.write_bytes, now)
        except Exception:  # noqa: BLE001
            LOGGER.exception("磁盘速率采集失败")
            return 0.0, 0.0

    def _uptime(self) -> int:
        try:
            return int(time.time() - psutil.boot_time())
        except Exception:  # noqa: BLE001
            LOGGER.debug("开机时长读取失败", exc_info=True)
            return 0

    def _gpu(self) -> Optional[gpu_collector.GpuSample]:
        try:
            return self._gpu_query()
        except Exception:  # noqa: BLE001 - nvidia-smi 已内部兜底，这里防注入实现抛错
            LOGGER.exception("GPU 采集失败")
            return None
