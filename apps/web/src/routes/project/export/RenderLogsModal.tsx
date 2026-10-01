import { useState } from "react";
import { ChevronDown, ChevronRight, Clock } from "lucide-react";
import { Badge, Button, Modal, StateGlyph, type StateKey } from "../../../components/ui";
import type { JobKind, RenderLog } from "../../../lib/types";
import { cn, fmtDur, fmtTime } from "../../../lib/utils";

const KIND_ZH: Record<JobKind, string> = {
  llm_chat: "文本",
  image: "图像",
  video: "视频",
  video_chain: "续拍链",
  upscale: "放大",
  detect_shots: "镜头切分",
  assemble: "合成",
  workflow_test: "试运行",
};

const RESOURCE_ZH: Record<NonNullable<RenderLog["resourceType"]>, string> = {
  character: "角色",
  "character-variation": "服装变体",
  scene: "场景",
  keyframe: "关键帧",
  video: "视频段",
  "script-parsing": "剧本拆解",
  export: "导出",
};

function stateKey(s: RenderLog["status"]): StateKey {
  if (s === "dispatching") return "running";
  return s;
}

/**
 * 渲染日志：按时间倒序回答「这一条是谁、用了哪个模型、跑了多久、成没成、错在哪」。
 * 缺字段就显示缺，不替后端编造资源名或模型名。
 */
export function RenderLogsModal({
  open,
  onClose,
  logs,
  shotLabelById,
}: {
  open: boolean;
  onClose: () => void;
  logs: RenderLog[];
  shotLabelById: Map<string, string>;
}) {
  const [onlyFailed, setOnlyFailed] = useState(false);
  const [expanded, setExpanded] = useState<string | null>(null);

  const failed = logs.filter((l) => l.status === "failed").length;
  const shown = onlyFailed ? logs.filter((l) => l.status === "failed") : logs;

  return (
    <Modal open={open} onClose={onClose} width={820} title={<Title failed={failed} total={logs.length} />} footer={<Button onClick={onClose}>关闭</Button>}>
      {logs.length === 0 ? (
        <p className="py-10 text-center text-note text-ink-mute">这个项目还没有生成记录。</p>
      ) : (
        <div className="space-y-2">
          <div className="flex items-center justify-between gap-2">
            <div className="grid flex-1 grid-cols-3 gap-2">
              <Metric label="总条数" value={logs.length} />
              <Metric label="成功" value={logs.filter((l) => l.status === "succeeded").length} />
              <Metric label="失败" value={failed} tone="bad" />
            </div>
            <Button size="sm" variant={onlyFailed ? "primary" : "quiet"} aria-pressed={onlyFailed} onClick={() => setOnlyFailed((v) => !v)}>
              {onlyFailed ? "只看失败中" : "只看失败"}
            </Button>
          </div>

          {shown.length === 0 ? (
            <p className="py-6 text-center text-note text-ink-mute">没有失败记录。</p>
          ) : (
            <ul className="space-y-1.5">
              {shown.map((l, i) => {
                const key = `${l.ts}-${l.jobId}-${i}`;
                const name = l.resourceName ?? (l.shotId ? `镜 ${shotLabelById.get(l.shotId) ?? l.shotId}` : undefined);
                const open2 = expanded === key;
                const hasDetail = !!(l.prompt || l.resourceId || l.jobId || l.instanceId);
                return (
                  <li key={key} className="rounded-panel border border-rule-soft bg-panel">
                    <button
                      type="button"
                      onClick={() => setExpanded(open2 ? null : key)}
                      disabled={!hasDetail}
                      className="flex w-full items-start gap-2.5 px-3 py-2 text-left disabled:cursor-default"
                    >
                      <StateGlyph state={stateKey(l.status)} className="mt-1" />
                      <span className="min-w-0 flex-1">
                        <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
                          <span className="text-body font-medium">{name ?? "未命名任务"}</span>
                          <Badge>{KIND_ZH[l.kind] ?? l.kind}</Badge>
                          {l.resourceType && <Badge>{RESOURCE_ZH[l.resourceType] ?? l.resourceType}</Badge>}
                          {l.durationMs != null && <Badge>{fmtDur(l.durationMs)}</Badge>}
                          {l.model && <span className="mono text-caption text-ink-mute">{l.model}</span>}
                        </span>
                        <span className="mono mt-0.5 block text-caption text-ink-mute">
                          {fmtTime(l.ts)} · {l.status}
                          {l.instanceId ? ` · ${l.instanceId}` : ""}
                        </span>
                        {l.error && (
                          <span className="mt-1 block text-note leading-snug text-state-fail">{l.error}</span>
                        )}
                      </span>
                      {hasDetail && (open2 ? <ChevronDown className="mt-1 h-3.5 w-3.5 flex-none text-ink-mute" /> : <ChevronRight className="mt-1 h-3.5 w-3.5 flex-none text-ink-mute" />)}
                    </button>
                    {open2 && (
                      <div className="space-y-2 border-t border-rule-soft px-3 py-2">
                        {l.jobId && <Field label="任务 ID" value={l.jobId} mono />}
                        {l.resourceId && <Field label="资源 ID" value={l.resourceId} mono />}
                        {l.prompt ? (
                          <Field label="投产提示词" value={l.prompt} />
                        ) : (
                          <p className="text-caption text-ink-mute">这条日志没记提示词（后端只在生成请求里带 model/prompt 时才有）。</p>
                        )}
                      </div>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      )}
    </Modal>
  );
}

function Title({ failed, total }: { failed: number; total: number }) {
  return (
    <span className="flex items-center gap-2">
      <Clock className="h-4 w-4 text-chrome" />
      渲染日志
      <span className="label-mono rounded-full border border-chrome/25 bg-chrome/10 px-2 py-0.5 text-chrome">{total} 条</span>
      {failed > 0 && <span className="label-mono text-state-fail">{failed} 失败</span>}
    </span>
  );
}

function Metric({ label, value, tone }: { label: string; value: number; tone?: "bad" }) {
  return (
    <div className="rounded-panel border border-rule-soft bg-panel px-2.5 py-1.5">
      <div className="label-mono">{label}</div>
      <div className={cn("mono mt-0.5 text-title font-semibold", tone === "bad" ? "text-state-fail" : "text-ink")}>{value}</div>
    </div>
  );
}

function Field({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div>
      <div className="label-mono mb-0.5">{label}</div>
      <div className={cn("max-h-32 overflow-y-auto rounded-ctl bg-slate px-2 py-1 text-caption leading-relaxed text-ink-dim", mono && "mono break-all")}>
        {value}
      </div>
    </div>
  );
}
