"""生成模板：前端只填槽，图在后端拼。

不把 graph 交给浏览器拼，有三条硬理由：
  1. 模型文件名必须由实例回答 —— 本机 Qwen-Image/H3 的目录与大驼峰命名和文档不一致，
     写死在 JS 里就会变成「ComfyUI 400，前端看不懂为什么」。
  2. 参考图、首帧、尾帧是服务端 media 库里的文件，得先上传到目标实例的 input 目录才用得上。
  3. 槽位与节点的对应关系一旦分两端维护，改工作流必漏一端。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from .base import GenError
from .builtin_graphs import (
    CHAIN_NODE,
    H3Weights,
    QwenWeights,
    discover_h3_weights,
    discover_qwen_weights,
    h3_chain_graph,
    h3_length,
    h3_video_graph,
    qwen_image_graph,
)
from ..logging_setup import get_logger

log = get_logger("gen.templates")


@dataclass
class SlotSpec:
    name: str
    label: str
    type: str  # text | int | float | bool | enum | media | media_list
    default: Any = None
    required: bool = False
    min: float | None = None
    max: float | None = None
    options: list[str] = field(default_factory=list)
    hint: str = ""
    multiline: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "type": self.type,
            "default": self.default,
            "required": self.required,
            "min": self.min,
            "max": self.max,
            "options": self.options,
            "hint": self.hint,
            "multiline": self.multiline,
        }


@dataclass
class BuildContext:
    """建图时要问得到的东西：实例客户端 + 媒体库位置。"""

    client: Any
    media_root: Path
    _uploads: dict[tuple[str, int], str] = field(default_factory=dict, repr=False)

    async def to_input(self, media_id: int | str) -> str:
        """服务端 media id → 该实例 input 目录里的文件名（同实例同文件只传一次）。"""
        key = (str(getattr(self.client, "base_url", "?")), int(media_id))
        if key in self._uploads:
            return self._uploads[key]
        path = await self.local_path(media_id)
        name = await self.client.upload(path)
        self._uploads[key] = name
        return name

    async def local_path(self, media_id: int | str) -> Path:
        from sqlalchemy import select

        from ..db import session_factory
        from ..models import Media

        async with session_factory()() as s:
            row = await s.get(Media, int(media_id))
        if row is None or not row.path:
            raise GenError(f"媒体 {media_id} 不存在或还没有落盘文件", kind="client_validation")
        path = (self.media_root / row.path).resolve()
        if not path.is_relative_to(self.media_root.resolve()) or not path.exists():
            raise GenError(f"媒体 {media_id} 的文件不在媒体库里：{path.name}", kind="client_validation")
        return path


Builder = Callable[[BuildContext, dict[str, Any]], Awaitable[dict[str, Any]]]


@dataclass
class TemplateSpec:
    key: str
    label: str
    kind: str  # image | video
    description: str
    slots: list[SlotSpec]
    build: Builder
    # 前端按这个分组显示（图片 / 视频）
    group: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "kind": self.kind,
            "group": self.group or self.label,
            "description": self.description,
            "slots": [s.as_dict() for s in self.slots],
            "builtin": True,
        }


def _slot(name: str, label: str, type_: str, **kw: Any) -> SlotSpec:
    return SlotSpec(name=name, label=label, type=type_, **kw)


def _fill(specs: list[SlotSpec], slots: dict[str, Any]) -> dict[str, Any]:
    """按槽位定义补默认值并校验必填，前端只传它改过的那些。"""
    out: dict[str, Any] = {}
    for s in specs:
        v = slots.get(s.name, s.default)
        if v is None or v == "":
            if s.required:
                raise GenError(f"槽位「{s.label}」是必填的", kind="client_validation")
            v = s.default
        if s.type == "int" and v is not None:
            v = int(v)
        elif s.type == "float" and v is not None:
            v = float(v)
        elif s.type == "bool":
            v = bool(v)
        elif s.type in ("media", "media_list") and v is not None and not isinstance(v, list):
            v = [v] if s.type == "media_list" else v
        if s.type == "int":
            if s.min is not None and v < s.min:
                raise GenError(f"「{s.label}」不能小于 {s.min}", kind="client_validation")
            if s.max is not None and v > s.max:
                raise GenError(f"「{s.label}」不能大于 {s.max}", kind="client_validation")
        out[s.name] = v
    return out


# ───────── 内置模板 ─────────

async def _build_qwen_image(ctx: BuildContext, slots: dict[str, Any]) -> dict[str, Any]:
    weights: QwenWeights = await discover_qwen_weights(ctx.client)
    refs = [await ctx.to_input(m) for m in (slots.get("refs") or [])]
    return qwen_image_graph(
        weights,
        prompt=slots["prompt"],
        negative_prompt=slots.get("negative_prompt") or "",
        width=slots.get("width") or 1024,
        height=slots.get("height") or 1024,
        steps=slots.get("steps") or 25,
        cfg=1.0 if slots.get("cfg") is None else slots["cfg"],
        seed=slots.get("seed") or 0,
        filename_prefix=slots.get("filename_prefix") or "h3/image",
        ref_images=refs,
        ref_resolution=slots.get("resolution") or 1024,
    )


async def _build_h3_video(ctx: BuildContext, slots: dict[str, Any]) -> dict[str, Any]:
    weights: H3Weights = await discover_h3_weights(ctx.client)
    first = slots.get("first_frame")
    last = slots.get("last_frame")
    return h3_video_graph(
        weights,
        prompt=slots["prompt"],
        width=slots.get("width") or 864,
        height=slots.get("height") or 480,
        length=h3_length(float(slots.get("seconds") or 5.0)),
        steps=slots.get("steps") or (8 if slots.get("turbo") else 25),
        seed=slots.get("seed") or 0,
        filename_prefix=slots.get("filename_prefix") or "h3/video",
        use_turbo=bool(slots.get("turbo")),
        first_frame=await ctx.to_input(first) if first else None,
        last_frame=await ctx.to_input(last) if last else None,
    )


async def _build_h3_chain(ctx: BuildContext, slots: dict[str, Any]) -> dict[str, Any]:
    weights: H3Weights = await discover_h3_weights(ctx.client)
    raw = slots.get("segments") or []
    if not isinstance(raw, list):
        raise GenError("段落序列（segments）必须是数组，每项 {prompt, seconds?}", kind="client_validation")
    segments: list[dict[str, Any]] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise GenError(f"第 {i + 1} 段必须是对象（prompt/seconds）", kind="client_validation")
        prompt = str(item.get("prompt") or "").strip()
        if not prompt:
            raise GenError(f"第 {i + 1} 段没有提示词", kind="client_validation")
        sec = item.get("seconds")
        segments.append({"prompt": prompt, "seconds": float(sec) if sec else None})

    first = slots.get("first_frame")
    end = slots.get("end_frame")
    if end and not first:
        raise GenError(
            "长片续拍的「结尾帧」必须配链头的「起始帧」：官方 FL2VA 首尾帧只在首帧视频模式成立。"
            "请先给链头镜头出起始帧，或去掉这张结尾帧。",
            kind="client_validation",
        )

    async def _uploaded(media_id: Any) -> str | None:
        return await ctx.to_input(media_id) if media_id else None

    graph = h3_chain_graph(
        weights,
        segments=segments,
        archive_dir=str(slots.get("archive_dir") or ""),
        width=slots.get("width") or 864,
        height=slots.get("height") or 480,
        seconds=float(slots.get("seconds") or 5.0),
        guide_frames=str(slots.get("guide_frames") or "22"),
        steps=slots.get("steps") or (8 if slots.get("turbo") else 25),
        seed=slots.get("seed") or 0,
        use_turbo=bool(slots.get("turbo")),
        auto_reseed=bool(slots.get("auto_reseed")),
        first_frame=await _uploaded(first),
        end_frame=await _uploaded(end),
        identity_frame=await _uploaded(slots.get("identity_frame")),
        filename_prefix=slots.get("filename_prefix") or "h3/chain",
    )
    # 第三方节点的控件会随版本往末尾追加，漏一个就是一条只点名第一个缺项的
    # 「failed validation」。这里按实例自己报的 required 清单对一遍，一次说全。
    node = graph.get("160") or {}
    try:
        info = await ctx.client.object_info(CHAIN_NODE)
        spec = ((info.get(CHAIN_NODE) or {}).get("input") or {}).get("required") or {}
    except Exception as exc:  # 问不到就不拦，交给 ComfyUI 自己的校验
        log.warning("读不到 %s 的控件清单，跳过入参自检：%s", CHAIN_NODE, exc)
        return graph
    lacking = [k for k in spec if k not in (node.get("inputs") or {})]
    if lacking:
        raise GenError(f"{CHAIN_NODE} 缺少必填控件：{'、'.join(lacking)}", kind="client_validation")
    return graph


QWEN_IMAGE = TemplateSpec(
    key="qwen_image",
    label="Qwen-Image 出图",
    kind="image",
    group="图片",
    description="文生图；带参考图时同一节点直接做角色一致性编辑（尺寸跟随第一张参考图）。",
    slots=[
        _slot("prompt", "画面提示词", "text", required=True, multiline=True),
        _slot("negative_prompt", "负向提示词", "text", default="", multiline=True),
        _slot("refs", "参考图", "media_list", default=[], hint="按顺序喂给文本编码器；出图尺寸跟随第一张"),
        _slot("width", "宽", "int", default=1024, min=256, max=2048, hint="有参考图时忽略"),
        _slot("height", "高", "int", default=1024, min=256, max=2048, hint="有参考图时忽略"),
        _slot("resolution", "参考图像素档", "int", default=1024, min=512, max=2048, hint="参考图会被压到这个边长的像素量级"),
        _slot("steps", "步数", "int", default=25, min=4, max=60),
        _slot("cfg", "CFG", "float", default=1.0, min=0.5, max=8.0, hint="官方出厂值是 1，调高容易烧坏画面"),
        _slot("seed", "种子", "int", default=0, min=0, max=2**53, hint="同一角色固定种子有助于一致性"),
        _slot("filename_prefix", "输出前缀", "text", default="h3/image", hint="批量出图带上镜号/角色名，产物才认得出是谁的"),
    ],
    build=_build_qwen_image,
)

H3_VIDEO = TemplateSpec(
    key="h3_video",
    label="MiniMax H3 出片",
    kind="video",
    group="视频",
    description="文生视频 / 首帧 / 首尾帧三合一：不给帧图就是 t2v，只给首帧是 i2v，首尾都给是 fl2v。",
    slots=[
        _slot("prompt", "视频提示词", "text", required=True, multiline=True, hint="按项目模式来：三段式 / 官方六段式 / 中文导演分镜块 / hybrid"),
        _slot("first_frame", "起始帧", "media", default=None),
        _slot("last_frame", "结束帧", "media", default=None),
        _slot("width", "宽", "int", default=864, min=256, max=1344),
        _slot("height", "高", "int", default=480, min=256, max=1344),
        _slot("seconds", "时长（秒）", "int", default=5, min=1, max=15, hint="会向上吸附到合法帧数 17k+5"),
        _slot("turbo", "Turbo 四/八步", "bool", default=True),
        _slot("steps", "步数", "int", default=8, min=4, max=50, hint="留空则按 Turbo 取 8/25"),
        _slot("seed", "种子", "int", default=0, min=0, max=2**53),
        _slot("filename_prefix", "输出前缀", "text", default="h3/video", hint="批量派发按镜号前缀（如 项目/h3/镜007），出片才认得出是哪一镜"),
    ],
    build=_build_h3_video,
)

H3_CHAIN = TemplateSpec(
    key="h3_chain",
    label="MiniMax H3 长片续拍",
    kind="video",
    group="视频",
    description="段间引导无缝续拍（SequenceForge）：一条承接链一个任务，前段从 latent 存档秒级回放，只采样新段，产物是接到底的整条片。",
    slots=[
        _slot(
            "segments",
            "段落序列",
            "json_list",
            required=True,
            hint='按顺序的段，每段 {"prompt": "...", "seconds": 5}；seconds 留空则跟随「每段时长」',
        ),
        _slot("archive_dir", "存档目录", "text", required=True, hint="同一条承接链固定用同一个名字，续拍靠它认档；产物在 ComfyUI 的 output/h3_projects/<名字>/"),
        _slot("seconds", "每段时长（秒）", "float", default=5.0, min=0.5, max=15.0, hint="段落没单独给秒数时用它"),
        _slot("first_frame", "起始帧", "media", default=None, hint="只作用于链头那段（i2v 起手）"),
        _slot("end_frame", "结尾帧", "media", default=None, hint="官方 FL2VA 剧情终点，锚在整链最后一段末帧"),
        _slot("identity_frame", "身份锚定帧", "media", default=None, hint="每段末尾都钉一张人物参考帧，抑制长链漂移"),
        _slot("width", "宽", "int", default=864, min=256, max=1344),
        _slot("height", "高", "int", default=480, min=256, max=1344),
        _slot("guide_frames", "引导帧数", "enum", default="22", options=["5", "22", "39", "56"], hint="段间重叠桥，越大越顺但更吃显存"),
        _slot("turbo", "Turbo 四/八步", "bool", default=True),
        _slot("steps", "步数", "int", default=8, min=4, max=50, hint="留空则按 Turbo 取 8/25"),
        _slot("seed", "种子", "int", default=0, min=0, max=2**53, hint="第 i 段用 种子+i；续跑时以存档记录的种子序列为准"),
        _slot("auto_reseed", "接缝超阈值自动重摇", "bool", default=False, hint="开着时长不可控：触发一次重摇就多整段采样时间"),
    ],
    build=_build_h3_chain,
)

TEMPLATES: dict[str, TemplateSpec] = {t.key: t for t in (QWEN_IMAGE, H3_VIDEO, H3_CHAIN)}


def public_list() -> list[dict[str, Any]]:
    return [t.as_dict() for t in TEMPLATES.values()]


async def build_graph(ctx: BuildContext, key: str, slots: dict[str, Any]) -> dict[str, Any]:
    spec = TEMPLATES.get(key)
    if spec is None:
        raise GenError(f"没有这个生成模板：{key}（可用：{'、'.join(TEMPLATES)}）", kind="client_validation")
    filled = _fill(spec.slots, slots or {})
    graph = await spec.build(ctx, filled)
    log.info("模板 %s 建图完成：%d 个节点", key, len(graph))
    return graph
