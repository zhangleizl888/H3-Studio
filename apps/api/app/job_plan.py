"""派发前的参数表与校验（借鉴 oh-my-minimaxh3-director 的「参数确认」阶段）。

三条规矩，都是这个模块存在理由：

1. **校验只在这里做一次，前端不做表单校验。** `/jobs/plan` 与 `/jobs/batch` 共用同一套判断，
   免得出现「预览一片绿、真派发照样烧十几分钟显存」。
2. **耗时是按 PLAN §11.1.1 的实测锚点线性外推的，不是实测**：视频锚 864×480 / 56 帧 / Turbo 8 步 = 443s，
   图片锚 1024² / 25 步 = 28s（权重常驻）。分辨率换档、非 Turbo 的步数倍率都没有实测点，
   所以每张表都带 `etaBasis`，界面上必须把「外推」两个字显示出来，不许装成测量值。
3. **提示词形状要在这里拦。** 项目选了 hybrid，实际提交的是三段式回退文本，这种片子跑 7 分钟
   才发现白跑 —— 前端把 `meta.promptMode` 带上来，这里按形状核对。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .gen.builtin_graphs import h3_length
from .gen.templates import TEMPLATES

#: 实测锚点（见 PLAN §11.1.1 / §11.1.2），换锚点要同步改这里
VIDEO_ANCHOR = {"mp": 0.41472, "frames": 56, "steps": 8, "seconds_total": 443.0}
IMAGE_ANCHOR = {"mp": 1.048576, "steps": 25, "seconds_total": 28.0, "cold_extra": 229.0}

#: 本机 24GB 单卡的实测边界：864×480（0.41MP）能跑，1344×768（1.03MP）跑不了
LOCAL_MP_WARN = 0.55
LOCAL_MP_BLOCK = 0.9

#: H3 单次生成的合理时长；短于 5 秒不是不行，是固定开销摊不平
SHOT_SECONDS_WARN = 5.0

_WENWU_MARKS = ("一、整体场景描述", "二、分定时镜头")
_SIX_MARKS = ("subject_definitions:", "summary:", "retention_analysis:", "detailed_description:", "overall_soundscape:", "non_diegetic_music:")


@dataclass
class InstanceInfo:
    """实例的可用信息。路由从 gen_instances 表一次查齐再传进来。"""

    id: str
    label: str = ""
    placement: str = "local"
    protocol: str = "comfy_native"
    probe_ok: bool | None = None
    circuit_open: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "label": self.label, "placement": self.placement, "protocol": self.protocol, "probeOk": self.probe_ok, "circuitOpen": self.circuit_open}


@dataclass
class PlanRow:
    index: int
    title: str
    template: str
    kind: str
    instance: dict[str, Any] | None
    slots: dict[str, Any]
    derived: dict[str, Any] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)
    blocked: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "title": self.title,
            "template": self.template,
            "kind": self.kind,
            "instance": self.instance,
            "slots": self.slots,
            "derived": self.derived,
            "problems": self.problems,
            "blocked": self.blocked,
        }


def _coerce(spec_type: str, value: Any) -> Any:
    try:
        if spec_type == "int":
            return int(value)
        if spec_type == "float":
            return float(value)
        if spec_type == "bool":
            return bool(value)
    except (TypeError, ValueError):
        return None
    return value


def _mp(width: Any, height: Any) -> float:
    try:
        return (int(width) * int(height)) / 1_000_000
    except (TypeError, ValueError):
        return 0.0


def _eta_video(frames: int, steps: int, mp: float) -> int:
    per_frame = VIDEO_ANCHOR["seconds_total"] / VIDEO_ANCHOR["frames"] / (VIDEO_ANCHOR["steps"] / max(1, steps))
    scale = mp / VIDEO_ANCHOR["mp"] if mp else 1.0
    return int(round(per_frame * frames * max(1.0, scale)))


def _eta_image(steps: int, mp: float) -> int:
    scale = mp / IMAGE_ANCHOR["mp"] if mp else 1.0
    return int(round(IMAGE_ANCHOR["seconds_total"] * (max(1, steps) / IMAGE_ANCHOR["steps"]) * max(1.0, scale)))


def _check_prompt_shape(meta: dict[str, Any], prompt: str, row: PlanRow) -> None:
    """项目声明的模式与实际提交的文本形状不一致 → 警告。回退是刻意行为，所以要让人看见。"""
    mode = (meta or {}).get("promptMode") or (meta or {}).get("prompt_mode")
    if not mode:
        return
    text = prompt
    if mode in ("six_section", "hybrid"):
        missing = [m for m in _SIX_MARKS if m not in text]
        if missing:
            row.problems.append(f"项目模式是 {mode}，但提示词缺 {len(missing)} 个段名（{missing[0]} 起）—— 现在提交的是三段式回退文本")
        if mode == "hybrid" and "constraints:" not in text:
            row.problems.append("hybrid 提示词没有 constraints: 段")
    elif mode == "wenwu":
        missing = [m for m in _WENWU_MARKS if m not in text]
        if missing:
            row.problems.append(f"项目模式是 wenwu，但提示词缺「{missing[0]}」—— 现在提交的是三段式回退文本")


def plan_item(index: int, item: dict[str, Any], *, instances: dict[str, InstanceInfo], known_media: set[int] | None) -> PlanRow:
    """一条待派发请求 → 一行参数表。"""
    template = str(item.get("template") or "")
    raw_slots = item.get("slots") or {}
    meta = item.get("meta") or {}
    title = str(item.get("title") or f"{item.get('kind') or 'job'} · {index + 1}")
    kind = str(item.get("kind") or "video")

    # 工作流页的高级用法直接给 graph：参数表没法按槽位校验它，只如实说明校验范围
    # （缺模型那一层仍在 _enqueue 里查，那条检查才是真拦得住人的）。
    graph = item.get("graph")
    if graph:
        inst_id = item.get("instanceId") or item.get("instance_id")
        info = instances.get(str(inst_id)) if inst_id else (next(iter(instances.values())) if len(instances) == 1 else None)
        row = PlanRow(index=index, title=title, template="(直接给 graph)", kind=kind, instance=info.as_dict() if info else None, slots={})
        row.derived = {"graphNodes": len(graph) if isinstance(graph, dict) else 0, "etaSeconds": None, "etaBasis": "自拼工作流没有参数表可依，耗时不给估计"}
        if row.instance is None:
            row.problems.append("自拼工作流也必须指明实例（instanceId）")
            row.blocked = True
        row.problems.append("这条是直接给的 graph：槽位、时长、提示词形状都不在参数表的校验范围内")
        return row

    if template not in TEMPLATES:
        avail = "、".join(TEMPLATES) or "（没有已注册模板）"
        return PlanRow(index=index, title=title, template=template, kind=kind, instance=None, slots=raw_slots, problems=[f"没有生成模板 {template}（可用：{avail}）"], blocked=True)

    spec = TEMPLATES[template]
    row = PlanRow(index=index, title=title, template=template, kind=kind, instance=None, slots={})

    # 实例：猜错实例的代价是白烧十几分钟，所以缺省只允许「全场只有一台」
    inst_id = item.get("instanceId") or item.get("instance_id")
    if inst_id:
        info = instances.get(str(inst_id))
        if info is None:
            row.problems.append(f"实例 {inst_id} 不在已登记的实例里（去 设置 → 生成实例 看 id）")
            row.blocked = True
        else:
            row.instance = info.as_dict()
            if info.circuit_open:
                row.problems.append(f"实例「{info.label}」已熔断（通常是云端余额不足），队列不会再往它派活")
                row.blocked = True
            elif info.probe_ok is False:
                row.problems.append(f"实例「{info.label}」最近一次探活失败，派发上去多半直接报错")
    elif len(instances) == 1:
        row.instance = next(iter(instances.values())).as_dict()
    else:
        row.problems.append(f"有 {len(instances)} 台实例却没指定用哪一台（instanceId）" if instances else "一台生成实例都没有")
        row.blocked = True

    # 槽位归一化 + 范围校验：与 templates._fill 同一份 SlotSpec，两处不会说两套话
    local_only = bool(row.instance and row.instance.get("placement") == "local")
    for s in spec.slots:
        v = raw_slots.get(s.name, s.default)
        if v is None or v == "":
            if s.required:
                row.problems.append(f"「{s.label}」是必填的")
                row.blocked = True
                continue
            v = s.default
        cast = _coerce(s.type, v)
        if s.type in ("int", "float", "bool") and cast is None:
            row.problems.append(f"「{s.label}」的值 {v!r} 不是合法的{s.type}")
            row.blocked = True
            continue
        v = cast
        if s.type == "int" and v is not None:
            if s.min is not None and v < s.min:
                row.problems.append(f"「{s.label}」{v} 低于下限 {s.min}")
                row.blocked = True
            if s.max is not None and v > s.max:
                row.problems.append(f"「{s.label}」{v} 超过上限 {s.max}")
                row.blocked = True
        if s.type in ("media", "media_list") and v:
            ids = v if isinstance(v, list) else [v]
            for m in ids:
                try:
                    mi = int(m)
                except (TypeError, ValueError):
                    row.problems.append(f"「{s.label}」里的 {m!r} 不是服务端媒体 id")
                    row.blocked = True
                    continue
                # known_media 是 None 表示这台部署没有媒体库（无库模式），那就没得查
                if known_media is not None and mi not in known_media:
                    row.problems.append(f"「{s.label}」引用的媒体 {mi} 不在库里（本地上传要先落到服务端才能当参考图）")
                    row.blocked = True
        row.slots[s.name] = v

    prompt = str(row.slots.get("prompt") or "")
    if prompt and len(prompt) < 12:
        row.problems.append(f"提示词只有 {len(prompt)} 字，多半是空的占位文本")
    _check_prompt_shape(meta, prompt, row)

    derived: dict[str, Any] = {"promptChars": len(prompt)}
    if template == "h3_video":
        seconds = float(row.slots.get("seconds") or 5)
        frames = h3_length(seconds)
        mp = _mp(row.slots.get("width"), row.slots.get("height"))
        steps = int(row.slots.get("steps") or (8 if row.slots.get("turbo") else 25))
        derived.update(
            {
                "frameCount": frames,
                "realSeconds": round(frames / 24, 2),
                "megapixels": round(mp, 3),
                "steps": steps,
                "turbo": bool(row.slots.get("turbo")),
                "etaSeconds": _eta_video(frames, steps, mp),
                "etaBasis": "外推：锚点 864×480/56帧/Turbo8步=443s（PLAN §11.1.1），非实测",
            }
        )
        if seconds < SHOT_SECONDS_WARN:
            row.problems.append(f"这一镜只有 {seconds:g}s，冷启动与权重加载的固定开销摊不平（同卡实测一镜 6–7 分钟）")
        if mp and local_only and mp > LOCAL_MP_BLOCK:
            row.problems.append(f"本机 24GB 实测 {row.slots.get('width')}×{row.slots.get('height')}（{mp:.2f}MP）跑不动，1344×768 那档从没成功过")
            row.blocked = True
        elif mp and local_only and mp > LOCAL_MP_WARN:
            row.problems.append(f"{mp:.2f}MP 已超出本机实测过的 0.41MP 档，全质量档在这张卡上没有成功记录")
        if row.slots.get("last_frame") and not row.slots.get("first_frame"):
            row.problems.append("只给了结束帧没给起始帧：官方 FL2VA 的首尾帧要连着给，多半不成立")
        if row.slots.get("turbo") and steps > 8:
            row.problems.append(f"Turbo 档却给了 {steps} 步：Turbo 的加速就没了，这条比 8 步多花约 {round((steps / 8 - 1) * 100)}% 的时间")
    elif template == "qwen_image":
        mp = _mp(row.slots.get("width"), row.slots.get("height"))
        steps = int(row.slots.get("steps") or 25)
        derived.update(
            {
                "megapixels": round(mp, 3),
                "steps": steps,
                "refCount": len(row.slots.get("refs") or []),
                "etaSeconds": _eta_image(steps, mp),
                "etaBasis": "外推：锚点 1024²/25步=28s（权重常驻）；冷启动另加约 4 分钟",
            }
        )
        if mp and local_only and mp > 1.5:
            row.problems.append(f"{mp:.2f}MP 出图在本机没有成功记录（实测最大 1344×768）")
    elif template == "h3_chain":
        segs = row.slots.get("segments") or []
        derived.update({"segmentCount": len(segs) if isinstance(segs, list) else 0, "etaSeconds": None, "etaBasis": "续拍链没有实测锚点，不给耗时估计"})
    row.derived = derived
    return row


def plan_jobs(items: list[dict[str, Any]], *, instances: dict[str, InstanceInfo], known_media: set[int] | None) -> dict[str, Any]:
    rows = [plan_item(i, it, instances=instances, known_media=known_media) for i, it in enumerate(items)]
    # 被拦下的那条不会进队列，所以它的"耗时"不该算进总额
    eta = sum(int(r.derived.get("etaSeconds") or 0) for r in rows if not r.blocked)
    return {
        "rows": [r.as_dict() for r in rows],
        "totals": {
            "count": len(rows),
            "blocked": sum(1 for r in rows if r.blocked),
            "warned": sum(1 for r in rows if r.problems and not r.blocked),
            "etaSeconds": eta,
            "etaMinutes": round(eta / 60, 1),
            # 界面上必须把这两个字显示出来：这些数字是外推，不是实测
            "etaIsEstimate": True,
        },
    }


def media_ids_in(items: list[dict[str, Any]]) -> set[int]:
    """把请求里所有媒体引用抽出来，一次 SQL 查存在性，别一条条问。"""
    out: set[int] = set()
    for it in items:
        for v in (it.get("slots") or {}).values():
            for x in (v if isinstance(v, list) else [v]):
                if isinstance(x, int) or (isinstance(x, str) and x.isdigit() and len(x) <= 15):
                    try:
                        out.add(int(x))
                    except (TypeError, ValueError):
                        continue
    return out
