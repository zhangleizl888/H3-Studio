/**
 * 创作数据的本地仓库（A 方案的另一半）。
 *
 * 为什么在项目数据上花一层 debounce：导演台每敲一个字都会走 updateData，
 * 直接写 IDB 会把主线程压在序列化上；1 秒合并一次写入，配合 flushSaves() 在切页/关页前落盘。
 *
 * 「服务器不留项目实体」是刻意的：换浏览器/换机器的代价是导出导入，
 * 换来的是零后端也能把剧本、分镜、提示词全部写完。
 */

import type { AssetLibraryItem, Character, Media, Project, Scene, Shot } from "./types";
import { allRecords, deleteRecord, getRecord, putRecord, recordsByIndex, STORE_ASSETS, STORE_BLOBS, STORE_MEDIA, STORE_PROJECTS } from "./idb";
import { accessToken } from "./tokens";
import { uid } from "./utils";

const SAVE_DEBOUNCE_MS = 1000;
/** 本地上传的参考图默认不进导出文件；超过这个总量就明确告诉用户没带上 */
const EXPORT_INLINE_BUDGET = 48 * 1024 * 1024;

export function defaultProjectConfig(): Project["config"] {
  return {
    aspectRatio: "16:9",
    visualStyle: "live-action",
    targetDurationSec: 60,
    outputLanguage: "中文",
    imageInstanceId: null,
    videoInstanceId: null,
    llmBackendId: null,
    shotModelBackendId: null,
    shotModel: null,
    // 新项目默认让后端按任务从工作流库里挑；库里挑不出会自动回落内置模板（参数表里会写明）
    imageTemplate: "auto",
    videoTemplate: "auto",
    seedPolicy: "locked",
    resolutionMode: "preview",
    h3WorkflowKey: "h3_t2v",
    h3PromptMode: "three_field",
    h3PromptReason: "",
    continuity: true,
    continuityOverlapFrames: 22,
    enhancePrompts: false,
  };
}

/**
 * 老项目补字段。IDB 里的项目是上一版结构，直接读会到处 undefined，
 * 所以出口统一过一次；只补默认值，绝不改已有值。
 */
function normalize(p: Project): Project {
  p.config = { ...defaultProjectConfig(), ...(p.config ?? {}) };
  p.data = { ...emptyData(), ...(p.data ?? {}) };
  p.data.characters ??= [];
  p.data.scenes ??= [];
  p.data.shots ??= [];
  p.data.renderLogs ??= [];
  p.data.scriptChats ??= [];
  // 老项目根本没有"正文对应哪一版"的概念。不补这两默认值，读回来是 undefined，
  // 「当前正文和当前版一致吗」就永远答"不一致"，界面会一直催用户存版
  p.data.scriptVersionUuid ??= null;
  p.data.scriptWrittenAt ??= null;
  for (const c of p.data.characters) {
    c.variations ??= [];
    c.refMediaIds ??= [];
    c.status ??= c.refMediaIds.length ? "completed" : "pending";
    for (const v of c.variations) {
      v.refMediaIds ??= [];
      v.status ??= v.refMediaIds.length ? "completed" : "pending";
    }
  }
  for (const s of p.data.scenes) {
    s.refMediaIds ??= [];
    s.status ??= s.refMediaIds.length ? "completed" : "pending";
  }
  p.data.shots.forEach((s, i) => {
    s.characterIds ??= [];
    s.videoMediaIds ??= [];
    s.keyframes ??= [];
    s.index ??= i + 1;
    s.id ||= uid("s");
  });
  return p;
}

function emptyData(): Project["data"] {
  return { rawScript: "", characters: [], scenes: [], shots: [], renderLogs: [] };
}

/** 本地上传的媒体没有服务端路径，用 idb:<id> 标记，渲染时再换成 objectURL */
export function isLocalMedia(m: Pick<Media, "path">): boolean {
  return m.path.startsWith("idb:");
}

// ───────── 项目 ─────────

export async function listProjects(): Promise<Project[]> {
  const rows = await allRecords<Project>(STORE_PROJECTS);
  return rows.map(normalize).sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));
}

async function requireProject(id: string): Promise<Project> {
  const p = await getRecord<Project>(STORE_PROJECTS, id);
  if (!p) throw new Error(`本地没有项目 ${id}（换浏览器了？还是项目在别的设备上）`);
  return normalize(p);
}

export async function getProject(id: string): Promise<Project> {
  return clone(await requireProject(id));
}

export async function createProject(name: string, synopsis = "", ownerId: string | null = null): Promise<Project> {
  const now = new Date().toISOString();
  const p: Project = {
    id: uid("p"),
    name,
    synopsis,
    stage: "script",
    config: defaultProjectConfig(),
    data: emptyData(),
    ownerId,
    createdAt: now,
    updatedAt: now,
  };
  await putRecord(STORE_PROJECTS, p);
  return clone(p);
}

/** 合并写入。所有 mutation 都过这里，updatedAt 不会漏 */
export async function patchProject(id: string, patch: Partial<Project>, mutate?: (p: Project) => void): Promise<Project> {
  const p = await requireProject(id);
  Object.assign(p, patch);
  if (mutate) mutate(p);
  p.updatedAt = new Date().toISOString();
  await writeNow(p);
  return clone(p);
}

export async function updateProjectData(id: string, data: Partial<Project["data"]>): Promise<Project> {
  return patchProject(id, {}, (p) => {
    // 深合并一层：导演台只改 shots，不该把 characters/scenes 覆盖没
    Object.assign(p.data, data);
  });
}

export async function updateProjectConfig(id: string, config: Partial<Project["config"]>): Promise<Project> {
  return patchProject(id, {}, (p) => Object.assign(p.config, config));
}

export async function deleteProject(id: string): Promise<void> {
  const p = await getRecord<Project>(STORE_PROJECTS, id);
  // 服务端那条产物线也得有个交代：整批送进生成回收站，100 天后才真删文件。
  // 以前这里只删浏览器索引，服务端那些文件从此没人管 —— media.deleted_at 建了列
  // 却一直没有人写，就是因为缺这一步。失败不拦本地删除（离线也能删项目），但要吭一声。
  try {
    await trashProjectOnServer(id, p?.name);
  } catch (e) {
    console.warn("服务端没能把该项目的产物放进回收站：", e);
  }
  const medias = await recordsByIndex<Media>(STORE_MEDIA, "projectId", id);
  await Promise.all(medias.map((m) => deleteMedia(m.id)));
  await deleteRecord(STORE_PROJECTS, id);
  forget(id);
}

/**
 * 用裸 fetch 而不是 api 客户端：httpApi 反过来 import 了本模块，走它就是循环依赖。
 * 没有 token（= 没登录 / 演示模式）就直接跳过，别在控制台刷一堆假失败。
 */
async function trashProjectOnServer(id: string, name?: string): Promise<void> {
  const token = accessToken();
  if (!token) return;
  const res = await fetch(`/api/projects/${encodeURIComponent(id)}/trash`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
    body: JSON.stringify({ name: name ?? null }),
  });
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
}

export async function duplicateProject(id: string): Promise<Project> {
  const src = await requireProject(id);
  const copy = clone(src);
  copy.id = uid("p");
  copy.name = `${src.name} 副本`;
  copy.updatedAt = new Date().toISOString();
  copy.createdAt = copy.updatedAt;
  // 实体 id 全部换掉：否则两份项目共用一个 characterId，改一处两处跟着变
  const remap = new Map<string, string>();
  const fresh = (old: string) => {
    const next = uid(old.split("_")[0] ?? "x");
    remap.set(old, next);
    return next;
  };
  copy.data.characters = src.data.characters.map((c) => ({ ...clone(c), id: fresh(c.id) }) as Character);
  copy.data.scenes = src.data.scenes.map((s) => ({ ...clone(s), id: fresh(s.id) }) as Scene);
  copy.data.shots = src.data.shots.map((s) => remapShot(s, remap)) as Shot[];
  await putRecord(STORE_PROJECTS, copy);

  const medias = await recordsByIndex<Media>(STORE_MEDIA, "projectId", id);
  for (const m of medias) {
    const copyMedia: Media = { ...clone(m), id: uid("m"), refId: m.refId ? (remap.get(m.refId) ?? m.refId) : m.refId, projectId: copy.id };
    if (isLocalMedia(m)) {
      const blob = await getRecord<{ id: string; blob: Blob }>(STORE_BLOBS, m.path.slice(4));
      if (blob) await putRecord(STORE_BLOBS, { id: copyMedia.id, blob });
      copyMedia.path = `idb:${copyMedia.id}`;
    }
    await putRecord(STORE_MEDIA, copyMedia);
  }
  return clone(copy);
}

function remapShot(shot: Shot, remap: Map<string, string>): Shot {
  const s = clone(shot);
  s.id = uid("s");
  // 副本里的场景/角色只认映射得到的；指不到就退回「未分配」，不能留一个只有原项目才有的 id
  s.sceneId = s.sceneId ? (remap.get(s.sceneId) ?? null) : null;
  s.characterIds = s.characterIds.map((c) => remap.get(c)).filter((c): c is string => !!c);
  if (s.variationByChar) {
    // 键是角色 id，必须跟着换；值（服装变体 id）不用，变体是随角色一起复制进来的
    s.variationByChar = Object.fromEntries(Object.entries(s.variationByChar).map(([charId, variationId]) => [remap.get(charId) ?? charId, variationId]));
  }
  s.jobId = null;
  s.state = "idle";
  s.videoMediaIds = [];
  s.startFrameMediaId = null;
  s.endFrameMediaId = null;
  return s;
}

// ───────── 防抖写入 ─────────

const dirty = new Map<string, { project: Project; timer: number }>();

/** 编辑过程中用这个：1 秒内的连续改动合成一次写盘 */
export function queueSave(project: Project): void {
  const existing = dirty.get(project.id);
  if (existing) window.clearTimeout(existing.timer);
  const p = clone(project);
  dirty.set(project.id, { project: p, timer: window.setTimeout(() => void flushOne(p), SAVE_DEBOUNCE_MS) });
}

async function flushOne(p: Project): Promise<void> {
  dirty.delete(p.id);
  await putRecord(STORE_PROJECTS, p).catch((e) => console.error("项目保存失败", p.name, e));
}

/** 关页/切项目/触发生成前必须调用，否则最后 1 秒的编辑会丢 */
export async function flushSaves(): Promise<void> {
  const pending = [...dirty.values()];
  dirty.clear();
  // 定时器必须一起清掉：只清表的话，那条 timer 到点还会把这份快照再写一次，
  // 于是「flush 之后紧跟的 mutation」刚落的字段会被它用旧副本盖回去（实测丢过会话状态）。
  for (const { timer } of pending) window.clearTimeout(timer);
  await Promise.all(pending.map(({ project }) => putRecord(STORE_PROJECTS, project)));
}

function forget(id: string): void {
  const d = dirty.get(id);
  if (d) {
    window.clearTimeout(d.timer);
    dirty.delete(id);
  }
}

if (typeof window !== "undefined") {
  addEventListener("beforeunload", () => {
    // 事务一旦交给 IDB 就会跑完，但回调本身没法 await：最后 1 秒内的编辑有极小概率丢，
    // 所以触发生成、切项目、导出前都要显式 await flushSaves()
    void flushSaves();
  });
}

// ───────── 媒体与上传 ─────────

export async function listMedia(projectId: string): Promise<Media[]> {
  const rows = await recordsByIndex<Media>(STORE_MEDIA, "projectId", projectId);
  return rows.sort((a, b) => a.createdAt.localeCompare(b.createdAt));
}

/** 单条媒体索引。摘指针时要先知道这条属于哪个项目 */
export async function getMedia(id: string): Promise<Media | null> {
  return (await getRecord<Media>(STORE_MEDIA, id)) ?? null;
}

export async function putUpload(projectId: string, file: File | Blob, role: Media["role"], refId: string | null, kind: Media["kind"]): Promise<Media> {
  const m: Media = {
    id: uid("m"),
    projectId,
    kind,
    role,
    refId,
    path: "",
    mime: file.type || undefined,
    bytes: file.size,
    createdAt: new Date().toISOString(),
  };
  m.path = `idb:${m.id}`;
  await putRecord(STORE_BLOBS, { id: m.id, blob: file });
  await putRecord(STORE_MEDIA, m);
  return m;
}

/** 存服务端产物。id 用后端 media 表的数字主键字符串，mediaSrc 靠它拼 /api/media/{id}/raw */
export async function saveMedia(m: Media): Promise<Media> {
  await putRecord(STORE_MEDIA, m);
  return m;
}

export async function deleteMedia(id: string): Promise<void> {
  const m = await getRecord<Media>(STORE_MEDIA, id);
  if (!m) return;
  await deleteRecord(STORE_MEDIA, id);
  if (isLocalMedia(m)) await deleteRecord(STORE_BLOBS, m.path.slice(4));
  releaseUrl(id);
}

export async function localBlob(m: Media): Promise<Blob | null> {
  if (!isLocalMedia(m)) return null;
  return (await getRecord<{ id: string; blob: Blob }>(STORE_BLOBS, m.path.slice(4)))?.blob ?? null;
}

/**
 * objectURL 缓存：同一个关键帧在时间轴、对比视图、资产页里会被要很多次，
 * 每次都 createObjectURL 就是每次复制一份解码缓冲。页面生命周期内复用，删除媒体时释放。
 */
const urlCache = new Map<string, string>();

export function cachedUrl(id: string): string | null {
  return urlCache.get(id) ?? null;
}

export function cacheUrl(id: string, blob: Blob): string {
  const url = URL.createObjectURL(blob);
  urlCache.set(id, url);
  return url;
}

export function releaseUrl(id: string): void {
  const url = urlCache.get(id);
  if (url) {
    URL.revokeObjectURL(url);
    urlCache.delete(id);
  }
}

// ───────── 导出 / 导入 ─────────

export interface ProjectExport {
  format: "h3studio.project";
  version: 1;
  exportedAt: string;
  project: Project;
  media: (Media & { dataUrl?: string })[];
  /** 上传的参考图默认不进文件（太大）；这里记下被跳过的字节数，导入方好提示 */
  skippedBytes: number;
}

export async function exportProject(id: string, opts: { withUploads?: boolean } = {}): Promise<string> {
  const project = await requireProject(id);
  const medias = await listMedia(id);
  let budget = opts.withUploads ? EXPORT_INLINE_BUDGET : 0;
  let skipped = 0;
  const out: ProjectExport["media"] = [];
  for (const m of medias) {
    if (!isLocalMedia(m)) {
      out.push(m);
      continue;
    }
    const row = await getRecord<{ id: string; blob: Blob }>(STORE_BLOBS, m.path.slice(4));
    const size = row?.blob.size ?? 0;
    if (!row || size > budget) {
      skipped += size;
      continue;
    }
    budget -= size;
    out.push({ ...m, dataUrl: await blobToDataUrl(row.blob) });
  }
  const payload: ProjectExport = { format: "h3studio.project", version: 1, exportedAt: new Date().toISOString(), project, media: out, skippedBytes: skipped };
  return JSON.stringify(payload, null, 2);
}

export async function importProject(json: string): Promise<{ project: Project; mediaIn: number; mediaSkipped: number }> {
  const data = JSON.parse(json) as Partial<ProjectExport>;
  if (data.format !== "h3studio.project" || !data.project?.id) throw new Error("这不是 H3 Studio 的项目导出文件");
  if (data.version !== 1) throw new Error(`导出版本 ${data.version} 认不了，当前只支持 1`);
  const p = data.project;
  if (await getRecord(STORE_PROJECTS, p.id)) p.id = uid("p");
  p.updatedAt = new Date().toISOString();
  await putRecord(STORE_PROJECTS, p);

  let skipped = 0;
  for (const m of data.media ?? []) {
    const copy: Media = { ...m, projectId: p.id };
    if (isLocalMedia(m)) {
      copy.id = uid("m");
      copy.path = `idb:${copy.id}`;
      if (!m.dataUrl) {
        skipped += m.bytes ?? 0;
        continue;
      }
      await putRecord(STORE_BLOBS, { id: copy.id, blob: await dataUrlToBlob(m.dataUrl) });
    }
    await putRecord(STORE_MEDIA, copy);
  }
  return { project: clone(p), mediaIn: (data.media ?? []).length, mediaSkipped: skipped + (data.skippedBytes ?? 0) };
}

async function blobToDataUrl(blob: Blob): Promise<string> {
  const buf = new Uint8Array(await blob.arrayBuffer());
  let bin = "";
  for (let i = 0; i < buf.length; i += 0x8000) bin += String.fromCharCode(...buf.subarray(i, i + 0x8000));
  return `data:${blob.type || "application/octet-stream"};base64,${btoa(bin)}`;
}

async function dataUrlToBlob(url: string): Promise<Blob> {
  const res = await fetch(url);
  return res.blob();
}

// ───────── 资产库（跨项目复用） ─────────

/**
 * 资产库存的是「一份角色/场景的完整定义 + 它的参考图 media 行」。
 * 参考图本身在 blobs 或服务端 media 里，这里只带 id —— 所以删项目前要先把要留的资产存进库，
 * 否则导入到别的项目时那些 id 指向的 blob 已经没人认领了。
 */
export async function listAssets(type?: "character" | "scene"): Promise<AssetLibraryItem[]> {
  const rows = await allRecords<AssetLibraryItem>(STORE_ASSETS);
  const filtered = type ? rows.filter((r) => r.type === type) : rows;
  return filtered.sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));
}

export async function deleteAsset(id: string): Promise<void> {
  await deleteRecord(STORE_ASSETS, id);
}

export async function saveCharacterAsset(project: Project, char: Character): Promise<AssetLibraryItem> {
  const now = new Date().toISOString();
  const existing = (await allRecords<AssetLibraryItem>(STORE_ASSETS)).find((a) => a.type === "character" && a.originProjectId === project.id && a.character?.name === char.name);
  const item: AssetLibraryItem = {
    id: existing?.id ?? uid("asset"),
    type: "character",
    name: char.name,
    createdAt: existing?.createdAt ?? now,
    updatedAt: now,
    character: clone(char),
    originProjectId: project.id,
    originProjectName: project.name,
  };
  await putRecord(STORE_ASSETS, item);
  return item;
}

export async function saveSceneAsset(project: Project, scene: Scene): Promise<AssetLibraryItem> {
  const now = new Date().toISOString();
  const existing = (await allRecords<AssetLibraryItem>(STORE_ASSETS)).find((a) => a.type === "scene" && a.originProjectId === project.id && a.scene?.name === scene.name);
  const item: AssetLibraryItem = {
    id: existing?.id ?? uid("asset"),
    type: "scene",
    name: scene.name,
    createdAt: existing?.createdAt ?? now,
    updatedAt: now,
    scene: clone(scene),
    originProjectId: project.id,
    originProjectName: project.name,
  };
  await putRecord(STORE_ASSETS, item);
  return item;
}

/** 导入到别的项目：实体 id 全部重编，否则两个项目共用一个 characterId，改一处两处跟着变 */
export async function importAssetIntoProject(projectId: string, assetId: string): Promise<Project> {
  const asset = await getRecord<AssetLibraryItem>(STORE_ASSETS, assetId);
  if (!asset) throw new Error(`资产库里没有 ${assetId}`);
  const p = await requireProject(projectId);
  if (asset.type === "character" && asset.character) {
    if (p.data.characters.some((c) => c.id === asset.character!.id)) {
      const fresh = clone(asset.character);
      fresh.id = uid("c");
      fresh.variations = fresh.variations.map((v) => ({ ...v, id: uid("v") }));
      p.data.characters.push(fresh);
    } else {
      p.data.characters.push(clone(asset.character));
    }
  } else if (asset.type === "scene" && asset.scene) {
    const fresh = clone(asset.scene);
    if (p.data.scenes.some((s) => s.id === fresh.id)) fresh.id = uid("sc");
    p.data.scenes.push(fresh);
  }
  await writeNow(p);
  return clone(p);
}

// ───────── 中断收口 ─────────

/**
 * 打开项目时把「还在 generating 却没有 jobId」的镜头判失败。
 *
 * 有 jobId 的不能乱改：任务在服务器队列里，关页面不影响它跑完，
 * 那种情况交给 reconcileShots 去问后端真实状态。
 */
export async function markOrphanShotsFailed(project: Project): Promise<Project> {
  const orphans = project.data.shots.filter((s) => (s.state === "generating" || s.state === "queued") && !s.jobId);
  if (!orphans.length) return project;
  const now = new Date().toISOString();
  for (const s of orphans) {
    s.state = "failed";
    project.data.renderLogs.unshift({
      ts: now,
      shotId: s.id,
      kind: "video",
      status: "failed",
      instanceId: s.instanceId ?? "",
      jobId: "",
      error: "任务没进队列就断了（浏览器在派发前关过页），重新派发这一镜",
    });
  }
  project.updatedAt = now;
  await writeNow(project);
  return clone(project);
}

/**
 * 用服务端的任务状态纠正本地镜头状态 —— 两边都是真相，但生成结果只有后端知道。
 * jobs 传进来的是这个项目的任务列表（不额外请求，调用方已经在轮询）。
 */
export async function reconcileShots(project: Project, jobs: { id: string; state: string; outputMediaIds?: string[]; error?: { message?: string } | null }[]): Promise<Project | null> {
  const byId = new Map(jobs.map((j) => [j.id, j]));
  let changed = false;
  for (const s of project.data.shots) {
    if (!s.jobId) continue;
    const job = byId.get(s.jobId);
    if (!job) continue; // 任务被清理掉了就保持原样，别把历史状态改花
    const next: Shot["state"] =
      job.state === "succeeded" ? "completed" : job.state === "failed" || job.state === "canceled" ? "failed" : job.state === "running" || job.state === "dispatching" ? "generating" : "queued";
    if (next !== s.state) {
      s.state = next;
      changed = true;
      if ((next === "failed" || next === "completed") && !s.videoMediaIds.length && job.outputMediaIds?.length) s.videoMediaIds = job.outputMediaIds;
      if (next === "failed") {
        project.data.renderLogs.unshift({
          ts: new Date().toISOString(),
          shotId: s.id,
          kind: "video",
          status: "failed",
          instanceId: s.instanceId ?? "",
          jobId: s.jobId,
          error: job.error?.message ?? "渲染失败",
        });
      }
    }
  }
  if (!changed) return null;
  project.updatedAt = new Date().toISOString();
  await writeNow(project);
  return clone(project);
}

async function writeNow(p: Project): Promise<void> {
  forget(p.id);
  await putRecord(STORE_PROJECTS, clone(p));
}

function clone<T>(v: T): T {
  return structuredClone(v);
}
