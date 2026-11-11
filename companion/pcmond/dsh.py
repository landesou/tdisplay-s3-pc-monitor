"""DSH（DeepSeek Harness）数据源：会话日志缓存 + 进程监控 + 定时刷新。

职责边界：
- `dsh_parse.py`：zstd JSONL 解析、按天聚合、峰谷费用估算（纯函数，可单测）；
- 本模块：扫描 `~/.dsh/sessions/**/session*.jsonl.zstd`、按 (path, mtime, size)
  增量缓存到 `~/.tdisplay-pcmon/dsh_cache.json`、psutil 进程/端口统计、独立线程每 5 s 刷新。

契约见 `docs/design/architecture.md` §2.5。可用性规则：
- 1 Hz 发帧只读 `latest()` 缓存，绝不每帧解压日志；
- 单文件损坏/格式变化 → 跳过该文件（记 warning），其余文件照常；
- 近 7 天没有任何可用 token 记录时 `latest()` 返回 None → 帧里**不发** dsh 字段（设备显示 `--`），
  不塞一堆 0；DSH 失败永远不影响 sys 段。
"""

from __future__ import annotations

import json
import logging
import socket
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import psutil

from .config import DEFAULT_DSH_PORT, HOME_DIR
from .dsh_parse import (
    WEEK_DAYS,
    build_payload,
    merge_bucket,
    new_bucket,
    normalize_pricing,
    parse_session_file,
)

LOGGER = logging.getLogger(__name__)

SESSIONS_ROOT = Path.home() / ".dsh" / "sessions"
CACHE_PATH = HOME_DIR / "dsh_cache.json"
CACHE_VERSION = 2
REFRESH_S = 5.0
SESSION_GLOB = "session*.jsonl.zstd"
PROC_NAME_KEYWORD = "deepseek harness"
PROC_CPU_WARMUP_S = 0.12
PORT_PROBE_TIMEOUT_S = 0.15
MIB = 1024 * 1024


def _port_listening(port: int, host: str = "127.0.0.1", timeout: float = PORT_PROBE_TIMEOUT_S) -> bool:
    """TCP 探测本机端口是否在监听（比 psutil.net_connections 快，也不需要额外权限）。"""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


class DshSampler:
    """DSH 采集器：后台线程每 `refresh_s` 秒刷新，`latest()` 返回可直接塞进帧的 dsh 对象。"""

    def __init__(
        self,
        sessions_root: Optional[Path] = None,
        cache_path: Optional[Path] = None,
        pricing: Optional[Dict[str, Dict[str, float]]] = None,
        port: int = DEFAULT_DSH_PORT,
        refresh_s: float = REFRESH_S,
    ) -> None:
        self._root = Path(sessions_root) if sessions_root is not None else SESSIONS_ROOT
        self._cache_path = Path(cache_path) if cache_path is not None else CACHE_PATH
        self._pricing = normalize_pricing(pricing)
        self.port = int(port)
        self.refresh_s = max(0.1, float(refresh_s))
        self._lock = threading.Lock()
        self._latest: Optional[Dict[str, Any]] = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._cache = self._load_cache()
        self._cache_dirty = False
        self._proc_warmed = False
        # 观测计数（日志/测试用）
        self.scanned_files = 0
        self.parsed_files = 0

    # ---- 生命周期 ----

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="pcmond-dsh", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def latest(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._latest

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.refresh()
            except Exception:  # noqa: BLE001 - 线程必须活着，保留上一次缓存
                LOGGER.exception("DSH 刷新失败，保留上一次缓存值")
            self._stop.wait(self.refresh_s)

    # ---- 刷新 / 聚合 ----

    def refresh(self, now: Optional[float] = None) -> Optional[Dict[str, Any]]:
        """扫一次会话日志 + 进程，返回新的 dsh 对象；近 7 天没有可用数据时返回 None。"""
        now = time.time() if now is None else float(now)
        today = datetime.fromtimestamp(now).date()
        window_start = today - timedelta(days=WEEK_DAYS - 1)
        files = self._scan_files(window_start)
        self.scanned_files = len(files)
        self.parsed_files = 0

        cache_files = self._cache.setdefault("files", {})
        buckets: Dict[str, Dict[str, float]] = {}
        keep: set = set()
        for path, mtime, size in files:
            key = str(path)
            entry = cache_files.get(key)
            if not isinstance(entry, dict) or entry.get("mtime") != mtime or entry.get("size") != size:
                try:
                    days = parse_session_file(path, self._pricing)
                except Exception as exc:  # noqa: BLE001 - 单文件坏了只跳过
                    LOGGER.warning("跳过无法解析的 DSH 会话日志：%s（%s）", path, exc)
                    cache_files.pop(key, None)
                    self._cache_dirty = True
                    continue
                entry = {"mtime": mtime, "size": size, "days": days}
                cache_files[key] = entry
                self.parsed_files += 1
                self._cache_dirty = True
            days = entry.get("days")
            if not isinstance(days, dict):
                continue
            keep.add(key)
            for day_key, bucket in days.items():
                if not isinstance(day_key, str) or day_key < window_start.isoformat():
                    continue
                merge_bucket(buckets.setdefault(day_key, new_bucket()), bucket)

        # 窗口外/已删除文件的缓存条目不再保留
        for key in list(cache_files.keys()):
            if key not in keep:
                cache_files.pop(key, None)
                self._cache_dirty = True
        if self._cache_dirty:
            self._save_cache()
            self._cache_dirty = False

        payload: Optional[Dict[str, Any]] = None
        if buckets:
            payload = build_payload(buckets, today, self._collect_proc(), now)
        else:
            LOGGER.debug("DSH：近 %d 天没有可用 token 记录，本帧不带 dsh 字段", WEEK_DAYS)
        with self._lock:
            self._latest = payload
        return payload

    def _scan_files(self, window_start: date) -> List[Tuple[Path, float, int]]:
        """列出窗口内有改动的会话日志；mtime 早于窗口起点的文件不可能含窗口内消息，直接跳过。"""
        cutoff = datetime.combine(window_start, datetime.min.time()).timestamp()
        try:
            candidates = list(self._root.glob(f"**/{SESSION_GLOB}"))
        except OSError as exc:
            LOGGER.debug("扫描 DSH 会话目录失败：%s", exc)
            return []
        files: List[Tuple[Path, float, int]] = []
        for path in candidates:
            try:
                stat = path.stat()
            except OSError:
                continue
            if stat.st_mtime < cutoff:
                continue
            files.append((path, float(stat.st_mtime), int(stat.st_size)))
        files.sort(key=lambda item: item[1], reverse=True)
        return files

    # ---- 进程 ----

    def _collect_proc(self) -> Dict[str, Any]:
        count = 0
        rss_bytes = 0.0
        cpu_pct = 0.0
        processes = []
        try:
            # 只枚举 name（快），命中 DSH 后再取 memory_info：全量 memory_info 在 381 进程上要 1.6 s
            for proc in psutil.process_iter(["name"]):
                try:
                    info = proc.info or {}
                    if PROC_NAME_KEYWORD not in str(info.get("name") or "").lower():
                        continue
                    processes.append(proc)
                    count += 1
                    try:
                        rss_bytes += float(proc.memory_info().rss)
                    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
                        pass
                except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
                    continue
        except Exception:  # noqa: BLE001
            LOGGER.exception("枚举 DSH 进程失败")

        try:
            if not self._proc_warmed:
                # psutil cpu_percent(interval=None) 首次调用只建基准（返回 0.0），必须预热一次
                for proc in processes:
                    try:
                        proc.cpu_percent(interval=None)
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
                time.sleep(PROC_CPU_WARMUP_S)
                self._proc_warmed = True
            for proc in processes:
                try:
                    cpu_pct += proc.cpu_percent(interval=None)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
        except Exception:  # noqa: BLE001
            LOGGER.exception("统计 DSH 进程 CPU 失败")

        return {
            "count": count,
            "rss_mb": int(round(rss_bytes / MIB)),
            "cpu_pct": int(round(cpu_pct)),
            "port_up": _port_listening(self.port),
        }

    # ---- 缓存 ----

    def _load_cache(self) -> Dict[str, Any]:
        try:
            raw = json.loads(self._cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"version": CACHE_VERSION, "pricing": self._pricing, "files": {}}
        if not isinstance(raw, dict) or raw.get("version") != CACHE_VERSION or not isinstance(raw.get("files"), dict):
            LOGGER.debug("DSH 缓存版本/结构不匹配，重建：%s", self._cache_path)
            return {"version": CACHE_VERSION, "pricing": self._pricing, "files": {}}
        if raw.get("pricing") != self._pricing:
            # 费用是解析时按当时价格表算好的，价格表变了必须重算
            LOGGER.info("DSH 价格表已变化，重建缓存（%s）", self._cache_path)
            return {"version": CACHE_VERSION, "pricing": self._pricing, "files": {}}
        return raw

    def _save_cache(self) -> None:
        try:
            self._cache["version"] = CACHE_VERSION
            self._cache["pricing"] = self._pricing
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self._cache_path.with_suffix(self._cache_path.suffix + ".tmp")
            temporary.write_text(
                json.dumps(self._cache, ensure_ascii=False, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
            temporary.replace(self._cache_path)
        except OSError as exc:
            LOGGER.warning("写入 DSH 缓存失败：%s", exc)
