import { AlertCircle, CircleCheck } from "lucide-react";
import { Badge, Button, Input, Toggle } from "../../../components/ui";
import type { JobPlanResult } from "../../../lib/api";

/**
 * 派发现场的参数总表（借鉴 oh-my-minimaxh3-director 的「阶段 3：参数确认」）。
 *
 * 为什么要这张表：出片一镜 6–20 分钟，参数错了要在 ComfyUI 报错之后才发现。
 * 判定全在后端 job_plan.py，这里只把它算好的东西摊开 + 允许就地改数值再问一次。
 * 耗时那一列必须带着「外推」两个字显示，别让它看起来像实测。
 */

/** 能在这张表上直接改的槽位。其余（提示词、参考图、前缀）只读，改了要走导演台 */
const EDITABLE: Record<string, { label: string; kind: "int" | "float" | "bool" }> = {
  seconds: { label: "时长 s", kind: "int" },
  steps: { label: "步数", kind: "int" },
  seed: { label: "种子", kind: "int" },
  width: { label: "宽", kind: "int" },
  height: { label: "高", kind: "int" },
  cfg: { label: "CFG", kind: "float" },
  resolution: { label: "参考图档", kind: "int" },
  turbo: { label: "Turbo", kind: "bool" },
};

const DERIVED_LABEL: Record<string, string> = {
  frameCount: "帧",
  realSeconds: "实秒",
  megapixels: "MP",
  promptChars: "提示词字数",
  refCount: "参考图",
  segmentCount: "段数",
  graphNodes: "节点",
};

function fmtEta(sec: unknown): string {
  if (!sec) return "—";
  const s = Number(sec);
  const m = Math.floor(s / 60);
  return m >= 1 ? `${m} 分 ${String(s % 60).padStart(2, "0")} 秒` : `${s} 秒`;
}

interface Props {
  plan: JobPlanResult | null;
  busy: boolean;
  error: string | null;
  onPatch: (rowIndex: number, slot: string, value: unknown) => void;
  onRevalidate: () => void;
  onConfirm: () => void;
  onCancel: () => void;
}

export function PlanTable({ plan, busy, error, onPatch, onRevalidate, onConfirm, onCancel }: Props) {
  const rows = plan?.rows ?? [];
  const blocked = plan?.totals.blocked ?? 0;

  return (
    <div className="space-y-2.5">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11.5px] text-ink-dim">
        <span>
          共 <span className="mono text-ink">{plan?.totals.count ?? 0}</span> 条，
          <span className={blocked ? "text-[color:var(--color-state-fail)]" : "text-[color:var(--color-state-ok)]"}>
            {blocked ? ` ${blocked} 条被拦下` : " 没有拦下项"}
          </span>
        </span>
        <span>
          预计合计 <span className="mono text-ink">{fmtEta(plan?.totals.etaSeconds)}</span>
          <span className="ml-1 text-[10.5px] text-ink-mute">（外推，非实测：视频锚 864×480/56 帧/Turbo8=443s）</span>
        </span>
        <span className="ml-auto flex items-center gap-1.5">
          {busy && <span className="text-[10.5px] text-ink-mute">重新校验中…</span>}
          <Button size="sm" variant="ghost" onClick={onRevalidate} disabled={busy} title="按当前数值再问一次后端">
            重新校验
          </Button>
        </span>
      </div>

      {error && (
        <p className="flex items-start gap-1.5 rounded-ctl border border-[color:var(--color-state-fail)]/40 bg-[color:var(--color-state-fail)]/10 px-2 py-1.5 text-[11px] leading-snug text-ink">
          <AlertCircle className="mt-[1px] h-3 w-3 flex-none" />
          <span>后端参数表没拿到：{error}。这张表只是确认用，你也可以直接派发——但那就等于回到「跑出问题才知道」。</span>
        </p>
      )}

      <div className="max-h-[52vh] space-y-1.5 overflow-y-auto pr-1">
        {rows.map((r) => (
          <div key={r.index} className={r.blocked ? "rounded-ctl border border-[color:var(--color-state-fail)]/45 bg-[color:var(--color-state-fail)]/[0.07] p-2" : "rounded-ctl border border-rule-soft bg-white/[0.03] p-2"}>
            <div className="flex flex-wrap items-center gap-2">
              <span className="mono text-[11px] text-ink">{r.title}</span>
              <Badge>{r.template}</Badge>
              {r.instance && (
                <span className="text-[10.5px] text-ink-mute">
                  {r.instance.label || `实例 ${r.instance.id}`} · {r.instance.placement}
                  {r.instance.probeOk === false ? " · 探活失败" : ""}
                  {r.instance.circuitOpen ? " · 已熔断" : ""}
                </span>
              )}
              <span className="ml-auto text-[10.5px] text-ink-mute">{fmtEta(r.derived.etaSeconds)}</span>
            </div>

            <div className="mt-1.5 flex flex-wrap items-end gap-1.5">
              {Object.entries(EDITABLE)
                .filter(([k]) => k in r.slots)
                .map(([k, meta]) =>
                  meta.kind === "bool" ? (
                    <span key={k} className="flex items-center gap-1 rounded-ctl border border-rule bg-raised px-1.5 py-[3px]">
                      <span className="mono text-[10px] text-ink-mute">{meta.label}</span>
                      <Toggle checked={!!r.slots[k]} onChange={(v) => onPatch(r.index, k, v)} label={`${r.title} ${meta.label}`} />
                    </span>
                  ) : (
                    <label key={k} className="flex items-center gap-1">
                      <span className="mono text-[10px] text-ink-mute">{meta.label}</span>
                      <Input
                        className="h-6 w-[68px] text-[11px]"
                        type="number"
                        step={meta.kind === "float" ? "0.1" : "1"}
                        defaultValue={r.slots[k] as number | string}
                        aria-label={`${r.title} ${meta.label}`}
                        onBlur={(e) => {
                          const raw = e.target.value;
                          const num = Number(raw);
                          if (raw !== "" && Number.isFinite(num) && num !== Number(r.slots[k])) onPatch(r.index, k, num);
                        }}
                        onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()}
                      />
                    </label>
                  ),
                )}
              {Object.entries(DERIVED_LABEL).map(([k, label]) =>
                r.derived[k] === undefined || r.derived[k] === null ? null : (
                  <span key={k} className="mono rounded-ctl border border-rule-soft bg-raised/60 px-1.5 py-[3px] text-[10px] text-ink-dim">
                    {label} {String(r.derived[k])}
                  </span>
                ),
              )}
              {!!r.slots.filename_prefix && <span className="mono text-[10px] text-ink-mute">→ {String(r.slots.filename_prefix)}</span>}
            </div>

            {r.problems.length > 0 && (
              <ul className="mt-1.5 space-y-0.5">
                {r.problems.map((p) => (
                  <li key={p} className={r.blocked ? "flex items-start gap-1.5 text-[10.5px] leading-snug text-[color:var(--color-state-fail)]" : "flex items-start gap-1.5 text-[10.5px] leading-snug text-[color:var(--color-mach-rh)]"}>
                    <AlertCircle className="mt-[1px] h-3 w-3 flex-none" />
                    <span>{p}</span>
                  </li>
                ))}
              </ul>
            )}
            {!r.problems.length && (
              <p className="mt-1.5 flex items-center gap-1.5 text-[10.5px] text-[color:var(--color-state-ok)]">
                <CircleCheck className="h-3 w-3" /> 参数与形状都过了
              </p>
            )}
          </div>
        ))}
        {!rows.length && !busy && <p className="text-[11.5px] text-ink-mute">后端没给出任何行。</p>}
      </div>

      <div className="flex items-center gap-2 text-[11px] text-ink-mute">
        <span>被拦下的条目不会进队列。改数值直接在上面改，改完自动重问一次。</span>
      </div>

      <div className="flex items-center justify-end gap-2">
        <Button variant="quiet" onClick={onCancel}>
          取消
        </Button>
        <Button variant="primary" disabled={busy || !rows.length || blocked === rows.length} onClick={onConfirm} title={blocked ? `其中 ${blocked} 条不合格，会被后端逐条拒收` : "按这张表的参数逐条入队"}>
          开始派发 {blocked ? `（跳过 ${blocked} 条）` : ""}
        </Button>
      </div>
    </div>
  );
}
