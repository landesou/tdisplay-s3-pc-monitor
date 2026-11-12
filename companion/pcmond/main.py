"""pcmond 入口：参数解析、日志、CLI 常驻 / --once / 图形界面。

- 源码运行：`python -m pcmond` 是 CLI 常驻（与原行为一致），`--gui` 起设置/状态窗口；
- 打包成 exe（PyInstaller onefile/windowed）：无参数双击 → GUI；带参数（`--once`、`--no-usb` 等）→ CLI。
"""

from __future__ import annotations

import argparse
import logging
import logging.handlers
import os
import platform
import sys
import time
from typing import Any, Dict, List, Optional

from . import __version__
from .collectors import Collector
from .config import CONFIG_PATH, LOG_PATH, Config
from .runtime import PcmondRuntime
from .snapshot import build_snapshot, to_json

LOGGER = logging.getLogger("pcmond")
WARMUP_SECONDS = 0.25


def is_frozen() -> bool:
    """是否运行在 PyInstaller 单文件 exe 里。"""
    return bool(getattr(sys, "frozen", False))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pcmond",
        description="T-Display-S3 PC 性能监控上位机（采集 + USB/HTTP 双传输）",
    )
    parser.add_argument("--gui", action="store_true", help="启动图形设置/状态窗口（打包版无参数时默认 GUI）")
    parser.add_argument("--once", action="store_true", help="采集并打印一帧 JSON 后退出")
    parser.add_argument("--verbose", action="store_true", help="输出 DEBUG 日志")
    parser.add_argument("--port", type=int, help="HTTP 端口（默认 8787，仅本次生效）")
    parser.add_argument("--token", help="Bearer Token（仅本次生效）")
    parser.add_argument("--serial", help="指定串口，如 COM7（默认自动发现 ESP32-S3）")
    parser.add_argument("--no-usb", action="store_true", help="本次关闭 USB 链路")
    parser.add_argument("--no-http", action="store_true", help="本次关闭 HTTP 链路")
    parser.add_argument("--no-tray", action="store_true", help="本次关闭系统托盘（即使装了 pystray）")
    parser.add_argument("--no-dsh", action="store_true", help="本次关闭 DSH 数据源（帧里不带 dsh）")
    parser.add_argument("--interval", type=float, help="采集周期秒（默认 1.0）")
    parser.add_argument("--version", action="version", version=f"pcmond {__version__}")
    return parser


def apply_overrides(config: Config, args: argparse.Namespace) -> Config:
    """命令行覆盖只作用于本次进程，不写回 config.json。"""
    if args.port is not None:
        config.port = int(args.port)
    if args.token:
        config.token = str(args.token)
    if args.serial:
        config.serial = str(args.serial)
    if args.no_usb:
        config.usb_enabled = False
    if args.no_http:
        config.http_enabled = False
    if args.no_tray:
        config.tray_enabled = False
    if args.no_dsh:
        config.dsh_enabled = False
    if args.interval is not None:
        config.interval_s = max(0.2, float(args.interval))
    return config


def setup_logging(verbose: bool, once: bool) -> None:
    """RotatingFileHandler 到 ~/.tdisplay-pcmon/pcmond.log；pythonw 下没有 stderr 就只写文件。"""
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    level = logging.DEBUG if verbose else logging.INFO
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)

    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            LOG_PATH, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(fmt)
        file_handler.setLevel(level)
        root.addHandler(file_handler)
    except OSError as exc:
        LOGGER.warning("无法写日志文件 %s：%s（仅控制台日志）", LOG_PATH, exc)

    stream = sys.stderr
    if stream is not None:  # pythonw 下 sys.stderr 是 None，加 StreamHandler 会报错
        console = logging.StreamHandler(stream)
        console.setFormatter(fmt)
        console.setLevel(logging.WARNING if once else level)
        root.addHandler(console)


def _attach_console() -> None:
    """frozen + windowed（无控制台）时，让 `pcmond.exe --once` 等 CLI 用法仍能输出。

    优先复用父进程继承下来的 stdout/stderr 句柄（cmd/PowerShell 重定向、管道都能工作）；
    拿不到时再 AttachConsole 附着到父控制台。
    """
    if os.name != "nt" or not is_frozen():
        return
    try:
        import ctypes
        import msvcrt

        for std_handle, name in ((-11, "stdout"), (-12, "stderr")):
            handle = ctypes.windll.kernel32.GetStdHandle(std_handle)
            if handle in (0, -1, None):
                continue
            try:
                fd = msvcrt.open_osfhandle(handle, os.O_WRONLY)
                stream = open(fd, "w", encoding="utf-8", errors="replace", buffering=1, closefd=True)
                setattr(sys, name, stream)
            except OSError:
                continue
    except Exception:  # noqa: BLE001 - 控制台只是尽量恢复，失败不影响功能
        pass
    if sys.stdout is not None:
        return
    try:
        import ctypes

        if ctypes.windll.kernel32.AttachConsole(-1):
            sys.stdout = open("CONOUT$", "w", encoding="utf-8", errors="replace", buffering=1)
            sys.stderr = open("CONOUT$", "w", encoding="utf-8", errors="replace", buffering=1)
    except Exception:  # noqa: BLE001
        pass


def run_once(config: Config) -> int:
    """采一帧打印到 stdout 后退出（验收用，不连串口/不开端口）。"""
    collector = Collector()
    collector.warmup()
    time.sleep(WARMUP_SECONDS)  # 给 psutil 一个可测窗口，首帧 CPU 占用率才不是 0
    sample = collector.collect()
    dsh_payload = _collect_dsh_once(config)
    snapshot = build_snapshot(sample, seq=1, dsh=dsh_payload)
    line = to_json(snapshot)
    stream = sys.stdout or sys.stderr
    if stream is not None:
        try:
            stream.write(line + "\n")
            stream.flush()
        except (OSError, ValueError):
            pass
    if not line:
        LOGGER.info("--once 无输出流")
    elif stream is None:  # pythonw / 无控制台
        LOGGER.info("--once 输出：%s", line)
    LOGGER.info(
        "--once 完成：%d 字节，gpu=%s，dsh=%s",
        len(line.encode("utf-8")),
        "ok" if snapshot.get("gpu") else "null",
        "ok" if dsh_payload else "none",
    )
    return 0


def _collect_dsh_once(config: Config) -> Optional[Dict[str, Any]]:
    """--once 用：同步跑一次 DSH 采集（含进程 CPU 预热）；失败只降级为无 dsh。"""
    if not config.dsh_enabled:
        LOGGER.info("DSH 采集已关闭（--no-dsh / dsh_enabled=false）")
        return None
    try:
        from .dsh import DshSampler

        sampler = DshSampler(pricing=config.dsh_pricing, port=config.dsh_port)
        return sampler.refresh()
    except Exception:  # noqa: BLE001 - DSH 是附加数据，失败不能影响 sys 段
        LOGGER.exception("DSH 采集失败，本帧不带 dsh 字段")
        return None


def run_daemon(config: Config) -> int:
    """CLI 常驻模式（源码 `python -m pcmond` / exe 带参数）。"""
    LOGGER.info(
        "pcmond %s 启动（Python %s，%s）",
        __version__,
        platform.python_version(),
        platform.platform(),
    )
    LOGGER.info("配置文件：%s", CONFIG_PATH)
    LOGGER.info("Bearer Token：%s", config.token)

    runtime = PcmondRuntime(config)
    try:
        runtime.start()
        # 托盘「退出」/ GUI 关闭都会 set stop_event，这里统一走 runtime.stop()
        while not runtime.stop_event.wait(1.0):
            pass
    except KeyboardInterrupt:
        LOGGER.info("收到 Ctrl+C，开始退出")
    finally:
        runtime.stop()
        LOGGER.info("pcmond 已退出")
    return 0


def run_gui(config: Config) -> int:
    """图形界面模式；tkinter 缺失/无显示器时优雅报错，不影响 CLI。"""
    try:
        from .gui import launch
    except ImportError as exc:
        LOGGER.error("图形界面不可用（缺 tkinter？）：%s；CLI 用法不受影响（如 --once）", exc)
        return 2
    try:
        return launch(config)
    except Exception:  # noqa: BLE001 - 无显示器/headless 等
        LOGGER.exception("图形界面启动失败；CLI 用法不受影响（如 --once）")
        return 1


def main(argv: Optional[List[str]] = None) -> int:
    raw_argv = list(sys.argv[1:]) if argv is None else list(argv)
    args = build_parser().parse_args(raw_argv)
    config = Config.load()
    config = apply_overrides(config, args)
    setup_logging(args.verbose, args.once)

    # 打包版无参数 → GUI（双击即用）；带任何参数 → 保持 CLI 行为；源码运行必须显式 --gui
    gui_mode = args.gui or (is_frozen() and not raw_argv)
    if not gui_mode:
        _attach_console()

    if args.once:
        return run_once(config)
    if gui_mode:
        return run_gui(config)
    return run_daemon(config)


if __name__ == "__main__":
    sys.exit(main())
