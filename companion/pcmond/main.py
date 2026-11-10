"""pcmond 入口：参数解析、日志、采集线程 + USB/HTTP 两条传输链路。"""

from __future__ import annotations

import argparse
import logging
import logging.handlers
import platform
import sys
import threading
import time
from typing import Any, List, Optional

from . import __version__
from .collectors import Collector
from .config import CONFIG_PATH, LOG_PATH, Config
from .http_link import HttpLink
from .sampler import Sampler
from .snapshot import build_snapshot, to_json

LOGGER = logging.getLogger("pcmond")
WARMUP_SECONDS = 0.25


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pcmond",
        description="T-Display-S3 PC 性能监控上位机（采集 + USB/HTTP 双传输）",
    )
    parser.add_argument("--once", action="store_true", help="采集并打印一帧 JSON 后退出")
    parser.add_argument("--verbose", action="store_true", help="输出 DEBUG 日志")
    parser.add_argument("--port", type=int, help="HTTP 端口（默认 8787，仅本次生效）")
    parser.add_argument("--token", help="Bearer Token（仅本次生效）")
    parser.add_argument("--serial", help="指定串口，如 COM7（默认自动发现 ESP32-S3）")
    parser.add_argument("--no-usb", action="store_true", help="本次关闭 USB 链路")
    parser.add_argument("--no-http", action="store_true", help="本次关闭 HTTP 链路")
    parser.add_argument("--no-tray", action="store_true", help="本次关闭系统托盘（即使装了 pystray）")
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


def run_once() -> int:
    """采一帧打印到 stdout 后退出（验收用，不连串口/不开端口）。"""
    collector = Collector()
    collector.warmup()
    time.sleep(WARMUP_SECONDS)  # 给 psutil 一个可测窗口，首帧 CPU 占用率才不是 0
    snapshot = build_snapshot(collector.collect(), seq=1)
    line = to_json(snapshot)
    stream = sys.stdout or sys.stderr
    if stream is not None:
        stream.write(line + "\n")
        stream.flush()
    else:  # pythonw 下两个流都没了
        LOGGER.info("--once 输出：%s", line)
    LOGGER.info("--once 完成：%d 字节，gpu=%s", len(line.encode("utf-8")), "ok" if snapshot.get("gpu") else "null")
    return 0


def run_daemon(config: Config) -> int:
    LOGGER.info(
        "pcmond %s 启动（Python %s，%s）",
        __version__,
        platform.python_version(),
        platform.platform(),
    )
    LOGGER.info("配置文件：%s", CONFIG_PATH)
    LOGGER.info("Bearer Token：%s", config.token)

    stop_event = threading.Event()
    sampler = Sampler(config.interval_s)
    http_link: Optional[HttpLink] = None
    usb_link: Optional[Any] = None
    tray_icon: Optional[Any] = None

    try:
        sampler.start()
        LOGGER.info("采集线程已启动（%.2f s/帧）", sampler.interval_s)

        if config.http_enabled:
            http_link = HttpLink(sampler.latest, config.token, port=config.port, host=config.host)
            if not http_link.start():
                LOGGER.warning("HTTP 链路未启动（端口冲突？），采集与 USB 继续")
        else:
            LOGGER.info("HTTP 链路已按参数关闭")

        if config.usb_enabled:
            try:
                from .usb_link import UsbLink

                usb_link = UsbLink(sampler, config)
                usb_link.start()
                LOGGER.info("USB 链路线程已启动（%s）", f"指定 {config.serial}" if config.serial else "自动发现 VID_303A")
            except ImportError as exc:
                LOGGER.error("未安装 pyserial，USB 链路不可用：%s", exc)
        else:
            LOGGER.info("USB 链路已按参数关闭")

        tray_icon = _start_tray(config, sampler, http_link, usb_link, stop_event)

        # 托盘「退出」会 set stop_event；Ctrl+C 走下面分支，两者都优雅退出
        while not stop_event.wait(1.0):
            pass
    except KeyboardInterrupt:
        LOGGER.info("收到 Ctrl+C，开始退出")
    finally:
        if tray_icon is not None:
            tray_icon.stop()
        if usb_link is not None:
            usb_link.stop()
        if http_link is not None:
            http_link.stop()
        sampler.stop()
        LOGGER.info("pcmond 已退出")
    return 0


def _start_tray(
    config: Config,
    sampler: Sampler,
    http_link: Optional[HttpLink],
    usb_link: Optional[Any],
    stop_event: threading.Event,
) -> Optional[Any]:
    """托盘是可选功能：依赖缺失/启动失败都只记日志，返回 None 继续无托盘运行。"""
    if not config.tray_enabled:
        LOGGER.info("系统托盘已按配置/参数关闭（--no-tray）")
        return None
    try:
        from .netinfo import lan_ip
        from .tray import TrayIcon, TrayStatus
    except ImportError as exc:  # 防御性兜底：tray 模块内部已处理可选依赖
        LOGGER.warning("托盘模块不可用：%s；继续无托盘运行", exc)
        return None

    bound_port = http_link.bound_port if http_link is not None and http_link.bound_port else config.port
    base_url = f"http://{lan_ip() or '127.0.0.1'}:{bound_port}"

    def status_provider() -> "TrayStatus":
        snapshot = sampler.latest()
        return TrayStatus(
            usb_connected=bool(usb_link is not None and usb_link.connected),
            usb_port=usb_link.port_name if usb_link is not None else config.serial,
            http_running=bool(http_link is not None and http_link.running),
            http_port=bound_port,
            http_url=base_url,
            seq=snapshot.get("seq") if snapshot else None,
            last_frame_epoch=snapshot.get("t") if snapshot else None,
            interval_s=config.interval_s,
            token=config.token,
        )

    tray_icon = TrayIcon(status_provider, stop_event.set, config_dir=CONFIG_PATH.parent, log_path=LOG_PATH)
    if not tray_icon.start():
        return None
    return tray_icon


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    config = Config.load()
    config = apply_overrides(config, args)
    setup_logging(args.verbose, args.once)
    if args.once:
        return run_once()
    return run_daemon(config)


if __name__ == "__main__":
    sys.exit(main())
