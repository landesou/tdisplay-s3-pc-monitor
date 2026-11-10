"""拼 StatsSnapshot：字段/单位严格按 docs/design/architecture.md §2。

数值口径：百分比 1 位小数；速率 MiB/s 2 位小数；MiB 取整。
PC 端**不发** src 字段（由设备填写 usb/wifi）。
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, Optional

from .collectors import RawSample
from .gpu import GpuSample

PROTOCOL_VERSION = 1
HOST_MAX_CHARS = 15  # 契约：host ≤ 15 字符


def _round1(value: Optional[float]) -> Optional[float]:
    return None if value is None else round(float(value), 1)


def _round2(value: Optional[float]) -> Optional[float]:
    return None if value is None else round(float(value), 2)


def _gpu_payload(sample: Optional[GpuSample]) -> Optional[Dict[str, Any]]:
    """无 nvidia-smi 时整块 gpu 发 null（设备显示 --），不伪造 0。"""
    if sample is None:
        return None
    return {
        "name": sample.name,
        "util": sample.util,
        "temp": sample.temp,
        "vram_used": sample.vram_used,
        "vram_total": sample.vram_total,
        "power": _round1(sample.power),
        "mhz": None,  # 契约示例里的字段；nvidia-smi 不查，固定 null
    }


def build_snapshot(sample: RawSample, seq: int, now: Optional[float] = None) -> Dict[str, Any]:
    """把一次采集结果组装成契约帧（dict）。seq 由调用方单调递增。"""
    return {
        "v": PROTOCOL_VERSION,
        "seq": int(seq),
        "t": int(time.time() if now is None else now),
        "host": (sample.hostname or "")[:HOST_MAX_CHARS],
        "up": int(sample.uptime_s),
        "cpu": {
            "avg": _round1(sample.cpu_avg),
            "mhz": sample.cpu_mhz,
            "temp": None,  # v1 笔记本读不到 CPU 温度（见 journal 未验证项）
            "cores": [int(core) for core in sample.cpu_cores],
        },
        "mem": {
            "pct": _round1(sample.mem_pct),
            "used": sample.mem_used_mib,
            "total": sample.mem_total_mib,
        },
        "gpu": _gpu_payload(sample.gpu),
        "net": {"down": _round2(sample.net_down), "up": _round2(sample.net_up)},
        "disk": {"read": _round2(sample.disk_read), "write": _round2(sample.disk_write)},
    }


def to_json(snapshot: Dict[str, Any]) -> str:
    """紧凑 JSON（一行、无分隔空格、UTF-8 无 BOM）；USB 与 HTTP 共用同一字节序列。"""
    return json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))
