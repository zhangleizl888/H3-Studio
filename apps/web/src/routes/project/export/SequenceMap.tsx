import { cn } from "../../../lib/utils";
import type { SequenceEntry } from "./utils";
import { tc } from "./utils";

/**
 * SEQUENCE MAP —— 按镜头顺序画时间轴块，块宽随时长比例走。
 * 高亮 = 这一段已经有落盘的产物；空白 = 时间轴上的一处缺口，导出时它不会凭空出现。
 */
export function SequenceMap({
  entries,
  totalSec,
  onSelect,
}: {
  entries: SequenceEntry[];
  totalSec: number;
  onSelect?: (index: number) => void;
}) {
  const done = entries.filter((e) => e.mediaId && e.media).length;
  return (
    <section className="space-y-2">
      <div className="label-mono flex items-baseline justify-between px-1">
        <span>Sequence Map</span>
        <span className="mono">TC {tc(totalSec)}</span>
      </div>
      <div className="flex items-stretch gap-1 rounded-panel border border-rule-soft bg-panel/70 p-2">
        {entries.length === 0 ? (
          <div className="label-mono w-full py-6 text-center">No shots available</div>
        ) : (
          entries.map((e, i) => {
            const has = !!(e.mediaId && e.media);
            const inner = (
              <>
                <span className="mono text-caption text-ink-dim">{e.label}</span>
                <span className="mono text-micro text-ink-mute">{e.sec.toFixed(0)}s</span>
              </>
            );
            const box = cn(
              "flex h-16 min-w-[46px] flex-col items-start justify-end gap-0.5 rounded-strip border px-1.5 py-1 text-left transition-colors",
              has ? "border-chrome/35 bg-chrome/20 hover:bg-chrome/30" : "border-rule-soft bg-raised/40 hover:bg-raised",
            );
            const title = `镜 ${e.label} · ${e.sec.toFixed(1)}s · 入 ${tc(e.inSec)} 出 ${tc(e.outSec)} · ${
              has ? `有成片 ${e.media?.path}` : e.mediaId ? "有成片记录，但本地索引里读不到这条媒体" : "还没有成片"
            }`;
            return has && onSelect ? (
              <button
                key={e.shot.id}
                type="button"
                title={`${title}（点击预览这一镜）`}
                style={{ flexGrow: Math.max(0.5, e.sec), flexBasis: 0 }}
                className={box}
                onClick={() => onSelect(i)}
              >
                {inner}
              </button>
            ) : (
              <div
                key={e.shot.id}
                title={title}
                style={{ flexGrow: Math.max(0.5, e.sec), flexBasis: 0 }}
                className={box}
              >
                {inner}
              </div>
            );
          })
        )}
      </div>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 px-1 text-caption text-ink-mute">
        <span className="inline-flex items-center gap-1.5">
          <span className="h-2.5 w-4 flex-none rounded-[2px] border border-chrome/35 bg-chrome/25" aria-hidden />
          有产物 <span className="mono">{done}</span>
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span className="h-2.5 w-4 flex-none rounded-[2px] border border-rule-soft bg-raised/60" aria-hidden />
          缺口 <span className="mono">{entries.length - done}</span>
        </span>
        <span>时间轴总长 <span className="mono text-ink-dim">{tc(totalSec)}</span>，块宽按各镜 durationSec 比例。</span>
      </div>
    </section>
  );
}
