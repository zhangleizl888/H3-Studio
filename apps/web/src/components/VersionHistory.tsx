/**
 * 版本历史面板：一处实现，四张宿主（角色卡、场景卡、服装变体、镜头抽屉、剧本页、全量页）。
 *
 * 三条口径是这个功能最容易说谎的地方，都写死在这里：
 *  1. 「当前」来自宿主传的指针，不是服务端字段 —— 图片/视频的当前版本本来就是项目实体里的引用
 *  2. 视频行绝不进 <img>：本机没有缩略图端点，喂进去的结果是"先下载整段视频、再画一个破图标"
 *     （`lib/utils.ts` 的 isStill 是唯一守门人）。要封面就宿主传 `posterOf`
 *  3. 回收站里的版本只能"恢复"，不能一步跨到"设为当前"：一次动作只做一件事
 */

import { useState } from "react";
import { Link } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { History as HistoryIcon, RotateCcw, Trash2 } from "lucide-react";
import { Badge, Button, Empty, Modal, Progress } from "./ui";
import { ConfirmSheet, MediaImage, type ConfirmRequest } from "../routes/project/assets/common";
import { ImagePreview, VideoPreview } from "../routes/project/director/common";
import { isStill } from "../lib/utils";
import { currentIdFor, isLocalProject, setCurrent, targetFromGroup } from "../lib/versions";
import { flushSaves, patchProject } from "../lib/localStores";
import { useProject, useProjects, useVersionGroup, useVersionMutations } from "../lib/hooks";
import type { Media } from "../lib/types";

export interface VersionHistoryProps {
  /** 这一组的全部版本。传进来的应当包含回收站里的那些（V 号才不会重排） */
  rows: Media[];
  /** 当前版 id：宿主从实体指针里读出来的那个 */
  currentId?: string | null;
  /** 卡片与抽屉里用横条，全量页用列表 */
  compact?: boolean;
  aspect?: string;
  /** 这个项目实体在本机浏览器里吗？不在就只能禁用「设为当前」，不是抛错 */
  canSetCurrent?: boolean;
  /** 视频版本没有真缩略图：宿主能给首帧静帧就给，给不了就如实显示占位块 */
  posterOf?: (m: Media) => Media | undefined;
  onPreview?: (m: Media) => void;
  onSetCurrent?: (m: Media) => void;
  onTrash?: (m: Media) => void;
  onRestore?: (m: Media) => void;
  /** 分组还没有任何产物时给的话术 */
  emptyHint?: string;
}

const newestFirst = (rows: Media[]) =>
  [...rows].sort((a, b) => (b.version ?? 0) - (a.version ?? 0));

/**
 * 时间戳显示。
 *
 * 不要用 assets/common 里的 timeLabel —— 它是把「日 / night」这类拆解原词翻成两字徽标的，
 * 喂它 ISO 会原样吐回来（我踩过，列表里满屏裸时间戳）。
 * 读不出时间就写「时间未知」，绝不兜底成当前时间 —— 那正是剧本 V1 显示不出时间那一类 bug 的来路。
 */
const when = (iso?: string | null) => {
  if (!iso) return "时间未知";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "时间未知" : d.toLocaleString();
};

export function VersionHistory({
  rows,
  currentId,
  compact = false,
  aspect = "16/9",
  canSetCurrent = true,
  posterOf,
  onPreview,
  onSetCurrent,
  onTrash,
  onRestore,
  emptyHint,
}: VersionHistoryProps) {
  // 面板默认把回收站里的版本藏起来：用户要的是"我的第 3 版去哪了"，先给他看活的
  const [showTrash, setShowTrash] = useState(false);
  const ordered = newestFirst(rows);
  const live = ordered.filter((m) => !m.deletedAt);
  const trashed = ordered.filter((m) => m.deletedAt);
  const shown = showTrash ? ordered : live;

  if (!ordered.length) {
    return <Empty title="还没有版本记录" hint={emptyHint ?? "生成一次就会留下 V1。旧的项目记录不在这里 —— 后端只存它见过的产物。"} />;
  }

  return (
    <div className="space-y-1.5">
      <div className="flex flex-wrap items-center gap-2">
        <span className="label inline-flex items-center gap-1.5">
          <HistoryIcon className="h-3 w-3" aria-hidden />
          共 {ordered.length} 版
        </span>
        {trashed.length > 0 && (
          <button
            type="button"
            onClick={() => setShowTrash((v) => !v)}
            aria-pressed={showTrash}
            className="rounded-ctl border border-rule bg-raised px-2 py-[3px] text-caption text-ink-dim transition-colors hover:text-ink"
          >
            回收站里 {trashed.length} 版{showTrash ? "（收起）" : "（展开）"}
          </button>
        )}
        {!canSetCurrent && (
          <span className="text-caption text-ink-mute" title="A 方案：项目实体存在浏览器 IndexedDB 里，换机器就读不到，摘指针这一步做不了">
            这个项目不在本机浏览器里，「设为当前」不可用
          </span>
        )}
      </div>

      {compact ? (
        <ul className="flex flex-wrap gap-1.5">
          {shown.map((m) => (
            <li key={m.id} className="w-[74px]">
              <button
                type="button"
                onClick={() => onPreview?.(m)}
                title={`V${m.version ?? "?"} · ${when(m.createdAt)}${m.deletedAt ? " · 在回收站" : ""}`}
                className="block w-full text-left"
              >
                <MediaImage
                  media={m.kind === "video" ? posterOf?.(m) : m}
                  seedText={m.id}
                  alt={`V${m.version ?? "?"} 缩略图`}
                  aspect={aspect}
                  badge={<span className="mono">{m.deletedAt ? "废" : `V${m.version ?? "?"}`}</span>}
                />
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <ul className="divide-y divide-hairline overflow-hidden rounded-ctl border border-rule-soft">
          {shown.map((m) => (
            <li key={m.id} className="flex items-center gap-2.5 px-2 py-1.5">
              <button type="button" onClick={() => onPreview?.(m)} className="w-[92px] flex-none text-left" title="点开预览这一版">
                <MediaImage
                  media={m.kind === "video" ? posterOf?.(m) : m}
                  seedText={m.id}
                  alt={`V${m.version ?? "?"} 预览`}
                  aspect={aspect === "9/16" ? "9/16" : "16/9"}
                />
              </button>

              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-1.5">
                  <span className="mono text-note text-ink">V{m.version ?? "?"}</span>
                  {m.id === currentId && <Badge tone="good">当前</Badge>}
                  {m.deletedAt && <Badge tone="warn">在回收站 · 还剩 {m.daysLeft ?? 0} 天</Badge>}
                </div>
                <div className="text-caption leading-snug text-ink-mute">
                  {/* 视频行的时长/尺寸后端没填（本机实测全 NULL），没有就整段不说，不写「未知」糊弄 */}
                  {when(m.createdAt)}
                  {m.bytes ? ` · ${(m.bytes / 1048576).toFixed(2)} MB` : ""}
                  {m.width && m.height ? ` · ${m.width}×${m.height}` : ""}
                  {m.durationMs ? ` · ${(m.durationMs / 1000).toFixed(1)}s` : ""}
                </div>
              </div>

              <div className="flex flex-none items-center gap-1">
                {m.deletedAt ? (
                  <Button size="sm" variant="quiet" icon={<RotateCcw className="h-3 w-3" aria-hidden />} onClick={() => onRestore?.(m)}>
                    恢复
                  </Button>
                ) : (
                  <>
                    <Button
                      size="sm"
                      variant={m.id === currentId ? "ghost" : "default"}
                      disabled={!canSetCurrent || m.id === currentId}
                      title={canSetCurrent ? "把这一版设为当前（只改项目里的指针，不重生成、不占显存）" : "这个项目不在本机浏览器里"}
                      onClick={() => onSetCurrent?.(m)}
                    >
                      设为当前
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      aria-label={`把 V${m.version ?? "?"} 移进生成回收站`}
                      title="移进生成回收站：卡片、时间轴、导出里都不再出现，100 天内可恢复"
                      icon={<Trash2 className="h-3 w-3" aria-hidden />}
                      onClick={() => onTrash?.(m)}
                    />
                  </>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}

      {!live.length && (
        <p className="text-caption leading-snug text-ink-mute">
          这一组没有能用的产物了 —— 全在回收站里。恢复一版，或者重新生成一版。
        </p>
      )}

      {/* 非静帧又没拿到首帧封面时给个明确交代，别让"空白"看起来像坏了 */}
      {!compact && shown.some((m) => m.kind === "video" && !posterOf?.(m)) && (
        <p className="text-caption text-ink-mute">视频版本没有可用的封面（后端不产缩略图），上面那格是占位块。</p>
      )}
    </div>
  );
}

/** 生成中占位：新板还没落地时给一条真实进度，不假装完成 */
export function VersionBusy({ stage, progress }: { stage?: string | null; progress?: number | null }) {
  if (progress == null && !stage) return null;
  return <Progress value={progress != null ? Math.round(progress * 100) : undefined} max={100} unavailable={progress == null} stage={stage ?? null} />;
}

/** 这一版能不能画进 <img>。宿主自己做二次判断时用，别绕开组件里的 isStill */
export const stillRenderable = (m: Media): boolean => isStill(m);

/**
 * 内嵌版面板：给它服务端的分组键 `(role, refId)`，它自己拉这一组、自己写实体指针。
 *
 * 三处宿主（资产预览、镜头抽屉的关键帧与成片）共用这一份，而不是各写一遍取数 +
 * 各写一遍摘指针 —— 指针拓扑只允许有 lib/versions.ts 一个出口。
 */
export function VersionGroup({
  projectId,
  role,
  refId,
  aspect,
  compact = true,
  posterOf,
  onPreview,
  label = "版本",
}: {
  projectId: string;
  role?: string | null;
  refId?: string | null;
  aspect?: string;
  compact?: boolean;
  posterOf?: (m: Media) => Media | undefined;
  onPreview?: (m: Media) => void;
  label?: string;
}) {
  const { data: rows } = useVersionGroup(projectId, role, refId);
  const { data: project } = useProject(projectId);
  const { data: projects } = useProjects();
  const muts = useVersionMutations(projectId);
  const qc = useQueryClient();
  const [confirm, setConfirm] = useState<ConfirmRequest | null>(null);
  const [internal, setInternal] = useState<Media | null>(null);
  const target = targetFromGroup(role, refId);
  // 项目实体不在这台浏览器上时，指针写不了 —— 只能禁用，不能让 requireProject 抛错
  const canSet = !!target && isLocalProject(projects, projectId);

  if (!rows?.length) return null;

  const applyCurrent = async (m: Media) => {
    if (!target) return;
    await flushSaves();
    await patchProject(projectId, {}, (p) => setCurrent(p, target, m));
    // 直接写 IndexedDB 的动作绕过 react-query，不手动失效就会出现「点了没反应」
    qc.invalidateQueries({ queryKey: ["projects", projectId] });
    qc.invalidateQueries({ queryKey: ["media", projectId] });
    qc.invalidateQueries({ queryKey: ["mediaVersions"] });
  };

  return (
    <div className="space-y-1.5">
      <VersionHistory
        rows={rows}
        currentId={project && target ? currentIdFor(project, target) : null}
        compact={compact}
        aspect={aspect}
        canSetCurrent={canSet}
        posterOf={posterOf}
        onPreview={(m) => (onPreview ? onPreview(m) : setInternal(m))}
        onSetCurrent={(m) => void applyCurrent(m).catch((e) => console.error("设为当前失败：", e))}
        onTrash={(m) =>
          setConfirm({
            title: `把 ${label} V${m.version ?? "?"} 移进生成回收站`,
            confirmLabel: "移进回收站",
            danger: true,
            body: (
              <p className="text-note leading-relaxed">
                这一版会从卡片、时间轴、导出、提示词页<span className="text-ink">一起消失</span>，只剩生成回收站里那一份；
                如果它正是当前这一版，这里会退到剩下最新的一版。
              </p>
            ),
            onConfirm: () => {
              void muts.trashMedia.mutateAsync({ id: m.id, projectId }).catch((e) => console.error("移进回收站失败：", e));
              setConfirm(null);
            },
          })
        }
        onRestore={(m) => void muts.restoreMedia.mutateAsync({ id: m.id, projectId }).catch((e) => console.error("恢复失败：", e))}
      />
      <div className="flex items-center gap-2">
        <Link to={`/history?project=${encodeURIComponent(projectId)}&tab=${rows.some((m) => m.kind === "video") ? "video" : "image"}`} className="text-caption text-ink-dim underline-offset-2 hover:underline">
          管理版本
        </Link>
        {!canSet && <span className="text-caption text-ink-mute">这个项目不在本机浏览器里，改指针做不了（回收站与预览不受影响）</span>}
      </div>

      <Modal open={!!internal} onClose={() => setInternal(null)} width={880} title={internal ? `${label} V${internal.version ?? "?"}` : "预览"}>
        {internal?.kind === "video" ? <VideoPreview media={internal ?? undefined} /> : <ImagePreview media={internal ?? undefined} alt="版本预览" />}
      </Modal>
      <ConfirmSheet request={confirm} onClose={() => setConfirm(null)} />
    </div>
  );
}
