"""两种 transport 的启动器。

stdio：智能体与本进程同机，凭据取 `~/.h3/config.json`（或 H3_TOKEN）。
streamable-http：可以跨机器/走隧道。每一条 HTTP 请求都先拿调用方自己的 agent token
向后端验一次身，之后工具调用也以那把钥匙去访问后端 —— 不是「MCP 进程一把万能钥匙，
谁连进来都用它」。这一步是整个对外暴露的承重墙，别为了省一次请求把它去掉。
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any, Iterable

import uvicorn
from mcp.server.streamable_http import TransportSecuritySettings

from ..cli.store import Config
from . import runtime
from .runtime import H3Error, close_clients, current_token, verify_token
from .server import VERSION, build_server

PING_PATH = "/mcp/health"


async def _json(send: Any, status: int, payload: dict[str, Any], extra_headers: list[tuple[bytes, bytes]] | None = None) -> None:
    import json

    body = json.dumps(payload, ensure_ascii=False).encode()
    headers = [
        (b"content-type", b"application/json; charset=utf-8"),
        (b"content-length", str(len(body)).encode()),
        *(extra_headers or []),
    ]
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


def _header(scope: dict, name: str) -> str | None:
    want = name.lower().encode()
    for k, v in scope.get("headers") or []:
        if k.lower() == want:
            return v.decode("latin-1")
    return None


class BearerGuard:
    """纯 ASGI 中间件：只拦 http，其余 scope（lifespan/websocket）原样透传。"""

    def __init__(self, app: Any, *, server: str) -> None:
        self.app = app
        self.server = server

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            return await self.app(scope, receive, send)

        path = scope.get("path", "")
        if path.rstrip("/") == PING_PATH.rstrip("/"):
            return await _json(send, 200, {"ok": True, "service": "h3-studio-mcp", "version": VERSION, "backend": self.server})

        auth = _header(scope, "authorization") or ""
        token = auth[7:].strip() if auth.startswith("Bearer ") else ""
        if not token:
            return await _json(
                send,
                401,
                {"error": "missing_bearer", "hint": f"带上 agent token：Authorization: Bearer h3_at_…（发钥匙：h3 token create --scope dispatch）后端地址 {self.server}"},
                extra_headers=[(b"www-authenticate", b'Bearer realm="h3-studio-mcp"')],
            )
        try:
            info = await verify_token(token)
        except H3Error as exc:
            return await _json(send, exc.status, {"error": "token_rejected", "detail": exc.detail})
        except Exception as exc:
            # 后端没起来 / 网络不通：说清楚是它不在线，别让客户端以为是 token 错
            return await _json(send, 502, {"error": "backend_unreachable", "detail": f"{exc}（{self.server}）", "hint": "先 h3 serve 或 h3 doctor"})

        holder = current_token.set(token)
        try:
            await self.app(scope, receive, send)
        finally:
            current_token.reset(holder)


def _allowed_hosts(host: str, port: int, extra: Iterable[str]) -> list[str]:
    """Host 头白名单。留空等于把环回也拒了（实测回 "Invalid Host header"）。"""
    base = {"127.0.0.1", "localhost", "[::1]"} if host in {"127.0.0.1", "localhost", "", "::1"} else {host}
    out = {f"{h}:{port}" for h in base}
    for item in extra:
        item = item.strip()
        if not item:
            continue
        tail = item.rsplit(":", 1)[-1]
        out.add(item if tail.isdigit() else f"{item}:{port}")
    return sorted(out)


def _build_http_app(cfg: Config, *, host: str, port: int, path: str, allow_hosts: Iterable[str], allow_all_hosts: bool) -> Any:
    mcp = build_server()
    hosts = _allowed_hosts(host, port, allow_hosts)
    security = TransportSecuritySettings(
        # DNS 重绑定保护：默认只放行本机地址。绑到局域网或走隧道时把域名加进白名单；
        # 只有明确要放开（--allow-all-hosts）才整个关掉，那时门禁只剩 Bearer token。
        enable_dns_rebinding_protection=not allow_all_hosts,
        allowed_hosts=hosts,
        allowed_origins=[],
    )
    app = mcp.streamable_http_app(streamable_http_path=path, transport_security=security)
    return BearerGuard(app, server=cfg.server)


def run_stdio(cfg: Config, *, log_level: str = "INFO") -> int:
    runtime.set_server_config(cfg)
    # stdio 的 stdout 是协议通道，日志只能走 stderr
    print(
        f"[h3-mcp] stdio 已就绪；后端 {cfg.server}；凭据："
        f"{'agent token' if cfg.token else ('JWT 会话' if cfg.auth.get('access') else '未配置（先 h3 login 或 h3 token create）')}",
        file=sys.stderr,
        flush=True,
    )

    async def _go() -> None:
        mcp = build_server()
        try:
            await mcp.run_stdio_async()
        finally:
            # 关闭 httpx 连接必须在它被创建的那个事件循环里做
            await close_clients()

    asyncio.run(_go())
    return 0


def run_http(cfg: Config, *, host: str, port: int, path: str = "/mcp", allow_hosts: Iterable[str] = (), allow_all_hosts: bool = False, log_level: str = "INFO") -> int:
    runtime.set_server_config(cfg)
    app = _build_http_app(cfg, host=host, port=port, path=path, allow_hosts=allow_hosts, allow_all_hosts=allow_all_hosts)
    shown = "127.0.0.1" if host in {"0.0.0.0", "::", ""} else host
    print(f"[h3-mcp] streamable-http 监听 http://{shown}:{port}{path}；后端 {cfg.server}；鉴权 Bearer agent token")
    print(f"[h3-mcp] 健康探针 http://{shown}:{port}{PING_PATH}（无需凭据，只回版本号）")
    print(f"[h3-mcp] Host 白名单：{', '.join(_allowed_hosts(host, port, allow_hosts))}")
    if host in {"0.0.0.0", "::"}:
        print("[h3-mcp] ⚠ 正在监听所有网卡：任何能连到这个端口的人都可以用手里的 token 驱动整套生成流程。")
    uvicorn.run(app, host=host, port=port, log_level=log_level.lower(), access_log=False)
    return 0
