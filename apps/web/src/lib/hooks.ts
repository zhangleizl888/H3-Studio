import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { Api } from "./api";
import type { LlmRunOpts } from "./api";
import type { LlmPurpose } from "./types";
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
