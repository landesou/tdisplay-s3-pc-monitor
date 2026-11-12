"""pcmond 图形界面（tkinter）：状态 + 设置，复用 `PcmondRuntime`。

线程模型：**所有 Tk 操作都在主线程**；采集/链路/托盘线程由 `PcmondRuntime` 管理，
这里只用 `root.after(1000, ...)` 轮询 `runtime.latest()` 等共享状态，不跨线程碰控件。
关窗口调用 `runtime.stop()` —— 和托盘「退出」走同一条优雅停路径。

控件布局在 `gui_widgets.py`（mixin），本文件只做初始化、刷新和设置操作。
"""

from __future__ import annotations

import logging
import os
import secrets
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox
from typing import Optional

from . import autostart
from .config import CONFIG_PATH, DEFAULT_DSH_PORT, DEFAULT_PORT, LOG_PATH, Config
from .gui_util import (
    AUTO_SERIAL,
    api_url,
    describe_device,
    describe_link,
    describe_proc,
    describe_seq,
    describe_today,
    describe_week,
    mask_token,
    parse_dsh_port,
    parse_serial_choice,
    rate_badge,
    serial_choices,
    validate_settings,
)
from .gui_widgets import WidgetsMixin
from .runtime import PcmondRuntime

LOGGER = logging.getLogger(__name__)

REFRESH_MS = 1000


class PcmondApp(WidgetsMixin):
    """GUI 主窗口：控件构建见 WidgetsMixin，这里管运行时与交互。"""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.runtime: Optional[PcmondRuntime] = None
        self._closing = False
        self._token_value = config.token
        self._token_visible = False
        self._serial_choices = serial_choices()
        self.root = tk.Tk()
        self._setup_window()
        self._build_status()
        self._build_settings()
        self._build_bottom()
        self._start_runtime()

    # ---- 运行时 ----

    def _start_runtime(self) -> None:
        runtime = PcmondRuntime(self.config)
        try:
            runtime.start()
        except Exception:  # noqa: BLE001 - GUI 不能因单个组件失败而崩
            LOGGER.exception("启动后台运行时失败")
            runtime.stop()
            runtime = None
        self.runtime = runtime
        self.root.after(300, self._refresh)

    def _restart_runtime(self) -> None:
        if self.runtime is not None:
            self.runtime.stop()
            self.runtime = None
        self._start_runtime()

    # ---- 刷新（主线程，只读共享状态） ----

    def _refresh(self) -> None:
        if self._closing:
            return
        try:
            self._update_status()
        except Exception:  # noqa: BLE001 - 刷新失败不能把窗口弄崩
            LOGGER.exception("刷新状态失败")
        if not self._closing:
            self.root.after(REFRESH_MS, self._refresh)

    def _update_status(self) -> None:
        runtime = self.runtime
        if runtime is None:
            self.link_var.set("● 后台未启动（看日志）")
            self._set_style(self.link_label, "#c0392b")
            return
        if runtime.stop_event.is_set():  # 托盘里点了「退出」
            self._on_quit()
            return
        snapshot = runtime.latest()
        age = runtime.age_s()
        text, color = describe_link(runtime.usb_connected, runtime.usb_port, runtime.http_running)
        self.link_var.set(text)
        self._set_style(self.link_label, color)
        self.seq_var.set(describe_seq(snapshot, age))
        if runtime.http_running:
            self.http_var.set(f"{api_url(runtime.http_port)}")
        else:
            self.http_var.set("未启动")
        dsh = (snapshot or {}).get("dsh")
        self.today_var.set(describe_today(dsh))
        self.week_var.set(describe_week(dsh))
        self.proc_var.set(describe_proc(dsh, self.config.dsh_port))
        badge, badge_color = rate_badge((dsh or {}).get("rate"))
        self.rate_var.set(badge)
        self._set_style(self.rate_label, badge_color)
        self.device_var.set(describe_device(snapshot, age))

    # ---- Token ----

    def _toggle_token(self) -> None:
        self._token_visible = not self._token_visible
        self.token_var.set(self._token_value if self._token_visible else mask_token(self._token_value))

    def _regen_token(self) -> None:
        if not messagebox.askyesno(
            "重新生成 Token",
            "重新生成后设备里的 api_token 需要重新配置（USB 下发或串口命令）。继续？",
            parent=self.root,
        ):
            return
        self._token_value = secrets.token_urlsafe(18)
        self._token_visible = True
        self.token_var.set(self._token_value)
        self._status("已生成新 Token，点「保存并应用」生效")

    def _copy_token(self) -> None:
        self.root.clipboard_clear()
        self.root.clipboard_append(self._token_value)
        self._status("Token 已复制到剪贴板")

    # ---- 设置 ----

    def _on_save(self) -> None:
        error = validate_settings(self.port_var.get(), self.interval_var.get(), self.dsh_port_var.get())
        if error:
            messagebox.showerror("设置无效", error, parent=self.root)
            return
        config = self.config
        entered_url = self.api_var.get().strip()
        auto_old = api_url(config.port)
        try:
            config.wifi_ssid = self.ssid_var.get().strip() or None
            config.wifi_pass = self.pass_var.get() or None
            config.port = int(self.port_var.get())
            config.interval_s = float(self.interval_var.get())
            config.dsh_enabled = bool(self.dsh_var.get())
            config.dsh_port = parse_dsh_port(self.dsh_port_var.get())
            config.token = self._token_value
            config.serial = parse_serial_choice(self.serial_var.get())
            config.api_url = None if (not entered_url or entered_url == auto_old) else entered_url
            config.save()
        except (OSError, ValueError) as exc:
            messagebox.showerror("保存失败", str(exc), parent=self.root)
            return
        # URL 若是自动值，跟着新端口刷新
        self.api_var.set(config.api_url or api_url(config.port))
        self.dsh_port_var.set("自动" if config.dsh_port == 0 else str(config.dsh_port))
        self._restart_runtime()
        self._status(f"已保存并重启链路（HTTP {config.port}）")

    def _on_reset(self) -> None:
        if not messagebox.askyesno("恢复默认", "除 Token 外恢复默认设置并立即应用？", parent=self.root):
            return
        self.ssid_var.set("")
        self.pass_var.set("")
        self.port_var.set(str(DEFAULT_PORT))
        self.interval_var.set("1.0")
        self.dsh_var.set(True)
        self.dsh_port_var.set("自动" if DEFAULT_DSH_PORT == 0 else str(DEFAULT_DSH_PORT))
        self.api_var.set(api_url(DEFAULT_PORT))
        self.serial_var.set(AUTO_SERIAL)
        self._on_save()

    def _on_autostart(self) -> None:
        wanted = bool(self.autostart_var.get())
        if autostart.set_enabled(wanted):
            self._status("已" + ("开启" if wanted else "关闭") + "开机自启")
        else:
            self.autostart_var.set(not wanted)
            messagebox.showerror("开机自启", "设置失败，详见日志", parent=self.root)

    # ---- 其它 ----

    def _status(self, message: str) -> None:
        self.status_var.set(message)

    def _open_path(self, path: Path) -> None:
        target = path if path.exists() else path.parent
        try:
            os.startfile(str(target))  # type: ignore[attr-defined]
        except OSError:
            LOGGER.exception("打开路径失败：%s", target)

    def _on_quit(self) -> None:
        """关窗口/托盘退出：后台线程跑 runtime.stop()，完成后回主线程 destroy。"""
        if self._closing:
            return
        self._closing = True
        try:
            self.quit_btn.configure(state="disabled")
        except tk.TclError:
            pass
        threading.Thread(target=self._stop_worker, name="pcmond-gui-stop", daemon=True).start()

    def _stop_worker(self) -> None:
        try:
            if self.runtime is not None:
                self.runtime.stop()
        except Exception:  # noqa: BLE001
            LOGGER.exception("退出时停止运行时失败")
        finally:
            try:
                self.root.after(0, self._destroy)
            except (RuntimeError, tk.TclError):
                pass

    def _destroy(self) -> None:
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    def run(self) -> None:
        self.root.mainloop()


def launch(config: Config) -> int:
    """创建并运行 GUI；tkinter 缺失/无显示器时抛异常，由 main.run_gui 兜底。"""
    app = PcmondApp(config)
    app.run()
    return 0
