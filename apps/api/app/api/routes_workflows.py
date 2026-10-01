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
from ..gen.workflow_inputs import derive
from ..logging_setup import get_logger, redact
from ..models import Job, Workflow
from ..security import admin_gate, dispatch_gate, login_gate
from .common import CamelModel

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


def _wf_out(row: Workflow) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "uuid": row.uuid,
        "name": row.name,
        "description": row.description or "",
        "tags": list(row.tags or []),
        "family": row.family,
        "taskKind": row.task_kind,
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
    rows = (await session.execute(select(Workflow).order_by(Workflow.id))).scalars().all()
    return [_template_out(k) for k in TEMPLATES] + [_wf_out(r) for r in rows]


@router.get("/workflows/select")
async def select_preview(request: Request, kind: str = "video", instance_id: str | None = None,
                         slots: str | None = None, _: Any = Depends(login_gate),
                         session=Depends(get_session)) -> dict[str, Any]:
    """给前端看「这次任务会自动挑中哪条工作流、为什么」—— 只排序，不建任务。

    slots 传 JSON 字符串（信号写法）。库里没有合适的就返回空 candidates，
    前端要如实显示「回落内置模板」，别装作命中了工作流。
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
    return {**_wf_out(row), "graph": row.graph}


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
async def delete_workflow(workflow_id: str, actor: Any = Depends(admin_gate), session=Depends(get_session)) -> None:
    if workflow_id.startswith("builtin:"):
        raise _bad("内置模板不能删")
    row = await session.get(Workflow, int(workflow_id)) if workflow_id.isdigit() else None
    if row is None:
        raise HTTPException(404, f"工作流 {workflow_id} 不存在")
    await session.delete(row)
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
