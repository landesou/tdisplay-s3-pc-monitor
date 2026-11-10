"""托盘：状态→颜色/文案映射、token 打码、pystray 缺失降级、菜单/退出（假 pystray，不真建图标）。"""

import time
from types import SimpleNamespace

import pytest

from pcmond import tray
from pcmond.tray import TrayIcon, TrayStatus, mask_token

SECRET_TOKEN = "DUMMY-TOKEN-FOR-TESTS"


def make_status(**overrides) -> TrayStatus:
    values = dict(
        usb_connected=False,
        usb_port=None,
        http_running=True,
        http_port=8787,
        http_url="http://10.21.132.85:8787",
        seq=42,
        last_frame_epoch=1762761600.0,
        interval_s=1.0,
        token=SECRET_TOKEN,
    )
    values.update(overrides)
    return TrayStatus(**values)


# ---------- ① 状态 → 颜色 / 文案 ----------


def test_green_when_usb_connected():
    status = make_status(usb_connected=True, usb_port="COM7")
    assert status.color == "green"
    assert status.rgb == tray.COLOR_GREEN
    assert "串口在线" in status.status_text


def test_yellow_when_only_http_running():
    status = make_status(usb_connected=False, http_running=True)
    assert status.color == "yellow"
    assert status.rgb == tray.COLOR_YELLOW
    assert "仅 HTTP" in status.status_text


def test_red_when_no_link():
    status = make_status(usb_connected=False, http_running=False)
    assert status.color == "red"
    assert status.rgb == tray.COLOR_RED
    assert "不通" in status.status_text


def test_usb_has_priority_over_http():
    assert make_status(usb_connected=True, http_running=True).color == "green"


# ---------- ② token 打码 + tooltip/弹窗内容 ----------


def test_mask_token_never_leaks_value():
    assert mask_token(SECRET_TOKEN) == "***"
    assert mask_token("") == "未设置"
    assert mask_token(None) == "未设置"


def test_tooltip_has_required_fields_and_masked_token():
    status = make_status()
    text = status.tooltip()
    assert "pcmond" in text and "0.1.0" in text
    assert "仅 HTTP" in text
    assert "8787" in text and "10.21.132.85" in text
    assert "seq 42" in text
    assert "1.0s" in text
    assert "***" in text
    assert SECRET_TOKEN not in text  # 明文 token 绝不出现
    assert len(text) <= 127  # Windows 托盘 tooltip 上限


def test_tooltip_shows_dashes_without_frames():
    text = make_status(seq=None, last_frame_epoch=None).tooltip()
    assert "seq --" in text


def test_dialog_text_has_full_url_and_masked_token():
    status = make_status()
    text = status.dialog_text()
    assert "http://10.21.132.85:8787/api/stats" in text
    assert "***" in text
    assert SECRET_TOKEN not in text


# ---------- ③ pystray 缺失时降级（不抛异常） ----------


def test_start_returns_false_without_pystray(monkeypatch, caplog):
    monkeypatch.setattr(tray, "HAS_TRAY", False)
    icon = TrayIcon(lambda: make_status(), lambda: None)
    with caplog.at_level("WARNING"):
        assert icon.start() is False
    assert any("托盘不可用" in record.message for record in caplog.records)
    icon.stop()  # 从没启动过也要能安全 stop
    icon.stop()


# ---------- 假 pystray：菜单、启动线程、退出回调（不真弹图标） ----------


class FakeIcon:
    def __init__(self, name, image, title, menu=None):
        self.name = name
        self.image = image
        self.title = title
        self.menu = menu
        self.run_called = False
        self.stopped = False
        self.updates = 0

    def run(self, setup=None):
        self.run_called = True
        self.setup = setup

    def update_menu(self):
        self.updates += 1

    def stop(self):
        self.stopped = True


class FakeMenuItem:
    def __init__(self, text, action=None, enabled=True):
        self.text = text
        self.action = action
        self.enabled = enabled


class FakeMenu:
    SEPARATOR = FakeMenuItem("-", None, False)

    def __init__(self, *items):
        self.items = list(items)


def install_fake_pystray(monkeypatch):
    holder = {"icon": None}

    def icon_factory(name, image, title, menu=None):
        holder["icon"] = FakeIcon(name, image, title, menu)
        return holder["icon"]

    fake = SimpleNamespace(Icon=icon_factory, Menu=FakeMenu, MenuItem=FakeMenuItem)
    monkeypatch.setattr(tray, "pystray", fake)
    monkeypatch.setattr(tray, "HAS_TRAY", True)
    monkeypatch.setattr(tray, "_draw_icon", lambda rgb: ("fake-image", rgb))
    return holder


def wait_for(predicate, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_start_runs_pystray_in_daemon_thread(monkeypatch):
    holder = install_fake_pystray(monkeypatch)
    icon = TrayIcon(lambda: make_status(), lambda: None)
    assert icon.start() is True
    thread = icon._thread
    try:
        assert thread is not None and thread.daemon
        assert wait_for(lambda: holder["icon"] is not None and holder["icon"].run_called)
    finally:
        icon.stop()
    assert holder["icon"].stopped is True
    assert not thread.is_alive()


def test_menu_contains_required_actions(monkeypatch):
    install_fake_pystray(monkeypatch)
    icon = TrayIcon(lambda: make_status(), lambda: None)
    menu = icon._build_menu()
    texts = []
    for item in menu.items:
        text = item.text
        texts.append(text(None) if callable(text) else str(text))
    assert any("状态" in text for text in texts)
    assert "打开配置目录" in texts
    assert "打开日志" in texts
    assert "退出" in texts


def test_quit_stops_icon_and_notifies_main(monkeypatch):
    holder = install_fake_pystray(monkeypatch)
    quit_calls = []
    icon = TrayIcon(lambda: make_status(), lambda: quit_calls.append(True))
    assert icon.start() is True
    assert wait_for(lambda: holder["icon"] is not None)
    icon._quit(holder["icon"])
    assert quit_calls == [True]
    assert holder["icon"].stopped is True
    icon.stop()


def test_show_status_uses_masked_dialog(monkeypatch):
    captured = {}
    monkeypatch.setattr(tray, "_show_message", lambda text: captured.setdefault("text", text))
    icon = TrayIcon(lambda: make_status(), lambda: None)
    icon._show_status(None)
    assert "***" in captured["text"]
    assert SECRET_TOKEN not in captured["text"]


def test_open_config_dir_calls_explorer(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(tray.subprocess, "Popen", lambda argv, *args, **kwargs: calls.append(argv))
    icon = TrayIcon(lambda: make_status(), lambda: None, config_dir=tmp_path)
    icon._open_config_dir()
    assert calls and calls[0][0] == "explorer"
    assert str(tmp_path) in calls[0]


def test_open_log_calls_notepad(monkeypatch, tmp_path):
    calls = []
    log_file = tmp_path / "pcmond.log"
    log_file.write_text("x", encoding="utf-8")
    monkeypatch.setattr(tray.subprocess, "Popen", lambda argv, *args, **kwargs: calls.append(argv))
    icon = TrayIcon(lambda: make_status(), lambda: None, log_path=log_file)
    icon._open_log()
    assert calls and calls[0][0] == "notepad.exe"
    assert str(log_file) in calls[0]


@pytest.mark.skipif(not tray.HAS_TRAY, reason="未安装 Pillow/pystray，跳过真实绘图测试")
def test_draw_icon_is_colored_dot():
    image = tray._draw_icon(tray.COLOR_GREEN)
    assert image.size == (tray.ICON_SIZE, tray.ICON_SIZE)
    center = image.getpixel((tray.ICON_SIZE // 2, tray.ICON_SIZE // 2))
    assert center[:3] == tray.COLOR_GREEN
