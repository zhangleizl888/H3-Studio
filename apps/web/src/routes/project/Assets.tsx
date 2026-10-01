/**
 * 场景角色（ASSETS & CASTING）—— 两个子视图：角色定妆 / 场景概念。
 *
 * 数据契约：项目实体在浏览器（IndexedDB），产物在服务端媒体库。
 * 所以这里所有写操作都先 api.projects.get 读一次活数据再改，避免和后台轮询的
 * 回写互相覆盖 —— 生成完成时 useGenerator 会把 media id 挂到 refMediaIds 上，
 * 我们这边如果拿渲染时的旧数组整体覆盖，那张刚出的图就没了。
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { Archive, Images, Monitor, Plus, RefreshCw, Smartphone, Sparkles, Wand2 } from "lucide-react";
import { Badge, Button, Empty, Select, Skeleton, Spinner, Tabs } from "../../components/ui";
import { VersionGroup } from "../../components/VersionHistory";
import { useApi } from "../../lib/apiClient";
import { keys, useGpuState, useInstances, useMedia, useProject, useProjectMutations, useWorkflows } from "../../lib/hooks";
import { characterRequest, sceneRequest, variationRequest, type GenTarget } from "../../lib/generate";
import { genKey, useGenerator } from "../../lib/useGenerate";
import { IMAGE_SIZES } from "../../lib/prompts";
import { importAssetIntoProject, saveCharacterAsset, saveSceneAsset } from "../../lib/localStores";
import type { AssetLibraryItem, Character, Media, Project, Scene } from "../../lib/types";
import type { GenerateRequest } from "../../lib/api";
import { cn, isStill, uid } from "../../lib/utils";
import { CharacterCard } from "./assets/CharacterCard";
import { SceneCard } from "./assets/SceneCard";
import { WardrobeModal } from "./assets/WardrobeModal";
import { AssetLibraryModal, type LibraryFilter } from "./assets/AssetLibraryModal";
import { NewCharacterSheet, NewSceneSheet } from "./assets/NewForms";
import { ConfirmSheet, SectionDot, Sheet, useMediaSrc, type ConfirmRequest } from "./assets/common";

type View = "casting" | "locations";
type Notice = { tone: "good" | "bad" | "info"; text: string };

const msg = (e: unknown) => (e instanceof Error ? e.message : String(e));

/** 后端把内置模板的 id 发成 "builtin:<key>"，而 config.imageTemplate 要的是裸 key */
const templateKey = (w: { id: string }) => (w.id.startsWith("builtin:") ? w.id.slice("builtin:".length) : w.id);

export default function Assets() {
  const { id } = useParams();
  const projectId = id;
  const api = useApi();
  const qc = useQueryClient();
  const { data: project } = useProject(projectId);
  const { data: media } = useMedia(projectId);
  const { data: workflows } = useWorkflows();
  const { data: instances } = useInstances();
  const { data: gpu } = useGpuState();
  const dataMut = useProjectMutations(projectId);
  const gen = useGenerator(projectId);

  const [view, setView] = useState<View>("casting");
  const [wardrobeId, setWardrobeId] = useState<string | null>(null);
  const [library, setLibrary] = useState<{ filter: LibraryFilter; replaceId: string | null } | null>(null);
  const [creating, setCreating] = useState<"character" | "scene" | null>(null);
  const [confirm, setConfirm] = useState<ConfirmRequest | null>(null);
  const [preview, setPreview] = useState<{ media: Media; title: string } | null>(null);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [batch, setBatch] = useState<{ label: string; keys: string[] } | null>(null);

  const characters = project?.data.characters ?? [];
  const scenes = project?.data.scenes ?? [];
  const handles = gen.handles;
  const mediaById = useMemo(() => new Map((media ?? []).map((m) => [m.id, m] as const)), [media]);
  const imageWorkflows = useMemo(() => (workflows ?? []).filter((w) => w.family === "image"), [workflows]);

  const noticeTimer = useRef<number | undefined>(undefined);
  useEffect(() => () => window.clearTimeout(noticeTimer.current), []);
  function flash(tone: Notice["tone"], text: string) {
    setNotice({ tone, text });
    window.clearTimeout(noticeTimer.current);
    noticeTimer.current = window.setTimeout(() => setNotice(null), 7000);
  }

  /* ───────── 打开项目时收口：接回任务 + 把假 generating 判失败 ───────── */

  const handlesRef = useRef(handles);
  useEffect(() => {
    handlesRef.current = handles;
  }, [handles]);

  const settledFor = useRef<string | null>(null);
  useEffect(() => {
    if (!project || settledFor.current === project.id) return;
    settledFor.current = project.id;
    let alive = true;
    void (async () => {
      await gen.reconcileFromServer(project).catch(() => undefined);
      if (!alive) return;
      const live = handlesRef.current;
      const stuckChar = project.data.characters.filter(
        (c) =>
          !live[genKey({ kind: "character", characterId: c.id })] &&
          ((c.status === "generating" && !c.refMediaIds.length) || c.variations.some((v) => v.status === "generating" && !v.refMediaIds.length && !live[genKey({ kind: "variation", characterId: c.id, variationId: v.id })])),
      );
      const stuckScene = project.data.scenes.filter((s) => s.status === "generating" && !s.refMediaIds.length && !live[genKey({ kind: "scene", sceneId: s.id })]);
      if (!stuckChar.length && !stuckScene.length) return;
      await edit((p) => {
        for (const c of p.data.characters) {
          if (!stuckChar.some((x) => x.id === c.id)) continue;
          if (c.status === "generating" && !c.refMediaIds.length) c.status = "failed";
          c.variations = c.variations.map((v) => (v.status === "generating" && !v.refMediaIds.length ? { ...v, status: "failed" as const } : v));
        }
        for (const s of p.data.scenes) if (stuckScene.some((x) => x.id === s.id)) s.status = "failed";
      }, ["characters", "scenes"]);
    })();
    return () => {
      alive = false;
    };
    // 只在换项目时跑一次；edit 读的是活数据，快照依赖不需要进数组
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [project?.id]);

  /* ───────── 写数据 ───────── */

  async function edit(mutate: (p: Project) => void, pick: Array<"characters" | "scenes" | "shots"> = ["characters", "scenes"]): Promise<boolean> {
    if (!projectId) return false;
    try {
      const live = await api.projects.get(projectId);
      mutate(live);
      const patch: Partial<Project["data"]> = {};
      if (pick.includes("characters")) patch.characters = live.data.characters;
      if (pick.includes("scenes")) patch.scenes = live.data.scenes;
      if (pick.includes("shots")) patch.shots = live.data.shots;
      await dataMut.data.mutateAsync(patch);
      return true;
    } catch (e) {
      flash("bad", `保存失败：${msg(e)}`);
      return false;
    }
  }

  /* ───────── 生成 ───────── */

  async function generate(target: GenTarget, req: GenerateRequest, label: string) {
    try {
      const res = await gen.run(target, req);
      if (res.error) flash("bad", `${label}：${res.error}`);
    } catch (e) {
      flash("bad", `${label}派发失败：${msg(e)}`);
    }
  }

  const genCharacter = (c: Character) => project && generate({ kind: "character", characterId: c.id }, characterRequest(project, c), `定妆 · ${c.name}`);
  const genScene = (s: Scene) => project && generate({ kind: "scene", sceneId: s.id }, sceneRequest(project, s), `场景 · ${s.name}`);

  function genVariation(c: Character, variationId: string) {
    if (!project) return;
    const v = c.variations.find((x) => x.id === variationId);
    if (!v) return;
    void generate({ kind: "variation", characterId: c.id, variationId: v.id }, variationRequest(project, c, v), `变体 · ${c.name} / ${v.name}`);
  }

  const batchDone = useMemo(() => {
    if (!batch) return 0;
    return batch.keys.filter((k) => {
      const h = handles[k];
      return !h || h.state === "succeeded" || h.state === "failed" || h.state === "canceled";
    }).length;
  }, [batch, handles]);

  useEffect(() => {
    if (!batch || batchDone < batch.keys.length) return;
    setBatch(null);
    flash("info", `${batch.label}已全部派发完毕，产物逐张回写`);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [batch, batchDone]);

  async function batchGenerate(kind: "character" | "scene") {
    if (!project) return;
    const list = kind === "character" ? project.data.characters : project.data.scenes;
    const what = kind === "character" ? "角色" : "场景";
    if (!list.length) {
      flash("info", `还没有${what}：先点「新建${what}」，或去剧本页解析剧本`);
      return;
    }
    const missing = list.filter((x) => !x.refMediaIds.length);
    const targets = missing.length ? missing : list;

    const run = async () => {
      const items = targets.map((x) =>
        kind === "character"
          ? { target: { kind: "character" as const, characterId: x.id }, req: characterRequest(project, x as Character) }
          : { target: { kind: "scene" as const, sceneId: x.id }, req: sceneRequest(project, x as Scene) },
      );
      setBatch({ label: `${what}参考图`, keys: items.map((i) => genKey(i.target)) });
      try {
        const res = await gen.runBatch(items);
        if (!res.accepted) {
          setBatch(null);
          flash("bad", `批量派发失败：${res.rejected} 条被后端拒绝，去任务队列看原因`);
        } else if (res.rejected) {
          flash("info", `${res.accepted} 条已入队，${res.rejected} 条被拒`);
        }
      } catch (e) {
        setBatch(null);
        flash("bad", `批量派发失败：${msg(e)}`);
      }
    };

    if (!missing.length) {
      setConfirm({
        title: `重新生成所有${what}`,
        confirmLabel: `重出 ${list.length} 张`,
        body: `${list.length} 个${what}都已有参考图。重新生成会覆盖卡片上显示的那张（历史图仍留在媒体库），确定继续？`,
        onConfirm: () => void run(),
      });
      return;
    }
    await run();
  }

  /* ───────── 上传 ───────── */

  async function upload(file: File, role: Media["role"], refId: string, apply: (p: Project, mediaId: string) => void, label: string) {
    if (!projectId) return;
    try {
      const m = await api.media.put(file, role, refId, projectId);
      await qc.invalidateQueries({ queryKey: keys.media(projectId) });
      await edit((p) => apply(p, m.id), ["characters", "scenes"]);
      flash("good", `${label}：已把 ${file.name} 设为参考图`);
    } catch (e) {
      flash("bad", `上传失败：${msg(e)}`);
    }
  }

  const attachCharacter = (id: string) => (p: Project, mediaId: string) => {
    const c = p.data.characters.find((x) => x.id === id);
    if (!c) return;
    c.refMediaIds = [mediaId, ...c.refMediaIds.filter((x) => x !== mediaId)];
    c.status = "completed";
  };
  const attachScene = (id: string) => (p: Project, mediaId: string) => {
    const s = p.data.scenes.find((x) => x.id === id);
    if (!s) return;
    s.refMediaIds = [mediaId, ...s.refMediaIds.filter((x) => x !== mediaId)];
    s.status = "completed";
  };
  const attachVariation = (charId: string, variationId: string) => (p: Project, mediaId: string) => {
    const v = p.data.characters.find((x) => x.id === charId)?.variations.find((x) => x.id === variationId);
    if (!v) return;
    v.refMediaIds = [mediaId, ...v.refMediaIds.filter((x) => x !== mediaId)];
    v.status = "completed";
  };

  /* ───────── 资产库 ───────── */

  async function addToLibrary(kind: "character" | "scene", entity: Character | Scene) {
    if (!project) return;
    const name = entity.name || "未命名";
    const what = kind === "character" ? "角色" : "场景";
    const save = async () => {
      try {
        const item = kind === "character" ? await saveCharacterAsset(project, entity as Character) : await saveSceneAsset(project, entity as Scene);
        flash("good", `已加入资产库：${item.name}`);
      } catch (e) {
        flash("bad", `加入资产库失败：${msg(e)}`);
      }
    };
    if (!entity.refMediaIds.length) {
      setConfirm({
        title: `该${what}还没有参考图`,
        confirmLabel: "仍然加入",
        body: `「${name}」还没有参考图，加入资产库后导入到别的项目也只有定义没有图。仍要加入？`,
        onConfirm: () => void save(),
      });
      return;
    }
    await save();
  }

  async function importAsset(item: AssetLibraryItem) {
    if (!projectId) return;
    try {
      await importAssetIntoProject(projectId, item.id);
      await qc.invalidateQueries({ queryKey: keys.project(projectId) });
      await qc.invalidateQueries({ queryKey: keys.projects });
      flash("good", `已导入：${item.name}`);
      setLibrary(null);
    } catch (e) {
      flash("bad", `导入失败：${msg(e)}`);
    }
  }

  async function replaceCharacter(targetId: string, item: AssetLibraryItem) {
    if (item.type !== "character" || !item.character) {
      flash("info", "替换角色请选择角色类资产");
      return;
    }
    const src = item.character;
    const ok = await edit((p) => {
      const at = p.data.characters.findIndex((x) => x.id === targetId);
      if (at < 0) return;
      const cloned: Character = structuredClone(src);
      // 保持 id 不变：镜头的出场名单与 variationByChar 都按 id 引用，换 id 就等于把所有分镜打散
      cloned.id = targetId;
      cloned.variations = (src.variations ?? []).map((v) => ({ ...structuredClone(v), id: uid("v") }));
      p.data.characters[at] = cloned;
      for (const s of p.data.shots) {
        if (!s.variationByChar?.[targetId]) continue;
        const next = { ...s.variationByChar };
        delete next[targetId];
        s.variationByChar = Object.keys(next).length ? next : undefined;
      }
    }, ["characters", "shots"]);
    if (ok) {
      flash("good", `已替换角色，改用「${src.name}」`);
      setLibrary(null);
    }
  }

  /* ───────── 删除 ───────── */

  function askDeleteCharacter(c: Character) {
    setConfirm({
      title: `删除角色「${c.name || "未命名"}」`,
      confirmLabel: "删除角色",
      danger: true,
      body: (
        <>
          <div>这会影响所有使用该角色的分镜：{c.name || "该角色"}会从出场名单里摘掉，选中的服装变体也会失效。</div>
          <div className="text-ink-mute">已生成的定妆照仍留在媒体库里，可在资产管理里清。</div>
        </>
      ),
      onConfirm: () =>
        void edit(
          (p) => {
            p.data.characters = p.data.characters.filter((x) => x.id !== c.id);
            for (const s of p.data.shots) {
              s.characterIds = s.characterIds.filter((x) => x !== c.id);
              if (s.variationByChar?.[c.id]) {
                const next = { ...s.variationByChar };
                delete next[c.id];
                s.variationByChar = Object.keys(next).length ? next : undefined;
              }
            }
          },
          ["characters", "shots"],
        ).then((ok) => ok && flash("good", `已删除角色「${c.name}」`)),
    });
  }

  function askDeleteScene(s: Scene) {
    setConfirm({
      title: `删除场景「${s.name || "未命名"}」`,
      confirmLabel: "删除场景",
      danger: true,
      body: (
        <>
          <div>这会影响所有使用该场景的分镜：引用它的镜头会退回「未分配场景」，首尾帧提示词里已经写进去的环境描述不会自动改。</div>
          <div className="text-ink-mute">已生成的场景图仍留在媒体库里。</div>
        </>
      ),
      onConfirm: () =>
        void edit(
          (p) => {
            p.data.scenes = p.data.scenes.filter((x) => x.id !== s.id);
            for (const shot of p.data.shots) if (shot.sceneId === s.id) shot.sceneId = null;
          },
          ["scenes", "shots"],
        ).then((ok) => ok && flash("good", `已删除场景「${s.name}」`)),
    });
  }

  /* ───────── 渲染 ───────── */

  if (!project) {
    return (
      <div className="space-y-3 p-4">
        <Skeleton className="h-12 w-full" />
        <div className="grid gap-4 md:grid-cols-2 2xl:grid-cols-3">
          {[0, 1, 2, 3].map((i) => (
            <Skeleton key={i} className="h-80 w-full" />
          ))}
        </div>
      </div>
    );
  }

  const aspect = project.config.aspectRatio;
  const size = IMAGE_SIZES[aspect] ?? IMAGE_SIZES["16:9"];
  const allCharsReady = characters.length > 0 && characters.every((c) => c.refMediaIds.length > 0);
  const allScenesReady = scenes.length > 0 && scenes.every((s) => s.refMediaIds.length > 0);
  const wardrobeChar = characters.find((c) => c.id === wardrobeId);
  const templateOptions = imageWorkflows.filter((w) => w.isBuiltin);
  const libraryOptions = imageWorkflows.filter((w) => !w.isBuiltin);
  const currentTemplate = project.config.imageTemplate;
  const isLibraryTemplate = libraryOptions.some((w) => templateKey(w) === currentTemplate);

  return (
    <div className="flex min-h-full flex-col">
      {/* ① 工具条 */}
      <header className="sticky top-0 z-30 border-b border-hairline bg-slate/80 backdrop-blur-xl">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2 px-4 py-2.5">
          <span
            className="grid h-9 w-9 flex-none place-items-center rounded-tile bg-gradient-to-br from-chrome to-chrome-2 text-chrome-ink"
            style={{ boxShadow: "0 0 18px color-mix(in srgb, var(--color-chrome) 22%, transparent)" }}
          >
            <Images className="h-[18px] w-[18px]" />
          </span>
          <span className="leading-tight">
            <span className="block text-subtitle font-semibold tracking-tight">场景角色</span>
            <span className="label-mono block">Assets &amp; Casting</span>
          </span>

          <div className="ml-auto flex flex-wrap items-center gap-2">
            <Button icon={<Archive className="h-3.5 w-3.5" />} onClick={() => setLibrary({ filter: "all", replaceId: null })}>
              资产库
            </Button>
            <span className="h-6 w-px flex-none bg-hairline" aria-hidden />

            <label className="flex items-center gap-1.5">
              <span className="label-mono">模型</span>
              <Select
                value={currentTemplate}
                onChange={(e) => void dataMut.config.mutateAsync({ imageTemplate: e.target.value }).catch((x: unknown) => flash("bad", `保存模型选择失败：${msg(x)}`))}
                className="h-8 max-w-[220px]"
                title="出图用的生成模板或库工作流"
              >
                {!imageWorkflows.some((w) => templateKey(w) === currentTemplate) && <option value={currentTemplate}>{currentTemplate}（当前）</option>}
                {templateOptions.length > 0 && (
                  <optgroup label="内置模板">
                    {templateOptions.map((w) => (
                      <option key={w.id} value={templateKey(w)}>
                        {w.name}
                      </option>
                    ))}
                  </optgroup>
                )}
                {libraryOptions.length > 0 && (
                  <optgroup label="库工作流">
                    {libraryOptions.map((w) => (
                      <option key={w.id} value={templateKey(w)}>
                        {w.name}
                      </option>
                    ))}
                  </optgroup>
                )}
              </Select>
            </label>
            {isLibraryTemplate && (
              <span title="库工作流的图要在后端按 graph 提交，而生成请求只带 template key —— 选它出图会被后端打回，先换回内置模板">
                <Badge tone="warn" className="flex-none">
                  库工作流·还不能直接出图
                </Badge>
              </span>
            )}

            <label className="flex items-center gap-1.5">
              <span className="label-mono">实例</span>
              <Select
                value={project.config.imageInstanceId ?? ""}
                onChange={(e) => void dataMut.config.mutateAsync({ imageInstanceId: e.target.value || null }).catch((x: unknown) => flash("bad", `保存实例选择失败：${msg(x)}`))}
                className="h-8 max-w-[190px]"
                title="哪台机器跑出图；跟随项目默认会用后端配置的默认实例"
              >
                <option value="">跟随默认</option>
                {(instances ?? []).map((i) => (
                  <option key={i.id} value={i.id}>
                    {i.name}
                    {i.lastProbeOk === false ? "（探活失败）" : ""}
                  </option>
                ))}
              </Select>
            </label>

            <span className="h-6 w-px flex-none bg-hairline" aria-hidden />
            <div className="flex items-center gap-1.5">
              <span className="label-mono">比例</span>
              <div className="flex rounded-ctl border border-hairline bg-sheen p-[2px]" role="group" aria-label="出图比例">
                {(
                  [
                    { v: "16:9", label: "横屏", icon: Monitor },
                    { v: "9:16", label: "竖屏", icon: Smartphone },
                  ] as const
                ).map((o) => (
                  <button
                    key={o.v}
                    type="button"
                    aria-pressed={aspect === o.v}
                    onClick={() => void dataMut.config.mutateAsync({ aspectRatio: o.v }).catch((x: unknown) => flash("bad", `保存比例失败：${msg(x)}`))}
                    className={cn(
                      "flex items-center gap-1 rounded-ctl px-2 py-[3px] text-note font-semibold transition-colors",
                      aspect === o.v ? "bg-chrome text-chrome-ink" : "text-ink-mute hover:text-ink",
                    )}
                    title={`${o.label} ${o.v} · 出图 ${IMAGE_SIZES[o.v].width}×${IMAGE_SIZES[o.v].height}`}
                  >
                    <o.icon className="h-3.5 w-3.5" />
                    {o.label}
                  </button>
                ))}
              </div>
              <span className="mono hidden text-caption text-ink-mute xl:inline" title="比例只改出图尺寸，不改这里的排版">
                {size.width}×{size.height}
              </span>
            </div>

            <span className="h-6 w-px flex-none bg-hairline" aria-hidden />
            <span className="label-mono rounded-full border border-hairline bg-sheen px-2 py-1">{characters.length} chars</span>
            <span className="label-mono rounded-full border border-hairline bg-sheen px-2 py-1">{scenes.length} scenes</span>
            {gpu?.yielded && (
              <Badge tone="warn" className="flex-none">
                GPU 已让给文本模型
              </Badge>
            )}
          </div>
        </div>

        <div className="px-4">
          <Tabs<View>
            value={view}
            onChange={setView}
            tabs={[
              { key: "casting", label: "角色定妆", badge: <span className="label-mono">Casting</span> },
              { key: "locations", label: "场景概念", badge: <span className="label-mono">Locations</span> },
            ]}
          />
        </div>
      </header>

      {/* ② 内容 */}
      <div className="flex-1 space-y-4 p-4">
        {notice && (
          <div
            role="status"
            className={cn(
              "flex items-start justify-between gap-3 rounded-panel border px-3 py-2 text-note leading-snug",
              notice.tone === "bad"
                ? "border-state-fail/45 bg-state-fail/10 text-state-fail"
                : notice.tone === "good"
                  ? "border-state-ok/40 bg-state-ok/10 text-ink-dim"
                  : "border-hairline bg-sheen text-ink-dim",
            )}
          >
            <span>{notice.text}</span>
            <button onClick={() => setNotice(null)} className="flex-none text-caption text-ink-mute hover:text-ink" aria-label="关闭提示">
              知道了
            </button>
          </div>
        )}

        {view === "casting" ? (
          <section className="space-y-4">
            <SectionHeader
              title="角色定妆 (CASTING)"
              desc="为剧本中的角色生成一致的参考形象"
              actions={
                <>
                  <Button icon={<Plus className="h-3.5 w-3.5" />} onClick={() => setCreating("character")}>
                    新建角色
                  </Button>
                  <Button icon={<Archive className="h-3.5 w-3.5" />} onClick={() => setLibrary({ filter: "character", replaceId: null })}>
                    从资产库选择
                  </Button>
                  <Button
                    variant={allCharsReady ? "default" : "primary"}
                    icon={allCharsReady ? <RefreshCw className="h-3.5 w-3.5" /> : <Sparkles className="h-3.5 w-3.5" />}
                    disabled={!!batch}
                    onClick={() => void batchGenerate("character")}
                  >
                    {allCharsReady ? "重新生成所有角色" : "生成所有缺图角色"}
                  </Button>
                </>
              }
            />
            {characters.length === 0 ? (
              <Empty
                title="还没有角色"
                hint="去剧本页点「解析剧本」让本地模型拆出角色，或在这里手动补一个 —— 定妆照是后续所有镜头一致性的底子。"
                action={
                  <Button variant="primary" icon={<Plus className="h-3.5 w-3.5" />} onClick={() => setCreating("character")}>
                    新建角色
                  </Button>
                }
              />
            ) : (
              <ul className="grid gap-4 md:grid-cols-2 2xl:grid-cols-3">
                {characters.map((c) => (
                  <li key={c.id}>
                    <CharacterCard
                      project={project}
                      char={c}
                      mediaById={mediaById}
                      handle={handles[genKey({ kind: "character", characterId: c.id })]}
                      onGenerate={() => void genCharacter(c)}
                      onUpload={(file) => void upload(file, "character", c.id, attachCharacter(c.id), `已上传 ${c.name} 的定妆照`)}
                      onPatch={(patch) => void edit((p) => {
                        const x = p.data.characters.find((y) => y.id === c.id);
                        if (x) Object.assign(x, patch);
                      })}
                      onSavePrompts={(patch) => void edit((p) => {
                        const x = p.data.characters.find((y) => y.id === c.id);
                        if (x) Object.assign(x, patch);
                      })}
                      onOpenWardrobe={() => setWardrobeId(c.id)}
                      onAddToLibrary={() => void addToLibrary("character", c)}
                      onReplaceFromLibrary={() => setLibrary({ filter: "character", replaceId: c.id })}
                      onDelete={() => askDeleteCharacter(c)}
                      onPreview={(m, title) => setPreview({ media: m, title })}
                    />
                  </li>
                ))}
              </ul>
            )}
          </section>
        ) : (
          <section className="space-y-4">
            <SectionHeader
              title="场景概念 (LOCATIONS)"
              desc="为剧本场景生成环境参考图"
              tone="ok"
              actions={
                <>
                  <Button icon={<Plus className="h-3.5 w-3.5" />} onClick={() => setCreating("scene")}>
                    新建场景
                  </Button>
                  <Button icon={<Archive className="h-3.5 w-3.5" />} onClick={() => setLibrary({ filter: "scene", replaceId: null })}>
                    从资产库选择
                  </Button>
                  <Button
                    variant={allScenesReady ? "default" : "primary"}
                    icon={allScenesReady ? <RefreshCw className="h-3.5 w-3.5" /> : <Sparkles className="h-3.5 w-3.5" />}
                    disabled={!!batch}
                    onClick={() => void batchGenerate("scene")}
                  >
                    {allScenesReady ? "重新生成所有场景" : "生成所有缺图场景"}
                  </Button>
                </>
              }
            />
            {scenes.length === 0 ? (
              <Empty
                title="还没有场景"
                hint="剧本页解析会按场次拆出场景；也可以在这里补一个。场景图之后会被当成关键帧的环境参考。"
                action={
                  <Button variant="primary" icon={<Plus className="h-3.5 w-3.5" />} onClick={() => setCreating("scene")}>
                    新建场景
                  </Button>
                }
              />
            ) : (
              <ul className="grid gap-4 md:grid-cols-2 2xl:grid-cols-3">
                {scenes.map((s) => (
                  <li key={s.id}>
                    <SceneCard
                      project={project}
                      scene={s}
                      mediaById={mediaById}
                      handle={handles[genKey({ kind: "scene", sceneId: s.id })]}
                      onGenerate={() => void genScene(s)}
                      onUpload={(file) => void upload(file, "scene", s.id, attachScene(s.id), `已上传 ${s.name} 的场景图`)}
                      onPatch={(patch) => void edit((p) => {
                        const x = p.data.scenes.find((y) => y.id === s.id);
                        if (x) Object.assign(x, patch);
                      })}
                      onSavePrompts={(patch) => void edit((p) => {
                        const x = p.data.scenes.find((y) => y.id === s.id);
                        if (x) Object.assign(x, patch);
                      })}
                      onAddToLibrary={() => void addToLibrary("scene", s)}
                      onDelete={() => askDeleteScene(s)}
                      onPreview={(m, title) => setPreview({ media: m, title })}
                    />
                  </li>
                ))}
              </ul>
            )}
          </section>
        )}

        <p className="flex items-start gap-1.5 pt-1 text-caption leading-snug text-ink-mute">
          <Wand2 className="mt-0.5 h-3.5 w-3.5 flex-none" />
          定妆照与场景图都会挂回项目的 refMediaIds：导演台拼关键帧时按这个取参考图，所以换图请在这里换，别只在本地留一份预览。
        </p>
      </div>

      {/* ③ 批量遮罩 */}
      {batch && (
        <div role="status" aria-live="polite" className="fixed inset-0 z-40 flex flex-col items-center justify-center gap-3 bg-void/85 backdrop-blur-md">
          <Spinner className="h-10 w-10 text-chrome" />
          <div className="text-subtitle font-semibold">正在批量生成{batch.label}</div>
          <div className="h-1.5 w-64 overflow-hidden rounded-hairline bg-hairline">
            <div
              className="h-full transition-[width] duration-500"
              style={{ width: `${Math.round((batchDone / Math.max(1, batch.keys.length)) * 100)}%`, background: "linear-gradient(90deg, var(--color-chrome), var(--color-chrome-2))" }}
            />
          </div>
          <div className="label-mono">
            第 {Math.min(batchDone + 1, batch.keys.length)} 张 / 共 {batch.keys.length} 张
          </div>
          <Button size="sm" variant="quiet" onClick={() => setBatch(null)}>
            收起遮罩，任务继续在后台跑
          </Button>
        </div>
      )}

      {/* ④ 弹层 */}
      {wardrobeChar && (
        <WardrobeModal
          project={project}
          char={wardrobeChar}
          mediaById={mediaById}
          handles={handles}
          onClose={() => setWardrobeId(null)}
          onPatch={(patch) =>
            void edit((p) => {
              const x = p.data.characters.find((y) => y.id === wardrobeChar.id);
              if (!x) return;
              const { variations, ...rest } = patch;
              Object.assign(x, rest);
              if (!variations) return;
              // 弹窗打开期间后台可能刚回写某张变体图：按 id 合并，别让弹窗里的旧快照把产物抹掉
              const live = new Map(x.variations.map((v) => [v.id, v]));
              x.variations = variations.map((v) => {
                const prev = live.get(v.id);
                if (!prev || v.refMediaIds.length) return v;
                return { ...v, refMediaIds: prev.refMediaIds, status: prev.status };
              });
            })
          }
          onGenerateVariation={(variationId) => genVariation(wardrobeChar, variationId)}
          onUploadVariation={(variationId, file) => void upload(file, "variation", `${wardrobeChar.id}:${variationId}`, attachVariation(wardrobeChar.id, variationId), "已上传变体图")}
          onPreview={(m, title) => setPreview({ media: m, title })}
        />
      )}

      {library && (
        <AssetLibraryModal
          filter={library.filter}
          replaceTarget={library.replaceId ? { characterId: library.replaceId, characterName: characters.find((c) => c.id === library.replaceId)?.name ?? "该角色" } : null}
          mediaById={mediaById}
          onFilterChange={(f) => setLibrary({ filter: f, replaceId: library.replaceId })}
          onImport={(item) => void importAsset(item)}
          onReplace={(item) => void replaceCharacter(library.replaceId!, item)}
          onPreview={(m, title) => setPreview({ media: m, title })}
          onClose={() => setLibrary(null)}
        />
      )}

      {creating === "character" && (
        <NewCharacterSheet
          onClose={() => setCreating(null)}
          onCreate={(c) => {
            setCreating(null);
            setView("casting");
            void edit((p) => void p.data.characters.push(c)).then((ok) => ok && flash("good", `已创建角色「${c.name}」，补一下提示词就能出图`));
          }}
        />
      )}
      {creating === "scene" && (
        <NewSceneSheet
          onClose={() => setCreating(null)}
          onCreate={(s) => {
            setCreating(null);
            setView("locations");
            void edit((p) => void p.data.scenes.push(s)).then((ok) => ok && flash("good", `已创建场景「${s.name}」`));
          }}
        />
      )}

      <ConfirmSheet request={confirm} onClose={() => setConfirm(null)} />

      <Sheet open={!!preview} onClose={() => setPreview(null)} width={900} eyebrow="Preview" title={preview?.title ?? ""}>
        {preview && <PreviewBody media={preview.media} title={preview.title} />}
      </Sheet>
    </div>
  );
}

function PreviewBody({ media, title }: { media: Media; title: string }) {
  // 灯箱也只认静帧：喂进视频会得到破图标，而且要先下完整段
  const still = isStill(media) ? media : undefined;
  const src = useMediaSrc(still);
  return (
    <div className="space-y-2">
      {src ? (
        <img src={src} alt={title} className="max-h-[62vh] w-full rounded-panel border border-hairline bg-void/70 object-contain" />
      ) : (
        <div className="grid h-64 place-items-center rounded-panel border border-hairline text-note text-ink-mute">
          {still ? "这张图读不回来：可能只存在于原项目的媒体库里" : "这条记录不是静帧，没有可显示的封面"}
        </div>
      )}
      <div className="label-mono">
        media {media.id}
        {media.width && media.height ? ` · ${media.width}×${media.height}` : ""}
        {media.bytes ? ` · ${(media.bytes / 1024 / 1024).toFixed(1)}MB` : ""}
      </div>

      {/* 这一组的其他版本：重新生成过的图以前只是被盖在 refMediaIds 后面看不见，
          现在能翻出来、能设当前、能删进回收站 */}
      {media.projectId && <VersionGroup projectId={media.projectId} role={media.role} refId={media.refId} aspect="16/9" label={title} />}
    </div>
  );
}

function SectionHeader({ title, desc, actions, tone = "chrome" }: { title: string; desc: string; actions: React.ReactNode; tone?: "chrome" | "ok" }) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-3 border-b border-hairline pb-3">
      <div className="min-w-0">
        <h3 className="flex items-center gap-2 text-body font-bold uppercase tracking-[0.14em]">
          <SectionDot tone={tone} />
          {title}
        </h3>
        <p className="mt-1 pl-3.5 text-note text-ink-mute">{desc}</p>
      </div>
      <div className="flex flex-wrap items-center gap-2">{actions}</div>
    </div>
  );
}
