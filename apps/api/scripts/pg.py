"""本机开发用 PostgreSQL 的启停工具（非侵入式）。

不用系统安装包的三个理由：EDB 下载站直连返回 403；装服务要管理员权限；会常驻系统并留下
卸载残留。这里用 PyPI 的 pgserver wheel（自带官方 PG 二进制），数据目录在 F:/H3/data/pgdata，
端口 54329，随时整目录删掉即可，不动系统任何配置。

用法（用 .tooling-pg 这个 3.12 环境跑，pgserver 只出到 cp312）：
  F:/H3/.tooling-pg/Scripts/python.exe apps/api/scripts/pg.py boot
  ... status | stop | uri | psql "SELECT 1"
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pgserver

DATA_DIR = Path("F:/H3/data/pgdata")
PORT = 54329
APP_ROLE = "h3"
APP_DB = "h3studio"
# 只绑环回、只用于本机开发。生产必须换成强口令并走 H3_DATABASE_URL 注入。
APP_PASSWORD = "h3-dev-local-only"


def _connect_args() -> list[str]:
    return ["-h", "127.0.0.1", "-p", str(PORT)]


def _server() -> "pgserver.postgres_server.PostgresServer":
    # cleanup_mode=None → Python 进程退出后 postmaster 继续跑，
    # 这样 uvicorn 和 alembic 才能各自独立连接。
    return pgserver.get_server(DATA_DIR, cleanup_mode=None)


def boot() -> int:
    fresh = not (DATA_DIR / "PG_VERSION").exists()
    s = _server()
    s.ensure_pgdata_inited()
    s.ensure_postgres_running()
    _ensure_role_and_db(s)
    write_env()
    print(f"PG 就绪 (pid={s.get_pid()}) 实际端口 {live_port()}")
    print("已写入 apps/api/.env")
    print("应用连接串：", app_uri())
    return 0


def _restart() -> None:
    # 不猜 pg_ctl.exe 的路径：pgserver 自己管理生命周期
    srv = _server()
    srv.cleanup()
    _server().ensure_postgres_running()


def _ensure_role_and_db(s) -> None:
    admin = s.get_uri()
    has_role = APP_ROLE in s.psql(f"SELECT rolname FROM pg_roles WHERE rolname='{APP_ROLE}'")
    if not has_role:
        s.psql(f"CREATE ROLE {APP_ROLE} LOGIN PASSWORD '{APP_PASSWORD}'")
        print(f"已创建角色 {APP_ROLE}")
    has_db = APP_DB in s.psql(f"SELECT datname FROM pg_database WHERE datname='{APP_DB}'")
    if not has_db:
        # CREATE DATABASE 不能在事务块里跑，psql -c 单条执行正好满足
        s.psql(f"CREATE DATABASE {APP_DB} OWNER {APP_ROLE}")
        print(f"已创建数据库 {APP_DB}（owner={APP_ROLE}）；迁移不需要超级用户")
    print("管理连接串（仅本机）:", admin.split("?")[0][:60], "…")


def live_port() -> int:
    """pgserver 在命令行传 -p 会覆盖 postgresql.conf 的 port，而且那个端口每次启动都可能变。
    唯一可靠来源是 postmaster.pid 第 4 行。硬编码端口一定会连错。"""
    pid_file = DATA_DIR / "postmaster.pid"
    if not pid_file.exists():
        raise SystemExit("postmaster.pid 不存在 —— PostgreSQL 没在跑，先执行 boot")
    lines = pid_file.read_text(encoding="utf-8").splitlines()
    return int(lines[3].strip()) if len(lines) >= 4 else PORT


def app_uri() -> str:
    return f"postgresql+asyncpg://{APP_ROLE}:{APP_PASSWORD}@127.0.0.1:{live_port()}/{APP_DB}"


def write_env() -> Path:
    """把真实连接串落到 apps/api/.env，后端与 alembic 都从这里读。"""
    env = Path("apps/api/.env")
    line = f"H3_DATABASE_URL={app_uri()}"
    text = env.read_text(encoding="utf-8").splitlines() if env.exists() else []
    text = [l for l in text if l.strip() and not l.startswith("H3_DATABASE_URL=")]
    (DATA_DIR / "h3_media").mkdir(exist_ok=True)
    text += [
        "# 由 apps/api/scripts/pg.py 自动写入，端口会变，别手改",
        line,
        "H3_MEDIA_ROOT=F:/H3/data/media",
        "H3_TMP_ROOT=F:/H3/data/tmp",
        "H3_INSTANCES_FILE=F:/H3/apps/api/instances.dev.json",
    ]
    env.write_text(os.linesep.join(text) + os.linesep, encoding="utf-8")
    return env


def status() -> int:
    try:
        s = _server()
        pid = s.get_pid()
        out = s.psql("SELECT version()")
        print(f"pid={pid}")
        print((out.strip().splitlines() or ["(无输出)"])[0])
        print(app_uri())
        return 0
    except Exception as exc:
        print(f"未运行：{type(exc).__name__}: {exc}")
        return 1


def stop() -> int:
    _server().cleanup()
    return 0


def psql_inline(sql: str) -> int:
    print(_server().psql(sql))
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "boot"
    if cmd == "psql":
        raise SystemExit(psql_inline(sys.argv[2]))
    raise SystemExit({"boot": boot, "status": status, "stop": stop, "uri": lambda: print(app_uri())}[cmd]())
