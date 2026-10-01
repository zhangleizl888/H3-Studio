import { useState } from "react";
import { Play, ZoomIn, ZoomOut } from "lucide-react";
import { Button, Empty } from "../../../components/ui";
import type { Media, Shot } from "../../../lib/types";
import { clamp } from "../../../lib/utils";
import { cardLabel, frameMediaId, timecode, useMediaSrc } from "./common";
import type { DirectorCtx } from "./common";

const MIN_PX = 14;
const MAX_PX = 120;

/**
 * 时间轴：一段一格，宽度按秒计。
 *
 * 与旧版的区别是这里不再画 CSS 渐变假装成片 —— 有视频段就挂真实首帧缩略图并可点击播放，
 * 没有的格子如实显示为空格，读起来才是「哪几段真出了片」。
 */
export function Timeline({ ctx, onPlay }: { ctx: DirectorCtx; onPlay: (mediaId: string, title: string) => void }) {
  const [pxPerSec, setPxPerSec] = useState(26);
  const shots = ctx.shots;
  const total = shots.reduce((a, s) => a + (s.durationSec || 0), 0);
  const width = Math.max(560, total * pxPerSec + 24);

  if (!shots.length) return null;

  return (
    <section className="rounded-panel border border-rule-soft bg-panel">
      <header className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-rule-soft px-3 py-1.5">
        <h2 className="text-note font-semibold text-ink-dim">时间轴</h2>
        <span className="mono text-caption text-ink-mute">
          总时长 {timecode(total)} · {total.toFixed(1)}s · {shots.length} 镜
        </span>
        <span className="label">
          已出片 <span className="mono text-ink-dim">{shots.filter((s) => s.videoMediaIds.length > 0).length}</span>/{shots.length}
        </span>
        <div className="ml-auto flex items-center gap-1">
          <Button size="sm" variant="quiet" aria-label="缩小时间轴" icon={<ZoomOut className="h-3 w-3" />} onClick={() => setPxPerSec(clamp(pxPerSec / 1.4, MIN_PX, MAX_PX))} />
          <span className="mono w-14 text-center text-caption text-ink-mute">{pxPerSec}px/秒</span>
          <Button size="sm" variant="quiet" aria-label="放大时间轴" icon={<ZoomIn className="h-3 w-3" />} onClick={() => setPxPerSec(clamp(pxPerSec * 1.4, MIN_PX, MAX_PX))} />
        </div>
      </header>

      <div className="overflow-x-auto p-3">
        <div className="relative" style={{ width }}>
          <div className="flex h-4 items-end">
            {Array.from({ length: Math.floor(total) + 1 }).map((_, s) => (
              <span key={s} className="absolute bottom-0 flex items-end gap-1" style={{ left: s * pxPerSec }}>
                <span className="mono text-micro leading-none text-ink-mute">{s}s</span>
                <span className="w-px flex-none bg-rule" style={{ height: s % 5 === 0 ? 10 : 5 }} />
              </span>
            ))}
          </div>
          <div className="mt-1 flex gap-px">
            {shots.map((s, i) => {
              const start = shots.slice(0, i).reduce((a, x) => a + (x.durationSec || 0), 0);
              return (
                <TimelineBlock
                  key={s.id}
                  ctx={ctx}
                  shot={s}
                  tc={timecode(start)}
                  width={Math.max(MIN_PX * 3, (s.durationSec || 1) * pxPerSec)}
                  onPlay={onPlay}
                />
              );
            })}
          </div>
        </div>
      </div>
      {shots.every((s) => !s.videoMediaIds.length) && (
        <div className="px-3 pb-3">
          <Empty title="还没有任何成片" hint="在镜头详情里出首帧、再点出片；出好的段会真的挂在这里，点一下就能播。" />
        </div>
      )}
    </section>
  );
}

function TimelineBlock({
  ctx,
  shot,
  tc,
  width,
  onPlay,
}: {
  ctx: DirectorCtx;
  shot: Shot;
  tc: string;
  width: number;
  onPlay: (mediaId: string, title: string) => void;
}) {
  const videoId = shot.videoMediaIds[0];
  const thumbId = frameMediaId(shot, "start");
  const media: Media | undefined = (videoId ? ctx.mediaById.get(videoId) : undefined) ?? (thumbId ? ctx.mediaById.get(thumbId) : undefined);
  const src = useMediaSrc(media);
  const playable = !!videoId;

  return (
    <button
      type="button"
      onClick={() => playable && onPlay(videoId, `镜 ${cardLabel(shot)}`)}
      title={playable ? "点击播放这一段" : shot.action || "还没有画面"}
      style={{ width }}
      className="group relative h-16 flex-none overflow-hidden border-y border-r border-white/10 bg-white/[0.045] text-left"
    >
      {src ? (
        <img src={src} alt="" className="h-full w-full object-cover opacity-80 transition-opacity group-hover:opacity-100" loading="lazy" />
      ) : (
        <span className="absolute inset-0 bg-[repeating-linear-gradient(135deg,transparent_0_7px,rgb(255_255_255/0.04)_7px_14px)]" aria-hidden />
      )}
      <span className="mono absolute left-1 top-0.5 text-micro leading-tight text-white/85 [text-shadow:0_1px_2px_rgba(0,0,0,.9)]">{cardLabel(shot)}</span>
      <span className="mono absolute inset-x-1 bottom-0.5 truncate text-micro leading-tight text-white/75 [text-shadow:0_1px_2px_rgba(0,0,0,.9)]">
        {tc}
        {playable ? "" : " ·"}
      </span>
      {playable && (
        <span className="absolute inset-0 grid place-items-center bg-black/25 opacity-0 transition-opacity group-hover:opacity-100">
          <Play className="h-5 w-5 text-white" fill="currentColor" aria-hidden />
        </span>
      )}
    </button>
  );
}
