import type { Media } from "./types";

export function cn(...parts: (string | false | null | undefined)[]): string {
  return parts.filter(Boolean).join(" ");
}

export function pad(n: number, len = 3): string {
  return String(n).padStart(len, "0");
}

/** 长会话里时间一律绝对化，避免「3 分钟前」在跨天时失效 */
export function fmtTime(iso?: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  const now = new Date();
  const hm = `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  if (d.toDateString() === now.toDateString()) return `${hm}`;
  return `${d.getMonth() + 1}/${d.getDate()} ${hm}`;
}

export function fmtDur(ms?: number | null): string {
  if (!ms && ms !== 0) return "—";
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s}秒`;
  const m = Math.floor(s / 60);
  return `${m}分${String(s % 60).padStart(2, "0")}秒`;
}

export function fmtSec(sec?: number | null): string {
  if (sec === null || sec === undefined) return "—";
  if (sec < 60) return `${Math.round(sec)}s`;
  return `${Math.floor(sec / 60)}m${String(Math.round(sec % 60)).padStart(2, "0")}`;
}

export function fmtBytes(b?: number | null): string {
  if (!b) return "—";
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  let v = b;
  while (v >= 1024 && i < u.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(v >= 10 || i === 0 ? 0 : 1)} ${u[i]}`;
}

export function fmtMoney(n?: number | null, currency = "¥"): string {
  if (n === null || n === undefined) return "—";
  return `${currency}${n.toFixed(2)}`;
}

export function fmtFrames(sec: number): string {
  return `${sec.toFixed(1)}s`;
}

/** 相对时间只用于「最近 N 分钟」这种短窗口 */export function ago(iso?: string | null): string {
  if (!iso) return "从未";
  const diff = Date.now() - new Date(iso).getTime();
  const m = Math.floor(diff / 60000);
  if (m < 1) return "刚刚";
  if (m < 60) return `${m} 分钟前`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h} 小时前`;
  return `${Math.floor(h / 24)} 天前`;
}

export function pct(v?: number, max?: number): number | null {
  if (!v && v !== 0 || !max) return null;
  return Math.min(100, Math.round((v / max) * 100));
}

export function uid(prefix: string): string {
  return `${prefix}_${Math.random().toString(36).slice(2, 10)}`;
}

export function clamp(n: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, n));
}

/** 从 URL 里把 RunningHub 的 apiKey 摘掉后再落日志/报错 */
export function redactKeyUrl(url: string): string {
  return url.replace(/\/proxy(-plus)?\/[A-Za-z0-9]{8,}/g, "/proxy$1/<REDACTED>");
}

export function download(filename: string, text: string) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([text], { type: "application/json" }));
  a.download = filename;
  a.click();
  URL.revokeObjectURL(a.href);
}

/**
 * 只有静帧能进 <img>。
 *
 * 后端没有缩略图端点（media 表有 thumb_path 列但没人写、也没人读），而 media.url() 要把整
 * 个文件读成 blob 才拿得到 src —— 把视频行喂给 <img> 的结果是：下载整段视频，然后画出一个
 * 破图标。视频格一律用首帧静帧当封面，或者如实显示为占位块。
 */
export function isStill(m: Media | undefined): m is Media {
  return !!m && (m.kind === "image" || m.kind === "ref_image");
}
