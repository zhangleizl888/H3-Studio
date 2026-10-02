r"""桌面形态的入口：一个进程把「内嵌 PG → 建表 → HTTP 服务」全带起来。

为什么不复用 `h3 setup`：那条链是给开发机的（要 venv、要 alembic.exe、要把连接串写进
`apps/api/.env`）。装机用户手上只有一个 exe，所以这里做成一次进程内动作。

三条和开发态不同的硬规定：

1. **端口不固定**：8788 可能已被开发版占着，取不到就换随机端口，真值写进 `desktop.json`，
   Electron 壳读它而不是猜它。
2. **PG 口令随机**：开发用的 `h3/h3-dev-local-only` 绝不能进装机包，口令是本机生成的，
   存在数据目录里（600，仅 POSIX 有意义）。
3. **退出要把 PG 带下去**：桌面版没有「重启机器自动清理」这回事，postmaster 留在后台就是泄漏。
"""

from __future__ import annotations

import json
import os
import secrets
import socket
import sys
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Header, HTTPException

from . import runtime
from .logging_setup import get_logger

log = get_logger("desktop")

ROLE = "h3"
DATABASE = "h3studio"

_server: Any = None
_state: dict[str, Any] = {"token": None, "uvicorn_server": None, "port": None, "pgPort": None}


# ---------------------------------------------------------------- 凭据与连接串


def _credentials_path() -> Path:
    return runtime.data_root() / "pg-credentials.json"


def _credentials() -> dict[str, str]:
    """本机自持的 PG 口令。已存在就复用 —— 换口令等于让旧数据目录连不上。"""
    path = _credentials_path()
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if raw.get("password"):
                return {**{"role": ROLE, "database": DATABASE}, **raw}
        except Exception as exc:
            log.warning("%s 读不出来（%s），重新生成一份", path, exc)
    pw = secrets.token_urlsafe(24)
    raw = {"role": ROLE, "database": DATABASE, "password": pw}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(raw, indent=2), encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass  # Windows 上 chmod 语义有限，数据目录本身已在用户配置文件下
    return raw


def boot_database() -> str:
    """起内嵌 PostgreSQL 并返回 asyncpg 连接串。"""
    global _server
    try:
        import pgserver  # noqa: PLC0415 —— 只有桌面形态才需要它，开发态没装也不该炸
    except ImportError as exc:
        raise RuntimeError(
            "桌面形态要内嵌 PostgreSQL，但当前环境没装 pgserver。"
            "装依赖时带上 extras：pip install -e 'apps/api[desktop]'（pgserver 只有 3.12 的轮子）"
        ) from exc

    cred = _credentials()
    data_dir = runtime.pgdata_root()
    data_dir.mkdir(parents=True, exist_ok=True)
    # cleanup_mode="stop"：本进程退出时把 postmaster 一起带走（atexit 注册）
    _server = pgserver.get_server(data_dir, cleanup_mode="stop")
    _server.ensure_pgdata_inited()
    _server.ensure_postgres_running()

    roles = _server.psql(f"SELECT rolname FROM pg_roles WHERE rolname='{cred['role']}'")
    if cred["role"] not in roles:
        _server.psql(f"CREATE ROLE {cred['role']} LOGIN PASSWORD '{cred['password']}'")
        log.info("已创建数据库角色 %s", cred["role"])
    dbs = _server.psql(f"SELECT datname FROM pg_database WHERE datname='{cred['database']}'")
    if cred["database"] not in dbs:
        # CREATE DATABASE 不能在事务块里，psql -c 单条正好满足
        _server.psql(f"CREATE DATABASE {cred['database']} OWNER {cred['role']}")
        log.info("已创建数据库 %s", cred["database"])
    port = _live_port(data_dir)
    _state["pgPort"] = port
    url = f"postgresql+asyncpg://{cred['role']}:{cred['password']}@127.0.0.1:{port}/{cred['database']}"
    os.environ["H3_DATABASE_URL"] = url
    return url


def _live_port(data_dir: Path) -> int:
    """pgserver 传的 -p 会覆盖 postgresql.conf，而那个端口每次启动都可能变。
    唯一可靠来源是 postmaster.pid 第 4 行 —— 和 scripts/pg.py 同一个理由。"""
    pid_file = data_dir / "postmaster.pid"
    try:
        lines = pid_file.read_text(encoding="utf-8").splitlines()
        if len(lines) >= 4:
            return int(lines[3].strip())
    except Exception:
        pass
    info = getattr(_server, "get_postmaster_info", lambda: None)()
    return int(getattr(info, "port", 5432)) if info else 5432


def stop_database() -> None:
    """把内嵌 PostgreSQL 停干净 —— 桌面版没有「重启机器自然清理」这回事。

    实测踩过：本次启动如果是**接上了一次崩溃留下的 postmaster**（数据目录里还有
    `postmaster.pid` 且端口在听），`pgserver.cleanup()` 对这种「不是自己起的」实例只解注册
    不停服，于是数据库会活过应用退出。所以停完必须验一遍，还在就自己用 pg_ctl 收尾。
    """
    global _server
    data_dir = runtime.pgdata_root()
    pid_file = data_dir / "postmaster.pid"
    port = _state.get("pgPort")
    if _server is not None:
        try:
            _server.cleanup()
        except Exception as exc:
            log.warning("停内嵌 PG 时出错：%s", exc)
        finally:
            _server = None
    if _wait_pg_stopped(pid_file, port):
        return
    ctl = runtime.find_executable("pg_ctl")
    if not ctl:
        log.warning("内嵌 PostgreSQL 还在监听（端口 %s），但包里没有 pg_ctl —— 请手动结束它", port)
        return
    import subprocess

    subprocess.run(
        [ctl, "-D", str(data_dir), "-m", "fast", "stop", "-t", "20"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if _wait_pg_stopped(pid_file, port, timeout=15.0):
        log.info("内嵌 PostgreSQL 已停（pg_ctl fast）")
    else:
        log.warning("内嵌 PostgreSQL 仍监听在端口 %s，没敢强杀数据库进程，请手动处理", port)


def _wait_pg_stopped(pid_file: Path, port: int | None, timeout: float = 8.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        still = (port is not None and runtime.port_in_use(port)) or pid_file.exists()
        if not still:
            return True
        time.sleep(0.4)
    return False


# ---------------------------------------------------------------- 迁移


def run_migrations() -> None:
    """进程内跑 alembic upgrade head。

    装机包里没有 `alembic.exe`，也没有 CWD 可依赖，所以不能用命令行那条路。
    """
    from alembic import command
    from alembic.config import Config

    script_dir = runtime.alembic_dir()
    if not (script_dir / "env.py").exists():
        raise RuntimeError(f"找不到 alembic 目录：{script_dir}（打包时要把 alembic/ 作为数据文件带进去）")
    cfg = Config()
    cfg.set_main_option("script_location", str(script_dir))
    command.upgrade(cfg, "head")


# ---------------------------------------------------------------- desktop.json 握手


def pick_port(preferred: int) -> tuple[int, bool]:
    """优先用配置的端口，被占了就随机。返回 (端口, 是否换了)。"""
    if not runtime.port_in_use(preferred):
        return preferred, False
    return runtime.free_port(), True


def write_handshake(*, port: int, token: str) -> Path:
    path = runtime.desktop_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "port": port,
                "pid": os.getpid(),
                "token": token,
                "startedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "dataRoot": str(runtime.data_root()),
                "platform": sys.platform,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


def clear_handshake() -> None:
    """只删自己写的那份：按 pid 核对，别的实例写的不能动。"""
    path = runtime.desktop_file()
    if not path.exists():
        return
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return
    if int(raw.get("pid") or -1) == os.getpid():
        path.unlink(missing_ok=True)


def read_handshake() -> dict[str, Any] | None:
    path = runtime.desktop_file()
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    pid = int(raw.get("pid") or -1)
    port = int(raw.get("port") or 0)
    if pid <= 0 or not port:
        return None
    # 进程在不在：PID 会被复用，所以还要端口真的在听才算「有一个活着的桌面后端」
    if not runtime.port_in_use(port):
        return None
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        alive = s.connect_ex(("127.0.0.1", port)) == 0
    return raw if alive else None


# ---------------------------------------------------------------- 桌面端点


router = APIRouter(tags=["desktop"])


def _require_token(x_h3_desktop_token: str | None) -> None:
    want = _state.get("token")
    if not want or (x_h3_desktop_token or "") != want:
        raise HTTPException(403, "缺少或不对的桌面令牌（desktop.json 里那个）")


@router.get("/desktop/info")
async def info(x_h3_desktop_token: str | None = Header(default=None)) -> dict[str, Any]:
    """给桌面壳看后端自己看到的形状（端口、数据根、是否冻结）。

    这两个端点都不走登录门禁（壳里没有 JWT），但都要桌面令牌 —— 它会往外报数据目录绝对路径。
    """
    _require_token(x_h3_desktop_token)
    return {
        "desktop": runtime.is_desktop(),
        "frozen": runtime.is_frozen(),
        "port": _state.get("port"),
        "dataRoot": str(runtime.data_root()),
        "logsDir": str(runtime.logs_dir()),
        "webDist": str(runtime.web_dist() or ""),
        "platform": sys.platform,
    }


@router.post("/desktop/shutdown")
async def shutdown(x_h3_desktop_token: str | None = Header(default=None)) -> dict[str, str]:
    """Electron 关窗口后请后端自己退出 —— 它得先把内嵌 PG 停干净。

    直接 taskkill 后端进程等于让 postmaster 变成孤儿，下次开机才会被清理。
    """
    _require_token(x_h3_desktop_token)
    server = _state.get("uvicorn_server")
    if server is None:
        return {"ok": "false", "why": "没有可停的服务"}
    server.should_exit = True
    log.info("收到桌面壳的停机请求，正在优雅退出（pid=%s）", os.getpid())
    return {"ok": "true"}


# ---------------------------------------------------------------- 主入口


def main(argv: list[str] | None = None) -> int:
    """桌面形态入口：`h3 desktop` 或冻结 exe 的默认动作。"""
    import uvicorn

    args = argv or sys.argv[1:]
    host = os.environ.get("H3_HOST", "127.0.0.1")
    if "--host" in args:
        host = args[args.index("--host") + 1]

    runtime.ensure_dirs()
    log.info(
        "桌面形态启动：frozen=%s 数据根=%s 平台=%s",
        runtime.is_frozen(),
        runtime.data_root(),
        sys.platform,
    )

    try:
        url = boot_database()
        log.info("内嵌数据库就绪")
    except Exception as exc:
        log.error("内嵌数据库起不来：%s", exc)
        # 库坏了也要把界面开出来，否则用户只剩一扇黑窗：无库模式至少能看日志目录
        url = ""
    if url:
        try:
            run_migrations()
            log.info("表结构已到 head")
        except Exception as exc:
            log.error("数据库迁移失败：%s", exc)
            stop_database()
            return 2

    from .config import get_settings

    s = get_settings()
    port, changed = pick_port(int(os.environ.get("H3_PORT") or s.port))
    if changed:
        log.warning("端口 %s 已被占用，改用 %s（desktop.json 里是真值）", s.port, port)
    token = secrets.token_urlsafe(24)
    _state["token"] = token
    _state["port"] = port
    handshake = write_handshake(port=port, token=token)

    # 这里才 import app.main：lifespan 会读 H3_DATABASE_URL，环境变量必须先落定
    from .main import app

    config = uvicorn.Config(app, host=host, port=port, log_level=s.log_level.lower(), access_log=False)
    server = uvicorn.Server(config)
    _state["uvicorn_server"] = server
    try:
        server.run()
    finally:
        clear_handshake()
        stop_database()
        log.info("已退出（握手文件 %s 已清）", handshake)
    return 0


__all__ = ["clear_handshake", "info", "main", "read_handshake", "router", "run_migrations", "shutdown", "write_handshake"]

if __name__ == "__main__":
    # 不能直接在这个分支里跑 main()：`python -m app.desktop` 时本模块的名字是 __main__，
    # 而路由是 app.main 通过 `app.desktop` 注册的 —— 两份模块就有两份 _state，
    # 桌面令牌写在 __main__ 那份里，校验时读的是另一份，于是永远 403（实测踩过）。
    from app.desktop import main as _main

    raise SystemExit(_main(sys.argv[1:]))
