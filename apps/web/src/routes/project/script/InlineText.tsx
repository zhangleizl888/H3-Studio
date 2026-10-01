import { useEffect, useState } from "react";
import { Check, Pencil, X } from "lucide-react";
import { Button, Textarea } from "../../../components/ui";
import { cn } from "../../../lib/utils";

/**
 * 拍摄清单里的就地编辑：平时是一段可读文本，点铅笔才变输入框。
 *
 * 保存走 onBlur 之外的显式按钮 —— 提示词是长文本，敲错一个字就写回 IndexedDB 太鲁莽。
 */
export function InlineText({
  value,
  onSave,
  placeholder = "（空）",
  emptyText = "暂无内容",
  mono,
  serif,
  rows = 5,
  disabled,
  className,
}: {
  value: string;
  onSave: (v: string) => void;
  placeholder?: string;
  emptyText?: string;
  mono?: boolean;
  serif?: boolean;
  rows?: number;
  disabled?: boolean;
  className?: string;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(value);
  useEffect(() => {
    if (!editing) setDraft(value);
  }, [value, editing]);

  const commit = () => {
    if (draft !== value) onSave(draft);
    setEditing(false);
  };

  if (editing) {
    return (
      <div className="space-y-1.5">
        <Textarea
          autoFocus
          className={cn("w-full", mono && "mono", serif && "italic", className)}
          rows={rows}
          value={draft}
          placeholder={placeholder}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Escape") setEditing(false);
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) commit();
          }}
        />
        <div className="flex items-center gap-1.5">
          <Button size="sm" variant="primary" icon={<Check className="h-3 w-3" />} onClick={commit}>
            保存
          </Button>
          <Button size="sm" variant="ghost" icon={<X className="h-3 w-3" />} onClick={() => setEditing(false)}>
            取消
          </Button>
          <span className="label ml-auto">Ctrl+Enter 保存</span>
        </div>
      </div>
    );
  }

  return (
    <div className={cn("group/inline flex items-start gap-2", className)}>
      <p className={cn("min-w-0 flex-1 text-note leading-relaxed text-ink-dim", mono && "mono whitespace-pre-wrap", serif && "italic")}>
        {value?.trim() ? value : <span className="text-ink-mute">{emptyText}</span>}
      </p>
      {!disabled && (
        <button
          type="button"
          onClick={() => setEditing(true)}
          title="就地编辑"
          className="flex-none rounded-ctl p-1 text-ink-mute opacity-0 transition-opacity hover:bg-raised hover:text-ink focus-visible:opacity-100 group-hover/inline:opacity-100"
        >
          <Pencil className="h-3 w-3" />
        </button>
      )}
    </div>
  );
}
