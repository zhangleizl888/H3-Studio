import { useState } from "react";
import { Plus, PlugZap, Trash2 } from "lucide-react";
import { Badge, Button, Empty, Field, Input, KeyVal, MachChip, Modal, Panel, Select, StateGlyph, Toggle } from "../../components/ui";
import { useInstanceMutations, useInstances } from "../../lib/hooks";
import { useApi } from "../../lib/apiClient";
import type { GenInstance, InstanceCaps, ProbeReport } from "../../lib/types";
import { cn, fmtMoney, fmtTime, redactKeyUrl } from "../../lib/utils";

const PROTOCOL_LABEL: Record<GenInstance["protocol"], string> = {
  comfy_native: "原生 ComfyUI 协议",
  rh_task: "RunningHub 任务 API",
};

export default function Instances() {
  const { data: instances } = useInstances();
  const mut = useInstanceMutations();
  const [addOpen, setAddOpen] = useState(false);
  const [report, setReport] = useState<{ id: string; r: ProbeReport } | null>(null);

  const groups: { placement: GenInstance["placement"]; title: string; desc: string }[] = [
    { placement: "local", title: "本机 ComfyUI", desc: "免费、完全可控，产物可直接从磁盘读，不用下载一次。" },
    { placement: "cloud_self", title: "自建云端 ComfyUI", desc: "自己的 GPU 主机 + cloudflared 命名隧道，走同一套原生协议。" },
    { placement: "cloud_runninghub", title: "RunningHub", desc: "托管 ComfyUI。两种接法：原生代理（等同本机 8188）与任务 API（排队、按秒计费、可选显存档位）。" },
  ];

  async function probe(id: string) {
    const r = await mut.probe.mutateAsync(id);
    setReport({ id, r });
  }

  return (
    <div className="space-y-4 p-4">
      <header className="flex items-end justify-between">
        <div>
          <h1 className="text-title font-semibold">生成实例</h1>
          <p className="text-note text-ink-mute">
            分派按<span className="text-ink-dim">协议</span>走，不按服务商走：本机、自建云、RunningHub 原生代理共用同一套客户端代码。
          </p>
        </div>
        <Button size="sm" icon={<Plus className="h-3.5 w-3.5" />} onClick={() => setAddOpen(true)}>
          添加实例
        </Button>
      </header>

      {instances?.length === 0 && (
        <Empty
          title="还没有配置任何生成实例"
          hint="最省事的开始方式是添加本机 ComfyUI（默认 http://127.0.0.1:8188）。"
          action={
            <Button size="sm" onClick={() => setAddOpen(true)}>
              添加第一个实例
            </Button>
          }
        />
      )}

      {groups.map((g) => {
        const list = (instances ?? []).filter((i) => i.placement === g.placement);
        if (g.placement !== "cloud_runninghub" && list.length === 0) return null;
        return (
          <Panel
            key={g.placement}
            title={
              <span className="flex items-center gap-2">
                <MachChip placement={g.placement} label={g.title} />
                <Badge>{list.length}</Badge>
              </span>
            }
            actions={<Button size="sm" variant="quiet" onClick={() => list.forEach((i) => probe(i.id))} disabled={list.length === 0}>全部探活</Button>}
            dense
          >
            <div className="border-b border-rule-soft px-3 py-1.5 text-caption text-ink-mute">{g.desc}</div>
            {list.length === 0 ? (
              <div className="px-3 py-4 text-note text-ink-mute">
                这一类还没有实例。
                {g.placement === "cloud_runninghub" && " RunningHub 的原生代理几乎零成本：填 key 就能当云端 ComfyUI 用。"}
              </div>
            ) : (
              <ul className="divide-y divide-rule-soft">
                {list.map((i) => (
                  <InstanceRow key={i.id} inst={i} onProbe={() => probe(i.id)} onRemove={() => mut.remove.mutate(i.id)} onToggleDefault={() => mut.update.mutate({ id: i.id, body: { isDefault: !i.isDefault } })} />
                ))}
              </ul>
            )}
          </Panel>
        );
      })}

      <Panel title="为什么浏览器不直连这些地址">
        <ul className="list-disc space-y-1 pl-4 text-note leading-relaxed text-ink-dim">
          <li>ComfyUI 开源版没有任何鉴权，默认只有「同站点」防护；一旦为前端开 <span className="mono">--enable-cors-header</span> 就等于把它暴露给任意网页。</li>
          <li>RunningHub 的 apiKey 在 URL 路径里（<span className="mono">/proxy/&#123;key&#125;</span>），绝不能进前端包、日志或报错栈。</li>
          <li>媒体必须落盘。RunningHub 的结果链接约 1 天过期，存外链等于给自己埋 404。</li>
          <li>取消、重试、配额、成本记账只能在服务端统一做。</li>
        </ul>
      </Panel>

      <AddInstanceModal open={addOpen} onClose={() => setAddOpen(false)} />

      {report && <ReportModal inst={(instances ?? []).find((x) => x.id === report.id)} report={report.r} onClose={() => setReport(null)} />}
    </div>
  );
}

function InstanceRow({ inst, onProbe, onRemove, onToggleDefault }: { inst: GenInstance; onProbe: () => void; onRemove: () => void; onToggleDefault: () => void }) {
  const busy = useInstanceMutations().probe.isPending;
  const caps = inst.capabilities as InstanceCaps | undefined;
  return (
    <li className="space-y-2 px-3 py-2.5">
      <div className="flex flex-wrap items-center gap-2">
        <StateGlyph state={inst.lastProbeOk ? "succeeded" : inst.lastProbeOk === false ? "failed" : "idle"} />
        <span className="text-body font-medium">{inst.name}</span>
        <Badge>{PROTOCOL_LABEL[inst.protocol]}</Badge>
        {inst.isDefault && <Badge tone="good">默认</Badge>}
        {inst.placement === "cloud_runninghub" && inst.site && <Badge>{inst.site === "cn" ? "cn 站" : "global 站"}</Badge>}
        {inst.instanceType && <Badge>{inst.instanceType === "default" ? "24G" : inst.instanceType === "plus" ? "48G" : "84G"}</Badge>}
        <div className="ml-auto flex items-center gap-1.5">
          <Button size="sm" variant="quiet" icon={<PlugZap className="h-3 w-3" />} onClick={onProbe} disabled={busy}>
            探活
          </Button>
          <Button size="sm" variant="quiet" onClick={onToggleDefault}>
            {inst.isDefault ? "取消默认" : "设为默认"}
          </Button>
          <Button size="sm" variant="danger" icon={<Trash2 className="h-3 w-3" />} onClick={onRemove} disabled={busy}>
            删除
          </Button>
        </div>
      </div>

      <div className="mono truncate text-caption text-ink-mute" title={inst.baseUrl}>
        {redactKeyUrl(inst.baseUrl)}
        {inst.apiKeySet ? " · key 已配置" : inst.placement === "cloud_runninghub" ? " · 未配置 key" : ""}
      </div>

      {inst.lastProbeOk === false && inst.lastError && (
        <div className="rounded-ctl border border-rule-soft bg-inset px-2 py-1.5 text-caption leading-snug text-ink-dim">{inst.lastError}</div>
      )}

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
          <span className={cn(caps.qwenImage ? "text-state-ok" : "text-ink-mute")}>Qwen-Image</span>
        </div>
      ) : null}

      {inst.quota?.concurrentLimit != null && (
        <div className="mono text-caption text-ink-mute">
          并发 {inst.quota.runningCount ?? 0}/{inst.quota.concurrentLimit} · 排队 {inst.quota.queuedCount ?? 0}
          {inst.quota.remainMoney != null && (
            <span className="text-mach-rh"> · 余额 {fmtMoney(inst.quota.remainMoney)}</span>
          )}
          {inst.quota.remainCoins != null && <span> · {inst.quota.remainCoins} RH币</span>}
        </div>
      )}

      {/* 节点在、权重不在，是这台机器当前唯一的真实缺口 —— 必须显式说出来 */}
      {inst.lastProbeOk && caps?.missingModels && caps.missingModels.length > 0 && (
        <div className="rounded-ctl border border-mach-rh/45 bg-mach-rh/8 px-2 py-1.5 text-caption leading-snug">
          <div className="text-mach-rh">
            实例可用，但缺 {caps.missingModels.length} 个模型文件，H3 任务现在跑不了：
          </div>
          <ul className="mono mt-1 space-y-0.5 text-caption text-ink-dim">
            {caps.missingModels.map((m) => (
              <li key={m}>{m}</li>
            ))}
          </ul>
          <div className="mt-1 text-caption text-ink-mute">下载约 45 GB，需要单独授权后执行。</div>
        </div>
      )}

      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-caption text-ink-mute">
        <span>上次探活 {fmtTime(inst.lastProbeAt)}</span>
        {inst.cost?.money != null && (
          <span className="text-mach-rh">
            累计 {fmtMoney(inst.cost.money)} · {inst.cost.tasks ?? 0} 次 · GPU {Math.round(inst.cost.gpuSeconds ?? 0)}s
          </span>
        )}
        {inst.retainSeconds != null && <span>保热实例 {inst.retainSeconds}s（额外计费）</span>}
      </div>
    </li>
  );
}

function ReportModal({ inst, report, onClose }: { inst?: GenInstance; report: ProbeReport; onClose: () => void }) {
  return (
    <Modal open onClose={onClose} title={`探活结果 · ${inst?.name ?? ""}`} width={640}>
      <div className="space-y-3">
        <div
          className={cn(
            "rounded-ctl border px-2.5 py-2 text-note",
            report.ok ? "border-state-ok/40 bg-state-ok/8" : "border-state-fail/40 bg-state-fail/8",
          )}
        >
          {report.ok ? "连上了，可以派发任务。" : (report.error ?? "连不上")}
        </div>
        {report.native && (
          <KeyVal
            items={[
              ["ComfyUI 版本", report.native.comfyVersion ?? "—"],
              ["节点数", String(report.native.nodeCount ?? "—")],
              ["GPU", report.native.gpu ?? "—"],
              ["显存", report.native.vramTotalGb ? `${report.native.vramTotalGb.toFixed(1)} GB` : "—"],
              ["H3 支持", report.native.caps?.h3?.MiniMaxH3ImageToVideo ? "MiniMaxH3ImageToVideo 在" : "不在（需 ≥0.30.0）"],
              ["缺失模型", report.native.missingModels?.length ? report.native.missingModels.join("、") : "无"],
            ]}
          />
        )}
        {report.task?.queue && (
          <KeyVal
            items={[
              ["key 类型", report.task.queue.apiKeyType ?? "—"],
              ["并发上限", String(report.task.queue.concurrentLimit ?? "—")],
              ["在跑 / 排队", `${report.task.queue.runningCount ?? 0} / ${report.task.queue.queuedCount ?? 0}`],
              ["余额", report.task.account?.remainMoney != null ? fmtMoney(report.task.account.remainMoney) : "—"],
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

function AddInstanceModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const api = useApi();
  const mut = useInstanceMutations();
  const [protocol, setProtocol] = useState<GenInstance["protocol"]>("comfy_native");
  const [placement, setPlacement] = useState<GenInstance["placement"]>("local");
  const [name, setName] = useState("");
  const [baseUrl, setBaseUrl] = useState("http://127.0.0.1:8188");
  const [apiKey, setApiKey] = useState("");
  const [site, setSite] = useState<"cn" | "global">("cn");
  const [instanceType, setInstanceType] = useState<"default" | "plus" | "ultra">("default");
  const [localOutputRoot, setLocalOutputRoot] = useState("");
  const [isDefault, setIsDefault] = useState(false);
  const [dry, setDry] = useState<{ loading: boolean; r: ProbeReport | null }>({ loading: false, r: null });

  function pickProtocol(p: GenInstance["protocol"], pl: GenInstance["placement"]) {
    setProtocol(p);
    setPlacement(pl);
    if (pl === "local") setBaseUrl("http://127.0.0.1:8188");
    if (p === "rh_task") setBaseUrl("https://www.runninghub.cn");
    if (p === "comfy_native" && pl === "cloud_runninghub") setBaseUrl("https://www.runninghub.cn/proxy/");
  }

  const presets = [
    { key: "local", label: "本机 ComfyUI", protocol: "comfy_native" as const, placement: "local" as const, url: "http://127.0.0.1:8188" },
    { key: "self", label: "自建云端（cloudflared 隧道）", protocol: "comfy_native" as const, placement: "cloud_self" as const, url: "https://" },
    { key: "rhproxy", label: "RunningHub 原生代理", protocol: "comfy_native" as const, placement: "cloud_runninghub" as const, url: "https://www.runninghub.cn/proxy/" },
    { key: "rhtask", label: "RunningHub 任务 API", protocol: "rh_task" as const, placement: "cloud_runninghub" as const, url: "https://www.runninghub.cn" },
  ];

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="添加生成实例"
      width={660}
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
              const r = await api.instances.dryProbe({
                protocol,
                baseUrl: placement === "cloud_runninghub" && protocol === "comfy_native" && apiKey ? baseUrl.replace(/\/$/, "") + "/" + apiKey : baseUrl,
                apiKey: apiKey || undefined,
                site,
              });
              setDry({ loading: false, r });
            }}
          >
            先试连通
          </Button>
          <Button
            variant="primary"
            disabled={!name.trim() || !baseUrl.trim()}
            loading={mut.create.isPending}
            onClick={async () => {
              await mut.create.mutateAsync({
                name: name.trim(),
                protocol,
                placement,
                baseUrl: placement === "cloud_runninghub" && protocol === "comfy_native" && apiKey ? baseUrl.replace(/\/$/, "") + "/" + apiKey : baseUrl.trim(),
                apiKeySet: !!apiKey,
                site: placement === "cloud_runninghub" ? site : undefined,
                instanceType: placement === "cloud_runninghub" ? instanceType : undefined,
                localOutputRoot: placement === "local" ? localOutputRoot || null : null,
                isDefault,
              });
              onClose();
            }}
          >
            保存并探活
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <Field label="接法">
          <div className="grid grid-cols-2 gap-1.5">
            {presets.map((p) => (
              <button
                key={p.key}
                onClick={() => {
                  pickProtocol(p.protocol, p.placement);
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

        <Field label="显示名">
          <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="本机 4090" />
        </Field>

        <Field
          label={protocol === "rh_task" ? "API 根地址" : "ComfyUI 地址"}
          hint={
            protocol === "comfy_native" && placement === "cloud_runninghub"
              ? "key 会拼到路径里：https://www.runninghub.cn/proxy/{apiKey}。这个 URL 属于凭据，我们不会把它显示在前端列表或日志里。"
              : placement === "cloud_self"
                ? "远端 ComfyUI 只绑 loopback，由 cloudflared 转发；隧道 config 里要设 originRequest.httpHostHeader: 127.0.0.1:8188。"
                : "本机 ComfyUI 不要加 --enable-cors-header：我们走服务端调用，开了反而拆掉同站点防护。"
          }
        >
          <Input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} className="mono" />
        </Field>

        {placement === "cloud_runninghub" && (
          <>
            <Field label="apiKey" hint="在 RunningHub 控制台的 API 调用页取。cn 站的 key 在 global 站不能用，两站余额与素材互不通用。">
              <Input type="password" value={apiKey} onChange={(e) => setApiKey(e.target.value)} className="mono" placeholder="留空表示稍后再填" />
            </Field>
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
            {protocol === "comfy_native" && (
              <p className="text-caption leading-snug text-ink-mute">
                原生代理这一路没有排队与计费接口，且实测并发不支持 —— 我们会把它的并发锁成 1。要批量出片请另配任务 API。
              </p>
            )}
          </>
        )}

        {placement === "local" && (
          <Field label="ComfyUI output 目录（可选）" hint="填了就直接从磁盘搬产物，省一次网络下载。">
            <Input value={localOutputRoot} onChange={(e) => setLocalOutputRoot(e.target.value)} className="mono" placeholder="F:/H3/comfyui/ComfyUI/output" />
          </Field>
        )}

        <Toggle checked={isDefault} onChange={setIsDefault} label="设为默认执行实例" />

        {dry.r && <ProbeReportBlock r={dry.r} />}
      </div>
    </Modal>
  );
}

function ProbeReportBlock({ r }: { r: ProbeReport }) {
  return (
    <div className={cn("rounded-ctl border px-2.5 py-2 text-note", r.ok ? "border-state-ok/40" : "border-state-fail/40")}>
      {r.ok ? "连通" : (r.error ?? "连不上")}
      {r.hints?.map((h) => (
        <div key={h} className="mt-1 text-ink-mute">
          · {h}
        </div>
      ))}
    </div>
  );
}
