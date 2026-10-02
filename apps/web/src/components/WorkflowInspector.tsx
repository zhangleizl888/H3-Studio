import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Badge,
  Button,
  Copyable,
  Empty,
  Field,
  Input,
  MachChip,
  Panel,
  Progress,
  Select,
  StateLabel,
  Tabs,
} from "./ui";
import { RH_ERROR_HINTS } from "../lib/constants";
import { useApi } from "../lib/apiClient";
import { useJobMutations, useJobs, useWorkflowMutations } from "../lib/hooks";
import type { GenInstance, Job, NodeOverride, Workflow, WorkflowFamily, WorkflowSlot } from "../lib/types";
import { cn, fmtMoney, fmtTime } from "../lib/utils";

/*
 * 一条工作流的详情面板：槽位、任务信号与本机改写、试运行、RunningHub 投影、导出。
 *
 * 它原来独占 /workflows 一个页面，和「设置 · 工作流管理」那套卡片是同一个库的两副面孔 ——
 * 改一次权重要在两处之间跳。现在合并成一处：卡片上点名字就地展开这块。
 *
 * 一份槽位定义喂两种执行后端：comfy_native 用 patch 后的 API 图，rh_task 用 nodeInfoList 投影，
 * 所以这里把两个形状都摊开给人看，而不是只给一个"看起来对"的。
 */

type TabKey = "slots" | "adapt" | "test" | "rh" | "export";

const FAMILY_LABEL: Record<WorkflowFamily, string> = {
  image: "图像",
  video: "视频",
  audio: "音频",
  upscale: "放大",
  custom: "自定义",
};

export default function WorkflowInspector({ wf, instances }: { wf: Workflow; instances: GenInstance[] }) {
  const nav = useNavigate();
  // 「只能跑在 RunningHub 上」是从导入时报回来的专有节点判的，不是按实例名字猜的
  const rhOnly = (wf.requirements?.runninghubOnly?.length ?? 0) > 0;
  const [tab, setTab] = useState<TabKey>("slots");
  /** 页内槽位值：只用来算 patch 预览与 nodeInfoList 投影，没有写回接口 */
  const [raws, setRaws] = useState<Record<string, string>>({});

  const slots = wf.slots ?? [];
  const wfMut = useWorkflowMutations();
  const [scanMsg, setScanMsg] = useState("");
  const typed = useMemo(() => typedValues(slots, raws), [slots, raws]);

  /** 重扫 = 后端从 graph_original 重改写一遍；装了缺的节点包之后必须点它 */
  function rescan(w: Workflow) {
    setScanMsg("正在按实例的 /object_info 重改写…");
    wfMut.rescan.mutate({ id: w.id }, {
      onSuccess: (res) => {
        setScanMsg(`重扫完成：改写 ${res.report.adaptations.length} 处、还缺 ${res.report.gaps.length} 处`);
      },
      onError: (e) => setScanMsg(`重扫失败：${(e as Error).message}`.slice(0, 180)),
    });
  }

  function toggleAuto(w: Workflow) {
    const next = w.autoSelect === false;
    wfMut.patch.mutate({ id: w.id, body: { autoSelect: next } }, {
      onSuccess: () => setScanMsg(next ? "已允许参与自动选" : "已禁止参与自动选"),
      onError: (e) => setScanMsg(`改失败：${(e as Error).message}`.slice(0, 180)),
    });
  }
  const patch = useMemo(() => apiPatch(slots, raws), [slots, raws]);
  const changed = Object.keys(raws).filter((k) => raws[k] !== "").length;

  return (
    <div className="space-y-4">
      <Panel
        title={<span className="text-body">{wf.name}</span>}
        actions={<span className="mono text-caption text-ink-mute">{wf.id}</span>}
      >
        <div className="space-y-2">
          <div className="flex flex-wrap items-center gap-1.5">
            <Badge>{FAMILY_LABEL[wf.family]}</Badge>
            <Badge>{wf.isBuiltin ? "内置" : "导入"}</Badge>
            <Badge>源格式 {wf.sourceFormat}</Badge>
            <Badge>{wf.executesOn === "cloud_runninghub" ? "只能云端跑" : wf.executesOn === "local" ? "本机跑" : "本机与云端都能跑"}</Badge>
            {(wf.gaps?.length ?? 0) > 0 ? <Badge tone="warn">本机缺 {wf.gaps!.length} 处</Badge> : <Badge tone="good">本机节点齐</Badge>}
            {!!(wf.adaptations?.length ?? 0) && <Badge>改写 {wf.adaptations!.length} 处</Badge>}
            {wf.verifiedAt ? <Badge tone="good">真机跑通过</Badge> : <Badge tone="warn">还没跑过</Badge>}
            {rhOnly && <Badge tone="warn">只能跑在 RunningHub 实例上</Badge>}
          </div>
          {wf.description && <p className="text-note leading-relaxed text-ink-dim">{wf.description}</p>}
          <div className="flex flex-wrap gap-1.5">
            {wf.tags.map((t) => (
              <span key={t} className="rounded-panel bg-slate px-1.5 py-[1px] text-caption text-ink-mute">
                {t}
              </span>
            ))}
          </div>
          {rhOnly && (
            <p className="text-note leading-snug text-ink-mute">
              图里有 RunningHub 专有节点：{wf.requirements?.runninghubOnly?.join("、")}。本机与自建云的 ComfyUI 装不到它们，
              所以下面所有实例选择器里，非 RunningHub 的选项都是灰的。{" "}
              <button className="text-ink underline" onClick={() => nav("/settings/gen")}>
                去检查 RunningHub 实例
              </button>
            </p>
          )}
          <SlotRequirements wf={wf} />
          {!wf.isBuiltin && (
            <div className="flex flex-wrap items-center gap-2">
              <Button size="sm" variant="quiet" onClick={() => rescan(wf)} disabled={wfMut.rescan.isPending}>
                重新扫描（按实例现在的节点与权重重改写）
              </Button>
              <Button
                size="sm"
                variant="quiet"
                onClick={() => toggleAuto(wf)}
                title="关掉后这条只认手动指定，不会被「按任务自动选」挑中"
              >
                {wf.autoSelect === false ? "允许参与自动选" : "禁止参与自动选"}
              </Button>
              {scanMsg && <span className="text-caption text-ink-mute">{scanMsg}</span>}
            </div>
          )}
        </div>
      </Panel>

      <Panel dense>
        <Tabs
          className="px-3 pt-1.5"
          value={tab}
          onChange={setTab}
          tabs={[
            { key: "slots", label: "槽位", badge: <span className="mono text-caption text-ink-mute">{slots.length}</span> },
            {
              key: "adapt",
              label: "任务信号与本机改写",
              badge: <span className="mono text-caption text-ink-mute">{(wf.signals?.length ?? 0) + (wf.adaptations?.length ?? 0)}</span>,
            },
            { key: "test", label: "试运行" },
            { key: "rh", label: "RunningHub 投影", badge: rhOnly ? <Badge tone="warn">必需</Badge> : undefined },
            { key: "export", label: "导出" },
          ]}
        />

        <div className="p-3">
          {tab === "slots" && (
            <SlotsTab
              slots={slots}
              raws={raws}
              setRaws={setRaws}
              patch={patch}
              changed={changed}
              typed={typed}
            />
          )}
          {tab === "adapt" && <AdaptTab wf={wf} />}
          {tab === "test" && <TestTab wf={wf} slots={slots} raws={raws} setRaws={setRaws} instances={instances} rhOnly={rhOnly} />}
          {tab === "rh" && <RhTab wf={wf} slots={slots} typed={typed} changed={changed} raws={raws} setRaws={setRaws} rhOnly={rhOnly} />}
          {tab === "export" && <ExportTab wf={wf} />}
        </div>
      </Panel>
    </div>
  );
}

/**
 * 「任务信号 / 本机改写」页签。
 *
 * 这两样都是导入时后端算出来的，界面只是把它们摊开：
 * 信号说明这条工作流吃任务给的哪些东西，改写说明为了让它在这台机器上跑动我们动了哪些节点。
 */
function AdaptTab({ wf }: { wf: Workflow }) {
  const signals = wf.signals ?? [];
  const gaps = wf.gaps ?? [];
  const adaptations = wf.adaptations ?? [];
  return (
    <div className="space-y-4">
      <section className="space-y-2">
        <p className="text-note leading-snug text-ink-mute">
          「任务信号」是后端从图上读出来的可填输入 —— 自动选工作流就是拿这次任务给的东西跟它们对，填槽也是按这里的落点写。
        </p>
        {signals.length === 0 ? (
          <Empty title="没解析出任务信号" hint="只能整图提交或手动指定；装上缺的节点包后重新扫描，信号会更全。" />
        ) : (
          <ul className="grid gap-2 sm:grid-cols-2">
            {signals.map((sig) => (
              <li key={sig.name} className="rounded-panel border border-rule-soft bg-sheen p-2">
                <div className="flex flex-wrap items-center gap-1.5">
                  <span className="text-body font-semibold">{sig.label}</span>
                  <span className="mono text-caption text-ink-mute">{sig.name}</span>
                  {sig.required && <Badge tone="warn">必填</Badge>}
                  {sig.many && <Badge>可多份</Badge>}
                </div>
                <p className="mono mt-1 text-caption leading-snug text-ink-mute">
                  {sig.type} · {sig.addresses.length} 个落点：{sig.addresses.slice(0, 3).join("、")}
                  {sig.addresses.length > 3 ? ` 等 ${sig.addresses.length} 个` : ""}
                </p>
                {!!sig.also?.length && <p className="mt-0.5 text-caption text-ink-mute">别处还有 {sig.also.length} 处同名参数，填槽不动它们</p>}
              </li>
            ))}
          </ul>
        )}
      </section>

      {gaps.length > 0 && (
        <section className="space-y-1.5">
          <span className="label-mono">这台实例还缺</span>
          <ul className="space-y-1">
            {gaps.map((g, i) => (
              <li key={`${g.node}-${g.class_type}-${i}`} className="rounded-panel border border-state-warn/40 bg-state-warn/5 p-2 text-note leading-snug">
                <span className="mono text-caption text-state-warn">#{g.node}</span> <b>{g.class_type}</b>
                <span className="text-ink-mute"> —— {g.reason}</span>
                {g.pack ? <span className="text-ink-mute">（要装：{g.pack}）</span> : null}
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="space-y-1.5">
        <span className="label-mono">导入时改写过什么（{adaptations.length} 处）</span>
        {adaptations.length === 0 ? (
          <p className="text-note text-ink-mute">这份图原样就能在这台实例上跑，没有替换过任何节点。</p>
        ) : (
          <ul className="max-h-[340px] space-y-1 overflow-y-auto pr-1">
            {adaptations.map((a, i) => (
              <li key={`${a.node}-${i}`} className="rounded-panel border border-rule-soft bg-slate/40 p-2 text-caption leading-snug">
                <span className="mono text-ink-mute">#{a.node}</span> <b>{a.action}</b> <span className="mono">{a.from}</span>
                {a.to ? <span className="mono text-chrome"> → {a.to}</span> : null}
                <div className="text-ink-mute">{a.detail}</div>
              </li>
            ))}
          </ul>
        )}
      </section>

      {!!wf.pendingMedia?.length && (
        <p className="text-note leading-snug text-state-warn">
          {wf.pendingMedia.length} 个素材位写的还是作者机器里的文件名（{wf.pendingMedia.slice(0, 3).join("、")}
          {wf.pendingMedia.length > 3 ? "…" : ""}），派发时必须由这次任务重新给素材，没给到的位置会被整条撤掉。
        </p>
      )}
    </div>
  );
}

function SlotRequirements({ wf }: { wf: Workflow }) {
  const r = wf.requirements;
  if (!r) return null;
  const models = r.models ?? [];
  const nodes = r.customNodes ?? [];
  const pip = r.pip ?? [];
  if (!models.length && !nodes.length && !pip.length) return null;
  return (
    <div className="rounded-tile border border-rule-soft bg-slate px-2.5 py-2 text-note leading-snug text-ink-mute">
      <span className="label">运行前提</span>
      {models.length > 0 && (
        <ul className="mt-1 space-y-0.5">
          {models.map((m, i) => (
            <li key={i} className="mono">
              models/{m.folder}/{m.filename}
            </li>
          ))}
        </ul>
      )}
      {nodes.length > 0 && <div className="mt-1">自定义节点：{nodes.join("、")}</div>}
      {pip.length > 0 && <div className="mt-1">Python 依赖：{pip.join(" ")}</div>}
    </div>
  );
}

/* ───────── 槽位 ───────── */

function SlotsTab({
  slots,
  raws,
  setRaws,
  patch,
  changed,
  typed,
}: {
  slots: WorkflowSlot[];
  raws: Record<string, string>;
  setRaws: (fn: (prev: Record<string, string>) => Record<string, string>) => void;
  patch: Record<string, Record<string, unknown>>;
  changed: number;
  typed: Record<string, unknown>;
}) {
  const [group, setGroup] = useState<"全部" | "输入" | "采样" | "输出">("全部");
  const shown = slots.filter((s) => (group === "全部" ? true : s.group === group));
  const patchText = JSON.stringify(patch, null, 2);

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-1.5">
          {(["全部", "输入", "采样", "输出"] as const).map((g) => (
            <button
              key={g}
              onClick={() => setGroup(g)}
              className={cn(
                "rounded-ctl border px-2 py-[3px] text-note",
                group === g ? "border-transparent bg-ink text-slate" : "border-rule bg-raised text-ink-dim hover:text-ink",
              )}
            >
              {g} <span className="mono">{g === "全部" ? slots.length : slots.filter((s) => s.group === g).length}</span>
            </button>
          ))}
        </div>
        <span className="text-caption text-ink-mute">地址语法：<span className="mono">nodeId.inputName</span>、<span className="mono">nodeId.index</span>、<span className="mono">parent/child.inputName</span>、<span className="mono">*:inputName</span></span>
      </div>

      {slots.length === 0 ? (
        <Empty title="这份工作流没有解析出槽位" hint="导入时后端要抽 slots；没有槽位就只能整图提交，参数改不了。" />
      ) : (
        <div className="overflow-x-auto rounded-tile border border-rule-soft">
          <table className="w-full border-collapse text-note">
            <thead>
              <tr className="border-b border-rule text-left">
                <th className="label px-2 py-1.5 font-normal">地址</th>
                <th className="label px-2 py-1.5 font-normal">名称</th>
                <th className="label px-2 py-1.5 font-normal">类型</th>
                <th className="label px-2 py-1.5 font-normal">分组</th>
                <th className="label px-2 py-1.5 font-normal">默认值</th>
                <th className="label px-2 py-1.5 font-normal">必填</th>
                <th className="label px-2 py-1.5 font-normal">可选值 / 范围</th>
                <th className="label w-[190px] px-2 py-1.5 font-normal">改成</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((s) => (
                <tr key={s.address} className="border-b border-rule-soft align-middle">
                  <td className="px-2 py-1.5">
                    <Copyable text={s.address} className="max-w-[170px]">
                      <span className="mono">{s.address}</span>
                    </Copyable>
                    {s.path && <div className="mono text-caption text-ink-mute">{s.path}</div>}
                  </td>
                  <td className="px-2 py-1.5 whitespace-nowrap">{s.name}</td>
                  <td className="mono px-2 py-1.5 text-caption text-ink-dim">{s.type}</td>
                  <td className="px-2 py-1.5 text-note text-ink-mute">{s.group}</td>
                  <td className="mono px-2 py-1.5 text-caption">{s.default === undefined ? "—" : String(s.default)}</td>
                  <td className="px-2 py-1.5">{s.required ? <Badge>必填</Badge> : <span className="text-ink-mute">否</span>}</td>
                  <td className="px-2 py-1.5 text-caption text-ink-mute">
                    {s.options?.length ? (
                      <span className="mono block max-w-[240px] truncate">{s.options.join(" | ")}</span>
                    ) : s.min != null || s.max != null ? (
                      <span className="mono">
                        {s.min ?? "—"} … {s.max ?? "—"}
                        {s.step != null ? ` · 步长 ${s.step}` : ""}
                      </span>
                    ) : (
                      s.widget ? "控件" : "连线输入"
                    )}
                  </td>
                  <td className="px-2 py-1.5">
                    <SlotControl slot={s} raw={raws[s.address] ?? ""} onChange={(v) => setRaws((prev) => ({ ...prev, [s.address]: v }))} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="grid gap-3 lg:grid-cols-2">
        <div className="space-y-1.5">
          <div className="flex items-baseline justify-between gap-2">
            <span className="label">API 格式 patch（给 comfy_native 的 slot 引擎）</span>
            <span className="text-caption text-ink-mute">{changed} 项已改</span>
          </div>
          {changed === 0 ? (
            <p className="rounded-tile border border-dashed border-rule px-2.5 py-3 text-note leading-snug text-ink-mute">
              改上面任意一格，这里就出现实际会提交的 patch。留空的槽位不会被写进图里。
            </p>
          ) : (
            <pre className="mono max-h-56 overflow-auto rounded-tile bg-slate p-2.5 text-caption leading-[1.55] text-ink-dim">{patchText}</pre>
          )}
        </div>

        <div className="space-y-1.5">
          <span className="label">这份 patch 提交给原生实例前要知道的</span>
          <ul className="space-y-1 text-note leading-snug text-ink-mute">
            <li>int 和 float 会按槽位类型转成数字提交，不会带引号。</li>
            <li>combo 只能取上面列出的可选值，其它值会被 /prompt 校验拒掉。</li>
            <li>连线（inputs 里的 <span className="mono">["128", 0]</span>）不是参数，这里改不到也不该改。</li>
            <li>
              ComfyUI 前端专属控件（比如 <span className="mono">control_after_generate</span>）在 API 格式里不存在，设不了。
            </li>
          </ul>
          {changed > 0 && (
            <div className="flex items-center justify-between gap-2 rounded-tile border border-rule-soft bg-slate px-2 py-1.5 text-caption text-ink-dim">
              <span className="mono">{Object.keys(typed).length} 个值同时喂给 RunningHub 投影那一页</span>
              <Copyable text={patchText}>复制 patch</Copyable>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function SlotControl({ slot, raw, onChange }: { slot: WorkflowSlot; raw: string; onChange: (v: string) => void }) {
  if (slot.type === "combo") {
    return (
      <Select value={raw} onChange={(e) => onChange(e.target.value)} className="h-6 w-full">
        <option value="">不改</option>
        {(slot.options ?? []).map((o) => (
          <option key={o} value={o}>
            {o}
          </option>
        ))}
      </Select>
    );
  }
  if (slot.type === "bool") {
    return (
      <Select value={raw} onChange={(e) => onChange(e.target.value)} className="h-6 w-full">
        <option value="">不改</option>
        <option value="true">true</option>
        <option value="false">false</option>
      </Select>
    );
  }
  const numeric = slot.type === "int" || slot.type === "float";
  return (
    <Input
      value={raw}
      onChange={(e) => onChange(e.target.value)}
      placeholder={slot.default === undefined ? "改值" : String(slot.default)}
      inputMode={numeric ? "numeric" : undefined}
      type={numeric ? "number" : "text"}
      step={numeric ? (slot.step ?? undefined) : undefined}
      className="h-6 w-full"
    />
  );
}

/* ───────── 试运行 ───────── */

function TestTab({
  wf,
  slots,
  raws,
  setRaws,
  instances,
  rhOnly,
}: {
  wf: Workflow;
  slots: WorkflowSlot[];
  raws: Record<string, string>;
  setRaws: (fn: (prev: Record<string, string>) => Record<string, string>) => void;
  instances: GenInstance[];
  rhOnly: boolean;
}) {
  const mut = useWorkflowMutations();
  const jobMut = useJobMutations(null);
  const { data: jobs } = useJobs(null);
  const [instanceId, setInstanceId] = useState("");

  // 默认选第一台可用且探活成功的实例；只能跑在 RH 上的图不选原生实例
  useEffect(() => {
    if (instanceId || instances.length === 0) return;
    const usable = instances.filter((i) => (rhOnly ? i.placement === "cloud_runninghub" : true));
    const pick = usable.find((i) => i.lastProbeOk) ?? usable.find((i) => i.isDefault) ?? usable[0];
    if (pick) setInstanceId(pick.id);
  }, [instances, instanceId, rhOnly]);

  const chosen = instances.find((i) => i.id === instanceId) ?? null;
  const submittedId = mut.testRun.data?.id ?? null;
  const live = submittedId ? jobs?.find((j) => j.id === submittedId) ?? null : null;
  const keySlots = slots.filter((s) => s.required).slice(0, 6);
  const error = mut.testRun.error?.message ?? null;

  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
      <div className="space-y-3">
        <Field label="跑在哪台实例" hint={rhOnly ? "这份工作流含 RunningHub 专有节点，原生实例已置灰。" : "comfy_native 与 rh_task 的分派按协议走，不按服务商。"}>
          <Select value={instanceId} onChange={(e) => setInstanceId(e.target.value)} className="w-full">
            <option value="">选择实例</option>
            {instances.map((i) => (
              <option key={i.id} value={i.id} disabled={rhOnly && i.placement !== "cloud_runninghub"}>
                {i.name} · {i.protocol}
                {rhOnly && i.placement !== "cloud_runninghub" ? "（跑不了这份图）" : ""}
              </option>
            ))}
          </Select>
        </Field>

        {chosen && (
          <div className="flex flex-wrap items-center gap-2 text-caption text-ink-mute">
            <MachChip placement={chosen.placement} label={chosen.name} />
            <span className="mono">{chosen.baseUrl}</span>
            {chosen.site && <span className="mono">站点 {chosen.site}</span>}
            {chosen.instanceType && <span className="mono">档位 {chosen.instanceType}</span>}
            {chosen.quota?.concurrentLimit != null && (
              <span className="mono">
                并发 {chosen.quota.runningCount ?? 0}/{chosen.quota.concurrentLimit}
              </span>
            )}
            {chosen.quota?.remainMoney != null && <span className="mono">余额 {fmtMoney(chosen.quota.remainMoney)}</span>}
            {chosen.quota?.remainCoins != null && <span className="mono">{chosen.quota.remainCoins} RH币</span>}
            <span>{chosen.lastProbeOk ? "上次探活通过" : chosen.lastProbeOk === false ? "上次探活失败" : "还没探活"}</span>
          </div>
        )}

        {chosen?.protocol === "rh_task" && (
          <p className="rounded-tile border border-rule-soft bg-slate px-2.5 py-2 text-note leading-snug text-ink-mute">
            这台走 <span className="mono">rh_task</span>：任务状态里没有百分比，seed 会被平台强制重置（我们每次显式注入），
            结果链接约 1 天过期，完成即转存。试运行会真的占用实例并按秒计费。
          </p>
        )}

        <div className="space-y-1.5">
          <div className="label">关键槽位</div>
          {keySlots.length === 0 ? (
            <p className="text-note text-ink-mute">这份工作流没有必填槽位，直接派发即可。</p>
          ) : (
            keySlots.map((s) => (
              <div key={s.address} className="flex items-center gap-2">
                <span className="mono w-[150px] flex-none truncate text-caption text-ink-mute">{s.address}</span>
                <span className="w-[110px] flex-none truncate text-note">{s.name}</span>
                <div className="min-w-0 flex-1">
                  <SlotControl slot={s} raw={raws[s.address] ?? ""} onChange={(v) => setRaws((prev) => ({ ...prev, [s.address]: v }))} />
                </div>
              </div>
            ))
          )}
        </div>

        <p className="text-caption leading-snug text-ink-mute">
          <span className="mono">testRun</span> 只提交工作流 id 与实例两样东西：这一页填的值用来把 patch 和
          <span className="mono"> nodeInfoList</span> 算准，不会随试运行一起带过去，也没有写回工作流的接口。
        </p>

        <div className="flex items-center gap-2">
          <Button
            variant="primary"
            disabled={!instanceId}
            loading={mut.testRun.isPending}
            onClick={() => mut.testRun.mutate({ id: wf.id, instanceId })}
          >
            发起试运行
          </Button>
          <span className="text-caption text-ink-mute">类型记为「工作流试运行」，会出现在队列里</span>
        </div>

        {error && <p className="text-note text-state-fail">{error}</p>}
        {rhOnly && chosen && chosen.placement !== "cloud_runninghub" && (
          <p className="text-note text-state-fail">当前实例跑不了含 RunningHub 专有节点的图。</p>
        )}
      </div>

      <div className="space-y-2">
        <div className="label">这次试运行的任务</div>
        {!submittedId ? (
          <p className="rounded-tile border border-dashed border-rule px-2.5 py-4 text-note leading-snug text-ink-mute">
            还没发起。发起之后这里显示实时状态、日志和失败原因。
          </p>
        ) : !live ? (
          <p className="text-note text-ink-mute">已提交，任务记录 {submittedId}，等队列把它读出来。</p>
        ) : (
          <TestJobCard job={live} instance={chosen} onCancel={() => jobMut.cancel.mutate(live.id)} onRetry={() => jobMut.retry.mutate(live.id)} />
        )}
        {chosen?.protocol === "rh_task" && (
          <p className="text-caption leading-snug text-ink-mute">
            进度条不画是刻意的：<span className="mono">rh_task</span> 不给百分比，编一个数字只会误导你。
          </p>
        )}
      </div>
    </div>
  );
}

function TestJobCard({
  job,
  instance,
  onCancel,
  onRetry,
}: {
  job: Job;
  instance: GenInstance | null;
  onCancel: () => void;
  onRetry: () => void;
}) {
  const isLive = job.state === "running" || job.state === "queued" || job.state === "dispatching";
  return (
    <div className="space-y-2 rounded-tile border border-rule bg-slate p-2.5">
      <div className="flex flex-wrap items-center gap-2">
        <StateLabel state={job.state === "dispatching" ? "queued" : job.state} />
        <span className="mono text-caption text-ink-mute">{job.id}</span>
        {instance && <MachChip placement={instance.placement} label={instance.name} />}
        {job.queuePos != null && <span className="mono text-caption text-ink-mute">第 {job.queuePos} 位</span>}
        <span className="mono text-caption text-ink-mute">第 {job.attempts} 次</span>
      </div>
      <Progress
        value={job.progress.value}
        max={job.progress.max}
        unavailable={job.progress.unavailable}
        stripe={isLive}
        stage={job.progress.stage ?? (job.progress.nodeTitle ? `节点 ${job.progress.nodeTitle}` : null)}
      />
      <div className="mono break-all text-caption text-ink-mute">promptId {job.promptId ?? "—"}</div>
      {job.log.length > 0 && (
        <ul className="space-y-1 border-t border-rule-soft pt-1.5">
          {job.log.slice(-4).map((l, i) => (
            <li key={i} className="mono text-caption leading-snug text-ink-dim">
              <span className="text-ink-mute">{fmtTime(l.ts)}</span> {l.msg}
            </li>
          ))}
        </ul>
      )}
      {job.error && (
        <div className="space-y-1 border-t border-rule-soft pt-1.5">
          <div className="text-note text-ink">{job.error.message}</div>
          {job.error.hint && <div className="text-caption leading-snug text-ink-mute">处置：{job.error.hint}</div>}
        </div>
      )}
      <div className="flex items-center gap-2">
        {isLive && (
          <Button size="sm" variant="quiet" onClick={onCancel}>
            取消
          </Button>
        )}
        {(job.state === "failed" || job.state === "canceled") && (
          <Button size="sm" variant="default" onClick={onRetry}>
            再跑一次
          </Button>
        )}
        {job.state === "succeeded" && (
          <span className="text-note text-ink-dim">
            {job.outputMediaIds.length ? `产物 ${job.outputMediaIds.join("、")}` : "任务完成，没有留下产物记录"}
          </span>
        )}
      </div>
    </div>
  );
}

/* ───────── RunningHub 投影 ───────── */

function RhTab({
  wf,
  slots,
  typed,
  changed,
  raws,
  setRaws,
  rhOnly,
}: {
  wf: Workflow;
  slots: WorkflowSlot[];
  typed: Record<string, unknown>;
  changed: number;
  raws: Record<string, string>;
  setRaws: (fn: (prev: Record<string, string>) => Record<string, string>) => void;
  rhOnly: boolean;
}) {
  const api = useApi();
  const [rows, setRows] = useState<NodeOverride[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const typedKey = JSON.stringify(typed);

  useEffect(() => {
    let alive = true;
    setBusy(true);
    api.workflows
      .nodeOverrides(wf.id, typed)
      .then((r) => {
        if (!alive) return;
        setRows(r);
        setErr(null);
      })
      .catch((e: unknown) => alive && setErr((e as Error).message))
      .finally(() => alive && setBusy(false));
    return () => {
      alive = false;
    };
    // typedKey 是 typed 的稳定序列化，值没变就不会重复请求
  }, [api, wf.id, typedKey]);

  const seedSlots = slots.filter((s) => /seed/i.test(s.address) || /seed/i.test(s.rhFieldName ?? ""));
  const json = JSON.stringify({ nodeInfoList: rows ?? [] }, null, 2);

  return (
    <div className="space-y-3">
      {rhOnly && (
        <div className="flex items-center gap-2 rounded-tile border border-mach-rh/40 bg-slate px-2.5 py-2">
          <Badge tone="warn">仅 RunningHub</Badge>
          <span className="text-note leading-snug text-ink-dim">
            这份图的专有节点只有 RunningHub 有，所以只有 <span className="mono">rh_task</span> 这条路；换成走 /proxy 的原生实例也一样缺节点。
          </span>
        </div>
      )}

      <div className="rounded-tile border border-rule-soft bg-slate px-3 py-2.5 text-note leading-relaxed text-ink-dim">
        <div className="label mb-1">投影的三条硬规则</div>
        <ol className="list-decimal space-y-1 pl-4">
          <li>
            <span className="mono">fieldValue</span> 要保持线上原始类型：<span className="mono">864</span> 就是数字，不能写成{" "}
            <span className="mono">"864"</span>；bool 用 <span className="mono">true</span> 而不是 <span className="mono">"1"</span>。
          </li>
          <li>
            <span className="mono">inputs</span> 里的连接数组，比如 <span className="mono">[["7",0]]</span>，是连线不是入参，绝对不要写进{" "}
            <span className="mono">nodeInfoList</span>，也不要拿去当 <span className="mono">fieldName</span>。
          </li>
          <li>
            平台会强制重置 <span className="mono">seed</span>，除非你在 <span className="mono">nodeInfoList</span> 里显式注入。
            {seedSlots.length === 0 ? "这份工作流没有暴露 seed 槽位，试跑结果不可复现。" : "下面把 seed 槽位单独列出来了。"}
          </li>
        </ol>
        <ul className="mt-2 space-y-1 text-caption leading-snug text-ink-mute">
          {PROJECTION_CODES.map((code) => (
            <li key={code}>
              <span className="mono text-ink-dim">{code}</span> {RH_ERROR_HINTS[code]}
            </li>
          ))}
        </ul>
      </div>

      {seedSlots.length > 0 && (
        <div className="space-y-1.5">
          <div className="label">显式注入 seed</div>
          {seedSlots.map((s) => (
            <div key={s.address} className="flex items-center gap-2">
              <span className="mono w-[160px] flex-none truncate text-caption text-ink-mute">{s.address}</span>
              <Input
                value={raws[s.address] ?? ""}
                onChange={(e) => setRaws((prev) => ({ ...prev, [s.address]: e.target.value }))}
                inputMode="numeric"
                type="number"
                placeholder={String(s.default ?? 0)}
                className="h-6 w-[160px]"
              />
              <span className="text-caption text-ink-mute">{raws[s.address] ? "会进 nodeInfoList" : "留空 = 不注入 = 被平台重置"}</span>
            </div>
          ))}
        </div>
      )}

      <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_260px]">
        <div className="space-y-1.5">
          <div className="flex items-baseline justify-between gap-2">
            <span className="label">nodeInfoList</span>
            <span className="text-caption text-ink-mute">{busy ? "正在投影" : `${rows?.length ?? 0} 项`}</span>
          </div>
          {err ? (
            <p className="text-note text-state-fail">投影失败：{err}</p>
          ) : (
            <pre className="mono max-h-72 overflow-auto rounded-tile bg-slate p-2.5 text-caption leading-[1.55] text-ink-dim">{json}</pre>
          )}
          <Copyable text={json} className="text-caption">
            复制这段 nodeInfoList
          </Copyable>
        </div>

        <div className="space-y-1.5">
          <span className="label">怎么读这段</span>
          <ul className="space-y-1 text-note leading-snug text-ink-mute">
            <li>只有填了值的槽位才会出现；未填的走工作流里存着的默认值。</li>
            <li>当前 <span className="mono">changed = {changed}</span>，投影 <span className="mono">{rows?.length ?? 0}</span> 项。</li>
            <li>
              <span className="mono">nodeId</span> 取地址点号前那段；子图地址（<span className="mono">parent/child.x</span>）要人工确认。
            </li>
            <li>RunningHub 的 <span className="mono">/proxy</span> 走原生 <span className="mono">/prompt</span>，不需要这段投影。</li>
          </ul>
          <div className="pt-1">
            <Button size="sm" variant="quiet" disabled={changed === 0} onClick={() => setRaws(() => ({}))}>
              清空本页填的值
            </Button>
            <span className="ml-2 text-caption text-ink-mute">已填 {changed} 项</span>
          </div>
        </div>
      </div>
    </div>
  );
}

/* ───────── 导出 ───────── */

function ExportTab({ wf }: { wf: Workflow }) {
  const api = useApi();
  const [fmt, setFmt] = useState<"api" | "ui">("api");
  const [text, setText] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setText(null);
    api.workflows
      .export(wf.id, fmt)
      .then((t) => alive && setText(t))
      .catch((e: unknown) => alive && setErr((e as Error).message));
    return () => {
      alive = false;
    };
  }, [api, wf.id, fmt]);

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        {(["api", "ui"] as const).map((f) => (
          <button
            key={f}
            onClick={() => setFmt(f)}
            className={cn(
              "rounded-ctl border px-2.5 py-1 text-note",
              fmt === f ? "border-transparent bg-ink text-slate" : "border-rule bg-raised text-ink-dim hover:text-ink",
            )}
          >
            {f === "api" ? "API 格式" : "UI 格式"}
          </button>
        ))}
        <span className="text-caption text-ink-mute">
          工作流本身是 <span className="mono">{wf.sourceFormat}</span> 格式存的
        </span>
      </div>

      <p className="text-note leading-snug text-ink-mute">
        <span className="mono">POST /prompt</span> 只接受 API 格式。UI 格式是 ComfyUI 前端的画布数据（nodes/links/groups），
        只能在 ComfyUI 里打开和编辑，直接提交会失败。
      </p>

      {err ? (
        <p className="text-note text-state-fail">取导出内容失败：{err}</p>
      ) : text === null ? (
        <p className="text-note text-ink-mute">正在取 {fmt === "api" ? "API" : "UI"} 格式内容。</p>
      ) : (
        <div className="space-y-1.5">
          <div className="flex items-center justify-between gap-2">
            <span className="label mono">{fmt === "api" ? `${wf.id}.api.json` : `${wf.id}.ui.json`}</span>
            <Copyable text={text}>{text.length > 4000 ? "复制整段（很长）" : "复制整段"}</Copyable>
          </div>
          <pre className="mono max-h-[420px] overflow-auto rounded-tile border border-rule-soft bg-slate p-2.5 text-caption leading-[1.55] text-ink-dim">
            {text}
          </pre>
        </div>
      )}
    </div>
  );
}

/** 投影这一页最常撞上的 RunningHub 错误码，文案来自 constants 的映射表 */
const PROJECTION_CODES = ["803", "805", "810", "435"];

/* ───────── 地址解析与值转换 ───────── */

type ParsedAddress = { node: string; key: string; kind: "named" | "index" | "wildcard" | "nested" };

function parseAddress(addr: string): ParsedAddress {
  if (addr.startsWith("*:")) return { node: "*", key: addr.slice(2), kind: "wildcard" };
  const dot = addr.indexOf(".");
  if (dot < 0) return { node: addr, key: "", kind: "named" };
  const head = addr.slice(0, dot);
  const rest = addr.slice(dot + 1);
  if (head.includes("/")) return { node: head, key: rest, kind: "nested" };
  if (/^\d+$/.test(rest)) return { node: head, key: rest, kind: "index" };
  return { node: head, key: rest, kind: "named" };
}

function coerce(slot: WorkflowSlot, raw: string): unknown {
  if (raw === "") return undefined;
  if (slot.type === "int") {
    const n = Math.round(Number(raw));
    return Number.isFinite(n) ? n : undefined;
  }
  if (slot.type === "float") {
    const n = Number(raw);
    return Number.isFinite(n) ? n : undefined;
  }
  if (slot.type === "bool") return raw === "true";
  return raw;
}

function typedValues(slots: WorkflowSlot[], raws: Record<string, string>): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const s of slots) {
    const v = coerce(s, raws[s.address] ?? "");
    if (v !== undefined) out[s.address] = v;
  }
  return out;
}

/** 槽位值转成给 comfy_native 的 API 图 patch：{ nodeId: { inputs: { key: value } } } */
function apiPatch(slots: WorkflowSlot[], raws: Record<string, string>): Record<string, Record<string, unknown>> {
  const grouped: Record<string, Record<string, unknown>> = {};
  for (const s of slots) {
    const raw = raws[s.address] ?? "";
    const value = coerce(s, raw);
    if (value === undefined) continue;
    const { node, key } = parseAddress(s.address);
    const bucket = grouped[node] ?? (grouped[node] = {});
    if (key) bucket[key] = value;
    else bucket[s.address] = value;
  }
  const out: Record<string, Record<string, unknown>> = {};
  for (const [node, inputs] of Object.entries(grouped)) {
    if (node === "*") out["*:同名控件全部替换"] = inputs;
    else out[node] = { inputs };
  }
  return out;
}
