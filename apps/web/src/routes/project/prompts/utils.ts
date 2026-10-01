import {
  buildCharacterPrompt,
  buildH3Prompt,
  buildKeyframePrompt,
  buildScenePrompt,
  buildVariationPrompt,
  extractBasePrompt,
  h3PromptText,
  negativeFor,
  shotLabel,
} from "../../../lib/prompts";
import type { AssetState, H3Prompt, Keyframe, Project } from "../../../lib/types";

/**
 * 提示词统一管理的行模型。
 *
 * 一份提示词有两个来源：实体上存着的（人改过或模型回写过），或者按当前模板现拼的。
 * 这里把两者都摊平出来，并且标清楚是哪一种 —— 「模板拼装」的按钮不能悄悄盖掉手改内容。
 */

export type PromptTarget =
  | { kind: "character"; characterId: string }
  | { kind: "variation"; characterId: string; variationId: string }
  | { kind: "scene"; sceneId: string }
  | { kind: "keyframe"; shotId: string; frameType: "start" | "end" }
  | { kind: "video"; shotId: string };

export type PromptGroup = "characters" | "scenes" | "keyframes" | "videos";

export interface PromptRow {
  key: string;
  target: PromptTarget;
  group: PromptGroup;
  name: string;
  summary: string;
  /** 投产用的完整提示词 */
  prompt: string;
  negative: string;
  /** 角色才有：跨镜头必须一致的特征 */
  coreFeatures?: string;
  /** 视频段才有：H3 官方三段式 */
  h3?: H3Prompt;
  status?: AssetState;
  /** true = 实体上存着这份文本；false = 现在按模板拼出来的 */
  stored: boolean;
  /** 有没有产物（定妆图 / 关键帧图 / 视频段） */
  hasMedia: boolean;
  mediaId?: string | null;
  children?: PromptRow[];
}

export interface PromptDraft {
  target: PromptTarget;
  prompt: string;
  negative: string;
  coreFeatures: string;
  h3: H3Prompt;
}

export const GROUP_LABEL: Record<PromptGroup, string> = {
  characters: "角色",
  scenes: "场景",
  keyframes: "镜头关键帧",
  videos: "视频提示词",
};

export interface GroupedRows {
  characters: PromptRow[];
  scenes: PromptRow[];
  keyframes: PromptRow[];
  videos: PromptRow[];
}

function hasMedia(ids: (string | null | undefined)[]): boolean {
  return ids.some((x) => !!x);
}

/** 关键帧要不要挂「角色一致性」段：有参考图可喂的时候才挂，和生成请求的口径一致 */
function refsAvailable(project: Project, sceneId: string | null, characterIds: string[]): boolean {
  const scene = project.data.scenes.find((s) => s.id === sceneId);
  const chars = project.data.characters.filter((c) => characterIds.includes(c.id));
  return hasMedia(scene?.refMediaIds ?? []) || chars.some((c) => hasMedia(c.refMediaIds));
}

export function buildRows(project: Project): GroupedRows {
  const config = project.config;

  const characters: PromptRow[] = project.data.characters.map((c) => {
    const stored = !!c.visualPrompt?.trim();
    return {
      key: `char-${c.id}`,
      target: { kind: "character", characterId: c.id } as PromptTarget,
      group: "characters" as const,
      name: c.name || "未命名角色",
      summary: [c.gender, c.age, c.desc].filter(Boolean).join(" · ") || "还没有描述",
      prompt: stored ? (c.visualPrompt as string) : buildCharacterPrompt(c, config),
      negative: c.negativePrompt ?? negativeFor("character"),
      coreFeatures: c.coreFeatures ?? "",
      status: c.status,
      stored,
      hasMedia: hasMedia(c.refMediaIds),
      mediaId: c.refMediaIds[0] ?? null,
      children: c.variations.map((v) => {
        const vStored = !!v.visualPrompt?.trim();
        return {
          key: `var-${c.id}-${v.id}`,
          target: { kind: "variation", characterId: c.id, variationId: v.id } as PromptTarget,
          group: "characters" as const,
          name: `${c.name || "未命名角色"} · ${v.name || "变体"}`,
          summary: v.desc || "服装/光照变体，只换穿着，外形必须与定妆图一致",
          prompt: vStored ? (v.visualPrompt as string) : buildVariationPrompt(c, v, config),
          negative: v.negativePrompt ?? negativeFor("character"),
          status: v.status,
          stored: vStored,
          hasMedia: hasMedia(v.refMediaIds),
          mediaId: v.refMediaIds[0] ?? null,
        };
      }),
    };
  });

  const scenes: PromptRow[] = project.data.scenes.map((s) => {
    const stored = !!s.visualPrompt?.trim();
    return {
      key: `scene-${s.id}`,
      target: { kind: "scene", sceneId: s.id } as PromptTarget,
      group: "scenes" as const,
      name: s.name || "未命名场景",
      summary: [s.location, s.time, s.atmosphere].filter(Boolean).join(" · ") || s.desc || "还没有描述",
      prompt: stored ? (s.visualPrompt as string) : buildScenePrompt(s, config),
      negative: s.negativePrompt ?? negativeFor("scene"),
      status: s.status,
      stored,
      hasMedia: hasMedia(s.refMediaIds),
      mediaId: s.refMediaIds[0] ?? null,
    };
  });

  const keyframes: PromptRow[] = [];
  const videos: PromptRow[] = [];
  for (const shot of [...project.data.shots].sort((a, b) => a.index - b.index)) {
    const label = shotLabel(shot.id, shot.index);
    const scene = project.data.scenes.find((s) => s.id === shot.sceneId);
    const frames: Keyframe[] = shot.keyframes?.length
      ? shot.keyframes
      : [{ id: `${shot.id}-start`, type: "start", visualPrompt: "", status: "pending" }];
    for (const k of frames) {
      const stored = !!k.visualPrompt?.trim();
      keyframes.push({
        key: `kf-${shot.id}-${k.type}`,
        target: { kind: "keyframe", shotId: shot.id, frameType: k.type } as PromptTarget,
        group: "keyframes" as const,
        name: `镜 ${label} · ${k.type === "start" ? "起始帧" : "结束帧"}`,
        summary: `${scene?.name ?? "未分配场景"} · ${shot.cameraMovement || "固定"} · ${shot.action || "没有动作描述"}`,
        prompt: stored
          ? k.visualPrompt
          : buildKeyframePrompt({
              base: shot.action || `${scene?.name ?? ""}${scene?.time ? `（${scene.time}）` : ""}`,
              visualStyle: config.visualStyle,
              cameraMovement: shot.cameraMovement,
              frameType: k.type,
              withConsistency: refsAvailable(project, shot.sceneId, shot.characterIds),
            }),
        negative: k.negativePrompt ?? negativeFor("keyframe"),
        status: k.status,
        stored,
        hasMedia: !!k.mediaId,
        mediaId: k.mediaId ?? null,
      });
    }

    const h3 = shot.h3Prompt ?? { integrated: "", soundscape: "", music: "" };
    const vStored = !!shot.videoPrompt?.trim();
    videos.push({
      key: `vid-${shot.id}`,
      target: { kind: "video", shotId: shot.id } as PromptTarget,
      group: "videos" as const,
      name: `镜 ${label} · 视频段（${shot.durationSec || 0}s / ${shot.frameCount || 0} 帧）`,
      summary: `${scene?.name ?? "未分配场景"} · ${shot.shotSize || "中景"} · ${shot.action || "没有动作描述"}`,
      prompt: vStored ? (shot.videoPrompt as string) : h3PromptText(h3),
      negative: "",
      h3,
      status: shot.state === "completed" ? "completed" : shot.state === "failed" ? "failed" : shot.state === "generating" ? "generating" : "pending",
      stored: vStored,
      hasMedia: shot.videoMediaIds.length > 0,
      mediaId: shot.videoMediaIds[0] ?? null,
    });
  }

  return { characters, scenes, keyframes, videos };
}

/** 按当前模板重新拼装出来的预览值：只填进编辑器，用户点保存才写回数据模型 */
export function rebuildTemplate(project: Project, target: PromptTarget): Pick<PromptDraft, "prompt" | "negative" | "coreFeatures" | "h3"> {
  const config = project.config;
  if (target.kind === "character") {
    const c = project.data.characters.find((x) => x.id === target.characterId);
    if (!c) return { prompt: "", negative: "", coreFeatures: "", h3: { integrated: "", soundscape: "", music: "" } };
    return {
      prompt: buildCharacterPrompt({ ...c, visualPrompt: "" }, config),
      negative: negativeFor("character"),
      coreFeatures: c.coreFeatures ?? "",
      h3: { integrated: "", soundscape: "", music: "" },
    };
  }
  if (target.kind === "variation") {
    const c = project.data.characters.find((x) => x.id === target.characterId);
    const v = c?.variations.find((x) => x.id === target.variationId);
    if (!c || !v) return { prompt: "", negative: "", coreFeatures: "", h3: { integrated: "", soundscape: "", music: "" } };
    return { prompt: buildVariationPrompt(c, { ...v, visualPrompt: "" }, config), negative: negativeFor("character"), coreFeatures: "", h3: { integrated: "", soundscape: "", music: "" } };
  }
  if (target.kind === "scene") {
    const s = project.data.scenes.find((x) => x.id === target.sceneId);
    if (!s) return { prompt: "", negative: "", coreFeatures: "", h3: { integrated: "", soundscape: "", music: "" } };
    return { prompt: buildScenePrompt({ ...s, visualPrompt: "" }, config), negative: negativeFor("scene"), coreFeatures: "", h3: { integrated: "", soundscape: "", music: "" } };
  }
  const shot = project.data.shots.find((x) => x.id === target.shotId);
  if (!shot) return { prompt: "", negative: "", coreFeatures: "", h3: { integrated: "", soundscape: "", music: "" } };
  if (target.kind === "keyframe") {
    const existing = shot.keyframes?.find((k) => k.type === target.frameType);
    const scene = project.data.scenes.find((s) => s.id === shot.sceneId);
    // 主体段沿用现有文本（可能是人写的），只重拼风格 / 运镜 / 一致性三段
    const base = extractBasePrompt(existing?.visualPrompt ?? "", shot.action || scene?.name || "");
    return {
      prompt: buildKeyframePrompt({
        base,
        visualStyle: project.config.visualStyle,
        cameraMovement: shot.cameraMovement,
        frameType: target.frameType,
        withConsistency: refsAvailable(project, shot.sceneId, shot.characterIds),
      }),
      negative: negativeFor("keyframe"),
      coreFeatures: "",
      h3: { integrated: "", soundscape: "", music: "" },
    };
  }
  const scene = project.data.scenes.find((s) => s.id === shot.sceneId);
  const chars = project.data.characters.filter((c) => shot.characterIds.includes(c.id));
  const h3 = buildH3Prompt(shot, scene, chars, config);
  return { prompt: h3PromptText(h3), negative: "", coreFeatures: "", h3 };
}

/** 编辑目标还在不在：实体被删掉时不给写回，避免「保存成功」但其实什么都没改 */
export function entityExists(project: Project, t: PromptTarget): boolean {
  if (t.kind === "character") return project.data.characters.some((x) => x.id === t.characterId);
  if (t.kind === "variation")
    return project.data.characters.some((c) => c.id === t.characterId && c.variations.some((v) => v.id === t.variationId));
  if (t.kind === "scene") return project.data.scenes.some((x) => x.id === t.sceneId);
  return project.data.shots.some((x) => x.id === t.shotId);
}

/** 把编辑器的值写回数据模型（返回新对象，不原地改 query 缓存） */
export function applyDraft(project: Project, draft: PromptDraft): Project {
  const next: Project = structuredClone(project);
  const t = draft.target;
  if (t.kind === "character") {
    const c = next.data.characters.find((x) => x.id === t.characterId);
    if (c) {
      c.visualPrompt = draft.prompt;
      c.negativePrompt = draft.negative;
      c.coreFeatures = draft.coreFeatures;
    }
    return next;
  }
  if (t.kind === "variation") {
    const c = next.data.characters.find((x) => x.id === t.characterId);
    const v = c?.variations.find((x) => x.id === t.variationId);
    if (v) {
      v.visualPrompt = draft.prompt;
      v.negativePrompt = draft.negative;
    }
    return next;
  }
  if (t.kind === "scene") {
    const s = next.data.scenes.find((x) => x.id === t.sceneId);
    if (s) {
      s.visualPrompt = draft.prompt;
      s.negativePrompt = draft.negative;
    }
    return next;
  }
  const shot = next.data.shots.find((x) => x.id === t.shotId);
  if (!shot) return next;
  if (t.kind === "keyframe") {
    shot.keyframes ??= [];
    const kf = shot.keyframes.find((k) => k.type === t.frameType);
    if (kf) {
      kf.visualPrompt = draft.prompt;
      kf.negativePrompt = draft.negative;
    } else {
      shot.keyframes.push({
        id: `${shot.id}-${t.frameType}`,
        type: t.frameType,
        visualPrompt: draft.prompt,
        negativePrompt: draft.negative,
        status: "pending",
        mediaId: null,
        jobId: null,
      });
    }
    return next;
  }
  shot.videoPrompt = draft.prompt;
  shot.h3Prompt = draft.h3;
  return next;
}

function hay(row: PromptRow): string {
  return [
    row.name,
    row.summary,
    row.prompt,
    row.negative,
    row.coreFeatures ?? "",
    row.h3 ? h3PromptText(row.h3) : "",
    ...(row.children ?? []).map((c) => `${c.name} ${c.summary} ${c.prompt}`),
  ]
    .join("\n")
    .toLowerCase();
}

export function filterRows(rows: PromptRow[], q: string): PromptRow[] {
  const needle = q.trim().toLowerCase();
  if (!needle) return rows;
  return rows.filter((r) => hay(r).includes(needle));
}

export function countRows(rows: PromptRow[]): number {
  return rows.reduce((a, r) => a + 1 + (r.children?.length ?? 0), 0);
}
