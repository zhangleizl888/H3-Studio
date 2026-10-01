/**
 * 场景概念卡：环境图 + 地点/时段/氛围 + 提示词 + 出图/上传/入库/删除。
 *
 * 时段显示成「日间」「午后」这类两字徽标（timeLabel），拆解回来的「日」「night」这种
 * 原词直接贴在卡片上会显得没加工过。
 */

import { FolderPlus, MapPin, RefreshCw, Trash2 } from "lucide-react";
import { StateGlyph } from "../../../components/ui";
import { buildScenePrompt } from "../../../lib/prompts";
import type { Media, Project, Scene } from "../../../lib/types";
import type { GenHandle } from "../../../lib/useGenerate";
import { CardAction, DoneBadge, GenBar, InlineEdit, MediaImage, UploadButton, assetStateOf, timeLabel } from "./common";
import { PromptEditor } from "./PromptEditor";

export interface SceneCardProps {
  project: Project;
  scene: Scene;
  mediaById: Map<string, Media>;
  handle?: GenHandle;
  onGenerate: () => void;
  onUpload: (file: File) => void;
  onPatch: (patch: Partial<Scene>) => void;
  onSavePrompts: (patch: { visualPrompt: string; negativePrompt: string }) => void;
  onAddToLibrary: () => void;
  onDelete: () => void;
  onPreview: (media: Media, title: string) => void;
}

export function SceneCard({ project, scene, mediaById, handle, onGenerate, onUpload, onPatch, onSavePrompts, onAddToLibrary, onDelete, onPreview }: SceneCardProps) {
  const refId = scene.refMediaIds[0];
  const refMedia = refId ? mediaById.get(refId) : undefined;
  const running = !!handle && (handle.state === "queued" || handle.state === "dispatching" || handle.state === "running");
  const state = assetStateOf(scene.status, !!refMedia, handle);

  return (
    <article className="glass flex flex-col overflow-hidden rounded-panel transition-shadow hover:ring-1 hover:ring-chrome/30">
      <MediaImage
        media={refMedia}
        seedText={scene.id}
        alt={`${scene.name} 场景图`}
        aspect={project.config.aspectRatio === "9:16" ? "4/3" : "16/9"}
        busy={running}
        badge={state === "succeeded" ? <DoneBadge label="已有场景图" /> : undefined}
        emptyLabel={state === "failed" ? <span className="text-state-fail">生成失败</span> : refMedia ? undefined : "还没有场景图"}
        onClick={refMedia ? () => onPreview(refMedia, `${scene.name} · 场景图`) : undefined}
      />

      <div className="flex flex-1 flex-col gap-3 border-t border-hairline p-3">
        <div className="space-y-1">
          <div className="flex items-start justify-between gap-2">
            <div className="flex min-w-0 flex-1 items-center gap-2">
              <StateGlyph state={state} />
              <InlineEdit
                value={scene.name}
                ariaLabel="场景名"
                placeholder="未命名场景"
                onCommit={(v) => onPatch({ name: v })}
                className="min-w-0 flex-1 text-body font-semibold tracking-tight"
              />
            </div>
            <span className="flex-none rounded-full border border-hairline bg-chrome/10 px-2 py-[1px] text-caption text-ink-dim">
              <InlineEdit value={timeLabel(scene.time)} ariaLabel="时段" placeholder="未定时段" onCommit={(v) => onPatch({ time: v })} className="text-caption" inputClassName="w-20 text-caption" />
            </span>
          </div>

          <div className="flex items-start gap-1.5 text-note leading-snug text-ink-mute">
            <MapPin className="mt-[2px] h-3 w-3 flex-none" />
            <InlineEdit
              value={scene.location}
              ariaLabel="地点"
              placeholder="未填地点，例如「内廷·未央宫偏殿」"
              onCommit={(v) => onPatch({ location: v })}
              className="min-w-0 flex-1"
            />
          </div>
          <MetaRow label="氛围" value={scene.atmosphere} placeholder="补一句氛围（写进 Lighting 段）" onCommit={(v) => onPatch({ atmosphere: v })} />
          <MetaRow label="环境描述" value={scene.desc} placeholder="这里有什么：梁柱、香炉、地砖、窗外的光" onCommit={(v) => onPatch({ desc: v })} />
        </div>

        <PromptEditor
          label="场景提示词"
          prompt={scene.visualPrompt}
          negative={scene.negativePrompt}
          fallback={buildScenePrompt(scene, project.config)}
          placeholder="Environment / Lighting / Composition… 留空则按地点、时段、氛围现拼"
          onSave={onSavePrompts}
          maxHeight="max-h-[132px]"
          disabled={running}
        />

        {handle && <GenBar handle={handle} />}

        <div className="mt-auto space-y-1.5">
          <div className="flex gap-1.5">
            <CardAction icon={<RefreshCw className="h-3.5 w-3.5" />} className="flex-1" disabled={running} onClick={onGenerate}>
              {running ? "生成中" : "重新生成"}
            </CardAction>
            <UploadButton className="flex-1" label="上传图片" disabled={running} onFile={onUpload} title="上传一张本地场景图，直接作为该环境的参考图" />
          </div>
          <CardAction icon={<FolderPlus className="h-3.5 w-3.5" />} className="w-full" disabled={running} onClick={onAddToLibrary}>
            加入资产库
          </CardAction>
          <CardAction tone="danger" icon={<Trash2 className="h-3.5 w-3.5" />} className="w-full" disabled={running} onClick={onDelete} title="删除场景，并把引用它的镜头退回「未分配场景」">
            删除场景
          </CardAction>
        </div>
      </div>
    </article>
  );
}

function MetaRow({ label, value, placeholder, onCommit }: { label: string; value: string | undefined; placeholder: string; onCommit: (v: string) => void }) {
  return (
    <div className="flex items-baseline gap-1.5 text-note leading-snug">
      <span className="label w-14 flex-none">{label}</span>
      <InlineEdit value={value} ariaLabel={label} placeholder={placeholder} onCommit={onCommit} className="min-w-0 flex-1 text-ink-mute" />
    </div>
  );
}
