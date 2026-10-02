/**
 * 「生成回收站」：被删掉的版本在界面上只剩这里这一份。
 *
 * 三条话术是这个页面存在的意义，都必须是真的：
 *  - 满保留期（默认 100 天）后端自动删文件，不等人来点
 *  - 预览能点开 —— 后端对软删行的读接口带 ?trashed=1 才放行
 *  - 「彻底删除」必须先进过回收站：没有一步到位的删除
 */

import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { RotateCcw, Trash2 } from "lucide-react";
import { Badge, Button, Empty, FilterChip, Modal, Panel, Select, Skeleton } from "../components/ui";
import { ImagePreview, VideoPreview } from "./project/director/common";
import { ConfirmSheet, type ConfirmRequest } from "./project/assets/common";
import { useProjects, useTrash, useVersionMutations } from "../lib/hooks";
import { fmtBytes } from "../lib/utils";
import type { TrashItem, VersionBucket } from "../lib/types";

const BUCKETS: { key: VersionBucket | "all"; label: string }[] = [
  { key: "all", label: "全部" },
  { key: "script", label: "生成脚本" },
  { key: "image", label: "生成图片" },
  { key: "video", label: "生成视频" },
];
const ALL = "__all__";

export default function Trash() {
  const [params, setParams] = useSearchParams();
  const scope = params.get("project") ?? ALL;
  const bucket = (params.get("bucket") as (typeof BUCKETS)[number]["key"]) ?? "all";
  const { data: projects } = useProjects();
  const projectId = scope === ALL ? null : scope;
  const { data, isLoading } = useTrash({ projectId, bucket: bucket === "all" ? undefined : bucket });
  const muts = useVersionMutations(projectId);
  const [preview, setPreview] = useState<TrashItem | null>(null);
  const [confirm, setConfirm] = useState<ConfirmRequest | null>(null);
  /** 彻底删除是两次点击：第一次只是"armed"，第二次才真删。全应用同一个手势 */
  const [armed, setArmed] = useState<string | null>(null);

  const setParam = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    next.set(key, value);
    setParams(next, { replace: true });
  };

  const items = data?.items ?? [];

  return (
    <div className="space-y-3 p-4">
      <Panel
        title="生成回收站"
        actions={
          <span className="label mono">
            {data ? `${items.length} 项 · ${fmtBytes(data.totalBytes)}` : "…"}
          </span>
        }
      >
        <p className="text-note leading-relaxed text-ink-dim">
          被删掉的剧本、图片、成片都只剩这里这一份。默认保留
          <span className="mono text-ink"> {data?.retentionDays ?? 100} </span>
          天，到期由后端自动删文件（开机扫一趟、之后每 6 小时一趟），不用人守着。
        </p>

        <div className="mt-2.5 flex flex-wrap items-center gap-2">
          {BUCKETS.map((b) => (
            <FilterChip key={b.key} active={bucket === b.key} onClick={() => setParam("bucket", b.key)}>
              {b.label}
            </FilterChip>
          ))}
          <label className="ml-auto flex items-center gap-1.5">
            <span className="label">项目</span>
            <Select value={scope} onChange={(e) => setParam("project", e.target.value)} aria-label="选择项目">
              <option value={ALL}>全部项目</option>
              {(projects ?? []).map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </Select>
          </label>
        </div>
      </Panel>

      {isLoading && <Skeleton className="h-32" />}

      {!isLoading && !items.length && (
        <Empty title="回收站是空的" hint="删除某个版本之后它会出现在这里。恢复它就回到原来的位置（不会自动变成当前版）。" />
      )}

      {!!items.length && (
        <Panel>
          <table className="w-full border-collapse text-note">
            <thead>
              <tr className="text-left text-caption text-ink-mute">
                <th className="px-2 py-1.5 font-medium">版本</th>
                <th className="px-2 py-1.5 font-medium">类别</th>
                {scope === ALL && <th className="px-2 py-1.5 font-medium">项目</th>}
                <th className="px-2 py-1.5 font-medium">名称</th>
                <th className="px-2 py-1.5 font-medium">删除于</th>
                <th className="px-2 py-1.5 font-medium">剩余</th>
                <th className="px-2 py-1.5 text-right font-medium">操作</th>
              </tr>
            </thead>
            <tbody>
              {items.map((it) => (
                <tr key={it.key} className="border-t border-hairline align-middle">
                  <td className="px-2 py-1.5">
                    <span className="mono text-ink">V{it.version}</span>
                    <span className="ml-1 text-caption text-ink-mute">/ {it.versionCount}</span>
                  </td>
                  <td className="px-2 py-1.5">
                    <Badge>{it.bucket === "script" ? "脚本" : it.bucket === "video" ? "视频" : "图片"}</Badge>
                  </td>
                  {scope === ALL && (
                    <td className="px-2 py-1.5 text-ink-dim">
                      {/* 项目实体在浏览器里，服务端只认 project_key；名字来自删项目时留下的快照 */}
                      {it.projectName ?? projects?.find((p) => p.id === it.projectKey)?.name ?? <span className="mono text-ink-mute">{it.projectKey ?? "(未归属)"}</span>}
                    </td>
                  )}
                  <td className="max-w-[240px] truncate px-2 py-1.5" title={it.title ?? it.textPreview ?? it.refId ?? ""}>
                    {it.title ?? it.refId ?? (it.textPreview ? `${it.textPreview.slice(0, 28)}…` : "—")}
                  </td>
                  <td className="px-2 py-1.5 text-ink-dim">{it.deletedAt ? new Date(it.deletedAt).toLocaleString() : "—"}</td>
                  <td className="px-2 py-1.5">
                    <span className={(it.daysLeft ?? 0) <= 14 ? "mono text-state-fail" : "mono text-ink-dim"}>{it.daysLeft ?? 0} 天</span>
                  </td>
                  <td className="px-2 py-1.5">
                    <div className="flex items-center justify-end gap-2">
                      <Button size="sm" variant="ghost" onClick={() => setPreview(it)}>
                        预览
                      </Button>
                      <Button
                        size="sm"
                        variant="quiet"
                        icon={<RotateCcw className="h-3 w-3" aria-hidden />}
                        onClick={() =>
                          void (it.kind === "media"
                            ? muts.restoreMedia.mutateAsync({ id: it.id, projectId: it.projectKey })
                            : muts.restoreScript.mutateAsync({ uuid: it.uuid, projectId: it.projectKey })
                          ).catch((e) => console.error("恢复失败：", e))
                        }
                      >
                        恢复
                      </Button>
                      {armed === it.key ? (
                        <Button
                          size="sm"
                          variant="danger"
                          title="再点一次就真的删掉：文件与记录一起没了，没有第二次反悔"
                          onClick={() => {
                            setConfirm({
                              title: `彻底删除 V${it.version}`,
                              confirmLabel: "真的删掉",
                              danger: true,
                              body: (
                                <p className="text-note leading-relaxed">
                                  这一步<span className="text-ink">不回收站、不保留</span>：图片/视频会连磁盘文件一起删，剧本会删掉那一版正文。
                                  要留就点取消。
                                </p>
                              ),
                              onConfirm: () => {
                                void (it.kind === "media"
                                  ? muts.purgeMedia.mutateAsync({ id: it.id, projectId: it.projectKey })
                                  : muts.purgeScript.mutateAsync({ uuid: it.uuid, projectId: it.projectKey })
                                ).catch((e) => console.error("彻底删除失败：", e));
                                setArmed(null);
                                setConfirm(null);
                              },
                            });
                          }}
                        >
                          确认删除
                        </Button>
                      ) : (
                        <Button
                          size="sm"
                          variant="ghost"
                          aria-label={`彻底删除 V${it.version}`}
                          title="彻底删除（先点一次，再点确认）"
                          icon={<Trash2 className="h-3 w-3" aria-hidden />}
                          onClick={() => setArmed(it.key)}
                        />
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="mt-2 text-caption text-ink-mute">
            恢复只让这一版回到历史里，<span className="text-ink">不会自动变成当前版</span> —— 要当当前用，回到「生成历史」再点「设为当前」。
          </p>
        </Panel>
      )}

      <Modal open={!!preview} onClose={() => setPreview(null)} width={880} title={preview ? `V${preview.version} · ${preview.title ?? preview.refId ?? (preview.kind === "script" ? "剧本" : "未打标签的产物")}` : "预览"}>
        {preview?.kind === "script" ? (
          <pre className="max-h-[62vh] overflow-auto whitespace-pre-wrap break-words rounded-ctl bg-inset p-3 text-note leading-relaxed">
            {preview.textPreview || "（这一版没有留下正文）"}
          </pre>
        ) : preview?.media ? (
          preview.media.kind === "video" ? (
            <VideoPreview media={preview.media} />
          ) : (
            <ImagePreview media={preview.media} alt="回收站预览" />
          )
        ) : (
          <p className="py-8 text-center text-note text-ink-mute">这一项没带回可显示的产物数据：列表里的行只有记录，文件要么在本机库里读不出，要么这条本来就不是图片/成片。</p>
        )}
      </Modal>

      <ConfirmSheet request={confirm} onClose={() => setConfirm(null)} />
    </div>
  );
}
