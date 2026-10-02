r"""「设置 → 系统」里那四个目录的真值来源。

以前这一页显示的是页面常量（D:/h3studio/data/media），后端真正在用的其实是另一份
（`H3_MEDIA_ROOT`，本机是 F:/H3/data/media）—— 用户照着页面上的路径去备份，备了个空目录。
这里统一成：库里 `app_settings['paths']` 是用户意图，环境变量是默认值，
`effective()` 一次算清「现在实际生效的是哪个、哪个要重启才换」。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import text

from . import runtime
from .config import Settings, get_settings
from .db import session_factory

KEY = "paths"
#: 媒体/临时根目录只影响已经落盘的文件怎么解释，运行中改会把旧产物的相对路径指错 —— 必须重启
RESTART_KEYS = ("media", "tmp")
#: ffmpeg 每次调用时才查，存完立刻生效
LIVE_KEYS = ("ffmpeg",)


def _clean(value: Any) -> str | None:
    s = str(value or "").strip()
    return s or None


def stored(overrides: dict[str, Any] | None) -> dict[str, str | None]:
    """只留三个已知键，去掉首尾空白；空串按「没配」处理，不是「配成空目录」。"""
    src = overrides or {}
    return {k: _clean(src.get(k)) for k in (*RESTART_KEYS, *LIVE_KEYS)}


async def load_overrides() -> dict[str, str | None]:
    try:
        async with session_factory()() as s:
            row = (await s.execute(text("SELECT value FROM app_settings WHERE key = :k"), {"k": KEY})).first()
    except Exception:
        # 无库模式（只跑 instances_file）没有这张表：目录设置就该是环境变量那份
        return stored({})
    return stored(row[0] if row and isinstance(row[0], dict) else {})


def effective(overrides: dict[str, str | None], settings: Settings | None = None) -> dict[str, Any]:
    """每个目录返回「生效路径 + 来源」，来源只有 env / stored 两种，别让人猜。"""
    s = settings or get_settings()
    out: dict[str, Any] = {}
    for k, env_val in (("media", s.media_root), ("tmp", s.tmp_root)):
        want = overrides.get(k)
        out[k] = {"path": str(Path(want) if want else Path(env_val)), "source": "stored" if want else "env"}
    exe = overrides.get("ffmpeg")
    found = runtime.ffmpeg_binary()
    out["ffmpeg"] = {
        "path": exe or (found or ""),
        "source": "stored" if exe else ("PATH" if found else "missing"),
    }
    out["needsRestart"] = [k for k in RESTART_KEYS if overrides.get(k)]
    return out


async def apply_at_startup() -> dict[str, Any]:
    """lifespan 在建注册表之前调：把库里的媒体/临时根目录装进进程内 Settings。

    注册表、队列、导出都拿同一个 Settings 单例，所以这里改一次全局都跟着走；
    没在这一步生效的（比如运行中刚保存的）等下一次启动，界面上也照实写「要重启」。
    """
    s = get_settings()
    ov = await load_overrides()
    applied: dict[str, Any] = {}
    for key in RESTART_KEYS:
        want = ov.get(key)
        if not want:
            continue
        p = Path(want)
        try:
            p.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            applied[key] = f"没建成：{exc}"
            continue
        setattr(s, "media_root" if key == "media" else "tmp_root", p)
        applied[key] = str(p)
    return applied


def validate(candidate: dict[str, str | None]) -> dict[str, str]:
    """只挡「明显会出事」的：根目录必须是个能建的绝对路径，ffmpeg 必须是存在的文件。"""
    problems: dict[str, str] = {}
    for key in (*RESTART_KEYS,):
        val = candidate.get(key)
        if not val:
            continue
        p = Path(val)
        if not p.is_absolute():
            problems[key] = f"{key} 要填绝对路径（现在：{val}）"
        elif p.exists() and not p.is_dir():
            problems[key] = f"{key} 那个位置上是个文件，不是目录（{val}）"
    ff = candidate.get("ffmpeg")
    if ff:
        p = Path(ff)
        if not p.is_absolute() or not p.is_file():
            problems["ffmpeg"] = f"ffmpeg 要填一个存在的可执行文件绝对路径（现在：{ff}）"
    return problems
