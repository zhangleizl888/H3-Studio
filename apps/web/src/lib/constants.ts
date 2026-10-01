import type { JobKind, Workflow } from "./types";

/** H3 合法帧数是 17k+5 网格；官方核心节点向上吸附，SequenceForge 就近吸附。
 *  我们跟随官方节点向上取整，并自己算清楚直接写进 length，不依赖节点静默吸附。
 *  ⚠️ 不能写成 n + (5 - n%17)%17：JS 的 % 对负数返回负值，会向下吸附，
 *     6 秒会得到 141 帧而官方节点给 158 帧 —— 每个镜头都被悄悄剪短。 */
export function h3FrameCount(seconds: number, fps = 24): number {
  const n = Math.max(5, Math.round(seconds * fps));
  if (n <= 5) return 5;
  return 17 * Math.ceil((n - 5) / 17) + 5;
}

export function h3SecondsOf(frames: number, fps = 24): number {
  return frames / fps;
}

/** 官方节点里 length 的合法步进 */
export const H3_GRID = 17;
export const H3_MIN_FRAMES = 5;
export const H3_TRAINED_RANGE = [124, 362] as const;

export const H3_BASELINE = {
  steps: 25,
  sampler: "res_multistep",
  scheduler: "simple",
  cfg: 1.0,
  shiftVideo: 12.0,
  shiftAudio: 3.0,
  fps: 24,
} as const;

export const CANVAS = {
  preview: { w: 864, h: 480, mp: 0.4, note: "官方模板预览档" },
  full: { w: 1344, h: 768, mp: 0.98, note: "全质量档；硬上限 768×1344" },
} as const;

/** Turbo LoRA 步数必须严格配对，且 ref2v 只能配 ref2va 底模 */
export const TURBO: Record<string, { file: string; steps: number; base: "fl2va" | "ref2va"; label: string }> = {
  none: { file: "", steps: 25, base: "fl2va", label: "不用 Turbo（25 步）" },
  fl2v_8: {
    file: "minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors",
    steps: 8,
    base: "fl2va",
    label: "fl2v 8 步",
  },
  fl2v_4: {
    file: "minimax_h3_fl2v_turbo_4step_v1.0_768p_comfyui_bf16.safetensors",
    steps: 4,
    base: "fl2va",
    label: "fl2v 4 步 · 768p",
  },
  ref2v_4: {
    file: "minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors",
    steps: 4,
    base: "ref2va",
    label: "ref2v 4 步（必须配 ref2va 底模）",
  },
};

export const CONTEXT_FRAMES = [5, 22, 39, 56] as const;

/** 景别与运镜的词表在 lib/prompts.ts（SHOT_SIZES_ZH / CAMERA_ALIASES），后端同源的是 app/director.py。
 *  这里不再重复一份，避免出现第二套选不中的枚举。 */

export const VISUAL_STYLES: { key: string; name: string; promptZh: string }[] = [
  { key: "ink_manga", name: "黑白漫画", promptZh: "黑白漫画线稿，粗犷蘸水笔触，密集排线阴影，网点纸高光，无彩色" },
  { key: "cel_shaded", name: "赛璐璐动画", promptZh: "赛璐璐上色，清晰硬边阴影，两档色阶，饱和动画配色" },
  { key: "guofeng", name: "国风工笔", promptZh: "中国工笔重彩，绢本设色，细腻描金，留白构图，淡雅青绿" },
  { key: "cinematic_real", name: "电影写实", promptZh: "电影级写实摄影，柔和主光，浅景深，35mm 胶片颗粒，冷调分级" },
  { key: "noir", name: "黑色电影", promptZh: "黑色电影布光，硬侧光，百叶窗投影，高反差，湿润街景，单色调偏冷" },
  { key: "ghibli", name: "治愈系", promptZh: "手绘动画背景质感，通透云层，暖阳光晕，饱和植被，柔和轮廓" },
  { key: "cyberpunk", name: "赛博霓虹", promptZh: "赛博朋克，霓虹湿地反射，品红与青色补光，全息广告，密集管线细节" },
  { key: "watercolor", name: "水彩", promptZh: "水彩晕染，湿边扩散，纸纹肌理，低饱和，留白呼吸感" },
  { key: "oil_impasto", name: "厚涂", promptZh: "油画厚涂，明显笔触堆叠，刮刀肌理，暖反射光" },
  { key: "pixel", name: "像素风", promptZh: "像素艺术，16 色受限调色，抖动渐变，清晰像素轮廓" },
  { key: "ui_clean", name: "扁平插画", promptZh: "扁平矢量插画，有限调色板，无渐变，几何形状，干净描边" },
  { key: "claymation", name: "黏土定格", promptZh: "黏土定格质感，指纹痕迹，柔光，微缩景深，逐帧抖动" },
];

export const JOB_KIND_LABEL: Record<JobKind, string> = {
  llm_chat: "文本",
  image: "图像",
  video: "视频",
  video_chain: "长片续拍",
  upscale: "放大",
  detect_shots: "镜头切分",
  assemble: "合成",
  workflow_test: "工作流试运行",
};

export const WORKFLOW_OPTIONS = [
  { key: "h3_t2v", label: "H3 文生视频（带音频）" },
  { key: "h3_i2v", label: "H3 首帧生视频" },
  { key: "h3_fl2v", label: "H3 首尾帧生视频" },
  { key: "h3_r2v", label: "H3 参考驱动（角色一致性）" },
  { key: "h3_turbo_t2v", label: "H3 Turbo 8 步 文生视频" },
  { key: "h3_turbo_r2v", label: "H3 Turbo 4 步 参考驱动" },
  { key: "qwen_image_21_t2i", label: "Qwen-Image-2.1 文生图" },
  { key: "qwen_image_21_edit", label: "Qwen-Image-2.1 图编辑（≤10 参考）" },
  { key: "h3_chain_segment", label: "长片无缝续拍单段" },
];

/** RunningHub 错误码 → 人的语言 + 处置 */
export const RH_ERROR_HINTS: Record<string, string> = {
  "801": "免费账号不支持 API 调用，需要开通消费级会员",
  "803": "nodeId / fieldName 与工作流实际入参不匹配",
  "804": "任务还在排队，稍后再查",
  "805": "任务失败，failedReason 里有原始 ComfyUI 节点报错",
  "810": "该工作流还没在 RunningHub 网页里保存并手动跑通，API 无法调用",
  "813": "任务仍在运行中",
  "415": "独占机器已用满，按官方建议等 30–120 秒再试",
  "416": "余额不足，已停止向该实例派发任务",
  "421": "共享并发已满，排队中",
  "433": "图校验失败（节点或入参不合法）",
  "435": "instanceType 填错，只能是 default / plus / ultra",
  "1003": "请求过于频繁，退避后重试",
  "1014": "该接口仅企业级-共享 key 可用",
  "1520": "并发达到上限，退避后重试",
};

/** 这些错误是「资源紧张」而不是「任务错了」，必须退避重试而非判失败 */
export const RH_RETRYABLE = new Set(["415", "421", "1003", "1520", "804", "813"]);

export const PLACE_META = {
  local: { label: "本机", mach: "local" as const, why: "免费、可控、产物可直接读盘" },
  cloud_self: { label: "自建云端", mach: "cloud_self" as const, why: "自己的 GPU + cloudflared 隧道" },
  cloud_runninghub: { label: "RunningHub", mach: "cloud_runninghub" as const, why: "按秒计费，显存档位可选" },
};

/** 内置工作流的最小图（真·core-only；宽/高/length 由后端算成字面量） */
export function coreH3Graph(opts: {
  width: number;
  height: number;
  length: number;
  steps: number;
  seed: number;
  prompt: string;
  unet?: string;
}): Record<string, unknown> {
  return {
    "127": {
      class_type: "UNETLoader",
      inputs: { unet_name: opts.unet ?? "minimax_h3_fl2va_pruned_int8_convrot.safetensors", weight_dtype: "default" },
    },
    "128": {
      class_type: "CLIPLoader",
      inputs: { clip_name: "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors", type: "minimax", device: "default" },
    },
    "119": { class_type: "VAELoader", inputs: { vae_name: "minimax_h3_video_vae_fp16.safetensors" } },
    "120": { class_type: "VAELoader", inputs: { vae_name: "minimax_h3_audio_vae_fp32.safetensors" } },
    "131": {
      class_type: "MiniMaxH3ImageToVideo",
      inputs: {
        clip: ["128", 0],
        vae: ["119", 0],
        width: opts.width,
        height: opts.height,
        length: opts.length,
        prompt: opts.prompt,
      },
    },
    "137": {
      class_type: "MiniMaxH3SigmaShift",
      inputs: { model: ["127", 0], shift_video: 12.0, shift_audio: 3.0 },
    },
    "135": { class_type: "KSamplerSelect", inputs: { sampler_name: "res_multistep" } },
    "124": { class_type: "BasicScheduler", inputs: { model: ["137", 0], scheduler: "simple", steps: opts.steps, denoise: 1 } },
    "129": { class_type: "RandomNoise", inputs: { noise_seed: opts.seed } },
    "126": { class_type: "BasicGuider", inputs: { model: ["137", 0], conditioning: ["131", 0] } },
    "125": {
      class_type: "SamplerCustomAdvanced",
      inputs: { noise: ["129", 0], guider: ["126", 0], sampler: ["135", 0], sigmas: ["124", 0], latent_image: ["131", 1] },
    },
    "122": { class_type: "VAEDecode", inputs: { samples: ["125", 0], vae: ["119", 0] } },
    "121": { class_type: "VAEDecodeAudio", inputs: { samples: ["125", 0], vae: ["120", 0] } },
    "130": { class_type: "CreateVideo", inputs: { images: ["122", 0], audio: ["121", 0], fps: 24, bit_depth: 8 } },
    "92": { class_type: "SaveVideo", inputs: { video: ["130", 0], filename_prefix: "h3/seg", format: "auto", codec: "auto" } },
  };
}

export const BUILTIN_WORKFLOWS: Pick<Workflow, "id" | "name" | "family" | "tags" | "description">[] = [
  { id: "h3_t2v", name: "H3 文生视频", family: "video", tags: ["H3", "带音频"], description: "官方 core-only 节点链，输出画面 + 立体声" },
  { id: "h3_i2v", name: "H3 首帧生视频", family: "video", tags: ["H3"], description: "first_frame 驱动" },
  { id: "h3_fl2v", name: "H3 首尾帧生视频", family: "video", tags: ["H3"], description: "首尾锁定，中间由模型补" },
  { id: "h3_r2v", name: "H3 参考驱动", family: "video", tags: ["H3", "角色一致性"], description: "ref_image ≤9 / ref_video ≤3 / ref_audio ≤3" },
  { id: "qwen_image_21_t2i", name: "Qwen-Image-2.1 文生图", family: "image", tags: ["Qwen"], description: "1K/2K 预设，40 步 CFG 1.0" },
  { id: "qwen_image_21_edit", name: "Qwen-Image-2.1 图编辑", family: "image", tags: ["Qwen", "一致性"], description: "≤10 参考图，原生 RGBA" },
];
