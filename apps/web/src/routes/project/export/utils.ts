import type { ExportShotRow } from "../../../lib/api";
import type { Media, Project, RenderLog, Shot } from "../../../lib/types";
import { shotLabel } from "../../../lib/prompts";
import { pad } from "../../../lib/utils";

/**
 * 秒 → HH:MM:SS:FF。时间基固定 30fps，与后端 routes_export._tc 同口径 ——
 * 页面上的 TC 和 EDL 里的 TC 差一帧，人对时间轴就没有信任了。
 */
export function tc(seconds: number, fps = 30): string {
  const total = Math.max(0, seconds || 0);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = Math.floor(total % 60);
  const f = Math.round((total - Math.floor(total)) * fps) % fps;
  return `${pad(h, 2)}:${pad(m, 2)}:${pad(s, 2)}:${pad(f, 2)}`;
}

/** 时间轴上的一条：镜头 + 它在整片里的入出点 + 当前认领它的那段成片 */
export interface SequenceEntry {
  shot: Shot;
  label: string;
  sec: number;
  inSec: number;
  outSec: number;
  /** attachResult 把新产物放最前，所以 [0] 就是「这一镜最新的那段片」 */
  mediaId: string | null;
  media: Media | null;
}

export function orderedShots(project: Project | undefined): Shot[] {
  return [...(project?.data.shots ?? [])].sort((a, b) => a.index - b.index);
}

export function sequenceEntries(project: Project | undefined, byId: Map<string, Media>): SequenceEntry[] {
  let cursor = 0;
  return orderedShots(project).map((shot) => {
    const sec = shot.durationSec || 0;
    const mediaId = shot.videoMediaIds[0] ?? null;
    const e: SequenceEntry = {
      shot,
      label: shotLabel(shot.id, shot.index),
      sec,
      inSec: cursor,
      outSec: cursor + sec,
      mediaId,
      media: mediaId ? (byId.get(mediaId) ?? null) : null,
    };
    cursor += sec;
    return e;
  });
}

/** EDL / FCP XML 的行。后端不猜顺序，顺序与时长全在这里给出去 */
export function edlRows(project: Project | undefined, entries: SequenceEntry[]): ExportShotRow[] {
  if (!project) return [];
  const sceneName = (id: string | null) => project.data.scenes.find((s) => s.id === id)?.name ?? "";
  return entries.map((e) => ({
    index: e.shot.index,
    title: `SHOT_${e.label}`,
    durationSec: e.sec,
    mediaId: e.mediaId,
    action: e.shot.action,
    cameraMovement: e.shot.cameraMovement,
    sceneName: sceneName(e.shot.sceneId),
  }));
}

/** 触发一次文本文件下载：EDL / XML / 项目 JSON 都靠它 */
export function downloadText(filename: string, text: string, mime: string): void {
  const blob = new Blob([text], { type: `${mime};charset=utf-8` });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.append(a);
  a.click();
  a.remove();
  // 交给浏览器接管下载需要一点时间，立刻 revoke 会拿到 0 字节文件
  setTimeout(() => URL.revokeObjectURL(url), 30_000);
}

function safeName(s: string): string {
  return s.replace(/[\\/:*?"<>|\s]+/g, "_").replace(/^_+|_+$/g, "") || "未命名";
}

/** 扩展名优先取真实文件路径；IDB 上传与演示行没有可信路径，退到 mime / kind */
function extOf(m: Media): string {
  const p = m.path || "";
  if (!p.startsWith("idb:") && !p.startsWith("data:")) {
    const hit = /\.(mp4|webm|mov|mkv|png|jpe?g|webp|gif|wav|mp3|m4a|aac)$/i.exec(p);
    if (hit) return `.${hit[1].toLowerCase() === "jpeg" ? "jpg" : hit[1].toLowerCase()}`;
  }
  const mime = (m.mime ?? "").toLowerCase();
  if (mime.includes("mp4") || mime.includes("webm") || mime.includes("quicktime")) return mime.includes("webm") ? ".webm" : ".mp4";
  if (mime.includes("png")) return ".png";
  if (mime.includes("jpeg") || mime.includes("jpg")) return ".jpg";
  if (mime.includes("wav")) return ".wav";
  if (mime.includes("mpeg") || mime.includes("mp3")) return ".mp3";
  if (m.kind.includes("video")) return ".mp4";
  if (m.kind.includes("audio")) return ".m4a";
  return ".png";
}

export type AssetDir = "characters" | "scenes" | "shots" | "videos";

export interface AssetBundle {
  items: { mediaId: string; path: string }[];
  counts: Record<AssetDir, number>;
  /** 项目引用了、但本地索引里没有行的媒体 id：后端也拿不到，如实列出来 */
  missing: string[];
}

/**
 * 素材包目录树。后端只认 mediaId，目录结构完全由这里给，
 * 所以命名要能还原成「这是谁、第几镜、首帧还是尾帧」。
 */
export function assetBundle(project: Project | undefined, byId: Map<string, Media>): AssetBundle {
  const items: { mediaId: string; path: string }[] = [];
  const counts: Record<AssetDir, number> = { characters: 0, scenes: 0, shots: 0, videos: 0 };
  const missing: string[] = [];
  const seenPath = new Set<string>();
  const seenMedia = new Set<string>();
  if (!project) return { items, counts, missing };

  const push = (dir: AssetDir, mediaId: string | null | undefined, name: string) => {
    if (!mediaId) return;
    const m = byId.get(mediaId);
    if (!m) {
      if (!missing.includes(mediaId)) missing.push(mediaId);
      return;
    }
    const path = `${dir}/${safeName(name)}${extOf(m)}`;
    if (seenPath.has(path) || seenMedia.has(`${dir}:${mediaId}`)) return;
    seenPath.add(path);
    seenMedia.add(`${dir}:${mediaId}`);
    items.push({ mediaId, path });
    counts[dir] += 1;
  };

  project.data.characters.forEach((c, i) => {
    const base = `${pad(i + 1, 2)}_${c.name || "角色"}`;
    push("characters", c.refMediaIds[0], base);
    c.variations.forEach((v, j) => push("characters", v.refMediaIds[0], `${base}/${pad(j + 1, 2)}_${v.name || "变体"}`));
  });
  project.data.scenes.forEach((s, i) => push("scenes", s.refMediaIds[0], `${pad(i + 1, 2)}_${s.name || "场景"}`));

  for (const e of sequenceEntries(project, byId)) {
    const base = `${e.label}_${safeName(e.shot.action || "镜头")}`;
    const frames = e.shot.keyframes ?? [];
    if (frames.length) {
      for (const k of frames) push("shots", k.mediaId, `${base}/${k.type === "start" ? "首帧" : "尾帧"}`);
    } else {
      push("shots", e.shot.startFrameMediaId, `${base}/首帧`);
      push("shots", e.shot.endFrameMediaId, `${base}/尾帧`);
    }
    e.shot.videoMediaIds.forEach((mid, i) => push("videos", mid, i === 0 ? base : `${base}_第${i + 1}段`));
  }
  return { items, counts, missing };
}

export function sortedLogs(project: Project | undefined): RenderLog[] {
  return [...(project?.data.renderLogs ?? [])].sort((a, b) => (b.ts || "").localeCompare(a.ts || ""));
}

/** 后端 merge 回来的 mode 要用人话说清：用户有权知道成片是被重编码过的 */
export function mergeModeNote(mode?: string): string {
  if (mode === "copy")
    return "串流复制（ffmpeg -c copy）：各段编码参数一致，直接首尾相接，没有二次编码，画质与源段相同、耗时秒级。";
  if (mode === "reencode")
    return "重编码（libx264 CRF 18 + aac 192k）：串流复制被判定会花屏（预览档与全质量档混切时常见），后端改走重编码，耗时更长但画面稳定。";
  return mode ? `后端回报的模式：${mode}` : "后端没回模式字段。";
}
