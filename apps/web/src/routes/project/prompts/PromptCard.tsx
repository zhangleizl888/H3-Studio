import { useState } from "react";
import { ChevronDown, ChevronRight, Pencil, RotateCcw } from "lucide-react";
import { Badge, Button, StateGlyph, Textarea, type StateKey } from "../../../components/ui";
import type { AssetState, Project } from "../../../lib/types";
import { h3PromptText } from "../../../lib/prompts";
import { cn } from "../../../lib/utils";
import { rebuildTemplate, type PromptDraft, type PromptRow } from "./utils";

const STATUS: Record<AssetState, { key: StateKey; text: string }> = {
  pending: { key: "idle", text: "待生成" },
  generating: { key: "running", text: "生成中" },
  completed: { key: "succeeded", text: "已有产物" },
  failed: { key: "failed", text: "失败" },
};

/** 每一类提示词都能按当前模板重拼：角色/场景/关键帧走各自 builder，视频段走 H3 三段式 */

export function PromptCard({
  row,
  project,
  onSave,
  depth = 0,
}: {
  row: PromptRow;
  project: Project;
  onSave: (draft: PromptDraft) => Promise<void> | void;
  depth?: number;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<PromptDraft | null>(null);
  const [rebuilt, setRebuilt] = useState(false);
  const [flash, setFlash] = useState<string | null>(null);
  const [open, setOpen] = useState(false);

  const st = row.status ? STATUS[row.status] : null;
  const isVideo = row.target.kind === "video";

  function startEdit() {
    setDraft({
      target: row.target,
      prompt: row.prompt,
      negative: row.negative,
      coreFeatures: row.coreFeatures ?? "",
      h3: row.h3 ?? { integrated: "", soundscape: "", music: "" },
    });
    setRebuilt(false);
    setEditing(true);
  }

  function doRebuild() {
    if (!draft) return;
    const t = rebuildTemplate(project, draft.target);
    setDraft({ ...draft, prompt: t.prompt, negative: t.negative, coreFeatures: t.coreFeatures || draft.coreFeatures, h3: t.h3 });
    setRebuilt(true);
  }

  async function commit() {
    if (!draft) return;
    await onSave(draft);
    setEditing(false);
    setDraft(null);
    setFlash("已写回项目数据");
    window.setTimeout(() => setFlash(null), 2400);
  }

  const full = row.prompt.trim().length > 120;

  return (
    <article
      className={cn(
        "rounded-panel border border-rule-soft bg-panel/80 p-3",
        depth > 0 && "border-l-2 border-l-chrome/30 bg-raised/40",
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="text-body font-semibold leading-tight text-ink">{row.name}</h3>
          <p className="mt-0.5 text-note leading-snug text-ink-mute">{row.summary}</p>
        </div>
        <div className="flex flex-none items-center gap-1.5">
          {st && (
            <Badge className="inline-flex items-center gap-1">
              <StateGlyph state={st.key} />
              {st.text}
            </Badge>
          )}
          <Badge tone={row.stored ? "neutral" : "warn"}>{row.stored ? "已存文本" : "模板拼装"}</Badge>
          {!editing && (
            <Button size="sm" variant="quiet" icon={<Pencil className="h-3 w-3" />} onClick={startEdit}>
              编辑
            </Button>
          )}
        </div>
      </div>

      {editing && draft ? (
        <div className="mt-3 space-y-3">
          <label className="block space-y-1">
            <span className="label-mono">完整提示词 Prompt</span>
            <Textarea
              className="mono w-full text-note"
              rows={Math.min(18, Math.max(6, Math.ceil(draft.prompt.length / 70)))}
              value={draft.prompt}
              onChange={(e) => setDraft({ ...draft, prompt: e.target.value })}
              placeholder="留空则回到按模板现拼"
            />
          </label>

          {row.target.kind === "character" && (
            <label className="block space-y-1">
              <span className="label-mono">跨镜一致特征 Core Features</span>
              <Textarea
                className="w-full"
                rows={2}
                value={draft.coreFeatures}
                onChange={(e) => setDraft({ ...draft, coreFeatures: e.target.value })}
                placeholder="脸、发型、标志物 —— 拼提示词时单独成段"
              />
            </label>
          )}

          {isVideo && (
            <div className="space-y-2">
              <div className="grid gap-2 md:grid-cols-3">
                {(["integrated", "soundscape", "music"] as const).map((k) => (
                  <label key={k} className="block space-y-1">
                    <span className="label-mono">
                      {k === "integrated" ? "画面 integrated" : k === "soundscape" ? "环境声 soundscape" : "配乐 music"}
                    </span>
                    <Textarea
                      className="w-full"
                      rows={3}
                      value={draft.h3[k]}
                      onChange={(e) => setDraft({ ...draft, h3: { ...draft.h3, [k]: e.target.value } })}
                    />
                  </label>
                ))}
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <Button
                  size="sm"
                  variant="quiet"
                  onClick={() => setDraft({ ...draft, prompt: h3PromptText(draft.h3) })}
                >
                  把三段拼进正文
                </Button>
                <span className="text-caption leading-snug text-ink-mute">
                  生成时优先读正文（shot.videoPrompt），正文为空才会用 H3 三段现拼 —— 改完三段要同步正文，否则改动不会进节点。
                </span>
              </div>
            </div>
          )}

          {!isVideo && (
            <label className="block space-y-1">
              <span className="label-mono">负向提示词 Negative</span>
              <Textarea className="w-full" rows={2} value={draft.negative} onChange={(e) => setDraft({ ...draft, negative: e.target.value })} />
            </label>
          )}

          <div className="flex flex-wrap items-center gap-2">
            <Button size="sm" variant="quiet" icon={<RotateCcw className="h-3 w-3" />} onClick={doRebuild}>
              按当前模板重新拼装
            </Button>
            <span className="flex-1" />
            <Button size="sm" variant="ghost" onClick={() => { setEditing(false); setDraft(null); }}>
              取消
            </Button>
            <Button size="sm" variant="primary" onClick={() => void commit()}>
              保存并写回
            </Button>
          </div>
          <p className="text-caption leading-snug text-ink-mute">
            {rebuilt
              ? "上面是模板重拼的结果，还没写回：确认无误再点「保存并写回」，否则取消即可保留原文。"
              : "重拼只填进这个输入框，不会自动盖掉你手改的内容 —— 点保存才写回。"}
          </p>
        </div>
      ) : (
        <div className="mt-2.5 space-y-2">
          <pre
            className={cn(
              "mono whitespace-pre-wrap break-words rounded-ctl border border-rule-soft bg-slate px-2.5 py-2 text-note leading-relaxed text-ink-dim",
              !open && full && "line-clamp-4",
            )}
          >
            {row.prompt || "（空）"}
          </pre>
          <div className="flex flex-wrap items-center gap-2">
            {full && (
              <button
                type="button"
                onClick={() => setOpen((v) => !v)}
                className="inline-flex items-center gap-1 text-caption text-ink-mute hover:text-ink"
              >
                {open ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
                {open ? "收起" : "展开全文"}
              </button>
            )}
            {row.negative && (
              <span className="truncate text-caption text-ink-mute" title={row.negative}>
                负向：<span className="mono">{row.negative.length > 40 ? `${row.negative.slice(0, 40)}…` : row.negative}</span>
              </span>
            )}
            {!row.hasMedia && <span className="text-caption text-ink-mute">还没有产物，这条提示词尚未投产</span>}
            {flash && <span className="text-caption text-state-ok">{flash}</span>}
          </div>
        </div>
      )}

      {row.children?.length ? (
        <div className="mt-3 space-y-2 border-t border-rule-soft pt-3">
          <span className="label-mono">服装变体 · {row.children.length}</span>
          {row.children.map((c) => (
            <PromptCard key={c.key} row={c} project={project} onSave={onSave} depth={depth + 1} />
          ))}
        </div>
      ) : null}
    </article>
  );
}
