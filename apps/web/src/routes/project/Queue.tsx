import { Fragment, useState } from "react";
import { useParams } from "react-router-dom";
import {
  Badge,
  Button,
  Empty,
  Input,
  MachChip,
  Panel,
  Progress,
  Select,
  StateGlyph,
  StateLabel,
  FilterChip,
  Toggle,
  type StateKey,
} from "../../components/ui";
import { JOB_KIND_LABEL, RH_ERROR_HINTS } from "../../lib/constants";
import { useInstances, useJobMutations, useJobs, useProjects } from "../../lib/hooks";
import type { GenInstance, Job, JobError, JobKind, JobState, Placement } from "../../lib/types";
import { cn, fmtDur, fmtMoney, fmtSec, fmtTime } from "../../lib/utils";

/**
 * /p/:id/queue —— 这个项目的任务表。
 * 表里只放后端真的给了字段的东西：rh_task 不给百分比就不画进度条。
 */

const STATE_ORDER: JobState[] = ["queued", "dispatching", "running", "succeeded", "failed", "canceled"];

const STATE_TEXT: Record<JobState, string> = {
  queued: "排队",
  dispatching: "派发中",
  running: "进行中",
  succeeded: "完成",
  failed: "失败",
  canceled: "已取消",
};

/** JobState 比 StateKey 多一个 dispatching；图形上归到排队，文案单独写 */
const GLYPH: Record<JobState, StateKey> = {
  queued: "queued",
  dispatching: "queued",
  running: "running",
  succeeded: "succeeded",
  failed: "failed",
  canceled: "canceled",
};

const KIND_ORDER = Object.keys(JOB_KIND_LABEL) as JobKind[];

const MACH_COLOR: Record<Placement, string> = {
  local: "var(--color-mach-local)",
  cloud_self: "var(--color-mach-self)",
  cloud_runninghub: "var(--color-mach-rh)",
};

export default function Queue() {
  const { id } = useParams<{ id: string }>();
  const mut = useJobMutations(id ?? null);
  const { data: scoped, error: scopedErr } = useJobs(id);
  const { data: all, error: allErr } = useJobs(null);
  const { data: instances } = useInstances();
  const { data: projects } = useProjects();

  const [globalView, setGlobalView] = useState(false);
  const [states, setStates] = useState<JobState[]>([]);
  const [kind, setKind] = useState<JobKind | "">("");
  const [instanceId, setInstanceId] = useState("");
  const [open, setOpen] = useState<string[]>([]);
  const [drafts, setDrafts] = useState<Record<string, string>>({});

  const source = globalView ? all : scoped;
  const err = globalView ? allErr : scopedErr;
  const list = source ?? [];
  const liveCount = list.filter((j) => j.state === "running" || j.state === "queued" || j.state === "dispatching").length;

  // 与派发顺序一致：先按优先级，再按 id
  const rows = list
    .filter((j) => (states.length ? states.includes(j.state) : true))
    .filter((j) => (kind ? j.kind === kind : true))
    .filter((j) => (instanceId ? j.instanceId === instanceId : true))
    .sort((a, b) => a.priority - b.priority || a.id.localeCompare(b.id));

  const instOf = (j: Job) => instances?.find((i) => i.id === j.instanceId) ?? null;
  const now = Date.now();
  const actionError = mut.cancel.error?.message ?? mut.retry.error?.message ?? mut.priority.error?.message ?? null;

  function toggleState(s: JobState) {
    setStates((prev) => (prev.includes(s) ? prev.filter((x) => x !== s) : [...prev, s]));
  }

  function toggleOpen(jobId: string) {
    setOpen((prev) => (prev.includes(jobId) ? prev.filter((x) => x !== jobId) : [...prev, jobId]));
  }

  function savePriority(j: Job) {
    const raw = drafts[j.id];
    if (raw === undefined) return;
    const v = Number(raw);
    if (!Number.isFinite(v)) return;
    setDrafts((d) => {
      const next = { ...d };
      delete next[j.id];
      return next;
    });
    mut.priority.mutate({ id: j.id, priority: Math.round(v) });
  }

  return (
    <div className="space-y-4 p-4">
      <Panel
        title={
          <span className="flex items-center gap-2">
            队列
            <Badge>{rows.length}</Badge>
            {liveCount > 0 && <Badge>{liveCount} 条在产线上</Badge>}
          </span>
        }
        actions={
          <span className="text-caption text-ink-mute">
            {globalView ? "所有项目的任务" : id ? `项目 ${id}` : "地址里没有项目 id"}
            {" · "}
            有排队或进行中的任务时这一页每秒刷新，全部落定后放慢到 8 秒
          </span>
        }
        dense
      >
        <div className="space-y-2.5 px-3 py-2.5">
          <div className="flex flex-wrap items-center gap-1.5">
            <FilterChip active={states.length === 0} onClick={() => setStates([])}>
              全部 <span className="mono">{list.length}</span>
            </FilterChip>
            {STATE_ORDER.map((s) => (
              <FilterChip key={s} active={states.includes(s)} onClick={() => toggleState(s)}>
                <StateGlyph state={GLYPH[s]} className="mr-1.5" />
                {STATE_TEXT[s]} <span className="mono">{list.filter((j) => j.state === s).length}</span>
              </FilterChip>
            ))}
          </div>

          <div className="flex flex-wrap items-center gap-3">
            <label className="flex items-center gap-1.5">
              <span className="label">类型</span>
              <Select value={kind} onChange={(e) => setKind(e.target.value as JobKind | "")}>
                <option value="">全部</option>
                {KIND_ORDER.map((k) => (
                  <option key={k} value={k}>
                    {JOB_KIND_LABEL[k]}
                  </option>
                ))}
              </Select>
            </label>

            <label className="flex items-center gap-1.5">
              <span className="label">实例</span>
              <Select value={instanceId} onChange={(e) => setInstanceId(e.target.value)}>
                <option value="">全部</option>
                {instances?.map((i) => (
                  <option key={i.id} value={i.id}>
                    {i.name}
                  </option>
                ))}
              </Select>
            </label>

            <Toggle
              checked={globalView}
              onChange={setGlobalView}
              label="全部在跑的任务"
              hint="不只看本项目，横切整个产线"
            />

            {(states.length > 0 || kind || instanceId) && (
              <Button
                size="sm"
                variant="quiet"
                onClick={() => {
                  setStates([]);
                  setKind("");
                  setInstanceId("");
                }}
              >
                清空筛选
              </Button>
            )}
          </div>

          <div className="grid gap-2 text-note leading-snug text-ink-mute sm:grid-cols-2">
            <p>
              优先级数字小者先跑，默认 100；手动插队填 <span className="mono text-ink-dim">0</span>。同优先级按提交顺序取。
            </p>
            <p>协议决定能看到什么：<span className="mono text-ink-dim">comfy_native</span> 有真实百分比，
              <span className="mono text-ink-dim">rh_task</span> 只给状态，不给百分比。</p>
          </div>
        </div>
      </Panel>

      <Panel title={globalView ? "所有任务" : "本项目的任务"} dense>
        {source === undefined ? (
          <div className="px-3 py-8 text-center text-note text-ink-mute">正在读取任务列表。</div>
        ) : err ? (
          <div className="px-3 py-8 text-center text-note text-state-fail">读不到任务：{err.message}</div>
        ) : !id && !globalView ? (
          <div className="px-3 py-8 text-center text-note text-ink-mute">地址里没有项目 id，打开「全部在跑的任务」看整条产线。</div>
        ) : rows.length === 0 ? (
          <div className="p-3">
            <Empty title="没有符合筛选条件的任务" hint="去掉一两个筛选试试，或者打开「全部在跑的任务」看别的项目。" />
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full border-collapse text-note">
              <thead>
                <tr className="border-b border-rule text-left">
                  <th className="w-6 px-1 py-1.5" />
                  <th className="label px-2 py-1.5 font-normal">状态</th>
                  <th className="label px-2 py-1.5 font-normal">标题</th>
                  <th className="label px-2 py-1.5 font-normal">类型</th>
                  <th className="label px-2 py-1.5 font-normal">实例</th>
                  <th className="label w-[150px] px-2 py-1.5 font-normal">进度</th>
                  {globalView && <th className="label px-2 py-1.5 font-normal">项目</th>}
                  <th className="label px-2 py-1.5 text-right font-normal">排队</th>
                  <th className="label px-2 py-1.5 text-right font-normal">尝试</th>
                  <th className="label px-2 py-1.5 text-right font-normal">耗时</th>
                  <th className="label px-2 py-1.5 text-right font-normal">成本</th>
                  <th className="label px-2 py-1.5 text-right font-normal">操作</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((j) => {
                  const inst = instOf(j);
                  const isOpen = open.includes(j.id);
                  const isLive = j.state === "running" || j.state === "queued" || j.state === "dispatching";
                  const stage = j.progress.stage ?? (j.progress.nodeTitle ? `节点 ${j.progress.nodeTitle}` : null);
                  const elapsed = elapsedMs(j, now);
                  const prio = drafts[j.id] ?? String(j.priority);
                  return (
                    <Fragment key={j.id}>
                      <tr className={cn("border-b border-rule-soft", isOpen && "bg-row-hover")}>
                        <td className="px-1 py-2 align-middle">
                          <button
                            onClick={() => toggleOpen(j.id)}
                            aria-label={isOpen ? "收起详情" : "展开详情"}
                            aria-expanded={isOpen}
                            className="mono h-5 w-5 rounded-ctl text-body text-ink-mute hover:bg-raised hover:text-ink"
                          >
                            {isOpen ? "−" : "+"}
                          </button>
                        </td>
                        <td className="px-2 py-2 align-middle">
                          <span className="inline-flex items-center gap-1.5 whitespace-nowrap text-note text-ink-dim">
                            <StateGlyph state={GLYPH[j.state]} />
                            {STATE_TEXT[j.state]}
                          </span>
                        </td>
                        <td className="max-w-[280px] px-2 py-2 align-middle">
                          <button onClick={() => toggleOpen(j.id)} className="block max-w-full truncate text-left hover:text-ink">
                            {j.title}
                          </button>
                        </td>
                        <td className="px-2 py-2 align-middle whitespace-nowrap">
                          <span className="label">{JOB_KIND_LABEL[j.kind]}</span>
                          {!!j.workflowName && (
                            <span className="mono ml-1.5 text-caption text-chrome" title={`选法：${j.chosenBy ?? "手动指定"}`}>
                              {j.workflowName}
                              {j.chosenBy?.includes("自动") ? " ·自动选" : j.chosenBy?.includes("回落") ? " ·回落模板" : ""}
                            </span>
                          )}
                        </td>
                        <td className="px-2 py-2 align-middle whitespace-nowrap">
                          {inst ? (
                            <MachChip placement={inst.placement} label={inst.name} />
                          ) : j.kind === "llm_chat" ? (
                            <span className="label">本地文本模型</span>
                          ) : (
                            <span className="label">本机合成</span>
                          )}
                        </td>
                        <td className="px-2 py-2 align-middle">
                          <Progress
                            value={j.progress.value}
                            max={j.progress.max}
                            unavailable={j.progress.unavailable}
                            stripe={isLive}
                            machine={inst ? MACH_COLOR[inst.placement] : undefined}
                            stage={stage}
                          />
                          {j.progress.etaSec != null && !j.progress.unavailable && (
                            <div className="mono text-micro text-ink-mute">还剩约 {fmtSec(j.progress.etaSec)}</div>
                          )}
                        </td>
                        {globalView && (
                          <td className="px-2 py-2 align-middle text-caption whitespace-nowrap">
                            {projects?.find((p) => p.id === j.projectId)?.name ?? (j.projectId ? <span className="mono">{j.projectId}</span> : <span className="text-ink-mute">无项目</span>)}
                          </td>
                        )}
                        <td className="mono px-2 py-2 text-right align-middle">
                          {j.queuePos == null ? <span className="text-ink-mute">—</span> : j.queuePos}
                        </td>
                        <td className="mono px-2 py-2 text-right align-middle">{j.attempts}</td>
                        <td className="mono px-2 py-2 text-right align-middle">
                          {elapsed == null ? <span className="text-ink-mute">—</span> : fmtDur(elapsed)}
                        </td>
                        <td className="px-2 py-2 text-right align-middle whitespace-nowrap">
                          <CostCell job={j} />
                        </td>
                        <td className="px-2 py-2 text-right align-middle">
                          <div className="inline-flex items-center gap-1">
                            {isLive && (
                              <Button
                                size="sm"
                                variant="quiet"
                                loading={mut.cancel.isPending && mut.cancel.variables === j.id}
                                onClick={() => mut.cancel.mutate(j.id)}
                              >
                                取消
                              </Button>
                            )}
                            {(j.state === "failed" || j.state === "canceled") && (
                              <Button
                                size="sm"
                                variant="default"
                                loading={mut.retry.isPending && mut.retry.variables === j.id}
                                onClick={() => mut.retry.mutate(j.id)}
                              >
                                重试
                              </Button>
                            )}
                            <label className="ml-1 inline-flex items-center gap-1">
                              <span className="label">优先</span>
                              <Input
                                value={prio}
                                onChange={(e) => setDrafts((d) => ({ ...d, [j.id]: e.target.value }))}
                                onKeyDown={(e) => e.key === "Enter" && savePriority(j)}
                                inputMode="numeric"
                                className="h-6 w-[52px] px-1 text-right"
                              />
                            </label>
                            <Button
                              size="sm"
                              variant="quiet"
                              disabled={drafts[j.id] === undefined || !Number.isFinite(Number(prio)) || Number(prio) === j.priority}
                              onClick={() => savePriority(j)}
                            >
                              存
                            </Button>
                          </div>
                        </td>
                      </tr>
                      {isOpen && (
                        <tr className="border-b border-rule bg-row-hover">
                          <td colSpan={globalView ? 12 : 11} className="px-3 py-3">
                            <JobDetail job={j} instance={inst} interject={() => mut.priority.mutate({ id: j.id, priority: 0 })} />
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Panel>

      {actionError && <p className="px-1 text-note leading-snug text-state-fail">这条操作没生效：{actionError}</p>}
    </div>
  );
}

function CostCell({ job }: { job: Job }) {
  const money = job.cost?.money;
  const coins = job.cost?.coins;
  if (money != null) return <span className="mono text-note text-mach-rh">{fmtMoney(money)}</span>;
  if (coins != null)
    return (
      <span className="mono text-note text-ink-dim">
        {coins} <span className="text-ink-mute">RH币</span>
      </span>
    );
  return <span className="text-ink-mute">—</span>;
}

function JobDetail({ job, instance, interject }: { job: Job; instance: GenInstance | null; interject: () => void }) {
  const rhError = findRhCode(job.error);
  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
      <div className="space-y-2">
        <div className="label">任务标识</div>
        <div className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-note">
          <span className="text-ink-mute">id</span>
          <span className="mono">{job.id}</span>
          <span className="text-ink-mute">protocol</span>
          <span className="mono">{instance ? instance.protocol : "不占生成实例"}</span>
          <span className="text-ink-mute">promptId</span>
          <span className="mono break-all">{job.promptId ?? "—"}</span>
          <span className="text-ink-mute">优先级</span>
          <span className="mono">
            {job.priority}
            <button onClick={interject} className="ml-2 text-caption text-ink-dim underline hover:text-ink">
              插队到 0
            </button>
          </span>
          <span className="text-ink-mute">起止</span>
          <span className="mono">
            {fmtTime(job.startedAt)} — {fmtTime(job.finishedAt)}
          </span>
          <span className="text-ink-mute">GPU 秒</span>
          <span className="mono">{job.cost?.seconds != null ? Math.round(job.cost.seconds) : "—"}</span>
        </div>

        {instance?.protocol === "rh_task" && (
          <ul className="space-y-1 border-t border-rule-soft pt-2 text-caption leading-snug text-ink-mute">
            <li>这台走 rh_task：进度只到状态粒度，没有百分比可以给你。</li>
            <li>seed 每次都由我们显式注入，平台会强制重置它。</li>
            <li>结果链接约 1 天过期，完成时立刻转存到 data/media。</li>
          </ul>
        )}
        {instance?.protocol === "comfy_native" && instance.placement === "cloud_runninghub" && (
          <p className="border-t border-rule-soft pt-2 text-caption leading-snug text-ink-mute">
            同为 RunningHub，但这台是 <span className="mono">/proxy</span> 网关，走的是 <span className="mono">comfy_native</span>，所以有真实进度。
          </p>
        )}
      </div>

      <div className="space-y-2">
        <div className="label">日志</div>
        {job.log.length === 0 ? (
          <div className="text-note text-ink-mute">这条任务还没有留下日志。</div>
        ) : (
          <ul className="space-y-1">
            {job.log.map((l, i) => (
              <li key={i} className="mono flex gap-2 text-caption leading-snug">
                <span className="flex-none text-ink-mute">{fmtTime(l.ts)}</span>
                <span className={cn("flex-none", l.level === "error" ? "text-state-fail" : "text-ink-mute")}>{l.level}</span>
                <span className="text-ink-dim">{l.msg}</span>
              </li>
            ))}
          </ul>
        )}

        {job.error && (
          <div className="space-y-2 rounded-tile border border-rule bg-inset p-2.5">
            <div className="flex flex-wrap items-center gap-2">
              <StateLabel state="failed" />
              <span className="mono text-caption text-ink-mute">{job.error.type}</span>
              {job.error.nodeId && <Badge>节点 {job.error.nodeId}</Badge>}
              {job.error.nodeType && <Badge>{job.error.nodeType}</Badge>}
            </div>
            <p className="text-note leading-snug text-ink">{job.error.message}</p>
            <div className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-note">
              <span className="text-ink-mute">nodeId</span>
              <span className="mono">{job.error.nodeId ?? "—"}</span>
              <span className="text-ink-mute">nodeType</span>
              <span className="mono">{job.error.nodeType ?? "—"}</span>
            </div>
            {job.error.hint && <p className="text-note leading-snug text-ink-dim">处置：{job.error.hint}</p>}
            {rhError && (
              <div className="rounded-ctl border border-rule-soft bg-slate px-2 py-1.5">
                <div className="text-note text-ink">RunningHub 错误码 {rhError.code}：{rhError.text}</div>
              </div>
            )}
            {job.error.tracebackTail && (
              <div className="space-y-1">
                <div className="label">traceback 尾部（来自 failedReason）</div>
                <pre className="mono max-h-40 overflow-auto whitespace-pre-wrap rounded-ctl bg-slate p-2 text-caption leading-[1.5] text-ink-dim">
                  {job.error.tracebackTail}
                </pre>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function elapsedMs(job: Job, now: number): number | null {
  if (!job.startedAt) return null;
  const start = Date.parse(job.startedAt);
  const end = job.finishedAt ? Date.parse(job.finishedAt) : now;
  if (!Number.isFinite(start) || !Number.isFinite(end)) return null;
  return Math.max(0, end - start);
}

/** 只在错误文本里找已知错误码，避免把节点号当成码 */
function findRhCode(error?: JobError | null): { code: string; text: string } | null {
  if (!error) return null;
  const hay = [error.type, error.message, error.hint];
  for (const field of hay) {
    for (const token of field?.match(/\d{3,4}/g) ?? []) {
      const hint = RH_ERROR_HINTS[token];
      if (hint) return { code: token, text: hint };
    }
  }
  return null;
}
