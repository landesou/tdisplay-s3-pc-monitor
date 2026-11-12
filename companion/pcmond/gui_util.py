"""GUI 用纯函数（不碰 Tk，便于单测）。

只负责格式化/校验/文案；任何 Tk 控件操作都在 `gui.py` 的主线程里。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .netinfo import lan_ip

LOGGER = logging.getLogger(__name__)

LINK_GREEN = "#1e8449"
LINK_AMBER = "#b9770e"
LINK_RED = "#c0392b"
COLOR_MUTED = "#7f8c8d"
AUTO_SERIAL = "自动发现（推荐）"


def mask_token(token: str, visible: int = 6) -> str:
    """Token 默认只显前 6 位；短 token 原样返回（不会更长）。"""
    text = token or ""
    if len(text) <= visible:
        return text
    return text[:visible] + "…"


def format_tokens(value: Any) -> str:
    try:
        count = int(value)
    except (TypeError, ValueError):
        return "--"
    if count < 0:
        return "--"
    if count >= 1_000_000_000:
        return f"{count / 1_000_000_000:.2f}B"
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}M"
    if count >= 1_000:
        return f"{count / 1_000:.0f}K"
    return str(count)


def format_cost(value: Any) -> str:
    try:
        cost = float(value)
    except (TypeError, ValueError):
        return "≈ --"
    return f"≈ {cost:.2f} 元"


def format_until(minutes: Any) -> str:
    try:
        total = max(0, int(minutes))
    except (TypeError, ValueError):
        return "--"
    if total >= 120:
        return f"{total // 60}h{total % 60:02d}m"
    return f"{total} min"


def rate_badge(rate: Optional[Dict[str, Any]]) -> Tuple[str, str]:
    """峰谷徽标文案与颜色：高峰红 ▲、空闲绿 ▼；数据缺失时灰色 --。"""
    if not isinstance(rate, dict):
        return "峰谷 --（等 DSH 数据）", COLOR_MUTED
    peak = bool(rate.get("peak"))
    try:
        mult = int(rate.get("mult", 2 if peak else 1))
    except (TypeError, ValueError):
        mult = 2 if peak else 1
    countdown = format_until(rate.get("until_min"))
    if peak:
        return f"▲ PEAK ×{mult} · {countdown} 后转 OFF", LINK_RED
    return f"▼ OFF ×{mult} · {countdown} 后转 PEAK", LINK_GREEN


def describe_link(usb_connected: bool, usb_port: Optional[str], http_running: bool) -> Tuple[str, str]:
    """链路状态文案 + 颜色（绿=USB / 黄=仅 HTTP / 红=离线）。"""
    if usb_connected:
        return f"● USB 在线" + (f"（{usb_port}）" if usb_port else ""), LINK_GREEN
    if http_running:
        return "● 仅 HTTP 在线（未发现串口）", LINK_AMBER
    return "● 离线（USB/HTTP 都不可用）", LINK_RED


def describe_seq(snapshot: Optional[Dict[str, Any]], age_s: Optional[float]) -> str:
    if not snapshot:
        return "seq --（等待第一帧）"
    seq = snapshot.get("seq", "--")
    if age_s is None:
        return f"seq {seq}"
    return f"seq {seq} · {age_s:.1f}s 前"


def describe_device(snapshot: Optional[Dict[str, Any]], age_s: Optional[float]) -> str:
    """设备在线情况 + 协议版本（数据超过 3 s 没更新就标 STALE）。"""
    if not snapshot:
        return "等待设备首帧…"
    version = snapshot.get("v", "?")
    epoch = snapshot.get("t")
    try:
        when = datetime.fromtimestamp(float(epoch)).strftime("%H:%M:%S")
    except (TypeError, ValueError):
        when = "--"
    if age_s is not None and age_s > 3.0:
        return f"STALE（{age_s:.0f}s 无新帧）· 协议 v{version}"
    return f"在线 · 最近 {when} · 协议 v{version}"


def describe_today(dsh: Optional[Dict[str, Any]]) -> str:
    today = (dsh or {}).get("today") or {}
    if not today:
        return "tokens -- · ≈ -- · -- 次"
    cache_pct = "--"
    week = (dsh or {}).get("week") or {}
    if week:
        cache_pct = f"{week.get('cache_pct', '--')}%"
    return (
        f"tokens {format_tokens(today.get('tok'))} · {format_cost(today.get('cost'))}"
        f" · {today.get('req', '--')} 次 · 缓存命中 {cache_pct}"
    )


def describe_week(dsh: Optional[Dict[str, Any]]) -> str:
    week = (dsh or {}).get("week") or {}
    if not week:
        return "tokens -- · ≈ -- · -- 次"
    return (
        f"tokens {format_tokens(week.get('tok'))} · {format_cost(week.get('cost'))}"
        f" · {week.get('req', '--')} 次 · 缓存命中 {week.get('cache_pct', '--')}%"
    )


def describe_proc(dsh: Optional[Dict[str, Any]], configured_port: int = 0) -> str:
    """DSH 进程行；端口显示 `自动→8679` / `未探测到`（配置非 0 时显示指定端口状态）。"""
    proc = (dsh or {}).get("proc") or {}
    if not proc:
        return "-- 进程 · RSS -- · CPU --% · 端口 --"
    try:
        detected = int(proc.get("port") or 0)
    except (TypeError, ValueError):
        detected = 0
    if configured_port:
        port_text = f"端口 {detected}" if detected else f"端口 {configured_port} 未监听"
    else:
        port_text = f"端口 自动→{detected}" if detected else "端口 自动·未探测到"
    return (
        f"{proc.get('count', '--')} 进程 · RSS {proc.get('rss_mb', '--')} MiB"
        f" · CPU {proc.get('cpu_pct', '--')}% · {port_text}"
    )


def api_url(port: int) -> str:
    """设备端 /api/stats URL（自动用本机局域网 IP）。"""
    return f"http://{lan_ip() or '127.0.0.1'}:{int(port)}/api/stats"


def validate_settings(port_text: str, interval_text: str, dsh_port_text: str) -> Optional[str]:
    """校验设置区输入；返回错误文案，合法时返回 None。"""
    try:
        port = int(str(port_text).strip())
    except (TypeError, ValueError):
        return "HTTP 端口必须是整数"
    if not 1 <= port <= 65535:
        return "HTTP 端口必须在 1–65535"
    try:
        interval = float(str(interval_text).strip())
    except (TypeError, ValueError):
        return "采集间隔必须是数字"
    if not 0.5 <= interval <= 5.0:
        return "采集间隔必须在 0.5–5 秒"
    try:
        dsh_port = parse_dsh_port(dsh_port_text)
    except (TypeError, ValueError):
        return "DSH 端口必须是整数或「自动」"
    if dsh_port != 0 and not 1 <= dsh_port <= 65535:
        return "DSH 端口必须在 1–65535（0/自动 = 自动探测）"
    return None


def parse_dsh_port(text: str) -> int:
    """GUI「DSH 端口」输入 → config.dsh_port：留空/自动/auto/0 都表示自动探测。"""
    value = str(text if text is not None else "").strip().lower()
    if value in ("", "自动", "auto", "0"):
        return 0
    return int(value)


def serial_choices(ports: Optional[Iterable[Any]] = None) -> List[str]:
    """串口下拉框选项：自动发现 + 枚举到的 COM 口。"""
    choices = [AUTO_SERIAL]
    try:
        if ports is None:
            from serial.tools import list_ports

            ports = list_ports.comports()
        for port in list(ports):
            device = getattr(port, "device", "") or ""
            description = getattr(port, "description", "") or ""
            if not device:
                continue
            choices.append(f"{device}  {description}".strip())
    except Exception:  # noqa: BLE001 - 枚举串口失败不应影响 GUI
        LOGGER.debug("枚举串口失败", exc_info=True)
    return choices


def parse_serial_choice(text: Optional[str]) -> Optional[str]:
    """把下拉框文案转回 config.serial（自动发现 → None）。"""
    if not text or text.startswith("自动发现"):
        return None
    device = str(text).split()[0].strip()
    return device or None


def serial_display(config_serial: Optional[str], choices: List[str]) -> str:
    """当前串口配置对应的下拉框显示文案。"""
    if not config_serial:
        return AUTO_SERIAL
    for choice in choices:
        if choice.split()[0] == config_serial:
            return choice
    return config_serial
