import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Download, Plus, RefreshCw, Radar, Star } from "lucide-react";
import { Badge, Button, Copyable, Empty, Field, Input, Modal, Panel, Select, StateGlyph, Toggle } from "../../components/ui";
import { useLlmDefaults, useLlmMutations, useLlms, useLocalScan } from "../../lib/hooks";
import type { DetectedBackend, LlmBackend } from "../../lib/types";
import { cn, fmtTime } from "../../lib/utils";

const PRESETS: Record<string, { label: string; baseUrl: string; kind: LlmBackend["kind"] }> = {
  ollama: { label: "Ollama（原生 API）", baseUrl: "http://127.0.0.1:11434", kind: "ollama" },
  llamacpp: { label: "llama.cpp llama-server", baseUrl: "http://127.0.0.1:8080/v1", kind: "openai_compat" },
  lmstudio: { label: "LM Studio", baseUrl: "http://127.0.0.1:1234/v1", kind: "openai_compat" },
  vllm: { label: "vLLM", baseUrl: "http://127.0.0.1:8000/v1", kind: "openai_compat" },
  cloud: { label: "任意云端 OpenAI 兼容端点", baseUrl: "https://", kind: "openai_compat" },
};

const DETECT_LABEL: Record<DetectedBackend, string> = {
  ollama: "Ollama",
  llamacpp: "llama.cpp",
  lmstudio: "LM Studio",
  vllm: "vLLM",
  unknown: "未知服务",
};

export default function Llm() {
  const nav = useNavigate();
  const { data: local } = useLlms("local");
  const { data: cloud } = useLlms("cloud");
  const { data: defaults } = useLlmDefaults();
  const mut = useLlmMutations();
  const { data: scan, refetch: rescan, isFetching } = useLocalScan(true);
  const [addFor, setAddFor] = useState<LlmBackend["scope"] | null>(null);

  return (
    <div className="space-y-4 p-4">
      <header className="flex items-end justify-between">
        <div>
          <h1 className="text-title font-semibold">AI 模型设置</h1>
          <p className="text-note text-ink-mute">文本环节（剧本拆解、分镜、提示词改写）的后端。本地与云端分开管理，互不拖累。</p>
        </div>
        <Button size="sm" icon={<Plus className="h-3.5 w-3.5" />} onClick={() => setAddFor("local")}>
          添加后端
        </Button>
      </header>

      {/* ① 本地可用连接：先探再说，避免用户手抄端口 */}
      <Panel
        title={
          <span className="flex items-center gap-2">
            <Radar className="h-3.5 w-3.5" />
            本地可用连接
          </span>
        }
        actions={
          <div className="flex items-center gap-2">
            <span className="text-caption text-ink-mute">按响应形状判别，不只看端口</span>
            <Button size="sm" variant="quiet" icon={<RefreshCw className={cn("h-3 w-3", isFetching && "animate-spin")} />} onClick={() => rescan()}>
              重新扫描
            </Button>
          </div>
        }
        dense
      >
        {!scan ? (
          <div className="px-3 py-6 text-center text-note text-ink-mute">正在探测 11434 / 8080 / 1234 / 8000…</div>
        ) : scan.found.every((f) => !f.ok) ? (
          <div className="space-y-3 p-3">
            <div className="rounded-ctl border border-mach-rh/45 bg-mach-rh/8 px-2.5 py-2 text-note">
              没有检测到任何在跑的本地推理服务。下面这些是本机已知的状态和启动方式。
            </div>
            <div className="grid gap-2 sm:grid-cols-2">
              {scan.found.map((f) => (
                <div key={f.port} className="rounded-ctl border border-rule-soft bg-slate px-2.5 py-2">
                  <div className="flex items-baseline justify-between">
                    <span className="mono text-note">:{f.port}</span>
                    <span className="text-caption text-ink-mute">{DETECT_LABEL[f.detectedAs]}</span>
                  </div>
                  <div className="mt-0.5 text-caption leading-snug text-ink-mute">{f.detail}</div>
                </div>
              ))}
            </div>
            <ul className="space-y-2">
              {scan.hints.map((h) => (
                <li key={h.backend} className="space-y-1 rounded-ctl border border-rule-soft bg-slate p-2.5">
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-note font-medium">{h.label}</span>
                    <Button size="sm" variant="quiet" onClick={() => setAddFor("local")}>
                      添加它
                    </Button>
                  </div>
                  {h.note && <div className="text-caption text-ink-mute">{h.note}</div>}
                  <Copyable text={h.command} className="mono block truncate rounded-panel bg-inset px-2 py-1 text-caption text-ink-dim">
                    {h.command}
                  </Copyable>
                </li>
              ))}
            </ul>
          </div>
        ) : (
          <ul className="divide-y divide-rule-soft">
            {scan.found
              .filter((f) => f.ok)
              .map((f) => (
                <li key={f.port} className="flex items-center justify-between gap-3 px-3 py-2">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <StateGlyph state="succeeded" />
                      <span className="text-body">{DETECT_LABEL[f.detectedAs]}</span>
                      <span className="mono text-caption text-ink-mute">:{f.port}</span>
                    </div>
                    <div className="mt-0.5 text-caption text-ink-mute">
                      {f.models != null && <>模型 <span className="mono">{f.models}</span> 个 · </>}
                      {f.ctxSize != null && <>上下文 <span className="mono">{f.ctxSize}</span></>}
                    </div>
                  </div>
                  <Button size="sm" onClick={() => setAddFor("local")}>
                    添加为本地后端
                  </Button>
                </li>
              ))}
          </ul>
        )}
      </Panel>

      {/* ② 本地 AI 模型 */}
      <BackendGroup
        title="本地 AI 模型"
        desc="剧本拆解与提示词改写默认走这里；断网也能干活。"
        backends={local ?? []}
        scope="local"
        onAdd={() => setAddFor("local")}
        onProbe={(id) => mut.probe.mutate(id)}
        onRemove={(id) => mut.remove.mutate(id)}
        onDefault={(id) => mut.setDefault.mutate(id)}
        busy={mut.probe.isPending || mut.remove.isPending}
      />

      {/* ③ 云端 AI 模型 */}
      <BackendGroup
        title="云端 AI 模型"
        desc="本地模型跑不动的长剧本再用它。云端不可用不影响本地产线。"
        backends={cloud ?? []}
        scope="cloud"
        onAdd={() => setAddFor("cloud")}
        onProbe={(id) => mut.probe.mutate(id)}
        onRemove={(id) => mut.remove.mutate(id)}
        onDefault={(id) => mut.setDefault.mutate(id)}
        busy={mut.probe.isPending}
      />

      {/* ④ 全局默认 */}
      <Panel title="各用途的默认模型">
        {!defaults ? (
          <div className="text-note text-ink-mute">读取中…</div>
        ) : (
          <div className="grid gap-3 sm:grid-cols-2">
            {(
              [
                ["script_parse", "剧本拆解", "把整段剧本文本变成角色/场景/节拍"],
                ["storyboard", "分镜生成", "把节拍变成镜头卡"],
                ["h3_prompt", "H3 提示词重写", "按项目选的模式出：三段式 / 官方六段式 / 中文导演分镜块 / hybrid"],
                ["embed", "向量检索", "角色与分镜的相似检索"],
              ] as const
            ).map(([key, label, note]) => {
              const cur = defaults[key] ?? { backendId: "", model: "" };
              const all = [...(local ?? []), ...(cloud ?? [])];
              const backend = all.find((b) => b.id === cur.backendId);
              return (
                <div key={key} className="space-y-2 rounded-ctl border border-rule-soft bg-slate p-2.5">
                  <div>
                    <div className="text-note font-medium">{label}</div>
                    <div className="text-caption text-ink-mute">{note}</div>
                  </div>
                  <Select
                    className="w-full"
                    value={cur.backendId}
                    onChange={(e) => mut.saveDefaults.mutate({ ...defaults, [key]: { backendId: e.target.value, model: "" } })}
                  >
                    <option value="">未指定</option>
                    {all.map((b) => (
                      <option key={b.id} value={b.id}>
                        {b.scope === "local" ? "本地" : "云端"} · {b.name}
                      </option>
                    ))}
                  </Select>
                  {backend && (
                    <Select
                      className="w-full"
                      value={cur.model}
                      onChange={(e) => mut.saveDefaults.mutate({ ...defaults, [key]: { backendId: cur.backendId, model: e.target.value } })}
                    >
                      <option value="">选择模型</option>
                      {backend.capabilities.models.map((m) => (
                        <option key={m} value={m}>
                          {m}
                        </option>
                      ))}
                    </Select>
                  )}
                  {backend && key !== "embed" && backend.capabilities.ctxSize != null && (
                    <CtxWarning backend={backend} need={16384} />
                  )}
                </div>
              );
            })}
          </div>
        )}
      </Panel>

      <AddBackendModal
        scope={addFor}
        onClose={() => setAddFor(null)}
        onCreate={async (body) => {
          await mut.create.mutateAsync(body);
          setAddFor(null);
          nav("/settings/llm");
        }}
      />
    </div>
  );
}

function CtxWarning({ backend, need }: { backend: LlmBackend; need: number }) {
  const have = backend.capabilities.ctxSize ?? 0;
  if (have >= need) return null;
  return (
    <div className="rounded-ctl border border-state-fail/40 bg-state-fail/8 px-2 py-1.5 text-caption leading-snug">
      <div className="text-state-fail">
        上下文只有 <span className="mono">{have}</span>，剧本拆解需要约 <span className="mono">{need}</span>。
      </div>
      <div className="mt-1 text-ink-dim">
        {backend.capabilities.ctxIsPerRequest
          ? "这个后端可以按请求提高 num_ctx，我们会自动带上；但要留意显存占用随之上升。"
          : "这个后端的上下文由启动参数决定，请求里改不了 —— 需要重启并加大 -c。"}
      </div>
    </div>
  );
}

function BackendGroup({
  title,
  desc,
  backends,
  scope,
  onAdd,
  onProbe,
  onRemove,
  onDefault,
  busy,
}: {
  title: string;
  desc: string;
  backends: LlmBackend[];
  scope: "local" | "cloud";
  onAdd: () => void;
  onProbe: (id: string) => void;
  onRemove: (id: string) => void;
  onDefault: (id: string) => void;
  busy: boolean;
}) {
  const [pullFor, setPullFor] = useState<LlmBackend | null>(null);
  const mut = useLlmMutations();
  return (
    <Panel
      title={
        <span className="flex items-center gap-2">
          {title}
          <Badge>{backends.length}</Badge>
        </span>
      }
      actions={
        <Button size="sm" variant="quiet" icon={<Plus className="h-3 w-3" />} onClick={onAdd}>
          添加
        </Button>
      }
      dense
    >
      <div className="border-b border-rule-soft px-3 py-1.5 text-caption text-ink-mute">{desc}</div>
      {backends.length === 0 ? (
        <div className="p-3">
          <Empty
            title={scope === "local" ? "还没有可用的本地模型" : "还没有云端模型"}
            hint={scope === "local" ? "上面的扫描如果发现服务，点一下就能加进来。" : "本地模型跑不动长剧本时再用它兜底。"}
            action={
              <Button size="sm" onClick={onAdd}>
                添加后端
              </Button>
            }
          />
        </div>
      ) : (
        <ul className="divide-y divide-rule-soft">
          {backends.map((b) => (
            <li key={b.id} className="space-y-2 px-3 py-2.5">
              <div className="flex flex-wrap items-center gap-2">
                <StateGlyph state={b.lastProbeOk ? "succeeded" : b.lastProbeOk === false ? "failed" : "idle"} />
                <span className="text-body font-medium">{b.name}</span>
                <Badge>{b.kind === "ollama" ? "Ollama 原生" : "OpenAI 兼容"}</Badge>
                {b.isDefault && (
                  <span className="inline-flex items-center gap-1 text-caption text-mach-local">
                    <Star className="h-3 w-3" /> 本组默认
                  </span>
                )}
                {b.capabilities.enterpriseSharedOnly && <Badge tone="warn">仅企业级-共享 key</Badge>}
                <div className="ml-auto flex items-center gap-1.5">
                  <Button size="sm" variant="quiet" onClick={() => onProbe(b.id)} disabled={busy}>
                    探活
                  </Button>
                  {!b.isDefault && (
                    <Button size="sm" variant="quiet" onClick={() => onDefault(b.id)}>
                      设为默认
                    </Button>
                  )}
                  <Button size="sm" variant="quiet" onClick={() => onRemove(b.id)} disabled={busy}>
                    删除
                  </Button>
                </div>
              </div>

              <div className="mono text-caption text-ink-mute">{b.baseUrl}{b.apiKeySet ? " · key 已配置" : ""}</div>

              {b.lastProbeOk === false && b.lastError && (
                <div className="rounded-ctl border border-rule-soft bg-inset px-2 py-1.5 text-caption leading-snug text-ink-dim">{b.lastError}</div>
              )}

              <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-caption text-ink-mute">
                <span>
                  上下文 <span className="mono text-ink-dim">{b.capabilities.ctxSize ?? "未知"}</span>
                  <span className="ml-1">{b.capabilities.ctxIsPerRequest ? "（可按请求调整）" : "（由启动参数决定）"}</span>
                </span>
                <CapMark on={b.capabilities.hasJsonSchema} label="JSON Schema" />
                <CapMark on={b.capabilities.hasVision} label="视觉" />
                <CapMark on={b.capabilities.hasTools} label="工具调用" />
                <span>{b.streamStyle === "ndjson" ? "流式 NDJSON" : "流式 SSE"}</span>
                <span className="ml-auto">上次探活 {fmtTime(b.lastProbeAt)}</span>
              </div>

              {b.capabilities.models.length > 0 && (
                <div className="flex flex-wrap gap-1">
                  {b.capabilities.models.map((m) => (
                    <span key={m} className="mono rounded-panel border border-rule-soft bg-slate px-1.5 py-[1px] text-caption text-ink-dim">
                      {m}
                    </span>
                  ))}
                </div>
              )}

              {b.capabilities.supportsPull ? (
                <Button size="sm" variant="quiet" icon={<Download className="h-3 w-3" />} onClick={() => setPullFor(b)}>
                  拉取模型
                </Button>
              ) : (
                <p className="text-caption leading-snug text-ink-mute">
                  这个后端没有拉取接口。llama.cpp 请把 GGUF 放进 <span className="mono">--models-dir</span> 指向的目录，列表会自动出现。
                </p>
              )}
            </li>
          ))}
        </ul>
      )}

      <PullModal backend={pullFor} onClose={() => setPullFor(null)} onPull={(model) => mut.pull.mutate({ id: pullFor!.id, model })} pulling={mut.pull.isPending} />
    </Panel>
  );
}

function CapMark({ on, label }: { on: boolean; label: string }) {
  return <span className={cn(on ? "text-ink-dim" : "line-through opacity-55")}>{label}</span>;
}

function PullModal({ backend, onClose, onPull, pulling }: { backend: LlmBackend | null; onClose: () => void; onPull: (m: string) => void; pulling: boolean }) {
  const [model, setModel] = useState("qwen3:8b");
  return (
    <Modal
      open={!!backend}
      onClose={onClose}
      title={`从 ${backend?.name ?? ""} 拉取模型`}
      footer={
        <>
          <Button variant="quiet" onClick={onClose}>
            关闭
          </Button>
          <Button variant="primary" loading={pulling} onClick={() => onPull(model.trim())}>
            拉取
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <Field label="模型标签" hint="本机显存 24 GB：qwen3:14b 已经够中文创作，更大的榜单模型不必追。">
          <Input value={model} onChange={(e) => setModel(e.target.value)} placeholder="qwen3:8b" className="mono" />
        </Field>
        <div className="flex flex-wrap gap-1.5">
          {["qwen3:8b", "qwen3:14b", "deepseek-r1:8b", "bge-m3"].map((m) => (
            <button key={m} onClick={() => setModel(m)} className="mono rounded-panel border border-rule px-2 py-0.5 text-caption text-ink-dim hover:bg-raised">
              {m}
            </button>
          ))}
        </div>
      </div>
    </Modal>
  );
}

function AddBackendModal({
  scope,
  onClose,
  onCreate,
}: {
  scope: LlmBackend["scope"] | null;
  onClose: () => void;
  onCreate: (body: { name: string; scope: LlmBackend["scope"]; kind: LlmBackend["kind"]; baseUrl: string; isDefault?: boolean }) => void;
}) {
  const [preset, setPreset] = useState("ollama");
  const [name, setName] = useState("");
  const [baseUrl, setBaseUrl] = useState(PRESETS.ollama.baseUrl);
  const [isDefault, setIsDefault] = useState(false);

  const effScope = scope ?? "local";
  return (
    <Modal
      open={!!scope}
      onClose={onClose}
      title={scope === "cloud" ? "添加云端文本后端" : "添加本地文本后端"}
      footer={
        <>
          <Button variant="quiet" onClick={onClose}>
            取消
          </Button>
          <Button
            variant="primary"
            disabled={!name.trim() || !baseUrl.trim()}
            onClick={() =>
              onCreate({
                name: name.trim(),
                scope: effScope,
                kind: PRESETS[preset].kind,
                baseUrl: baseUrl.trim(),
                isDefault,
              })
            }
          >
            保存并探活
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <Field label="类型" hint="选 Ollama 会走原生 /api/chat（流式是 NDJSON）；其余都按 OpenAI 兼容协议说话。">
          <Select
            value={preset}
            onChange={(e) => {
              setPreset(e.target.value);
              setBaseUrl(PRESETS[e.target.value].baseUrl);
            }}
          >
            {Object.entries(PRESETS).map(([k, v]) => (
              <option key={k} value={k}>
                {v.label}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="显示名">
          <Input value={name} onChange={(e) => setName(e.target.value)} placeholder={PRESETS[preset].label} />
        </Field>
        <Field
          label="Base URL"
          hint={
            PRESETS[preset].kind === "ollama"
              ? "Ollama 默认只绑环回；跨机访问要设 OLLAMA_HOST，并且别用 * 放开 OLLAMA_ORIGINS。"
              : "llama.cpp 记得带 /v1，且要确认它启动时的 -c 够用。"
          }
        >
          <Input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} className="mono" />
        </Field>
        <Toggle checked={isDefault} onChange={setIsDefault} label="设为本组默认" />
      </div>
    </Modal>
  );
}
