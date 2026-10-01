import { forwardRef, useEffect, useId, useRef, useState, type ReactNode } from "react";
import { cn } from "../lib/utils";

/* 说明：整套控件不用装饰性色彩。色相只出现在 MachChip（哪台机器）
   与状态条的「亮度 + 图形」上，见 index.css 的 token 注释。 */

type BtnVariant = "primary" | "default" | "ghost" | "danger" | "quiet";

export const Button = forwardRef<
  HTMLButtonElement,
  React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: BtnVariant; size?: "sm" | "md"; icon?: ReactNode; loading?: boolean }
>(function Button(
  { variant = "default", size = "md", type = "button", icon, loading, className, children, disabled, ...rest },
  ref,
) {
  const base =
    "inline-flex items-center justify-center gap-1.5 rounded-ctl border font-medium transition-colors disabled:opacity-45 disabled:cursor-not-allowed";
  const sizes = size === "sm" ? "h-6 px-2 text-note" : "h-7 px-3 text-body";
  const variants: Record<BtnVariant, string> = {
    // 主操作：反相的中性块，不靠彩色
    primary: "bg-ink text-slate border-transparent hover:bg-ink-hi",
    default: "bg-raised text-ink border-rule hover:bg-row-hover",
    ghost: "bg-transparent text-ink-dim border-transparent hover:bg-sheen hover:text-ink",
    danger: "bg-transparent text-state-fail border-state-fail/45 hover:bg-state-fail/12",
    quiet: "bg-transparent text-ink-mute border-rule hover:text-ink",
  };
  return (
    <button ref={ref} type={type} className={cn(base, sizes, variants[variant], className)} disabled={disabled || loading} {...rest}>
      {loading ? <Spinner /> : icon}
      {children}
    </button>
  );
});

export function Spinner({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 16 16" className={cn("h-3.5 w-3.5 animate-spin", className)} aria-hidden>
      <circle cx="8" cy="8" r="6" fill="none" stroke="currentColor" strokeWidth="2" strokeDasharray="28 10" />
    </svg>
  );
}

export function Input({ className, ...p }: React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      className={cn(
        "h-7 rounded-ctl border border-rule bg-slate px-2 text-body text-ink placeholder:text-ink-mute",
        "focus:border-ink-dim disabled:opacity-50",
        className,
      )}
      {...p}
    />
  );
}

export function Textarea({ className, ...p }: React.TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return (
    <textarea
      className={cn(
        "rounded-ctl border border-rule bg-slate p-2 text-body leading-[1.6] text-ink placeholder:text-ink-mute",
        "focus:border-ink-dim font-sans",
        className,
      )}
      {...p}
    />
  );
}

export function Select({ className, children, ...p }: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select
      className={cn(
        "h-7 rounded-ctl border border-rule bg-slate px-1.5 text-body text-ink focus:border-ink-dim",
        className,
      )}
      {...p}
    >
      {children}
    </select>
  );
}

export function Toggle({
  checked,
  onChange,
  label,
  hint,
  disabled,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label?: ReactNode;
  hint?: ReactNode;
  disabled?: boolean;
}) {
  return (
    // 用真的 <input type=checkbox role=switch> 包在 <label> 里：
    // 之前是 <label> 套 <button>，点标题文字完全不触发，中文长标签等于白白那么大一块没反应。
    <label className={cn("flex cursor-pointer items-start gap-2", disabled && "cursor-not-allowed opacity-50")}>
      <input
        type="checkbox"
        role="switch"
        checked={checked}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
        className="peer sr-only"
      />
      <span
        aria-hidden
        className={cn(
          // 轨道 36×20，比原来的 28×16 好点；焦点环画在轨道上，因为 input 本身是屏幕阅读器专用的
          "mt-px block h-5 w-9 flex-none rounded-panel border transition-colors",
          "peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-chrome",
          checked ? "border-transparent bg-ink-dim" : "border-rule bg-slate",
        )}
      >
        <span
          className={cn(
            "block h-4 w-4 translate-y-[1px] rounded-full bg-ink transition-transform",
            checked ? "translate-x-[19px]" : "translate-x-[2px]",
          )}
        />
      </span>
      {label && (
        <span>
          <span className="block text-body leading-tight text-ink">{label}</span>
          {hint && <span className="block text-caption leading-tight text-ink-mute">{hint}</span>}
        </span>
      )}
    </label>
  );
}

export function Field({ label, hint, children, className }: { label: ReactNode; hint?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <div className={cn("space-y-1", className)}>
      <div className="label">{label}</div>
      {children}
      {hint && <div className="text-caption leading-snug text-ink-mute">{hint}</div>}
    </div>
  );
}

export function Panel({
  title,
  actions,
  children,
  className,
  bodyClass,
  dense,
}: {
  title?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClass?: string;
  dense?: boolean;
}) {
  return (
    <section className={cn("rounded-panel border border-rule-soft bg-panel", className)}>
      {(title || actions) && (
        <header className="flex items-center justify-between gap-3 border-b border-rule-soft px-3 py-1.5">
          <h2 className="text-note font-semibold text-ink-dim">{title}</h2>
          {actions && <div className="flex items-center gap-1.5">{actions}</div>}
        </header>
      )}
      <div className={cn(dense ? "" : "p-3", bodyClass)}>{children}</div>
    </section>
  );
}

export function KeyVal({ items }: { items: readonly (readonly [ReactNode, ReactNode])[] }) {
  return (
    <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-note">
      {items.map(([k, v], i) => (
        <div key={i} className="contents">
          <dt className="text-ink-mute">{k}</dt>
          <dd className="text-ink">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

/* ───────── 状态：亮度 + 图形，不用色相 ───────── */

export type StateKey = "idle" | "queued" | "running" | "succeeded" | "failed" | "canceled";

const STATE_TEXT: Record<StateKey, string> = {
  idle: "待处理",
  queued: "排队",
  running: "进行中",
  succeeded: "完成",
  failed: "失败",
  canceled: "已取消",
};

/** 图形与颜色同时出现，色盲也能分辨 */
export function StateGlyph({ state, className }: { state: StateKey; className?: string }) {
  const c = cn("inline-block flex-none", className);
  if (state === "running")
    return <Spinner className={cn(c, "text-state-running")} />;
  if (state === "succeeded")
    return (
      <svg viewBox="0 0 12 12" className={cn(c, "h-3 w-3 text-state-ok")} aria-hidden>
        <path d="M2 6.4 4.6 9 10 3" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
      </svg>
    );
  if (state === "failed")
    return (
      <svg viewBox="0 0 12 12" className={cn(c, "h-3 w-3 text-state-fail")} aria-hidden>
        <path d="M3 3l6 6M9 3l-6 6" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
      </svg>
    );
  if (state === "canceled")
    return (
      <svg viewBox="0 0 12 12" className={cn(c, "h-3 w-3 text-state-canceled")} aria-hidden>
        <circle cx="6" cy="6" r="4.2" fill="none" stroke="currentColor" strokeWidth="1.3" />
        <path d="M3.2 8.8 8.8 3.2" stroke="currentColor" strokeWidth="1.3" />
      </svg>
    );
  if (state === "queued") return <span className={cn(c, "h-2 w-2 rounded-full ring-1 ring-state-queued")} />;
  return <span className={cn(c, "h-1.5 w-1.5 rounded-full bg-state-idle")} />;
}

export function StateLabel({ state }: { state: StateKey }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-note text-ink-dim">
      <StateGlyph state={state} />
      {STATE_TEXT[state]}
    </span>
  );
}

export function MachChip({ placement, label, className }: { placement: "local" | "cloud_self" | "cloud_runninghub"; label?: string; className?: string }) {
  const names = { local: "本机", cloud_self: "自建云", cloud_runninghub: "RunningHub" } as const;
  return (
    <span data-mach={placement} className={cn("machchip", className)}>
      <i />
      {label ?? names[placement]}
    </span>
  );
}

/** 进度：给不出百分比时明确说出来，不用假进度 */
export function Progress({
  value,
  max,
  stripe,
  machine,
  unavailable,
  stage,
  className,
}: {
  value?: number;
  max?: number;
  stripe?: boolean;
  machine?: string;
  unavailable?: boolean;
  stage?: string | null;
  className?: string;
}) {
  const pctv = value !== undefined && max ? Math.min(100, Math.round((value / max) * 100)) : null;
  return (
    <div className={cn("space-y-1", className)}>
      <div
        className={cn("h-1 overflow-hidden rounded-hairline bg-track", stripe && pctv === null && "stripe")}
        style={{ ["--stripe" as string]: machine ?? "var(--color-state-running)" }}
        role="progressbar"
        aria-valuenow={pctv ?? undefined}
        aria-valuemin={0}
        aria-valuemax={100}
      >
        {pctv !== null && (
          <div
            className="h-full transition-[width] duration-500"
            style={{ width: `${pctv}%`, background: machine ?? "var(--color-state-running)" }}
          />
        )}
      </div>
      <div className="flex items-baseline justify-between text-caption text-ink-mute">
        <span>{unavailable ? "该平台不给百分比" : pctv !== null ? `${pctv}%` : "—"}</span>
        <span className="truncate pl-2">{stage}</span>
      </div>
    </div>
  );
}

export function Badge({ children, tone = "neutral", className }: { children: ReactNode; tone?: "neutral" | "warn" | "bad" | "good"; className?: string }) {
  const tones = {
    neutral: "border-rule text-ink-dim",
    warn: "border-mach-rh/50 text-mach-rh",
    bad: "border-state-fail/50 text-state-fail",
    good: "border-state-ok/45 text-state-ok",
  } as const;
  return <span className={cn("rounded-panel border px-1.5 py-[1px] text-caption leading-tight", tones[tone], className)}>{children}</span>;
}

export function Tabs<T extends string>({
  tabs,
  value,
  onChange,
  className,
}: {
  tabs: { key: T; label: ReactNode; badge?: ReactNode }[];
  value: T;
  onChange: (t: T) => void;
  className?: string;
}) {
  const refs = useRef<Record<string, HTMLButtonElement | null>>({});
  // role=tablist 必须能用方向键走；只给 Tab 键的话，读屏用户要按 N 次 Tab 才能换一页
  const move = (from: number, step: number) => {
    const next = (from + step + tabs.length) % tabs.length;
    const key = tabs[next].key;
    onChange(key);
    refs.current[key]?.focus();
  };
  return (
    <div className={cn("flex items-center gap-0.5 border-b border-rule-soft", className)} role="tablist">
      {tabs.map((t, i) => (
        <button
          key={t.key}
          ref={(el) => {
            refs.current[t.key] = el;
          }}
          type="button"
          role="tab"
          aria-selected={value === t.key}
          tabIndex={value === t.key ? 0 : -1}
          onKeyDown={(e) => {
            if (e.key === "ArrowRight") {
              e.preventDefault();
              move(i, 1);
            } else if (e.key === "ArrowLeft") {
              e.preventDefault();
              move(i, -1);
            }
          }}
          onClick={() => onChange(t.key)}
          className={cn(
            "-mb-px flex items-center gap-1.5 border-b-2 px-2.5 py-1.5 text-body transition-colors",
            value === t.key ? "border-ink-dim text-ink" : "border-transparent text-ink-mute hover:text-ink-dim",
          )}
        >
          {t.label}
          {t.badge}
        </button>
      ))}
    </div>
  );
}

export function Modal({
  open,
  onClose,
  title,
  children,
  footer,
  width = 620,
}: {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  width?: number;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const titleId = useId();
  // 关掉之后焦点要回到打开它的那个按钮，否则键盘用户会掉回页面顶部
  const opener = useRef<HTMLElement | null>(null);
  useEffect(() => {
    if (!open) return;
    opener.current = document.activeElement as HTMLElement | null;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);
  useEffect(() => {
    if (open) ref.current?.querySelector<HTMLElement>("input,textarea,select,button")?.focus();
    else opener.current?.focus();
  }, [open]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-scrim/60 p-6" onMouseDown={onClose}>
      <div
        ref={ref}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        onMouseDown={(e) => e.stopPropagation()}
        style={{ maxWidth: width }}
        className="mt-6 w-full rounded-sheet border border-rule bg-panel shadow-[0_18px_50px_rgba(0,0,0,.55)]"
      >
        <header className="flex items-center justify-between border-b border-rule-soft px-3 py-2">
          <h2 id={titleId} className="text-body font-semibold">
            {title}
          </h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="关闭"
            className="rounded-ctl p-1 text-ink-mute hover:bg-sheen hover:text-ink"
          >
            <svg viewBox="0 0 14 14" className="h-3.5 w-3.5" aria-hidden>
              <path d="M3 3l8 8M11 3l-8 8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
            </svg>
          </button>
        </header>
        <div className="max-h-[68vh] overflow-y-auto p-3">{children}</div>
        {footer && <footer className="flex items-center justify-end gap-2 border-t border-rule-soft px-3 py-2">{footer}</footer>}
      </div>
    </div>
  );
}

export function Empty({ title, action, hint }: { title: ReactNode; action?: ReactNode; hint?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 rounded-panel border border-dashed border-rule px-6 py-10 text-center">
      <div className="text-body text-ink-dim">{title}</div>
      {hint && <div className="max-w-md text-note leading-snug text-ink-mute">{hint}</div>}
      {action}
    </div>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={cn("animate-pulse rounded-ctl bg-skeleton", className)} />;
}

/** 没有后端时不加载外部图片：用 id 派生的确定性色块代表画面 */
export function MediaFrame({
  seedText,
  kind,
  className,
  label,
  aspect = "16/9",
}: {
  seedText: string;
  kind: "image" | "video" | "audio" | "none";
  className?: string;
  label?: ReactNode;
  aspect?: string;
}) {
  let h = 0;
  for (let i = 0; i < seedText.length; i++) h = (h * 31 + seedText.charCodeAt(i)) >>> 0;
  const a = h % 360;
  const b = (a + 40 + (h % 70)) % 360;
  const bg =
    kind === "none"
      ? "repeating-linear-gradient(45deg,#1a2226 0 8px,#151d21 8px 16px)"
      : `linear-gradient(${(h % 4) * 45}deg, hsl(${a} 22% 26%), hsl(${b} 26% 14%))`;
  return (
    <div
      className={cn("relative overflow-hidden rounded-panel border border-rule-soft", className)}
      style={{ aspectRatio: aspect, background: bg }}
    >
      {kind === "video" && (
        <svg viewBox="0 0 24 24" className="absolute left-1/2 top-1/2 h-6 w-6 -translate-x-1/2 -translate-y-1/2 text-white/70" aria-hidden>
          <path d="M8 5v14l11-7z" fill="currentColor" />
        </svg>
      )}
      {kind === "audio" && (
        <div className="absolute inset-x-2 bottom-2 flex h-4 items-end gap-[2px]">
          {Array.from({ length: 26 }).map((_, i) => (
            <span key={i} className="flex-1 bg-white/45" style={{ height: `${20 + ((h >> i) % 80)}%` }} />
          ))}
        </div>
      )}
      {label && <div className="absolute bottom-0 left-0 right-0 bg-black/45 px-1.5 py-[2px] text-micro text-white/85">{label}</div>}
    </div>
  );
}

export function Copyable({ text, children, className }: { text: string; children?: ReactNode; className?: string }) {
  const [done, setDone] = useState(false);
  return (
    <button
      type="button"
      className={cn("group inline-flex max-w-full items-center gap-1 text-left", className)}
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text);
        } catch {
          const ta = document.createElement("textarea");
          ta.value = text;
          document.body.append(ta);
          ta.select();
          document.execCommand("copy");
          ta.remove();
        }
        setDone(true);
        setTimeout(() => setDone(false), 1400);
      }}
      title="点击复制"
      aria-label={`复制 ${text}`}
    >
      <span className="mono truncate">{children ?? text}</span>
      {/* 只在 hover 出现的提示，键盘用户永远看不见：焦点也要点亮它 */}
      <span aria-live="polite" className="flex-none text-micro text-ink-mute opacity-0 transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100">
        {done ? "已复制" : "复制"}
      </span>
    </button>
  );
}

/**
 * 筛选小胶囊。原来长在队列页里，回收站与生成历史也要用同一颗 ——
 * 再复制一份就是第三种 chip（第三种已经在资产库弹窗里了），所以提到控件层。
 */
export function FilterChip({ active, onClick, children }: { active: boolean; onClick: () => void; children: ReactNode }) {
  return (
    <button
      onClick={onClick}
      aria-pressed={active}
      className={cn(
        "inline-flex items-center rounded-ctl border px-2 py-[3px] text-note transition-colors",
        active ? "border-transparent bg-ink text-slate" : "border-rule bg-raised text-ink-dim hover:text-ink",
      )}
    >
      {children}
    </button>
  );
}
