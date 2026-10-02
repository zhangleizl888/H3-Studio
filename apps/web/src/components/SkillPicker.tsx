/**
 * 提示词框旁边的「技能库」按钮 + 选择弹窗。
 *
 * 三处共用（角色/场景卡、镜头详情的视频提示词、剧本页的对话助手）：判据只有一份 ——
 * 本环节的 + 通用的才列出来，勾中顺序就是发给模型的先后顺序。
 *
 * 这里只交 id，不拼正文：正文由后端在提交时从库里读出来并进提示词 / system。
 * 前端跟着拼一份就会出现「框里显示的是一串、真发出去是另一串」。
 */

import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { BookMarked, Check, ExternalLink, TriangleAlert } from "lucide-react";
import { Badge, Button, Empty, Input, Modal, Spinner } from "./ui";
import { useSkills } from "../lib/hooks";
import { SKILL_STAGE_LABEL, pickedSkills, skillSummary, skillsForStage } from "../lib/skills";
import type { Skill, SkillStage } from "../lib/types";
import { cn } from "../lib/utils";

export function SkillPicker({
  stage,
  value,
  onChange,
  disabled,
  label = "技能库",
  className,
}: {
  stage: SkillStage;
  value?: string[];
  onChange: (ids: string[]) => void;
  disabled?: boolean;
  label?: string;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const { data: all, error } = useSkills();
  const ids = value ?? [];
  const { items, missing } = pickedSkills(all ?? [], ids);

  return (
    <>
      <div className={cn("space-y-1", className)}>
        <div className="flex flex-wrap items-center gap-1.5">
          <Button
            size="sm"
            variant={ids.length ? "default" : "quiet"}
            icon={<BookMarked className="h-3 w-3" />}
            disabled={disabled}
            onClick={() => setOpen(true)}
            title={
              ids.length
                ? `已挂 ${ids.length} 条技能：${skillSummary(items, missing)}。点开改`
                : `从技能库挑几条写法要求，随这段提示词一起发给模型（当前环节：${SKILL_STAGE_LABEL[stage]}＋通用）`
            }
          >
            {label}
            {ids.length > 0 && <span className="mono ml-1">{ids.length}</span>}
          </Button>
          {missing.length > 0 && (
            <span className="flex items-center gap-1 text-caption leading-snug text-state-fail" title={missing.join("、")}>
              <TriangleAlert className="h-3 w-3 flex-none" />
              {missing.length} 条技能已从库里删掉，这次生成不会带上
            </span>
          )}
        </div>
        {items.length > 0 && (
          <ul className="flex flex-wrap gap-1">
            {items.map((s) => (
              <li key={s.id}>
                <button
                  type="button"
                  onClick={() => onChange(ids.filter((x) => x !== s.id))}
                  title={`${s.description || s.name}\n\n${s.content}\n\n点一下摘掉这条`}
                  className="anim-pop-in flex max-w-[220px] items-center gap-1 rounded-ctl border border-chrome/35 bg-chrome/10 px-1.5 py-[1px] text-caption text-ink-dim transition-colors duration-160 ease-std hover:border-state-fail/50 hover:text-state-fail"
                >
                  <span className="truncate">{s.name}</span>
                  <span className="mono flex-none opacity-60">{SKILL_STAGE_LABEL[s.stage]}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
        {error && <p className="text-caption leading-snug text-state-fail">技能库读不出来：{error.message}</p>}
      </div>

      <SkillPickerModal open={open} onClose={() => setOpen(false)} stage={stage} value={ids} onApply={(next) => onChange(next)} />
    </>
  );
}

function SkillPickerModal({
  open,
  onClose,
  stage,
  value,
  onApply,
}: {
  open: boolean;
  onClose: () => void;
  stage: SkillStage;
  value: string[];
  onApply: (ids: string[]) => void;
}) {
  const nav = useNavigate();
  const { data: all, isLoading, error } = useSkills();
  const [draft, setDraft] = useState<string[]>(value);
  const [q, setQ] = useState("");
  const [expanded, setExpanded] = useState<string | null>(null);

  const pool = useMemo(() => skillsForStage(all ?? [], stage), [all, stage]);
  const shown = useMemo(() => {
    const key = q.trim().toLowerCase();
    if (!key) return pool;
    return pool.filter((s) => [s.name, s.description, s.content, ...s.tags].join(" ").toLowerCase().includes(key));
  }, [pool, q]);
  /** 搜索把勾过的筛出去时不能丢选择：draft 是全量，shown 只是视图 */
  const ordered = useMemo(() => draft.map((id) => pool.find((s) => s.id === id)).filter((s): s is Skill => !!s), [draft, pool]);

  function toggle(id: string) {
    setDraft((d) => (d.includes(id) ? d.filter((x) => x !== id) : [...d, id]));
  }

  function reset() {
    setDraft(value);
    setQ("");
    setExpanded(null);
  }

  return (
    <Modal
      open={open}
      onClose={() => {
        reset();
        onClose();
      }}
      title={`技能库 · 挑给${SKILL_STAGE_LABEL[stage]}这一环`}
      width={720}
      footer={
        <>
          <Button
            variant="quiet"
            icon={<ExternalLink className="h-3 w-3" />}
            onClick={() => {
              onClose();
              nav("/skills");
            }}
            title="去技能库新建一条，或导一批进来"
          >
            去技能库管理
          </Button>
          <span className="mr-auto text-caption text-ink-mute">
            {ordered.length ? `已选 ${ordered.length} 条，按勾选顺序发给模型` : "一条都没选：这次只发你写的提示词"}
          </span>
          <Button
            variant="quiet"
            onClick={() => {
              reset();
              onClose();
            }}
          >
            取消
          </Button>
          <Button
            variant="primary"
            onClick={() => {
              onApply(draft);
              onClose();
            }}
          >
            挂上这 {draft.length} 条
          </Button>
        </>
      }
    >
      <div className="space-y-2.5">
        <p className="text-note leading-snug text-ink-mute">
          技能是「这次额外要怎么写」的要求，不是素材：勾中之后它跟着提示词一起发出去，图/视频模型进提示词槽，文本模型进这次调用的要求里。
          这里列的是<span className="text-ink-dim">{SKILL_STAGE_LABEL[stage]}</span>环节的技能加上标了「通用」的那些。
        </p>

        <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="按名字、说明或正文里的词筛一下" aria-label="搜索技能" className="w-full" />

        {error ? (
          <p className="text-note text-state-fail">技能库读不出来：{error.message}</p>
        ) : isLoading ? (
          <p className="flex items-center gap-2 text-note text-ink-mute">
            <Spinner className="h-3.5 w-3.5" /> 正在读技能库。
          </p>
        ) : pool.length === 0 ? (
          <Empty
            title={`${SKILL_STAGE_LABEL[stage]}这一环还没有可用的技能`}
            hint="技能库存一段可复用的写法要求。新建一条，或者把现成的那份技能库（.json / .md，一整个文件夹也行）导进来。"
            action={
              <Button
                variant="primary"
                onClick={() => {
                  onClose();
                  nav("/skills");
                }}
              >
                去技能库
              </Button>
            }
          />
        ) : shown.length === 0 ? (
          <p className="px-1 py-4 text-center text-note text-ink-mute">没有匹配「{q}」的技能，共 {pool.length} 条可选。</p>
        ) : (
          <ul className="space-y-1.5">
            {shown.map((s) => {
              const on = draft.includes(s.id);
              const pos = draft.indexOf(s.id) + 1;
              return (
                <li key={s.id} className={cn("rounded-tile border transition-[background-color,border-color] duration-160 ease-std", on ? "border-chrome/45 bg-chrome/[0.07]" : "border-rule-soft bg-slate/50 hover:border-hairline")}>
                  <label className="flex cursor-pointer items-start gap-2 px-2.5 py-2">
                    <input type="checkbox" checked={on} onChange={() => toggle(s.id)} className="mt-1 h-3.5 w-3.5 flex-none accent-[color:var(--color-chrome)]" aria-label={`选用技能 ${s.name}`} />
                    <span className="min-w-0 flex-1 space-y-0.5">
                      <span className="flex flex-wrap items-center gap-1.5">
                        <span className="text-body font-semibold leading-tight">{s.name}</span>
                        <Badge>{SKILL_STAGE_LABEL[s.stage]}</Badge>
                        {on && (
                          <span className="mono flex items-center gap-1 text-caption text-chrome">
                            <Check className="h-3 w-3" />
                            第 {pos} 条
                          </span>
                        )}
                        {s.tags.map((t) => (
                          <span key={t} className="rounded-panel bg-slate px-1.5 py-[1px] text-caption text-ink-mute">
                            {t}
                          </span>
                        ))}
                      </span>
                      {s.description && <span className="block text-note leading-snug text-ink-dim">{s.description}</span>}
                    </span>
                    <button
                      type="button"
                      onClick={(e) => {
                        e.preventDefault();
                        setExpanded(expanded === s.id ? null : s.id);
                      }}
                      className="flex-none rounded-ctl px-1.5 py-0.5 text-caption text-ink-mute hover:bg-sheen hover:text-ink"
                      aria-expanded={expanded === s.id}
                    >
                      {expanded === s.id ? "收起正文" : "看正文"}
                    </button>
                  </label>
                  {expanded === s.id && (
                    <pre className="max-h-[180px] overflow-y-auto border-t border-rule-soft px-2.5 py-2 text-caption leading-relaxed whitespace-pre-wrap text-ink-dim">{s.content}</pre>
                  )}
                </li>
              );
            })}
          </ul>
        )}
        {ordered.length > 1 && <p className="text-caption leading-snug text-ink-mute">顺序有用：文本模型按先后读，前面的先满足。点名字底下那颗小标签可以单独摘掉一条。</p>}
      </div>
    </Modal>
  );
}
