/**
 * 生成按钮背后那套「提交 → 轮询 → 回写」的统一实现。
 *
 * 为什么单独一层：五个页面都要点生成，如果各写各的轮询，就会有人忘了
 * 收口（任务成功了但项目里还是 generating）、有人忘了 flushSaves（最后 1 秒的编辑丢），
 * 也有人会在页面卸载后继续 setState。这里一次写对。
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import type { GenerateRequest } from "./api";
import { useApi } from "./apiClient";
import { keys } from "./hooks";
import * as local from "./localStores";
import type { GenTarget } from "./generate";
import { attachResult, markFailed, markGenerating, refFor, roleFor } from "./generate";
import type { Job, Media, Project } from "./types";

export interface GenHandle {
  key: string;
  jobId: string;
  state: Job["state"];
  /** 0-1；实例不给百分比时为 null（RunningHub Task API 就是这样） */
  progress: number | null;
  stage?: string | null;
  error?: string | null;
}

export type GenOutcome = { ok: true; media: Media[] } | { ok: false; error: string };

const POLL_MS = 2000;
/** 2s 一次、上限 60 分钟：H3 一镜十几分钟是常态，别在半路放弃轮询留下假的 generating */
const MAX_POLLS = 1800;

export function genKey(target: GenTarget): string {
  return `${target.kind}:${refFor(target)}`;
}

export function useGenerator(projectId: string | undefined) {
  const api = useApi();
  const qc = useQueryClient();
  const [handles, setHandles] = useState<Record<string, GenHandle>>({});
  const timers = useRef(new Map<string, number>());
  const alive = useRef(true);

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      timers.current.forEach((t) => window.clearInterval(t));
      timers.current.clear();
    };
  }, []);

  const refresh = useCallback(() => {
    if (!projectId) return;
    qc.invalidateQueries({ queryKey: keys.project(projectId) });
    qc.invalidateQueries({ queryKey: keys.jobs(projectId) });
    qc.invalidateQueries({ queryKey: keys.media(projectId) });
  }, [projectId, qc]);

  /** 把产物从服务端读回并登记进 IndexedDB，然后挂到实体上 */
  const settle = useCallback(
    async (target: GenTarget, job: Job): Promise<GenOutcome> => {
      if (!projectId) return { ok: false, error: "没有项目" };
      if (job.state !== "succeeded") {
        const message = (job.error as { message?: string } | null)?.message ?? `任务${job.state === "canceled" ? "已取消" : "失败"}`;
        const p = await local.getProject(projectId);
        markFailed(p, target, message);
        await local.patchProject(projectId, {}, (live) => {
          live.data.shots = p.data.shots;
          live.data.characters = p.data.characters;
          live.data.scenes = p.data.scenes;
          live.data.renderLogs = p.data.renderLogs;
        });
        return { ok: false, error: message };
      }
      const ids = (job.outputMediaIds ?? []).map(String);
      if (!ids.length) {
        const p = await local.getProject(projectId);
        markFailed(p, target, "执行完成但没有任何产物文件，请检查实例上的保存节点与输出目录");
        await local.patchProject(projectId, {}, (live) => {
          live.data.renderLogs = p.data.renderLogs;
        });
        return { ok: false, error: "执行完成但没有产物" };
      }
      const rows = await api.media.server({ projectKey: projectId, ids });
      for (const m of rows) await api.media.adopt(m);
      if (!rows.length) return { ok: false, error: `产物已落库但读不回记录（media ${ids.join(",")}）` };
      await local.patchProject(projectId, {}, (p) => {
        attachResult(p, target, rows);
      });
      return { ok: true, media: rows };
    },
    [api, projectId],
  );

  const watch = useCallback(
    (target: GenTarget, jobId: string) => {
      const key = genKey(target);
      let polls = 0;
      const id = window.setInterval(async () => {
        if (!alive.current) {
          window.clearInterval(id);
          return;
        }
        polls += 1;
        if (polls > MAX_POLLS) {
          window.clearInterval(id);
          timers.current.delete(key);
          setHandles((h) => ({ ...h, [key]: { ...(h[key] as GenHandle), state: "failed", error: "轮询超时（60 分钟），去队列页看真实状态" } }));
          return;
        }
        let job: Job;
        try {
          job = await api.jobs.get(jobId);
        } catch (e) {
          // 单次网络抖动不该把按钮打红：下一轮再问
          if (polls % 5 === 0) setHandles((h) => ({ ...h, [key]: { ...(h[key] as GenHandle), stage: `查询失败，重试中：${(e as Error).message}` } }));
          return;
        }
        const value = job.progress?.value;
        const max = job.progress?.max;
        const handle: GenHandle = {
          key,
          jobId,
          state: job.state,
          progress: job.progress?.unavailable || !max ? null : Math.min(1, (value ?? 0) / max),
          stage: job.progress?.stage ?? null,
          error: (job.error as { message?: string } | null)?.message ?? null,
        };
        setHandles((h) => ({ ...h, [key]: handle }));
        if (!["succeeded", "failed", "canceled"].includes(job.state)) return;
        window.clearInterval(id);
        timers.current.delete(key);
        await settle(target, job);
        refresh();
        // 收口后留一小会儿让按钮显示结果，再把它从 active 里摘掉
        window.setTimeout(() => {
          if (!alive.current) return;
          setHandles((h) => {
            const next = { ...h };
            delete next[key];
            return next;
          });
        }, 4000);
      }, POLL_MS);
      timers.current.set(key, id);
    },
    [api, refresh, settle],
  );

  /** 提交一个生成请求。返回 jobId 表示已入队，结果由后台轮询回写项目 */
  const run = useCallback(
    async (target: GenTarget, req: GenerateRequest): Promise<{ jobId?: string; error?: string }> => {
      if (!projectId) return { error: "没有项目" };
      const key = genKey(target);
      if (timers.current.has(key)) return { error: "这个对象已经有一条任务在跑" };
      await local.flushSaves();
      let job: Job;
      try {
        job = await api.jobs.generate(req);
      } catch (e) {
        const message = (e as Error).message;
        await local.patchProject(projectId, {}, (p) => markFailed(p, target, message));
        refresh();
        return { error: message };
      }
      await local.patchProject(projectId, {}, (p) => {
        markGenerating(p, target);
        const shot = p.data.shots.find((s) => s.id === (target.kind === "keyframe" || target.kind === "video" ? target.shotId : ""));
        if (shot && target.kind === "video") shot.jobId = job.id;
      });
      setHandles((h) => ({ ...h, [key]: { key, jobId: job.id, state: job.state, progress: 0, stage: "已入队" } }));
      watch(target, job.id);
      refresh();
      return { jobId: job.id };
    },
    [api, projectId, refresh, watch],
  );

  /** 批量派发：逐条入队，一条一个任务，能单独重试/取消 */
  const runBatch = useCallback(
    async (items: { target: GenTarget; req: GenerateRequest }[]) => {
      if (!projectId || !items.length) return { accepted: 0, rejected: 0 };
      await local.flushSaves();
      const res = await api.jobs.generateBatch(items.map((i) => i.req));
      const errors = new Map(res.errors.map((e) => [e.index, e.error]));
      let accepted = 0;
      for (let i = 0; i < items.length; i += 1) {
        const err = errors.get(i);
        if (err) {
          await local.patchProject(projectId, {}, (p) => markFailed(p, items[i].target, err));
          continue;
        }
        const job = res.jobs[accepted];
        accepted += 1;
        if (!job) continue;
        const key = genKey(items[i].target);
        setHandles((h) => ({ ...h, [key]: { key, jobId: job.id, state: job.state, progress: 0, stage: "已入队" } }));
        watch(items[i].target, job.id);
      }
      await local.patchProject(projectId, {}, (p) => {
        for (const item of items) if (!errors.has(items.indexOf(item))) markGenerating(p, item.target);
      });
      refresh();
      return { accepted, rejected: res.errors.length };
    },
    [api, projectId, refresh, watch],
  );

  /** 打开项目时把「generating 但已经没有对应任务」的对象收口，避免按钮永远转圈 */
  const reconcileFromServer = useCallback(
    async (project: Project) => {
      const jobs = await api.jobs.list({ projectId }).catch(() => [] as Job[]);
      const byId = new Map(jobs.map((j) => [j.id, j]));
      for (const shot of project.data.shots) {
        if (!shot.jobId) continue;
        const job = byId.get(shot.jobId);
        if (!job) continue;
        if (job.state === "succeeded" || job.state === "failed" || job.state === "canceled") {
          if (shot.state === "generating" || shot.state === "queued") {
            await settle({ kind: "video", shotId: shot.id }, job);
          }
        }
      }
      for (const [id, job] of byId) {
        if (job.state === "running" || job.state === "queued" || job.state === "dispatching") {
          // 上次关页面时提交的任务还在跑：把进度条接回来，别让它变成看不见的幽灵
          const target = targetFromJob(job, project);
          if (target && !timers.current.has(genKey(target))) {
            setHandles((h) => ({ ...h, [genKey(target)]: { key: genKey(target), jobId: id, state: job.state, progress: 0, stage: "接回上次未结束的任务" } }));
            watch(target, id);
          }
        }
      }
    },
    [api, settle, watch],
  );

  const busy = Object.keys(handles).length;
  return { run, runBatch, handles, busy, reconcileFromServer, roleFor };
}

/** 从任务的 title/meta 反推它属于哪个对象，用来接回刷新前提交的任务 */
function targetFromJob(job: Job, project: Project): GenTarget | null {
  const title = job.title ?? "";
  const shot = project.data.shots.find((s) => s.jobId === job.id);
  if (shot) return { kind: "video", shotId: shot.id };
  const byShotRef = project.data.shots.find((s) => title.includes(`镜 ${s.index}`));
  if (byShotRef) return title.includes("首帧") ? { kind: "keyframe", shotId: byShotRef.id, frameType: "start" } : title.includes("尾帧") ? { kind: "keyframe", shotId: byShotRef.id, frameType: "end" } : { kind: "video", shotId: byShotRef.id };
  const ch = project.data.characters.find((c) => title.includes(c.name) && title.startsWith("定妆"));
  if (ch) return { kind: "character", characterId: ch.id };
  const sc = project.data.scenes.find((s) => title.includes(s.name));
  if (sc) return { kind: "scene", sceneId: sc.id };
  return null;
}
