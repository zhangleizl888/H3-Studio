r"""出站 HTTP 客户端的统一入口：打本机的请求一律绕过环境代理。

这台机器（以及多数桌面开发机）在注册表里挂着系统代理。httpx 的 `trust_env` 默认是 True，
Windows 上它会经 `urllib.request.getproxies()` 读到那个代理，于是**发给 127.0.0.1 的环回请求
也被送进代理**：目标端口活着时看起来一切正常，端口一停就变成
`RemoteProtocolError: Server disconnected without sending a response`，
本地 llama.cpp 探活/调用、ComfyUI 实例、显存看门狗会全被误判成「服务挂了」。

规则：目标是环回或私网（局域网那台 ComfyUI）→ 绕过环境代理；公网目标（RunningHub、模型下载）
→ 照旧走环境代理。
"""

from __future__ import annotations

import ipaddress
from typing import Any
from urllib.parse import urlparse

import httpx

_LOCAL_SUFFIXES = (".local", ".localhost")


def is_local_target(url: str) -> bool:
    """URL 的主机是不是本机/局域网。认不出主机名时按公网处理，别把云端也断了。"""
    host = (urlparse(url).hostname or "").strip().lower()
    if not host:
        return False
    if host == "localhost" or host.endswith(_LOCAL_SUFFIXES):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_link_local or ip.is_private


def async_client(url: str, **kwargs: Any) -> httpx.AsyncClient:
    """按目标地址决定要不要吃环境代理，其余参数原样交给 httpx。"""
    kwargs.setdefault("trust_env", not is_local_target(url))
    return httpx.AsyncClient(**kwargs)
