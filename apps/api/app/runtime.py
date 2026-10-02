r"""运行位置的真值来源：开发态 / 冻结态（PyInstaller）/ 桌面态各自在哪。

以前这些事实散在三处，每条在打包后都会**静默指错**而不是启动就炸：

1. `config.py` 的默认值写死 `F:/H3/data/media`；
2. `Settings` 用相对路径 `.env`，CWD 一变就读不到；
3. `cli/mcpconfig.api_root()` 靠 `__file__` 往上跳三级 —— 冻结后 `__file__` 在
   `_MEIPASS` 临时目录里，跳出来的三层什么都不是。

统一在这里，判据只有两个：`sys.frozen`（是不是冻结产物）与 `H3_HOME`（数据根）。
"""

from __future__ import annotations

import os
import shutil
import socket
import sys
from pathlib import Path

APP_NAME = "H3 Studio"
APP_ID = "com.h3studio.desktop"

# PyInstaller 冻结后 _MEIPASS 指到随包的分发目录（onedir 下就是 exe 旁边的 _internal）
FROZEN = getattr(sys, "frozen", False) or bool(os.environ.get("H3_FROZEN_BUNDLE"))


def is_frozen() -> bool:
    return FROZEN


def is_desktop() -> bool:
    """桌面形态：冻结产物，或被 Electron 壳以 H3_DESKTOP=1 拉起的源码后端（便于不冻结就验桌面链路）。"""
    return FROZEN or os.environ.get("H3_DESKTOP") == "1"


def api_root() -> Path:
    """apps/api 目录；冻结后等价物是随包资源根目录。"""
    if FROZEN:
        return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return Path(__file__).resolve().parents[1]


def repo_root() -> Path:
    return api_root().parents[1]


def _platform_data_dir() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / APP_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "h3-studio"


def data_root() -> Path:
    r"""可写数据根：媒体产物、内嵌 PG 的数据目录、日志、密钥都挂在它下面。

    - 桌面态默认落到用户数据目录（安装位置可能在 Program Files，不能往那儿写）；
    - 开发态就是仓库的 `data/`，与改造前完全一致（本机是 `F:/H3/data`）。
    """
    env = os.environ.get("H3_HOME")
    if env:
        return Path(env)
    if is_desktop():
        return _platform_data_dir()
    return repo_root() / "data"


def media_root() -> Path:
    return data_root() / "media"


def tmp_root() -> Path:
    return data_root() / "tmp"


def pgdata_root() -> Path:
    return data_root() / "pgdata"


def logs_dir() -> Path:
    return data_root() / "logs"


def secret_path() -> Path:
    """Fernet 密钥位置。

    开发态保持在 `apps/api/.secret.key`：换位置等于让库里已加密的实例 apiKey 全解不开。
    桌面态必须离开安装目录（Program Files 下不可写，且卸载会连带删掉密钥）。
    """
    env = os.environ.get("H3_SECRET_FILE")
    if env:
        return Path(env)
    if is_desktop():
        return data_root() / ".secret.key"
    return api_root() / ".secret.key"


def instances_file() -> Path:
    if is_desktop():
        return data_root() / "instances.json"
    return api_root() / "instances.dev.json"


def env_files() -> list[Path]:
    r"""设置文件候选，按优先级。

    桌面态没有 `apps/api/.env`（那是开发用的、含本机绝对路径），只有用户数据目录里那份；
    而且源码跑桌面形态（`H3_DESKTOP=1`）时也不能去继承它 —— 那会让「桌面链路」的测试
    悄悄写到开发机的媒体目录和开发库上，测过了也不算数。
    """
    out = [data_root() / "h3.env"]
    if not FROZEN and not is_desktop():
        out.append(api_root() / ".env")
    return out


def web_dist() -> Path | None:
    """前端构建产物目录，找不到返回 None（后端就只跑 API，不挂静态）。"""
    env = os.environ.get("H3_WEB_DIST")
    if env:
        p = Path(env)
        return p if (p / "index.html").exists() else None
    candidates = [api_root() / "web", repo_root() / "apps" / "web" / "dist"]
    for c in candidates:
        if (c / "index.html").exists():
            return c
    return None


def alembic_dir() -> Path:
    return api_root() / "alembic"


def pg_binary_dir() -> Path | None:
    """内嵌 PostgreSQL 的 bin 目录（pgserver wheel 自带官方二进制）。"""
    if FROZEN:
        # --collect-data pgserver 会把 pginstall 原样搬进包；路径随平台不同，两处都试
        for rel in ("pgserver/pginstall", "pgserver/pginstall/lib"):
            p = api_root() / rel
            if p.exists():
                return p
        return None
    try:
        import pgserver  # noqa: PLC0415 —— 只有查路径时才需要它

        root = Path(pgserver.__file__).resolve().parent / "pginstall"
        return root if root.exists() else None
    except Exception:
        return None


def find_executable(name: str, *, env_key: str | None = None, in_pg_bundle: bool = True) -> str | None:
    """先用户指定，再随包二进制，最后系统 PATH。

    这台机器上 pg_dump/ffmpeg 都出现过「PATH 里没有、但某个目录里躺着」的情况，
    顺序反过来就会让用户以为功能没做。
    """
    if env_key:
        env = os.environ.get(env_key)
        if env:
            return env
    if in_pg_bundle:
        bindir = pg_binary_dir()
        if bindir is not None:
            for cand in (bindir / "bin" / name, bindir / name, bindir / "bin" / f"{name}.exe", bindir / f"{name}.exe"):
                if cand.exists():
                    return str(cand)
    found = shutil.which(name)
    if found:
        return found
    return None


def ffmpeg_binary() -> str | None:
    """导出与探针都要它。本机曾经只有「WinGet 装的 ffmpeg 不在 PATH 上」这一种情况，
    只查 PATH 会让功能在装了软件的机器上显示成「没做」。"""
    return find_executable("ffmpeg", env_key="H3_FFMPEG", in_pg_bundle=False)


def free_port(host: str = "127.0.0.1") -> int:
    """要一个没人占的端口。桌面版不能和用户已经在跑的 :8788 抢。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        return int(s.getsockname()[1])


def port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.4)
        return s.connect_ex((host, port)) == 0


def desktop_file() -> Path:
    """Electron 壳与后端之间的手写握手文件：端口、PID、停服令牌。"""
    return data_root() / "desktop.json"


def ensure_dirs() -> None:
    for p in (data_root(), media_root(), tmp_root(), logs_dir(), pgdata_root()):
        p.mkdir(parents=True, exist_ok=True)


__all__ = [
    "APP_ID",
    "APP_NAME",
    "alembic_dir",
    "api_root",
    "data_root",
    "desktop_file",
    "ensure_dirs",
    "env_files",
    "find_executable",
    "free_port",
    "instances_file",
    "is_desktop",
    "is_frozen",
    "logs_dir",
    "media_root",
    "pg_binary_dir",
    "pgdata_root",
    "port_in_use",
    "repo_root",
    "secret_path",
    "tmp_root",
    "web_dist",
]
