"""实例路由：配置增删改查（仅 admin）+ 探活 + 直投。

约定：任何返回给前端的 base_url 都不含 RunningHub 的 apiKey —— key 在路径里，
一旦吐出去就进浏览器 history / 前端日志了。只回 api_key_masked + 脱敏后的 URL。
apiKey 用 Fernet 加密存 gen_instances.api_key_enc，解密只发生在构造客户端时。
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..crypto import b64, encrypt
from ..db import get_session, session_factory
from ..gen.base import GenError
from ..gen.registry import config_from_row
from ..logging_setup import get_logger, redact
from ..models import GenInstance
from ..security import admin_gate, dispatch_gate, login_gate
from .common import CamelModel

log = get_logger("api.instances")
router = APIRouter(tags=["instances"])


class InstanceOut(BaseModel):
    """字段名按 §10 的前端契约走 camelCase —— 页面（types.ts 的 GenInstance）就是照这个写的。"""

    id: str
    name: str
    protocol: str
    placement: str
    # 永远脱敏：RunningHub 的 key 在 URL 里，吐出去就进了浏览器 history
    baseUrl: str
    wsUrl: str | None = None
    apiKeySet: bool
    site: str
    instanceType: str | None = None
    retainSeconds: int | None = None
    isDefault: bool
    localOutputRoot: str | None = None
    tunnelName: str | None = None
    capabilities: dict[str, Any] = {}
    lastProbeAt: str | None = None
    lastProbeOk: bool | None = None
    lastError: str | None = None
    circuitOpen: bool = False


def _to_out(cfg: Any, row: GenInstance | None = None) -> InstanceOut:
    return InstanceOut(
        id=cfg.id,
        name=cfg.name,
        protocol=cfg.protocol,
        placement=cfg.placement,
        baseUrl=redact(cfg.base_url),
        wsUrl=cfg.ws_url,
        apiKeySet=bool(cfg.api_key),
        site=cfg.site,
        instanceType=cfg.instance_type,
        retainSeconds=cfg.retain_seconds,
        # 有库就以库为准：把别的实例设成默认会连带清掉这一个，内存里那份是旧的
        isDefault=bool(row.is_default) if row is not None else cfg.is_default,
        localOutputRoot=str(cfg.local_output_root) if cfg.local_output_root else None,
        tunnelName=cfg.tunnel_name,
        capabilities=(row.capabilities or {}) if row is not None else {},
        lastProbeAt=row.last_probe_at.isoformat() if row is not None and row.last_probe_at else None,
        lastProbeOk=row.last_probe_ok if row is not None else None,
        lastError=row.last_error if row is not None else None,
        circuitOpen=bool(row.circuit_open) if row is not None else False,
    )


async def _sync_registry(request: Request, session: AsyncSession) -> None:
    rows = (await session.execute(select(GenInstance).order_by(GenInstance.id))).scalars().all()
    await request.app.state.registry.sync_rows(rows)


class InstanceBody(CamelModel):
    name: str = Field(..., min_length=1, max_length=128)
    protocol: Literal["comfy_native", "rh_task"] = "comfy_native"
    placement: Literal["local", "cloud_self", "cloud_runninghub"] = "local"
    base_url: str
    ws_url: str | None = None
    # 只进不出：读接口永远返回 apiKeySet + 脱敏后的 URL
    api_key: str | None = None
    site: Literal["cn", "global"] = "cn"
    instance_type: Literal["default", "plus", "ultra"] | None = None
    retain_seconds: int | None = Field(None, ge=10, le=180)
    is_default: bool = False
    local_output_root: str | None = None
    tunnel_name: str | None = None


class InstancePatch(CamelModel):
    """PATCH 用全可选字段。

    复用 InstanceBody 的话，前端只改名字也会把 placement 悄悄重置成 local ——
    因为 body 的默认值会被当成「用户传了这个字段」。
    """

    name: str | None = Field(None, min_length=1, max_length=128)
    protocol: Literal["comfy_native", "rh_task"] | None = None
    placement: Literal["local", "cloud_self", "cloud_runninghub"] | None = None
    base_url: str | None = None
    ws_url: str | None = None
    api_key: str | None = None
    site: Literal["cn", "global"] | None = None
    instance_type: Literal["default", "plus", "ultra"] | None = None
    retain_seconds: int | None = Field(None, ge=10, le=180)
    is_default: bool | None = None
    local_output_root: str | None = None
    tunnel_name: str | None = None


_PATCHABLE = {
    "name", "protocol", "placement", "base_url", "ws_url", "site",
    "instance_type", "retain_seconds", "is_default", "local_output_root", "tunnel_name",
}
# 库里是 NOT NULL 的列：显式传 null 要直接 400，而不是让它走到 IntegrityError 报成「名字冲突」
_NOT_NULL = {"name", "protocol", "placement", "base_url", "is_default"}


def _require_db(request: Request) -> None:
    if not request.app.state.settings.database_url:
        raise HTTPException(400, "后端在无库模式，实例配置不可改：请编辑 apps/api/instances.dev.json 后重启，或配好 H3_DATABASE_URL")


def _check_config(base_url: str, protocol: str, local_output_root: str | None) -> None:
    if "://" not in base_url:
        raise HTTPException(400, "base_url 必须是 http(s):// 开头的完整地址")
    if "@" in base_url.split("://", 1)[1].split("/", 1)[0]:
        # 用户名密码写在 URL 里会一路进日志和异常栈；要认证就填 api_key 字段
        raise HTTPException(400, "base_url 里不能带账号密码（@），请改用 api_key 字段")
    if protocol == "rh_task":
        raise HTTPException(
            400,
            "RunningHub 专有任务 API（rh_task）客户端还没实现。现在要接 RunningHub 请用原生代理："
            "protocol=comfy_native，base_url=https://www.runninghub.cn/proxy/{你的 apiKey}。",
        )
    if local_output_root and not Path(local_output_root).is_dir():
        raise HTTPException(400, f"本地产物目录不存在：{local_output_root}")


async def _row_of(session: AsyncSession, instance_id: str) -> GenInstance:
    row = await session.get(GenInstance, int(instance_id)) if instance_id.isdigit() else None
    if row is None:
        raise HTTPException(404, f"实例 {instance_id} 不在库里")
    return row


@router.get("/instances", response_model=list[InstanceOut])
async def list_instances(
    request: Request,
    _: Any = Depends(login_gate),
    session: AsyncSession = Depends(get_session),
) -> list[InstanceOut]:
    registry = request.app.state.registry
    rows: dict[str, GenInstance] = {}
    if request.app.state.settings.database_url:
        rows = {str(r.id): r for r in (await session.execute(select(GenInstance))).scalars()}
    return [_to_out(registry.config(i), rows.get(i)) for i in registry.ids]


@router.post("/instances", response_model=InstanceOut, status_code=201)
async def create_instance(
    body: InstanceBody,
    request: Request,
    actor: Any = Depends(admin_gate),
    session: AsyncSession = Depends(get_session),
) -> InstanceOut:
    _require_db(request)
    _check_config(body.base_url, body.protocol, body.local_output_root)
    # 第一条实例自动当默认：前端「派发到默认实例」不该一上来就没有目标
    others = (await session.execute(select(func.count()).select_from(GenInstance))).scalar() or 0
    row = GenInstance(
        name=body.name,
        protocol=body.protocol,
        placement=body.placement,
        base_url=body.base_url.rstrip("/"),
        ws_url=body.ws_url,
        api_key_enc=b64(encrypt(body.api_key)),
        site=body.site,
        instance_type=body.instance_type,
        retain_seconds=body.retain_seconds,
        is_default=body.is_default or others == 0,
        local_output_root=body.local_output_root,
        tunnel_name=body.tunnel_name,
    )
    session.add(row)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise HTTPException(409, f"实例名 {body.name!r} 已存在，换一个") from exc
    if row.is_default:
        await _sole_default(session, row.id)
    await session.commit()
    await session.refresh(row)
    await _sync_registry(request, session)
    cfg = config_from_row(row)
    log.info("实例 %s（%s）已登记，操作人：%s", cfg.id, cfg.name, getattr(actor, "username", "-"))
    return _to_out(cfg, row)


@router.patch("/instances/{instance_id}", response_model=InstanceOut)
async def update_instance(
    instance_id: str,
    body: InstancePatch,
    request: Request,
    actor: Any = Depends(admin_gate),
    session: AsyncSession = Depends(get_session),
) -> InstanceOut:
    _require_db(request)
    row = await _row_of(session, instance_id)
    given = set(body.model_fields_set)
    if "base_url" in given and body.base_url and "<REDACTED>" in body.base_url:
        if redact(row.base_url) == body.base_url:
            # 设置页把 GET 回来的脱敏 URL 原样 PATCH 回来了。那不是「用户改了地址」——
            # 照单收下就会把真实地址写成一串 <REDACTED>，实例当场变成连不上。
            given -= {"base_url"}
        else:
            raise HTTPException(400, "base_url 里带着 <REDACTED>：那是展示用的脱敏形式，请填写真实地址")
    if "api_key" in given and body.api_key and "<REDACTED>" in body.api_key:
        raise HTTPException(400, "apiKey 不能是脱敏串，请重新粘贴完整的 key")
    if not given - {"api_key"}:
        raise HTTPException(400, "没有任何字段被改动")
    for field in _PATCHABLE:
        if field not in given:
            continue
        value = getattr(body, field)
        if value is None and field in _NOT_NULL:
            # 显式 null 打到 NOT NULL 列上，不该以「实例名 None 冲突」这种话报出去
            raise HTTPException(400, f"{field} 不能为空")
        setattr(row, field, value.rstrip("/") if field in ("base_url", "ws_url") and value else value)
    # apiKey 唯一靠得住的判据是「字段有没有出现」：
    # 省略 = 不动已存的 key；显式 null = 清掉；给了值 = 重新加密存进去。
    if "api_key" in given:
        row.api_key_enc = b64(encrypt(body.api_key)) if body.api_key else None
    _check_config(row.base_url, row.protocol, row.local_output_root)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise HTTPException(409, f"实例名 {row.name!r} 已被别的实例占用") from exc
    if row.is_default:
        await _sole_default(session, row.id)
    await session.commit()
    await session.refresh(row)
    await _sync_registry(request, session)
    cfg = config_from_row(row)
    log.info("实例 %s 配置已更新，操作人：%s", cfg.id, getattr(actor, "username", "-"))
    return _to_out(cfg, row)


async def _sole_default(session: AsyncSession, keep_id: int) -> None:
    """默认实例只能有一个，否则「派发到默认实例」这件事没有确定含义。"""
    await session.execute(text("UPDATE gen_instances SET is_default=false WHERE id<>:id"), {"id": keep_id})


@router.delete("/instances/{instance_id}", status_code=204)
async def delete_instance(
    instance_id: str,
    request: Request,
    actor: Any = Depends(admin_gate),
    session: AsyncSession = Depends(get_session),
) -> None:
    _require_db(request)
    row = await _row_of(session, instance_id)
    pending = (
        await session.execute(
            text("SELECT count(*) FROM jobs WHERE instance_id=:i AND state IN ('queued','dispatching','running')"),
            {"i": row.id},
        )
    ).scalar()
    if pending:
        raise HTTPException(409, f"实例上还有 {pending} 个未收口的任务，先取消或等它们跑完再删")
    name = row.name
    await session.delete(row)
    await session.commit()
    await request.app.state.registry.drop(str(row.id))
    log.info("实例 %s（%s）已删除，操作人：%s", row.id, name, getattr(actor, "username", "-"))


def _probe_report(caps: Any) -> dict[str, Any]:
    """§10 的 ProbeReport：{ok, native?, task?, error, hints[]}。

    hints 是给真人看的下一步，不是日志。前端直接渲染，不再自己猜错误码。
    """
    native = {
        "comfyVersion": caps.comfy_version,
        "nodeCount": caps.node_count,
        "gpu": caps.gpu,
        "vramTotalGb": caps.vram_total_gb,
        "vramFreeGb": caps.vram_free_gb,
        "missingModels": caps.missing_models,
        "h3": caps.h3_nodes,
    }
    hints: list[str] = []
    if not caps.reachable:
        hints.append("连不上：确认 ComfyUI 在跑、地址与端口对，并且这台机器能访问那个地址")
    else:
        if not caps.supports_websocket:
            hints.append("这个实例没通 /ws：进度只能靠轮询，队列页的实时进度会缺")
        if not (caps.h3_nodes or {}).get("MiniMaxH3ImageToVideo"):
            hints.append("实例上没有 MiniMaxH3 节点：视频档跑不了，只能出图或换实例")
        if caps.missing_models:
            hints.append(f"缺权重：{'；'.join(caps.missing_models[:3])}")
    return {
        "ok": caps.reachable,
        "protocol": caps.protocol,
        "native": native,
        "supportsProgress": caps.supports_progress,
        "error": (caps.raw or {}).get("error"),
        "hints": hints,
    }


@router.post("/instances/{instance_id}/probe")
async def probe_instance(instance_id: str, request: Request, _: Any = Depends(login_gate)) -> dict[str, Any]:
    """探活结果写回 gen_instances —— 设置页的「本地可用连接」绿点读的就是这几列。"""
    registry = request.app.state.registry
    try:
        client = registry.client(instance_id)
    except (KeyError, NotImplementedError) as exc:
        raise HTTPException(400, str(exc)) from exc
    caps = await client.probe()
    report = _probe_report(caps)
    if request.app.state.settings.database_url and instance_id.isdigit():
        async with session_factory()() as s:
            await s.execute(
                text(
                    """
                    UPDATE gen_instances
                       SET last_probe_at=now(), last_probe_ok=:ok, last_error=:err, capabilities=:caps
                     WHERE id=:id
                    """
                ),
                {
                    "id": int(instance_id),
                    "ok": bool(caps.reachable),
                    "err": None if caps.reachable else str(report.get("error") or "")[:500] or None,
                    "caps": json.dumps(report["native"], ensure_ascii=False),
                },
            )
            await s.commit()
    return report


@router.post("/instances/{instance_id}/ping")
async def ping_instance(instance_id: str, request: Request, _: Any = Depends(login_gate)) -> dict[str, Any]:
    """轻量在线探测：只打 `/system_stats` 与 `/queue`。

    和探活（/probe）的分工要说清楚：完整探活要拉一次全量 /object_info（本机约 30MB），
    管理页每 10 秒刷一次在线状态要是走那条，实例正在出片时就是自己把自己的读请求挤断。
    所以这里只更新 last_probe_at / last_probe_ok / last_error 三列，
    **不动 capabilities** —— 那份是完整探活的成果，不能被半个数据盖成缺节点。
    """
    registry = request.app.state.registry
    try:
        client = registry.client(instance_id)
    except (KeyError, NotImplementedError) as exc:
        raise HTTPException(400, str(exc)) from exc
    if not hasattr(client, "system_stats"):
        raise HTTPException(400, f"实例 {instance_id} 的协议没有轻量探测口，请用「测试连接」")
    ok = False
    error: str | None = None
    running = queued = 0
    try:
        await client.system_stats()
        queue = await client.queue_state()
        running = len(queue.get("queue_running") or [])
        queued = len(queue.get("queue_pending") or [])
        ok = True
    except Exception as exc:
        error = redact(str(exc))[:200] or type(exc).__name__
    if request.app.state.settings.database_url and instance_id.isdigit():
        async with session_factory()() as s:
            await s.execute(
                text(
                    "UPDATE gen_instances SET last_probe_at=now(), last_probe_ok=:ok, last_error=:err WHERE id=:id"
                ),
                {"id": int(instance_id), "ok": ok, "err": None if ok else error},
            )
            await s.commit()
    return {"ok": ok, "instanceId": instance_id, "running": running, "queued": queued, "error": error}


class DryProbeBody(CamelModel):
    protocol: Literal["comfy_native", "rh_task"] = "comfy_native"
    base_url: str
    api_key: str | None = None
    site: Literal["cn", "global"] = "cn"


@router.post("/instances/dry-probe")
async def dry_probe(body: DryProbeBody, _: Any = Depends(admin_gate)) -> dict[str, Any]:
    """存库之前先试连通性。客户端用完即关，不留进注册表。"""
    from ..gen.comfy_native import ComfyNativeClient

    if body.protocol != "comfy_native":
        raise HTTPException(400, "rh_task 客户端还没实现，先用 /proxy/{apiKey} 的原生代理接法试")
    client = ComfyNativeClient(body.base_url, api_key=body.api_key)
    try:
        return _probe_report(await client.probe())
    finally:
        await client.close()


@router.get("/instances/{instance_id}/object-info")
async def object_info(
    instance_id: str, request: Request, node_class: str | None = None, _: Any = Depends(login_gate)
) -> dict[str, Any]:
    """未知 class_type 会返回空 dict —— 前端要按「判空」而不是「判状态码」来校验。"""
    registry = request.app.state.registry
    try:
        client = registry.client(instance_id)
    except (KeyError, NotImplementedError) as exc:
        raise HTTPException(400, str(exc)) from exc
    if not hasattr(client, "object_info"):
        raise HTTPException(400, f"实例 {instance_id} 的协议不支持 /object_info")
    return await client.object_info(node_class)


class SubmitBody(CamelModel):
    graph: dict[str, Any] = Field(..., description="API 格式的工作流图")
    client_id: str | None = Field(None, description="留空则由服务端生成，并与 WS 共用")
    prompt_id: str | None = Field(None, description="可预生成，必须规范小写 UUID")


@router.post("/instances/{instance_id}/submit")
async def submit(instance_id: str, body: SubmitBody, request: Request, _: Any = Depends(dispatch_gate)) -> dict[str, Any]:
    """直投：绕过队列立刻打进实例。

    这条路只给「工作流试运行」用。正式产出走 POST /jobs，因为直投不记账，
    在按秒计费的云端实例上等于花别人的钱查不到出处。
    """
    registry = request.app.state.registry
    try:
        client = registry.client(instance_id)
    except (KeyError, NotImplementedError) as exc:
        raise HTTPException(400, str(exc)) from exc

    client_id = body.client_id or uuid_str()
    try:
        missing = await client.missing_models(body.graph) if hasattr(client, "missing_models") else []
        if missing:
            raise GenError(f"实例上缺少权重：{', '.join(missing)}", kind="missing_models")
        sub = await client.submit(body.graph, client_id=client_id, job_ref=body.prompt_id)
    except GenError as exc:
        raise HTTPException(400, redact(exc.message)) from exc
    return {
        "promptId": sub.job_ref,
        "clientId": sub.client_id,
        "queueNumber": sub.queue_number,
        "missingModels": [],
    }


def uuid_str() -> str:
    return str(uuid.uuid4())
