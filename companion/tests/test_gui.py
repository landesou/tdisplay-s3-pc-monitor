"""GUI 纯函数与自启命令（不创建 Tk 窗口）。"""

from datetime import datetime

import pytest

from pcmond import autostart, gui, gui_util
from pcmond.gui_util import (
    COLOR_MUTED,
    LINK_AMBER,
    LINK_GREEN,
    LINK_RED,
    api_url,
    describe_device,
    describe_link,
    describe_proc,
    describe_seq,
    describe_today,
    describe_week,
    format_cost,
    format_tokens,
    format_until,
    mask_token,
    parse_dsh_port,
    parse_serial_choice,
    rate_badge,
    serial_choices,
    serial_display,
    validate_settings,
)


def test_mask_token_shows_only_prefix():
    token = "GG1KgbgLiWwuFP3jzHCd-z9L"
    assert mask_token(token) == "GG1Kgb…"
    assert mask_token("short") == "short"
    assert mask_token("") == ""


def test_format_tokens_units():
    assert format_tokens(0) == "0"
    assert format_tokens(999) == "999"
    assert format_tokens(1500) == "2K"
    assert format_tokens(341381865) == "341.4M"
    assert format_tokens(1_200_000_000) == "1.20B"
    assert format_tokens(None) == "--"


def test_format_cost():
    assert format_cost(12.88) == "≈ 12.88 元"
    assert format_cost(None) == "≈ --"


def test_format_until():
    assert format_until(0) == "0 min"
    assert format_until(59) == "59 min"
    assert format_until(120) == "2h00m"
    assert format_until(742) == "12h22m"
    assert format_until(None) == "--"


def test_rate_badge_peak_and_off_peak():
    peak_text, peak_color = rate_badge({"peak": True, "mult": 2, "until_min": 42})
    assert "PEAK" in peak_text and "×2" in peak_text and "转 OFF" in peak_text and "42 min" in peak_text
    assert peak_color == LINK_RED
    off_text, off_color = rate_badge({"peak": False, "mult": 1, "until_min": 151})
    assert "OFF" in off_text and "×1" in off_text and "转 PEAK" in off_text
    assert off_color == LINK_GREEN
    text, color = rate_badge(None)
    assert "峰谷 --" in text and color == COLOR_MUTED


def test_describe_link_three_states():
    assert describe_link(True, "COM10", True)[1] == LINK_GREEN
    assert "COM10" in describe_link(True, "COM10", True)[0]
    assert describe_link(False, None, True)[1] == LINK_AMBER
    assert describe_link(False, None, False)[1] == LINK_RED


def test_describe_seq_and_device():
    snapshot = {"v": 1, "seq": 42, "t": datetime(2026, 11, 12, 20, 0).timestamp()}
    assert describe_seq(None, None).startswith("seq --")
    assert "seq 42" in describe_seq(snapshot, 0.4)
    assert "在线" in describe_device(snapshot, 0.4)
    assert "协议 v1" in describe_device(snapshot, 0.4)
    assert "STALE" in describe_device(snapshot, 5.0)
    assert "等待设备" in describe_device(None, None)


def test_describe_today_week_proc():
    dsh = {
        "today": {"tok": 341381865, "cost": 12.88, "req": 1105},
        "week": {"tok": 606784206, "cost": 33.21, "req": 2361, "cache_pct": 99},
        "proc": {"count": 10, "rss_mb": 1059, "cpu_pct": 4, "port": 8679, "port_up": True},
    }
    today = describe_today(dsh)
    assert "341.4M" in today and "12.88" in today and "1105 次" in today and "99%" in today
    week = describe_week(dsh)
    assert "606.8M" in week and "2361 次" in week and "99%" in week
    proc = describe_proc(dsh)  # 自动模式
    assert "10 进程" in proc and "1059 MiB" in proc and "CPU 4%" in proc and "端口 自动→8679" in proc
    # 手动指定端口：显示指定值/是否监听
    manual = describe_proc(dsh, configured_port=8679)
    assert "端口 8679" in manual
    unavailable = describe_proc({"proc": {"count": 0, "rss_mb": 0, "cpu_pct": 0, "port": 0}}, 9999)
    assert "端口 9999 未监听" in unavailable
    auto_none = describe_proc({"proc": {"count": 1, "rss_mb": 1, "cpu_pct": 0, "port": 0}})
    assert "自动·未探测到" in auto_none
    assert "tokens --" in describe_today(None)
    assert "-- 进程" in describe_proc(None)


def test_api_url_uses_lan_ip(monkeypatch):
    monkeypatch.setattr(gui_util, "lan_ip", lambda: "192.168.1.10")
    assert api_url(8787) == "http://192.168.1.10:8787/api/stats"


def test_validate_settings():
    assert validate_settings("8787", "1.0", "10899") is None
    assert validate_settings("8787", "1.0", "自动") is None  # 默认自动探测
    assert validate_settings("8787", "1.0", "") is None
    assert validate_settings("8787", "1.0", "0") is None
    assert "端口必须" in validate_settings("abc", "1.0", "自动")
    assert "1–65535" in validate_settings("70000", "1.0", "自动")
    assert "0.5–5" in validate_settings("8787", "8", "自动")
    assert "间隔必须" in validate_settings("8787", "abc", "自动")
    assert "整数或「自动」" in validate_settings("8787", "1.0", "x")
    assert "1–65535" in validate_settings("8787", "1.0", "70000")


def test_parse_dsh_port():
    assert parse_dsh_port("") == 0
    assert parse_dsh_port("自动") == 0
    assert parse_dsh_port("AUTO") == 0
    assert parse_dsh_port("0") == 0
    assert parse_dsh_port("8679") == 8679
    with pytest.raises(ValueError):
        parse_dsh_port("abc")


def test_serial_choices_and_parse():
    ports = [
        type("P", (), {"device": "COM10", "description": "USB 串行设备"})(),
        type("P", (), {"device": "COM3", "description": "蓝牙串行"})(),
    ]
    choices = serial_choices(ports)
    assert choices[0] == "自动发现（推荐）"
    assert choices[1].startswith("COM10")
    assert parse_serial_choice(choices[0]) is None
    assert parse_serial_choice(choices[1]) == "COM10"
    assert serial_display(None, choices) == "自动发现（推荐）"
    assert serial_display("COM10", choices).startswith("COM10")
    assert serial_display("COM99", choices) == "COM99"  # 不在列表里也照实显示


def test_autostart_command_frozen_only(monkeypatch):
    monkeypatch.setattr(autostart, "is_frozen", lambda: False)
    assert autostart.autostart_command() is None
    monkeypatch.setattr(autostart, "is_frozen", lambda: True)
    monkeypatch.setattr(autostart.sys, "executable", r"C:\apps\pcmond.exe")
    assert autostart.autostart_command() == r'"C:\apps\pcmond.exe" --gui'


def test_gui_module_imports_without_creating_window():
    """只验证模块可导入、有入口类；不实例化 Tk（测试机可能无显示器）。"""
    assert callable(gui.launch)
    assert hasattr(gui, "PcmondApp")
