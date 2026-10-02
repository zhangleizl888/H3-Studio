/**
 * 角色的音色：参考音频 + 那句话的原文 + 要念的新词，再加这一类自己的生成选择。
 *
 * 为什么要 ref_text 这一栏而不是只传音频：声音克隆的解码条件就是「这段音频 + 它真实
 * 说过的话」。本机实测过缺省条件的后果 —— 拿环境音配一句编出来的台词，十几个字被拉成
 * 两分四十三秒的连续发声。所以这里没有原文就不让点生成，而不是给个默认值糊过去。
 */

import { Mic, RefreshCw, Trash2, Upload } from "lucide-react";
import { Badge, Input, Select, Textarea } from "../../../components/ui";
import { GenPresetPicker } from "../../../components/GenPresetPicker";
import type { Character, Media, Project, VoiceProfile } from "../../../lib/types";
import type { GenHandle } from "../../../lib/useGenerate";
import { cn } from "../../../lib/utils";
import { CardAction, InlineEdit, useMediaSrc } from "./common";

const LANGUAGES = ["Auto", "中文", "English", "日本語"];

interface Props {
  project: Project;
  char: Character;
  mediaById: Map<string, Media>;
  handle?: GenHandle;
  onPatch: (patch: Partial<VoiceProfile>) => void;
  onUploadAudio: (file: File) => void;
  onGenerate: () => void;
}

export function VoicePanel({ project, char, mediaById, handle, onPatch, onUploadAudio, onGenerate }: Props) {
  const voice: VoiceProfile = char.voice ?? { refAudioIds: [], sampleMediaIds: [] };
  const running = !!handle && (handle.state === "queued" || handle.state === "dispatching" || handle.state === "running");
  const ready = voice.refAudioIds.length > 0 && !!voice.refText?.trim();

  return (
    <div className="space-y-2 rounded-ctl border border-hairline bg-inset p-2">
      <div className="flex flex-wrap items-center gap-2">
        <span className="label-mono flex items-center gap-1.5 text-ink-dim">
          <Mic className="h-3 w-3" />
          音色 · Voice
        </span>
        {voice.status && voice.status !== "pending" && (
          <Badge tone={voice.status === "failed" ? "bad" : voice.status === "completed" ? "good" : "neutral"}>
            {voice.status === "generating" ? "生成中" : voice.status === "completed" ? "已有样音" : "失败"}
          </Badge>
        )}
      </div>

      <div className="space-y-1.5">
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="label-mono w-[86px] flex-none text-ink-mute">参考音频</span>
          <label className={cn("flex items-center gap-1 rounded-ctl border border-hairline bg-sheen px-2 py-1 text-caption text-ink-dim hover:text-ink")}>
            <input type="file" accept="audio/*" className="sr-only" disabled={running} onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ""; if (f) onUploadAudio(f); }} />
            <Upload className="h-3 w-3" />
            选一段真人语音
          </label>
          <span className="text-caption text-ink-mute">几秒就够，必须是干净人声（环境音、音乐、带 BGM 的片段都不行）</span>
        </div>
        {voice.refAudioIds.length > 0 && (
          <ul className="flex flex-wrap gap-1.5">
            {voice.refAudioIds.map((id) => (
              <AudioChip key={id} media={mediaById.get(id)} label="参考" onRemove={() => onPatch({ refAudioIds: voice.refAudioIds.filter((x) => x !== id) })} />
            ))}
          </ul>
        )}

        <div className="flex flex-wrap items-center gap-1.5">
          <span className="label-mono w-[86px] flex-none text-ink-mute">原话文本</span>
          <Input
            className="min-w-[220px] flex-1"
            value={voice.refText ?? ""}
            onChange={(e) => onPatch({ refText: e.target.value })}
            placeholder="参考音频里那句话的真实内容（必填）"
            aria-label="参考音频的原话文本"
          />
        </div>

        <div className="flex flex-wrap items-start gap-1.5">
          <span className="label-mono w-[86px] flex-none pt-1.5 text-ink-mute">试念台词</span>
          <Textarea
            className="min-w-[220px] flex-1"
            rows={2}
            value={voice.testText ?? ""}
            onChange={(e) => onPatch({ testText: e.target.value })}
            placeholder={`留空就用「我是${char.name}。」试音`}
            aria-label="要念的台词"
          />
          <Select className="w-[110px]" value={voice.language ?? "Auto"} onChange={(e) => onPatch({ language: e.target.value })} aria-label="语种">
            {LANGUAGES.map((l) => (
              <option key={l} value={l}>
                {l}
              </option>
            ))}
          </Select>
        </div>

        <div className="flex flex-wrap items-center gap-1.5">
          <span className="label-mono w-[86px] flex-none text-ink-mute">音色描述</span>
          <InlineEdit
            value={voice.timbre}
            ariaLabel="音色描述"
            placeholder="沙哑低沉 / 少年音 / 语速偏快（记档用，不进模型）"
            onCommit={(v) => onPatch({ timbre: v })}
            className="min-w-0 flex-1 text-note"
            inputClassName="text-note"
          />
        </div>
      </div>

      <GenPresetPicker
        project={project}
        kind="audio"
        value={voice.preset}
        onChange={(p) => onPatch({ preset: p })}
        compact
      />

      <CardAction
        icon={running ? <RefreshCw className="h-3.5 w-3.5 animate-spin" /> : <Mic className="h-3.5 w-3.5" />}
        className="w-full"
        disabled={running || !ready}
        onClick={onGenerate}
        title={ready ? "用工作流库里 audio 那一条出音色样音" : "要先生成音色：得有一段真人参考音频，并填上它说的原话"}
      >
        {running ? "生成中…" : voice.sampleMediaIds.length ? "重新出音色" : "生成音色"}
      </CardAction>
      {!ready && voice.refAudioIds.length > 0 && <p className="text-caption leading-snug text-ink-mute">还差「原话文本」—— 音频里到底说了什么，模型要这句话才对得上。</p>}

      {voice.sampleMediaIds.length > 0 && (
        <ul className="space-y-1">
          {voice.sampleMediaIds.map((id) => (
            <AudioChip key={id} media={mediaById.get(id)} label="样音" onRemove={() => onPatch({ sampleMediaIds: voice.sampleMediaIds.filter((x) => x !== id) })} />
          ))}
        </ul>
      )}
    </div>
  );
}

function AudioChip({ media, label, onRemove }: { media: Media | undefined; label: string; onRemove: () => void }) {
  const src = useMediaSrc(media);
  if (!media) {
    return (
      <li className="flex items-center gap-1.5 rounded-ctl border border-hairline bg-sheen px-2 py-1 text-caption text-ink-mute">
        {label}：这条素材不在本地索引里（可能在别的浏览器/机器上）
        <button type="button" onClick={onRemove} className="text-ink-mute hover:text-state-fail" aria-label="移除">
          <Trash2 className="h-3 w-3" />
        </button>
      </li>
    );
  }
  return (
    <li className="flex min-w-[240px] flex-1 items-center gap-2 rounded-ctl border border-hairline bg-sheen px-2 py-1">
      <span className="label-mono flex-none text-ink-mute">{label}</span>
      {src ? <audio src={src} controls className="h-7 min-w-0 flex-1" /> : <span className="text-caption text-ink-mute">读取中…</span>}
      <button type="button" onClick={onRemove} className="flex-none text-ink-mute hover:text-state-fail" aria-label={`移除${label}`}>
        <Trash2 className="h-3 w-3" />
      </button>
    </li>
  );
}
