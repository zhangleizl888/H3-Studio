/**
 * 领域类型 —— 与 PLAN.md §4 数据模型一一对应。
 * 后端尚未存在，先以这份契约驱动前端与 mock；实现后端时以此为唯一真源。
 */

/* ───────── 生成实例 ───────── */

/** 分派依据：协议，而不是服务商 */
export type GenProtocol = "comfy_native" | "rh_task";

/** 仅用于 UI 分组与筛选，不参与分派逻辑 */
export type Placement = "local" | "cloud_self" | "cloud_runninghub";

export type RhSite = "cn" | "global";
export type RhInstanceType = "default" | "plus" | "ultra"; // 24G / 48G / 84G

export interface GenInstance {
  id: string;
  name: string;
  protocol: GenProtocol;
  placement: Placement;
  /** 后端返回给前端时，RunningHub 的 apiKey 已从 URL 中脱敏 */
  baseUrl: string;
  wsUrl?: string | null;
  apiKeySet?: boolean;
  site?: RhSite;
  instanceType?: RhInstanceType;
  retainSeconds?: number | null;
  isDefault: boolean;
  /** 仅本地：ComfyUI output 目录，可直接读盘省一次下载 */
  localOutputRoot?: string | null;
  tunnelName?: string | null;
  capabilities?: InstanceCaps;
  quota?: RhQuota;
  cost?: CostSummary;
  lastProbeAt?: string | null;
  lastProbeOk?: boolean | null;
  lastError?: string | null;
  /** 该实例当前是否有任务在跑（前端用于串行显示） */
  busy?: boolean;
}

export interface InstanceCaps {
  comfyVersion?: string;
  nodeCount?: number;
  vramTotalGb?: number;
  vramFreeGb?: number;
  gpu?: string;
  h3?: {
    MiniMaxH3ImageToVideo?: boolean;
    MiniMaxH3SigmaShift?: boolean;
    MiniMaxH3ReferenceToVideo?: boolean;
    MiniMaxH3AddGuide?: boolean;
  };
  qwenImage?: boolean;
  missingModels?: string[];
}

export interface RhQuota {
  apiKeyType?: "consumer" | "enterprise_shared" | "enterprise_exclusive";
  concurrentLimit?: number;
  runningCount?: number;
  queuedCount?: number;
  remainCoins?: number;
  remainMoney?: number;
  currency?: string;
}

export interface CostSummary {
  coins?: number;
  money?: number;
  gpuSeconds?: number;
  tasks?: number;
}

/* ───────── 文本模型后端 ───────── */

export type LlmScope = "local" | "cloud";
export type LlmKind = "ollama" | "openai_compat";
export type StreamStyle = "ndjson" | "sse";

/** 自动发现时判别出的后端种类 */
export type DetectedBackend = "ollama" | "llamacpp" | "lmstudio" | "vllm" | "unknown";

export interface LlmBackend {
  id: string;
  name: string;
  scope: LlmScope;
  kind: LlmKind;
  baseUrl: string;
  apiKeySet?: boolean;
  chatPath: string;
  streamStyle: StreamStyle;
  capabilities: LlmCaps;
  isDefault: boolean;
  lastProbeAt?: string | null;
  lastProbeOk?: boolean | null;
  lastError?: string | null;
}

export interface LlmCaps {
  models: string[];
  /** 探测到的实际上下文上限 */
  ctxSize?: number | null;
  hasJsonSchema: boolean;
  hasVision: boolean;
  hasTools: boolean;
  /** Ollama 可每请求传 num_ctx；llama.cpp 的 -c 是启动参数，改不了 */
  ctxIsPerRequest: boolean;
  /** 模型能否由本后端拉取（Ollama 有 /api/pull，llama.cpp 没有） */
  supportsPull: boolean;
  enterpriseSharedOnly?: boolean;
}

export interface LocalScanEntry {
  port: number;
  detectedAs: DetectedBackend;
  ok: boolean;
  baseUrl?: string;
  name?: string;
  models?: number;
  ctxSize?: number | null;
  detail?: string;
}

export interface LocalScanResult {
  found: LocalScanEntry[];
  /** 全空时给出各家启动命令，而不是只报「连接失败」 */
  hints: { backend: DetectedBackend; label: string; command: string; note?: string }[];
}

/* ───────── 工作流 ───────── */

export type WorkflowFamily = "image" | "video" | "audio" | "upscale" | "custom";

export interface WorkflowSlot {
  /** 地址语法：nodeId.inputName / nodeId.index / parent/child.inputName / *:inputName */
  address: string;
  path?: string;
  name: string;
  type: "string" | "int" | "float" | "bool" | "combo" | "image" | "video" | "audio";
  default?: unknown;
  widget: boolean;
  required: boolean;
  group: "输入" | "采样" | "输出";
  min?: number | null;
  max?: number | null;
  step?: number | null;
  options?: string[] | null;
  /** RunningHub 执行时投影成 nodeInfoList 的 fieldName */
  rhFieldName?: string;
}

/** 工作流的一条「任务信号」：后端导入时从图上读出来的可填输入 */
export interface WorkflowSignal {
  name: string;
  label: string;
  type: "text" | "int" | "float" | "bool" | "image" | "video" | "audio";
  addresses: string[];
  /** 同名参数在别处也有（如图片分支的提示词），填槽时不碰它们 */
  also?: string[];
  required: boolean;
  /** 一组素材位（参考图可以有 N 张） */
  many?: boolean;
  value?: unknown;
  options?: string[] | null;
  min?: number | null;
  max?: number | null;
}

/** 本机跑不动这条工作流的原因（缺节点包 / 缺权重） */
export interface WorkflowGap {
  node: string;
  class_type: string;
  reason: string;
  pack?: string;
}

/** 导入时对本机不可用节点的等价改写记录，逐条可审计 */
export interface WorkflowAdaptation {
  node: string;
  action: string;
  from: string;
  to: string;
  detail: string;
}

export interface Workflow {
  id: string;
  name: string;
  description?: string;
  tags: string[];
  family: WorkflowFamily;
  /** 按产出判的任务种类，自动选工作流的第一层过滤 */
  taskKind?: WorkflowFamily;
  /** 这条能在哪种实例上跑 */
  executesOn?: "local" | "cloud_runninghub" | "any";
  signals?: WorkflowSignal[];
  gaps?: WorkflowGap[];
  adaptations?: WorkflowAdaptation[];
  /** 指向作者机器素材的控件：使用时必须由前端重新指定，不算缺东西 */
  pendingMedia?: string[];
  autoSelect?: boolean;
  priority?: number;
  verifiedAt?: string | null;
  nodeCount?: number;
  slotCount?: number;
  sourceFormat: "api" | "ui";
  graph: Record<string, GraphNode>;
  slots: WorkflowSlot[];
  nodeSummary?: Record<string, string[]>;
  requirements?: {
    models?: { folder: string; filename: string }[];
    customNodes?: string[];
    pip?: string[];
    /** 只能在 RunningHub 上跑的专有节点 */
    runninghubOnly?: string[];
    /** 图上用 ResolutionSelector 算出来的真实尺寸，参数表按它判显存 */
    resolution?: { aspect_ratio: string; megapixels: number; width: number; height: number; mp: number } | null;
  };
  isBuiltin: boolean;
  objectInfoHash?: string | null;
  updatedAt: string;
}

export interface GraphNode {
  class_type: string;
  inputs: Record<string, unknown>;
  _meta?: { title?: string };
}

/** RunningHub 的入参覆盖形状 */
export interface NodeOverride {
  nodeId: string;
  fieldName: string;
  fieldValue: unknown;
}

/**
 * 一条工作流（或内置模板）上一个可以换的权重位。
 *
 * 清单一律由目标实例的 /object_info 回答 —— 这台的权重目录与命名和官方文档不一致是常态，
 * 前端列什么用户就能选什么，服务端绝不就近凑一个。
 */
export interface ModelSlot {
  /** "节点号.字段名"，就是任务里 models 的键 */
  key: string;
  node: string;
  field: string;
  classType: string;
  label: string;
  /** 底模 / 一体化模型 / 文本编码器 / VAE / LoRA 适配器 */
  role: string;
  current: string;
  options: string[];
  /** 图里写的那个文件名在这台实例上不存在 */
  missing: boolean;
}

export interface WorkflowModelOptions {
  workflowId: string;
  workflowName: string;
  instanceId: string;
  placement: string;
  protocol: string;
  /** 下拉里默认展开的那一位（通常是底模） */
  primary: string | null;
  slots: ModelSlot[];
  notes: string[];
}

/* ───────── 生成选择（每条资产/镜头各自一份） ───────── */

/**
 * 「这次用什么工作流、在哪台机器上、换哪颗权重」。
 *
 * 挂在角色/服装变体/场景/音色/镜头上，缺省回落项目配置 —— 同一部片里角色走 Klein、
 * 场景走 Qwen-Image、某几个镜头走云端，是常态而不是例外。
 */
export interface GenPreset {
  /** 与 config.imageTemplate 同一套写法："auto" / 内置模板名 / 库工作流的数字 id；留空=跟随项目 */
  workflow?: string | null;
  /** 生成实例 id；留空=跟随项目默认 */
  instanceId?: string | null;
  /** 模型覆盖：键取 ModelSlot.key，值必须是该实例报出来的文件名 */
  models?: Record<string, string>;
}

/**
 * 角色的音色。
 *
 * 本机出音色只有工作流库那一条路（audio 没有内置回落模板），所以这一份选择是必须的：
 * 参考音频要落到服务端媒体库才可能被实例读走。
 */
export interface VoiceProfile {
  /** 参考音频（克隆底子）：服务端 media id 才算数 */
  refAudioIds: string[];
  /** 克隆出来的试听样本 */
  sampleMediaIds: string[];
  /** 一句话音色描述：非克隆类工作流用它当提示词 */
  timbre?: string;
  /** 试念的文本 */
  testText?: string;
  language?: string;
  preset?: GenPreset;
  status?: AssetState;
}

/* ───────── 任务 ───────── */

export type JobState = "queued" | "dispatching" | "running" | "succeeded" | "failed" | "canceled";
export type JobKind =
  | "llm_chat"
  | "image"
  | "video"
  | "video_chain"
  | "upscale"
  | "detect_shots"
  | "assemble"
  | "workflow_test"
  | "audio";

export interface JobProgress {
  value?: number;
  max?: number;
  node?: string | null;
  nodeTitle?: string | null;
  stage?: string | null;
  etaSec?: number | null;
  /** RunningHub Task API 不给百分比 */
  unavailable?: boolean;
}

export interface Job {
  id: string;
  projectId?: string | null;
  kind: JobKind;
  state: JobState;
  priority: number;
  title: string;
  instanceId?: string | null;
  llmBackendId?: string | null;
  workflowId?: string | null;
  /** 后端按任务自动选中的那条工作流：用户要能看出「用的哪条、凭什么是它」 */
  workflowName?: string | null;
  chosenBy?: string | null;
  /** 填图说明：哪张素材接到了哪个加载器、哪个分支被撤掉 */
  fillNotes?: string[];
  /** 换权重的结果（"底模 #127 ← xxx.safetensors"）。选了却没用上，这里就看得穿 */
  modelNotes?: string[];
  promptId?: string | null;
  progress: JobProgress;
  queuePos?: number | null;
  attempts: number;
  error?: JobError | null;
  outputMediaIds: string[];
  log: { ts: string; level: string; msg: string }[];
  cost?: { coins?: number; money?: number; seconds?: number };
  startedAt?: string | null;
  finishedAt?: string | null;
}

export interface JobError {
  type: string;
  message: string;
  nodeId?: string | null;
  nodeType?: string | null;
  /** RunningHub 的 failedReason 会带原始 ComfyUI traceback */
  tracebackTail?: string | null;
  /** 面向人的处置建议，由后端映射错误码生成 */
  hint?: string | null;
}

/* ───────── 媒体 ───────── */

export type MediaKind = "image" | "video" | "audio" | "archive" | "ref_image" | "ref_video" | "ref_audio";
export type MediaRole =
  | "character"
  | "variation"
  | "scene"
  | "keyframe_start"
  | "keyframe_end"
  | "video"
  | "thumbnail"
  | "export"
  | "final";

export interface Media {
  id: string;
  projectId?: string | null;
  kind: MediaKind;
  role: MediaRole;
  refId?: string | null;
  /** 相对 data/media/ 的路径。后端绝不存外链 */
  path: string;
  thumbPath?: string | null;
  width?: number | null;
  height?: number | null;
  fps?: number | null;
  durationMs?: number | null;
  bytes?: number | null;
  mime?: string;
  instanceId?: string | null;
  promptId?: string | null;
  seed?: number | null;
  createdAt: string;
  /** 下面几个只在 /api/media-versions 与 /api/trash 上有值：/api/media 给的是"活行"，没有版本概念 */
  version?: number | null;
  versionCount?: number | null;
  /** 服务端真正用来分区的那把键：ref_id 为空时是 jobs/<任务目录>。客户端分组照它，别自己再算一遍 */
  groupValue?: string | null;
  /** 软删（进了生成回收站）的时刻。有值就意味着取 blob 必须带 ?trashed=1 */
  deletedAt?: string | null;
  purgeAfter?: string | null;
  daysLeft?: number | null;
  /** 这条按哪条保留期算的回收时间。后端把它和 purgeAfter 一起给，界面不许自己写死 100 */
  retentionDays?: number | null;
  /** 后端从 job.title 带回来的人类可读名（「定妆 · 林溪」「镜 3 首帧」） */
  title?: string | null;
}

/** 版本历史的三个分类，也就是生成历史页的那三个 tab */
export type VersionBucket = "script" | "image" | "video";

export type ScriptVersionSource = "ai-write" | "storyboard" | "manual";

/** 一版剧本正文。服务端真源是 script_versions 表 */
export interface ScriptVersionRow {
  id: string;
  uuid: string;
  projectKey: string;
  version: number;
  versionCount: number;
  source: ScriptVersionSource;
  text: string;
  /** 拆解结果快照。纯续写没有，所以是空对象而不是 null */
  snapshot: { script?: ScriptData; characters?: Character[]; scenes?: Scene[]; shots?: Shot[] };
  isCurrent: boolean;
  deletedAt: string | null;
  /** 入库时间（审计）。显示一律用 writtenAt */
  createdAt: string;
  /** 这版正文「何时写就」。补存的 V1 用的是编辑器上次保存的时间，不是生成时间 */
  writtenAt: string;
  /** createdAt 明显晚于 writtenAt ⇒ 这是补档，界面要标出来，别让它冒充生成产物 */
  backfilled: boolean;
  purgeAfter: string | null;
  daysLeft: number | null;
  retentionDays: number | null;
}

/** 生成回收站里的一条（媒体版本或剧本版本混在一张表里） */
export interface TrashItem {
  key: string;
  kind: "media" | "script";
  bucket: VersionBucket;
  id: string;
  uuid: string;
  projectKey: string | null;
  projectName?: string | null;
  role: string | null;
  refId: string | null;
  title: string | null;
  version: number;
  versionCount: number;
  bytes: number | null;
  url: string | null;
  deletedAt: string | null;
  createdAt: string;
  writtenAt?: string | null;
  purgeAfter: string | null;
  daysLeft: number | null;
  retentionDays: number | null;
  media?: Media;
  textPreview?: string | null;
}

export interface TrashList {
  retentionDays: number;
  totalBytes: number;
  items: TrashItem[];
}

/* ───────── 项目 ───────── */

export type Stage = "script" | "manifest" | "assets" | "director" | "export" | "prompts";

/** 横竖屏。影响出图尺寸、出片分辨率与模型可选档，不改布局 */
export type AspectRatio = "16:9" | "9:16" | "1:1";

export interface Project {
  id: string;
  name: string;
  synopsis?: string;
  stage: Stage;
  config: ProjectConfig;
  data: ProjectData;
  ownerId?: string | null;
  updatedAt: string;
  createdAt: string;
  archived?: boolean;
}

export interface ProjectConfig {
  aspectRatio: AspectRatio;
  visualStyle: string;
  /** 目标时长（秒）：30/60/120/300/900/自定义。分镜拆解按它分配每镜时长 */
  targetDurationSec: number;
  /** 输出语言：中文 / English / 日本語 / Français / Español */
  outputLanguage: string;
  /** 默认出图/出视频实例 */
  imageInstanceId?: string | null;
  videoInstanceId?: string | null;
  /** 默认文本后端（剧本拆解 / 分镜 / 提示词改写共用） */
  llmBackendId?: string | null;
  /** 分镜生成用哪个后端的哪个模型；留空跟 llmBackendId */
  shotModelBackendId?: string | null;
  shotModel?: string | null;
  /** 生成模板 key（见后端 /api/workflows 的 builtin:*） */
  imageTemplate: string;
  videoTemplate: string;
  seedPolicy: "locked" | "random" | "increment";
  resolutionMode: "preview" | "full";
  h3WorkflowKey: string;
  /** H3 提示词模式：决定 buildH3Prompt 出哪种结构、AI 重写让模型按哪种规范写 */
  h3PromptMode: H3PromptMode;
  /** 为什么选这个模式。参考项目要求模式决策留痕，禁止静默走默认 */
  h3PromptReason?: string;
  continuity: boolean;
  continuityOverlapFrames: number;
  /** AI 增强提示词：本地模板拼完再让模型扩写一次 */
  enhancePrompts: boolean;
}

export interface ProjectData {
  rawScript: string;
  script?: ScriptData;
  characters: Character[];
  scenes: Scene[];
  shots: Shot[];
  renderLogs: RenderLog[];
  /** 剧本页悬浮助手的会话历史。留在项目里才能跟着导出导入走（A 方案） */
  scriptChats?: ScriptChatSession[];
  /** 当前正文对应服务端哪一版（script_versions.uuid）。手改之后它和那版的 text 不再相等，界面才说得出「本地已改，未存版」 */
  scriptVersionUuid?: string | null;
  /** 这版正文实际写就的时间。补存的 V1 靠它显示，拿入库时间冒充生成时间是另一种撒谎 */
  scriptWrittenAt?: string | null;
  /** 任务进行中的步骤描述：切页/刷新后能恢复「正在拆解第 2/3 场」这种提示 */
  taskStep?: string;
  taskError?: string;
  isParsingScript?: boolean;
}

export interface ScriptData {
  logline?: string;
  title?: string;
  genre?: string[];
  /** traits 由拆解回填，是拼一致性提示词的结构化外形 */
  characters: { name: string; desc: string; traits?: Character["traits"] }[];
  scenes: { name: string; desc: string }[];
  beats: { text: string; sceneName?: string }[];
  /** 按叙事顺序的故事段落，拍摄清单的「故事梗概」用它 */
  storyParagraphs?: { id: number; text: string; sceneRefId?: string }[];
}

/**
 * 剧本页悬浮助手的一轮对话。
 *
 * assistant 消息可能带一份完整改稿（scriptText）。它**不**自动进编辑器 ——
 * 整篇替换是不可逆的，必须等人在预览上点确认。
 */
export interface ScriptChatMessage {
  id: string;
  role: "user" | "assistant";
  /** user 这轮是创作者的指令；assistant 这轮是模型那句「改了什么」 */
  text: string;
  ts: string;
  latencyMs?: number;
  /** 模型回吐的完整正文。为空表示这轮只是问答 */
  scriptText?: string;
  /** 生成这条回复时编辑器正文的字数。写回前用它判断中间有没有人手改过 */
  baseChars?: number;
  outcome?: "applied" | "discarded";
  error?: string;
  /** 后端对这次回复的结构化自检（例如「只回了被改的那一段」） */
  warnings?: string[];
  /**
   * 上传解析出来的换稿提案。和 scriptText 一样：**确认前绝不进编辑器** ——
   * 读出来的东西可能是另一部戏，自动覆盖就是拿别人的稿子盖掉用户正在写的。
   * 存进会话是为了刷新/换页之后这份提案还在，不用重传一遍。
   */
  upload?: ParsedScript;
}

export interface ScriptChatSession {
  id: string;
  title: string;
  createdAt: string;
  updatedAt: string;
  messages: ScriptChatMessage[];
}

/** /api/parse-script 的返回：一份读出来的稿子，还没进编辑器 */
export interface ParsedScript {
  name: string;
  format: string;
  encoding: string | null;
  chars: number;
  lines: number;
  text: string;
  /** 后端如实报的取舍：只抽了正文、超过模型单次上限要分段改…… */
  notes: string[];
  overModelCap: boolean;
  modelCap: number;
}

/** 四类生成对象的统一状态。generating 却没有产物 = failed，见 localStores.reconcileShots */
export type AssetState = "pending" | "generating" | "completed" | "failed";

export interface Character {
  id: string;
  name: string;
  desc: string;
  gender?: string;
  age?: string;
  /** 性格与说话方式，写进演员表但不进画面提示词 */
  personality?: string;
  /** 结构化外形，用于拼一致性提示词 */
  traits?: { age?: string; build?: string; hair?: string; costume?: string; palette?: string; signature?: string };
  refMediaIds: string[];
  variations: Variation[];
  seed: number;
  locked: boolean;
  promptNote?: string;
  /** 用户手改过的画面/负向提示词；为空则按 traits 现拼 */
  visualPrompt?: string;
  negativePrompt?: string;
  /** 跨镜头必须一致的特征（脸、发型、标志物），拼提示词时单独成段 */
  coreFeatures?: string;
  /** 这个角色用哪条工作流/哪台实例/哪颗权重出定妆照；留空跟项目默认 */
  preset?: GenPreset;
  /** 音色：角色的另一半，配音与带口型的镜头都靠它 */
  voice?: VoiceProfile;
  status?: AssetState;
}

export interface Variation {
  id: string;
  name: string;
  desc: string;
  refMediaIds: string[];
  visualPrompt?: string;
  negativePrompt?: string;
  /** 换装这条路和普通出图不是同一个形状，允许单独挑工作流 */
  preset?: GenPreset;
  status?: AssetState;
}

export interface Scene {
  id: string;
  name: string;
  desc: string;
  /** 地点 / 时段 / 氛围三要素，对应「内廷-未央宫偏殿-日」 */
  location?: string;
  time?: string;
  atmosphere?: string;
  refMediaIds: string[];
  visualPrompt?: string;
  negativePrompt?: string;
  /** 场景概念图用哪条工作流/实例/权重：环境和人物本来就常常不是一个模型擅长 */
  preset?: GenPreset;
  status?: AssetState;
}

export type ShotState = "idle" | "queued" | "generating" | "completed" | "failed";

/** 关键帧：一镜的起始/结束画面，各自带提示词与产物 */export interface Keyframe {
  id: string;
  type: "start" | "end";
  visualPrompt: string;
  negativePrompt?: string;
  mediaId?: string | null;
  status: AssetState;
  jobId?: string | null;
}

export interface Shot {
  id: string;
  index: number;
  sceneId: string | null;
  characterIds: string[];
  /** 选用的服装变体 */
  variationByChar?: Record<string, string>;
  action: string;
  dialogue?: string;
  cameraMovement: string;
  shotSize: string;
  startFrameMediaId: string | null;
  endFrameMediaId: string | null;
  /** 首尾帧的结构化版本；startFrameMediaId/endFrameMediaId 是它们的只读镜像 */
  keyframes?: Keyframe[];
  videoMediaIds: string[];
  /** 视频段的提示词与时长（manga-studio 的 VideoInterval） */
  videoPrompt?: string;
  durationSec: number;
  /** H3 合法帧数 17k+5，由前端算好并显示 */
  frameCount: number;
  seed: number;
  locked: boolean;
  instanceId: string | null;
  workflowKey: string;
  /** 这一镜出片用哪条工作流/哪台实例/哪颗权重。优先于上面那个旧 instanceId */
  preset?: GenPreset;
  /** 首尾帧是图片任务，和出片不是同一类工作流，允许各挑各的 */
  imagePreset?: GenPreset;
  turbo: string | null;
  h3Prompt: H3Prompt;
  state: ShotState;
  jobId?: string | null;
  /** 续拍：本段用上一段尾帧作引导 */
  continuesPrevious: boolean;
  /**
   * 跨镜衔接锚点：本镜从上一镜接住什么（动作方向 / 视线目标 / 同一道光 / 同一个道具 /
   * 同一种轮廓 / 同一段声音 / 同一股受力）。这是**文字接续**，和 continuesPrevious 的
   * 尾帧接续是两条独立机制：勾了续拍也照样要写锚点，因为接的不只是画面，还有动机。
   * 首镜写 N/A 或留空。
   */
  continuityAnchor?: string;
  /** AI 拆分镜头产生的子镜：'shot-1-2'，父镜 id 是 'shot-1' */
  parentShotId?: string | null;
}

/**
 * H3 提示词模式。与后端 apps/api/app/director.py 的 MODES 同源，改一处必须改两处。
 *  - three_field   三段式（本机已实测出片，默认）
 *  - six_section   官方六段式，英文
 *  - wenwu         中文导演分镜块（逐镜定时）
 *  - hybrid        六段外壳 + 导演级内容 + constraints 负向块
 */
export type H3PromptMode = "three_field" | "six_section" | "wenwu" | "hybrid";

/**
 * 一条视频段的提示词。四种模式共用同一个对象：
 *  - integrated / soundscape / music 四种模式都出（integrated 只写一句主旨，供列表与兜底显示）；
 *  - 深度内容放各自字段：六段用 subjectDefinitions…detailedDescription，wenwu 用 sceneDescription + shotBlocks。
 * mode 缺省按三段式读——IDB 里的旧项目根本没有这个字段。
 */
export interface H3Prompt {
  mode?: H3PromptMode;
  integrated: string;
  soundscape: string;
  music: string;
  subjectDefinitions?: string;
  summary?: string;
  retentionAnalysis?: string;
  detailedDescription?: string;
  /** hybrid 结尾的风格与负向约束块 */
  constraints?: string;
  /** wenwu：开篇的时长/画幅/类型/生命核/素材职责/最终发展线 */
  sceneDescription?: string;
  /** wenwu：逐镜定时块原文，每条形如「镜头1（0-3s）：…」 */
  shotBlocks?: string[];
  /**
   * 这一镜是模型在何时重写的。项目换提示词模式时要靠它把 AI 稿和模板稿分开：
   * 本地模板重拼会把六段式的导演级内容降级成一句话，不能让一次点击悄悄吃掉几分钟显存换来的稿子。
   */
  aiRewrittenAt?: string;
}

/** 资产库：跨项目复用的角色/场景（含参考图与全部提示词） */
export interface AssetLibraryItem {
  id: string;
  type: "character" | "scene";
  name: string;
  createdAt: string;
  updatedAt: string;
  character?: Character;
  scene?: Scene;
  /** 来源项目，用于「选择项目使用」的二跳 */
  originProjectId?: string;
  originProjectName?: string;
}

export interface RenderLog {
  ts: string;
  shotId: string;
  kind: JobKind;
  status: JobState;
  instanceId: string;
  durationMs?: number;
  jobId: string;
  error?: string;
  /** 日志弹窗要能回答「这是谁的图、用了哪个模型、多长的提示词」 */
  resourceType?: "character" | "character-variation" | "scene" | "keyframe" | "video" | "voice" | "script-parsing" | "export";
  resourceId?: string;
  resourceName?: string;
  model?: string;
  prompt?: string;
  projectId?: string;
}

/* ───────── 风格 / 用户 ───────── */

export interface VisualStyle {
  key: string;
  name: string;
  promptZh: string;
  promptEn?: string;
}

export type Role = "admin" | "editor" | "viewer";

export interface User {
  id: string;
  username: string;
  displayName: string;
  role: Role;
  isActive: boolean;
  lastLoginAt?: string | null;
  quota?: { concurrentJobs: number; dailyMoneyLimit?: number | null };
  usage?: { jobsToday: number; moneyToday?: number };
}

/* ───────── 探活报告 ───────── */

export interface ProbeReport {
  ok: boolean;
  /** 按协议返回不同形状，前端分支渲染 */
  native?: {
    comfyVersion?: string;
    nodeCount?: number;
    gpu?: string;
    vramTotalGb?: number;
    vramFreeGb?: number;
    caps?: InstanceCaps;
    missingModels?: string[];
  };
  task?: {
    queue?: RhQuota;
    account?: RhQuota;
    reachable?: boolean;
  };
  error?: string;
  /** 给用户的下一步动作 */
  hints?: string[];
}

export interface ImportReport {
  valid: boolean;
  sourceFormat: "api" | "ui";
  errors: { node?: string; classType?: string; message: string }[];
  warnings: string[];
  unknownNodes: string[];
  runninghubOnlyNodes: string[];
  missingModels: { folder: string; filename: string }[];
  missingCustomNodes: string[];
  slotCount: number;
  estimated?: { seconds: number; frames: number; width: number; height: number; steps: number };
  installPlan?: string[];
}

/**
 * 重新扫描的报告：后端报「这次按实例的 /object_info 重写了什么、还剩什么缺口」。
 *
 * 与 ImportReport 不是同一张单子 —— 导入那份要判图合法与否，重扫只报改动与剩余缺口，
 * 硬套成 ImportReport 就得在调用处 `as unknown as` 一次，类型从此不再说明任何事。
 */
export interface RescanReport {
  adaptations: WorkflowAdaptation[];
  gaps: WorkflowGap[];
  /** 写死的权重名被换成本机真实存在的文件，逐条改动 */
  alignment: Record<string, string>[];
  signals: WorkflowSignal[];
  executesOn: string;
  taskKind: string;
}

/* ───────── 文本模型用途（同步返回，结果直接写进 IndexedDB 的项目） ───────── */

export type LlmPurpose = "script_parse" | "storyboard" | "visualize" | "h3_prompt" | "script_write" | "script_chat";

export interface LlmRunResult<T = unknown> {
  purpose: LlmPurpose;
  data: T;
  model?: string | null;
  latencyMs: number;
  usage?: Record<string, unknown>;
  /** h3_prompt 回来的提示词是按哪个模式写的 */
  mode?: H3PromptMode;
  /** 后端的结构化自检结论（分镜规模/时长越界）。非阻塞，界面要如实显示 */
  warnings?: string[];
}

/** 分镜规划回来的每一镜 */
export interface LlmShot {
  index: number;
  sceneName: string;
  characterNames: string[];
  action: string;
  dialogue: string;
  visualPrompt: string;
  durationSec: number;
  cameraMovement: string;
  shotSize: string;
  /** 接住上一镜的锚点，首镜 N/A。后端 storyboard 必填，老后端可能不给 */
  continuityAnchor?: string;
}
