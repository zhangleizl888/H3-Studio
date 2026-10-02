"""CLI 与 MCP 共用的后端 HTTP 客户端。

只走 HTTP，不在进程里 import `app.main`：后端那个进程带着派发循环和 GPU 仲裁，
再来一个实例就是两个调度器抢同一张卡（这条在 PLAN 与 gpu_arbiter 的注释里反复强调过）。
CLI/MCP 永远是客户端，后端是唯一事实源 —— 顺带让「对外暴露」只是换个监听地址。
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import time
from pathlib import Path
from typing import Any, Mapping

import httpx

from .store import Config


class H3Error(RuntimeError):
    def __init__(self, status: int, detail: str, *, url: str | None = None, body: Any = None) -> None:
        super().__init__(f"[{status}] {detail}" + (f"（{url}）" if url else ""))
        self.status = status
        self.detail = detail
        self.url = url
        self.body = body


class H3Client:
    """薄封装：认证、错误翻译、JSON 进出。不解释业务字段。"""

    def __init__(self, cfg: Config, *, timeout: float = 60.0, on_config_change=None) -> None:
        self.cfg = cfg
        self._timeout = timeout
        self._on_config_change = on_config_change
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "H3Client":
        self._client = httpx.AsyncClient(timeout=self._timeout, trust_env=False)
        return self

    async def __aexit__(self, *exc) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def server(self) -> str:
        return self.cfg.server

    # ---- 认证 ----------------------------------------------------------------

    def _auth_headers(self) -> dict[str, str]:
        if self.cfg.token:
            return {"Authorization": f"Bearer {self.cfg.token}"}
        access = self.cfg.auth.get("access")
        if access:
            return {"Authorization": f"Bearer {access}"}
        return {}

    def _persist(self) -> None:
        if self._on_config_change is not None:
            self._on_config_change(self.cfg)

    async def login(self, username: str, password: str) -> dict[str, Any]:
        data = await self.request("POST", "/api/auth/login", json_body={"username": username, "password": password}, auth=False)
        self.cfg.auth = {
            "access": data["access"],
            "refresh": data["refresh"],
            "expiresAt": time.time() + int(data.get("expiresIn", 900)),
            "user": data.get("user"),
        }
        self._persist()
        return data

    async def _refresh_jwt(self) -> bool:
        refresh = self.cfg.auth.get("refresh")
        if not refresh or self.cfg.token:
            return False
        try:
            data = await self.request(
                "POST", "/api/auth/refresh", json_body={"refresh": refresh}, auth=False
            )
        except H3Error:
            return False
        self.cfg.auth = {
            "access": data["access"],
            "refresh": data["refresh"],
            "expiresAt": time.time() + int(data.get("expiresIn", 900)),
            "user": data.get("user") or self.cfg.auth.get("user"),
        }
        self._persist()
        return True

    # ---- 请求 ----------------------------------------------------------------

    async def request(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = None,
        params: Mapping[str, Any] | None = None,
        files: Any = None,
        data: Any = None,
        auth: bool = True,
    ) -> Any:
        client = self._client
        if client is None:
            raise RuntimeError("H3Client 要用 async with（或先 await _ensure()）")
        if not path.startswith("/"):
            # Git Bash 会把 /healthz 这类参数重写成 C:/Program Files/Git/healthz，
            # 直接送进 httpx 只会得到一句看不懂的 "missing an http:// protocol"
            raise ValueError(f"路径要以 / 开头，收到 {path!r}（在 Git Bash/MSYS 里请给参数加引号，或设 MSYS_NO_PATHCONV=1）")
        url = self.server + path
        clean_params = {k: v for k, v in (params or {}).items() if v is not None}
        headers = self._auth_headers() if auth else {}

        resp = await client.request(method, url, json=json_body, params=clean_params, files=files, data=data, headers=headers)
        if resp.status_code == 401 and auth and not self.cfg.token and self.cfg.auth.get("refresh"):
            if await self._refresh_jwt():
                headers = self._auth_headers()
                resp = await client.request(
                    method, url, json=json_body, params=clean_params, files=files, data=data, headers=headers
                )
        if resp.status_code >= 400:
            raise self._error(resp, url)
        if resp.status_code == 204 or not resp.content:
            return None
        ctype = resp.headers.get("content-type", "")
        if "json" in ctype:
            return resp.json()
        return resp.text

    def _error(self, resp: httpx.Response, url: str) -> H3Error:
        detail = ""
        body: Any = None
        try:
            body = resp.json()
            d = body.get("detail", body) if isinstance(body, dict) else body
            if isinstance(d, (dict, list)):
                detail = json.dumps(d, ensure_ascii=False)
            else:
                detail = str(d)
        except Exception:
            detail = (resp.text or "").strip()[:400] or resp.reason_phrase
        return H3Error(resp.status_code, detail, url=url, body=body)

    async def get(self, path: str, **kw) -> Any:
        return await self.request("GET", path, **kw)

    async def post(self, path: str, json_body: Any = None, **kw) -> Any:
        return await self.request("POST", path, json_body=json_body, **kw)

    async def patch(self, path: str, json_body: Any = None, **kw) -> Any:
        return await self.request("PATCH", path, json_body=json_body, **kw)

    async def put(self, path: str, json_body: Any = None, **kw) -> Any:
        return await self.request("PUT", path, json_body=json_body, **kw)

    async def delete(self, path: str, **kw) -> Any:
        return await self.request("DELETE", path, **kw)

    async def upload(self, path: str, file: Path, *, fields: Mapping[str, Any] | None = None, extra_files: Mapping[str, Path] | None = None) -> Any:
        """multipart 上传。`fields` 会按表单字段发出去（后端有些路由用 Form 取值）。"""
        opened = []
        try:
            fh = file.open("rb")
            opened.append(fh)
            files = {"file": (file.name, fh)}
            for name, p in (extra_files or {}).items():
                h = p.open("rb")
                opened.append(h)
                files[name] = (p.name, h)
            return await self.request("POST", path, files=files, data=dict(fields or {}))
        finally:
            for h in opened:
                h.close()

    async def download(self, path: str, dest: Path, *, params: Mapping[str, Any] | None = None) -> Path:
        client = self._client
        assert client is not None
        url = self.server + path
        async with client.stream("GET", url, params={k: v for k, v in (params or {}).items() if v is not None}, headers=self._auth_headers()) as resp:
            if resp.status_code >= 400:
                await resp.aread()
                raise self._error(resp, url)
            dest.parent.mkdir(parents=True, exist_ok=True)
            with dest.open("wb") as fh:
                async for chunk in resp.aiter_bytes(1 << 16):
                    fh.write(chunk)
        return dest

    # ---- 常用组合 ------------------------------------------------------------

    async def wait_for_job(
        self, job_id: str, *, timeout_s: float = 1800.0, interval_s: float = 3.0, on_update=None
    ) -> dict[str, Any]:
        """轮询到终态。用 SSE 更漂亮，但那条流只是观察（任务收口在派发循环），
        CLI/MCP 要的是「这个任务现在是成是败」，直接问状态最可靠。"""
        deadline = time.monotonic() + timeout_s
        last: tuple[str, str] | None = None
        while True:
            state = await self.get(f"/api/jobs/{job_id}")
            status = str(state.get("state") or "")
            prog = state.get("progress") or {}
            mark = f"{prog.get('value')}/{prog.get('max')}@{prog.get('stage') or prog.get('node') or ''}"
            if (status, mark) != last:
                last = (status, mark)
                if on_update:
                    on_update(state)
            if state.get("terminal") or status in {"succeeded", "failed", "canceled"}:
                return state
            if time.monotonic() > deadline:
                raise TimeoutError(f"等 /api/jobs/{job_id} 超时（{timeout_s:.0f}s，最后状态 {status or '未知'}）")
            await asyncio.sleep(interval_s)


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
