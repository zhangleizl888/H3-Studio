import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { Api, ScriptTrashResult } from "./api";
import type { LlmRunOpts } from "./api";
import type { LlmPurpose, ScriptVersionRow, ScriptVersionSource, VersionBucket } from "./types";
import { flushSaves, patchProject } from "./localStores";
import { sanitizeDanglingRefs } from "./versions";
import { useApi } from "./apiClient";

export const keys = {
  instances: ["instances"] as const,
  llm: (scope?: string) => ["llm", scope ?? "all"] as const,
  llmScan: ["llm", "scan"] as const,
  llmDefaults: ["llm", "defaults"] as const,
  workflows: ["workflows"] as const,
  workflow: (id: string) => ["workflows", id] as const,
  projects: ["projects"] as const,
  project: (id: string) => ["projects", id] as const,
  media: (id: string) => ["media", id] as const,
  jobs: (projectId?: string | null) => ["jobs", projectId ?? "all"] as const,
  users: ["users"] as const,
  styles: ["styles"] as const,
  storage: ["system", "storage"] as const,
  /**
   * 版本历史。每一族都给「前缀」和「具体键」两种：失效时整族一起清 ——
   * 这些查询都很便宜，而漏掉一个分组就会出现"删了但卡片还挂着那张图"的假象。
   */
  mediaVersions: ["mediaVersions"] as const,
  mediaVersionsOf: (projectId?: string | null, bucket?: string) => ["mediaVersions", projectId ?? "all", bucket ?? "all"] as const,
  scriptVersions: ["scriptVersions"] as const,
  scriptVersionsOf: (projectId?: string | null) => ["scriptVersions", projectId ?? "all"] as const,
  trash: ["trash"] as const,
  trashOf: (projectId?: string | null, bucket?: string) => ["trash", projectId ?? "all", bucket ?? "all"] as const,
};

export function useInstances() {
  const api = useApi();
  return useQuery({ queryKey: keys.instances, queryFn: () => api.instances.list(), refetchInterval: 30_000 });
}

export function useLlms(scope?: "local" | "cloud") {
  const api = useApi();
  return useQuery({ queryKey: keys.llm(scope), queryFn: () => api.llm.list(scope) });
}

export function useLocalScan(enabled: boolean) {
  const api = useApi();
  return useQuery({ queryKey: keys.llmScan, queryFn: () => api.llm.scanLocal(), enabled, staleTime: 20_000 });
}

export function useLlmDefaults() {
  const api = useApi();
  return useQuery({ queryKey: keys.llmDefaults, queryFn: () => api.llm.defaults() });
}

export function useWorkflows() {
  const api = useApi();
  return useQuery({ queryKey: keys.workflows, queryFn: () => api.workflows.list() });
}

export function useWorkflow(id: string | null) {
  const api = useApi();
  return useQuery({ queryKey: keys.workflow(id ?? ""), queryFn: () => api.workflows.get(id!), enabled: !!id });
}

export function useProjects() {
  const api = useApi();
  return useQuery({ queryKey: keys.projects, queryFn: () => api.projects.list() });
}

export function useProject(id: string | undefined) {
  const api = useApi();
  return useQuery({ queryKey: keys.project(id ?? ""), queryFn: () => api.projects.get(id!), enabled: !!id });
}

export function useMedia(projectId: string | undefined) {
  const api = useApi();
  return useQuery({ queryKey: keys.media(projectId ?? ""), queryFn: () => api.media.project(projectId!), enabled: !!projectId });
}

/** 队列页与导演台共用；运行中时轮询，全部落定后停止 */
export function useJobs(projectId?: string | null) {
  const api = useApi();
  return useQuery({
    queryKey: keys.jobs(projectId),
    queryFn: () => api.jobs.list({ projectId: projectId ?? undefined }),
    refetchInterval: (q) => (q.state.data?.some((j) => j.state === "running" || j.state === "queued") ? 1000 : 8000),
  });
}

/** 文本用途是同步的：结果直接回给调用方写进 IndexedDB，不进任务队列 */
export function useLlmRun(projectId?: string) {
  const api = useApi();
  const inv = useInvalidate();
  return useMutation({
    mutationFn: ({ purpose, input, opts }: { purpose: LlmPurpose; input: string; opts?: LlmRunOpts }) => api.llm.run(purpose, input, opts),
    onSuccess: () => inv(keys.project(projectId ?? ""), keys.projects),
  });
}

export function useUsers() {
  const api = useApi();
  return useQuery({ queryKey: keys.users, queryFn: () => api.users.list() });
}

export function useStyles() {
  const api = useApi();
  return useQuery({ queryKey: keys.styles, queryFn: () => api.styles.list() });
}

export function useStorage() {
  const api = useApi();
  return useQuery({ queryKey: keys.storage, queryFn: () => api.system.storage() });
}

function useInvalidate() {
  const qc = useQueryClient();
  return (...keysList: readonly (readonly unknown[])[]) => keysList.forEach((k) => qc.invalidateQueries({ queryKey: k }));
}

export function useInstanceMutations() {
  const api = useApi();
  const inv = useInvalidate();
  return {
    create: useMutation({ mutationFn: api.instances.create, onSuccess: () => inv(keys.instances) }),
    update: useMutation({ mutationFn: ({ id, body }: { id: string; body: Parameters<Api["instances"]["update"]>[1] }) => api.instances.update(id, body), onSuccess: () => inv(keys.instances) }),
    remove: useMutation({ mutationFn: (id: string) => api.instances.remove(id), onSuccess: () => inv(keys.instances) }),
    probe: useMutation({ mutationFn: (id: string) => api.instances.probe(id), onSuccess: () => inv(keys.instances) }),
  };
}

export function useLlmMutations() {
  const api = useApi();
  const inv = useInvalidate();
  return {
    create: useMutation({ mutationFn: api.llm.create, onSuccess: () => inv(keys.llm(), keys.llm("local"), keys.llm("cloud")) }),
    update: useMutation({
      mutationFn: ({ id, body }: { id: string; body: Parameters<Api["llm"]["update"]>[1] }) => api.llm.update(id, body),
      onSuccess: () => inv(keys.llm(), keys.llm("local"), keys.llm("cloud")),
    }),
    remove: useMutation({ mutationFn: (id: string) => api.llm.remove(id), onSuccess: () => inv(keys.llm(), keys.llm("local"), keys.llm("cloud")) }),
    probe: useMutation({
      mutationFn: (id: string) => api.llm.probe(id),
      onSuccess: () => inv(keys.llm(), keys.llm("local"), keys.llm("cloud")),
    }),
    pull: useMutation({ mutationFn: ({ id, model }: { id: string; model: string }) => api.llm.pull(id, model), onSuccess: () => inv(keys.llm()) }),
    setDefault: useMutation({ mutationFn: (id: string) => api.llm.setDefault(id), onSuccess: () => inv(keys.llm(), keys.llmDefaults) }),
    saveDefaults: useMutation({ mutationFn: api.llm.saveDefaults, onSuccess: () => inv(keys.llmDefaults) }),
  };
}

export function useWorkflowMutations() {
  const api = useApi();
  const inv = useInvalidate();
  return {
    importJson: useMutation({
      mutationFn: ({ name, json, instanceId }: { name: string; json: string; instanceId?: string }) =>
        api.workflows.importJson(name, json, instanceId),
      onSuccess: () => inv(keys.workflows),
    }),
    validate: useMutation({ mutationFn: ({ json, instanceId }: { json: string; instanceId?: string }) => api.workflows.validate(json, instanceId) }),
    remove: useMutation({ mutationFn: (id: string) => api.workflows.remove(id), onSuccess: () => inv(keys.workflows) }),
    testRun: useMutation({
      mutationFn: ({ id, instanceId }: { id: string; instanceId: string }) => api.workflows.testRun(id, instanceId),
      onSuccess: () => inv(keys.jobs()),
    }),
    rescan: useMutation({
      mutationFn: ({ id, instanceId }: { id: string; instanceId?: string }) => api.workflows.rescan(id, instanceId),
      onSuccess: () => inv(keys.workflows),
    }),
    patch: useMutation({
      mutationFn: ({ id, body }: { id: string; body: { autoSelect?: boolean; priority?: number } }) => api.workflows.patch(id, body),
      onSuccess: () => inv(keys.workflows),
    }),
  };
}

export function useProjectMutations(id?: string) {
  const api = useApi();
  const inv = useInvalidate();
  return {
    create: useMutation({ mutationFn: ({ name, synopsis }: { name: string; synopsis?: string }) => api.projects.create(name, synopsis), onSuccess: () => inv(keys.projects) }),
    update: useMutation({ mutationFn: (patch: Parameters<Api["projects"]["update"]>[1]) => api.projects.update(id!, patch), onSuccess: () => inv(keys.projects, keys.project(id ?? "")) }),
    data: useMutation({ mutationFn: (d: Parameters<Api["projects"]["updateData"]>[1]) => api.projects.updateData(id!, d), onSuccess: () => inv(keys.projects, keys.project(id ?? "")) }),
    config: useMutation({ mutationFn: (c: Parameters<Api["projects"]["updateConfig"]>[1]) => api.projects.updateConfig(id!, c), onSuccess: () => inv(keys.project(id ?? ""), keys.projects) }),
    remove: useMutation({ mutationFn: () => api.projects.remove(id!), onSuccess: () => inv(keys.projects) }),
    duplicate: useMutation({ mutationFn: () => api.projects.duplicate(id!), onSuccess: () => inv(keys.projects) }),
  };
}

export function useJobMutations(projectId?: string | null) {
  const api = useApi();
  const inv = useInvalidate();
  const after = () => inv(keys.jobs(projectId), keys.project(projectId ?? ""), keys.instances);
  return {
    generate: useMutation({ mutationFn: (r: Parameters<Api["jobs"]["generate"]>[0]) => api.jobs.generate(r), onSuccess: after }),
    generateBatch: useMutation({ mutationFn: (rs: Parameters<Api["jobs"]["generateBatch"]>[0]) => api.jobs.generateBatch(rs), onSuccess: after }),
    cancel: useMutation({ mutationFn: (id: string) => api.jobs.cancel(id), onSuccess: after }),
    retry: useMutation({ mutationFn: (id: string) => api.jobs.retry(id), onSuccess: after }),
    priority: useMutation({ mutationFn: ({ id, priority }: { id: string; priority: number }) => api.jobs.setPriority(id, priority), onSuccess: after }),
  };
}

/** 单卡状态：谁正占着这张 GPU。文本按钮与生成按钮都要看它来决定禁用还是提示 */
export function useGpuState() {
  const api = useApi();
  return useQuery({
    queryKey: ["system", "gpu"] as const,
    queryFn: () => api.system.gpu(),
    refetchInterval: (q) => (q.state.data?.renderingLocally || q.state.data?.llmCalling ? 2000 : 10_000),
  });
}

export function useGpuMutations() {
  const api = useApi();
  const inv = useInvalidate();
  const after = () => inv(["system", "gpu"] as const);
  return {
    yield: useMutation({ mutationFn: () => api.system.gpuYield(), onSuccess: after }),
    restore: useMutation({ mutationFn: () => api.system.gpuRestore(), onSuccess: after }),
  };
}

export function useUserMutations() {
  const api = useApi();
  const inv = useInvalidate();
  return {
    create: useMutation({ mutationFn: api.users.create, onSuccess: () => inv(keys.users) }),
    update: useMutation({ mutationFn: ({ id, body }: { id: string; body: Parameters<Api["users"]["update"]>[1] }) => api.users.update(id, body), onSuccess: () => inv(keys.users) }),
    remove: useMutation({ mutationFn: (id: string) => api.users.remove(id), onSuccess: () => inv(keys.users) }),
  };
}

export function useExportMutations(projectId: string) {
  const api = useApi();
  const inv = useInvalidate();
  const on = () => inv(keys.media(projectId), keys.storage);
  return {
    merge: useMutation({
      mutationFn: ({ mediaIds, title }: { mediaIds: string[]; title?: string }) => api.exports.merge(projectId, mediaIds, title),
      onSuccess: on,
    }),
    pack: useMutation({
      mutationFn: ({ items, title }: { items: { mediaId: string; path: string }[]; title?: string }) => api.exports.pack(projectId, items, title),
      onSuccess: on,
    }),
    edl: useMutation({ mutationFn: ({ shots, title }: { shots: Parameters<Api["exports"]["edl"]>[1]; title?: string }) => api.exports.edl(projectId, shots, title) }),
    xml: useMutation({ mutationFn: ({ shots, title }: { shots: Parameters<Api["exports"]["xml"]>[1]; title?: string }) => api.exports.xml(projectId, shots, title) }),
    jianying: useMutation({ mutationFn: (shots: Parameters<Api["exports"]["jianying"]>[1]) => api.exports.jianying(projectId, shots) }),
  };
}

/**
 * 某一项目（或全部项目）某个 bucket 的所有版本。
 *
 * 故意做成「一次拉一桶、分组在内存里做」而不是逐张卡片查一个分组：资产页一次渲染几十张卡，
 * 按分组查就是 N+1。分组键是 (kind, role, refId)，用 lib/versions.ts 的 groupOf 对上。
 */
export function useMediaVersions(scope: { projectId?: string | null; bucket?: VersionBucket; includeDeleted?: boolean }) {
  const api = useApi();
  return useQuery({
    queryKey: keys.mediaVersionsOf(scope.projectId ?? null, scope.bucket),
    queryFn: () =>
      api.versions.media({
        projectKey: scope.projectId ?? undefined,
        // 没有 project id 就是"全部项目"视图，必须显式说出来，否则后端按漏传处理
        allProjects: !scope.projectId,
        bucket: scope.bucket,
        includeDeleted: scope.includeDeleted,
      }),
    staleTime: 30_000,
  });
}

/**
 * 某一个生成对象的版本组（按服务端 (role, ref_id) 取）。
 *
 * 和 useMediaVersions 的分工：那个是「一桶全拉、前端分组」给全局页用的（那里几十张卡，
 * 逐个查就是 N+1）；这个是内嵌面板用的 —— 抽屉/预览同一时刻只开一个，按分组精确查更省。
 */
export function useVersionGroup(projectId: string | null | undefined, role?: string | null, refId?: string | null) {
  const api = useApi();
  return useQuery({
    queryKey: keys.mediaVersionsOf(projectId ?? null, `group:${role ?? ""}:${refId ?? ""}`),
    queryFn: () => api.versions.media({ projectKey: projectId ?? undefined, role: role ?? undefined, refId: refId ?? undefined, includeDeleted: true }),
    enabled: !!projectId && !!role,
    staleTime: 15_000,
  });
}

export function useScriptVersions(projectId: string | null | undefined, includeDeleted = false) {  const api = useApi();
  return useQuery({
    queryKey: keys.scriptVersionsOf(projectId ?? null),
    queryFn: () => api.versions.script(projectId ?? "", { includeDeleted }),
    enabled: !!projectId,
    staleTime: 30_000,
  });
}

/** 生成回收站。列表不轮询：倒计时是天级别的，进来查一次就够 */
export function useTrash(scope: { projectId?: string | null; bucket?: VersionBucket } = {}) {
  const api = useApi();
  return useQuery({
    queryKey: keys.trashOf(scope.projectId ?? null, scope.bucket),
    queryFn: () => api.versions.trash({ projectKey: scope.projectId ?? undefined, allProjects: !scope.projectId, bucket: scope.bucket }),
    staleTime: 15_000,
  });
}

/**
 * 版本历史与回收站的全部写操作。
 *
 * 为什么每个动作都把 mediaVersions/scriptVersions/trash/project/media/storage 一起失效：
 * 摘指针、删索引行这些是**直接写 IndexedDB** 的，绕过 react-query；少失效一个，卡片就会
 * 拿着旧快照继续显示那张已经进回收站的图 —— 正是用户明确否掉的"只标记没删干净"。
 */
export function useVersionMutations(defaultProjectId?: string | null) {
  const api = useApi();
  const inv = useInvalidate();
  const after = (projectId: string | null | undefined = defaultProjectId) => {
    if (projectId) {
      inv(keys.project(projectId), keys.media(projectId), keys.scriptVersionsOf(projectId), keys.trashOf(projectId));
    }
    inv(keys.mediaVersions, keys.scriptVersions, keys.trash, keys.storage);
  };
  return {
    /** 移进回收站：服务端软删 → 摘实体指针 → 删本地索引行（都在 api.media.remove 里按顺序做完） */
    trashMedia: useMutation({
      mutationFn: ({ id }: { id: string; projectId?: string | null }) => api.media.remove(id),
      onSuccess: (_r, v) => after(v.projectId),
    }),
    restoreMedia: useMutation({
      mutationFn: async ({ id, projectId }: { id: string; projectId?: string | null }) => {
        await api.versions.restoreMedia(id);
        // 恢复必须把服务端那行重新 adopt 回本地索引：进回收站时索引行和 blob 都清了，
        // 不补回来卡片就是空的
        const rows = await api.media.server({ ids: [id] });
        for (const r of rows) await api.media.adopt(r);
        after(projectId);
      },
    }),
    purgeMedia: useMutation({
      mutationFn: ({ id }: { id: string; projectId?: string | null }) => api.versions.purgeMedia(id),
      onSuccess: (_r, v) => after(v.projectId),
    }),
    purgeScript: useMutation({
      mutationFn: ({ uuid, projectId }: { uuid: string; projectId?: string | null }) =>
        api.versions.purgeScript(uuid).then(() => after(projectId)),
    }),
    createScript: useMutation({
      mutationFn: (body: Parameters<Api["versions"]["createScript"]>[0]) =>
        api.versions.createScript(body).then((r) => {
          after(body.projectKey);
          return r;
        }),
    }),
    /** 返回里带着 rawScript：调用方必须把它写回编辑器，否则正文和"当前版"会分家 */
    setScriptCurrent: useMutation({
      mutationFn: ({ uuid, projectId }: { uuid: string; projectId?: string | null }) =>
        api.versions.setScriptCurrent(uuid).then((r) => {
          after(projectId ?? r.current.projectKey);
          return r;
        }),
    }),
    trashScript: useMutation({
      mutationFn: ({ uuid, projectId }: { uuid: string; projectId?: string | null }) =>
        api.versions.trashScript(uuid).then((r: ScriptTrashResult) => {
          after(projectId);
          return r;
        }),
    }),
    restoreScript: useMutation({
      mutationFn: ({ uuid, projectId }: { uuid: string; projectId?: string | null }) =>
        api.versions.restoreScript(uuid).then((r) => {
          after(projectId);
          return r;
        }),
    }),
    trashProject: useMutation({
      mutationFn: ({ projectId, name }: { projectId: string; name?: string }) =>
        api.versions.trashProject(projectId, name).then((r) => {
          after(projectId);
          return r;
        }),
    }),
  };
}

/**
 * 把服务端还活着的产物重新登记进本地索引，然后摘掉指向"哪都没有"的实体指针。
 *
 * 顺序绝不能反：先 adopt 服务端活行，再清指针 —— 反过来就会把「索引被清过但服务端还在」
 * 的正常产物当成坏的删掉。换浏览器 / 清过缓存 / 在别的机器上出过片之后靠它对齐。
 * 做成显式按钮而不是自动跑，是因为它在改用户的项目实体，得能解释。
 */
export async function resyncMediaIndex(api: Api, projectId: string) {
  const live = await api.media.server({ projectKey: projectId });
  for (const m of live) await api.media.adopt(m);
  const indexed = await api.media.project(projectId);
  const valid = new Set([...live, ...indexed].map((m) => m.id));
  const dangling: string[] = [];
  await flushSaves();
  await patchProject(projectId, {}, (p) => dangling.push(...sanitizeDanglingRefs(p, valid)));
  return { adopted: live.length, dangling: dangling.length };
}

/**
 * 剧本版本的三个动作，封在一处：任何宿主（剧本页、生成历史、以后的编辑助手）都不该
 * 自己拼这些步骤 —— 少写一步就是丢正文或者指针和正文分家。
 */
export function useScriptVersionActions(projectId: string | null | undefined) {
  const muts = useVersionMutations(projectId);

  /**
   * 生成完成时存一版。
   *
   * `previous` 是"这一版之前的正文"：项目第一次存版时，服务端会把它补成 V1
   * （需求里那句"再次生成之后就存储之前的版本历史记录"）。分两次 POST 不行 ——
   * 中间崩了就只剩一个被标成当前版的 V1，这次生成的正文永远进不了历史。
   */
  async function snapshot(o: {
    text: string;
    source: ScriptVersionSource;
    snapshot?: ScriptVersionRow["snapshot"];
    previous?: { text: string; writtenAt?: string | null };
  }) {
    if (!projectId || !o.text.trim()) return null;
    const backfill =
      o.previous && o.previous.text.trim() && o.previous.text !== o.text
        ? { text: o.previous.text, ...(o.previous.writtenAt ? { writtenAt: o.previous.writtenAt } : {}) }
        : undefined;
    await flushSaves();
    const row = await muts.createScript.mutateAsync({
      projectKey: projectId,
      text: o.text,
      source: o.source,
      snapshot: o.snapshot,
      ...(backfill ? { backfillFrom: backfill } : {}),
    });
    await patchProject(projectId, {}, (p) => {
      p.data.scriptVersionUuid = row.uuid;
      p.data.scriptWrittenAt = row.writtenAt;
    });
    return row;
  }

  /**
   * 把某一版设为当前，并把正文写回编辑器。
   *
   * 切之前先比对：工作正文和"当前版"的文字不一致，说明用户在编辑器里手改过 ——
   * 那就先把这份手改存成一版（source=manual）再切。不做这一步，切版本就是
   * 直接吃掉用户手打的字，而那正是"版本历史"最不该发生的事。
   */
  async function switchTo(
    v: ScriptVersionRow,
    ctx: { workingText: string; workingWrittenAt?: string | null; currentText?: string | null },
  ) {
    if (!projectId) return null;
    const edited = ctx.currentText != null && ctx.workingText !== ctx.currentText && ctx.workingText.trim().length > 0;
    if (edited) {
      await muts.createScript.mutateAsync({
        projectKey: projectId,
        text: ctx.workingText,
        source: "manual",
        setCurrent: false,
        ...(ctx.workingWrittenAt ? { writtenAt: ctx.workingWrittenAt } : {}),
      });
    }
    const r = await muts.setScriptCurrent.mutateAsync({ uuid: v.uuid, projectId });
    await flushSaves();
    await patchProject(projectId, {}, (p) => {
      p.data.rawScript = r.rawScript;
      p.data.scriptVersionUuid = r.current.uuid;
      p.data.scriptWrittenAt = r.current.writtenAt;
    });
    return { ...r, savedLocalEdit: edited };
  }

  /** 删掉一版。服务端承诺：正文不会被吃掉，编辑器里那份一个字都不动 */
  async function trash(uuid: string) {
    return muts.trashScript.mutateAsync({ uuid, projectId });
  }

  return { snapshot, switchTo, trash, busy: muts.createScript.isPending || muts.setScriptCurrent.isPending };
}
