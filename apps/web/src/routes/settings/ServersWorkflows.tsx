import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Download, Pencil, PlugZap, Plus, RefreshCw, Server, ShieldCheck, Star, Trash2, Upload, Wand2 } from "lucide-react";
import { Badge, Button, Empty, Field, Input, KeyVal, Modal, Panel, Select, StateGlyph, Tabs, Toggle } from "../../components/ui";
import { ImportWizard, ReplaceJsonModal } from "../../components/WorkflowImportWizard";
import WorkflowInspector from "../../components/WorkflowInspector";
import { useApi } from "../../lib/apiClient";
import { useInstanceLiveness, useInstanceMutations, useInstances, useWorkflowBindings, useWorkflowMutations, useWorkflows } from "../../lib/hooks";
import type { GenInstance, InstanceCaps, ModelSlot, PingResult, ProbeReport, SyncTargetResult, Workflow, WorkflowCheckReport } from "../../lib/types";
import { cn, fmtBytes, fmtTime, redactKeyUrl } from "../../lib/utils";

/*
 * 设置 · Server 与工作流。
 *
 * 这一页管两件事，而且它们是同一件事的两面：工作流库里的图是"通用"的，
 * 但跑图的机器各有各的权重目录、各有各的显存，所以「这条工作流在这台机器上用哪个底模」
 * 必须能按机器分别记下来 —— 这就是绑定，也是这一页存在的理由。
 *
 * 三条纪律：
 * ① 能换哪些权重只问实例的 /object_info，前端不写死任何文件名；
 * ② 认不出同族同模式的权重就留空并说明，绝不"就近凑一个"（凑错过一次，代价是一整轮看不懂的采样错）；
 * ③ 在线状态用的是刚问出来的，不是几十分钟前那次探活的结果。
 */

type TabKey = "servers" | "workflows";

const PROTOCOL_LABEL: Record<GenInstance["protocol"], string> = {
  comfy_native: "原生 ComfyUI",
  rh_task: "RunningHub 任务 API",
};

export default function ServersWorkflows() {
  // 侧栏「工作流库」与老书签都从 ?tab= 进来，所以初始页签要跟着地址走
  const [params] = useSearchParams();
  const [tab, setTab] = useState<TabKey>(params.get("tab") === "workflows" ? "workflows" : "servers");
  useEffect(() => {
    if (params.get("tab") === "workflows") setTab("workflows");
  }, [params]);
  const { data: instances } = useInstances();
  const { data: workflows } = useWorkflows();

  return (
    <div className="space-y-4 p-4">
      <header className="space-y-2">
        <h1 className="text-title font-semibold tracking-tight">Server 与工作流</h1>
        <p className="text-note text-ink-mute">
          管生成机器，也管工作流库在这每台机器上默认用哪些权重。分派按<span className="text-ink-dim">协议</span>走，不按服务商走。
        </p>
        <Tabs
          value={tab}
          onChange={setTab}
          tabs={[
            { key: "servers", label: "Server 管理", badge: instances?.length ? <span className="mono text-caption text-ink-mute">{instances.length}</span> : undefined },
            { key: "workflows", label: "工作流管理", badge: workflows?.length ? <span className="mono text-caption text-ink-mute">{workflows.length}</span> : undefined },
          ]}
        />
      </header>
      {tab === "servers" ? <ServerTab /> : <WorkflowTab />}
    </div>
  );
}

/* ───────── Server 管理 ───────── */

function ServerTab() {
  const { data: instances } = useInstances();
  const { data: liveness } = useInstanceLiveness();
  const mut = useInstanceMutations();
  const [addOpen, setAddOpen] = useState(false);
  const [editing, setEditing] = useState<GenInstance | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<GenInstance | null>(null);
  const [report, setReport] = useState<{ name: string; r: ProbeReport } | null>(null);
  const [checkedAt, setCheckedAt] = useState<string>("");

  const list = instances ?? [];
  useEffect(() => {
    if (liveness) setCheckedAt(new Date().toISOString());
  }, [liveness]);

  async function probeAll() {
    for (const i of list) {
      try {
        setReport({ name: i.name, r: await mut.probe.mutateAsync(i.id) });
      } catch {
        // 单台失败不影响其余：错误已经写进 lastError，行上会显示
      }
    }
  }

  return (
    <div className="space-y-3">
      <Panel
        title={
          <span className="flex items-center gap-2">
            <Server className="h-3.5 w-3.5 text-ink-mute" aria-hidden />
            ComfyUI Server 实例
            <span className="text-caption font-normal text-ink-mute">
              每 12 秒在线探测 · 上次 {checkedAt ? fmtTime(checkedAt) : "—"}
            </span>
          </span>
        }
        actions={
          <>
            <Button size="sm" variant="quiet" icon={<PlugZap className="h-3 w-3" />} onClick={probeAll} loading={mut.probe.isPending}>
              全部完整探活
            </Button>
            <Button size="sm" variant="primary" icon={<Plus className="h-3 w-3" />} onClick={() => setAddOpen(true)}>
              添加 Server
            </Button>
          </>
        }
        dense
      >
        {list.length === 0 ? (
          <div className="p-3">
            <Empty
              title="还没有登记任何生成实例"
              hint="最省事的开始方式是添加本机 ComfyUI（默认 http://127.0.0.1:8188）。"
              action={
                <Button size="sm" onClick={() => setAddOpen(true)}>
                  添加第一个实例
                </Button>
              }
            />
          </div>
        ) : (
          <ul className="divide-y divide-rule-soft">
            {list.map((i) => (
              <ServerRow
                key={i.id}
                inst={i}
                live={liveness?.[i.id] ?? null}
                onProbe={() => mut.probe.mutateAsync(i.id).then((r) => setReport({ name: i.name, r }))}
                onEdit={() => setEditing(i)}
                onDelete={() => setConfirmDelete(i)}
                onToggleDefault={() => mut.update.mutate({ id: i.id, body: { isDefault: !i.isDefault } })}
              />
            ))}
          </ul>
        )}
      </Panel>

      <Panel title="为什么浏览器不直连这些地址">
        <ul className="list-disc space-y-1 pl-4 text-note leading-relaxed text-ink-dim">
          <li>ComfyUI 开源版没有任何鉴权，默认只有「同站点」防护；一旦为前端开 <span className="mono">--enable-cors-header</span> 就等于把它暴露给任意网页。</li>
          <li>RunningHub 的 apiKey 在 URL 路径里（<span className="mono">/proxy/&#123;key&#125;</span>），绝不能进前端包、日志或报错栈。</li>
          <li>取消、重试、配额、成本记账与「这台能不能跑这条图」的判断，都只能在服务端统一做。</li>
        </ul>
      </Panel>

      <ServerFormModal open={addOpen || !!editing} inst={editing ?? undefined} onClose={() => { setAddOpen(false); setEditing(null); }} />

      <Modal
        open={!!confirmDelete}
        onClose={() => setConfirmDelete(null)}
        title="删除这台 Server"
        width={440}
        footer={
          <>
            <Button variant="quiet" onClick={() => setConfirmDelete(null)}>
              取消
            </Button>
            <Button
              variant="danger"
              loading={mut.remove.isPending}
              onClick={() => {
                if (!confirmDelete) return;
                mut.remove.mutate(confirmDelete.id, { onSuccess: () => setConfirmDelete(null) });
              }}
            >
              确认删除
            </Button>
          </>
        }
      >
        <p className="text-note leading-relaxed text-ink-dim">
          删掉 <b>{confirmDelete?.name}</b> 会连带清掉它上面配的<b>按实例默认权重绑定</b>（外键级联），历史任务与产物保留。
          如果它上面还有没收口的任务，后端会拒绝删除并告诉你有几个。
        </p>
      </Modal>

      {report && <ProbeModal name={report.name} report={report.r} onClose={() => setReport(null)} />}
    </div>
  );
}

function ServerRow({
  inst,
  live,
  onProbe,
  onEdit,
  onDelete,
  onToggleDefault,
}: {
  inst: GenInstance;
  live: PingResult | null;
  onProbe: () => void;
  onEdit: () => void;
  onDelete: () => void;
  onToggleDefault: () => void;
}) {
  const [open, setOpen] = useState(false);
  const caps = inst.capabilities as InstanceCaps | undefined;
  // 在线状态优先用刚问出来的那一把；ping 还没回来时按上次的探活结果，并说清是哪一种
  const online = live ? live.ok : inst.lastProbeOk === true;
  const busy = !!live && live.running > 0;
  return (
    <li className="px-3 py-2.5">
      <div className="flex flex-wrap items-center gap-2">
        <button onClick={() => setOpen((v) => !v)} className="flex min-w-0 flex-1 items-center gap-2 text-left" aria-expanded={open}>
          <StateGlyph state={live === null ? "idle" : online ? "succeeded" : "failed"} />
          <span className="truncate text-body font-medium">{inst.name}</span>
          <Badge>{inst.placement === "local" ? "本地" : inst.placement === "cloud_self" ? "自建云" : "RunningHub"}</Badge>
          {inst.isDefault && <Badge tone="good">默认</Badge>}
          {busy && <Badge tone="warn">在跑 {live?.running}</Badge>}
          <span className="mono truncate text-caption text-ink-mute">
            {redactKeyUrl(inst.baseUrl)} · {live === null ? (online ? "在线（上次探活）" : "离线（上次探活）") : online ? "在线" : "离线"}
            {live && online ? ` · 排队 ${live.queued}` : ""}
          </span>
        </button>
        <div className="flex flex-none items-center gap-2">
          <Button size="sm" variant="quiet" onClick={onProbe} title="完整探活：拉一次 /object_info，认节点与权重清单">
            测试连接
          </Button>
          <Button size="sm" variant="quiet" icon={<Pencil className="h-3 w-3" />} onClick={onEdit}>
            编辑
          </Button>
          {!inst.isDefault && (
            <Button size="sm" variant="quiet" onClick={onToggleDefault} title="新任务默认派到这台">
              设为默认
            </Button>
          )}
          <Button size="sm" variant="danger" icon={<Trash2 className="h-3 w-3" />} onClick={onDelete}>
            删除
          </Button>
        </div>
      </div>

      {!online && inst.lastError && <div className="mono mt-1 pl-5 text-caption leading-snug text-state-fail">{live?.error ?? inst.lastError}</div>}

      {open && (
        <div className="mt-2 space-y-2 rounded-tile border border-rule-soft bg-slate px-3 py-2.5">
          <KeyVal
            items={[
              ["协议", PROTOCOL_LABEL[inst.protocol]],
              ["站点", inst.placement === "cloud_runninghub" ? (inst.site === "global" ? "global 站" : "cn 站") : "—"],
              ["apiKey", inst.apiKeySet ? "已配置" : inst.placement === "cloud_runninghub" ? "未配置" : "不需要"],
              ["产物目录", inst.localOutputRoot ?? "（走网络下载）"],
              ["上次探活", fmtTime(inst.lastProbeAt)],
            ]}
          />
          {caps?.nodeCount ? (
            <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-caption text-ink-mute">
              <span>
                ComfyUI <span className="mono text-ink-dim">{caps.comfyVersion ?? "未知"}</span>
              </span>
              <span>
                节点 <span className="mono text-ink-dim">{caps.nodeCount}</span>
              </span>
              {caps.gpu && (
                <span>
                  {caps.gpu} · 显存 <span className="mono text-ink-dim">{caps.vramFreeGb?.toFixed(1) ?? "?"}/{caps.vramTotalGb?.toFixed(0) ?? "?"} GB</span>
                </span>
              )}
              <span className={cn(caps.h3?.MiniMaxH3ImageToVideo ? "text-state-ok" : "text-ink-mute")}>
                H3 节点 {caps.h3?.MiniMaxH3ImageToVideo ? "在" : "不在"}
              </span>
            </div>
          ) : (
            <p className="text-caption text-ink-mute">还没有完整探活过：上面这行节点数与显存要点了「测试连接」才有。</p>
          )}
          {!!caps?.missingModels?.length && (
            <div className="rounded-ctl border border-state-warn/40 bg-state-warn/5 px-2 py-1.5 text-caption leading-snug">
              <div className="text-state-warn">实例可用，但缺 {caps.missingModels.length} 个权重文件：</div>
              <ul className="mono mt-1 space-y-0.5 text-ink-dim">
                {caps.missingModels.map((m) => (
                  <li key={m}>{m}</li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </li>
  );
}

function ProbeModal({ name, report, onClose }: { name: string; report: ProbeReport; onClose: () => void }) {
  return (
    <Modal open onClose={onClose} title={`连接结果 · ${name}`} width={600} footer={<Button variant="primary" onClick={onClose}>关闭</Button>}>
      <div className="space-y-3">
        <div className={cn("rounded-ctl border px-2.5 py-2 text-note", report.ok ? "border-state-ok/40 bg-state-ok/8" : "border-state-fail/40 bg-state-fail/8")}>
          {report.ok ? "连上了，可以派发任务。" : report.error ?? "连不上"}
        </div>
        {report.native && (
          <KeyVal
            items={[
              ["ComfyUI 版本", report.native.comfyVersion ?? "—"],
              ["节点数", String(report.native.nodeCount ?? "—")],
              ["GPU", report.native.gpu ?? "—"],
              ["显存", report.native.vramTotalGb ? `${report.native.vramTotalGb.toFixed(1)} GB` : "—"],
              ["缺失权重", report.native.missingModels?.length ? report.native.missingModels.join("、") : "无"],
            ]}
          />
        )}
        {report.hints?.length ? (
          <ul className="space-y-1 rounded-ctl border border-rule-soft bg-slate p-2.5 text-note leading-snug text-ink-dim">
            {report.hints.map((h) => (
              <li key={h}>· {h}</li>
            ))}
          </ul>
        ) : null}
      </div>
    </Modal>
  );
}

const SERVER_PRESETS = [
  { key: "local", label: "本机 ComfyUI", protocol: "comfy_native", placement: "local", url: "http://127.0.0.1:8188" },
  { key: "self", label: "自建云端（cloudflared 隧道）", protocol: "comfy_native", placement: "cloud_self", url: "https://" },
  { key: "rhproxy", label: "RunningHub 原生代理", protocol: "comfy_native", placement: "cloud_runninghub", url: "https://www.runninghub.cn/proxy/" },
] as const;

/** 新建与编辑共用一个弹窗。编辑时地址与 key 都可能"没动"，那就要原样留着后端那一份 */
function ServerFormModal({ open, inst, onClose }: { open: boolean; inst?: GenInstance; onClose: () => void }) {
  const api = useApi();
  const mut = useInstanceMutations();
  const editing = !!inst;
  const [protocol, setProtocol] = useState<GenInstance["protocol"]>("comfy_native");
  const [placement, setPlacement] = useState<GenInstance["placement"]>("local");
  const [name, setName] = useState("");
  const [baseUrl, setBaseUrl] = useState("http://127.0.0.1:8188");
  const [apiKey, setApiKey] = useState("");
  const [clearKey, setClearKey] = useState(false);
  const [site, setSite] = useState<"cn" | "global">("cn");
  const [instanceType, setInstanceType] = useState<"default" | "plus" | "ultra">("default");
  const [localOutputRoot, setLocalOutputRoot] = useState("");
  const [isDefault, setIsDefault] = useState(false);
  const [dry, setDry] = useState<{ loading: boolean; r: ProbeReport | null }>({ loading: false, r: null });
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setError(null);
    setDry({ loading: false, r: null });
    setClearKey(false);
    setApiKey("");
    if (inst) {
      setProtocol(inst.protocol);
      setPlacement(inst.placement);
      setName(inst.name);
      setBaseUrl(inst.baseUrl);
      setSite(inst.site ?? "cn");
      setInstanceType(inst.instanceType ?? "default");
      setLocalOutputRoot(inst.localOutputRoot ?? "");
      setIsDefault(inst.isDefault);
    } else {
      setProtocol("comfy_native");
      setPlacement("local");
      setName("");
      setBaseUrl("http://127.0.0.1:8188");
      setSite("cn");
      setInstanceType("default");
      setLocalOutputRoot("");
      setIsDefault(false);
    }
  }, [open, inst]);

  const isRh = placement === "cloud_runninghub";
  // 列表里那串是脱敏后的展示形式，原样提交后端会认出来并当成"没改"
  const urlUntouched = editing && baseUrl === inst?.baseUrl;

  async function save() {
    setError(null);
    const body: Record<string, unknown> = {
      name: name.trim(),
      protocol,
      placement,
      baseUrl: baseUrl.trim(),
      site: isRh ? site : undefined,
      instanceType: isRh ? instanceType : undefined,
      localOutputRoot: placement === "local" ? localOutputRoot || null : null,
      isDefault,
    };
    // apiKey 的三种意思必须分开：没填 = 不动、填了 = 换成这把、勾了清掉 = 显式 null
    if (apiKey.trim()) body.apiKey = apiKey.trim();
    else if (clearKey) body.apiKey = null;
    if (urlUntouched) delete body.baseUrl;
    try {
      if (editing) await mut.update.mutateAsync({ id: inst!.id, body: body as Partial<GenInstance> });
      else await mut.create.mutateAsync(body as Parameters<typeof api.instances.create>[0]);
      onClose();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={editing ? `编辑 Server · ${editing ? inst?.name : ""}` : "添加 Server"}
      width={680}
      footer={
        <>
          <Button variant="quiet" onClick={onClose}>
            取消
          </Button>
          <Button
            variant="quiet"
            loading={dry.loading}
            onClick={async () => {
              setDry({ loading: true, r: null });
              try {
                const r = await api.instances.dryProbe({ protocol, baseUrl: baseUrl.trim(), apiKey: apiKey.trim() || undefined, site });
                setDry({ loading: false, r });
              } catch (e) {
                setDry({ loading: false, r: { ok: false, error: (e as Error).message, hints: [] } });
              }
            }}
          >
            先试连通
          </Button>
          <Button variant="primary" disabled={!name.trim() || !baseUrl.trim()} loading={mut.create.isPending || mut.update.isPending} onClick={save}>
            保存
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        {!editing && (
          <Field label="接法">
            <div className="grid grid-cols-3 gap-1.5">
              {SERVER_PRESETS.map((p) => (
                <button
                  key={p.key}
                  onClick={() => {
                    setProtocol(p.protocol);
                    setPlacement(p.placement);
                    setBaseUrl(p.url);
                  }}
                  className={cn(
                    "rounded-ctl border px-2 py-1.5 text-left text-note transition-colors",
                    protocol === p.protocol && placement === p.placement ? "border-ink-dim bg-raised text-ink" : "border-rule-soft bg-slate text-ink-dim hover:bg-raised",
                  )}
                >
                  {p.label}
                </button>
              ))}
            </div>
          </Field>
        )}

        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="显示名">
            <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="本机 4090" />
          </Field>
          <Field label="位置" hint="决定这条工作流能不能派到它身上。">
            <Select value={placement} onChange={(e) => setPlacement(e.target.value as GenInstance["placement"])} className="w-full">
              <option value="local">本机</option>
              <option value="cloud_self">自建云端</option>
              <option value="cloud_runninghub">RunningHub</option>
            </Select>
          </Field>
        </div>

        <Field
          label="ComfyUI 地址"
          hint={
            isRh
              ? "原生代理的 key 拼在路径里：https://www.runninghub.cn/proxy/{apiKey}。这个地址属于凭据，列表与日志里都会脱敏显示。"
              : placement === "cloud_self"
                ? "远端 ComfyUI 只绑 loopback，由 cloudflared 转发；隧道 config 里要设 originRequest.httpHostHeader: 127.0.0.1:8188。"
                : "本机 ComfyUI 不要加 --enable-cors-header：我们走服务端调用，开了反而拆掉同站点防护。"
          }
        >
          <Input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} className="mono" />
        </Field>
        {urlUntouched && (
          <p className="text-caption leading-snug text-ink-mute">
            地址框里是脱敏后的展示形式。保持不动就等于不改地址；要换就填完整真实地址。
          </p>
        )}

        {isRh && (
          <>
            <Field label="apiKey" hint="在 RunningHub 控制台的 API 调用页取。cn 站的 key 在 global 站不能用，两站余额与素材互不通用。">
              <Input type="password" value={apiKey} onChange={(e) => setApiKey(e.target.value)} className="mono" placeholder={inst?.apiKeySet ? "留空表示不换已存的那把" : "留空表示稍后再填"} />
            </Field>
            {inst?.apiKeySet && (
              <Toggle checked={clearKey} onChange={setClearKey} label="清掉已存的 apiKey" hint="只在你要换成另一把或暂时收回去时用。" />
            )}
            <div className="grid grid-cols-2 gap-3">
              <Field label="站点">
                <Select value={site} onChange={(e) => setSite(e.target.value as "cn" | "global")}>
                  <option value="cn">www.runninghub.cn</option>
                  <option value="global">www.runninghub.ai</option>
                </Select>
              </Field>
              <Field label="显存档位">
                <Select value={instanceType} onChange={(e) => setInstanceType(e.target.value as "default" | "plus" | "ultra")}>
                  <option value="default">default · 24G</option>
                  <option value="plus">plus · 48G</option>
                  <option value="ultra">ultra · 84G</option>
                </Select>
              </Field>
            </div>
          </>
        )}

        {placement === "local" && (
          <Field label="ComfyUI output 目录（可选）" hint="填了就直接从磁盘搬产物，省一次网络下载；权重体检也靠它认出半截下载。">
            <Input value={localOutputRoot} onChange={(e) => setLocalOutputRoot(e.target.value)} className="mono" placeholder="F:/H3/comfyui/ComfyUI/output" />
          </Field>
        )}

        <Toggle checked={isDefault} onChange={setIsDefault} label="设为默认执行实例" hint="新任务没指定实例时派到这台。" />

        {error && <p className="text-note text-state-fail">保存失败：{error}</p>}
        {dry.r && (
          <div className={cn("rounded-ctl border px-2.5 py-2 text-note", dry.r.ok ? "border-state-ok/40" : "border-state-fail/40")}>
            {dry.r.ok ? "连通" : dry.r.error ?? "连不上"}
            {dry.r.hints?.map((h) => (
              <div key={h} className="mt-1 text-ink-mute">
                · {h}
              </div>
            ))}
          </div>
        )}
      </div>
    </Modal>
  );
}

/* ───────── 工作流管理 ───────── */

/** 体检结论只活在页面这次会话里：它是「点下检查那一秒」的事实，实例上的东西随时会变 */
type CheckState = { status: "checking" | "done" | "failed"; report?: WorkflowCheckReport[]; error?: string };

function WorkflowTab() {
  const api = useApi();
  const { data: list } = useWorkflows();
  const { data: instances } = useInstances();
  const [importOpen, setImportOpen] = useState(false);
  const [syncAllOpen, setSyncAllOpen] = useState(false);
  const [modelFor, setModelFor] = useState<Workflow | null>(null);
  const [checkFor, setCheckFor] = useState<Workflow | null>(null);
  const [syncFor, setSyncFor] = useState<Workflow | null>(null);
  const [replaceFor, setReplaceFor] = useState<Workflow | null>(null);
  const [detail, setDetail] = useState<Workflow | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<Workflow | null>(null);
  const [checks, setChecks] = useState<Record<string, CheckState>>({});
  const wfMut = useWorkflowMutations();
  const [msg, setMsg] = useState("");

  const workflows = useMemo(() => (list ?? []).filter((w) => !w.isBuiltin), [list]);
  const builtins = useMemo(() => (list ?? []).filter((w) => w.isBuiltin), [list]);
  const insts = instances ?? [];

  // 每个任务种类里优先级最高的那条就是「默认」—— 自动选真就是按这个顺序挑的
  const topByKind = useMemo(() => {
    const out: Record<string, number> = {};
    for (const w of workflows) {
      const kind = w.taskKind ?? w.family;
      out[kind] = Math.max(out[kind] ?? -1, w.priority ?? 100);
    }
    return out;
  }, [workflows]);

  const groups = useMemo(() => {
    const map = new Map<string, { label: string; items: Workflow[] }>();
    for (const w of workflows) {
      const key = w.mode ?? w.taskKind ?? w.family;
      if (!map.has(key)) map.set(key, { label: w.modeLabel ?? key, items: [] });
      map.get(key)!.items.push(w);
    }
    return [...map.entries()].sort((a, b) => b[1].items.length - a[1].items.length);
  }, [workflows]);

  function runCheck(w: Workflow, instanceIds?: string[]) {
    setChecks((c) => ({ ...c, [w.id]: { status: "checking" } }));
    wfMut.check.mutate({ id: w.id, instanceIds }, {
      onSuccess: (r) => setChecks((c) => ({ ...c, [w.id]: { status: "done", report: r.reports } })),
      onError: (e) => setChecks((c) => ({ ...c, [w.id]: { status: "failed", error: (e as Error).message } })),
    });
  }

  async function download(w: Workflow) {
    setMsg(`正在取 ${w.name} 的 API 导出…`);
    try {
      const text = await apiExport(w.id);
      const blob = new Blob([text], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${w.name}_api.json`;
      a.click();
      URL.revokeObjectURL(url);
      setMsg(`已导出 ${w.name}（API 格式，${fmtBytes(text.length)}）`);
    } catch (e) {
      setMsg(`导出失败：${(e as Error).message}`);
    }
  }

  async function apiExport(id: string) {
    return api.workflows.export(id, "api");
  }

  function setDefault(w: Workflow) {
    const kind = w.taskKind ?? w.family;
    const next = (topByKind[kind] ?? 100) + 10;
    wfMut.patch.mutate({ id: w.id, body: { priority: next, autoSelect: true } }, {
      onSuccess: () => setMsg(`「${w.name}」现在是${w.modeLabel ?? kind}这一类里优先级最高的（${next}），自动选会先挑它`),
      onError: (e) => setMsg(`设默认失败：${(e as Error).message}`),
    });
  }

  return (
    <div className="space-y-3">
      <Panel
        title={
          <span className="flex items-center gap-2">
            工作流库
            <span className="text-caption font-normal text-ink-mute">共 {workflows.length} 个 · 导入后自动识别模式与权重位</span>
          </span>
        }
        actions={
          <>
            <Button
              size="sm"
              variant="quiet"
              icon={<RefreshCw className="h-3 w-3" />}
              disabled={insts.length < 2}
              title={insts.length < 2 ? "至少要登记两台 ComfyUI 才谈得上跨服务器对齐" : "库里每条按同一套规则搬到别处"}
              onClick={() => setSyncAllOpen(true)}
            >
              整库对齐
            </Button>
            <Button size="sm" variant="primary" icon={<Upload className="h-3 w-3" />} onClick={() => setImportOpen(true)}>
              导入工作流（JSON）
            </Button>
          </>
        }
        dense
      >
        <div className="border-b border-rule-soft px-3 py-2 text-note leading-snug text-ink-mute">
          卡片上的「模型」按 Server 存默认权重：本机用 4step、自建 48G 用 8step，互不影响。生成派到那台时自动套用，任务里临时挑的那一份优先。
        </div>
        {groups.length === 0 ? (
          <div className="p-3">
            <Empty title="库里还没有导入的工作流" hint="点右上角导入一份 ComfyUI 导出（API 版或画布版都收）。" />
          </div>
        ) : (
          groups.map(([key, g]) => (
            <section key={key} className="border-b border-rule-soft px-3 py-3 last:border-b-0">
              <div className="mb-2 flex items-baseline justify-between">
                <span className="label-mono">{g.label}</span>
                <span className="text-caption text-ink-mute">{g.items.length} 个工作流</span>
              </div>
              <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
                {g.items.map((w) => (
                  <WorkflowCard
                    key={w.id}
                    wf={w}
                    isDefault={(w.priority ?? 100) >= (topByKind[w.taskKind ?? w.family] ?? 100)}
                    instances={insts}
                    check={checks[w.id]}
                    busy={wfMut.patch.isPending}
                    onCheck={() => {
                      // 点「检查」先看结果：没有结果才当场问一次，有就直接开弹窗（里面有重新检测）
                      setCheckFor(w);
                      if (!checks[w.id]) runCheck(w);
                    }}
                    onModels={() => setModelFor(w)}
                    onSync={() => setSyncFor(w)}
                    onReplace={() => setReplaceFor(w)}
                    onDownload={() => download(w)}
                    onSetDefault={() => setDefault(w)}
                    onDelete={() => setConfirmDelete(w)}
                    onDetail={() => setDetail(w)}
                  />
                ))}
              </div>
            </section>
          ))
        )}
      </Panel>

      {builtins.length > 0 && (
        <Panel
          dense
          title={
            <span className="flex items-center gap-2">
              内置模板
              <span className="text-caption font-normal text-ink-mute">图在服务端现拼，所以权重位同样能按 Server 绑</span>
            </span>
          }
        >
          <div className="grid gap-2 p-3 sm:grid-cols-2 xl:grid-cols-3">
            {builtins.map((w) => (
              <WorkflowCard
                key={w.id}
                wf={w}
                instances={insts}
                check={checks[w.id]}
                onCheck={() => {
                  setCheckFor(w);
                  if (!checks[w.id]) runCheck(w);
                }}
                onModels={() => setModelFor(w)}
                onSync={() => setSyncFor(w)}
                onDownload={() => setMsg("内置模板没有存盘的 JSON：它是按任务参数现拼的")}
                onDelete={() => undefined}
                onDetail={() => setDetail(w)}
              />
            ))}
          </div>
        </Panel>
      )}

      {msg && <p className="px-1 text-caption text-ink-mute">{msg}</p>}

      <ImportWizard open={importOpen} onClose={() => setImportOpen(false)} instances={insts} onImported={() => setImportOpen(false)} />
      {modelFor && <ModelBindingModal wf={modelFor} instances={insts} onClose={() => setModelFor(null)} />}
      {checkFor && <CheckModal wf={checkFor} state={checks[checkFor.id]} instances={insts} onClose={() => setCheckFor(null)} onRecheck={runCheck} />}
      {syncFor && <SyncBindingsModal wf={syncFor} instances={insts} onClose={() => setSyncFor(null)} />}
      {syncAllOpen && <SyncAllModal instances={insts} onClose={() => setSyncAllOpen(false)} />}
      {detail && (
        <Modal
          open
          onClose={() => setDetail(null)}
          title={`工作流详情 · ${detail.name}`}
          width={1180}
          footer={
            <Button variant="quiet" onClick={() => setDetail(null)}>
              关闭
            </Button>
          }
        >
          <div className="max-h-[72vh] overflow-y-auto pr-1">
            <WorkflowInspector wf={detail} instances={insts} />
          </div>
        </Modal>
      )}
      {replaceFor && (
        <ReplaceJsonModal
          open
          onClose={() => setReplaceFor(null)}
          workflowId={replaceFor.id}
          workflowName={replaceFor.name}
          instances={insts}
          onReplaced={() => setMsg(`「${replaceFor.name}」的图已替换并按实例重扫`)}
        />
      )}

      <Modal
        open={!!confirmDelete}
        onClose={() => setConfirmDelete(null)}
        title="删除工作流"
        width={440}
        footer={
          <>
            <Button variant="quiet" onClick={() => setConfirmDelete(null)}>
              取消
            </Button>
            <Button
              variant="danger"
              loading={wfMut.remove.isPending}
              onClick={() => {
                if (!confirmDelete) return;
                wfMut.remove.mutate(confirmDelete.id, { onSuccess: () => setConfirmDelete(null) });
              }}
            >
              确认删除
            </Button>
          </>
        }
      >
        <p className="text-note leading-relaxed text-ink-dim">
          删掉 <b>{confirmDelete?.name}</b> 会一起删掉它在所有 Server 上的默认权重绑定。已产生的任务与媒体记录保留，
          历史任务里引用的 workflowId 会变成失效链接。
        </p>
      </Modal>
    </div>
  );
}

function WorkflowCard({
  wf,
  isDefault,
  instances,
  check,
  busy,
  onCheck,
  onModels,
  onSync,
  onReplace,
  onDownload,
  onSetDefault,
  onDelete,
  onDetail,
}: {
  wf: Workflow;
  isDefault?: boolean;
  instances: GenInstance[];
  check?: CheckState;
  busy?: boolean;
  onCheck: () => void;
  onModels: () => void;
  onSync?: () => void;
  onReplace?: () => void;
  onDownload: () => void;
  onSetDefault?: () => void;
  onDelete: () => void;
  onDetail: () => void;
}) {
  const boundTotal = Object.values(wf.bindings ?? {}).reduce((a, b) => a + b, 0);
  const reports = check?.report ?? [];
  const bad = reports.filter((r) => !r.ok).length;
  const gaps = wf.gaps?.length ?? 0;
  return (
    <div className="flex flex-col gap-2 rounded-tile border border-rule-soft bg-sheen p-2.5">
      <div className="flex flex-wrap items-center gap-1">
        <Badge tone={wf.isBuiltin ? "neutral" : "good"}>{wf.modeLabel ?? (wf.taskKind ?? wf.family)}</Badge>
        {isDefault && <Badge tone="good">默认</Badge>}
        {wf.isBuiltin && <Badge>内置</Badge>}
        {gaps > 0 ? <Badge tone="warn">缺 {gaps} 处节点</Badge> : <Badge tone="good">节点齐</Badge>}
        {wf.verifiedAt && <Badge tone="good">真机跑通过</Badge>}
      </div>
      <button onClick={onDetail} className="text-left">
        <div className="text-body font-semibold leading-tight hover:underline">{wf.name}</div>
      </button>
      {wf.description && <p className="line-clamp-2 text-caption leading-snug text-ink-mute">{wf.description}</p>}
      <div className="space-y-0.5 text-caption text-ink-mute">
        <div className="truncate">
          <span className="text-ink-dim">文件</span> <span className="mono">{wf.sourceFile ?? "—"}</span>
        </div>
        <div>
          <span className="text-ink-dim">JSON</span> {fmtBytes(wf.jsonBytes)} · 更新 {fmtTime(wf.updatedAt)} · {wf.nodeCount ?? 0} 节点
        </div>
      </div>

      <div className="flex items-center gap-1.5 text-caption">
        {!check && <span className="text-ink-mute">○ 未检查</span>}
        {check?.status === "checking" && <span className="text-ink-mute">正在按实例的 /object_info 比对…</span>}
        {check?.status === "failed" && <span className="text-state-fail">× 检查失败</span>}
        {check?.status === "done" && (bad === 0 ? <span className="text-state-ok">✓ 检查通过（{reports.length} 台）</span> : <span className="text-state-fail">× 有问题（{bad}/{reports.length} 台）</span>)}
      </div>

      <div className="flex flex-wrap items-center gap-1.5 text-caption">
        {boundTotal > 0 ? (
          <Badge tone="good">
            已绑 {Object.keys(wf.bindings ?? {}).length} 台 · {boundTotal} 项
          </Badge>
        ) : (
          <Badge>沿用图内权重</Badge>
        )}
        {wf.autoSelect === false && <Badge tone="warn">不参与自动选</Badge>}
        <span className="mono text-ink-mute">优先级 {wf.priority ?? "—"}</span>
      </div>

      <div className="mt-auto flex flex-wrap items-center gap-2 pt-1">
        {onReplace && (
          <Button size="sm" variant="quiet" onClick={onReplace}>
            替换 JSON
          </Button>
        )}
        <Button size="sm" variant="quiet" icon={<ShieldCheck className="h-3 w-3" />} onClick={onCheck} disabled={busy}>
          检查
        </Button>
        <Button size="sm" variant="quiet" icon={<Wand2 className="h-3 w-3" />} onClick={onModels}>
          模型
        </Button>
        {onSync && instances.length > 1 && (
          <Button size="sm" variant="quiet" onClick={onSync} title="把一台上配好的默认权重搬到别处">
            同步
          </Button>
        )}
        <Button size="sm" variant="quiet" icon={<Download className="h-3 w-3" />} onClick={onDownload}>
          下载
        </Button>
        {onSetDefault && !isDefault && (
          <Button size="sm" variant="quiet" icon={<Star className="h-3 w-3" />} onClick={onSetDefault} title="同类任务自动选时先挑它">
            设默认
          </Button>
        )}
        {!wf.isBuiltin && (
          <Button size="sm" variant="danger" icon={<Trash2 className="h-3 w-3" />} onClick={onDelete}>
            删除
          </Button>
        )}
      </div>
    </div>
  );
}

/* ───────── 模型编辑（按 Server 的默认权重） ───────── */

function ModelBindingModal({ wf, instances, onClose }: { wf: Workflow; instances: GenInstance[]; onClose: () => void }) {
  const wfMut = useWorkflowMutations();
  const { data: bindingList } = useWorkflowBindings(wf.id);
  const fallbackInstance = instances.find((i) => i.isDefault) ?? instances[0];
  const [instanceId, setInstanceId] = useState(fallbackInstance?.id ?? "");
  const [copyFrom, setCopyFrom] = useState("");
  const [picks, setPicks] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const { data: options, isLoading, error: loadError } = useWorkflowModelsOrSkip(wf.id, instanceId);

  const slots = options?.slots ?? [];
  const boundForInstance = useMemo(
    () => Object.fromEntries((bindingList?.bindings ?? []).filter((b) => b.instanceId === instanceId).flatMap((b) => Object.entries(b.overrides))) as Record<string, string>,
    [bindingList, instanceId],
  );

  // 换一台 Server 就把编辑区换成那台的绑定：这正是"绑定按实例独立"的意思
  useEffect(() => {
    setPicks(boundForInstance);
    setError(null);
  }, [instanceId, bindingList]); // eslint-disable-line react-hooks/exhaustive-deps

  const dirty = JSON.stringify(picks) !== JSON.stringify(boundForInstance);
  const current = instances.find((i) => i.id === instanceId) ?? null;

  return (
    <Modal
      open
      onClose={onClose}
      title={`模型编辑 · ${wf.name}`}
      width={720}
      footer={
        <>
          {Object.keys(boundForInstance).length > 0 && (
            <Button
              variant="quiet"
              className="mr-auto"
              onClick={() =>
                wfMut.clearBindings.mutate({ id: wf.id, instanceId }, { onSuccess: onClose, onError: (e) => setError((e as Error).message) })
              }
            >
              恢复这条工作流的默认权重
            </Button>
          )}
          <Button variant="quiet" onClick={onClose}>
            关闭
          </Button>
          <Button
            variant="primary"
            disabled={!dirty || !slots.length}
            loading={wfMut.saveBindings.isPending}
            onClick={() =>
              wfMut.saveBindings.mutate(
                { id: wf.id, instanceId, overrides: picks },
                { onSuccess: () => onClose(), onError: (e) => setError((e as Error).message) },
              )
            }
          >
            完成
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <span className="label">正在配置</span>
          <Select value={instanceId} onChange={(e) => setInstanceId(e.target.value)} className="h-7 w-auto">
            {instances.map((i) => (
              <option key={i.id} value={i.id}>
                {i.name}
                {i.isDefault ? " · 默认" : ""}
              </option>
            ))}
          </Select>
          {bindingList && bindingList.bindings.filter((b) => b.instanceId !== instanceId).length > 0 && (
            <>
              <span className="label">复制自其它 Server</span>
              <Select
                value={copyFrom}
                onChange={(e) => {
                  setCopyFrom(e.target.value);
                  const src = bindingList.bindings.find((b) => b.instanceId === e.target.value);
                  if (src) setPicks(Object.fromEntries(Object.entries(src.overrides).filter(([k]) => slots.some((s) => s.key === k))));
                }}
                className="h-7 w-auto"
              >
                <option value="">不复制</option>
                {bindingList.bindings
                  .filter((b) => b.instanceId !== instanceId && Object.keys(b.overrides).length)
                  .map((b) => (
                    <option key={b.instanceId} value={b.instanceId}>
                      {b.instanceName}（{Object.keys(b.overrides).length} 项）
                    </option>
                  ))}
              </Select>
            </>
          )}
        </div>

        {!instances.length ? (
          <Empty title="还没有可配置的 Server" hint="先在「Server 管理」里添加本机 ComfyUI —— 能换哪些权重是那台实例报出来的。" />
        ) : (
          <>
            <p className="text-caption leading-snug text-ink-mute">
              模型来自 Server：<span className="text-ink-dim">{current?.name ?? "—"}</span>
              {current?.lastProbeOk === false && <span className="text-state-fail"> · 这台上次探活失败，清单可能取不到</span>}
            </p>
            {isLoading && <p className="text-note text-ink-mute">正在读这台的 /object_info…</p>}
            {loadError && <p className="text-note text-state-fail">取不到模型清单：{(loadError as Error).message}</p>}
            {!isLoading && !loadError && slots.length === 0 && (
              <p className="text-note text-ink-mute">这张图上没有可替换的权重位（或者这台一个候选都没报出来）。</p>
            )}
            <ul className="space-y-2">
              {slots.map((s) => (
                <li key={s.key} className="space-y-1">
                  <div className="flex flex-wrap items-baseline gap-2">
                    <span className="text-note font-medium">{s.label}</span>
                    <span className="mono text-caption text-ink-mute">
                      {s.classType} · {s.field}
                    </span>
                    <span className="label">{s.role}</span>
                  </div>
                  <div className="flex items-center gap-2">
                    <Select
                      value={picks[s.key] ?? ""}
                      onChange={(e) => setPicks((p) => ({ ...p, [s.key]: e.target.value }))}
                      className="h-7 min-w-0 flex-1"
                    >
                      <option value="">{`跟随工作流默认 · ${s.current || "（图里没写）"}`}</option>
                      {s.options.map((o) => (
                        <option key={o} value={o}>
                          {o}
                        </option>
                      ))}
                    </Select>
                    <SlotStatus slot={s} picked={picks[s.key] ?? ""} />
                  </div>
                </li>
              ))}
            </ul>
            <p className="text-caption leading-snug text-ink-mute">
              绑定按 Server 独立保存：这里改的是上方所选 Server 的配置，生成到这台时自动套用，其它 Server 不受影响。
              选「跟随工作流默认」表示不动图里写死的那一份。清单只列这台实例真的报出来的文件 —— 没有就是没有，不会拿别的族凑一个。
            </p>
            {error && <p className="text-note text-state-fail">保存失败：{error}</p>}
          </>
        )}
      </div>
    </Modal>
  );
}

function SlotStatus({ slot, picked }: { slot: ModelSlot; picked: string }) {
  if (picked) return <span className="flex-none text-caption text-state-ok">已绑定</span>;
  if (slot.missing) return <span className="flex-none text-caption text-state-fail">图里那个文件这台没有</span>;
  return <span className="flex-none text-caption text-ink-mute">当前 {slot.current.split("/").pop() || "—"}</span>;
}

/** 模型清单要按实例问；实例没选就不发请求 */
function useWorkflowModelsOrSkip(ref: string | null, instanceId: string) {
  const api = useApi();
  return useQuery({
    queryKey: ["workflows", ref ?? "", "models", instanceId || "none"],
    queryFn: () => api.workflows.modelOptions(ref!, instanceId),
    enabled: !!ref && !!instanceId,
    staleTime: 60_000,
  });
}

/* ───────── 工作流体检 ───────── */

function CheckModal({
  wf,
  state,
  instances,
  onClose,
  onRecheck,
}: {
  wf: Workflow;
  state?: CheckState;
  instances: GenInstance[];
  onClose: () => void;
  onRecheck: (w: Workflow, ids?: string[]) => void;
}) {
  const [tab, setTab] = useState(state?.report?.[0]?.instanceId ?? "");
  const reports = state?.report ?? [];
  const active = reports.find((r) => r.instanceId === tab) ?? reports[0] ?? null;
  useEffect(() => {
    if (reports.length && !reports.some((r) => r.instanceId === tab)) setTab(reports[0].instanceId);
  }, [reports, tab]);

  return (
    <Modal
      open
      onClose={onClose}
      title={`工作流体检 · ${wf.name}`}
      width={700}
      footer={
        <>
          <Button variant="quiet" className="mr-auto" icon={<RefreshCw className="h-3 w-3" />} onClick={() => onRecheck(wf)} disabled={state?.status === "checking"}>
            重新检测全部
          </Button>
          <Button variant="primary" onClick={onClose}>
            关闭
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        {state?.status === "checking" && <p className="text-note text-ink-mute">正在逐台读 /object_info 并比对节点与权重…</p>}
        {state?.status === "failed" && <p className="text-note text-state-fail">体检失败：{state.error}</p>}
        {reports.length === 0 && state?.status !== "checking" && (
          <Empty title="还没有体检结果" hint="体检会逐台问：连不连得上、缺哪些节点、图里与绑定里的权重在这台上有没有、是不是半截下载。" action={<Button size="sm" onClick={() => onRecheck(wf)}>开始检查</Button>} />
        )}
        {reports.length > 0 && (
          <>
            <Tabs
              value={active?.instanceId ?? reports[0].instanceId}
              onChange={setTab}
              tabs={reports.map((r) => ({
                key: r.instanceId,
                label: (
                  <span className="flex items-center gap-1.5">
                    <StateGlyph state={r.ok ? "succeeded" : "failed"} />
                    {r.instanceName}
                  </span>
                ),
              }))}
            />
            {active && (
              <div className="space-y-2">
                <div
                  className={cn(
                    "rounded-ctl border px-3 py-2 text-note leading-snug",
                    active.ok ? "border-state-ok/40 bg-state-ok/8" : "border-state-warn/45 bg-state-warn/8",
                  )}
                >
                  <div className="font-medium">{active.ok ? "这台能跑" : "存在以下问题（生成前建议先解决）"}</div>
                  <div className="mt-0.5 text-caption text-ink-mute">
                    对照 Server：{active.instanceName}（{active.placement === "local" ? "本机" : "云端"}）· 协议 {active.protocol ?? "comfy_native"}
                  </div>
                </div>
                {active.problems.map((p, i) => (
                  <div key={i} className="rounded-ctl border border-state-fail/35 bg-state-fail/6 px-3 py-2 text-note leading-snug">
                    {p}
                  </div>
                ))}
                <div className="rounded-ctl border border-rule-soft bg-slate px-3 py-2">
                  <div className="label mb-1">这张图吃什么</div>
                  <div className="text-note text-ink-dim">
                    {wf.modeLabel ?? active.taskKind} · {active.signals?.length ? `信号 ${active.signals.join("、")}` : "没有解析出任务信号（只能手动指定）"}
                  </div>
                </div>
                {active.models.length > 0 && (
                  <div className="space-y-1">
                    <div className="label">权重位（{active.models.length}）</div>
                    <table className="w-full border-collapse text-note">
                      <thead>
                        <tr className="border-b border-rule text-left">
                          <th className="label py-1 font-normal">位置</th>
                          <th className="label py-1 font-normal">真会用的文件</th>
                          <th className="label w-[86px] py-1 font-normal">来源</th>
                        </tr>
                      </thead>
                      <tbody>
                        {active.models.map((s) => (
                          <tr key={s.key} className="border-b border-rule-soft">
                            <td className="py-1 pr-2">
                              <span className="text-ink-dim">{s.label}</span>
                              <span className="mono ml-1 text-caption text-ink-mute">{s.key}</span>
                            </td>
                            <td className={cn("mono max-w-[280px] truncate py-1 pr-2 text-caption", s.missing ? "text-state-fail" : "text-ink-dim")} title={s.effective}>
                              {s.effective || "（空）"}
                            </td>
                            <td className="py-1 text-caption">{s.bound ? <span className="text-state-ok">绑定</span> : <span className="text-ink-mute">图内</span>}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
                {!!active.weightProblems?.length && (
                  <div className="rounded-ctl border border-state-fail/40 bg-state-fail/6 px-3 py-2 text-caption leading-snug">
                    <div className="text-state-fail">这几份权重在盘上是半截的（加载不报错，但输出恒为 0）：</div>
                    <ul className="mono mt-1 space-y-0.5 text-ink-dim">
                      {active.weightProblems.map((w) => (
                        <li key={w}>{w}</li>
                      ))}
                    </ul>
                  </div>
                )}
                <div className="flex flex-wrap gap-x-4 gap-y-1 text-caption text-ink-mute">
                  <span>节点 {active.nodeCount ?? "—"}</span>
                  <span>缺口 {active.gaps.length}</span>
                  <span>真机跑过 {active.verifiedAt ? fmtTime(active.verifiedAt) : "没有"}</span>
                  <span>自动选 {active.autoSelect ? "开" : "关"} · 优先级 {active.priority ?? "—"}</span>
                </div>
              </div>
            )}
          </>
        )}
        {instances.length === 0 && <p className="text-note text-state-fail">还没有登记任何 Server，没有可对照的对象。</p>}
      </div>
    </Modal>
  );
}

/* ───────── 同步模型绑定 ───────── */

/**
 * 一次同步的结论。四类必须分开显示，因为它们对「生成用的是哪个文件」的影响不一样：
 * 照搬 = 和源那台完全一样；换档 = 换了同族另一个精度/模式的文件；
 * 对齐 = 你没绑过、但图里写死的名字在这台根本不存在，不换就任务失败；跳过 = 认不出同族，宁可不换。
 */
function SyncResultBlock({ r }: { r: SyncTargetResult }) {
  if (!r.ok) {
    return (
      <div className="rounded-tile border border-state-fail/40 bg-state-fail/6 px-3 py-2 text-note">
        <b>{r.instanceName ?? r.instanceId}</b> 同步失败：{r.error}
      </div>
    );
  }
  return (
    <div className="space-y-1 rounded-tile border border-rule-soft bg-slate px-3 py-2 text-note leading-snug">
      <div className="flex flex-wrap items-center gap-2">
        <b className="text-ink">{r.instanceName ?? r.instanceId}</b>
        <span className="text-caption text-ink-mute">
          写入 <span className="mono text-ink-dim">{r.written ?? 0}</span> 项 · 照搬 <span className="mono text-ink-dim">{r.applied.length}</span> · 换档{" "}
          <span className="mono text-ink-dim">{r.converted.length}</span> · 对齐 <span className="mono text-ink-dim">{r.aligned.length}</span> · 跳过{" "}
          <span className="mono text-ink-dim">{r.skipped.length}</span>
        </span>
        {r.blocked && <Badge tone="bad">这台缺节点，整条跑不了</Badge>}
      </div>
      {!!r.missingNodes?.length && (
        <div className="mono text-caption leading-snug text-state-fail">
          缺 {r.missingNodes.length} 个节点：{r.missingNodes.slice(0, 6).join("、")}
          {r.missingNodes.length > 6 ? "…" : ""}。绑定照写，但派到这台会在建图前被拦下 —— 装好节点包再去工作流详情「重新扫描」。
        </div>
      )}
      {r.converted.map((c) => (
        <div key={c.key} className="mono text-caption text-state-warn">
          换档 {c.key}：{c.from} → {c.to}（该台真有的 {c.role}）
        </div>
      ))}
      {r.aligned.map((c) => (
        <div key={c.key} className="mono text-caption text-ink-dim">
          对齐 {c.key}：{c.from} → {c.to}（图里那个这台没有）
        </div>
      ))}
      {r.skipped.map((s) => (
        <div key={s.key} className="text-caption text-ink-mute">
          跳过 {s.key} · {s.reason}
        </div>
      ))}
    </div>
  );
}

function AlignToggle({ on, onChange }: { on: boolean; onChange: (v: boolean) => void }) {
  return (
    <Toggle
      checked={on}
      onChange={onChange}
      label="未绑定的权重位也按目标实例对齐一遍"
      hint="图里写死的是导入那台机器上的文件名。换台机器（尤其只有另一种精度的云机）不对齐就会撞「这台实例上没有这个权重」，任务在建图前失败。对齐只在这台真的没有那个文件时才写绑定，本来能用的位保持未绑定。"
    />
  );
}

function SyncBindingsModal({ wf, instances, onClose }: { wf: Workflow; instances: GenInstance[]; onClose: () => void }) {
  const wfMut = useWorkflowMutations();
  const { data: bindingList } = useWorkflowBindings(wf.id);
  const sources = (bindingList?.bindings ?? []).filter((b) => Object.keys(b.overrides).length);
  const [source, setSource] = useState(sources[0]?.instanceId ?? "");
  const [align, setAlign] = useState(true);
  const [targets, setTargets] = useState<string[]>([]);
  useEffect(() => {
    if (!source && sources[0]) setSource(sources[0].instanceId);
  }, [source, sources]);
  const result = wfMut.syncBindings.data;
  const error = wfMut.syncBindings.error?.message ?? null;
  const candidates = instances.filter((i) => i.id !== source);
  // 没配过源绑定但开了对齐是有意义的（就是「把图里的默认按这台重算一遍」），所以只有两者都没有才不让点
  const canRun = (!!source || align) && targets.length > 0 && !wfMut.syncBindings.isPending;

  return (
    <Modal
      open
      onClose={onClose}
      title={`同步模型绑定 · ${wf.name}`}
      width={680}
      footer={
        <>
          <Button variant="quiet" onClick={onClose}>
            关闭
          </Button>
          <Button
            variant="primary"
            disabled={!canRun}
            loading={wfMut.syncBindings.isPending}
            onClick={() =>
              wfMut.syncBindings.mutate({
                id: wf.id,
                sourceInstanceId: source || instances[0]?.id || "",
                targetInstanceIds: targets,
                alignUnbound: align,
              })
            }
          >
            开始同步
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <Field label="基准（源）Server" hint="源上没配过的位，只要开着下面的对齐就照样会按目标实例重算一遍。">
          <Select value={source} onChange={(e) => setSource(e.target.value)} className="w-full">
            <option value="">不指定源（只按图里写死的对齐）</option>
            {(bindingList?.bindings ?? []).map((b) => (
              <option key={b.instanceId} value={b.instanceId}>
                {b.instanceName}（{Object.keys(b.overrides).length} 项 · {b.source === "sync" ? "同步来的" : "手配"}）
              </option>
            ))}
          </Select>
        </Field>
        {sources.length === 0 && (
          <p className="text-caption leading-snug text-ink-mute">这台源上还没有任何绑定：现在同步等于「只把图里写死的权重按目标机器对齐」。</p>
        )}
        <AlignToggle on={align} onChange={setAlign} />
        <div className="space-y-1.5">
          <span className="label">同步到（{targets.length} 台）</span>
          <ul className="space-y-1">
            {candidates.map((i) => (
              <li key={i.id}>
                <label className="flex items-center gap-2 rounded-ctl border border-rule-soft bg-slate px-2 py-1.5 text-note hover:bg-raised">
                  <input
                    type="checkbox"
                    checked={targets.includes(i.id)}
                    onChange={(e) => setTargets((t) => (e.target.checked ? [...t, i.id] : t.filter((x) => x !== i.id)))}
                  />
                  {i.name}
                  <span className="mono ml-auto text-caption text-ink-mute">{i.placement}</span>
                </label>
              </li>
            ))}
          </ul>
          {candidates.length === 0 && (
            <p className="text-caption text-ink-mute">没有别的目标实例。要跨服务器同步，先在「Server 管理」里把第二台 ComfyUI 登记进来。</p>
          )}
        </div>
        <p className="rounded-ctl border border-state-warn/40 bg-state-warn/6 px-2.5 py-2 text-caption leading-snug text-state-warn">
          规则：同名模型优先沿用；那台上没有同名文件时，只按<b>同族、同模式、同角色</b>的判据换成该台真有的文件，认不出就留空并说明原因。
          缺节点的台会整条标出来 —— 绝不拿名字最像的另一个族顶上，那种换法提交上去不报缺文件，只报一堆看不懂的采样错。
        </p>
        {error && <p className="text-note text-state-fail">同步失败：{error}</p>}
        {result?.results.map((r) => (
          <SyncResultBlock key={r.instanceId} r={r} />
        ))}
      </div>
    </Modal>
  );
}

/** 整库对齐：接了第二台 ComfyUI 时不必逐条点同步 */
function SyncAllModal({ instances, onClose }: { instances: GenInstance[]; onClose: () => void }) {
  const wfMut = useWorkflowMutations();
  const fallbackSource = instances.find((i) => i.isDefault) ?? instances[0];
  const [source, setSource] = useState(fallbackSource?.id ?? "");
  const [targets, setTargets] = useState<string[]>([]);
  const [align, setAlign] = useState(true);
  const [onlyMissing, setOnlyMissing] = useState(true);
  const [includeBuiltin, setIncludeBuiltin] = useState(false);
  const result = wfMut.syncAll.data;
  const error = wfMut.syncAll.error?.message ?? null;
  const candidates = instances.filter((i) => i.id !== source);

  return (
    <Modal
      open
      onClose={onClose}
      title="整库对齐到别的 Server"
      width={760}
      footer={
        <>
          <Button variant="quiet" onClick={onClose}>
            关闭
          </Button>
          <Button
            variant="primary"
            disabled={!source || targets.length === 0 || wfMut.syncAll.isPending}
            loading={wfMut.syncAll.isPending}
            onClick={() =>
              wfMut.syncAll.mutate({ sourceInstanceId: source, targetInstanceIds: targets, alignUnbound: align, onlyMissing, includeBuiltin })
            }
          >
            开始对齐
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <p className="text-note leading-snug text-ink-mute">
          库里每一条工作流（含可选的内置模板）按同一套规则搬到所选目标实例上。每台实例的节点清单只问一次，不逐条重复拉。
        </p>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="基准（源）Server">
            <Select value={source} onChange={(e) => setSource(e.target.value)} className="w-full">
              {instances.map((i) => (
                <option key={i.id} value={i.id}>
                  {i.name}
                  {i.isDefault ? " · 默认" : ""}
                </option>
              ))}
            </Select>
          </Field>
          <div className="space-y-1.5">
            <span className="label">目标（{targets.length} 台）</span>
            <ul className="max-h-[150px] space-y-1 overflow-y-auto">
              {candidates.map((i) => (
                <li key={i.id}>
                  <label className="flex items-center gap-2 rounded-ctl border border-rule-soft bg-slate px-2 py-1.5 text-note hover:bg-raised">
                    <input
                      type="checkbox"
                      checked={targets.includes(i.id)}
                      onChange={(e) => setTargets((t) => (e.target.checked ? [...t, i.id] : t.filter((x) => x !== i.id)))}
                    />
                    {i.name}
                    <span className="mono ml-auto text-caption text-ink-mute">{i.placement}</span>
                  </label>
                </li>
              ))}
              {candidates.length === 0 && <li className="text-caption text-ink-mute">没有别的目标实例，先去「Server 管理」登记第二台。</li>}
            </ul>
          </div>
        </div>
        <AlignToggle on={align} onChange={setAlign} />
        <Toggle
          checked={onlyMissing}
          onChange={setOnlyMissing}
          label="只补目标上还没配过的条目"
          hint="关掉会覆盖目标机上手工挑过的那些位。整库对齐通常是补漏，不是重刷。"
        />
        <Toggle
          checked={includeBuiltin}
          onChange={setIncludeBuiltin}
          label="连内置模板一起对齐"
          hint="内置模板是出图/出片按钮没挑工作流时的回落路径，动它的默认权重会影响所有项目，所以默认不勾。"
        />
        {error && <p className="text-note text-state-fail">对齐失败：{error}</p>}
        {result && (
          <div className="space-y-2">
            <div className="rounded-ctl border border-rule-soft bg-slate px-3 py-2 text-note">
              <b className="text-ink">{result.totals.workflows}</b> 条工作流 · 写入{" "}
              <b className="mono text-ink">{result.totals.written}</b> 项 · 对齐{" "}
              <span className="mono text-ink">{result.totals.aligned}</span> · 跳过{" "}
              <span className="mono text-ink">{result.totals.skipped}</span>
              {result.totals.blocked > 0 && <span className="text-state-fail"> · {result.totals.blocked} 条在目标机上整条跑不了</span>}
            </div>
            <ul className="max-h-[320px] space-y-2 overflow-y-auto pr-1">
              {result.workflows.map((w) => (
                <li key={w.workflowId} className="space-y-1.5 rounded-tile border border-rule-soft bg-sheen px-3 py-2">
                  <div className="flex flex-wrap items-center gap-2 text-note">
                    <b className="text-ink">{w.workflowName}</b>
                    {w.untouched ? <span className="text-caption text-ink-mute">{w.reason ?? "跳过"}</span> : <span className="mono text-caption text-ink-mute">源上绑 {w.sourceBound ?? 0} 项</span>}
                  </div>
                  {w.results.map((r) => (
                    <SyncResultBlock key={r.instanceId} r={r} />
                  ))}
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </Modal>
  );
}
