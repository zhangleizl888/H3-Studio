import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { CircleCheck, TriangleAlert } from "lucide-react";
import { Badge, Button, Modal, StateGlyph, Tabs } from "../../components/ui";
import { ScriptVersionList } from "../../components/ScriptVersionList";
import { SplitHandle, usePane } from "../../components/SplitPane";
import { useLlmDefaults, useLlmRun, useLlms, useProject, useProjectMutations, useScriptVersionActions, useScriptVersions } from "../../lib/hooks";
import { flushSaves, queueSave } from "../../lib/localStores";
import type { AspectRatio, H3PromptMode, LlmShot, Project } from "../../lib/types";
import { ConfigPanel } from "./script/ConfigPanel";
import { Manifest } from "./script/Manifest";
import { ScriptEditor } from "./script/ScriptEditor";
import { planReprompt, repromptShots, type RepromptPlan } from "./script/reprompt";
import { ChatDock } from "./script/ChatDock";
import { LLM_MAX_INPUT_CHARS, buildShots, errText, mergeScriptEntities, normalizeParsed, storyboardBrief } from "./script/merge";

type TabKey = "create" | "manifest" | "history";

/** 本机模型没有百分比可报，进度只能说清「现在在干哪一步」，实测区间写在文案里 */
const STEP_PARSE = "第 1/2 步：正在拆解剧本结构（标题 / 角色 / 场景 / 节拍），预计 1–3 分钟…";

export default function Script() {
  const pane = usePane("script.config", 340, 260, 560);
  const { id } = useParams();
  const { data: project } = useProject(id);
  const { data: llms } = useLlms();
  const { data: llmDefaults } = useLlmDefaults();
  const muts = useProjectMutations(id);
  const llmRun = useLlmRun(id);
  // 剧本的 V1/V2 存在服务端（script_versions），正文仍写 IndexedDB：
  // 版本历史要活到浏览器之外才谈得上回收站与到期真删
  const versions = useScriptVersionActions(id);
  const { data: scriptVersions } = useScriptVersions(id);
  const [saveNote, setSaveNote] = useState<string | null>(null);

  const [tab, setTab] = useState<TabKey>("create");
  const [draft, setDraft] = useState("");
  const [title, setTitle] = useState("");
  const [saveState, setSaveState] = useState<"idle" | "saving" | "saved">("idle");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  /** 换提示词模式前先让人看清会动到哪些镜头 */
  const [modePlan, setModePlan] = useState<RepromptPlan | null>(null);
  const [applyingMode, setApplyingMode] = useState(false);

  // 生成中要挡住重复提交与切页：state 给 UI 看，ref 给事件回调用（回调里拿不到最新 state）
  const projectRef = useRef<Project | null>(null);
  const draftRef = useRef("");
  const titleRef = useRef("");
  const busyRef = useRef<string | null>(null);
  const saveTimer = useRef<number | null>(null);
  const seeded = useRef(false);
  projectRef.current = project ?? projectRef.current;

  /** 换项目（侧栏直接跳到别的项目的剧本页）必须重播种，否则草稿还是上一个项目的正文 */
  const lastId = useRef<string | undefined>(id);
  useEffect(() => {
    if (lastId.current === id) return;
    lastId.current = id;
    seeded.current = false;
    busyRef.current = null;
    setDraft("");
    setTitle("");
    setBusy(null);
    setError(null);
    setDone(null);
    setSaveState("idle");
    setModePlan(null);
    setTab("create");
  }, [id]);

  /** 首次拿到项目才播种本地草稿；之后以本地为准，否则 refetch 会把用户正敲的字冲掉 */
  useEffect(() => {
    if (!project || seeded.current) return;
    seeded.current = true;
    setDraft(project.data.rawScript);
    setTitle(project.name);
    draftRef.current = project.data.rawScript;
    titleRef.current = project.name;
    setError(project.data.taskError ?? null);
  }, [project]);

  useEffect(
    () => () => {
      if (saveTimer.current) window.clearTimeout(saveTimer.current);
    },
    [],
  );

  useEffect(() => {
    if (!busy) return;
    const block = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "分镜脚本正在生成，关掉页面这次请求就白等了";
    };
    addEventListener("beforeunload", block);
    return () => removeEventListener("beforeunload", block);
  }, [busy]);

  const markSaving = () => {
    setSaveState("saving");
    if (saveTimer.current) window.clearTimeout(saveTimer.current);
    saveTimer.current = window.setTimeout(() => setSaveState("saved"), 1300);
  };

  /** 把当前草稿 + 标题压进 1 秒防抖的本地写盘（localStores.queueSave） */
  const queueLocal = () => {
    const base = projectRef.current;
    if (!base) return;
    queueSave({
      ...base,
      name: titleRef.current,
      updatedAt: new Date().toISOString(),
      data: { ...base.data, rawScript: draftRef.current },
    });
    markSaving();
  };

  /**
   * 写库前先冲掉防抖里的草稿。
   *
   * patchProject 是从 IndexedDB 读出旧副本改完再写回的，防抖队列里要是压着更新的副本，
   * 这次写回就会把用户最后敲的那一秒抹掉 —— 所以每个 mutation 前都要 flushSaves()。
   */
  async function patchData(patch: Partial<Project["data"]>) {
    await flushSaves();
    await muts.data.mutateAsync({ rawScript: draftRef.current, ...patch });
  }

  /**
   * 生成完成 → 存一版。
   *
   * 失败绝不拦住生成：正文已经在编辑器里了，缺的只是一条历史。但一定要吭一声 ——
   * 用户以为进了历史而其实没进，比看到"这一版没存进历史"糟得多。
   */
  async function saveVersion(o: Parameters<typeof versions.snapshot>[0]) {
    try {
      await versions.snapshot(o);
      setSaveNote(null);
    } catch (e) {
      setSaveNote(errText(e));
    }
  }

  /** 这一版之前的正文。项目第一次存版时服务端会把它补成 V1（需求里那句"存之前的版本"） */
  const previousText = () => {
    const p = projectRef.current;
    return { text: p?.data.rawScript ?? "", writtenAt: p?.data.scriptWrittenAt ?? p?.updatedAt ?? null };
  };

  async function patchConfig(patch: Partial<Project["config"]>) {
    await flushSaves();
    await muts.config.mutateAsync(patch);
  }

  /**
   * 换提示词模式。没镜头就直接写 config；有镜头先把改动摊开让人确认，
   * 确认后按新模式用本地模板重拼 —— 这一步不叫模型，毫秒级完事。
   */
  function changePromptMode(next: H3PromptMode) {
    const p = projectRef.current;
    if (!p || next === p.config.h3PromptMode) return;
    if (!p.data.shots.length) {
      void patchConfig({ h3PromptMode: next });
      return;
    }
    setModePlan(planReprompt(p, next));
  }

  async function applyPromptMode(includeAi: boolean) {
    const p = projectRef.current;
    if (!p || !modePlan) return;
    setApplyingMode(true);
    setError(null);
    try {
      const res = repromptShots(p, modePlan.mode, includeAi);
      await patchConfig({ h3PromptMode: modePlan.mode });
      await patchData({ shots: res.shots });
      const bits = [`重拼 ${res.toRebuild} 镜`];
      if (res.already) bits.push(`${res.already} 镜本来就是这档`);
      if (res.aiKept) bits.push(`保留 ${res.aiKept} 镜 AI 稿`);
      if (res.clearingVideoPrompt) bits.push(`清空 ${res.clearingVideoPrompt} 镜手写的提交文本`);
      setDone(`已按「${res.modeName}」${bits.join("，")}。${res.incomplete.length ? `其中 ${res.incomplete.length} 镜本地模板填不满（${res.incomplete[0].reason}），要去导演台让模型重写。` : "字段都齐。"}`);
      setModePlan(null);
    } catch (e) {
      setError(errText(e));
    } finally {
      setApplyingMode(false);
    }
  }

  /**
   * AI 续写 / 改写。走后端第五个用途 script_write（schema 只要 { text }），
   * 它是唯一一个能吐剧本正文的口子 —— 另外四个都被 strict schema 框成结构体。
   */
  async function aiWrite(mode: "continue" | "rewrite") {
    if (busy) return;
    if (!draft.trim()) {
      setError("剧本是空的：先写点东西，AI 才知道要续什么、改什么。");
      return;
    }
    const instruction =
      mode === "continue"
        ? "接着现有剧情往下写约 300 字：保持人称、场次标题格式与每个人物的说话方式，不要复述已有内容。"
        : "在情节与人物不变的前提下改写下面这段，让动作更具体、对白更贴人物身份、去掉解释性的旁白。只输出改写后的正文。";
    const step = mode === "continue" ? "AI 续写中" : "AI 改写中";
    setBusy(step);
    setError(null);
    try {
      await flushSaves();
      const res = await llmRun.mutateAsync({ purpose: "script_write", input: `${instruction}\n\n---\n${draft}` });
      const text = String((res.data as { text?: unknown }).text ?? "").trim();
      if (!text) throw new Error("模型没有返回正文");
      const before = previousText();
      const next = mode === "continue" ? `${draft.trimEnd()}\n\n${text}` : text;
      onDraftChange(next);
      await patchData({ taskStep: undefined });
      await saveVersion({ text: next, source: "ai-write", previous: before });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  function onDraftChange(v: string) {
    setDraft(v);
    draftRef.current = v;
    queueLocal();
  }

  function onTitleChange(v: string) {
    setTitle(v);
    titleRef.current = v;
    queueLocal();
  }

  /** 标题要落到 project.name 本身（顶栏与项目列表读它），不能只停在 data 里 */
  async function commitTitle() {
    const v = titleRef.current.trim();
    if (!v || v === projectRef.current?.name) return;
    await flushSaves();
    await muts.update.mutateAsync({ name: v });
  }

  function switchTab(next: TabKey) {
    if (next === tab) return;
    if (busyRef.current && !window.confirm(`分镜脚本还在生成中（${busyRef.current}）。切过去只是换个看法，请求不会中断，但也别急着再点一次生成。确定切换？`)) return;
    setTab(next);
  }

  /**
   * 生成/重新生成分镜脚本：拆解 → 回写实体 → 分镜 → 回写镜头表。
   *
   * 每一步都真的写进 IndexedDB（这是这条链的产物，不是只发个请求），
   * 步骤写 taskStep、失败写 taskError，切页回来还能看到停在哪。
   */
  async function generate() {
    const p = projectRef.current;
    if (!p || busyRef.current) return;
    const text = draftRef.current.trim();
    const targetSec = p.config.targetDurationSec;
    // 项目没显式指定就走后端默认（/api/llm/run 在 backendId 为空时自己挑 defaults→is_default）。
    // 这里只把项目级配置和已知默认解析出来当兜底，真正的默认交给后端，避免前端和后端各挑一套。
    const backendId =
      p.config.shotModelBackendId ??
      p.config.llmBackendId ??
      llmDefaults?.script_parse?.backendId ??
      llmDefaults?.storyboard?.backendId ??
      undefined;
    // 只有「一个文本后端都没有」时才拦：项目没配、后端没默认、列表也是空的。
    const hasAnyBackend = !!backendId || (llms?.length ?? 0) > 0;

    if (!text) {
      setError("剧本是空的：先把大纲或正文贴进右侧编辑器再点生成。");
      return;
    }
    if (text.length > LLM_MAX_INPUT_CHARS) {
      setError(`剧本 ${text.length} 字符，超过后端单次上限 ${LLM_MAX_INPUT_CHARS} 字符，它会直接拒收。请先分块（一次拍一集）。`);
      return;
    }
    if (!hasAnyBackend) {
      setError("还没有可选的分镜生成模型：先去 设置 → AI 模型 加一个文本后端并探活。");
      return;
    }
    if (targetSec < 5 || targetSec > 1800) {
      setError(`目标时长 ${targetSec} 秒不在后端允许的 5–1800 秒之间。`);
      return;
    }

    setDone(null);
    setError(null);
    busyRef.current = STEP_PARSE;
    setBusy(STEP_PARSE);
    try {
      await patchData({ isParsingScript: true, taskStep: STEP_PARSE, taskError: undefined });

      const r1 = await llmRun.mutateAsync({ purpose: "script_parse", input: text, opts: { backendId } });
      const base = projectRef.current ?? p;
      const merged = mergeScriptEntities(base, normalizeParsed(r1.data));
      const step2 = `第 2/2 步：拆解完成（${merged.characters.length} 个角色 / ${merged.scenes.length} 场），正在按 ${targetSec} 秒规划分镜，预计 1–3 分钟…`;
      busyRef.current = step2;
      setBusy(step2);
      await patchData({ script: merged.script, characters: merged.characters, scenes: merged.scenes, taskStep: step2 });

      const brief = storyboardBrief(merged.script, merged.characters, merged.scenes, targetSec, text);
      const r2 = await llmRun.mutateAsync({ purpose: "storyboard", input: brief, opts: { targetSec, pace: "均匀", backendId } });
      const list = ((r2.data as { shots?: LlmShot[] }).shots ?? []).slice().sort((a, b) => a.index - b.index);
      if (!list.length) throw new Error("分镜规划返回了 0 个镜头：多半是这台模型没守住 schema。换个后端或把剧本缩短再试。");

      const step3 = `正在写回 ${list.length} 个镜头与首尾帧提示词…`;
      busyRef.current = step3;
      setBusy(step3);
      const current = projectRef.current ?? p;
      const shots = buildShots(list, merged.characters, merged.scenes, current.config, current.data.shots);
      await patchData({ shots, isParsingScript: false, taskStep: undefined, taskError: undefined });

      // 拆解会整片覆盖 characters/scenes/shots，所以这一版的快照要一起进去：
      // 只存正文的话，回到 V2 也只是回到那段字，回不到那一轮的分镜
      await saveVersion({
        text,
        source: "storyboard",
        snapshot: { script: merged.script, characters: merged.characters, scenes: merged.scenes, shots },
      });

      const warn = r2.warnings?.length ? `｜后端自检 ${r2.warnings.length} 条：${r2.warnings.slice(0, 3).join("；")}` : "";
      setDone(`已生成 ${shots.length} 个镜头（拆解 ${(r1.latencyMs / 1000).toFixed(0)} 秒 + 分镜 ${(r2.latencyMs / 1000).toFixed(0)} 秒），角色与场景已按名字并回项目。${warn}`);
      setTab("manifest");
    } catch (e) {
      const msg = errText(e);
      setError(msg);
      await patchData({ isParsingScript: false, taskStep: undefined, taskError: msg }).catch(() => undefined);
    } finally {
      busyRef.current = null;
      setBusy(null);
    }
  }

  if (!project) {
    return <div className="p-6 text-note text-ink-mute">正在读本地项目…（项目存在这台浏览器的 IndexedDB 里，换浏览器要重新导出导入）</div>;
  }

  /** 让「切档」在界面上看得见落点：这一档已经覆盖了几个镜头、有几个是 AI 重写稿 */
  const shots = project.data.shots;
  const onMode = shots.filter((s) => (s.h3Prompt?.mode ?? "three_field") === project.config.h3PromptMode).length;
  const aiShots = shots.filter((s) => s.h3Prompt?.aiRewrittenAt).length;
  const modeNote = shots.length
    ? `${onMode}/${shots.length} 镜的提示词已是这一档${aiShots ? `，其中 ${aiShots} 镜是 AI 重写稿（切档默认保留）` : ""}`
    : null;

  const shotCount = project.data.shots.length;
  // tab 上的数字只数活版本（回收站里的不算"可用"）
  const scriptVersionCount = (scriptVersions ?? []).filter((v) => !v.deletedAt).length;
  const orphan = !busy && project.data.isParsingScript;
  const backend = llms?.find((b) => b.id === (project.config.shotModelBackendId ?? project.config.llmBackendId));

  return (
    <div className="flex min-h-full flex-col">
      <header className="flex flex-wrap items-center gap-x-4 gap-y-1 border-b border-rule-soft bg-panel/70 px-4">
        <Tabs<TabKey>
          className="border-b-transparent"
          value={tab}
          onChange={switchTab}
          tabs={[
            { key: "create", label: "剧本创作" },
            { key: "manifest", label: "拍摄清单", badge: <span className="label mono">{shotCount}</span> },
            { key: "history", label: "版本历史", badge: <span className="label mono">{scriptVersionCount}</span> },
          ]}
        />
        <div className="ml-auto flex items-center gap-2 py-2">
          {busy ? (
            <span className="inline-flex items-center gap-1.5 text-note text-ink-dim">
              <StateGlyph state="running" /> 生成中 · 禁止重复提交
            </span>
          ) : (
            <span className="label">
              {backend ? `分镜走：${backend.name}${backend.capabilities.models[0] ? ` · ${backend.capabilities.models[0]}` : ""}` : "分镜走：未选后端"}
            </span>
          )}
          {saveState === "saving" ? <Badge>保存中…</Badge> : <Badge tone="good">已自动保存</Badge>}
          {/* 存版失败只在这里挂个 chip：正文已经在编辑器里，缺的只是一条历史，不该弹窗打断 */}
          {saveNote && (
            <span title={saveNote} className="cursor-help">
              <Badge tone="warn">这一版没存进历史</Badge>
            </span>
          )}
          <Link
            to={`/history?tab=script&project=${encodeURIComponent(project.id)}`}
            className="rounded-ctl border border-rule px-2 py-1 text-note text-ink-dim transition-colors hover:text-ink"
          >
            历史版本
          </Link>
        </div>
      </header>

      {orphan && (
        <div className="flex flex-wrap items-center gap-3 border-b border-state-fail/40 bg-state-fail/10 px-4 py-2 text-note">
          <TriangleAlert className="h-3.5 w-3.5 flex-none text-state-fail" />
          <span className="min-w-0 flex-1 text-state-fail">
            上一次生成停在「{project.data.taskStep || "未知步骤"}」就没动静了 —— 页面刷过或关过，请求已经断了，这条是残留状态。
          </span>
          <Button size="sm" variant="quiet" onClick={() => void patchData({ isParsingScript: false, taskStep: undefined })}>
            清除残留状态
          </Button>
        </div>
      )}

      {tab === "manifest" && error && (
        <div className="flex items-start gap-2 border-b border-state-fail/40 bg-state-fail/10 px-4 py-2 text-note text-state-fail">
          <TriangleAlert className="mt-[1px] h-3.5 w-3.5 flex-none" />
          <span className="min-w-0 break-words">{error}</span>
        </div>
      )}

      {tab === "create" ? (
        <div className="flex min-h-0 flex-1 flex-col xl:flex-row">
          <div
            style={pane.style}
            className="relative flex-none border-b border-rule-soft xl:h-full xl:w-[var(--pane-w)] xl:border-b-0 xl:border-r"
          >
            <SplitHandle pane={pane} side="left" label="配置栏宽度" className="hidden xl:block" />
            <ConfigPanel
              projectName={title}
              outputLanguage={project.config.outputLanguage}
              targetDurationSec={project.config.targetDurationSec}
              visualStyle={project.config.visualStyle}
              promptMode={project.config.h3PromptMode}
              promptReason={project.config.h3PromptReason ?? ""}
              continuity={project.config.continuity}
              continuityOverlapFrames={project.config.continuityOverlapFrames}
              aspectRatio={project.config.aspectRatio}
              llms={llms}
              shotBackendId={project.config.shotModelBackendId}
              shotModel={project.config.shotModel}
              busy={busy}
              error={error}
              hasShots={shotCount > 0}
              scriptChars={draft.length}
              onProjectName={onTitleChange}
              onProjectNameCommit={() => void commitTitle()}
              onOutputLanguage={(v) => void patchConfig({ outputLanguage: v })}
              onTargetDuration={(v) => void patchConfig({ targetDurationSec: v })}
              onVisualStyle={(v) => void patchConfig({ visualStyle: v })}
              onPromptMode={(m) => changePromptMode(m)}
              onPromptReason={(v) => void patchConfig({ h3PromptReason: v })}
              promptModeBusy={applyingMode}
              promptModeNote={modeNote}
              onContinuity={(v) => void patchConfig({ continuity: v })}
              onContinuityOverlap={(n) => void patchConfig({ continuityOverlapFrames: n })}
              onAspectRatio={(v: AspectRatio) => void patchConfig({ aspectRatio: v })}
              onShotModel={(bid, model) => void patchConfig({ shotModelBackendId: bid, shotModel: model })}
              onGenerate={() => void generate()}
            />
          </div>

          <div className="flex min-h-0 flex-1 flex-col">
            <div className="flex items-center gap-2 border-b border-rule-soft px-4 py-1.5">
              {done ? (
                <span className="inline-flex min-w-0 items-center gap-1.5 text-note text-state-ok">
                  <CircleCheck className="h-3.5 w-3.5 flex-none" />
                  <span className="truncate">{done}</span>
                </span>
              ) : (
                <span className="label truncate">
                  {busy
                    ? "生成中：这一步不能并发，等它跑完。GPU 正在出图/出片时后端会回 409，错误原话显示在左栏。"
                    : "改完自动存（停 1 秒写盘）。生成会先把剧本拆成角色/场景并回写项目，再按目标时长出镜头表。"}
                </span>
              )}
            </div>
            <div className="min-h-0 flex-1">
              <ScriptEditor
                value={draft}
                onChange={onDraftChange}
                saveState={saveState}
                busy={busy}
                hasShots={shotCount > 0}
                onGenerate={() => void generate()}
                onAiWrite={(m) => void aiWrite(m)}
              />
            </div>
          </div>
        </div>
      ) : tab === "manifest" ? (
        <Manifest project={project} onPatchData={(patch) => void patchData(patch)} onBackToCreate={() => switchTab("create")} />
      ) : (
        // 版本历史：AI 续写、改写、助手写回、拆解各留一版；正文手改了但还没存版，这里也看得出来
        <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-4">
          {id && <ScriptVersionList projectId={id} />}
          {project.data.scriptVersionUuid === null && project.data.rawScript.trim() && (
            <p className="text-caption leading-snug text-ink-mute">
              编辑器里的正文还没有对应的版本记录 —— 下一次生成会自动补成 V1，不用你手动存。
            </p>
          )}
        </div>
      )}

      {tab === "create" && (
        <ChatDock
          projectId={id!}
          sessions={project.data.scriptChats ?? []}
          script={draft}
          backendId={project.config.llmBackendId ?? project.config.shotModelBackendId ?? undefined}
          blocked={busy ? `分镜脚本正在生成（${busy}），这台模型是单槽串行的，等它跑完再对话。` : null}
          onPersist={(chats) => void patchData({ scriptChats: chats })}
          onWriteBack={(text) => {
            const before = previousText();
            onDraftChange(text);
            // 助手写回是整篇替换，和 AI 续写一样必须留版：不存就等于上一版当场消失，
            // 而这条路径本来就是「模型只回被改的那一段」最容易丢字的地方
            void saveVersion({ text, source: "ai-write", previous: before });
            setDone(`已由对话助手写回，共 ${text.length} 字。拍摄清单里的镜头没动 —— 要按新稿子重出镜头表，点「生成分镜脚本」。`);
          }}
        />
      )}

      <Modal
        open={!!modePlan}
        onClose={() => setModePlan(null)}
        width={560}
        title={modePlan ? `切到「${modePlan.modeName}」` : ""}
        footer={
          modePlan && (
            <>
              <span className="mr-auto text-caption text-ink-mute">本地模板重拼，不叫模型、不占 GPU</span>
              <Button variant="ghost" onClick={() => setModePlan(null)}>
                取消
              </Button>
              {modePlan.aiKept > 0 && (
                <Button variant="default" disabled={applyingMode} onClick={() => void applyPromptMode(true)} title="连 AI 重写过的镜头一起用本地模板重拼">
                  全部重拼
                </Button>
              )}
              <Button variant="primary" loading={applyingMode} disabled={modePlan.toRebuild === 0} onClick={() => void applyPromptMode(false)}>
                {modePlan.toRebuild === 0 ? "没有要重拼的镜头" : `重拼 ${modePlan.toRebuild} 镜`}
              </Button>
            </>
          )
        }
      >
        {modePlan && (
          <ul className="space-y-1.5 text-note leading-snug text-ink-dim">
            <li>
              项目里 <span className="mono text-ink">{modePlan.total}</span> 个镜头：
              {modePlan.toRebuild} 镜会按「{modePlan.modeName}」用本地模板重拼结构化提示词。
            </li>
            {modePlan.already > 0 && <li>{modePlan.already} 镜本来就是这一档，原样不动。</li>}
            {modePlan.aiKept > 0 && (
              <li className="text-mach-rh">
                {modePlan.aiKept} 镜是模型重写过的导演级稿，本地模板会把它们压回一句话 —— 默认保留。要一起换就点「全部重拼」，或去导演台逐镜让模型按新模式重写。
              </li>
            )}
            {modePlan.clearingVideoPrompt > 0 && (
              <li className="text-state-fail">
                {modePlan.clearingVideoPrompt} 镜手写过「提交文本」，重拼时会清空（不清空就还在发旧模式的措辞，等于白切）。清空后提交改由结构化字段现拼。
              </li>
            )}
            <li className="text-ink-mute">重拼出的模板稿深度有限；六段式 / 导演分镜块要真正的内容，仍要去导演台让模型按新模式重写。</li>
          </ul>
        )}
      </Modal>
    </div>
  );
}
