"""生成产物的版本历史与生成回收站。

剧本版本在这里有真表（`script_versions`）；图片/视频的版本就是 `media` 行本身，
按 `(project_key, kind, role, ref_id)` 分组数出来，所以两边"版本"是两套实现，
但对外口径一致：**V1..Vn 按时间升序、序号永不复用** —— 进了回收站的行继续占着自己的号，
否则用户恢复出来的"V2"会和他记忆里的 V2 不是同一份东西。

`written_at` 与 `created_at` 的分工是刻意的：前者是"这版正文何时写就"（给用户看），
后者是"这行为何时进库"（审计，不许改）。首次生成时要把用户已有正文补存成 V1，两行在同一次
请求里插入，用 created_at 当显示时间会让 V1 看起来比 V2 还晚 —— 界面看到这种结果就干脆不显示。

**排序一律用 seq，不用 written_at**：seq 是 `max+1` 发出来的，天然与时间同向且永不重排；
而 manual/补存行的 written_at 可能指向过去（那本来就是正文上次被写的时间），拿它排序会让
列表出现 "V1, V3, V4" 这种号与时间不对齐的结果 —— 实测踩过。written_at 只负责"这一版何时写就"
的显示，不参与顺序。

每个写接口都遵守同一条收口顺序：`commit()` → `refresh()` → **先把响应字典组装完** → 再写审计。
异步 SQLAlchemy 在提交后会让实例属性过期，审计那次提交之后再读 `row.seq` 会抛 MissingGreenlet。
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy import text as sa_text
from sqlalchemy.exc import IntegrityError

from ..config import get_settings
from ..db import get_session
from ..logging_setup import get_logger
from ..models import Media, ScriptVersion
from ..purge import media_root, purge_media_rows
from ..security import dispatch_gate, login_gate
from .common import CamelModel
from .routes_auth import _audit
from .routes_jobs import _bad_request, _has_db, _not_found

log = get_logger("api.versions")
router = APIRouter(tags=["versions"])

_SOURCES = ("ai-write", "storyboard", "manual")
# 差过这个数就认定是补存而不是当场写的（补存的 V1 用的正文可能早就写好了）
_BACKFILL_GAP = dt.timedelta(minutes=2)
_SEQ_PREFIX = "scriptver:"


def _seq_key(project_key: str) -> str:
    """app_settings.key 只有 String(64)，而 project_key 允许 64 长 —— 超了就换摘要。

    摘要只会和「同一个项目自己」撞上，不会把两个不同项目的计数器并到一起。
    """
    raw = f"{_SEQ_PREFIX}{project_key}"
    if len(raw) <= 64:
        return raw
    return _SEQ_PREFIX + hashlib.sha1(project_key.encode("utf-8")).hexdigest()[:40]


def _iso(value: dt.datetime | None) -> str | None:
    return value.isoformat() if value else None


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _as_time(value: Any) -> dt.datetime | None:
    if not value:
        return None
    if isinstance(value, dt.datetime):
        return value
    try:
        return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _client_time(value: dt.datetime | None, now: dt.datetime) -> dt.datetime:
    """客户端传来的时间只许往过去。没传就用服务端时钟。"""
    if value is None:
        return now
    return min(value.replace(tzinfo=dt.timezone.utc) if value.tzinfo is None else value, now)


def _purge_fields(deleted_at: dt.datetime | None) -> dict[str, Any]:
    """回收站里的行必须能回答「还剩几天」。没删除时三个字段都是 null，界面据此不显示倒计时。"""
    if deleted_at is None:
        return {"purgeAfter": None, "daysLeft": None, "retentionDays": None}
    days = get_settings().trash_retention_days
    after = deleted_at + dt.timedelta(days=days)
    # 用 ceil：刚删完那一瞬 (after-now) 是 99.99 天，floor 会显示「剩 99 天」，
    # 用户会觉得一天都没存住
    left = math.ceil((after - _now()).total_seconds() / 86400)
    return {"purgeAfter": _iso(after), "daysLeft": max(left, 0), "retentionDays": days}


def _out(v: ScriptVersion, version_count: int | None = None) -> dict[str, Any]:
    return {
        "id": str(v.id),
        "uuid": v.uuid,
        "projectKey": v.project_key,
        "version": v.seq,
        "versionCount": version_count,
        "source": v.source,
        "text": v.text,
        "snapshot": v.snapshot or {},
        "isCurrent": v.is_current,
        "deletedAt": _iso(v.deleted_at),
        "createdAt": _iso(v.created_at),
        "writtenAt": _iso(v.written_at),
        # 界面要说清「这一版的时间不是生成时间」，而不是让补存的 V1 顶着一个像生成时刻的时间
        "backfilled": bool(v.created_at and v.written_at and v.created_at - v.written_at > _BACKFILL_GAP),
        **_purge_fields(v.deleted_at),
    }


async def _count(session: Any, project_key: str, *, live_only: bool = False) -> int:
    sql = "SELECT count(*) FROM script_versions WHERE project_key=:p"
    if live_only:
        sql += " AND deleted_at IS NULL"
    return int((await session.scalar(sa_text(sql), {"p": project_key})) or 0)


async def _claim_seq(session: Any, project_key: str) -> int:
    """发一个版本号。**号一旦发出去就不回收。**

    只靠 `max(seq)+1` 是不够的：彻底删除（purge）会把整行抹掉，max 跟着退回去，
    下一个版本就捡到已经用过的号 —— 用户记忆里的 V2 于是变成别的东西。所以计数器落在
    现成的 app_settings 上，不为这件事加一张表。
    """
    key = _seq_key(project_key)
    stored = int((await session.scalar(sa_text("SELECT (value->>'next')::int FROM app_settings WHERE key=:k"), {"k": key})) or 0)
    max_seq = int((await session.scalar(sa_text("SELECT coalesce(max(seq),0) FROM script_versions WHERE project_key=:p"), {"p": project_key})) or 0)
    nxt = max(stored, max_seq) + 1
    await session.execute(
        sa_text(
            # 用 CAST 而不是 :n::int —— 绑定参数紧挨着 :: 会让 SQLAlchemy 认不出这个占位符
            "INSERT INTO app_settings(key, value, updated_at) VALUES(:k, jsonb_build_object('next', CAST(:n AS bigint)), now())"
            " ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=now()"
        ),
        {"k": key, "n": nxt},
    )
    return nxt


async def _demote(session: Any, project_key: str) -> None:
    await session.execute(
        sa_text("UPDATE script_versions SET is_current=false WHERE project_key=:p AND is_current=true"),
        {"p": project_key},
    )


async def _by_uuid(session: Any, version_uuid: str) -> ScriptVersion:
    row = (await session.execute(select(ScriptVersion).where(ScriptVersion.uuid == version_uuid))).scalars().first()
    if row is None:
        raise _not_found(f"没有这一版剧本（{version_uuid[:8]}）—— 可能已经被彻底删除了")
    return row


class ScriptVersionBody(CamelModel):
    project_key: str
    text: str
    source: str = "ai-write"
    snapshot: dict[str, Any] = {}
    written_at: dt.datetime | None = None
    # {"text":…, "writtenAt":…}：首次存版时把用户早就写好的那版正文一起交上来，服务端在一个
    # 事务里先插 V1 再插 V2。绝不让前端分两次 POST —— 中间崩了就只剩一个被标成当前版的 V1，
    # 这次生成的正文永远进不了历史。
    backfill_from: dict[str, Any] | None = None
    set_current: bool = True


@router.get("/script-versions")
async def list_script_versions(
    request: Request,
    project_key: str | None = None,
    include_deleted: bool = False,
    _: Any = Depends(login_gate),
    session=Depends(get_session),
) -> list[dict[str, Any]]:
    """某项目的剧本版本，最新在前。

    `versionCount` 数的是**全部**版本（含回收站里的），所以 `include_deleted` 只决定哪些行
    出现在结果里，不会让剩下的行重新编号。
    """
    if not _has_db(request):
        return []
    if not project_key:
        raise _bad_request("剧本版本必须给 project_key")
    total = await _count(session, project_key)
    stmt = select(ScriptVersion).where(ScriptVersion.project_key == project_key)
    if not include_deleted:
        stmt = stmt.where(ScriptVersion.deleted_at.is_(None))
    stmt = stmt.order_by(ScriptVersion.seq.desc()).limit(500)
    rows = (await session.execute(stmt)).scalars().all()
    return [_out(v, total) for v in rows]


@router.post("/script-versions", status_code=201)
async def create_script_version(
    body: ScriptVersionBody,
    request: Request,
    actor: Any = Depends(dispatch_gate),
    session=Depends(get_session),
) -> dict[str, Any]:
    """存一版剧本正文。带 `backfillFrom` 且本项目还没有任何版本时，先补一版 V1。"""
    if not _has_db(request):
        raise _bad_request("无库模式没有 script_versions 表，请配置 H3_DATABASE_URL")
    pk = body.project_key.strip()
    if not pk:
        raise _bad_request("project_key 不能为空")
    if body.source not in _SOURCES:
        raise _bad_request(f"source 只能是 {' / '.join(_SOURCES)}")
    if not body.text.strip():
        raise _bad_request("正文是空的：存一版空文本没有意义，只会把历史搞脏")

    now = _now()
    # writtenAt 只信两种情况：用户手动存版、和补存那版旧正文。其余一律服务端时钟 ——
    # 这是「V3 的时间比 V2 还早」唯一的封口。
    backfill = body.backfill_from if (body.backfill_from and await _count(session, pk) == 0) else None
    old_text = str(backfill.get("text") or "") if backfill else ""
    if backfill and not old_text.strip():
        backfill = None

    for attempt in range(2):
        try:
            if backfill:
                session.add(
                    ScriptVersion(
                        project_key=pk,
                        owner_id=getattr(actor, "id", None),
                        seq=await _claim_seq(session, pk),
                        source="manual",
                        text=old_text,
                        is_current=False,
                        written_at=_client_time(_as_time(backfill.get("writtenAt")), now),
                    )
                )
            seq = await _claim_seq(session, pk)
            if body.set_current:
                await _demote(session, pk)
            row = ScriptVersion(
                project_key=pk,
                owner_id=getattr(actor, "id", None),
                seq=seq,
                source=body.source,
                text=body.text,
                snapshot=body.snapshot or {},
                is_current=body.set_current,
                written_at=now if body.source != "manual" else _client_time(body.written_at, now),
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            break
        except IntegrityError:
            # 两个用途同时落版（续写撞上拆解）会撞 seq 唯一键。重试一次就够，不加 advisory lock：
            # 本机 llama.cpp 是 --parallel 1，真并发走到这里的机会几乎没有。
            await session.rollback()
            if attempt:
                raise
    payload = _out(row, await _count(session, pk))
    await _audit(
        session,
        user_id=getattr(actor, "id", None),
        actor=getattr(actor, "username", None),
        action="script.version.create",
        target=f"script_version:{row.uuid}",
        request=request,
        detail={"projectKey": pk, "version": row.seq, "source": row.source, "chars": len(row.text), "backfilled": bool(backfill)},
    )
    await session.commit()
    log.info("剧本存版 %s V%d（%s，%d 字%s）", pk, payload["version"], payload["source"], len(payload["text"]), "，含补存 V1" if backfill else "")
    return payload


@router.post("/script-versions/{version_uuid}/current")
async def set_script_current(
    version_uuid: str,
    request: Request,
    actor: Any = Depends(dispatch_gate),
    session=Depends(get_session),
) -> dict[str, Any]:
    """把某一版设为当前。顺带回正文，前端要把它写回编辑器的 rawScript。"""
    if not _has_db(request):
        raise _bad_request("无库模式没有 script_versions 表")
    row = await _by_uuid(session, version_uuid)
    if row.deleted_at is not None:
        raise _bad_request("这一版在回收站里：先恢复，再设为当前")
    await _demote(session, row.project_key)
    row.is_current = True
    session.add(row)
    await session.commit()
    await session.refresh(row)
    payload = {"current": _out(row, await _count(session, row.project_key)), "rawScript": row.text}
    await _audit(
        session,
        user_id=getattr(actor, "id", None),
        actor=getattr(actor, "username", None),
        action="script.version.current",
        target=f"script_version:{row.uuid}",
        request=request,
        detail={"projectKey": row.project_key, "version": row.seq},
    )
    await session.commit()
    return payload


@router.delete("/script-versions/{version_uuid}")
async def trash_script_version(
    version_uuid: str,
    request: Request,
    actor: Any = Depends(dispatch_gate),
    session=Depends(get_session),
) -> dict[str, Any]:
    """移进生成回收站。编辑器里的 rawScript 一个字节都不动 —— 删版本记录不许吃掉用户的正文。"""
    if not _has_db(request):
        raise _bad_request("无库模式没有 script_versions 表")
    row = await _by_uuid(session, version_uuid)
    if row.deleted_at is not None:
        raise _bad_request("这一版已经在回收站里了")
    was_current = row.is_current
    row.deleted_at = _now()
    # 必须同时清掉 is_current：部分唯一索引 uq_script_versions_one_current 的条件排除了软删行，
    # 留着标记会让这行继续占着「当前」的坑，下一次 setCurrent 直接撞索引报 500。
    row.is_current = False
    session.add(row)
    # 删的是当前版就退到剩下最新的一版；一版都不剩就不设当前，由前端提示「存为 V{n+1}」
    survivors = (
        await session.execute(
            select(ScriptVersion)
            .where(ScriptVersion.project_key == row.project_key, ScriptVersion.deleted_at.is_(None))
            .order_by(ScriptVersion.seq.desc())
            .limit(1)
        )
    ).scalars().first() if was_current else None
    if survivors is not None:
        survivors.is_current = True
        session.add(survivors)
    await session.commit()
    await session.refresh(row)
    if survivors is not None:
        await session.refresh(survivors)
    total = await _count(session, row.project_key)
    payload = {
        "uuid": row.uuid,
        "wasCurrent": was_current,
        "current": _out(survivors, total) if survivors is not None else None,
        **_purge_fields(row.deleted_at),
    }
    await _audit(
        session,
        user_id=getattr(actor, "id", None),
        actor=getattr(actor, "username", None),
        action="script.version.trash",
        target=f"script_version:{row.uuid}",
        request=request,
        detail={"projectKey": row.project_key, "version": row.seq, "wasCurrent": was_current, "promoted": survivors.seq if survivors is not None else None},
    )
    await session.commit()
    log.info("剧本版 V%d 进回收站（%s）", row.seq, row.project_key)
    return payload


@router.post("/script-versions/{version_uuid}/restore")
async def restore_script_version(
    version_uuid: str,
    request: Request,
    actor: Any = Depends(dispatch_gate),
    session=Depends(get_session),
) -> dict[str, Any]:
    """从回收站恢复。恢复**不抢**当前版：只有在项目已经没有当前版时才顶上，一次动作只做一件事。"""
    if not _has_db(request):
        raise _bad_request("无库模式没有 script_versions 表")
    row = await _by_uuid(session, version_uuid)
    if row.deleted_at is None:
        raise _bad_request("这一版不在回收站里")
    row.deleted_at = None
    has_current = int(
        (
            await session.scalar(
                sa_text("SELECT count(*) FROM script_versions WHERE project_key=:p AND is_current=true AND deleted_at IS NULL"),
                {"p": row.project_key},
            )
            or 0
        )
    )
    promoted_to_current = False
    if not has_current:
        await _demote(session, row.project_key)
        row.is_current = True
        promoted_to_current = True
    session.add(row)
    await session.commit()
    await session.refresh(row)
    payload = {
        "uuid": row.uuid,
        "promotedToCurrent": promoted_to_current,
        "current": _out(row, await _count(session, row.project_key)),
    }
    await _audit(
        session,
        user_id=getattr(actor, "id", None),
        actor=getattr(actor, "username", None),
        action="script.version.restore",
        target=f"script_version:{row.uuid}",
        request=request,
        detail={"projectKey": row.project_key, "version": row.seq, "promotedToCurrent": promoted_to_current},
    )
    await session.commit()
    return payload


@router.delete("/script-versions/{version_uuid}/purge", status_code=204)
async def purge_script_version(
    version_uuid: str,
    request: Request,
    actor: Any = Depends(dispatch_gate),
    session=Depends(get_session),
) -> None:
    """立即彻底删除这一版（剧本没有磁盘文件，删行就是全删）。必须先进过回收站。"""
    if not _has_db(request):
        raise _bad_request("无库模式没有 script_versions 表")
    row = await _by_uuid(session, version_uuid)
    if row.deleted_at is None:
        raise _bad_request("先在回收站里删掉它，才能彻底删除")
    pk, seq = row.project_key, row.seq
    await session.delete(row)
    await session.commit()
    await _audit(
        session,
        user_id=getattr(actor, "id", None),
        actor=getattr(actor, "username", None),
        action="script.version.purge",
        target=f"script_version:{version_uuid}",
        request=request,
        detail={"projectKey": pk, "version": seq},
    )
    await session.commit()
    log.info("剧本版 V%d 已彻底删除（%s）", seq, pk)


# ───────────────── 图片 / 视频的版本（就是 media 行的分组视图） ─────────────────

def _grp(alias: str = "") -> str:
    """版本分组键里 ref_id 那一段。

    `(project_key, kind, role, ref_id)` 是主口径；`ref_id` 为空的行（早期没打标签的任务）
    退回按 `jobs/<uuid8>` 目录成组 —— 那是 `_persist_outputs` 一定会写的路径段，不加字段就能用。
    不这么做的话整个项目的无标签产物会塌进同一个大组，看起来像"它们是同一件东西的历史"。
    """
    p = f"{alias}." if alias else ""
    return (
        f"COALESCE({p}ref_id, CASE WHEN split_part({p}path, '/', 1) = 'jobs'"
        f" THEN split_part({p}path, '/', 2) ELSE 'job-' || {p}id END)"
    )


def _grp_value(ref_id: str | None, path: str | None, media_id: int) -> str:
    """`_grp()` 的 Python 版。两处算的必须是同一个键，别只改一边。

    键的优先次序和 SQL 里的 COALESCE 一致：**有 ref_id 就用它**，没有才退回 job 目录。
    漏掉前一半（只算目录）会让所有打了标签的同组行都对不上键，剩余版数直接算成 0 —— 实测踩过。
    """
    if ref_id:
        return ref_id
    parts = (path or "").split("/")
    if parts[0] == "jobs" and len(parts) > 1:
        return parts[1]
    return f"job-{media_id}"


# 只有"生成的产物"参与版本历史：用户上传的参考图（origin.source='upload'）和合并导出
# （role='export'）都不是生成结果，混进来会让回收站里冒出用户自己传的图。
_GENERATED = (
    "kind IN ('image','video')"
    " AND (origin->>'source') IS DISTINCT FROM 'upload'"
    " AND COALESCE(role,'') <> 'export'"
)

# 序号在**没筛软删**的那一层算：WHERE 先于窗口求值，把 deleted_at IS NULL 写进同一层，
# 删掉 V2 就会让 V3 变成 V2。
_MEDIA_VERSIONS_SQL = f"""
WITH base AS (
    SELECT m.id, m.uuid, m.project_key, m.kind, m.role, m.ref_id, m.path, m.thumb_path,
           m.width, m.height, m.fps, m.duration_ms, m.bytes, m.mime, m.origin, m.deleted_at, m.created_at
      FROM media m
     WHERE {_GENERATED}
       AND (CAST(:pk AS varchar) IS NULL OR m.project_key = CAST(:pk AS varchar))
),
ranked AS (
    SELECT b.*,
           {_grp('b')} AS group_value,
           row_number() OVER (PARTITION BY b.project_key, b.kind, COALESCE(b.role, ''), {_grp('b')} ORDER BY b.id) AS version,
           count(*)     OVER (PARTITION BY b.project_key, b.kind, COALESCE(b.role, ''), {_grp('b')})              AS version_count
      FROM base b
)
SELECT id, uuid, project_key, kind, role, ref_id, path, thumb_path, width, height, fps, duration_ms,
       bytes, mime, origin, deleted_at, created_at, group_value, version, version_count
  FROM ranked
 WHERE (CAST(:bucket AS varchar) IS NULL OR kind = CAST(:bucket AS varchar))
   AND (CAST(:role AS varchar) IS NULL OR role = CAST(:role AS varchar))
   AND (CAST(:refid AS varchar) IS NULL OR ref_id = CAST(:refid AS varchar))
   AND CASE WHEN CAST(:only_del AS boolean) THEN deleted_at IS NOT NULL
            WHEN CAST(:include_del AS boolean) THEN TRUE
            ELSE deleted_at IS NULL END
 ORDER BY id DESC
 LIMIT :n
"""


def _media_version_out(r: Any) -> dict[str, Any]:
    origin = r["origin"] or {}
    return {
        "id": str(r["id"]),
        "uuid": r["uuid"],
        "projectKey": r["project_key"],
        "kind": r["kind"],
        "bucket": "video" if r["kind"] == "video" else "image",
        "role": r["role"],
        "refId": r["ref_id"],
        # 服务端实际用来分区的那把键（ref_id 为空时是 jobs/<目录>）。客户端分组必须用它，
        # 自己按 ref_id 再算一遍会把不同任务的无标签产物塌成一组，V 号就对不上
        "groupValue": r["group_value"],
        "path": r["path"],
        "bytes": r["bytes"],
        "mime": r["mime"],
        "width": r["width"],
        "height": r["height"],
        "fps": float(r["fps"]) if r["fps"] is not None else None,
        "durationMs": r["duration_ms"],
        "thumbPath": r["thumb_path"],
        "url": f"/api/media/{r['id']}/raw",
        "version": int(r["version"]),
        "versionCount": int(r["version_count"]),
        "deletedAt": _iso(r["deleted_at"]),
        "createdAt": _iso(r["created_at"]),
        # 产物的人类可读名（「定妆 · 林溪」「镜 3 首帧」）。全局页与回收站遇到"项目实体不在这台
        # 浏览器上"时只有这个能当标题，不然只能显示一串 refId
        "title": origin.get("jobTitle"),
        "instanceId": origin.get("instance_id"),
        **_purge_fields(r["deleted_at"]),
    }


@router.get("/media-versions")
async def list_media_versions(
    request: Request,
    project_key: str | None = None,
    all_projects: bool = False,
    bucket: str | None = None,
    role: str | None = None,
    ref_id: str | None = None,
    include_deleted: bool = False,
    only_deleted: bool = False,
    limit: int = 500,
    _: Any = Depends(login_gate),
    session=Depends(get_session),
) -> list[dict[str, Any]]:
    """把 media 行数成 V1..Vn。`include_deleted` 只决定哪些行出现在结果里，不重新编号。

    没扩展 `GET /api/media` 是刻意的：它三个调用方要的都是"只给活行"，塞一个开关会逼每个
    调用方重新过滤，还会把窗口函数压进生成期 2 秒轮询的热路径上。
    """
    if not _has_db(request):
        return []
    if not project_key and not all_projects:
        raise _bad_request("媒体版本必须给 project_key，或显式带 all_projects=true（否则等于拉全库产物）")
    if bucket not in (None, "image", "video"):
        raise _bad_request("bucket 只能是 image 或 video（剧本走 /api/script-versions）")
    rows = (
        await session.execute(
            sa_text(_MEDIA_VERSIONS_SQL),
            {
                "pk": project_key,
                "bucket": bucket,
                "role": role,
                "refid": ref_id,
                "only_del": only_deleted,
                "include_del": include_deleted,
                "n": min(max(limit, 1), 3000),
            },
        )
    ).mappings().all()
    return [_media_version_out(r) for r in rows]


async def _media_row(session: Any, media_id: int) -> Media:
    row = (await session.execute(select(Media).where(Media.id == media_id))).scalars().first()
    if row is None:
        raise _not_found(f"媒体 {media_id} 不在库里（可能还没渲染完，或已被回收）")
    return row


async def _group_stats(session: Any, row: Media) -> tuple[int, int | None, int]:
    """同组还剩几版（活）、最新存活的那版 id、这一行自己的版本号（含软删）。"""
    where = (
        f"COALESCE(project_key, '') = COALESCE(:pk, '') AND kind = :kind"
        f" AND COALESCE(role, '') = COALESCE(:role, '') AND {_grp()} = :g AND {_GENERATED}"
    )
    p = {"pk": row.project_key, "kind": row.kind, "role": row.role, "g": _grp_value(row.ref_id, row.path, int(row.id))}
    remaining = int(
        (await session.scalar(sa_text(f"SELECT count(*) FROM media WHERE {where} AND deleted_at IS NULL"), p)) or 0
    )
    candidate = await session.scalar(
        sa_text(f"SELECT id FROM media WHERE {where} AND deleted_at IS NULL ORDER BY id DESC LIMIT 1"), p
    )
    version = int(
        (
            await session.scalar(
                sa_text(f"SELECT count(*) FROM media WHERE {where} AND id <= :mid"),
                {**p, "mid": int(row.id)},
            )
            or 0
        )
    )
    return remaining, (int(candidate) if candidate is not None else None), version


@router.delete("/media/{media_id}")
async def trash_media(
    media_id: int,
    request: Request,
    actor: Any = Depends(dispatch_gate),
    session=Depends(get_session),
) -> dict[str, Any]:
    """把一版产物移进生成回收站（只写 deleted_at，文件继续留在盘上，100 天后才真删）。

    **响应必须自带 deref 需要的全部字段**：置了 deleted_at 之后，服务端所有读路径都会把这行
    藏起来，前端再也问不到它的 path/分组/候选版。顺序是「服务端软删 → 前端摘实体指针 →
    前端删本地索引行」，摘指针和删索引两步不能颠倒（先删索引会让一帧里 `refMediaIds[0]`
    指向已摘掉的行，每张卡片闪一次占位块）。
    """
    if not _has_db(request):
        raise _bad_request("无库模式没有 media 表")
    row = await _media_row(session, media_id)
    if row.deleted_at is not None:
        raise _bad_request("这一版已经在回收站里了")
    row.deleted_at = _now()
    session.add(row)
    await session.commit()
    await session.refresh(row)
    remaining, candidate, version = await _group_stats(session, row)
    payload = {
        "id": str(row.id),
        "projectKey": row.project_key,
        "kind": row.kind,
        "bucket": "video" if row.kind == "video" else "image",
        "role": row.role,
        "refId": row.ref_id,
        "version": version,
        "groupRemaining": remaining,
        "promoteCandidateId": str(candidate) if candidate else None,
        **_purge_fields(row.deleted_at),
    }
    await _audit(
        session,
        user_id=getattr(actor, "id", None),
        actor=getattr(actor, "username", None),
        action="trash.media",
        target=f"media:{row.id}",
        request=request,
        detail={"projectKey": row.project_key, "role": row.role, "refId": row.ref_id, "version": version, "groupRemaining": remaining},
    )
    await session.commit()
    log.info("产物 %s 进回收站（%s/%s，同组还剩 %d 版）", row.id, row.role, row.ref_id, remaining)
    return payload


@router.post("/media/{media_id}/restore")
async def restore_media(
    media_id: int,
    request: Request,
    actor: Any = Depends(dispatch_gate),
    session=Depends(get_session),
) -> dict[str, Any]:
    """从回收站恢复一版产物。文件已经不在盘上就报 410，不要恢复出一个点开是 404 的版本。"""
    if not _has_db(request):
        raise _bad_request("无库模式没有 media 表")
    row = await _media_row(session, media_id)
    if row.deleted_at is None:
        raise _bad_request("这一版不在回收站里")
    root = media_root()
    target = (root / row.path).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        raise HTTPException(410, "文件已经不在磁盘上（可能已被回收），这一版恢复不回来了")
    row.deleted_at = None
    session.add(row)
    await session.commit()
    await session.refresh(row)
    remaining, candidate, version = await _group_stats(session, row)
    payload = {"id": str(row.id), "version": version, "groupRemaining": remaining, "restored": True}
    await _audit(
        session,
        user_id=getattr(actor, "id", None),
        actor=getattr(actor, "username", None),
        action="trash.restore",
        target=f"media:{row.id}",
        request=request,
        detail={"projectKey": row.project_key, "role": row.role, "refId": row.ref_id, "version": version},
    )
    await session.commit()
    return payload


@router.delete("/media/{media_id}/purge")
async def purge_media(
    media_id: int,
    request: Request,
    actor: Any = Depends(dispatch_gate),
    session=Depends(get_session),
) -> dict[str, Any]:
    """立即彻底删除一版产物（连文件）。必须先进过回收站 —— 绕过回收站的"彻底删除"不该存在。"""
    if not _has_db(request):
        raise _bad_request("无库模式没有 media 表")
    row = await _media_row(session, media_id)
    if row.deleted_at is None:
        raise _bad_request("先在回收站里删掉它，才能彻底删除")
    info = {"projectKey": row.project_key, "role": row.role, "refId": row.ref_id, "bytes": row.bytes_}
    report = await purge_media_rows([media_id])
    await _audit(
        session,
        user_id=getattr(actor, "id", None),
        actor=getattr(actor, "username", None),
        action="trash.purge_row",
        target=f"media:{media_id}",
        request=request,
        detail={**info, **report.as_dict()},
    )
    await session.commit()
    return report.as_dict()


@router.get("/trash")
async def list_trash(
    request: Request,
    project_key: str | None = None,
    all_projects: bool = False,
    bucket: str | None = None,
    limit: int = 500,
    _: Any = Depends(login_gate),
    session=Depends(get_session),
) -> dict[str, Any]:
    """生成回收站：软删的产物 + 软删的剧本版本，按删除时间倒序。

    每一项都自带 `purgeAfter`/`daysLeft`，界面才答得出"这条还能反悔几天"。
    """
    if not _has_db(request):
        return {"retentionDays": get_settings().trash_retention_days, "totalBytes": 0, "items": []}
    if not project_key and not all_projects:
        raise _bad_request("回收站必须给 project_key，或显式带 all_projects=true")
    if bucket not in (None, "script", "image", "video"):
        raise _bad_request("bucket 只能是 script / image / video")

    items: list[dict[str, Any]] = []
    if bucket in (None, "image", "video"):
        rows = (
            await session.execute(
                sa_text(_MEDIA_VERSIONS_SQL),
                {
                    "pk": project_key,
                    "bucket": bucket,
                    "role": None,
                    "refid": None,
                    "only_del": True,
                    "include_del": False,
                    "n": min(max(limit, 1), 3000),
                },
            )
        ).mappings().all()
        for r in rows:
            m = _media_version_out(r)
            # kind 放在 **m 之后：_media_version_out 里那一份是媒体自己的 image/video，
            # 先写 "media" 会被盖掉，前端就分不出这是产物还是剧本版（媒体自己是 image/video
            # 这件事由 bucket 说）。顺序反过一次，回收站预览因此瞎了。
            items.append({"key": f"media:{m['id']}", **m, "kind": "media", "textPreview": None})

    if bucket in (None, "script") and project_key:
        srows = (
            await session.execute(
                select(ScriptVersion)
                .where(ScriptVersion.project_key == project_key, ScriptVersion.deleted_at.isnot(None))
                .order_by(ScriptVersion.seq.desc())
                .limit(min(max(limit, 1), 3000))
            )
        ).scalars().all()
        total = await _count(session, project_key)
        for v in srows:
            o = _out(v, total)
            items.append(
                {
                    "key": f"script:{o['uuid']}",
                    "kind": "script",
                    "bucket": "script",
                    "id": o["id"],
                    "uuid": o["uuid"],
                    "projectKey": o["projectKey"],
                    "role": None,
                    "refId": None,
                    "title": None,
                    "version": o["version"],
                    "versionCount": o["versionCount"],
                    "bytes": len(o["text"].encode("utf-8")),
                    "url": None,
                    "deletedAt": o["deletedAt"],
                    "createdAt": o["createdAt"],
                    "writtenAt": o["writtenAt"],
                    "purgeAfter": o["purgeAfter"],
                    "daysLeft": o["daysLeft"],
                    "retentionDays": o["retentionDays"],
                    "textPreview": o["text"][:200],
                }
            )

    items.sort(key=lambda x: (x["deletedAt"] or ""), reverse=True)
    names = await _project_names(session)
    for it in items:
        # 项目实体在浏览器 IndexedDB 里，服务端认不出项目名；彻底删掉的项目就退回这张表
        it["projectName"] = names.get(it.get("projectKey") or "")
    return {
        "retentionDays": get_settings().trash_retention_days,
        "totalBytes": sum(int(x.get("bytes") or 0) for x in items),
        "items": items[: min(max(limit, 1), 3000)],
    }


async def _project_names(session: Any) -> dict[str, str]:
    """app_settings['project_names'] → {project_key: 项目名}。没有就当空表。"""
    raw = await session.scalar(sa_text("SELECT value FROM app_settings WHERE key='project_names'"))
    return {str(k): str(v) for k, v in (raw or {}).items()}


class ProjectTrashBody(CamelModel):
    name: str | None = None


@router.post("/projects/{project_key}/trash")
async def trash_project(
    project_key: str,
    request: Request,
    body: ProjectTrashBody,
    actor: Any = Depends(dispatch_gate),
    session=Depends(get_session),
) -> dict[str, Any]:
    """项目被删时，把它的产物与剧本版本整批送进生成回收站。

    以前删项目只删浏览器里的索引行，服务端那些文件谁都不管 —— 那正是 `media.deleted_at`
    建了列却一直没有写入方的原因。整批进回收站之后，100 天的自动回收才真的接得上。

    `name` 会记进 app_settings['project_names']：项目实体没了以后，回收站还得说得出这一条属于谁。
    """
    if not _has_db(request):
        raise _bad_request("无库模式没有 media / script_versions 表")
    media_ids = (
        await session.execute(
            sa_text("UPDATE media SET deleted_at=now() WHERE project_key=:p AND deleted_at IS NULL RETURNING id, bytes"),
            {"p": project_key},
        )
    ).mappings().all()
    # 剧本这边必须同时清 is_current：部分唯一索引只排除软删行，留着标记会占住「当前」的坑
    script_ids = (
        await session.execute(
            sa_text(
                "UPDATE script_versions SET deleted_at=now(), is_current=false"
                " WHERE project_key=:p AND deleted_at IS NULL RETURNING id"
            ),
            {"p": project_key},
        )
    ).scalars().all()
    if body.name:
        names = await _project_names(session)
        names[project_key] = body.name
        await session.execute(
            sa_text(
                "INSERT INTO app_settings(key, value, updated_at) VALUES('project_names', CAST(:v AS jsonb), now())"
                " ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=now()"
            ),
            {"v": json.dumps(names, ensure_ascii=False)},
        )
    payload = {
        "mediaTrashed": len(media_ids),
        "scriptTrashed": len(script_ids),
        "bytes": sum(int(r["bytes"] or 0) for r in media_ids),
    }
    await _audit(
        session,
        user_id=getattr(actor, "id", None),
        actor=getattr(actor, "username", None),
        action="trash.project",
        target=f"project:{project_key}",
        request=request,
        detail={**payload, "name": body.name},
    )
    await session.commit()
    log.info("项目 %s 的产物进回收站：%d 个媒体 / %d 版剧本", project_key, payload["mediaTrashed"], payload["scriptTrashed"])
    return payload
