/**
 * 「场景角色」页共用的小件：图片解析、上传、卡片按钮、弹层、行内编辑、生成进度。
 *
 * 只服务 Assets.tsx 与它的子视图，别的页面要用时再提到 components/。
 * 颜色一律取 index.css 的 token（装饰 = chrome，状态 = state-*），不写死十六进制。
 */

import { useEffect, useRef, useState, type ReactNode } from "react";
import { Check, CircleAlert, Pencil, Upload, X } from "lucide-react";
import { Button, MediaFrame, Spinner, type StateKey } from "../../../components/ui";
import { useApi } from "../../../lib/apiClient";
import { cn, isStill } from "../../../lib/utils";
import type { AssetState, Media } from "../../../lib/types";
import type { GenHandle } from "../../../lib/useGenerate";

/* ───────── 图片：media id → 能用的 src ───────── */

/**
 * 解析一张图的 src。上传的走 IndexedDB blob，服务端产物经后端 /api/media/{id}/raw 读回，
 * 两种都由 api.media.url 内部缓存 objectURL，所以这里只管跟着 id/path 变化重解。
 */
export function useMediaSrc(media: Media | undefined): string | null {
  const api = useApi();
  const ref = useRef(media);
  const [src, setSrc] = useState<string | null>(null);
  const id = media?.id;
  const path = media?.path;

  useEffect(() => {
    ref.current = media;
  }, [media]);

  useEffect(() => {
    let alive = true;
    setSrc(null);
    const m = ref.current;
    if (!m) return;
    api.media
      .url(m)
      .then((u) => alive && setSrc(u))
      .catch(() => alive && setSrc(null));
    return () => {
      alive = false;
    };
  }, [api, id, path]);

  return src;
}

export function MediaImage({
  media,
  seedText,
  alt,
  aspect = "16/9",
  className,
  onClick,
  emptyLabel,
  busy,
  badge,
}: {
  media: Media | undefined;
  seedText: string;
  alt: string;
  aspect?: string;
  className?: string;
  onClick?: () => void;
  emptyLabel?: ReactNode;
  busy?: boolean;
  badge?: ReactNode;
}) {
  // 只有静帧能进 <img>：视频行喂进去会先被读成整段 blob，再画出一个破图标（见 lib/utils 的 isStill）
  const still = isStill(media) ? media : undefined;
  const src = useMediaSrc(still);
  const overlay = (
    <>
      {busy && (
        <div className="absolute inset-0 grid place-items-center bg-void/70 backdrop-blur-[2px]">
          <div className="flex flex-col items-center gap-1.5">
            <Spinner className="h-5 w-5 text-chrome" />
            <span className="label-mono">生成中</span>
          </div>
        </div>
      )}
      {badge && <span className="absolute right-1.5 top-1.5 z-10">{badge}</span>}
    </>
  );

  if (!src) {
    return (
      <div className={cn("relative", className)}>
        <MediaFrame
          seedText={seedText}
          kind={!media ? "none" : !still ? "video" : "image"}
          aspect={aspect}
          label={emptyLabel ?? (!media ? undefined : still ? "这张图读不回来" : "这不是静帧，没有可显示的封面")}
        />
        {overlay}
        {onClick && <button type="button" onClick={onClick} aria-label={`放大查看 ${alt}`} className="absolute inset-0 cursor-zoom-in rounded-panel" />}
      </div>
    );
  }

  return (
    <div className={cn("relative overflow-hidden rounded-panel border border-hairline bg-void/70", className)} style={{ aspectRatio: aspect }}>
      <img src={src} alt={alt} loading="lazy" className="h-full w-full object-cover" />
      {overlay}
      {onClick && <button type="button" onClick={onClick} aria-label={`放大查看 ${alt}`} className="absolute inset-0 cursor-zoom-in" />}
    </div>
  );
}

/** 已有产物的勾选态：色相 + 图形同时出现，不单靠颜色 */
export function DoneBadge({ label = "已有参考图" }: { label?: string }) {
  return (
    <span
      title={label}
      className="grid h-5 w-5 place-items-center rounded-full bg-chrome text-chrome-ink"
      style={{ boxShadow: "0 0 12px color-mix(in srgb, var(--color-chrome) 45%, transparent)" }}
    >
      <Check className="h-3 w-3" strokeWidth={3} />
    </span>
  );
}

/* ───────── 状态与进度 ───────── */

/**
 * 卡片显示用的状态。
 * generating 却没有产物 = failed：页面关在半路任务就没了，留着转圈只会让人以为还在跑。
 */
export function assetStateOf(status: AssetState | undefined, hasMedia: boolean, live?: GenHandle): StateKey {
  if (live && (live.state === "queued" || live.state === "dispatching" || live.state === "running")) return "running";
  if (live && (live.state === "failed" || live.state === "canceled")) return "failed";
  if (hasMedia) return "succeeded";
  if (status === "generating" || status === "failed") return "failed";
  if (status === "completed") return "succeeded";
  return "idle";
}

export function GenBar({ handle }: { handle?: GenHandle }) {
  if (!handle) return null;
  const pct = handle.progress == null ? null : Math.max(0, Math.min(100, Math.round(handle.progress * 100)));
  const failed = handle.state === "failed" || handle.state === "canceled";
  return (
    <div className="space-y-1">
      <div
        role="progressbar"
        aria-valuenow={pct ?? undefined}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label="生成进度"
        className={cn("h-1 overflow-hidden rounded-hairline bg-hairline", pct === null && "stripe")}
        style={{ ["--stripe" as string]: failed ? "var(--color-state-fail)" : "var(--color-chrome)" }}
      >
        {pct !== null && (
          <div
            className="h-full transition-[width] duration-500"
            style={{ width: `${pct}%`, background: failed ? "var(--color-state-fail)" : "linear-gradient(90deg, var(--color-chrome), var(--color-chrome-2))" }}
          />
        )}
      </div>
      <div className="flex items-baseline justify-between gap-2 text-caption text-ink-mute">
        <span className="mono flex-none">{pct !== null ? `${pct}%` : "该平台不给百分比"}</span>
        <span className={cn("truncate", failed && "text-state-fail")}>{handle.error || handle.stage || "已入队"}</span>
      </div>
    </div>
  );
}

/* ───────── 卡片上的按钮 ───────── */

export type ActionTone = "quiet" | "chrome" | "ghost" | "danger";

const ACTION_TONES: Record<ActionTone, string> = {
  quiet: "border-hairline bg-sheen text-ink-dim hover:bg-sheen-hi hover:text-ink",
  chrome: "border-transparent bg-gradient-to-r from-chrome to-chrome-2 text-chrome-ink hover:brightness-110",
  ghost: "border-transparent bg-transparent text-ink-mute hover:bg-sheen hover:text-ink",
  danger: "border-state-fail/45 bg-transparent text-state-fail hover:bg-state-fail/12",
};

export function actionClass(tone: ActionTone, className?: string): string {
  return cn(
    "inline-flex items-center justify-center gap-1.5 rounded-ctl border px-3 py-[7px] text-note font-semibold tracking-wide transition-colors",
    "disabled:cursor-not-allowed disabled:opacity-45",
    ACTION_TONES[tone],
    className,
  );
}

export function CardAction({
  tone = "quiet",
  icon,
  className,
  children,
  ...rest
}: React.ButtonHTMLAttributes<HTMLButtonElement> & { tone?: ActionTone; icon?: ReactNode }) {
  return (
    <button type="button" className={actionClass(tone, className)} {...rest}>
      {icon}
      {children}
    </button>
  );
}

/**
 * 上传：真的把文件交给 api.media.put 落盘（IndexedDB / 服务端媒体库），
 * 不是 createObjectURL 糊一张预览图 —— 预览会在刷新后变成死链，而参考图要能被后端读走。
 */
export function UploadButton({
  onFile,
  label = "上传",
  tone = "quiet",
  className,
  disabled,
  title,
}: {
  onFile: (file: File) => Promise<void> | void;
  label?: string;
  tone?: ActionTone;
  className?: string;
  disabled?: boolean;
  title?: string;
}) {
  const [busy, setBusy] = useState(false);
  return (
    <label className={cn(actionClass(tone, className), (disabled || busy) && "cursor-not-allowed opacity-45")} title={title ?? "选一张本地参考图，直接作为该对象的产物"}>
      <input
        type="file"
        accept="image/*"
        className="sr-only"
        disabled={disabled || busy}
        onChange={async (e) => {
          const file = e.target.files?.[0];
          e.target.value = "";
          if (!file) return;
          setBusy(true);
          try {
            await onFile(file);
          } finally {
            setBusy(false);
          }
        }}
      />
      {busy ? <Spinner className="h-3.5 w-3.5" /> : <Upload className="h-3.5 w-3.5" />}
      {label}
    </label>
  );
}

/* ───────── 行内编辑 ───────── */

/** 点字即改：Enter/失焦提交，Esc 撤销。参考页的「点名字改名字」都走这里 */
export function InlineEdit({
  value,
  onCommit,
  placeholder = "点击填写",
  className,
  inputClassName,
  ariaLabel,
  disabled,
}: {
  value: string | undefined;
  onCommit: (v: string) => void;
  placeholder?: string;
  className?: string;
  inputClassName?: string;
  ariaLabel: string;
  disabled?: boolean;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(value ?? "");
  useEffect(() => {
    if (!editing) setDraft(value ?? "");
  }, [value, editing]);

  const commit = () => {
    setEditing(false);
    const next = draft.trim();
    if (next !== (value ?? "").trim()) onCommit(next);
  };

  if (editing) {
    return (
      <input
        autoFocus
        aria-label={ariaLabel}
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            commit();
          }
          if (e.key === "Escape") {
            setDraft(value ?? "");
            setEditing(false);
          }
        }}
        className={cn("w-full min-w-0 rounded-ctl border border-chrome/40 bg-void/60 px-1.5 py-0.5 text-inherit", inputClassName, className)}
      />
    );
  }

  const empty = !(value ?? "").trim();
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={() => setEditing(true)}
      aria-label={`编辑${ariaLabel}`}
      title="点击编辑"
      className={cn("group/edit inline-flex max-w-full items-center gap-1 text-left", disabled && "cursor-not-allowed opacity-50", className)}
    >
      <span className={cn("truncate", empty && "text-ink-mute")}>{empty ? placeholder : value}</span>
      <Pencil className="h-2.5 w-2.5 flex-none opacity-0 transition-opacity group-hover/edit:opacity-70" />
    </button>
  );
}

/* ───────── 时段徽标 ───────── */

const TIME_WORDS: [RegExp, string][] = [
  [/午夜|子夜/, "子夜"],
  [/晨|朝|黎明/, "清晨"],
  [/黄昏|傍晚|暮/, "黄昏"],
  [/夜|晚/, "夜间"],
  [/午/, "午后"],
  [/日|昼|白天/, "日间"],
  [/阴|雾/, "阴翳"],
  [/雨/, "雨中"],
  [/雪/, "雪后"],
];

/** 「日」「night」这类拆解回来的原词统一成截图里那种两字徽标 */
export function timeLabel(time?: string): string {
  const raw = (time ?? "").trim();
  if (!raw) return "未定时段";
  const hit = TIME_WORDS.find(([re]) => re.test(raw));
  return hit ? hit[1] : raw;
}

/* ───────── 弹层 ───────── */

export function Sheet({
  open,
  onClose,
  title,
  eyebrow,
  children,
  footer,
  width = 760,
}: {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  eyebrow?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  width?: number;
}) {
  const panel = useRef<HTMLDivElement>(null);
  const restore = useRef<HTMLElement | null>(null);
  /** onClose 每次渲染都是新函数：放进 ref，避免每帧重挂监听 + 抢焦点 */
  const close = useRef(onClose);
  useEffect(() => {
    close.current = onClose;
  }, [onClose]);

  useEffect(() => {
    if (!open) return;
    restore.current = document.activeElement as HTMLElement | null;
    const timer = window.setTimeout(() => {
      const first = panel.current?.querySelector<HTMLElement>("input:not([type='hidden']),textarea,select,button");
      (first ?? panel.current)?.focus();
    }, 0);
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        close.current();
        return;
      }
      if (e.key !== "Tab" || !panel.current) return;
      const nodes = Array.from(
        panel.current.querySelectorAll<HTMLElement>(
          "a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex='-1'])",
        ),
      ).filter((n) => n.offsetParent !== null || n === document.activeElement);
      if (!nodes.length) return;
      const first = nodes[0];
      const last = nodes[nodes.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.clearTimeout(timer);
      window.removeEventListener("keydown", onKey);
      restore.current?.focus?.();
    };
  }, [open]);

  if (!open) return null;
  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-void/80 p-4 backdrop-blur-sm"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-label={typeof title === "string" ? title : undefined}
        tabIndex={-1}
        style={{ maxWidth: width }}
        className="glass-strong mb-6 mt-6 w-full rounded-sheet shadow-2xl"
      >
        <header className="flex items-start justify-between gap-4 border-b border-hairline px-5 py-3.5">
          <div className="min-w-0">
            {eyebrow && <div className="label-mono mb-0.5">{eyebrow}</div>}
            <h2 className="truncate text-subtitle font-semibold">{title}</h2>
          </div>
          <button onClick={onClose} aria-label="关闭" className="flex-none rounded-ctl p-1.5 text-ink-mute transition-colors hover:bg-hairline hover:text-ink">
            <X className="h-4 w-4" />
          </button>
        </header>
        <div className="max-h-[70vh] overflow-y-auto px-5 py-4">{children}</div>
        {footer && <footer className="flex flex-wrap items-center justify-end gap-2 border-t border-hairline px-5 py-3">{footer}</footer>}
      </div>
    </div>
  );
}

export interface ConfirmRequest {
  title: string;
  body: ReactNode;
  confirmLabel: string;
  danger?: boolean;
  onConfirm: () => void;
}

export function ConfirmSheet({ request, onClose }: { request: ConfirmRequest | null; onClose: () => void }) {
  return (
    <Sheet
      open={!!request}
      onClose={onClose}
      title={request?.title ?? ""}
      eyebrow="Confirm"
      width={460}
      footer={
        <>
          <Button variant="quiet" onClick={onClose}>
            取消
          </Button>
          <Button
            variant={request?.danger ? "danger" : "primary"}
            onClick={() => {
              request?.onConfirm();
              onClose();
            }}
          >
            {request?.confirmLabel ?? "确定"}
          </Button>
        </>
      }
    >
      <div className="flex items-start gap-2.5 text-body leading-relaxed text-ink-dim">
        {request?.danger && <CircleAlert className="mt-0.5 h-4 w-4 flex-none text-state-fail" />}
        <div className="min-w-0 space-y-2">{request?.body}</div>
      </div>
    </Sheet>
  );
}

/* ───────── 小装饰 ───────── */

/** 分区标题前那个会发光的点 */
export function SectionDot({ tone = "chrome" }: { tone?: "chrome" | "ok" }) {
  const color = tone === "ok" ? "var(--color-state-ok)" : "var(--color-chrome)";
  return <span className="h-1.5 w-1.5 flex-none rounded-full" style={{ background: color, boxShadow: `0 0 10px color-mix(in srgb, ${color} 55%, transparent)` }} />;
}
