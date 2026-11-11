"""DSH 数据源：解析/峰谷费用/缓存增量/坏文件降级/进程统计/线程韧性。

测试假设本机时区为 Asia/Shanghai（与开发机一致）；北京时间的峰谷判断用固定时间点单独测。
"""

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import zstandard

from pcmond import dsh
from pcmond.dsh import DshSampler
from pcmond.dsh_parse import build_rate, is_peak, local_day, next_rate_boundary

BEIJING = timezone(timedelta(hours=8))
NOW = datetime(2026, 11, 10, 20, 0, tzinfo=BEIJING).timestamp()  # 周二 20:00（空闲时段）


def at(year: int, month: int, day: int, hour: int, minute: int = 0, second: int = 0) -> float:
    return datetime(year, month, day, hour, minute, second, tzinfo=BEIJING).timestamp()


def ms(year: int, month: int, day: int, hour: int, minute: int = 0) -> int:
    return int(at(year, month, day, hour, minute) * 1000)


def record(
    time_ms: int,
    inp: int = 0,
    out: int = 0,
    cache: int = 0,
    cache_write: int = 0,
    total: int = None,
    model: str = "deepseek-flash",
    include_usage: bool = True,
) -> dict:
    data = {"turn": 1, "step": 1, "message": {"role": "assistant", "source": {"model": model}}}
    if include_usage:
        usage = {
            "inputTokens": inp,
            "outputTokens": out,
            "cacheReadTokens": cache,
            "cacheWriteTokens": cache_write,
            "totalTokens": total if total is not None else inp + out + cache + cache_write,
        }
        data["usage"] = usage
    return {"type": "assistant/message", "seq": 1, "time": time_ms, "data": data}


def write_session(root: Path, relative: str, records, mtime: float) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(json.dumps(item, ensure_ascii=False) for item in records) + "\n"
    with open(path, "wb") as handle:
        handle.write(zstandard.ZstdCompressor().compress(text.encode("utf-8")))
    path.touch()
    import os

    os.utime(path, (mtime, mtime))
    return path


def write_raw(root: Path, relative: str, payload: bytes, mtime: float) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    import os

    os.utime(path, (mtime, mtime))
    return path


def make_sampler(tmp_path: Path, monkeypatch, pricing=None, port: int = 10899, refresh_s: float = 5.0) -> DshSampler:
    """进程统计替换成固定值，单测只关注 token 聚合。"""
    monkeypatch.setattr(
        DshSampler,
        "_collect_proc",
        lambda self: {"count": 10, "rss_mb": 906, "cpu_pct": 12, "port_up": True},
    )
    return DshSampler(
        sessions_root=tmp_path / "sessions",
        cache_path=tmp_path / "dsh_cache.json",
        pricing=pricing,
        port=port,
        refresh_s=refresh_s,
    )


# ---------------------------------------------------------------- 峰谷 / 日期


def test_is_peak_beijing_weekday_boundaries():
    assert is_peak(at(2026, 11, 9, 9, 0))  # 周一 09:00 左闭
    assert is_peak(at(2026, 11, 9, 11, 59))
    assert not is_peak(at(2026, 11, 9, 12, 0))  # 12:00 右开
    assert not is_peak(at(2026, 11, 9, 13, 59))
    assert is_peak(at(2026, 11, 9, 14, 0))
    assert is_peak(at(2026, 11, 9, 17, 59))
    assert not is_peak(at(2026, 11, 9, 18, 0))
    assert not is_peak(at(2026, 11, 9, 23, 30))
    assert not is_peak(at(2026, 11, 7, 10, 0))  # 周六
    assert not is_peak(at(2026, 11, 8, 15, 0))  # 周日


def test_local_day_matches_local_timezone():
    assert local_day(NOW) == datetime.fromtimestamp(NOW).strftime("%Y-%m-%d")


# ---------------------------------------------------------------- rate 峰谷徽标


def rate_at(year: int, month: int, day: int, hour: int, minute: int = 0, second: int = 0) -> dict:
    return build_rate(at(year, month, day, hour, minute, second))


def test_rate_monday_daily_cycle():
    # 2026-11-09 是周一
    assert rate_at(2026, 11, 9, 8, 59) == {"peak": False, "mult": 1, "until_min": 1}
    assert rate_at(2026, 11, 9, 9, 0) == {"peak": True, "mult": 2, "until_min": 180}
    assert rate_at(2026, 11, 9, 11, 59) == {"peak": True, "mult": 2, "until_min": 1}
    assert rate_at(2026, 11, 9, 12, 0) == {"peak": False, "mult": 1, "until_min": 120}
    assert rate_at(2026, 11, 9, 13, 59) == {"peak": False, "mult": 1, "until_min": 1}
    assert rate_at(2026, 11, 9, 14, 0) == {"peak": True, "mult": 2, "until_min": 240}
    assert rate_at(2026, 11, 9, 17, 59) == {"peak": True, "mult": 2, "until_min": 1}
    assert rate_at(2026, 11, 9, 18, 0) == {"peak": False, "mult": 1, "until_min": 900}  # 次日 09:00


def test_rate_until_min_rounds_up():
    # 11:59:30 → 还差 30 s 到 12:00，向上取整 = 1 分钟
    assert rate_at(2026, 11, 9, 11, 59, 30)["until_min"] == 1
    assert rate_at(2026, 11, 9, 8, 59, 30)["until_min"] == 1
    # 09:59:01 → 还差 2h0m59s → ceil = 121
    assert rate_at(2026, 11, 9, 9, 59, 1)["until_min"] == 121


def test_rate_friday_evening_goes_to_monday():
    # 2026-11-13 周五
    assert rate_at(2026, 11, 13, 17, 59) == {"peak": True, "mult": 2, "until_min": 1}
    assert rate_at(2026, 11, 13, 18, 0) == {"peak": False, "mult": 1, "until_min": 3780}  # 下周一 09:00
    assert rate_at(2026, 11, 13, 23, 59) == {"peak": False, "mult": 1, "until_min": 3421}  # 57h1m


def test_rate_weekend_goes_to_monday():
    # 周六 12:00 → 下周一 09:00 = 45h = 2700 分钟
    assert rate_at(2026, 11, 14, 12, 0) == {"peak": False, "mult": 1, "until_min": 2700}
    # 周日 23:59 → 下周一 09:00 = 9h1m = 541 分钟
    assert rate_at(2026, 11, 15, 23, 59) == {"peak": False, "mult": 1, "until_min": 541}


def test_next_rate_boundary_datetimes():
    assert next_rate_boundary(at(2026, 11, 9, 10, 0)) == datetime(2026, 11, 9, 12, 0, tzinfo=BEIJING)
    assert next_rate_boundary(at(2026, 11, 9, 18, 0)) == datetime(2026, 11, 10, 9, 0, tzinfo=BEIJING)
    assert next_rate_boundary(at(2026, 11, 13, 18, 0)) == datetime(2026, 11, 16, 9, 0, tzinfo=BEIJING)
    assert next_rate_boundary(at(2026, 11, 14, 12, 0)) == datetime(2026, 11, 16, 9, 0, tzinfo=BEIJING)
    assert next_rate_boundary(at(2026, 11, 15, 23, 59)) == datetime(2026, 11, 16, 9, 0, tzinfo=BEIJING)


def test_rate_boundary_always_flips_peak():
    """边界必须严格在未来，且边界时刻的峰谷与当前相反（与 is_peak 同一套规则）。"""
    samples = [
        at(2026, 11, 9, hour, minute)
        for hour in (0, 8, 9, 11, 12, 13, 14, 17, 18, 23)
        for minute in (0, 30, 59)
    ] + [
        at(2026, 11, 13, 18, 0),
        at(2026, 11, 14, 0, 0),
        at(2026, 11, 15, 23, 59, 30),
    ]
    for now in samples:
        boundary = next_rate_boundary(now)
        assert boundary.timestamp() > now
        assert is_peak(boundary.timestamp()) != is_peak(now)


def test_rate_is_in_payload(tmp_path, monkeypatch):
    root = tmp_path / "sessions"
    write_session(root, "p/s/session.v4.jsonl.zstd", [record(ms(2026, 11, 10, 20, 0), inp=1000)], mtime=NOW - 10)
    payload = make_sampler(tmp_path, monkeypatch).refresh(now=NOW)  # 周二 20:00
    assert set(payload) == {"today", "week", "proc", "rate"}
    assert payload["rate"] == {"peak": False, "mult": 1, "until_min": 780}  # 到次日 09:00


# ---------------------------------------------------------------- 聚合


def test_today_and_week_buckets(tmp_path, monkeypatch):
    root = tmp_path / "sessions"
    records = [
        record(ms(2026, 11, 10, 10, 0), inp=1_000_000),  # 今天高峰：2 元
        record(ms(2026, 11, 10, 20, 0), inp=1_000_000),  # 今天空闲：1 元
        record(ms(2026, 11, 7, 20, 0), inp=500_000),  # 3 天前（周六空闲）：0.5 元
        record(ms(2026, 11, 2, 20, 0), inp=500_000),  # 8 天前：窗口外，忽略
        record(ms(2026, 11, 3, 23, 30), inp=500_000),  # 窗口起点前一天：忽略
    ]
    write_session(root, "proj--a/session-1/session.v4.jsonl.zstd", records, mtime=NOW - 10)
    sampler = make_sampler(tmp_path, monkeypatch)
    payload = sampler.refresh(now=NOW)
    assert payload is not None
    assert payload["today"] == {
        "tok": 2_000_000,
        "in": 2_000_000,
        "out": 0,
        "cache": 0,
        "req": 2,
        "cost": 3.0,
    }
    assert payload["week"] == {"tok": 2_500_000, "req": 3, "cache_pct": 0, "cost": 3.5}
    assert payload["proc"] == {"count": 10, "rss_mb": 906, "cpu_pct": 12, "port_up": True}


def test_week_cache_pct_and_today_cache_field(tmp_path, monkeypatch):
    root = tmp_path / "sessions"
    # 空闲价：in 600 * 1.0 + cache 400 * 0.02
    records = [record(ms(2026, 11, 10, 20, 0), inp=600, cache=400)]
    write_session(root, "p/session-1/session.v4.jsonl.zstd", records, mtime=NOW - 10)
    payload = make_sampler(tmp_path, monkeypatch).refresh(now=NOW)
    assert payload["today"]["cache"] == 400
    assert payload["today"]["tok"] == 1000
    assert payload["week"]["cache_pct"] == 40  # 400 / (600 + 400)
    assert payload["week"]["cost"] == round((600 * 1.0 + 400 * 0.02) / 1e6, 2)


def test_cost_rounded_to_two_decimals(tmp_path, monkeypatch):
    root = tmp_path / "sessions"
    # 高峰未命中：617000 * 2 / 1e6 = 1.234 → 1.23
    write_session(root, "p/s/session.v4.jsonl.zstd", [record(ms(2026, 11, 10, 10, 0), inp=617_000)], mtime=NOW - 10)
    payload = make_sampler(tmp_path, monkeypatch).refresh(now=NOW)
    assert payload["today"]["cost"] == 1.23


def test_pricing_override_and_unknown_model_fallback(tmp_path, monkeypatch):
    root = tmp_path / "sessions"
    write_session(
        root,
        "p/s/session.v4.jsonl.zstd",
        [
            record(ms(2026, 11, 9, 10, 0), inp=1_000_000, model="deepseek-flash"),
            record(ms(2026, 11, 9, 10, 0), inp=1_000_000, model="some-new-model"),
        ],
        mtime=NOW - 10,
    )
    default = make_sampler(tmp_path, monkeypatch).refresh(now=NOW)
    assert default["week"]["cost"] == 4.0  # 两条都按 flash 高峰价 2 元/百万

    override = make_sampler(
        tmp_path,
        monkeypatch,
        pricing={"deepseek-flash": {"cache_hit": 0.08, "cache_miss": 4.0, "output": 16.0}},
    ).refresh(now=NOW)
    assert override["week"]["cost"] == 8.0  # 未知模型也回退到（被覆盖的）flash 价


# ---------------------------------------------------------------- 缓存


def test_cache_reuses_unchanged_files(tmp_path, monkeypatch):
    root = tmp_path / "sessions"
    relative = "p/session-1/session.v4.jsonl.zstd"
    write_session(root, relative, [record(ms(2026, 11, 10, 20, 0), inp=1000)], mtime=NOW - 10)

    sampler = make_sampler(tmp_path, monkeypatch)
    first = sampler.refresh(now=NOW)
    assert sampler.parsed_files == 1
    assert first["today"]["req"] == 1

    sampler.refresh(now=NOW)
    assert sampler.parsed_files == 0  # 未变动：命中缓存

    fresh = make_sampler(tmp_path, monkeypatch)  # 新实例读同一个缓存文件
    fresh.refresh(now=NOW)
    assert fresh.parsed_files == 0

    write_session(
        root,
        relative,
        [record(ms(2026, 11, 10, 20, 0), inp=1000), record(ms(2026, 11, 10, 20, 1), inp=2000)],
        mtime=NOW - 5,
    )
    updated = fresh.refresh(now=NOW)
    assert fresh.parsed_files == 1  # 只重解析变动文件
    assert updated["today"]["req"] == 2
    assert updated["today"]["in"] == 3000


def test_corrupt_cache_is_rebuilt(tmp_path, monkeypatch):
    root = tmp_path / "sessions"
    write_session(root, "p/s/session.v4.jsonl.zstd", [record(ms(2026, 11, 10, 20, 0), inp=1000)], mtime=NOW - 10)
    cache_path = tmp_path / "dsh_cache.json"
    cache_path.write_text("{this is not json", encoding="utf-8")
    sampler = make_sampler(tmp_path, monkeypatch)
    payload = sampler.refresh(now=NOW)
    assert payload["today"]["req"] == 1
    raw = json.loads(cache_path.read_text(encoding="utf-8"))
    assert raw["version"] == dsh.CACHE_VERSION


# ---------------------------------------------------------------- 容错 / 降级


def test_corrupt_file_is_skipped_without_breaking_other_files(tmp_path, monkeypatch):
    root = tmp_path / "sessions"
    write_session(root, "good/session.v4.jsonl.zstd", [record(ms(2026, 11, 10, 20, 0), inp=1000)], mtime=NOW - 10)
    write_raw(root, "bad/session.broken.jsonl.zstd", b"definitely not zstd", mtime=NOW - 10)
    payload = make_sampler(tmp_path, monkeypatch).refresh(now=NOW)
    assert payload["today"]["req"] == 1  # 坏文件被跳过，好文件照常

    # 只剩坏文件：没有有效数据 → 不发 dsh（而不是塞一堆 0）
    (root / "good/session.v4.jsonl.zstd").unlink()
    assert make_sampler(tmp_path, monkeypatch).refresh(now=NOW) is None


def test_missing_fields_tolerated(tmp_path, monkeypatch):
    root = tmp_path / "sessions"
    lines = [
        json.dumps(record(ms(2026, 11, 10, 20, 0), inp=1000)),
        # 缺 usage（interrupted）：计一次请求，token 记 0
        json.dumps({"type": "assistant/message", "time": ms(2026, 11, 10, 20, 1), "data": {"message": {}}}),
        # 缺 time：忽略
        json.dumps({"type": "assistant/message", "data": {"usage": {"inputTokens": 999}}}),
        # 部分字段：只按有的算
        json.dumps({"type": "assistant/message", "time": ms(2026, 11, 10, 20, 2), "data": {"usage": {"inputTokens": 500}}}),
        json.dumps({"type": "tool/result", "time": ms(2026, 11, 10, 20, 3), "data": {}}),
        "{not valid json",
    ]
    write_raw(
        root,
        "p/s/session.v4.jsonl.zstd",
        zstandard.ZstdCompressor().compress(("\n".join(lines) + "\n").encode("utf-8")),
        mtime=NOW - 10,
    )
    payload = make_sampler(tmp_path, monkeypatch).refresh(now=NOW)
    assert payload["today"]["req"] == 3  # 有效时间的 assistant/message 三条
    assert payload["today"]["in"] == 1500
    assert payload["today"]["tok"] == 1500


def test_files_older_than_window_are_not_scanned(tmp_path, monkeypatch):
    root = tmp_path / "sessions"
    old_mtime = at(2026, 10, 1, 12, 0)  # 内容写的是今天，但 mtime 很早（现实中不可能，验证扫描优化）
    write_session(root, "p/s/session.v4.jsonl.zstd", [record(ms(2026, 11, 10, 20, 0), inp=1000)], mtime=old_mtime)
    sampler = make_sampler(tmp_path, monkeypatch)
    assert sampler.refresh(now=NOW) is None
    assert sampler.scanned_files == 0


# ---------------------------------------------------------------- 进程


def test_collect_proc_counts_rss_cpu_and_port(monkeypatch, tmp_path):
    class FakeProc:
        def __init__(self, name, rss, cpu):
            self.info = {"name": name}
            self._rss = rss
            self._cpu = cpu

        def memory_info(self):
            return SimpleNamespace(rss=self._rss)

        def cpu_percent(self, interval=None):
            return self._cpu

    procs = [
        FakeProc("DeepSeek Harness.exe", 500 * 1024 * 1024, 7.5),
        FakeProc("deepseek harness helper.exe", 400 * 1024 * 1024, 4.0),
        FakeProc("notepad.exe", 10 * 1024 * 1024, 80.0),  # 名字不含 DSH，忽略
    ]
    monkeypatch.setattr(dsh.psutil, "process_iter", lambda attrs=None: iter(procs))
    monkeypatch.setattr(dsh, "_port_listening", lambda *args, **kwargs: True)
    sampler = DshSampler(sessions_root=tmp_path / "s", cache_path=tmp_path / "c.json")
    stats = sampler._collect_proc()
    assert stats["count"] == 2
    assert stats["rss_mb"] == 900
    assert stats["cpu_pct"] == 12  # round(7.5 + 4.0)
    assert stats["port_up"] is True


def test_port_probe_reports_closed_on_oserror(monkeypatch):
    import socket

    def boom(*args, **kwargs):
        raise OSError("connection refused")

    monkeypatch.setattr(socket, "create_connection", boom)
    assert dsh._port_listening(10899) is False


# ---------------------------------------------------------------- 线程


def test_thread_survives_refresh_errors(tmp_path, monkeypatch):
    calls = {"count": 0}
    original = DshSampler.refresh

    def flaky(self, now=None):
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("模拟刷新失败")
        return original(self, now)

    monkeypatch.setattr(DshSampler, "refresh", flaky)
    sampler = DshSampler(sessions_root=tmp_path / "s", cache_path=tmp_path / "c.json", refresh_s=0.1)
    sampler.start()
    deadline = time.time() + 3.0
    while calls["count"] < 3 and time.time() < deadline:
        time.sleep(0.02)
    sampler.stop()
    assert calls["count"] >= 3  # 第一次异常没有杀死线程
    assert sampler._thread is not None and not sampler._thread.is_alive()
