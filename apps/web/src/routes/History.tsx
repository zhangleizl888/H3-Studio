/**
 * 「生成历史」页：三个 tab（生成脚本 / 生成图片 / 生成视频），默认只看当前项目，可切到别的项目或全部项目。
 *
 * 为什么"当前项目"是默认而不是全部：A 方案里项目实体在浏览器 IndexedDB，跨项目视图里
 * 「设为当前」这类要写实体的动作做不了（只能预览、恢复、删除）。默认锁本项目，
 * 用户主动切到「全部项目」时再按情况禁用，比一进来就到处是灰按钮诚实。
 */

import { useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Badge, Button, Empty, Modal, Panel, Select, Skeleton, Tabs } from "../components/ui";
import { ConfirmSheet, type ConfirmRequest } from "./project/assets/common";
import { ImagePreview, VideoPreview } from "./project/director/common";
import { VersionHistory } from "../components/VersionHistory";
import { ScriptVersionList } from "../components/ScriptVersionList";
import { useMediaVersions, useProject, useProjects, useScriptVersions, useVersionMutations, resyncMediaIndex } from "../lib/hooks";
import { useApi } from "../lib/apiClient";
import { useQueryClient } from "@tanstack/react-query";
import { flushSaves, patchProject } from "../lib/localStores";
import { setCurrent, targetFromGroup } from "../lib/versions";
import type { Media, Project, VersionBucket } from "../lib/types";

type TabKey = VersionBucket;

const TABS: { key: TabKey; label: string }[] = [
  { key: "script", label: "生成脚本" },
  { key: "image", label: "生成图片" },
  { key: "video", label: "生成视频" },
];

const ALL = "__all__";

export default function History() {
  const [params, setParams] = useSearchParams();
  const tab = (params.get("tab") as TabKey) || "script";
  const { data: projects } = useProjects();

  // 默认项目 = URL 上那个 → 否则列表第一条（listProjects 按 updatedAt 倒序，就是"你正在做的那个"）
  const scope = params.get("project") ?? projects?.[0]?.id ?? ALL;
  const isAll = scope === ALL || !scope;
  const projectId = isAll ? null : scope;

  const setParam = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    next.set(key, value);
    setParams(next, { replace: true });
  };

  const media = useMediaVersions({ projectId, bucket: tab === "script" ? undefined : tab });
  const scripts = useScriptVersions(projectId);

  return (
    <div className="flex min-h-full flex-col">
      <header className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-rule-soft bg-panel/70 px-4 py-2">
        <Tabs<TabKey>
          className="border-b-transparent"
          value={tab}
          onChange={(t) => setParam("tab", t)}
          tabs={TABS.map((t) => ({
            ...t,
            // 只给「已经查过的那一桶」报数；没查的显示 — 而不是 0，0 会让人以为这个项目没出过图
            badge: <span className="label mono">{t.key === "script" ? (scripts.data?.length ?? 0) : t.key === tab ? (media.data?.length ?? 0) : "—"}</span>,
          }))}
        />
        <div className="ml-auto flex items-center gap-2">
          <label className="flex items-center gap-1.5">
            <span className="label">项目</span>
            <Select value={isAll ? ALL : scope} onChange={(e) => setParam("project", e.target.value)} aria-label="选择项目">
              <option value={ALL}>全部项目</option>
              {(projects ?? []).map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </Select>
          </label>
          {projectId && <ResyncButton projectId={projectId} />}
        </div>
      </header>

      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-4">
        {tab === "script" ? (
          <ScriptTab projectIds={isAll ? (projects ?? []).map((p) => p.id) : [projectId ?? ""]} />
        ) : (
          <MediaTab bucket={tab} projectId={projectId} rows={media.data} loading={media.isLoading} />
        )}
      </div>
    </div>
  );
}

/* ───────── 图片 / 视频 ───────── */

function MediaTab({ bucket, projectId, rows, loading }: { bucket: VersionBucket; projectId: string | null; rows?: Media[]; loading: boolean }) {
  const [preview, setPreview] = useState<Media | null>(null);
  const [confirm, setConfirm] = useState<ConfirmRequest | null>(null);
  const { data: projects } = useProjects();
  const muts = useVersionMutations(projectId);

  // 一个项目一次查询，分组在内存里做：逐张卡片查就是 N+1
  const groups = useMemo(() => {
    const map = new Map<string, Media[]>();
    for (const m of rows ?? []) {
      const key = `${m.projectId ?? ""}|${m.kind}|${m.role ?? ""}|${m.refId ?? ""}`;
      (map.get(key) ?? map.set(key, []).get(key)!).push(m);
    }
    return [...map.entries()];
  }, [rows]);

  if (loading) return <Skeleton className="h-40" />;
  if (!groups.length) {
    return (
      <Empty
        title={`还没有${bucket === "video" ? "成片" : "图片"}版本记录`}
        hint="生成一次就会留下 V1。旧任务留下的产物如果没打过标签，会按「每次任务」各自成组。"
      />
    );
  }

  const trashOne = (m: Media, label: string) =>
    setConfirm({
      title: `把 ${label} 移进生成回收站`,
      confirmLabel: "移进回收站",
      danger: true,
      body: (
        <div className="space-y-2 text-note leading-relaxed">
          <p>
            这一版会从<span className="text-ink">卡片、时间轴、导出、提示词页</span>一起消失，只剩生成回收站里那一份。
          </p>
          <p className="text-ink-mute">
            {m.daysLeft != null ? `回收站保留 ${m.daysLeft} 天，到期后文件会被后端自动删掉。` : ""}
            如果这是它当前那一版，卡片会退到剩下最新的一版。
          </p>
        </div>
      ),
      onConfirm: () => {
        void muts.trashMedia.mutateAsync({ id: m.id, projectId: m.projectId ?? projectId }).catch((e) => console.error("移进回收站失败：", e));
        setConfirm(null);
      },
    });

  return (
    <div className="space-y-3">
      {groups.map(([key, list]) => {
        const head = list[0];
        const label = head.title ?? head.refId ?? key;
        const canSet = isLocal(projects, head.projectId);
        return (
          <Panel
            key={key}
            title={
              <span className="flex items-center gap-2">
                {label}
                <Badge>{head.role ?? "产物"}</Badge>
                {!canSet && <span className="text-caption text-ink-mute">不在本机浏览器</span>}
              </span>
            }
            actions={<span className="label mono">{list.filter((m) => !m.deletedAt).length} 版可用</span>}
          >
            {projectId && canSet ? (
              <GroupBody
                projectId={projectId}
                rows={list}
                aspect={undefined}
                canSetCurrent={canSet}
                onPreview={(m) => setPreview(m)}
                onSetCurrent={(m) => void applySetCurrent(projectId, m)}
                onTrash={(m) => trashOne(m, `V${m.version ?? "?"} ${label}`)}
                onRestore={(m) => void muts.restoreMedia.mutateAsync({ id: m.id, projectId }).catch((e) => console.error("恢复失败：", e))}
              />
            ) : (
              <VersionHistory
                rows={list}
                aspect="16/9"
                canSetCurrent={false}
                onPreview={(m) => setPreview(m)}
                onTrash={(m) => trashOne(m, `V${m.version ?? "?"} ${label}`)}
                onRestore={(m) => void muts.restoreMedia.mutateAsync({ id: m.id, projectId }).catch((e) => console.error("恢复失败：", e))}
              />
            )}
          </Panel>
        );
      })}

      <Modal open={!!preview} onClose={() => setPreview(null)} width={880} title={preview ? `V${preview.version ?? "?"} · ${preview.title ?? preview.refId ?? ""}` : "预览"}>
        {preview?.kind === "video" ? <VideoPreview media={preview ?? undefined} /> : <ImagePreview media={preview ?? undefined} alt="版本预览" />}
        {preview?.kind === "video" && <p className="mt-2 text-caption text-ink-mute">演示模式没有真文件，读不回来就是这句 —— 接上后端再点就能播。</p>}
      </Modal>

      <ConfirmSheet request={confirm} onClose={() => setConfirm(null)} />
    </div>
  );
}

/** 当前版指针要按实体类型写：这一层把 (role, refId) 还原成生成对象 */
function GroupBody(props: {
  projectId: string;
  rows: Media[];
  aspect?: string;
  canSetCurrent: boolean;
  onPreview: (m: Media) => void;
  onSetCurrent: (m: Media) => void;
  onTrash: (m: Media) => void;
  onRestore: (m: Media) => void;
}) {
  const { data: project } = useProject(props.projectId);
  const current = currentIdOf(project, props.rows);
  return (
    <VersionHistory
      rows={props.rows}
      currentId={current}
      aspect={props.aspect ?? "16/9"}
      canSetCurrent={props.canSetCurrent}
      onPreview={props.onPreview}
      onSetCurrent={props.onSetCurrent}
      onTrash={props.onTrash}
      onRestore={props.onRestore}
    />
  );
}

const isLocal = (projects: { id: string }[] | undefined, key: string | null | undefined) => !!key && !!projects?.some((p) => p.id === key);

/** 实体指针里的当前版。项目实体不在本机就读不到，返回 null，面板就一个「当前」都不标 */
function currentIdOf(project: Project | undefined, rows: Media[]): string | null {
  if (!project) return null;
  const head = rows.find((m) => !m.deletedAt) ?? rows[0];
  const target = targetFromGroup(head?.role, head?.refId);
  if (!target) return null;
  const d = project.data;
  if (target.kind === "character") return d.characters.find((c) => c.id === target.characterId)?.refMediaIds[0] ?? null;
  if (target.kind === "variation")
    return d.characters.find((c) => c.id === target.characterId)?.variations.find((v) => v.id === target.variationId)?.refMediaIds[0] ?? null;
  if (target.kind === "scene") return d.scenes.find((s) => s.id === target.sceneId)?.refMediaIds[0] ?? null;
  const shot = d.shots.find((s) => s.id === target.shotId);
  if (!shot) return null;
  if (target.kind === "video") return shot.videoMediaIds[0] ?? null;
  const kf = shot.keyframes?.find((k) => k.type === target.frameType);
  return kf?.mediaId ?? (target.frameType === "start" ? shot.startFrameMediaId : shot.endFrameMediaId) ?? null;
}

async function applySetCurrent(projectId: string, m: Media) {
  const target = targetFromGroup(m.role, m.refId);
  if (!target) {
    console.warn("这一版还原不出所属对象（多半是没打标签的旧产物），不能设当前");
    return;
  }
  await flushSaves();
  await patchProject(projectId, {}, (p) => setCurrent(p, target, m));
}

/* ───────── 剧本 ───────── */

function ScriptTab({ projectIds }: { projectIds: string[] }) {
  const keys = projectIds.filter(Boolean);
  if (!keys.length) return <Empty title="还没有项目" hint="先在仪表盘建一个项目，剧本的每一版才会留在服务端。" />;
  return (
    <div className="space-y-3">
      {keys.map((id) => (
        <ScriptVersionList key={id} projectId={id} />
      ))}
    </div>
  );
}

/**
 * 「同步产物索引」：换浏览器、清过缓存、或在别的机器上出过片之后，项目实体里的指针会指向
 * 哪都不存在的产物（卡片亮着破图）。这里显式补登记 + 摘坏指针。
 *
 * 做成按钮而不是开机自动跑：它在改用户的项目实体，得让人知道它动了什么。
 */
function ResyncButton({ projectId }: { projectId: string }) {
  const api = useApi();
  const qc = useQueryClient();
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const run = () => {
    setBusy(true);
    setNote(null);
    void resyncMediaIndex(api, projectId)
      .then((r) => {
        setNote(`补登记 ${r.adopted} 项，摘掉 ${r.dangling} 个失效指针`);
        qc.invalidateQueries({ queryKey: ["projects", projectId] });
        qc.invalidateQueries({ queryKey: ["media", projectId] });
        qc.invalidateQueries({ queryKey: ["mediaVersions"] });
      })
      .catch((e) => setNote(`同步失败：${String(e instanceof Error ? e.message : e)}`))
      .finally(() => setBusy(false));
  };

  return (
    <span className="flex items-center gap-1.5">
      <Button
        size="sm"
        variant="ghost"
        loading={busy}
        title="把服务端还在的产物重新登记进本地索引，并摘掉指向已不存在对象的指针（只在选定的那一个项目上做）"
        onClick={run}
      >
        同步产物索引
      </Button>
      {note && (
        <span className="text-caption text-ink-mute" role="status">
          {note}
        </span>
      )}
    </span>
  );
}
