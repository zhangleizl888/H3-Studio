"""从一张 API 图里抽出「前端任务能提供的东西」—— 任务种类（kind）与信号（signals）。

工作流库要能被「按前端任务自动选」，前提是每张图先回答两个问题：它产什么
（image/video/audio），它要吃什么（提示词、首帧、参考图、参考视频、音频、尺寸、时长…）。
这两个问题都能从图上机械地读出来：出口节点决定产什么，几个条件节点加素材加载器决定要什么 ——
所以不需要给每条工作流手写元数据，导入时它自己就说清楚了。

这里只读**核心节点**的形态（gen/local_adapt.py 已经把专有节点换成核心等价物）。
地址语法与 gen/workflow.py 的槽位一致（"<node_id>.<input_name>"）；素材类信号指向
**加载器的文件控件**而不是条件节点的连线口 —— 填槽时要写的是文件名，不是连线。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: 任务入口加载器：类名 → (信号类型, 文件控件名)
MEDIA_LOADERS = {
    "LoadImage": ("image", "image"),
    "LoadImageMask": ("image", "image"),
    "LoadImageOutput": ("image", "image"),
    "LoadVideo": ("video", "file"),
    "LoadAudio": ("audio", "audio"),
    "VHS_LoadVideo": ("video", "video"),
}

#: 条件/采样节点上「前端会给值」的输入：类名 → {输入名: 信号名}
COND_SIGNALS = {
    "MiniMaxH3ImageToVideo": {"prompt": "prompt", "first_frame": "first_frame", "last_frame": "last_frame",
                              "width": "width", "height": "height", "length": "frames"},
    "MiniMaxH3ReferenceToVideo": {"prompt": "prompt", "width": "width", "height": "height", "length": "frames",
                                  "ref_images": "ref_images", "ref_videos": "ref_videos",
                                  "ref_audios": "ref_audios", "ref_video_audios": "ref_video_audios"},
    "MiniMaxH3AddGuide": {"frame": "guide_frame"},
    "EmptyMiniMaxH3LatentAV": {"length": "frames", "width": "width", "height": "height"},
    "EmptyLatentImage": {"width": "width", "height": "height", "batch_size": "batch_size"},
    # 各家的空 latent 都要认：不然这条工作流看起来「不吃尺寸」，前端既改不了画幅，
    # 也没法在本机显存放不下时降级（作者常按 4K 机器写死 1088×1920）。
    "EmptyFlux2LatentImage": {"width": "width", "height": "height", "batch_size": "batch_size"},
    "EmptySD3LatentImage": {"width": "width", "height": "height", "batch_size": "batch_size"},
    "EmptyLatentImagevideo": {"width": "width", "height": "height"},
    "CLIPTextEncode": {"text": "prompt"},
    "TextEncodeQwenImage21": {"prompt": "prompt", "negative_prompt": "negative_prompt"},
    "TextEncodeQwenImage": {"prompt": "prompt", "negative_prompt": "negative_prompt"},
    "TextEncodeQwenImageEditPlus": {"prompt": "prompt", "negative_prompt": "negative_prompt",
                                    "image1": "ref_images", "image2": "ref_images", "image3": "ref_images"},
    "KSampler": {"steps": "steps", "seed": "seed", "cfg": "cfg", "denoise": "denoise"},
    "BasicScheduler": {"steps": "steps", "denoise": "denoise"},
    # 文本拼接节点：正文就是「这条工作流要什么画面」，前缀位本来是留给 VLM 现算描述的
    "AddTextPrefix": {"texts": "prompt", "prefix": "prompt_extra"},
    "AddTextSuffix": {"texts": "prompt", "suffix": "prompt_extra"},
    "RandomNoise": {"noise_seed": "seed"},
    "CFGGuider": {"cfg": "cfg"},
    # Flux2 的 σ 表也吃宽高，尺寸改了不一起改这里会出「按新尺寸算的 latent 配旧 shift」
    "Flux2Scheduler": {"width": "width", "height": "height"},
    # 声音克隆两条腿（装了包之后才会出现在图上；现在填了也只是不命中）
    "FB_Qwen3TTSVoiceClone": {"target_text": "prompt", "ref_text": "ref_text", "language": "language",
                              "seed": "seed", "temperature": "temperature"},
    "IndexTTS2Run": {"text": "prompt", "speed": "speed"},
    "Apply Whisper": {"language": "language"},
}

#: autogrow 组 → 信号名
GROUP_SIGNALS = {"ref_images": "ref_images", "ref_videos": "ref_videos", "ref_audios": "ref_audios",
                 "ref_video_audios": "ref_video_audios", "images": "ref_images", "input_images": "ref_images"}

#: 出口节点 → 产出类型
OUTPUT_KIND = {"SaveVideo": "video", "SaveImage": "image", "SaveAnimatedWEBP": "image",
               "SaveAudioMP3": "audio", "SaveAudio": "audio", "SaveAudioOpus": "audio",
               "PreviewAudio": "audio", "PreviewImage": "image"}

#: 信号类型是「素材」的那些名字 → 素材种类
_MEDIA_SIGNAL_TYPE = {"first_frame": "image", "last_frame": "image", "guide_frame": "image",
                      "ref_images": "image", "ref_videos": "video", "ref_audios": "audio",
                      "ref_video_audios": "audio", "image_1": "image", "image_2": "image", "image_3": "image"}

LABELS = {
    "prompt": "提示词", "negative_prompt": "负向提示词", "first_frame": "首帧", "last_frame": "尾帧",
    "guide_frame": "引导帧", "ref_images": "参考图", "ref_videos": "参考视频", "ref_audios": "参考音频",
    "ref_video_audios": "参考视频音轨", "width": "宽", "height": "高", "frames": "帧数（17k+5）",
    "steps": "采样步数", "seed": "随机种子", "cfg": "CFG", "denoise": "降噪强度", "batch_size": "批量数",
    "filename_prefix": "输出路径前缀",
}


@dataclass
class Signal:
    name: str
    type: str            # text | int | float | bool | image | video | audio
    label: str = ""
    addresses: list[str] = field(default_factory=list)
    required: bool = False
    many: bool = False   # 一组素材位（参考图可以有 N 张）
    value: Any = None    # 图里当前带的值（导入时是作者机器上的那套，等前端重指）
    also: list[str] = field(default_factory=list)   # 同名参数在别处也有，但填槽不碰它们
    options: list[str] | None = None
    minimum: float | None = None
    maximum: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "label": self.label or LABELS.get(self.name, self.name), "type": self.type,
                "addresses": self.addresses, "required": self.required, "many": self.many, "value": self.value,
                "also": self.also, "options": self.options, "min": self.minimum, "max": self.maximum}


#: 只把上游素材穿过去的处理节点：输入名 → 继续往哪一跳找加载器。
#: 不追这一跳的话，`ref_videos` 会落在 H3 节点的连线口上（那是连线，不是可填的文件名），
#: 前端就没法换素材。
TRACE_THROUGH = {
    "GetVideoComponents": ["video"],
    "Video Slice": ["video"],
    "TrimVideoLatent": ["video"],
    "TrimAudioDuration": ["audio"],
    "SplitAudioChannels": ["audio"],
    "AudioConcat": ["audio1"],
    "CreateVideo": ["images"],
}


def _is_link(val: Any) -> bool:
    return isinstance(val, list) and len(val) == 2 and isinstance(val[0], (str, int)) and isinstance(val[1], int)


def _trace_loader(graph: dict[str, Any], ref: Any, want: str = "image", depth: int = 0) -> tuple[str, str] | None:
    """顺着处理节点往上找素材加载器，返回 (节点id, 文件控件名)。

    want 必须传对：ref_videos 要的是**帧**（IMAGE），所以穿过 GetVideoComponents 找到
    LoadVideo 是对的；而 ref_audios / ref_video_audios 要 AUDIO，穿过同一个节点会指到那个
    **视频文件**上 —— 填槽时就会把音频位写成 .mp4，看着合理，跑起来是错的。音轨跟着视频走，
    不该成为一个单独可填的素材位。
    """
    if not _is_link(ref) or depth > 3:
        return None
    src = graph.get(str(ref[0])) or {}
    ct = src.get("class_type") or ""
    loader = MEDIA_LOADERS.get(ct)
    if loader:
        return (str(ref[0]), loader[1]) if loader[0] == want else None
    for fname in TRACE_THROUGH.get(ct, []):
        if want == "audio" and ct == "GetVideoComponents" and fname != "audio":
            continue
        hit = _trace_loader(graph, (src.get("inputs") or {}).get(fname), want, depth + 1)
        if hit:
            return hit
    return None


def _widget_bounds(object_info: dict[str, Any], class_type: str, field_name: str) -> tuple[Any, Any]:
    info = object_info.get(class_type) or {}
    for sec in ("required", "optional"):
        spec = (info.get("input") or {}).get(sec, {}).get(field_name)
        if isinstance(spec, list) and len(spec) > 1 and isinstance(spec[1], dict):
            return spec[1].get("min"), spec[1].get("max")
    return None, None


def _combo_options(object_info: dict[str, Any], class_type: str, field_name: str) -> list[str] | None:
    """下拉控件的候选值。前端要渲染成选择框，参数表也要能挡住「填了个不存在的档位」。"""
    info = object_info.get(class_type) or {}
    for sec in ("required", "optional"):
        spec = (info.get("input") or {}).get(sec, {}).get(field_name)
        if not isinstance(spec, list) or not spec:
            continue
        if isinstance(spec[0], list):
            return [str(c) for c in spec[0] if isinstance(c, str)][:40] or None
        if isinstance(spec[0], str) and spec[0].upper() == "COMBO" and len(spec) > 1 and isinstance(spec[1], dict):
            opts = spec[1].get("options")
            if isinstance(opts, list):
                return [str(o.get("label") or o.get("value") or o) if isinstance(o, dict) else str(o)
                        for o in opts][:40]
    return None


def _link_src(graph: dict[str, Any], ref: Any) -> tuple[str, dict[str, Any]] | None:
    if not _is_link(ref):
        return None
    node = graph.get(str(ref[0]))
    return (str(ref[0]), node) if isinstance(node, dict) else None


def _trace_seconds(graph: dict[str, Any], ref: Any) -> tuple[str, str] | None:
    """H3 的 length 是「秒 → 17k+5 帧」的数学表达式，前端真正该填的是表达式那个秒数控件。"""
    src = _link_src(graph, ref)
    if not src or src[1].get("class_type") != "ComfyMathExpression":
        return None
    inner = _link_src(graph, (src[1].get("inputs") or {}).get("values.a"))
    if inner and inner[1].get("class_type") in ("PrimitiveFloat", "PrimitiveInt"):
        return inner[0], "value"
    return None


#: 与 comfy_extras/nodes_resolution.py 的 ASPECT_RATIOS 同一张表（本机 core 0.37.4 实测）
ASPECT_RATIOS = {"1:1 (Square)": (1, 1), "2:3 (Portrait Photo)": (2, 3), "3:2 (Photo)": (3, 2),
                 "3:4 (Portrait Standard)": (3, 4), "4:3 (Standard)": (4, 3),
                 "9:16 (Portrait Widescreen)": (9, 16), "16:9 (Widescreen)": (16, 9),
                 "21:9 (Ultrawide)": (21, 9)}


def _resolution_of(graph: dict[str, Any], ref: Any) -> dict[str, Any] | None:
    """把 ResolutionSelector 按核心同款公式算成实际宽高，供参数表判「这台机器跑不跑得起」。

    照抄 nodes_resolution.py：target = megapixels×1024×1024，按宽高比缩放后吸附到 multiple
    的最近倍数。把一兆当 1000×1000 会算小 4.6%，显存闸门就差这点会把能跑的档判成跑不了。
    """
    import math

    src = _link_src(graph, ref)
    if not src or src[1].get("class_type") != "ResolutionSelector":
        return None
    nid, nd = src
    ins = nd.get("inputs") or {}
    ratio = str(ins.get("aspect_ratio") or "")
    if ratio not in ASPECT_RATIOS:
        return None
    try:
        mega = float(ins.get("megapixels") or 1.0)
        multiple = int(ins.get("multiple") or 8)
    except (TypeError, ValueError):
        return None
    w_ratio, h_ratio = ASPECT_RATIOS[ratio]
    scale = math.sqrt(mega * 1024 * 1024 / (w_ratio * h_ratio))
    width = round(w_ratio * scale / multiple) * multiple
    height = round(h_ratio * scale / multiple) * multiple
    return {"node": nid, "aspect_ratio": ratio, "megapixels": mega, "multiple": multiple,
            "width": width, "height": height, "mp": round(width * height / 1_000_000, 3)}


def _scalar_type(value: Any) -> str:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    return "text"


def derive(graph: dict[str, Any], object_info: dict[str, Any]) -> dict[str, Any]:
    """→ {"kind": "video|image|audio", "signals": [...], "outputs": [...], "resolution": {...}}"""
    bucket: dict[str, Signal] = {}
    outputs: list[dict[str, Any]] = []
    resolution: dict[str, Any] | None = None
    resolution: dict[str, Any] | None = None

    def put(name: str, type_: str, address: str, *, many: bool = False, value: Any = None) -> None:
        sig = bucket.get(name)
        if sig is None:
            nid, _, fld = address.partition(".")
            ct = (graph.get(nid) or {}).get("class_type") or ""
            lo, hi = _widget_bounds(object_info, ct, fld.split(".", 1)[0])
            sig = bucket[name] = Signal(name=name, type=type_, many=many, minimum=lo, maximum=hi,
                                        options=_combo_options(object_info, ct, fld.split(".", 1)[0]))
        if address not in sig.addresses:
            sig.addresses.append(address)
        if value not in (None, "", [], {}) and sig.value in (None, "", [], {}):
            sig.value = value

    for nid, nd in sorted(graph.items(), key=lambda kv: int(kv[0]) if kv[0].isdigit() else 0):
        ct = nd.get("class_type") or ""
        inputs = nd.get("inputs") or {}
        for fname, val in inputs.items():
            group, _, tail = fname.partition(".")
            # ① autogrow 素材组：一个组是一串素材位
            if group in GROUP_SIGNALS and (tail or isinstance(val, dict)):
                items = list(val.items()) if isinstance(val, dict) else [(tail, val)]
                want = _MEDIA_SIGNAL_TYPE.get(group, "image")
                for _, ref in items:
                    hit = _trace_loader(graph, ref, want)
                    if hit:
                        cur = ((graph.get(hit[0]) or {}).get("inputs") or {}).get(hit[1])
                        put(GROUP_SIGNALS[group], want, f"{hit[0]}.{hit[1]}", many=True, value=cur)
                    # 追不到加载器就说明这一格接的是图内部算出来的东西（例如并排对照里
                    # 「生成结果」那一格），把它当素材位会让前端以为要传一张图进来。
                    continue
                continue
            # ② 条件/采样节点上的具名输入
            sig_name = (COND_SIGNALS.get(ct) or {}).get(fname)
            if sig_name is None:
                continue
            if _is_link(val):
                want = _MEDIA_SIGNAL_TYPE.get(sig_name, "image")
                hit = _trace_loader(graph, val, want)
                if hit:            # 素材位：把地址落在加载器的文件控件上，前端才能换素材
                    cur = ((graph.get(hit[0]) or {}).get("inputs") or {}).get(hit[1])
                    put(sig_name, want, f"{hit[0]}.{hit[1]}", value=cur)
                    continue
                # 尺寸/时长是「算出来的」，但算式那两个入口控件是前端真正该填的东西：
                # 分辨率来自 ResolutionSelector（宽高比 + 兆像素），帧数来自「秒 → 17k+5」的表达式。
                # 不把它们露成信号，导入的工作流就只能按作者调好的档位出片，项目改不了画幅。
                if sig_name in ("width", "height") and resolution is None:
                    resolution = _resolution_of(graph, val)
                    if resolution:
                        put("aspect_ratio", "text", f"{resolution['node']}.aspect_ratio", value=resolution["aspect_ratio"])
                        put("megapixels", "float", f"{resolution['node']}.megapixels", value=resolution["megapixels"])
                if sig_name == "frames":
                    sec = _trace_seconds(graph, val)
                    if sec:
                        put("seconds", "float", f"{sec[0]}.{sec[1]}",
                            value=((graph.get(sec[0]) or {}).get("inputs") or {}).get("value"))
                continue
            put(sig_name, _MEDIA_SIGNAL_TYPE.get(sig_name) or _scalar_type(val), f"{nid}.{fname}", value=val)

        if ct in OUTPUT_KIND:
            put("filename_prefix", "text", f"{nid}.filename_prefix", value=inputs.get("filename_prefix"))
            outputs.append({"node": nid, "class_type": ct, "kind": OUTPUT_KIND[ct]})

    # ③ 素材加载器不管从哪条路进模型，都是「这次任务该给的东西」。
    #     只靠条件节点的具名输入会漏掉一整类图（FLUX 指令编辑的两张 LoadImage 就是），
    #     漏掉的后果不是报错，是这份工作流看起来「不吃图」—— 自动选会挑错条。
    for lid, lnd in sorted(graph.items(), key=lambda kv: int(kv[0]) if kv[0].isdigit() else 0):
        spec = MEDIA_LOADERS.get(lnd.get("class_type") or "")
        if not spec:
            continue
        addr = f"{lid}.{spec[1]}"
        if any(addr in sg.addresses for sg in bucket.values()):
            continue
        name = {"image": "ref_images", "video": "ref_videos", "audio": "ref_audios"}[spec[0]]
        put(name, spec[0], addr, many=True, value=(lnd.get("inputs") or {}).get(spec[1]))

    kinds = {o["kind"] for o in outputs}
    kind = "video" if "video" in kinds else ("audio" if kinds == {"audio"} else "image" if kinds else "video")

    # 必填判定：出片必须有提示词；有首帧位就说明这条吃首帧（图生视频的主路径）
    for name, sig in bucket.items():
        sig.required = name == "prompt" or (name == "first_frame" and kind == "video")
        # 「画面描述」只在它确实是空位（VLM 被降级）时才算可填槽，否则界面上一格没用的空框
        if name == "prompt_extra" and sig.value not in (None, ""):
            sig.value = sig.value

    # 主片尺寸走 aspect_ratio/megapixels 时，图里剩下的 width/height 多半是另一条分支
    # （换背景用的图片分支那个 80×80 空 latent），留着会让前端把成片尺寸写到那条分支上去。
    if resolution is not None:
        for name in ("width", "height"):
            sig = bucket.get(name)
            if sig and all((graph.get(a.split(".", 1)[0]) or {}).get("class_type") != "ResolutionSelector"
                           for a in sig.addresses):
                if all((graph.get(a.split(".", 1)[0]) or {}).get("class_type") == "EmptyLatentImage"
                       for a in sig.addresses):
                    bucket.pop(name, None)

    # 一条图里可能有两个「提示词」：主生成节点的、和顺手做一张参考图的分支的。
    # 填槽是往 addresses 全写，写进图片分支的 CLIPTextEncode 会把那条分支的提示词覆盖掉，
    # 所以 prompt 只保留排名最高的那一类节点，其余挪进 also 供界面显示。
    _dedupe_prompt(bucket, graph)

    order = ["prompt", "prompt_extra", "negative_prompt", "first_frame", "last_frame", "ref_images", "ref_videos",
             "ref_audios", "ref_video_audios", "aspect_ratio", "megapixels", "seconds", "width", "height",
             "frames", "steps", "cfg", "denoise", "seed", "filename_prefix"]
    signals = [bucket[k].as_dict() for k in order if k in bucket] + [s.as_dict() for k, s in bucket.items() if k not in order]
    return {"kind": kind, "signals": signals, "outputs": outputs, "resolution": resolution}


#: 提示词该落在哪类节点上：主生成节点 > 专用编码器 > 通用 CLIPTextEncode
_PROMPT_RANK = {"MiniMaxH3ReferenceToVideo": 0, "MiniMaxH3ImageToVideo": 0, "MiniMaxH3AddGuide": 0,
                "TextEncodeQwenImage21": 1, "TextEncodeQwenImageEditPlus": 1, "TextEncodeQwenImage": 1}


def _dedupe_prompt(bucket: dict[str, Signal], graph: dict[str, Any]) -> None:
    sig = bucket.get("prompt")
    if not sig or len(sig.addresses) < 2:
        return

    def rank(addr: str) -> int:
        nid = addr.split(".", 1)[0]
        return _PROMPT_RANK.get((graph.get(nid) or {}).get("class_type") or "", 2)

    best = min(rank(a) for a in sig.addresses)
    keep = [a for a in sig.addresses if rank(a) == best]
    rest = [a for a in sig.addresses if a not in keep]
    if rest:
        sig.addresses = keep
        sig.also = rest


#: 前端任务描述里的字段 → 信号名（选工作流时按这个对齐）
TASK_FIELD_TO_SIGNAL = {
    "prompt": "prompt", "negative_prompt": "negative_prompt",
    "first_frame": "first_frame", "last_frame": "last_frame",
    "ref_images": "ref_images", "images": "ref_images", "identity_frame": "ref_images",
    "ref_video": "ref_videos", "ref_videos": "ref_videos", "motion_video": "ref_videos",
    "ref_audio": "ref_audios", "ref_audios": "ref_audios", "audio": "ref_audios",
    "width": "width", "height": "height", "seconds": "frames", "frames": "frames",
    "steps": "steps", "seed": "seed",
}
