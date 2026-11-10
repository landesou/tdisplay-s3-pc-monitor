"""配置：~/.tdisplay-pcmon/config.json；首次运行自动生成随机 Bearer Token。

命令行参数（--port/--token/...）只覆盖本次进程，不写回文件。
"""

from __future__ import annotations

import json
import logging
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional, TypeVar

LOGGER = logging.getLogger(__name__)

HOME_DIR = Path.home() / ".tdisplay-pcmon"
CONFIG_PATH = HOME_DIR / "config.json"
LOG_PATH = HOME_DIR / "pcmond.log"

DEFAULT_PORT = 8787
TOKEN_BYTES = 18  # token_urlsafe(18) → 24 个 URL 安全字符

T = TypeVar("T")


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"无法识别的布尔值：{value!r}")


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


@dataclass
class Config:
    token: str = field(default_factory=lambda: secrets.token_urlsafe(TOKEN_BYTES))
    port: int = DEFAULT_PORT
    host: str = "0.0.0.0"
    serial: Optional[str] = None  # None = 自动发现 ESP32-S3
    usb_enabled: bool = True
    http_enabled: bool = True
    interval_s: float = 1.0  # 采集周期
    usb_hz: float = 1.0  # USB 推送频率（契约 1–2 Hz）
    # 可选：设备 hello 请求配置时，通过 USB 下发写 NVS（架构 §4）
    wifi_ssid: Optional[str] = None
    wifi_pass: Optional[str] = None

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "Config":
        """读配置；文件不存在/损坏时生成默认配置并写盘（含随机 Token）。"""
        target = Path(path) if path is not None else CONFIG_PATH
        if not target.exists():
            config = cls()
            config.save(target)
            LOGGER.info("首次运行，已生成配置文件与 Token：%s", target)
            return config
        try:
            raw = json.loads(target.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("config.json 顶层必须是 JSON 对象")
        except (OSError, ValueError) as exc:
            LOGGER.warning("配置文件不可用（%s），已按默认值重建：%s", exc, target)
            config = cls()
            config.save(target)
            return config

        config = cls()
        config.token = str(_pick(raw, "token", config.token, str)).strip()
        if not config.token:
            config.token = secrets.token_urlsafe(TOKEN_BYTES)
            LOGGER.warning("配置里的 token 为空，已自动生成新 Token")
        port = _pick(raw, "port", config.port, int)
        config.port = port if 1 <= port <= 65535 else DEFAULT_PORT
        if config.port != port:
            LOGGER.warning("端口 %s 非法，改用默认 %s", port, DEFAULT_PORT)
        config.host = str(_pick(raw, "host", config.host, str)).strip() or "0.0.0.0"
        serial = _pick(raw, "serial", config.serial, str)
        if serial is not None:
            config.serial = str(serial).strip() or None
        config.usb_enabled = _pick(raw, "usb_enabled", config.usb_enabled, _as_bool)
        config.http_enabled = _pick(raw, "http_enabled", config.http_enabled, _as_bool)
        config.interval_s = _clamp(_pick(raw, "interval_s", config.interval_s, float), 0.2, 60.0)
        config.usb_hz = _clamp(_pick(raw, "usb_hz", config.usb_hz, float), 1.0, 2.0)
        for key in ("wifi_ssid", "wifi_pass"):
            value = _pick(raw, key, None, str)
            setattr(config, key, str(value) if value else None)

        if config.token != str(raw.get("token", "")):
            config.save(target)  # 自动补了 Token，立刻落盘
        return config

    def save(self, path: Optional[Path] = None) -> Path:
        """原子写（先写 .tmp 再替换），避免半截 JSON 导致下次启动丢 Token。"""
        target = Path(path) if path is not None else CONFIG_PATH
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "token": self.token,
            "port": self.port,
            "host": self.host,
            "serial": self.serial,
            "usb_enabled": self.usb_enabled,
            "http_enabled": self.http_enabled,
            "interval_s": self.interval_s,
            "usb_hz": self.usb_hz,
            "wifi_ssid": self.wifi_ssid,
            "wifi_pass": self.wifi_pass,
        }
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(target)
        return target


def _pick(raw: dict, key: str, default: T, caster: Callable[[Any], T]) -> T:
    """按类型取字段；缺省/非法时回退默认值并记 warning（不因一个字段坏掉整个配置）。"""
    if key not in raw or raw[key] is None:
        return default
    try:
        return caster(raw[key])
    except (TypeError, ValueError) as exc:
        LOGGER.warning("配置字段 %s 非法（%s），使用默认值 %r", key, exc, default)
        return default
