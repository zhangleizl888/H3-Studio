import { useEffect, useState } from "react";
import { MonitorPlay, SkipBack, SkipForward } from "lucide-react";
import { Badge, MediaFrame, Modal, Spinner } from "../../../components/ui";
import { useApi } from "../../../lib/apiClient";
import type { Media } from "../../../lib/types";
import { cn, fmtBytes, fmtDur } from "../../../lib/utils";
import type { SequenceEntry } from "./utils";
import { tc } from "./utils";

/**
 * 逐镜预览：播的是真产物（media.url 解析出来的 objectURL / 后端读回的流），
 * 不是程序化占位图。解析不到文件时如实说清原因，不放一段假视频。
 */
export function PreviewModal({
  open,
  onClose,
  entries,
  startIndex,
  mediaById,
}: {
  open: boolean;
  onClose: () => void;
  entries: SequenceEntry[];
  startIndex: number;
  mediaById: Map<string, Media>;
}) {
  const api = useApi();
  const [idx, setIdx] = useState(0);
  const [src, setSrc] = useState<string | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "missing">("loading");
  const [reason, setReason] = useState<string>("");

  const entry = entries[idx];
  const media = entry?.mediaId ? (mediaById.get(entry.mediaId) ?? null) : null;

  useEffect(() => {
    if (open) setIdx(Math.min(Math.max(0, startIndex), Math.max(0, entries.length - 1)));
  }, [open, startIndex, entries.length]);

  useEffect(() => {
    if (!open) return;
    const m = entries[idx]?.mediaId ? mediaById.get(entries[idx].mediaId!) : null;
    if (!m) {
      setSrc(null);
      setState("missing");
      setReason("这一镜在本地媒体索引里没有记录，读不到文件。");
      return;
    }
    let alive = true;
    setState("loading");
    setSrc(null);
    void api.media
      .url(m)
      .then((u) => {
        if (!alive) return;
        setSrc(u);
        setState(u ? "ready" : "missing");
        setReason(
          u
            ? ""
            : m.path.startsWith("idb:")
              ? "这是浏览器本地上传的文件，预览要走后端读盘；上传的原图仍在 IndexedDB 里，合并导出时后端拿不到它。"
              : "后端没能把这个媒体读回来（文件不在磁盘上，或演示模式没有真文件）。",
        );
      })
      .catch((e: unknown) => {
        if (!alive) return;
        setSrc(null);
        setState("missing");
        setReason(`读取失败：${e instanceof Error ? e.message : String(e)}`);
      });
    return () => {
      alive = false;
    };
  }, [api, entries, idx, mediaById, open]);

  if (!open) return null;

  return (
    <Modal
      open={open}
      onClose={onClose}
      width={920}
      title={
        <span className="flex items-center gap-2">
          <MonitorPlay className="h-4 w-4 text-chrome" />
          视频预览
          <span className="label-mono rounded-full border border-chrome/25 bg-chrome/10 px-2 py-0.5 text-chrome">
            Shot {idx + 1} / {entries.length}
          </span>
        </span>
      }
      footer={
        <>
          <div className="mr-auto flex items-center gap-1.5">
            <button
              type="button"
              onClick={() => setIdx((i) => Math.max(0, i - 1))}
              disabled={idx === 0}
              aria-label="上一镜"
              className="rounded-ctl border border-rule p-1.5 text-ink-dim hover:bg-raised disabled:opacity-40"
            >
              <SkipBack className="h-3.5 w-3.5" />
            </button>
            <button
              type="button"
              onClick={() => setIdx((i) => Math.min(entries.length - 1, i + 1))}
              disabled={idx >= entries.length - 1}
              aria-label="下一镜"
              className="rounded-ctl border border-rule p-1.5 text-ink-dim hover:bg-raised disabled:opacity-40"
            >
              <SkipForward className="h-3.5 w-3.5" />
            </button>
            <span className="mono ml-1 text-caption text-ink-mute">
              {entry ? `${tc(entry.inSec)} → ${tc(entry.outSec)}` : "—"}
            </span>
          </div>
          <span className="mono text-caption text-ink-mute">{media ? `${media.width ?? "?"}×${media.height ?? "?"} · ${fmtDur(media.durationMs)}` : ""}</span>
        </>
      }
    >
      {!entry ? (
        <p className="py-10 text-center text-note text-ink-mute">没有可预览的镜头。</p>
      ) : (
        <div className="space-y-3">
          <div className="relative flex aspect-video items-center justify-center overflow-hidden rounded-panel border border-rule-soft bg-void">
            {state === "loading" && (
              <span className="flex items-center gap-2 text-note text-ink-mute">
                <Spinner className="h-4 w-4 text-chrome" /> 正在读取产物文件
              </span>
            )}
            {state === "missing" && (
              <div className="w-full space-y-2 p-4">
                <MediaFrame seedText={entry.mediaId ?? entry.shot.id} kind="video" label="无本地文件" />
                <p className="text-note leading-snug text-ink-mute">{reason}</p>
              </div>
            )}
            {state === "ready" && src && (
              <video
                key={entry.shot.id}
                src={src}
                className="h-full w-full object-contain"
                controls
                autoPlay
                playsInline
                onError={() => {
                  setState("missing");
                  setReason("文件读到了但解不开：可能是编码不被这个浏览器支持，换 Chrome/Edge 或用合并后的 MP4 再看。");
                }}
                onEnded={() => setIdx((i) => Math.min(entries.length - 1, i + 1))}
              />
            )}
          </div>

          <div className="space-y-1">
            <div className="flex flex-wrap items-center gap-2">
              <span className="mono text-body text-ink">镜 {entry.label}</span>
              <Badge>{entry.sec.toFixed(1)}s</Badge>
              {entry.shot.cameraMovement && <Badge>{entry.shot.cameraMovement}</Badge>}
              {entry.shot.shotSize && <Badge>{entry.shot.shotSize}</Badge>}
              {media && <Badge tone="good">{fmtBytes(media.bytes)}</Badge>}
            </div>
            <p className="text-note leading-snug text-ink-dim">{entry.shot.action || "（这一镜没有动作描述）"}</p>
            {entry.shot.dialogue && <p className="quote-bar text-note leading-snug text-ink-mute">{entry.shot.dialogue}</p>}
          </div>

          <div className="space-y-1.5">
            <span className="label">跳到某一镜</span>
            <div className="flex flex-wrap gap-1">
              {entries.map((e, i) => (
                <button
                  key={e.shot.id}
                  type="button"
                  onClick={() => setIdx(i)}
                  className={cn(
                    "mono rounded-ctl border px-1.5 py-0.5 text-caption",
                    i === idx ? "border-chrome/40 bg-chrome/15 text-chrome" : "border-rule-soft text-ink-mute hover:bg-raised",
                  )}
                >
                  {e.label}
                </button>
              ))}
            </div>
          </div>
        </div>
      )}
    </Modal>
  );
}
