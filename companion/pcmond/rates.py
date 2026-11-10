"""累计计数器 → 瞬时速率（差分换算）。

psutil 的 net_io_counters()/disk_io_counters() 给的是**累计字节数**，要显示 MB/s
必须自己存上一次的计数器和时间做差分。首帧没有历史，返回 0.0。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

MIB = 1024 * 1024


@dataclass
class CounterRate:
    """一个累计字节计数器的差分器（每个方向/每个设备各用一个实例）。"""

    last_bytes: Optional[int] = None
    last_time: Optional[float] = None

    def seed(self, total_bytes: int, now: Optional[float] = None) -> None:
        """建立基准但不产生速率（预热用，避免首帧把开机以来的累计量算成尖峰）。"""
        self.last_bytes = int(total_bytes)
        self.last_time = time.monotonic() if now is None else float(now)

    def update(self, total_bytes: int, now: Optional[float] = None) -> float:
        """喂入新累计值，返回距上次的 MiB/s；首帧/时间倒退/计数器回绕都返回 0.0。"""
        now = time.monotonic() if now is None else float(now)
        total_bytes = int(total_bytes)
        if self.last_bytes is None or self.last_time is None or now <= self.last_time:
            self.last_bytes = total_bytes
            self.last_time = now
            return 0.0
        delta = total_bytes - self.last_bytes
        elapsed = now - self.last_time
        self.last_bytes = total_bytes
        self.last_time = now
        if delta < 0:  # 32 位系统计数器回绕，宁可显示 0 也不报天文数字
            return 0.0
        return delta / MIB / elapsed
