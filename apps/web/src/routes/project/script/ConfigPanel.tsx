import { useEffect, useState } from "react";
import { AlertCircle, BookOpen, BrainCircuit, Info, Wand2 } from "lucide-react";
import { Badge, Button, Field, Input, Select, Toggle } from "../../../components/ui";
import { DURATION_OPTIONS, H3_PROMPT_MODES, LANGUAGE_OPTIONS, VISUAL_STYLES, stylePrompt } from "../../../lib/prompts";
import type { AspectRatio, H3PromptMode, LlmBackend } from "../../../lib/types";
import { cn } from "../../../lib/utils";
import { LLM_ETA_HINT } from "./merge";

/** 后端 target_sec 的硬约束（routes_llm.RunBody：ge=5 / le=1800），超了会被 400 拒 */
const SEC_MIN = 5;
const SEC_MAX = 1800;

const PRESET_KEYS = new Set(VISUAL_STYLES.filter((s) => s.prompt).map((s) => s.key));

interface Props {
  projectName: string;
  outputLanguage: string;
  targetDurationSec: number;
  visualStyle: string;
  promptMode: H3PromptMode;
  promptReason: string;
  continuity: boolean;
  continuityOverlapFrames: number;
  aspectRatio: AspectRatio;
  llms: LlmBackend[] | undefined;
  shotBackendId: string | null | undefined;
  shotModel: string | null | undefined;
  busy: string | null;
  error: string | null;
  hasShots: boolean;
  scriptChars: number;
  onProjectName: (v: string) => void;
  onProjectNameCommit: () => void;
  onOutputLanguage: (v: string) => void;
  onTargetDuration: (sec: number) => void;
  onVisualStyle: (v: string) => void;
  onPromptMode: (m: H3PromptMode) => void;
  onPromptReason: (v: string) => void;
  /** 正在按新模式重拼已有镜头：这期间再点一次会把上一半的写盘搅乱 */
  promptModeBusy: boolean;
  /** 这一档已经覆盖了几个镜头，让「切档」这件事在界面上看得见结果 */
  promptModeNote: string | null;
  onContinuity: (v: boolean) => void;
  onContinuityOverlap: (n: number) => void;
  onAspectRatio: (v: AspectRatio) => void;
  onShotModel: (backendId: string, model: string | null) => void;
  onGenerate: () => void;
}

export function ConfigPanel(p: Props) {
  const customDuration = !DURATION_OPTIONS.some((o) => o.value === p.targetDurationSec && o.value !== 0);
  const customStyle = !PRESET_KEYS.has(p.visualStyle);
  const picked = p.llms?.find((b) => b.id === p.shotBackendId);
  const modelOptions = (p.llms ?? []).flatMap(modelChoices);
  /** 生成进行中锁住配置：中途改风格/时长，跑完的产物就和界面说的不是一回事了 */
  const lock = !!p.busy;
  const currentModelValue = `${p.shotBackendId ?? ""}::${p.shotModel ?? ""}`;
  // 选中的那一对不在清单里（后端被删了/从没选过）：补一条如实显示，别让下拉框悄悄指向另一个后端
  const fallbackOption =
    !modelOptions.some((o) => o.value === currentModelValue) && (p.shotBackendId || modelOptions.length)
      ? {
          value: currentModelValue,
          label: p.shotBackendId ? `当前：后端 ${p.shotBackendId} · ${p.shotModel ?? "默认模型"}（清单里已找不到）` : "（未指定：走默认文本后端）",
        }
      : null;

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="flex h-11 flex-none items-center gap-2 border-b border-rule-soft px-4">
        <BookOpen className="h-4 w-4 text-chrome" />
        <h2 className="text-body font-semibold tracking-wide text-ink">项目配置</h2>
      </header>

      <div className="min-h-0 flex-1 space-y-5 overflow-y-auto p-4">
        <Field label="项目标题" hint="失焦即写回项目名，顶栏与拍摄清单都读它">
          <Input
            className="w-full"
            value={p.projectName}
            placeholder="输入项目名称"
            disabled={!!p.busy}
            onChange={(e) => p.onProjectName(e.target.value)}
            onBlur={p.onProjectNameCommit}
            onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()}
          />
        </Field>

        <Field label="输出语言" hint="拆解与分镜的文字都按这个语言出">
          <Select className="w-full" value={p.outputLanguage} disabled={lock} onChange={(e) => p.onOutputLanguage(e.target.value)}>
            {LANGUAGE_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </Select>
        </Field>

        <Field label="目标时长" hint={`分镜规划按它分配每镜秒数；后端接受 ${SEC_MIN}–${SEC_MAX} 秒`}>
          <div className="grid grid-cols-2 gap-1.5">
            {DURATION_OPTIONS.map((o) => {
              const active = o.value === 0 ? customDuration : p.targetDurationSec === o.value;
              return (
                <button
                  key={o.label}
                  type="button"
                  aria-pressed={active}
                  disabled={lock}
                  onClick={() => p.onTargetDuration(o.value === 0 ? 90 : o.value)}
                  className={cn(
                    "h-7 rounded-ctl border px-2 text-note transition-colors disabled:opacity-45",
                    active ? "border-transparent bg-ink text-slate" : "border-rule bg-raised text-ink-dim hover:text-ink",
                  )}
                >
                  {o.label}
                </button>
              );
            })}
          </div>
          {customDuration && (
            <NumberCommit
              label="自定义秒数"
              value={p.targetDurationSec}
              min={SEC_MIN}
              max={SEC_MAX}
              onCommit={(v) => p.onTargetDuration(Math.min(SEC_MAX, Math.max(SEC_MIN, v)))}
            />
          )}
        </Field>

        <Field
          label="分镜生成模型"
          hint={
            picked
              ? `ID: ${picked.id}${picked.capabilities.ctxSize ? ` · 实际上下文 ${picked.capabilities.ctxSize}` : " · 上下文未探到"}${
                  picked.capabilities.ctxIsPerRequest ? "（可按请求调整）" : "（llama.cpp 的 -c 是启动参数，改它要重启服务）"
                }`
              : "还没有可用的文本后端：去 设置 → AI 模型 加一个并探活"
          }
        >
          <Select
            className="w-full"
            value={currentModelValue}
            disabled={!modelOptions.length || lock}
            onChange={(e) => {
              const [bid, model] = e.target.value.split("::");
              p.onShotModel(bid, model || null);
            }}
          >
            {!modelOptions.length && <option value="">（没有可用后端：去 设置 → AI 模型 加一个）</option>}
            {fallbackOption && <option value={fallbackOption.value}>{fallbackOption.label}</option>}
            {modelOptions.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </Select>
          {picked?.lastProbeOk === false && (
            <p className="mt-1 text-caption leading-snug text-state-fail">这台后端最近一次探活失败：{picked.lastError || "未返回原因"}</p>
          )}
        </Field>

        <Field label="视觉风格" hint="选中即写回项目；自定义文本会被原样当风格提示词用">
          <div className="grid grid-cols-2 gap-1.5">
            {VISUAL_STYLES.map((s) => {
              const active = s.prompt ? p.visualStyle === s.key : customStyle;
              return (
                <button
                  key={s.key}
                  type="button"
                  aria-pressed={active}
                  disabled={lock}
                  title={s.desc}
                  onClick={() => p.onVisualStyle(s.prompt ? s.key : "")}
                  className={cn(
                    "flex h-7 items-center justify-center gap-1 rounded-ctl border px-1.5 text-caption transition-colors disabled:opacity-45",
                    active ? "border-transparent bg-ink text-slate" : "border-rule bg-raised text-ink-dim hover:text-ink",
                  )}
                >
                  <span aria-hidden>{s.emoji}</span>
                  <span className="truncate">{s.name}</span>
                </button>
              );
            })}
          </div>
          {customStyle && (
            <div className="space-y-1">
              <TextCommit
                value={p.visualStyle}
                disabled={lock}
                placeholder="自定义风格，如：水墨写意 / 复古胶片颗粒"
                onCommit={(v) => p.onVisualStyle(v)}
              />
              <p className="text-caption leading-snug text-ink-mute">
                当前拼进提示词的这一段是：<span className="mono text-ink-dim">{stylePrompt(p.visualStyle)}</span>
              </p>
            </div>
          )}
        </Field>

        <Field label="画幅" hint="只影响出图尺寸与出片分辨率，不改这一页的布局">
          <div className="grid grid-cols-2 gap-1.5">
            {(
              [
                { v: "16:9", label: "横屏 16:9" },
                { v: "9:16", label: "竖屏 9:16" },
              ] as { v: AspectRatio; label: string }[]
            ).map((o) => (
              <button
                key={o.v}
                type="button"
                aria-pressed={p.aspectRatio === o.v}
                disabled={lock}
                onClick={() => p.onAspectRatio(o.v)}
                className={cn(
                  "h-7 rounded-ctl border px-2 text-note transition-colors disabled:opacity-45",
                  p.aspectRatio === o.v ? "border-transparent bg-ink text-slate" : "border-rule bg-raised text-ink-dim hover:text-ink",
                )}
              >
                {o.label}
              </button>
            ))}
          </div>
        </Field>

        <Field
          label="H3 提示词模式"
          hint="切档会立刻按新模式重拼已有镜头的结构化提示词（本地模板，不占 GPU）；AI 重写过的镜头默认保留"
        >
          <div className="space-y-1.5">
            {H3_PROMPT_MODES.map((m) => {
              const active = p.promptMode === m.key;
              return (
                <button
                  key={m.key}
                  type="button"
                  aria-pressed={active}
                  disabled={lock || p.promptModeBusy}
                  onClick={() => p.onPromptMode(m.key)}
                  className={cn(
                    "w-full rounded-ctl border px-2 py-1.5 text-left transition-colors disabled:opacity-45",
                    active ? "border-chrome/45 bg-chrome/10" : "border-rule bg-raised hover:border-rule/70",
                  )}
                >
                  <span className="flex items-center gap-2">
                    <span className={cn("text-note font-semibold", active ? "text-ink" : "text-ink-dim")}>{m.name}</span>
                    <span className="mono ml-auto shrink-0 text-micro text-ink-mute">{m.eta}</span>
                  </span>
                  <span className="mt-0.5 block text-caption leading-snug text-ink-mute">{m.when}</span>
                </button>
              );
            })}
          </div>
          {p.promptModeBusy ? (
            <p className="text-caption leading-snug text-ink-dim">正在按新模式重拼镜头…</p>
          ) : (
            p.promptModeNote && <p className="text-caption leading-snug text-ink-mute">{p.promptModeNote}</p>
          )}
          <TextCommit value={p.promptReason} disabled={lock} placeholder="为什么选这个模式（模式决策留痕，避免静默走默认）" onCommit={(v) => p.onPromptReason(v)} />
        </Field>

        <Field label="镜头衔接" hint="只作用于勾了「承接上一镜」的镜头所串成的续拍链">
          <div className="rounded-ctl border border-rule-soft bg-white/[0.03] px-2 py-1.5">
            <Toggle checked={p.continuity} disabled={lock} onChange={p.onContinuity} label={<span className="text-caption">允许把相邻镜头串成一条续拍链</span>} />
          </div>
          {p.continuity && (
            <div className="flex items-center gap-1.5">
              <span className="mono text-[10px] text-ink-mute">段间重叠帧</span>
              {[5, 22, 39, 56].map((n) => {
                const active = p.continuityOverlapFrames === n;
                return (
                  <button
                    key={n}
                    type="button"
                    aria-pressed={active}
                    disabled={lock}
                    title={n <= 5 ? "接缝最短，最省时间" : n >= 56 ? "最顺但每段多出一大截重采样" : "默认档，顺与省之间的折中"}
                    onClick={() => p.onContinuityOverlap(n)}
                    className={cn(
                      "h-6 min-w-[42px] rounded-ctl border px-1.5 text-[11px] transition-colors disabled:opacity-45",
                      active ? "border-transparent bg-ink text-slate" : "border-rule bg-raised text-ink-dim hover:text-ink",
                    )}
                  >
                    {n}
                  </button>
                );
              })}
            </div>
          )}
          <p className="text-[10.5px] leading-snug text-ink-mute">
            关掉这张开关就逐镜单独出片：接缝只靠各自的首帧与「接上一镜」写下的文字锚点接续，不走 h3_chain 的 latent 回放。
          </p>
        </Field>

        <div className="flex items-start gap-2 rounded-ctl border border-rule bg-raised/50 p-2.5 text-caption leading-snug text-ink-mute">
          <Info className="mt-[1px] h-3 w-3 flex-none" />
          <span>
            {LLM_ETA_HINT}。GPU 正在出图/出片时后端会直接回 409，错误原话会显示在下面。当前剧本 {p.scriptChars} 字符。
          </span>
        </div>
      </div>

      <div className="flex-none space-y-2 border-t border-rule-soft p-4">
        <Button
          className="h-10 w-full text-body tracking-wide"
          variant="primary"
          icon={p.busy ? <BrainCircuit className="h-4 w-4 animate-spin" /> : <Wand2 className="h-4 w-4" />}
          loading={!!p.busy}
          disabled={!p.projectName.trim()}
          title={p.busy ? "上一次生成还没结束：本机模型是单槽串行，排队只会一起变慢" : "拆解剧本 → 回写角色/场景 → 规划分镜镜头表"}
          onClick={p.onGenerate}
        >
          {p.busy ? "生成中…" : p.hasShots ? "重新生成分镜脚本" : "生成分镜脚本"}
        </Button>

        {p.busy && (
          <div className="rounded-ctl border border-rule bg-raised/60 px-2.5 py-2 text-note leading-snug text-ink-dim">
            <span className="mono label-mono block">STEP</span>
            {p.busy}
          </div>
        )}
        {p.error && (
          <div className="flex items-start gap-2 rounded-ctl border border-state-fail/45 bg-state-fail/10 px-2.5 py-2 text-note leading-snug text-state-fail">
            <AlertCircle className="mt-[1px] h-3.5 w-3.5 flex-none" />
            <span className="min-w-0 break-words">{p.error}</span>
          </div>
        )}
        {!p.busy && !p.error && p.hasShots && <Badge tone="good">已有镜头表，重跑会按序号对齐保留产物</Badge>}
      </div>
    </div>
  );
}

/** 一个后端能选哪个模型：Ollama 一台多模型，llama.cpp 通常就是挂着的那一个 */
function modelChoices(b: LlmBackend): { value: string; label: string }[] {
  const models = b.capabilities.models?.length ? b.capabilities.models : [""];
  return models.map((m) => ({
    value: `${b.id}::${m}`,
    label: `${b.name}${m ? ` - ${m}` : " - 未探到模型清单"}${b.isDefault ? "（默认）" : ""} · ${b.scope === "local" ? "本机" : "云端"}`,
  }));
}

/** 自定义风格文本：本地敲字，失焦/回车才写 config.visualStyle（一次一个 mutation，不会互相覆盖） */
function TextCommit({ value, placeholder, disabled, onCommit }: { value: string; placeholder: string; disabled: boolean; onCommit: (v: string) => void }) {
  const [v, setV] = useState(value);
  useEffect(() => setV(value), [value]);
  return (
    <Input
      className="w-full"
      value={v}
      disabled={disabled}
      placeholder={placeholder}
      onChange={(e) => setV(e.target.value)}
      onBlur={() => v.trim() !== value && onCommit(v.trim())}
      onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()}
    />
  );
}

function NumberCommit({ label, value, min, max, onCommit }: { label: string; value: number; min: number; max: number; onCommit: (v: number) => void }) {
  const [v, setV] = useState(String(value));
  useEffect(() => setV(String(value)), [value]);
  return (
    <label className="mt-1.5 flex items-center gap-2">
      <span className="label flex-none">{label}</span>
      <Input
        className="w-full flex-1"
        inputMode="numeric"
        value={v}
        onChange={(e) => setV(e.target.value.replace(/[^0-9]/g, ""))}
        onBlur={() => v && onCommit(Number(v))}
        onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()}
      />
      <span className="label flex-none">
        {min}–{max} 秒
      </span>
    </label>
  );
}
