"""系统托盘（可选依赖 pystray + Pillow）：状态圆点 + 右键菜单 + 优雅退出。

**可选依赖**：没装 pystray/Pillow 时本模块仍可 import，`TrayIcon.start()` 只记一条
warning 并返回 False，pcmond 继续无托盘运行（采集/HTTP/USB 都不受影响）。
Token 在任何展示位置（tooltip / 状态弹窗）一律打码成 `***`。
"""

from __future__ import annotations

import ctypes
import logging
import os
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Tuple

from . import __version__
from .config import CONFIG_PATH, LOG_PATH

LOGGER = logging.getLogger(__name__)

try:  # 可选依赖：只影响托盘
    import pystray
    from PIL import Image, ImageDraw

    HAS_TRAY = True
except ImportError as _exc:  # pragma: no cover - 取决于运行环境是否安装
    pystray = None  # type: ignore[assignment]
    Image = None  # type: ignore[assignment]
    ImageDraw = None  # type: ignore[assignment]
    HAS_TRAY = False
    _TRAY_IMPORT_ERROR: Optional[ImportError] = _exc
else:  # pragma: no cover - 取决于运行环境是否安装
    _TRAY_IMPORT_ERROR = None

TRAY_TITLE = "pcmond"
ICON_SIZE = 32
REFRESH_S = 1.0
TOKEN_MASK = "***"
MB_ICONINFORMATION = 0x00000040

COLOR_GREEN = (46, 204, 113)  # 绿：串口已打开，USB 在推帧
COLOR_YELLOW = (241, 196, 15)  # 黄：只有 HTTP 在跑
COLOR_RED = (231, 76, 60)  # 红：两个链路都不通
COLOR_RGB = {"green": COLOR_GREEN, "yellow": COLOR_YELLOW, "red": COLOR_RED}
STATUS_TEXT = {
    "green": "串口在线（USB 推帧中）",
    "yellow": "仅 HTTP 在线（未发现串口）",
    "red": "链路都不通",
}


def mask_token(token: Optional[str]) -> str:
    """Token 一律打码：任何界面/日志展示都不出现明文。"""
    return TOKEN_MASK if (token or "").strip() else "未设置"


@dataclass(frozen=True)
class TrayStatus:
    """托盘展示所需的一次状态快照（由 main 中的 status_provider 每秒构建）。"""

    usb_connected: bool = False
    usb_port: Optional[str] = None
    http_running: bool = False
    http_port: int = 8787
    http_url: str = ""
    seq: Optional[int] = None
    last_frame_epoch: Optional[float] = None
    interval_s: float = 1.0
    token: str = ""

    @property
    def color(self) -> str:
        """绿 > 黄 > 红：USB 优先，其次 HTTP，都没有就红。"""
        if self.usb_connected:
            return "green"
        if self.http_running:
            return "yellow"
        return "red"

    @property
    def rgb(self) -> Tuple[int, int, int]:
        return COLOR_RGB[self.color]

    @property
    def status_text(self) -> str:
        return STATUS_TEXT[self.color]

    def _usb_text(self) -> str:
        if not self.usb_connected:
            return "未连接"
        return self.usb_port or "已连接"

    def _http_text(self) -> str:
        if not self.http_running:
            return "未启动"
        if self.http_url:
            return self.http_url
        return f"http://127.0.0.1:{self.http_port}"

    def _frame_text(self) -> str:
        seq = "--" if self.seq is None else str(self.seq)
        if self.last_frame_epoch is None:
            return f"seq {seq} @ --"
        return f"seq {seq} @ {time.strftime('%H:%M:%S', time.localtime(self.last_frame_epoch))}"

    def tooltip(self) -> str:
        """鼠标悬停提示（Windows 托盘 tooltip 上限 128 字符，保持紧凑单行）。"""
        return (
            f"{TRAY_TITLE} v{__version__} · {self.status_text} | "
            f"HTTP {self._http_text()} | USB {self._usb_text()} | "
            f"{self._frame_text()} | {self.interval_s:.1f}s | Token {mask_token(self.token)}"
        )

    def dialog_text(self) -> str:
        """右键「状态」弹窗内容：与 tooltip 同源，多一行完整 API URL，Token 打码。"""
        api_url = f"{self._http_text()}/api/stats" if self.http_running else "未启动"
        return "\n".join(
            [
                f"pcmond v{__version__}",
                f"状态：{self.status_text}",
                f"HTTP：{api_url}",
                f"USB：{self._usb_text()}",
                f"最近一帧：{self._frame_text()}",
                f"采集间隔：{self.interval_s:.1f} s",
                f"Token：{mask_token(self.token)}",
            ]
        )


def _draw_icon(rgb: Tuple[int, int, int]):
    """用 Pillow 程序化画状态圆点（不提交二进制图片）。"""
    image = Image.new("RGBA", (ICON_SIZE, ICON_SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    margin = 3
    draw.ellipse(
        (margin, margin, ICON_SIZE - margin, ICON_SIZE - margin),
        fill=rgb + (255,),
        outline=(35, 35, 35, 255),
        width=3,
    )
    # 左上小高光，让圆点看着像状态灯
    draw.ellipse((9, 7, 15, 13), fill=(255, 255, 255, 90))
    return image


def _show_message(text: str) -> None:
    """弹系统消息框（ctypes MessageBoxW，避免在托盘线程里用 tkinter）；失败只记日志。"""
    if os.name == "nt":
        try:
            ctypes.windll.user32.MessageBoxW(None, text, "pcmond 状态", MB_ICONINFORMATION)
            return
        except Exception:  # noqa: BLE001 - 弹窗失败不能影响托盘
            LOGGER.exception("MessageBox 弹出失败")
    LOGGER.info("状态信息：\n%s", text)


class TrayIcon:
    """托盘图标：daemon 线程跑 pystray 消息循环，后台每秒刷新颜色/tooltip。"""

    def __init__(
        self,
        status_provider: Callable[[], TrayStatus],
        on_quit: Callable[[], None],
        config_dir: Optional[Path] = None,
        log_path: Optional[Path] = None,
    ) -> None:
        self._status_provider = status_provider
        self._on_quit = on_quit
        self._config_dir = Path(config_dir) if config_dir is not None else CONFIG_PATH.parent
        self._log_path = Path(log_path) if log_path is not None else LOG_PATH
        self._stop = threading.Event()
        self._icon = None
        self._thread: Optional[threading.Thread] = None
        self._last_color: Optional[str] = None

    # ---- 生命周期 ----

    def start(self) -> bool:
        """返回是否真的启动了托盘；没装依赖/启动失败都返回 False 且不抛异常。"""
        if not HAS_TRAY:
            LOGGER.warning(
                "未安装 pystray/Pillow，系统托盘不可用（pip install -r requirements-tray.txt）；继续无托盘运行（原因：%s）",
                _TRAY_IMPORT_ERROR,
            )
            return False
        try:
            status = self._safe_status()
            menu = self._build_menu()
            self._icon = pystray.Icon(
                TRAY_TITLE,
                _draw_icon(status.rgb),
                status.tooltip(),
                menu=menu,
            )
            self._thread = threading.Thread(
                target=self._icon.run,
                kwargs={"setup": self._setup},
                name="pcmond-tray",
                daemon=True,  # 托盘线程必须是 daemon，主进程退出就跟着退
            )
            self._thread.start()
        except Exception:  # noqa: BLE001 - 托盘是可选功能，任何异常都降级
            LOGGER.exception("系统托盘启动失败，继续无托盘运行")
            self._icon = None
            self._thread = None
            return False
        LOGGER.info("系统托盘已启动（右键菜单：状态 / 打开配置目录 / 打开日志 / 退出）")
        return True

    def stop(self) -> None:
        """停止托盘；可从任意线程调用，重复调用安全。"""
        self._stop.set()
        icon, thread = self._icon, self._thread
        self._icon, self._thread = None, None
        if icon is not None:
            try:
                icon.stop()
            except Exception:  # noqa: BLE001
                LOGGER.exception("停止托盘图标失败")
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=3.0)

    # ---- pystray 回调 ----

    def _setup(self, icon) -> None:
        """pystray 图标就绪后调用（独立线程）：先显示图标，再首刷 + 每秒刷新。

        注意：**自定义 setup 必须显式 `icon.visible = True`**。pystray 只有在
        `run(setup=None)` 时才会自动显示图标，传自定义 setup 时不会——真机验收
        抓到的坑：不设的话图标永远不出现，日志却一切正常。
        """
        icon.visible = True
        self._refresh(icon)
        while not self._stop.is_set():
            self._stop.wait(REFRESH_S)
            if self._stop.is_set():
                break
            self._refresh(icon)

    def _refresh(self, icon) -> None:
        try:
            status = self._safe_status()
            LOGGER.debug("托盘刷新：%s（USB=%s）", status.color, status.usb_port if status.usb_connected else "未连接")
            LOGGER.debug("tooltip=%s", status.tooltip())
            icon.icon = _draw_icon(status.rgb)
            icon.title = status.tooltip()
            icon.update_menu()
            if status.color != self._last_color:
                self._last_color = status.color
                LOGGER.info("托盘状态：%s（%s）", status.status_text, status.color)
        except Exception:  # noqa: BLE001 - 刷新失败不能让托盘线程死掉
            LOGGER.exception("托盘刷新失败")

    def _build_menu(self):
        def status_label(item=None) -> str:
            return f"状态：{self._safe_status().status_text}"

        def on_status(icon, item) -> None:
            self._show_status(icon)

        def on_open_config(icon, item) -> None:
            self._open_config_dir()

        def on_open_log(icon, item) -> None:
            self._open_log()

        def on_quit(icon, item) -> None:
            self._quit(icon)

        return pystray.Menu(
            pystray.MenuItem(status_label, on_status),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("打开配置目录", on_open_config),
            pystray.MenuItem("打开日志", on_open_log),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出", on_quit),
        )

    def _quit(self, icon=None) -> None:
        """菜单「退出」：先停图标，再通知 main 走正常停链路流程。"""
        LOGGER.info("托盘菜单：退出 pcmond")
        self._stop.set()
        target = icon if icon is not None else self._icon
        if target is not None:
            try:
                target.stop()
            except Exception:  # noqa: BLE001
                LOGGER.exception("停止托盘图标失败")
        self._on_quit()

    def _show_status(self, icon) -> None:
        text = self._safe_status().dialog_text()
        LOGGER.info("托盘菜单：状态\n%s", text)
        _show_message(text)

    def _open_config_dir(self) -> None:
        self._launch(["explorer", str(self._config_dir)], self._config_dir)

    def _open_log(self) -> None:
        target = self._log_path
        if not target.exists():
            target = target.parent
        if os.name == "nt":
            argv = ["notepad.exe", str(target)]
        else:
            argv = ["xdg-open", str(target)]
        self._launch(argv, target)

    def _launch(self, argv, target: Path) -> None:
        LOGGER.info("托盘菜单：打开 %s", target)
        try:
            subprocess.Popen(argv)
        except OSError:
            LOGGER.exception("打开失败：%s", target)
            opener = getattr(os, "startfile", None)
            if opener is not None:
                try:
                    opener(str(target))
                except OSError:
                    LOGGER.exception("startfile 打开失败：%s", target)

    # ---- 状态 ----

    def _safe_status(self) -> TrayStatus:
        try:
            return self._status_provider()
        except Exception:  # noqa: BLE001
            LOGGER.exception("读取托盘状态失败，按红色显示")
            return TrayStatus()
