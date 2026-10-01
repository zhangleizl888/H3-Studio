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

import type { GenPreset, Project, Workflow } from "./types";

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

/** 一行小结，给卡片与下拉旁边的说明用 */
export function describePreset(project: Project, kind: GenKind, preset: GenPreset | undefined, workflows: Workflow[]): string {
  const eff = effectivePreset(project, kind, preset);
  const ref = workflowRef(eff.workflow);
  const hit = ref ? workflows.find((w) => w.id === ref) : null;
  const wfLabel = !ref ? "自动挑" : hit?.name ?? eff.workflow;
  const inst = eff.instanceId ? `@${eff.instanceId}` : "@跟随默认";
  const n = Object.keys(eff.models).length;
  return `${wfLabel}${inst}${n ? ` · 换 ${n} 个模型` : ""}`;
}
