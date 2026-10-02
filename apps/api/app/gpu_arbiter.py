"""单卡仲裁：一张 GPU 上，文本模型与图像/视频模型不能同时驻留。

本机实测出来的约束（不是推测）：llama-server 常驻 17.5 GB（27B NVFP4 + 128k 上下文），
Qwen-Image 2.1 栈要 23.6 GB（文本编码器 16.7 + DiT 6.9）。两者同时在卡上时互相换页 ——
一次带参考图的出图 25 分钟没走完一步，ComfyUI 连 HTTP 都不响应，
而 llama-server 的 /slots 显示 n_prompt_tokens_processed=0，两边一起停在原地。

所以：本地实例要开跑图/视频前，先把文本模型进程让开（记下完整命令行 → 结束 → 队列空了原样拉起）；
文本模型开跑前，先让 ComfyUI 把权重卸掉（/free）。

三条边界：
  1. 只动「认得出的本地推理端口进程」，而且必须先拿到可原样复现的命令行；拿不到就不动它，只告警。
  2. 每次停/起都写审计与日志，用户看得见是谁动的、原命令是什么。
  3. H3_GPU_ARBITER=off 一键关掉整条链路。
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from typing import Any

from .logging_setup import get_logger, redact
from .net import async_client

log = get_logger("gpu.arbiter")

# 只肯停这些可执行文件：见过推理端口就停会误伤用户的其它服务
SERVER_HINTS = ("llama-server", "llama.cpp", "ollama", "lm-studio", "lmstudio", "vllm", "llama-bench")

_CREATE_FLAGS = 0x00000008 | 0x00000200 | 0x0000000008000000  # DETACHED | NEW_PROCESS_GROUP | NO_WINDOW


async def configured_local_ports() -> list[int]:
    """应用自己配置的本地文本后端端口。

    绝不按候选端口扫：实测那样会把用户开着但本项目没在用的 Ollama 停掉。
    """
    from urllib.parse import urlsplit

    from sqlalchemy import text

    from .db import session_factory

    try:
        async with session_factory()() as s:
            rows = (await s.execute(text("SELECT base_url FROM llm_backends WHERE scope='local'"))).mappings().all()
    except Exception as exc:
        log.debug("读不到本地文本后端配置：%s", redact(str(exc))[:120])
        return []
    ports: list[int] = []
    for r in rows:
        u = urlsplit(str(r["base_url"]))
        if u.hostname not in {"127.0.0.1", "localhost", "::1"}:
            continue
        ports.append(int(u.port or (443 if u.scheme == "https" else 80)))
    return sorted(set(ports))


def _run(cmd: list[str], timeout: float = 20.0) -> str:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace")
        return (out.stdout or "") + (out.stderr or "")
    except Exception as exc:
        log.warning("执行 %s 失败：%s", cmd[0], redact(str(exc))[:160])
        return ""


def pid_on_port(port: int) -> int | None:
    """从 netstat 里找正在监听这个端口的进程。不引 psutil：本机是 Windows，够用了。"""
    text = _run(["netstat", "-ano"])
    for line in text.splitlines():
        if f":{port}" not in line or "LISTENING" not in line.upper():
            continue
        parts = line.split()
        local = next((p for p in parts if f":{port}" in p), "")
        if not local.endswith(f":{port}"):
            continue
        try:
            return int(parts[-1])
        except (ValueError, IndexError):
            continue
    return None


def cmdline_of(pid: int) -> str | None:
    if sys.platform != "win32":
        out = _run(["ps", "-p", str(pid), "-o", "args="])
        return out.strip() or None
    ps = f"(Get-CimInstance Win32_Process -Filter 'ProcessId={pid}').CommandLine"
    out = _run(["powershell", "-NoProfile", "-Command", ps])
    return out.strip() or None


def _wait_port_gone(port: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pid_on_port(port) is None:
            return True
        time.sleep(0.4)
    return False


async def _port_alive(port: int) -> bool:
    try:
        async with async_client(f"http://127.0.0.1:{port}/v1/models", timeout=2.0) as c:
            await c.get(f"http://127.0.0.1:{port}/v1/models")
        return True
    except Exception:
        return False


class GpuArbiter:
    """文本模型进程的让卡/恢复。同一时刻只记一个被停掉的进程。"""

    def __init__(self, *, enabled: bool = True) -> None:
        self.enabled = enabled
        self.stopped: dict[str, Any] | None = None
        self.last_error: str | None = None

    async def find_running(self) -> tuple[int, int] | None:
        """返回 (端口, PID)。只看配置里的本地文本后端。"""
        for port in await configured_local_ports():
            pid = await asyncio.to_thread(pid_on_port, port)
            if pid:
                return port, pid
        return None

    async def yield_llm(self, *, reason: str = "") -> dict[str, Any] | None:
        """结束文本模型进程，把显存让给本地图/视频任务。返回被停掉的东西。"""
        if not self.enabled:
            return None
        if self.stopped:
            return self.stopped  # 已经让开了，别重复停
        found = await self.find_running()
        if found is None:
            return None
        port, pid = found
        cmd = await asyncio.to_thread(cmdline_of, pid)
        if not cmd:
            # 拿不到原命令就绝不能停：停完拉不回来，用户失去的是整个文本链路
            self.last_error = f"端口 {port} 上的进程 {pid} 读不到命令行，拒绝停它"
            log.warning("%s", self.last_error)
            return None
        if not any(h in cmd.lower() for h in SERVER_HINTS):
            # 端口被别的服务占了（本机 8080 就曾被第三方应用占着）。停它会伤到无关进程。
            self.last_error = f"端口 {port} 上的进程不是本地推理服务，拒绝停它：{cmd[:120]}"
            log.warning("%s", self.last_error)
            return None
        # macOS/Linux 上没有 taskkill；先 TERM 让它自己落盘，等不到再 KILL
        killed = await asyncio.to_thread(self._terminate, pid)
        gone = await asyncio.to_thread(_wait_port_gone, port, 15.0)
        self.stopped = {"port": port, "pid": pid, "cmdline": cmd, "at": time.time(), "reason": reason, "killed": killed and gone}
        if not gone:
            self.last_error = f"进程 {pid} 已发出结束命令，但端口 {port} 还在监听"
            log.warning("%s", self.last_error)
        else:
            self.last_error = None
            log.info("已让出显存：停掉端口 %d 的文本模型进程（%s）", port, reason or "本地生成任务")
        await self._persist()
        return self.stopped

    async def restore_llm(self) -> bool:
        """原样拉起刚才停掉的进程。"""
        if not self.enabled or not self.stopped:
            return False
        rec, self.stopped = self.stopped, None
        port = int(rec["port"])
        if await _port_alive(port):
            await self._persist()
            return True
        cmd = str(rec.get("cmdline") or "")
        if not cmd:
            self.last_error = "没有可复现的命令行，文本模型需要手动重启"
            return False
        ok = await asyncio.to_thread(self._spawn, cmd)
        # llama-server 装 17.5GB 权重不是瞬间的事，给足时间
        for _ in range(90):
            if await _port_alive(port):
                self.last_error = None
                log.info("文本模型已恢复：端口 %d", port)
                await self._persist()
                return True
            await asyncio.sleep(1.0)
        self.last_error = f"已拉起但端口 {port} 60 秒内没有起来，请手动检查"
        log.warning("%s", self.last_error)
        await self._persist()
        return False

    @staticmethod
    def _terminate(pid: int) -> bool:
        """结束进程。Windows 只有强制那一条路；POSIX 先 TERM，一秒钟后还在才 KILL。

        调用方还会再等端口消失（`_wait_port_gone`），这里不重复判「真的没了」。
        """
        if sys.platform == "win32":
            return bool(_run(["taskkill", "/PID", str(pid), "/F"]))
        import signal

        try:
            os.kill(pid, signal.SIGTERM)
        except OSError as exc:
            log.warning("停进程 %s 失败：%s", pid, exc)
            return False
        time.sleep(1.0)
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError as exc:
            log.warning("强杀进程 %s 失败：%s", pid, exc)
            return False
        return True

    @staticmethod
    def _spawn(cmdline: str) -> bool:
        try:
            if sys.platform == "win32":
                subprocess.Popen(cmdline, shell=False, creationflags=_CREATE_FLAGS)  # noqa: S603
            else:
                subprocess.Popen(cmdline, shell=True)  # noqa: S602
            return True
        except Exception as exc:
            log.warning("拉起文本模型失败：%s", redact(str(exc))[:200])
            return False

    async def status(self) -> dict[str, Any]:
        running = await self.find_running()
        return {
            "enabled": self.enabled,
            "llmRunning": bool(running),
            "llmPort": running[0] if running else None,
            "llmPid": running[1] if running else None,
            "yielded": bool(self.stopped),
            "pendingRestore": self.stopped if self.stopped and not running else None,
            "lastError": self.last_error,
        }

    # ── 断电/重启后还能找回原命令 ──

    async def _persist(self) -> None:
        from sqlalchemy import text

        from .db import session_factory

        try:
            async with session_factory()() as s:
                await s.execute(
                    text(
                        """
                        INSERT INTO app_settings(key, value) VALUES ('gpu_arbiter', CAST(:v AS jsonb))
                        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
                        """
                    ),
                    {"v": _dumps({"stopped": self.stopped, "enabled": self.enabled})},
                )
                await s.commit()
        except Exception as exc:
            log.debug("仲裁状态落库失败（不影响生成）：%s", redact(str(exc))[:160])

    async def load_persisted(self) -> None:
        from sqlalchemy import text

        from .db import session_factory

        try:
            async with session_factory()() as s:
                row = (await s.execute(text("SELECT value FROM app_settings WHERE key='gpu_arbiter'"))).first()
        except Exception:
            return
        if not row:
            return
        import json

        value = row[0] if not isinstance(row[0], str) else json.loads(row[0])
        stopped = (value or {}).get("stopped")
        if stopped and pid_on_port(int(stopped.get("port") or 0)) is None:
            # 后端重启前留下的「被我停掉且还没拉起来」的进程：记下来，UI 上给一个恢复按钮
            self.stopped = stopped
            log.warning("发现上次让卡后未恢复的文本模型进程（端口 %s），可在 系统设置 里恢复", stopped.get("port"))


def _dumps(obj: Any) -> str:
    import json

    return json.dumps(obj, ensure_ascii=False)


def env_enabled(default: bool = True) -> bool:
    raw = os.environ.get("H3_GPU_ARBITER", "").strip().lower()
    if raw in {"0", "off", "false", "no"}:
        return False
    if raw in {"1", "on", "true", "yes"}:
        return True
    return default
