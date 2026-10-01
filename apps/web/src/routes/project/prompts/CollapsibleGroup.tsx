import type { ReactNode } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { cn } from "../../../lib/utils";

/** 可折叠分组：标题带计数，收起时只留一行，展开时把这一类的提示词卡全摊开 */
export function CollapsibleGroup({
  title,
  icon,
  shown,
  total,
  open,
  onToggle,
  children,
}: {
  title: string;
  icon: ReactNode;
  shown: number;
  total: number;
  open: boolean;
  onToggle: () => void;
  children: ReactNode;
}) {
  if (total === 0) return null;
  return (
    <section className="space-y-2">
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={open}
        className="flex w-full items-center gap-2 rounded-ctl px-1 py-1 text-left hover:bg-raised/50"
      >
        {open ? <ChevronDown className="h-4 w-4 flex-none text-ink-dim" /> : <ChevronRight className="h-4 w-4 flex-none text-ink-mute" />}
        <span className="text-ink-dim">{icon}</span>
        <span className="text-subtitle font-bold text-ink">{title}</span>
        <span className={cn("mono text-note", shown === total ? "text-ink-dim" : "text-chrome")}>
          ({shown}
          {shown === total ? "" : ` / ${total}`})
        </span>
        {shown === 0 && <span className="text-caption text-ink-mute">当前筛选下没有条目</span>}
      </button>
      {open && (
        <div className="space-y-2.5 pl-1 md:pl-6">
          {shown === 0 ? (
            <p className="rounded-panel border border-dashed border-rule px-3 py-4 text-note text-ink-mute">
              这一组共 {total} 条，都不匹配现在的搜索词或类型筛选。
            </p>
          ) : (
            children
          )}
        </div>
      )}
    </section>
  );
}
