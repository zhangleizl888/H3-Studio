/**
 * 剧本创作视图的纯数据层：拆解结果 → 项目实体、分镜结果 → 镜头表。
 *
 * 三条不可让的规矩：
 *   1. 拆解回来的角色/场景按名字增量合并，**绝不覆盖**用户手改过的提示词、参考图、seed；
 *   2. 镜头按序号对齐复用 id，重跑一次规划不能把已经出好的片变成孤儿；
 *   3. 关键帧提示词只在「这一镜的文字真的变了」时才重拼，否则保留手改。
 */

import { buildH3Prompt, buildKeyframePrompt, extractBasePrompt, h3FrameCount } from "../../../lib/prompts";
import type { Character, Keyframe, LlmShot, Project, ProjectConfig, Scene, ScriptData, Shot } from "../../../lib/types";
import { uid } from "../../../lib/utils";

/** 与后端 apps/api/app/llm.py 的 MAX_INPUT_CHARS 对齐：超了后端直接返回中文错误 */
export const LLM_MAX_INPUT_CHARS = 24_000;

/** 拆解 / 分镜在本机 27B 上的实测耗时，用来如实告诉用户等多久 */
export const LLM_ETA_HINT = "本机模型单槽串行：拆解实测 30–170 秒，分镜 22–113 秒，预计 1–3 分钟";

/** 时段词：场景名「内廷-未央宫偏殿-日」的最后一段靠它认 */
const TIME_HINTS = ["日", "夜", "晨", "暮", "昏", "昼", "深夜", "清晨", "傍晚", "黄昏", "白天", "夜晚", "凌晨", "正午"];

const str = (v: unknown): string => (typeof v === "string" ? v.trim() : "");

const rows = (v: unknown): Record<string, unknown>[] =>
  Array.isArray(v) ? v.filter((x): x is Record<string, unknown> => !!x && typeof x === "object" && !Array.isArray(x)) : [];

/** 错误原样转中文：后端 409 / 400 的 message 就是给人看的，不要包一层「操作失败」 */
export function errText(e: unknown): string {
  if (e instanceof Error && e.message.trim()) return e.message.trim();
  return String(e ?? "未知错误");
}

/** 「内廷-未央宫偏殿-日」→ 地点 / 时段 / 氛围摘要，拍摄清单的场景头用它 */
export function splitSceneName(name: string, desc: string): { location: string; time: string; atmosphere: string } {
  const parts = name
    .split(/[-·—/|｜]/)
    .map((s) => s.trim())
    .filter(Boolean);
  let location = name;
  let time = "";
  if (parts.length > 1) {
    const last = parts[parts.length - 1];
    if (TIME_HINTS.some((t) => last.includes(t))) {
      time = last;
      location = parts.slice(0, -1).join(" · ");
    }
  }
  if (!time) time = TIME_HINTS.find((t) => name.includes(t)) ?? "";
  const atmosphere = str(desc).split(/[，。；;、]/)[0] ?? "";
  return { location, time, atmosphere: atmosphere.slice(0, 48) };
}

/** 模型违约少字段也不能炸：兜成 ScriptData 的形状再往下走 */
export function normalizeParsed(raw: unknown): ScriptData {
  const o = (raw && typeof raw === "object" ? raw : {}) as Record<string, unknown>;
  return {
    title: str(o.title),
    logline: str(o.logline),
    genre: Array.isArray(o.genre) ? o.genre.map(str).filter(Boolean) : [],
    characters: rows(o.characters).map((c) => {
      const t = (c.traits && typeof c.traits === "object" ? c.traits : {}) as Record<string, unknown>;
      const traits: NonNullable<Character["traits"]> = {};
      for (const k of ["age", "build", "hair", "costume", "palette", "signature"] as const) {
        const v = str(t[k]);
        if (v) traits[k] = v;
      }
      return { name: str(c.name), desc: str(c.desc), traits };
    }),
    scenes: rows(o.scenes).map((s) => ({ name: str(s.name), desc: str(s.desc) })),
    beats: rows(o.beats).map((b) => ({ text: str(b.text), sceneName: str(b.sceneName) })),
  };
}

/** 相邻同场景的节拍并成一段，梗概读起来才是散文而不是清单 */
function paragraphsFromBeats(beats: ScriptData["beats"]): ScriptData["storyParagraphs"] {
  const out: NonNullable<ScriptData["storyParagraphs"]> = [];
  for (const b of beats) {
    if (!b.text) continue;
    const last = out[out.length - 1];
    if (last && (last.sceneRefId ?? "") === (b.sceneName ?? "")) {
      out[out.length - 1] = { ...last, text: `${last.text}\n${b.text}` };
      continue;
    }
    out.push({ id: out.length + 1, text: b.text, sceneRefId: b.sceneName || undefined });
  }
  return out;
}

const randSeed = () => Math.floor(Math.random() * 1_000_000_000);

/**
 * 拆解结果并进项目：同名实体只补空、只更新外形描述，
 * visualPrompt / negativePrompt / coreFeatures / refMediaIds / seed / locked / variations 一律不碰。
 */
export function mergeScriptEntities(
  project: Project,
  parsed: ScriptData,
): { script: ScriptData; characters: Character[]; scenes: Scene[] } {
  const characters = project.data.characters.map((c) => ({ ...c }));
  for (const c of parsed.characters) {
    if (!c.name) continue;
    const traits = c.traits ?? {};
    const at = characters.findIndex((x) => x.name.trim() === c.name);
    if (at >= 0) {
      const hit = characters[at];
      characters[at] = {
        ...hit,
        desc: c.desc || hit.desc,
        age: hit.age || traits.age || "",
        traits: { ...hit.traits, ...traits },
      };
      continue;
    }
    characters.push({
      id: uid("c"),
      name: c.name,
      desc: c.desc,
      age: traits.age || "",
      traits,
      refMediaIds: [],
      variations: [],
      seed: randSeed(),
      locked: false,
      status: "pending",
    });
  }

  const scenes = project.data.scenes.map((s) => ({ ...s }));
  for (const sc of parsed.scenes) {
    if (!sc.name) continue;
    const at = scenes.findIndex((x) => x.name.trim() === sc.name);
    const split = splitSceneName(sc.name, sc.desc);
    if (at >= 0) {
      const hit = scenes[at];
      scenes[at] = {
        ...hit,
        desc: sc.desc || hit.desc,
        location: hit.location || split.location,
        time: hit.time || split.time,
        atmosphere: hit.atmosphere || split.atmosphere,
      };
      continue;
    }
    scenes.push({
      id: uid("sc"),
      name: sc.name,
      desc: sc.desc,
      location: split.location,
      time: split.time,
      atmosphere: split.atmosphere,
      refMediaIds: [],
      status: "pending",
    });
  }

  const prev = project.data.script;
  const script: ScriptData = {
    ...parsed,
    title: parsed.title || prev?.title || project.name,
    logline: parsed.logline || prev?.logline || "",
    // 段落一旦存在就认用户的：重跑拆解不该把改过的梗概打回节拍列表
    storyParagraphs: prev?.storyParagraphs?.length ? prev.storyParagraphs : paragraphsFromBeats(parsed.beats),
  };
  return { script, characters, scenes };
}

/** 喂给分镜用途的简报：角色名与场景名必须来自给定列表，所以把实体名字原样带上 */
export function storyboardBrief(script: ScriptData, characters: Character[], scenes: Scene[], targetSec: number, fallback: string): string {
  const pack = (withBeats: boolean) =>
    JSON.stringify({
      title: script.title ?? "",
      logline: script.logline ?? "",
      genre: script.genre ?? [],
      targetSec,
      characters: characters.map((c) => ({ name: c.name, desc: c.desc, gender: c.gender ?? "", age: c.age ?? "" })),
      scenes: scenes.map((s) => ({ name: s.name, location: s.location ?? "", time: s.time ?? "", desc: s.desc })),
      beats: withBeats ? script.beats ?? [] : (script.beats ?? []).map((b) => ({ text: b.text.slice(0, 60), sceneName: b.sceneName ?? "" })),
    });
  for (const text of [pack(true), pack(false)]) {
    if (text.length <= LLM_MAX_INPUT_CHARS) return text;
  }
  // 结构本身就超限（角色/场景上千个）：退回截断原文，让模型至少能看到故事
  return fallback.slice(0, LLM_MAX_INPUT_CHARS);
}

function keyframe(shotId: string, type: "start" | "end", visualPrompt: string, prev?: Keyframe): Keyframe {
  return {
    id: `${shotId}-${type}`,
    type,
    visualPrompt,
    mediaId: prev?.mediaId ?? null,
    status: prev?.status === "completed" ? "completed" : "pending",
    jobId: prev?.jobId ?? null,
  };
}

/**
 * 分镜表 → 镜头实体。
 *
 * 手改判定：这一镜的 action 没变、但关键帧提示词的画面主体段和这次要拼的不一样 →
 * 只能是人改过，保留；action 变了则重拼（模型给了新的画面描述）。
 */
export function buildShots(list: LlmShot[], characters: Character[], scenes: Scene[], config: ProjectConfig, prevShots: Shot[]): Shot[] {
  const byIndex = new Map(prevShots.map((s) => [s.index, s]));
  const charByName = new Map(characters.map((c) => [c.name.trim(), c]));
  const sceneByName = new Map(scenes.map((s) => [s.name.trim(), s]));

  return list.map((s, i) => {
    const index = Number.isFinite(s.index) && s.index > 0 ? s.index : i + 1;
    const prev = byIndex.get(index);
    const durationSec = Math.max(1, Math.min(15, Math.round(s.durationSec || 4)));
    const cameraMovement = str(s.cameraMovement) || "固定";
    const shotSize = str(s.shotSize) || "中景";
    const action = str(s.action);
    const scene = sceneByName.get(str(s.sceneName));
    const chars = (s.characterNames ?? []).map((n) => charByName.get(str(n))).filter((c): c is Character => !!c);
    const base = str(s.visualPrompt) || action || (scene ? `${scene.name}${scene.time ? `（${scene.time}）` : ""}` : "");
    const actionUnchanged = !!prev && prev.action.trim() === action.trim();

    const frameFor = (type: "start" | "end"): Keyframe => {
      const prevKf = prev?.keyframes?.find((k) => k.type === type);
      const generated = buildKeyframePrompt({
        base,
        visualStyle: config.visualStyle,
        cameraMovement,
        frameType: type,
        withConsistency: chars.length > 0,
      });
      const kept = prevKf?.visualPrompt?.trim();
      const edited = !!kept && extractBasePrompt(kept, "") !== base.trim();
      return keyframe(prev?.id ?? uid("s"), type, actionUnchanged && edited ? kept! : generated, prevKf);
    };

    const id = prev?.id ?? uid("s");
    const shot: Shot = {
      id,
      index,
      sceneId: scene?.id ?? null,
      characterIds: chars.map((c) => c.id),
      variationByChar: prev?.variationByChar,
      action,
      dialogue: str(s.dialogue),
      cameraMovement,
      shotSize,
      startFrameMediaId: prev?.startFrameMediaId ?? null,
      endFrameMediaId: prev?.endFrameMediaId ?? null,
      keyframes: [frameFor("start"), frameFor("end")],
      videoMediaIds: prev?.videoMediaIds ?? [],
      videoPrompt: prev?.videoPrompt,
      durationSec,
      frameCount: h3FrameCount(durationSec),
      seed: prev?.seed ?? randSeed(),
      locked: prev?.locked ?? false,
      instanceId: prev?.instanceId ?? config.videoInstanceId ?? null,
      workflowKey: prev?.workflowKey ?? config.h3WorkflowKey,
      turbo: prev?.turbo ?? null,
      h3Prompt: { integrated: "", soundscape: "", music: "" },
      state: prev?.videoMediaIds?.length ? "completed" : "idle",
      continuesPrevious: prev?.continuesPrevious ?? false,
      parentShotId: prev?.parentShotId ?? null,
    };
    shot.h3Prompt = buildH3Prompt(shot, scene, chars, config);
    return shot;
  });
}
