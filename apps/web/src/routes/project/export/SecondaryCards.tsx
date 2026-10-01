import type { ReactNode } from "react";
import { Clock, Download, Layers, Share2, TriangleAlert } from "lucide-react";
import { Button } from "../../../components/ui";
import type { ExportFile } from "../../../lib/api";
import type { AssetBundle } from "./utils";
import { fmtBytes } from "../../../lib/utils";

/**
 * 三张次级卡：源素材包 / 分享项目 / 渲染日志。
 * 共同点：动作都在本机后端或本机浏览器完成，结果与失败原因都就地显示，不弹提示就走。
 */
export function SecondaryCard({
  icon,
  title,
  desc,
  children,
}: {
  icon: ReactNode;
  title: string;
  desc: ReactNode;
  children?: ReactNode;
}) {
  return (
    <section className="flex flex-col gap-3 rounded-panel border border-rule-soft bg-panel/70 p-4">
      <div className="flex items-start gap-2.5">
        <span className="mt-0.5 grid h-8 w-8 flex-none place-items-center rounded-ctl bg-chrome/10 text-chrome">{icon}</span>
        <div className="min-w-0">
          <h3 className="text-body font-semibold text-ink">{title}</h3>
          <p className="mt-0.5 text-note leading-snug text-ink-mute">{desc}</p>
        </div>
      </div>
      <div className="mt-auto space-y-2">{children}</div>
    </section>
  );
}

/** 产物结果行：下载链接 + 模式/体积说明；失败时原样贴后端给的中文错误 */
export function ResultLine({ file, note }: { file: ExportFile; note?: string }) {
  return (
    <div className="space-y-1 rounded-ctl border border-state-ok/35 bg-state-ok/10 px-2 py-1.5">
      <div className="flex flex-wrap items-center gap-2">
        <a
          href={file.url}
          download
          className="inline-flex items-center gap-1.5 text-note text-state-ok hover:underline"
        >
          <Download className="h-3 w-3" />
          下载（{fmtBytes(file.bytes)}）
        </a>
        <span className="mono text-caption text-ink-mute">media {file.mediaId}</span>
      </div>
      {note && <p className="text-caption leading-snug text-ink-dim">{note}</p>}
    </div>
  );
}

export function ErrorLine({ message }: { message: string }) {
  return (
    <div className="flex items-start gap-2 rounded-ctl border border-state-fail/40 bg-state-fail/10 px-2 py-1.5">
      <TriangleAlert className="mt-0.5 h-3.5 w-3.5 flex-none text-state-fail" />
      <p className="text-note leading-snug text-state-fail">{message}</p>
    </div>
  );
}

export function SourceAssetsCard({
  bundle,
  busy,
  result,
  error,
  onRun,
}: {
  bundle: AssetBundle;
  busy: boolean;
  result?: ExportFile;
  error?: string;
  onRun: () => void;
}) {
  const { counts, items, missing } = bundle;
  return (
    <SecondaryCard
      icon={<Layers className="h-4 w-4" />}
      title="源素材包 Source Assets"
      desc="把定妆图、服装变体、场景图、首尾帧与成片按目录树打成一个 ZIP，给外包、备份或换机器用。"
    >
      <ul className="mono space-y-0.5 text-caption text-ink-mute">
        <li>characters/ <span className="text-ink-dim">{counts.characters}</span></li>
        <li>scenes/ <span className="text-ink-dim">{counts.scenes}</span></li>
        <li>shots/ <span className="text-ink-dim">{counts.shots}</span></li>
        <li>videos/ <span className="text-ink-dim">{counts.videos}</span></li>
      </ul>
      <Button size="sm" className="w-full" loading={busy} disabled={!items.length} onClick={onRun}>
        {items.length ? `打包 ${items.length} 个文件` : "没有可打包的产物"}
      </Button>
      {missing.length > 0 && (
        <p className="text-caption leading-snug text-ink-mute">
          有 <span className="mono text-ink-dim">{missing.length}</span> 个媒体 id 在本地索引里找不到记录（{missing.slice(0, 3).join("、")}
          {missing.length > 3 ? " 等" : ""}），它们不会进包。
        </p>
      )}
      {result && <ResultLine file={result} note={`按 ${items.length} 个目录条目请求打包；后端解析不到文件的条目会跳过，不会造一个空文件占位。`} />}
      {error && <ErrorLine message={error} />}
    </SecondaryCard>
  );
}

export function ShareProjectCard({
  busy,
  info,
  error,
  onRun,
}: {
  busy: boolean;
  info: { fileName: string; bytes: number; media: number; skippedBytes: number } | null;
  error?: string;
  onRun: () => void;
}) {
  return (
    <SecondaryCard
      icon={<Share2 className="h-4 w-4" />}
      title="分享项目 Share Project"
      desc="导出项目 JSON（剧本、分镜、全部提示词与媒体索引）。A 方案下项目实体只存在这台浏览器的 IndexedDB 里，服务端不持有它 —— 换机器、换浏览器只有这一条路：导出文件，在另一台机器的首页导入。"
    >
      <Button size="sm" className="w-full" loading={busy} onClick={onRun}>
        导出项目 JSON
      </Button>
      {info && (
        <div className="space-y-1 rounded-ctl border border-state-ok/35 bg-state-ok/10 px-2 py-1.5">
          <div className="flex flex-wrap items-center gap-2">
            <span className="mono text-caption text-ink-dim">{info.fileName}</span>
          </div>
          <p className="text-caption leading-snug text-ink-dim">
            含 <span className="mono">{info.media}</span> 条媒体索引 · 文件约 {fmtBytes(info.bytes)}
            {info.skippedBytes > 0 ? ` · 本地上传的参考图有 ${fmtBytes(info.skippedBytes)} 因为超过 48MB 预算没内嵌` : ""}
          </p>
          <p className="text-caption leading-snug text-ink-mute">把这份文件拷到另一台机器，在仪表盘点「导入项目」即可接着做。</p>
        </div>
      )}
      {error && <ErrorLine message={error} />}
    </SecondaryCard>
  );
}

export function RenderLogsCard({ total, failed, onOpen }: { total: number; failed: number; onOpen: () => void }) {
  return (
    <SecondaryCard
      icon={<Clock className="h-4 w-4" />}
      title="渲染日志 Render Logs"
      desc="按时间倒序回看每一次生成：资源名、用的模型、耗时、状态与错误，能只看失败项。"
    >
      <Button size="sm" variant="quiet" className="w-full" onClick={onOpen}>
        查看 {total} 条记录{failed > 0 ? ` · ${failed} 失败` : ""}
      </Button>
      <p className="text-caption leading-snug text-ink-mute">
        日志记在项目数据里，只留最近 200 条生成任务；导出动作的失败不写这里，直接显示在对应按钮下面。
      </p>
    </SecondaryCard>
  );
}
