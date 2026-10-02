/**
 * 生成请求层：把项目里的角色/场景/镜头填成后端的 template+slots，再把产物挂回实体。
 *
 * 浏览器不拼 graph —— 模型文件名要由实例回答，参考图要由服务端读盘上传，
 * 这两件事都只能在后端做。这里负责的是另一半：槽位取值与「产物属于谁」。
 */

import type { GenerateRequest } from "./api";
import { presetToRequest } from "./preset";
import type { Character, GenPreset, Media, Project, RenderLog, Scene, Shot, Variation } from "./types";
import {
  H3_SIZES,
  IMAGE_SIZES,
  buildCharacterPrompt,
  buildKeyframePrompt,
  buildScenePrompt,
  buildVariationPrompt,
  h3FrameCount,
  h3PromptText,
  negativeFor,
  shotLabel,
} from "./prompts";

/**
 * 出图 / 出片 / 出音色用哪条工作流、哪台实例、哪颗权重：实体上的预设优先，缺省回落项目默认。
 *
 * 具体解析在 lib/preset.ts（与下拉组件共用同一套判据，两处两套说法就会出现
 * 「界面显示 A、请求发出去是 B」）。库里一条都挑不出时后端会回落内置模板并在参数表里写明，
 * 前端不重复那套判断。
 */

/** 生成对象：一个联合标签，决定产物挂回哪里 */
export type GenTarget =
  | { kind: "character"; characterId: string }
  | { kind: "variation"; characterId: string; variationId: string }
  | { kind: "scene"; sceneId: string }
  | { kind: "voice"; characterId: string }
  | { kind: "keyframe"; shotId: string; frameType: "start" | "end" }
  | { kind: "video"; shotId: string };

export const roleFor = (t: GenTarget): string =>
  t.kind === "character" ? "character" : t.kind === "variation" ? "variation" : t.kind === "scene" ? "scene" : t.kind === "voice" ? "voice" : t.kind === "keyframe" ? `keyframe_${t.frameType}` : "video";

export const refFor = (t: GenTarget): string =>
  t.kind === "character" || t.kind === "voice" ? t.characterId : t.kind === "variation" ? `${t.characterId}:${t.variationId}` : t.kind === "scene" ? t.sceneId : t.kind === "keyframe" ? `${t.shotId}:${t.frameType}` : t.shotId;

export function findShot(project: Project, shotId: string): Shot | undefined {
  return project.data.shots.find((s) => s.id === shotId);
}

/** 服务端 media id 才能当参考图；IDB 里 path=idb: 的本地上传还不算（要先 media.put 落服务端） */
export function serverMediaIds(ids: (string | null | undefined)[] | undefined): number[] {
  return (ids ?? [])
    .filter((x): x is string => !!x && /^\d+$/.test(String(x)))
    .map((x) => Number(x));
}

function imageSize(project: Project) {
  const s = IMAGE_SIZES[project.config.aspectRatio] ?? IMAGE_SIZES["16:9"];
  return s;
}

function videoSize(project: Project) {
  const table = H3_SIZES[project.config.aspectRatio] ?? H3_SIZES["16:9"];
  return project.config.resolutionMode === "full" ? table.full : table.preview;
}

/** 角色定妆照。同一角色固定 seed，配合参考图复用才有稳定外形 */
export function characterRequest(project: Project, char: Character): GenerateRequest {
  const { width, height } = imageSize(project);
  return {
    projectId: project.id,
    ...presetToRequest(project, "image", char.preset),
    kind: "image",
    title: `定妆 · ${char.name}`,
    slots: {
      prompt: buildCharacterPrompt(char, project.config),
      negative_prompt: negativeFor("character", char.negativePrompt),
      refs: [],
      width,
      height,
      seed: char.seed || undefined,
    },
    meta: { role: roleFor({ kind: "character", characterId: char.id }), refId: char.id },
    // 技能只交 id：正文由后端读库并进上面那个 prompt 槽（参数表里看到的是并好之后那串）
    skillIds: char.skillIds,
  };
}

/** 服装变体：以角色定妆照为参考图，只换服装 */
export function variationRequest(project: Project, char: Character, variation: Variation): GenerateRequest {
  const { width, height } = imageSize(project);
  return {
    projectId: project.id,
    ...presetToRequest(project, "image", char.preset),
    kind: "image",
    title: `变体 · ${char.name} / ${variation.name}`,
    slots: {
      prompt: buildVariationPrompt(char, variation, project.config),
      negative_prompt: negativeFor("character", variation.negativePrompt),
      refs: serverMediaIds(char.refMediaIds),
      width,
      height,
      seed: char.seed || undefined,
    },
    meta: { role: "variation", refId: `${char.id}:${variation.id}` },
    // 变体沿用它所属角色的技能选择：换了套衣服不该顺手改掉写法要求
    skillIds: char.skillIds,
  };
}

export function sceneRequest(project: Project, scene: Scene): GenerateRequest {
  const { width, height } = imageSize(project);
  return {
    projectId: project.id,
    ...presetToRequest(project, "image", scene.preset),
    kind: "image",
    title: `场景 · ${scene.name}`,
    slots: {
      prompt: buildScenePrompt(scene, project.config),
      negative_prompt: negativeFor("scene", scene.negativePrompt),
      refs: [],
      width,
      height,
    },
    meta: { role: "scene", refId: scene.id },
    skillIds: scene.skillIds,
  };
}

/**
 * 角色的音色样音。
 *
 * audio 这一类没有内置回落模板（后端 job_plan 里就没有），只能靠工作流库里那几条 ——
 * 库里挑不出就如实失败，不会悄悄用出图的路径凑。
 * 参考音频必须是真人语音且配上它真实说过的那句：条件缺一，模型会把十几个字拉成几分钟发声。
 */
export function voiceRequest(project: Project, char: Character): GenerateRequest {
  const voice = char.voice;
  const refs = serverMediaIds(voice?.refAudioIds);
  const text = voice?.testText?.trim() || `我是${char.name}。`;
  return {
    projectId: project.id,
    ...presetToRequest(project, "audio", voice?.preset),
    kind: "audio",
    title: `音色 · ${char.name}`,
    slots: {
      prompt: text,
      ref_audios: refs,
      ref_text: voice?.refText?.trim() || "",
      language: voice?.language || "Auto",
      seed: char.seed || undefined,
      filename_prefix: `h3/voice-${char.name}`,
    },
    meta: { role: "voice", refId: char.id },
  };
}

/** 关键帧：场景图在前、角色图在后当参考图，提示词是三段式 */
export function keyframeRequest(project: Project, shot: Shot, frameType: "start" | "end"): GenerateRequest {
  const scene = project.data.scenes.find((s) => s.id === shot.sceneId);
  const chars = project.data.characters.filter((c) => shot.characterIds.includes(c.id));
  const refs = [...serverMediaIds(scene?.refMediaIds), ...chars.flatMap((c) => serverMediaIds([variationMedia(c, shot) ?? c.refMediaIds[0]]))];
  const base = shot.action?.trim() || `${scene?.name ?? ""}${scene?.time ? `（${scene.time}）` : ""}`;
  const { width, height } = imageSize(project);
  const existing = shot.keyframes?.find((k) => k.type === frameType);
  return {
    projectId: project.id,
    ...presetToRequest(project, "image", scene?.preset),
    kind: "image",
    title: `${frameType === "start" ? "首帧" : "尾帧"} · 镜 ${shot.index}`,
    slots: {
      prompt: existing?.visualPrompt?.trim() || buildKeyframePrompt({ base, visualStyle: project.config.visualStyle, cameraMovement: shot.cameraMovement, frameType, withConsistency: refs.length > 0 }),
      negative_prompt: negativeFor("keyframe", existing?.negativePrompt),
      refs,
      width,
      height,
      resolution: 768,
      filename_prefix: `h3/镜${shotLabel(shot.id, shot.index)}-${frameType === "start" ? "首" : "尾"}帧`,
    },
    meta: { role: `keyframe_${frameType}`, refId: shot.id },
  };
}

/** 该镜头这个角色实际用的那张图（有服装变体就用变体的） */
function variationMedia(char: Character, shot: Shot): string | null {
  const vid = shot.variationByChar?.[char.id];
  if (!vid) return null;
  const v = char.variations.find((x) => x.id === vid);
  return v?.refMediaIds?.[0] ?? null;
}

/** 镜头的起始/结尾帧（keyframes 是结构化真源，旧字段做兜底） */
const startFrameOf = (s: Shot) => s.keyframes?.find((k) => k.type === "start")?.mediaId ?? s.startFrameMediaId;
const endFrameOf = (s: Shot) => s.keyframes?.find((k) => k.type === "end")?.mediaId ?? s.endFrameMediaId;
const videoPromptOf = (s: Shot) => s.videoPrompt?.trim() || h3PromptText(s.h3Prompt);

/**
 * 承接链：从本镜头往回收集连续的「承接上一镜」镜头，链头在前、本镜头是链尾。
 * 本镜头没勾承接时就只有它自己一个，出片照旧走单镜头模板。
 * 项目级开关 continuity 关掉时一律不串链：逐镜独立出片，接缝靠画面首帧与文字锚点接续。
 */
export function videoChainShots(project: Project, shot: Shot): Shot[] {
  if (!project.config.continuity) return [shot];
  const ordered = [...project.data.shots].sort((a, b) => a.index - b.index);
  const at = ordered.findIndex((s) => s.id === shot.id);
  if (at < 0) return [shot];
  let head = at;
  while (head > 0 && ordered[head].continuesPrevious) head -= 1;
  return ordered.slice(head, at + 1);
}

/** 段间重叠帧：h3_chain 的 guide_frames 只认这五档，配置里填了别的就就近取一档 */
function guideFrames(n: number | undefined): string {
  const allowed = [5, 22, 39, 56];
  const want = n ?? 22;
  const hit = allowed.reduce((best, x) => (Math.abs(x - want) < Math.abs(best - want) ? x : best), allowed[0]);
  return String(hit);
}

export function videoRequest(project: Project, shot: Shot): GenerateRequest {
  const [width, height] = videoSize(project);
  const turbo = project.config.resolutionMode === "preview";
  // 老存档的镜头只存了 instanceId，新的一律走 preset：两者取先有那个，别让改动把
  // 「这一镜已经指定过实例」这件事抹平
  const preset: GenPreset | undefined = shot.preset ?? (shot.instanceId ? { instanceId: shot.instanceId } : undefined);
  const gen = presetToRequest(project, "video", preset);

  // 两个以上镜头相连 → 走 SequenceForge 的无缝续拍节点，一条链一个任务。
  // 存档目录按「项目 + 链头 + 链头起始帧」命名：同一链的后续镜头带同名目录进去，
  // 节点就从 latent 存档秒级回放前段、只采样新的一段；换了起始帧就是换了另一条链。
  const chain = videoChainShots(project, shot);
  if (chain.length > 1) {
    const head = chain[0];
    const headStart = serverMediaIds([startFrameOf(head)])[0];
    return {
      projectId: project.id,
      // 续拍固定走内置那条：多段无缝拼接只有 SequenceForge 那个节点做得了，
      // 库里挑的工作流没有这个形状。实例与权重覆盖仍然照用户选的来。
      template: "h3_chain",
      instanceId: gen.instanceId,
      models: gen.models,
      kind: "video_chain",
      title: `长片续拍 · 镜 ${head.index}→${shot.index}（${chain.length} 段）`,
      slots: {
        segments: chain.map((s) => ({
          prompt: videoPromptOf(s),
          seconds: Math.max(1, Math.round(s.durationSec || 5)),
        })),
        archive_dir: `h3s-${project.id}-${head.id}${headStart ? `-${headStart}` : "-t2v"}`,
        seconds: Math.max(1, Math.round(shot.durationSec || 5)),
        first_frame: headStart,
        end_frame: serverMediaIds([endFrameOf(shot)])[0],
        width,
        height,
        guide_frames: guideFrames(project.config.continuityOverlapFrames),
        turbo,
        steps: turbo ? 8 : 25,
        seed: head.seed || undefined,
        filename_prefix: `h3/链${shotLabel(head.id, head.index)}-${shotLabel(shot.id, shot.index)}`,
      },
      meta: { role: "video", refId: shot.id, promptMode: shot.h3Prompt.mode ?? project.config.h3PromptMode },
      // 续拍链没有顶层 prompt：后端把技能并进链尾那一段（本镜头），前段已经在 latent 存档里跑过
      skillIds: shot.skillIds,
    };
  }

  const refs = serverMediaIds([startFrameOf(shot)]);
  const refEnd = serverMediaIds([endFrameOf(shot)]);
  const voiceRefs = serverMediaIds(
    shot.characterIds.flatMap((cid) => project.data.characters.find((c) => c.id === cid)?.voice?.refAudioIds ?? []),
  ).slice(0, 1);
  return {
    projectId: project.id,
    ...gen,
    kind: "video",
    title: `出片 · 镜 ${shot.index}`,
    slots: {
      prompt: videoPromptOf(shot),
      first_frame: refs[0],
      last_frame: refEnd[0],
      // 角色配过音色就把参考音频递过去：能吃 ref_audios 的工作流才会被自动挑中（+2 分）
      ...(voiceRefs.length ? { ref_audios: voiceRefs } : {}),
      width,
      height,
      seconds: Math.max(1, Math.round(shot.durationSec || 5)),
      turbo,
      steps: turbo ? 8 : 25,
      seed: shot.seed || undefined,
      filename_prefix: `h3/镜${shotLabel(shot.id, shot.index)}`,
    },
    meta: { role: "video", refId: shot.id, promptMode: shot.h3Prompt.mode ?? project.config.h3PromptMode },
    skillIds: shot.skillIds,
  };
}

/** 时长 → 合法帧数，导演台显示用 */
export function frameCountFor(seconds: number): number {
  return h3FrameCount(seconds);
}

/**
 * 把成功的产物挂回实体。返回是否真的改了数据。
 *
 * 只认 succeeded：中途的 running 快照没有 outputs，写进去就是「时间轴一片绿却没有片」。
 */
export function attachResult(project: Project, target: GenTarget, media: Media[]): boolean {
  const ids = media.map((m) => String(m.id));
  if (!ids.length) return false;
  const first = ids[0];
  const data = project.data;
  if (target.kind === "character") {
    const c = data.characters.find((x) => x.id === target.characterId);
    if (!c) return false;
    c.refMediaIds = [first, ...c.refMediaIds.filter((x) => x !== first)];
    c.status = "completed";
    return true;
  }
  if (target.kind === "variation") {
    const c = data.characters.find((x) => x.id === target.characterId);
    const v = c?.variations.find((x) => x.id === target.variationId);
    if (!v) return false;
    v.refMediaIds = [first, ...v.refMediaIds.filter((x) => x !== first)];
    v.status = "completed";
    return true;
  }
  if (target.kind === "scene") {
    const s = data.scenes.find((x) => x.id === target.sceneId);
    if (!s) return false;
    s.refMediaIds = [first, ...s.refMediaIds.filter((x) => x !== first)];
    s.status = "completed";
    return true;
  }
  if (target.kind === "voice") {
    const c = data.characters.find((x) => x.id === target.characterId);
    if (!c) return false;
    // 样本按时间倒序攒着：音色是「听一下决定要不要」的东西，覆盖掉上一次就等于让人重跑
    c.voice ??= { refAudioIds: [], sampleMediaIds: [] };
    c.voice.sampleMediaIds = [...ids, ...c.voice.sampleMediaIds.filter((x) => !ids.includes(x))];
    c.voice.status = "completed";
    return true;
  }
  const shot = findShot(project, target.shotId);
  if (!shot) return false;
  if (target.kind === "keyframe") {
    shot.keyframes ??= [];
    const kf = shot.keyframes.find((k) => k.type === target.frameType) ?? { id: `${shot.id}-${target.frameType}`, type: target.frameType, visualPrompt: "", status: "pending" as const };
    kf.mediaId = first;
    kf.status = "completed";
    if (!shot.keyframes.includes(kf)) shot.keyframes.push(kf);
    if (target.frameType === "start") shot.startFrameMediaId = first;
    else shot.endFrameMediaId = first;
    return true;
  }
  shot.videoMediaIds = [...new Set([...ids, ...shot.videoMediaIds])];
  shot.state = "completed";
  return true;
}

export function markFailed(project: Project, target: GenTarget, message: string): void {
  const data = project.data;
  const log: RenderLog = {
    ts: new Date().toISOString(),
    shotId: target.kind === "keyframe" || target.kind === "video" ? target.shotId : "",
    kind: target.kind === "video" ? "video" : target.kind === "voice" ? "audio" : "image",
    status: "failed" as const,
    instanceId: "",
    jobId: "",
    error: message,
    resourceType: target.kind === "variation" ? "character-variation" : target.kind,
    resourceId: refFor(target),
  };
  data.renderLogs.unshift(log);
  if (data.renderLogs.length > 200) data.renderLogs.length = 200;
  if (target.kind === "character") {
    const c = data.characters.find((x) => x.id === target.characterId);
    if (c) c.status = "failed";
  } else if (target.kind === "scene") {
    const s = data.scenes.find((x) => x.id === target.sceneId);
    if (s) s.status = "failed";
  } else if (target.kind === "variation") {
    const v = data.characters.find((x) => x.id === target.characterId)?.variations.find((x) => x.id === target.variationId);
    if (v) v.status = "failed";
  } else if (target.kind === "voice") {
    const voice = data.characters.find((x) => x.id === target.characterId)?.voice;
    if (voice) voice.status = "failed";
  } else {
    const shot = findShot(project, target.shotId);
    if (shot && target.kind === "video") shot.state = "failed";
    if (shot && target.kind === "keyframe") {
      const kf = shot.keyframes?.find((k) => k.type === target.frameType);
      if (kf) kf.status = "failed";
    }
  }
}

export function markGenerating(project: Project, target: GenTarget): void {
  const data = project.data;
  if (target.kind === "character") {
    const c = data.characters.find((x) => x.id === target.characterId);
    if (c) c.status = "generating";
  } else if (target.kind === "scene") {
    const s = data.scenes.find((x) => x.id === target.sceneId);
    if (s) s.status = "generating";
  } else if (target.kind === "variation") {
    const v = data.characters.find((x) => x.id === target.characterId)?.variations.find((x) => x.id === target.variationId);
    if (v) v.status = "generating";
  } else if (target.kind === "voice") {
    const voice = data.characters.find((x) => x.id === target.characterId)?.voice;
    if (voice) voice.status = "generating";
  } else {
    const shot = findShot(project, target.shotId);
    if (!shot) return;
    if (target.kind === "video") {
      shot.state = "queued";
    } else {
      shot.keyframes ??= [];
      const kf = shot.keyframes.find((k) => k.type === target.frameType) ?? { id: `${shot.id}-${target.frameType}`, type: target.frameType, visualPrompt: "", status: "pending" as const };
      kf.status = "generating";
      if (!shot.keyframes.includes(kf)) shot.keyframes.push(kf);
    }
  }
}

/** 完成度：出片的镜头数 / 总镜头数，制片导出右上角那个百分比就是它 */
export function renderProgress(project: Project): { done: number; total: number; percent: number; estSeconds: number; targetSeconds: number } {
  const shots = project.data.shots;
  const done = shots.filter((s) => s.videoMediaIds.length > 0).length;
  const estSeconds = shots.reduce((a, s) => a + (s.durationSec || 0), 0);
  return {
    done,
    total: shots.length,
    percent: shots.length ? Math.round((done / shots.length) * 100) : 0,
    estSeconds,
    targetSeconds: project.config.targetDurationSec,
  };
}
