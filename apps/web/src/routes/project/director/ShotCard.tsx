import { Video } from "lucide-react";
import type { Shot } from "../../../lib/types";
import { movementLabel } from "../../../lib/prompts";
import { cn } from "../../../lib/utils";
import { StateGlyph, type StateKey } from "../../../components/ui";
import { cardLabel, frameMediaId, mediaOf, MediaImage, type DirectorCtx } from "./common";

const STATE_BY_SHOT: Record<Shot["state"], StateKey> = {
  idle: "idle",
  queued: "queued",
  generating: "running",
  completed: "succeeded",
  failed: "failed",
};

/**
 * 网格里的一张镜头卡：编号 + 运镜提示 + 首帧真图 + VIDEO 徽标 + 画面摘要。
 * 首帧没出图时不塞假缩略图，直接给 MediaFrame 的程序化占位 —— 一眼能分清「出过图」和「没出图」。
 */
export function ShotCard({
  ctx,
  shot,
  active,
  onClick,
}: {
  ctx: DirectorCtx;
  shot: Shot;
  active: boolean;
  onClick: () => void;
}) {
  const startId = frameMediaId(shot, "start");
  const thumb = mediaOf(ctx, startId);
  const videos = shot.videoMediaIds.length;
  const movement = shot.cameraMovement ? movementLabel(shot.cameraMovement) : "未设运镜";

  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={cn(
        "group flex flex-col overflow-hidden rounded-panel border text-left transition-colors",
        active
          ? "border-chrome/55 bg-sheen"
          : "border-hairline bg-sheen hover:border-chrome/30 hover:bg-sheen",
      )}
    >
      <div className="flex items-start gap-2 border-b border-hairline bg-inset px-2.5 py-1.5">
        <span className="label-mono flex-none pt-[2px] text-chrome/90">SHOT {cardLabel(shot)}</span>
        <span className="line-clamp-3 flex-1 rounded-full bg-sheen px-2 py-[3px] text-caption leading-snug text-ink-dim">
          {movement}
        </span>
        <StateGlyph state={STATE_BY_SHOT[shot.state]} className="mt-[3px]" />
      </div>

      <div className="relative">
        <MediaImage media={thumb} seedText={shot.id} aspect={ctx.aspect} className="rounded-none border-0" alt={`镜 ${cardLabel(shot)} 首帧`} />
        {videos > 0 && (
          <span className="absolute right-1.5 top-1.5 inline-flex items-center gap-1 rounded-full bg-state-ok px-1.5 py-[2px] text-micro font-bold uppercase leading-tight text-void shadow-lg">
            <Video className="h-2.5 w-2.5" />
            VIDEO{videos > 1 ? ` ×${videos}` : ""}
          </span>
        )}
      </div>

      <p className="line-clamp-3 px-2.5 py-2 text-note leading-snug text-ink-dim">
        {shot.action?.trim() || <span className="text-ink-mute">（还没有画面描述，点开抽屉补）</span>}
      </p>
    </button>
  );
}
