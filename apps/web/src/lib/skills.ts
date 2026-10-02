/**
 * 技能库的前端共用件：环节文案、按环节筛、选中项的显示。
 *
 * 只有筛与文案放得下这一处 —— 正文怎么拼进提示词是后端的事（routes_skills.skills_block），
 * 前端如果也拼一份，就会出现「界面显示一串、真提交另一串」。
 */

import type { Skill, SkillStage } from "./types";

export const SKILL_STAGE_LABEL: Record<SkillStage, string> = {
  general: "通用",
  script: "剧本",
  asset: "画面",
  video: "视频",
};

export const SKILL_STAGE_HINT: Record<SkillStage, string> = {
  general: "哪一步都能挂",
  script: "剧本创作与对话改稿",
  asset: "角色定妆与场景概念图的提示词框",
  video: "镜头详情里的视频提示词框",
};

/**
 * 这一步能选的技能：本环节的 + 通用的。
 *
 * 按 stage 精确筛而不是「全都给」：在出片框里挑到编剧写法，模型只会把它当画面描述念一遍。
 */
export function skillsForStage(all: Skill[], stage: SkillStage): Skill[] {
  return all.filter((s) => s.stage === stage || s.stage === "general");
}

/** 选中的那几条（按用户挑的顺序）。库里已被删掉的 id 会掉出来，界面据此显示「已失效」 */
export function pickedSkills(all: Skill[], ids: string[] | undefined): { items: Skill[]; missing: string[] } {
  const items = (ids ?? []).map((id) => all.find((s) => s.id === id)).filter((s): s is Skill => !!s);
  const missing = (ids ?? []).filter((id) => !all.some((s) => s.id === id));
  return { items, missing };
}

/** 一行摘要，用在卡片与提示词框底下 */
export function skillSummary(items: Skill[], missing: string[]): string {
  const parts = items.map((s) => s.name);
  if (missing.length) parts.push(`${missing.length} 条已从技能库删掉`);
  return parts.join("、");
}
