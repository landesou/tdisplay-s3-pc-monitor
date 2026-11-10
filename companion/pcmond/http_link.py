"""HTTP 链路：stdlib ThreadingHTTPServer。

GET /api/stats  Bearer Token 校验，返回**最新快照**（不为请求重新采集）
GET /health     免鉴权，给设备/人做探活
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import socket
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, Optional, Type

from . import __version__
from .netinfo import lan_addresses
from .snapshot import to_json

LOGGER = logging.getLogger(__name__)

STATS_PATH = "/api/stats"
HEALTH_PATH = "/health"
BEARER_PREFIX = "Bearer "

SnapshotProvider = Callable[[], Optional[Dict[str, Any]]]


def check_bearer(header: Optional[str], token: str) -> bool:
    """校验 `Authorization: Bearer <token>`；用 hmac.compare_digest 防时序侧信道。"""
    if not token or not header or not header.startswith(BEARER_PREFIX):
        return False
    supplied = header[len(BEARER_PREFIX) :].strip()
    if not supplied:
        return False
    return hmac.compare_digest(supplied.encode("utf-8"), token.encode("utf-8"))


def build_handler(provider: SnapshotProvider, token: str) -> Type[BaseHTTPRequestHandler]:
    """把 provider/token 闭包进 handler 类（每台 server 一份配置）。"""

    class _Handler(BaseHTTPRequestHandler):
        server_version = f"pcmond/{__version__}"
        protocol_version = "HTTP/1.1"  # 始终带 Content-Length，可安全 keep-alive

        def _send_json(self, status: HTTPStatus, payload: Dict[str, Any], extra: Optional[Dict[str, str]] = None) -> None:
            body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            self.send_response(int(status))
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 - stdlib 约定
            path = self.path.split("?", 1)[0]
            if path == HEALTH_PATH:
                snapshot = self._safe_provider()
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "status": "ok",
                        "version": __version__,
                        "seq": snapshot.get("seq") if snapshot else None,
                    },
                )
                return

            if path != STATS_PATH:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
                return

            if not check_bearer(self.headers.get("Authorization"), token):
                LOGGER.debug("401 未授权请求：%s", self.client_address)
                self._send_json(
                    HTTPStatus.UNAUTHORIZED,
                    {"error": "unauthorized"},
                    {"WWW-Authenticate": 'Bearer realm="pcmond"'},
                )
                return

            snapshot = self._safe_provider()
            if snapshot is None:
                self._send_json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "no snapshot yet"})
                return
            # 与 USB 完全相同的紧凑 JSON 字节序列
            body = to_json(snapshot).encode("utf-8")
            self.send_response(int(HTTPStatus.OK))
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _safe_provider(self) -> Optional[Dict[str, Any]]:
            try:
                return provider()
            except Exception:  # noqa: BLE001 - 采集异常不能把 server 带崩
                LOGGER.exception("读取最新快照失败")
                return None

        def log_message(self, fmt: str, *args: Any) -> None:
            LOGGER.debug("%s - %s", self.address_string(), fmt % args)

    return _Handler


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    # Windows 上 SO_REUSEADDR 的含义是「允许别人再绑这个端口」，反而会导致第二个
    # pcmond 实例静默抢端口；所以 Windows 用 SO_EXCLUSIVEADDRUSE，Linux/macOS 才开 reuse。
    allow_reuse_address = os.name != "nt"

    def server_bind(self) -> None:  # noqa: D102 - 覆盖 stdlib 以设置独占端口
        if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class HttpLink:
    """HTTP 服务线程；端口被占用时 start() 返回 False，调用方降级继续跑。"""

    def __init__(self, provider: SnapshotProvider, token: str, port: int = 8787, host: str = "0.0.0.0") -> None:
        self._provider = provider
        self._token = token
        self.port = int(port)
        self.host = host
        self._server: Optional[_Server] = None
        self._thread: Optional[threading.Thread] = None

    @property
    def bound_port(self) -> Optional[int]:
        """实际监听端口（测试用 port=0 时取系统分配值）。"""
        return self._server.server_address[1] if self._server is not None else None

    def start(self) -> bool:
        try:
            server = _Server((self.host, self.port), build_handler(self._provider, self._token))
        except OSError as exc:
            LOGGER.error("HTTP 端口 %d 启动失败（可能被占用）：%s；HTTP 链路关闭，采集/USB 继续", self.port, exc)
            return False
        self._server = server
        self._thread = threading.Thread(
            target=server.serve_forever,
            kwargs={"poll_interval": 0.2},
            name="pcmond-http",
            daemon=True,
        )
        self._thread.start()
        LOGGER.info("HTTP 已监听 %s:%d（Bearer Token 已启用）", self.host, server.server_address[1])
        for address in lan_addresses():
            LOGGER.info("设备 URL: http://%s:%d%s", address, server.server_address[1], STATS_PATH)
        return True

    def stop(self) -> None:
        server, thread = self._server, self._thread
        self._server = None
        self._thread = None
        if server is not None:
            try:
                server.shutdown()
            except Exception:  # noqa: BLE001
                LOGGER.exception("HTTP server 关闭异常")
            server.server_close()
        if thread is not None:
            thread.join(timeout=2.0)
