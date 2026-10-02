import { useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { CircleCheck, Download, FileVideo, Film, Play } from "lucide-react";
import { Badge, Button, Empty, Panel } from "../../components/ui";
import { useApi } from "../../lib/apiClient";
import { renderProgress } from "../../lib/generate";
import { useExportMutations, useMedia, useProject } from "../../lib/hooks";
import { flushSaves } from "../../lib/localStores";
import { useProjectReconcile } from "../../lib/useGenerate";
import { cn, fmtTime } from "../../lib/utils";
import { MasterCard } from "./export/MasterCard";
import { PreviewModal } from "./export/PreviewModal";
import { RenderLogsModal } from "./export/RenderLogsModal";
import { ErrorLine, RenderLogsCard, ResultLine, ShareProjectCard, SourceAssetsCard } from "./export/SecondaryCards";
import { SequenceMap } from "./export/SequenceMap";
import { assetBundle, downloadText, edlRows, mergeModeNote, sequenceEntries, sortedLogs, tc } from "./export/utils";

/**
 * /p/:id/export —— 制片导出。
 *
 * 这一页只处理「已经落盘的东西」：时间轴上每一块都对应一条真实的媒体记录，
 * 缺片的地方就画成缺口，合并/打包/导时间轴都只带得走的部分，不假装整片已经齐了。
 */

const errOf = (e: unknown) => (e instanceof Error ? e.message : String(e));
const safeFile = (s: string) => s.replace(/[\\/:*?"<>|\s]+/g, "_").replace(/^_+|_+$/g, "") || "master";

export default function Export() {
  const { id } = useParams<{ id: string }>();
  const api = useApi();
  const { data: project, error: projectErr } = useProject(id);
  const { data: mediaRows } = useMedia(id);
  const exp = useExportMutations(id ?? "");
  useProjectReconcile(project);

  const [previewOpen, setPreviewOpen] = useState(false);
  const [previewIndex, setPreviewIndex] = useState(0);
  const [logsOpen, setLogsOpen] = useState(false);
  const [shareBusy, setShareBusy] = useState(false);
  const [shareInfo, setShareInfo] = useState<{ fileName: string; bytes: number; media: number; skippedBytes: number } | null>(null);
  const [shareErr, setShareErr] = useState<string | null>(null);
  const [timeline, setTimeline] = useState<{ format: string; fileName: string; chars: number } | null>(null);

  const mediaById = useMemo(() => new Map((mediaRows ?? []).map((m) => [m.id, m] as const)), [mediaRows]);
  const entries = useMemo(() => sequenceEntries(project, mediaById), [project, mediaById]);
  const withVideo = useMemo(() => entries.filter((e) => e.mediaId && e.media), [entries]);
  const bundle = useMemo(() => assetBundle(project, mediaById), [project, mediaById]);
  const logs = useMemo(() => sortedLogs(project), [project]);
  const shotLabelById = useMemo(() => new Map(entries.map((e) => [e.shot.id, e.label] as const)), [entries]);
  const prog = useMemo(
    () => (project ? renderProgress(project) : { done: 0, total: 0, percent: 0, estSeconds: 0, targetSeconds: 0 }),
    [project],
  );
  const totalSec = entries.reduce((a, e) => a + e.sec, 0);
  const logsFailed = logs.filter((l) => l.status === "failed").length;

  function openPreview(at: number) {
    const atMedia = entries.slice(0, at + 1).filter((e) => e.mediaId && e.media).length - 1;
    setPreviewIndex(Math.max(0, atMedia));
    setPreviewOpen(true);
  }

  function runMerge() {
    if (!project || !withVideo.length) return;
    exp.merge.mutate({ mediaIds: withVideo.map((e) => e.mediaId!).filter((x): x is string => !!x), title: project.name });
  }

  function runPack() {
    if (!project || !bundle.items.length) return;
    exp.pack.mutate({ items: bundle.items, title: project.name });
  }

  function runTimeline(kind: "edl" | "xml") {
    if (!project || !entries.length) return;
    const rows = edlRows(project, entries);
    const done = (format: string, text: string) => {
      const ext = format === "xml" ? "xml" : "edl";
      const fileName = `${safeFile(project.name)}.${ext}`;
      downloadText(fileName, text, format === "xml" ? "application/xml" : "text/plain");
      setTimeline({ format, fileName, chars: text.length });
    };
    if (kind === "edl") exp.edl.mutate({ shots: rows, title: project.name }, { onSuccess: (r) => done(r.format, r.text) });
    else exp.xml.mutate({ shots: rows, title: project.name }, { onSuccess: (r) => done(r.format, r.text) });
  }

  async function runShare() {
    if (!id || !project) return;
    setShareBusy(true);
    setShareErr(null);
    try {
      // 导出前先把防抖里压着的编辑冲回 IDB，否则导出去的是 1 秒前的项目
      await flushSaves();
      // withUploads=true：48MB 预算内的本地上传一起内嵌，超出的由本地导出函数记进 skippedBytes
      const json = await api.projects.export(id, true);
      const fileName = `${safeFile(project.name)}.h3studio.project.json`;
      downloadText(fileName, json, "application/json");
      let media = 0;
      let skippedBytes = 0;
      try {
        const parsed = JSON.parse(json) as { media?: unknown[]; skippedBytes?: number };
        media = parsed.media?.length ?? 0;
        skippedBytes = parsed.skippedBytes ?? 0;
      } catch {
        /* 解析不动就只报体积，不猜条数 */
      }
      setShareInfo({ fileName, bytes: new Blob([json]).size, media, skippedBytes });
    } catch (e) {
      setShareInfo(null);
      setShareErr(errOf(e));
    } finally {
      setShareBusy(false);
    }
  }

  const mergeIds = withVideo.map((e) => e.label).join("、");
  const skippedIds = entries.filter((e) => !(e.mediaId && e.media)).map((e) => e.label);
  const timelineErr = exp.edl.error ?? exp.xml.error;

  return (
    <div className="space-y-6 p-4 md:p-8">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <span className="grid h-9 w-9 place-items-center rounded-panel bg-chrome/10 text-chrome">
            <Film className="h-[18px] w-[18px]" />
          </span>
          <div>
            <h1 className="flex items-center gap-2 text-heading font-bold leading-tight tracking-tight">
              制片导出
              <span className="label-mono rounded-full border border-rule bg-raised px-2 py-0.5">Rendering &amp; Export</span>
            </h1>
            <p className="text-note text-ink-mute">整片时间轴、合并导出与交付文件。所有动作只读已落盘的产物。</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <span className="label-mono">Status</span>
          <Badge tone={project ? "good" : "neutral"} className="inline-flex items-center gap-1">
            <CircleCheck className="h-3 w-3" />
            {project ? `已保存 ${fmtTime(project.updatedAt)}` : "读不到项目"}
          </Badge>
        </div>
      </header>

      {projectErr && <ErrorLine message={`读不到项目：${errOf(projectErr)}`} />}

      {project && entries.length === 0 ? (
        <Panel title="还没有时间轴">
          <Empty
            title="这个项目里没有镜头，导出无从下手"
            hint="先去剧本页拆解出角色与场景，再在 AI 工作台生成分镜和镜头视频；有产物的镜头会自动出现在这条时间轴上。"
            action={
              <Link to={`/p/${id}/director`} className="text-note text-chrome hover:underline">
                去 AI 工作台排镜头 →
              </Link>
            }
          />
        </Panel>
      ) : (
        <>
          <MasterCard
            name={project?.name ?? "读取中"}
            shots={prog.total}
            estSec={prog.estSeconds}
            targetSec={prog.targetSeconds}
            percent={prog.percent}
            done={prog.done}
            total={prog.total}
            aspectRatio={project?.config.aspectRatio ?? "—"}
            fullQuality={project?.config.resolutionMode === "full"}
          />

          <SequenceMap entries={entries} totalSec={totalSec} onSelect={openPreview} />

          <div className="grid gap-3 lg:grid-cols-3">
            <button
              type="button"
              onClick={() => openPreview(entries.length - 1)}
              disabled={withVideo.length === 0}
              className={cn(
                "flex h-12 items-center justify-center gap-2 rounded-panel border text-body font-bold tracking-wide transition-colors",
                withVideo.length
                  ? "border-transparent bg-gradient-to-r from-chrome to-chrome-2 text-chrome-ink hover:brightness-110"
                  : "cursor-not-allowed border-rule-soft bg-raised/40 text-ink-mute",
              )}
            >
              <Play className="h-4 w-4" />
              预览视频 PREVIEW VIDEO ({withVideo.length}/{entries.length})
            </button>

            <div className="space-y-2">
              <Button
                className="h-12 w-full rounded-panel text-body tracking-wide"
                icon={<Download className="h-4 w-4" />}
                loading={exp.merge.isPending}
                disabled={withVideo.length === 0}
                onClick={runMerge}
              >
                {exp.merge.isPending ? "后端合成中" : "合并导出 DOWNLOAD MASTER (.MP4)"}
              </Button>
              {withVideo.length === 0 ? (
                <p className="text-caption leading-snug text-ink-mute">还没有任何一段成片，合并列表是空的。</p>
              ) : (
                <p className="text-caption leading-snug text-ink-mute">
                  {prog.percent < 100 ? (
                    <span className="text-ink-dim">
                      部分导出：只有 <span className="mono">{withVideo.length}</span>/{entries.length} 镜有产物，拼出来的是这一段的部分片。
                    </span>
                  ) : (
                    <span>整片 {entries.length} 段按镜号顺序首尾相接，总长 {tc(totalSec)}。</span>
                  )}
                  <span className="mono block truncate">参与：{mergeIds || "—"}</span>
                  {skippedIds.length > 0 && <span className="mono block truncate">跳过：{skippedIds.join("、")}</span>}
                </p>
              )}
            </div>

            <div className="space-y-2">
              <div className="grid h-12 grid-cols-2 gap-2">
                <Button className="w-full" icon={<FileVideo className="h-4 w-4" />} loading={exp.edl.isPending} disabled={!entries.length} onClick={() => runTimeline("edl")}>
                  导出 EDL
                </Button>
                <Button className="w-full" loading={exp.xml.isPending} disabled={!entries.length} onClick={() => runTimeline("xml")}>
                  导出 XML
                </Button>
              </div>
              <p className="text-caption leading-snug text-ink-mute">
                CMX3600 EDL 与 FCP7 XML（剪映 / Premiere / Resolve 都能导入）。时间轴文本由后端按这里给的镜头顺序与时长生成，浏览器直接存成文件。
              </p>
            </div>
          </div>

          <div className="space-y-3">
            {exp.merge.data && (
              <Panel title="合并结果" dense>
                <div className="space-y-2 p-3">
                  <ResultLine file={exp.merge.data} note={mergeModeNote(exp.merge.data.mode)} />
                  <p className="text-caption leading-snug text-ink-mute">
                    合成了 <span className="mono text-ink-dim">{exp.merge.data.segments ?? withVideo.length}</span> 段 ·{" "}
                    {exp.merge.data.mode === "reencode" ? "重编码走的是 CPU 上的编码器，不占生成实例的显存" : "串流复制不重编码，所以秒级完成"}。
                    文件落在后端 media_root/exports/{id ?? "项目"}/ 下，并在服务端媒体表登记了一条 role=export 的记录；
                    本机的媒体索引要重新拉取才会看到它。
                  </p>
                </div>
              </Panel>
            )}
            {exp.merge.error && (
              <ErrorLine message={`合并导出失败：${errOf(exp.merge.error)}`} />
            )}

            {timeline && (
              <Panel title="时间轴文件" dense>
                <p className="p-3 text-note leading-snug text-ink-dim">
                  已生成 <span className="mono text-ink">{timeline.fileName}</span>（{timeline.format.toUpperCase()}，{timeline.chars} 字符）并开始下载。
                  文件里是整条时间轴的镜头顺序与入出点，不含任何像素数据。
                </p>
              </Panel>
            )}
            {timelineErr && <ErrorLine message={`时间轴导出失败：${errOf(timelineErr)}`} />}
          </div>

          <div className="grid gap-4 lg:grid-cols-3">
            <SourceAssetsCard
              bundle={bundle}
              busy={exp.pack.isPending}
              result={exp.pack.data}
              error={exp.pack.error ? errOf(exp.pack.error) : undefined}
              onRun={runPack}
            />
            <ShareProjectCard busy={shareBusy} info={shareInfo} error={shareErr ?? undefined} onRun={() => void runShare()} />
            <RenderLogsCard total={logs.length} failed={logsFailed} onOpen={() => setLogsOpen(true)} />
          </div>
        </>
      )}

      <PreviewModal open={previewOpen} onClose={() => setPreviewOpen(false)} entries={withVideo} startIndex={previewIndex} mediaById={mediaById} />
      <RenderLogsModal open={logsOpen} onClose={() => setLogsOpen(false)} logs={logs} shotLabelById={shotLabelById} />
    </div>
  );
}
