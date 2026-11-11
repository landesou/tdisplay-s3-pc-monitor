"""DSH 会话日志解析与费用估算（纯函数，便于单测）。

日志格式（DSH 内部实现，可能变化）：zstd 压缩的 JSONL，每行一条记录；
只有 `type == "assistant/message"` 的记录带 token 用量，字段：
- 顶层 `time`（毫秒 epoch）
- `data.usage = {inputTokens, outputTokens, cacheReadTokens, cacheWriteTokens, totalTokens}`
- `data.message.source.model`（实测 `deepseek-flash`）

费用口径（契约 §2.5）：
- 逐条按该条北京时间的峰谷价 + 该条模型单价累加（估算，不是账单）；
- 高峰 = 北京时间周一至周五 9:00–12:00、14:00–18:00（**简化：不查法定节假日表**）；
- 空闲价 = 高峰价的一半；`cacheWriteTokens` 按缓存未命中价计；
- 模型不在价格表里时回退到 `deepseek-flash` 价。
"""

from __future__ import annotations

import io
import json
import logging
import math
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import zstandard

from .config import DEFAULT_DSH_PRICING

LOGGER = logging.getLogger(__name__)

WEEK_DAYS = 7
BEIJING_TZ = timezone(timedelta(hours=8))
BEIJING_PEAK_RANGES = ((9 * 60, 12 * 60), (14 * 60, 18 * 60))  # 左闭右开
BUCKET_KEYS = ("tok", "in", "out", "cache", "req")
PRICE_KEYS = ("cache_hit", "cache_miss", "output")


def is_peak(epoch_seconds: float) -> bool:
    """该时刻是否高峰（北京时间周一至五 9:00–12:00、14:00–18:00；不含节假日判断）。"""
    moment = datetime.fromtimestamp(epoch_seconds, tz=BEIJING_TZ)
    if moment.weekday() >= 5:
        return False
    minutes = moment.hour * 60 + moment.minute
    return any(low <= minutes < high for low, high in BEIJING_PEAK_RANGES)


def local_day(epoch_seconds: float) -> str:
    """按本机本地时区取日期键（YYYY-MM-DD）。"""
    return datetime.fromtimestamp(epoch_seconds).strftime("%Y-%m-%d")


def next_rate_boundary(now_epoch: float) -> datetime:
    """下一次峰↔谷切换时刻（北京时间），与 is_peak() 是同一份规则的补集边界。

    - 工作日高峰 [9:00,12:00) / [14:00,18:00) 内 → 本段结束（12:00 / 18:00）；
    - 工作日空闲 → 9:00 / 14:00 / 下一个工作日 9:00（周五 18:00 后 → 下周一 9:00）；
    - 周六/周日整段空闲 → 下周一 9:00；
    - 简化：不查中国法定节假日表（与 is_peak() 口径一致）。
    """
    moment = datetime.fromtimestamp(now_epoch, tz=BEIJING_TZ)
    weekday = moment.weekday()  # 0 = 周一
    minutes = moment.hour * 60 + moment.minute

    def boundary_at(hour: int, day_offset: int = 0) -> datetime:
        target = moment + timedelta(days=day_offset)
        return target.replace(hour=hour, minute=0, second=0, microsecond=0)

    if weekday >= 5:  # 周六、周日全天空闲 → 下周一 9:00
        return boundary_at(9, 7 - weekday)
    if 9 * 60 <= minutes < 12 * 60:  # 上午高峰 → 12:00 转空闲
        return boundary_at(12)
    if 14 * 60 <= minutes < 18 * 60:  # 下午高峰 → 18:00 转空闲
        return boundary_at(18)
    if minutes < 9 * 60:  # 早晨空闲 → 当天 9:00
        return boundary_at(9)
    if 12 * 60 <= minutes < 14 * 60:  # 午休空闲 → 14:00
        return boundary_at(14)
    # 工作日 18:00 之后空闲：周一~周四 → 次日 9:00；周五 → 下周一 9:00
    return boundary_at(9, 1 if weekday < 4 else 3)


def build_rate(now_epoch: float) -> Dict[str, Any]:
    """当前峰谷徽标（契约 §2.5 rate）：peak / mult / until_min（分钟，向上取整）。"""
    peak = is_peak(now_epoch)
    boundary = next_rate_boundary(now_epoch)
    remaining = boundary.timestamp() - now_epoch
    until_min = 0 if remaining <= 0 else int(math.ceil(remaining / 60.0))
    return {"peak": peak, "mult": 2 if peak else 1, "until_min": until_min}


def as_int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def new_bucket() -> Dict[str, float]:
    return {"tok": 0, "in": 0, "out": 0, "cache": 0, "req": 0, "cost": 0.0}


def merge_bucket(target: Dict[str, float], source: Any) -> None:
    if not isinstance(source, dict):
        return
    for key in BUCKET_KEYS:
        target[key] = target.get(key, 0) + as_int(source.get(key))
    try:
        target["cost"] = target.get("cost", 0.0) + float(source.get("cost") or 0.0)
    except (TypeError, ValueError):
        pass


def normalize_pricing(pricing: Optional[Dict[str, Dict[str, float]]]) -> Dict[str, Dict[str, float]]:
    """合并用户价格覆盖；非法项忽略并保留默认（默认见 config.DEFAULT_DSH_PRICING）。"""
    normalized = {model: dict(units) for model, units in DEFAULT_DSH_PRICING.items()}
    if not isinstance(pricing, dict):
        return normalized
    for model, units in pricing.items():
        if not isinstance(model, str) or not isinstance(units, dict):
            continue
        target = normalized.setdefault(model, dict(DEFAULT_DSH_PRICING["deepseek-flash"]))
        for key in PRICE_KEYS:
            value = units.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
                target[key] = float(value)
    return normalized


def prices(pricing: Dict[str, Dict[str, float]], model: Optional[str], peak: bool) -> Tuple[float, float, float]:
    """返回 (缓存命中单价, 未命中单价, 输出单价)，元/百万 tokens；空闲价为高峰价一半。"""
    table = pricing.get(model or "") or pricing.get("deepseek-flash") or DEFAULT_DSH_PRICING["deepseek-flash"]
    factor = 1.0 if peak else 0.5
    return (
        float(table.get("cache_hit", 0.04)) * factor,
        float(table.get("cache_miss", 2.0)) * factor,
        float(table.get("output", 8.0)) * factor,
    )


def parse_session_file(path: Path, pricing: Dict[str, Dict[str, float]]) -> Dict[str, Dict[str, float]]:
    """解压一个会话日志，按本地日期聚合 usage 与估算费用；坏文件抛异常由调用方跳过。"""
    days: Dict[str, Dict[str, float]] = {}
    decompressor = zstandard.ZstdDecompressor()
    with open(path, "rb") as handle, decompressor.stream_reader(handle) as reader:
        for line in io.TextIOWrapper(reader, encoding="utf-8", errors="replace"):
            text = line.strip()
            if not text:
                continue
            if '"assistant/message"' not in text:
                # 廉价过滤：非 assistant/message 的大行（tool/result 等）不用过 json 解析
                continue
            try:
                record = json.loads(text)
            except ValueError:
                continue
            if not isinstance(record, dict) or record.get("type") != "assistant/message":
                continue
            try:
                epoch = float(record.get("time")) / 1000.0
            except (TypeError, ValueError):
                continue
            if epoch <= 0:
                continue
            data = record.get("data")
            usage = data.get("usage") if isinstance(data, dict) else None
            if not isinstance(usage, dict):
                # 容忍缺 usage 的 assistant/message（如 interrupted 消息）：
                # 契约 today.req = assistant/message 条数，计一次请求，token/cost 记 0
                usage = {}
            message = data.get("message") if isinstance(data, dict) else None
            source = message.get("source") if isinstance(message, dict) else None
            model = source.get("model") if isinstance(source, dict) else None
            model = model if isinstance(model, str) else None

            tokens_in = as_int(usage.get("inputTokens"))
            tokens_out = as_int(usage.get("outputTokens"))
            tokens_hit = as_int(usage.get("cacheReadTokens"))
            tokens_write = as_int(usage.get("cacheWriteTokens"))
            total = as_int(usage.get("totalTokens"))
            if total <= 0:
                total = tokens_in + tokens_out + tokens_hit + tokens_write
            hit_price, miss_price, out_price = prices(pricing, model, is_peak(epoch))
            cost = (
                tokens_in * miss_price
                + tokens_hit * hit_price
                + tokens_write * miss_price
                + tokens_out * out_price
            ) / 1_000_000.0

            bucket = days.setdefault(local_day(epoch), new_bucket())
            bucket["tok"] += total
            bucket["in"] += tokens_in
            bucket["out"] += tokens_out
            bucket["cache"] += tokens_hit
            bucket["req"] += 1
            bucket["cost"] += cost
    return days


def build_payload(
    buckets: Dict[str, Dict[str, float]],
    today: date,
    proc: Dict[str, Any],
    now_epoch: float,
) -> Dict[str, Any]:
    """把按天桶聚合成契约 §2.5 的 dsh 对象（字段名/类型严格按契约）。"""
    today_key = today.isoformat()
    week_start = (today - timedelta(days=WEEK_DAYS - 1)).isoformat()
    today_bucket = new_bucket()
    week_bucket = new_bucket()
    for day_key, bucket in buckets.items():
        if day_key < week_start or day_key > today_key:  # 忽略窗口外/时钟异常的未来日期
            continue
        merge_bucket(week_bucket, bucket)
        if day_key == today_key:
            merge_bucket(today_bucket, bucket)
    cached_input = week_bucket["in"] + week_bucket["cache"]
    cache_pct = int(round(100 * week_bucket["cache"] / cached_input)) if cached_input > 0 else 0
    return {
        "today": {
            "tok": int(today_bucket["tok"]),
            "in": int(today_bucket["in"]),
            "out": int(today_bucket["out"]),
            "cache": int(today_bucket["cache"]),
            "req": int(today_bucket["req"]),
            "cost": round(today_bucket["cost"], 2),
        },
        "week": {
            "tok": int(week_bucket["tok"]),
            "req": int(week_bucket["req"]),
            "cache_pct": cache_pct,
            "cost": round(week_bucket["cost"], 2),
        },
        "proc": proc,
        "rate": build_rate(now_epoch),
    }
