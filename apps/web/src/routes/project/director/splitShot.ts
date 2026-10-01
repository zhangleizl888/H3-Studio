/**
 * AI 拆分镜头：把一个镜头拆成 2-3 个子镜。
 *
 * 子镜 id 用 prompts.subShotIds（'shot-1-2'），这样卡片编号能显示成 001-2；
 * 首尾帧提示词按子镜自己的动作与运镜重算（buildKeyframePrompt），
 * 视频段提示词按子镜重算（buildH3Prompt）—— 沿用父镜的提示词会让三段都念同一句话。
 */

import { buildH3Prompt, buildKeyframePrompt, h3FrameCount, subShotIds } from "../../../lib/prompts";
import type { LlmShot, Project, Shot } from "../../../lib/types";
import { FRAME_TYPES } from "./common";

export function buildSubShots(parent: Shot, list: LlmShot[], project: Project): Shot[] {
  const n = Math.min(3, Math.max(2, list.length || 2));
  const ids = subShotIds(parent.id, n);
  const scene = project.data.scenes.find((s) => s.id === parent.sceneId);
  const chars = project.data.characters.filter((c) => parent.characterIds.includes(c.id));
  const per = Math.max(2, Math.round((parent.durationSec || 6) / n));

  return ids.map((sid, i) => {
    const src = list[i];
    const action = (src?.action || `${parent.action}（第 ${i + 1}/${n} 段）`).trim();
    const movement = src?.cameraMovement || parent.cameraMovement;
    const sub: Shot = {
      ...parent,
      id: sid,
      index: parent.index,
      sceneId: parent.sceneId,
      characterIds: [...parent.characterIds],
      variationByChar: { ...(parent.variationByChar ?? {}) },
      action,
      dialogue: src?.dialogue ?? (i === 0 ? parent.dialogue ?? "" : ""),
      cameraMovement: movement,
      shotSize: src?.shotSize || parent.shotSize,
      startFrameMediaId: null,
      endFrameMediaId: null,
      keyframes: [],
      videoMediaIds: [],
      videoPrompt: "",
      durationSec: per,
      frameCount: h3FrameCount(per),
      state: "idle",
      jobId: null,
      locked: false,
      parentShotId: parent.parentShotId ?? parent.id,
      h3Prompt: { integrated: "", soundscape: "", music: "" },
    };
    sub.h3Prompt = buildH3Prompt(sub, scene, chars, project.config);
    sub.keyframes = FRAME_TYPES.map((t) => ({
      id: `${sid}-${t}`,
      type: t,
      visualPrompt: buildKeyframePrompt({
        base: action,
        visualStyle: project.config.visualStyle,
        cameraMovement: movement,
        frameType: t,
        withConsistency: chars.length > 0,
      }),
      status: "pending" as const,
    }));
    return sub;
  });
}

/** 拆完就地替换父镜，index 按新顺序重排（卡片编号跟 index 走） */
export function replaceShot(shots: Shot[], shotId: string, subs: Shot[]): Shot[] {
  return shots.flatMap((s) => (s.id === shotId ? subs : [s])).map((s, i) => ({ ...s, index: i + 1 }));
}
