/**
 * 生成预设：一条资产/一个镜头「用哪条工作流、在哪台实例上、换哪颗权重」。
 *
 * 为什么要在实体上开这一层：同一部片里角色走 Klein、场景走 Qwen-Image、某几个镜头想走
 * 云端，是常态而不是例外。项目级配置留着当默认值，实体上的预设优先 —— 老项目里没有
 * preset 字段，读出来就是 undefined，行为与改动前一致。
 *
 * workflow 的写法与 config.imageTemplate 完全一致："auto" / 内置模板名 / 库工作流的数字 id。
 * 两处两套说法就会出现「界面显示的是 A、请求发出去是 B」。
 */

import type { Character, GenInstance, GenPreset, Project, ProjectConfig, Scene, Shot, Workflow } from "./types";

export type GenKind = "image" | "video" | "audio";

/** 预设里的写法 → 工作流接口认的引用（"28" 或 "builtin:qwen_image"）；null = 让后端挑 */
export function workflowRef(raw?: string | null): string | null {
  const v = String(raw ?? "").trim();
  if (!v || v === "auto") return null;
  return /^\d+$/.test(v) ? v : `builtin:${v}`;
}

/** 工作流列表里那一条 → 预设里该存的写法 */
export function presetValueOf(w: { id: string }): string {
  return w.id.startsWith("builtin:") ? w.id.slice("builtin:".length) : w.id;
}

/** 这一类任务在库里有哪几条（内置模板 + 工作流行都按 taskKind 分） */
export function workflowsForKind(list: Workflow[], kind: GenKind): Workflow[] {
  return list.filter((w) => (w.taskKind ?? w.family) === kind);
}

/** 项目级的默认选择。audio 没有默认模板（后端也没有内置回落），只跟着出片那台实例走 */
export function projectPreset(project: Project, kind: GenKind): GenPreset {
  const c = project.config;
  if (kind === "image") return { workflow: c.imageTemplate, instanceId: c.imageInstanceId ?? null };
  if (kind === "video") return { workflow: c.videoTemplate, instanceId: c.videoInstanceId ?? null };
  return { workflow: "auto", instanceId: c.videoInstanceId ?? c.imageInstanceId ?? null };
}

/** 生效值：实体预设的每一项各自优先，缺的那一项才回落项目默认 */
export function effectivePreset(project: Project, kind: GenKind, preset?: GenPreset): Required<GenPreset> {
  const base = projectPreset(project, kind);
  return {
    workflow: preset?.workflow || base.workflow || "auto",
    instanceId: preset?.instanceId ?? base.instanceId ?? null,
    models: preset?.models ?? {},
  };
}

/**
 * 生效预设 → 请求里的那三样。
 *
 * 「auto」在后端有两个含义：让库挑（挑不出才回落内置模板）。数字 id 才是显式指定某条，
 * 走 template=auto + workflowId —— 与后端 JobCreate 的约定一致。
 */
export function presetToRequest(project: Project, kind: GenKind, preset?: GenPreset) {
  const eff = effectivePreset(project, kind, preset);
  const ref = workflowRef(eff.workflow);
  const models = Object.keys(eff.models).length ? eff.models : undefined;
  const instanceId = eff.instanceId || undefined;
  if (!ref) return { template: "auto", instanceId, models };
  // 库里某一条：template 仍然写 auto，由 workflowId 决定用谁（后端 JobCreate 的约定）
  if (/^\d+$/.test(ref)) return { template: "auto", workflowId: Number(ref), instanceId, models };
  return { template: ref.replace(/^builtin:/, ""), instanceId, models };
}

/* ───────── 实例死指针的迁移 ───────── */

/**
 * 实例行是会没的，项目里存的实例 id 不会跟着变。
 *
 * 本机 ComfyUI 那行被删过之后重建，id 从 503 变成 530，于是项目里每个指着 503 的指针都成了
 * 死链：出图出片全先撞在「实例不在已登记的实例里」上，而那看起来像参数填错。
 * 登记的实例**只有一台**时这个映射没有歧义，直接搬过去；不止一台就只摘不猜 ——
 * 猜错实例的代价是白烧十几分钟显存，宁可回落到后端登记的默认实例。
 *
 * 只动「以后要用哪台」的指针。renderLogs 里的 instanceId 是当时的事实记录，不改写历史。
 */
export interface PointerSync {
  config: Partial<ProjectConfig>;
  data: { characters?: Character[]; scenes?: Scene[]; shots?: Shot[] };
  /** 逐条说清改了什么，形如「镜 1·实例：503 → 530」；界面上要能原话讲出来 */
  changes: string[];
}

export function syncInstancePointers(project: Project, instances: GenInstance[]): PointerSync | null {
  const alive = instances.map((i) => String(i.id));
  const known = new Set(alive);
  const target = alive.length === 1 ? alive[0] : null;
  const changes: string[] = [];

  /** 返回新值代表这一处要改（null = 摘掉跟默认走）；返回 undefined 代表不用动 */
  const move = (from: string | null | undefined, where: string): string | null | undefined => {
    if (!from || known.has(String(from))) return undefined;
    changes.push(target ? `${where}：${from} → ${target}` : `${where}：${from} 已不存在，改回跟随后端默认实例`);
    return target;
  };

  const config: Partial<ProjectConfig> = {};
  const cfgImage = move(project.config.imageInstanceId, "项目默认·出图");
  if (cfgImage !== undefined) config.imageInstanceId = cfgImage;
  const cfgVideo = move(project.config.videoInstanceId, "项目默认·出片");
  if (cfgVideo !== undefined) config.videoInstanceId = cfgVideo;

  const data: PointerSync["data"] = {};

  const shots = project.data.shots.map((s) => {
    const legacy = move(s.instanceId, `镜 ${s.index}·实例`);
    const presetTo = move(s.preset?.instanceId, `镜 ${s.index}·生成选择`);
    if (legacy === undefined && presetTo === undefined) return s;
    const next = { ...s };
    if (legacy !== undefined) next.instanceId = legacy;
    if (presetTo !== undefined) next.preset = { ...s.preset, instanceId: presetTo };
    return next;
  });
  if (shots.some((s, i) => s !== project.data.shots[i])) data.shots = shots;

  const characters = project.data.characters.map((c) => {
    const presetTo = move(c.preset?.instanceId, `角色 ${c.name}·生成选择`);
    const voiceTo = move(c.voice?.preset?.instanceId, `角色 ${c.name}·音色`);
    if (presetTo === undefined && voiceTo === undefined) return c;
    const next = { ...c };
    if (presetTo !== undefined) next.preset = { ...c.preset, instanceId: presetTo };
    if (voiceTo !== undefined && c.voice) next.voice = { ...c.voice, preset: { ...c.voice.preset, instanceId: voiceTo } };
    return next;
  });
  if (characters.some((c, i) => c !== project.data.characters[i])) data.characters = characters;

  const scenes = project.data.scenes.map((s) => {
    const to = move(s.preset?.instanceId, `场景 ${s.name}·生成选择`);
    return to === undefined ? s : { ...s, preset: { ...s.preset, instanceId: to } };
  });
  if (scenes.some((s, i) => s !== project.data.scenes[i])) data.scenes = scenes;

  if (!changes.length) return null;
  return { config, data, changes };
}
