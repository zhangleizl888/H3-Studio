"""工作流 × 实例的「默认权重」读写，以及跨实例同步的决策。

一条工作流在本机和在自建云上常要用不同的文件（本机只有 pruned int8，云上放得下 bf16），
所以绑定按 (workflow_ref, instance_id) 一行存着。这里只管读写与「搬到别处该怎么算」，
**不判断那个文件在不在** —— 判据只在那台实例的 /object_info 里，归 gen/model_slots.py 问。

三层优先级（谁说了算）：
    任务显式挑的 models  >  这条工作流在这台实例上的绑定  >  图里写死的文件名
所以队列与入队校验都走 `merge()`，别在别处再拼一遍，两遍就会有一遍忘了谁优先。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from .models import WorkflowModelBinding


def ref_for(workflow_id: Any = None, template: Any = None) -> str | None:
    """任务参数 → 绑定键。内置模板的图在后端现拼，所以它的键带 "builtin:" 前缀。"""
    if workflow_id is not None and str(workflow_id).strip():
        return str(workflow_id)
    if template:
        return f"builtin:{template}"
    return None


def merge(binding: dict[str, Any] | None, explicit: dict[str, Any] | None) -> dict[str, Any]:
    """把库绑定垫在任务显式选择下面。空值一律丢掉，否则会覆盖成无效文件名。"""
    out: dict[str, Any] = {}
    for src in (binding or {}, explicit or {}):
        for key, value in src.items():
            text = str(value or "").strip()
            if text:
                out[str(key)] = text
            else:
                out.pop(str(key), None)
    return out


async def load(session: AsyncSession, workflow_ref: str, instance_id: str | int) -> dict[str, Any]:
    """这台实例上这条工作流的默认权重。id 不是数字就当没有（无库模式引导出来的实例没有行）。"""
    try:
        iid = int(instance_id)
    except (TypeError, ValueError):
        return {}
    row = (
        await session.execute(
            select(WorkflowModelBinding).where(
                WorkflowModelBinding.workflow_ref == str(workflow_ref),
                WorkflowModelBinding.instance_id == iid,
            )
        )
    ).scalars().first()
    return dict((row.overrides or {}) if row else {})


async def load_map(session: AsyncSession, workflow_refs: list[str]) -> dict[str, dict[str, dict[str, Any]]]:
    """一批工作流的绑定，按 ref → 实例 id → 覆盖表。列表页一次问齐，别一条条查。"""
    if not workflow_refs:
        return {}
    rows = (
        await session.execute(
            select(WorkflowModelBinding).where(WorkflowModelBinding.workflow_ref.in_(workflow_refs))
        )
    ).scalars().all()
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for row in rows:
        out.setdefault(str(row.workflow_ref), {})[str(row.instance_id)] = dict(row.overrides or {})
    return out


async def rows_for(session: AsyncSession, workflow_ref: str) -> list[WorkflowModelBinding]:
    return list(
        (
            await session.execute(
                select(WorkflowModelBinding)
                .where(WorkflowModelBinding.workflow_ref == str(workflow_ref))
                .order_by(WorkflowModelBinding.instance_id)
            )
        ).scalars().all()
    )


async def save(session: AsyncSession, workflow_ref: str, instance_id: str | int,
               overrides: dict[str, Any], *, source: str = "manual",
               actor_id: int | None = None) -> None:
    """整条覆盖表替换式写入；全空就是把这条绑定删掉（界面上叫「恢复工作流默认模型」）。"""
    clean = {str(k): str(v).strip() for k, v in (overrides or {}).items() if str(v or "").strip()}
    iid = int(instance_id)
    if not clean:
        await drop(session, workflow_ref, iid)
        return
    stmt = (
        pg_insert(WorkflowModelBinding)
        .values(workflow_ref=str(workflow_ref), instance_id=iid, overrides=clean,
                source=source, created_by=actor_id)
        .on_conflict_do_update(
            index_elements=["workflow_ref", "instance_id"],
            set_={"overrides": clean, "source": source, "updated_at": func.now()},
        )
    )
    await session.execute(stmt)


async def drop(session: AsyncSession, workflow_ref: str, instance_id: str | int | None = None) -> int:
    stmt = select(WorkflowModelBinding).where(WorkflowModelBinding.workflow_ref == str(workflow_ref))
    if instance_id is not None:
        stmt = stmt.where(WorkflowModelBinding.instance_id == int(instance_id))
    rows = (await session.execute(stmt)).scalars().all()
    n = 0
    for row in rows:
        await session.delete(row)
        n += 1
    return n


async def prune_to_graph(session: AsyncSession, workflow_ref: str, graph: dict[str, Any]) -> list[str]:
    """图上没有那个节点了就把这一位从所有绑定里摘掉，返回被摘掉的说明。

    换过 JSON 之后节点号会变，留着旧绑定等于给队列埋一颗「要换的模型位在这张图上没有对应节点」
    （gen/model_slots.apply 就是这么报的），用户看到的是一次失败任务而不是一句提示。
    """
    nodes = {str(k) for k in (graph or {})}
    removed: list[str] = []
    for row in await rows_for(session, workflow_ref):
        keep: dict[str, Any] = {}
        for key, value in (row.overrides or {}).items():
            if str(key).split(".", 1)[0] in nodes:
                keep[str(key)] = value
            else:
                removed.append(f"实例 {row.instance_id} · {key}")
        if len(keep) != len(row.overrides or {}):
            if keep:
                row.overrides = keep
            else:
                await session.delete(row)
    return removed


class Catalog:
    """一台实例的「这个节点的这个控件有哪些候选」，问过就不再问第二遍。

    整库同步是「N 条工作流 × 每条几个权重位 × M 台实例」，逐位现问会把 ComfyUI 的
    /object_info/{class} 打出上百次重复请求；实例正忙时这种打法就是自己把读请求挤断。
    """

    def __init__(self, client: Any) -> None:
        self._client = client
        self._by_class: dict[str, dict[str, Any]] = {}

    async def info(self, class_type: str) -> dict[str, Any]:
        if class_type not in self._by_class:
            try:
                payload = await self._client.object_info(class_type)
            except Exception:
                payload = {}
            # 未知 class_type 返回的是 200 + {}，不是 404 —— 判「这台有没有这个节点」用的就是这个
            self._by_class[class_type] = payload.get(class_type) or {}
        return self._by_class[class_type]

    async def options(self, class_type: str, field_name: str) -> list[str]:
        from .gen.model_slots import _options_of

        return _options_of({class_type: await self.info(class_type)}, class_type, field_name)

    async def missing_nodes(self, class_types: list[str]) -> list[str]:
        return sorted({ct for ct in class_types if ct and not await self.info(ct)})


@dataclass
class SyncPlan:
    """一次「把这台配的东西搬到那台」的结论。写不写库由调用方决定，这里只做判断。"""

    overrides: dict[str, str] = field(default_factory=dict)
    applied: list[str] = field(default_factory=list)        # 那台上同名文件真的有 → 照搬
    converted: list[dict[str, str]] = field(default_factory=list)   # 源里那个文件那台没有，按族换了一个
    aligned: list[dict[str, str]] = field(default_factory=list)     # 没绑的位：图里写死的在这台不存在，换成这台真有的
    skipped: list[dict[str, str]] = field(default_factory=list)     # 认不出同族就留空，绝不凑
    missing_nodes: list[str] = field(default_factory=list)          # 整条在这台跑不了的原因

    @property
    def blocked(self) -> bool:
        return bool(self.missing_nodes)

    def as_dict(self) -> dict[str, Any]:
        return {
            "applied": self.applied,
            "converted": self.converted,
            "aligned": self.aligned,
            "skipped": self.skipped,
            "missingNodes": self.missing_nodes,
            "blocked": self.blocked,
            "written": len(self.overrides),
        }


async def plan_sync(client: Any, catalog: Catalog, *, class_map: dict[str, str], graph: dict[str, Any],
                    source: dict[str, Any], slots: list[Any], align_unbound: bool = True,
                    mode_hint: str | None = None) -> SyncPlan:
    """同步的规则。界面上要能把这四类分开说，因为它们对「生成用的是哪个文件」的影响不一样：

    ① 源里挑的文件那台上也有 → 照搬（applied）；
    ② 那台没有同名文件 → 只按 comfy_native.resolve_model_name 的族/模式/角色/步数档判据换一个，
       这一步与导入时对齐权重名是同一把尺子（converted）；
    ③ 开了 align_unbound 时，**没绑的位**也要看：图里写死的是导入那台机器的文件名，
       换台机器很可能根本不存在，那就按同样的判据换成这台真有的（aligned）——
       不做这一步的话接了第二台 ComfyUI 要逐个位手动挑一遍，而且漏一个就是任务失败；
    ④ 认不出同族、或那台答不出候选清单、或图上没有这个节点 → 这一位留空并说明原因（skipped）。

    绝不「取该台第一个可用模型」：把 H3 的底模换成名字最像的 Qwen-Image 是出过事的判法。
    只有图里的值**在这台上真的不存在**才写 aligned：本来就能用的位留成未绑定，
    以后换 JSON 或改图内默认时不会被一条多余的绑定焊死。
    """
    from .gen.model_slots import ROLE_BY_FIELD

    plan = SyncPlan()
    plan.missing_nodes = await catalog.missing_nodes(list(class_map.values()))
    graph = graph or {}

    async def resolve_or_none(node_id: str, class_type: str, field_name: str, wanted: str) -> str | None:
        if not hasattr(client, "resolve_model_name"):
            return None
        context = ((graph.get(node_id) or {}).get("inputs") or {}).get("type")
        try:
            return await client.resolve_model_name(class_type, field_name, wanted,
                                                   context=context if isinstance(context, str) else None,
                                                   mode=mode_hint)
        except Exception:
            return None

    async def place(key: str, value: str, *, node_id: str, class_type: str, field_name: str,
                    bucket_applied: str, bucket_converted: str) -> None:
        options = await catalog.options(class_type, field_name)
        if not options:
            plan.skipped.append({"key": key, "reason": f"这台实例答不出 {class_type}.{field_name} 的可选清单"})
            return
        if value in options:
            plan.overrides[key] = value
            if bucket_applied:
                plan.applied.append(f"{key} ← {value}")
            return
        resolved = await resolve_or_none(node_id, class_type, field_name, value)
        if resolved and resolved in options:
            plan.overrides[key] = resolved
            (plan.converted if bucket_converted == "converted" else plan.aligned).append(
                {"key": key, "from": value, "to": resolved, "role": ROLE_BY_FIELD.get(field_name) or "模型"}
            )
        else:
            plan.skipped.append({"key": key, "reason": f"这台没有 {value}，也没有同族同模式的文件可对上（该位留空，沿用图里写死的）"})

    for key, value in (source or {}).items():
        node_id, _, field_name = str(key).partition(".")
        class_type = class_map.get(node_id)
        if class_type is None:
            plan.skipped.append({"key": key, "reason": "图上没有这个节点（换过 JSON，或这是内置模板的位）"})
            continue
        if not str(value or "").strip():
            continue
        await place(key, str(value).strip(), node_id=node_id, class_type=class_type, field_name=field_name,
                    bucket_applied="y", bucket_converted="converted")

    if align_unbound:
        for slot in slots or []:
            key = str(slot.get("key") if isinstance(slot, dict) else slot.key)
            if key in plan.overrides:
                continue
            node_id = str(slot.get("node") if isinstance(slot, dict) else slot.node)
            class_type = str(slot.get("classType") if isinstance(slot, dict) else slot.class_type)
            field_name = str(slot.get("field") if isinstance(slot, dict) else slot.field_name)
            current = str((slot.get("current") if isinstance(slot, dict) else slot.current) or "")
            if not current:
                continue
            options = await catalog.options(class_type, field_name)
            if options and current in options:
                continue  # 这台本来就能用图里那个，不留绑定
            await place(key, current, node_id=node_id, class_type=class_type, field_name=field_name,
                        bucket_applied="", bucket_converted="aligned")
    return plan
