"""`h3 setup`：从零到「智能体能连上这台工作台」。

六步，全部可重跑（幂等）：环境 → 依赖 → 数据库 → 迁移 → 后端进程 → agent token → 客户端配置。
每步只报「做了什么 / 为什么停在这」，失败就停，不带着半套往下走。

`--check-only` 是这条链的只读版（等同 h3 doctor）。真正会动手的只有依赖安装、迁移、
起进程、发 token、改客户端配置这五类，所以每一步都单独打印出来，跑完能在
`~/.h3/setup.log` 里回看。
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import mcpconfig, proc
from .client import H3Client, H3Error
from .mcpconfig import api_root
from .store import Config, config_path, load, save

REPO_ROOT = api_root().parents[1]
ENV_FILE = api_root() / ".env"
TOOLING_PY = REPO_ROOT / ".tooling-pg" / "Scripts" / "python.exe"


@dataclass
class Step:
    name: str
    ok: bool
    detail: str
    changed: bool = False


class SetupAbort(RuntimeError):
    pass


def _run(argv: list[str], *, cwd: Path | None = None, timeout: int = 600, env: dict[str, str] | None = None) -> tuple[int, str]:
    try:
        out = subprocess.run(
            argv,
            cwd=str(cwd or api_root()),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env={**__import__("os").environ, **(env or {})},
        )
    except Exception as exc:
        return 127, str(exc)
    return out.returncode, (out.stdout or "") + (out.stderr or "")


def _env_value(key: str) -> str | None:
    if not ENV_FILE.exists():
        return None
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        m = re.match(rf"^\s*{re.escape(key)}\s*=\s*(.+?)\s*$", line)
        if m:
            return m.group(1)
    return None


# ---------------------------------------------------------------- 各步骤


def step_env(cfg: Config, *, dry: bool) -> Step:
    missing = [p for p in (api_root() / "app", ENV_FILE) if not p.exists()]
    if missing:
        return Step("仓库结构", False, f"缺 {', '.join(str(m) for m in missing)}；请在 F:/H3 这份仓库里跑 h3", False)
    return Step("仓库结构", True, f"apps/api={api_root()}，后端指向 {cfg.server}")


def step_python(cfg: Config, *, dry: bool) -> Step:
    venv_python = api_root() / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    if not venv_python.exists():
        if dry:
            return Step("Python 环境", False, f"没有 {venv_python}；将创建 venv 并安装依赖", True)
        code, out = _run([sys.executable, "-m", "venv", str(api_root() / ".venv")])
        if code != 0:
            raise SetupAbort(f"建 venv 失败：{out[-400:]}")
    return Step("Python 环境", True, str(venv_python))


def step_deps(cfg: Config, *, dry: bool) -> Step:
    need = {"fastapi": "fastapi", "uvicorn": "uvicorn[standard]", "sqlalchemy": "sqlalchemy[asyncio]", "asyncpg": "asyncpg", "httpx": "httpx", "mcp": "mcp>=2,<3"}
    missing = [pkg for mod, pkg in need.items() if importlib.util.find_spec(mod) is None]
    exe = "Scripts" if sys.platform == "win32" else "bin"
    h3 = api_root() / ".venv" / exe / ("h3.exe" if sys.platform == "win32" else "h3")
    if not h3.exists():
        missing.append("-e .（h3 命令本体）")
    if not missing:
        return Step("依赖与 h3 命令", True, f"已齐（含 MCP SDK）：{', '.join(sorted(need))}")
    if dry:
        return Step("依赖与 h3 命令", False, f"将安装：{', '.join(missing)}", True)
    if missing[-1].startswith("-e ."):
        code, out = _run([sys.executable, "-m", "pip", "install", "-e", str(api_root())], timeout=900)
        if code != 0:
            raise SetupAbort(f"安装 h3 命令失败：{out[-600:]}")
        missing = missing[:-1]
    if missing:
        code, out = _run([sys.executable, "-m", "pip", "install", *missing], timeout=900)
        if code != 0:
            raise SetupAbort(f"安装依赖失败：{out[-600:]}")
    return Step("依赖与 h3 命令", True, f"已安装：{', '.join(missing)}；h3 → {h3}", changed=True)


def step_database(cfg: Config, *, dry: bool) -> Step:
    url = _env_value("H3_DATABASE_URL")
    if url:
        host_port = re.search(r"@([\w.-]+):(\d+)", url)
        if host_port and proc.port_listening(int(host_port.group(2)), host=host_port.group(1)):
            return Step("数据库", True, f"{host_port.group(1)}:{host_port.group(2)} 在线（.env 里那把连接串）")
    if not TOOLING_PY.exists():
        raise SetupAbort(f"数据库没在跑，且没找到 {TOOLING_PY}（内嵌 PG 的工具环境）。先起一个 PostgreSQL 并把 H3_DATABASE_URL 写进 {ENV_FILE}")
    if dry:
        return Step("数据库", False, "将用 .tooling-pg 启内嵌 PostgreSQL 并把连接串写进 .env", True)
    code, out = _run([str(TOOLING_PY), str(api_root() / "scripts" / "pg.py"), "boot"], timeout=300)
    if code != 0:
        raise SetupAbort(f"内嵌 PostgreSQL 启动失败：{out[-600:]}")
    new = _env_value("H3_DATABASE_URL") or "?"
    return Step("数据库", True, f"已启动并写入 .env：{new}", changed=True)


def step_migrate(cfg: Config, *, dry: bool) -> Step:
    alembic = api_root() / ".venv" / ("Scripts/alembic.exe" if sys.platform == "win32" else "bin/alembic")
    if not alembic.exists():
        raise SetupAbort(f"没有 {alembic}；先装依赖")
    if dry:
        # 迁移会建表，dry-run 绝不能碰库
        return Step("迁移", True, "（dry-run）将执行 alembic upgrade head")
    code, out = _run([str(alembic), "upgrade", "head"], cwd=api_root(), timeout=300)
    if code != 0:
        raise SetupAbort(f"数据库迁移失败：{out[-600:]}")
    return Step("迁移", True, "alembic upgrade head 完成（表结构已含 api_tokens）")


def step_backend(cfg: Config, *, dry: bool, restart: bool) -> Step:
    health = proc.http_get_json(cfg.server + "/healthz")
    if health and not restart:
        return Step("后端", True, f"{cfg.server} 在线；实例 {health.get('instances')} 台；库 {health.get('database')}")
    if dry:
        return Step("后端", False, f"将在 {cfg.server} 起后端（脱离终端，日志 ~/.h3/logs/api.log）", True)
    host, _, port = _host_port(cfg.server)
    argv = [sys.executable, "-X", "utf8", "-m", "uvicorn", "app.main:app", "--host", host, "--port", str(port)]
    pid = proc.spawn_detached(argv, log_path=_log_dir() / "api.log", cwd=api_root())
    if not proc.wait_port(int(port), host="127.0.0.1" if host in {"0.0.0.0", "::"} else host, timeout=45):
        raise SetupAbort(f"后端 45 秒内没起来，看日志 {_log_dir() / 'api.log'}")
    return Step("后端", True, f"已起（PID {pid}）：{cfg.server}", changed=True)


def _host_port(server: str) -> tuple[str, str, int]:
    m = re.match(r"https?://([\w.-]+):?(\d*)", server)
    if not m:
        return ("127.0.0.1", "", 8788)
    return (m.group(1), m.group(1), int(m.group(2) or 8788))


def _log_dir() -> Path:
    from .store import config_dir

    d = config_dir() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


async def _mint_token(cfg: Config, *, name: str, scopes: list[str], login: tuple[str, str] | None, ttl_days: int | None) -> dict[str, Any]:
    """发一把 agent token：优先用已有凭据（含 admin），否则用刚登录的会话。"""
    async with H3Client(cfg, timeout=60.0) as client:
        try:
            if login:
                await client.login(*login)
            return await client.post("/api/agent-tokens", {"name": name, "scopes": scopes, "ttlDays": ttl_days})
        except H3Error as exc:
            if exc.status not in (401, 403):
                raise SetupAbort(f"发 token 失败：{exc.detail}") from exc
            needed = await client.get("/api/auth/setup-required", auth=False)
            if isinstance(needed, dict) and needed.get("needed"):
                raise SetupAbort("库里还没有任何账号：先跑 h3 bootstrap（或带 --username/--password 重跑，让它用这个账号登录）") from exc
            raise SetupAbort(
                f"发 token 被拒（{exc.status} {exc.detail}）。用一个 admin 账号跑：h3 login -u admin 之后重试，或加 --username/--password"
            ) from exc


def step_token(cfg: Config, *, dry: bool, name: str, scopes: list[str], login: tuple[str, str] | None, force: bool, ttl_days: int | None) -> Step:
    if cfg.token and not force:
        return Step("agent token", True, f"已有一把（{cfg.token[:9]}…{cfg.token[-4:]}），跳过；换新的用 --force")
    if dry:
        return Step("agent token", False, f"将发放 scope={'/'.join(scopes)} 的钥匙并写进 {config_path()}", True)
    created = __import__("asyncio").run(_mint_token(cfg, name=name, scopes=scopes, login=login, ttl_days=ttl_days))
    cfg.token = created["token"]
    save(cfg)
    return Step(
        "agent token",
        True,
        f"已发 {created['name']}（scope={'/'.join(created['scopes'])}），明文只出现这一次，已写进 {config_path()}",
        changed=True,
    )


def step_clients(cfg: Config, *, dry: bool, which: list[str], qoder_scope: str, url: str | None) -> Step:
    repo = REPO_ROOT
    chosen: list[mcpconfig.Target] = []
    for w in which:
        if w == "auto":
            chosen += mcpconfig.auto_detected(repo, qoder_scope=qoder_scope) or [mcpconfig.qoder_local(repo)]
        elif w == "qoder":
            chosen.append({"user": mcpconfig.qoder_user(), "project": mcpconfig.qoder_project(repo)}.get(qoder_scope, mcpconfig.qoder_local(repo)))
        elif w == "claude":
            chosen.append(mcpconfig.claude_desktop())
        elif w == "cursor":
            chosen.append(mcpconfig.cursor())
        elif w == "windsurf":
            chosen.append(mcpconfig.windsurf())
    seen: set[str] = set()
    targets = [t for t in chosen if not (t.id in seen or seen.add(t.id))]
    if not targets:
        return Step("客户端配置", False, "没有可写的客户端（--client 指定一个）", False)
    entry: dict[str, Any] = mcpconfig.http_entry(url, cfg.token) if url else mcpconfig.stdio_entry()
    lines = []
    for t in targets:
        if dry:
            lines.append(f"{t.id}: 将写入 {t.path}")
            continue
        res = mcpconfig.install(t, entry)
        lines.append(f"{t.id}: {res.get('action') or res.get('reason')} → {res['path']}" + (f"（备份 {res['backup']}）" if res.get("backup") else ""))
    if dry:
        return Step("客户端配置", True, "；".join(lines), changed=False)
    return Step("客户端配置", True, "；".join(lines), changed=True)


def run(cfg: Config | None = None, *, dry: bool = False, scopes: list[str] | None = None, login: tuple[str, str] | None = None, clients: list[str] | None = None, qoder_scope: str = "local", url: str | None = None, force_token: bool = False, restart_backend: bool = False, ttl_days: int | None = None) -> int:
    cfg = cfg or load()
    steps: list[Step] = []
    print(f"h3 setup —— 把这台工作台接到智能体上{'（dry-run，只报告不动手）' if dry else ''}")
    print(f"仓库 {api_root()}\n配置 {config_path()}\n")
    token_scopes = scopes or ["read", "dispatch"]
    try:
        steps.append(step_env(cfg, dry=dry))
        _print(steps[-1])
        steps.append(step_python(cfg, dry=dry))
        _print(steps[-1])
        steps.append(step_deps(cfg, dry=dry))
        _print(steps[-1])
        steps.append(step_database(cfg, dry=dry))
        _print(steps[-1])
        steps.append(step_migrate(cfg, dry=dry))
        _print(steps[-1])
        steps.append(step_backend(cfg, dry=dry, restart=restart_backend))
        _print(steps[-1])
        steps.append(step_token(cfg, dry=dry, name=f"agent@{__import__('socket').gethostname()}", scopes=token_scopes, login=login, force=force_token, ttl_days=ttl_days))
        _print(steps[-1])
        steps.append(step_clients(cfg, dry=dry, which=clients or ["auto"], qoder_scope=qoder_scope, url=url))
        _print(steps[-1])
    except SetupAbort as exc:
        print(f"\n✗ 停下：{exc}")
        print("修好这条再重跑 h3 setup（所有步骤都是幂等的）")
        return 1

    print("\n下一步：")
    print("  h3 doctor      # 体检，确认后端与实例都对")
    print("  h3 status      # 看队列、显存、实例")
    print("  h3 mcp test    # 真跑一次 MCP 握手并列出工具")
    print("  客户端里 /mcp reload（Qoder）或重启客户端，工具就出现了")
    if dry:
        print("\n这是 dry-run，上面写「将」的动作都没发生。")
    return 0


def _print(s: Step) -> None:
    mark = "✓" if s.ok else "·"
    done = "（已改动）" if s.changed else ""
    print(f"{mark} {s.name}{done}：{s.detail}")
