import { BarChart3, CircleCheck } from "lucide-react";
import { cn } from "../../../lib/utils";

/**
 * MASTER SEQUENCE —— 整片的「场记板」。
 * 数字全部来自项目本身：SHOTS 是镜头条数，EST. DURATION 是 Σ durationSec，
 * TARGET 是项目配置的目标时长，RENDER STATUS 是有成片的镜头占比（renderProgress）。
 */
export function MasterCard({
  name,
  shots,
  estSec,
  targetSec,
  percent,
  done,
  total,
  aspectRatio,
  fullQuality,
}: {
  name: string;
  shots: number;
  estSec: number;
  targetSec: number;
  percent: number;
  done: number;
  total: number;
  aspectRatio: string;
  fullQuality: boolean;
}) {
  const complete = total > 0 && done === total;
  return (
    <section className="glass rounded-panel p-6 md:p-8">
      <div className="flex flex-col justify-between gap-6 md:flex-row md:items-start">
        <div className="min-w-0 space-y-4">
          <div className="flex flex-wrap items-center gap-3">
            <h2 className="truncate text-display font-bold leading-none tracking-tight text-ink">{name || "未命名项目"}</h2>
            <span className="label-mono rounded-full border border-chrome/25 bg-chrome/10 px-2 py-1 text-chrome">Master Sequence</span>
          </div>
          <div className="flex flex-wrap items-center gap-x-6 gap-y-3">
            <Stat label="Shots" value={String(shots)} />
            <Divider />
            <Stat label="Est. Duration" value={`~${Math.round(estSec)}s`} />
            <Divider />
            <Stat label="Target" value={`${targetSec}s`} />
            <Divider />
            <Stat label="Format" value={`${aspectRatio} · ${fullQuality ? "全质量" : "预览档"}`} />
          </div>
          {estSec > 0 && Math.abs(estSec - targetSec) / Math.max(1, targetSec) > 0.15 && (
            <p className="text-note leading-snug text-ink-mute">
              计划时长与目标差 {Math.round(Math.abs(estSec - targetSec))} 秒：{estSec > targetSec ? "要么删镜，要么把目标调高" : "还有余量，可以再加镜头或拉长每镜秒数"}。
              导出不会替你做这个决定，它只按现在的时间轴拼。
            </p>
          )}
        </div>

        <div
          className={cn(
            "flex-none rounded-panel border px-5 py-3 text-right",
            complete ? "border-state-ok/40 bg-state-ok/10" : "border-chrome/25 bg-chrome/10",
          )}
        >
          <div className="flex items-baseline justify-end gap-1">
            <span className={cn("mono text-stat font-bold leading-none", complete ? "text-state-ok" : "text-chrome")}>{percent}</span>
            <span className="text-body text-ink-mute">%</span>
          </div>
          <div className="label-mono mt-1.5 flex items-center justify-end gap-1.5">
            {complete ? <CircleCheck className="h-3 w-3 text-state-ok" /> : <BarChart3 className="h-3 w-3" />}
            Render Status
          </div>
          <div className="mono mt-1 text-caption text-ink-mute">
            {done}/{total} 镜有成片
          </div>
        </div>
      </div>
    </section>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="label-mono">{label}</div>
      <div className="mono mt-1 text-title font-semibold text-ink">{value}</div>
    </div>
  );
}

function Divider() {
  return <span className="hidden h-8 w-px flex-none bg-rule-soft md:block" aria-hidden />;
}
