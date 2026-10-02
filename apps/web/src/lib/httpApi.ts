import type { Api, ExportFile, GenerateRequest, JobPlanResult, MediaTrashResult, ScriptTrashResult } from "./api";
import type {
  GenInstance,
  ImportReport,
  Job,
  LlmBackend,
  LlmRunResult,
  Media,
  ParsedScript,
  ScriptVersionRow,
  Skill,
  SkillImportReport,
  TrashItem,
  User,
  Workflow,
  WorkflowModelOptions,
} from "./types";
import * as local from "./localStores";
import { accessToken, clearTokens, devAutoLogin, refreshTokenValue, setTokens } from "./tokens";
import { dereference } from "./versions";

const BASE = import.meta.env.VITE_API_BASE ?? "";

class HttpError extends Error {
  constructor(
    public status: number,
    message: string,
    public detail?: unknown,
  ) {
    super(message);
  }
}

async function raw(path: string, init?: RequestInit): Promise<Response> {
  const token = accessToken();
  // FormData 不能自己写 Content-Type：boundary 必须由浏览器补，写了就变成非法请求体
  const isForm = typeof FormData !== "undefined" && init?.body instanceof FormData;
  return fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      ...(isForm ? {} : { "Content-Type": "application/json" }),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(init?.headers ?? {}),
    },
  });
}

/** 刷新要单飞：并发 401 时只发一次 /auth/refresh，否则一次泄露的刷新令牌会把自家会话全吊销 */
let refreshing: Promise<boolean> | null = null;

async function refreshOnce(): Promise<boolean> {
  const value = refreshTokenValue();
  if (!value) return false;
  refreshing ??= (async () => {
    try {
      const res = await fetch(`${BASE}/api/auth/refresh`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh: value }),
      });
      if (!res.ok) {
        clearTokens();
        return false;
      }
      const data = (await res.json()) as { access: string; refresh: string; expires_in: number };
      setTokens(data);
      return true;
    } catch {
      return false;
    } finally {
      setTimeout(() => (refreshing = null), 0);
    }
  })();
  return refreshing;
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  let res = await raw(path, init);
  // 401 且不是登录/刷新本身：换一张票再试一次，用户不用重登
  if (res.status === 401 && !path.startsWith("/api/auth/") && (await refreshOnce())) res = await raw(path, init);
  if (!res.ok) {
    let detail: unknown;
    let msg = `${res.status} ${res.statusText}`;
    try {
      detail = await res.json();
      const d = detail as { error?: { message?: string }; detail?: string; message?: string; hints?: string[] };
      msg = d?.detail ?? d?.message ?? d?.error?.message ?? msg;
      if (d?.hints?.length) msg += `（${d.hints.join("；")}）`;
    } catch {
      /* 没有 JSON body */
    }
    throw new HttpError(res.status, msg, detail);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

const j = (body: unknown) => JSON.stringify(body);

/**
 * multipart 版请求。不能复用 req()：raw() 会写死 Content-Type: application/json，
 * 而 FormData 的 boundary 必须让浏览器自己生成，写了就是一份没人认领的 body。
 * 认证与错误处理照 req() 抄一遍：带 Bearer、401 换票重试一次、detail/hints 拼成人话。
 */
async function postForm<T>(path: string, form: FormData): Promise<T> {
  const send = () => {
    const token = accessToken();
    return fetch(`${BASE}${path}`, { method: "POST", body: form, headers: token ? { Authorization: `Bearer ${token}` } : undefined });
  };
  let res = await send();
  if (res.status === 401 && (await refreshOnce())) res = await send();
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;
    try {
      const d = (await res.json()) as { detail?: string; message?: string; hints?: string[] };
      msg = d?.detail ?? d?.message ?? msg;
      if (d?.hints?.length) msg += `（${d.hints.join("；")}）`;
    } catch {
      /* 没有 JSON body */
    }
    throw new HttpError(res.status, msg);
  }
  return (await res.json()) as T;
}

/** 后端的 job 行字段是 camelCase（见 routes_jobs._job_out），这里只做前端要补的两件事 */
function toJob(raw: Record<string, unknown>): Job {
  const j = raw as unknown as Job;
  return { ...j, projectId: (raw.projectKey as string) ?? null, outputMediaIds: (raw.mediaIds as string[]) ?? (raw.output as string[]) ?? [], log: (raw.log as Job["log"]) ?? [] };
}

/** 前端一条生成请求 → 后端 JobCreate。/jobs、/jobs/batch、/jobs/plan 三条路共用，别各写一份 */
function jobBody(r: GenerateRequest) {
  return {
    template: r.template,
    slots: r.slots,
    // template=auto 时 kind 必须由调用方给（选工作流按它过滤），不能再靠模板名字猜
    kind: r.kind ?? (r.template.includes("video") ? "video" : "image"),
    title: r.title,
    projectKey: r.projectId,
    instanceId: r.instanceId,
    workflowId: r.workflowId,
    models: r.models,
    priority: r.priority ?? 100,
    meta: r.meta,
    // 技能只交 id：正文由后端读库并进 slots.prompt，参数表看到的就是并好之后那串
    skillIds: r.skillIds,
  };
}

/** 后端 media 行 → 前端 Media。id 用服务端数字主键字符串，media.url 靠它拼 /api/media/{id}/raw */
function toMedia(raw: Record<string, unknown>, projectId: string): Media {
  return {
    id: String(raw.id),
    projectId: (raw.projectKey as string) ?? projectId,
    kind: (raw.kind as Media["kind"]) ?? "image",
    role: raw.role as Media["role"],
    refId: (raw.refId as string) ?? null,
    path: (raw.path as string) ?? "",
    bytes: (raw.bytes as number) ?? null,
    mime: (raw.mime as string) ?? undefined,
    width: (raw.width as number) ?? null,
    height: (raw.height as number) ?? null,
    fps: (raw.fps as number) ?? null,
    thumbPath: (raw.thumbPath as string) ?? null,
    durationMs: (raw.durationMs as number) ?? null,
    createdAt: (raw.createdAt as string) ?? new Date().toISOString(),
    // 下面这几项只有 /api/media-versions 与 /api/trash 会给；/api/media 返回的是"活行"
    version: (raw.version as number) ?? null,
    versionCount: (raw.versionCount as number) ?? null,
    groupValue: (raw.groupValue as string) ?? null,
    deletedAt: (raw.deletedAt as string) ?? null,
    purgeAfter: (raw.purgeAfter as string) ?? null,
    daysLeft: (raw.daysLeft as number) ?? null,
    title: (raw.title as string) ?? null,
  } as Media;
}

/** 后端剧本版本行 → 前端。字段基本同名，只给 snapshot 兜个空对象 */
function toScriptVersion(raw: Record<string, unknown>): ScriptVersionRow {
  return { ...(raw as unknown as ScriptVersionRow), snapshot: (raw.snapshot as ScriptVersionRow["snapshot"]) ?? {} };
}

/**
 * 软删一版产物。本地上传的参考图没有服务端行（path=idb:），也就进不了回收站，返回 null
 * 让调用方照样把指针和本地索引清干净。
 */
async function trashMediaRow(id: string): Promise<MediaTrashResult | null> {
  if (!/^\d+$/.test(id)) return null;
  return req<MediaTrashResult>(`/api/media/${id}`, { method: "DELETE" });
}

/**
 * 后端把文本模型能力放在 `caps`，前端类型是 `capabilities`。
 * 在这里归一次，而不是让每个页面各自兜底 —— ConfigPanel 就是直接读 capabilities.models 崩掉的。
 * 另外两处键名漂移也在这里收：后端报 `ctxTotal`（前端读 `ctxSize`），
 * 而 `supportsPull` 后端根本不发（只有 Ollama 有 /api/pull，llama.cpp 没有 → 按 kind 推）。
 */
function toLlm(raw: Record<string, unknown>): LlmBackend {
  const caps = (raw.caps ?? raw.capabilities ?? {}) as Record<string, unknown>;
  const capabilities = {
    models: [],
    ...caps,
    ctxSize: caps.ctxSize ?? caps.ctxTotal ?? null,
    supportsPull: caps.supportsPull ?? caps.kind === "ollama",
  } as unknown as LlmBackend["capabilities"];
  return { ...(raw as unknown as LlmBackend), capabilities };
}

/** 导出产物回填进本地媒体索引：不然资产库与「最近产物」看不到刚合成的成片 */
async function adoptById(projectId: string, mediaId: string): Promise<void> {
  try {
    const rows = await req<Record<string, unknown>[]>(`/api/media?ids=${encodeURIComponent(mediaId)}`);
    if (rows[0]) await local.saveMedia(toMedia(rows[0], projectId));
  } catch {
    // 回填失败不影响下载本身，界面上顶多少一条历史
  }
}

async function doLogin(username: string, password: string) {
  const s = await req<{ access: string; refresh: string; expires_in: number; expiresIn?: number; user: User }>("/api/auth/login", {
    method: "POST",
    body: j({ username, password }),
  });
  setTokens({ access: s.access, refresh: s.refresh, expires_in: s.expires_in ?? s.expiresIn });
  return s;
}

/** 拿一张能用的票：本地票 → 刷新 → 演示账号自动登录。三条都不成才要用户自己登录 */
async function ensureTicket(): Promise<boolean> {
  if (accessToken()) return true;
  if (await refreshOnce()) return true;
  if (!devAutoLogin) return false;
  try {
    await doLogin(devAutoLogin.username, devAutoLogin.password);
    return true;
  } catch {
    clearTokens();
    return false;
  }
}

/**
 * 真实后端客户端。方法路径与 PLAN.md §10 的 API 契约一一对应。
 *
 * 例外是 projects / media：按 A 方案它们留在浏览器 IndexedDB 里，
 * 没有后端也能写完剧本和分镜；生成、实例、用户、工作流仍然走服务端。
 */
export const httpApi: Api = {
  auth: {
    async login(username, password) {
      const s = await doLogin(username, password);
      return { user: s.user, access: s.access, refresh: s.refresh };
    },
    async me() {
      if (!(await ensureTicket())) return null;
      try {
        return await req<User>("/api/auth/me");
      } catch (e) {
        if (e instanceof HttpError && e.status === 401) return null;
        throw e;
      }
    },
    async logout() {
      const refresh = refreshTokenValue();
      try {
        await req("/api/auth/logout", { method: "POST", body: j({ refresh: refresh ?? "" }) });
      } finally {
        clearTokens();
      }
    },
    async changePassword(current, next) {
      await req("/api/auth/change-password", { method: "POST", body: j({ currentPassword: current, newPassword: next }) });
      // 服务端已经把该账号的会话全部吊销，本地这两张票留着只是一用就 401，直接丢掉
      clearTokens();
    },
    setupRequired: () => req<{ needed: boolean }>("/api/auth/setup-required").then((r) => r.needed),
  },
  instances: {
    list: () => req<GenInstance[]>("/api/instances"),
    create: (body) => req("/api/instances", { method: "POST", body: j(body) }),
    update: (id, body) => req(`/api/instances/${id}`, { method: "PATCH", body: j(body) }),
    remove: (id) => req(`/api/instances/${id}`, { method: "DELETE" }),
    probe: (id) => req(`/api/instances/${id}/probe`, { method: "POST" }),
    ping: (id) => req(`/api/instances/${id}/ping`, { method: "POST" }),
    dryProbe: (body) => req("/api/instances/dry-probe", { method: "POST", body: j(body) }),
  },
  llm: {
    async list(scope) {
      const rows = await req<Record<string, unknown>[]>(`/api/llm/backends${scope ? `?scope=${scope}` : ""}`);
      return rows.map(toLlm);
    },
    run: (purpose, input, opts) => req<LlmRunResult>("/api/llm/run", { method: "POST", body: j({ purpose, input, ...opts }) }),
    scanLocal: () => req("/api/llm/local-scan"),
    async create(body) {
      return toLlm(await req<Record<string, unknown>>("/api/llm/backends", { method: "POST", body: j(body) }));
    },
    async update(id, body) {
      return toLlm(await req<Record<string, unknown>>(`/api/llm/backends/${id}`, { method: "PATCH", body: j(body) }));
    },
    remove: (id) => req(`/api/llm/backends/${id}`, { method: "DELETE" }),
    async probe(id) {
      return toLlm(await req<Record<string, unknown>>(`/api/llm/backends/${id}/probe`, { method: "POST" }));
    },
    pull: (id, model) => req(`/api/llm/backends/${id}/pull`, { method: "POST", body: j({ model }) }),
    setDefault: (id) => req(`/api/llm/backends/${id}/default`, { method: "POST" }),
    defaults: () => req("/api/llm/defaults"),
    saveDefaults: (d) => req("/api/llm/defaults", { method: "PUT", body: j(d) }),
  },
  workflows: {
    list: () => req("/api/workflows"),
    get: (id) => req(`/api/workflows/${id}`),
    async importJson(body) {
      // 走 postForm：它带 Bearer、401 换票重试一次。以前这里是裸 fetch，
      // 只写了 credentials:"include"，而这站的票在 localStorage 的 Bearer 头里 —— 导入必 401。
      const fd = new FormData();
      fd.append("file", new Blob([body.json], { type: "application/json" }), `${body.name || "workflow"}.json`);
      // 画布版是可选的第二份：给了就存进 ui_graph，编辑器与回导 ComfyUI 用那一份
      if (body.uiJson) fd.append("ui_file", new Blob([body.uiJson], { type: "application/json" }), `${body.name || "workflow"}.ui.json`);
      // 这些是后端的 Form 字段：不传就全退回默认值（名字变文件名、导入不做实例比对）
      fd.append("name", body.name);
      if (body.description) fd.append("description", body.description);
      if (body.instanceId) fd.append("instance_id", body.instanceId);
      if (body.priority != null) fd.append("priority", String(body.priority));
      if (body.tags?.length) fd.append("tags", body.tags.join(","));
      return postForm<{ workflow: Workflow; report: ImportReport }>("/api/workflows/import", fd);
    },
    validate: (graph, instanceId) => req("/api/workflows/validate", { method: "POST", body: j({ graph, instance_id: instanceId }) }),
    /** 装了节点包 / 换了实例之后重算一次：从原始导出重改写，不累积上一次的改动 */
    rescan: (id, instanceId) =>
      req(
        `/api/workflows/${id}/rescan${instanceId ? `?instance_id=${encodeURIComponent(instanceId)}` : ""}`,
        { method: "POST" },
      ),
    patch: (id, body) => req(`/api/workflows/${id}`, { method: "PATCH", body: j(body) }),
    /** 只排序不建任务：这次任务会挑中哪条、凭什么是它 */
    selectPreview: (kind, slots, instanceId) =>
      req(`/api/workflows/select?kind=${encodeURIComponent(kind)}&slots=${encodeURIComponent(JSON.stringify(slots))}${
        instanceId ? `&instance_id=${encodeURIComponent(instanceId)}` : ""
      }`),
    remove: (id) => req(`/api/workflows/${id}`, { method: "DELETE" }),
    /**
     * 后端回的是 {format, json}，而接口声明的是 Promise<string> —— 这里必须把字符串拆出来。
     * 以前直接把整个对象当文本返回，导出页签的 <pre> 一渲染对象就把整页崩成白屏（React 200）。
     */
    export: async (id, format) => {
      const r = await req<{ format: string; json: string }>(`/api/workflows/${encodeURIComponent(id)}/export?format=${format}`);
      return typeof r === "string" ? r : r.json;
    },
    slots: (id) => req(`/api/workflows/${id}/slots`),
    /** 这条工作流在那台实例上能换哪些权重。清单是实例报的，不是前端猜的 */
    modelOptions: (id, instanceId) =>
      req<WorkflowModelOptions>(
        `/api/workflows/${encodeURIComponent(id)}/models${instanceId ? `?instance_id=${encodeURIComponent(instanceId)}` : ""}`,
      ),
    nodeOverrides: (id, values) => req(`/api/workflows/${id}/node-overrides`, { method: "POST", body: j(values) }),
    testRun: (id, instanceId, slots) => req(`/api/workflows/${id}/test`, { method: "POST", body: j({ instanceId, slots: slots ?? {} }) }),
    /** 这条工作流在每台实例上的默认权重。一次问齐，模型编辑与同步弹窗共用 */
    bindings: (id) => req(`/api/workflows/${encodeURIComponent(id)}/bindings`),
    saveBindings: (id, body) =>
      req(`/api/workflows/${encodeURIComponent(id)}/bindings`, { method: "PUT", body: j({ instanceId: body.instanceId, overrides: body.overrides }) }),
    clearBindings: (id, instanceId) => req(`/api/workflows/${encodeURIComponent(id)}/bindings/${encodeURIComponent(instanceId)}`, { method: "DELETE" }),
    syncBindings: (id, body) =>
      req(`/api/workflows/${encodeURIComponent(id)}/bindings/sync`, {
        method: "POST",
        body: j({
          sourceInstanceId: body.sourceInstanceId,
          targetInstanceIds: body.targetInstanceIds,
          alignUnbound: body.alignUnbound ?? true,
        }),
      }),
    syncAll: (body) =>
      req("/api/workflows/sync-all", {
        method: "POST",
        body: j({
          sourceInstanceId: body.sourceInstanceId,
          targetInstanceIds: body.targetInstanceIds,
          alignUnbound: body.alignUnbound ?? true,
          onlyMissing: body.onlyMissing ?? false,
          includeBuiltin: body.includeBuiltin ?? false,
        }),
      }),
    check: (id, instanceIds) =>
      req(`/api/workflows/${encodeURIComponent(id)}/check`, { method: "POST", body: j({ instanceIds: instanceIds ?? [] }) }),
    replaceGraph: (id, body) =>
      req(`/api/workflows/${encodeURIComponent(id)}/graph`, { method: "POST", body: j({ graph: body.graph, instance_id: body.instanceId }) }),
  },
  /**
   * 技能库：真源在服务端。正文不在这里带过去 —— 前端只交选中的 id，
   * 后端在 /llm/run 与 /jobs 各自读库拼好，所以改了技能不需要重存项目。
   */
  skills: {
    async list(stage) {
      return req<Skill[]>(`/api/skills${stage ? `?stage=${stage}` : ""}`);
    },
    create: (body) => req<Skill>("/api/skills", { method: "POST", body: j(body) }),
    update: (id, body) => req<Skill>(`/api/skills/${encodeURIComponent(id)}`, { method: "PATCH", body: j(body) }),
    remove: (id) => req<void>(`/api/skills/${encodeURIComponent(id)}`, { method: "DELETE" }),
    async importFiles(files, library) {
      // 走 postForm：multipart 的 boundary 必须让浏览器自己生成，且要带 Bearer、401 换票重试一次
      const fd = new FormData();
      for (const f of files) fd.append("files", f, f.name);
      if (library) fd.append("library", library);
      return postForm<SkillImportReport>("/api/skills/import", fd);
    },
    clear: () => req<{ deleted: number; names: string[] }>(`/api/skills/clear?confirm=${encodeURIComponent("清空技能库")}`, { method: "POST" }),
  },
  // A 方案：项目实体留在浏览器 IndexedDB，服务端不持有创作数据
  projects: {
    list: () => local.listProjects(),
    get: (id) => local.getProject(id),
    create: (name, synopsis) => local.createProject(name, synopsis ?? ""),
    update: (id, patch) => local.patchProject(id, patch),
    updateData: (id, data) => local.updateProjectData(id, data),
    updateConfig: (id, config) => local.updateProjectConfig(id, config),
    remove: (id) => local.deleteProject(id),
    duplicate: (id) => local.duplicateProject(id),
    export: (id, withUploads) => local.exportProject(id, { withUploads }),
    import: (json) => local.importProject(json),
  },
  media: {
    async project(id) {
      return local.listMedia(id);
    },
    async url(m) {
      if (!m) return null;
      // 回收站里的行服务端默认 404，必须带 ?trashed=1 才读得到（文件本来就在盘上，满保留期才真删）。
      // 缓存键要分开写，否则恢复之后还在吃旧的 objectURL。
      const trashed = !!m.deletedAt && !local.isLocalMedia(m);
      const key = trashed ? `${m.id}:t` : m.id;
      const cached = await local.cachedUrl(key);
      if (cached) return cached;
      let blob: Blob | null = null;
      if (local.isLocalMedia(m)) {
        blob = await local.localBlob(m);
      } else {
        // 产物一律经后端读：浏览器不该知道实例地址，也不该拿到会过期的 RunningHub 外链
        const res = await raw(`/api/media/${m.id}/raw${trashed ? "?trashed=1" : ""}`);
        blob = res.ok ? await res.blob() : null;
      }
      if (!blob) return null;
      return local.cacheUrl(key, blob);
    },
    async put(file, role, refId, projectId) {
      // 必须真落服务端：出图/出片时 ComfyUI 要从磁盘读它，而 generate.ts 的 serverMediaIds()
      // 只认纯数字 id —— 只存进 IndexedDB 的上传永远当不了参考素材（音频也是，音色参考同理）。
      const fd = new FormData();
      fd.append("file", file, file instanceof File ? file.name : "upload");
      const q = new URLSearchParams({ role });
      if (refId) q.set("ref_id", refId);
      if (projectId) q.set("project_key", projectId);
      const row = await req<Record<string, unknown>>(`/api/media/upload?${q}`, { method: "POST", body: fd });
      const m = toMedia(row, projectId ?? "");
      await local.saveMedia(m);
      return m;
    },
    async adopt(m) {
      return local.saveMedia(m);
    },
    async server(filter) {
      const q = new URLSearchParams();
      if (filter.projectKey) q.set("project_key", filter.projectKey);
      if (filter.role) q.set("role", filter.role);
      if (filter.refId) q.set("ref_id", filter.refId);
      if (filter.kind) q.set("kind", filter.kind);
      if (filter.ids?.length) q.set("ids", filter.ids.join(","));
      const rows = await req<Record<string, unknown>[]>(`/api/media?${q}`);
      return rows.map((r) => toMedia(r, filter.projectKey ?? ""));
    },
    /** 移进生成回收站：服务端软删 → 摘实体指针 → 删本地索引行。顺序反了会闪一帧破图 */
    async remove(id) {
      const result = await trashMediaRow(id);
      const m = await local.getMedia(id);
      // 指针一定要摘，不管这条有没有服务端行：本地上传图 trashMediaRow 返回 null，
      // 但它在 refMediaIds / videoMediaIds 里同样占着一格，漏摘就是"只标记没删干净"
      if (m?.projectId) {
        await local.flushSaves();
        await local.patchProject(m.projectId, {}, (p) => dereference(p, id, result?.promoteCandidateId ?? null));
      }
      local.releaseUrl(`${id}:t`);
      await local.deleteMedia(id);
    },
  },
  versions: {
    async media(filter) {
      const q = new URLSearchParams();
      if (filter.projectKey) q.set("project_key", filter.projectKey);
      if (filter.allProjects) q.set("all_projects", "true");
      if (filter.bucket && filter.bucket !== "script") q.set("bucket", filter.bucket);
      if (filter.role) q.set("role", filter.role);
      if (filter.refId) q.set("ref_id", filter.refId);
      if (filter.includeDeleted) q.set("include_deleted", "true");
      if (filter.onlyDeleted) q.set("only_deleted", "true");
      const rows = await req<Record<string, unknown>[]>(`/api/media-versions?${q}`);
      return rows.map((r) => toMedia(r, filter.projectKey ?? ""));
    },
    async script(projectKey, opts) {
      const q = new URLSearchParams({ project_key: projectKey });
      if (opts?.includeDeleted) q.set("include_deleted", "true");
      return (await req<Record<string, unknown>[]>(`/api/script-versions?${q}`)).map(toScriptVersion);
    },
    async createScript(body) {
      return toScriptVersion(await req<Record<string, unknown>>("/api/script-versions", { method: "POST", body: j(body) }));
    },
    async setScriptCurrent(uuid) {
      const r = await req<{ current: Record<string, unknown>; rawScript: string }>(
        `/api/script-versions/${encodeURIComponent(uuid)}/current`,
        { method: "POST" },
      );
      return { current: toScriptVersion(r.current), rawScript: r.rawScript };
    },
    async trashScript(uuid) {
      return req<ScriptTrashResult>(`/api/script-versions/${encodeURIComponent(uuid)}`, { method: "DELETE" });
    },
    async restoreScript(uuid) {
      const r = await req<{ uuid: string; promotedToCurrent: boolean; current: Record<string, unknown> }>(
        `/api/script-versions/${encodeURIComponent(uuid)}/restore`,
        { method: "POST" },
      );
      return { ...r, current: toScriptVersion(r.current) };
    },
    async purgeScript(uuid) {
      await req<void>(`/api/script-versions/${encodeURIComponent(uuid)}/purge`, { method: "DELETE" });
    },
    async trashMedia(id) {
      return trashMediaRow(id);
    },
    async restoreMedia(id) {
      await req<unknown>(`/api/media/${id}/restore`, { method: "POST" });
    },
    async purgeMedia(id) {
      return req<{ deleted: number; bytes: number; orphans: number; locked: number; skipped: number }>(
        `/api/media/${id}/purge`,
        { method: "DELETE" },
      );
    },
    async trashProject(projectKey, name) {
      return req<{ mediaTrashed: number; scriptTrashed: number; bytes: number }>(
        `/api/projects/${encodeURIComponent(projectKey)}/trash`,
        { method: "POST", body: j({ name: name ?? null }) },
      );
    },
    async trash(filter) {
      const q = new URLSearchParams();
      if (filter.projectKey) q.set("project_key", filter.projectKey);
      if (filter.allProjects) q.set("all_projects", "true");
      if (filter.bucket) q.set("bucket", filter.bucket);
      const r = await req<{ retentionDays: number; totalBytes: number; items: Record<string, unknown>[] }>(`/api/trash?${q}`);
      return {
        retentionDays: r.retentionDays,
        totalBytes: r.totalBytes,
        items: r.items.map((it) => {
          // 顶层 kind 是这张 union 的判别式（media|script），媒体自己是 image/video —— 那份在 bucket 里。
          // 直接把它喂给 toMedia 会得到 kind:"media"，预览就认不出这是图还是片
          const media = it.kind === "media" ? toMedia({ ...it, kind: it.bucket }, (it.projectKey as string) ?? "") : undefined;
          return { ...(it as unknown as TrashItem), media };
        }),
      };
    },
  },
  jobs: {
    async list(f) {
      const q = new URLSearchParams({ limit: "200", ...(f?.projectId ? { project_key: f.projectId } : {}), ...(f?.state ? { state: f.state } : {}) });
      const rows = await req<Record<string, unknown>[]>(`/api/jobs?${q}`);
      return rows.map(toJob);
    },
    async get(id) {
      return toJob(await req<Record<string, unknown>>(`/api/jobs/${id}`));
    },
    async generate(r) {
      await local.flushSaves();
      return toJob(await req<Record<string, unknown>>("/api/jobs", { method: "POST", body: j(jobBody(r)) }));
    },
    async generateBatch(reqs) {
      await local.flushSaves();
      const res = await req<{ jobs: Record<string, unknown>[]; errors: { index: number; title?: string; error: string }[] }>("/api/jobs/batch", {
        method: "POST",
        body: j({ jobs: reqs.map(jobBody) }),
      });
      return { jobs: res.jobs.map(toJob), errors: res.errors ?? [] };
    },
    async plan(reqs) {
      await local.flushSaves();
      return req<JobPlanResult>("/api/jobs/plan", { method: "POST", body: j({ jobs: reqs.map(jobBody) }) });
    },
    async cancel(id) {
      await req(`/api/jobs/${id}/cancel`, { method: "POST" });
    },
    async retry(id) {
      return req<Record<string, unknown>>(`/api/jobs/${id}/retry`, { method: "POST" }).then((r) => toJob(r));
    },
    setPriority: (id, priority) => req(`/api/jobs/${id}`, { method: "PATCH", body: j({ priority }) }),
  },
  styles: { list: () => req("/api/styles") },
  parse: {
    script: (file) => {
      const form = new FormData();
      form.append("file", file, file.name);
      return postForm<ParsedScript>("/api/parse-script", form);
    },
  },
  users: {
    list: () => req("/api/users"),
    create: (body) => req("/api/users", { method: "POST", body: j(body) }),
    update: (id, body) => req(`/api/users/${id}`, { method: "PATCH", body: j(body) }),
    remove: (id) => req(`/api/users/${id}`, { method: "DELETE" }),
  },
  system: {
    storage: () => req("/api/system/storage"),
    gc: (dryRun) => req("/api/system/gc", { method: "POST", body: j({ dryRun }) }),
    paths: () => req("/api/system/paths"),
    backup: () => req("/api/system/backup"),
    savePaths: (body) => req("/api/system/paths", { method: "PUT", body: j(body) }),
    audit: (opts) => {
      const q = new URLSearchParams();
      if (opts?.limit) q.set("limit", String(opts.limit));
      if (opts?.action) q.set("action", opts.action);
      if (opts?.actor) q.set("actor", opts.actor);
      const tail = q.toString();
      return req(`/api/system/audit${tail ? `?${tail}` : ""}`);
    },
    gpu: () => req("/api/system/gpu"),
    gpuYield: () => req("/api/system/gpu/yield", { method: "POST" }),
    gpuRestore: () => req("/api/system/gpu/restore", { method: "POST" }),
  },
  exports: {
    merge: async (projectId, mediaIds, title) => {
      const res = await req<ExportFile>(`/api/projects/${projectId}/export/merge`, { method: "POST", body: j({ mediaIds: mediaIds.map(Number), title }) });
      await adoptById(projectId, res.mediaId);
      return res;
    },
    pack: async (projectId, items, title) => {
      const res = await req<ExportFile>(`/api/projects/${projectId}/export/pack`, { method: "POST", body: j({ items: items.map((i) => ({ ...i, mediaId: Number(i.mediaId) })), title }) });
      await adoptById(projectId, res.mediaId);
      return res;
    },
    edl: (projectId, shots, title) => req(`/api/projects/${projectId}/export/edl`, { method: "POST", body: j({ shots, title }) }),
    xml: (projectId, shots, title) => req(`/api/projects/${projectId}/export/xml`, { method: "POST", body: j({ shots, title }) }),
    jianying: (projectId, shots) => req(`/api/projects/${projectId}/export/jianying`, { method: "POST", body: j({ shots }) }),
  },
};
