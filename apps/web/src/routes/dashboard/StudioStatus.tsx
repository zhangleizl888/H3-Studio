import { useNavigate } from "react-router-dom";
import { CircleAlert, HardDrive, Waypoints } from "lucide-react";
import { Badge, Button, MachChip, Panel, Progress, StateGlyph } from "../../components/ui";
import { useInstances, useJobs, useStorage } from "../../lib/hooks";
import { JOB_KIND_LABEL } from "../../lib/constants";
import { cn, fmtMoney, fmtTime } from "../../lib/utils";

/**
 * 产线状态：项目库的次级信息。
 *
 * 放在项目网格下面，是因为这一屏的主视觉必须是项目本身 ——
 * 但要开工时最先问的仍是「机器还活着吗、队列里压着什么、盘还剩多少」。
 */
export function StudioStatus({ projectId }: { projectId?: string }) {
  const nav = useNavigate();
  const { data: jobs } = useJobs(null);
  const { data: instances } = useInstances();
  const { data: st } = useStorage();

  const live = jobs?.filter((j) => j.state === "running" || j.state === "queued" || j.state === "dispatching") ?? [];
  const failing = jobs?.filter((j) => j.state === "failed") ?? [];
  const up = instances?.filter((i) => i.lastProbeOk).length ?? 0;
  const total = instances?.length ?? 0;
  const used = st ? st.mediaBytes + st.tmpBytes : 0;
  const capacity = used + (st?.freeBytes ?? 0);

  return (
    <section className="space-y-3">
      <div className="flex items-center gap-2">
        <span className="label-mono">Pipeline Status</span>
        <span className="h-px flex-1 bg-hairline" />
      </div>

      <div className="grid gap-3 xl:grid-cols-3">
        <Panel
          title={
            <span className="flex items-center gap-2">
              <Waypoints className="h-3.5 w-3.5" />
              队列概览
              {live.length > 0 && <Badge>{live.length}</Badge>}
            </span>
          }
          actions={
            projectId ? (
              <Button size="sm" variant="quiet" onClick={() => nav(`/p/${projectId}/queue`)}>
                看队列
              </Button>
            ) : (
              <span className="text-caption text-ink-mute">还没有项目</span>
            )
          }
          dense
        >
          {live.length === 0 ? (
            <div className="px-3 py-5 text-center text-note text-ink-mute">产线上没有任务。关掉页面也会继续跑。</div>
          ) : (
            <ul className="divide-y divide-rule-soft">
              {live.slice(0, 4).map((j) => {
                const inst = instances?.find((i) => i.id === j.instanceId);
                return (
                  <li key={j.id} className="space-y-1.5 px-3 py-2">
                    <div className="flex items-baseline gap-2">
                      <StateGlyph state={j.state === "running" ? "running" : "queued"} />
                      <span className="truncate text-note">{j.title}</span>
                      <span className="label flex-none">{JOB_KIND_LABEL[j.kind]}</span>
                      {j.cost?.money != null && <span className="mono ml-auto flex-none text-caption text-mach-rh">{fmtMoney(j.cost.money)}</span>}
                    </div>
                    <Progress
                      value={j.progress.value}
                      max={j.progress.max}
                      unavailable={j.progress.unavailable}
                      stage={j.progress.stage ?? j.progress.nodeTitle}
                      machine={inst ? `var(--color-mach-${inst.placement === "local" ? "local" : inst.placement === "cloud_self" ? "self" : "rh"})` : undefined}
                      stripe={j.state === "running"}
                    />
                  </li>
                );
              })}
              {live.length > 4 && <li className="px-3 py-1.5 text-caption text-ink-mute">另有 {live.length - 4} 条在排队</li>}
            </ul>
          )}
        </Panel>

        <Panel title="实例健康" actions={<span className="mono text-caption text-ink-mute">{up}/{total} 可用</span>} dense>
          {!instances?.length ? (
            <div className="px-3 py-5 text-center text-note text-ink-mute">还没有生成实例</div>
          ) : (
            <ul className="divide-y divide-rule-soft">
              {instances.map((i) => (
                <li key={i.id} className="space-y-0.5 px-3 py-2">
                  <div className="flex items-center justify-between gap-2">
                    <MachChip placement={i.placement} label={i.name} />
                    <span className={cn("text-caption", i.lastProbeOk ? "text-state-ok" : i.lastProbeOk === false ? "text-state-fail" : "text-ink-mute")}>
                      {i.lastProbeOk ? "可用" : i.lastProbeOk === false ? "不可用" : "未探活"}
                    </span>
                  </div>
                  {i.quota?.concurrentLimit != null && (
                    <div className="mono text-caption text-ink-mute">
                      并发 {i.quota.runningCount ?? 0}/{i.quota.concurrentLimit} · 排队 {i.quota.queuedCount ?? 0}
                      {i.quota.remainCoins != null && <> · {i.quota.remainCoins} RH币</>}
                      {i.quota.remainMoney != null && <> · 余额 {fmtMoney(i.quota.remainMoney)}</>}
                    </div>
                  )}
                </li>
              ))}
            </ul>
          )}
        </Panel>

        <Panel
          title={
            <span className="flex items-center gap-2">
              <HardDrive className="h-3.5 w-3.5" />
              磁盘
            </span>
          }
          actions={<span className="mono text-caption text-ink-mute">{st ? `${st.mediaCount} 个文件` : "—"}</span>}
        >
          {!st ? (
            <div className="text-note text-ink-mute">读不到磁盘信息（后端没起？）</div>
          ) : (
            <div className="space-y-2">
              <div className="h-1 overflow-hidden rounded-hairline bg-hairline">
                <div className="h-full bg-gradient-to-r from-chrome to-chrome-2" style={{ width: `${capacity ? Math.min(100, (used / capacity) * 100) : 0}%` }} />
              </div>
              <div className="flex items-baseline justify-between text-caption">
                <span className="text-ink-mute">媒体 + 临时</span>
                <span className="mono">
                  {(used / 1024 ** 3).toFixed(1)} / {((capacity) / 1024 ** 3).toFixed(0)} GB
                </span>
              </div>
              <p className="text-caption leading-snug text-ink-mute">双底模约 85 GB，视频涨得快 —— 清理策略在系统设置里。</p>
            </div>
          )}
        </Panel>
      </div>

      {failing.length > 0 && (
        <Panel
          title={
            <span className="flex items-center gap-1.5">
              <CircleAlert className="h-3.5 w-3.5 text-state-fail" />
              需要处理
              <Badge tone="bad">{failing.length}</Badge>
            </span>
          }
        >
          <ul className="space-y-2">
            {failing.slice(0, 4).map((j) => (
              <li key={j.id} className="space-y-0.5">
                <div className="flex items-baseline gap-2">
                  <StateGlyph state="failed" />
                  <span className="text-note">{j.title}</span>
                  <span className="label ml-auto">{fmtTime(j.finishedAt)}</span>
                </div>
                <div className="pl-[18px] text-caption leading-snug text-ink-mute">{j.error?.message}</div>
                {j.error?.hint && <div className="pl-[18px] text-caption leading-snug text-state-ok">{j.error.hint}</div>}
              </li>
            ))}
          </ul>
        </Panel>
      )}
    </section>
  );
}
