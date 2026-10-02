/**
 * 版本历史的「谁是谁的第几版、当前是哪一版」这一层。
 *
 * 服务端只认一组标签 `(project_key, kind, role, ref_id)`；而"当前版本"对图片/视频来说
 * 从来不是服务端的字段 —— 它就是项目实体里的那个指针（`refMediaIds[0]`、
 * `keyframes[].mediaId` + `start/endFrameMediaId` 镜像、`videoMediaIds[0]`）。
 * 已核实下游三处都取首位：`Timeline.tsx`、`export/utils.ts`、`prompts/utils.ts`。
 * 剧本反过来，当前版是服务端 `script_versions.is_current`，所以它不在这里写指针。
 *
 * 这个文件是唯一知道全部展示槽位的地方。加新的产物槽位时改这里，
 * 别在组件里就地 filter 一遍数组 —— 那就是"删除只标记、没摘干净"的来路。
 */

import { attachResult, findShot, refFor, roleFor, type GenTarget } from "./generate";
import { frameMediaId, withFrame } from "../routes/project/director/common";
import type { Media, Project, VersionBucket } from "./types";

export type { GenTarget };

/** 某一版媒体在服务器端的分组标签。roleFor/refFor 是 generate.ts 定的，别再算一套 */
export const groupOf = (t: GenTarget) => ({ role: roleFor(t), refId: refFor(t) });

/**
 * 反向解析：全局页与回收站手里只有服务端给的 `(role, refId)`，
 * 要改指针就得先还原成 GenTarget。refId 里的 `:` 是 refFor 加的分隔符，取最后一段。
 */
export function targetFromGroup(role: string | null | undefined, refId: string | null | undefined): GenTarget | null {
  if (!role || !refId) return null;
  if (role === "character") return { kind: "character", characterId: refId };
  if (role === "scene") return { kind: "scene", sceneId: refId };
  const head = refId.slice(0, refId.lastIndexOf(":"));
  const tail = refId.slice(refId.lastIndexOf(":") + 1);
  if (role === "variation") {
    const [characterId, variationId] = [head, tail];
    return characterId && variationId ? { kind: "variation", characterId, variationId } : null;
  }
  if (role === "keyframe_start" || role === "keyframe_end") {
    return head && (tail === "start" || tail === "end") ? { kind: "keyframe", shotId: head, frameType: tail } : null;
  }
  if (role === "video") return { kind: "video", shotId: refId };
  if (role === "voice") return { kind: "voice", characterId: refId };
  return null;
}

/** 三个 tab 的分类口径按 kind 判（后端按扩展名写它），不按 role —— role 是"谁的"，不是"什么" */
export const bucketOf = (m: Pick<Media, "kind">): VersionBucket => (m.kind === "video" ? "video" : "image");

/** 这一组里当前是哪一版。读不到实体就返回 null（项目不在这台浏览器上时就是这样） */
export function currentIdFor(project: Project, t: GenTarget): string | null {
  const d = project.data;
  if (t.kind === "character") return d.characters.find((c) => c.id === t.characterId)?.refMediaIds[0] ?? null;
  if (t.kind === "variation")
    return (
      d.characters.find((c) => c.id === t.characterId)?.variations.find((v) => v.id === t.variationId)?.refMediaIds[0] ??
      null
    );
  if (t.kind === "scene") return d.scenes.find((s) => s.id === t.sceneId)?.refMediaIds[0] ?? null;
  if (t.kind === "voice") return d.characters.find((c) => c.id === t.characterId)?.voice?.sampleMediaIds[0] ?? null;
  const shot = findShot(project, t.shotId);
  if (!shot) return null;
  return t.kind === "keyframe" ? frameMediaId(shot, t.frameType) : (shot.videoMediaIds[0] ?? null);
}

/**
 * 把某一版设为当前。
 *
 * 直接复用 attachResult：它已经在做「去重 + 挪到首位 + status/state 回写 + 关键帧镜像同步」，
 * 这里再造一份就是第七份 prepend 实现。
 */
export const setCurrent = (project: Project, t: GenTarget, m: Media): boolean => attachResult(project, t, [m]);

/**
 * 从项目实体里把某一版彻底摘掉。返回被改动过的槽位名，给调用方决定要失效哪些查询。
 *
 * `replacement` 是服务端在同一次删除响应里带回来的"同组最新存活那一版"：删的正好是当前版时
 * 指针要落到它身上，不能留空。每个槽位都在这儿处理，否则回收站之外还会残留一份 ——
 * 卡片、时间轴、导出、提示词页读的都是这些指针。
 */
export function dereference(project: Project, mediaId: string, replacement?: string | null): string[] {
  const touched: string[] = [];
  const d = project.data;
  const drop = (ids: string[]) => ids.filter((x) => x !== mediaId);
  // 图片类实体的 refMediaIds 本身就是"这个对象的版本表"，摘掉一条就是少一版
  const swap = (ids: string[]) => {
    const next = drop(ids);
    if (replacement && !next.includes(replacement)) next.unshift(replacement);
    return next;
  };

  for (const c of d.characters) {
    if (c.refMediaIds.includes(mediaId)) {
      c.refMediaIds = swap(c.refMediaIds);
      if (!c.refMediaIds.length) c.status = "pending";
      touched.push(`character:${c.id}`);
    }
    for (const v of c.variations) {
      if (v.refMediaIds.includes(mediaId)) {
        v.refMediaIds = swap(v.refMediaIds);
        if (!v.refMediaIds.length) v.status = "pending";
        touched.push(`variation:${c.id}:${v.id}`);
      }
    }
  }
  for (const s of d.scenes) {
    if (s.refMediaIds.includes(mediaId)) {
      s.refMediaIds = swap(s.refMediaIds);
      if (!s.refMediaIds.length) s.status = "pending";
      touched.push(`scene:${s.id}`);
    }
  }

  d.shots.forEach((shot, i) => {
    if (shot.videoMediaIds.includes(mediaId)) {
      // 成片列表里 [0] 就是下游公认的那一版，filter 掉自然完成"退到下一版"
      shot.videoMediaIds = drop(shot.videoMediaIds);
      if (!shot.videoMediaIds.length) shot.state = "idle";
      touched.push(`video:${shot.id}`);
    }
    for (const type of ["start", "end"] as const) {
      const kf = shot.keyframes?.find((k) => k.type === type);
      if (kf?.mediaId !== mediaId && (type === "start" ? shot.startFrameMediaId : shot.endFrameMediaId) !== mediaId) continue;
      const next = frameMediaId(shot, type) === mediaId ? (replacement ?? null) : frameMediaId(shot, type);
      // 走 withFrame：它是唯一同步 start/endFrameMediaId 镜像的写入口，手写会漏镜像
      d.shots[i] = withFrame(d.shots[i], type, { mediaId: next, status: next ? "completed" : "pending" });
      touched.push(`keyframe:${shot.id}:${type}`);
    }
  });

  return touched;
}

/**
 * 换浏览器/清缓存之后，实体指针可能还指着已经不存在的产物。
 *
 * 只清「既不在本地索引、也不在服务端活行列表里」的 id —— 顺序很重要，必须在把服务端活行
 * 重新 adopt 回 IndexedDB 之后再调用，否则一次索引被清就把还能恢复的指针全判成坏的。
 */
export function sanitizeDanglingRefs(project: Project, validIds: Set<string>): string[] {
  const bad: string[] = [];
  const known = (id: string | null | undefined) => !id || validIds.has(id);
  const d = project.data;
  const prune = (ids: string[]) => {
    const gone = ids.filter((x) => !known(x));
    bad.push(...gone);
    return ids.filter((x) => known(x));
  };
  for (const c of d.characters) {
    c.refMediaIds = prune(c.refMediaIds);
    for (const v of c.variations) v.refMediaIds = prune(v.refMediaIds);
  }
  for (const s of d.scenes) s.refMediaIds = prune(s.refMediaIds);
  for (const shot of d.shots) {
    shot.videoMediaIds = prune(shot.videoMediaIds);
    for (const kf of shot.keyframes ?? []) if (!known(kf.mediaId)) kf.mediaId = null;
    if (!known(shot.startFrameMediaId)) shot.startFrameMediaId = null;
    if (!known(shot.endFrameMediaId)) shot.endFrameMediaId = null;
  }
  return bad;
}

/**
 * 这个项目实体在不在本机浏览器里。
 *
 * A 方案的代价：服务端有全部产物与版本，实体只在 IndexedDB。所以"设为当前/存为版本"
 * 这类要写实体的动作，在跨项目视图里必须先看这一眼再决定禁用还是抛错。
 */
export const isLocalProject = (projects: { id: string }[] | undefined, key: string | null | undefined): boolean =>
  !!key && !!projects?.some((p) => p.id === key);
