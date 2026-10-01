import { useState } from "react";
import { Copy, Sparkles, Upload, ZoomIn } from "lucide-react";
import { Badge, Button, Progress, StateGlyph, type StateKey } from "../../../components/ui";
import { VersionGroup } from "../../../components/VersionHistory";
import { keyframeRequest } from "../../../lib/generate";
import type { GenTarget } from "../../../lib/generate";
import { genKey } from "../../../lib/useGenerate";
import { buildKeyframePrompt } from "../../../lib/prompts";
import type { AssetState, JobState, Shot } from "../../../lib/types";
import { cardLabel, CommitText, frameMediaId, frameOf, mediaOf, MediaImage, pickImage, withFrame } from "./common";
import type { DirectorCtx, FrameType } from "./common";

const KF_STATE: Record<AssetState, StateKey> = {
  pending: "idle",
  generating: "running",
  completed: "succeeded",
  failed: "failed",
};

const DONE: JobState[] = ["succeeded", "failed", "canceled"];

/**
 * 起始帧 / 结束帧一块：提示词、生成、上传、预览、进度。
 *
 * 进度条只在本面板自己按过「生成」之后才挂出来 —— 契约里 genKey 对同一镜的
 * start/end 算出的是同一个 key（refFor 只带 shotId），拿不到就没法说清这条任务是哪一帧的。
 */
export function KeyframePanel({
  ctx,
  shot,
  type,
  prevShot,
  claim,
  onClaim,
}: {
  ctx: DirectorCtx;
  shot: Shot;
  type: FrameType;
  prevShot: Shot | null;
  claim: FrameType | null;
  onClaim: (t: FrameType | null) => void;
}) {
  const [uploading, setUploading] = useState(false);
  const [busy, setBusy] = useState(false);
  const kf = frameOf(shot, type);
  const mediaId = frameMediaId(shot, type);
  const media = mediaOf(ctx, mediaId);
  const label = type === "start" ? "起始帧" : "结束帧";
  const target: GenTarget = { kind: "keyframe", shotId: shot.id, frameType: type };
  const handle = ctx.handles[genKey(target)];
  const running = !!handle && !DONE.includes(handle.state);
  const mine = claim === type;

  const scene = ctx.project.data.scenes.find((s) => s.id === shot.sceneId);
  const chars = ctx.project.data.characters.filter((c) => shot.characterIds.includes(c.id));
  const base = shot.action?.trim() || `${scene?.name ?? ""}${scene?.time ? `（${scene.time}）` : ""}`;
  const autoPrompt = buildKeyframePrompt({
    base,
    visualStyle: ctx.project.config.visualStyle,
    cameraMovement: shot.cameraMovement,
    frameType: type,
    withConsistency: chars.length > 0,
  });

  async function generate() {
    setBusy(true);
    onClaim(type);
    const res = await ctx.run(target, keyframeRequest(ctx.project, shot, type));
    if (res.error) ctx.notify(`${label}提交失败：${res.error}`, "bad");
    setBusy(false);
  }

  async function upload() {
    const file = await pickImage();
    if (!file) return;
    setUploading(true);
    try {
      const row = await ctx.api.media.put(file, type === "start" ? "keyframe_start" : "keyframe_end", shot.id, ctx.project.id);
      ctx.patchShot(shot.id, withFrame(shot, type, { mediaId: row.id, status: "completed" }));
      ctx.refetchMedia();
      ctx.notify(`${label}已用本地文件填入`, "ok");
    } catch (e) {
      ctx.notify(`${label}上传失败：${e instanceof Error ? e.message : String(e)}`, "bad");
    } finally {
      setUploading(false);
    }
  }

  const tailOfPrev = prevShot ? frameMediaId(prevShot, "end") : null;

  return (
    <div className="space-y-2 rounded-panel border border-rule-soft bg-sheen p-2.5">
      <div className="flex items-center gap-2">
        <span className="label-mono">{label}</span>
        <StateGlyph state={KF_STATE[kf?.status ?? "pending"]} />
        {mediaId && (
          <Button
            size="sm"
            variant="ghost"
            className="ml-auto"
            icon={<ZoomIn className="h-3 w-3" />}
            onClick={() => ctx.onPreview(mediaId, `${cardLabel(shot)} · ${label}`)}
            title="放大预览"
          >
            预览
          </Button>
        )}
      </div>

      <MediaImage media={media} seedText={`${shot.id}-${type}`} aspect={ctx.aspect} alt={`${cardLabel(shot)} ${label}`} />

      {/* 这一帧的旧版本：重新生成以前只是把上一张顶掉，现在能翻回来、能删进回收站 */}
      <VersionGroup
        projectId={ctx.project.id}
        role={`keyframe_${type}`}
        refId={`${shot.id}:${type}`}
        aspect={ctx.aspect}
        label={`${cardLabel(shot)} ${label}`}
      />

      <CommitText
        ariaLabel={`${label}提示词`}
        value={kf?.visualPrompt ?? ""}
        rows={3}
        mono
        placeholder={`留空则按「画面动作 + 视觉风格 + 运镜构图」自动拼装：\n\n${autoPrompt.slice(0, 150)}…`}
        onCommit={(v) => ctx.patchShot(shot.id, withFrame(shot, type, { visualPrompt: v }))}
      />

      <div className="flex gap-1.5">
        <Button size="sm" variant="primary" className="flex-1" loading={busy || (mine && running)} icon={<Sparkles className="h-3 w-3" />} onClick={() => void generate()}>
          {mediaId ? "重新生成" : "生成"}
        </Button>
        <Button size="sm" className="flex-1" loading={uploading} icon={<Upload className="h-3 w-3" />} onClick={() => void upload()}>
          上传
        </Button>
      </div>

      {type === "start" && (
        <Button
          size="sm"
          variant="quiet"
          className="w-full"
          icon={<Copy className="h-3 w-3" />}
          disabled={!tailOfPrev || !!mediaId}
          title={tailOfPrev ? "把上一镜的结束帧接到本镜起始帧，动作才连得上" : "上一镜还没有生成结束帧"}
          onClick={() => {
            if (!tailOfPrev) return;
            ctx.patchShot(shot.id, withFrame(shot, "start", { mediaId: tailOfPrev, status: "completed" }));
            ctx.notify("已把上一镜尾帧接到本镜首帧", "ok");
          }}
        >
          复制上一镜头尾帧到此镜首帧
        </Button>
      )}

      {mine && handle && (
        <div className="space-y-1 rounded-ctl border border-rule-soft bg-sheen p-2">
          <div className="flex items-baseline justify-between gap-2 text-caption">
            <span className="truncate text-ink-dim">{label}</span>
            <Badge tone={handle.state === "failed" ? "bad" : handle.state === "succeeded" ? "good" : "neutral"}>{handle.state}</Badge>
          </div>
          <Progress
            value={handle.progress === null ? undefined : Math.round(handle.progress * 100)}
            max={handle.progress === null ? undefined : 100}
            unavailable={handle.progress === null}
            stage={handle.stage ?? null}
            machine={ctx.machColor(shot.instanceId ?? ctx.project.config.imageInstanceId)}
            stripe={handle.state === "running" || handle.state === "queued"}
          />
          {handle.error && <p className="text-caption leading-snug text-state-fail">{handle.error}</p>}
        </div>
      )}
    </div>
  );
}
