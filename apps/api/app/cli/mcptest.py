"""`h3 mcp test`：真的起一个 MCP 子进程走一遍握手。

不这么做的话，「配置写好了」和「智能体能用」之间差着一整个运行时：命令找不到、
Python 环境不对、后端没起、token 过期，任何一种都会让客户端只显示一行「failed」。
这里把 initialize → tools/list → tools/call 三步跑实，报错直接指出是哪一步。
"""

from __future__ import annotations

import json
import queue
import subprocess
import sys
import threading
from typing import Any

from .mcpconfig import api_root
from .store import Config

CALL_TIMEOUT_S = 60.0


def _reader(pipe, out: "queue.Queue[str | None]") -> None:
    for line in iter(pipe.readline, b""):
        if line.strip():
            out.put(line.decode("utf-8", "replace").rstrip("\r\n"))
    out.put(None)


class StdioClient:
    def __init__(self, proc: subprocess.Popen) -> None:
        self.proc = proc
        self.q: queue.Queue[str | None] = queue.Queue()
        self.thread = threading.Thread(target=_reader, args=(proc.stdout, self.q), daemon=True)
        self.thread.start()
        self._id = 0

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def call(self, method: str, params: dict[str, Any] | None = None, *, timeout: float = 30.0) -> Any:
        self._id += 1
        rid = self._id
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
        deadline = timeout
        while True:
            try:
                line = self.q.get(timeout=deadline)
            except queue.Empty:
                raise TimeoutError(f"{deadline:.0f} 秒内没等到 {method} 的回包")
            if line is None:
                raise RuntimeError("MCP 子进程已退出：" + (self.proc.stderr.read().decode("utf-8", "replace")[-400:] if self.proc.stderr else "?"))
            msg = json.loads(line)
            if msg.get("id") != rid:
                continue
            if "error" in msg:
                raise RuntimeError(f"{method} 失败：{json.dumps(msg['error'], ensure_ascii=False)}")
            return msg.get("result")

    def _send(self, payload: dict[str, Any]) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
        self.proc.stdin.flush()

    def close(self) -> None:
        try:
            self.proc.terminate()
        except Exception:
            pass


def handshake_stdio(cfg: Config, *, call_tool: str = "health") -> tuple[int, str]:
    """返回 (退出码, 给人看的报告)。"""
    argv = [sys.executable, "-m", "app.cli", "mcp", "serve", "--transport", "stdio"]
    env = {"PYTHONPATH": str(api_root()), "H3_SERVER": cfg.server}
    if cfg.token:
        env["H3_TOKEN"] = cfg.token
    proc = subprocess.Popen(
        argv,
        cwd=str(api_root()),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**dict(__import__("os").environ), **env},
    )
    client = StdioClient(proc)
    lines: list[str] = []
    try:
        info = client.call(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "h3-cli-test", "version": "0.1.0"},
            },
            timeout=45,
        )
        server = (info or {}).get("serverInfo") or {}
        lines.append(f"✓ 握手成功：{server.get('name')} v{server.get('version')}，协议 {(info or {}).get('protocolVersion')}")
        client.notify("notifications/initialized")
        tools = client.call("tools/list").get("tools") or []
        lines.append(f"✓ 工具 {len(tools)} 个（后端 {cfg.server}）")
        if call_tool and call_tool != "none":
            try:
                res = client.call("tools/call", {"name": call_tool, "arguments": {}}, timeout=CALL_TIMEOUT_S)
                text = "".join(c.get("text", "") for c in (res or {}).get("content", []) if c.get("type") == "text")
                if (res or {}).get("isError"):
                    lines.append(f"✗ 调用 {call_tool} 返回错误：{text[:300]}")
                    return 1, "\n".join(lines)
                lines.append(f"✓ 调用 {call_tool} 成功：{text[:220]}{'…' if len(text) > 220 else ''}")
            except Exception as exc:
                lines.append(f"✗ 调用 {call_tool} 失败：{exc}")
                return 1, "\n".join(lines)
        lines.append("")
        lines.append("客户端里执行 /mcp reload（Qoder）或重启客户端即可看到这些工具。")
        return 0, "\n".join(lines)
    except Exception as exc:
        err = ""
        if proc.stderr is not None:
            try:
                err = proc.stderr.read().decode("utf-8", "replace")[-500:]
            except Exception:
                err = ""
        lines.append(f"✗ 握手失败：{exc}")
        if err:
            lines.append(f"子进程 stderr 尾部：\n{err}")
        lines.append(f"命令：{' '.join(argv)}（cwd={api_root()}）")
        return 1, "\n".join(lines)
    finally:
        client.close()
