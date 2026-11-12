"""pcmond 运行时：采集 + USB/HTTP 链路 + DSH + 托盘 的统一编排。

`main.run_daemon`（CLI 常驻）和 `gui.PcmondApp`（桌面窗口）共用这一个类：
- `start()` 依次起 DSH 采集线程、采集线程、HTTP、USB、托盘；
- `stop()` 是**唯一**的优雅停路径（关 GUI 窗口和托盘「退出」都走它），重复调用安全；
- 托盘「退出」只 set `stop_event`，由宿主循环调用 `stop()`。

链路组件全部复用既有实现（sampler/usb_link/http_link/tray），这里只做编排，不重写采集。
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING, Any, Dict, Optional

from .config import CONFIG_PATH, LOG_PATH, Config
from .http_link import HttpLink
from .netinfo import lan_ip
from .sampler import Sampler
from .tray import TrayIcon, TrayStatus

if TYPE_CHECKING:  # 只有运行时才真正 import，pyserial 缺失时仍能给出友好降级
    from .dsh import DshSampler
    from .usb_link import UsbLink

LOGGER = logging.getLogger(__name__)


class PcmondRuntime:
    """采集与传输的后台运行时。"""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.stop_event = threading.Event()
        self.sampler: Optional[Sampler] = None
        self.dsh: Optional["DshSampler"] = None
        self.http: Optional[HttpLink] = None
        self.usb: Optional["UsbLink"] = None
        self.tray: Optional[TrayIcon] = None
        self._started = False

    # ---- 生命周期 ----

    def start(self) -> None:
        """起所有后台线程；单个组件失败只降级，不抛给调用方。"""
        if self._started:
            return
        config = self.config

        if config.dsh_enabled:
            try:
                from .dsh import DshSampler

                self.dsh = DshSampler(pricing=config.dsh_pricing, port=config.dsh_port)
                self.dsh.start()
                LOGGER.info("DSH 采集线程已启动（%.0f s/刷新，端口 %d）", self.dsh.refresh_s, config.dsh_port)
            except Exception:  # noqa: BLE001
                LOGGER.exception("DSH 采集启动失败，帧里将不带 dsh 字段")
                self.dsh = None
        else:
            LOGGER.info("DSH 采集已关闭（--no-dsh / dsh_enabled=false）")

        self.sampler = Sampler(config.interval_s, dsh_provider=self.dsh.latest if self.dsh is not None else None)
        self.sampler.start()
        LOGGER.info("采集线程已启动（%.2f s/帧）", self.sampler.interval_s)

        if config.http_enabled:
            self.http = HttpLink(self.sampler.latest, config.token, port=config.port, host=config.host)
            if not self.http.start():
                LOGGER.warning("HTTP 链路未启动（端口冲突？），采集与 USB 继续")
                self.http = None
        else:
            LOGGER.info("HTTP 链路已按参数关闭")

        if config.usb_enabled:
            try:
                from .usb_link import UsbLink

                self.usb = UsbLink(self.sampler, config)
                self.usb.start()
                LOGGER.info("USB 链路线程已启动（%s）", f"指定 {config.serial}" if config.serial else "自动发现 VID_303A")
            except ImportError as exc:
                LOGGER.error("未安装 pyserial，USB 链路不可用：%s", exc)
                self.usb = None
        else:
            LOGGER.info("USB 链路已按参数关闭")

        self.tray = self._start_tray()
        self._started = True

    def stop(self) -> None:
        """优雅停：托盘 → USB → HTTP → 采集 → DSH。重复调用安全。"""
        if not self._started:
            return
        self._started = False
        for name, component in (
            ("托盘", self.tray),
            ("USB", self.usb),
            ("HTTP", self.http),
            ("采集", self.sampler),
            ("DSH", self.dsh),
        ):
            if component is None:
                continue
            try:
                component.stop()
            except Exception:  # noqa: BLE001 - 单个组件停失败也要继续停别的
                LOGGER.exception("停止 %s 失败", name)
        self.tray = self.usb = self.http = self.sampler = self.dsh = None
        LOGGER.info("pcmond 运行时已停止")

    def _start_tray(self) -> Optional[TrayIcon]:
        if not self.config.tray_enabled:
            LOGGER.info("系统托盘已按配置/参数关闭（--no-tray）")
            return None
        try:
            tray = TrayIcon(self.tray_status, self.stop_event.set, config_dir=CONFIG_PATH.parent, log_path=LOG_PATH)
            return tray if tray.start() else None
        except Exception:  # noqa: BLE001
            LOGGER.exception("托盘启动失败，继续无托盘运行")
            return None

    # ---- 状态 ----

    @property
    def running(self) -> bool:
        return self._started

    def latest(self) -> Optional[Dict[str, Any]]:
        """最新一帧（含 sys 段与可选 dsh 段）；由采集线程写、GUI/HTTP 只读。"""
        return self.sampler.latest() if self.sampler is not None else None

    def age_s(self) -> Optional[float]:
        return self.sampler.age_s() if self.sampler is not None else None

    @property
    def usb_connected(self) -> bool:
        return bool(self.usb is not None and self.usb.connected)

    @property
    def usb_port(self) -> Optional[str]:
        if self.usb is not None and self.usb.port_name:
            return self.usb.port_name
        return self.config.serial

    @property
    def http_running(self) -> bool:
        return bool(self.http is not None and self.http.running)

    @property
    def http_port(self) -> int:
        if self.http is not None and self.http.bound_port:
            return int(self.http.bound_port)
        return int(self.config.port)

    def tray_status(self) -> TrayStatus:
        snapshot = self.latest()
        bound = self.http_port
        return TrayStatus(
            usb_connected=self.usb_connected,
            usb_port=self.usb_port,
            http_running=self.http_running,
            http_port=bound,
            http_url=f"http://{lan_ip() or '127.0.0.1'}:{bound}",
            seq=snapshot.get("seq") if snapshot else None,
            last_frame_epoch=snapshot.get("t") if snapshot else None,
            interval_s=self.config.interval_s,
            token=self.config.token,
        )
