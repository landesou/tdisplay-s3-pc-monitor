"""pcmond GUI 的 Tk 控件构建（mixin）：状态区 / 设置区 / 底部。

只负责布局和创建 StringVar/BooleanVar；业务逻辑（刷新、保存、退出）在 `gui.py`。
"""

from __future__ import annotations

import logging
import sys
import tkinter as tk
from pathlib import Path
from tkinter import ttk
from typing import Optional

from . import __version__, autostart
from .config import DEFAULT_DSH_PORT, DEFAULT_PORT, CONFIG_PATH, LOG_PATH
from .gui_util import (
    AUTO_SERIAL,
    COLOR_MUTED,
    LINK_AMBER,
    LINK_GREEN,
    LINK_RED,
    api_url,
    mask_token,
    serial_display,
)

LOGGER = logging.getLogger(__name__)

FONT_UI = ("Microsoft YaHei UI", 10)
FONT_BOLD = ("Microsoft YaHei UI", 10, "bold")
COLOR_STYLES = {LINK_GREEN: "Green.TLabel", LINK_AMBER: "Amber.TLabel", LINK_RED: "Red.TLabel", COLOR_MUTED: "Muted.TLabel"}


def asset_path(name: str) -> Optional[Path]:
    """资源路径：打包后在 _MEIPASS，源码运行在 companion/assets。"""
    base = getattr(sys, "_MEIPASS", None)
    root = Path(base) if base else Path(__file__).resolve().parent.parent
    return root / "assets" / name


class WidgetsMixin:
    """给 PcmondApp 提供控件构建方法（self.root / self.config 由调用方先创建）。"""

    # ---- 窗口 ----

    def _setup_window(self) -> None:
        self.root.title(f"pcmond {__version__} — T-Display-S3 监控")
        self.root.geometry("560x620")
        self.root.minsize(540, 600)
        style = ttk.Style(self.root)
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass
        style.configure("TLabel", font=FONT_UI)
        style.configure("TLabelframe.Label", font=FONT_BOLD)
        style.configure("TButton", font=FONT_UI)
        style.configure("TCheckbutton", font=FONT_UI)
        style.configure("Green.TLabel", foreground=LINK_GREEN)
        style.configure("Amber.TLabel", foreground=LINK_AMBER)
        style.configure("Red.TLabel", foreground=LINK_RED)
        style.configure("Muted.TLabel", foreground=COLOR_MUTED)
        self.root.protocol("WM_DELETE_WINDOW", self._on_quit)
        icon = asset_path("pcmond.ico")
        if icon is not None and icon.exists():
            try:
                self.root.iconbitmap(default=str(icon))
            except tk.TclError:
                LOGGER.debug("窗口图标设置失败", exc_info=True)

    # ---- 状态区 ----

    def _build_status(self) -> None:
        frame = ttk.LabelFrame(self.root, text="状态", padding=(10, 6))
        frame.pack(fill="x", padx=10, pady=(10, 4))
        self.link_var = tk.StringVar(value="启动中…")
        self.seq_var = tk.StringVar(value="seq --")
        self.http_var = tk.StringVar(value="--")
        self.today_var = tk.StringVar(value="--")
        self.week_var = tk.StringVar(value="--")
        self.proc_var = tk.StringVar(value="--")
        self.rate_var = tk.StringVar(value="峰谷 --")
        self.device_var = tk.StringVar(value="--")

        rows = (
            ("链路", self.link_var, "link"),
            ("帧", self.seq_var, None),
            ("HTTP", self.http_var, None),
            ("今日", self.today_var, None),
            ("近 7 天", self.week_var, None),
            ("DSH", self.proc_var, None),
            ("峰谷", self.rate_var, "rate"),
            ("设备", self.device_var, None),
        )
        for index, (name, var, tag) in enumerate(rows):
            ttk.Label(frame, text=name, style="Muted.TLabel").grid(row=index, column=0, sticky="w", padx=(0, 10), pady=1)
            label = ttk.Label(frame, textvariable=var, anchor="w")
            label.grid(row=index, column=1, sticky="we", pady=1)
            frame.columnconfigure(1, weight=1)
            if tag == "link":
                self.link_label = label
            elif tag == "rate":
                self.rate_label = label

    # ---- 设置区 ----

    def _build_settings(self) -> None:
        frame = ttk.LabelFrame(self.root, text="设置", padding=(10, 6))
        frame.pack(fill="x", padx=10, pady=4)
        frame.columnconfigure(1, weight=1)

        self.ssid_var = tk.StringVar(value=self.config.wifi_ssid or "")
        self.pass_var = tk.StringVar(value=self.config.wifi_pass or "")
        self.port_var = tk.StringVar(value=str(self.config.port))
        self.interval_var = tk.StringVar(value=f"{self.config.interval_s:g}")
        self.api_var = tk.StringVar(value=self.config.api_url or api_url(self.config.port))
        self.serial_var = tk.StringVar(value=serial_display(self.config.serial, self._serial_choices))
        self.dsh_var = tk.BooleanVar(value=self.config.dsh_enabled)
        self.dsh_port_var = tk.StringVar(value="自动" if self.config.dsh_port == 0 else str(self.config.dsh_port))
        self.token_var = tk.StringVar(value=mask_token(self._token_value))

        entries = (
            ("Wi-Fi SSID", self.ssid_var, False),
            ("Wi-Fi 密码", self.pass_var, True),
            ("HTTP 端口", self.port_var, False),
            ("采集间隔（秒）", self.interval_var, False),
            ("API URL", self.api_var, False),
        )
        row = 0
        for label, var, secret in entries:
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", pady=2)
            ttk.Entry(frame, textvariable=var, show="*" if secret else "").grid(
                row=row, column=1, columnspan=2, sticky="we", pady=2
            )
            row += 1

        ttk.Label(frame, text="串口").grid(row=row, column=0, sticky="w", pady=2)
        ttk.Combobox(frame, textvariable=self.serial_var, values=self._serial_choices, state="readonly").grid(
            row=row, column=1, columnspan=2, sticky="we", pady=2
        )
        row += 1

        ttk.Label(frame, text="DSH 采集").grid(row=row, column=0, sticky="w", pady=2)
        ttk.Checkbutton(frame, text="启用 token/进程监控", variable=self.dsh_var).grid(
            row=row, column=1, sticky="w", pady=2
        )
        row += 1

        ttk.Label(frame, text="DSH 端口").grid(row=row, column=0, sticky="w", pady=2)
        ttk.Entry(frame, textvariable=self.dsh_port_var, width=10).grid(row=row, column=1, sticky="w", pady=2)
        row += 1

        ttk.Label(frame, text="Bearer Token").grid(row=row, column=0, sticky="w", pady=2)
        ttk.Entry(frame, textvariable=self.token_var, state="readonly").grid(row=row, column=1, sticky="we", pady=2)
        token_buttons = ttk.Frame(frame)
        token_buttons.grid(row=row, column=2, sticky="e", padx=(6, 0))
        ttk.Button(token_buttons, text="显示", width=5, command=self._toggle_token).pack(side="left")
        ttk.Button(token_buttons, text="重新生成", width=8, command=self._regen_token).pack(side="left", padx=3)
        ttk.Button(token_buttons, text="复制", width=5, command=self._copy_token).pack(side="left")
        row += 1

        actions = ttk.Frame(frame)
        actions.grid(row=row, column=0, columnspan=3, sticky="we", pady=(6, 0))
        ttk.Button(actions, text="保存并应用", command=self._on_save).pack(side="left")
        ttk.Button(actions, text="恢复默认", command=self._on_reset).pack(side="left", padx=8)
        self.status_var = tk.StringVar(value="")
        ttk.Label(actions, textvariable=self.status_var, style="Muted.TLabel").pack(side="left", padx=10)

    # ---- 底部 ----

    def _build_bottom(self) -> None:
        frame = ttk.Frame(self.root, padding=(10, 4))
        frame.pack(fill="x", side="bottom", padx=10, pady=(4, 10))
        self.autostart_var = tk.BooleanVar(value=autostart.is_enabled())
        ttk.Checkbutton(frame, text="开机自启", variable=self.autostart_var, command=self._on_autostart).pack(
            side="left"
        )
        ttk.Button(frame, text="打开配置目录", command=lambda: self._open_path(CONFIG_PATH.parent)).pack(
            side="left", padx=(12, 0)
        )
        ttk.Button(frame, text="打开日志", command=lambda: self._open_path(LOG_PATH)).pack(side="left", padx=(8, 0))
        self.quit_btn = ttk.Button(frame, text="退出", command=self._on_quit)
        self.quit_btn.pack(side="right")

    # ---- 样式 ----

    def _set_style(self, label: Optional[ttk.Label], color: str) -> None:
        if label is None:
            return
        label.configure(style=COLOR_STYLES.get(color, "TLabel"))
