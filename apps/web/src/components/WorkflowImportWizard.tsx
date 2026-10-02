import { useEffect, useState, type DragEvent, type ReactNode } from "react";
import { Badge, Button, Copyable, Field, Input, KeyVal, MachChip, Modal, Panel, Select, Textarea, Toggle } from "./ui";
import { useWorkflowMutations } from "../lib/hooks";
import type { GenInstance, ImportReport } from "../lib/types";

/*
 * 导入向导：一份 ComfyUI 导出 → 库里的一条工作流。
 *
 * 为什么坚持「先校验再入库」：导入不是把 JSON 存下来就完事 —— 后端要按目标实例的
 * /object_info 做等价改写（作者私有节点换成核心等价物）、对齐权重名、剪掉真跑不了的分支、
 * 再抽出槽位与任务信号。这一串会改图，所以必须让用户先看见改了什么再落库。
 *
 * 同一个工作流常常有两份导出（API 版与画布版）。两份都给：API 版当可执行图、
 * 画布版原样存着给编辑器与回导 ComfyUI —— 省掉一次有损的 UI↔API 往返。
 */

const STEPS = ["选文件与目标实例", "校验报告", "确认导入"] as const;

type Picked = { name: string; text: string };

export function ImportWizard({
  open,
  onClose,
  instances,
  onImported,
}: {
  open: boolean;
  onClose: () => void;
  instances: GenInstance[];
  onImported: (id: string) => void;
}) {
  const mut = useWorkflowMutations();
  const [step, setStep] = useState(0);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [instanceId, setInstanceId] = useState("");
  const [priority, setPriority] = useState("100");
  const [tags, setTags] = useState("导入");
  const [main, setMain] = useState<Picked | null>(null);
  const [ui, setUi] = useState<Picked | null>(null);
  const [ack, setAck] = useState(false);
  const [dropHint, setDropHint] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setStep(0);
    setAck(false);
    setDropHint(null);
    setMain(null);
    setUi(null);
    setName("");
    setDescription("");
    mut.importJson.reset();
    mut.validate.reset();
    // 只在弹窗打开时重置一次
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const report = mut.validate.data ?? mut.importJson.data?.report ?? null;
  const chosen = instances.find((i) => i.id === instanceId) ?? null;
  const importError = mut.validate.error?.message ?? mut.importJson.error?.message ?? null;
  const canValidate = !!main?.text.trim() && !!name.trim();

  function readFiles(files: FileList | File[]) {
    const list = Array.from(files);
    if (!list.length) return;
    // 成对文件按名字认：带 _api / _API 的那份是 API 导出（后端要的图），另一份是画布版
    const api = list.find((f) => /_api\b/i.test(f.name)) ?? list[0];
    const companion = list.find((f) => f !== api);
    void Promise.all(list.map((f) => f.text())).then((texts) => {
      const apiText = texts[list.indexOf(api)] ?? "";
      setMain({ name: api.name, text: apiText });
      if (companion) setUi({ name: companion.name, text: texts[list.indexOf(companion)] ?? "" });
      else setUi(null);
      if (!name) setName(api.name.replace(/_api\.json$/i, "").replace(/\.json$/i, ""));
      setDropHint(companion ? `已读入 ${api.name} + ${companion.name}` : `已读入 ${api.name}`);
    });
  }

  function onDrop(e: DragEvent<HTMLDivElement>) {
    e.preventDefault();
    if (e.dataTransfer.files?.length) readFiles(e.dataTransfer.files);
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="导入工作流"
      width={780}
      footer={
        <>
          <span className="mr-auto text-caption text-ink-mute">
            第 {step + 1} 步 / 共 {STEPS.length} 步：{STEPS[step]}
          </span>
          {step > 0 && (
            <Button variant="quiet" onClick={() => setStep((s) => s - 1)}>
              返回上一步
            </Button>
          )}
          {step === 0 && (
            <Button
              variant="primary"
              loading={mut.validate.isPending}
              disabled={!canValidate}
              onClick={() =>
                mut.validate.mutate(
                  { json: main!.text, instanceId: instanceId || undefined },
                  { onSuccess: () => setStep(1), onError: () => setStep(1) },
                )
              }
            >
              校验并继续
            </Button>
          )}
          {step === 1 && (
            <Button variant="primary" onClick={() => setStep(2)}>
              看确认页
            </Button>
          )}
          {step === 2 && (
            <Button
              variant="primary"
              loading={mut.importJson.isPending}
              disabled={!report || (report.errors.length > 0 && !ack)}
              onClick={() =>
                mut.importJson.mutate(
                  {
                    name: name.trim(),
                    json: main!.text,
                    uiJson: ui?.text,
                    description: description.trim() || undefined,
                    instanceId: instanceId || undefined,
                    priority: Number(priority) || 100,
                    tags: tags.split(/[,，]/).map((t) => t.trim()).filter(Boolean),
                  },
                  { onSuccess: (r) => onImported(r.workflow.id) },
                )
              }
            >
              导入并入库
            </Button>
          )}
        </>
      }
    >
      <div className="space-y-4">
        {step === 0 && (
          <>
            <div
              onDragOver={(e) => e.preventDefault()}
              onDrop={onDrop}
              className="space-y-2 rounded-tile border border-dashed border-rule px-3 py-3"
            >
              <div className="flex flex-wrap items-center gap-2">
                <label className="inline-flex cursor-pointer items-center gap-1.5 rounded-ctl border border-rule bg-raised px-2.5 py-1 text-note text-ink-dim hover:text-ink">
                  选择 JSON 文件
                  <input
                    type="file"
                    accept=".json,application/json"
                    multiple
                    className="hidden"
                    onChange={(e) => e.target.files && readFiles(e.target.files)}
                  />
                </label>
                <span className="text-caption text-ink-mute">
                  可以一次选两个（API 版 + 画布版），带 <span className="mono">_api</span> 的那份当可执行图
                </span>
              </div>
              <div className="text-note leading-snug text-ink-mute">
                也可以把文件直接拖进来。UI 格式与 API 格式都收，后端判完格式再入库。
                {dropHint && <span className="ml-2 text-ink-dim">{dropHint}</span>}
              </div>
              {main && (
                <div className="flex flex-wrap items-center gap-2 text-caption">
                  <Badge tone="good">可执行图 · {main.name}（{(main.text.length / 1024).toFixed(0)} KB）</Badge>
                  {ui && <Badge>画布版 · {ui.name}（{(ui.text.length / 1024).toFixed(0)} KB）</Badge>}
                </div>
              )}
            </div>

            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="工作流名称" hint="库里显示这个，别写得太长。">
                <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="H3 首尾帧 · 雨夜车内" />
              </Field>
              <Field label="目标实例" hint="校验会拿这台实例的 object_info 与模型列表比对。">
                <Select value={instanceId} onChange={(e) => setInstanceId(e.target.value)} className="w-full">
                  <option value="">不做实例比对</option>
                  {instances.map((i) => (
                    <option key={i.id} value={i.id}>
                      {i.name} · {i.protocol}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label="一句话说明">
                <Input value={description} onChange={(e) => setDescription(e.target.value)} placeholder="4步 Turbo 工作流，多参考图/视频/音频" />
              </Field>
              <div className="grid grid-cols-2 gap-3">
                <Field label="优先级" hint="同类任务多条合格时，数字大的先被自动选。">
                  <Input value={priority} onChange={(e) => setPriority(e.target.value)} inputMode="numeric" type="number" className="mono" />
                </Field>
                <Field label="标签">
                  <Input value={tags} onChange={(e) => setTags(e.target.value)} placeholder="导入, H3" className="mono" />
                </Field>
              </div>
            </div>

            {chosen && (
              <div className="flex flex-wrap items-center gap-2 text-caption text-ink-mute">
                <MachChip placement={chosen.placement} label={chosen.name} />
                <span className="mono">{chosen.baseUrl}</span>
                {chosen.lastProbeOk === false && <span className="text-state-fail">这台上次探活失败，比对结果可能不完整</span>}
              </div>
            )}
          </>
        )}

        {step >= 1 && (
          <>
            {importError && <p className="text-note text-state-fail">{importError}</p>}
            {!report ? (
              <p className="text-note text-ink-mute">{mut.validate.isPending ? "正在按实例的 /object_info 改写与比对…" : "没有拿到校验结果。"}</p>
            ) : (
              <ReportView report={report} />
            )}
          </>
        )}

        {step === 2 && report && (
          <div className="space-y-2 rounded-tile border border-rule bg-slate p-3">
            <KeyVal
              items={[
                ["名称", name],
                ["目标实例", chosen ? chosen.name : "不比对"],
                ["源格式", report.sourceFormat === "api" ? "API 格式" : "UI 格式（已归一化成 API）"],
                ["画布版", ui ? ui.name : "没给（只存可执行图）"],
                ["槽位数", report.slotCount],
              ]}
            />
            {report.errors.length > 0 && (
              <Toggle
                checked={ack}
                onChange={setAck}
                label="我已确认这些节点在目标实例上不存在"
                hint="不勾不让导入。导入只是把图存下来，不会替你装东西。"
              />
            )}
            <p className="text-caption leading-snug text-ink-mute">
              导入后这份图会出现在库里。要跑通还得补齐上面列出的缺失项；权重可以在卡片上的「模型」里按实例挑。
            </p>
          </div>
        )}
      </div>
    </Modal>
  );
}

export function ReportView({ report }: { report: ImportReport }) {
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={report.valid ? "good" : "bad"}>{report.valid ? "校验通过" : "有节点不认识"}</Badge>
        <span className="text-note text-ink-dim">源格式 {report.sourceFormat}</span>
        <span className="mono text-note text-ink-mute">{report.slotCount} 个槽位</span>
        {report.estimated && (
          <span className="mono text-caption text-ink-mute">
            估算 {report.estimated.seconds} 秒 · {report.estimated.frames} 帧 · {report.estimated.width}×{report.estimated.height} ·{" "}
            {report.estimated.steps} 步
          </span>
        )}
      </div>

      <Section title="未知节点" empty={report.unknownNodes.length === 0} hint="目标实例的 object_info 里没有这些 class_type。">
        <Chips items={report.unknownNodes} />
      </Section>

      <Section
        title="只能跑在 RunningHub 上的节点"
        empty={report.runninghubOnlyNodes.length === 0}
        hint="这些是平台专有节点，本机与自建云装不到，别当成可移植的图。"
      >
        <Chips items={report.runninghubOnlyNodes} tone="warn" />
      </Section>

      <Section title="缺失模型文件" empty={report.missingModels.length === 0} hint="按 models/{folder}/{filename} 摆放，或从别处拷过来。">
        <ul className="space-y-1">
          {report.missingModels.map((m, i) => (
            <li key={i}>
              <Copyable text={`models/${m.folder}/${m.filename}`} className="text-note">
                <span className="mono">
                  models/{m.folder}/<span className="text-ink">{m.filename}</span>
                </span>
              </Copyable>
            </li>
          ))}
        </ul>
      </Section>

      <Section title="缺失自定义节点" empty={report.missingCustomNodes.length === 0} hint="缺这些节点，POST /prompt 会在 node_errors 里报错。">
        <Chips items={report.missingCustomNodes} />
      </Section>

      <Section title="错误" empty={report.errors.length === 0}>
        <ul className="space-y-1.5">
          {report.errors.map((e, i) => (
            <li key={i} className="rounded-ctl border border-rule-soft bg-slate px-2 py-1.5 text-note leading-snug">
              <div className="text-ink">{e.message}</div>
              <div className="mono text-caption text-ink-mute">
                {e.node ? `节点 ${e.node}` : ""}
                {e.classType ? ` · ${e.classType}` : ""}
              </div>
            </li>
          ))}
        </ul>
      </Section>

      <Section title="提醒" empty={report.warnings.length === 0}>
        <ul className="space-y-1 text-note leading-snug text-ink-dim">
          {report.warnings.map((w, i) => (
            <li key={i}>{w}</li>
          ))}
        </ul>
      </Section>

      {report.installPlan && report.installPlan.length > 0 && (
        <div className="space-y-1.5 rounded-tile border border-rule bg-slate p-2.5">
          <div className="label">可执行的安装命令（我们不会替你跑）</div>
          <ul className="space-y-1">
            {report.installPlan.map((cmd, i) => (
              <li key={i}>
                <Copyable text={cmd} className="text-note">
                  <span className="mono">{cmd}</span>
                </Copyable>
              </li>
            ))}
          </ul>
          <p className="text-caption leading-snug text-ink-mute">
            装完记得重新探活实例，再回这一页重跑一次校验。任何 pip 或 Comfy-Manager 操作都由你自己执行。
          </p>
        </div>
      )}

      {report.valid && report.warnings.length === 0 && <p className="text-note text-ink-dim">这份图在目标实例上没有障碍。</p>}
    </div>
  );
}

function Section({ title, empty, hint, children }: { title: string; empty: boolean; hint?: ReactNode; children?: ReactNode }) {
  return (
    <div className="space-y-1 border-t border-rule-soft pt-2">
      <div className="flex items-baseline gap-2">
        <span className="label">{title}</span>
        {empty && <span className="text-caption text-ink-mute">没有</span>}
      </div>
      {!empty && children}
      {!empty && hint && <p className="text-caption leading-snug text-ink-mute">{hint}</p>}
    </div>
  );
}

function Chips({ items, tone = "neutral" }: { items: string[]; tone?: "neutral" | "warn" }) {
  return (
    <div className="flex flex-wrap gap-1">
      {items.map((t) => (
        <Badge key={t} tone={tone}>
          {t}
        </Badge>
      ))}
    </div>
  );
}

/** 替换 JSON：走与导入完全同一条改写流水线，只是落到库里已有那一条上 */
export function ReplaceJsonModal({
  open,
  onClose,
  workflowId,
  workflowName,
  instances,
  onReplaced,
}: {
  open: boolean;
  onClose: () => void;
  workflowId: string;
  workflowName: string;
  instances: GenInstance[];
  onReplaced: () => void;
}) {
  const mut = useWorkflowMutations();
  const [text, setText] = useState("");
  const [fileName, setFileName] = useState("");
  const [instanceId, setInstanceId] = useState("");
  const result = mut.replaceGraph.data ?? null;
  const error = mut.replaceGraph.error?.message ?? null;

  useEffect(() => {
    if (!open) return;
    setText("");
    setFileName("");
    mut.replaceGraph.reset();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={`替换 JSON · ${workflowName}`}
      width={680}
      footer={
        <>
          <span className="mr-auto text-caption text-ink-mute">名字、标签、优先级与按实例的权重绑定都保留</span>
          <Button variant="quiet" onClick={onClose}>
            {result ? "关闭" : "取消"}
          </Button>
          <Button
            variant="primary"
            loading={mut.replaceGraph.isPending}
            disabled={!text.trim()}
            onClick={() =>
              mut.replaceGraph.mutate(
                { id: workflowId, graph: text, instanceId: instanceId || undefined },
                { onSuccess: onReplaced },
              )
            }
          >
            替换并重扫
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <label className="inline-flex cursor-pointer items-center gap-1.5 rounded-ctl border border-rule bg-raised px-2.5 py-1 text-note text-ink-dim hover:text-ink">
            选择新导出
            <input
              type="file"
              accept=".json,application/json"
              className="hidden"
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (!f) return;
                void f.text().then((t) => {
                  setText(t);
                  setFileName(f.name);
                });
              }}
            />
          </label>
          {fileName && <Badge tone="good">{fileName}</Badge>}
          <Select value={instanceId} onChange={(e) => setInstanceId(e.target.value)} className="ml-auto h-7 w-auto">
            <option value="">按默认实例改写</option>
            {instances.map((i) => (
              <option key={i.id} value={i.id}>
                {i.name}
              </option>
            ))}
          </Select>
        </div>
        <Textarea value={text} onChange={(e) => setText(e.target.value)} rows={8} className="mono w-full" placeholder={'{"127":{"class_type":"UNETLoader","inputs":{…}}, …}'} />
        <p className="text-caption leading-snug text-ink-mute">
          后端会按所选实例重做一遍：等价改写 → 对齐权重名 → 剪掉跑不了的分支 → 重抽槽位与任务信号。
          节点号变了的话，指向失效节点的权重绑定会自动摘掉并在下面说出来。
        </p>
        {error && <p className="text-note text-state-fail">替换失败：{error}</p>}
        {result && (
          <Panel dense title="替换结果">
            <ul className="space-y-1 p-3 text-note leading-snug text-ink-dim">
              <li>
                节点 <span className="mono">{Object.keys(result.workflow.graph ?? {}).length}</span> 个 · 改写{" "}
                <span className="mono">{result.report.adaptations.length}</span> 处 · 仍缺{" "}
                <span className="mono">{result.report.gaps.length}</span> 处
              </li>
              {result.report.notes.map((n, i) => (
                <li key={i} className="text-ink-mute">
                  · {n}
                </li>
              ))}
            </ul>
          </Panel>
        )}
      </div>
    </Modal>
  );
}
