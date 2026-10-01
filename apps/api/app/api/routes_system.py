"""系统路由：存储占用、媒体回收、单卡仲裁状态、风格表。"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select, text

from ..config import get_settings
from ..db import get_session, session_factory
from ..logging_setup import get_logger
from ..models import Media
from ..security import admin_gate, login_gate
from ..styles import VISUAL_STYLES
from .common import CamelModel

log = get_logger("api.system")
router = APIRouter(tags=["system"])


class GcBody(CamelModel):
    dry_run: bool = True


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
    names: dict[str, str] = {}
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
    return {
        "mediaBytes": _du(media_root),
        "tmpBytes": _du(Path(s.tmp_root)),
        "freeBytes": usage.free,
        "mediaCount": (await session.scalar(text("SELECT count(*) FROM media WHERE deleted_at IS NULL"))) or 0,
        "byProject": sorted(by_project, key=lambda x: -x["bytes"]),
        "root": str(media_root),
    }


@router.post("/system/gc")
async def gc(body: GcBody, actor: Any = Depends(admin_gate), session=Depends(get_session)) -> dict[str, Any]:
    """回收：软删超过 7 天的媒体，以及库里有记录但磁盘上已不存在的孤儿行。

    只按库里的 deleted_at 判断，绝不去猜「这个文件没人引用」——项目实体在浏览器
    IndexedDB 里，服务端根本没有引用计数，猜的结果就是删掉用户的成片。
    """
    root = Path(get_settings().media_root)
    rows = (await session.execute(select(Media).where(Media.deleted_at.isnot(None)))).scalars().all()
    reclaimable = 0
    orphans = 0
    for m in rows:
        target = (root / m.path).resolve()
        if not target.is_relative_to(root.resolve()):
            continue
        if target.is_file():
            reclaimable += target.stat().st_size
            if not body.dry_run:
                target.unlink(missing_ok=True)
        else:
            orphans += 1
        if not body.dry_run:
            await session.execute(text("DELETE FROM media WHERE id=:i"), {"i": m.id})
    if not body.dry_run:
        await session.commit()
    log.info("媒体回收 %s：%d 字节 / %d 个孤儿行，操作人：%s", "试运行" if body.dry_run else "已执行", reclaimable, orphans, getattr(actor, "username", "-"))
    return {"reclaimableBytes": reclaimable, "orphans": orphans, "dryRun": body.dry_run}


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
