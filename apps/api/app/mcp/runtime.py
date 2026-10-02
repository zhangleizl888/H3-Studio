"""MCP server 的运行期接入。

一把进程级凭据（stdio：`h3 mcp serve` 自己那份配置），或每个 HTTP 会话自带一把
（streamable-http：中间件把 Authorization 头塞进 contextvar，工具按那把钥匙去问后端）。
后者是对「对外暴露」必要的交代：MCP 端点不能变成「所有连进来的人共用主机身份」的后门。
"""

from __future__ import annotations

import contextvars
import time
from typing import Any

from ..cli.client import H3Client, H3Error
from ..cli.store import Config, load

# 当前 MCP 调用携带的 agent token（HTTP 会话才有；stdio 恒为 None）
current_token: contextvars.ContextVar[str | None] = contextvars.ContextVar("h3_mcp_token", default=None)

_server_cfg: Config | None = None
_clients: dict[str, H3Client] = {}
# 每个会话 token 上一次向后端验身的结果，60 秒内不重复问
_verify_cache: dict[str, tuple[float, dict[str, Any]]] = {}

VERIFY_TTL_S = 60.0


def server_config() -> Config:
    global _server_cfg
    if _server_cfg is None:
        _server_cfg = load()
    return _server_cfg


def set_server_config(cfg: Config) -> None:
    global _server_cfg
    _server_cfg = cfg


def _key() -> str:
    tok = current_token.get()
    return tok if tok else "__process__"


async def _open(cfg: Config) -> H3Client:
    client = H3Client(cfg, timeout=120.0)
    await client.__aenter__()
    return client


async def get_client() -> H3Client:
    """按当前调用身份拿到一个已认证的客户端。"""
    key = _key()
    base = server_config()
    if key == "__process__":
        cfg = base
    else:
        cfg = Config.from_dict({**base.to_dict(), "token": key})
    client = _clients.get(key)
    if client is None:
        client = await _open(cfg)
        _clients[key] = client
    return client


async def close_clients() -> None:
    for client in list(_clients.values()):
        try:
            await client.__aexit__(None, None, None)
        except Exception:
            pass
    _clients.clear()
    _verify_cache.clear()


async def verify_token(token: str) -> dict[str, Any]:
    """中间件用的身份校验：只认 agent token，不认浏览器的 15 分钟 JWT。

    为什么拒绝 JWT：那把钥匙属于某个坐在屏幕前的人的登录会话，把它当成常驻服务凭据
    等于让所有连 MCP 的智能体共享一个人的身份，而且随时会过期变成难查的 401。
    """
    now = time.monotonic()
    hit = _verify_cache.get(token)
    if hit and hit[0] > now:
        return hit[1]
    cfg = Config.from_dict({**server_config().to_dict(), "token": token})
    client = await _open(cfg)
    try:
        info = await client.get("/api/agent-tokens/whoami")
    finally:
        await client.__aexit__(None, None, None)
    if info.get("via") != "agent-token":
        raise H3Error(401, "MCP HTTP 入口只接受 agent token（h3_at_…），不接受浏览器登录用的 JWT；用 h3 token create 发一把")
    _verify_cache[token] = (now + VERIFY_TTL_S, info)
    return info


__all__ = [
    "H3Error",
    "close_clients",
    "current_token",
    "get_client",
    "server_config",
    "set_server_config",
    "verify_token",
]
