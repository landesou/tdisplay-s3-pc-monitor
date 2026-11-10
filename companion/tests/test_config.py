"""配置：首次生成 Token、持久化、损坏重建、非法值回退/夹逼。"""

import json

from pcmond.config import DEFAULT_PORT, Config


def test_first_load_generates_token_and_saves(tmp_path):
    path = tmp_path / "config.json"
    config = Config.load(path)
    assert path.exists()
    assert len(config.token) >= 20
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["token"] == config.token
    assert raw["port"] == DEFAULT_PORT
    assert raw["usb_enabled"] is True
    assert raw["interval_s"] == 1.0


def test_token_persists_across_loads(tmp_path):
    path = tmp_path / "config.json"
    first = Config.load(path)
    second = Config.load(path)
    assert first.token == second.token


def test_corrupt_config_is_rebuilt(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{not valid json", encoding="utf-8")
    config = Config.load(path)
    assert config.token
    assert json.loads(path.read_text(encoding="utf-8"))["token"] == config.token


def test_invalid_values_fall_back_to_defaults(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps({"token": "t", "port": 99999, "interval_s": 0.01, "usb_hz": 5.0, "usb_enabled": "no"}),
        encoding="utf-8",
    )
    config = Config.load(path)
    assert config.token == "t"
    assert config.port == DEFAULT_PORT  # 超范围回退默认
    assert config.interval_s == 0.2  # 夹到下限
    assert config.usb_hz == 2.0  # 契约 1–2 Hz 上限
    assert config.usb_enabled is False  # 字符串 "no" 正确解析


def test_wifi_credentials_are_loaded(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"token": "t", "wifi_ssid": "MyWiFi", "wifi_pass": "pw"}), encoding="utf-8")
    config = Config.load(path)
    assert config.wifi_ssid == "MyWiFi"
    assert config.wifi_pass == "pw"


def test_tray_enabled_defaults_true_and_roundtrips(tmp_path):
    path = tmp_path / "config.json"
    assert Config.load(path).tray_enabled is True
    Config(token="t", tray_enabled=False).save(path)
    assert Config.load(path).tray_enabled is False


def test_save_roundtrip(tmp_path):
    path = tmp_path / "sub" / "config.json"
    config = Config(token="tok", port=9999, serial="COM7", wifi_ssid="S")
    config.save(path)
    again = Config.load(path)
    assert (again.token, again.port, again.serial, again.wifi_ssid) == ("tok", 9999, "COM7", "S")
