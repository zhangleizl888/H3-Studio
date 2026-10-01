/**
 * 导演台内部共用的一层：媒体取址、可提交文本、镜头/关键帧小工具。
 *
 * 为什么单独一个文件：卡片、抽屉、时间轴都要显示同一批图，
 * 而 api.media.url 是异步的（本地产物要读 blob，服务端产物要经后端读回）。
 * 每个组件各自 await 一次就会同图多次读盘，这里做一层按 mediaId 的进程内缓存。
 */

import { useEffect, useRef, useState } from "react";
import { MediaFrame, Textarea } from "../../../components/ui";
import { useApi } from "../../../lib/apiClient";
import type { Api, GenerateRequest } from "../../../lib/api";
import type { GenTarget } from "../../../lib/generate";
import type { GenHandle } from "../../../lib/useGenerate";
import type { Keyframe, Media, Project, Shot } from "../../../lib/types";
import { shotLabel } from "../../../lib/prompts";
import { cn } from "../../../lib/utils";

export type FrameType = "start" | "end";
export const FRAME_TYPES: FrameType[] = ["start", "end"];

/** 页面把这一份传进抽屉/卡片，避免三四十个 props 在中间层来回转 */
export interface DirectorCtx {
  project: Project;
  shots: Shot[];
  mediaById: Map<string, Media>;
  aspect: string;
  handles: Record<string, GenHandle>;
  api: Api;
  run: (target: GenTarget, req: GenerateRequest) => Promise<{ jobId?: string; error?: string }>;
  patchShot: (shotId: string, patch: Partial<Shot>) => void;
  writeShots: (next: Shot[]) => void;
  setConfig: (patch: Partial<Project["config"]>) => void;
  /** 上传完一张图，本地索引要重新拉一次，否则卡片还是空的 */
  refetchMedia: () => void;
  onPreview: (mediaId: string, title: string) => void;
  onPlay: (mediaId: string, title: string) => void;
  notify: (msg: string, tone?: "ok" | "bad") => void;
  machColor: (instanceId?: string | null) => string | undefined;
}

/* ───────── 镜头与关键帧 ───────── */

/**
 * 卡片上的 SHOT 编号。
 * shotLabel 认 'shot-1-2'；本项目历史 id 是 'shot_1' / 'shot_1-2'，正则不匹配时
 * 它退回按 index 补零 —— 那样拆出来的子镜会和父镜同号，所以这里自己补子镜后缀。
 */
export function cardLabel(shot: Shot): string {
  const base = shotLabel(shot.id, shot.index);
  const parent = shot.parentShotId;
  if (!parent || base.includes("-") || !shot.id.startsWith(parent)) return base;
  const suffix = shot.id.slice(parent.length).replace(/^[-_]+/, "");
  return suffix ? `${base}-${suffix}` : base;
}

export function frameOf(shot: Shot, type: FrameType): Keyframe | undefined {
  return shot.keyframes?.find((k) => k.type === type);
}

export function frameMediaId(shot: Shot, type: FrameType): string | null {
  return frameOf(shot, type)?.mediaId ?? (type === "start" ? shot.startFrameMediaId : shot.endFrameMediaId) ?? null;
}

/** 写回某一帧。mediaId 变了要同步 shot.startFrameMediaId/endFrameMediaId —— 那是别的页面读的镜像 */
export function withFrame(shot: Shot, type: FrameType, patch: Partial<Keyframe>): Shot {
  const list = [...(shot.keyframes ?? [])];
  const i = list.findIndex((k) => k.type === type);
  const base: Keyframe =
    i >= 0
      ? list[i]
      : { id: `${shot.id}-${type}`, type, visualPrompt: "", status: "pending" };
  list[i >= 0 ? i : list.length] = { ...base, ...patch };
  const next: Shot = { ...shot, keyframes: list };
  if (patch.mediaId !== undefined) {
    if (type === "start") next.startFrameMediaId = patch.mediaId;
    else next.endFrameMediaId = patch.mediaId;
  }
  return next;
}

export function aspectOf(project: Project): string {
  return project.config.aspectRatio === "9:16" ? "9/16" : project.config.aspectRatio === "1:1" ? "1/1" : "16/9";
}

/** 秒 → HH:MM:SS:FF，时间轴与 TC 标签用 */
export function timecode(sec: number, fps = 24): string {
  const t = Math.max(0, sec);
  const p = (n: number) => String(Math.floor(n)).padStart(2, "0");
  return `${p(t / 3600)}:${p((t % 3600) / 60)}:${p(t % 60)}:${p(Math.round((t % 1) * fps))}`;
}

/* ───────── 媒体取址 ───────── */

const srcCache = new Map<string, string>();

/** 解析一张图/一段视频能用的 src。解析不出来（文件还没落盘）就返回 null，让调用方回退到占位 */
export function useMediaSrc(media: Media | undefined): string | null {
  const api = useApi();
  const [src, setSrc] = useState<string | null>(null);
  useEffect(() => {
    if (!media) {
      setSrc(null);
      return;
    }
    const hit = srcCache.get(media.id);
    if (hit) {
      setSrc(hit);
      return;
    }
    let alive = true;
    api.media
      .url(media)
      .then((u) => {
        if (!alive) return;
        if (u) srcCache.set(media.id, u);
        setSrc(u);
      })
      .catch(() => {
        if (alive) setSrc(null);
      });
    return () => {
      alive = false;
    };
  }, [media, api]);
  return src;
}

export function mediaOf(ctx: DirectorCtx, id?: string | null): Media | undefined {
  return id ? ctx.mediaById.get(id) : undefined;
}

/** 有真图用真图，没有再退回 MediaFrame 的程序化占位（它本来就是给「还没有产物」画的） */
export function MediaImage({
  media,
  seedText,
  aspect,
  className,
  label,
  alt,
  onClick,
}: {
  media?: Media;
  seedText: string;
  aspect: string;
  className?: string;
  label?: React.ReactNode;
  alt?: string;
  onClick?: () => void;
}) {
  const src = useMediaSrc(media);
  return (
    <div
      className={cn("relative overflow-hidden rounded-panel border border-rule-soft bg-slate", onClick && "cursor-zoom-in", className)}
      style={{ aspectRatio: aspect }}
      onClick={onClick}
      role={onClick ? "button" : undefined}
      tabIndex={onClick ? 0 : undefined}
      onKeyDown={
        onClick
          ? (e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                onClick();
              }
            }
          : undefined
      }
    >
      {src ? (
        <img src={src} alt={alt ?? "镜头画面"} className="h-full w-full object-cover" loading="lazy" />
      ) : (
        <MediaFrame seedText={seedText} kind={media ? "image" : "none"} className="absolute inset-0 rounded-none border-0" />
      )}
      {label && <div className="absolute inset-x-0 bottom-0 bg-black/45 px-1.5 py-[2px] text-micro text-white/85">{label}</div>}
    </div>
  );
}

/* ───────── 文本编辑 ───────── */

/**
 * 以「服务端值」为基准的本地草稿。
 *
 * 不这么做的话：每次按键都写 IDB，输入框的值要等一次 refetch 才回来，
 * 快速输入就会丢字符；而 AI 回填提示词时又必须让输入框跟着变。
 * 规则是「你没在敲，外部改动就跟随；正在敲，你的字优先」。
 */
export function CommitText({
  value,
  onCommit,
  rows = 3,
  placeholder,
  className,
  mono,
  ariaLabel,
}: {
  value: string;
  onCommit: (v: string) => void;
  rows?: number;
  placeholder?: string;
  className?: string;
  mono?: boolean;
  ariaLabel?: string;
}) {
  const [v, setV] = useState(value);
  const typing = useRef(false);
  useEffect(() => {
    if (!typing.current) setV(value);
  }, [value]);
  return (
    <Textarea
      rows={rows}
      aria-label={ariaLabel}
      value={v}
      placeholder={placeholder}
      className={cn(mono && "mono text-note", className)}
      onChange={(e) => {
        typing.current = true;
        setV(e.target.value);
      }}
      onBlur={() => {
        typing.current = false;
        if (v !== value) onCommit(v);
      }}
    />
  );
}

/* ───────── 文件选择 ───────── */

/** 弹一次文件选择框。取消不会有任何事件，所以靠窗口重新聚焦收尾 */
export function pickImage(): Promise<File | null> {
  return new Promise((resolve) => {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = "image/*";
    const settle = (f: File | null) => {
      window.removeEventListener("focus", onFocus);
      resolve(f);
    };
    const onFocus = () => window.setTimeout(() => settle(null), 500);
    input.onchange = () => settle(input.files?.[0] ?? null);
    input.onerror = () => settle(null);
    window.addEventListener("focus", onFocus);
    input.click();
  });
}

/** 任务的机器身份色：进度条按所在机器着色 */
export function machVar(placement: "local" | "cloud_self" | "cloud_runninghub"): string {
  return placement === "local" ? "var(--color-mach-local)" : placement === "cloud_self" ? "var(--color-mach-self)" : "var(--color-mach-rh)";
}
