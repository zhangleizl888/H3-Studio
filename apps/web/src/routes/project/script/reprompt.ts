/**
 * 项目换 H3 提示词模式时，把已有镜头按新模式重拼一遍。
 *
 * 走的是本地模板（buildH3Prompt），一次点击毫秒级、不碰 GPU，所以敢在切换时就做；
 * 深度不够是预期的 —— 要导演级内容仍得回导演台让模型按新模式重写。
 *
 * 两条不能悄悄做的事：
 *   1. AI 重写过的镜头默认留下。模板稿会把六段式的 subject_definitions / 表演肌理压回一句话，
 *      一次模式点击不该把几分钟显存换来的稿子降级。
 *   2. 手工写过的 videoPrompt 必须清空，否则新模式根本不生效 —— 提交时优先用这串旧文本，
 *      烧显存的还是上一模式的措辞。清掉之前要先让人数着。
 */

import { buildH3Prompt, h3ModeMeta, h3PromptIncomplete } from "../../../lib/prompts";
import type { Character, H3PromptMode, Project, Scene, Shot } from "../../../lib/types";

export interface RepromptPlan {
  mode: H3PromptMode;
  modeName: string;
  total: number;
  /** 已经是这个模式的模板稿，重拼也是原地踏步 */
  already: number;
  /** 带 AI 重写标记、默认不动的镜数 */
  aiKept: number;
  /** 会被本地模板重拼的镜数 */
  toRebuild: number;
  /** 其中手写过提交文本、会被清空的镜数 */
  clearingVideoPrompt: number;
}

export interface RepromptResult extends RepromptPlan {
  shots: Shot[];
  /** 重拼后结构化字段仍不齐的镜头：本地模板填不满，要去导演台让模型重写 */
  incomplete: { index: number; reason: string }[];
}

export function planReprompt(project: Project, mode: H3PromptMode, includeAi = false): RepromptPlan {
  const shots = project.data.shots ?? [];
  let already = 0;
  let aiKept = 0;
  let clearing = 0;
  for (const s of shots) {
    const p = s.h3Prompt;
    if (p?.aiRewrittenAt && !includeAi) {
      aiKept++;
      continue;
    }
    if (p?.mode === mode) {
      already++;
      continue;
    }
    if (s.videoPrompt?.trim()) clearing++;
  }
  return {
    mode,
    modeName: h3ModeMeta(mode).name,
    total: shots.length,
    already,
    aiKept,
    toRebuild: shots.length - already - aiKept,
    clearingVideoPrompt: clearing,
  };
}

export function repromptShots(project: Project, mode: H3PromptMode, includeAi = false): RepromptResult {
  const plan = planReprompt(project, mode, includeAi);
  const config = { ...project.config, h3PromptMode: mode };
  const scenes = new Map<string, Scene>(project.data.scenes.map((s) => [s.id, s]));
  const charsById = new Map<string, Character>(project.data.characters.map((c) => [c.id, c]));
  const incomplete: { index: number; reason: string }[] = [];

  const shots = (project.data.shots ?? []).map((shot) => {
    const p = shot.h3Prompt;
    if (p?.aiRewrittenAt && !includeAi) return shot;
    if (p?.mode === mode) return shot;

    const scene = shot.sceneId ? scenes.get(shot.sceneId) : undefined;
    const cast = shot.characterIds.map((id) => charsById.get(id)).filter((c): c is Character => !!c);
    const next: Shot = { ...shot, h3Prompt: buildH3Prompt(shot, scene, cast, config), videoPrompt: "" };
    const reason = h3PromptIncomplete(next.h3Prompt);
    if (reason) incomplete.push({ index: next.index, reason });
    return next;
  });

  return { ...plan, shots, incomplete };
}
