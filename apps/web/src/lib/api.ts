import type {
  GenInstance,
  H3PromptMode,
  LlmPurpose,
  ImportReport,
  LlmRunResult,
  Job,
  LlmBackend,
  LocalScanResult,
  Media,
  NodeOverride,
  ParsedScript,
  ProbeReport,
  Project,
  ScriptVersionRow,
  ScriptVersionSource,
  TrashList,
  User,
  VersionBucket,
  VisualStyle,
  Workflow,
  WorkflowSlot,
} from "./types";

/** 软删一版产物后服务端回的东西：摘实体指针要用它，之后再问就要不到了 */
export interface MediaTrashResult {
  id: string;
  projectKey: string | null;
  kind: string;
  bucket: VersionBucket;
  role: string | null;
  refId: string | null;
  version: number;
  groupRemaining: number;
  /** 同组里最新存活的那一版：删掉当前版时实体指针该挪到这里 */
  promoteCandidateId: string | null;
  deletedAt: string;
  purgeAfter: string | null;
  daysLeft: number | null;
  retentionDays: number | null;
}

/** 软删一版剧本：删的正好是当前版时，服务端顺手把最新存活那一版顶上来 */
export interface ScriptTrashResult {
  uuid: string;
  wasCurrent: boolean;
  current: ScriptVersionRow | null;
  deletedAt: string;
  purgeAfter: string | null;
  daysLeft: number | null;
  retentionDays: number | null;
}

/** 一次用途调用的可选参数；mode/durationSec/aspect/style 只有 h3_prompt 读，messages/script 只有 script_chat 读 */
export interface LlmRunOpts {
  targetSec?: number;
  pace?: string;
  backendId?: string;
  model?: string;
  mode?: H3PromptMode;
  durationSec?: number;
  aspect?: string;
  style?: string;
  /** script_chat：编辑器当前正文。不走 input —— input 是这一轮的指令，正文要整篇喂给模型好回吐全稿 */
  script?: string;
  /** script_chat：往轮对话。assistant 只带说明文字，别把上一轮的整篇正文再喂一遍 */
  messages?: { role: "user" | "assistant"; content: string }[];
}

/**
 * 前端唯一的数据入口。后端起来后只需把 VITE_USE_MOCK 设为 false，
 * 方法签名即 §10 API 契约，不改页面。
 */
export interface Api {
  auth: {
    login(username: string, password: string): Promise<{ user: User; access: string; refresh: string }>;
    me(): Promise<User | null>;
    logout(): Promise<void>;
    setupRequired(): Promise<boolean>;
  };
  instances: {
    list(): Promise<GenInstance[]>;
    create(body: Partial<GenInstance> & { name: string; protocol: GenInstance["protocol"] }): Promise<GenInstance>;
    update(id: string, body: Partial<GenInstance>): Promise<GenInstance>;
    remove(id: string): Promise<void>;
    probe(id: string): Promise<ProbeReport>;
    /** 新建前的连通性试探，不落库 */
    dryProbe(body: { protocol: GenInstance["protocol"]; baseUrl: string; apiKey?: string; site?: string }): Promise<ProbeReport>;
  };
  llm: {
    list(scope?: "local" | "cloud"): Promise<LlmBackend[]>;
    scanLocal(): Promise<LocalScanResult>;
    create(body: Partial<LlmBackend> & { name: string; scope: "local" | "cloud"; kind: "ollama" | "openai_compat"; baseUrl: string }): Promise<LlmBackend>;
    update(id: string, body: Partial<LlmBackend>): Promise<LlmBackend>;
    remove(id: string): Promise<void>;
    probe(id: string): Promise<LlmBackend>;
    pull(id: string, model: string): Promise<void>;
    setDefault(id: string): Promise<void>;
    /** 同步跑一个用途：拆解 / 分镜 / 视觉化翻译 / H3 提示词（mode 只影响最后一个） */
    run(purpose: LlmPurpose, input: string, opts?: LlmRunOpts): Promise<LlmRunResult<unknown>>;
    defaults(): Promise<Record<string, { backendId: string; model: string }>>;
    saveDefaults(d: Record<string, { backendId: string; model: string }>): Promise<void>;
  };
  workflows: {
    list(): Promise<Workflow[]>;
    get(id: string): Promise<Workflow>;
    importJson(name: string, json: string, instanceId?: string): Promise<{ workflow: Workflow; report: ImportReport }>;
    validate(graph: string, instanceId?: string): Promise<ImportReport>;
    remove(id: string): Promise<void>;
    export(id: string, format: "api" | "ui"): Promise<string>;
    slots(id: string): Promise<WorkflowSlot[]>;
    /** slots 投影成 RunningHub 的 nodeInfoList 骨架 */
    nodeOverrides(id: string, values: Record<string, unknown>): Promise<NodeOverride[]>;
    testRun(id: string, instanceId: string): Promise<Job>;
    /** 装了节点包 / 换了实例之后重算：后端从原始导出重改写，不累积上一次改动 */
    rescan(id: string, instanceId?: string): Promise<{ workflow: Workflow; report: ImportReport }>;
    /** 改「要不要参与自动选」与优先级 */
    patch(id: string, body: { autoSelect?: boolean; priority?: number; description?: string; tags?: string[] }): Promise<Workflow>;
    /** 只排序不建任务：这次任务会挑中哪条、凭什么是它 */
    selectPreview(kind: string, slots: Record<string, unknown>, instanceId?: string): Promise<WorkflowPickResult>;
  };
  projects: {
    list(): Promise<Project[]>;
    get(id: string): Promise<Project>;
    create(name: string, synopsis?: string): Promise<Project>;
    update(id: string, patch: Partial<Project>): Promise<Project>;
    updateData(id: string, data: Partial<Project["data"]>): Promise<Project>;
    updateConfig(id: string, config: Partial<Project["config"]>): Promise<Project>;
    remove(id: string): Promise<void>;
    duplicate(id: string): Promise<Project>;
    /** 换浏览器/换机器就靠这两条（A 方案的代价） */
    export(id: string, withUploads?: boolean): Promise<string>;
    import(json: string): Promise<{ project: Project; mediaIn: number; mediaSkipped: number }>;
  };
  media: {
    /** 本地索引视图：上传的参考图 + 已回写的服务端产物 */
    project(id: string): Promise<Media[]>;
    /** 解析成 <img>/<video> 能直接用的 src：本地产物走 objectURL，服务端产物经后端读回 */
    url(m: Media | null | undefined): Promise<string | null>;
    /** 图片上传。落 IDB，同时在需要时被服务端读走当参考图 */
    put(file: File | Blob, role: Media["role"], refId: string | null, projectId: string): Promise<Media>;
    /** 把服务端产物登记进本地索引（任务成功后必须调，否则刷新就找不到图） */
    adopt(m: Media): Promise<Media>;
    /** 从服务端按项目/标签拉产物行，用于刷新与资产库 */
    server(filter: { projectKey?: string; role?: string; refId?: string; kind?: string; ids?: string[] }): Promise<Media[]>;
    /** 移进生成回收站：服务端写 deleted_at，前端摘实体指针并删本地索引行。没进过回收站谈不上恢复 */
    remove(id: string): Promise<void>;
  };
  /**
   * 版本历史与生成回收站。真源在服务端：剧本走 script_versions 表，图片/视频按
   * (project_key, kind, role, ref_id) 把 media 行数成 V1..Vn —— 序号永不复用。
   * 「当前版本」两边不同：媒体看项目实体里的指针，剧本看 isCurrent。
   */
  versions: {
    media(filter: {
      projectKey?: string;
      allProjects?: boolean;
      bucket?: VersionBucket;
      role?: string;
      refId?: string;
      includeDeleted?: boolean;
      onlyDeleted?: boolean;
    }): Promise<Media[]>;
    script(projectKey: string, opts?: { includeDeleted?: boolean }): Promise<ScriptVersionRow[]>;
    createScript(body: {
      projectKey: string;
      text: string;
      source: ScriptVersionSource;
      snapshot?: ScriptVersionRow["snapshot"];
      /** 只在前端手动存版/补存旧正文时被后端采纳，其余一律服务端时钟 */
      writtenAt?: string;
      /** 首次存版时把用户早已写好的那版一起交上来，服务端在一个事务里先插 V1 再插 V2 */
      backfillFrom?: { text: string; writtenAt?: string };
      setCurrent?: boolean;
    }): Promise<ScriptVersionRow>;
    /** 设为当前。后端顺手把正文带回来，前端要写回 rawScript */
    setScriptCurrent(uuid: string): Promise<{ current: ScriptVersionRow; rawScript: string }>;
    trashScript(uuid: string): Promise<ScriptTrashResult>;
    restoreScript(uuid: string): Promise<{ uuid: string; promotedToCurrent: boolean; current: ScriptVersionRow }>;
    purgeScript(uuid: string): Promise<void>;
    /** 软删一版产物。响应自带摘指针所需的一切（置了 deleted_at 之后服务端就把这行藏起来了）。
     *  本地上传的参考图没有服务端行，返回 null —— 但调用方仍必须摘指针 */
    trashMedia(id: string): Promise<MediaTrashResult | null>;
    restoreMedia(id: string): Promise<void>;
    purgeMedia(id: string): Promise<{ deleted: number; bytes: number; orphans: number; locked: number; skipped: number }>;
    /** 删项目时把它的产物与剧本版本整批送进回收站 */
    trashProject(projectKey: string, name?: string): Promise<{ mediaTrashed: number; scriptTrashed: number; bytes: number }>;
    trash(filter: { projectKey?: string; allProjects?: boolean; bucket?: VersionBucket }): Promise<TrashList>;
  };
  jobs: {
    list(filter?: { projectId?: string; state?: Job["state"] }): Promise<Job[]>;
    /** 单任务状态。生成按钮靠它轮询到终态 */
    get(id: string): Promise<Job>;
    /** 一次生成 = 一个任务。图由后端按 template+slots 拼，浏览器不碰 graph */
    generate(req: GenerateRequest): Promise<Job>;
    /** 批量派发：逐条入队，一条镜头一个任务，能单独重试/取消 */
    generateBatch(reqs: GenerateRequest[]): Promise<{ jobs: Job[]; errors: { index: number; title?: string; error: string }[] }>;
    /** 派发前的参数确认表：逐条参数 + 问题清单 + 外推耗时。只读，不入队 */
    plan(reqs: GenerateRequest[]): Promise<JobPlanResult>;
    cancel(id: string): Promise<void>;
    retry(id: string): Promise<Job>;
    setPriority(id: string, priority: number): Promise<void>;
  };
  styles: { list(): Promise<VisualStyle[]> };
  parse: {
    /** 把外部剧本文件读成纯文本。读不了后端把「为什么 + 下一步」原话回出来 */
    script(file: File): Promise<ParsedScript>;
  };
  users: {
    list(): Promise<User[]>;
    create(body: { username: string; displayName: string; role: User["role"]; password: string }): Promise<User>;
    update(id: string, body: Partial<User>): Promise<User>;
    remove(id: string): Promise<void>;
  };
  system: {
    storage(): Promise<{
      mediaBytes: number;
      tmpBytes: number;
      freeBytes: number;
      mediaCount: number;
      root?: string;
      /** 回收站积压了多少：这些字节还要再占满保留期才真删 */
      trashCount?: number;
      trashBytes?: number;
      retentionDays?: number;
      /** 后端上次自动回收是什么时候、收回了多少 —— 设置页要说实话，不写死假数字 */
      lastPurge?: Record<string, unknown>;
      byProject: { projectId: string; name: string; bytes: number; count: number }[];
    }>;
    gc(dryRun: boolean): Promise<{ reclaimableBytes: number; orphans: number; dryRun?: boolean }>;
    /** 单卡仲裁状态：谁正占着这张 4090 */
    gpu(): Promise<GpuState>;
    gpuYield(): Promise<{ yielded: boolean; reason?: string }>;
    gpuRestore(): Promise<{ restored: boolean; reason?: string }>;
  };
  exports: {
    merge(projectId: string, mediaIds: string[], title?: string): Promise<ExportFile>;
    pack(projectId: string, items: { mediaId: string; path: string }[], title?: string): Promise<ExportFile>;
    edl(projectId: string, shots: ExportShotRow[], title?: string): Promise<{ format: string; text: string }>;
    xml(projectId: string, shots: ExportShotRow[], title?: string): Promise<{ format: string; text: string }>;
    jianying(projectId: string, shots: ExportShotRow[]): Promise<never>;
  };
}

/** 一次生成请求：template 见后端 /api/workflows 的 builtin:* */
/** /api/workflows/select 的返回：候选按分数从高到低，空数组表示库里没有能干的 */
export interface WorkflowPickResult {
  kind: string;
  placement: string;
  provided: string[];
  fallbackTemplate: string | null;
  candidates: { id: number; name: string; score: number; reasons: string[]; taskKind: string; executesOn: string }[];
}

export interface GenerateRequest {
  projectId: string;
  /** 内置模板名，或 "auto"：让后端按任务从工作流库里挑 */
  template: string;
  /** 指定用库里哪条工作流（数字 id）。给了它就跳过自动选 */
  workflowId?: number;
  slots: Record<string, unknown>;
  instanceId?: string;
  kind?: "image" | "video" | "video_chain" | "upscale" | "workflow_test";
  title?: string;
  priority?: number;
  /** 产物落库时打成谁的 —— 角色定妆照 / 场景图 / 某镜的首帧。promptMode 让后端能核对提示词形状 */
  meta?: { role: string; refId: string; promptMode?: string };
}

/** 派发前参数表的一行 —— 后端 job_plan.py 的返回，前端只渲染不改判 */
export interface JobPlanRow {
  index: number;
  title: string;
  template: string;
  kind: string;
  instance: { id: string; label: string; placement: string; protocol: string; probeOk: boolean | null; circuitOpen: boolean } | null;
  slots: Record<string, unknown>;
  derived: Record<string, string | number | null>;
  problems: string[];
  blocked: boolean;
}

export interface JobPlanResult {
  rows: JobPlanRow[];
  totals: { count: number; blocked: number; warned: number; etaSeconds: number; etaMinutes: number; etaIsEstimate: boolean };
}

export interface GpuState {
  enabled: boolean;
  llmRunning: boolean;
  llmPort?: number | null;
  llmPid?: number | null;
  yielded: boolean;
  renderingLocally: boolean;
  llmCalling: boolean;
  lastError?: string | null;
}

export interface ExportFile {
  mediaId: string;
  url: string;
  bytes: number;
  mode?: string;
  segments?: number;
}

export interface ExportShotRow {
  index: number;
  title?: string;
  durationSec: number;
  mediaId?: string | null;
  action?: string;
  cameraMovement?: string;
  sceneName?: string;
}

const USE_MOCK = import.meta.env.VITE_USE_MOCK !== "false";

/** 页面用它说明「演示模式下的产物不是真文件」，别让用户以为出片了 */
export const IS_MOCK = USE_MOCK;

/** 动态选择实现：真实后端未起时不会加载 http 模块 */
export async function getApi(): Promise<Api> {
  if (USE_MOCK) {
    const m = await import("./mock/mockApi");
    return m.mockApi;
  }
  const h = await import("./httpApi");
  return h.httpApi;
}
