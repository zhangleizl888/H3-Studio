import { useState } from "react";
import { Aperture, ChevronDown, ChevronRight, Clock, ListChecks, Users } from "lucide-react";
import { Badge, Button, Empty, Panel } from "../../../components/ui";
import { SplitHandle, usePane } from "../../../components/SplitPane";
import { buildCharacterPrompt, buildKeyframePrompt, buildScenePrompt, movementLabel, shotLabel } from "../../../lib/prompts";
import { renderProgress } from "../../../lib/generate";
import type { Character, Keyframe, Project, Scene, Shot } from "../../../lib/types";
import { ago, cn, fmtSec } from "../../../lib/utils";
import { InlineText } from "./InlineText";
import { splitSceneName } from "./merge";

interface Props {
  project: Project;
  onPatchData: (d: Partial<Project["data"]>) => void;
  onBackToCreate: () => void;
}

/** 拍摄清单：梗概 + 演员表 + 按场次分组的镜头表。所有提示词都是「拼好后的原文」，就地可改可存 */
export function Manifest({ project, onPatchData, onBackToCreate }: Props) {
  const pane = usePane("manifest.side", 300, 240, 460);
  const { config, data } = project;
  const script = data.script;
  const progress = renderProgress(project);
  const groups = groupByScene(project);

  const patchCharacters = (next: Character[]) => onPatchData({ characters: next });
  const patchScenes = (next: Scene[]) => onPatchData({ scenes: next });
  const patchShots = (next: Shot[]) => onPatchData({ shots: next });
  const patchShot = (shotId: string, fn: (s: Shot) => Shot) => patchShots(data.shots.map((s) => (s.id === shotId ? fn(s) : s)));
  const patchScript = (patch: Partial<NonNullable<typeof script>>) => script && onPatchData({ script: { ...script, ...patch } });

  return (
    <div className="min-h-full">
      <header className="flex flex-wrap items-end gap-x-6 gap-y-2 border-b border-rule-soft bg-panel/70 px-4 py-2.5">
        <div className="flex items-center gap-2">
          <ListChecks className="h-4 w-4 text-chrome" />
          <h2 className="text-title font-light tracking-tight text-ink">拍摄清单</h2>
          <span className="label-mono">Script Manifest</span>
        </div>
        <Stat label="项目" value={script?.title || project.name} />
        <Stat label="时长" value={fmtSec(config.targetDurationSec)} />
        <Stat label="已规划镜头" value={`${progress.total} 个`} />
        <Stat label="预计总时长" value={progress.total ? fmtSec(progress.estSeconds) : "—"} />
        <span className="ml-auto flex items-center gap-3">
          <span className="label">上次保存 {ago(project.updatedAt)}</span>
          <Button size="sm" variant="quiet" onClick={onBackToCreate}>
            返回剧本创作
          </Button>
        </span>
      </header>

      <div style={pane.style} className="grid items-start gap-4 p-4 xl:grid-cols-[var(--pane-w)_1fr]">
        <div className="relative space-y-4">
          <Panel title="故事梗概" dense>
            <div className="space-y-3 p-3">
              {!script ? (
                <p className="text-note leading-snug text-ink-mute">还没有拆解结果：没有 logline、没有段落可编辑。</p>
              ) : (
                <>
                  <div className="quote-bar">
                    <div className="label mb-1">一句话故事</div>
                    <InlineText
                      value={script.logline ?? ""}
                      rows={3}
                      serif
                      emptyText="拆解没给 logline，点铅笔补一句"
                      onSave={(v) => patchScript({ logline: v })}
                    />
                  </div>
                  {(script.storyParagraphs ?? []).length === 0 ? (
                    <p className="text-note leading-snug text-ink-mute">没有故事段落：拆解给了节拍才会生成段落，当前节拍列表是空的。</p>
                  ) : (
                    <div className="space-y-2.5">
                      <div className="label">正文段落（按叙事顺序）</div>
                      {(script.storyParagraphs ?? []).map((para, i) => (
                        <div key={para.id} className="rounded-ctl border border-rule-soft bg-raised/40 p-2">
                          <div className="label-mono mb-1">
                            {String(i + 1).padStart(2, "0")}
                            {para.sceneRefId ? ` · ${para.sceneRefId}` : ""}
                          </div>
                          <InlineText
                            value={para.text}
                            rows={4}
                            onSave={(v) =>
                              patchScript({
                                storyParagraphs: (script.storyParagraphs ?? []).map((x) => (x.id === para.id ? { ...x, text: v } : x)),
                              })
                            }
                          />
                        </div>
                      ))}
                    </div>
                  )}
                  {script.genre?.length ? (
                    <div className="flex flex-wrap items-center gap-1.5">
                      <span className="label">类型</span>
                      {script.genre.map((g) => (
                        <Badge key={g}>{g}</Badge>
                      ))}
                    </div>
                  ) : null}
                </>
              )}
            </div>
          </Panel>

          <Panel
            title="演员表"
            actions={<span className="label">{data.characters.length} 人</span>}
            dense
          >
            <div className="space-y-3 p-3">
              {data.characters.length === 0 ? (
                <p className="text-note leading-snug text-ink-mute">还没有角色。生成分镜脚本后，拆解结果会按名字并进来，已有的不会被覆盖。</p>
              ) : (
                data.characters.map((c) => (
                  <div key={c.id} className="rounded-ctl border border-rule-soft bg-raised/40 p-2.5">
                    <div className="mb-1.5 flex items-baseline gap-2">
                      <Users className="h-3 w-3 flex-none text-ink-mute" />
                      <span className="text-body font-semibold text-ink">{c.name || "（未命名）"}</span>
                      <span className="label mono">
                        {[c.gender, c.age || c.traits?.age].filter(Boolean).join(" · ")}
                      </span>
                    </div>
                    <div className="label mb-1">完整角色提示词（定妆照就吃这一段）</div>
                    <InlineText
                      className="rounded-ctl border border-rule-soft bg-slate/60 p-2"
                      mono
                      rows={6}
                      value={buildCharacterPrompt(c, config)}
                      emptyText="还没有外形描述"
                      onSave={(v) => patchCharacters(data.characters.map((x) => (x.id === c.id ? { ...x, visualPrompt: v } : x)))}
                    />
                  </div>
                ))
              )}
            </div>
          </Panel>
          <SplitHandle pane={pane} side="left" label="拍摄清单左栏宽度" className="hidden xl:block" />
        </div>

        <div className="min-w-0">
          {progress.total === 0 ? (
            <Empty
              title="还没有镜头可拍"
              hint="先去「剧本创作」把剧本贴好，点左下角「生成/重新生成 分镜脚本」：拆解会回写角色与场景，分镜会按目标时长出镜头表，并预拼每镜的首尾帧提示词。"
              action={
                <Button variant="primary" onClick={onBackToCreate}>
                  去剧本创作
                </Button>
              }
            />
          ) : (
            <div className="space-y-4">
              {groups.map((g, i) => (
                <SceneGroup
                  key={g.scene?.id ?? "unassigned"}
                  order={i}
                  group={g}
                  project={project}
                  onPatchScene={(next) => patchScenes(next)}
                  onPatchShot={patchShot}
                />
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col leading-tight">
      <span className="label-mono">{label}</span>
      <span className="mono text-body text-ink">{value}</span>
    </div>
  );
}

interface SceneGroupRow {
  scene: Scene | null;
  shots: Shot[];
}

/** 按场景实体的顺序分组；指不到场景的镜头单独一组，别让它悄悄消失 */
function groupByScene(project: Project): SceneGroupRow[] {
  const out: SceneGroupRow[] = project.data.scenes.map((scene) => ({
    scene,
    shots: project.data.shots.filter((s) => s.sceneId === scene.id).sort((a, b) => a.index - b.index),
  }));
  const known = new Set(project.data.scenes.map((s) => s.id));
  const loose = project.data.shots.filter((s) => !s.sceneId || !known.has(s.sceneId)).sort((a, b) => a.index - b.index);
  if (loose.length) out.push({ scene: null, shots: loose });
  return out;
}

function SceneGroup({
  order,
  group,
  project,
  onPatchScene,
  onPatchShot,
}: {
  order: number;
  group: SceneGroupRow;
  project: Project;
  onPatchScene: (next: Scene[]) => void;
  onPatchShot: (shotId: string, fn: (s: Shot) => Shot) => void;
}) {
  const { scene, shots } = group;
  const split = scene ? splitSceneName(scene.name, scene.desc) : { location: "未分配场景", time: "", atmosphere: "拆解没给这些镜头标场景名，去导演台手动归场" };
  const name = scene?.location || split.location;
  const atmosphere = scene?.atmosphere || split.atmosphere;
  const time = scene?.time || split.time;

  return (
    <section className="rounded-panel border border-rule-soft bg-panel">
      <header className="flex flex-wrap items-baseline gap-x-4 gap-y-1 border-b border-rule-soft px-4 py-3">
        <span className="mono text-display leading-none text-ink-mute/70">{String(order + 1).padStart(2, "0")}</span>
        <h3 className="text-subtitle font-semibold tracking-wide text-ink">{name}</h3>
        {time ? (
          <span className="inline-flex items-center gap-1 rounded-full border border-rule px-2 py-[1px] text-caption text-ink-dim">
            <Clock className="h-3 w-3" /> {time}
          </span>
        ) : (
          <Badge>时段未标</Badge>
        )}
        <span className="ml-auto max-w-[46ch] truncate text-caption text-ink-mute" title={atmosphere}>
          {atmosphere}
        </span>
      </header>

      {scene && (
        <div className="border-b border-rule-soft px-4 py-2">
          <div className="label mb-1">场景提示词（概念图就吃这一段）</div>
          <InlineText
            className="rounded-ctl border border-rule-soft bg-slate/60 p-2"
            mono
            rows={3}
            value={buildScenePrompt(scene, project.config)}
            emptyText="还没有场景描述"
            onSave={(v) => onPatchScene(project.data.scenes.map((x) => (x.id === scene.id ? { ...x, visualPrompt: v } : x)))}
          />
        </div>
      )}

      <div className="divide-y divide-rule-soft">
        {shots.length === 0 ? (
          <p className="px-4 py-3 text-note text-ink-mute">本场还没有镜头：分镜规划没给它分配镜位。</p>
        ) : (
          shots.map((shot) => <ShotRow key={shot.id} shot={shot} project={project} onPatch={(fn) => onPatchShot(shot.id, fn)} />)
        )}
      </div>
    </section>
  );
}

function ShotRow({ shot, project, onPatch }: { shot: Shot; project: Project; onPatch: (fn: (s: Shot) => Shot) => void }) {
  const [open, setOpen] = useState<Record<"start" | "end", boolean>>({ start: false, end: false });
  const chars = project.data.characters.filter((c) => shot.characterIds.includes(c.id));
  const scene = project.data.scenes.find((s) => s.id === shot.sceneId);

  const frameOf = (type: "start" | "end"): Keyframe =>
    shot.keyframes?.find((k) => k.type === type) ?? {
      id: `${shot.id}-${type}`,
      type,
      visualPrompt: buildKeyframePrompt({
        base: shot.action,
        visualStyle: project.config.visualStyle,
        cameraMovement: shot.cameraMovement,
        frameType: type,
        withConsistency: chars.length > 0,
      }),
      status: "pending",
      mediaId: null,
    };

  const savePrompt = (type: "start" | "end", v: string) => {
    const kf = frameOf(type);
    const other = (shot.keyframes ?? []).find((k) => k.type !== type);
    const next: Keyframe = { ...kf, visualPrompt: v };
    // 永远按 首帧/尾帧 的顺序存，导演台与 generate.ts 都按这个约定读
    const keyframes = [type === "start" ? next : other, type === "start" ? other : next].filter((k): k is Keyframe => !!k);
    onPatch((s) => ({ ...s, keyframes }));
  };

  return (
    <div className="flex flex-col gap-4 p-4 lg:flex-row">
      <div className="flex flex-none flex-col gap-2 lg:w-36">
        <span className="mono text-caption tracking-widest text-ink-mute">SHOT {shotLabel(shot.id, shot.index)}</span>
        <span className="w-fit rounded-full border border-rule bg-raised/60 px-2 py-[2px] text-caption text-ink-dim">{shot.shotSize}</span>
        <span className="w-fit rounded-full border border-rule bg-raised/60 px-2 py-[2px] text-caption text-ink-dim" title={movementLabel(shot.cameraMovement)}>
          {shot.cameraMovement}
        </span>
        <span className="mono text-caption text-ink-mute">
          {fmtSec(shot.durationSec)} · {shot.frameCount} 帧
        </span>
        {scene ? null : <Badge tone="warn">未归场</Badge>}
      </div>

      <div className="min-w-0 flex-1 space-y-3">
        <div>
          <div className="label mb-1">画面描述</div>
          <InlineText value={shot.action} rows={3} emptyText="这一镜还没有动作描述" onSave={(v) => onPatch((s) => ({ ...s, action: v }))} />
        </div>
        <div className="quote-bar">
          <div className="label mb-1">台词</div>
          <InlineText
            serif
            rows={2}
            value={shot.dialogue ?? ""}
            emptyText="这一镜没有台词"
            onSave={(v) => onPatch((s) => ({ ...s, dialogue: v }))}
          />
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="label">角色</span>
          {chars.length === 0 ? (
            <span className="text-caption text-ink-mute">无角色（拆解没给这一镜标人名）</span>
          ) : (
            chars.map((c) => (
              <span key={c.id} className="rounded-full border border-rule bg-raised/60 px-2 py-[1px] text-caption text-ink-dim">
                {c.name}
              </span>
            ))
          )}
        </div>
      </div>

      <div className="flex-none space-y-2 border-rule-soft lg:w-[38%] lg:border-l lg:pl-4">
        <div className="flex items-center gap-1.5">
          <Aperture className="h-3 w-3 text-ink-mute" />
          <span className="label">三段式画面提示词</span>
          <span className="ml-auto flex items-center gap-1">
            {(["start", "end"] as const).map((t) => (
              <button
                key={t}
                type="button"
                aria-expanded={open[t]}
                onClick={() => setOpen((o) => ({ ...o, [t]: !o[t] }))}
                className={cn("inline-flex items-center gap-0.5 rounded-ctl border border-rule px-1.5 py-[1px] text-caption", open[t] ? "bg-raised text-ink" : "text-ink-mute hover:text-ink")}
              >
                {open[t] ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
                {t === "start" ? "首帧" : "尾帧"}
              </button>
            ))}
          </span>
        </div>
        {(["start", "end"] as const).map((t) =>
          open[t] ? (
            <InlineText
              key={t}
              mono
              rows={9}
              className="max-h-72 overflow-y-auto rounded-ctl border border-rule-soft bg-slate/60 p-2"
              value={frameOf(t).visualPrompt}
              emptyText="还没有提示词"
              onSave={(v) => savePrompt(t, v)}
            />
          ) : (
            <button
              key={t}
              type="button"
              onClick={() => setOpen((o) => ({ ...o, [t]: true }))}
              className="block w-full truncate rounded-ctl border border-rule-soft bg-raised/40 px-2 py-1 text-left text-caption text-ink-mute hover:text-ink-dim"
              title={frameOf(t).visualPrompt}
            >
              {t === "start" ? "首帧" : "尾帧"}：{frameOf(t).visualPrompt.split("\n")[0] || "（空）"}
            </button>
          ),
        )}
      </div>
    </div>
  );
}
