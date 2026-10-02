import { useState } from "react";
import { Play, ZoomIn, ZoomOut } from "lucide-react";
import { Button, Empty } from "../../../components/ui";
import type { Shot } from "../../../lib/types";
import { clamp, isStill } from "../../../lib/utils";
import { cardLabel, frameMediaId, mediaOf, timecode, useMediaSrc } from "./common";
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
          {/* 这一行必须自己 relative：刻度 span 是 absolute 的，父级不设定位就会一路找到
              外层轨道容器，结果整排刻度落到轨道底部，和每格右下角的时码叠在一起 */}
          <div className="relative flex h-4 items-end">
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
  // 封面只认首帧静帧。原来这里优先取成片视频：media.url() 会把整段视频读成 blob 才给出 src，
  // 而 <img> 拿到视频 blob 只能画出一个破图标 —— 时间轴上每一格都在白白下一遍片。
  // 没有首帧的格子如实留占位条纹，读起来才是「哪几段真出了片」。
  const media = mediaOf(ctx, frameMediaId(shot, "start"));
  const src = useMediaSrc(isStill(media) ? media : undefined);
  const playable = !!videoId;

  return (
    <button
      type="button"
      onClick={() => playable && onPlay(videoId, `镜 ${cardLabel(shot)}`)}
      title={playable ? "点击播放这一段" : shot.action || "还没有画面"}
      style={{ width }}
      className="group relative h-16 flex-none overflow-hidden border-y border-r border-hairline bg-sheen text-left"
    >
      {src ? (
        <img src={src} alt="" className="h-full w-full object-cover opacity-80 transition-opacity group-hover:opacity-100" loading="lazy" />
      ) : (
        <span className="tile-empty absolute inset-0" aria-hidden />
      )}
      <span className="mono absolute left-1 top-0.5 rounded-ctl bg-scrim/70 px-1 text-micro leading-tight text-on-scrim">{cardLabel(shot)}</span>
      <span className="mono absolute inset-x-1 bottom-0.5 truncate rounded-ctl bg-scrim/70 px-1 text-micro leading-tight text-on-scrim-dim">
        {tc}
        {playable ? "" : " ·"}
      </span>
      {playable && (
        <span className="absolute inset-0 grid place-items-center bg-scrim/40 opacity-0 transition-opacity group-hover:opacity-100">
          <Play className="h-5 w-5 text-on-scrim" fill="currentColor" aria-hidden />
        </span>
      )}
    </button>
  );
}
