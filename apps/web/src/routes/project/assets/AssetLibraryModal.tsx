/**
 * 资产库弹窗：跨项目的角色/场景，搜索 + 类型筛选 + 点选导入 / 替换。
 *
 * 数据来自 localStores 的 STORE_ASSETS（不是后端）—— 资产是创作数据，
 * 按 A 方案留在浏览器里，所以这里直接 await 那几个函数，不进 react-query。
 */

import { useEffect, useMemo, useState } from "react";
import { Archive, MapPin, Search, Trash2, Users } from "lucide-react";
import { Badge, Button, Input, Skeleton } from "../../../components/ui";
import { deleteAsset, listAssets } from "../../../lib/localStores";
import type { AssetLibraryItem, Media } from "../../../lib/types";
import { ago, cn } from "../../../lib/utils";
import { MediaImage, Sheet } from "./common";

export type LibraryFilter = "all" | "character" | "scene";

export interface AssetLibraryModalProps {
  filter: LibraryFilter;
  /** 非空表示「替换模式」：点资产是把目标角色换掉，不是追加 */
  replaceTarget: { characterId: string; characterName: string } | null;
  mediaById: Map<string, Media>;
  onFilterChange: (f: LibraryFilter) => void;
  onImport: (item: AssetLibraryItem) => void;
  onReplace: (item: AssetLibraryItem) => void;
  onPreview: (media: Media, title: string) => void;
  onClose: () => void;
}

export function AssetLibraryModal({ filter, replaceTarget, mediaById, onFilterChange, onImport, onReplace, onPreview, onClose }: AssetLibraryModalProps) {
  const [items, setItems] = useState<AssetLibraryItem[] | null>(null);
  const [query, setQuery] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [armed, setArmed] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    listAssets()
      .then((rows) => alive && setItems(rows))
      .catch((e: unknown) => alive && setError(e instanceof Error ? e.message : String(e)));
    return () => {
      alive = false;
    };
  }, []);

  const filtered = useMemo(() => {
    const rows = items ?? [];
    const q = query.trim().toLowerCase();
    return rows.filter((r) => {
      if (filter !== "all" && r.type !== filter) return false;
      if (!q) return true;
      return `${r.name} ${r.originProjectName ?? ""}`.toLowerCase().includes(q);
    });
  }, [items, filter, query]);

  /** 优先用本项目已登记的媒体行（能拿到真实 path）；跨项目的资产就按 id 让后端读 */
  function mediaOf(item: AssetLibraryItem): Media | undefined {
    const id = (item.character?.refMediaIds ?? item.scene?.refMediaIds ?? [])[0];
    if (!id) return undefined;
    return (
      mediaById.get(id) ?? {
        id,
        kind: "image",
        role: item.type === "character" ? "character" : "scene",
        path: "",
        projectId: item.originProjectId,
        createdAt: item.updatedAt,
      }
    );
  }

  async function remove(item: AssetLibraryItem) {
    try {
      await deleteAsset(item.id);
      setItems((prev) => (prev ?? []).filter((r) => r.id !== item.id));
      setArmed(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  return (
    <Sheet
      open
      onClose={onClose}
      width={980}
      eyebrow={`Assets · ${(items ?? []).length} 项`}
      title={replaceTarget ? `从资产库替换「${replaceTarget.characterName}」` : "资产库"}
    >
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <div className="relative min-w-[220px] flex-1">
          <Search className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-ink-mute" />
          <Input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="搜索资产名称…" className="h-8 w-full pl-8" aria-label="搜索资产" />
        </div>
        <div className="flex items-center gap-1.5" role="group" aria-label="按类型筛选">
          {(["all", "character", "scene"] as const).map((t) => (
            <button
              key={t}
              type="button"
              aria-pressed={filter === t}
              onClick={() => onFilterChange(t)}
              className={cn(
                "rounded-ctl border px-2.5 py-1.5 text-caption font-semibold transition-colors",
                filter === t ? "border-transparent bg-chrome text-chrome-ink" : "border-hairline bg-sheen text-ink-mute hover:text-ink",
              )}
            >
              {t === "all" ? "全部" : t === "character" ? "角色" : "场景"}
            </button>
          ))}
        </div>
      </div>

      {error && <p className="mb-3 text-note text-state-fail">资产库操作失败：{error}</p>}

      {!items && !error ? (
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-52 w-full" />
          ))}
        </div>
      ) : filtered.length === 0 ? (
        <div className="rounded-panel border border-dashed border-hairline px-6 py-12 text-center text-note text-ink-mute">
          {items?.length ? "没有匹配的资产，换个关键词或切筛选。" : "资产库还是空的。在角色卡或场景卡上点「加入资产库」，就能在别的项目里直接复用。"}
        </div>
      ) : (
        <ul className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {filtered.map((item) => {
            const media = mediaOf(item);
            const usable = item.type === "character" || !replaceTarget;
            return (
              <li key={item.id} className="glass flex flex-col gap-2 rounded-panel p-2.5">
                <MediaImage
                  media={media}
                  seedText={item.id}
                  alt={item.name}
                  aspect="16/9"
                  emptyLabel={item.type === "character" ? "无参考图" : "无场景图"}
                  onClick={media ? () => onPreview(media, `${item.name} · 资产库预览`) : undefined}
                />
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <div className="truncate text-body font-semibold">{item.name || "未命名"}</div>
                    <div className="label-mono mt-0.5 flex items-center gap-1.5">
                      {item.type === "character" ? <Users className="h-3 w-3" /> : <MapPin className="h-3 w-3" />}
                      {item.type === "character" ? "角色" : "场景"}
                      {item.originProjectName && <span className="truncate normal-case tracking-normal">· {item.originProjectName}</span>}
                    </div>
                  </div>
                  <Badge className="flex-none">{ago(item.updatedAt)}</Badge>
                </div>
                <div className="mt-auto flex items-center gap-1.5">
                  <Button
                    variant="primary"
                    size="sm"
                    className="flex-1"
                    disabled={!usable}
                    title={usable ? (replaceTarget ? "用这个资产覆盖当前角色" : "复制一份到本项目") : "替换模式只能选角色资产"}
                    onClick={() => (replaceTarget ? onReplace(item) : onImport(item))}
                  >
                    {replaceTarget ? "替换当前角色" : "导入到当前项目"}
                  </Button>
                  {armed === item.id ? (
                    <Button size="sm" variant="danger" title="再点一次就真的从资产库删掉" onClick={() => void remove(item)}>
                      确认删除
                    </Button>
                  ) : (
                    <Button size="sm" variant="ghost" aria-label={`从资产库删除 ${item.name}`} title="从资产库删除（不影响已导入的项目）" onClick={() => setArmed(item.id)} icon={<Trash2 className="h-3 w-3" />} />
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      )}

      <p className="mt-4 flex items-start gap-1.5 text-note leading-snug text-ink-mute">
        <Archive className="mt-0.5 h-3.5 w-3.5 flex-none" />
        资产库存的是定义 + 参考图的引用。原项目删掉之后，那些图的归属就没人认领了，所以要先入库再删项目。
      </p>
    </Sheet>
  );
}
