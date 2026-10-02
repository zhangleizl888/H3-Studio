import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { Archive, ArrowRight, Check, MapPin, Search, Trash2, Users, X } from "lucide-react";
import type { AssetLibraryItem, Project } from "../../lib/types";
import { deleteAsset, importAssetIntoProject, listAssets } from "../../lib/localStores";
import { keys } from "../../lib/hooks";
import { Badge, Button, Empty, Input, Modal, Skeleton } from "../../components/ui";
import { cn } from "../../lib/utils";
import { fmtDate } from "./ProjectCard";

type Filter = "all" | "character" | "scene";

const FILTERS: { key: Filter; label: string }[] = [
  { key: "all", label: "全部" },
  { key: "character", label: "角色" },
  { key: "scene", label: "场景" },
];

/**
 * 资产库：跨项目复用的角色/场景定义。
 *
 * 库存在浏览器 IndexedDB 里（和项目同一套），所以这里直接调 localStores，
 * 不绕后端 —— 后端也没有这份数据。
 */
export function AssetLibraryModal({ open, onClose, projects }: { open: boolean; onClose: () => void; projects: Project[] }) {
  const nav = useNavigate();
  const qc = useQueryClient();
  const [items, setItems] = useState<AssetLibraryItem[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<Filter>("all");
  const [confirmId, setConfirmId] = useState<string | null>(null);
  const [target, setTarget] = useState<AssetLibraryItem | null>(null);
  const [importing, setImporting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ asset: string; project: Project } | null>(null);

  const reload = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setItems(await listAssets());
    } catch (e) {
      setError(e instanceof Error ? e.message : "读资产库失败");
      setItems([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (open) void reload();
  }, [open, reload]);

  const shown = (items ?? []).filter((it) => {
    if (filter !== "all" && it.type !== filter) return false;
    const q = query.trim().toLowerCase();
    if (!q) return true;
    return it.name.toLowerCase().includes(q) || (it.originProjectName ?? "").toLowerCase().includes(q);
  });

  const remove = async (id: string) => {
    await deleteAsset(id);
    setConfirmId(null);
    await reload();
  };

  const useInProject = async (project: Project) => {
    if (!target) return;
    setImporting(true);
    setError(null);
    try {
      await importAssetIntoProject(project.id, target.id);
      await qc.invalidateQueries({ queryKey: keys.projects });
      await qc.invalidateQueries({ queryKey: keys.project(project.id) });
      setNotice({ asset: target.name, project });
      setTarget(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "导入失败");
    } finally {
      setImporting(false);
    }
  };

  return (
    <>
      <Modal open={open} onClose={onClose} title={<span className="flex items-center gap-2"><Archive className="h-4 w-4 text-chrome" />资产库<span className="label-mono">Asset Library</span></span>} width={980}>
        <div className="space-y-4">
          <div className="flex flex-wrap items-end justify-between gap-2">
            <p className="text-note leading-snug text-ink-mute">
              在项目的「场景角色」页把角色/场景存进资产库，就能在别的项目里直接复用 —— 连提示词和参考图一起带过去。
            </p>
            <span className="label-mono">{items?.length ?? 0} assets</span>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <div className="relative min-w-[220px] flex-1">
              <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-ink-mute" aria-hidden />
              <Input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="搜资产名称或来源项目…" className="h-8 w-full pl-8" />
            </div>
            <div className="flex items-center gap-2">
              {FILTERS.map((f) => (
                <button
                  key={f.key}
                  onClick={() => setFilter(f.key)}
                  className={cn(
                    "rounded-ctl border px-2.5 py-1.5 text-note transition-colors",
                    filter === f.key ? "border-transparent bg-gradient-to-r from-chrome to-chrome-2 font-semibold text-chrome-ink" : "border-rule bg-raised text-ink-dim hover:text-ink",
                  )}
                >
                  {f.label}
                </button>
              ))}
            </div>
          </div>

          {error && <p className="text-note text-state-fail">{error}</p>}

          {notice && (
            <div className="flex flex-wrap items-center gap-2 rounded-ctl border border-state-ok/40 bg-state-ok/8 px-3 py-2 text-note">
              <Check className="h-3.5 w-3.5 text-state-ok" aria-hidden />
              已把「{notice.asset}」导入项目「{notice.project.name}」
              <button className="ml-auto inline-flex items-center gap-1 text-ink underline hover:text-chrome" onClick={() => nav(`/p/${notice.project.id}/assets`)}>
                去看它 <ArrowRight className="h-3 w-3" />
              </button>
              <button onClick={() => setNotice(null)} aria-label="知道了" className="rounded-ctl p-1 text-ink-mute hover:text-ink">
                <X className="h-3 w-3" />
              </button>
            </div>
          )}

          {loading && !items ? (
            <div className="space-y-2">
              {[0, 1, 2].map((i) => (
                <Skeleton key={i} className="h-[76px] w-full rounded-panel" />
              ))}
            </div>
          ) : shown.length === 0 ? (
            <Empty
              title={items?.length ? "没有匹配的资产" : "资产库还是空的"}
              hint={items?.length ? "换个关键词，或把筛选切回「全部」。" : "进项目的「场景角色」页，角色/场景卡上有一键「存进资产库」。"}
            />
          ) : (
            <ul className="space-y-2">
              {shown.map((it) => (
                <li key={it.id} className="rounded-panel border border-hairline bg-sheen p-3 transition-colors hover:border-chrome/35">
                  <div className="flex flex-wrap items-start gap-3">
                    <span className="grid h-9 w-9 flex-none place-items-center rounded-ctl border border-hairline bg-sheen text-chrome">
                      {it.type === "character" ? <Users className="h-4 w-4" aria-hidden /> : <MapPin className="h-4 w-4" aria-hidden />}
                    </span>
                    <div className="min-w-0 flex-1 space-y-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="text-body font-semibold">{it.name}</span>
                        <Badge>{it.type === "character" ? "角色" : "场景"}</Badge>
                        {it.type === "character" && (it.character?.variations.length ?? 0) > 0 && <Badge>{it.character!.variations.length} 个变体</Badge>}
                      </div>
                      <p className="line-clamp-1 text-note text-ink-mute">{(it.character?.desc ?? it.scene?.desc) || "没有描述"}</p>
                      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-caption text-ink-mute">
                        <span>来源：<span className="text-ink-dim">{it.originProjectName ?? "未知项目"}</span></span>
                        <span className="mono">更新 {fmtDate(it.updatedAt)}</span>
                        <span>参考图 <span className="mono text-ink-dim">{refCount(it)}</span> 张</span>
                        <span>{hasPrompt(it) ? "提示词已写入" : "提示词按外形现拼"}</span>
                      </div>
                    </div>
                    <div className="flex flex-none items-center gap-2">
                      {confirmId === it.id ? (
                        <>
                          <span className="text-caption text-ink-mute">确认删除？</span>
                          <Button size="sm" variant="quiet" onClick={() => setConfirmId(null)}>
                            取消
                          </Button>
                          <Button size="sm" variant="danger" onClick={() => void remove(it.id)}>
                            删除
                          </Button>
                        </>
                      ) : (
                        <>
                          <Button size="sm" onClick={() => setTarget(it)}>
                            选择项目使用
                          </Button>
                          <Button size="sm" variant="quiet" aria-label={`删除资产 ${it.name}`} onClick={() => setConfirmId(it.id)}>
                            <Trash2 className="h-3 w-3" />
                          </Button>
                        </>
                      )}
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      </Modal>

      <Modal
        open={!!target}
        onClose={() => setTarget(null)}
        title={`把「${target?.name ?? ""}」导入哪个项目`}
        width={620}
      >
        {projects.length === 0 ? (
          <Empty title="还没有项目" hint="先回项目库建一个，再来导资产。" />
        ) : (
          <div className="grid gap-2 sm:grid-cols-2">
            {projects.map((p) => (
              <button
                key={p.id}
                disabled={importing}
                onClick={() => void useInProject(p)}
                className="rounded-panel border border-hairline bg-sheen p-3 text-left transition-colors hover:border-chrome/40 hover:bg-sheen disabled:opacity-50"
              >
                <div className="line-clamp-1 text-body font-semibold">{p.name}</div>
                <div className="mono mt-1 text-caption text-ink-mute">
                  {p.data.characters.length} 角色 · {p.data.scenes.length} 场景 · {fmtDate(p.updatedAt)}
                </div>
              </button>
            ))}
          </div>
        )}
        <p className="mt-3 text-caption leading-snug text-ink-mute">
          导入会重编实体 id：两个项目里的同名角色从此各改各的，不会互相牵连。
        </p>
      </Modal>
    </>
  );
}

function refCount(it: AssetLibraryItem): number {
  if (it.type === "character") {
    const c = it.character;
    if (!c) return 0;
    return c.refMediaIds.length + c.variations.reduce((a, v) => a + v.refMediaIds.length, 0);
  }
  return it.scene?.refMediaIds.length ?? 0;
}

function hasPrompt(it: AssetLibraryItem): boolean {
  return it.type === "character" ? !!it.character?.visualPrompt?.trim() : !!it.scene?.visualPrompt?.trim();
}
