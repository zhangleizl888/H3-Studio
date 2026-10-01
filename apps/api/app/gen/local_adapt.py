"""把外部导出的工作流改写成本机 ComfyUI 能执行的样子。

导入的工作流里总有本机没有的节点，大致四种：

1. 作者自己造的「好看控件」（CR Prompt Text / JjkText / DF_Integer）—— 核心里有语义
   等价的 Primitive，换掉即可；
2. 只存在于 RunningHub 的专有节点（RHMiniMaxH3ModelLoader / RHMiniMaxH3RefGen）——
   用本机 core 的 MiniMaxH3ReferenceToVideo 采样链整段重建；
3. 只转发不产语义的包装节点（ModelPreviewOverrideKJ、各种 attention patch、
   easy cleanGpuUsed、Anything Everywhere）—— 穿透：把下游直接接到它的上游，
   丢掉的只是可选的性能/预览行为，不改生成结果；
4. 注释与调试节点（Note / MarkdownNote / ShowAnything|Mie / 忽略多组孤海）—— 没人
   引用就删。

拿不准语义的一律**不猜**：原样留着并登记成 gap，让上层明确告诉用户「这条工作流在这台
实例上还缺什么、要装哪个包」。静默降级交出一张看着完整、跑起来必炸的图，比报错危险得多。

每条改动都留 Change 记录（工作流详情页展示）—— 我们改了用户的图，必须可审计。
所有规则只在「本机确实没有这个类」时生效：以后装了包，原图就原样跑。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, NamedTuple

from .builtin_graphs import h3_length

# ───────────────────────── 连线与形状的小工具 ─────────────────────────


def is_link(val: Any) -> bool:
    """API 格式里的连线是 [节点id, 槽号]。"""
    return isinstance(val, list) and len(val) == 2 and isinstance(val[0], (str, int)) and isinstance(val[1], int)


def autogrow_items(inputs: dict[str, Any], group: str) -> list[tuple[str, Any]]:
    """收集展平写法的 autogrow 项：`ref_images.ref_image_0` 归到 group=ref_images，按序号排。"""
    out = [(name.split(".", 1)[1], val) for name, val in inputs.items() if name.startswith(group + ".")]
    if isinstance(inputs.get(group), dict):      # 老写法：整组是一个 dict
        out += list(inputs[group].items())
    out.sort(key=lambda kv: kv[0])
    return out


def group_key(object_info: dict[str, Any], class_type: str, group: str) -> str | None:
    """从 object_info 问出 autogrow 的项前缀（ref_image_ / video / …）。"""
    info = object_info.get(class_type) or {}
    for sec in ("required", "optional"):
        spec = (info.get("input") or {}).get(sec, {}).get(group)
        if isinstance(spec, list) and len(spec) > 1 and isinstance(spec[1], dict):
            tpl = spec[1].get("template") or {}
            p = tpl.get("prefix")
            if isinstance(p, str):
                return p
    return None


def consumers_of(graph: dict[str, Any], node_id: str) -> list[tuple[str, str, int]]:
    """谁连了这个节点的输出：[(消费节点, 输入名, 来源槽号)]，autogrow 展平项也算。"""
    out = []
    for nid, nd in graph.items():
        for fname, val in (nd.get("inputs") or {}).items():
            if is_link(val) and str(val[0]) == str(node_id):
                out.append((nid, fname, int(val[1])))
    return out


def rewire(graph: dict[str, Any], node_id: str, slot: int, target: list[Any]) -> int:
    n = 0
    for nid, fname, sslot in consumers_of(graph, node_id):
        if sslot == slot:
            graph[nid]["inputs"][fname] = list(target)
            n += 1
    return n


def slot_type(graph: dict[str, Any], object_info: dict[str, Any], ref: Any) -> str | None:
    """某条连线指向的那个输出是什么类型。类型不对的替换会被 ComfyUI 直接退回，先在这里挡。"""
    if not is_link(ref):
        return None
    nd = graph.get(str(ref[0])) or {}
    types = (object_info.get(nd.get("class_type") or "") or {}).get("output") or []
    idx = int(ref[1])
    return str(types[idx]) if idx < len(types) else None


def _add(graph: dict[str, Any], class_type: str, inputs: dict[str, Any], title: str) -> str:
    nums = [int(k) for k in graph if str(k).isdigit()]
    nid = str(max(nums, default=0) + 1)
    graph[nid] = {"class_type": class_type, "inputs": inputs, "_meta": {"title": title}}
    return nid


@dataclass
class Change:
    node: str
    action: str          # 删除 | 穿透 | 替换 | 展开
    from_class: str
    to_class: str
    detail: str

    def as_dict(self) -> dict[str, str]:
        return {"node": self.node, "action": self.action, "from": self.from_class,
                "to": self.to_class, "detail": self.detail}


@dataclass
class Gap:
    node: str
    class_type: str
    reason: str
    pack: str = ""

    def as_dict(self) -> dict[str, str]:
        return {"node": self.node, "class_type": self.class_type, "reason": self.reason, "pack": self.pack}


@dataclass
class Ctx:
    """一次改写过程的上下文。跨规则的中间状态放这里，不塞进图里。"""

    object_info: dict[str, Any]
    changes: list[Change] = field(default_factory=list)
    gaps: list[Gap] = field(default_factory=list)
    vae_pairs: dict[str, tuple[list[Any], list[Any]]] = field(default_factory=dict)
    #: 已经展开过的节点。展开规则一轮只许走一次，否则失败的节点会被反复报同一个 gap。
    done: set[str] = field(default_factory=set)
    #: 指向作者机器素材的加载器控件（本机没有该文件不算缺东西，是等前端重指的槽位）
    pending_media: list[str] = field(default_factory=list)

    @property
    def known(self) -> set[str]:
        return set(self.object_info)


# ───────────────────────── 规则表 ─────────────────────────

#: 纯注释/调试节点。没有下游才敢删。
DOC_CLASSES = {
    "Note", "MarkdownNote", "ShowAnything|Mie", "忽略多组孤海", "PreviewAny",
    "Fast Groups Bypasser (rgthree)", "Image Comparer (rgthree)",
}

#: 只把某一个输入原样传到输出槽 0 的包装节点 → 穿透。键 = 类名，值 = 转发的输入名。
#: MinimaxH3LatentUpscaler3D 也在这一列：本机没有那级学习型 3D latent 上采样，
#: 退成「不放大」而不是换成 LatentUpscaleBy —— 后者按普通 VAE latent 的通道数处理，
#: H3 的音视频混合 latent 会被算坏。少一级上采样是画质损失，算错 latent 是废片。
PASSTHROUGH: dict[str, str] = {
    "ModelPreviewOverrideKJ": "model",
    "MiniMaxLowVRAMAttention": "model",
    "MiniMaxH3MemoryEfficientSageAttentionPatch": "model",
    "PathchSageAttentionKJ": "model",
    "MinimaxH3LatentUpscaler3D": "latent",
    "easy cleanGpuUsed": "anything",
    "Anything Everywhere": "anything",
    "Anything Everywhere3": "anything",
    # pysssss 的 ShowText 只是把文本显示出来，下游要的还是那份文本
    "ShowText|pysssss": "text",
    # LayerStyle 的拼接板：透传已拼好的画面，只是丢了对照框上的标签文字
    "LayerUtility: ImageReelComposit": "reel_1",
    "LayerUtility: ImageReelCompose": "reel_1",
}

#: 类名 + 字段名替换。值 = (核心等价类, {旧字段: 新字段}, {写进新节点的默认值})
REMAP: dict[str, tuple[str, dict[str, str], dict[str, Any]]] = {
    "CR Prompt Text": ("PrimitiveStringMultiline", {"prompt": "value"}, {}),
    "CR Text": ("PrimitiveStringMultiline", {"text": "value"}, {}),
    "JjkText": ("PrimitiveStringMultiline", {"text": "value"}, {}),
    "Text": ("PrimitiveStringMultiline", {"text": "value"}, {}),
    "DF_Integer": ("PrimitiveInt", {"Value": "value"}, {}),
    "DF_Int_to_Float": ("PrimitiveFloat", {"Value": "value"}, {}),
    # 「在 σ 表中间补几步」核心节点同义，只是字段名不同
    "H3SigmaRefiner": ("ExtendIntermediateSigmas",
                       {"sigmas": "sigmas", "extra_steps": "steps",
                        "start_at_sigma": "start_at_sigma", "end_at_sigma": "end_at_sigma",
                        "spacing": "spacing"}, {}),
}

#: 一个节点变成一小段链（或一次带语义的折叠）。值 = 下面 CHAIN_FUNCS 里的函数名。
CHAIN: dict[str, str] = {
    "VHS_LoadVideo": "load_video",
    "VideoConcatenate": "video_concat",
    "solarL_SaveImagesToZip": "save_images",
    "AudioCrop": "audio_crop",
    "ImpactSwitch": "impact_switch",
    "CR Text Concatenate": "text_concat",
    "ImageResize+": "image_resize",
    "LayerUtility: ImageReel": "image_reel",
    "RHMiniMaxH3ModelLoader": "rh_model",
    "RHMiniMaxH3TextEncoderLoader": "rh_clip",
    "RHMiniMaxH3VAELoader": "rh_vae",
    "RHMiniMaxH3RefGen": "rh_refgen",
}

#: 本机装不到 / 还没装的包，报错时直接告诉用户去哪找
PACK_HINTS: dict[str, str] = {
    "IndexTTS2Run": "ComfyUI_IndexTTS（billwuhao）+ IndexTTS-2 权重",
    "FB_Qwen3TTSVoiceClone": "ComfyUI-Qwen-TTS（flybirdxx）+ Qwen3-TTS 权重",
    "Apply Whisper": "ComfyUI-Whisper（yuvraj108c）+ whisper 模型",
    "ImpactSwitch": "ComfyUI-Impact-Pack（Dr.Lt.Data）",
    "MinimaxH3LatentUpscaler3D": "Comfyui_Minimax_h3_latent_Upscaler（LBH-123-AI）+ models/latent_upscale_models",
    "VHS_LoadVideo": "ComfyUI-VideoHelperSuite（Kosinkadink）",
}

#: 外部节点的输入/输出形状桩。UI→API 转换要靠 object_info 判断「哪些值是控件、哪些是连线」，
#: 本机没有这些类就查不到 —— 没有桩，UI 版工作流连转换这一步都过不去（加速版就是这么卡的）。
#: 只声明转换需要的形状，不声明运行行为。
SHIM: dict[str, dict[str, Any]] = {
    "CR Prompt Text": {"input": {"required": {"prompt": ["STRING", {"multiline": True}]}},
                       "output": ["STRING"], "output_name": ["STRING"]},
    "JjkText": {"input": {"required": {"text": ["STRING", {"multiline": True}]}},
                "output": ["STRING"], "output_name": ["STRING"]},
    "Text": {"input": {"required": {"text": ["STRING", {"multiline": True}]}},
             "output": ["STRING"], "output_name": ["text"]},
    "DF_Integer": {"input": {"required": {"Value": ["INT", {"default": 0}]}},
                   "output": ["INT"], "output_name": ["INT"]},
    "DF_Int_to_Float": {"input": {"required": {"Value": ["FLOAT", {"default": 0}]}},
                        "output": ["FLOAT"], "output_name": ["FLOAT"]},
    "H3SigmaRefiner": {"input": {"required": {"sigmas": ["SIGMAS"], "extra_steps": ["INT", {"default": 1}],
                                              "start_at_sigma": ["FLOAT", {"default": 0.7}],
                                              "end_at_sigma": ["FLOAT", {"default": 0.0}],
                                              "spacing": (["linear", "cosine", "beta", "karr"], {})}},
                       "output": ["SIGMAS"], "output_name": ["SIGMAS"]},
    "AudioCrop": {"input": {"required": {"audio": ["AUDIO"], "start_time": ["STRING"],
                                         "end_time": ["STRING"]}},
                  "output": ["AUDIO"], "output_name": ["AUDIO"]},
    "VHS_LoadVideo": {"input": {"required": {"video": ["STRING"], "force_rate": ["INT", {"default": 0}],
                                             "custom_width": ["INT", {"default": 0}],
                                             "custom_height": ["INT", {"default": 0}],
                                             "frame_load_cap": ["INT", {"default": 0}],
                                             "skip_first_frames": ["INT", {"default": 0}],
                                             "select_every_nth": ["INT", {"default": 1}]}},
                      "output": ["IMAGE", "MASK", "AUDIO", "INT", "FLOAT"],
                      "output_name": ["IMAGE", "MASK", "Audio", "frame_count", "audio_fps"]},
    "VideoConcatenate": {"input": {"optional": {"video_1": ["VIDEO"], "video_2": ["VIDEO"],
                                                "video_3": ["VIDEO"], "video_4": ["VIDEO"]}},
                         "output": ["VIDEO"], "output_name": ["video"]},
    "CR Text": {"input": {"required": {"text": ["STRING", {"multiline": True}]}},
                "output": ["STRING"], "output_name": ["text"]},
    # 画布导出里 text1/text2 是连线插座（只有 separator 占控件位），声明成 "*" 才不会被
    # 当成控件 —— 报成「widgets_values 有 1 个值但控件序列是 3 个」就是这个原因。
    "CR Text Concatenate": {"input": {"required": {"separator": ["STRING", {"default": ","}]},
                                      "optional": {"text1": ["*", {}], "text2": ["*", {}]}},
                            "output": ["STRING"], "output_name": ["text"]},
    # rgthree 的「编组开关」只是个开关面板，没有任何数据流；对照器同理（它的控件是一坨
    # 预览元数据，不是参数）。桩必须给出来，否则画布版连转换都进不去。
    "Fast Groups Bypasser (rgthree)": {"input": {}, "output": [], "output_name": []},
    # 本机装不到的 VLM 描述节点：桩只给形状，值一律由降级规则留空（不猜它的行为）
    "Qwen3_VQA_Plus": {"input": {"required": {"text": ["STRING", {"multiline": True}],
                                              "text2": ["STRING", {"multiline": True}],
                                              "model": (["Qwen3-VL-8B-Thinking"], {}),
                                              "quantization": (["none"], {}),
                                              "keep_model_loaded": ["BOOLEAN", {"default": False}],
                                              "temperature": ["FLOAT", {"default": 0.7}],
                                              "max_new_tokens": ["INT", {"default": 2048}],
                                              "min_pixels": ["INT", {"default": 200704}],
                                              "max_pixels": ["INT", {"default": 1003520}],
                                              "seed": ["INT", {"default": 0}],
                                              "attention": (["eager"], {})},
                                 "optional": {"image": ["IMAGE"]}},
                       "output": ["STRING"], "output_name": ["text"]},
    "Image Comparer (rgthree)": {"input": {"optional": {"image_a": ["IMAGE"], "image_b": ["IMAGE"]}},
                                 "output": [], "output_name": []},
    "ShowText|pysssss": {"input": {"optional": {"text": ["*", {}], "images": ["*", {}]}},
                         "output": ["STRING"], "output_name": ["text"]},
    # 控件顺序照 comfyui-image-resize-plus 的 INPUT_TYPES：width/height/method/condition/multiple_of
    # 控件顺序按本机导出实测：[width, height, interpolation, method, condition, multiple_of]
    "ImageResize+": {"input": {"required": {"image": ["IMAGE"], "width": ["INT", {"default": 1024}],
                                            "height": ["INT", {"default": 1024}],
                                            "interpolation": (["nearest-exact", "bilinear", "area",
                                                               "bicubic", "lanczos"], {}),
                                            "method": (["keep proportion", "stretch", "force nearest"], {}),
                                            "condition": (["disabled", "downscale if bigger", "upscale if smaller",
                                                           "resize if different"], {}),
                                            "multiple_of": ["INT", {"default": 1}]}},
                     "output": ["IMAGE"], "output_name": ["IMAGE"]},
    "Image Comparer (rgthree)": {"input": {"optional": {"image_a": ["IMAGE"], "image_b": ["IMAGE"]}},
                                 "output": [], "output_name": []},
    "LayerUtility: ImageReel": {"input": {"optional": {f"image{i}": ["IMAGE"] for i in range(1, 5)}},
                                "output": ["IMAGE"], "output_name": ["IMAGE"]},
    "LayerUtility: ImageReelComposit": {"input": {"required": {"reel_1": ["IMAGE"]}},
                                        "output": ["IMAGE"], "output_name": ["IMAGE"]},
    "solarL_SaveImagesToZip": {"input": {"required": {"zip": ["*"]}, "optional": {"zip_filename": ["STRING"]}},
                               "output": ["*"], "output_name": ["*"]},
    "Anything Everywhere": {"input": {"optional": {"anything": ["*"]}},
                            "output": ["*"], "output_name": ["*"]},
    "Anything Everywhere3": {"input": {"optional": {"anything": ["*"], "anything2": ["*"], "anything3": ["*"]}},
                             "output": ["*", "*", "*"], "output_name": ["*", "*", "*"]},
    "ModelPreviewOverrideKJ": {"input": {"required": {"model": ["MODEL"]},
                                         "optional": {"max_resolution": ["INT", {"default": 1024}],
                                                      "jpeg_quality": ["INT", {"default": 80}],
                                                      "suppress_default_preview": ["BOOLEAN", {"default": True}],
                                                      "preview_frames": ["INT", {"default": 0}],
                                                      "preview_fps": ["FLOAT", {"default": 12}],
                                                      "tiny_vae": ["VAE"]}},
                               "output": ["MODEL"], "output_name": ["model"]},
    "MiniMaxLowVRAMAttention": {"input": {"required": {"model": ["MODEL"]},
                                          "optional": {"head_chunks": ["INT", {"default": 4}]}},
                                "output": ["MODEL"], "output_name": ["model"]},
    "MiniMaxH3MemoryEfficientSageAttentionPatch": {"input": {"required": {"model": ["MODEL"]}},
                                                   "output": ["MODEL"], "output_name": ["MODEL"]},
    "PathchSageAttentionKJ": {"input": {"required": {"model": ["MODEL"],
                                                      "sage_attention": (["auto", "enabled", "disabled"], {}),
                                                      "allow_compile": ["BOOLEAN", {"default": False}]}},
                              "output": ["MODEL"], "output_name": ["model"]},
    "easy cleanGpuUsed": {"input": {"required": {"anything": ["*"]}},
                          "output": ["*"], "output_name": ["*"]},
    "MinimaxH3LatentUpscaler3D": {"input": {"required": {"latent": ["LATENT"], "model_name": ["COMBO"],
                                                         "mode": ["COMBO"], "align": ["INT", {"default": 32}],
                                                         "device": ["COMBO"], "precision": ["COMBO"],
                                                         "enable_chunking": ["BOOLEAN", {"default": True}]}},
                                  "output": ["LATENT"], "output_name": ["latent"]},
    "ShowAnything|Mie": {"input": {"required": {"anything": ["*"]}}, "output": ["*"], "output_name": ["*"]},
    # Impact 的「任意开关」：一个 select 控件在 input1..N 里挑一条。UI 导出里控件值就是
    # widgets_values 的前两项（选择序号 + 是否按值匹配），剩下的 inputN 全是连线槽。
    "ImpactSwitch": {"input": {"required": {"select": ["INT", {"default": 1}],
                                            "sel_mode": ["BOOLEAN", {"default": True}]},
                               "optional": {f"input{i}": ["*", {}] for i in range(1, 6)}},
                     "output": ["*", "INT", "INT"], "output_name": ["OUTPUT", "selected_index", "selected_val_name"]},
    "忽略多组孤海": {"input": {"optional": {"a": ["*"], "b": ["*"], "c": ["*"]}},
                     "output": [], "output_name": []},
}


# ───────────────────────── 链式替换实现 ─────────────────────────


def _seconds(val: Any) -> float:
    """「0:10」/「1:05」/ 裸秒数 → 浮点秒。"""
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val or "").strip()
    if not s:
        return 0.0
    if ":" in s:
        parts = s.split(":")
        try:
            nums = [float(p) for p in parts]
        except ValueError:
            return 0.0
        total = 0.0
        for p in nums:
            total = total * 60 + p
        return total
    m = re.match(r"^[\d.]+", s)
    return float(m.group()) if m else 0.0


def _chain_load_video(graph: dict[str, Any], nid: str, nd: dict[str, Any], ctx: Ctx) -> None:
    """VHS_LoadVideo（帧 IMAGE + 掩码 + 音轨）→ LoadVideo + GetVideoComponents。

    H3 的 ref_videos 要的是帧序列（IMAGE），VHS 槽 0 正好给这个；音轨在 VHS 槽 2，
    对应 GetVideoComponents 的 AUDIO（槽 1）。槽号对不上就报 gap，不做「大概是这样」的猜测。
    """
    src = nd.get("inputs") or {}
    file_val = src.get("video") or src.get("path") or ""
    lv = _add(graph, "LoadVideo", {"file": file_val}, "参考视频")
    gv = _add(graph, "GetVideoComponents", {"video": [lv, 0]}, "拆视频")
    mapping = {0: [gv, 0], 2: [gv, 1]}
    bad = sorted({s for _, _, s in consumers_of(graph, nid) if s not in mapping})
    if bad:
        ctx.gaps.append(Gap(nid, "VHS_LoadVideo", f"下游还用了第 {bad} 个输出，本机 LoadVideo+GetVideoComponents 给不了"))
    for cnid, fname, sslot in consumers_of(graph, nid):
        if sslot in mapping:
            graph[cnid]["inputs"][fname] = list(mapping[sslot])
    del graph[nid]
    ctx.changes.append(Change(nid, "展开", "VHS_LoadVideo", "LoadVideo+GetVideoComponents",
                              f"{file_val or '(待填素材)'}；帧→槽0、音轨→槽1"))


def _chain_video_concat(graph: dict[str, Any], nid: str, nd: dict[str, Any], ctx: Ctx) -> None:
    """VideoConcatenate(video_1..N) → ConcatenateVideo（autogrow 组）。

    组内键名是从 object_info 问出来的 prefix，不硬写 video_0 —— 这个 prefix
    没有下划线结尾，写死就会「节点认到了、素材全丢」。
    """
    src = nd.get("inputs") or {}
    items = [(k, v) for k, v in src.items() if re.fullmatch(r"video_?\d+", str(k)) and is_link(v)]
    items.sort(key=lambda kv: int(re.sub(r"\D", "", kv[0]) or 0))
    prefix = group_key(ctx.object_info, "ConcatenateVideo", "videos")
    if not items or prefix is None:
        ctx.gaps.append(Gap(nid, "VideoConcatenate", "拼不出 ConcatenateVideo 的素材列表（没有 video_N 连线或问不到前缀）"))
        return
    inputs: dict[str, Any] = {}
    for idx, (_, val) in enumerate(items):
        inputs[f"videos.{prefix}{idx}"] = list(val)
    inputs["codec"] = "auto"
    graph[nid] = {"class_type": "ConcatenateVideo", "inputs": inputs,
                  "_meta": {"title": (nd.get("_meta") or {}).get("title") or "拼接视频"}}
    ctx.changes.append(Change(nid, "替换", "VideoConcatenate", "ConcatenateVideo",
                              f"{len(items)} 段视频接进 videos.{prefix}N"))


def _chain_save_images(graph: dict[str, Any], nid: str, nd: dict[str, Any], ctx: Ctx) -> None:
    """solarL_SaveImagesToZip → SaveImage。喂进来的不是 IMAGE 就整节点删掉并说明。"""
    src = nd.get("inputs") or {}
    ref = src.get("zip")
    if not is_link(ref):
        del graph[nid]
        ctx.changes.append(Change(nid, "删除", "solarL_SaveImagesToZip", "", "没有连线输入，是个空归档节点"))
        return
    t = slot_type(graph, ctx.object_info, ref)
    if t != "IMAGE":
        del graph[nid]
        ctx.changes.append(Change(nid, "删除", "solarL_SaveImagesToZip", "",
                                  f"上游给的是 {t or '未知类型'}，不是 IMAGE，落不了盘（这条链的成片另有出口）"))
        return
    graph[nid] = {"class_type": "SaveImage",
                  "inputs": {"images": list(ref), "filename_prefix": str(src.get("zip_filename") or "h3/export")},
                  "_meta": {"title": "导出参考帧"}}
    ctx.changes.append(Change(nid, "替换", "solarL_SaveImagesToZip", "SaveImage", "图片打包改成逐帧保存"))


def _chain_rh_model(graph: dict[str, Any], nid: str, nd: dict[str, Any], ctx: Ctx) -> None:
    src = nd.get("inputs") or {}
    unet = _add(graph, "UNETLoader", {"unet_name": src.get("transformer_path") or src.get("model_path") or "",
                                      "weight_dtype": "default"}, "H3 DiT")
    out: list[Any] = [unet, 0]
    adapter = src.get("adapter") or ""
    if adapter:
        lora = _add(graph, "LoraLoaderModelOnly",
                    {"model": out, "lora_name": adapter, "strength_model": float(src.get("lora_strength") or 1.0)},
                    "H3 加速 LoRA")
        out = [lora, 0]
    rewire(graph, nid, 0, out)
    del graph[nid]
    ctx.changes.append(Change(nid, "展开", "RHMiniMaxH3ModelLoader", "UNETLoader(+LoraLoaderModelOnly)",
                              f"transformer={src.get('transformer_path')} adapter={adapter or '（无）'}"))


def _chain_rh_clip(graph: dict[str, Any], nid: str, nd: dict[str, Any], ctx: Ctx) -> None:
    src = nd.get("inputs") or {}
    clip = _add(graph, "CLIPLoader", {"clip_name": src.get("text_encoder_path") or "",
                                      "type": "minimax", "device": "default"}, "H3 文本编码器")
    rewire(graph, nid, 0, [clip, 0])
    del graph[nid]
    ctx.changes.append(Change(nid, "展开", "RHMiniMaxH3TextEncoderLoader", "CLIPLoader(type=minimax)",
                              str(src.get("text_encoder_path"))))


def _chain_rh_vae(graph: dict[str, Any], nid: str, nd: dict[str, Any], ctx: Ctx) -> None:
    """RH 把视频/音频两个 VAE 打包成一个输出，core 是两个 VAELoader。

    打包结果记在 ctx.vae_pairs 里给 RefGen 重建用（不塞进图里 —— 图里躺一个假节点，
    提交时 ComfyUI 会当成未知 class_type 直接报错）。
    下游里「自己也要被展开」的节点（RefGen）不改写：它要的是这一对，改写了就找不到
    原始打包节点 id，重建时接不上（本机实测踩过：VAE 先被接成了视频 VAE，四段全报接不上）。
    """
    src = nd.get("inputs") or {}
    vv = _add(graph, "VAELoader", {"vae_name": src.get("video_vae_path") or ""}, "视频 VAE")
    av = _add(graph, "VAELoader", {"vae_name": src.get("audio_vae_path") or ""}, "音频 VAE")
    ctx.vae_pairs[str(nid)] = ([vv, 0], [av, 0])
    bad = sorted({s for c, _, s in consumers_of(graph, nid)
                  if s not in (0, 1) and graph.get(c, {}).get("class_type") not in CHAIN})
    if bad:
        ctx.gaps.append(Gap(nid, "RHMiniMaxH3VAELoader", f"下游还用了第 {bad} 个输出"))
    for cnid, fname, sslot in consumers_of(graph, nid):
        if graph.get(cnid, {}).get("class_type") in CHAIN:
            continue
        graph[cnid]["inputs"][fname] = [vv, 0] if sslot == 0 else ([av, 0] if sslot == 1 else [vv, 0])
    del graph[nid]
    ctx.changes.append(Change(nid, "展开", "RHMiniMaxH3VAELoader", "VAELoader×2",
                              f"video={src.get('video_vae_path')} audio={src.get('audio_vae_path')}"))


def _chain_rh_refgen(graph: dict[str, Any], nid: str, nd: dict[str, Any], ctx: Ctx) -> None:
    """RHMiniMaxH3RefGen → core 的参考生视频采样链（与 builtin_graphs.h3_video_graph 同构）。

    参数一一对得上：duration_seconds→length（17k+5 吸附）、video_shift/audio_shift→
    MiniMaxH3SigmaShift、sampler_mode→KSamplerSelect、images/videos/audios 三组素材→
    ref_images / ref_videos / ref_audios 三个 autogrow。RH 节点的两个输出（画面帧、音轨）
    重建后分别由 VAEDecode / VAEDecodeAudio 承担。
    """
    src = dict(nd.get("inputs") or {})
    link = lambda name: list(src[name]) if is_link(src.get(name)) else None  # noqa: E731
    clip, model = link("h3_text_encoder"), link("h3_model")
    bundle = link("h3_vae_bundle")
    pair = ctx.vae_pairs.get(str(bundle[0])) if bundle else None
    if clip is None or model is None or pair is None:
        ctx.gaps.append(Gap(nid, RH_REFGEN, "模型/文本编码器/VAE 三个连线接不上 core 加载器，这段没法重建"))
        return
    vae, audio_vae = pair

    seconds = _seconds(src.get("duration_seconds") or 5)
    cond: dict[str, Any] = {
        "clip": clip, "prompt": src.get("prompt") if is_link(src.get("prompt")) else (src.get("prompt") or ""),
        "width": int(src.get("width") or 832), "height": int(src.get("height") or 480),
        "length": h3_length(seconds), "ref_image_size": src.get("ref_image_size") or "match",
        "vae": vae, "audio_vae": audio_vae,
    }
    counts = {}
    for group, target in (("images", "ref_images"), ("videos", "ref_videos"), ("audios", "ref_audios")):
        prefix = group_key(ctx.object_info, "MiniMaxH3ReferenceToVideo", target) or f"{target[:-1]}_"
        for idx, (_, val) in enumerate(autogrow_items(src, group)):
            if is_link(val):
                cond[f"{target}.{prefix}{idx}"] = list(val)
        counts[target] = len([k for k in cond if k.startswith(target + ".")])
    h3 = _add(graph, "MiniMaxH3ReferenceToVideo", cond, "H3 参考生视频")

    shift = _add(graph, "MiniMaxH3SigmaShift",
                 {"model": model, "shift_video": float(src.get("video_shift") or 12),
                  "shift_audio": float(src.get("audio_shift") or 3)}, "Sigma Shift")
    sampler = _add(graph, "KSamplerSelect", {"sampler_name": src.get("sampler_mode") or "res_multistep"}, "采样器")
    sched = _add(graph, "BasicScheduler", {"model": [shift, 0], "scheduler": "simple",
                                           "steps": int(src.get("steps") or 4), "denoise": 1.0}, "σ 表")
    noise = _add(graph, "RandomNoise", {"noise_seed": int(src.get("seed") or 0)}, "噪声种子")
    guider = _add(graph, "BasicGuider", {"model": [shift, 0], "conditioning": [h3, 0]}, "Guider")
    sampled = _add(graph, "SamplerCustomAdvanced",
                   {"noise": [noise, 0], "guider": [guider, 0], "sampler": [sampler, 0],
                    "sigmas": [sched, 0], "latent_image": [h3, 1]}, "采样")
    dec_v = _add(graph, "VAEDecode", {"samples": [sampled, 0], "vae": vae}, "解码画面")
    dec_a = _add(graph, "VAEDecodeAudio", {"samples": [sampled, 0], "vae": audio_vae}, "解码音频")

    rewire(graph, nid, 0, [dec_v, 0])
    rewire(graph, nid, 1, [dec_a, 0])
    left = consumers_of(graph, nid)
    if left:
        ctx.gaps.append(Gap(nid, RH_REFGEN, f"重建后还有接不住的下游：{[(c, f, s) for c, f, s in left]}"))
        return
    del graph[nid]
    ctx.changes.append(Change(nid, "展开", RH_REFGEN, "MiniMaxH3ReferenceToVideo 采样链",
                              f"{seconds}s→{cond['length']}帧、{cond['width']}×{cond['height']}、"
                              f"{counts['ref_images']}图/{counts['ref_videos']}视频/{counts['ref_audios']}音频"))


def _chain_audio_crop(graph: dict[str, Any], nid: str, nd: dict[str, Any], ctx: Ctx) -> None:
    """AudioCrop(start_time="0:00", end_time="0:10") → TrimAudioDuration(秒)。

    外部包用的是「分:秒」串，core 的 TrimAudioDuration 要 start_index/duration 两个浮点秒。
    直接改名会把 "0:10" 塞进 float 控件 —— ComfyUI 会当成 0，参考音频被截成空。
    """
    src = nd.get("inputs") or {}
    start = _seconds(src.get("start_time", src.get("start", 0)))
    end = _seconds(src.get("end_time", src.get("end", 0)))
    audio = src.get("audio")
    if not is_link(audio):
        ctx.gaps.append(Gap(nid, "AudioCrop", "audio 不是连线，TrimAudioDuration 接不上上游"))
        return
    dur = round(end - start, 3)
    if dur <= 0:
        ctx.gaps.append(Gap(nid, "AudioCrop", f"起止时间算不出长度（{src.get('start_time')}→{src.get('end_time')}）"))
        return
    graph[nid] = {"class_type": "TrimAudioDuration",
                  "inputs": {"audio": list(audio), "start_index": start, "duration": dur},
                  "_meta": {"title": (nd.get("_meta") or {}).get("title") or "截取音频"}}
    ctx.changes.append(Change(nid, "替换", "AudioCrop", "TrimAudioDuration",
                              f"「{src.get('start_time')}~{src.get('end_time')}」按秒拆成 start_index={start}、duration={dur}"))


def _chain_impact_switch(graph: dict[str, Any], nid: str, nd: dict[str, Any], ctx: Ctx) -> None:
    """Impact 的「任意开关」→ 按导入时的 select 固定走一条分支。

    这类节点在作者的图里是「分辨率档位选择」这种人工开关，本机没装 ImpactPack 就没法
    运行期判断。固定成导入时选中的那一档，并把这件事写进改动说明 —— 想换档位是改这条
    工作流的槽位，不是让程序猜。选中项没连线就报 gap，不替它挑一条。
    """
    src = nd.get("inputs") or {}
    try:
        pick = int(src.get("select") or 0)
    except (TypeError, ValueError):
        pick = 0
    field = f"input{pick}"
    target = src.get(field)
    if not is_link(target):
        ctx.gaps.append(Gap(nid, "ImpactSwitch", f"select={pick} 指向的「{field}」没有连线，本机不知道该走哪条分支"))
        return
    rewire(graph, nid, 0, target)
    del graph[nid]
    ctx.changes.append(Change(nid, "穿透", "ImpactSwitch", "",
                              f"按导入时的 select={pick} 固定走「{field}」，其余分支剪掉"))


def _chain_text_concat(graph: dict[str, Any], nid: str, nd: dict[str, Any], ctx: Ctx) -> None:
    """Comfyroll 的「文本拼接」→ 核心 AddTextPrefix(prefix + texts)。

    AddTextPrefix 没有分隔符这个概念，所以分隔符非空时不硬套：那种情况下拼接结果
    会少掉分隔符，属于改语义，报 gap 让用户自己决定。
    """
    src = nd.get("inputs") or {}
    sep = src.get("separator")
    if isinstance(sep, str) and sep:
        ctx.gaps.append(Gap(nid, "CR Text Concatenate", f"分隔符是 {sep!r}，核心 AddTextPrefix 表达不了，没敢替换"))
        return
    t1, t2 = src.get("text1"), src.get("text2")
    if t1 is None and t2 is None:
        ctx.gaps.append(Gap(nid, "CR Text Concatenate", "两个文本输入都是空的"))
        return
    graph[nid] = {"class_type": "AddTextPrefix",
                  "inputs": {"texts": t2 if t2 is not None else "", "prefix": t1 if t1 is not None else ""},
                  "_meta": {"title": (nd.get("_meta") or {}).get("title") or "拼接文本"}}
    ctx.changes.append(Change(nid, "替换", "CR Text Concatenate", "AddTextPrefix", "text1 当前缀、text2 当正文（分隔符为空）"))


def _chain_image_resize(graph: dict[str, Any], nid: str, nd: dict[str, Any], ctx: Ctx) -> None:
    """ImageResize+ → ImageScaleToMaxDimension：等比缩到不超过 longest 边。

    原节点的 method="keep proportion" + condition="downscale if bigger" 与核心这个节点同义；
    multiple_of（对齐到 16 的倍数）核心没有，丢掉会让 latent 尺寸偶尔不是 16 的倍数，
    所以照实写进改动说明，不当它不存在。
    """
    src = nd.get("inputs") or {}
    image = src.get("image")
    if not is_link(image):
        ctx.gaps.append(Gap(nid, "ImageResize+", "image 不是连线，接不上核心缩放节点"))
        return
    try:
        longest = max(int(src.get("width") or 0), int(src.get("height") or 0)) or 1024
    except (TypeError, ValueError):
        longest = 1024
    interp = str(src.get("interpolation") or "lanczos").lower()
    method = {"lanczos": "lanczos", "bicubic": "bicubic", "bilinear": "bilinear",
              "nearest": "nearest-exact", "nearest-exact": "nearest-exact", "area": "area"}.get(interp, "lanczos")
    keeps = str(src.get("method") or "") == "keep proportion" and str(src.get("condition") or "") == "downscale if bigger"
    graph[nid] = {"class_type": "ImageScaleToMaxDimension",
                  "inputs": {"image": list(image), "upscale_method": method, "largest_size": longest},
                  "_meta": {"title": (nd.get("_meta") or {}).get("title") or "等比缩放"}}
    ctx.changes.append(Change(nid, "替换", "ImageResize+", "ImageScaleToMaxDimension",
                              f"等比缩到最长边 {longest}px（method/condition {'等价' if keeps else '与核心语义不完全一致'}）；"
                              f"multiple_of={src.get('multiple_of')} 这个对齐参数核心没有，已丢弃"))


def _chain_image_reel(graph: dict[str, Any], nid: str, nd: dict[str, Any], ctx: Ctx) -> None:
    """LayerStyle 的 ImageReel（并排贴图 + 标签）→ 核心 BatchImagesNode + ImageGrid。

    保留「几张图并排看」这件事，丢掉边框文字与配色 —— 这条链一般只喂 PreviewImage，
    不影响出片结果，所以不需要用户确认。
    """
    src = nd.get("inputs") or {}
    links = [v for k, v in src.items() if re.fullmatch(r"image\d+", str(k)) and is_link(v)]
    if not links:
        ctx.gaps.append(Gap(nid, "LayerUtility: ImageReel", "没有任何图片输入，换不成核心拼图"))
        return
    prefix = group_key(ctx.object_info, "BatchImagesNode", "images") or "image"
    # autogrow 组在 API 图里是「组名.项名」的展平写法，不是嵌套 dict（本机实测：写成 dict
    # 会以「Required input: images」被校验退回）
    autogrow = {f"images.{prefix}{i}": list(v) for i, v in enumerate(links)}
    batch = _add(graph, "BatchImagesNode", autogrow, "打包画面")
    grid = _add(graph, "ImageGrid", {"images": [batch, 0], "columns": len(links)}, "并排对照")
    rewire(graph, nid, 0, [grid, 0])
    del graph[nid]
    ctx.changes.append(Change(nid, "展开", "LayerUtility: ImageReel", "BatchImagesNode+ImageGrid",
                              f"{len(links)} 张并排，边框标签文字（image1_text 等）丢掉"))


CHAIN_FUNCS: dict[str, Callable[[dict, str, dict, Ctx], None]] = {
    "load_video": _chain_load_video,
    "video_concat": _chain_video_concat,
    "save_images": _chain_save_images,
    "audio_crop": _chain_audio_crop,
    "impact_switch": _chain_impact_switch,
    "text_concat": _chain_text_concat,
    "image_resize": _chain_image_resize,
    "image_reel": _chain_image_reel,
    "rh_model": _chain_rh_model,
    "rh_clip": _chain_rh_clip,
    "rh_vae": _chain_rh_vae,
    "rh_refgen": _chain_rh_refgen,
}

RH_REFGEN = "RHMiniMaxH3RefGen"


# ───────────────────────── 素材面板对 ─────────────────────────

_MEDIA_KIND_TARGET = {
    "picture": ("LoadImage", "image"),
    "image": ("LoadImage", "image"),
    "video": ("LoadVideo", "file"),
    "audio": ("LoadAudio", "audio"),
}


def _flatten_media_loader(graph: dict[str, Any], ctx: Ctx) -> None:
    """MiniMaxH3MediaLoaderFantastic + MiniMaxH3ReferenceSplitter → 一组核心素材加载节点。

    这两个是作者自造的「素材面板」：前者把图/视频/音频混在一个 JSON 状态串里，后者按
    **每类各自计数**拆成 ref_image_0.. / ref_video_0.. / ref_audio_0..。所以序号必须
    按 kind 分桶（本机实测踩过：按面板全局下标接，第 1 张图会被同时接到 ref_video_0 和
    ref_audio_0 上，IMAGE 喂进 AUDIO 口，提交就报 Unexpected input type）。
    拆成核心加载器后，每个素材各自成为一个槽位 —— 前端才可能替换素材。
    media_state 里的文件名是作者机器上的，本机没有，值先留原名，改动说明里写清要重指。
    """
    #: 面板 kind → (目标组名, 组内计数键, 需要的输入类型)
    kind_map = {
        "picture": ("ref_images", "image"),
        "image": ("ref_images", "image"),
        "video": ("ref_videos", "video"),
        "audio": ("ref_audios", "audio"),
    }
    for lid in [n for n, d in graph.items() if d.get("class_type") == "MiniMaxH3MediaLoaderFantastic"]:
        nd = graph.get(lid)
        if not nd:
            continue
        hits = consumers_of(graph, lid)
        splitter = hits[0][0] if hits else None
        if (graph.get(splitter or "") or {}).get("class_type") != "MiniMaxH3ReferenceSplitter":
            ctx.gaps.append(Gap(lid, "MiniMaxH3MediaLoaderFantastic", "找不到配套的 ReferenceSplitter，不知道素材该接到哪"))
            continue
        raw = (nd.get("inputs") or {}).get("media_state")
        try:
            items = json.loads(raw) if isinstance(raw, str) else list(raw or [])
        except json.JSONDecodeError as exc:
            ctx.gaps.append(Gap(lid, "MiniMaxH3MediaLoaderFantastic", f"media_state 不是合法 JSON：{exc.msg}"))
            continue
        # 下游输入全名 → 消费点，形如 136:ref_images.ref_image_2
        want = {(cnid, fname): [] for cnid, fname, _ in consumers_of(graph, splitter)}
        counters = {"ref_images": 0, "ref_videos": 0, "ref_audios": 0}
        made = {"ref_images": 0, "ref_videos": 0, "ref_audios": 0}
        for entry in items:
            kind = str((entry or {}).get("kind") or "picture")
            spec = kind_map.get(kind)
            if not spec:
                ctx.gaps.append(Gap(lid, "MiniMaxH3MediaLoaderFantastic", f"kind={kind} 认不出来，这个素材没接进去"))
                continue
            group, cls_fld = spec
            cls, fld = _MEDIA_KIND_TARGET[kind]
            name = str((entry or {}).get("file") or "").split(" [")[0].split("/")[-1]
            idx = counters[group]
            prefix = group_key(ctx.object_info, "MiniMaxH3ReferenceToVideo", group) or f"{group[:-1]}_"
            node_id = _add(graph, cls, {fld: name}, f"参考素材 {group} 第 {idx + 1} 个")
            made[group] += 1
            if kind == "video":
                # ref_videos 要帧序列（IMAGE 口），LoadVideo 给 VIDEO；音轨另接 ref_video_audios
                gv = _add(graph, "GetVideoComponents", {"video": [node_id, 0]}, "拆视频帧")
                refs = [([gv, 0], f"{group}.{prefix}{idx}"),
                        ([gv, 1], f"ref_video_audios.{group_key(ctx.object_info, 'MiniMaxH3ReferenceToVideo', 'ref_video_audios') or 'ref_video_audio_'}{idx}")]
            else:
                refs = [([node_id, 0], f"{group}.{prefix}{idx}")]
            for target, fname in refs:
                for cnid, k in list(want):
                    if k == fname:
                        graph[cnid]["inputs"][fname] = list(target)
                        want.pop((cnid, k))
                        break
            counters[group] = idx + 1
        # 面板上没被素材填到的位置必须删掉这个输入键：拆完 splitter 就没了，
        # 留着是一条指向不存在节点的连线，提交直接 400。
        for cnid, fname, _ in consumers_of(graph, splitter):
            graph[cnid]["inputs"].pop(fname, None)
        del graph[splitter]
        del graph[lid]
        unmet = len(want)
        ctx.changes.append(Change(lid, "展开", "MediaLoaderFantastic+ReferenceSplitter",
                                  "核心素材节点",
                                  "素材面板拆成 {img}图/{vid}视频/{aud}音频；原文件名来自作者机器，"
                                  "本机要在槽位里重新指定" .format(img=made["ref_images"], vid=made["ref_videos"], aud=made["ref_audios"])
                                  + (f"；还有 {unmet} 个下游素材位空着，已删除" if unmet else "")))


# ───────────────────────── 主入口 ─────────────────────────


def degrade_unresolved(graph: dict[str, Any], ctx: Ctx) -> int:
    """把「本机装不到的节点」降级成能跑的形状，一次一轮，直到没有新的降级。

    只做两件有明确依据的事：

    1. 上游是未解析节点、下游要的又是一段文本（STRING 控件）→ 断掉这条连线并把值留空。
       作者的图常见「VLM 现算一段画面描述喂进提示词」，本机没有那个 VLM 节点，
       但应用这一侧本来就有写提示词的环节（llama.cpp + 导演台），留空 = 交给那条路填，
       比整条工作流判死有用。留空的槽位会作为 prompt 信号暴露出去，不会被当成已填好的值。
    2. 只通往 Preview*（纯预览）的子图里有未解析节点 → 连着预览一起删。
       预览不产出任何要保存的东西，删了不影响成片；留着就是「工作流跑不起来」。
    """
    known = ctx.known
    changed = 0
    for _ in range(6):
        before = changed
        unresolved = {nid for nid, nd in graph.items()
                      if (nd.get("class_type") or "") not in known}
        # ① 断文本连线
        for nid, nd in list(graph.items()):
            ct = nd.get("class_type") or ""
            if ct not in known or nid in unresolved:
                continue
            info = ctx.object_info.get(ct) or {}
            for fname, val in list((nd.get("inputs") or {}).items()):
                if not is_link(val) or str(val[0]) not in unresolved:
                    continue
                declared = None
                base = fname.split(".", 1)[0]
                for sec in ("required", "optional"):
                    spec = (info.get("input") or {}).get(sec, {}).get(base)
                    if isinstance(spec, list) and spec:
                        declared = str(spec[0]).upper()
                        break
                if declared not in (None, "STRING", "*", "ANY", "COMBO"):
                    continue
                if declared in (None, "STRING"):
                    nd["inputs"][fname] = ""
                    ctx.changes.append(Change(nid, "降级", ct, "",
                                              f"输入「{fname}」原本连的是本机装不到的节点 #{val[0]}，"
                              "已留空等前端填（这一格会作为可填槽位显示出来）"))
                    changed += 1
        # ② 删纯预览分支
        sinks = {nid for nid, nd in graph.items() if str(nd.get("class_type") or "").startswith("Save")}
        preview_only = {nid for nid, nd in graph.items() if str(nd.get("class_type") or "").startswith("Preview")}
        keep = _producers(graph, list(sinks)) if sinks else set()
        for pid in list(preview_only):
            if pid in keep:
                continue
            up = _producers(graph, [pid])
            if not (up & unresolved):
                continue
            for nid in up - keep:
                graph.pop(nid, None)
            ctx.changes.append(Change(pid, "删除", str((graph.get(pid) or {}).get("class_type") or "Preview*"), "",
                                      f"纯预览分支里有本机装不到的节点，已连同 {len(up - keep)} 个节点删掉（不影响保存结果）"))
            changed += 1
        if changed == before:
            break
    return changed


def object_info_with_shim(object_info: dict[str, Any]) -> dict[str, Any]:
    """转换阶段用的 object_info：把外部节点的形状桩并进去（不动原字典）。"""
    merged = dict(object_info)
    for k, v in SHIM.items():
        merged.setdefault(k, v)
    return merged


def _coerce_combo(graph: dict[str, Any], nid: str, ctx: Ctx) -> None:
    """把控件值对齐到目标节点候选清单的写法（只差大小写/下划线时自动改），对不上记 gap。

    权重文件名不在这里处理：那是 client.align_graph 的活（它按实例真实目录解析）。
    素材文件名也不在这里处理：导入的工作流里写的是作者机器上的文件，本机必然没有，
    那是「等工作流被使用时由前端重新指一张图」的槽位，不是缺东西。
    """
    nd = graph[nid]
    info = ctx.object_info.get(nd["class_type"]) or {}
    for sec in ("required", "optional"):
        for fname, spec in (info.get("input") or {}).get(sec, {}).items():
            if not (isinstance(spec, list) and spec and isinstance(spec[0], list)):
                continue
            val = nd["inputs"].get(fname)
            if not isinstance(val, str) or not val or val in spec[0]:
                continue
            choices = [c for c in spec[0] if isinstance(c, str)]
            norm = {c.lower().replace("_", "").replace("-", ""): c for c in choices}
            hit = norm.get(val.lower().replace("_", "").replace("-", ""))
            if hit:
                nd["inputs"][fname] = hit
            elif fname in _MODEL_FIELDS or fname in _MEDIA_FIELDS:
                # 权重交给 align_graph；素材留给「待填」，值先原样留着，槽位会暴露出去
                if fname in _MEDIA_FIELDS:
                    ctx.pending_media.append(f"{nid}.{fname}")
                continue
            else:
                ctx.gaps.append(Gap(nid, nd["class_type"],
                                    f"{fname}={val!r} 不在本机候选里（可选：{choices[:5]}）"))
                nd["inputs"].pop(fname, None)


# ───────────────────────── 常量折叠与孤立分支清理 ─────────────────────────

#: 加载器类控件（值是文件/权重名，不该被当成枚举校验失败）
_MODEL_FIELDS = {"unet_name", "clip_name", "vae_name", "lora_name", "model_name", "checkpoint_name",
                 "ckpt_name", "text_encoder_name", "tiny_vae"}
_MEDIA_FIELDS = {"image", "file", "audio", "video", "audio_file", "video_file", "image_path"}

#: 能折成字面量的 Primitive 类 → (控件值字段, 转换函数)
_PRIMITIVE_FOLD = {
    "PrimitiveInt": ("value", int),
    "PrimitiveFloat": ("value", float),
    "PrimitiveStringMultiline": ("value", str),
    "PrimitiveString": ("value", str),
}
#: 接收端是这些控件类型时，字面量才安全（连线值喂给控件会被 ComfyUI 退回）
_FOLDABLE_SLOT = {"INT", "FLOAT", "STRING", "COMBO", "BOOLEAN", "COMFY_DYNAMICCOMBO_V3"}


def _slot_declared(object_info: dict[str, Any], class_type: str, field: str) -> str | None:
    """某个输入声明的类型。autogrow 的展平名（ref_images.ref_image_0）按组名查。"""
    info = object_info.get(class_type) or {}
    name = field.split(".", 1)[0] if "." in field else field
    for sec in ("required", "optional"):
        spec = (info.get("input") or {}).get(sec, {}).get(name)
        if isinstance(spec, list) and spec:
            return str(spec[0]).upper()
    return None


def _fold_primitives(graph: dict[str, Any], ctx: Ctx) -> int:
    """把「只给某个参数供值」的 Primitive 节点折成字面量。

    作者的图习惯把提示词、步数、种子放在文本框/数字框节点里，看着直观，但对导入方是负担：
    值藏在另一个节点里，槽位表就指不到它，前端也没法填。折成字面量之后，H3 节点的 prompt
    直接成为一个可填槽位 —— 这是「按前端任务自动选工作流」能成立的前提之一。
    只在接收端确实是标量控件时折，且要求这条连线没有 autogrow 展平名（那种是素材不是参数）。
    """
    folded = 0
    for nid in [n for n, d in graph.items() if d.get("class_type") in _PRIMITIVE_FOLD]:
        nd = graph[nid]
        fld, cast = _PRIMITIVE_FOLD[nd["class_type"]]
        raw = nd["inputs"].get(fld)
        if not isinstance(raw, (str, int, float)) or isinstance(raw, bool):
            continue
        cons = consumers_of(graph, nid)
        if not cons:
            continue
        hits = [(cnid, fname) for cnid, fname, slot in cons
                if slot == 0 and "." not in fname
                and (_slot_declared(ctx.object_info, graph[cnid]["class_type"], fname) or "") in _FOLDABLE_SLOT]
        if not hits:
            continue
        for cnid, fname in hits:
            try:
                graph[cnid]["inputs"][fname] = cast(raw)
            except (TypeError, ValueError):
                graph[cnid]["inputs"][fname] = raw
            folded += 1
        if not consumers_of(graph, nid):
            del graph[nid]
            ctx.changes.append(Change(nid, "折叠", nd["class_type"], "",
                                      f"把常量 {str(raw)[:60]!r} 直接写进下游参数"))
    return folded


def _prune_unreferenced(graph: dict[str, Any], ctx: Ctx) -> int:
    """剪掉改写后不再被任何人引用、也不产出可见结果的节点。

    典型来源：开关只留一条分支、素材面板剩下的空位。Save*/Preview* 是结果出口，绝不动。
    """
    removed = 0
    for nid in list(graph.keys()):
        nd = graph[nid]
        ct = nd.get("class_type") or ""
        if ct.startswith(("Save", "Preview")) or ct in DOC_CLASSES:
            continue
        if consumers_of(graph, nid):
            continue
        if all(not is_link(v) and not isinstance(v, dict) for v in (nd.get("inputs") or {}).values()):
            del graph[nid]
            removed += 1
            ctx.changes.append(Change(nid, "删除", ct, "", "改写后没有任何下游，是个死分支"))
    return removed


class Adapted(NamedTuple):
    """一次改写的全部产出。pending_media 单列出来：那不是「缺东西」，是等工作流被使用时
    由前端重新指素材的槽位（作者机器上的文件名本机永远不会有）。"""

    graph: dict[str, Any]
    changes: list[Change]
    gaps: list[Gap]
    pending_media: list[str]


def _fill_required_defaults(graph: dict[str, Any], ctx: Ctx) -> int:
    """必填控件在图里没有值时，按节点自己声明的 default 补上。

    展开链是我们自己造节点的（ImageGrid 就是例子：只给了 columns，漏了 cell_width/
    cell_height/padding），补不上就是提交时一句「Required input is missing」，
    看不出是哪个参数。补的是节点定义里的默认值 —— 和前端加载工作流时的行为一致，
    不是替用户编一个参数，所以逐条记进改动清单。
    """
    filled = 0
    for nid, nd in list(graph.items()):
        info = ctx.object_info.get(nd.get("class_type") or "") or {}
        inputs = nd.get("inputs") or {}
        for fname, spec in ((info.get("input") or {}).get("required") or {}).items():
            if fname in inputs or "." in fname:
                continue          # 展平的 autogrow 组不在这管
            if not (isinstance(spec, list) and len(spec) > 1 and isinstance(spec[1], dict)):
                continue
            if "default" not in spec[1]:
                continue
            if any(is_link(v) and str(k).startswith(fname + ".") for k, v in inputs.items()):
                continue          # 组已经通过展平名接了线
            inputs[fname] = spec[1]["default"]
            ctx.changes.append(Change(nid, "补默认", nd["class_type"], nd["class_type"],
                                      f"{fname} 图里没有值，按节点默认值 {spec[1]['default']!r} 补齐"))
            filled += 1
    return filled


def adapt_graph(graph: dict[str, Any], object_info: dict[str, Any], *, rounds: int = 8) -> Adapted:
    """返回 (改写后的图, 改动清单, 本机仍缺的东西, 待重指素材的控件)。不改调用方的字典。

    规则会连锁（穿透一个包装节点后，它的上游可能又是一个包装节点），所以按轮次跑到没有新增改动。
    """
    out: dict[str, Any] = {
        k: {"class_type": v.get("class_type"), "inputs": dict(v.get("inputs") or {}),
            **({"_meta": dict(v["_meta"])} if isinstance(v.get("_meta"), dict) else {})}
        for k, v in graph.items()
    }
    ctx = Ctx(object_info=object_info)
    _flatten_media_loader(out, ctx)

    for _ in range(rounds):
        before = len(ctx.changes) + len(ctx.gaps)
        for nid in list(out.keys()):
            nd = out.get(nid)
            if not nd:
                continue
            ct = nd.get("class_type") or ""
            if ct in ctx.known:
                continue

            if ct in DOC_CLASSES:
                if consumers_of(out, nid):
                    ctx.gaps.append(Gap(nid, ct, "注释/调试节点却有下游，删不掉"))
                    continue
                del out[nid]
                ctx.changes.append(Change(nid, "删除", ct, "", "注释/调试节点，无人引用"))
                continue

            if ct in CHAIN:
                if nid in ctx.done:
                    continue
                # 展开可能要等它的上游先被展开（RH 的 VAE 打包节点在 RefGen 之前处理），
                # 所以只有「节点真的被换掉了」才算做完；没换成就下一轮再试，
                # 重复报的 gap 由出口处按 (节点, 原因) 去重。
                CHAIN_FUNCS[CHAIN[ct]](out, nid, nd, ctx)
                if (out.get(nid) or {}).get("class_type") != ct:
                    ctx.done.add(nid)
                continue

            if ct in PASSTHROUGH:
                val = nd["inputs"].get(PASSTHROUGH[ct])
                if is_link(val):
                    rewire(out, nid, 0, val)
                    del out[nid]
                    ctx.changes.append(Change(nid, "穿透", ct, "",
                                              f"只转发「{PASSTHROUGH[ct]}」，下游改接上游"
                                              + ("（本机因此少一级上采样/预览）" if ct == "MinimaxH3LatentUpscaler3D" else "")))
                    continue
                if not consumers_of(out, nid):
                    del out[nid]
                    ctx.changes.append(Change(nid, "删除", ct, "", "包装节点既没连线输入也没下游"))
                    continue
                ctx.gaps.append(Gap(nid, ct, f"「{PASSTHROUGH[ct]}」不是连线，无法穿透"))
                continue

            if ct in REMAP:
                new_cls, fields, defaults = REMAP[ct]
                moved: dict[str, Any] = {}
                renamed = []
                for old, new in fields.items():
                    if old in nd["inputs"]:
                        moved[new] = nd["inputs"][old]
                        renamed.append(f"{old}→{new}")
                for k, v in nd["inputs"].items():
                    if k not in fields and "." not in k:
                        moved.setdefault(k, v)
                moved.update({k: v for k, v in defaults.items() if k not in moved})
                nd["class_type"] = new_cls
                nd["inputs"] = moved
                nd.setdefault("_meta", {})["title"] = f"{(nd.get('_meta') or {}).get('title') or ct}（本机等价）"
                ctx.changes.append(Change(nid, "替换", ct, new_cls, "、".join(renamed) or "同名字段直传"))
                _coerce_combo(out, nid, ctx)
                continue

        # 常量折叠：规则把文本框/数字框换成核心 Primitive 之后，还要把它们折进下游参数，
        # 提示词才会变成 H3 节点上一个可直接填的槽位（自动选工作流填槽就靠这个）。
        _fold_primitives(out, ctx)
        if len(ctx.changes) + len(ctx.gaps) == before:
            break

    # 装不到的节点先降级（断文本连线 / 删纯预览分支），再清死分支
    degrade_unresolved(out, ctx)
    for nid in [n for n, d in out.items() if (d.get("class_type") or "") not in ctx.known]:
        nd = out.get(nid)
        if not nd or consumers_of(out, nid):
            continue
        out.pop(nid, None)
        ctx.changes.append(Change(nid, "删除", nd.get("class_type") or "", "",
                                  "降级后没有任何下游引用，删掉（它要的东西由槽位/其它节点承担）"))
    _prune_unreferenced(out, ctx)
    _fill_required_defaults(out, ctx)
    for nid, nd in out.items():
        ct = nd.get("class_type") or ""
        if ct not in ctx.known:
            ctx.gaps.append(Gap(nid, ct, "本机没有这个节点类", PACK_HINTS.get(ct, "")))
        else:
            _coerce_combo(out, nid, ctx)
    # 多轮重试会把同一个 gap 报好几遍，按 (节点, 原因) 收一遍
    seen: set[tuple[str, str]] = set()
    uniq = []
    for g in ctx.gaps:
        key = (g.node, g.reason)
        if key not in seen:
            seen.add(key)
            uniq.append(g)
    return Adapted(out, ctx.changes, uniq, sorted(set(ctx.pending_media)))


def missing_classes(graph: dict[str, Any], object_info: dict[str, Any]) -> dict[str, list[str]]:
    """按类名归并「还缺哪些节点、分别在哪几个节点上」。"""
    known = set(object_info)
    out: dict[str, list[str]] = {}
    for nid, nd in graph.items():
        ct = nd.get("class_type") or ""
        if ct and ct not in known:
            out.setdefault(ct, []).append(nid)
    return out


#: 值必须是「这台实例上存在的权重」的那些控件
_WEIGHT_FIELDS = {"unet_name", "clip_name", "vae_name", "lora_name", "model_name",
                  "checkpoint_name", "ckpt_name", "text_encoder", "audio_encoder_name"}


def unavailable_weights(graph: dict[str, Any], object_info: dict[str, Any]) -> dict[str, list[str]]:
    """节点 → 它引用但这台实例给不出的权重名。

    判据只用 /object_info 的候选清单（align_graph 已经尽力换过同族精度档），
    不去猜磁盘目录 —— 目录名有两个扫描路径，猜必错。
    """
    out: dict[str, list[str]] = {}
    for nid, nd in graph.items():
        ct = nd.get("class_type") or ""
        info = object_info.get(ct) or {}
        for fname, value in (nd.get("inputs") or {}).items():
            if fname not in _WEIGHT_FIELDS or not isinstance(value, str) or not value:
                continue
            for sec in ("required", "optional"):
                spec = (info.get("input") or {}).get(sec, {}).get(fname)
                if isinstance(spec, list) and spec and isinstance(spec[0], list):
                    choices = [c for c in spec[0] if isinstance(c, str)]
                    if choices and value not in choices and value != "pixel_space":
                        out.setdefault(nid, []).append(f"{fname}={value}")
                    break
    return out


def _producers(graph: dict[str, Any], roots: list[str]) -> set[str]:
    """从若干节点往上找出它们的全部上游依赖。"""
    keep: set[str] = set()
    stack = [str(r) for r in roots]
    while stack:
        nid = stack.pop()
        if nid in keep or nid not in graph:
            continue
        keep.add(nid)
        for val in (graph[nid].get("inputs") or {}).values():
            refs = val.values() if isinstance(val, dict) else [val]
            for ref in refs:
                if is_link(ref):
                    stack.append(str(ref[0]))
    return keep


#: LoRA 类节点：值对不上时可以整级摘掉（底模照样出片，只是少一加快照加速器）
_ADAPTER_CLASSES = {"LoraLoaderModelOnly", "LoraLoader", "QwenImageLoraLoader", "LoraLoaderAdvanced"}


def unwrap_unavailable_adapters(graph: dict[str, Any], object_info: dict[str, Any],
                                bad: dict[str, list[str]]) -> tuple[dict[str, Any], list[Change]]:
    """把「本机没有这个 LoRA」的适配器节点摘掉，让模型线直接穿过。

    只摘适配器，不动底模：缺一个 Turbo 适配器，退成底模原采样是「慢一点、画质一档」的差别；
    缺底模本身就是另一回事，那要报缺，不许拿别的模型凑。
    摘掉的级别必须写进改动清单 —— 作者图里写着 4 步 Turbo，摘掉后跑 25 步才对得上画质。
    """
    changes: list[Change] = []
    for nid in [n for n, d in graph.items() if str(d.get("class_type") or "") in _ADAPTER_CLASSES]:
        if nid not in bad:
            continue
        nd = graph[nid]
        src = nd["inputs"].get("model") or nd["inputs"].get("input") or nd["inputs"].get("input1")
        if not is_link(src):
            continue
        for cnid, fname, _ in consumers_of(graph, nid):
            graph[cnid]["inputs"][fname] = list(src)
        lora = str(nd["inputs"].get("lora_name") or "")
        del graph[nid]
        changes.append(Change(nid, "摘除", nd["class_type"], "",
                              f"本机没有适配器 {lora}，这一级跳过（改用底模原采样；步数要按底模的档位设）"))
    return graph, changes


def prune_unavailable(graph: dict[str, Any], object_info: dict[str, Any]) -> tuple[dict[str, Any], list[Change]]:
    """剪掉「只有它自己缺权重、且主输出不依赖它」的分支。

    导入的作者图里常有一条顺手产参考图的分支（作者自己的风格 LoRA / 本机没有的底模）。
    留着它，整个任务会因为一条用不到的支线报「缺权重」而跑不起来；
    删掉它，主链路照样出片 —— 但必须逐条写明删了什么、为什么缺，不能悄悄删。

    主输出那条链缺权重时**不删任何东西**：那是真跑不了，交给 missing_models 明确报错，
    比交出一张缺了主角的片子诚实。
    """
    bad = unavailable_weights(graph, object_info)
    if not bad:
        return graph, []
    # 先摘可跳过的适配器，再判分支：顺序反过来会把「只差一个 Turbo 适配器」的主链当成废链剪掉
    graph, changes = unwrap_unavailable_adapters(graph, object_info, bad)
    bad = {nid: names for nid, names in unavailable_weights(graph, object_info).items()
           if str((graph.get(nid) or {}).get("class_type") or "") not in _ADAPTER_CLASSES}
    if not bad:
        return graph, changes
    sinks = [nid for nid, nd in graph.items()
             if str(nd.get("class_type") or "").startswith(("Save", "Preview"))]
    if not sinks:
        return graph, changes
    keep_sets = [(s, _producers(graph, [s])) for s in sinks]
    good = [(s, up) for s, up in keep_sets if not (up & set(bad))]
    if not good:
        return graph, changes
    keep: set[str] = set()
    for _, up in good:
        keep |= up
    for s, up in keep_sets:
        if s in keep:
            continue
        sclass = str((graph.get(s) or {}).get("class_type") or "Save*")
        dead = sorted(up - keep)
        for nid in dead:
            graph.pop(nid, None)
        why = "、".join(f"#{nid} " + ",".join(bad[nid]) for nid in sorted(set(dead) & set(bad)))
        changes.append(Change(s, "剪分支", sclass, "",
                              f"这条分支产的是另一路结果，本机缺权重用不了，已连同上游 {len(dead)} 个节点删掉"
                              + (f"：{why}" if why else "")))
    return graph, changes
