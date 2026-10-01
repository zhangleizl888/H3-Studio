/**
 * 提示词拼装：项目里的角色/场景/镜头 → 能直接投产的提示词。
 *
 * 形态语言抄 manga-studio（三段式：画面主体 → 视觉风格 → 镜头运动 + 首尾帧要求 + 角色一致性），
 * 但有两处刻意不照抄：
 *   1. manga-studio 的视觉风格选项有 15 项、提示词表只写了 6 项，选到没写的那 9 项等于没选；
 *      这里 15 项全部补齐。
 *   2. 视频段不再套 sora/veo 的自然语言模板，改成 MiniMax H3 的三段式（画面/环境声/配乐），
 *      因为本机跑的是 H3，写「配音语言」这类字段没有任何节点会读它。
 */

import type { AspectRatio, Character, H3Prompt, H3PromptMode, ProjectConfig, Scene, Shot, Variation } from "./types";

/* ───────── 视觉风格 ───────── */

export interface VisualStyleDef {
  key: string;
  name: string;
  emoji: string;
  desc: string;
  prompt: string;
}

export const VISUAL_STYLES: VisualStyleDef[] = [
  { key: "anime", name: "2D日漫", emoji: "🌟", desc: "日式动漫风格，线条感强", prompt: "Japanese anime style, cel-shaded, clean line art, vibrant colors, expressive eyes, dynamic poses, Makoto Shinkai quality" },
  { key: "2d-guoman", name: "2D国漫", emoji: "🎨", desc: "中国风 2D 国漫", prompt: "Chinese donghua 2D style, elegant flowing line art, ink-wash influenced coloring, ornate costume detail, cinematic composition" },
  { key: "2d-otome", name: "2D乙女", emoji: "💗", desc: "乙女向 2D 风格，柔美细腻", prompt: "otome game illustration style, soft pastel palette, delicate sparkle effects, slender elegant characters, romantic lighting" },
  { key: "2d-korean", name: "2D韩漫", emoji: "🇰🇷", desc: "韩式漫画风格", prompt: "Korean manhwa webtoon style, sharp digital line art, tall stylized proportions, high contrast shading, glossy color rendering" },
  { key: "2d-korean-urban", name: "2D韩漫都市", emoji: "🏙️", desc: "韩式都市漫画风", prompt: "modern Korean webtoon style, urban night scenery, neon reflections, fashionable characters, clean digital flat shading" },
  { key: "3d-animation", name: "3D卡通", emoji: "👾", desc: "3D 卡通 / 皮克斯风格", prompt: "high-quality 3D CGI animation, Pixar/DreamWorks style, subsurface scattering, detailed textures, stylized characters" },
  { key: "3d-xianxia", name: "3D仙侠", emoji: "🐉", desc: "3D 仙侠玄幻风，国风山水与法术特效", prompt: "3D xianxia fantasy CG style, flowing immortal robes, luminous spell VFX, floating mountains and sea of clouds, epic volumetric lighting" },
  { key: "3d-guofeng", name: "3D国风", emoji: "🏯", desc: "3D 中国古风场景", prompt: "3D Chinese historical rendering, carved wooden palaces, silk and lacquer detail, lantern light, ink-inspired atmosphere" },
  { key: "cyberpunk", name: "CG赛博朋克", emoji: "🌌", desc: "高科技赛博朋克风", prompt: "cyberpunk aesthetic, neon-lit, rain-soaked streets, holographic displays, high-tech low-life, Blade Runner style" },
  { key: "cg", name: "CG风格", emoji: "🖼️", desc: "CG 渲染艺术风格", prompt: "photoreal CG render, ray-traced global illumination, physically based materials, cinematic color grading, ultra detailed" },
  { key: "gongbi", name: "工笔画", emoji: "🖌️", desc: "传统工笔画艺术风", prompt: "traditional Chinese gongbi brush painting, meticulous mineral pigment layers, silk texture, refined outlines, restrained elegant palette" },
  { key: "live-action", name: "写实电影感", emoji: "🎬", desc: "超写实电影/电视剧风格", prompt: "photorealistic, cinematic film quality, real human actors, professional cinematography, natural lighting, 8K resolution" },
  { key: "realistic-urban", name: "写实都市", emoji: "🌆", desc: "写实都市场景", prompt: "realistic contemporary urban photography, natural daylight, candid framing, detailed street texture, shallow depth of field" },
  { key: "realistic", name: "写实通用", emoji: "🎞️", desc: "通用写实风格", prompt: "photorealistic, natural lighting, physically accurate materials, fine detail, professional photography" },
  { key: "oil-painting", name: "油画风格", emoji: "🖼️", desc: "油画笔触与古典构图", prompt: "oil painting style, visible brushstrokes, rich textures, classical art composition, museum quality fine art" },
  { key: "custom", name: "其他（自定义）", emoji: "✨", desc: "手动输入风格", prompt: "" },
];

const STYLE_BY_KEY = new Map(VISUAL_STYLES.map((s) => [s.key, s]));

/** 自定义风格直接当提示词用，不再套模板 */
export function stylePrompt(key: string, custom = ""): string {
  const hit = STYLE_BY_KEY.get(key);
  if (hit && hit.prompt) return hit.prompt;
  return custom || key || VISUAL_STYLES[11].prompt;
}

/* ───────── 目标时长 / 语言 / 画幅 ───────── */

export const DURATION_OPTIONS = [
  { label: "30秒 (广告)", value: 30 },
  { label: "60秒 (预告)", value: 60 },
  { label: "2分钟 (片花)", value: 120 },
  { label: "5分钟 (短片)", value: 300 },
  { label: "15分钟 (长剧/单集)", value: 900 },
  { label: "自定义", value: 0 },
];

export const LANGUAGE_OPTIONS = [
  { label: "中文 (Chinese)", value: "中文" },
  { label: "English (US)", value: "English" },
  { label: "日本語 (Japanese)", value: "Japanese" },
  { label: "Français (French)", value: "French" },
  { label: "Español (Spanish)", value: "Spanish" },
];

/** 与后端 builtin_graphs 的 17k+5 公式一致，改一处必须改两处 */
export function h3FrameCount(seconds: number, fps = 24): number {
  const n = Math.max(5, Math.round(seconds * fps));
  if (n <= 5) return 5;
  return 17 * Math.ceil((n - 5) / 17) + 5;
}

export function framesToSeconds(frames: number, fps = 24): number {
  return Math.round((frames / fps) * 10) / 10;
}

/** 出图尺寸。Qwen-Image 走 16 的倍数，H3 走官方推荐档 */
export const IMAGE_SIZES: Record<AspectRatio, { width: number; height: number }> = {
  "16:9": { width: 1344, height: 768 },
  "9:16": { width: 768, height: 1344 },
  "1:1": { width: 1024, height: 1024 },
};

export const H3_SIZES: Record<AspectRatio, { preview: [number, number]; full: [number, number] }> = {
  "16:9": { preview: [864, 480], full: [1344, 768] },
  "9:16": { preview: [480, 864], full: [768, 1344] },
  "1:1": { preview: [640, 640], full: [960, 960] },
};

/* ───────── 运镜构图指引 ───────── */

export interface MovementGuide {
  start: string;
  end: string;
}

export const CAMERA_MOVEMENT_GUIDES: Record<string, MovementGuide> = {
  "horizontal left shot": { start: "Composition: Subject positioned on the right side of frame, with space on the left for movement.", end: "Composition: Subject moved to left side of frame, showing the journey from right to left." },
  "horizontal right shot": { start: "Composition: Subject positioned on the left side of frame, with space on the right for movement.", end: "Composition: Subject moved to right side of frame, showing the journey from left to right." },
  "pan left shot": { start: "Composition: Frame focused on right portion of scene, anticipating leftward pan.", end: "Composition: Frame reveals left portion of scene, completing the pan movement." },
  "pan right shot": { start: "Composition: Frame focused on left portion of scene, anticipating rightward pan.", end: "Composition: Frame reveals right portion of scene, completing the pan movement." },
  "zoom in shot": { start: "Composition: Wide establishing shot showing full scene context, subject smaller in frame.", end: "Composition: Tight close-up on subject, filling frame with detail and intimacy." },
  "zoom out shot": { start: "Composition: Close-up on subject, emphasizing details and emotion.", end: "Composition: Wide pullback revealing surrounding environment and context." },
  "dolly shot": { start: "Composition: Initial framing with subject at specific distance and perspective.", end: "Composition: Changed perspective with subject closer/further, revealing depth and space." },
  "tilt up shot": { start: "Composition: Camera angle pointing downward or level, capturing lower portion of subject.", end: "Composition: Camera tilted upward, revealing height and vertical expanse above." },
  "tilt down shot": { start: "Composition: Camera angle pointing upward or level, emphasizing upper portion.", end: "Composition: Camera tilted downward, revealing lower elements and ground level." },
  "vertical up shot": { start: "Composition: Lower vertical position, subject at bottom of frame or ground level.", end: "Composition: Elevated position, subject risen vertically showing upward movement." },
  "vertical down shot": { start: "Composition: Elevated vertical position, subject higher in frame.", end: "Composition: Lower position, subject descended showing downward movement." },
  "tracking shot": { start: "Composition: Subject in frame with forward/lateral space for tracking movement.", end: "Composition: Subject tracked through space, maintaining visual relationship." },
  "circular shot": { start: "Composition: Subject centered, camera at initial angle of circular path.", end: "Composition: Subject still centered, camera at opposite side revealing new angle." },
  "360-degree circular shot": { start: "Composition: Subject centered, camera beginning 360° orbit.", end: "Composition: Subject centered, camera completing full revolution from different angle." },
  "low angle shot": { start: "Composition: Low camera angle looking upward, emphasizing height and power.", end: "Composition: Maintained low angle, subject towering with dramatic perspective." },
  "high angle shot": { start: "Composition: High camera angle looking downward, creating overview perspective.", end: "Composition: Maintained high angle, emphasizing scale and spatial relationships." },
  "bird's eye view shot": { start: "Composition: Directly overhead view, showing layout and patterns from above.", end: "Composition: Continued overhead perspective, revealing changed spatial arrangement." },
  "pov shot": { start: "Composition: First-person perspective from character's viewpoint.", end: "Composition: Maintained POV, showing what character sees after movement/action." },
  "over the shoulder shot": { start: "Composition: Frame includes foreground character's shoulder, looking at subject.", end: "Composition: Maintained over-shoulder framing, possibly with shifted focus or angle." },
  "handheld shot": { start: "Composition: Dynamic handheld framing with natural movement and energy.", end: "Composition: Continued handheld aesthetic with organic repositioning." },
  "static shot": { start: "Composition: Fixed camera position, stable framing throughout.", end: "Composition: Same camera position, only subject movement within frame." },
  "rotating shot": { start: "Composition: Subject in frame, camera beginning rotational movement.", end: "Composition: Subject with changed orientation due to camera rotation." },
  "slow motion shot": { start: "Composition: Action captured at beginning of slow-motion sequence.", end: "Composition: Action progressed, emphasizing graceful movement detail." },
  "parallel tracking shot": { start: "Composition: Subject with camera tracking parallel alongside.", end: "Composition: Maintained parallel relationship, subject moved through space." },
  "diagonal tracking shot": { start: "Composition: Subject with camera on diagonal tracking path.", end: "Composition: Diagonal perspective maintained, dynamic spatial progression." },
  "canted shot": { start: "Composition: Tilted horizon line creating dutch angle, dynamic unease.", end: "Composition: Maintained or adjusted dutch angle, emphasizing disorientation." },
  "cinematic dolly zoom": { start: "Composition: Initial balanced framing before vertigo effect.", end: "Composition: Distorted perspective with foreground/background relationship altered." },
};

/** 中文运镜名 → 英文 key。分镜模型给的是中文枚举，构图指引表是英文的 */
const CAMERA_ALIASES: Record<string, string> = {
  固定: "static shot",
  推近: "zoom in shot",
  拉远: "zoom out shot",
  横移: "horizontal right shot",
  跟随: "tracking shot",
  升降: "vertical up shot",
  手持: "handheld shot",
  摇镜: "pan right shot",
  环绕: "circular shot",
  俯拍: "high angle shot",
  仰拍: "low angle shot",
  过肩: "over the shoulder shot",
  第一人称: "pov shot",
  荷兰角: "canted shot",
  变焦推近: "cinematic dolly zoom",
};

export const CAMERA_MOVEMENTS = Object.keys(CAMERA_MOVEMENT_GUIDES);
export const CAMERA_MOVEMENTS_ZH = Object.keys(CAMERA_ALIASES);

export function cameraGuide(movement: string, frameType: "start" | "end"): string {
  const zh = CAMERA_ALIASES[movement.trim()];
  const needle = (zh || movement).toLowerCase().trim();
  const keys = Object.keys(CAMERA_MOVEMENT_GUIDES);
  const hit =
    keys.find((k) => k === needle) ||
    keys.find((k) => needle.includes(k) || (needle.length > 3 && k.includes(needle)));
  if (hit) return CAMERA_MOVEMENT_GUIDES[hit][frameType];
  return frameType === "start"
    ? "Composition: Initial frame composition suited for the camera movement."
    : "Composition: Final frame composition showing the result of camera movement.";
}

/** 提示词里写中文运镜名，同时带上英文构图指引 */
export function movementLabel(movement: string): string {
  const zh = CAMERA_ALIASES[movement.trim()];
  return zh ? `${movement} (${zh})` : movement;
}

/* ───────── H3 提示词模式与镜头语言词表 ───────── */

/**
 * 模式清单与后端 apps/api/app/director.py 的 MODES 同源（名字、什么时候用、耗时预期），
 * 改一处必须改两处。eta 是给人的预期管理：这台 27B 单槽串行，别让人以为点完就好。
 */
export interface H3ModeMeta {
  key: H3PromptMode;
  name: string;
  language: string;
  when: string;
  eta: string;
}

export const H3_PROMPT_MODES: H3ModeMeta[] = [
  { key: "three_field", name: "三段式（本机实测）", language: "中文为主", when: "快速批量出片、无强风格要求；单卡 24GB 的默认档", eta: "一镜约 20–60 秒" },
  { key: "six_section", name: "官方六段式", language: "英文（中文台词原样保留）", when: "需要身份锁定与保留关系核对：变身、换装、多角色同框", eta: "一镜约 1–3 分钟" },
  { key: "wenwu", name: "中文导演分镜块", language: "中文", when: "点名要导演级分镜：逐镜定时、表演与声音写在镜头里", eta: "一镜约 1–2 分钟" },
  { key: "hybrid", name: "六段外壳 × 导演级内容", language: "英文外壳 + 中文台词", when: "宣传片 / PV / 广告 / 强风格短片的精修档", eta: "一镜约 2–4 分钟" },
];

const MODE_BY_KEY = new Map(H3_PROMPT_MODES.map((m) => [m.key, m]));

export function h3ModeMeta(mode?: H3PromptMode): H3ModeMeta {
  return MODE_BY_KEY.get(mode ?? "three_field") ?? H3_PROMPT_MODES[0];
}

/** 六段式字段 → H3 文本里的段名（和 detailed_description 那串一起被模型读） */
const SIX_SECTIONS: [key: keyof H3Prompt, label: string][] = [
  ["subjectDefinitions", "subject_definitions"],
  ["summary", "summary"],
  ["retentionAnalysis", "retention_analysis"],
  ["detailedDescription", "detailed_description"],
  ["soundscape", "overall_soundscape"],
  ["music", "non_diegetic_music"],
];

/** 景别七档：中文强制，禁止 ECU/CU 这类英文缩写。与后端 director.SHOT_SIZES 同源 */
export const SHOT_SIZES_ZH = ["极端特写", "近景", "中近景", "中景", "中远景", "全景", "远景"] as const;

/* ───────── 角色 / 场景 / 关键帧 / 视频 四类提示词 ───────── */

const NEGATIVE_BASE =
  "低分辨率，低画质，肢体畸形，手指畸形，多余手指，画面过饱和，蜡像感，文字水印，字幕，logo，画面模糊，构图混乱";

/** 角色定妆：三段（外形 → 姿态与光 → 技术质量），与拍摄清单里演员表显示的一致 */
export function buildCharacterPrompt(char: Character, config: ProjectConfig): string {
  if (char.visualPrompt?.trim()) return char.visualPrompt.trim();
  const t = char.traits ?? {};
  const identity = [
    `Core Identity: ${[char.gender, char.age || t.age, char.desc].filter(Boolean).join("，")}`,
  ].join("，");
  const face = `Facial Features: ${t.signature || char.coreFeatures || "五官清晰、比例自然"}`;
  const hair = `Hairstyle: ${t.hair || "发型与时代相符"}`;
  const costume = `Clothing: ${t.costume || "服装符合身份与场景"}`;
  const palette = t.palette ? `Palette: ${t.palette}` : "";
  const pose = `Pose & Expression: 站立正面半身，双手自然，表情中性可复用，便于后续镜头保持一致`;
  const quality = `Technical Quality: ${stylePrompt(config.visualStyle)}`;
  return [identity, face, hair, [costume, palette].filter(Boolean).join("，"), pose, quality].filter(Boolean).join("，");
}

/** 场景概念图：Environment / Lighting / Composition / Visual Style 四段 */
export function buildScenePrompt(scene: Scene, config: ProjectConfig): string {
  if (scene.visualPrompt?.trim()) return scene.visualPrompt.trim();
  const env = `Environment: ${scene.location ? scene.location + "，" : ""}${scene.desc}`;
  const light = `Lighting: ${scene.time ? `${scene.time}时段的光线` : "自然光"}，${scene.atmosphere || "氛围与剧情一致"}`;
  const comp = `Composition: 眼平视角，广角环境参考图，留出人物活动空间，无人物或仅远景剪影`;
  const style = `Visual Style: ${stylePrompt(config.visualStyle)}`;
  return [env, light, comp, style].join("，");
}

/** 服装变体：以角色核心特征为底，只换服装与气质 */
export function buildVariationPrompt(char: Character, variation: Variation, config: ProjectConfig): string {
  if (variation.visualPrompt?.trim()) return variation.visualPrompt.trim();
  const base = buildCharacterPrompt(char, config);
  const t = char.traits ?? {};
  const keep = `必须保持与参考图完全一致：${[t.signature, t.hair].filter(Boolean).join("、") || "面部特征与发型"}`;
  return [base.replace(/Clothing: [^，]*/, `Clothing: ${variation.desc || variation.name}`), `【变体】${variation.name}`, keep].join("，");
}

export const CONSISTENCY_GUIDE = `【角色一致性要求】CRITICAL
⚠️ 画面中的人物外观必须严格遵循参考图：
• 面部特征：五官轮廓、眼睛颜色和形状、鼻子与嘴的结构完全一致
• 发型发色：长度、颜色、质感、发式保持一致
• 服装造型：款式、颜色、材质、配饰与参考图匹配
• 体型特征：身材比例与身高保持一致
⚠️ 这是最高优先级要求，不可妥协。`;

/** 关键帧三段式：画面动作 → 视觉风格 → 镜头运动与构图 → 首/尾帧要求 → 一致性 */
export function buildKeyframePrompt(args: {
  base: string;
  visualStyle: string;
  cameraMovement: string;
  frameType: "start" | "end";
  withConsistency?: boolean;
}): string {
  const { base, visualStyle, cameraMovement, frameType } = args;
  const frameGuide =
    frameType === "start"
      ? "【起始帧要求】建立清晰的初始状态与场景氛围，人物/物体的起始位置、姿态和表情明确，为后续运动预留视觉空间和动势。"
      : "【结束帧要求】展现动作完成后的最终状态，人物/物体的终点位置、姿态与情绪变化，体现镜头运动带来的视角变化。";
  const parts = [
    base.trim(),
    "━━━━━━━━━━━━━━━━━━━━━━━━━━",
    `【视觉风格】Visual Style\n${stylePrompt(visualStyle)}`,
    "━━━━━━━━━━━━━━━━━━━━━━━━━━",
    `【镜头运动】Camera Movement\n${movementLabel(cameraMovement)}（${frameType === "start" ? "Initial Frame 起始帧" : "Final Frame 结束帧"}）`,
    `【构图指导】Composition Guide\n${cameraGuide(cameraMovement, frameType)}`,
    frameGuide,
  ];
  if (args.withConsistency !== false) {
    parts.push("━━━━━━━━━━━━━━━━━━━━━━━━━━", CONSISTENCY_GUIDE);
  }
  return parts.join("\n\n");
}

/** 起始帧是唯一真正送进 H3 的参考图，素材契约只声明它，不吹别的 */
function hasStartFrame(shot: Shot): boolean {
  return !!(shot.keyframes?.find((k) => k.type === "start")?.mediaId ?? shot.startFrameMediaId);
}

function pictureContract(shot: Shot, chars: Character[]): string {
  if (!hasStartFrame(shot)) return "";
  const who = chars.map((c) => c.name).filter(Boolean).join("、");
  return `<Picture 1> 是本镜起始帧${who ? `（${who}）` : ""}：身份、脸型、发型、服装、材质与场景光线都以它为准，不得改写。`;
}

/** 角色身份行：六段式的 subject_definitions 与 wenwu 的生命核都从这里长出来 */
function castLines(chars: Character[]): string {
  return chars
    .map((c, i) => {
      const t = c.traits ?? {};
      const bits = [t.age || c.age, t.build, t.hair, t.costume, t.palette, t.signature || c.coreFeatures].filter(Boolean).join("，");
      return `<Subject ${i + 1}> ${c.name}：${bits || c.desc || "身份以起始帧为准"}`;
    })
    .join("\n");
}

function sceneLine(scene?: Scene): string {
  return scene ? `场景：${scene.location || scene.name}${scene.time ? `（${scene.time}）` : ""}` : "";
}

/**
 * 衔接锚点：这一镜从上一镜接住什么。
 * 它是**文字接续**，和 continuesPrevious 的尾帧接续各走各的 —— 勾了续拍也要写，
 * 因为接过来的不只是画面，还有动作方向、视线、受力和声音。首镜或没填就不出这一句。
 */
function anchorOf(shot: Shot): string {
  const a = shot.continuityAnchor?.trim();
  if (!a || /^n\/?a$/i.test(a)) return "";
  return a;
}

function text(p: H3Prompt, key: string): string {
  const v = (p as unknown as Record<string, unknown>)[key];
  return typeof v === "string" ? v.trim() : "";
}

/**
 * 本地拼一份 H3 提示词（不叫模型，纯模板）。
 *
 * config 决定模式、画幅与风格；没传 config 就按三段式拼，跟旧行为一致。
 * 深度不够是预期的：本地模板只保证「形状正确、能出片」，导演级内容靠 h3_prompt 用途让模型重写。
 */
export function buildH3Prompt(shot: Shot, scene?: Scene, chars: Character[] = [], config?: ProjectConfig): H3Prompt {
  const mode: H3PromptMode = config?.h3PromptMode ?? "three_field";
  const seconds = Math.max(1, Math.round((shot.durationSec || 5) * 100) / 100);
  const aspect: AspectRatio = config?.aspectRatio ?? "16:9";
  const style = stylePrompt(config?.visualStyle ?? "");
  const who = chars.map((c) => c.name).filter(Boolean).join("、");
  const contract = pictureContract(shot, chars);
  const cam = `${shot.shotSize || "中景"}，${movementLabel(shot.cameraMovement || "固定")}`;
  const gist = (shot.action || scene?.name || "按剧本推进本镜事件").replace(/\s+/g, " ").slice(0, 60);
  const say = shot.dialogue?.trim();
  const anchor = anchorOf(shot);

  const base = [
    shot.action?.trim(),
    say ? `台词：${say}` : "",
    `镜头：${cam}`,
    sceneLine(scene),
    who ? `出场：${who}` : "",
    anchor ? `接上一镜：${anchor}` : "",
  ]
    .filter(Boolean)
    .join("；");

  // 无 BGM 时必须写 N/A，否则 H3 会自己配一段音乐盖掉原生对白
  const soundscape = `${scene?.atmosphere || "环境声贴合场景"}${say ? "；本镜原生对白直出，口型与音节对齐" : ""}`;

  if (mode === "three_field") {
    return { mode, integrated: base, soundscape, music: "N/A" };
  }

  if (mode === "wenwu") {
    const block = [
      `镜头1（0-${seconds}s）：${cam}`,
      `    摄像机状态：${cameraGuide(shot.cameraMovement || "固定", "start")}`,
      anchor ? `    与上一镜的接续：${anchor}` : "",
      `    画面内容：${shot.action || "本镜唯一事件按剧本推进"}${contract ? `；${contract}` : ""}`,
      `    音频：${soundscape}`,
      `    人物台词（清晰口语）：${say ? `"${say}"` : "无"}`,
    ]
      .filter(Boolean)
      .join("\n");
    return {
      mode,
      integrated: gist,
      sceneDescription: `本片 ${seconds} 秒，画幅 ${aspect}，${style}。生命核：${who || "本镜主体"}${scene ? `，身处${scene.location || scene.name}` : ""}。${contract}最终发展线：${shot.action || "本镜完成一次可见变化"}`,
      shotBlocks: [block],
      soundscape,
      music: "N/A",
    };
  }

  // six_section / hybrid
  const pulse = `[Shot 1] 0.00-${seconds.toFixed(2)}s. ${cam}. ${shot.action || "本镜唯一事件按剧本推进"}.${say ? ` The character (S1) says: <d>[Chinese]${say}</d> Lips, jaw and facial muscles move in sync with every syllable.` : ""}${anchor ? ` 本镜开场接住上一镜留下的：${anchor}。` : ""} 主体在镜内发生可见变化，摄影机按上述运镜回应，镜末留下被看见的结果。${seconds > 6 ? "Do not render this as one continuous static shot; place internal beats inside this shot with the time codes above." : "Keep this as one continuous take."}`;
  const retention = contract
    ? "<Subject 1> identity, face, hairstyle, costume, prop ownership and scene lighting are fully_preserved from <Picture 1>; only the action and camera change."
    : "No reference image supplied: identity must stay consistent across every frame of this shot; no wardrobe or prop drift.";
  const six: H3Prompt = {
    mode,
    integrated: gist,
    subjectDefinitions: [castLines(chars), contract, sceneLine(scene) && `<Setting> ${sceneLine(scene).replace("场景：", "")}`]
      .filter((l) => l && l.trim())
      .join("\n"),
    summary: `${seconds} 秒 ${aspect} 的一镜：${shot.action || "本镜事件"}${say ? `；台词「${say}」` : ""}。开头状态→本镜变化→镜末结果。`,
    retentionAnalysis: retention,
    detailedDescription: pulse,
    soundscape,
    music: "N/A",
  };
  if (mode === "hybrid") {
    six.constraints = `${style}, no readable text, no subtitles, no watermark, no extra characters, no identity drift, no wardrobe change`;
  }
  return six;
}

/** 这一镜的结构化字段是否真的按它的模式填齐了（没填齐就别硬拼给模型） */
export function h3PromptIncomplete(p: H3Prompt): string | null {
  const mode = p.mode ?? "three_field";
  if (mode === "three_field") return p.integrated?.trim() ? null : "三段式缺 integrated";
  if (mode === "wenwu") {
    if (!p.sceneDescription?.trim()) return "缺 sceneDescription（开篇时长/类型/生命核/素材职责）";
    if (!p.shotBlocks?.length) return "缺 shotBlocks（逐镜定时块）";
    return null;
  }
  const miss = SIX_SECTIONS.filter(([k]) => !text(p, k)).map(([, label]) => label);
  if (mode === "hybrid" && !p.constraints?.trim()) miss.push("constraints");
  return miss.length ? `缺 ${miss.join("、")}` : null;
}

/** 拼成 H3 节点实际吃的那一长串文本。模式不齐的内容会退回三段式，绝不交空提示词 */
export function h3PromptText(p: H3Prompt): string {
  const mode = p.mode ?? "three_field";
  if ((mode === "six_section" || mode === "hybrid") && !h3PromptIncomplete(p)) {
    const body = SIX_SECTIONS.map(([k, label]) => `${label}:\n${text(p, k)}`).join("\n\n");
    return mode === "hybrid" ? `${body}\n\nconstraints:\n${p.constraints?.trim()}` : body;
  }
  if (mode === "wenwu" && !h3PromptIncomplete(p)) {
    return [
      `一、整体场景描述\n${p.sceneDescription?.trim()}`,
      `二、分定时镜头+摄影+音频完整提示块\n${(p.shotBlocks ?? []).map((b) => b.trim()).join("\n")}`,
      `三、声音与配乐\noverall_soundscape: ${p.soundscape.trim() || "环境声贴合场景"}\nnon_diegetic_music: ${p.music.trim() || "N/A"}`,
    ].join("\n\n");
  }
  return [`integrated_multimodal_description: ${p.integrated}`, `overall_soundscape: ${p.soundscape}`, `non_diegetic_music: ${p.music}`].join("\n");
}

export function negativeFor(kind: "character" | "scene" | "keyframe", custom?: string): string {
  if (custom?.trim()) return custom.trim();
  if (kind === "scene") return `${NEGATIVE_BASE}，画面文字，人物面部特写`;
  return NEGATIVE_BASE;
}

/* ───────── 镜头编号 ───────── */

/**
 * 'shot-1' → 001；'shot-1-2' → 001-2（AI 拆分出来的子镜）。
 * id 的分隔符在几处实现里不统一（shot-1 / shot_1 / uid("s") 生成的随机串），
 * 所以这里只提数字段，并且优先用调用方传的 index —— 编号是镜头的属性，不是 id 的属性。
 */
export function shotLabel(id: string, index?: number): string {
  const nums = String(id || "").split(/[-_]/).filter((p) => /^\d+$/.test(p));
  const base = index ?? Number(nums[0]);
  const suffix = nums.length > 1 ? `-${nums.slice(1).join("-")}` : "";
  return Number.isFinite(base) ? `${String(base).padStart(3, "0")}${suffix}` : `000${suffix}`;
}

export function subShotIds(originalId: string, count: number): string[] {
  return Array.from({ length: count }, (_, i) => `${originalId}-${i + 1}`);
}

/** 从已拼好的关键帧提示词里取回「画面主体」那一段，供重新拼装 */
export function extractBasePrompt(full: string, fallback: string): string {
  const idx = full.indexOf("\n\n━━━━━━━━━━");
  const cut = idx > 0 ? full.slice(0, idx) : full;
  return cut.trim() || fallback;
}
