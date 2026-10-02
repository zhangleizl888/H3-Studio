import { useEffect, useState } from "react";
import { AlertCircle, ChevronLeft, ChevronRight, Film, MapPin, MessageSquare, Scissors, Sparkles, Video, Wand2, X } from "lucide-react";
import { Badge, Button, Input, MachChip, Panel, Progress, Select, StateGlyph, type StateKey, Toggle } from "../../../components/ui";
import { GenPresetPicker } from "../../../components/GenPresetPicker";
import { SkillPicker } from "../../../components/SkillPicker";
import { SplitHandle, usePane } from "../../../components/SplitPane";
import { VersionGroup } from "../../../components/VersionHistory";
import { videoChainShots, videoRequest } from "../../../lib/generate";
import type { GenTarget } from "../../../lib/generate";
import { genKey } from "../../../lib/useGenerate";
import { useInstances } from "../../../lib/hooks";
import { CAMERA_MOVEMENTS_ZH, SHOT_SIZES_ZH, buildH3Prompt, framesToSeconds, h3FrameCount, h3ModeMeta, h3PromptIncomplete, h3PromptText, movementLabel } from "../../../lib/prompts";
import type { Character, H3Prompt, H3PromptMode, Shot } from "../../../lib/types";
import { cardLabel, CommitText, FRAME_TYPES, frameMediaId, frameOf, mediaOf, MediaImage } from "./common";
import type { DirectorCtx, FrameType } from "./common";
import { KeyframePanel } from "./KeyframePanel";

const SHOT_STATE: Record<Shot["state"], StateKey> = {
  idle: "idle",
  queued: "queued",
  generating: "running",
  completed: "succeeded",
  failed: "failed",
};

/**
 * 镜头详情抽屉：场景环境 → 叙事动作 → 视觉制作，三段从上到下。
 * 写回全部过 ctx.patchShot（一份 shots 数组），生成全部走 useGenerator。
 */
export function ShotDrawer({
  ctx,
  shot,
  index,
  onClose,
  onPrev,
  onNext,
  onSplit,
}: {
  ctx: DirectorCtx;
  shot: Shot;
  index: number;
  onClose: () => void;
  onPrev: () => void;
  onNext: () => void;
  onSplit: (shot: Shot) => Promise<void>;
}) {
  const pane = usePane("director.drawer", 430, 340, 820);
  const [llm, setLlm] = useState<null | "action" | "moderation" | "split">(null);
  const [kfClaim, setKfClaim] = useState<FrameType | null>(null);
  const { data: instances } = useInstances();

  useEffect(() => {
    setKfClaim(null);
  }, [shot.id]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLElement && ["INPUT", "TEXTAREA", "SELECT"].includes(e.target.tagName)) return;
      if (e.key === "Escape") onClose();
      if (e.key === "ArrowLeft" && index > 0) onPrev();
      if (e.key === "ArrowRight" && index < ctx.shots.length - 1) onNext();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, onPrev, onNext, index, ctx.shots.length]);

  const scene = ctx.project.data.scenes.find((s) => s.id === shot.sceneId);
  const chars = ctx.project.data.characters.filter((c) => shot.characterIds.includes(c.id));
  const modeMeta = h3ModeMeta(ctx.project.config.h3PromptMode);
  const prevShot = index > 0 ? ctx.shots[index - 1] : null;
  const videoHandle = ctx.handles[genKey({ kind: "video", shotId: shot.id } satisfies GenTarget)];
  const inst = instances?.find((i) => i.id === (shot.preset?.instanceId ?? shot.instanceId ?? ctx.project.config.videoInstanceId));
  const hasStart = !!frameMediaId(shot, "start");
  // 连着几个「承接上一镜」就是几段：段数 >1 时出片改走无缝续拍节点
  const chainLen = videoChainShots(ctx.project, shot).length;

  /** AI 生成动作建议：把这一镜的上下文交给 visualize 用途，结果回填动作描述 */
  async function suggestAction() {
    setLlm("action");
    try {
      const brief = JSON.stringify({
        场景: scene ? `${scene.location || scene.name}${scene.time ? `·${scene.time}` : ""}` : "未指定",
        出场: chars.map((c) => `${c.name}（${c.desc}）`),
        现有动作: shot.action,
        台词: shot.dialogue ?? "",
        运镜: shot.cameraMovement,
        景别: shot.shotSize,
        时长秒: shot.durationSec,
        起始帧提示词: frameOf(shot, "start")?.visualPrompt ?? "",
        结束帧提示词: frameOf(shot, "end")?.visualPrompt ?? "",
      });
      const res = await ctx.api.llm.run("visualize", brief, { backendId: ctx.project.config.llmBackendId ?? undefined, skillIds: shot.skillIds });
      const text = ((res.data as { visualPrompt?: string }).visualPrompt ?? "").trim();
      if (!text) {
        ctx.notify("模型没给出可用的动作建议", "bad");
        return;
      }
      ctx.patchShot(shot.id, { action: text });
      ctx.notify(`动作建议已回填（${(res.latencyMs / 1000).toFixed(1)}s）`, "ok");
    } catch (e) {
      ctx.notify(`AI 生成动作建议失败：${e instanceof Error ? e.message : String(e)}`, "bad");
    } finally {
      setLlm(null);
    }
  }

  /** 按项目的 H3 提示词模式让模型重写这一镜（三段式 / 六段式 / 中文分镜块 / hybrid） */
  async function optimizeForModeration() {
    setLlm("moderation");
    const mode = ctx.project.config.h3PromptMode;
    const meta = h3ModeMeta(mode);
    try {
      const brief = JSON.stringify({
        画幅: ctx.project.config.aspectRatio,
        时长秒: shot.durationSec,
        景别: shot.shotSize,
        运镜: shot.cameraMovement,
        动作: shot.action,
        台词: shot.dialogue ?? "",
        场景: scene ? `${scene.location || scene.name}${scene.time ? `·${scene.time}` : ""}` : "未指定",
        出场: chars.map((c) => `${c.name}（${c.desc}）`),
        上一镜: prevShot ? `${prevShot.action}${prevShot.dialogue ? `｜台词「${prevShot.dialogue}」` : ""}` : "（本片第一镜）",
        接上一镜: shot.continuityAnchor ?? "",
        起始帧: frameOf(shot, "start")?.visualPrompt ?? "",
        现有提示词: shot.videoPrompt?.trim() || h3PromptText(shot.h3Prompt),
      });
      const res = await ctx.api.llm.run("h3_prompt", brief, {
        backendId: ctx.project.config.llmBackendId ?? undefined,
        mode,
        durationSec: shot.durationSec,
        aspect: ctx.project.config.aspectRatio,
        style: ctx.project.config.visualStyle,
        skillIds: shot.skillIds,
      });
      const d = res.data as Partial<H3Prompt>;
      const keep = (v: string | undefined, fallback: string) => (typeof v === "string" && v.trim() ? v.trim() : fallback);
      const next: H3Prompt = {
        mode,
        integrated: keep(d.integrated, shot.h3Prompt.integrated),
        soundscape: keep(d.soundscape, shot.h3Prompt.soundscape),
        music: keep(d.music, shot.h3Prompt.music),
        aiRewrittenAt: new Date().toISOString(),
      };
      if (mode === "six_section" || mode === "hybrid") {
        next.subjectDefinitions = keep(d.subjectDefinitions, shot.h3Prompt.subjectDefinitions ?? "");
        next.summary = keep(d.summary, shot.h3Prompt.summary ?? "");
        next.retentionAnalysis = keep(d.retentionAnalysis, shot.h3Prompt.retentionAnalysis ?? "");
        next.detailedDescription = keep(d.detailedDescription, shot.h3Prompt.detailedDescription ?? "");
        if (mode === "hybrid") next.constraints = keep(d.constraints, shot.h3Prompt.constraints ?? "");
      } else if (mode === "wenwu") {
        next.sceneDescription = keep(d.sceneDescription, shot.h3Prompt.sceneDescription ?? "");
        next.shotBlocks = Array.isArray(d.shotBlocks) && d.shotBlocks.length ? d.shotBlocks : (shot.h3Prompt.shotBlocks ?? []);
      }
      // 提交文本清空：让 videoRequest 现拼结构化字段，换模式才不会把上一模式的旧文本发去烧显存
      ctx.patchShot(shot.id, { h3Prompt: next, videoPrompt: "" });
      ctx.notify(`已按「${meta.name}」重写这一镜（${(res.latencyMs / 1000).toFixed(1)}s，${meta.eta} 是预期）`, "ok");
    } catch (e) {
      ctx.notify(`AI 重写失败：${e instanceof Error ? e.message : String(e)}`, "bad");
    } finally {
      setLlm(null);
    }
  }

  async function runSplit() {
    setLlm("split");
    try {
      await onSplit(shot);
    } finally {
      setLlm(null);
    }
  }

  return (
    <aside
      style={pane.style}
      className="relative flex h-full w-[var(--pane-w)] flex-none flex-col border-l border-hairline bg-slate/80 backdrop-blur-2xl"
      aria-label="镜头详情"
    >
      <SplitHandle pane={pane} side="right" label="镜头详情栏宽度" />
      <header className="flex flex-none items-start gap-2.5 border-b border-hairline bg-sheen px-3 py-2.5">
        <span className="mono grid h-8 w-8 flex-none place-items-center rounded-panel border border-chrome/25 bg-chrome/10 text-caption font-bold text-chrome">
          {cardLabel(shot)}
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            <h3 className="text-body font-semibold">镜头详情</h3>
            <StateGlyph state={SHOT_STATE[shot.state]} />
          </div>
          <p className="line-clamp-2 text-caption leading-snug text-ink-mute">{movementLabel(shot.cameraMovement) || "未设运镜"}</p>
        </div>
        <div className="flex flex-none items-center gap-0.5">
          <Button size="sm" variant="ghost" aria-label="上一镜" disabled={index === 0} onClick={onPrev} icon={<ChevronLeft className="h-3.5 w-3.5" />} />
          <Button size="sm" variant="ghost" aria-label="下一镜" disabled={index >= ctx.shots.length - 1} onClick={onNext} icon={<ChevronRight className="h-3.5 w-3.5" />} />
          <span className="mx-1 h-4 w-px bg-rule" aria-hidden />
          <Button size="sm" variant="ghost" aria-label="关闭镜头详情" onClick={onClose} icon={<X className="h-3.5 w-3.5" />} />
        </div>
      </header>

      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-3">
        {/* ① 场景环境 */}
        <Panel
          title={
            <span className="flex items-center gap-1.5">
              <MapPin className="h-3.5 w-3.5" /> 场景环境 (SCENE CONTEXT)
            </span>
          }
        >
          <SceneContext ctx={ctx} shot={shot} />
        </Panel>

        {/* ② 叙事动作 */}
        <Panel
          title={
            <span className="flex items-center gap-1.5">
              <Film className="h-3.5 w-3.5" /> 叙事动作 (ACTION & DIALOGUE)
            </span>
          }
          actions={
            <div className="flex items-center gap-1">
              <Button size="sm" variant="ghost" loading={llm === "action"} icon={<Sparkles className="h-3 w-3" />} onClick={() => void suggestAction()} title="按场景、角色与首尾帧提示词生成这一镜的动作建议">
                动作建议
              </Button>
              <Button size="sm" variant="ghost" loading={llm === "moderation"} icon={<Wand2 className="h-3 w-3" />} onClick={() => void optimizeForModeration()} title={`按项目模式「${modeMeta.name}」重写这一镜的提示词（${modeMeta.eta}）`}>
                按模式重写
              </Button>
              <Button size="sm" variant="ghost" loading={llm === "split"} icon={<Scissors className="h-3 w-3" />} onClick={() => void runSplit()} title="用模型把这一镜拆成 2-3 个子镜">
                拆分
              </Button>
            </div>
          }
        >
          <div className="space-y-2.5">
            <div>
              <span className="label">画面与动作</span>
              <CommitText ariaLabel="动作描述" value={shot.action} rows={4} placeholder="这一镜里谁做了什么，镜头怎么动" onCommit={(v) => ctx.patchShot(shot.id, { action: v })} />
            </div>
            <div>
              <span className="label flex items-center gap-1">
                <MessageSquare className="h-3 w-3" /> 台词
              </span>
              <CommitText ariaLabel="台词" value={shot.dialogue ?? ""} rows={2} placeholder="留空表示这一镜没有台词" onCommit={(v) => ctx.patchShot(shot.id, { dialogue: v })} />
            </div>
            <div>
              <span className="label">接上一镜（锚点）</span>
              <CommitText
                ariaLabel="衔接锚点"
                value={shot.continuityAnchor ?? ""}
                rows={2}
                placeholder="动作方向 / 视线目标 / 同一道光 / 同一个道具 / 同一种轮廓 / 同一段声音 / 同一股受力，写成一句看得见的话"
                onCommit={(v) => ctx.patchShot(shot.id, { continuityAnchor: v })}
              />
              <p className="mt-1 text-caption leading-snug text-ink-mute">
                {prevShot ? (
                  <>
                    上一镜是 <span className="mono">{cardLabel(prevShot)}</span>：{prevShot.action || "没写动作"}。这句会被拼进视频提示词，属于
                    <span className="text-ink-dim">文字接续</span>；勾了「承接上一镜」还另有一路尾帧的
                    <span className="text-ink-dim">画面接续</span>，两条互不替代。
                  </>
                ) : (
                  "这是本片第一镜，没有要接的东西。"
                )}
              </p>
            </div>
            <div className="grid grid-cols-2 gap-2">
              <label className="block space-y-1">
                <span className="label">运镜</span>
                <Select className="w-full" value={shot.cameraMovement} onChange={(e) => ctx.patchShot(shot.id, { cameraMovement: e.target.value })} aria-label="运镜">
                  {shot.cameraMovement && !CAMERA_MOVEMENTS_ZH.includes(shot.cameraMovement) && <option value={shot.cameraMovement}>{shot.cameraMovement}</option>}
                  {CAMERA_MOVEMENTS_ZH.map((m) => (
                    <option key={m} value={m}>
                      {movementLabel(m)}
                    </option>
                  ))}
                </Select>
              </label>
              <label className="block space-y-1">
                <span className="label">景别</span>
                <Select className="w-full" value={shot.shotSize} onChange={(e) => ctx.patchShot(shot.id, { shotSize: e.target.value })} aria-label="景别">
                  {shot.shotSize && !(SHOT_SIZES_ZH as readonly string[]).includes(shot.shotSize) && <option value={shot.shotSize}>{shot.shotSize}（旧值，不在七档里）</option>}
                  {SHOT_SIZES_ZH.map((s) => (
                    <option key={s} value={s}>
                      {s}
                    </option>
                  ))}
                </Select>
              </label>
            </div>
            {shot.parentShotId && <Badge>子镜 · 由 {cardLabel({ ...shot, id: shot.parentShotId, parentShotId: undefined })} 拆分而来</Badge>}
          </div>
        </Panel>

        {/* ③ 视觉制作 */}
        <Panel
          title={<span className="label-mono">视觉制作 (VISUAL PRODUCTION)</span>}
          actions={
            <Toggle
              checked={ctx.project.config.enhancePrompts}
              onChange={(v) => ctx.setConfig({ enhancePrompts: v })}
              label={<span className="text-caption">AI增强提示词</span>}
            />
          }
        >
          <div className="space-y-3">
            <div className="grid grid-cols-2 gap-2.5">
              {FRAME_TYPES.map((t) => (
                <KeyframePanel key={t} ctx={ctx} shot={shot} type={t} prevShot={prevShot} claim={kfClaim} onClaim={setKfClaim} />
              ))}
            </div>

            <div className="flex flex-wrap items-end gap-x-3 gap-y-1">
              <label className="block space-y-1">
                <span className="label">时长（秒）</span>
                <div className="flex items-center gap-2">
                  <Input
                    type="number"
                    min={2}
                    max={15}
                    step={0.1}
                    aria-label="时长秒"
                    className="w-20"
                    value={shot.durationSec}
                    onChange={(e) => {
                      const sec = Math.min(15, Math.max(2, Number(e.target.value) || 2));
                      ctx.patchShot(shot.id, { durationSec: sec, frameCount: h3FrameCount(sec) });
                    }}
                  />
                  <span className="mono text-caption text-ink-mute">→ {shot.frameCount} 帧</span>
                </div>
              </label>
              <p className="pb-1 text-caption leading-snug text-ink-mute">
                H3 只吃 17k+5 帧，这里已向上吸附：{shot.frameCount} 帧 ≈ {framesToSeconds(shot.frameCount).toFixed(2)}s
                {shot.frameCount !== h3FrameCount(shot.durationSec) && <span className="text-state-fail"> · 与时长不符，改一下时长即可修正</span>}
              </p>
            </div>

            <H3PromptFields ctx={ctx} shot={shot} />

            <div className="space-y-2 border-t border-rule-soft pt-2.5">
              <div className="flex items-center gap-2">
                <span className="label-mono">出片</span>
                {inst && <MachChip placement={inst.placement} label={inst.name} />}
                {chainLen > 1 && (
                  <span className="ml-auto text-caption text-ink-mute" title="多段无缝拼接只有 SequenceForge 那个节点做得了，库里的工作流没有这个形状">
                    续拍链固定走内置模板，工作流这一项用不上
                  </span>
                )}
              </div>
              <GenPresetPicker project={ctx.project} kind="video" value={shot.preset} onChange={(p) => ctx.patchShot(shot.id, { preset: p })} compact />
              {prevShot && (
                <div className="rounded-ctl border border-rule-soft bg-sheen px-2 py-1.5">
                  <Toggle
                    checked={!!shot.continuesPrevious}
                    onChange={(v) => ctx.patchShot(shot.id, { continuesPrevious: v })}
                    label={<span className="text-caption">承接上一镜（无缝续拍）</span>}
                    hint={
                      <span className="text-caption leading-snug text-ink-mute">
                        {!ctx.project.config.continuity
                          ? "项目配置里「允许串成续拍链」已经关掉，这个勾现在不生效：出片仍会逐镜单独走，接续只剩「接上一镜」那句文字锚点。"
                          : shot.continuesPrevious
                            ? `本镜顺着「${cardLabel(prevShot)}」的结尾继续画，中间那些镜的首尾帧图不再介入；产物是从链头接到底的一条片。重叠帧 ${ctx.project.config.continuityOverlapFrames} 帧，在 剧本页 → 项目配置 里改。`
                            : "关着就是本镜单独出片（首帧 / 首尾帧）。"}
                      </span>
                    }
                  />
                </div>
              )}
              <VideoRow ctx={ctx} shot={shot} hasStart={hasStart} chainLen={chainLen} />
              {videoHandle && (
                <div className="space-y-1 rounded-ctl border border-rule-soft bg-sheen p-2">
                  <div className="flex items-baseline justify-between gap-2 text-caption">
                    <span className="truncate text-ink-dim">出片任务</span>
                    <Badge tone={videoHandle.state === "failed" ? "bad" : videoHandle.state === "succeeded" ? "good" : "neutral"}>{videoHandle.state}</Badge>
                  </div>
                  <Progress
                    value={videoHandle.progress === null ? undefined : Math.round(videoHandle.progress * 100)}
                    max={videoHandle.progress === null ? undefined : 100}
                    unavailable={videoHandle.progress === null}
                    stage={videoHandle.stage ?? null}
                    machine={ctx.machColor(shot.instanceId ?? ctx.project.config.videoInstanceId)}
                    stripe={videoHandle.state === "running" || videoHandle.state === "queued"}
                  />
                  {videoHandle.error && <p className="text-caption leading-snug text-state-fail">{videoHandle.error}</p>}
                </div>
              )}
              {shot.videoMediaIds.length > 0 ? (
                <ul className="grid grid-cols-3 gap-1.5">
                  {shot.videoMediaIds.map((mid, i) => {
                    const m = mediaOf(ctx, mid);
                    // 成片本身没有可显示的封面（后端不出缩略图），用这一镜的首帧静帧当封面：
                    // 那正是这段视频的第一帧。缺首帧时 MediaImage 会退回视频占位块，不会变破图。
                    const poster = mediaOf(ctx, frameMediaId(shot, "start"));
                    return (
                      <li key={mid}>
                        <button type="button" onClick={() => ctx.onPlay(mid, `${cardLabel(shot)} 成片 ${i + 1}`)} className="block w-full text-left">
                          <MediaImage media={poster} seedText={mid} aspect={ctx.aspect} label={<span className="mono">{m?.durationMs ? `${(m.durationMs / 1000).toFixed(1)}s` : `第 ${i + 1} 段`}</span>} />
                        </button>
                      </li>
                    );
                  })}
                </ul>
              ) : (
                <p className="text-caption leading-snug text-ink-mute">
                  {hasStart ? "还没有成片。" : "至少要有一张起始帧才能出片。"}
                  {hasStart && !frameMediaId(shot, "end") && " 没有结束帧时走单图生视频（i2v），有则走首尾帧（fl2v）。"}
                </p>
              )}

              {/* 成片的多版本：videoMediaIds 一直把所有版本都存着，界面上却只看得见「第 N 段」。
                  这条轨道能翻旧版、能删进回收站；[0] 就是时间轴与导出实际取用的那一版。
                  成片没有缩略图，封面一律用这一镜的首帧静帧。 */}
              <VersionGroup
                projectId={ctx.project.id}
                role="video"
                refId={shot.id}
                aspect={ctx.aspect}
                label={`${cardLabel(shot)} 成片`}
                posterOf={() => mediaOf(ctx, frameMediaId(shot, "start"))}
              />
            </div>
          </div>
        </Panel>
      </div>
    </aside>
  );
}

/* ───────── ① 场景环境 ───────── */

function SceneContext({ ctx, shot }: { ctx: DirectorCtx; shot: Shot }) {
  const { scenes, characters } = ctx.project.data;
  const scene = scenes.find((s) => s.id === shot.sceneId);
  const active = shot.characterIds.map((cid) => characters.find((c) => c.id === cid)).filter((c): c is Character => !!c);
  const available = characters.filter((c) => !shot.characterIds.includes(c.id));
  const sceneMedia = mediaOf(ctx, scene?.refMediaIds[0]);

  function setCharacters(ids: string[]) {
    const keep = Object.fromEntries(Object.entries(shot.variationByChar ?? {}).filter(([cid]) => ids.includes(cid)));
    ctx.patchShot(shot.id, { characterIds: ids, variationByChar: keep });
  }

  return (
    <div className="space-y-2.5">
      <div className="flex gap-2.5">
        <MediaImage media={sceneMedia} seedText={scene?.id ?? "scene"} aspect="4/3" className="w-24 flex-none" alt="场景参考图" />
        <div className="min-w-0 flex-1 space-y-1.5">
          <div className="flex items-center gap-1.5">
            <Select className="min-w-0 flex-1" aria-label="场景" value={shot.sceneId ?? ""} onChange={(e) => ctx.patchShot(shot.id, { sceneId: e.target.value || null })}>
              <option value="">未指定场景</option>
              {scenes.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.location || s.name}
                </option>
              ))}
            </Select>
            {scene?.time && (
              <Badge className="flex-none">
                <span className="label-mono mr-1">时段</span>
                {scene.time}
              </Badge>
            )}
          </div>
          <p className="line-clamp-3 text-note leading-snug text-ink-mute">{scene?.desc || scene?.atmosphere || "这个场景还没有描述。"}</p>
        </div>
      </div>

      <ul className="space-y-1">
        {active.map((c) => {
          const avatar = mediaOf(ctx, c.refMediaIds[0]);
          const chosen = shot.variationByChar?.[c.id] ?? "";
          return (
            <li key={c.id} className="flex items-center gap-2 rounded-ctl border border-rule-soft bg-sheen px-1.5 py-1">
              <MediaImage media={avatar} seedText={c.id} aspect="1/1" className="h-6 w-6 flex-none rounded-full" />
              <span className="min-w-0 flex-1 truncate text-note text-ink-dim">{c.name || "未命名角色"}</span>
              {c.variations.length > 0 && (
                <Select
                  className="h-6 w-24 flex-none text-caption"
                  aria-label={`${c.name} 服装变体`}
                  value={chosen}
                  onChange={(e) => {
                    const next = { ...(shot.variationByChar ?? {}) };
                    if (e.target.value) next[c.id] = e.target.value;
                    else delete next[c.id];
                    ctx.patchShot(shot.id, { variationByChar: next });
                  }}
                >
                  <option value="">基础造型</option>
                  {c.variations.map((v) => (
                    <option key={v.id} value={v.id}>
                      {v.name}
                    </option>
                  ))}
                </Select>
              )}
              <Button size="sm" variant="ghost" aria-label={`把 ${c.name} 移出这一镜`} onClick={() => setCharacters(shot.characterIds.filter((x) => x !== c.id))} icon={<X className="h-3 w-3" />} />
            </li>
          );
        })}
        {active.length === 0 && <li className="text-caption text-ink-mute">这一镜还没有登记出场角色。</li>}
      </ul>

      {available.length > 0 && (
        <Select className="w-full" aria-label="添加角色到此镜头" value="" onChange={(e) => e.target.value && setCharacters([...shot.characterIds, e.target.value])}>
          <option value="">+ 添加角色到此镜头</option>
          {available.map((c) => (
            <option key={c.id} value={c.id}>
              {c.name || "未命名角色"}
            </option>
          ))}
        </Select>
      )}
    </div>
  );
}

/* ───────── ③ H3 提示词与出片 ───────── */

/** 每种模式要编辑的字段。段名与后端 director.h3_schema 的字段一一对应 */
const MODE_FIELDS: Record<H3PromptMode, { key: keyof H3Prompt; label: string; rows: number }[]> = {
  three_field: [
    { key: "integrated", label: "integrated_multimodal_description（画面+运动+镜头）", rows: 3 },
    { key: "soundscape", label: "overall_soundscape（环境声）", rows: 2 },
    { key: "music", label: "non_diegetic_music（无 BGM 写 N/A）", rows: 1 },
  ],
  six_section: [
    { key: "integrated", label: "一句话主旨（列表显示用）", rows: 1 },
    { key: "subjectDefinitions", label: "subject_definitions（生命核与素材职责）", rows: 3 },
    { key: "summary", label: "summary（最终发展线）", rows: 2 },
    { key: "retentionAnalysis", label: "retention_analysis（保留关系核对）", rows: 2 },
    { key: "detailedDescription", label: "detailed_description（[Shot N] 时间码镜头脉冲）", rows: 5 },
    { key: "soundscape", label: "overall_soundscape", rows: 2 },
    { key: "music", label: "non_diegetic_music（无 BGM 写 N/A）", rows: 1 },
  ],
  hybrid: [
    { key: "integrated", label: "一句话主旨（列表显示用）", rows: 1 },
    { key: "subjectDefinitions", label: "subject_definitions（生命核与素材职责）", rows: 3 },
    { key: "summary", label: "summary（最终发展线）", rows: 2 },
    { key: "retentionAnalysis", label: "retention_analysis（八条生命通道逐条核对）", rows: 3 },
    { key: "detailedDescription", label: "detailed_description（镜头脉冲 + 表演/状态肌理）", rows: 6 },
    { key: "soundscape", label: "overall_soundscape", rows: 2 },
    { key: "music", label: "non_diegetic_music（BPM/乐器/进出时间；无 BGM 写 N/A）", rows: 1 },
    { key: "constraints", label: "constraints（风格与负向约束块）", rows: 2 },
  ],
  wenwu: [
    { key: "integrated", label: "一句话主旨（列表显示用）", rows: 1 },
    { key: "sceneDescription", label: "一、整体场景描述（时长/画幅/类型/生命核/素材职责/发展线）", rows: 3 },
    { key: "soundscape", label: "三、声音落点", rows: 2 },
    { key: "music", label: "三、配乐（无 BGM 写 N/A）", rows: 1 },
  ],
};

function H3PromptFields({ ctx, shot }: { ctx: DirectorCtx; shot: Shot }) {
  const scene = ctx.project.data.scenes.find((s) => s.id === shot.sceneId);
  const chars = ctx.project.data.characters.filter((c) => shot.characterIds.includes(c.id));
  const mode = shot.h3Prompt.mode ?? ctx.project.config.h3PromptMode;
  const meta = h3ModeMeta(mode);
  const rebuilt = buildH3Prompt(shot, scene, chars, ctx.project.config);
  const incomplete = h3PromptIncomplete(shot.h3Prompt);
  const setH3 = (k: keyof H3Prompt, v: string) => ctx.patchShot(shot.id, { h3Prompt: { ...shot.h3Prompt, [k]: v } as H3Prompt });

  return (
    <div className="space-y-2 rounded-ctl border border-rule-soft bg-sheen p-2">
      <div className="flex items-center gap-2">
        <span className="label-mono">视频提示词（{meta.name}）</span>
        <Button size="sm" variant="quiet" className="ml-auto" onClick={() => ctx.patchShot(shot.id, { h3Prompt: rebuilt, videoPrompt: "" })} title="按本镜的场景、角色、运镜与项目模式重拼一份结构化提示词">
          按模式重拼
        </Button>
      </div>
      <CommitText
        ariaLabel="视频提示词"
        value={shot.videoPrompt ?? ""}
        rows={3}
        mono
        placeholder={`留空则提交这一串：\n\n${h3PromptText(incomplete && !shot.videoPrompt ? rebuilt : shot.h3Prompt)}`}
        onCommit={(v) => ctx.patchShot(shot.id, { videoPrompt: v })}
      />
      {/* 技能挂在这个框上：勾中的正文由后端并进这次出片的提示词，框里仍是你自己写的那串 */}
      <SkillPicker stage="video" value={shot.skillIds} onChange={(ids) => ctx.patchShot(shot.id, { skillIds: ids })} />
      {incomplete && (
        <p className="flex items-start gap-1.5 text-caption leading-snug text-state-fail">
          <AlertCircle className="mt-[1px] h-3 w-3 flex-none" />
          <span>
            本镜的结构化字段还不齐（{incomplete}）。现在提交会退回三段式文本；点「按模式重拼」用本地模板补齐，或让模型按「{meta.name}」重写（{meta.eta}）。
          </span>
        </p>
      )}
      <div className="space-y-1.5">
        {MODE_FIELDS[mode].map((f) => (
          <label key={String(f.key)} className="block min-w-0 space-y-1">
            <span className="mono block truncate text-caption text-ink-mute">{f.label}</span>
            <CommitText ariaLabel={`H3 ${String(f.key)}`} value={String(shot.h3Prompt[f.key] ?? "")} rows={f.rows} mono onCommit={(v) => setH3(f.key, v)} />
          </label>
        ))}
        {mode === "wenwu" && (
          <label className="block min-w-0 space-y-1">
            <span className="mono block truncate text-caption text-ink-mute">二、分定时镜头块（块之间空一行）</span>
            <CommitText
              ariaLabel="H3 shotBlocks"
              value={(shot.h3Prompt.shotBlocks ?? []).join("\n\n")}
              rows={6}
              mono
              onCommit={(v) => ctx.patchShot(shot.id, { h3Prompt: { ...shot.h3Prompt, shotBlocks: v.split(/\n\s*\n/).map((b) => b.trim()).filter(Boolean) } })}
            />
          </label>
        )}
      </div>
      {shot.dialogue?.trim() && !h3PromptText(shot.h3Prompt).includes("<d>") && !h3PromptText(shot.h3Prompt).includes("台词") && (
        <p className="text-caption leading-snug text-mach-rh">
          本镜有台词，但提示词里没有 <span className="mono">&lt;d&gt;[中文] …&lt;/d&gt;</span>，模型不会念出来。
        </p>
      )}
    </div>
  );
}

function VideoRow({ ctx, shot, hasStart, chainLen }: { ctx: DirectorCtx; shot: Shot; hasStart: boolean; chainLen: number }) {
  const [busy, setBusy] = useState(false);
  const hasVideo = shot.videoMediaIds.length > 0;
  const chained = chainLen > 1;
  return (
    <Button
      variant="primary"
      className="w-full"
      loading={busy}
      disabled={!chained && !hasStart}
      icon={<Video className="h-3.5 w-3.5" />}
      title={
        chained
          ? `无缝续拍：本镜是这条 ${chainLen} 镜链的最新一段，前段从 latent 存档秒级回放，只采样本段，产物是从链头接到底的一条片`
          : hasStart
            ? "用首帧（有尾帧则首尾帧）生成这一镜的成片"
            : "先有起始帧才能出片"
      }
      onClick={async () => {
        setBusy(true);
        const res = await ctx.run({ kind: "video", shotId: shot.id }, videoRequest(ctx.project, shot));
        if (res.error) ctx.notify(`出片提交失败：${res.error}`, "bad");
        setBusy(false);
      }}
    >
      {chained ? `续拍到底（${chainLen} 段）` : hasVideo ? "重新出片" : "出片"}
    </Button>
  );
}
