/**
 * 提示词编辑区：默认显示「实际会打出去的那一段」，点铅笔才进编辑。
 *
 * 存的是 character.visualPrompt / scene.visualPrompt —— prompts.ts 的 build*Prompt 见到非空
 * 就优先用它，留空则回到按 traits / 场景三要素现拼。所以「清空」也是一个动作：
 * 手改过之后想回到自动拼装，就得能一键抹掉。
 */

import { useEffect, useState } from "react";
import { Camera, CircleAlert, Pencil, RotateCcw, Save, X } from "lucide-react";
import { Badge, Button, Copyable, Textarea } from "../../../components/ui";
import { cn } from "../../../lib/utils";

export function PromptEditor({
  label,
  prompt,
  negative,
  fallback,
  placeholder,
  onSave,
  maxHeight = "max-h-[190px]",
  disabled,
}: {
  label: string;
  prompt?: string;
  negative?: string;
  fallback: string;
  placeholder: string;
  onSave: (patch: { visualPrompt: string; negativePrompt: string }) => void;
  maxHeight?: string;
  disabled?: boolean;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(prompt ?? "");
  const [draftNeg, setDraftNeg] = useState(negative ?? "");

  useEffect(() => {
    if (!editing) {
      setDraft(prompt ?? "");
      setDraftNeg(negative ?? "");
    }
  }, [prompt, negative, editing]);

  const handEdited = !!prompt?.trim();
  const shown = handEdited ? (prompt ?? "").trim() : fallback;

  function save() {
    onSave({ visualPrompt: draft.trim(), negativePrompt: draftNeg.trim() });
    setEditing(false);
  }

  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between gap-2">
        <span className="label-mono flex items-center gap-1.5">
          <Camera className="h-3 w-3" />
          {label}
        </span>
        <div className="flex items-center gap-1.5">
          {handEdited ? <Badge>已手改</Badge> : <Badge>按资料现拼</Badge>}
          {!editing && (
            <Button size="sm" variant="ghost" icon={<Pencil className="h-3 w-3" />} disabled={disabled} onClick={() => setEditing(true)}>
              编辑
            </Button>
          )}
        </div>
      </div>

      {editing ? (
        <div className="space-y-2">
          <Textarea
            autoFocus
            rows={7}
            value={draft}
            placeholder={placeholder}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
                e.preventDefault();
                save();
              }
              if (e.key === "Escape") {
                setDraft(prompt ?? "");
                setDraftNeg(negative ?? "");
                setEditing(false);
              }
            }}
            className="w-full leading-relaxed"
          />
          <div>
            <span className="label-mono">负向提示词（可留空，留空用内置通用负向）</span>
            <Textarea rows={2} value={draftNeg} placeholder="低分辨率、文字水印、多余手指…" onChange={(e) => setDraftNeg(e.target.value)} className="mt-1 w-full" />
          </div>
          <div className="flex items-center gap-1.5">
            <Button variant="primary" size="sm" icon={<Save className="h-3 w-3" />} onClick={save} className="flex-1">
              保存（Ctrl+Enter）
            </Button>
            <Button
              size="sm"
              variant="quiet"
              icon={<RotateCcw className="h-3 w-3" />}
              title="抹掉手改内容，回到按资料现拼"
              onClick={() => {
                onSave({ visualPrompt: "", negativePrompt: "" });
                setEditing(false);
              }}
            >
              清空
            </Button>
            <Button size="sm" variant="ghost" icon={<X className="h-3 w-3" />} onClick={() => setEditing(false)}>
              取消
            </Button>
          </div>
        </div>
      ) : (
        <div className={cn("rounded-ctl border border-hairline bg-void/45 px-2.5 py-2", maxHeight, shown ? "overflow-y-auto" : "")}>
          {shown ? (
            <Copyable text={shown} className="block">
              <span className="whitespace-pre-wrap break-words text-note leading-relaxed text-ink-dim">{shown}</span>
            </Copyable>
          ) : (
            <div className="flex items-start gap-2 text-note text-ink-mute">
              <CircleAlert className="mt-0.5 h-3.5 w-3.5 flex-none" />
              <span>还没有画面描述：补一句外形，或直接点「编辑」写完整提示词。</span>
            </div>
          )}
          {!handEdited && shown && <div className="label mt-1">留空即按上面的资料现拼；保存后以你写的为准。</div>}
          {negative?.trim() && <div className="label mt-1 truncate" title={negative}>负向：{negative}</div>}
        </div>
      )}
    </div>
  );
}
