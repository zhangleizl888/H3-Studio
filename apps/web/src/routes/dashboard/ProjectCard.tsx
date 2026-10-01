import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { Calendar, ChevronRight, Folder, Trash2, TriangleAlert } from "lucide-react";
import type { Project, Stage } from "../../lib/types";
import { Button } from "../../components/ui";
import { useApi } from "../../lib/apiClient";
import { keys } from "../../lib/hooks";

/** project.stage → 界面语言。徽标上出现的必须是这六个词之一 */
export const STAGE_LABEL: Record<Stage, string> = {
  script: "剧本创作",
  manifest: "分镜清单",
  assets: "场景角色",
  director: "AI工作台",
  export: "制片导出",
  prompts: "资产管理",
};

/** 点卡片落到哪一页：manifest 没有独立页面，回剧本页继续拆 */
const STAGE_ROUTE: Record<Stage, string> = {
  script: "script",
  manifest: "script",
  assets: "assets",
  director: "director",
  export: "export",
  prompts: "prompts",
};

export function fmtDate(iso?: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return `${d.getFullYear()}/${String(d.getMonth() + 1).padStart(2, "0")}/${String(d.getDate()).padStart(2, "0")}`;
}

type Counts = { characters: number; scenes: number; shots: number; media: number | null };

/**
 * 一张项目卡。
 *
 * 删除必须二次确认，而且要把「连带删掉多少东西」摆出来 ——
 * 项目实体在浏览器 IndexedDB 里，删了就只能靠导出文件找回。
 */
export function ProjectCard({ project }: { project: Project }) {
  const api = useApi();
  const nav = useNavigate();
  const qc = useQueryClient();
  const [confirming, setConfirming] = useState(false);
  const [counts, setCounts] = useState<Counts | null>(null);
  const [removing, setRemoving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const shots = project.data?.shots ?? [];
  const done = shots.filter((s) => s.videoMediaIds.length > 0 || s.state === "completed").length;

  useEffect(() => {
    if (!confirming) return;
    let alive = true;
    setCounts({ characters: project.data.characters.length, scenes: project.data.scenes.length, shots: shots.length, media: null });
    api.media
      .project(project.id)
      .then((rows) => alive && setCounts((c) => (c ? { ...c, media: rows.length } : c)))
      .catch(() => alive && setCounts((c) => (c ? { ...c, media: -1 } : c)));
    return () => {
      alive = false;
    };
    // 只在打开确认框那一刻取一次数：卡片本身跟着列表刷新，不该反复读盘
  }, [confirming, api, project.id, project.data.characters.length, project.data.scenes.length, shots.length]);

  const remove = async () => {
    setRemoving(true);
    setError(null);
    try {
      await api.projects.remove(project.id);
      await qc.invalidateQueries({ queryKey: keys.projects });
    } catch (e) {
      setError(e instanceof Error ? e.message : "删除失败");
    } finally {
      setRemoving(false);
      setConfirming(false);
    }
  };

  return (
    <article className="group relative flex h-[244px] flex-col overflow-hidden rounded-sheet border border-hairline bg-slate/55 shadow-xl shadow-black/25 backdrop-blur-xl transition-colors hover:border-chrome/40">
      <button
        onClick={() => nav(`/p/${project.id}/${STAGE_ROUTE[project.stage] ?? "script"}`)}
        className="flex min-h-0 flex-1 flex-col items-start gap-2 p-5 text-left"
      >
        <Folder className="h-8 w-8 flex-none text-chrome/30 transition-colors group-hover:text-chrome/75" aria-hidden />
        <h3 className="line-clamp-1 text-subtitle font-semibold tracking-tight text-ink">{project.name}</h3>
        <span className="rounded-full border border-chrome/25 bg-chrome/10 px-2 py-[3px] text-caption text-ink-dim">
          {STAGE_LABEL[project.stage] ?? project.stage}
        </span>
        {project.synopsis ? (
          <p className="quote-bar line-clamp-2 text-note leading-relaxed text-ink-mute">{project.synopsis}</p>
        ) : (
          <p className="quote-bar text-note leading-relaxed text-ink-mute/70">还没有梗概 —— 进剧本页写一句话，拆解时它会当全局上下文。</p>
        )}
        {shots.length > 0 && (
          <div className="mt-auto w-full space-y-1">
            <div className="h-[3px] w-full overflow-hidden rounded-full bg-hairline">
              <div className="h-full bg-gradient-to-r from-chrome to-chrome-2" style={{ width: `${Math.round((done / shots.length) * 100)}%` }} />
            </div>
            <div className="mono text-micro text-ink-mute">
              {done}/{shots.length} 镜已出片
            </div>
          </div>
        )}
      </button>

      <footer className="flex flex-none items-center justify-between border-t border-hairline bg-sheen px-5 py-2.5">
        <span className="label-mono flex items-center gap-1.5">
          <Calendar className="h-3 w-3" aria-hidden />
          {fmtDate(project.updatedAt)}
        </span>
        <ChevronRight className="h-3.5 w-3.5 text-chrome/35 transition-colors group-hover:text-chrome" aria-hidden />
      </footer>

      <button
        onClick={() => setConfirming(true)}
        title="删除项目"
        aria-label={`删除项目 ${project.name}`}
        className="absolute right-3 top-3 rounded-ctl p-1.5 text-ink-mute opacity-0 transition-opacity hover:bg-white/10 hover:text-state-fail focus-visible:opacity-100 group-hover:opacity-100"
      >
        <Trash2 className="h-3.5 w-3.5" aria-hidden />
      </button>

      {confirming && (
        <div className="absolute inset-0 z-20 flex flex-col justify-center gap-3 bg-scrim/95 p-5 backdrop-blur-xl">
          <div className="flex items-center gap-2">
            <TriangleAlert className="h-4 w-4 flex-none text-state-fail" aria-hidden />
            <span className="text-body font-semibold text-ink">永久删除「{project.name}」？</span>
          </div>
          <p className="label-mono">此操作无法撤销 · 项目只存在这台浏览器的 IndexedDB 里</p>
          <ul className="space-y-1 rounded-ctl border border-white/10 bg-white/[0.04] px-3 py-2 text-note text-ink-dim">
            <li className="flex items-baseline justify-between gap-3">
              <span>角色（含服装变体）</span>
              <span className="mono text-ink">{counts ? counts.characters : "—"}</span>
            </li>
            <li className="flex items-baseline justify-between gap-3">
              <span>场景</span>
              <span className="mono text-ink">{counts ? counts.scenes : "—"}</span>
            </li>
            <li className="flex items-baseline justify-between gap-3">
              <span>镜头</span>
              <span className="mono text-ink">{counts ? counts.shots : "—"}</span>
            </li>
            <li className="flex items-baseline justify-between gap-3">
              <span>产物（定妆图 / 关键帧 / 视频）</span>
              <span className="mono text-ink">{mediaCountText(counts?.media)}</span>
            </li>
          </ul>
          <p className="text-caption leading-snug text-ink-mute">
            已存进资产库的角色/场景定义会留下，但它们的参考图会随这个项目一起删除。要留图先导出项目。
          </p>
          {error && <p className="text-caption leading-snug text-state-fail">{error}</p>}
          <div className="flex gap-2">
            <Button variant="quiet" className="flex-1" onClick={() => setConfirming(false)} disabled={removing}>
              取消
            </Button>
            <Button variant="danger" className="flex-1" loading={removing} onClick={() => void remove()}>
              永久删除
            </Button>
          </div>
        </div>
      )}
    </article>
  );
}

/** -1 = 读不到（媒体索引在别的机器上）：宁可说不清，也不给一个假数字 */
function mediaCountText(n: number | null | undefined): string {
  if (n === null || n === undefined) return "统计中…";
  if (n < 0) return "读不到";
  return String(n);
}
