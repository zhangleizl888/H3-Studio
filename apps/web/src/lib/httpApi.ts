import type { Api, ExportFile, GenerateRequest, JobPlanResult } from "./api";
import type { GenInstance, Job, LlmBackend, LlmRunResult, Media, User } from "./types";
import * as local from "./localStores";
import { accessToken, clearTokens, devAutoLogin, refreshTokenValue, setTokens } from "./tokens";

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
  return fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
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
    kind: r.kind ?? (r.template.includes("video") ? "video" : "image"),
    title: r.title,
    projectKey: r.projectId,
    instanceId: r.instanceId,
    priority: r.priority ?? 100,
    meta: r.meta,
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
    durationMs: (raw.durationMs as number) ?? null,
    createdAt: (raw.createdAt as string) ?? new Date().toISOString(),
  } as Media;
}

/**
 * 后端把文本模型能力放在 `caps`，前端类型是 `capabilities`。
 * 在这里归一次，而不是让每个页面各自兜底 —— ConfigPanel 就是直接读 capabilities.models 崩掉的。
 */
function toLlm(raw: Record<string, unknown>): LlmBackend {
  const caps = (raw.caps ?? raw.capabilities ?? {}) as Record<string, unknown>;
  return { ...(raw as unknown as LlmBackend), capabilities: { models: [], ...caps } as unknown as LlmBackend["capabilities"] };
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
    setupRequired: () => req<{ needed: boolean }>("/api/auth/setup-required").then((r) => r.needed),
  },
  instances: {
    list: () => req<GenInstance[]>("/api/instances"),
    create: (body) => req("/api/instances", { method: "POST", body: j(body) }),
    update: (id, body) => req(`/api/instances/${id}`, { method: "PATCH", body: j(body) }),
    remove: (id) => req(`/api/instances/${id}`, { method: "DELETE" }),
    probe: (id) => req(`/api/instances/${id}/probe`, { method: "POST" }),
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
    async importJson(name, json) {
      const fd = new FormData();
      fd.append("file", new Blob([json], { type: "application/json" }), name);
      const res = await fetch(`${BASE}/api/workflows/import`, { method: "POST", body: fd, credentials: "include" });
      if (!res.ok) throw new HttpError(res.status, await res.text());
      return res.json();
    },
    validate: (graph, instanceId) => req("/api/workflows/validate", { method: "POST", body: j({ graph, instance_id: instanceId }) }),
    remove: (id) => req(`/api/workflows/${id}`, { method: "DELETE" }),
    export: (id, format) => req(`/api/workflows/${id}/export?format=${format}`),
    slots: (id) => req(`/api/workflows/${id}/slots`),
    nodeOverrides: (id, values) => req(`/api/workflows/${id}/node-overrides`, { method: "POST", body: j(values) }),
    testRun: (id, instanceId) => req(`/api/workflows/${id}/test`, { method: "POST", body: j({ instanceId }) }),
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
      const cached = await local.cachedUrl(m.id);
      if (cached) return cached;
      let blob: Blob | null = null;
      if (local.isLocalMedia(m)) {
        blob = await local.localBlob(m);
      } else {
        // 产物一律经后端读：浏览器不该知道实例地址，也不该拿到会过期的 RunningHub 外链
        const res = await raw(`/api/media/${m.id}/raw`);
        blob = res.ok ? await res.blob() : null;
      }
      if (!blob) return null;
      return local.cacheUrl(m.id, blob);
    },
    async put(file, role, refId, projectId) {
      const kind = file.type.startsWith("video") ? "ref_video" : file.type.startsWith("audio") ? "ref_audio" : "ref_image";
      return local.putUpload(projectId, file, role, refId ?? null, kind);
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
    async remove(id) {
      await local.deleteMedia(id);
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
  users: {
    list: () => req("/api/users"),
    create: (body) => req("/api/users", { method: "POST", body: j(body) }),
    update: (id, body) => req(`/api/users/${id}`, { method: "PATCH", body: j(body) }),
    remove: (id) => req(`/api/users/${id}`, { method: "DELETE" }),
  },
  system: {
    storage: () => req("/api/system/storage"),
    gc: (dryRun) => req("/api/system/gc", { method: "POST", body: j({ dryRun }) }),
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
