"""图里的「模型位」：哪个加载节点吃哪个权重文件、这台实例上还有哪些可选。

模型切换只能在这里做，理由与 gen/templates.py 开头那三条一样：字段名（unet_name /
clip_name / vae_name…）、节点编号、以及「有哪些文件可换」全都由实例的 /object_info 回答，
写死在浏览器里就会变成「ComfyUI 400，前端看不懂为什么」。

覆盖值**不做任何就近凑匹配**。这一条是踩过事故的（见 comfy_native.resolve_model_name 的
说明）：把 H3 的底模换成名字最像的 Qwen-Image，提交上去不报缺文件，报一堆采样错。
所以这里要么用户在候选清单里挑了一个真实存在的文件，要么明确报错。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from .base import GenError
from .comfy_native import MODEL_WIDGETS, ComfyNativeClient

# 前端按 role 分组显示；rank 决定下拉里谁排在前面
ROLE_BY_FIELD = {
    "unet_name": "底模",
    "ckpt_name": "一体化模型",
    "model_name": "模型",
    "clip_name": "文本编码器",
    "vae_name": "VAE",
    "lora_name": "LoRA 适配器",
}
_ROLE_RANK = {"底模": 0, "一体化模型": 0, "模型": 1, "文本编码器": 2, "VAE": 3, "LoRA 适配器": 4}


def is_model_field(field_name: str) -> bool:
    """这个字段算不算「一个模型位」。

    除了那五个 ComfyUI 惯用的权重文件字段，还认名字里带 model 的下拉项 ——
    第三方节点就这么写：`FB_Qwen3TTSVoiceClone.model_choice`（0.6B/1.7B 两档）、
    `Apply Whisper.model`（tiny…turbo）。这些不是文件名而是档位，但同样是"换哪个模型"，
    用户在音色那条上要的正是这个。连线型输入（KSampler.model）不会被算进来：
    它的 /object_info 描述是类型名而不是候选清单，取不到清单就没有可换的东西。
    """
    low = str(field_name).lower()
    return field_name in MODEL_WIDGETS or "model" in low


@dataclass
class ModelSlot:
    """一个可以换的权重位。key 就是「节点号.字段名」，与任务里的覆盖表同形。"""

    node: str
    field_name: str
    class_type: str
    label: str
    role: str
    current: str
    options: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{self.node}.{self.field_name}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "node": self.node,
            "field": self.field_name,
            "classType": self.class_type,
            "label": self.label,
            "role": self.role,
            "current": self.current,
            "options": self.options,
            # 图里写的文件名在这台实例上不存在 —— 不遮盖，让界面标出来
            "missing": bool(self.options) and self.current not in self.options,
        }


def _options_of(info: dict[str, Any], class_type: str, field_name: str) -> list[str]:
    """从已经拿到的 /object_info 片段里取候选清单。加载一次全量要 30MB，别反复问。"""
    bag = (info.get(class_type) or {}).get("input") or {}
    for group in ("required", "optional"):
        spec = (bag.get(group) or {}).get(field_name)
        # 候选清单的形状是 [["a.safetensors", ...], {控件配置}]
        if isinstance(spec, list) and spec and isinstance(spec[0], list):
            return [c for c in spec[0] if isinstance(c, str)]
    return []


async def _options(client: ComfyNativeClient, class_type: str, field_name: str) -> list[str]:
    try:
        info = await client.object_info(class_type)
    except Exception:
        return []
    return _options_of(info, class_type, field_name)


def slots_from_info(info: dict[str, Any], graph: dict[str, Any]) -> list[ModelSlot]:
    """用**已经拿到手**的 /object_info 挑模型位。

    体检与重扫本来就拉过一次全量（约 30MB），再让 extract() 按 class 逐个问一遍，
    等于在实例正忙的时候多拖它两趟 —— 那条 ReadError 就是这么来的。
    """
    out: list[ModelSlot] = []
    for node_id, node in graph.items():
        class_type = str(node.get("class_type") or "")
        for field_name in (node.get("inputs") or {}):
            if not is_model_field(field_name):
                continue
            options = _options_of(info, class_type, field_name)
            if not options:
                continue
            role = ROLE_BY_FIELD.get(field_name) or "模型"
            title = ((node.get("_meta") or {}).get("title") or "").strip()
            out.append(
                ModelSlot(
                    node=str(node_id),
                    field_name=field_name,
                    class_type=class_type,
                    label=title or f"{class_type} #{node_id}",
                    role=role,
                    current=str((node.get("inputs") or {}).get(field_name) or ""),
                    options=options,
                )
            )
    out.sort(key=lambda s: (_ROLE_RANK.get(s.role, 9), s.node))
    return out


async def extract(client: ComfyNativeClient, graph: dict[str, Any]) -> list[ModelSlot]:
    """从一张 API 图里挑出所有能换权重的节点。"""
    class_types = sorted({str(n.get("class_type") or "") for n in graph.values() if n.get("class_type")})
    payloads = await asyncio.gather(*(client.object_info(ct) for ct in class_types))
    info = {ct: (payload.get(ct) or {}) for ct, payload in zip(class_types, payloads)}
    return slots_from_info(info, graph)


# 内置模板的图在后端现拼，节点号是 builtin_graphs.py 里写死的，所以这里同样写死一份声明。
# 对不上时 apply() 会明确报「图上没有这个节点」，不会静默换错权重。
BUILTIN_MODEL_LOADERS: dict[str, list[tuple[str, str, str, str]]] = {
    "qwen_image": [
        ("501", "UNETLoader", "unet_name", "Qwen-Image DiT"),
        ("502", "CLIPLoader", "clip_name", "Qwen3-VL 文本编码器"),
        ("503", "VAELoader", "vae_name", "VAE"),
    ],
    "h3_video": [
        ("127", "UNETLoader", "unet_name", "H3 底模"),
        ("128", "CLIPLoader", "clip_name", "H3 文本编码器"),
        ("119", "VAELoader", "vae_name", "视频 VAE"),
        ("120", "VAELoader", "vae_name", "音频 VAE"),
        ("140", "LoraLoaderModelOnly", "lora_name", "Turbo LoRA（只在加速档挂得上）"),
    ],
}
BUILTIN_MODEL_LOADERS["h3_chain"] = BUILTIN_MODEL_LOADERS["h3_video"]


async def builtin(client: ComfyNativeClient, template_key: str) -> list[ModelSlot]:
    """内置模板能换哪些权重。「现在用的是哪个」由 discover_* 回答，与真出图同一套判据。"""
    from .builtin_graphs import discover_h3_weights, discover_qwen_weights

    specs = BUILTIN_MODEL_LOADERS.get(template_key)
    if not specs:
        return []
    class_types = sorted({s[1] for s in specs})
    payloads = await asyncio.gather(*(client.object_info(ct) for ct in class_types))
    info = {ct: (payload.get(ct) or {}) for ct, payload in zip(class_types, payloads)}

    if template_key == "qwen_image":
        w = await discover_qwen_weights(client)
        current = {"501": w.unet, "502": w.clip, "503": w.vae}
    else:
        w = await discover_h3_weights(client)
        current = {"127": w.unet, "128": w.text_encoder, "119": w.vae_video, "120": w.vae_audio, "140": w.turbo_lora or ""}

    out: list[ModelSlot] = []
    for node_id, class_type, field_name, label in specs:
        options = _options_of(info, class_type, field_name)
        if not options:
            continue
        out.append(
            ModelSlot(node=node_id, field_name=field_name, class_type=class_type, label=label,
                      role=ROLE_BY_FIELD.get(field_name) or "模型",
                      current=str(current.get(node_id) or ""), options=options)
        )
    out.sort(key=lambda s: (_ROLE_RANK.get(s.role, 9), s.node))
    return out


async def validate(client: ComfyNativeClient, class_map: dict[str, str], overrides: dict[str, Any],
                   *, where: str = "这台实例") -> None:
    """入队前就把话说清楚：要换的位存不存在、那个文件在这台实例上有没有。

    建图要到队列里才做（要问实例、要上传参考图），但「选了一个不存在的权重」这种错
    等到那时才报，用户看到的只是一个失败任务。这里只需要「节点号 → class」的对应关系，
    所以三条路各有出处：直接给图看图、给工作流看库里那条、给内置模板看 BUILTIN_MODEL_LOADERS。
    """
    seen: dict[str, dict[str, Any]] = {}
    for raw_key, raw_value in (overrides or {}).items():
        key = str(raw_key).strip()
        value = str(raw_value or "").strip()
        if not value:
            continue
        node_id, _, field_name = key.partition(".")
        class_type = class_map.get(node_id)
        if class_type is None:
            raise GenError(
                f"要换的模型位 {key} 在这张图上没有对应节点（没勾加速档时就不挂 Turbo LoRA；"
                "也可能是工作流改过或实例变了，去工作流库重新扫描）",
                kind="workflow_drift",
            )
        if not is_model_field(field_name):
            raise GenError(f"{key} 不是模型位（认 {sorted(MODEL_WIDGETS)} 与名字里带 model 的下拉项）", kind="client_validation")
        if class_type not in seen:
            seen[class_type] = await client.object_info(class_type)
        options = _options_of(seen[class_type], class_type, field_name)
        if not options:
            raise GenError(f"{where}没报出 {class_type}.{field_name} 的可选清单，换不了这个模型",
                           kind="missing_models")
        if value not in options:
            near = [o for o in options if o.split("/")[-1].lower() in value.lower()][:5] or options[:5]
            raise GenError(
                f"{where}没有 {value}（{class_type}.{field_name}）。可换成：{'、'.join(near)}",
                kind="missing_models",
            )


def value_problems(info: dict[str, Any], graph: dict[str, Any]) -> list[str]:
    """图里写死的模型值在这台实例的清单里找不到。

    提交上去只会得到一条 ComfyUI 的 validation 错（"model: 'whisper-large-v3' not in [...]"），
    那是程序的语言。这里提前说人话，并且把可选项递出去 —— 界面上正是有模型下拉可换。
    只报不改：凑一个同族档位是出过事的判法（见本模块开头）。
    """
    out: list[str] = []
    for node_id, node in graph.items():
        class_type = str(node.get("class_type") or "")
        for field_name, value in (node.get("inputs") or {}).items():
            if not is_model_field(field_name) or not isinstance(value, str) or not value:
                continue
            options = _options_of(info, class_type, field_name)
            if options and value not in options:
                out.append(
                    f"{class_type}.{field_name}（#{node_id}）要的是 {value}，这台实例上没有；"
                    f"可换成：{'、'.join(options[:8])}"
                )
    return out


async def apply(client: ComfyNativeClient, graph: dict[str, Any], overrides: dict[str, Any],
                *, where: str = "这台实例") -> tuple[dict[str, Any], list[str]]:
    """把「节点号.字段名 → 文件名」写进图里，逐个核对文件真的存在。

    返回 (改过的图, 说明清单)。说明清单会进任务的 fillNotes，用户在任务详情里要能看见
    自己选的权重确实落到了哪个节点上。
    """
    import copy

    out = copy.deepcopy(graph)
    notes: list[str] = []
    for raw_key, raw_value in (overrides or {}).items():
        key = str(raw_key).strip()
        value = str(raw_value or "").strip()
        if not value:
            continue
        node_id, _, field_name = key.partition(".")
        node = out.get(node_id)
        if node is None:
            raise GenError(
                f"要换的模型位 {key} 在这张图上没有对应节点（没勾加速档时就不挂 Turbo LoRA；"
                "也可能是工作流改过或实例变了，去工作流库重新扫描）",
                kind="workflow_drift",
            )
        if not is_model_field(field_name):
            raise GenError(f"{key} 不是模型位（认 {sorted(MODEL_WIDGETS)} 与名字里带 model 的下拉项）", kind="client_validation")
        class_type = str(node.get("class_type") or "")
        options = await _options(client, class_type, field_name)
        if not options:
            raise GenError(f"{where}没有报出 {class_type}.{field_name} 的可选清单，换不了这个模型",
                           kind="missing_models")
        if value not in options:
            near = [o for o in options if o.split("/")[-1].lower() in value.lower()][:5] or options[:5]
            raise GenError(
                f"{where}没有 {value}（{class_type}.{field_name}）。可换成：{'、'.join(near)}",
                kind="missing_models",
            )
        before = str((node.get("inputs") or {}).get(field_name) or "")
        node.setdefault("inputs", {})[field_name] = value
        notes.append(f"{ROLE_BY_FIELD.get(field_name, '模型')} #{node_id} ← {value}"
                     + (f"（原 {before}）" if before and before != value else ""))
    return out, notes
