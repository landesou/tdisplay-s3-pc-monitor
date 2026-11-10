"""GPU 采集：调用 nvidia-smi 读 CSV；任何失败都降级为 None，不抛异常。

只用一条命令（约 60 ms）拿到契约 §2 需要的全部 GPU 字段：
    nvidia-smi --query-gpu=name,utilization.gpu,temperature.gpu,
                 memory.used,memory.total,power.draw
               --format=csv,noheader,nounits
"""

from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass
from typing import Optional

LOGGER = logging.getLogger(__name__)

NVIDIA_SMI = "nvidia-smi"
QUERY_FIELDS = "name,utilization.gpu,temperature.gpu,memory.used,memory.total,power.draw"
TIMEOUT_S = 3.0
# Windows 下不留黑框；非 Windows 必须传 0
_CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
# 本机有 ToDesk/GameViewer/ExtensoDesk 等虚拟显示适配器，名称里带这些词的一律不当真卡
_VIRTUAL_HINTS = ("VIRTUAL", "TODESK", "GAMEVIEWER", "EXTENSODESK", "BASIC DISPLAY", "REMOTE")
_NA_VALUES = {"", "N/A", "[N/A]", "NA", "NONE", "UNKNOWN", "NOT SUPPORTED"}


@dataclass(frozen=True)
class GpuSample:
    """单块 GPU 的一次采样；读不到的字段为 None（契约允许 null）。"""

    name: str
    util: Optional[int] = None
    temp: Optional[int] = None
    vram_used: Optional[int] = None
    vram_total: Optional[int] = None
    power: Optional[float] = None


def _to_float(raw: str) -> Optional[float]:
    text = raw.strip()
    if text.upper() in _NA_VALUES:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _to_int(raw: str) -> Optional[int]:
    value = _to_float(raw)
    return None if value is None else int(round(value))


def is_nvidia(name: str) -> bool:
    """过滤 N/A 行和虚拟显示适配器，只保留真实 NVIDIA 卡。"""
    upper = name.strip().upper()
    if upper in _NA_VALUES:
        return False
    return not any(hint in upper for hint in _VIRTUAL_HINTS)


def parse_nvidia_smi(output: str) -> Optional[GpuSample]:
    """解析 csv,noheader,nounits 输出；多卡时取第一块真卡，解析不了返回 None。"""
    for line in output.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 6:  # 行不完整就跳过，不猜
            continue
        name = parts[0]
        if not is_nvidia(name):
            continue
        return GpuSample(
            name=name,
            util=_to_int(parts[1]),
            temp=_to_int(parts[2]),
            vram_used=_to_int(parts[3]),
            vram_total=_to_int(parts[4]),
            power=_to_float(parts[5]),
        )
    return None


def query_gpu(timeout: float = TIMEOUT_S) -> Optional[GpuSample]:
    """跑一次 nvidia-smi。命令不存在/超时/非零退出/输出乱码 → None（GPU 字段发 null）。"""
    command = [NVIDIA_SMI, f"--query-gpu={QUERY_FIELDS}", "--format=csv,noheader,nounits"]
    try:
        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=_CREATE_NO_WINDOW,
            check=False,
        )
    except FileNotFoundError:
        LOGGER.debug("未找到 nvidia-smi，GPU 指标降级为 null")
        return None
    except subprocess.TimeoutExpired:
        LOGGER.warning("nvidia-smi 超时（>%.1fs），本帧 GPU 指标为 null", timeout)
        return None
    except OSError as exc:
        LOGGER.warning("nvidia-smi 启动失败：%s", exc)
        return None

    if proc.returncode != 0:
        LOGGER.warning("nvidia-smi 退出码 %s：%s", proc.returncode, (proc.stderr or "").strip()[:200])
        return None

    sample = parse_nvidia_smi(proc.stdout or "")
    if sample is None:
        LOGGER.debug("nvidia-smi 输出无法解析：%r", (proc.stdout or "")[:200])
    return sample
