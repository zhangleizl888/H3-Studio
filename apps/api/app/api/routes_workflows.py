"""工作流库路由：导入向导、槽位、导出、试运行、按任务自动选。

内置模板（gen/templates.py）与库里的工作流在同一个列表里出现，
但只有库里的才带 graph —— 内置模板的图必须在服务端建（模型文件名要问实例、
参考图要先上传），所以它的 id 是 "builtin:<key>"，取详情时只回槽位定义。

导入这条路是「自动选工作流」的地基：解析 → 本地等价改写（gen/local_adapt.py）→
对齐权重名 → 抽槽位 → 读任务信号（gen/workflow_inputs.py）→ 落库。
库里存的 graph 是本机可执行那一份，原始导出留在 graph_original，改写清单留在 adaptations。
"""

from __future__ import annotations

import hashlib
import json
import uuid as _uuid
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from sqlalchemy import select, text

from ..db import get_session, session_factory
from ..gen.base import GenError
from ..gen.local_adapt import adapt_graph, object_info_with_shim, prune_unavailable
from ..gen.subgraph import flatten_subgraphs
from ..gen.templates import TEMPLATES
from ..gen.workflow import ConversionError, api_to_ui, apply_slots, extract_slots, to_node_overrides, ui_to_api
from ..gen.workflow_inputs import derive, mode_of
from ..logging_setup import get_logger, redact
from ..models import Job, Workflow
from ..security import admin_gate, dispatch_gate, login_gate
from .common import CamelModel
# 试运行要走的入队逻辑在任务路由里：这里以前直接调 `_enqueue`/`JobCreate` 却两个都没 import，
# 结果点「发起试运行」百分之百 500（NameError: name '_enqueue' is not defined）。
from .routes_auth import _audit
from .routes_jobs import JobCreate, _enqueue

log = get_logger("api.workflows")
router = APIRouter(tags=["workflows"])

# 只能在 RunningHub 上跑的专有节点：本机 ComfyUI 装了也没有
RH_ONLY = ("WanImageToVideo", "RhImage2Video", "Comfy_Rh", "RunComfyWorkflow", "RHMiniMaxH3")


class WorkflowValidate(CamelModel):
    graph: str | dict[str, Any]
    instance_id: str | None = None


class WorkflowTest(CamelModel):
    instance_id: str
    slots: dict[str, Any] = {}
    project_key: str | None = None


class NodeValues(CamelModel):
    values: dict[str, Any] = {}


def _bad(detail: str) -> HTTPException:
    return HTTPException(400, detail)


def _parse_graph(raw: str | dict[str, Any]) -> tuple[Any, str]:
    """返回 (解析结果, 来源格式)。UI 格式的特征是有 nodes/links 数组。"""
    data = raw
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise _bad(f"不是合法 JSON：{exc.msg}（第 {exc.lineno} 行）") from exc
    if not isinstance(data, dict):
        raise _bad("工作流必须是一个 JSON 对象")
    if "nodes" in data or "definitions" in data:
        return data, "ui"
    if any(isinstance(v, dict) and "class_type" in v for v in data.values()):
        return data, "api"
    raise _bad("认不出工作流格式：既没有 nodes/links（UI 格式），也没有带 class_type 的节点表（API 格式）")


async def _client_for(request: Request, instance_id: str | None):
    registry = request.app.state.registry
    ids = list(registry.ids)
    if not ids:
        raise _bad("还没有可用的生成实例，导入向导需要拿它的 /object_info 来校验节点与模型")
    chosen = instance_id or ids[0]
    try:
        return chosen, registry.client(chosen)
    except (KeyError, NotImplementedError) as exc:
        raise _bad(str(exc)) from exc


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def build_executable(raw: Any, source: str, object_info: dict[str, Any]) -> dict[str, Any]:
    """任意导出 → 本机可执行图 + 改写审计 + 任务信号。

    转换阶段用带「外部节点形状桩」的 object_info：加速版那种只有 UI 导出的文件，
    里面全是本机没有的类，不打桩连转换都过不去（不是「放宽校验」，是给转换器知道
    哪些值是控件、哪些是连线）。
    """
    notes: list[str] = []
    if source == "ui":
        shim = object_info_with_shim(object_info)
        api_graph = ui_to_api(flatten_subgraphs(raw, shim), shim, report=notes)
        ui_graph = raw
    else:
        api_graph = raw
        ui_graph = None

    adapted = adapt_graph(api_graph, object_info)
    info = derive(adapted.graph, object_info)
    unresolved = {g.class_type for g in adapted.gaps}
    rh_left = [c for c in unresolved if any(tag in c for tag in RH_ONLY)]
    executes_on = "cloud_runninghub" if rh_left and len(rh_left) == len(unresolved) else "any"
    return {
        "graph": adapted.graph,
        "ui_graph": ui_graph,
        "api_source": api_graph,
        "adaptations": [c.as_dict() for c in adapted.changes],
        "gaps": [g.as_dict() for g in adapted.gaps],
        "pending_media": adapted.pending_media,
        "task_kind": info["kind"],
        "resolution": info.get("resolution"),
        "signals": info["signals"],
        "outputs": info["outputs"],
        "executes_on": executes_on,
        "notes": notes,
    }


def _wf_out(row: Workflow, bindings: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    mode, mode_label = mode_of(row.task_kind, list(row.signals or []))
    graph_bytes = len(json.dumps(row.graph or {}, ensure_ascii=False).encode("utf-8"))
    return {
        "id": str(row.id),
        "uuid": row.uuid,
        "name": row.name,
        "description": row.description or "",
        "tags": list(row.tags or []),
        "family": row.family,
        "taskKind": row.task_kind,
        # 界面上「工作流库」按模式分组（文生视频 / 图生视频 / 全能参考 …），
        # 这是从任务信号里读出来的，不是又一份要人维护的标签
        "mode": mode,
        "modeLabel": mode_label,
        "sourceFile": row.source_file or f"{row.name}.json",
        "jsonBytes": graph_bytes,
        # 实例 id → 绑了几项。卡片上「本机已绑 4 项」读的就是这个
        "bindings": {iid: len(ov or {}) for iid, ov in (bindings or {}).items()},
        "executesOn": row.executes_on,
        "signals": list(row.signals or []),
        "gaps": list(row.gaps or []),
        "adaptations": list(row.adaptations or []),
        "pendingMedia": list(row.pending_media or []),
        "autoSelect": bool(row.auto_select),
        "priority": row.priority,
        "verifiedAt": row.verified_at.isoformat() if row.verified_at else None,
        "sourceFormat": row.source_format,
        "slotCount": len(row.slots or []),
        "slots": list(row.slots or []),
        "requirements": row.requirements or {},
        "isBuiltin": bool(row.is_builtin),
        "objectInfoHash": row.object_info_hash,
        "nodeCount": len(row.graph or {}),
        "updatedAt": row.updated_at.isoformat() if row.updated_at else "",
    }


def _template_out(key: str) -> dict[str, Any]:
    t = TEMPLATES[key]
    return {
        "id": f"builtin:{key}",
        "name": t.label,
        "description": t.description,
        "tags": ["内置", t.group],
        "family": t.kind,
        "taskKind": t.kind,
        "mode": t.kind,
        "modeLabel": "内置模板",
        "sourceFile": f"内置 · {key}",
        "jsonBytes": 0,
        "bindings": {},
        "sourceFormat": "api",
        # 模板槽位与 WorkflowSlot 形状不同，前端按 builtin 分支渲染
        "slots": [s.as_dict() for s in t.slots],
        "requirements": {},
        "isBuiltin": True,
        "templateKey": key,
        "updatedAt": "",
    }


def _analyze(graph: dict[str, Any], object_info: dict[str, Any], slots: list[Any]) -> dict[str, Any]:
    """导入校验报告：缺什么节点、缺什么模型、哪些只能在 RunningHub 上跑。"""
    errors: list[dict[str, str]] = []
    unknown: list[str] = []
    rh_only: list[str] = []
    missing_models: list[dict[str, str]] = []
    warnings: list[str] = []

    for node_id, node in graph.items():
        class_type = str(node.get("class_type") or "")
        if not class_type:
            errors.append({"node": str(node_id), "message": "节点缺少 class_type"})
            continue
        if class_type not in object_info:
            unknown.append(class_type)
            if any(tag.lower() in class_type.lower() for tag in RH_ONLY):
                rh_only.append(class_type)
            continue
        for name, value in (node.get("inputs") or {}).items():
            if not isinstance(value, str) or not value.lower().endswith((".safetensors", ".ckpt", ".pt", ".bin", ".gguf")):
                continue
            spec = ((object_info[class_type].get("input") or {}).get("required") or {}).get(name)
            if isinstance(spec, list) and spec and isinstance(spec[0], list):
                choices = [c for c in spec[0] if isinstance(c, str)]
                if value not in choices:
                    missing_models.append({"folder": class_type, "filename": value})
                    warnings.append(f"节点 {node_id} 的 {name}={value} 在本实例上找不到，导入时会自动对齐到同名精度档")

    seen: set[str] = set()
    unknown = [c for c in unknown if not (c in seen or seen.add(c))]
    return {
        "valid": not errors and not unknown,
        "sourceFormat": "api",
        "errors": errors,
        "warnings": warnings,
        "unknownNodes": unknown,
        "runninghubOnlyNodes": rh_only,
        "missingModels": missing_models,
        "missingCustomNodes": unknown,
        "slotCount": len(slots),
        "installPlan": [f"在实例上安装提供 {c} 的自定义节点包" for c in unknown],
    }


@router.get("/workflows")
async def list_workflows(_: Any = Depends(login_gate), session=Depends(get_session)) -> list[dict[str, Any]]:
    from ..workflow_bindings import load_map

    rows = (await session.execute(select(Workflow).order_by(Workflow.id))).scalars().all()
    # 一次问齐所有绑定再分发给各行：卡片上「本机已绑 4 项」要在列表里就有，
    # 逐条查会变成打开这一页就打 N 次 SQL
    bindings = await load_map(session, [str(r.id) for r in rows])
    return [_template_out(k) for k in TEMPLATES] + [_wf_out(r, bindings.get(str(r.id))) for r in rows]


@router.get("/workflows/select")
async def select_preview(request: Request, kind: Literal["image", "video", "audio"] = "video",
                         instance_id: str | None = None,
                         slots: str | None = None, _: Any = Depends(login_gate),
                         session=Depends(get_session)) -> dict[str, Any]:
    """给前端看「这次任务会自动挑中哪条工作流、为什么」—— 只排序，不建任务。

    slots 传 JSON 字符串（信号写法）。库里没有合适的就返回空 candidates，
    前端要如实显示「回落内置模板」，别装作命中了工作流。

    kind 只认这三类：库里 task_kind 的检查约束就是它们。写错的 kind 以前会
    一路走到 `Task.from_params` 被静悄悄兜成 "video"，界面拿到的是「video 的候选」
    却以为问的是别的 —— 现在由 FastAPI 直接 422，说清楚能填什么。
    """
    from ..workflow_select import Task, rank

    try:
        payload = json.loads(slots) if slots else {}
    except json.JSONDecodeError as exc:
        raise _bad(f"slots 不是合法 JSON：{exc.msg}") from exc
    rows = (await session.execute(select(Workflow).order_by(Workflow.id))).scalars().all()
    task = Task.from_params({"kind": kind, "slots": payload,
                             "instance_placement": _placement_of(request, instance_id)})
    scored = rank([_wf_out(r) | {"task_kind": r.task_kind, "executes_on": r.executes_on,
                                 "pending_media": r.pending_media, "verified_at": r.verified_at,
                                 "auto_select": r.auto_select, "priority": r.priority,
                                 "signals": r.signals} for r in rows], task)
    from ..job_plan import default_template_for

    return {"kind": kind, "placement": task.placement, "provided": sorted(task.provided()),
            "candidates": [s.as_dict() for s in scored],
            # 库里挑不出时真的会回落到哪个内置模板；None 表示这一类任务没得回落，入队会被拦下
            "fallbackTemplate": default_template_for(kind)}


@router.get("/workflows/{workflow_id}")
async def get_workflow(workflow_id: str, _: Any = Depends(login_gate), session=Depends(get_session)) -> dict[str, Any]:
    if workflow_id.startswith("builtin:"):
        key = workflow_id.split(":", 1)[1]
        if key not in TEMPLATES:
            raise HTTPException(404, f"没有内置模板 {key}")
        return _template_out(key)
    row = await session.get(Workflow, int(workflow_id)) if workflow_id.isdigit() else None
    if row is None:
        raise HTTPException(404, f"工作流 {workflow_id} 不存在")
    from ..workflow_bindings import load_map

    bindings = await load_map(session, [str(row.id)])
    return {**_wf_out(row, bindings.get(str(row.id))), "graph": row.graph}


@router.post("/workflows/validate")
async def validate_workflow(body: WorkflowValidate, request: Request, _: Any = Depends(login_gate)) -> dict[str, Any]:
    """导入前体检：能不能改写成本机可执行的、还缺什么、任务信号是什么。不落库。"""
    raw, source = _parse_graph(body.graph)
    instance_id, client = await _client_for(request, body.instance_id)
    object_info = await client.object_info()
    built = build_executable(raw, source, object_info)
    graph, aligned = await client.align_graph(built["graph"])
    graph, pruned = prune_unavailable(graph, object_info)
    built["adaptations"] += [c.as_dict() for c in pruned]
    built["signals"] = derive(graph, object_info)["signals"]
    built["task_kind"] = derive(graph, object_info)["kind"]
    slots = [s.as_dict() for s in extract_slots(graph, object_info)]
    report = _analyze(graph, object_info, slots)
    report.update({
        "sourceFormat": source,
        "instanceId": instance_id,
        "taskKind": built["task_kind"],
        "executesOn": built["executes_on"],
        "signals": built["signals"],
        "outputs": built["outputs"],
        "adaptations": built["adaptations"],
        "gaps": built["gaps"],
        "pendingMedia": built["pending_media"],
        "resolution": built.get("resolution"),
        "alignment": aligned,
        "notes": built["notes"],
    })
    report["warnings"] = report["warnings"] + built["notes"]
    return report


@router.post("/workflows/import", status_code=201)
async def import_workflow(
    request: Request,
    file: UploadFile = File(...),
    ui_file: UploadFile | None = File(None),
    # 这些是 multipart 表单字段，不是 query：不写 Form() 就会全部收到 None，
    # 于是名字退回文件名、优先级退回默认值 —— 界面上看着像「导入没带元数据」。
    name: str | None = Form(None),
    description: str | None = Form(None),
    instance_id: str | None = Form(None),
    priority: int = Form(100),
    tags: str | None = Form(None, description="逗号分隔"),
    actor: Any = Depends(admin_gate),
    session=Depends(get_session),
) -> dict[str, Any]:
    """导入向导：解析 → 本机等价改写 → 对齐权重名 → 抽槽位与任务信号 → 落库。

    同一个工作流常常有两份导出（API 版与画布版）。两份一起传：API 版当可执行图，
    画布版存成 ui_graph 给编辑器用 —— 省掉一次有损的 UI↔API 往返，也避开子图摊平
    丢控件值那一类坑。只给画布版照样能导（走转换 + 外部节点形状桩）。
    """
    payload = (await file.read()).decode("utf-8", errors="replace")
    raw, source = _parse_graph(payload)
    _, client = await _client_for(request, instance_id)
    object_info = await client.object_info()
    built = build_executable(raw, source, object_info)

    ui_graph = built["ui_graph"]
    if ui_file is not None:
        companion = (await ui_file.read()).decode("utf-8", errors="replace")
        ui_raw, ui_source = _parse_graph(companion)
        if ui_source != "ui":
            raise _bad("第二个文件（ui_file）必须是 ComfyUI 画布导出（有 nodes/links），不是 API 导出")
        ui_graph = ui_raw

    graph, aligned = await client.align_graph(built["graph"])
    # 对齐之后才有「这台实例真给不出这个权重」的判据，剪分支必须排在它后面
    graph, pruned = prune_unavailable(graph, object_info)
    if pruned:
        built["adaptations"] += [c.as_dict() for c in pruned]
        info = derive(graph, object_info)
        built["signals"], built["task_kind"] = info["signals"], info["kind"]
        built["resolution"], built["outputs"] = info["resolution"], info["outputs"]
    warnings = list(built["notes"])
    for change in aligned:
        warnings.append(f"节点 {change['node']} 的 {change['field']}：{change['from']} → "
                        f"{change['to']}（本实例上的实际文件名）")

    slots = [s.as_dict() for s in extract_slots(graph, object_info)]
    report = _analyze(graph, object_info, slots)
    report.update({
        "sourceFormat": source, "warnings": report["warnings"] + warnings,
        "taskKind": built["task_kind"], "executesOn": built["executes_on"],
        "signals": built["signals"], "gaps": built["gaps"],
        "adaptations": built["adaptations"], "pendingMedia": built["pending_media"],
    })

    row = Workflow(
        uuid=str(_uuid.uuid4()),
        name=(name or file.filename or "导入的工作流").removesuffix(".json").removesuffix("_api"),
        description=description,
        tags=[t.strip() for t in (tags or "导入").split(",") if t.strip()],
        family=built["task_kind"],
        task_kind=built["task_kind"],
        signals=built["signals"],
        executes_on=built["executes_on"],
        gaps=built["gaps"],
        adaptations=built["adaptations"],
        pending_media=built["pending_media"],
        graph_original=built["api_source"],
        source_file=(file.filename or "")[:200] or None,
        priority=priority,
        source_format=source,
        graph=graph,
        ui_graph=ui_graph,
        slots=slots,
        requirements={"models": report["missingModels"], "customNodes": report["unknownNodes"],
                      "resolution": built.get("resolution"), "outputs": built.get("outputs")},
        object_info_hash=_digest(json.dumps(sorted(object_info), ensure_ascii=False)),
    )
    session.add(row)
    await session.flush()  # 先拿到 row.id，审计条目才指得准
    await _audit(
        session,
        user_id=getattr(actor, "id", None),
        actor=getattr(actor, "username", None),
        action="workflow.import",
        target=f"{row.id} · {row.name}",
        request=request,
        detail={"sourceFormat": source, "slots": len(slots), "gaps": len(built["gaps"]),
                "adaptations": len(built["adaptations"])},
    )
    await session.commit()
    await session.refresh(row)
    log.info("工作流 %s 导入完成（%d 节点 / %d 槽位 / %d 处改写 / %d 个缺口），操作人：%s",
             row.name, len(graph), len(slots), len(built["adaptations"]), len(built["gaps"]),
             getattr(actor, "username", "-"))
    return {"workflow": {**_wf_out(row), "graph": row.graph}, "report": report}


@router.post("/workflows/{workflow_id}/rescan")
async def rescan_workflow(workflow_id: str, request: Request, instance_id: str | None = Query(None),
                          actor: Any = Depends(admin_gate), session=Depends(get_session)) -> dict[str, Any]:
    """按实例当前的 /object_info 重新改写一次。

    装了缺的节点包、或者换了实例之后要点这个：库里的图是从 graph_original 重造的，
    不是「在已改写的图上再改一遍」，所以重复扫描不会累积改写。
    """
    row = await session.get(Workflow, int(workflow_id)) if workflow_id.isdigit() else None
    if row is None:
        raise HTTPException(404, f"工作流 {workflow_id} 不存在")
    if row.is_builtin:
        raise _bad("内置模板不需要扫描")
    base = row.graph_original or row.graph
    _, client = await _client_for(request, instance_id)
    object_info = await client.object_info()
    built = build_executable(base, "api", object_info)
    graph, aligned = await client.align_graph(built["graph"])
    graph, pruned = prune_unavailable(graph, object_info)
    if pruned:
        built["adaptations"] += [c.as_dict() for c in pruned]
        info = derive(graph, object_info)
        built["signals"], built["task_kind"] = info["signals"], info["kind"]
        built["resolution"], built["outputs"] = info["resolution"], info["outputs"]
    slots = [s.as_dict() for s in extract_slots(graph, object_info)]
    row.graph = graph
    row.signals = built["signals"]
    row.gaps = built["gaps"]
    row.adaptations = built["adaptations"]
    row.pending_media = built["pending_media"]
    row.task_kind = built["task_kind"]
    row.family = built["task_kind"]
    row.executes_on = built["executes_on"]
    row.slots = slots
    row.requirements = {"models": [], "customNodes": sorted({g["class_type"] for g in built["gaps"]}),
                        "resolution": built.get("resolution"), "outputs": built.get("outputs")}
    row.object_info_hash = _digest(json.dumps(sorted(object_info), ensure_ascii=False))
    await session.commit()
    await session.refresh(row)
    log.info("工作流 %s 重扫：%d 处改写、%d 个缺口，操作人：%s", row.name, len(built["adaptations"]),
             len(built["gaps"]), getattr(actor, "username", "-"))
    return {"workflow": {**_wf_out(row), "graph": row.graph},
            "report": {"adaptations": built["adaptations"], "gaps": built["gaps"], "alignment": aligned,
                       "signals": built["signals"], "executesOn": built["executes_on"],
                       "taskKind": built["task_kind"]}}


@router.delete("/workflows/{workflow_id}", status_code=204)
async def delete_workflow(workflow_id: str, request: Request, actor: Any = Depends(admin_gate), session=Depends(get_session)) -> None:
    if workflow_id.startswith("builtin:"):
        raise _bad("内置模板不能删")
    row = await session.get(Workflow, int(workflow_id)) if workflow_id.isdigit() else None
    if row is None:
        raise HTTPException(404, f"工作流 {workflow_id} 不存在")
    name, task_kind = row.name, row.task_kind
    # 绑定表对 workflows 没有外键（内置模板也要能往这张表里放，所以 ref 是字符串），
    # 删条目时必须顺手把它那些绑定一起清掉，不然留着的是指不到工作流的孤儿行
    from ..workflow_bindings import drop

    await drop(session, str(row.id))
    await session.delete(row)
    # 「删除这份工作流」在界面上说的是删定义与槽位、留下任务与媒体 —— 那这一步必须可回溯
    await _audit(
        session,
        user_id=getattr(actor, "id", None),
        actor=getattr(actor, "username", None),
        action="workflow.delete",
        target=f"{workflow_id} · {name}",
        request=request,
        detail={"taskKind": task_kind},
    )
    await session.commit()


@router.get("/workflows/{workflow_id}/export")
async def export_workflow(workflow_id: str, format: Literal["api", "ui"] = "api", _: Any = Depends(login_gate), session=Depends(get_session)) -> dict[str, str]:
    row = await session.get(Workflow, int(workflow_id)) if workflow_id.isdigit() else None
    if row is None:
        raise HTTPException(404, f"工作流 {workflow_id} 不存在")
    if format == "api":
        return {"format": "api", "json": json.dumps(row.graph, ensure_ascii=False)}
    ui = row.ui_graph
    if not ui:
        raise _bad("这条工作流没有存 UI 格式，选 api 导出")
    return {"format": "ui", "json": json.dumps(ui, ensure_ascii=False)}


@router.get("/workflows/{workflow_id}/slots")
async def workflow_slots(workflow_id: str, _: Any = Depends(login_gate), session=Depends(get_session)) -> list[dict[str, Any]]:
    if workflow_id.startswith("builtin:"):
        key = workflow_id.split(":", 1)[1]
        return [s.as_dict() for s in TEMPLATES[key].slots] if key in TEMPLATES else []
    row = await session.get(Workflow, int(workflow_id)) if workflow_id.isdigit() else None
    if row is None:
        raise HTTPException(404, f"工作流 {workflow_id} 不存在")
    return list(row.slots or [])


@router.get("/workflows/{workflow_id}/models")
async def workflow_models(workflow_id: str, request: Request, instance_id: str | None = Query(None),
                          _: Any = Depends(login_gate), session=Depends(get_session)) -> dict[str, Any]:
    """这条工作流（或内置模板）在那台实例上能换哪些权重。

    清单只从实例自己的 /object_info 来：这台机器的权重目录与命名和官方文档不一致是常态，
    前端拿到什么就能填什么，**服务端绝不就近凑一个**（见 gen/model_slots.py 的来历）。
    """
    from ..gen.model_slots import builtin, extract

    chosen, client = await _client_for(request, instance_id)
    if not hasattr(client, "object_info"):
        raise _bad(f"实例 {chosen} 的协议答不出模型清单（只有 comfy_native 支持 /object_info）")

    notes: list[str] = []
    try:
        if workflow_id.startswith("builtin:"):
            key = workflow_id.split(":", 1)[1]
            if key not in TEMPLATES:
                raise HTTPException(404, f"没有内置模板 {key}")
            slots = await builtin(client, key)
            name = TEMPLATES[key].label
        else:
            row = await session.get(Workflow, int(workflow_id)) if workflow_id.isdigit() else None
            if row is None:
                raise HTTPException(404, f"工作流 {workflow_id} 不存在")
            slots = await extract(client, row.graph or {})
            name = row.name
            if row.gaps:
                notes.append(f"这条还有 {len(row.gaps)} 处缺口，装上节点包后重新扫描才会补齐清单")
    except GenError as exc:
        # 实例没在跑 / object_info 读断是常事（这台机器上 ComfyUI 会被重启）。
        # 报 500 前端只能说「请求失败」，这里要把「哪台实例、为什么」原话给出去。
        raise _bad(redact(exc.message)) from exc

    if not slots:
        notes.append("这张图上没有可替换的权重位（或者那台实例一个候选都没报出来）")
    primary = next((s.key for s in slots if s.role in ("底模", "一体化模型")), slots[0].key if slots else None)
    registry = request.app.state.registry
    return {
        "workflowId": workflow_id,
        "workflowName": name,
        "instanceId": chosen,
        "placement": registry.config(chosen).placement,
        "protocol": getattr(client, "protocol", "comfy_native"),
        "primary": primary,
        "slots": [s.as_dict() for s in slots],
        "notes": notes,
    }


@router.post("/workflows/{workflow_id}/node-overrides")
async def workflow_node_overrides(workflow_id: str, body: NodeValues, _: Any = Depends(login_gate), session=Depends(get_session)) -> list[dict[str, Any]]:
    """槽位值投影成 RunningHub 的 nodeInfoList 骨架。"""
    row = await session.get(Workflow, int(workflow_id)) if workflow_id.isdigit() else None
    if row is None:
        raise HTTPException(404, f"工作流 {workflow_id} 不存在")
    return to_node_overrides(body.values)


@router.post("/workflows/{workflow_id}/test")
async def test_run(
    workflow_id: str, body: WorkflowTest, request: Request, actor: Any = Depends(dispatch_gate), session=Depends(get_session)
) -> dict[str, Any]:
    """试运行。槽位两种写法都收：

    - **地址写法**（`{"131.prompt": "…"}`）：工作流页的高级用法，逐节点硬填；
    - **信号写法**（`{"prompt": "…", "first_frame": 12, "seconds": 5}`）：与项目生成按钮同一套，
      由 workflow_select 把任务落到素材加载器上（素材要先上传到这台实例）。
    内置模板不在这里，走 POST /api/jobs。
    """
    if workflow_id.startswith("builtin:"):
        raise _bad("内置模板请用 POST /api/jobs（template + slots），它的图要在服务端建")
    row = await session.get(Workflow, int(workflow_id)) if workflow_id.isdigit() else None
    if row is None:
        raise HTTPException(404, f"工作流 {workflow_id} 不存在")
    values = body.slots or {}
    from ..gen.templates import BuildContext
    from ..workflow_select import SlotStyle, Task, prepare

    client = request.app.state.registry.client(body.instance_id)
    if values and all(SlotStyle.is_address_like(k) for k in values):
        try:
            graph = apply_slots(row.graph, values)
        except ConversionError as exc:
            raise _bad(str(exc)) from exc
        notes: list[str] = []
    else:
        task = Task.from_params({"kind": row.task_kind, "slots": values,
                                 "instance_placement": _placement_of(request, body.instance_id)})
        ctx = BuildContext(client=client, media_root=Path(request.app.state.settings.media_root))
        graph, notes = await prepare({"graph": row.graph, "signals": row.signals or [], "name": row.name},
                                     task, ctx=ctx)
    return await _enqueue(request, session, actor, JobCreate(
        instance_id=body.instance_id,
        graph=graph,
        kind="workflow_test",
        title=f"试运行 · {row.name}",
        project_key=body.project_key,
        workflow_id=row.id,
        meta={"role": "workflow_test", "refId": str(row.id), "fillNotes": notes},
    ))


def _placement_of(request: Request, instance_id: str | None) -> str:
    """目标实例在哪儿。云端实例不该被「本机能不能跑」的判断蒙混过去。"""
    if not instance_id:
        ids = request.app.state.registry.ids
        if len(ids) != 1:
            return "local"
        instance_id = ids[0]
    try:
        return str(request.app.state.registry.config(str(instance_id)).placement)
    except KeyError:
        return "local"


class WorkflowPatch(CamelModel):
    auto_select: bool | None = None
    priority: int | None = None
    description: str | None = None
    tags: list[str] | None = None

@router.patch("/workflows/{workflow_id}")
async def patch_workflow(workflow_id: str, body: WorkflowPatch, actor: Any = Depends(admin_gate),
                         session=Depends(get_session)) -> dict[str, Any]:
    """改「要不要参与自动选」与优先级。库里多条同 kind 时靠这两个值定谁先上。"""
    row = await session.get(Workflow, int(workflow_id)) if workflow_id.isdigit() else None
    if row is None:
        raise HTTPException(404, f"工作流 {workflow_id} 不存在")
    if body.auto_select is not None:
        row.auto_select = body.auto_select
    if body.priority is not None:
        row.priority = body.priority
    if body.description is not None:
        row.description = body.description
    if body.tags is not None:
        row.tags = body.tags
    await session.commit()
    await session.refresh(row)
    log.info("工作流 %s 配置更新（autoSelect=%s priority=%s），操作人：%s", row.name, row.auto_select,
             row.priority, getattr(actor, "username", "-"))
    return _wf_out(row)


# ───────── 按实例的权重绑定 · 体检 · 同步 · 换 JSON ─────────
#
# 这三件事共用一个前提：库里那条工作流在**每台**实例上都可以有自己的默认权重。
# 图里写死的文件名只是「导入那台机器上的那一版」，换到另一台机器上要么对齐、要么由人挑，
# 而不是拿 align_graph 猜一个 —— 猜错过一次（把 H3 底模配成 Qwen-Image），代价是一整轮
# 看不懂的采样错。所以这里所有兜底都只走 comfy_native.resolve_model_name 那套族判据，
# 认不出来就明说「这台没有」，绝不就近凑。


class BindingBody(CamelModel):
    instance_id: str
    # {"127.unet_name": "MiniMax_H3_...safetensors"}；空值 = 清掉这一位，全空 = 删掉整条绑定
    overrides: dict[str, str] = {}


class BindingSyncBody(CamelModel):
    source_instance_id: str
    target_instance_ids: list[str] = []
    # 未绑定的位也按目标实例对齐一遍：图里写死的是导入那台机器的文件名，换台机器多半不存在
    align_unbound: bool = True


class SyncAllBody(CamelModel):
    source_instance_id: str
    target_instance_ids: list[str] = []
    align_unbound: bool = True
    # 目标上已经配过的条目不再动：整库对齐常常是「补上没同步过的」而不是覆盖手工挑选
    only_missing: bool = False
    include_builtin: bool = False


class WorkflowCheckBody(CamelModel):
    instance_ids: list[str] = []


class GraphReplace(CamelModel):
    graph: str | dict[str, Any]
    instance_id: str | None = None


def _parts_from_template(key: str) -> dict[str, Any] | None:
    """内置模板那份「能换的位写在代码里」的清单。认不出这个 key 给 None。"""
    from ..gen.model_slots import BUILTIN_MODEL_LOADERS

    t = TEMPLATES.get(key)
    if t is None:
        return None
    specs = BUILTIN_MODEL_LOADERS.get(key, [])
    return {
        "ref": f"builtin:{key}",
        "name": t.label,
        "row": None,
        "graph": None,
        "classMap": {node: ct for node, ct, _field, _label in specs},
        "gaps": [],
        "taskKind": t.kind,
        "signals": [],
        "executesOn": "any",
        "autoSelect": False,
        "priority": None,
        "verifiedAt": None,
        "nodeCount": None,
    }


def _parts_from_row(row: Workflow) -> dict[str, Any]:
    graph = row.graph or {}
    return {
        "ref": str(row.id),
        "name": row.name,
        "row": row,
        "graph": graph,
        "classMap": {str(k): str((v or {}).get("class_type") or "") for k, v in graph.items()},
        "gaps": list(row.gaps or []),
        "taskKind": row.task_kind,
        "signals": list(row.signals or []),
        "executesOn": row.executes_on,
        "autoSelect": bool(row.auto_select),
        "priority": row.priority,
        "verifiedAt": row.verified_at.isoformat() if row.verified_at else None,
        "nodeCount": len(graph),
    }


async def _ref_parts(request: Request, session, workflow_id: str) -> dict[str, Any]:
    """把 "28" / "builtin:h3_video" 归成一份 {ref, name, graph, classMap, gaps, row}。

    内置模板的图是派发时在服务端现拼的（模型名要问实例、素材要先上传），所以它没有 graph，
    能换的位与节点号写在 gen/model_slots.BUILTIN_MODEL_LOADERS 里 —— 那边对不上时
    apply() 会报「图上没有这个节点」，不会静默换错。
    """
    if workflow_id.startswith("builtin:"):
        parts = _parts_from_template(workflow_id.split(":", 1)[1])
        if parts is None:
            raise HTTPException(404, f"没有内置模板 {workflow_id}")
        return parts
    row = await session.get(Workflow, int(workflow_id)) if workflow_id.isdigit() else None
    if row is None:
        raise HTTPException(404, f"工作流 {workflow_id} 不存在")
    return _parts_from_row(row)


async def _slots_for(client, target: dict[str, Any], info: dict[str, Any] | None) -> list[dict[str, Any]]:
    """这台实例上这条工作流有哪些权重位，以及每一位当前真正会用的文件名。

    info 传 None 时按 class 逐个问（模型编辑弹窗只要一台，不值得拉 30MB 全量）；
    体检一次问好几台，就把已经拿到的全量递进来。
    """
    from ..gen.model_slots import builtin, slots_from_info

    if target["graph"] is None:
        slots = await builtin(client, str(target["ref"]).split(":", 1)[1])
        return [s.as_dict() for s in slots]
    if info is None:
        from ..gen.model_slots import extract

        return [s.as_dict() for s in await extract(client, target["graph"])]
    return [s.as_dict() for s in slots_from_info(info, target["graph"])]


@router.get("/workflows/{workflow_id}/bindings")
async def list_bindings(workflow_id: str, request: Request, _: Any = Depends(login_gate),
                        session=Depends(get_session)) -> dict[str, Any]:
    """这条工作流在每台实例上的默认权重。列表页要按它给卡片挂「本机已绑 4 项」。"""
    from ..models import GenInstance
    from ..workflow_bindings import rows_for

    target = await _ref_parts(request, session, workflow_id)
    rows = await rows_for(session, target["ref"])
    insts = {str(r.id): r for r in (await session.execute(select(GenInstance))).scalars()}
    return {
        "workflowId": target["ref"],
        "workflowName": target["name"],
        "bindings": [
            {
                "instanceId": str(r.instance_id),
                "instanceName": (insts.get(str(r.instance_id)).name if str(r.instance_id) in insts else f"已删除的实例 {r.instance_id}"),
                "placement": (insts.get(str(r.instance_id)).placement if str(r.instance_id) in insts else "local"),
                "source": r.source,
                "overrides": dict(r.overrides or {}),
                "updatedAt": r.updated_at.isoformat() if r.updated_at else "",
            }
            for r in rows
        ],
    }


@router.put("/workflows/{workflow_id}/bindings")
async def put_binding(workflow_id: str, body: BindingBody, request: Request,
                      actor: Any = Depends(admin_gate), session=Depends(get_session)) -> dict[str, Any]:
    """保存「这条工作流在这台 Server 上用哪些权重」。

    写之前逐个核：节点在不在图上、字段是不是模型位、那个文件这台实例报不报得出来。
    这一层不查就等于把「Server 上没有这个权重」推迟到派发之后，用户看到的只是一个失败任务。
    """
    from ..gen.model_slots import validate as validate_models
    from ..workflow_bindings import load, save

    target = await _ref_parts(request, session, workflow_id)
    overrides = {str(k): str(v).strip() for k, v in (body.overrides or {}).items() if str(v or "").strip()}
    if overrides:
        try:
            client = request.app.state.registry.client(body.instance_id)
        except (KeyError, NotImplementedError) as exc:
            raise _bad(str(exc)) from exc
        try:
            await validate_models(client, target["classMap"], overrides, where=f"实例 {body.instance_id}")
        except GenError as exc:
            raise _bad(exc.message) from exc
    await save(session, target["ref"], body.instance_id, overrides,
               actor_id=getattr(actor, "id", None))
    await session.commit()
    log.info("工作流 %s 在实例 %s 上的权重绑定已更新（%d 项），操作人：%s", target["name"],
             body.instance_id, len(overrides), getattr(actor, "username", "-"))
    return {"workflowId": target["ref"], "instanceId": body.instance_id,
            "overrides": await load(session, target["ref"], body.instance_id)}


@router.delete("/workflows/{workflow_id}/bindings/{instance_id}", status_code=204)
async def delete_binding(workflow_id: str, instance_id: str, request: Request,
                         actor: Any = Depends(admin_gate), session=Depends(get_session)) -> None:
    """删掉这一台的绑定 = 恢复用工作流图里写死的那份权重。"""
    from ..workflow_bindings import drop

    target = await _ref_parts(request, session, workflow_id)
    await drop(session, target["ref"], instance_id)
    await session.commit()
    log.info("工作流 %s 在实例 %s 上的绑定已清除，操作人：%s", target["name"], instance_id,
             getattr(actor, "username", "-"))


async def _slots_from_catalog(catalog: "Catalog", client, parts: dict[str, Any]) -> list[dict[str, Any]]:
    """这条工作流在那台实例上有哪些权重位。清单走 catalog，一台问一次不重复拉。"""
    from ..gen.model_slots import builtin, slots_from_info

    if parts["graph"] is None:
        slots = await builtin(client, str(parts["ref"]).split(":", 1)[1])
        return [s.as_dict() for s in slots]
    classes = sorted({str(n.get("class_type") or "") for n in parts["graph"].values() if n.get("class_type")})
    info = {ct: await catalog.info(ct) for ct in classes}
    return [s.as_dict() for s in slots_from_info(info, parts["graph"])]


async def _sync_one_target(session, actor_id, parts: dict[str, Any], client, catalog: "Catalog",
                           target_iid: str, source_overrides: dict[str, Any], *,
                           align_unbound: bool) -> dict[str, Any]:
    from ..workflow_bindings import plan_sync, save

    graph = parts["graph"] or {}
    slots = await _slots_from_catalog(catalog, client, parts)
    plan = await plan_sync(client, catalog, class_map=parts["classMap"], graph=graph,
                           source=source_overrides, slots=slots, align_unbound=align_unbound,
                           mode_hint=_mode_hint(graph))
    await save(session, parts["ref"], target_iid, plan.overrides, source="sync", actor_id=actor_id)
    return plan.as_dict()


@router.post("/workflows/{workflow_id}/bindings/sync")
async def sync_bindings(workflow_id: str, body: BindingSyncBody, request: Request,
                        actor: Any = Depends(admin_gate), session=Depends(get_session)) -> dict[str, Any]:
    """把一台 Server 上配好的默认权重搬到别的 Server 上。规则写在 workflow_bindings.plan_sync。"""
    from ..workflow_bindings import Catalog, load

    target = await _ref_parts(request, session, workflow_id)
    source = await load(session, target["ref"], body.source_instance_id)
    if not source and not body.align_unbound:
        raise _bad(f"实例 {body.source_instance_id} 上这条工作流还没有绑定：要么先在那台上配一次，"
                   "要么勾上「未绑定的位也按目标实例对齐」")
    registry = request.app.state.registry
    results: list[dict[str, Any]] = []
    for iid in body.target_instance_ids or []:
        if str(iid) == str(body.source_instance_id):
            continue
        try:
            client = registry.client(str(iid))
        except (KeyError, NotImplementedError) as exc:
            results.append({"instanceId": str(iid), "ok": False, "error": str(exc),
                            "applied": [], "converted": [], "aligned": [], "skipped": [],
                            "missingNodes": [], "blocked": False, "written": 0})
            continue
        plan = await _sync_one_target(session, getattr(actor, "id", None), target, client, Catalog(client),
                                      str(iid), source, align_unbound=body.align_unbound)
        results.append({"instanceId": str(iid), "instanceName": _instance_name(registry, str(iid)),
                        "ok": True, **plan})
    await session.commit()
    log.info("工作流 %s 的权重绑定从实例 %s 同步到 %d 台，操作人：%s", target["name"],
             body.source_instance_id, len(results), getattr(actor, "username", "-"))
    return {"workflowId": target["ref"], "workflowName": target["name"],
            "sourceInstanceId": body.source_instance_id, "results": results}


@router.post("/workflows/sync-all")
async def sync_all_bindings(body: SyncAllBody, request: Request, actor: Any = Depends(admin_gate),
                            session=Depends(get_session)) -> dict[str, Any]:
    """整库对齐：接了第二台 ComfyUI 时不必逐条点同步。

    每台实例只建一个 Catalog（节点清单跨工作流复用），否则「N 条 × 每个位 × M 台」会把
    /object_info/{class} 打成上百次重复请求 —— 实例正在出片时那就是自己把自己的读请求挤断。
    """
    from ..workflow_bindings import Catalog, load_map

    registry = request.app.state.registry
    targets = [str(i) for i in (body.target_instance_ids or []) if str(i) != str(body.source_instance_id)]
    if not targets:
        raise _bad("没有可同步的目标实例（源与目标不能是同一台）")
    rows = (await session.execute(select(Workflow).order_by(Workflow.id))).scalars().all()
    parts_list = [_parts_from_row(r) for r in rows]
    if body.include_builtin:
        parts_list += [p for p in (_parts_from_template(k) for k in TEMPLATES) if p is not None]
    refs = [p["ref"] for p in parts_list]
    existing = await load_map(session, refs)

    clients: dict[str, Any] = {}
    catalogs: dict[str, Catalog] = {}
    for iid in targets:
        try:
            clients[iid] = registry.client(iid)
            catalogs[iid] = Catalog(clients[iid])
        except (KeyError, NotImplementedError) as exc:
            clients[iid] = None
            log.warning("整库同步跳不过去：实例 %s 拿不到客户端（%s）", iid, str(exc)[:120])

    out: list[dict[str, Any]] = []
    totals = {"workflows": 0, "written": 0, "aligned": 0, "skipped": 0, "blocked": 0}
    for parts in parts_list:
        source = {str(k): str(v) for k, v in ((existing.get(parts["ref"]) or {}).get(str(body.source_instance_id)) or {}).items()}
        already = {iid: (existing.get(parts["ref"]) or {}).get(iid) or {} for iid in targets}
        picked = [iid for iid in targets if not (body.only_missing and already.get(iid))]
        if not picked:
            out.append({"workflowId": parts["ref"], "workflowName": parts["name"], "untouched": True,
                        "reason": "目标上都已经配过" if body.only_missing else "没有可同步的目标",
                        "results": []})
            continue
        results: list[dict[str, Any]] = []
        for iid in picked:
            client = clients.get(iid)
            if client is None:
                results.append({"instanceId": iid, "instanceName": _instance_name(registry, iid), "ok": False,
                                "error": "拿不到这台实例的客户端（未登记或协议不支持）",
                                "applied": [], "converted": [], "aligned": [], "skipped": [],
                                "missingNodes": [], "blocked": False, "written": 0})
                continue
            try:
                plan = await _sync_one_target(session, getattr(actor, "id", None), parts, client, catalogs[iid],
                                              iid, source, align_unbound=body.align_unbound)
            except Exception as exc:  # 一条工作流问不到清单不该掀掉整库同步
                log.warning("整库同步里 %s → 实例 %s 失败：%s", parts["name"], iid, str(exc)[:160])
                plan = {"applied": [], "converted": [], "aligned": [],
                        "skipped": [{"key": "*", "reason": f"问这台实例失败：{str(exc)[:120]}"}],
                        "missingNodes": [], "blocked": False, "written": 0}
            results.append({"instanceId": iid, "instanceName": _instance_name(registry, iid), "ok": True, **plan})
        totals["workflows"] += 1
        for r in results:
            totals["written"] += r.get("written", 0)
            totals["aligned"] += len(r.get("aligned", []))
            totals["skipped"] += len(r.get("skipped", []))
            totals["blocked"] += 1 if r.get("blocked") else 0
        out.append({"workflowId": parts["ref"], "workflowName": parts["name"], "untouched": False,
                    "sourceBound": len(source), "results": results})
    await session.commit()
    log.info("权重绑定从实例 %s 整库对齐到 %d 台（%d 条工作流，写 %d 项），操作人：%s",
             body.source_instance_id, len(targets), len(out), totals["written"], getattr(actor, "username", "-"))
    return {"sourceInstanceId": body.source_instance_id, "targets": targets,
            "onlyMissing": body.only_missing, "includeBuiltin": body.include_builtin,
            "workflows": out, "totals": totals}


def _instance_name(registry, instance_id: str) -> str:
    try:
        return str(registry.config(instance_id).name)
    except KeyError:
        return f"实例 {instance_id}"


def _mode_hint(graph: dict[str, Any]) -> str | None:
    """图里用的是哪个 H3 条件节点，决定该配 fl2va 还是 ref2va 那一系。

    与 align_graph 里同一套判据：作者的合并版名字里两种都写，光看文件名定不了模式。
    """
    from ..gen.comfy_native import _FL_MODE_NODES, _REF_MODE_NODES

    classes = {str(v.get("class_type") or "") for v in (graph or {}).values()}
    if classes & _REF_MODE_NODES:
        return "ref"
    if classes & _FL_MODE_NODES:
        return "fl"
    return None


@router.post("/workflows/{workflow_id}/check")
async def check_workflow(workflow_id: str, body: WorkflowCheckBody, request: Request,
                         _: Any = Depends(login_gate), session=Depends(get_session)) -> dict[str, Any]:
    """工作流体检：逐台实例回答「这条工作流在这台上能不能跑、跑起来用的是哪些权重」。

    只读，不改库。检查完的结论是当次的 —— 实例上的节点与权重随时会变，
    所以界面不把这份报告存成「上次检查结果」，点一次问一次。
    """
    from ..workflow_bindings import load

    target = await _ref_parts(request, session, workflow_id)
    registry = request.app.state.registry
    ids = [str(i) for i in (body.instance_ids or [])] or list(registry.ids)
    reports: list[dict[str, Any]] = []
    for iid in ids:
        try:
            client = registry.client(iid)
            placement = str(registry.config(iid).placement)
            name = str(registry.config(iid).name)
        except (KeyError, NotImplementedError) as exc:
            reports.append({"instanceId": iid, "instanceName": f"实例 {iid}", "reachable": False,
                            "error": str(exc), "ok": False, "problems": [f"Server 连接失败：{exc}"],
                            "gaps": [], "models": [], "binding": {}})
            continue
        report: dict[str, Any] = {"instanceId": iid, "instanceName": name, "placement": placement,
                                  "protocol": getattr(client, "protocol", "comfy_native"),
                                  "reachable": True, "error": None, "ok": True, "problems": [],
                                  "gaps": [], "models": [], "binding": {}, "weightProblems": []}
        try:
            info = await client.object_info()
        except Exception as exc:
            report.update({"reachable": False, "error": str(exc)[:300], "ok": False,
                           "problems": [f"Server 连接失败或 /object_info 读断：{str(exc)[:160]}"]})
            reports.append(report)
            continue

        graph = target["graph"] or {}
        unknown = sorted({str(n.get("class_type") or "") for n in graph.values()
                          if n.get("class_type") and str(n.get("class_type")) not in info})
        report["gaps"] = [{"node": nid, "class_type": str(n.get("class_type") or ""),
                           "reason": "这台实例上没有这个节点", "pack": ""}
                          for nid, n in graph.items()
                          if n.get("class_type") and str(n.get("class_type")) not in info]
        binding = await load(session, target["ref"], iid)
        report["binding"] = binding
        slots = await _slots_for(client, target, info)
        for slot in slots:
            bound = str(binding.get(slot["key"]) or "").strip()
            effective = bound or slot["current"]
            slot["bound"] = bound
            slot["effective"] = effective
            # options 是这台实例报出来的清单：图里写死的和绑定挑的，只要不在清单里就是跑不了
            slot["missing"] = bool(slot["options"]) and effective not in slot["options"]
        report["models"] = slots
        report["weightProblems"] = await _weight_problems(client, [s["effective"] for s in slots])

        problems: list[str] = []
        if unknown:
            problems.append(f"这台实例缺 {len(unknown)} 个节点：{'、'.join(unknown[:6])}"
                            + ("…" if len(unknown) > 6 else "") + "。装上节点包后点「重新扫描」。")
        if not target["signals"] and target["graph"] is not None:
            problems.append("没解析出任务信号：这条不会被「按任务自动选」挑中，只能手动指定。")
        bad_models = [s for s in slots if s["missing"]]
        if bad_models:
            problems.append(f"{len(bad_models)} 个权重位在这台上没有对应文件："
                            + "、".join(f"{s['label']}（{s['effective'] or '空'}）" for s in bad_models[:4]))
        if report["weightProblems"]:
            problems.append(f"{len(report['weightProblems'])} 份权重是半截下载，加载不报错但输出恒为 0")
        if target["executesOn"] == "cloud_runninghub" and placement == "local":
            problems.append("这份图含 RunningHub 专有节点，派到本机这一台跑不了")
        report["problems"] = problems
        report["ok"] = not problems
        report.update({"taskKind": target["taskKind"], "nodeCount": target["nodeCount"],
                       "verifiedAt": target["verifiedAt"], "autoSelect": target["autoSelect"],
                       "priority": target["priority"], "signals": [s.get("name") for s in target["signals"]]})
        reports.append(report)
    mode, mode_label = mode_of(target["taskKind"], target["signals"])
    return {"workflowId": target["ref"], "workflowName": target["name"], "taskKind": target["taskKind"],
            "mode": mode, "modeLabel": mode_label, "reports": reports}


async def _weight_problems(client, values: list[str]) -> list[str]:
    """半截权重只有配了本机 output 目录的实例答得出（远端没有本地盘可看）。"""
    if not hasattr(client, "weight_file_problems"):
        return []
    try:
        return await client.weight_file_problems(values)
    except Exception:
        return []


@router.post("/workflows/{workflow_id}/graph")
async def replace_graph(workflow_id: str, body: GraphReplace, request: Request,
                        actor: Any = Depends(admin_gate), session=Depends(get_session)) -> dict[str, Any]:
    """用一份新导出替换库里这条工作流的图（界面上叫「替换 JSON」）。

    走的是和导入完全同一条流水线：解析 → 本机等价改写 → 对齐权重名 → 剪掉真跑不了的分支
    → 重抽槽位与任务信号。名字、标签、优先级、绑定都保留 —— 换图不是重建条目，
    但**节点号可能变**，所以最后要按新图把绑定里那些指不到节点的位摘掉。
    """
    from ..gen.model_slots import extract
    from ..workflow_bindings import prune_to_graph

    if workflow_id.startswith("builtin:"):
        raise _bad("内置模板的图由服务端现拼，不能替换 JSON")
    row = await session.get(Workflow, int(workflow_id)) if workflow_id.isdigit() else None
    if row is None:
        raise HTTPException(404, f"工作流 {workflow_id} 不存在")
    raw, source = _parse_graph(body.graph)
    _, client = await _client_for(request, body.instance_id)
    info = await client.object_info()
    built = build_executable(raw, source, info)
    graph, aligned = await client.align_graph(built["graph"])
    graph, pruned = prune_unavailable(graph, info)
    adaptations = built["adaptations"] + [c.as_dict() for c in pruned]
    info_out = derive(graph, info)
    slots = [s.as_dict() for s in await extract(client, graph)]

    before_nodes = len(row.graph or {})
    row.graph_original = built["api_source"]
    row.graph = graph
    if source == "ui":
        row.ui_graph = built["ui_graph"] or raw
    row.source_format = source
    row.signals = info_out["signals"]
    row.task_kind = info_out["kind"]
    row.family = info_out["kind"]
    row.gaps = [g.as_dict() for g in built["gaps"]]
    row.adaptations = adaptations
    row.pending_media = built["pending_media"]
    row.executes_on = built["executes_on"]
    row.slots = slots
    row.requirements = {"models": [], "customNodes": sorted({g["class_type"] for g in built["gaps"]}),
                        "resolution": info_out.get("resolution"), "outputs": info_out["outputs"]}
    row.object_info_hash = _digest(json.dumps(sorted(info), ensure_ascii=False))
    row.verified_at = None  # 换过图就没在这台机器上跑过了，别留着上一次的绿标
    removed = await prune_to_graph(session, str(row.id), graph)
    await session.flush()
    await _audit(session, user_id=getattr(actor, "id", None), actor=getattr(actor, "username", None),
                 action="workflow.replace", target=f"{row.id} · {row.name}", request=request,
                 detail={"nodes": f"{before_nodes} → {len(graph)}", "adaptations": len(adaptations),
                         "gaps": len(row.gaps)})
    await session.commit()
    await session.refresh(row)
    notes = list(built["notes"]) + [f"节点数 {before_nodes} → {len(graph)}"]
    notes += [f"{c['node']}.{c.get('field')}：{c['from']} → {c['to']}（本实例上的实际文件名）" for c in aligned]
    if removed:
        notes.append(f"{len(removed)} 条权重绑定指向了新图上不存在的节点，已摘掉：{'、'.join(removed[:5])}")
    if source == "api" and row.ui_graph:
        notes.append("画布版还是替换前那一份（这次只给了 API 导出），要一起换就在导入时把两个文件都传")
    log.info("工作流 %s 的图已替换：%d 节点 / %d 处改写 / %d 个缺口，操作人：%s", row.name, len(graph),
             len(adaptations), len(row.gaps), getattr(actor, "username", "-"))
    return {"workflow": {**_wf_out(row), "graph": row.graph},
            "report": {"adaptations": adaptations, "gaps": row.gaps, "alignment": aligned,
                       "signals": row.signals, "taskKind": row.task_kind, "executesOn": row.executes_on,
                       "notes": notes, "prunedBindings": removed}}
