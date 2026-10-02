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
import subprocess
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


def _start_embedded_pg(data_dir: Path) -> None:
    """让 pgserver 把内嵌库带起来并认下这个实例。

    注意 `get_server()` 本身就负责启动（不是只拿个句柄），所以这三步必须整体看住 ——
    只把 `ensure_postgres_running()` 包进 try 是接不到异常的（第一版就这么漏过去了）。
    """
    global _server
    import pgserver  # noqa: PLC0415 —— 只有桌面形态才需要它

    _server = pgserver.get_server(data_dir, cleanup_mode="stop")
    _server.ensure_pgdata_inited()
    _server.ensure_postgres_running()


def boot_database() -> str:
    """起内嵌 PostgreSQL 并返回 asyncpg 连接串。"""
    try:
        import pgserver  # noqa: F401, PLC0415 —— 提前探一次依赖，报错要说人话
    except ImportError as exc:
        raise RuntimeError(
            "桌面形态要内嵌 PostgreSQL，但当前环境没装 pgserver。"
            "装依赖时带上 extras：pip install -e 'apps/api[desktop]'（pgserver 只有 3.12 的轮子）"
        ) from exc

    cred = _credentials()
    data_dir = runtime.pgdata_root()
    data_dir.mkdir(parents=True, exist_ok=True)
    # cleanup_mode="stop"：本进程退出时把 postmaster 一起带走（atexit 注册）
    try:
        _start_embedded_pg(data_dir)
    except Exception as exc:
        # pgserver 内部只等 10 秒（写死的 subprocess 超时）。上一次被强杀过就要做崩溃恢复，
        # 装机版实测：postgres 33 秒才 ready（其中 30 秒在重试 "./log sharing violation"），
        # pg_ctl 已经报超时 —— 当成致命错误会让整个应用退化成无库模式（登录 500），
        # 而库其实几秒后就起来了。所以这里自己等，等到了再让 pgserver 认领一次。
        log.warning("内嵌 PG 在 10 秒内没报就绪（多半还在崩溃恢复），改为自己等：%s", str(exc)[:180])
        if not _wait_postgres_ready(data_dir, timeout=120.0):
            raise RuntimeError(f"内嵌 PostgreSQL 在 120 秒内没就绪，看 {data_dir / 'log'}") from exc
        _start_embedded_pg(data_dir)

    pg = _postmaster_info(data_dir)
    _state["pgPort"] = pg["port"]
    _ensure_role_and_db(pg, cred)

    url = _app_url(pg, cred)
    os.environ["H3_DATABASE_URL"] = url
    return url


def _postmaster_info(data_dir: Path) -> dict[str, Any]:
    """从 postmaster.pid 读真值：第 1 行 pid、第 4 行端口、第 5 行 socket 目录。

    pgserver 每次挑的端口都可能变，而且我们可能是在「上一次崩溃留下的库」上继续跑，
    所以这个文件才是唯一可靠来源（开发侧 scripts/pg.py 同一个理由）。
    """
    pid_file = data_dir / "postmaster.pid"
    lines = [ln.strip() for ln in pid_file.read_text(encoding="utf-8", errors="replace").splitlines()]
    if len(lines) < 4:
        raise RuntimeError(f"{pid_file} 内容不完整，内嵌 PG 没在跑")
    sock = lines[4] if len(lines) > 4 and lines[4].startswith(("/", ".")) else None
    return {"pid": int(lines[0]), "port": int(lines[3]), "socket": sock}


def _connect_kwargs(pg: dict[str, Any], *, user: str, database: str) -> dict[str, Any]:
    r"""pgserver 在两个平台上连法不同，必须分开：

    - Windows：给 postgres 传 `-h 127.0.0.1 -p <随机口>`，走 TCP；
    - macOS/Linux：传的是 `-h ""`（**完全不监听 IP**）+ `-k <socket 目录>`，只走 UNIX socket。
      照 Windows 那样连 TCP 在 Mac 上永远连不上 —— 这是 pgserver 源码里的分支，不是推测。
    """
    if pg.get("socket"):
        # asyncpg 用 host=目录 表示走 socket；port 对 socket 无意义但参数必填
        return {"host": str(pg["socket"]), "port": int(pg["port"] or 0), "user": user, "database": database}
    return {"host": "127.0.0.1", "port": int(pg["port"]), "user": user, "database": database}


def _wait_postgres_ready(data_dir: Path, *, timeout: float) -> bool:
    """自己等内嵌 PG 可用：要真握手一次维护库，光看端口在听不够（postmaster 还没开始 accept）。"""
    import asyncio

    import asyncpg

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            pg = _postmaster_info(data_dir)
        except Exception:
            time.sleep(1.0)
            continue

        async def _probe(target: dict[str, Any]) -> bool:
            conn = await asyncpg.connect(**_connect_kwargs(target, user="postgres", database="postgres"), timeout=5)
            await conn.close()
            return True

        try:
            return asyncio.run(_probe(pg))
        except Exception:
            time.sleep(1.5)
    return False


def _ensure_role_and_db(pg: dict[str, Any], cred: dict[str, str]) -> None:
    r"""建角色与库。

    绝不能用 `pgserver.psql()`：它内部是
    `subprocess.check_output(f'{executable} {uri}', shell=True)` —— 可执行文件路径里一有空格
    就被 cmd 拆开，于是**装到 `Programs\H3 Studio` 这种带空格目录时必然失败**（装机版实测：
    `Command '...\psql postgresql://…' returned non-zero exit status 1`，然后整个应用退化成
    无库模式，登录 500）。改成 asyncpg 直连维护库，参数不进 shell。
    """
    import asyncio

    import asyncpg

    role, db, pw = cred["role"], cred["database"], cred["password"]
    if any(c in role + db + pw for c in ("'", '"', "\\", ";")):
        # 口令是我们自己生成的（token_urlsafe，不含这些字符）；走到这里说明数据被人为改过
        raise RuntimeError("内嵌数据库的角色/口令里含引号或分号，拒绝执行建库语句")

    async def _go() -> None:
        conn = await asyncpg.connect(**_connect_kwargs(pg, user="postgres", database="postgres"), timeout=20)
        try:
            if await conn.fetchval("SELECT 1 FROM pg_roles WHERE rolname = $1", role) is None:
                await conn.execute(f'CREATE ROLE "{role}" LOGIN PASSWORD \'{pw}\'')
                log.info("已创建数据库角色 %s", role)
            if await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", db) is None:
                await conn.execute(f'CREATE DATABASE "{db}" OWNER "{role}"')
                log.info("已创建数据库 %s", db)
        finally:
            await conn.close()

    asyncio.run(_go())


def _app_url(pg: dict[str, Any], cred: dict[str, str]) -> str:
    """应用侧连接串（SQLAlchemy + asyncpg）。socket 与 TCP 两种写法不同。"""
    from urllib.parse import quote

    role, db, pw = cred["role"], cred["database"], cred["password"]
    kwargs = _connect_kwargs(pg, user=role, database=db)
    if pg.get("socket"):
        return f"postgresql+asyncpg://{role}:{quote(pw, safe='')}@/{db}?host={quote(kwargs['host'])}"
    return f"postgresql+asyncpg://{role}:{quote(pw, safe='')}@127.0.0.1:{kwargs['port']}/{db}"


def stop_database() -> None:
    """把内嵌 PostgreSQL 停干净 —— 桌面版没有「重启机器自然清理」这回事。

    两条实测教训：
    1. `pgserver.cleanup()` 只在「全局持有者名单里只剩自己」时才停服。上一次被强杀时它会在
       名单里留下死 PID，于是这次退出它只解注册不停服 —— 装机版实测退出后仍有 6 个 postgres 进程。
    2. 所以不能只看 cleanup 的脸色：等一遍，还在就自己用随包的 `pg_ctl -m fast stop` 收尾，
       并把每一步的结果写进日志（这一步出错也不能让退出流程炸掉）。
    """
    global _server
    try:
        data_dir = runtime.pgdata_root()
        port = _state.get("pgPort")
        if _server is not None:
            try:
                _server.cleanup()
            except Exception as exc:
                log.warning("pgserver.cleanup() 出错（继续自己收尾）：%s", str(exc)[:200])
            finally:
                _server = None
        if _wait_pg_stopped(data_dir, port, timeout=8.0):
            log.info("内嵌 PostgreSQL 已停")
            return
        ctl = runtime.find_executable("pg_ctl")
        if not ctl:
            log.warning("内嵌 PostgreSQL 还监听在端口 %s，但包里没有 pg_ctl —— 请手动结束它", port)
            return
        out = subprocess.run(
            [ctl, "-D", str(data_dir), "-m", "fast", "stop", "-t", "20"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        stopped = _wait_pg_stopped(data_dir, port, timeout=15.0)
        log.info(
            "pg_ctl fast stop 收尾：exit=%s 已停=%s %s",
            out.returncode,
            stopped,
            ((out.stdout or "") + (out.stderr or "")).strip()[:200],
        )
        if not stopped:
            log.warning("内嵌 PostgreSQL 仍监听在端口 %s，没敢强杀数据库进程，请手动处理", port)
    except Exception as exc:  # 停机路径上的任何意外都不能阻止进程退出
        log.warning("停内嵌 PostgreSQL 时异常：%s", str(exc)[:200])


def _wait_pg_stopped(data_dir: Path, port: int | None, timeout: float = 8.0) -> bool:
    """端口不再在听、且 postmaster.pid 已被自己删掉 —— 才算真的停了。"""
    pid_file = data_dir / "postmaster.pid"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not pid_file.exists() and not (port is not None and runtime.port_in_use(port)):
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
    # PID 会被复用，所以还要那个端口真的在听，才算「有一个活着的桌面后端」
    return raw if runtime.port_in_use(port) else None


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
    # 必须在起库之前就把日志接上：lifespan 里那次 setup_logging 太晚，
    # 「内嵌 PG 起不来」这种致命错误会连同原因一起被丢掉（装机版第一次就这么哑巴了）。
    from .logging_setup import setup_logging

    setup_logging(os.environ.get("H3_LOG_LEVEL", "INFO"))
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
