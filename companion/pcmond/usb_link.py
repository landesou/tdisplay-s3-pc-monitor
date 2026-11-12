"""USB CDC 链路：自动发现 ESP32-S3 串口，按 1–2 Hz 推一行 JSON；断开指数退避重连。

设备 → PC 的控制消息（如 {"t":"hello","need_cfg":1}）同线程读取并处理；
契约 §4：收到 need_cfg 时回一帧 {"t":"cfg",...}，让设备写 NVS。
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Dict, List, Optional

import serial
from serial.tools import list_ports

from .config import Config
from .netinfo import lan_ip
from .sampler import Sampler
from .snapshot import to_json

LOGGER = logging.getLogger(__name__)

ESP32_VID = 0x303A  # Espressif
ESP32_PID_USB_SERIAL_JTAG = 0x1001  # S3 内置 USB-Serial/JTAG（本机板子实测 303A:1001）
SERIAL_HINTS = ("USB 串行设备", "USB JTAG", "USB-SERIAL", "ESP32", "ESPRESSIF")
SERIAL_BAUDRATE = 115200
DEVICE_READ_TIMEOUT_S = 0.2
WRITE_TIMEOUT_S = 0.5  # 设备端没读走数据时写会阻塞；别等太久，保持推帧节奏
WRITE_WARN_INTERVAL_S = 5.0  # 写超时告警限频，避免日志刷屏
RECONNECT_INITIAL_S = 0.5
RECONNECT_MAX_S = 10.0
IDLE_SLEEP_S = 0.05


def _port_text(port: Any) -> str:
    return " ".join(str(getattr(port, name, "") or "") for name in ("description", "manufacturer", "hwid", "product"))


def find_port(override: Optional[str] = None, ports: Optional[List[Any]] = None) -> Optional[str]:
    """找 ESP32-S3 的 USB CDC 串口。

    匹配优先级：`--serial` 指定 > VID 0x303A + PID 0x1001（S3 内置 USB-Serial/JTAG）
    > 任意 VID 0x303A（如 TinyUSB CDC 303A:0002）> 描述含 USB 串行设备/USB JTAG 等。
    本机以后可能同时插别的 Espressif 设备，所以先认 PID，避免误抓。
    """
    if override:
        return override
    candidates = list(list_ports.comports() if ports is None else ports)
    for port in candidates:
        if getattr(port, "vid", None) == ESP32_VID and getattr(port, "pid", None) == ESP32_PID_USB_SERIAL_JTAG:
            return port.device
    for port in candidates:
        if getattr(port, "vid", None) == ESP32_VID:
            return port.device
    for port in candidates:
        text = _port_text(port).upper()
        if any(hint.upper() in text for hint in SERIAL_HINTS):
            return port.device
    return None


class UsbLink:
    """USB 推送线程；打不开串口只进日志/退避，绝不阻塞采集与主进程。"""

    def __init__(self, sampler: Sampler, config: Config) -> None:
        self._sampler = sampler
        self._config = config
        hz = max(1.0, min(2.0, float(config.usb_hz or 1.0)))
        self._interval_s = 1.0 / hz
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.port_name: Optional[str] = None
        self._last_write_warn = 0.0

    # ---- 生命周期 ----

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="pcmond-usb", daemon=True)
        self._thread.start()

    @property
    def connected(self) -> bool:
        """串口当前是否真的打开着（托盘绿色状态依据）。"""
        return self.port_name is not None

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3.0)

    # ---- 线程主体 ----

    def _run(self) -> None:
        backoff = RECONNECT_INITIAL_S
        while not self._stop.is_set():
            port_name = find_port(self._config.serial)
            if port_name is None:
                LOGGER.debug("未发现 ESP32-S3 串口（VID_303A），%.1fs 后重试", backoff)
                self._stop.wait(backoff)
                backoff = min(backoff * 2, RECONNECT_MAX_S)
                continue
            try:
                with serial.Serial(
                    port_name,
                    SERIAL_BAUDRATE,
                    timeout=DEVICE_READ_TIMEOUT_S,
                    write_timeout=WRITE_TIMEOUT_S,
                ) as port:
                    self.port_name = port_name
                    try:
                        LOGGER.info("USB 已连接：%s（推帧 %.1f Hz）", port_name, 1.0 / self._interval_s)
                        backoff = RECONNECT_INITIAL_S
                        self._pump(port, port_name)
                    finally:
                        # 先清状态再退避等待，别让托盘在断线期间还显示「绿」
                        self.port_name = None
            except (serial.SerialException, OSError) as exc:
                if self._stop.is_set():
                    break
                LOGGER.warning("USB 串口 %s 异常（%s），%.1fs 后重连", port_name, exc, backoff)
                self._stop.wait(backoff)
                backoff = min(backoff * 2, RECONNECT_MAX_S)
            except Exception:  # noqa: BLE001 - 线程绝不能因为意外异常退出
                if self._stop.is_set():
                    break
                LOGGER.exception("USB 线程未知异常，%.1fs 后重试", backoff)
                self._stop.wait(backoff)
                backoff = min(backoff * 2, RECONNECT_MAX_S)

    def _pump(self, port: "serial.Serial", port_name: str) -> None:
        next_send = 0.0
        while not self._stop.is_set():
            now = time.monotonic()
            if now >= next_send:
                snapshot = self._sampler.latest()  # 始终发最新值，不阻塞采集
                if snapshot is not None:
                    line = (to_json(snapshot) + "\n").encode("utf-8")
                    try:
                        port.write(line)
                        port.flush()
                        LOGGER.debug("USB → %s：seq=%s，%d 字节", port_name, snapshot.get("seq"), len(line))
                    except serial.SerialTimeoutException:
                        # 设备端没在读 CDC 数据时写会超时；这不是拔线，保持连接、下一帧重试
                        if now - self._last_write_warn >= WRITE_WARN_INTERVAL_S:
                            self._last_write_warn = now
                            LOGGER.warning("USB 写入超时（设备端可能没在读 CDC 数据），保持连接并重试")
                next_send = now + self._interval_s

            waiting = getattr(port, "in_waiting", 0)
            if waiting:
                raw = port.readline()
                if raw:
                    self._handle_device_line(port, raw)
            else:
                self._stop.wait(IDLE_SLEEP_S)

    # ---- 设备消息 ----

    def _handle_device_line(self, port: "serial.Serial", raw: bytes) -> None:
        text = raw.decode("utf-8", errors="replace").strip()
        if not text:
            return
        try:
            message = json.loads(text)
        except ValueError:
            LOGGER.debug("设备串口输出（非 JSON）：%s", text[:200])
            return
        if not isinstance(message, dict):
            return
        if message.get("t") == "hello":
            LOGGER.info("设备 hello：need_cfg=%s", message.get("need_cfg"))
            if message.get("need_cfg"):
                payload = self._build_cfg()
                if payload is not None:
                    port.write((json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8"))
                    port.flush()
                    LOGGER.info("已通过 USB 下发设备配置（设备将写 NVS）")
            return
        LOGGER.debug("设备消息：%s", text[:200])

    def _build_cfg(self) -> Optional[Dict[str, Any]]:
        """契约 §4：字段沿用 NVS key；没有 wifi 凭据就返回 None（可用串口命令配置）。"""
        ssid = (self._config.wifi_ssid or "").strip()
        password = self._config.wifi_pass or ""
        if not ssid:
            LOGGER.warning("设备请求配置，但 config.json 没有 wifi_ssid/wifi_pass，跳过下发")
            return None
        payload: Dict[str, Any] = {
            "t": "cfg",
            "wifi_ssid": ssid,
            "wifi_pass": password,
            "api_token": self._config.token,
        }
        if self._config.api_url:
            payload["api_url"] = self._config.api_url  # GUI/配置文件里手改过就用它
        else:
            address = lan_ip()
            if address:
                payload["api_url"] = f"http://{address}:{self._config.port}/api/stats"
            else:
                LOGGER.warning("未探测到局域网地址，cfg 帧不含 api_url")
        return payload
