"""本机进程与端口的最小工具集。

`kill` 一律带 `expect`：先确认那个 PID 的命令行里认得出我们的特征串才动手。
理由不是抽象洁癖 —— 这台机器上发生过「按端口扫进程、误停了用户的 Ollama」，
GPU 仲裁器后来就是靠「只停命令行认得出的推理程序」收的口（app/gpu_arbiter.py）。
PID 会被复用，几分钟前查到的 PID 现在可能属于别人的进程。
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        out = _powershell(
            f"(Get-Process -Id {int(pid)} -ErrorAction SilentlyContinue) -ne $null"
        )
        return out.strip().lower() == "true"
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def cmdline(pid: int) -> str:
    if sys.platform == "win32":
        script = (
            f"$p=Get-CimInstance Win32_Process -Filter 'ProcessId={int(pid)}' -ErrorAction SilentlyContinue;"
            "if($p){$p.CommandLine}"
        )
        return _powershell(script).strip()
    try:
        return Path(f"/proc/{int(pid)}/cmdline").read_bytes().decode("utf-8", "replace").replace("\0", " ").strip()
    except Exception:
        return ""


def _powershell(script: str) -> str:
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True,
            text=True,
            timeout=20,
            encoding="utf-8",
            errors="replace",
        )
        return out.stdout or ""
    except Exception:
        return ""


def kill(pid: int, *, expect: str) -> dict[str, str | int | bool]:
    """只停在命令行里认得出的进程。认不出就不停，交回人工判断。"""
    if not pid_alive(pid):
        return {"pid": pid, "killed": False, "reason": "进程已不在"}
    cl = cmdline(pid)
    if expect and expect not in cl:
        return {
            "pid": pid,
            "killed": False,
            "reason": f"命令行里没有 {expect!r}，PID 可能已被复用（实际是：{cl[:160] or '未知'}）",
        }
    if sys.platform == "win32":
        # taskkill 在中文 Windows 上回 GBK；UTF-8 模式下 text=True 会直接把读线程炸掉
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, encoding="utf-8", errors="replace")
    else:
        subprocess.run(["kill", "-TERM", str(pid)], capture_output=True)
        time.sleep(1.0)
        if pid_alive(pid):
            subprocess.run(["kill", "-KILL", str(pid)], capture_output=True)
    return {"pid": pid, "killed": not pid_alive(pid), "cmdline": cl[:200]}


def spawn_detached(argv: list[str], *, log_path: Path, cwd: Path, env: dict[str, str] | None = None) -> int:
    """拉起一个脱离当前终端的后台进程，返回它的 PID。"""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log = log_path.open("ab")
    popen_kwargs: dict[str, object] = {
        "cwd": str(cwd),
        "stdout": log,
        "stderr": subprocess.STDOUT,
        "stdin": subprocess.DEVNULL,
        "env": {**os.environ, **(env or {})},
    }
    if sys.platform == "win32":
        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        popen_kwargs["creationflags"] = 0x00000008 | 0x00000200
    else:
        popen_kwargs["start_new_session"] = True
    proc = subprocess.Popen(argv, **popen_kwargs)  # noqa: S603 —— 参数由我们自己拼，不接外部输入
    log.close()
    return proc.pid


def port_listening(port: int, *, host: str = "127.0.0.1", timeout: float = 0.6) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def wait_port(port: int, *, host: str = "127.0.0.1", timeout: float = 20.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if port_listening(port, host=host):
            return True
        time.sleep(0.4)
    return False


def http_get_json(url: str, *, timeout: float = 3.0):
    import httpx

    try:
        r = httpx.get(url, timeout=timeout, trust_env=False)
        return r.json() if r.status_code < 500 else None
    except Exception:
        return None
