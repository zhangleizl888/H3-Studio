/**
 * 生成选择：一条资产 / 一个镜头「用哪条工作流、在哪台实例上、换哪颗权重」。
 *
 * 三段是联动的：模型清单要拿着「哪条工作流 + 哪台实例」去问后端，而那份清单又是实例
 * 自己的 /object_info 报出来的，所以前端一个文件名都不写死。选"自动挑"时问不出清单
 * （还不知道会挑中哪条），这里就如实说"由后端挑中之后再定"，不摆一个假下拉。
 */

import { useEffect, useMemo, useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { Badge, Select } from "./ui";
import { useInstances, useWorkflowModels, useWorkflows } from "../lib/hooks";
import { effectivePreset, presetValueOf, workflowRef, workflowsForKind, type GenKind } from "../lib/preset";
import type { GenPreset, ModelSlot, Project } from "../lib/types";
import { cn } from "../lib/utils";

const AUTO = "auto";
const FOLLOW = "";

/** 云端那一路现在只到「档位」为止：能选、能记账，但 rh_task 客户端还没实现 */
const CLOUD_HINT = "云端实例还没有可接的（RunningHub 专有任务客户端未实现）。要试云端请在 设置 → 生成实例 用原生代理接法添加。";

interface Props {
  project: Project;
  kind: GenKind;
  /** 实体上已有的选择；留空表示完全跟随项目默认 */
  value?: GenPreset;
  onChange: (next: GenPreset | undefined) => void;
  /** 紧凑模式给卡片用：一行三段，模型位收进「其他」 */
  compact?: boolean;
  className?: string;
}

export function GenPresetPicker({ project, kind, value, onChange, compact = false, className }: Props) {
  const [openModels, setOpenModels] = useState(false);
  const { data: workflowList } = useWorkflows();
  const { data: instances } = useInstances();
  const eff = effectivePreset(project, kind, value);
  const ref = workflowRef(eff.workflow);

  // 项目默认可能指着一台已经被删掉的实例（实例行是会没的，项目里的指针不会自己跟着改）。
  // 这时候不能拿它去问模型清单 —— 只会问出一句「未登记的实例」，还把整排下拉堵死。
  const registered = new Set((instances ?? []).map((i) => String(i.id)));
  const staleInstance = !!eff.instanceId && !registered.has(String(eff.instanceId));
  const askInstanceId = staleInstance ? null : eff.instanceId;
  const { data: modelInfo, isLoading: modelsLoading, error: modelsError } = useWorkflowModels(ref, askInstanceId);

  const all = workflowList ?? [];
  const list = useMemo(() => workflowsForKind(all, kind), [all, kind]);
  const builtins = list.filter((w) => w.isBuiltin);
  const library = list.filter((w) => !w.isBuiltin);
  const chosen = ref ? all.find((w) => w.id === ref) : null;
  const gaps = chosen?.gaps?.length ?? 0;

  const slots = modelInfo?.slots ?? [];
  const primary = slots.find((s) => s.key === modelInfo?.primary) ?? slots[0];
  const rest = slots.filter((s) => s !== primary);

  const pick = (patch: Partial<GenPreset>) => onChange({ ...(value ?? {}), ...patch });

  // 存档里可能留着上一条工作流的权重键（那些坐标在这张图上根本不存在）。
  // 留着送去后端只会换回一句「图上没有这个模型位」，而且用户在界面上看不见是哪个。
  const staleKeys = useMemo(
    () => (ref && modelInfo ? Object.keys(eff.models).filter((k) => !slots.some((s) => s.key === k)) : []),
    [ref, modelInfo, eff.models, slots],
  );
  useEffect(() => {
    if (staleKeys.length) pick({ models: Object.fromEntries(Object.entries(eff.models).filter(([k]) => !staleKeys.includes(k))) });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [staleKeys.join(","), ref]);

  return (
    <div className={cn("space-y-2", className)}>
      <div className={cn("flex flex-wrap items-end gap-2", compact && "gap-1.5")}>
        <Labeled label="工作流" className="min-w-[190px] flex-1">
          <Select
            aria-label="工作流"
            className="w-full"
            value={eff.workflow || AUTO}
            onChange={(e) => {
              const v = e.target.value;
              // 换工作流一定清空权重：那些键是「节点号.字段名」，属于上一条图的坐标，
              // 留着就会把上一条的权重名往下一条的节点上写
              pick({ workflow: v === FOLLOW ? null : v === AUTO ? "auto" : v, models: {} });
            }}
          >
            <option value={FOLLOW}>跟随项目默认（{labelOf(projectDefault(project, kind))}）</option>
            <option value={AUTO}>自动挑（按这次任务的形状）</option>
            {builtins.length > 0 && (
              <optgroup label="内置模板">
                {builtins.map((w) => (
                  <option key={w.id} value={presetValueOf(w)}>
                    {w.name}
                  </option>
                ))}
              </optgroup>
            )}
            {library.length > 0 && (
              <optgroup label="工作流库">
                {library.map((w) => (
                  <option key={w.id} value={presetValueOf(w)} disabled={!!w.gaps?.length}>
                    {w.name}
                    {w.gaps?.length ? `（本机还缺 ${w.gaps.length} 处）` : ""}
                  </option>
                ))}
              </optgroup>
            )}
            {library.length === 0 && builtins.length === 0 && <option disabled>这一类任务还没有可用的工作流</option>}
          </Select>
        </Labeled>

        <Labeled label="实例" className="min-w-[150px]">
          <Select aria-label="生成实例" className="w-full" value={eff.instanceId || FOLLOW} onChange={(e) => pick({ instanceId: e.target.value || null, models: {} })}>
            <option value={FOLLOW}>跟随项目默认</option>
            {staleInstance && <option value={eff.instanceId ?? ""}>{`项目默认·${eff.instanceId}（已不在登记列表）`}</option>}
            {(instances ?? [])
              .filter((i) => i.placement === "local" || i.placement === "cloud_self")
              .map((i) => (
                <option key={i.id} value={i.id}>
                  {i.name}
                  {i.lastProbeOk === false ? "（探活失败）" : ""}
                </option>
              ))}
            {(instances ?? []).filter((i) => i.placement === "cloud_runninghub").length === 0 && <option disabled>云端·暂无可接实例</option>}
            {(instances ?? [])
              .filter((i) => i.placement === "cloud_runninghub")
              .map((i) => (
                <option key={i.id} value={i.id}>
                  {i.name}（RunningHub）
                  {i.lastProbeOk === false ? "·探活失败" : ""}
                </option>
              ))}
          </Select>
        </Labeled>
      </div>

      {/* 模型位：清单来自实例，一条都不写死 */}
      {!ref ? (
        <p className="text-caption leading-snug text-ink-mute">
          选「自动挑」时这里问不出模型清单 —— 要等后端挑中某条工作流才知道有哪些权重可换。
        </p>
      ) : modelsLoading ? (
        <p className="text-caption text-ink-mute">正在问这台实例有哪些权重…</p>
      ) : modelsError ? (
        <p className="text-caption leading-snug text-state-fail">{String((modelsError as Error)?.message ?? modelsError)}</p>
      ) : slots.length === 0 ? (
        <p className="text-caption text-ink-mute">这条图上没有可替换的权重位。</p>
      ) : (
        <div className="space-y-1.5">
          <ModelSlotRow
            slot={primary}
            value={eff.models[primary.key] ?? ""}
            onChange={(v) => pick({ models: withModel(value?.models ?? {}, primary.key, v) })}
          />
          {rest.length > 0 && (
            <>
              <button
                type="button"
                onClick={() => setOpenModels((x) => !x)}
                aria-expanded={openModels}
                className="flex items-center gap-1 text-caption text-ink-mute hover:text-ink"
              >
                {openModels ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
                其他模型位（{rest.length}）
              </button>
              {openModels &&
                rest.map((s) => (
                  <ModelSlotRow
                    key={s.key}
                    slot={s}
                    value={eff.models[s.key] ?? ""}
                    onChange={(v) => pick({ models: withModel(value?.models ?? {}, s.key, v) })}
                  />
                ))}
            </>
          )}
        </div>
      )}

      {(staleInstance || gaps > 0 || chosen?.executesOn === "cloud_runninghub" || modelInfo?.notes.length) && (
        <ul className="space-y-0.5">
          {staleInstance && (
            <li className="text-caption leading-snug text-state-warn">
              项目默认指向的实例 {eff.instanceId} 已经不在了（现在登记着
              {(instances ?? []).map((i) => `「${i.name}」`).join("、") || "零台"}）。
              在上面的「实例」里选一台，这一项就会记住；不然派发上去会被「实例不在已登记的实例里」拦下。
            </li>
          )}
          {staleKeys.length > 0 && (
            <li className="text-caption leading-snug text-state-warn">
              这条图上没有 {staleKeys.join("、")} 这个模型位（是别的工作流的节点），已经把它从选择里清掉了。
            </li>
          )}
          {gaps > 0 && (
            <li className="text-caption leading-snug text-state-warn">
              这条在本机还缺 {gaps} 处（{chosen?.gaps?.[0]?.class_type}）：装好节点包后去工作流库点「重新扫描」。
            </li>
          )}
          {chosen?.executesOn === "cloud_runninghub" && <li className="text-caption leading-snug text-ink-mute">{CLOUD_HINT}</li>}
          {(modelInfo?.notes ?? []).map((n) => (
            <li key={n} className="text-caption leading-snug text-ink-mute">
              {n}
            </li>
          ))}
        </ul>
      )}

      {Object.keys(eff.models).length > 0 && (
        <Badge tone="warn" className="mr-1">
          已换 {Object.keys(eff.models).length} 个模型
        </Badge>
      )}
    </div>
  );
}

function ModelSlotRow({ slot, value, onChange }: { slot: ModelSlot; value: string; onChange: (v: string) => void }) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <span className="label-mono w-[92px] flex-none text-ink-mute" title={`${slot.classType} #${slot.node}.${slot.field}`}>
        {slot.role}
      </span>
      <Select
        aria-label={`${slot.label}（模型）`}
        className="min-w-[220px] flex-1"
        value={value}
        onChange={(e) => onChange(e.target.value)}
      >
        <option value="">{`跟随默认（${slot.current ? shortName(slot.current) : "实例自己挑"}）`}</option>
        {slot.options.map((o) => (
          <option key={o} value={o}>
            {shortName(o)}
          </option>
        ))}
      </Select>
      {slot.missing && (
        <span className="text-caption text-state-fail" title={`图里写的 ${slot.current} 在这台实例上不存在`}>
          图上那个没有
        </span>
      )}
    </div>
  );
}

const Labeled = ({ label, className, children }: { label: string; className?: string; children: React.ReactNode }) => (
  <label className={cn("flex min-w-0 flex-col gap-1", className)}>
    <span className="label-mono text-ink-mute">{label}</span>
    {children}
  </label>
);

/** 改回"跟随默认"就是把这一位从覆盖表里删掉，而不是留一个空串 */
function withModel(models: Record<string, string>, key: string, value: string): Record<string, string> {
  const next = { ...models };
  if (value) next[key] = value;
  else delete next[key];
  return next;
}

function projectDefault(project: Project, kind: GenKind): string {
  const c = project.config;
  return kind === "image" ? c.imageTemplate : kind === "video" ? c.videoTemplate : AUTO;
}

function labelOf(raw: string): string {
  if (!raw || raw === AUTO) return "自动挑";
  return /^\d+$/.test(raw) ? `库工作流 ${raw}` : raw;
}

/** 权重名很长（qwen_image_2.1_int8_convrot.safetensors），子目录保留、后缀去掉才看得清 */
function shortName(v: string): string {
  const tail = v.split(/[\\/]/).pop() ?? v;
  return tail.replace(/\.(safetensors|ckpt|pt|bin|gguf)$/i, "");
}
