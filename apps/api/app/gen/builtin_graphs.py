"""内置工作流图构建。

⚠️ 关键约束：模型文件名一律从 /object_info 的候选清单里解析，绝不硬编码。
本机实测就踩到了：文档写 `minimax_h3_fl2va_pruned_int8_convrot.safetensors` 放 `models/diffusion_models/`，
实际这台机器是大驼峰 `MiniMax_H3_FL2VA_pruned_int8_convrot.safetensors` 放 `models/unet/`
（ComfyUI 两个目录都扫）。硬编码字符串会在 POST /prompt 的 node_errors 里失败，
而且失败信息很难看懂。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from .base import GenError
from .comfy_native import ComfyNativeClient
from ..logging_setup import get_logger

log = get_logger("gen.graphs")

# 候选文件名匹配用的小写关键词（不含分隔符，比对时去掉 _ 和 -）
UNET_FL2VA = "minimaxh3fl2vaprunedint8convrot"
UNET_REF2VA = "minimaxh3ref2vaprunedint8convrot"
TEXT_ENCODER = "qwen3vl32bminimaxh3int8convrot"
TEXT_ENCODER_FALLBACKS = ("qwen3vl32bminimaxh3", "qwen3vl32bminimaxh3bf16")
VAE_VIDEO = "minimaxh3videovaefp16"
VAE_AUDIO = "minimaxh3audiovaefp32"


def h3_length(seconds: float, fps: int = 24) -> int:
    """秒 → 合法帧数，向上吸附到 17k+5（跟随官方核心节点，不跟随 SequenceForge 的就近取整）。

    注意别写成 n + (5 - n % 17) % 17 之类"看起来对"的形式：
    Python 与 JS 的取模语义不同，而 -(-x//y) 少一个括号就退化成向下取整。
    本机实测 5 秒必须是 124 帧；曾因为写错得到 107 帧，等于把每个镜头剪短 0.7 秒。
    """
    n = max(5, round(seconds * fps))
    if n <= 5:
        return 5
    return 17 * (-(-(n - 5) // 17)) + 5


def _norm(name: str) -> str:
    return name.lower().replace("_", "").replace("-", "").replace(".", "")


def _pick(choices: list[str], want: str, fallbacks: tuple[str, ...] = ()) -> str | None:
    """先精确（归一化后）命中，再按前缀回落。"""
    normed = {_norm(c): c for c in choices}
    for cand in (want, *fallbacks):
        if cand in normed:
            return normed[cand]
    for cand in (want, *fallbacks):
        for n, original in normed.items():
            if n.startswith(cand):
                return original
    return None


# 图片侧（Qwen-Image 2.1）。文件名同样只当"优先挑哪个"的提示，真值一律从 /object_info 拿。
# 官方模板 image_qwen_image_2_1_t2i.json 出厂是 int8_convrot 的文本编码器，本机只有 bf16，
# 所以这里给的是优先级而不是唯一答案。
UNET_QWEN = "qwenimage21int8convrot"
UNET_QWEN_FALLBACKS = ("qwenimage21bf16", "qwenimage21")
CLIP_QWEN = ("qwen3vl8bint8convrot", "qwen3vl8bbf16", "qwen3vl8b")
VAE_QWEN = ("qwenimage21vaebf16", "qwenimage21vae", "qwenimagevae")

# 官方 t2i 模板实测参数：25 步 / cfg 1 / euler / simple。
# cfg=1 是有意为之 —— 负向提示词在这套上几乎不起作用，别按 SD 的习惯调高。
QWEN_STEPS = 25
QWEN_CFG = 1.0


@dataclass
class H3Weights:
    unet: str
    text_encoder: str
    vae_video: str
    vae_audio: str
    turbo_lora: str | None = None

    @property
    def missing(self) -> list[str]:
        out = []
        for label, value in (("UNET", self.unet), ("文本编码器", self.text_encoder), ("视频 VAE", self.vae_video), ("音频 VAE", self.vae_audio)):
            if not value:
                out.append(label)
        return out


@dataclass
class QwenWeights:
    unet: str
    clip: str
    vae: str

    @property
    def missing(self) -> list[str]:
        return [
            label
            for label, value in (("UNET", self.unet), ("文本编码器", self.clip), ("VAE", self.vae))
            if not value
        ]


async def discover_h3_weights(client: ComfyNativeClient) -> H3Weights:
    """问实例 itself 有哪些文件。这是唯一可靠的来源。"""
    unets = await _choices(client, "UNETLoader", "unet_name")
    clips = await _choices(client, "CLIPLoader", "clip_name")
    vaes = await _choices(client, "VAELoader", "vae_name")
    loras = await _choices(client, "LoraLoaderModelOnly", "lora_name")

    unet = _pick(unets, UNET_FL2VA) or _pick(unets, UNET_REF2VA)
    encoder = _pick(clips, TEXT_ENCODER, TEXT_ENCODER_FALLBACKS)
    vae_video = _pick(vaes, VAE_VIDEO)
    vae_audio = _pick(vaes, VAE_AUDIO)
    turbo = _pick(loras, "minimaxh3fl2vturbo8stepv10comfyuibf16", ("minimaxh3fl2vturbo8step", "minimaxh3turbo4step"))

    weights = H3Weights(unet=unet, text_encoder=encoder, vae_video=vae_video, vae_audio=vae_audio, turbo_lora=turbo)
    log.info(
        "H3 权重解析：unet=%s encoder=%s vae=%s/%s turbo=%s",
        unet,
        encoder,
        vae_video,
        vae_audio,
        turbo,
    )
    return weights


async def _choices(client: ComfyNativeClient, class_type: str, field_name: str) -> list[str]:
    try:
        info = await client.object_info(class_type)
    except Exception as exc:
        log.warning("读不到 %s 的候选清单：%s", class_type, exc)
        return []
    spec = ((info.get(class_type) or {}).get("input") or {}).get("required", {}).get(field_name)
    if isinstance(spec, list) and spec and isinstance(spec[0], list):
        return [c for c in spec[0] if isinstance(c, str)]
    return []


def h3_video_graph(
    weights: H3Weights,
    *,
    prompt: str,
    width: int,
    height: int,
    length: int,
    steps: int = 25,
    seed: int = 0,
    filename_prefix: str = "h3/seg",
    use_turbo: bool = False,
    first_frame: str | None = None,
    last_frame: str | None = None,
) -> dict:
    """core-only 的 H3 图：全官方节点，宽/高/帧数都是算好的字面量。

    first_frame / last_frame 传的是**已上传到该实例 input 目录的文件名**：
      都不给 = t2v；只给首帧 = i2v；首尾都给 = fl2v（导演台的主路径）。
    MiniMaxH3ImageToVideo 本身就带这两个可选输入，不必像老教程那样拼 AddGuide。

    class_type 用 MiniMaxH3SigmaShift（ModelSamplingMiniMaxH3 只是它的 display_name），
    输入名是 shift_video / shift_audio。
    """
    missing = weights.missing
    if missing:
        raise GenError(f"这台实例跑不了 H3，缺：{', '.join(missing)}。请补权重或换 RunningHub 实例。", kind="missing_models")

    h3_inputs: dict[str, Any] = {
        "clip": ["128", 0],
        "vae": ["119", 0],
        "width": width,
        "height": height,
        "length": length,
        "prompt": prompt,
    }
    nodes: dict = {
        "127": {"class_type": "UNETLoader", "inputs": {"unet_name": weights.unet, "weight_dtype": "default"}, "_meta": {"title": "UNET"}},
        "128": {"class_type": "CLIPLoader", "inputs": {"clip_name": weights.text_encoder, "type": "minimax", "device": "default"}, "_meta": {"title": "CLIP"}},
        "119": {"class_type": "VAELoader", "inputs": {"vae_name": weights.vae_video}, "_meta": {"title": "Video VAE"}},
        "120": {"class_type": "VAELoader", "inputs": {"vae_name": weights.vae_audio}, "_meta": {"title": "Audio VAE"}},
    }
    # 帧图节点编号从 150 起，避开下面采样链的 12x/13x
    for slot, name in (("first_frame", first_frame), ("last_frame", last_frame)):
        if not name:
            continue
        node_id = "150" if slot == "first_frame" else "151"
        nodes[node_id] = {"class_type": "LoadImage", "inputs": {"image": name}, "_meta": {"title": "首帧" if slot == "first_frame" else "尾帧"}}
        h3_inputs[slot] = [node_id, 0]

    nodes["131"] = {"class_type": "MiniMaxH3ImageToVideo", "inputs": h3_inputs, "_meta": {"title": "H3 条件与空 latent"}}
    nodes["137"] = {"class_type": "MiniMaxH3SigmaShift", "inputs": {"model": ["127", 0], "shift_video": 12.0, "shift_audio": 3.0}, "_meta": {"title": "Sigma Shift"}}
    model_out = ["137", 0]

    if use_turbo and weights.turbo_lora:
        nodes["140"] = {
            "class_type": "LoraLoaderModelOnly",
            "inputs": {"model": ["137", 0], "lora_name": weights.turbo_lora, "strength_model": 1.0},
            "_meta": {"title": "Turbo LoRA"},
        }
        model_out = ["140", 0]

    nodes.update(
        {
            "135": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "res_multistep"}, "_meta": {"title": "Sampler"}},
            "124": {"class_type": "BasicScheduler", "inputs": {"model": model_out, "scheduler": "simple", "steps": steps, "denoise": 1.0}, "_meta": {"title": "Sigmas"}},
            "129": {"class_type": "RandomNoise", "inputs": {"noise_seed": seed}, "_meta": {"title": "Noise"}},
            "126": {"class_type": "BasicGuider", "inputs": {"model": model_out, "conditioning": ["131", 0]}, "_meta": {"title": "Guider"}},
            "125": {
                "class_type": "SamplerCustomAdvanced",
                "inputs": {"noise": ["129", 0], "guider": ["126", 0], "sampler": ["135", 0], "sigmas": ["124", 0], "latent_image": ["131", 1]},
                "_meta": {"title": "采样"},
            },
            "122": {"class_type": "VAEDecode", "inputs": {"samples": ["125", 0], "vae": ["119", 0]}, "_meta": {"title": "解码画面"}},
            "121": {"class_type": "VAEDecodeAudio", "inputs": {"samples": ["125", 0], "vae": ["120", 0]}, "_meta": {"title": "解码音频"}},
            "130": {"class_type": "CreateVideo", "inputs": {"images": ["122", 0], "audio": ["121", 0], "fps": 24, "bit_depth": 8}, "_meta": {"title": "合成视频"}},
            # format/codec 都是 COMFY_DYNAMICCOMBO_V3 控件；codec 带 hidden:true，
            # 因此它不进 widgets_values（UI 往返会丢，见 workflow.py 的 hidden 处理），
            # 但作为 API 输入是合法的 —— 实测带 codec 的 H3 任务能正常出片。
            "92": {"class_type": "SaveVideo", "inputs": {"video": ["130", 0], "filename_prefix": filename_prefix, "format": "auto", "codec": "auto"}, "_meta": {"title": "保存"}},
        }
    )
    return nodes


# ───────── 长片无缝续拍（custom_nodes/ComfyUI-minimaxH3-SequenceForge） ─────────

CHAIN_NODE = "H3SeamlessChainSampler"
CHAIN_GUIDE_OPTIONS = ("5", "22", "39", "56")   # 节点只认这几个引导帧数（17k+5 网格点）
# 存档目录名里只留中英文、数字和 . _ -：插件的 checkpoint.ckpt_dir() 直接把控件值
# os.path.join 到 output/h3_projects 下，自己不做任何校验，穿越只能在这里挡。
_CHAIN_NAME_BAD = re.compile(r"[^0-9A-Za-z._\u4e00-\u9fff-]")


def chain_archive_name(raw: str) -> str:
    """清洗长片存档目录名；清洗后为空就报错，不静默换名（换名等于换了一条存档链）。"""
    name = _CHAIN_NAME_BAD.sub("", str(raw or "").strip()).lstrip(".").strip(".")
    if not name:
        raise GenError("长片存档目录名无效：只能用中英文、数字、点、下划线和短横线", kind="client_validation")
    return name[:80]


def h3_chain_graph(
    weights: H3Weights,
    *,
    segments: list[dict[str, Any]],
    archive_dir: str,
    width: int = 864,
    height: int = 480,
    seconds: float = 5.0,
    guide_frames: str = "22",
    steps: int = 25,
    seed: int = 0,
    use_turbo: bool = False,
    first_frame: str | None = None,
    end_frame: str | None = None,
    identity_frame: str | None = None,
    auto_reseed: bool = False,
    filename_prefix: str = "h3/chain",
) -> dict:
    """一条承接链一张图：N 段提示词喂给 H3SeamlessChainSampler，输出拼接好的整条视频。

    段数 = 1 时它等价于单镜头出片（没有段间桥），但仍然把 latent 存档落在
    output/h3_projects/<archive_dir>/，所以下一次带同一个目录名、多带一段的任务
    会秒级回放已有段、只采样新段 ——「承接上一镜」就是靠这个跨任务续上的。

    每段的秒数走导演台状态 JSON 的 segments[i].seconds：节点上的「每段时长」是
    全链一个值，而镜头时长本来就是一段一个，只有 JSON 那条路能逐段给。
    first_frame / end_frame / identity_frame 传的是**已上传到该实例 input 目录的文件名**。
    """
    missing = weights.missing
    if missing:
        raise GenError(f"这台实例跑不了 H3，缺：{', '.join(missing)}。请补权重或换 RunningHub 实例。", kind="missing_models")
    if not segments:
        raise GenError("长片续拍至少要有一段", kind="client_validation")
    if guide_frames not in CHAIN_GUIDE_OPTIONS:
        raise GenError(f"引导帧数只能是 {'、'.join(CHAIN_GUIDE_OPTIONS)}（节点的硬约束）", kind="client_validation")

    ds: dict[str, Any] = {
        "mode": "首帧视频" if first_frame else "文生视频",
        "prompts": [s["prompt"] for s in segments],
        "segments": [{"seconds": s["seconds"]} if s.get("seconds") else {} for s in segments],
    }
    if first_frame:
        ds["first_frame"] = first_frame
    if end_frame:
        ds["end_frame"] = end_frame
    if identity_frame:
        ds["last_frame"] = identity_frame

    nodes: dict = {
        "127": {"class_type": "UNETLoader", "inputs": {"unet_name": weights.unet, "weight_dtype": "default"}, "_meta": {"title": "UNET"}},
        "128": {"class_type": "CLIPLoader", "inputs": {"clip_name": weights.text_encoder, "type": "minimax", "device": "default"}, "_meta": {"title": "CLIP"}},
        "119": {"class_type": "VAELoader", "inputs": {"vae_name": weights.vae_video}, "_meta": {"title": "Video VAE"}},
        "120": {"class_type": "VAELoader", "inputs": {"vae_name": weights.vae_audio}, "_meta": {"title": "Audio VAE"}},
    }
    nodes["137"] = {"class_type": "MiniMaxH3SigmaShift", "inputs": {"model": ["127", 0], "shift_video": 12.0, "shift_audio": 3.0}, "_meta": {"title": "Sigma Shift"}}
    model_out = ["137", 0]
    if use_turbo and weights.turbo_lora:
        nodes["140"] = {
            "class_type": "LoraLoaderModelOnly",
            "inputs": {"model": ["137", 0], "lora_name": weights.turbo_lora, "strength_model": 1.0},
            "_meta": {"title": "Turbo LoRA"},
        }
        model_out = ["140", 0]

    nodes["160"] = {
        "class_type": CHAIN_NODE,
        "inputs": {
            "模型": model_out,
            "文本编码器": ["128", 0],
            "视频VAE": ["119", 0],
            "音频VAE": ["120", 0],
            # 「宽高比=自定义」时宽度/高度才直接生效：其余档位由 宽高比+百万像素 换算，
            # 会把导演台定的画幅改掉。百万像素本身不用管，但它是 required 控件，
            # API 提交必须逐个给齐 —— 漏一个就是一条「Prompt outputs failed validation｜递减锚定」，
            # 报错只点名第一个缺的，看不全。
            "宽高比": "自定义",
            "百万像素": 0.5,
            "宽度": width,
            "高度": height,
            "每段时长": float(seconds),
            "引导帧数": guide_frames,
            "种子": seed,
            "步数": steps,
            "CFG": 1.0,
            "采样器": "res_multistep",
            "调度器": "simple",
            # 「分段」= 落 latent 存档 + 每段 mp4，这是跨任务续拍的前提；
            # 成片不在节点里编码，交给下面的 SaveVideo，免得同一个链每跑一次多写一份 final_*。
            "自动存档": "关闭",
            "自动保存": "分段",
            "自动成片": "关闭",
            "存档目录": chain_archive_name(archive_dir),
            "审片模式": "关闭",
            "重跑起始段": 0,
            "生成模式": ds["mode"],
            "桥帧门控": "标注",
            "清晰度阈值": 30.0,
            "回退上限": 34,
            "锚定加噪": 0.0,
            "递减锚定": "关闭",
            "接缝重摇": "自动" if auto_reseed else "关闭",
            "重摇阈值": 0.06,
            "重摇上限": 1,
            "一采编码": "标准",
            # 提示词的权威来源是下面的导演台状态（只有它能逐段给秒数），但
            # 「提示词组」的 min=1 让 提示词_0 成了 API 必填项 —— 不给就是一条
            # 「failed validation｜提示词_0」。autogrow 在 API 里是**点号全路径**的平铺键
            # （实测：{"提示词组": {"提示词_0": …}} 被拒，"提示词组.提示词_0": … 通过校验），
            # 这里按同序把正文再填一份，两条路一致。
            **{f"提示词组.提示词_{i}": s["prompt"] for i, s in enumerate(segments)},
            "导演台状态": json.dumps(ds, ensure_ascii=False),
        },
        "_meta": {"title": f"H3 无缝续拍（{len(segments)} 段）"},
    }
    # 帧率是节点的 INT 输出，CreateVideo 的 fps 是 FLOAT，直接给字面量 24（H3 全程按 24fps 出）
    nodes["130"] = {"class_type": "CreateVideo", "inputs": {"images": ["160", 0], "audio": ["160", 1], "fps": 24, "bit_depth": 8}, "_meta": {"title": "合成视频"}}
    nodes["92"] = {"class_type": "SaveVideo", "inputs": {"video": ["130", 0], "filename_prefix": filename_prefix, "format": "auto", "codec": "auto"}, "_meta": {"title": "保存"}}
    return nodes


async def discover_qwen_weights(client: ComfyNativeClient) -> QwenWeights:
    unets = await _choices(client, "UNETLoader", "unet_name")
    clips = await _choices(client, "CLIPLoader", "clip_name")
    vaes = await _choices(client, "VAELoader", "vae_name")
    unet = _pick(unets, UNET_QWEN, UNET_QWEN_FALLBACKS)
    # 注意取的是 _pick 的返回值（实例上的真实文件名），不是候选模式本身 ——
    # 写成 `next(c for c in 候选 if _pick(...))` 会把 "qwen3vl8bbf16" 这种归一化提示当文件名交给 ComfyUI。
    clip = next((_pick(clips, c) for c in CLIP_QWEN if _pick(clips, c)), None)
    vae = next((_pick(vaes, v) for v in VAE_QWEN if _pick(vaes, v)), None)
    log.info("Qwen-Image 权重解析：unet=%s clip=%s vae=%s", unet, clip, vae)
    return QwenWeights(unet=unet or "", clip=clip or "", vae=vae or "")


def qwen_image_graph(
    weights: QwenWeights,
    *,
    prompt: str,
    width: int = 1024,
    height: int = 1024,
    negative_prompt: str = "",
    steps: int = QWEN_STEPS,
    cfg: float = QWEN_CFG,
    seed: int = 0,
    filename_prefix: str = "qwen/img",
    ref_images: list[str] | None = None,
    ref_resolution: int = 768,
    cache_dtype: str = "int8",
) -> dict:
    """Qwen-Image 2.1 文生图 / 参考图编辑，一张图两种用法。

    不带 ref_images 就是 t2i，尺寸由 EmptyLatentImage 给（这是官方 t2i 模板的接法）；
    带 ref_images 时尺寸必须改由 TextEncodeQwenImage21 的 latent 输出来 —— 它按第一张
    参考图的比例给 latent，EmptyLatentImage 的方形会把编辑结果拉成方的。
    """
    missing = weights.missing
    if missing:
        raise GenError(f"这台实例跑不了 Qwen-Image，缺：{', '.join(missing)}。请补权重或换实例。", kind="missing_models")

    refs = [r for r in (ref_images or []) if r]
    nodes: dict = {
        "501": {"class_type": "UNETLoader", "inputs": {"unet_name": weights.unet, "weight_dtype": "default"}, "_meta": {"title": "Qwen-Image DiT"}},
        "502": {"class_type": "CLIPLoader", "inputs": {"clip_name": weights.clip, "type": "qwen_image", "device": "default"}, "_meta": {"title": "Qwen3-VL 文本编码器"}},
        "503": {"class_type": "VAELoader", "inputs": {"vae_name": weights.vae}, "_meta": {"title": "VAE"}},
    }
    te_inputs: dict[str, Any] = {
        "clip": ["502", 0],
        "prompt": prompt,
        "negative_prompt": negative_prompt,
        "vae": ["503", 0],
        "resolution": ref_resolution,
    }
    for i, name in enumerate(refs, start=1):
        nodes[str(520 + i)] = {"class_type": "LoadImage", "inputs": {"image": name}, "_meta": {"title": f"参考图 {i}"}}
        # autogrow 输入在 API 里是一个叫 images 的字典（键 image_1..image_16），
        # 拆成顶层的 image_1 会被 execute() 当成未知参数直接拒
        te_inputs.setdefault("images", {})[f"image_{i}"] = [str(520 + i), 0]
    nodes["504"] = {"class_type": "TextEncodeQwenImage21", "inputs": te_inputs, "_meta": {"title": "条件与空 latent"}}

    # 尺寸一律由 EmptyLatentImage 按请求的 width/height 给。
    # 不要接 504 的 latent 输出：TextEncodeQwenImage21 的 resolution 是单个 int，只会吐
    # resolution×resolution 的方图，把 16:9 的首帧压成方形，再喂进 H3 出片就整体竖压。
    # 参考图仍然通过 504 的 images 进 VLM 做条件，与 latent 尺寸无关，所以换画布不影响一致性。
    nodes["505"] = {"class_type": "EmptyLatentImage", "inputs": {"width": width, "height": height, "batch_size": 1}, "_meta": {"title": "空 latent"}}
    latent_ref = ["505", 0]
    model_ref = ["501", 0]
    if refs:
        # 带参考图时参考段的 K/V 会在每一步被反复用到，官方 edit 模板因此挂了 QwenImage21Cache。
        # 本机实测：不加这个、又在和文本模型抢显存的情况下，一次编辑 15 分钟都跑不完。
        nodes["509"] = {
            "class_type": "QwenImage21Cache",
            "inputs": {"model": ["501", 0], "device": "auto", "dtype": cache_dtype},
            "_meta": {"title": "参考段 K/V 缓存"},
        }
        model_ref = ["509", 0]

    nodes["506"] = {
        "class_type": "KSampler",
        "inputs": {
            "model": model_ref,
            "positive": ["504", 0],
            "negative": ["504", 1],
            "latent_image": latent_ref,
            "seed": seed,
            "steps": steps,
            "cfg": cfg,
            "sampler_name": "euler",
            "scheduler": "simple",
            "denoise": 1.0,
        },
        "_meta": {"title": "采样"},
    }
    nodes["507"] = {"class_type": "VAEDecode", "inputs": {"samples": ["506", 0], "vae": ["503", 0]}, "_meta": {"title": "解码"}}
    nodes["508"] = {"class_type": "SaveImage", "inputs": {"images": ["507", 0], "filename_prefix": filename_prefix}, "_meta": {"title": "保存"}}
    return nodes
