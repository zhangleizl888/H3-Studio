"""工作流库路由：导入向导、槽位、导出、试运行。

内置模板（gen/templates.py）与库里的工作流在同一个列表里出现，
但只有库里的才带 graph —— 内置模板的图必须在服务端建（模型文件名要问实例、
参考图要先上传），所以它的 id 是 "builtin:<key>"，取详情时只回槽位定义。
"""

from __future__ import annotations

import json
import uuid as _uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from sqlalchemy import select, text

from ..db import get_session, session_factory
from ..gen.base import GenError
from ..gen.subgraph import flatten_subgraphs
from ..gen.templates import TEMPLATES
from ..gen.workflow import api_to_ui, apply_slots, extract_slots, to_node_overrides, ui_to_api
from ..logging_setup import get_logger, redact
from ..models import Job, Workflow
from ..security import admin_gate, dispatch_gate, login_gate
from .common import CamelModel

log = get_logger("api.workflows")
router = APIRouter(tags=["workflows"])

# 只能在 RunningHub 上跑的专有节点：本机 ComfyUI 装了也没有
RH_ONLY = ("WanImageToVideo", "RhImage2Video", "Comfy_Rh", "RunComfyWorkflow")


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


def _wf_out(row: Workflow) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "uuid": row.uuid,
        "name": row.name,
        "description": row.description or "",
        "tags": list(row.tags or []),
        "family": row.family,
        "sourceFormat": row.source_format,
        "slots": list(row.slots or []),
        "requirements": row.requirements or {},
        "isBuiltin": bool(row.is_builtin),
        "objectInfoHash": row.object_info_hash,
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
    raw, source = _parse_graph(body.graph)
    instance_id, client = await _client_for(request, body.instance_id)
    object_info = await client.object_info()
    graph = raw if source == "api" else ui_to_api(flatten_subgraphs(raw, object_info), object_info)
    slots = [s.as_dict() if hasattr(s, "as_dict") else s for s in extract_slots(graph, object_info)]
    report = _analyze(graph, object_info, slots)
    report["sourceFormat"] = source
    report["instanceId"] = instance_id
    return report


@router.post("/workflows/import", status_code=201)
async def import_workflow(
    request: Request,
    file: UploadFile = File(...),
    name: str | None = None,
    instance_id: str | None = None,
    actor: Any = Depends(admin_gate),
    session=Depends(get_session),
) -> dict[str, Any]:
    """导入向导：解析 → 校验 → 对齐模型名 → 抽槽位 → 落库。"""
    payload = (await file.read()).decode("utf-8", errors="replace")
    raw, source = _parse_graph(payload)
    _, client = await _client_for(request, instance_id)
    object_info = await client.object_info()

    if source == "ui":
        warnings: list[str] = []
        flat = flatten_subgraphs(raw, object_info)
        graph = ui_to_api(flat, object_info, report=warnings)
        ui_graph = raw
    else:
        graph = raw
        ui_graph = api_to_ui(graph, object_info)
        warnings = []

    graph, aligned = await client.align_graph(graph)
    for change in aligned:
        warnings.append(f"节点 {change['node']} 的 {change['field']}：{change['from']} → {change['to']}（本实例上的实际文件名）")

    slots = [s.as_dict() if hasattr(s, "as_dict") else s for s in extract_slots(graph, object_info)]
    report = _analyze(graph, object_info, slots)
    report.update({"sourceFormat": source, "warnings": report["warnings"] + warnings})

    row = Workflow(
        uuid=str(_uuid.uuid4()),
        name=(name or file.filename or "导入的工作流").removesuffix(".json"),
        description=None,
        tags=["导入"],
        family="video" if any("Video" in str(n.get("class_type", "")) for n in graph.values()) else "image",
        source_format=source,
        graph=graph,
        ui_graph=ui_graph,
        slots=slots,
        requirements={"models": report["missingModels"], "customNodes": report["unknownNodes"]},
        object_info_hash=None,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    log.info("工作流 %s 导入完成（%d 节点 / %d 槽位），操作人：%s", row.name, len(graph), len(slots), getattr(actor, "username", "-"))
    return {"workflow": {**_wf_out(row), "graph": row.graph}, "report": report}


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
    """试运行：库里的工作流填上槽位后入队。内置模板走 /api/jobs 的 template 形态，不在这里。"""
    if workflow_id.startswith("builtin:"):
        raise _bad("内置模板请用 POST /api/jobs（template + slots），它的图要在服务端建")
    row = await session.get(Workflow, int(workflow_id)) if workflow_id.isdigit() else None
    if row is None:
        raise HTTPException(404, f"工作流 {workflow_id} 不存在")
    graph = apply_slots(row.graph, body.slots or {})
    from .routes_jobs import JobCreate, _enqueue

    return await _enqueue(request, session, actor, JobCreate(
        instance_id=body.instance_id,
        graph=graph,
        kind="workflow_test",
        title=f"试运行 · {row.name}",
        project_key=body.project_key,
        workflow_id=row.id,
        meta={"role": "workflow_test", "refId": str(row.id)},
    ))
