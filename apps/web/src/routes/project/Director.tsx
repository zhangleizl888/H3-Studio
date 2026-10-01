import { useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { CircleCheck, Clapperboard, Images, Loader2, Video } from "lucide-react";
import { Badge, Button, Empty, Modal, Panel, Progress, Skeleton, StateGlyph, StateLabel, Toggle, type StateKey } from "../../components/ui";
import { useApi } from "../../lib/apiClient";
import { keys, useGpuState, useInstances, useMedia, useProject, useProjectMutations } from "../../lib/hooks";
import { keyframeRequest, renderProgress, videoRequest } from "../../lib/generate";
import type { GenTarget } from "../../lib/generate";
import type { GenerateRequest, JobPlanResult } from "../../lib/api";
import { useGenerator } from "../../lib/useGenerate";
import { flushSaves } from "../../lib/localStores";
import type { JobState, LlmShot, Media, Project, Shot } from "../../lib/types";
import { cn } from "../../lib/utils";
import { aspectOf, cardLabel, frameMediaId, machVar, useMediaSrc, type DirectorCtx } from "./director/common";
import { PlanTable } from "./director/PlanTable";
import { ShotCard } from "./director/ShotCard";
import { ShotDrawer } from "./director/ShotDrawer";
import { Timeline } from "./director/Timeline";
import { buildSubShots, replaceShot } from "./director/splitShot";

/**
 * AI工作台 / DIRECTOR WORKBENCH —— 镜头网格 + 右侧镜头详情抽屉。
 *
 * 状态归属：文本类编辑走「本地草稿 → 失焦写回」，其余（场景、角色、运镜、时长）即时写回。
 * 所有生成只经 useGenerator 一条路：提交、轮询、回写、接回刷新前的任务都在那里，
 * 这个页面只负责把 handles 里的进度显示出来，不再自己开第二个轮询。
 */
export default function Director() {
  const { id } = useParams();
  const api = useApi();
  const qc = useQueryClient();
  const { data: project } = useProject(id);
  const { data: media } = useMedia(id);
  const { data: instances } = useInstances();
  const { data: gpu } = useGpuState();
  const mut = useProjectMutations(id);
  const gen = useGenerator(id);

  const [activeId, setActiveId] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<null | "frames" | "videos">(null);
  const [planItems, setPlanItems] = useState<{ target: GenTarget; req: GenerateRequest }[]>([]);
  const [plan, setPlan] = useState<JobPlanResult | null>(null);
  const [planBusy, setPlanBusy] = useState(false);
  const [planError, setPlanError] = useState<string | null>(null);
  const [preview, setPreview] = useState<{ mediaId: string; title: string } | null>(null);
  const [playing, setPlaying] = useState<{ mediaId: string; title: string } | null>(null);
  const [toast, setToast] = useState<{ msg: string; tone: "ok" | "bad" } | null>(null);

  /* ── 写回：pending 是「已经写进 IDB、但还没回到查询缓存」的那一份镜头 ──
     没有它，输入框在 blur 之后、refetch 之前会被旧值盖回去；生成结果落地时又要立刻显示。 */
  const [pending, setPending] = useState<Record<string, Shot>>({});
  const lastServer = useRef<Project | undefined>(project);
  const liveRef = useRef<Shot[]>(project?.data.shots ?? []);
  if (lastServer.current !== project) {
    lastServer.current = project;
    const server = project?.data.shots ?? [];
    const byId = new Map(server.map((s) => [s.id, s] as const));
    const rest: Record<string, Shot> = {};
    for (const [sid, s] of Object.entries(pending)) {
      const cur = byId.get(sid);
      if (!cur || JSON.stringify(cur) !== JSON.stringify(s)) rest[sid] = s;
    }
    if (Object.keys(rest).length !== Object.keys(pending).length || Object.keys(rest).some((k) => pending[k] !== rest[k])) setPending(rest);
    liveRef.current = server.map((s) => rest[s.id] ?? s);
  }
  const shots = liveRef.current;

  const live = project ? ({ ...project, data: { ...project.data, shots } } as Project) : undefined;
  const mediaById = new Map((media ?? []).map((m) => [m.id, m] as const));

  const activeIndex = shots.findIndex((s) => s.id === activeId);
  const active = activeIndex >= 0 ? shots[activeIndex] : null;

  useEffect(() => {
    if (!toast) return;
    const t = window.setTimeout(() => setToast(null), 6000);
    return () => window.clearTimeout(t);
  }, [toast]);

  /* 打开页面时把刷新前已提交、还在跑的任务接回来。
     用 ref 而不是 state 做幂等：StrictMode 会把 effect 挂两次，两次都进 reconcile
     就会给同一个 job 起两个轮询，前一个 interval 再也清不掉。 */
  const reconciledRef = useRef<string | null>(null);
  useEffect(() => {
    if (!project || reconciledRef.current === project.id) return;
    reconciledRef.current = project.id;
    void gen.reconcileFromServer(project);
  }, [project, gen.reconcileFromServer]);

  if (!project || !live) {
    return (
      <div className="space-y-3 p-4">
        <Skeleton className="h-9 w-64" />
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-56 w-full" />
          ))}
        </div>
      </div>
    );
  }

  /* 过了上面的守卫，project/live 一定有值；起两个非可选别名给闭包里用 */
  const prj = project;
  const model = live;
  const progress = renderProgress(model);

  function writeShots(next: Shot[]) {
    liveRef.current = next;
    setPending(Object.fromEntries(next.map((s) => [s.id, s] as const)));
    mut.data.mutate({ shots: next });
  }

  function patchShot(shotId: string, patch: Partial<Shot>) {
    writeShots(liveRef.current.map((s) => (s.id === shotId ? { ...s, ...patch } : s)));
  }

  const ctx: DirectorCtx = {
    project: live,
    shots,
    mediaById,
    aspect: aspectOf(live),
    handles: gen.handles,
    api,
    run: gen.run,
    patchShot,
    writeShots,
    setConfig: (patch) => mut.config.mutate(patch),
    refetchMedia: () => void qc.invalidateQueries({ queryKey: keys.media(project.id) }),
    onPreview: (mediaId, title) => setPreview({ mediaId, title }),
    onPlay: (mediaId, title) => setPlaying({ mediaId, title }),
    notify: (msg, tone = "ok") => setToast({ msg, tone }),
    machColor: (instanceId) => {
      const inst = instances?.find((i) => i.id === instanceId);
      return inst ? machVar(inst.placement) : undefined;
    },
  };

  /** 批量派发的候选：一镜一条，帧只给首帧，片只给已有起始帧的镜头 */
  function buildItems(kind: "frames" | "videos") {
    const list = kind === "frames" ? shots : shots.filter((s) => !!frameMediaId(s, "start"));
    return list.map((s) =>
      kind === "frames"
        ? { target: { kind: "keyframe", shotId: s.id, frameType: "start" } satisfies GenTarget, req: keyframeRequest(model, s, "start") }
        : { target: { kind: "video", shotId: s.id } satisfies GenTarget, req: videoRequest(model, s) },
    );
  }

  async function askPlan(items: { target: GenTarget; req: GenerateRequest }[]) {
    setPlanBusy(true);
    try {
      const res = await api.jobs.plan(items.map((i) => i.req));
      setPlan(res);
      setPlanError(null);
    } catch (e) {
      // 表拉不到只降级，不锁死派发：后端 /jobs/batch 里还有同一套准入校验兜底
      setPlanError(e instanceof Error ? e.message : String(e));
    } finally {
      setPlanBusy(false);
    }
  }

  /** 打开确认表：先把这批请求摊给后端判一遍，再让人决定发不发 */
  async function openPlan(kind: "frames" | "videos") {
    const items = buildItems(kind);
    if (!items.length) {
      ctx.notify(kind === "frames" ? "没有镜头可生成" : "没有一镜有起始帧，先出首帧再出片", "bad");
      return;
    }
    setConfirm(kind);
    setPlanItems(items);
    await askPlan(items);
  }

  /** 就地改一个数值 → 整批重新问一次后端（行序不变，改哪条都行） */
  function patchRow(index: number, slot: string, value: unknown) {
    const next = planItems.map((it, i) => (i === index ? { ...it, req: { ...it.req, slots: { ...it.req.slots, [slot]: value } } } : it));
    setPlanItems(next);
    void askPlan(next);
  }

  /** 确认派发：用的就是这张表校验过的那批请求，改过的数值已经在里面 */
  async function dispatch() {
    if (!confirm) return;
    const kind = confirm;
    const items = planItems;
    const skipped = kind === "videos" ? shots.length - items.length : 0;
    setConfirm(null);
    await flushSaves();
    const res = await gen.runBatch(items);
    ctx.notify(
      `${kind === "frames" ? "首帧" : "成片"}任务已入队 ${res.accepted} 条${res.rejected ? `，${res.rejected} 条被拒` : ""}${skipped ? `；${skipped} 镜没有起始帧，已跳过` : ""}`,
      res.rejected ? "bad" : "ok",
    );
  }

  /** AI 拆分镜头：storyboard 用途出子镜动作，替换父镜 */
  async function splitShot(shot: Shot) {
    const scene = prj.data.scenes.find((s) => s.id === shot.sceneId);
    const names = prj.data.characters.filter((c) => shot.characterIds.includes(c.id)).map((c) => c.name);
    const brief = JSON.stringify({
      任务: "把下面这一个镜头拆成 2-3 个连续子镜，每个子镜给 action / dialogue / cameraMovement / shotSize",
      场景: scene ? `${scene.location || scene.name}${scene.time ? `·${scene.time}` : ""}` : "未指定",
      出场角色: names,
      原动作: shot.action,
      原台词: shot.dialogue ?? "",
      原运镜: shot.cameraMovement,
      总时长秒: shot.durationSec,
    });
    try {
      const res = await api.llm.run("storyboard", brief, {
        targetSec: shot.durationSec,
        pace: "均匀",
        backendId: prj.config.shotModelBackendId ?? prj.config.llmBackendId ?? undefined,
      });
      const list = (res.data as { shots?: LlmShot[] }).shots ?? [];
      const subs = buildSubShots(shot, list, model);
      writeShots(replaceShot(shots, shot.id, subs));
      setActiveId(subs[0].id);
      ctx.notify(`已拆成 ${subs.length} 个子镜，提示词按子镜重算`, "ok");
    } catch (e) {
      ctx.notify(`拆分失败：${e instanceof Error ? e.message : String(e)}`, "bad");
    }
  }

  const handleEntries = Object.entries(gen.handles);
  const startFrames = shots.filter((s) => !!frameMediaId(s, "start")).length;
  const saving = mut.data.isPending || mut.config.isPending;
  const dirty = Object.keys(pending).length > 0;

  return (
    <div className="flex h-full min-h-0 flex-col">
      {/* 顶部工具条 */}
      <header className="flex flex-none flex-wrap items-center gap-x-3 gap-y-2 border-b border-hairline bg-panel/70 px-4 py-2 backdrop-blur-xl">
        <h1 className="flex items-center gap-2 text-title font-semibold tracking-tight">
          <Clapperboard className="h-4 w-4 text-chrome" />
          AI工作台
        </h1>
        <span className="label-mono rounded-full border border-hairline bg-sheen px-2 py-1">Director Workbench</span>

        <div className="ml-auto flex flex-wrap items-center gap-2.5">
          {gpu?.renderingLocally && (
            <Badge tone="warn">
              <span className="mono mr-1">本机</span>GPU 正被渲染占用
            </Badge>
          )}
          <Toggle
            checked={project.config.enhancePrompts}
            onChange={(v) => mut.config.mutate({ enhancePrompts: v })}
            label={<span className="text-note">AI增强提示词</span>}
          />
          <span className="mono text-note text-ink-mute">
            <span className="text-ink">{progress.done}</span> / {progress.total} 完成
          </span>
          <Button size="sm" icon={<Images className="h-3.5 w-3.5" />} disabled={handleEntries.length > 0 || !shots.length} onClick={() => void openPlan("frames")}>
            {startFrames === shots.length && startFrames > 0 ? "重新生成所有首帧" : "生成所有首帧"}
          </Button>
          <Button size="sm" icon={<Video className="h-3.5 w-3.5" />} disabled={handleEntries.length > 0 || !shots.length} onClick={() => void openPlan("videos")}>
            {progress.done > 0 ? "重新生成所有视频" : "生成所有视频"}
          </Button>
          <span className="label inline-flex items-center gap-1">
            {saving || dirty ? (
              <>
                <Loader2 className="h-3 w-3 animate-spin" /> 保存中
              </>
            ) : mut.data.isError || mut.config.isError ? (
              <span className="text-state-fail">保存失败</span>
            ) : (
              <>
                <CircleCheck className="h-3 w-3 text-state-ok" /> 已保存
              </>
            )}
          </span>
        </div>
      </header>

      {toast && (
        <div
          className={cn(
            "flex flex-none items-center gap-2 border-b px-4 py-1.5 text-note",
            toast.tone === "ok" ? "border-state-ok/35 bg-state-ok/8 text-ink-dim" : "border-state-fail/40 bg-state-fail/10 text-state-fail",
          )}
          role="status"
        >
          <StateGlyph state={toast.tone === "ok" ? "succeeded" : "failed"} />
          {toast.msg}
        </div>
      )}

      <div className="flex min-h-0 flex-1">
        {/* 镜头网格 + 时间轴 */}
        <div className="min-h-0 flex-1 overflow-y-auto p-4">
          {shots.length === 0 ? (
            <Empty
              title="这个项目还没有镜头"
              hint="去「剧本创作」页点分镜规划，把剧本拆成镜头表，这里就会出现一格一格的镜头卡。"
            />
          ) : (
            <div className={cn("grid gap-3", active ? "grid-cols-1 md:grid-cols-2" : "grid-cols-2 lg:grid-cols-3 2xl:grid-cols-4")}>
              {shots.map((s) => (
                <ShotCard key={s.id} ctx={ctx} shot={s} active={s.id === activeId} onClick={() => setActiveId(s.id === activeId ? null : s.id)} />
              ))}
            </div>
          )}

          {handleEntries.length > 0 && (
            <Panel
              title={
                <span className="flex items-center gap-1.5">
                  进行中的任务 <Badge>{handleEntries.length}</Badge>
                </span>
              }
              className="mt-4"
            >
              <ul className="space-y-2">
                {handleEntries.map(([key, h]) => {
                  const [kind, ref] = key.split(":");
                  const shot = shots.find((s) => s.id === ref);
                  const inst = instances?.find((i) => i.id === (shot?.instanceId ?? (kind === "video" ? project.config.videoInstanceId : project.config.imageInstanceId)));
                  return (
                    <li key={key} className="space-y-1">
                      <div className="flex items-baseline justify-between gap-2 text-note">
                        <span className="truncate">
                          <span className="mono text-ink-dim">{shot ? `镜 ${cardLabel(shot)}` : ref}</span>
                          <span className="text-ink-mute"> · {kind === "video" ? "出片" : kind === "keyframe" ? "关键帧" : kind}</span>
                        </span>
                        <StateLabel state={asStateKey(h.state)} />
                      </div>
                      <Progress
                        value={h.progress === null ? undefined : Math.round(h.progress * 100)}
                        max={h.progress === null ? undefined : 100}
                        unavailable={h.progress === null}
                        stage={h.stage ?? h.error ?? null}
                        machine={inst ? machVar(inst.placement) : undefined}
                        stripe={h.state === "running" || h.state === "queued"}
                      />
                    </li>
                  );
                })}
              </ul>
            </Panel>
          )}

          {shots.length > 0 && <div className="mt-4"><Timeline ctx={ctx} onPlay={(m, t) => setPlaying({ mediaId: m, title: t })} /></div>}
        </div>

        {active && (
          <ShotDrawer
            ctx={ctx}
            shot={active}
            index={activeIndex}
            onClose={() => setActiveId(null)}
            onPrev={() => setActiveId(shots[Math.max(0, activeIndex - 1)].id)}
            onNext={() => setActiveId(shots[Math.min(shots.length - 1, activeIndex + 1)].id)}
            onSplit={splitShot}
          />
        )}
      </div>

      {/* 批量派发前的参数确认表：一条成片要烧十几分钟，参数必须先看清楚 */}
      <Modal open={confirm !== null} onClose={() => setConfirm(null)} title={`${confirm === "frames" ? "首帧" : "成片"}参数确认 · ${planItems.length} 条`} width={980}>
        <div className="space-y-2.5">
          <p className="text-[11.5px] leading-snug text-ink-mute">
            派发会覆盖这些镜头已有的{confirm === "frames" ? "首帧" : "成片"}。数值可以直接在下表改，改完自动重新问一次后端；
            被拦下的条目不进队列（后端 /jobs/batch 用的是同一套判断，不是这里另写一份）。
            {project.config.resolutionMode === "full" && (
              <>
                {" "}
                <Badge tone="warn">当前是全质量档，耗时与显存都会明显上升，本机 1344×768 没有成功记录</Badge>
              </>
            )}
          </p>
          <PlanTable
            plan={plan}
            busy={planBusy}
            error={planError}
            onPatch={patchRow}
            onRevalidate={() => void askPlan(planItems)}
            onConfirm={() => void dispatch()}
            onCancel={() => setConfirm(null)}
          />
        </div>
      </Modal>

      <Modal open={!!preview} onClose={() => setPreview(null)} title={preview?.title ?? "预览"} width={860}>
        <ImagePreview media={preview ? mediaById.get(preview.mediaId) : undefined} />
      </Modal>

      <Modal open={!!playing} onClose={() => setPlaying(null)} title={playing?.title ?? "播放成片"} width={900}>
        <VideoPreview media={playing ? mediaById.get(playing.mediaId) : undefined} />
      </Modal>
    </div>
  );
}

/* ───────── 队列状态与预览 ───────── */

/** Job 的 dispatching 在 StateKey 里归到 running */
function asStateKey(s: JobState): StateKey {
  return s === "dispatching" ? "running" : s;
}

function ImagePreview({ media }: { media: Media | undefined }) {
  const src = useMediaSrc(media);
  if (!media) return <p className="py-8 text-center text-note text-ink-mute">这条记录在本地索引里找不到，可能项目是在别的机器上出的片。</p>;
  if (!src) return <p className="py-8 text-center text-note text-ink-mute">这个文件还读不回来：检查产出它的实例是否还在，或后端是否起着。</p>;
  return <img src={src} alt="关键帧预览" className="mx-auto max-h-[62vh] w-auto rounded-ctl border border-rule-soft" />;
}

function VideoPreview({ media }: { media: Media | undefined }) {
  const src = useMediaSrc(media);
  if (!media) return <p className="py-8 text-center text-note text-ink-mute">这段成片在本地索引里找不到记录。</p>;
  if (!src) return <p className="py-8 text-center text-note text-ink-mute">这个文件还读不回来：产物可能已被清理，或后端没起着。</p>;
  return <video src={src} controls autoPlay playsInline className="max-h-[62vh] w-full rounded-ctl border border-rule-soft bg-void" />;
}
