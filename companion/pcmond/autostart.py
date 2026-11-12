"""开机自启：打包版写 HKCU Run（无需管理员），源码版复用启动文件夹快捷方式脚本。

- 打包 exe：`HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run` 里写 `"<exe>" --gui`；
- 源码运行：调用仓库里已有的 `install_startup.ps1` / `uninstall_startup.ps1`（启动文件夹 .lnk），
  这样 `python -m pcmond --gui` 的开发态也有同样的自启能力。
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

LOGGER = logging.getLogger(__name__)

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "pcmond"
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
STARTUP_LNK = (
    Path(os.environ.get("APPDATA", ""))
    / "Microsoft"
    / "Windows"
    / "Start Menu"
    / "Programs"
    / "Startup"
    / "pcmond.lnk"
)


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def autostart_command() -> Optional[str]:
    """打包版的自启命令行；源码版返回 None（改用启动文件夹快捷方式）。"""
    if not is_frozen():
        return None
    return f'"{sys.executable}" --gui'


def is_enabled() -> bool:
    """当前用户登录时是否会自动启动 pcmond。"""
    if is_frozen():
        return _read_run_value() == autostart_command()
    return STARTUP_LNK.exists()


def set_enabled(enabled: bool) -> bool:
    """打开/关闭自启；成功返回 True（失败只记日志，GUI 会提示）。"""
    if is_frozen():
        return _write_run_value(autostart_command() if enabled else None)
    script = "install_startup.ps1" if enabled else "uninstall_startup.ps1"
    script_path = Path(__file__).resolve().parent.parent / script
    if not script_path.exists():
        LOGGER.warning("找不到自启脚本：%s", script_path)
        return False
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script_path)],
            check=False,
            creationflags=CREATE_NO_WINDOW,
        )
    except OSError:
        LOGGER.exception("调用自启脚本失败：%s", script_path)
        return False
    return is_enabled() == enabled


def _read_run_value() -> Optional[str]:
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, VALUE_NAME)
            return str(value) if value else None
    except (OSError, ImportError):
        return None


def _write_run_value(command: Optional[str]) -> bool:
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if command:
                winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, command)
            else:
                try:
                    winreg.DeleteValue(key, VALUE_NAME)
                except FileNotFoundError:
                    pass
        return True
    except (OSError, ImportError):
        LOGGER.exception("写 HKCU Run 失败（自启开关未生效）")
        return False
