"""任务路由。

有库模式：POST /jobs 只入队（jobs 表），真正提交由 QueueDispatcher 的
FOR UPDATE SKIP LOCKED 循环做 —— 关页面、重启后端都不影响任务收口。
无库模式：直投实例，进程内登记表记住 job↔实例，功能只够开发时试工作流。
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, AsyncIterator, Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, Request, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, text

from ..db import get_session, session_factory
from ..gen.base import GenError, OutputRef
from ..logging_setup import get_logger, redact
from ..models import Job, JobState as JS, Media
from ..security import dispatch_gate, login_gate
from .common import CamelModel

log = get_logger("api.jobs")
router = APIRouter(tags=["jobs"])

# 无库模式下：prompt_id -> instance_id。有库时这个对应关系在 jobs 表里。
_ledger: dict[str, str] = {}


def _registry(request: Request):
    return request.app.state.registry


def _has_db(request: Request) -> bool:
    return bool(request.app.state.settings.database_url)


def _resolve_instance(request: Request, prompt_id: str, instance_id: str | None) -> str:
    if instance_id:
        return instance_id
    known = _ledger.get(prompt_id)
    if known:
        return known
    raise _bad_request(f"不知道 prompt_id {prompt_id} 属于哪个实例；请带上 instance_id")


def _bad_request(detail: str):
    from fastapi import HTTPException

    return HTTPException(status_code=400, detail=detail)


class JobCreate(CamelModel):
    """两种入队形态：直接给 graph（工作流页的高级用法），或给 template+slots（页面按钮）。

    页面按钮一律走后者：模型文件名要由实例回答，参考图要先上传到该实例，
    这些都在服务端才做得动。instanceId 可以省略，但只有「唯一一台可用实例」时才省略 ——
    猜错实例的代价是白烧十几分钟显存，宁可报一条让用户看得懂的 400。
    """

    instance_id: str | None = None
    graph: dict[str, Any] | None = None
    template: str | None = None
    slots: dict[str, Any] | None = None
    kind: Literal["image", "video", "video_chain", "upscale", "workflow_test"] = "video"
    title: str | None = Field(None, max_length=200)
    project_key: str | None = Field(None, max_length=64, description="前端 IndexedDB 里的项目 id")
    workflow_id: int | None = None
    priority: int = Field(100, ge=0, le=999)
    # {"role":"character_ref","refId":"char-3"} —— 产物落库时打成这个标签，资产库才认得出是谁的图
    meta: dict[str, Any] | None = None


class JobBatchCreate(CamelModel):
    jobs: list[JobCreate] = Field(..., min_length=1, max_length=100)


async def _instance_infos(request: Request, session) -> dict[str, Any]:
    """实例信息一次查齐。无库模式没有表，退回 registry 配置（探活状态未知，就不拿它拦人）。"""
    from ..job_plan import InstanceInfo

    infos: dict[str, Any] = {}
    registry = _registry(request)
    if _has_db(request):
        try:
            rows = (await session.execute(text("SELECT id, name, protocol, placement, last_probe_ok, circuit_open FROM gen_instances ORDER BY id"))).all()
        except Exception:
            rows = []
        for rid, name, protocol, placement, probe_ok, circuit in rows:
            infos[str(rid)] = InstanceInfo(id=str(rid), label=name, placement=placement, protocol=protocol, probe_ok=probe_ok, circuit_open=bool(circuit))
    if not infos:
        for cid in registry.ids:
            cfg = registry.config(cid)
            infos[str(cid)] = InstanceInfo(id=str(cid), label=cfg.name, placement=cfg.placement, protocol=cfg.protocol)
    return infos


async def _plan_for(request: Request, session, items: list[JobCreate]) -> list[dict[str, Any]]:
    """参数表 = /jobs/plan 的返回，也是 /jobs 与 /jobs/batch 的准入门禁（同一套判断）。"""
    from ..job_plan import InstanceInfo, media_ids_in, plan_jobs

    payloads = [i.model_dump(by_alias=True, exclude_none=True) for i in items]
    infos = await _instance_infos(request, session)
    known: set[int] | None = None
    wanted = media_ids_in(payloads)
    if wanted and _has_db(request):
        try:
            found = (await session.execute(text("SELECT id FROM media WHERE id = ANY(:ids) AND deleted_at IS NULL"), {"ids": list(wanted)})).scalars().all()
            known = {int(x) for x in found}
        except Exception:
            known = None
    out = plan_jobs(payloads, instances=infos, known_media=known)
    out["instances"] = [i.as_dict() if isinstance(i, InstanceInfo) else i for i in infos.values()]
    return out["rows"]


@router.post("/jobs/plan")
async def plan_jobs_endpoint(
    body: JobBatchCreate, request: Request, _: Any = Depends(login_gate), session=Depends(get_session)
) -> dict[str, Any]:
    """派发前的参数确认表：逐条参数、问题清单、耗时外推。只读，不入队、不写库。"""
    rows = await _plan_for(request, session, body.jobs)
    eta = sum(int((r.get("derived") or {}).get("etaSeconds") or 0) for r in rows)
    blocked = sum(1 for r in rows if r.get("blocked"))
    return {
        "rows": rows,
        "totals": {
            "count": len(rows),
            "blocked": blocked,
            "warned": sum(1 for r in rows if r.get("problems") and not r.get("blocked")),
            "etaSeconds": eta,
            "etaMinutes": round(eta / 60, 1),
            # 界面上必须把这两个字显示出来：这些数字是外推，不是实测
            "etaIsEstimate": True,
        },
    }


def _pick_instance(request: Request, body: JobCreate) -> str:
    if body.instance_id:
        return str(body.instance_id)
    ids = list(_registry(request).ids)
    if len(ids) == 1:
        return ids[0]
    raise _bad_request(
        f"没有生成实例可用" if not ids else f"有 {len(ids)} 台实例，必须指明用哪一台（instanceId）"
    )


def _job_body_to_params(body: JobCreate) -> dict[str, Any]:
    if not body.graph and not body.template:
        raise _bad_request("要么给 graph（API 格式工作流图），要么给 template + slots")
    params: dict[str, Any] = {"meta": body.meta or {}}
    if body.template and not body.graph:
        from ..gen.templates import TEMPLATES

        if body.template not in TEMPLATES:
            raise _bad_request(f"没有生成模板 {body.template}（可用：{'、'.join(TEMPLATES)}）")
        params.update({"template": body.template, "slots": body.slots or {}})
        return params
    params["graph"] = body.graph
    return params


async def _enqueue(request: Request, session, actor: Any, body: JobCreate, *, checked: bool = False) -> dict[str, Any]:
    from ..gen.base import GenError

    instance_id = _pick_instance(request, body)
    params = _job_body_to_params(body)
    # 参数表门禁：template+slots 这条路先按 SlotSpec 校验再入队，graph 那条交给下面的 missing_models
    if not checked and body.template and not body.graph:
        rows = await _plan_for(request, session, [body])
        if rows and rows[0].get("blocked"):
            raise _bad_request("参数不合格，没有入队：" + "；".join(rows[0].get("problems") or [])[:300])
    if params.get("graph"):
        try:
            client = _registry(request).client(instance_id)
        except (KeyError, NotImplementedError) as exc:
            raise _bad_request(str(exc)) from exc
        # 权重缺失在这里就查掉：入队后才发现少文件，用户看到的是一团黑排队
        missing = await client.missing_models(params["graph"])
        if missing:
            raise _bad_request(redact("；".join(missing)))

    if not _has_db(request):
        client = _registry(request).client(instance_id)
        client_id = str(uuid.uuid4())
        try:
            sub = await client.submit(params["graph"], client_id=client_id, job_ref=None)
        except GenError as exc:
            raise _bad_request(redact(exc.message)) from exc
        _ledger[sub.job_ref] = instance_id
        return {"promptId": sub.job_ref, "clientId": client_id, "instanceId": instance_id, "queued": False}

    job = await request.app.state.dispatcher.submit_job(
        kind=body.kind,
        title=body.title or f"{body.kind} · {instance_id}",
        params=params,
        instance_id=instance_id,
        project_key=body.project_key,
        owner_id=getattr(actor, "id", None),
        workflow_id=body.workflow_id,
        priority=body.priority,
    )
    await _audit_dispatch(request, session, actor, job)
    out = _job_out(job)
    out["queued"] = True
    return out


@router.post("/jobs/batch")
async def create_jobs(body: JobBatchCreate, request: Request, actor: Any = Depends(dispatch_gate), session=Depends(get_session)) -> dict[str, Any]:
    """批量派发：一条镜头一个任务。

    故意不做「一个任务出 N 张图」：队列的实例互斥是任务级的，捆在一起就没法单独重试/取消，
    进度也只能显示一个平均值。

    派发前先过一遍参数表（和 /jobs/plan 同一套判断）：被拦下的那条不进队列，
    原因逐条回给前端，不让它变成十几分钟后一条 ComfyUI 报错。
    """
    rows = await _plan_for(request, session, body.jobs)
    outs = []
    errors = []
    for i, (item, row) in enumerate(zip(body.jobs, rows, strict=False)):
        if row.get("blocked"):
            errors.append({"index": i, "title": item.title, "error": "；".join(row.get("problems") or ["参数不合格"])[:300]})
            continue
        try:
            outs.append(await _enqueue(request, session, actor, item, checked=True))
        except Exception as exc:  # 逐条入队：前面几条不该因为后面一条的参数错而回滚
            detail = getattr(exc, "detail", None) or str(exc)
            errors.append({"index": i, "title": item.title, "error": str(detail)[:300]})
    return {"jobs": outs, "errors": errors, "accepted": len(outs), "rejected": len(errors)}



def _job_out(job: Job, media_ids: list[int] | None = None) -> dict[str, Any]:
    state = job.state.value if isinstance(job.state, JS) else job.state
    return {
        "id": job.uuid,
        "jobId": job.uuid,
        "promptId": job.prompt_id,
        "instanceId": str(job.instance_id) if job.instance_id else None,
        "projectKey": job.project_key,
        "kind": job.kind.value if hasattr(job.kind, "value") else job.kind,
        "title": job.title,
        "state": state,
        "terminal": state in {"succeeded", "failed", "canceled"},
        "priority": job.priority,
        "attempts": job.attempts,
        "maxAttempts": job.max_attempts,
        "progress": job.progress or {},
        "queuePosition": job.queue_pos,
        "output": list(job.output or []),
        "mediaIds": media_ids if media_ids is not None else list(job.output or []),
        "cost": job.cost or {},
        "error": job.error,
        "startedAt": job.started_at.isoformat() if job.started_at else None,
        "finishedAt": job.finished_at.isoformat() if job.finished_at else None,
        "createdAt": job.created_at.isoformat() if job.created_at else None,
    }


@router.post("/jobs")
async def create_job(
    body: JobCreate, request: Request, actor: Any = Depends(dispatch_gate), session=Depends(get_session)
) -> dict[str, Any]:
    """入队（有库）或直投（无库）。见 JobCreate：graph 与 template 二选一。"""
    return await _enqueue(request, session, actor, body)


async def _audit_dispatch(request: Request, session, actor: Any, job: Job) -> None:
    if actor is None:
        return
    from .routes_auth import _audit

    await _audit(
        session,
        user_id=actor.id,
        actor=actor.username,
        action="job.dispatch",
        target=str(job.uuid),
        request=request,
        detail={"instance_id": str(job.instance_id), "kind": job.kind.value if hasattr(job.kind, "value") else job.kind},
    )


@router.get("/jobs")
async def list_jobs(
    request: Request,
    state: str | None = None,
    project_key: str | None = None,
    limit: int = 100,
    _: Any = Depends(login_gate),
    session=Depends(get_session),
) -> list[dict[str, Any]]:
    if not _has_db(request):
        return []
    stmt = select(Job).order_by(Job.id.desc()).limit(min(max(limit, 1), 500))
    if state:
        stmt = stmt.where(Job.state == state)
    if project_key:
        stmt = stmt.where(Job.project_key == project_key)
    rows = (await session.execute(stmt)).scalars().all()
    return [_job_out(r) for r in rows]


@router.get("/jobs/{job_id}")
async def job_state(
    job_id: str,
    request: Request,
    instance_id: str | None = None,
    _: Any = Depends(login_gate),
    session=Depends(get_session),
) -> dict[str, Any]:
    """有库时按 jobs.uuid 查（这才是前端拿得住的 id），查不到再按 prompt_id 直问实例。

    直问那条路是无库模式留下的：队列层的收口逻辑不依赖它，所以它没有记账。
    """
    if _has_db(request):
        row = (
            await session.execute(select(Job).where(Job.uuid == job_id))
        ).scalars().first()
        if row is None and job_id.count("-") == 4:
            row = (await session.execute(select(Job).where(Job.prompt_id == job_id))).scalars().first()
        if row is not None:
            out = _job_out(row)
            paths: dict[int, str] = {}
            if row.output:
                # 按 id 配对，不能靠 zip —— IN 查询不保证返回顺序
                paths = {
                    mid: p
                    for mid, p in (
                        await session.execute(select(Media.id, Media.path).where(Media.id.in_(list(row.output))))
                    ).all()
                }
            out["outputs"] = [{"mediaId": mid, "path": paths.get(mid)} for mid in list(row.output or [])]
            return out
        raise _not_found(f"任务 {job_id} 不在库里")

    from ..gen.base import Submission

    registry = _registry(request)
    inst = _resolve_instance(request, job_id, instance_id)
    client = registry.client(inst)
    snap = await client.snapshot(Submission(job_ref=job_id, client_id=None))
    return {
        "promptId": job_id,
        "instanceId": inst,
        "state": snap.state.value,
        "terminal": snap.state.terminal,
        "progress": {
            "value": snap.progress.value,
            "max": snap.progress.max,
            "node": snap.progress.node,
            "stage": snap.progress.stage,
            "percentageUnavailable": snap.progress.percentage_unavailable,
        },
        "queuePosition": snap.queue_position,
        "outputs": [o.as_dict() for o in snap.outputs],
        "mediaIds": [],
        "error": snap.error,
    }


def _not_found(detail: str):
    from fastapi import HTTPException

    return HTTPException(status_code=404, detail=detail)


@router.get("/jobs/{job_id}/stream")
async def job_stream(
    job_id: str,
    request: Request,
    instance_id: str | None = None,
    client_id: str | None = None,
    _: Any = Depends(login_gate),
) -> StreamingResponse:
    """把 ComfyUI 的 WS 事件归一化后转成 SSE 给浏览器。

    浏览器永远不直连 ComfyUI：它拿不到 client_id 与实例地址，也不该拿到。
    有库模式下这里按 jobs.uuid 找出 prompt_id/client_id；SSE 只是观察，
    任务收口在派发循环里，断流不影响结果。
    """
    registry = _registry(request)
    if _has_db(request):
        async with session_factory()() as s:
            row = (await s.execute(select(Job).where(Job.uuid == job_id))).scalars().first()
        if row is None:
            raise _not_found(f"任务 {job_id} 不在库里")
        if not row.prompt_id:
            raise _bad_request("任务还没拿到 prompt_id（仍在排队），先等派发再订阅事件")
        inst = str(row.instance_id)
        cid = client_id or row.client_id or str(uuid.uuid4())
        job_ref = row.prompt_id
    else:
        inst = _resolve_instance(request, job_id, instance_id)
        cid = client_id or str(uuid.uuid4())
        job_ref = job_id
    client = registry.client(inst)

    async def gen() -> AsyncIterator[str]:
        try:
            async for etype, payload in client.events(cid):
                data = {
                    "type": etype,
                    "promptId": payload.get("prompt_id"),
                    "jobId": job_id,
                    "node": payload.get("node"),
                    "value": payload.get("value"),
                    "max": payload.get("max"),
                }
                yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
                # 终止判据：executing 且 node 为 null。带上 prompt_id 一起判，
                # 是因为同一个 clientId 上如果排了多个任务，别人的收尾不该关掉这条流。
                if etype == "executing" and payload.get("node") is None and payload.get("prompt_id") in (None, job_ref):
                    yield 'data: {"type":"__done__"}\n\n'
                    return
        except GenError as exc:
            yield f"data: {json.dumps({'type': 'error', 'message': redact(exc.message)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


async def _media_file(media_id: int, request: Request) -> tuple[Path, str]:
    """按 §10 的契约读 media_root 里的产物。

    path 是库里的字符串，而库里的值来自实例返回的文件名 —— 目录穿越必须在读接口上挡死。
    """
    if not _has_db(request):
        raise _bad_request("无库模式没有 media 表，产物请走 /api/instances/{id}/output")
    async with session_factory()() as s:
        row = await s.get(Media, media_id)
    if row is None or row.deleted_at is not None:
        raise _not_found(f"媒体 {media_id} 不存在")
    root = Path(request.app.state.settings.media_root).resolve()
    target = (root / row.path).resolve()
    if not target.is_relative_to(root):
        log.warning("媒体 %s 路径越界已被拦下：%s", media_id, row.path)
        raise _not_found("媒体路径不合法")
    if not target.is_file():
        raise _not_found("产物文件不在磁盘上（可能被清理，或当次派发没落盘）")
    return target, _MEDIA_TYPES.get(target.suffix.lower(), row.mime or "application/octet-stream")


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(
    job_id: str, request: Request, actor: Any = Depends(dispatch_gate), session=Depends(get_session)
) -> dict[str, Any]:
    """取消。排队中的直接收口；已经在实例上跑的先去 interrupt，再把库里写成 canceled。

    实例那边报「找不到这个 prompt_id」不算失败 —— 我们的目的就是不让它继续占 GPU，
    它已经不在了正好，照样把任务收口。
    """
    from ..gen.base import Submission

    if not _has_db(request):
        raise _bad_request("无库模式没有任务表，请直接在 ComfyUI 的队列里删")
    row = (await session.execute(select(Job).where(Job.uuid == job_id))).scalars().first()
    if row is None:
        raise _not_found(f"任务 {job_id} 不在库里")
    state = row.state.value if isinstance(row.state, JS) else row.state
    if state in {"succeeded", "failed", "canceled"}:
        raise _bad_request(f"任务已经收口（{state}），没有可取消的东西")

    if row.prompt_id and row.instance_id:
        try:
            await _registry(request).client(str(row.instance_id)).cancel(Submission(job_ref=row.prompt_id, client_id=row.client_id))
        except GenError as exc:
            log.info("任务 %s 在实例上取消时报错（继续收口）：%s", str(job_id)[:8], redact(exc.message)[:160])
        except Exception as exc:
            # 实例卡死/断连时正是最需要取消的时候：那边通不通不该让收口失败，
            # 更不能把它冒成 500（用户只会看到「取消失败」而任务还在烧卡）。
            log.info("任务 %s 取消时连不上实例（继续收口）：%s", str(job_id)[:8], redact(str(exc))[:160])

    await session.execute(
        text("UPDATE jobs SET state='canceled', finished_at=now(), error=:e WHERE id=:id"),
        {"id": row.id, "e": _error_json("canceled", "已在页面上取消")},
    )
    await session.execute(text("DELETE FROM instance_locks WHERE job_id=:j"), {"j": row.id})
    await session.commit()
    await session.refresh(row)
    from .routes_auth import _audit

    if actor is not None:
        await _audit(session, user_id=actor.id, actor=actor.username, action="job.cancel", target=str(job_id), request=request)
    return _job_out(row)


def _error_json(kind: str, message: str) -> str:
    return json.dumps({"type": kind, "message": message, "retryable": False}, ensure_ascii=False)


@router.post("/jobs/{job_id}/retry")
async def retry_job(
    job_id: str, request: Request, actor: Any = Depends(dispatch_gate), session=Depends(get_session)
) -> dict[str, Any]:
    """重试。只收口过的任务能重跑，并且把 attempts 归零 ——
    不清零的话用户改完工作流再点重试，一次的额度都没有。
    """
    if not _has_db(request):
        raise _bad_request("无库模式没有任务表，重试请重新提交")
    row = (await session.execute(select(Job).where(Job.uuid == job_id))).scalars().first()
    if row is None:
        raise _not_found(f"任务 {job_id} 不在库里")
    state = row.state.value if isinstance(row.state, JS) else row.state
    if state not in {"succeeded", "failed", "canceled"}:
        raise _bad_request(f"任务还在 {state}，先取消再重试")
    await session.execute(
        text(
            """
            UPDATE jobs SET state='queued', attempts=0, error=NULL, prompt_id=NULL, client_id=NULL,
                            progress='{}'::jsonb, queue_pos=NULL, started_at=NULL, finished_at=NULL
             WHERE id=:id
            """
        ),
        {"id": row.id},
    )
    await session.execute(text("DELETE FROM instance_locks WHERE job_id=:j"), {"j": row.id})
    await session.commit()
    await session.refresh(row)
    from .routes_auth import _audit

    if actor is not None:
        await _audit(session, user_id=actor.id, actor=actor.username, action="job.retry", target=str(job_id), request=request)
    return _job_out(row)


class JobPatch(CamelModel):
    priority: int | None = Field(None, ge=0, le=999)
    title: str | None = Field(None, max_length=200)


@router.patch("/jobs/{job_id}")
async def patch_job(job_id: str, body: JobPatch, _: Any = Depends(dispatch_gate), session=Depends(get_session)) -> dict[str, Any]:
    """改优先级/标题。排队中的任务改优先级才有意义，已收口的直接拒。"""
    row = (await session.execute(select(Job).where(Job.uuid == job_id))).scalars().first()
    if row is None:
        raise _not_found(f"任务 {job_id} 不在库里")
    state = row.state.value if isinstance(row.state, JS) else row.state
    if state in {"succeeded", "failed", "canceled"}:
        raise _bad_request(f"任务已收口（{state}），改不了")
    if body.priority is not None:
        row.priority = body.priority
    if body.title is not None:
        row.title = body.title
    await session.commit()
    await session.refresh(row)
    return _job_out(row)


@router.get("/media")
async def list_media(
    request: Request,
    project_key: str | None = None,
    role: str | None = None,
    ref_id: str | None = None,
    kind: str | None = None,
    ids: str | None = Query(None, description="逗号分隔的 media id，任务成功后按 output 精确取回"),
    limit: int = 200,
    _: Any = Depends(login_gate),
    session=Depends(get_session),
) -> list[dict[str, Any]]:
    """按项目/标签列产物。项目实体在浏览器 IndexedDB 里，这里只按软引用 project_key 过滤。"""
    if not _has_db(request):
        return []
    stmt = select(Media).order_by(Media.id.desc()).limit(min(max(limit, 1), 1000))
    if ids:
        wanted = [int(x) for x in ids.split(",") if x.strip().isdigit()]
        if not wanted:
            return []
        stmt = stmt.where(Media.id.in_(wanted))
    if project_key:
        stmt = stmt.where(Media.project_key == project_key)
    if role:
        stmt = stmt.where(Media.role == role)
    if ref_id:
        stmt = stmt.where(Media.ref_id == ref_id)
    if kind:
        stmt = stmt.where(Media.kind == kind)
    stmt = stmt.where(Media.deleted_at.is_(None))
    return [_media_out(m) for m in (await session.execute(stmt)).scalars()]


def _media_out(m: Media) -> dict[str, Any]:
    return {
        "id": str(m.id),
        "uuid": m.uuid,
        "projectKey": m.project_key,
        "kind": m.kind,
        "role": m.role,
        "refId": m.ref_id,
        "path": m.path,
        "bytes": m.bytes_,
        "mime": m.mime,
        "width": m.width,
        "height": m.height,
        "url": f"/api/media/{m.id}/raw",
        "createdAt": m.created_at.isoformat() if m.created_at else None,
    }



@router.post("/media/upload", status_code=201)
async def upload_media(
    request: Request,
    file: UploadFile,
    project_key: str | None = None,
    role: str = "ref_image",
    ref_id: str | None = None,
    actor: Any = Depends(login_gate),
    session=Depends(get_session),
) -> dict[str, Any]:
    """用户上传的参考图落进服务端媒体库。

    只留在浏览器里当不了参考图：出图时 ComfyUI 要能从磁盘读到它，
    所以「上传图片」这个按钮背后必须是真落盘，而不是 createObjectURL。
    """
    if not _has_db(request):
        raise _bad_request("无库模式没有 media 表，请配置 H3_DATABASE_URL 后再上传")
    from ..models import Media

    data = await file.read()
    if not data:
        raise _bad_request("文件是空的")
    limit = 64 * 1024 * 1024
    if len(data) > limit:
        raise _bad_request(f"文件 {len(data) / 1024 / 1024:.1f} MB 超过 {limit // 1024 // 1024} MB 上限")
    suffix = Path(file.filename or "upload").suffix.lower() or ".bin"
    key = str(uuid.uuid4())
    rel = f"uploads/{key[:2]}/{key}{suffix}"
    root = Path(request.app.state.settings.media_root)
    dest = root / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)

    kind = "video" if suffix in (".mp4", ".webm", ".mov") else "image"
    row = Media(
        uuid=key,
        project_key=project_key,
        owner_id=getattr(actor, "id", None),
        kind=kind,
        role=role,
        ref_id=ref_id,
        path=rel,
        bytes_=len(data),
        mime=file.content_type or "application/octet-stream",
        origin={"source": "upload", "filename": file.filename},
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return _media_out(row)


@router.get("/media/{media_id}/raw")
async def media_raw(media_id: int, request: Request, _: Any = Depends(login_gate)) -> FileResponse:
    """预览用。FileResponse 自带 Range，所以 <video> 能拖进度条。

    文件名可能含中文（合并成片用项目名命名），HTTP 头只能 latin-1，
    所以走 RFC 5987：给一个 ASCII 兜底 filename + 百分号编码的 filename*。
    """
    target, mime = await _media_file(media_id, request)
    ascii_fallback = target.name.encode("ascii", "ignore").decode().replace('"', "").strip() or "media"
    disposition = f'inline; filename="{ascii_fallback}"; filename*=UTF-8\'\'{quote(target.name)}'
    return FileResponse(target, media_type=mime, headers={"Content-Disposition": disposition})


@router.get("/media/{media_id}/download")
async def media_download(media_id: int, request: Request, _: Any = Depends(login_gate)) -> FileResponse:
    """资产下载（阶段④）。Content-Disposition 交给 FileResponse 生成。"""
    target, mime = await _media_file(media_id, request)
    return FileResponse(target, media_type=mime, filename=target.name)


# <video>/<img> 要靠正确的 MIME 才会内联播放，落到 octet-stream 就变成下载
_MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".flac": "audio/flac",
}


@router.post("/instances/{instance_id}/upload")
async def upload(instance_id: str, request: Request, file: UploadFile, _: Any = Depends(dispatch_gate)) -> dict[str, Any]:
    """音频/视频也走这里 —— ComfyUI 没有 /upload/audio。"""
    registry = _registry(request)
    try:
        client = registry.client(instance_id)
    except (KeyError, NotImplementedError) as exc:
        raise _bad_request(str(exc)) from exc
    tmp = Path(request.app.state.settings.tmp_root) / f"{uuid.uuid4().hex}_{file.filename}"
    tmp.write_bytes(await file.read())
    try:
        name = await client.upload(tmp, type="input")
    finally:
        tmp.unlink(missing_ok=True)
    return {"fileName": name}


@router.get("/instances/{instance_id}/output")
async def output_bytes(instance_id: str, request: Request, filename: str, subfolder: str = "", type: str = "output") -> Response:
    """产物一律经后端转存，绝不让浏览器知道实例地址，也绝不存会过期的外链。"""
    registry = _registry(request)
    client = registry.client(instance_id)
    data = await client.fetch_output(OutputRef(filename=filename, subfolder=subfolder, type=type))
    media = "image/png"
    if filename.lower().endswith((".mp4", ".webm", ".mov")):
        media = "video/mp4"
    elif filename.lower().endswith((".mp3", ".flac", ".wav")):
        media = "audio/mpeg"
    elif filename.lower().endswith(".webp"):
        media = "image/webp"
    return Response(content=data, media_type=media, headers={"Content-Disposition": f'inline; filename="{filename}"'})
