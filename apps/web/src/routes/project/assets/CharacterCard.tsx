/**
 * 角色定妆卡：参考图 + 基本资料 + 服装变体入口 + 提示词 + 出图/入库/删除。
 *
 * 卡片本身不碰数据层，所有写操作由 Assets.tsx 注入 —— 这样变体弹窗和卡片
 * 改的是同一份 project，不会出现两边各自攒一份 characters 数组。
 */

import { useState } from "react";
import { FolderPlus, Layers, RefreshCw, Sparkles, Trash2, Users } from "lucide-react";
import { Badge, StateGlyph } from "../../../components/ui";
import { buildCharacterPrompt } from "../../../lib/prompts";
import type { Character, Media, Project } from "../../../lib/types";
import type { GenHandle } from "../../../lib/useGenerate";
import { CardAction, DoneBadge, GenBar, InlineEdit, MediaImage, UploadButton, assetStateOf } from "./common";
import { PromptEditor } from "./PromptEditor";

type Traits = NonNullable<Character["traits"]>;
/** traits.age 与卡片上的「年龄段」是同一件事，只在卡片上编辑 */
const TRAIT_FIELDS: { key: Exclude<keyof Traits, "age">; label: string; placeholder: string }[] = [
  { key: "build", label: "体型", placeholder: "纤细挺拔 / 壮实" },
  { key: "hair", label: "发型发色", placeholder: "乌黑长发高挽，碎发垂鬓" },
  { key: "costume", label: "服装", placeholder: "深青色绣云鹤纹宫装" },
  { key: "palette", label: "配色", placeholder: "青金 + 暗红" },
  { key: "signature", label: "标志细节", placeholder: "丹凤眼、右颊痣、随身玉佩" },
];

export interface CharacterCardProps {
  project: Project;
  char: Character;
  mediaById: Map<string, Media>;
  handle?: GenHandle;
  onGenerate: () => void;
  onUpload: (file: File) => void;
  onPatch: (patch: Partial<Character>) => void;
  onSavePrompts: (patch: { visualPrompt: string; negativePrompt: string }) => void;
  onOpenWardrobe: () => void;
  onAddToLibrary: () => void;
  onReplaceFromLibrary: () => void;
  onDelete: () => void;
  onPreview: (media: Media, title: string) => void;
}

export function CharacterCard({ project, char, mediaById, handle, onGenerate, onUpload, onPatch, onSavePrompts, onOpenWardrobe, onAddToLibrary, onReplaceFromLibrary, onDelete, onPreview }: CharacterCardProps) {
  const [more, setMore] = useState(false);
  const refId = char.refMediaIds[0];
  const refMedia = refId ? mediaById.get(refId) : undefined;
  const running = !!handle && (handle.state === "queued" || handle.state === "dispatching" || handle.state === "running");
  const state = assetStateOf(char.status, !!refMedia, handle);
  const variationCount = char.variations.length;
  const filled = TRAIT_FIELDS.filter((f) => char.traits?.[f.key]?.trim()).length + (char.desc?.trim() ? 1 : 0);

  return (
    <article className="glass flex flex-col gap-3 rounded-panel p-3 transition-shadow hover:ring-1 hover:ring-chrome/30">
      <div className="flex gap-3">
        <div className="w-44 flex-none">
          <MediaImage
            media={refMedia}
            seedText={char.id}
            alt={`${char.name} 定妆照`}
            aspect="4/3"
            busy={running}
            badge={state === "succeeded" ? <DoneBadge label="已有定妆照" /> : undefined}
            emptyLabel={state === "failed" ? <span className="text-state-fail">生成失败</span> : refMedia ? undefined : "还没有定妆照"}
            onClick={refMedia ? () => onPreview(refMedia, `${char.name} · 定妆照`) : undefined}
          />
        </div>

        <div className="flex min-w-0 flex-1 flex-col gap-2">
          <div className="space-y-1.5">
            <div className="flex items-center gap-2">
              <StateGlyph state={state} />
              <InlineEdit
                value={char.name}
                ariaLabel="角色名"
                placeholder="未命名角色"
                onCommit={(v) => onPatch({ name: v })}
                className="min-w-0 flex-1 text-subtitle font-semibold tracking-tight"
              />
            </div>
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="rounded-full border border-hairline bg-chrome/10 px-2 py-[1px] text-caption text-ink-dim">
                <InlineEdit value={char.gender || "未设定"} ariaLabel="性别" onCommit={(v) => onPatch({ gender: v })} className="text-caption" inputClassName="w-16 text-caption" />
              </span>
              <InlineEdit
                value={char.age}
                ariaLabel="年龄段"
                placeholder="未填年龄段"
                onCommit={(v) => onPatch({ age: v })}
                className="text-caption text-ink-mute hover:text-ink-dim"
                inputClassName="w-28 text-caption"
              />
              {variationCount > 0 && (
                <Badge className="flex-none">
                  <Layers className="mr-1 inline h-2.5 w-2.5" />
                  <span className="mono">+{variationCount}</span> 变体
                </Badge>
              )}
              <span className="mono ml-auto flex-none text-micro text-ink-mute" title="同一角色固定种子，配合参考图复用才有稳定外形">
                seed {char.seed}
              </span>
            </div>
            <InlineEdit
              value={char.personality}
              ariaLabel="性格"
              placeholder={char.desc?.trim() || "补一句性格与说话方式（进演员表，不进画面）"}
              onCommit={(v) => onPatch({ personality: v })}
              className="block text-note leading-snug text-ink-mute"
            />
          </div>

          <div className="mt-auto space-y-1.5">
            <CardAction icon={<Users className="h-3.5 w-3.5" />} className="w-full" onClick={onOpenWardrobe}>
              服装变体
            </CardAction>
            <div className="flex gap-1.5">
              <CardAction icon={<RefreshCw className="h-3.5 w-3.5" />} className="flex-1" disabled={running} onClick={onGenerate}>
                {running ? "生成中" : "重新生成"}
              </CardAction>
              <UploadButton className="flex-1" disabled={running} onFile={onUpload} title="上传一张本地参考图，直接作为该角色的定妆照" />
            </div>
            <CardAction icon={<FolderPlus className="h-3.5 w-3.5" />} className="w-full" disabled={running} onClick={onReplaceFromLibrary}>
              从资产库替换
            </CardAction>
          </div>
        </div>
      </div>

      {/* 结构化外形：剧本拆解回填的 traits，也是「按资料现拼」那段的原料 */}
      <div className="space-y-1.5">
        <button
          type="button"
          aria-expanded={more}
          onClick={() => setMore((m) => !m)}
          className="label-mono flex w-full items-center justify-between rounded-ctl px-1 py-0.5 transition-colors hover:bg-sheen"
        >
          <span>外形资料 · 拼提示词用</span>
          <span className="mono">{more ? "收起" : filled ? `${filled}/${TRAIT_FIELDS.length + 1}` : "待补"}</span>
        </button>
        {more && (
          <div className="space-y-1.5 rounded-ctl border border-hairline bg-void/40 p-2">
            <TraitRow label="外形一句话" value={char.desc} placeholder="身形、气质、明显特征" onCommit={(v) => onPatch({ desc: v })} />
            {TRAIT_FIELDS.map((f) => (
              <TraitRow key={f.key} label={f.label} value={char.traits?.[f.key]} placeholder={f.placeholder} onCommit={(v) => onPatch({ traits: { ...char.traits, [f.key]: v } })} />
            ))}
          </div>
        )}
      </div>

      {handle && <GenBar handle={handle} />}

      <PromptEditor
        label="角色提示词"
        prompt={char.visualPrompt}
        negative={char.negativePrompt}
        fallback={buildCharacterPrompt(char, project.config)}
        placeholder="Core Identity / Facial Features / Clothing… 留空则按上面的资料现拼"
        onSave={onSavePrompts}
        disabled={running}
      />

      <div className="space-y-1.5">
        <CardAction
          tone="chrome"
          icon={<Sparkles className="h-3.5 w-3.5" />}
          className="w-full py-2"
          disabled={running}
          onClick={onGenerate}
          title={`按项目比例 ${project.config.aspectRatio} 出图；种子固定为 ${char.seed}，负向提示词在上面的编辑区改`}
        >
          {running ? "生成中…" : refMedia ? "重新生成图片" : "生成角色图片"}
        </CardAction>
        <CardAction icon={<FolderPlus className="h-3.5 w-3.5" />} className="w-full" disabled={running} onClick={onAddToLibrary}>
          加入资产库
        </CardAction>
        <CardAction tone="danger" icon={<Trash2 className="h-3.5 w-3.5" />} className="w-full" disabled={running} onClick={onDelete} title="删除角色，并从所有镜头的出场名单里摘掉">
          删除角色
        </CardAction>
      </div>
    </article>
  );
}

function TraitRow({ label, value, placeholder, onCommit }: { label: string; value: string | undefined; placeholder: string; onCommit: (v: string) => void }) {
  return (
    <div className="flex items-baseline gap-2">
      <span className="label w-20 flex-none">{label}</span>
      <InlineEdit value={value} ariaLabel={label} placeholder={placeholder} onCommit={onCommit} className="min-w-0 flex-1 text-note" inputClassName="text-note" />
    </div>
  );
}
