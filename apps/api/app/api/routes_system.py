"""系统路由：存储占用、媒体回收、单卡仲裁状态、风格表。"""

from __future__ import annotations

import datetime as dt
import shutil
import sys
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import text

from .. import runtime
from ..config import get_settings
from ..db import get_session, session_factory
from ..logging_setup import get_logger
from ..purge import last_purge, purge_expired
from ..security import admin_gate, login_gate
from ..styles import VISUAL_STYLES
from .common import CamelModel
from .routes_auth import _audit
from .routes_versions import _project_names

log = get_logger("api.system")
router = APIRouter(tags=["system"])


class GcBody(CamelModel):
    dry_run: bool = True


class PathsBody(CamelModel):
    """目录设置。留空 = 交回环境变量那份（H3_MEDIA_ROOT / H3_TMP_ROOT / PATH 里的 ffmpeg）。"""

    media: str | None = None
    tmp: str | None = None
    ffmpeg: str | None = None


@router.get("/system/audit")
async def audit_trail(
    limit: int = 80,
    action: str | None = None,
    actor: str | None = None,
    _: Any = Depends(login_gate),
    session=Depends(get_session),
) -> dict[str, Any]:
    """「设置 → 系统 → 操作记录」的真数据。以前这一栏是页面里写死的示例。

    只读，登录就能看（这台机是多用户内网工具，谁改过配置本该看得见）；
    要改只能改后端，所以不给写口。
    """
    from sqlalchemy import text as sa_text

    cap = max(1, min(int(limit or 80), 500))
    conds, args = [], {"cap": cap}
    if action:
        conds.append("action ILIKE :action")
        args["action"] = f"%{action}%"
    if actor:
        conds.append("actor ILIKE :actor")
        args["actor"] = f"%{actor}%"
    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    rows = (
        await session.execute(
            sa_text(
                f"""
                SELECT to_char(created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"') AS ts,
                       COALESCE(actor, '未知') AS actor, action, COALESCE(target, '') AS target, detail
                  FROM audit_log {where}
                 ORDER BY id DESC LIMIT :cap
                """
            ),
            args,
        )
    ).mappings().all()
    total = await session.scalar(sa_text(f"SELECT count(*) FROM audit_log {where}"), args)
    return {"items": [dict(r) for r in rows], "total": int(total or 0), "limit": cap}


@router.get("/system/paths")
async def get_paths(_: Any = Depends(login_gate)) -> dict[str, Any]:
    """当前生效的目录与来源（env / stored / PATH / missing）。"""
    from .. import paths as path_store

    return path_store.effective(await path_store.load_overrides())


@router.put("/system/paths")
async def put_paths(body: PathsBody, request: Request, actor: Any = Depends(admin_gate),
                   session=Depends(get_session)) -> dict[str, Any]:
    """保存目录设置。ffmpeg 存完就用；媒体/临时根目录要重启后端 —— 界面按 needsRestart 说。"""
    from .. import paths as path_store
    from ..models import AppSetting

    cand = path_store.stored(body.model_dump())
    problems = path_store.validate(cand)
    if problems:
        raise HTTPException(400, "；".join(problems.values()))
    row = await session.get(AppSetting, path_store.KEY)
    if row is None:
        session.add(AppSetting(key=path_store.KEY, value={k: v for k, v in cand.items() if v}))
    else:
        row.value = {k: v for k, v in cand.items() if v}
    await _audit(
        session,
        user_id=getattr(actor, "id", None),
        actor=getattr(actor, "username", None),
        action="system.paths",
        target=path_store.KEY,
        request=request,
        detail=cand,
    )
    await session.commit()
    eff = path_store.effective(cand)
    log.info("目录设置已保存：%s，操作人：%s", cand, getattr(actor, "username", "-"))
    return {"paths": eff, "appliedNow": eff["ffmpeg"], "needsRestart": eff["needsRestart"]}


@router.get("/styles")
async def list_styles(_: Any = Depends(login_gate)) -> list[dict[str, Any]]:
    """视觉风格表。前端拼装提示词用的是它自己那份常量，这里给的是「后端也能算」的那份。"""
    return [s.as_dict() for s in VISUAL_STYLES]


@router.get("/system/storage")
async def storage(_: Any = Depends(login_gate), session=Depends(get_session)) -> dict[str, Any]:
    s = get_settings()
    media_root = Path(s.media_root)

    def _du(path: Path) -> int:
        if not path.exists():
            return 0
        return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())

    rows = (
        await session.execute(
            text("SELECT project_key, count(*) AS n, COALESCE(sum(bytes),0) AS b FROM media WHERE deleted_at IS NULL GROUP BY project_key")
        )
    ).mappings().all()
    # 服务端不持有项目实体（A 方案），项目名只能取自删项目时留在 app_settings 里的那份快照；
    # 没记过名字的项目如实显示 project_key
    names = await _project_names(session)
    total_bytes = 0
    by_project = []
    for row in rows:
        total_bytes += int(row["b"])
        by_project.append(
            {
                "projectId": row["project_key"] or "(未归属)",
                "name": names.get(row["project_key"] or "", row["project_key"] or "(未归属)"),
                "bytes": int(row["b"]),
                "count": int(row["n"]),
            }
        )
    usage = shutil.disk_usage(str(media_root.parent if media_root.exists() else Path(".")))
    # 目录设置的真相：页面不许再用自己的常量显示一份「看起来像」的路径
    from .. import paths as path_store

    paths_eff = path_store.effective(await path_store.load_overrides(), s)
    # 回收站里积压了多少必须报出来：保留 100 天意味着这些字节还要占 100 天，
    # 而这台机的 F 盘只剩 ~494 GB（总 4.8T 已用 90%）
    trash = (
        await session.execute(text("SELECT count(*) AS n, COALESCE(sum(bytes),0) AS b FROM media WHERE deleted_at IS NOT NULL"))
    ).mappings().first()
    trash_scripts = await session.scalar(text("SELECT count(*) FROM script_versions WHERE deleted_at IS NOT NULL"))
    return {
        "mediaBytes": _du(media_root),
        "tmpBytes": _du(Path(s.tmp_root)),
        "freeBytes": usage.free,
        "mediaCount": (await session.scalar(text("SELECT count(*) FROM media WHERE deleted_at IS NULL"))) or 0,
        "trashCount": int(trash["n"]) + int(trash_scripts or 0),
        "trashBytes": int(trash["b"]),
        "retentionDays": s.trash_retention_days,
        "lastPurge": await last_purge(),
        "byProject": sorted(by_project, key=lambda x: -x["bytes"]),
        "root": str(media_root),
        "paths": paths_eff,
    }


@router.post("/system/gc")
async def gc(body: GcBody, actor: Any = Depends(admin_gate), session=Depends(get_session)) -> dict[str, Any]:
    """回收：软删满保留期（默认 100 天）的版本，连文件一起清掉。

    只按库里的 `deleted_at` + 天数判断，绝不去猜「这个文件没人引用」——项目实体在浏览器
    IndexedDB 里，服务端根本没有引用计数，猜的结果就是删掉用户的成片。

    这条以前写着"超过 7 天"却一行天数都没筛：任何一条软删中的产物都会被立即清掉，
    回收站的"可撤销"是假的。现在和到期自动扫共用 `purge.purge_expired`，两处口径必然一致。
    """
    report = await purge_expired(dry_run=body.dry_run)
    if not body.dry_run and (report["deleted"] or report["orphans"] or report["scriptsDeleted"]):
        async with session_factory()() as s:
            await _audit(
                s,
                user_id=getattr(actor, "id", None),
                actor=getattr(actor, "username", None),
                action="trash.gc",
                target="trash",
                request=None,
                detail=report,
            )
            await s.commit()
    return {
        "retentionDays": get_settings().trash_retention_days,
        # 沿用旧字段名，前端那一块不用跟着改口径
        "reclaimableBytes": report["bytes"],
        **report,
    }


@router.get("/system/backup")
async def backup_commands(_: Any = Depends(admin_gate)) -> dict[str, Any]:
    """这套栈（主机直跑 + 内嵌 pgserver，不上 Docker）真能执行的备份/恢复命令。

    页面原来给的是 `docker exec -t h3studio-db pg_dump ...` —— 这台机上根本没有那个容器，
    照着复制只会得到 "docker: no such container"。端口还是 pgserver 每次启动随机分配的，
    所以命令必须由后端按当前真实连接串拼，不能写死。
    """
    from urllib.parse import urlparse

    s = get_settings()
    url = s.database_url or ""
    parsed = urlparse(url.replace("+asyncpg", ""))
    host, port = (parsed.hostname or "127.0.0.1"), (parsed.port or 5432)
    user, db = (parsed.username or "h3"), (parsed.path.lstrip("/") or "h3studio")

    # 顺序：用户指的（H3_PG_DUMP / 设置里存的那条）→ 随包或已装 pgserver 自带的那份 → PATH → 仓库里的 .tooling-pg
    exe = str(s.pg_dump_path) if s.pg_dump_path and Path(s.pg_dump_path).is_file() else runtime.find_executable("pg_dump")
    if not exe and not runtime.is_frozen():
        # 开发态的后端 venv 里没装 pgserver（它只有 cp312 的 wheel），内嵌 PG 是 .tooling-pg 那份
        here = Path(__file__).resolve()
        for ancestor in here.parents[:6]:
            found = next(iter(sorted(ancestor.glob(".tooling-pg/**/pg_dump.exe")) or []), None) or \
                next(iter(sorted(ancestor.glob(".tooling-pg/**/pg_dump")) or []), None)
            if found:
                exe = str(found)
                break

    # 文件名里带真实日期，复制出去就能跑；`$(date +%F)` 那种写法在 cmd 里不会展开
    stamp = dt.date.today().isoformat()
    target = str(Path(s.tmp_root) / f"h3studio-{stamp}.dump")
    note = (
        "dump 只含库里的东西（用户、任务、媒体索引、剧本版本、工作流、审计）；"
        "媒体文件本身在磁盘上，归文件系统快照管，dump 里不含视频。备份别和媒体目录放同一块盘。"
    )
    if not exe:
        return {
            "available": False,
            "note": "没找到 pg_dump。装了 PostgreSQL 或起过 .tooling-pg 那套内嵌库之后这里才会有真命令。",
            "database": f"{host}:{port}/{db}",
        }
    suffix = ".exe" if sys.platform == "win32" else ""
    restore_exe = str(Path(exe).with_name(f"pg_restore{suffix}"))
    return {
        "available": True,
        "pgDump": exe,
        "database": f"{host}:{port}/{db}",
        "target": target,
        "dump": f'"{exe}" -h {host} -p {port} -U {user} -d {db} -Fc -f "{target}"',
        # 看自定义格式的清单是 pg_restore 的活：pg_dump 没有 --list 这个选项（试过，直接 illegal option）
        "list": f'"{restore_exe}" -l "{target}"',
        "restore": f'"{restore_exe}" -h {host} -p {port} -U {user} -d {db} --clean --if-exists --no-owner "{target}"',
        "hint": f"口令：连接串里那个（本机开发库是 {parsed.password or '（未设）'}）；也可以先用 PGPASS 环境变量。",
        "note": note,
    }


@router.get("/system/gpu")
async def gpu_status(request: Request, _: Any = Depends(login_gate)) -> dict[str, Any]:
    arbiter = getattr(request.app.state, "gpu", None)
    dispatcher = getattr(request.app.state, "dispatcher", None)
    if arbiter is None:
        return {"enabled": False}
    out = await arbiter.status()
    out["renderingLocally"] = bool(getattr(dispatcher, "local_render_active", lambda: False)())
    out["llmCalling"] = bool(getattr(dispatcher, "_llm_inflight", 0))
    return out


@router.post("/system/gpu/restore")
async def gpu_restore(request: Request, actor: Any = Depends(admin_gate)) -> dict[str, Any]:
    """把「让卡」时停掉的文本模型手动拉回来。"""
    arbiter = getattr(request.app.state, "gpu", None)
    if arbiter is None or not arbiter.stopped:
        return {"restored": False, "reason": "没有等待恢复的文本模型进程"}
    ok = await arbiter.restore_llm()
    return {"restored": ok, "reason": arbiter.last_error, "state": await arbiter.status()}


@router.post("/system/gpu/yield")
async def gpu_yield(request: Request, actor: Any = Depends(admin_gate)) -> dict[str, Any]:
    """手动让卡：出图/出片之前先把文本模型的显存腾出来。"""
    arbiter = getattr(request.app.state, "gpu", None)
    if arbiter is None:
        raise HTTPException(400, "单卡仲裁未启用（H3_GPU_ARBITER=off）")
    stopped = await arbiter.yield_llm(reason="用户在系统设置里手动让卡")
    return {"yielded": bool(stopped), "stopped": stopped, "reason": arbiter.last_error}
