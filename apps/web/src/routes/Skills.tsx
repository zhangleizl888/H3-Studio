/**
 * /skills —— 技能库。
 *
 * 一条技能 = 名称 + 一句说明 + 要发给模型的正文 + 挂在哪个环节。
 * 库在服务端（skills 表），项目里只存选中的 id：正文由后端在 /llm/run 与 /jobs 提交那一刻
 * 读出来并进 system / 提示词槽，所以改一次技能，所有挂着它的角色、镜头、对话下次都跟着变，
 * 不需要回去重存项目。
 *
 * 这一页只管库本身：新建、导一批（文件或整个文件夹）、改正文、删一条、清空整库。
 * 在哪挂、挂哪几条，是在提示词框旁边的那颗「技能库」按钮上选的。
 */

import { useEffect, useMemo, useRef, useState, type DragEvent } from "react";
import { Badge, Button, Copyable, Empty, Field, Input, KeyVal, Modal, Panel, Select, Textarea } from "../components/ui";
import { SplitHandle, usePane } from "../components/SplitPane";
import { useSkillMutations, useSkills } from "../lib/hooks";
import { SKILL_STAGE_HINT, SKILL_STAGE_LABEL } from "../lib/skills";
import type { Skill, SkillImportReport, SkillStage } from "../lib/types";
import { cn, fmtTime } from "../lib/utils";

const STAGES: SkillStage[] = ["general", "script", "asset", "video"];
const ACCEPT = ".json,.md,.markdown,.txt";

export default function Skills() {
  const pane = usePane("skills.list", 320, 240, 560);
  const { data: list, error, isLoading } = useSkills();
  const [picked, setPicked] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [importing, setImporting] = useState(false);
  const [confirmClear, setConfirmClear] = useState(false);

  const sorted = useMemo(() => [...(list ?? [])].sort((a, b) => a.stage.localeCompare(b.stage) + a.name.localeCompare(b.name, "zh-CN")), [list]);
  const skill = sorted.find((s) => s.id === picked) ?? null;

  return (
    <div style={pane.style} className="grid gap-4 p-4 xl:grid-cols-[var(--pane-w)_minmax(0,1fr)]">
      <div className="relative min-w-0">
        <SkillList
          items={sorted}
          loading={isLoading}
          error={error?.message ?? null}
          selected={skill?.id ?? null}
          onSelect={setPicked}
          onNew={() => setCreating(true)}
          onImport={() => setImporting(true)}
          onClear={() => setConfirmClear(true)}
        />
        <SplitHandle pane={pane} side="left" label="技能列表宽度" className="hidden xl:block" />
      </div>

      {skill ? (
        <SkillEditor key={skill.id} skill={skill} onDelete={() => setPicked(null)} />
      ) : (
        <Panel title="技能">
          {!sorted.length ? (
            <Empty
              title="技能库是空的"
              hint="技能是一段可复用的写法要求，选好之后跟着提示词一起发给模型。先建一条，或者把现成的那批（.json / .md，或者整个文件夹）导进来。"
              action={
                <div className="flex gap-2">
                  <Button variant="primary" onClick={() => setCreating(true)}>
                    新建技能
                  </Button>
                  <Button onClick={() => setImporting(true)}>导入技能库</Button>
                </div>
              }
            />
          ) : (
            <div className="p-3 text-note text-ink-mute">从左边点一条技能就能改它的正文与适用环节。</div>
          )}
        </Panel>
      )}

      <NewSkillModal open={creating} onClose={() => setCreating(false)} onCreated={(id) => { setCreating(false); setPicked(id); }} />
      <ImportModal open={importing} onClose={() => setImporting(false)} />
      <ClearModal open={confirmClear} onClose={() => setConfirmClear(false)} total={sorted.length} />
    </div>
  );
}

function SkillList({
  items,
  loading,
  error,
  selected,
  onSelect,
  onNew,
  onImport,
  onClear,
}: {
  items: Skill[];
  loading: boolean;
  error: string | null;
  selected: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
  onImport: () => void;
  onClear: () => void;
}) {
  return (
    <Panel
      title={
        <span className="flex items-center gap-2">
          技能库
          {items.length > 0 && <Badge>{items.length}</Badge>}
        </span>
      }
      actions={
        <>
          <Button size="sm" variant="primary" onClick={onNew}>
            新建
          </Button>
          <Button size="sm" onClick={onImport}>
            导入
          </Button>
        </>
      }
      dense
      className="self-start"
    >
      {error ? (
        <div className="px-3 py-6 text-center text-note text-state-fail">{error}</div>
      ) : loading ? (
        <div className="px-3 py-6 text-center text-note text-ink-mute">正在读取技能库。</div>
      ) : items.length === 0 ? (
        <div className="p-3">
          <Empty title="还没有技能" hint="一条技能就是几句要模型照办的写法要求。新建一条，或导一批进来。" />
        </div>
      ) : (
        <ul className="divide-y divide-rule-soft">
          {items.map((s) => (
            <li key={s.id}>
              <button type="button" onClick={() => onSelect(s.id)} className={cn("block w-full space-y-1 px-3 py-2.5 text-left hover:bg-row-hover", selected === s.id && "bg-raised")}>
                <div className="flex items-start justify-between gap-2">
                  <span className="text-body font-semibold leading-tight">{s.name}</span>
                  <span className="label flex-none">{SKILL_STAGE_LABEL[s.stage]}</span>
                </div>
                {s.description && <p className="line-clamp-2 text-caption leading-snug text-ink-mute">{s.description}</p>}
                <div className="flex flex-wrap items-center gap-1">
                  <Badge>{s.origin === "imported" ? "导入" : "新建"}</Badge>
                  <span className="mono text-caption text-ink-mute">{s.content.length} 字</span>
                  {s.tags.map((t) => (
                    <span key={t} className="rounded-panel bg-slate px-1.5 py-[1px] text-caption text-ink-mute">
                      {t}
                    </span>
                  ))}
                </div>
                <div className="flex items-center justify-between gap-2 text-caption text-ink-mute">
                  <span className="truncate">{s.source ? `来自 ${s.source}` : "本页新建"}</span>
                  <span className="flex-none">{fmtTime(s.updatedAt)}</span>
                </div>
              </button>
            </li>
          ))}
        </ul>
      )}

      {items.length > 0 && (
        <div className="flex items-center justify-between gap-2 border-t border-rule-soft px-3 py-2">
          <span className="text-caption leading-snug text-ink-mute">
            这些技能在提示词框旁边挑：{SKILL_STAGE_HINT.script} / {SKILL_STAGE_HINT.asset} / {SKILL_STAGE_HINT.video}，通用的三处都能选。
          </span>
          <Button size="sm" variant="danger" onClick={onClear} title="删掉整个技能库（项目里挂着的选择会变成失效，界面会亮出来）">
            删除技能库
          </Button>
        </div>
      )}
    </Panel>
  );
}

function SkillEditor({ skill, onDelete }: { skill: Skill; onDelete: () => void }) {
  const mut = useSkillMutations();
  const [name, setName] = useState(skill.name);
  const [desc, setDesc] = useState(skill.description);
  const [content, setContent] = useState(skill.content);
  const [stage, setStage] = useState<SkillStage>(skill.stage);
  const [tags, setTags] = useState(skill.tags.join(", "));
  const [confirm, setConfirm] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  useEffect(() => {
    setName(skill.name);
    setDesc(skill.description);
    setContent(skill.content);
    setStage(skill.stage);
    setTags(skill.tags.join(", "));
    setMsg(null);
  }, [skill]);

  const dirty = name !== skill.name || desc !== skill.description || content !== skill.content || stage !== skill.stage || tags !== skill.tags.join(", ");

  function save() {
    mut.update.mutate(
      { id: skill.id, body: { name: name.trim(), description: desc.trim(), content: content.trim(), stage, tags: splitTags(tags) } },
      {
        onSuccess: (row) => setMsg(`已保存：${row.name}（${row.content.length} 字）。下次生成就按这份正文发。`),
        onError: (e) => setMsg(`保存失败：${(e as Error).message}`),
      },
    );
  }

  return (
    <div className="space-y-4">
      <Panel
        title={<span className="text-body">{skill.name}</span>}
        actions={<span className="mono text-caption text-ink-mute">#{skill.id}</span>}
      >
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-1.5">
            <Badge>{SKILL_STAGE_LABEL[skill.stage]}</Badge>
            <Badge>{skill.origin === "imported" ? `导入自 ${skill.source ?? "文件"}` : "本页新建"}</Badge>
            <span className="mono text-caption text-ink-mute">{skill.content.length} 字</span>
            <span className="text-caption text-ink-mute">改于 {fmtTime(skill.updatedAt)}</span>
          </div>
          <KeyVal
            items={[
              ["这一环之外还能选到它吗", skill.stage === "general" ? "能：三处提示词框都列" : `只列在「${SKILL_STAGE_LABEL[skill.stage]}」那处，加上通用那几条`],
              ["怎么发给模型", skill.stage === "video" || skill.stage === "asset" ? "并进这次提交的提示词槽（参数表里能看到并好之后那串）" : "进这次文本调用的要求里"],
            ]}
          />
        </div>
      </Panel>

      <Panel title="编辑这条技能" actions={dirty ? <Badge tone="warn">有改动没存</Badge> : undefined}>
        <div className="space-y-3">
          <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_150px]">
            <Field label="名字" hint="库里与提示词框旁边都显示这个，同名会被拦下来。">
              <Input value={name} onChange={(e) => setName(e.target.value)} maxLength={200} />
            </Field>
            <Field label="挂在哪个环节">
              <Select value={stage} onChange={(e) => setStage(e.target.value as SkillStage)} className="w-full">
                {STAGES.map((s) => (
                  <option key={s} value={s}>
                    {SKILL_STAGE_LABEL[s]}
                  </option>
                ))}
              </Select>
            </Field>
          </div>
          <Field label="一句说明" hint="只在列表与弹窗里给人看，不进模型。">
            <Input value={desc} onChange={(e) => setDesc(e.target.value)} placeholder="去掉评价词，换成光线与材质" />
          </Field>
          <Field label="标签" hint="逗号分隔，最多 12 个，用来在弹窗里筛。">
            <Input value={tags} onChange={(e) => setTags(e.target.value)} placeholder="画面, 负向" />
          </Field>
          <Field label="发给模型的正文" hint={`这段原样发出去。合计上限 6000 字（一次生成挂的所有技能加起来），单条上限 2 万字。`}>
            <Textarea value={content} onChange={(e) => setContent(e.target.value)} rows={12} className="w-full leading-relaxed" placeholder="要模型照办的，写成几句可执行的要求" />
          </Field>
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="primary" disabled={!dirty || !content.trim() || !name.trim()} loading={mut.update.isPending} onClick={save}>
              保存
            </Button>
            <Button variant="quiet" disabled={!dirty} onClick={() => { setName(skill.name); setDesc(skill.description); setContent(skill.content); setStage(skill.stage); setTags(skill.tags.join(", ")); setMsg(null); }}>
              撤销改动
            </Button>
            <Button variant="danger" onClick={() => setConfirm(true)}>
              删除这条技能
            </Button>
            {msg && <span className="text-caption leading-snug text-ink-mute">{msg}</span>}
          </div>
        </div>
      </Panel>

      <Panel title="这段正文现在会跟着发出去" dense>
        <div className="px-3 py-2">
          <Copyable text={skill.content} className="block">
            <pre className="max-h-[220px] overflow-y-auto whitespace-pre-wrap text-note leading-relaxed text-ink-dim">{skill.content}</pre>
          </Copyable>
          <p className="mt-2 text-caption leading-snug text-ink-mute">
            这是库里现在那一份。角色、场景、镜头上存的只是选中的 id，所以这里改完不用回去重存项目 —— 下一次生成就是这一份。
          </p>
        </div>
      </Panel>

      <Modal
        open={confirm}
        onClose={() => setConfirm(false)}
        title="删除这条技能"
        width={440}
        footer={
          <>
            <Button variant="quiet" onClick={() => setConfirm(false)}>
              取消
            </Button>
            <Button
              variant="danger"
              loading={mut.remove.isPending}
              onClick={() =>
                mut.remove.mutate(skill.id, {
                  onSuccess: () => {
                    setConfirm(false);
                    onDelete();
                  },
                })
              }
            >
              确认删除
            </Button>
          </>
        }
      >
        <p className="text-note leading-relaxed text-ink-dim">
          只删库里这条「{skill.name}」。项目里挂着它的角色、场景、镜头不会被动，但那颗按钮上会亮起「已从技能库删掉」，下次生成不再带上这段要求。
        </p>
      </Modal>
    </div>
  );
}

function NewSkillModal({ open, onClose, onCreated }: { open: boolean; onClose: () => void; onCreated: (id: string) => void }) {
  const mut = useSkillMutations();
  const [name, setName] = useState("");
  const [desc, setDesc] = useState("");
  const [content, setContent] = useState("");
  const [stage, setStage] = useState<SkillStage>("general");
  const [tags, setTags] = useState("");
  const err = mut.create.error?.message ?? null;

  useEffect(() => {
    if (!open) return;
    setName("");
    setDesc("");
    setContent("");
    setStage("general");
    setTags("");
    mut.create.reset();
    // 只在弹窗打开那一次清草稿
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="新建一条技能"
      width={700}
      footer={
        <>
          <span className="mr-auto text-caption text-ink-mute">正文原样发给模型：写「要怎么改」，不要抄一篇范文</span>
          <Button variant="quiet" onClick={onClose}>
            取消
          </Button>
          <Button
            variant="primary"
            loading={mut.create.isPending}
            disabled={!name.trim() || !content.trim()}
            onClick={() =>
              mut.create.mutate(
                { name: name.trim(), description: desc.trim(), content: content.trim(), stage, tags: splitTags(tags) },
                { onSuccess: (row) => onCreated(row.id) },
              )
            }
          >
            建好入库
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_170px]">
          <Field label="名字" hint="列表与提示词框旁边的按钮上显示这个。">
            <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="画面克制：只写拍得到的" maxLength={200} autoFocus />
          </Field>
          <Field label="挂在哪个环节">
            <Select value={stage} onChange={(e) => setStage(e.target.value as SkillStage)} className="w-full">
              {STAGES.map((s) => (
                <option key={s} value={s}>
                  {SKILL_STAGE_LABEL[s]} · {s === "general" ? "三处都能选" : SKILL_STAGE_HINT[s].slice(0, 6)}
                </option>
              ))}
            </Select>
          </Field>
        </div>
        <Field label="一句说明" hint="给人看的，不进模型。">
          <Input value={desc} onChange={(e) => setDesc(e.target.value)} placeholder="去掉评价词，换成构图、光线、材质" />
        </Field>
        <Field label="标签（逗号分隔）">
          <Input value={tags} onChange={(e) => setTags(e.target.value)} placeholder="画面, 出图" />
        </Field>
        <Field label="发给模型的正文" hint="这一段会跟着提示词一起发出去。勾多条时按勾选顺序拼。">
          <Textarea value={content} onChange={(e) => setContent(e.target.value)} rows={9} className="w-full leading-relaxed" placeholder={"不写「很美」「震撼」这类评价词；\n每个形容词落到构图、景别、机位、光线方向、色温或材质上。"} />
        </Field>
        {err && <p className="text-note text-state-fail">{err}</p>}
      </div>
    </Modal>
  );
}

function ImportModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const mut = useSkillMutations();
  const [files, setFiles] = useState<File[]>([]);
  const [library, setLibrary] = useState("");
  const [hint, setHint] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const dirRef = useRef<HTMLInputElement>(null);
  const report = mut.importFiles.data as SkillImportReport | undefined;
  const err = mut.importFiles.error?.message ?? null;

  useEffect(() => {
    // 文件夹选择器要靠这个非标准属性；React 的类型里没有它，挂 DOM 上更省事。
    // 挂在 open 上而不是只挂一次：弹窗关着的时候 Modal 不渲染，那时 ref 还是空的。
    if (open && dirRef.current) dirRef.current.setAttribute("webkitdirectory", "");
  }, [open]);

  useEffect(() => {
    if (!open) return;
    setFiles([]);
    setHint(null);
    mut.importFiles.reset();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  // 报告一出来就把待导入那批清掉：留着会让人以为还能再导一次（其实只会把刚建的刷新一遍）
  useEffect(() => {
    if (report) setFiles([]);
  }, [report]);

  function add(list: FileList | null) {
    const next = Array.from(list ?? []).filter((f) => /\.(json|md|markdown|txt)$/i.test(f.name));
    const dropped = Array.from(list ?? []).length - next.length;
    setFiles((prev) => {
      const seen = new Set(prev.map((p) => `${p.name}:${p.size}`));
      return [...prev, ...next.filter((f) => !seen.has(`${f.name}:${f.size}`))];
    });
    setHint(dropped > 0 ? `已经跳过 ${dropped} 个不收的文件（只认 ${ACCEPT.replace(/\./g, "")}）` : null);
    if (!library && next[0]) setLibrary(next[0].name.replace(/\.[^.]+$/, ""));
  }

  function onDrop(e: DragEvent<HTMLDivElement>) {
    e.preventDefault();
    add(e.dataTransfer?.files ?? null);
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="导入技能库"
      width={720}
      footer={
        <>
          <span className="mr-auto text-caption text-ink-mute">{files.length ? `${files.length} 个文件待导入` : "选文件或整个文件夹"}</span>
          <Button variant="quiet" onClick={onClose}>
            {report ? "关掉" : "取消"}
          </Button>
          {!report && (
            <Button
              variant="primary"
              loading={mut.importFiles.isPending}
              disabled={!files.length}
              onClick={() => mut.importFiles.mutate({ files, library: library.trim() || undefined })}
            >
              导入这 {files.length} 个文件
            </Button>
          )}
        </>
      }
    >
      <div className="space-y-3">
        <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_auto_auto] sm:items-end">
          <Field label="这批叫什么" hint="记在每条技能的来源上，列表里显示「来自 …」；同名技能会被刷新而不是再插一条。">
            <Input value={library} onChange={(e) => setLibrary(e.target.value)} placeholder="接续四法" maxLength={200} />
          </Field>
          <Button onClick={() => fileRef.current?.click()}>选文件</Button>
          <Button onClick={() => dirRef.current?.click()}>选整个文件夹</Button>
        </div>

        <input ref={fileRef} type="file" multiple accept={ACCEPT} className="sr-only" onChange={(e) => { add(e.target.files); e.target.value = ""; }} />
        <input ref={dirRef} type="file" multiple className="sr-only" onChange={(e) => { add(e.target.files); e.target.value = ""; }} />

        <div onDragOver={(e) => e.preventDefault()} onDrop={onDrop} className="rounded-tile border border-dashed border-rule px-3 py-3 text-note leading-snug text-ink-mute">
          把文件拖进来也行。收 <span className="mono">.json</span> / <span className="mono">.md</span> / <span className="mono">.txt</span>：
          <ul className="mt-1.5 space-y-0.5 pl-4">
            <li className="list-disc">
              <span className="mono">.json</span> 一份可以装多条：数组、单条对象，或 <span className="mono">{"{\"skills\": [...]}"}</span> 那种整库导出；字段名认 <span className="mono">content/正文/body/instruction</span>。
            </li>
            <li className="list-disc">
              <span className="mono">.md</span> 一份一条：frontmatter 里的 <span className="mono">name / description / tags / stage</span> 认，正文就是发给模型那段；<span className="mono">SKILL.md</span> 这种靠目录名区分的，名字取它所在的文件夹。
            </li>
            <li className="list-disc">同名走刷新而不是重复插一条，所以同一批重导不会让库里长出双胞胎。</li>
          </ul>
          {hint && <p className="mt-1.5 text-caption text-ink-dim">{hint}</p>}
        </div>

        {files.length > 0 && (
          <ul className="max-h-[160px] space-y-1 overflow-y-auto rounded-tile border border-rule-soft bg-slate/50 px-2 py-1.5">
            {files.map((f) => (
              <li key={`${f.name}:${f.size}`} className="flex items-center gap-2 text-caption">
                <span className="mono min-w-0 flex-1 truncate">{f.name}</span>
                <span className="flex-none text-ink-mute">{(f.size / 1024).toFixed(1)} KB</span>
                <button type="button" onClick={() => setFiles((prev) => prev.filter((x) => x !== f))} className="flex-none rounded-ctl px-1 text-ink-mute hover:text-state-fail" aria-label={`把 ${f.name} 从待导入里去掉`}>
                  去掉
                </button>
              </li>
            ))}
          </ul>
        )}

        {err && <p className="text-note text-state-fail">导入失败：{err}</p>}
        {report && <ReportView report={report} />}
      </div>
    </Modal>
  );
}

function ReportView({ report }: { report: SkillImportReport }) {
  const c = report.counts;
  return (
    <div className="space-y-2 rounded-tile border border-rule bg-slate px-3 py-2.5">
      <div className="flex flex-wrap items-center gap-1.5">
        <Badge tone={c.created + c.updated > 0 ? "good" : "warn"}>新建 {c.created}</Badge>
        <Badge tone={c.updated ? "good" : undefined}>刷新 {c.updated}</Badge>
        <Badge tone={c.skipped ? "warn" : undefined}>跳过 {c.skipped}</Badge>
        <Badge tone={c.errors ? "bad" : undefined}>读不动 {c.errors}</Badge>
        <span className="text-caption text-ink-mute">共读了 {report.files} 个文件</span>
      </div>
      <Detail title="新建" items={[...report.created.map((s) => `${s.name}（${SKILL_STAGE_LABEL[s.stage]} · ${s.content.length} 字）`)]} />
      <Detail title="刷新" items={report.updated.map((s) => `${s.name}（${s.content.length} 字）`)} />
      <Detail title="跳过" items={report.skipped.map((s) => `${s.name}：${s.reason}`)} tone="warn" />
      <Detail title="读不动" items={report.errors.map((e) => `${e.file}：${e.reason}`)} tone="bad" />
    </div>
  );
}

function Detail({ title, items, tone }: { title: string; items: string[]; tone?: "warn" | "bad" }) {
  if (!items.length) return null;
  return (
    <div className="space-y-0.5">
      <span className="label">{title}（{items.length}）</span>
      <ul className={cn("space-y-0.5 text-caption leading-snug", tone === "bad" ? "text-state-fail" : tone === "warn" ? "text-ink-dim" : "text-ink-mute")}>
        {items.map((t, i) => (
          <li key={i}>{t}</li>
        ))}
      </ul>
    </div>
  );
}

function ClearModal({ open, onClose, total }: { open: boolean; onClose: () => void; total: number }) {
  const mut = useSkillMutations();
  const [word, setWord] = useState("");
  const err = mut.clear.error?.message ?? null;

  useEffect(() => {
    if (open) {
      setWord("");
      mut.clear.reset();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="删除整个技能库"
      width={460}
      footer={
        <>
          <Button variant="quiet" onClick={onClose}>
            取消
          </Button>
          <Button variant="danger" loading={mut.clear.isPending} disabled={word !== "清空技能库"} onClick={() => mut.clear.mutate(undefined, { onSuccess: onClose })}>
            确认删 {total} 条
          </Button>
        </>
      }
    >
      <div className="space-y-2.5">
        <p className="text-note leading-relaxed text-ink-dim">
          库里这 <b className="mono">{total}</b> 条技能全部删掉。项目、角色、场景、镜头本身不动，但它们存的技能选择会变成失效 ——
          按钮旁边会亮出「N 条技能已从库里删掉」，下次生成不再带那段要求。
        </p>
        <Field label={`要把这 ${total} 条一起删掉，请把「清空技能库」原样打一遍`} hint="后端也认这一句：这是唯一的防手滑闸，不是界面自己加的。">
          <Input value={word} onChange={(e) => setWord(e.target.value)} placeholder="清空技能库" />
        </Field>
        {err && <p className="text-note text-state-fail">{err}</p>}
      </div>
    </Modal>
  );
}

function splitTags(v: string): string[] {
  return Array.from(new Set(v.split(/[,，、;；]/).map((s) => s.trim()).filter(Boolean))).slice(0, 12);
}
