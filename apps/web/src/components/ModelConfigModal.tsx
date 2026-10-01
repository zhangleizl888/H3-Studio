import { useEffect, useState } from "react";
import type { JSX, ReactNode } from "react";
import { Check, Cpu, Image as ImageIcon, KeyRound, MessageSquare, Pencil, Plus, Star, Trash2, Video, X, Zap } from "lucide-react";
import type { Api } from "../lib/api";
import type { GenInstance, LlmBackend, LlmCaps, Project, Workflow } from "../lib/types";
import { Badge, Button, Empty, Field, Input, MachChip, Modal, Panel, Select, StateGlyph, Toggle } from "../components/ui";
import { useGpuMutations, useGpuState, useInstanceMutations, useInstances, useLlmMutations, useLlms, useProject, useProjectMutations, useWorkflows } from "../lib/hooks";
import { CANVAS } from "../lib/constants";
import { cn, fmtTime } from "../lib/utils";

/*
 * 模型配置弹窗：一个片子开跑前要定的四件事 —— 用哪台机器、用哪个文本模型、
 * 用哪个出图模板、用哪个出片模板。
 *
 * projectId 决定写回哪一层：有项目就写项目配置（IndexedDB 里的那份），
 * 没有项目就只动全局默认（实例 isDefault / 文本后端 setDefault）。两条路都不报错。
 */

type TabKey = "global" | "chat" | "image" | "video";

const TABS: { key: TabKey; label: string; icon: ReactNode }[] = [
  { key: "global", label: "全局配置", icon: <KeyRound className="h-3.5 w-3.5" aria-hidden /> },
  { key: "chat", label: "对话模型", icon: <MessageSquare className="h-3.5 w-3.5" aria-hidden /> },
  { key: "image", label: "图片模型", icon: <ImageIcon className="h-3.5 w-3.5" aria-hidden /> },
  { key: "video", label: "视频模型", icon: <Video className="h-3.5 w-3.5" aria-hidden /> },
];

export default function ModelConfigModal({ open, onClose, projectId }: { open: boolean; onClose: () => void; projectId?: string }): JSX.Element | null {
  const [tab, setTab] = useState<TabKey>("global");
  const { data: project } = useProject(projectId);
  const { data: instances } = useInstances();
  const { data: local } = useLlms("local");
  const { data: cloud } = useLlms("cloud");
  const { data: workflows } = useWorkflows();

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/65 p-6" onMouseDown={onClose}>
      <div
        role="dialog"
        aria-modal="true"
        aria-label="模型配置"
        onMouseDown={(e) => e.stopPropagation()}
        className="mt-6 mb-6 flex w-full max-w-[860px] flex-col overflow-hidden rounded-sheet border border-hairline bg-slate shadow-[0_24px_70px_rgba(0,0,0,.6)]"
      >
        <header className="flex flex-none items-center gap-3 border-b border-hairline px-6 py-4">
          <span className="grid h-10 w-10 flex-none place-items-center rounded-panel border border-chrome/25 bg-chrome/10 text-chrome">
            <Cpu className="h-5 w-5" aria-hidden />
          </span>
          <span className="min-w-0">
            <span className="block text-title font-semibold tracking-tight">模型配置</span>
            <span className="label-mono block">Model Configuration</span>
          </span>
          <button onClick={onClose} aria-label="关闭" className="ml-auto rounded-ctl p-2 text-ink-mute hover:bg-hairline hover:text-ink">
            <X className="h-4 w-4" aria-hidden />
          </button>
        </header>

        <div className="flex flex-none border-b border-hairline" role="tablist">
          {TABS.map((t) => (
            <button
              key={t.key}
              role="tab"
              aria-selected={tab === t.key}
              onClick={() => setTab(t.key)}
              className={cn(
                "flex flex-1 items-center justify-center gap-2 border-b-2 py-3 text-note font-semibold tracking-tight transition-colors",
                tab === t.key
                  ? "border-chrome bg-chrome/10 text-ink"
                  : "border-transparent text-ink-mute hover:bg-sheen hover:text-ink-dim",
              )}
            >
              {t.icon}
              {t.label}
            </button>
          ))}
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto p-6">
          {tab === "global" && <GlobalTab project={project} instances={instances ?? []} backends={[...(local ?? []), ...(cloud ?? [])]} />}
          {tab === "chat" && <ChatTab local={local ?? []} cloud={cloud ?? []} />}
          {tab === "image" && <TemplateTab family="image" project={project} workflows={workflows ?? []} fallback="qwen_image" />}
          {tab === "video" && <TemplateTab family="video" project={project} workflows={workflows ?? []} fallback="h3_video" />}
        </div>

        <footer className="flex flex-none flex-wrap items-center gap-3 border-t border-hairline bg-sheen px-6 py-3.5">
          <p className="text-caption leading-snug text-ink-mute">
            配置仅保存在本地浏览器（IndexedDB）· 实例与文本后端登记在后端服务上
          </p>
          <Button variant="primary" className="ml-auto rounded-panel px-5" onClick={onClose}>
            完成
          </Button>
        </footer>
      </div>
    </div>
  );
}

/* ───────── ① 全局配置 ───────── */

function GlobalTab({ project, instances, backends }: { project?: Project; instances: GenInstance[]; backends: LlmBackend[] }) {
  const mut = useProjectMutations(project?.id);
  const inst = useInstanceMutations();
  const llm = useLlmMutations();
  const imageDefault = instances.find((i) => i.isDefault);

  return (
    <div className="space-y-4">
      <Panel
        title={project ? "本项目默认实例" : "全局默认实例"}
        actions={project && <Badge>{project.name}</Badge>}
      >
        {project ? (
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="图片实例" hint="定妆照、场景图、关键帧都走它。">
              <Select
                className="w-full"
                value={project.config.imageInstanceId ?? ""}
                onChange={(e) => mut.config.mutate({ imageInstanceId: e.target.value || null })}
              >
                <option value="">跟随全局默认{imageDefault ? `（${imageDefault.name}）` : ""}</option>
                {instances.map((i) => (
                  <option key={i.id} value={i.id}>
                    {i.name} · {placementLabel(i.placement)}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="视频实例" hint="出片比出图吃显存得多，常常是另一台机器。">
              <Select
                className="w-full"
                value={project.config.videoInstanceId ?? ""}
                onChange={(e) => mut.config.mutate({ videoInstanceId: e.target.value || null })}
              >
                <option value="">跟随全局默认{imageDefault ? `（${imageDefault.name}）` : ""}</option>
                {instances.map((i) => (
                  <option key={i.id} value={i.id}>
                    {i.name} · {placementLabel(i.placement)}
                  </option>
                ))}
              </Select>
            </Field>
          </div>
        ) : (
          <div className="space-y-2">
            <p className="text-note leading-snug text-ink-mute">
              没打开项目，这里改的是全局默认：派发任务时项目没指定实例就用它。要按项目分开设，回项目库进入某个项目。
            </p>
            {instances.length === 0 ? (
              <Empty title="还没有生成实例" hint="在「设置 → 生成实例」里加本机 ComfyUI 或 RunningHub。" />
            ) : (
              <ul className="divide-y divide-rule-soft">
                {instances.map((i) => (
                  <li key={i.id} className="flex flex-wrap items-center gap-2 py-2">
                    <MachChip placement={i.placement} label={i.name} />
                    <span className="mono text-caption text-ink-mute">{i.baseUrl}</span>
                    <span className={cn("text-caption", i.lastProbeOk ? "text-state-ok" : "text-state-fail")}>
                      {i.lastProbeOk ? "可用" : i.lastProbeOk === false ? "不可用" : "未探活"}
                    </span>
                    {i.isDefault ? (
                      <span className="ml-auto inline-flex items-center gap-1 text-caption text-mach-local">
                        <Star className="h-3 w-3" aria-hidden /> 全局默认
                      </span>
                    ) : (
                      <Button size="sm" variant="quiet" className="ml-auto" loading={inst.update.isPending} onClick={() => inst.update.mutate({ id: i.id, body: { isDefault: true } })}>
                        设为默认
                      </Button>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </Panel>

      <Panel title="默认文本后端" actions={project && <Badge>{project.name}</Badge>}>
        {project ? (
          <Field label="剧本拆解 / 分镜 / 提示词改写共用这一个" hint="留空则用后端登记的各用途默认。">
            <Select className="w-full" value={project.config.llmBackendId ?? ""} onChange={(e) => mut.config.mutate({ llmBackendId: e.target.value || null })}>
              <option value="">跟随全局默认</option>
              {backends.map((b) => (
                <option key={b.id} value={b.id}>
                  {b.scope === "local" ? "本地" : "云端"} · {b.name}
                </option>
              ))}
            </Select>
          </Field>
        ) : (
          <div className="space-y-2">
            <p className="text-note leading-snug text-ink-mute">
              没打开项目：这里改的是全局默认，本地与云端各记一个，切到「对话模型」标签页也能设。
            </p>
            {backends.length === 0 ? (
              <Empty title="还没有文本后端" hint="切到「对话模型」标签页添加：本地 llama.cpp / Ollama，或云端 OpenAI 兼容端点。" />
            ) : (
              <ul className="divide-y divide-rule-soft">
                {backends.map((b) => (
                  <li key={b.id} className="flex flex-wrap items-center gap-2 py-2">
                    <StateGlyph state={b.lastProbeOk ? "succeeded" : b.lastProbeOk === false ? "failed" : "idle"} />
                    <span className="text-body">{b.name}</span>
                    <Badge>{b.scope === "local" ? "本地" : "云端"}</Badge>
                    {b.isDefault && (
                      <span className="inline-flex items-center gap-1 text-caption text-mach-local">
                        <Star className="h-3 w-3" aria-hidden /> 本组默认
                      </span>
                    )}
                    {!b.isDefault && (
                      <Button size="sm" variant="quiet" className="ml-auto" onClick={() => llm.setDefault.mutate(b.id)}>
                        设为默认
                      </Button>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </Panel>

      <ArbiterPanel />
    </div>
  );
}

/** 单卡仲裁：一张卡上文本模型与生成任务轮流用显存 */
function ArbiterPanel() {
  const { data: gpu } = useGpuState();
  const mut = useGpuMutations();
  const enabled = gpu?.enabled ?? false;
  const holder = gpu?.renderingLocally
    ? "本机渲染正占着这张卡（出图/出片在跑）"
    : gpu?.llmRunning
      ? `文本模型在卡上${gpu.llmPort ? ` · 端口 ${gpu.llmPort}` : ""}${gpu.llmPid ? ` · PID ${gpu.llmPid}` : ""}`
      : gpu?.yielded
        ? "文本模型已让位，进程被停掉了，等恢复"
        : "这张卡现在是空的";

  return (
    <Panel
      title={
        <span className="flex items-center gap-2">
          <Zap className="h-3.5 w-3.5" />
          单卡仲裁
        </span>
      }
      actions={<Badge tone={!gpu ? "neutral" : enabled ? "good" : "warn"}>{!gpu ? "读不到" : enabled ? "已启用" : "未启用"}</Badge>}
    >
      <div className="space-y-3">
        <Toggle
          checked={enabled}
          disabled
          onChange={() => {}}
          label="让文本模型与出图/出片轮流用这张卡"
          hint="开关在后端环境变量：H3_GPU_ARBITER=on / off（不设则默认开）。界面改不了它，因为这是重启才生效的进程级设置。"
        />

        <div className="rounded-ctl border border-hairline bg-sheen px-3 py-2.5">
          <div className="label-mono">谁正占着这张卡</div>
          <div className="mt-1 text-body text-ink">
            {!gpu ? "还没读到 /api/system/gpu —— 后端没起来时这里给不出结论" : enabled ? holder : "仲裁关着 —— 文本模型和生成任务会同时抢显存"}
          </div>
          {gpu?.lastError && <div className="mt-1 text-caption leading-snug text-state-fail">{gpu.lastError}</div>}
        </div>

        {!gpu ? (
          <p className="text-note leading-relaxed text-ink-mute">
            读不到仲裁状态：后端未启动或 <span className="mono text-ink-dim">/api/system/gpu</span> 报错。
            开关本身在后端环境变量 <span className="mono text-ink-dim">H3_GPU_ARBITER</span>（on / off，不设则默认开）。
          </p>
        ) : enabled ? (
          <div className="flex flex-wrap items-center gap-2">
            <Button
              size="sm"
              disabled={!gpu?.llmRunning || gpu?.yielded}
              loading={mut.yield.isPending}
              onClick={() => mut.yield.mutate()}
            >
              让文本模型让位
            </Button>
            <Button size="sm" variant="quiet" disabled={!gpu?.yielded} loading={mut.restore.isPending} onClick={() => mut.restore.mutate()}>
              恢复文本模型
            </Button>
            <span className="text-caption text-ink-mute">
              24 GB 装不下 27B 文本模型 + 图像/视频底模；派发本地产物时会自动让位，这里是手动兜底。
            </span>
          </div>
        ) : (
          <p className="text-note leading-relaxed text-ink-mute">
            这台后端没开单卡仲裁（<span className="mono text-ink-dim">H3_GPU_ARBITER=off</span>，或仲裁器没装配）：不会自动停文本模型腾显存，
            出图/出片前先自己确认模型没在跑，否则就是 ComfyUI OOM。想开回来：在 <span className="mono">apps/api/.env</span> 里删掉这行或改成
            <span className="mono"> on</span>，重启后端。
          </p>
        )}

        {(mut.yield.data || mut.restore.data) && (
          <p className="text-caption text-ink-dim">
            {mut.yield.data ? `已让位：${mut.yield.data.reason ?? "文本模型进程已停"}` : `已恢复：${mut.restore.data?.reason ?? "文本模型重新在跑"}`}
          </p>
        )}
      </div>
    </Panel>
  );
}

/* ───────── ② 对话模型 ───────── */

type CreateBody = Parameters<Api["llm"]["create"]>[0];
type UpdateBody = Parameters<Api["llm"]["update"]>[1];

function ChatTab({ local, cloud }: { local: LlmBackend[]; cloud: LlmBackend[] }) {
  const [form, setForm] = useState<{ scope: "local" | "cloud"; editing?: LlmBackend } | null>(null);
  return (
    <div className="space-y-4">
      <p className="text-note leading-snug text-ink-mute">
        剧本拆解、分镜、提示词改写都走这里。本地与云端分开记默认：断网了本地产线照跑，云端只是长剧本的兜底。
      </p>
      <BackendGroup
        title="本地"
        desc="llama.cpp / Ollama / LM Studio / vLLM，看协议不看服务商。"
        backends={local}
        onAdd={() => setForm({ scope: "local" })}
        onEdit={(b) => setForm({ scope: b.scope, editing: b })}
      />
      <BackendGroup
        title="云端"
        desc="GitCC 一类 OpenAI 兼容端点；key 只进不出，界面永远只回脱敏地址。"
        backends={cloud}
        onAdd={() => setForm({ scope: "cloud" })}
        onEdit={(b) => setForm({ scope: b.scope, editing: b })}
      />
      <LlmFormModal state={form} onClose={() => setForm(null)} />
    </div>
  );
}

function BackendGroup({
  title,
  desc,
  backends,
  onAdd,
  onEdit,
}: {
  title: string;
  desc: string;
  backends: LlmBackend[];
  onAdd: () => void;
  onEdit: (b: LlmBackend) => void;
}) {
  const mut = useLlmMutations();
  const [confirmRemove, setConfirmRemove] = useState<LlmBackend | null>(null);

  return (
    <Panel
      title={
        <span className="flex items-center gap-2">
          {title}模型
          <Badge>{backends.length}</Badge>
        </span>
      }
      actions={
        <Button size="sm" variant="quiet" icon={<Plus className="h-3 w-3" />} onClick={onAdd}>
          新增
        </Button>
      }
    >
      <p className="mb-2 text-caption leading-snug text-ink-mute">{desc}</p>
      {backends.length === 0 ? (
        <Empty title={`还没有${title}文本后端`} hint="点上面的「新增」填名称、协议与地址；保存后会自动探一次活。" />
      ) : (
        <ul className="divide-y divide-rule-soft">
          {backends.map((b) => (
            <LlmRow
              key={b.id}
              backend={b}
              probing={mut.probe.isPending && mut.probe.variables === b.id}
              onProbe={() => mut.probe.mutate(b.id)}
              onDefault={() => mut.setDefault.mutate(b.id)}
              onEdit={() => onEdit(b)}
              onRemove={() => setConfirmRemove(b)}
            />
          ))}
        </ul>
      )}
      <LlmRemoveModal
        backend={confirmRemove}
        onClose={() => setConfirmRemove(null)}
        onConfirm={async () => {
          if (!confirmRemove) return;
          await mut.remove.mutateAsync(confirmRemove.id);
          setConfirmRemove(null);
        }}
      />
    </Panel>
  );
}

function LlmRow({
  backend: b,
  probing,
  onProbe,
  onDefault,
  onEdit,
  onRemove,
}: {
  backend: LlmBackend;
  probing: boolean;
  onProbe: () => void;
  onDefault: () => void;
  onEdit: () => void;
  onRemove: () => void;
}) {
  const caps = capsOf(b);
  const ctx = caps.ctxTotal ?? caps.ctxSize ?? null;
  const models = caps.models ?? [];
  return (
    <li className="space-y-1.5 py-2.5">
      <div className="flex flex-wrap items-center gap-2">
        <StateGlyph state={b.lastProbeOk ? "succeeded" : b.lastProbeOk === false ? "failed" : "idle"} />
        <span className="text-body font-medium">{b.name}</span>
        <Badge>{b.scope === "local" ? "本地" : "云端"}</Badge>
        <Badge>{b.kind === "ollama" ? "Ollama 原生" : "OpenAI 兼容"}</Badge>
        {b.isDefault && (
          <span className="inline-flex items-center gap-1 text-caption text-mach-local">
            <Star className="h-3 w-3" aria-hidden /> 本组默认
          </span>
        )}
        {caps.enterpriseSharedOnly && <Badge tone="warn">仅企业级-共享 key</Badge>}
        <div className="ml-auto flex items-center gap-1.5">
          <Button size="sm" variant="quiet" loading={probing} onClick={onProbe}>
            探活
          </Button>
          {!b.isDefault && (
            <Button size="sm" variant="quiet" onClick={onDefault}>
              设为默认
            </Button>
          )}
          <Button size="sm" variant="quiet" icon={<Pencil className="h-3 w-3" />} onClick={onEdit}>
            编辑
          </Button>
          <Button size="sm" variant="quiet" aria-label={`删除 ${b.name}`} onClick={onRemove}>
            <Trash2 className="h-3 w-3" />
          </Button>
        </div>
      </div>

      <div className="mono text-caption text-ink-mute">
        {b.baseUrl}
        {b.apiKeySet ? " · key 已配置" : " · 未配 key"}
        {caps.model ? ` · ${caps.model}` : ""}
      </div>

      {b.lastProbeOk === false && b.lastError && <div className="text-caption leading-snug text-state-fail">{b.lastError}</div>}

      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-caption text-ink-mute">
        <span>
          上下文上限 <span className="mono text-ink-dim">{ctx ?? "未探到"}</span>
          {ctx === null ? "" : caps.ctxIsPerRequest ? "（可按请求调整）" : "（由启动参数决定）"}
        </span>
        {models.length > 0 && <span>模型 {models.length} 个</span>}
        <span className="ml-auto">上次探活 {fmtTime(b.lastProbeAt)}</span>
      </div>

      {ctx !== null && !caps.ctxIsPerRequest && (
        <div className="text-caption leading-snug text-ink-mute">上下文由启动参数决定，改它要重启服务 —— 请求里带 num_ctx 是没用的。</div>
      )}

      {models.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {models.slice(0, 8).map((m) => (
            <span key={m} className="mono rounded-panel border border-rule-soft bg-sheen px-1.5 py-[1px] text-caption text-ink-dim">
              {m}
            </span>
          ))}
          {models.length > 8 && <span className="text-caption text-ink-mute">还有 {models.length - 8} 个</span>}
        </div>
      )}
    </li>
  );
}

function LlmRemoveModal({ backend, onClose, onConfirm }: { backend: LlmBackend | null; onClose: () => void; onConfirm: () => Promise<void> }) {
  const [busy, setBusy] = useState(false);
  return (
    <Modal
      open={!!backend}
      onClose={onClose}
      title={`删除文本后端「${backend?.name ?? ""}」？`}
      width={480}
      footer={
        <>
          <Button variant="quiet" onClick={onClose} disabled={busy}>
            取消
          </Button>
          <Button
            variant="danger"
            loading={busy}
            onClick={async () => {
              setBusy(true);
              await onConfirm();
              setBusy(false);
            }}
          >
            删除
          </Button>
        </>
      }
    >
      <p className="text-note leading-relaxed text-ink-dim">
        只删这条登记记录，不动你机器上的模型文件。已经指向它的用途默认会一起变空，需要重新指定。
      </p>
    </Modal>
  );
}

interface FormState {
  name: string;
  scope: "local" | "cloud";
  kind: "ollama" | "openai_compat";
  baseUrl: string;
  apiKey: string;
  model: string;
}

function LlmFormModal({ state, onClose }: { state: { scope: "local" | "cloud"; editing?: LlmBackend } | null; onClose: () => void }) {
  const mut = useLlmMutations();
  const editing = state?.editing;
  const [form, setForm] = useState<FormState>({ name: "", scope: "local", kind: "ollama", baseUrl: "http://127.0.0.1:11434", apiKey: "", model: "" });

  useEffect(() => {
    if (!state) return;
    setForm(
      state.editing
        ? {
            name: state.editing.name,
            scope: state.editing.scope,
            kind: state.editing.kind,
            baseUrl: state.editing.baseUrl,
            apiKey: "",
            model: capsOf(state.editing).model ?? "",
          }
        : {
            name: "",
            scope: state.scope,
            kind: state.scope === "local" ? "ollama" : "openai_compat",
            baseUrl: state.scope === "local" ? "http://127.0.0.1:11434" : "https://",
            apiKey: "",
            model: "",
          },
    );
  }, [state]);

  const set = <K extends keyof FormState>(k: K, v: FormState[K]) => setForm((f) => ({ ...f, [k]: v }));

  const submit = async () => {
    const name = form.name.trim();
    const baseUrl = form.baseUrl.trim();
    if (!name || !baseUrl) return;
    if (editing) {
      const body: UpdateBody & { apiKey?: string; model?: string } = {};
      body.name = name;
      body.scope = form.scope;
      body.kind = form.kind;
      // 服务端回的是脱敏地址：没改过就别写回去，否则把 <REDACTED> 存进库里
      if (baseUrl !== editing.baseUrl) body.baseUrl = baseUrl;
      if (form.apiKey.trim()) body.apiKey = form.apiKey.trim();
      if (form.model.trim()) body.model = form.model.trim();
      await mut.update.mutateAsync({ id: editing.id, body });
      await mut.probe.mutateAsync(editing.id);
    } else {
      const body = { name, scope: form.scope, kind: form.kind, baseUrl } as CreateBody & { apiKey?: string; model?: string };
      if (form.apiKey.trim()) body.apiKey = form.apiKey.trim();
      if (form.model.trim()) body.model = form.model.trim();
      const created = await mut.create.mutateAsync(body);
      if (created?.id) await mut.probe.mutateAsync(created.id);
    }
    onClose();
  };

  return (
    <Modal
      open={!!state}
      onClose={onClose}
      title={editing ? `编辑「${editing.name}」` : state?.scope === "cloud" ? "添加云端文本后端" : "添加本地文本后端"}
      width={560}
      footer={
        <>
          <Button variant="quiet" onClick={onClose} disabled={mut.create.isPending || mut.update.isPending}>
            取消
          </Button>
          <Button
            variant="primary"
            loading={mut.create.isPending || mut.update.isPending || mut.probe.isPending}
            disabled={!form.name.trim() || !form.baseUrl.trim()}
            onClick={() => void submit()}
          >
            {editing ? "保存并探活" : "创建并探活"}
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="名称">
            <Input value={form.name} onChange={(e) => set("name", e.target.value)} placeholder="本地 llama.cpp (router)" />
          </Field>
          <Field label="归属" hint="只影响分组显示，不参与分派逻辑。">
            <Select className="w-full" value={form.scope} onChange={(e) => set("scope", e.target.value as FormState["scope"])}>
              <option value="local">本地</option>
              <option value="cloud">云端</option>
            </Select>
          </Field>
        </div>
        <Field label="协议" hint="选 Ollama 走原生 /api/chat（流式 NDJSON）；其余按 OpenAI 兼容协议说话。">
          <Select className="w-full" value={form.kind} onChange={(e) => set("kind", e.target.value as FormState["kind"])}>
            <option value="ollama">Ollama 原生</option>
            <option value="openai_compat">OpenAI 兼容</option>
          </Select>
        </Field>
        <Field label="Base URL" hint="llama.cpp 记得带 /v1；它的上下文由启动参数 -c 决定，改不了。">
          <Input className="mono w-full" value={form.baseUrl} onChange={(e) => set("baseUrl", e.target.value)} placeholder="http://127.0.0.1:8080/v1" />
        </Field>
        <Field label="API Key" hint={editing ? "留空表示不动已存的那把；key 只进不出，界面读回来永远是脱敏的。" : "本地服务一般不需要；云端必填。"}>
          <Input className="mono w-full" type="password" value={form.apiKey} onChange={(e) => set("apiKey", e.target.value)} placeholder={editing ? "••••••••（不修改）" : "sk-…"} />
        </Field>
        <Field label="模型名" hint="探活回来的列表里没有你要用的模型时，在这里写死一个。">
          <Input className="mono w-full" value={form.model} onChange={(e) => set("model", e.target.value)} placeholder="qwen3:14b" />
        </Field>
      </div>
    </Modal>
  );
}

/* ───────── ③④ 图片 / 视频模型 ───────── */

function TemplateTab({ family, project, workflows, fallback }: { family: "image" | "video"; project?: Project; workflows: Workflow[]; fallback: string }) {
  const mut = useProjectMutations(project?.id);
  // 任务种类优先（后端按它过滤），老数据没这个字段时退回 family
  const list = workflows.filter((w) => (w.taskKind ?? w.family) === family);
  const current = project ? (family === "image" ? project.config.imageTemplate : project.config.videoTemplate) || fallback : fallback;
  const builtinOnly = list.filter((w) => !w.id.startsWith("builtin:") && !w.isBuiltin);
  const usable = builtinOnly.filter((w) => !(w.gaps?.length));

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="max-w-[520px] text-note leading-snug text-ink-mute">
          {family === "image"
            ? "出图模板决定角色定妆、场景图与关键帧用哪张 ComfyUI 图。槽位由后端按模板拼，浏览器不碰 graph。"
            : "出片模板决定镜头怎么变成视频：文生 / 首帧 / 首尾帧三合一在同一个模板里，看有没有喂帧图。"}
        </p>
        {project ? <Badge>{project.name}</Badge> : <Badge tone="warn">未打开项目</Badge>}
      </div>

      <button
        disabled={!project}
        onClick={() => mut.config.mutate(family === "image" ? { imageTemplate: "auto" } : { videoTemplate: "auto" })}
        className={cn(
          "w-full rounded-panel border p-3 text-left transition-colors",
          current === "auto" ? "border-chrome/50 bg-chrome/10" : "border-hairline bg-sheen hover:border-hairline",
          !project && "cursor-not-allowed opacity-60",
        )}
      >
        <div className="flex flex-wrap items-center gap-2">
          <span className={cn("grid h-4 w-4 flex-none place-items-center rounded-full border", current === "auto" ? "border-chrome bg-chrome text-chrome-ink" : "border-rule")}>
            {current === "auto" && <Check className="h-2.5 w-2.5" aria-hidden />}
          </span>
          <span className="text-body font-semibold">自动：按任务从工作流库挑</span>
          <Badge tone={current === "auto" ? "good" : undefined}>推荐</Badge>
          <span className="text-caption text-ink-mute">
            每次派发前按「这次任务给了什么」打分：有参考视频就走动作迁移，只给首尾帧就走一键出片；库里挑不出才回落内置模板，并在参数表里写明是回落。
          </span>
        </div>
        {project && (
          <p className="mt-1.5 pl-6 text-caption leading-snug text-ink-mute">
            当前可用候选 {usable.length} 条{builtinOnly.length > usable.length ? `，另有 ${builtinOnly.length - usable.length} 条因本机缺节点/权重不参与` : ""}
          </p>
        )}
      </button>

      {list.length === 0 ? (
        <Empty title={`库里没有${family === "image" ? "出图" : "出片"}模板`} hint="后端没起来，或工作流库里还没有这个族的工作流。内置模板由后端 /api/workflows 一并给出。" />
      ) : (
        <ul className="space-y-2">
          {list.map((w) => {
            const key = templateKeyOf(w);
            const selected = key === current;
            const gaps = w.gaps ?? [];
            const signals = w.signals ?? [];
            return (
              <li key={w.id}>
                <button
                  disabled={!project || gaps.length > 0}
                  title={gaps.length ? `本机还缺：${gaps.map((g) => g.class_type).join("、")}` : undefined}
                  onClick={() => mut.config.mutate(family === "image" ? { imageTemplate: key } : { videoTemplate: key })}
                  className={cn(
                    "w-full rounded-panel border p-3 text-left transition-colors",
                    selected ? "border-chrome/50 bg-chrome/10" : "border-hairline bg-sheen hover:border-hairline",
                    (!project || gaps.length > 0) && "cursor-not-allowed opacity-60",
                  )}
                >
                  <div className="flex flex-wrap items-center gap-2">
                    <span className={cn("grid h-4 w-4 flex-none place-items-center rounded-full border", selected ? "border-chrome bg-chrome text-chrome-ink" : "border-rule")}>
                      {selected && <Check className="h-2.5 w-2.5" aria-hidden />}
                    </span>
                    <span className="text-body font-semibold">{w.name}</span>
                    <Badge>{w.isBuiltin || w.id.startsWith("builtin:") ? "内置" : "导入"}</Badge>
                    {gaps.length > 0 && <Badge tone="warn">缺 {gaps.length} 处</Badge>}
                    {w.verifiedAt && <Badge tone="good">本机跑通过</Badge>}
                    {w.tags?.slice(0, 3).map((t) => (
                      <span key={t} className="mono rounded-panel border border-rule-soft px-1.5 py-[1px] text-micro text-ink-mute">
                        {t}
                      </span>
                    ))}
                    {selected && <span className="text-caption text-chrome">当前使用</span>}
                    <span className="mono ml-auto text-caption text-ink-mute">{key}</span>
                  </div>
                  {w.description && <p className="mt-1 pl-6 text-note leading-snug text-ink-mute">{w.description}</p>}
                  <div className="mt-1.5 pl-6 text-caption leading-snug text-ink-mute">
                    {signals.length > 0 ? (
                      <>
                        <span className="label-mono mr-1.5">吃任务</span>
                        {signals.slice(0, 7).map((s) => (
                          <span key={s.name} className="mono mr-1.5">
                            {s.label}
                            {s.required ? "*" : ""}
                          </span>
                        ))}
                        {signals.length > 7 ? `等 ${signals.length} 项` : ""}
                      </>
                    ) : (
                      slotSummary(w)
                    )}
                  </div>
                  {gaps.length > 0 && (
                    <p className="mt-1 pl-6 text-caption leading-snug text-warn">
                      这台实例跑不动：{gaps.slice(0, 2).map((g) => `${g.class_type}${g.pack ? `（要装 ${g.pack}）` : ""}`).join("；")}
                      {gaps.length > 2 ? ` 等 ${gaps.length} 处` : ""}
                    </p>
                  )}
                </button>
              </li>
            );
          })}
        </ul>
      )}

      {!project && (
        <p className="text-caption leading-snug text-ink-mute">
          没打开项目时这里只读：出图/出片模板是项目级配置（<span className="mono">project.config.imageTemplate / videoTemplate</span>），
          从项目库进入一个项目再改。
        </p>
      )}

      {project && builtinOnly.length > 0 && (
        <p className="text-caption leading-snug text-ink-mute">
          选中某条导入的工作流 = 每次都按这条填槽（后端把任务落到它自己的输入点上，素材会先上传到该实例）；
          选「自动」= 让后端按任务形状在库里挑。两条路都会先过参数表：缺必填输入或本机跑不动就不入队。
        </p>
      )}

      {family === "video" && <VideoShapePanel project={project} />}
    </div>
  );
}

function VideoShapePanel({ project }: { project?: Project }) {
  const mut = useProjectMutations(project?.id);
  return (
    <Panel title="分辨率档与画幅" actions={project && <Badge>{project.name}</Badge>}>
      {!project ? (
        <p className="text-note leading-snug text-ink-mute">未打开项目：这两项同样是项目配置，进入项目后再改。</p>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="分辨率档" hint={`预览档 ${CANVAS.preview.w}×${CANVAS.preview.h}（约 ${CANVAS.preview.mp} MP）出片快；全质量档 ${CANVAS.full.w}×${CANVAS.full.h}，硬上限 768×1344。`}>
            <Select className="w-full" value={project.config.resolutionMode} onChange={(e) => mut.config.mutate({ resolutionMode: e.target.value as "preview" | "full" })}>
              <option value="preview">预览档（Turbo 8 步）</option>
              <option value="full">全质量档（25 步）</option>
            </Select>
          </Field>
          <Field label="画幅" hint="影响出图尺寸与出片分辨率表，不改界面布局。">
            <Select className="w-full" value={project.config.aspectRatio} onChange={(e) => mut.config.mutate({ aspectRatio: e.target.value as "16:9" | "9:16" | "1:1" })}>
              <option value="16:9">横屏 16:9</option>
              <option value="9:16">竖屏 9:16</option>
              <option value="1:1">方形 1:1</option>
            </Select>
          </Field>
        </div>
      )}
    </Panel>
  );
}

/* ───────── 读法适配 ───────── */

/** 真实后端把能力放在 caps，mock 放在 capabilities：两边都读，缺什么就照实说什么 */
type CapsView = Partial<LlmCaps> & { ctxTotal?: number | null; slots?: number | null; reachable?: boolean; error?: string | null; model?: string | null };

function capsOf(b: LlmBackend): CapsView {
  const raw = (b as unknown as { caps?: CapsView }).caps ?? (b.capabilities as CapsView | undefined);
  return { models: [], hasJsonSchema: false, hasVision: false, hasTools: false, ctxIsPerRequest: false, supportsPull: false, ...raw };
}

/** builtin:qwen_image → qwen_image：后端按 TEMPLATES 的 key 建图 */
function templateKeyOf(w: Workflow): string {
  return w.id.startsWith("builtin:") ? w.id.slice("builtin:".length) : w.id;
}

function slotSummary(w: Workflow): string {
  const slots = (w.slots ?? []) as unknown as { name?: string; label?: string; address?: string; required?: boolean }[];
  if (!slots.length) return "没有解析出槽位";
  const names = slots.map((s) => s.label ?? s.name ?? s.address ?? "").filter(Boolean);
  const required = slots.filter((s) => s.required).length;
  return `${slots.length} 个槽位（必填 ${required}）：${names.slice(0, 6).join(" · ")}${names.length > 6 ? ` 等 ${names.length} 项` : ""}`;
}

function placementLabel(p: GenInstance["placement"]): string {
  return p === "local" ? "本机" : p === "cloud_self" ? "自建云" : "RunningHub";
}
