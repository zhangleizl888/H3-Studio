/**
 * 服装变体弹窗：一个角色的多套造型。
 *
 * 变体出图走 variationRequest —— 参考图是该角色的定妆照，提示词只替换 Clothing 段，
 * 所以定妆照得先存在（而且是服务端能读到的那张），否则一致性就只剩文字约束。
 */

import { useState } from "react";
import { CircleAlert, Layers, Plus, RefreshCw, X } from "lucide-react";
import { Badge, Button, Input, Spinner, StateGlyph, Textarea } from "../../../components/ui";
import { buildCharacterPrompt, buildVariationPrompt } from "../../../lib/prompts";
import { serverMediaIds } from "../../../lib/generate";
import type { Character, Media, Project, Variation } from "../../../lib/types";
import { genKey, type GenHandle } from "../../../lib/useGenerate";
import { uid } from "../../../lib/utils";
import { CardAction, DoneBadge, GenBar, InlineEdit, MediaImage, Sheet, UploadButton, assetStateOf } from "./common";

export interface WardrobeModalProps {
  project: Project;
  char: Character;
  mediaById: Map<string, Media>;
  handles: Record<string, GenHandle>;
  onClose: () => void;
  onPatch: (patch: Partial<Character>) => void;
  onGenerateVariation: (variationId: string) => void;
  onUploadVariation: (variationId: string, file: File) => void;
  onPreview: (media: Media, title: string) => void;
}

export function WardrobeModal({ project, char, mediaById, handles, onClose, onPatch, onGenerateVariation, onUploadVariation, onPreview }: WardrobeModalProps) {
  const [name, setName] = useState("");
  const [desc, setDesc] = useState("");
  const [armed, setArmed] = useState<string | null>(null);

  const refMedia = char.refMediaIds[0] ? mediaById.get(char.refMediaIds[0]) : undefined;
  const baseRunning = !!handles[genKey({ kind: "character", characterId: char.id })];
  /** 定妆照是本机上传（idb:）时后端读不到，变体就只能按文字出图 */
  const refUsable = serverMediaIds(char.refMediaIds).length > 0;

  const variations = char.variations ?? [];

  function addVariation() {
    const n = name.trim();
    if (!n) return;
    // visualPrompt 故意留空：buildVariationPrompt 会拿角色定妆提示词换掉 Clothing 段，
    // 在这里预先写死一段，后面改 desc 就不会再影响出图了。
    onPatch({
      variations: [...variations, { id: uid("v"), name: n, desc: desc.trim(), refMediaIds: [], status: "pending" }],
    });
    setName("");
    setDesc("");
  }

  function patchVariation(id: string, patch: Partial<Variation>) {
    onPatch({ variations: variations.map((v) => (v.id === id ? { ...v, ...patch } : v)) });
  }

  function removeVariation(id: string) {
    onPatch({ variations: variations.filter((v) => v.id !== id) });
    setArmed(null);
  }

  return (
    <Sheet open onClose={onClose} width={920} eyebrow={`Wardrobe · ${variations.length} variations`} title={`${char.name || "未命名角色"} · 服装变体`}>
      <div className="grid gap-6 md:grid-cols-2">
        {/* 定妆照 */}
        <section className="space-y-2.5">
          <h4 className="label-mono flex items-center gap-1.5">
            <Layers className="h-3 w-3" /> 基准形象
          </h4>
          <div className="rounded-panel border border-hairline bg-void/40 p-3">
            <MediaImage
              media={refMedia}
              seedText={char.id}
              alt={`${char.name} 定妆照`}
              aspect="16/9"
              busy={baseRunning}
              badge={refMedia ? <DoneBadge label="已有定妆照" /> : undefined}
              emptyLabel="还没有定妆照"
              onClick={refMedia ? () => onPreview(refMedia, `${char.name} · 定妆照`) : undefined}
            />
            <p className="mt-2.5 whitespace-pre-wrap break-words text-note leading-relaxed text-ink-mute">{buildCharacterPrompt(char, project.config)}</p>
          </div>
          {!refMedia ? (
            <p className="flex items-start gap-1.5 text-note leading-snug text-state-fail">
              <CircleAlert className="mt-0.5 h-3.5 w-3.5 flex-none" />
              还没有定妆照：变体会退化成纯文字出图，脸和发型很容易和角色本体不一致。
            </p>
          ) : !refUsable ? (
            <p className="flex items-start gap-1.5 text-note leading-snug text-mach-rh">
              <CircleAlert className="mt-0.5 h-3.5 w-3.5 flex-none" />
              这张定妆照是本机上传的，服务端还读不到它当参考图；变体会按文字提示词出图。
            </p>
          ) : (
            <p className="text-note leading-snug text-ink-mute">变体出图会把这张定妆照当参考图，只替换服装段。</p>
          )}
        </section>

        {/* 变体表 */}
        <section className="space-y-3">
          <h4 className="label-mono">变体 / 造型</h4>
          {variations.length === 0 && <p className="text-note text-ink-mute">还没有变体。下面填名字和造型描述，加一套。</p>}

          <ul className="space-y-2.5">
            {variations.map((v) => {
              const handle = handles[genKey({ kind: "variation", characterId: char.id, variationId: v.id })];
              const media = v.refMediaIds[0] ? mediaById.get(v.refMediaIds[0]) : undefined;
              const running = !!handle && (handle.state === "queued" || handle.state === "dispatching" || handle.state === "running");
              const state = assetStateOf(v.status, !!media, handle);
              return (
                <li key={v.id} className="flex gap-3 rounded-panel border border-hairline bg-sheen p-2.5">
                  <div className="w-20 flex-none">
                    <MediaImage
                      media={media}
                      seedText={v.id}
                      alt={`${char.name} · ${v.name}`}
                      aspect="3/4"
                      busy={running}
                      badge={state === "succeeded" ? <DoneBadge label="已出图" /> : undefined}
                      onClick={media ? () => onPreview(media, `${char.name} · ${v.name}`) : undefined}
                    />
                  </div>
                  <div className="min-w-0 flex-1 space-y-1.5">
                    <div className="flex items-start justify-between gap-2">
                      <InlineEdit
                        value={v.name}
                        ariaLabel="变体名"
                        placeholder="未命名变体"
                        onCommit={(x) => patchVariation(v.id, { name: x })}
                        className="min-w-0 flex-1 text-body font-semibold"
                      />
                      <StateGlyph state={state} />
                      {armed === v.id ? (
                        <button
                          type="button"
                          onClick={() => removeVariation(v.id)}
                          className="flex-none rounded-ctl border border-state-fail/50 bg-state-fail/12 px-1.5 py-0.5 text-caption text-state-fail"
                        >
                          确认删除
                        </button>
                      ) : (
                        <button
                          type="button"
                          onClick={() => setArmed(v.id)}
                          aria-label={`删除变体 ${v.name}`}
                          className="flex-none rounded-ctl p-1 text-ink-mute transition-colors hover:bg-hairline hover:text-state-fail"
                        >
                          <X className="h-3.5 w-3.5" />
                        </button>
                      )}
                    </div>
                    <InlineEdit
                      value={v.desc}
                      ariaLabel="造型描述"
                      placeholder="这套造型写进 Clothing 段，例如「深青绣云鹤纹宫装」"
                      onCommit={(x) => patchVariation(v.id, { desc: x })}
                      className="block text-note leading-snug text-ink-mute"
                    />
                    <p className="line-clamp-2 text-caption leading-snug text-ink-mute" title={buildVariationPrompt(char, v, project.config)}>
                      {buildVariationPrompt(char, v, project.config)}
                    </p>
                    {handle && <GenBar handle={handle} />}
                    <div className="flex flex-wrap items-center gap-1.5">
                      <CardAction
                        tone={v.refMediaIds.length ? "quiet" : "chrome"}
                        icon={running ? <Spinner className="h-3 w-3" /> : <RefreshCw className="h-3 w-3" />}
                        className="px-2 py-1 text-caption"
                        disabled={running}
                        onClick={() => onGenerateVariation(v.id)}
                      >
                        {running ? "生成中" : v.refMediaIds.length ? "重新出图" : "出图"}
                      </CardAction>
                      <UploadButton
                        label="上传参考图"
                        className="px-2 py-1 text-caption"
                        disabled={running}
                        onFile={(file) => onUploadVariation(v.id, file)}
                        title="上传一张造型图，直接作为该变体的产物"
                      />
                      <span className="mono ml-auto text-micro text-ink-mute">{v.refMediaIds.length ? `${v.refMediaIds.length} 图` : "无产物"}</span>
                    </div>
                  </div>
                </li>
              );
            })}
          </ul>

          <div className="space-y-2 rounded-panel border border-dashed border-hairline bg-sheen p-3">
            <div className="flex items-center gap-2">
              <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="变体名，例如「夜行宫装」" className="min-w-0 flex-1" />
              <Badge>{variations.length}</Badge>
            </div>
            <Textarea
              rows={2}
              value={desc}
              onChange={(e) => setDesc(e.target.value)}
              placeholder="这套造型的画面描述（会替换角色提示词里的 Clothing 段）"
              className="w-full"
              onKeyDown={(e) => {
                if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) addVariation();
              }}
            />
            <Button variant="primary" size="sm" icon={<Plus className="h-3 w-3" />} disabled={!name.trim()} onClick={addVariation} className="w-full">
              添加变体
            </Button>
            <p className="text-caption leading-snug text-ink-mute">
              名字与描述都可事后点开改；出图前记得先有定妆照。
            </p>
          </div>
        </section>
      </div>
    </Sheet>
  );
}
