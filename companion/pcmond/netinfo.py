"""本机局域网 IPv4 地址：打印设备 URL、生成设备端 api_url 用。"""

from __future__ import annotations

import logging
import socket
from typing import List, Optional

LOGGER = logging.getLogger(__name__)


def lan_addresses() -> List[str]:
    """尽力枚举本机非回环 IPv4；一个都拿不到时返回空列表（不抛异常）。"""
    addresses: set[str] = set()
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # UDP connect 不真正发包，只让内核挑一张出口网卡，拿到主网卡地址
        probe.connect(("8.8.8.8", 80))
        address = probe.getsockname()[0]
        if address and not address.startswith("127."):
            addresses.add(address)
    except OSError as exc:
        LOGGER.debug("探测主网卡地址失败：%s", exc)
    finally:
        probe.close()

    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            address = info[4][0]
            if not address.startswith("127."):
                addresses.add(address)
    except OSError as exc:
        LOGGER.debug("枚举本机地址失败：%s", exc)
    return sorted(addresses)


def lan_ip() -> Optional[str]:
    """返回第一张局域网网卡的 IPv4（设备端 api_url 用它）。"""
    addresses = lan_addresses()
    return addresses[0] if addresses else None
