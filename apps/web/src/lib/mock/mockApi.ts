import type { Api, GenerateRequest } from "../api";
import type { GenInstance, ImportReport, Job, LlmBackend, Media, ProbeReport, Project, User, Workflow } from "../types";
import { RH_ERROR_HINTS } from "../constants";
import { uid } from "../utils";
import { seedInstances, seedJobs, seedLlmBackends, seedMedia, seedProject, seedUsers, seedWorkflows } from "./db";

/**
 * mock 层不只是假数据：它模拟了任务的生命周期，
 * 因为「原生实例给真实进度、RunningHub Task API 不给百分比」这件事
 * 必须在没有后端的时候就能被界面验证。
 */

const store = {
  instances: structuredClone(seedInstances),
  llms: structuredClone(seedLlmBackends),
  workflows: structuredClone(seedWorkflows),
  media: structuredClone(seedMedia),
  projects: [structuredClone(seedProject)],
  jobs: structuredClone(seedJobs),
  users: structuredClone(seedUsers),
  // 原型模式默认以 admin 登录，省去每次重新输密码；真实后端由服务端 session 决定
  session: structuredClone(seedUsers[0]) as User | null,
  defaults: {
    script_parse: { backendId: "llm_ollama", model: "qwen3:8b" },
    storyboard: { backendId: "llm_ollama", model: "qwen3:14b" },
    h3_prompt: { backendId: "llm_ollama", model: "qwen3:8b" },
    embed: { backendId: "llm_ollama", model: "bge-m3" },
  } as Record<string, { backendId: string; model: string }>,
};

const delay = (ms = 140) => new Promise((r) => setTimeout(r, ms));
const clone = <T>(v: T): T => structuredClone(v);

function findProject(id: string): Project {
  const p = store.projects.find((x) => x.id === id);
  if (!p) throw new Error(`项目不存在：${id}`);
  return p;
}

function touch(p: Project) {
  p.updatedAt = new Date().toISOString();
}

/* ───────── 任务推进引擎 ───────── */

let ticking = false;
function ensureTicker() {
  if (ticking) return;
  ticking = true;
  setInterval(() => {
    for (const j of store.jobs) {
      if (j.state !== "running") continue;
      const inst = store.instances.find((i) => i.id === j.instanceId);
      if (!inst) continue;

      // 原生协议有真实 progress 事件；RH Task API 官方称 WS 不稳定，只有状态
      if (j.progress.unavailable) {
        j.progress.stage = ["排队中", "已派发到实例", "采样中", "解码中", "转存产物"][Math.floor(Math.random() * 5)];
        if (Math.random() < 0.12) finishJob(j, inst);
        continue;
      }
      const max = j.progress.max ?? 40;
      j.progress.value = Math.min(max, (j.progress.value ?? 0) + Math.ceil(max / 22));
      j.progress.etaSec = Math.max(0, Math.round(((max - (j.progress.value ?? 0)) / max) * 90));
      if ((j.progress.value ?? 0) >= max) finishJob(j, inst);
    }
  }, 900);
}

function finishJob(j: Job, inst: GenInstance) {
  const ok = Math.random() > 0.12;
  j.finishedAt = new Date().toISOString();
  if (!ok) {
    j.state = "failed";
    j.error = {
      type: "comfy_execution_error",
      message: "节点 131 MiniMaxH3ImageToVideo：length=200 不在 17k+5 网格上",
      nodeId: "131",
      nodeType: "MiniMaxH3ImageToVideo",
      hint: "让后端按 h3FrameCount() 取整后再提交，不要依赖节点静默吸附",
    };
    j.log.push({ ts: j.finishedAt, level: "error", msg: "执行失败，已停止重试" });
    syncShotStates();
    return;
  }
  j.state = "succeeded";
  const shot = j.title.match(/镜 (\d+)/);
  const m: Media = {
    id: uid("md"),
    projectId: j.projectId,
    kind: j.kind === "image" ? "image" : "video",
    role: j.kind === "image" ? "keyframe_start" : "video",
    refId: shot ? `shot_${Number(shot[1])}` : null,
    path: `p_1/generated/${uid("out")}.${j.kind === "image" ? "png" : "mp4"}`,
    width: 864,
    height: 480,
    fps: j.kind === "image" ? null : 24,
    durationMs: j.kind === "image" ? null : 5167,
    instanceId: inst.id,
    promptId: j.promptId,
    seed: Math.floor(Math.random() * 99999),
    createdAt: j.finishedAt,
    bytes: j.kind === "image" ? 1_240_000 : 4_900_000,
  };
  store.media.unshift(m);
  j.outputMediaIds = [m.id];
  j.log.push({
    ts: j.finishedAt,
    level: "info",
    msg: inst.protocol === "rh_task" ? "结果已在约 1 天过期前转存到 data/media" : "产物已落盘",
  });
  if (inst.protocol === "rh_task") {
    j.cost = { seconds: 40 + Math.random() * 300, money: 1 + Math.random() * 18 };
  }
  syncShotStates();
}

function syncShotStates() {
  for (const p of store.projects) {
    for (const s of p.data.shots) {
      const j = store.jobs.find((x) => x.id === s.jobId);
      if (!j) continue;
      s.state =
        j.state === "succeeded" ? "completed" : j.state === "failed" ? "failed" : j.state === "queued" ? "queued" : "generating";
      if (j.state === "succeeded" && j.outputMediaIds.length) {
        if (j.kind === "image" && !s.startFrameMediaId) s.startFrameMediaId = j.outputMediaIds[0];
        if (j.kind === "video") s.videoMediaIds = [...s.videoMediaIds, ...j.outputMediaIds];
      }
    }
    touch(p);
  }
}

/* ───────── 探活：如实反映这台机器 ───────── */

function probeInstance(inst: GenInstance): ProbeReport {
  if (inst.protocol === "comfy_native") {
    if (inst.baseUrl.includes("127.0.0.1")) {
      return {
        ok: false,
        error: "连不上 127.0.0.1:8188 —— ComfyUI 未启动",
        hints: [
          "cd F:/ComfyUI && python main.py --port 8188",
          "不需要加 --enable-cors-header：我们走服务端调用，开了反而拆掉 loopback 防护",
          "要 H3 节点必须 ComfyUI ≥ 0.30.0（当前最新 0.37.4）",
        ],
      };
    }
    return {
      ok: true,
      native: {
        comfyVersion: inst.capabilities?.comfyVersion ?? "0.37.4",
        nodeCount: inst.capabilities?.nodeCount ?? 1284,
        gpu: inst.capabilities?.gpu ?? "未知",
        vramTotalGb: inst.capabilities?.vramTotalGb,
        vramFreeGb: inst.capabilities?.vramFreeGb,
        caps: inst.capabilities,
        missingModels: inst.baseUrl.includes("runninghub") ? [] : ["minimax_h3_ref2va_pruned_int8_convrot.safetensors"],
      },
      hints: inst.baseUrl.includes("runninghub")
        ? ["这是 RunningHub 原生代理：产物走 /view 下载，batch 必须为 1"]
        : ["cloudflared 隧道需在 originRequest 里设 httpHostHeader: 127.0.0.1:8188"],
    };
  }
  // rh_task
  if (!inst.apiKeySet) {
    return { ok: false, error: `801 ${RH_ERROR_HINTS["801"]}`, hints: ["在 RunningHub 控制台「API 调用」页取 key，cn 与 global 两站的 key 不通用"] };
  }
  return {
    ok: true,
    task: {
      queue: { concurrentLimit: inst.quota?.concurrentLimit ?? 30, runningCount: 4, queuedCount: 11, apiKeyType: "enterprise_shared" },
      account: { remainMoney: inst.quota?.remainMoney ?? 0, currency: "¥" },
      reachable: true,
    },
    hints: ["结果链接是 COS 签名地址，约 1 天过期，我们会在完成时立即转存", "API 会强制重置 seed，我们每次都会显式注入"],
  };
}

/* ───────── Api 实现 ───────── */

export const mockApi: Api = {
  auth: {
    async login(username, password) {
      await delay();
      const u = store.users.find((x) => x.username === username);
      if (!u || password.length < 4) throw new Error("用户名或密码不对");
      if (!u.isActive) throw new Error("该账号已被停用，联系管理员");
      store.session = u;
      return { user: clone(u), access: "mock.access", refresh: "mock.refresh" };
    },
    async me() {
      await delay(60);
      return store.session ? clone(store.session) : null;
    },
    async logout() {
      store.session = null;
    },
    async setupRequired() {
      return false;
    },
  },

  instances: {
    async list() {
      await delay();
      return clone(store.instances);
    },
    async create(body) {
      await delay();
      const inst: GenInstance = {
        id: uid("inst"),
        name: body.name,
        protocol: body.protocol,
        placement: body.placement ?? "local",
        baseUrl: body.baseUrl ?? "",
        wsUrl: body.wsUrl ?? null,
        apiKeySet: !!body.apiKeySet,
        site: body.site,
        instanceType: body.instanceType,
        retainSeconds: body.retainSeconds ?? null,
        isDefault: !!body.isDefault,
        localOutputRoot: body.localOutputRoot ?? null,
        tunnelName: body.tunnelName ?? null,
        capabilities: {},
        lastProbeAt: null,
        lastProbeOk: null,
      };
      if (body.isDefault) store.instances.forEach((i) => (i.isDefault = false));
      store.instances.push(inst);
      return clone(inst);
    },
    async update(id, body) {
      await delay();
      const i = store.instances.find((x) => x.id === id)!;
      Object.assign(i, body);
      if (body.isDefault) store.instances.filter((x) => x.id !== id).forEach((x) => (x.isDefault = false));
      return clone(i);
    },
    async remove(id) {
      await delay();
      store.instances = store.instances.filter((x) => x.id !== id);
    },
    async probe(id) {
      await delay(420);
      const i = store.instances.find((x) => x.id === id)!;
      const r = probeInstance(i);
      i.lastProbeAt = new Date().toISOString();
      i.lastProbeOk = r.ok;
      i.lastError = r.error ?? null;
      if (r.ok && r.native) i.capabilities = { ...i.capabilities, ...r.native.caps };
      if (r.ok && r.task?.queue) i.quota = { ...i.quota, ...r.task.queue };
      return r;
    },
    async dryProbe(body) {
      await delay(320);
      return probeInstance({
        id: "draft",
        name: "草稿",
        protocol: body.protocol,
        placement: "local",
        baseUrl: body.baseUrl,
        apiKeySet: !!body.apiKey,
        site: body.site as GenInstance["site"],
        isDefault: false,
        capabilities: {},
      });
    },
  },

  llm: {
    async list(scope) {
      await delay();
      return clone(scope ? store.llms.filter((l) => l.scope === scope) : store.llms);
    },
    async run(purpose, input, opts) {
      // 演示模式：给一份形状正确的假结果，让「拆解→分镜→提示词」这条链在没后端时也能走通
      await delay(700);
      const head = input.slice(0, 24).replace(/\s+/g, " ");
      if (purpose === "storyboard") {
        return { purpose, latencyMs: 700, data: { shots: [1, 2, 3].map((i) => ({ index: i, sceneName: "3 号车站", characterNames: ["林晚"], action: `镜头 ${i}：${head}`, dialogue: "", visualPrompt: `雨夜车站，冷青色调，第 ${i} 镜`, durationSec: 6, cameraMovement: i === 1 ? "推近" : "固定", shotSize: i === 1 ? "中景" : "全景" })) } };
      }
      if (purpose === "visualize") return { purpose, latencyMs: 700, data: { visualPrompt: `${head}；中景，低角度，湿冷反光，青灰主色`, negative: "文字水印、畸变手指" } };
      if (purpose === "h3_prompt") {
        const mode = opts?.mode ?? "three_field";
        if (mode === "wenwu") {
          return {
            purpose,
            mode,
            latencyMs: 700,
            data: {
              integrated: `${head}，镜头缓慢推近`,
              sceneDescription: `本片 6 秒，画幅 16:9，写实电影感。生命核：林晚，25 岁东亚女性，黑色短发，卡其风衣。<Picture 1> 是本镜起始帧，身份与服装以它为准。最终发展线：她在雨夜车站等到那班不会来的车。`,
              shotBlocks: [
                "镜头1（0-6s）：中景，推近 (zoom in shot)\n    摄像机状态：机位平视，缓慢推近，对焦人物面部，景深适中\n    画面内容：林晚收伞坐下，视线落在铁轨上，呼吸停半拍\n    音频：雨声、远处电车铃，无背景音乐\n    人物台词（清晰口语）：无",
              ],
              soundscape: "雨声 + 远处电车铃，人声收录干净",
              music: "N/A",
            },
          };
        }
        if (mode === "six_section" || mode === "hybrid") {
          return {
            purpose,
            mode,
            latencyMs: 700,
            data: {
              integrated: `${head}，镜头缓慢推近`,
              subjectDefinitions: "<Subject 1> 林晚：25岁，纤细，黑色短发，卡其风衣，青灰主色，右手无名指银戒\n<Picture 1> 是本镜起始帧，锁定身份、服装与场景光线",
              summary: "6 秒 16:9 的一镜：林晚在雨夜车站收伞坐下，视线落向铁轨，镜末呼吸停住。开头等待→本镜确认→镜末落空。",
              retentionAnalysis: "<Subject 1> identity, face, hairstyle, costume and scene lighting fully_preserved from <Picture 1>; only the action and camera change.",
              detailedDescription: "[Shot 1] 0.00-6.00s. 中景, 推近 (zoom in shot). 林晚收伞坐下，视线落在铁轨上. 她的呼吸停半拍，握伞的手指收紧. 摄影机缓慢推近以确认她的表情. 镜末留下她被淋湿的肩线, 交给下一镜.",
              soundscape: "雨声、远处电车铃、伞面滴水；本镜无对白",
              music: "N/A",
              ...(mode === "hybrid" ? { constraints: "photorealistic, cinematic film quality, natural lighting, no readable text, no subtitles, no extra characters, no identity drift" } : {}),
            },
          };
        }
        return { purpose, mode, latencyMs: 700, data: { integrated: `${head}，镜头缓慢推近`, soundscape: "雨声、远处电车铃", music: "N/A" } };
      }
      const s = findProject(store.projects[0]?.id ?? "")?.data.script;
      return { purpose, latencyMs: 700, data: clone(s ?? { title: head, logline: head, genre: ["悬疑"], characters: [], scenes: [], beats: [] }) };
    },
    async scanLocal() {
      await delay(600);
      // 如实反映这台机器：四个常见端口都探不到活的服务
      return {
        found: [
          { port: 11434, detectedAs: "ollama", ok: false, detail: "已安装 v0.32.13，但进程未运行" },
          { port: 8080, detectedAs: "unknown", ok: false, detail: "端口无响应；注意 8080 常被其他程序占用" },
          { port: 1234, detectedAs: "unknown", ok: false, detail: "无响应" },
          { port: 8000, detectedAs: "unknown", ok: false, detail: "无响应" },
        ],
        hints: [
          { backend: "ollama", label: "Ollama", command: "ollama serve", note: "本机已装 0.32.13，建议升到 v0.34.x" },
          {
            backend: "llamacpp",
            label: "llama.cpp（router 模式）",
            command:
              'llama-server --models-dir D:/models/gguf --no-models-autoload --jinja --host 127.0.0.1 --port 8080 -ngl 999 -c 32768',
            note: "上下文由 -c 决定，不能按请求调整；--jinja 不开则没有 tool calling",
          },
          { backend: "lmstudio", label: "LM Studio", command: "lms server start", note: "默认 1234/v1" },
          { backend: "vllm", label: "vLLM", command: "vllm serve <model> --api-key ...", note: "默认 8000/v1" },
        ],
      };
    },
    async create(body) {
      await delay();
      const kind = body.kind;
      const l: LlmBackend = {
        id: uid("llm"),
        name: body.name,
        scope: body.scope,
        kind,
        baseUrl: body.baseUrl,
        apiKeySet: !!body.apiKeySet,
        chatPath: kind === "ollama" ? "/api/chat" : "/chat/completions",
        streamStyle: kind === "ollama" ? "ndjson" : "sse",
        capabilities: {
          models: [],
          ctxSize: null,
          hasJsonSchema: kind === "ollama",
          hasVision: false,
          hasTools: false,
          // Ollama 可每请求传 num_ctx；llama.cpp 的 -c 是启动参数
          ctxIsPerRequest: kind === "ollama",
          supportsPull: kind === "ollama",
        },
        isDefault: !!body.isDefault,
        lastProbeAt: null,
        lastProbeOk: null,
      };
      if (body.isDefault) store.llms.filter((x) => x.scope === l.scope).forEach((x) => (x.isDefault = false));
      store.llms.push(l);
      return clone(l);
    },
    async update(id, body) {
      await delay();
      const l = store.llms.find((x) => x.id === id)!;
      Object.assign(l, body);
      return clone(l);
    },
    async remove(id) {
      await delay();
      store.llms = store.llms.filter((x) => x.id !== id);
    },
    async probe(id) {
      await delay(520);
      const l = store.llms.find((x) => x.id === id)!;
      const alive = l.scope === "cloud" && !l.name.includes("RunningHub");
      l.lastProbeAt = new Date().toISOString();
      l.lastProbeOk = alive;
      l.lastError = alive ? null : l.scope === "local" ? "后端未运行，探不到模型列表" : "未配置 apiKey 或该接口需要企业级-共享 key（1014）";
      if (alive) l.capabilities = { ...l.capabilities, models: l.capabilities.models.length ? l.capabilities.models : ["default"] };
      return clone(l);
    },
    async pull(id, model) {
      await delay(900);
      const l = store.llms.find((x) => x.id === id)!;
      if (!l.capabilities.supportsPull) throw new Error("该后端没有拉取接口。llama.cpp 请手动把 GGUF 放进 --models-dir");
      if (!l.capabilities.models.includes(model)) l.capabilities.models.push(model);
    },
    async setDefault(id) {
      const l = store.llms.find((x) => x.id === id)!;
      store.llms.filter((x) => x.scope === l.scope && x.id !== id).forEach((x) => (x.isDefault = false));
      l.isDefault = true;
    },
    async defaults() {
      await delay(50);
      return clone(store.defaults);
    },
    async saveDefaults(d) {
      await delay(120);
      store.defaults = clone(d);
    },
  },

  workflows: {
    async list() {
      await delay();
      return clone(store.workflows);
    },
    async get(id) {
      const w = store.workflows.find((x) => x.id === id)!;
      return clone(w);
    },
    async importJson(name, json, instanceId) {
      await delay(700);
      const report = buildImportReport(json, instanceId);
      const w: Workflow = {
        id: uid("wf"),
        name,
        description: "用户导入",
        tags: ["导入"],
        family: report.estimated ? "video" : "image",
        sourceFormat: report.sourceFormat,
        graph: {},
        slots: [],
        isBuiltin: false,
        updatedAt: new Date().toISOString(),
      };
      store.workflows.unshift(w);
      return { workflow: clone(w), report };
    },
    async validate(json, instanceId) {
      await delay(500);
      return buildImportReport(json, instanceId);
    },
    async remove(id) {
      await delay();
      store.workflows = store.workflows.filter((x) => x.id !== id);
    },
    async export(id, format) {
      const w = store.workflows.find((x) => x.id === id)!;
      return JSON.stringify(
        format === "api" ? w.graph : { last_node_id: 92, last_link_id: 40, nodes: [], links: [], groups: [], version: 0.4 },
        null,
        2,
      );
    },
    async slots(id) {
      const w = store.workflows.find((x) => x.id === id);
      return clone(w?.slots ?? []);
    },
    async nodeOverrides(id, values) {
      const w = store.workflows.find((x) => x.id === id)!;
      return (w.slots ?? [])
        .filter((s) => values[s.address] !== undefined)
        .map((s) => ({
          nodeId: s.address.split(".")[0],
          fieldName: s.rhFieldName ?? s.address.split(".").slice(1).join("."),
          fieldValue: values[s.address],
        }));
    },
    async testRun(id, instanceId) {
      return submitJobs([id], instanceId, "workflow_test", null)[0];
    },
  },

  projects: {
    async list() {
      await delay();
      return clone(store.projects);
    },
    async get(id) {
      await delay(80);
      return clone(findProject(id));
    },
    async create(name, synopsis) {
      await delay();
      const p: Project = {
        id: uid("p"),
        name,
        synopsis: synopsis ?? "",
        stage: "script",
        ownerId: store.session?.id ?? null,
        createdAt: new Date().toISOString(),
        updatedAt: new Date().toISOString(),
        config: {
          aspectRatio: "16:9",
          visualStyle: "live-action",
          targetDurationSec: 60,
          outputLanguage: "中文",
          imageTemplate: "qwen_image",
          videoTemplate: "h3_video",
          h3PromptMode: "three_field",
          enhancePrompts: false,
          imageInstanceId: "inst_rh_proxy",
          videoInstanceId: "inst_rh_task",
          llmBackendId: "llm_ollama",
          seedPolicy: "locked",
          resolutionMode: "preview",
          h3WorkflowKey: "h3_t2v",
          continuity: true,
          continuityOverlapFrames: 22,
        },
        data: { rawScript: "", characters: [], scenes: [], shots: [], renderLogs: [] },
      };
      store.projects.unshift(p);
      return clone(p);
    },
    async update(id, patch) {
      const p = findProject(id);
      Object.assign(p, patch);
      touch(p);
      return clone(p);
    },
    async updateData(id, data) {
      const p = findProject(id);
      Object.assign(p.data, data);
      touch(p);
      return clone(p);
    },
    async updateConfig(id, config) {
      const p = findProject(id);
      Object.assign(p.config, config);
      touch(p);
      return clone(p);
    },
    async remove(id) {
      store.projects = store.projects.filter((x) => x.id !== id);
    },
    async duplicate(id) {
      const p = findProject(id);
      const c = structuredClone(p);
      c.id = uid("p");
      c.name = `${p.name} 副本`;
      c.updatedAt = new Date().toISOString();
      store.projects.unshift(c);
      return clone(c);
    },
    async export(id) {
      const p = findProject(id);
      return JSON.stringify({ format: "h3studio.project", version: 1, exportedAt: new Date().toISOString(), project: p, media: store.media.filter((m) => m.projectId === id), skippedBytes: 0 }, null, 2);
    },
    async import(json) {
      const data = JSON.parse(json) as { format?: string; version?: number; project?: Project; media?: Media[] };
      if (data.format !== "h3studio.project" || !data.project) throw new Error("这不是 H3 Studio 的项目导出文件");
      const p = { ...data.project, id: uid("p"), updatedAt: new Date().toISOString() };
      store.projects.unshift(clone(p));
      (data.media ?? []).forEach((m) => store.media.push({ ...clone(m), projectId: p.id }));
      return { project: clone(p), mediaIn: (data.media ?? []).length, mediaSkipped: 0 };
    },
  },

  media: {
    async project(id) {
      await delay();
      return clone(store.media.filter((m) => m.projectId === id));
    },
    async url(m) {
      // 演示模式没有真文件：上传的图读成了 dataURL 就直接显示，
      // 生成类的镜头仍走 MediaFrame 的程序化占位（它本来就是给「还没有产物」画的）
      if (!m) return null;
      return m.path.startsWith("data:") ? m.path : null;
    },
    async put(file, role, refId, projectId) {
      const kind = file.type.startsWith("video") ? "ref_video" : file.type.startsWith("audio") ? "ref_audio" : "ref_image";
      const m: Media = {
        id: uid("m"),
        projectId,
        kind,
        role,
        refId,
        path: `demo://${uid("f")}`,
        mime: file.type || undefined,
        bytes: file.size,
        createdAt: new Date().toISOString(),
      };
      m.path = await new Promise<string>((res, rej) => {
        const fr = new FileReader();
        fr.onload = () => res(String(fr.result));
        fr.onerror = () => rej(fr.error);
        fr.readAsDataURL(file);
      });
      store.media.push(m);
      return clone(m);
    },
    async adopt(m) {
      store.media = store.media.filter((x) => x.id !== m.id).concat(m);
      return clone(m);
    },
    async server(filter) {
      await delay(80);
      return clone(
        store.media.filter(
          (m) =>
            (!filter.projectKey || m.projectId === filter.projectKey) &&
            (!filter.role || m.role === filter.role) &&
            (!filter.refId || m.refId === filter.refId) &&
            (!filter.kind || m.kind === filter.kind) &&
            (!filter.ids?.length || filter.ids.includes(m.id)),
        ),
      );
    },
    async remove(id) {
      store.media = store.media.filter((x) => x.id !== id);
    },
  },

  jobs: {
    async list(filter) {
      await delay(60);
      ensureTicker();
      let js = store.jobs;
      if (filter?.projectId) js = js.filter((j) => j.projectId === filter.projectId);
      if (filter?.state) js = js.filter((j) => j.state === filter.state);
      return clone(js);
    },
    async get(id) {
      const j = store.jobs.find((x) => x.id === id);
      if (!j) throw new Error(`任务 ${id} 不存在`);
      return clone(j);
    },
    async generate(r) {
      return (await mockGenerate([r]))[0];
    },
    async generateBatch(reqs) {
      const jobs = await mockGenerate(reqs);
      return { jobs, errors: [] };
    },
    async plan(reqs) {
      // 演示模式：把后端 job_plan 的三件主要判断照出来（时长/步数越界、没有提示词、没指定实例）
      await delay(300);
      const rows = reqs.map((r, index) => {
        const slots = (r.slots ?? {}) as Record<string, unknown>;
        const seconds = Number(slots.seconds ?? 5);
        const steps = Number(slots.steps ?? 8);
        const prompt = String(slots.prompt ?? "");
        const problems: string[] = [];
        if (!prompt.trim()) problems.push("「视频提示词」是必填的");
        if (seconds > 15) problems.push(`「时长（秒）」${seconds} 超过上限 15`);
        if (steps > 50) problems.push(`「步数」${steps} 超过上限 50`);
        if (!r.instanceId) problems.push("演示模式没有登记实例，必须指明 instanceId");
        const frames = seconds <= 5 ? 5 : 17 * Math.ceil((Math.round(seconds * 24) - 5) / 17) + 5;
        return {
          index,
          title: r.title ?? `${r.template} · ${index + 1}`,
          template: r.template,
          kind: r.kind ?? "video",
          instance: r.instanceId ? { id: r.instanceId, label: "演示实例", placement: "local", protocol: "comfy_native", probeOk: true, circuitOpen: false } : null,
          slots,
          derived: { promptChars: prompt.length, frameCount: frames, realSeconds: Math.round((frames / 24) * 100) / 100, etaSeconds: Math.round((443 / 56) * frames), etaBasis: "演示模式按同一锚点外推" },
          problems,
          blocked: problems.some((p) => p.includes("必填") || p.includes("超过上限") || p.includes("instanceId")),
        };
      });
      const eta = rows.filter((r) => !r.blocked).reduce((a, r) => a + Number(r.derived.etaSeconds || 0), 0);
      return { rows, totals: { count: rows.length, blocked: rows.filter((r) => r.blocked).length, warned: rows.filter((r) => r.problems.length && !r.blocked).length, etaSeconds: eta, etaMinutes: Math.round((eta / 60) * 10) / 10, etaIsEstimate: true } };
    },
    async cancel(id) {
      await delay();
      const j = store.jobs.find((x) => x.id === id)!;
      j.state = "canceled";
      j.finishedAt = new Date().toISOString();
      j.log.push({ ts: j.finishedAt, level: "warn", msg: "已发送 POST /interrupt，等待实例确认" });
      syncShotStates();
    },
    async retry(id) {
      await delay();
      const j = store.jobs.find((x) => x.id === id)!;
      j.state = "queued";
      j.attempts += 1;
      j.error = null;
      j.progress = {};
      j.queuePos = 1;
      j.startedAt = null;
      j.finishedAt = null;
      j.log.push({ ts: new Date().toISOString(), level: "info", msg: `第 ${j.attempts} 次尝试` });
      setTimeout(() => {
        j.state = "running";
        j.startedAt = new Date().toISOString();
        j.progress = j.instanceId === "inst_rh_task" ? { unavailable: true, stage: "排队中" } : { value: 0, max: 40 };
      }, 1200);
      return clone(j);
    },
    async setPriority(id, priority) {
      const j = store.jobs.find((x) => x.id === id)!;
      j.priority = priority;
    },
  },

  styles: {
    async list() {
      const { VISUAL_STYLES } = await import("../constants");
      return clone(VISUAL_STYLES);
    },
  },

  users: {
    async list() {
      await delay();
      return clone(store.users);
    },
    async create(body) {
      await delay();
      if (store.users.some((u) => u.username === body.username)) throw new Error("用户名已存在");
      const u: User = {
        id: uid("u"),
        username: body.username,
        displayName: body.displayName,
        role: body.role,
        isActive: true,
        quota: { concurrentJobs: body.role === "editor" ? 2 : 0 },
        usage: { jobsToday: 0 },
        lastLoginAt: null,
      };
      store.users.push(u);
      return clone(u);
    },
    async update(id, body) {
      await delay();
      const u = store.users.find((x) => x.id === id)!;
      Object.assign(u, body);
      return clone(u);
    },
    async remove(id) {
      await delay();
      store.users = store.users.filter((x) => x.id !== id);
    },
  },

  system: {
    async storage() {
      await delay();
      const mediaBytes = store.media.reduce((a, m) => a + (m.bytes ?? 0), 0);
      return {
        mediaBytes,
        tmpBytes: 1_024_000_000,
        freeBytes: 494 * 1024 ** 3,
        mediaCount: store.media.length,
        root: "F:/H3/data/media（演示模式不落盘）",
        byProject: store.projects.map((p) => ({
          projectId: p.id,
          name: p.name,
          bytes: store.media.filter((m) => m.projectId === p.id).reduce((a, m) => a + (m.bytes ?? 0), 0),
          count: store.media.filter((m) => m.projectId === p.id).length,
        })),
      };
    },
    async gc(dryRun) {
      await delay(400);
      return { reclaimableBytes: 3_600_000_000, orphans: dryRun ? 7 : 3, dryRun };
    },
    async gpu() {
      await delay(40);
      const rendering = store.jobs.some((j) => j.state === "running" || j.state === "dispatching");
      return { enabled: true, llmRunning: !rendering, llmPort: 8080, llmPid: null, yielded: rendering, renderingLocally: rendering, llmCalling: false };
    },
    async gpuYield() {
      await delay(120);
      return { yielded: true, reason: "演示模式不会真的停进程" };
    },
    async gpuRestore() {
      await delay(120);
      return { restored: true, reason: "演示模式不会真的起进程" };
    },
  },

  exports: {
    async merge(projectId, mediaIds, title) {
      await delay(900);
      const id = uid("m");
      store.media.push({
        id,
        projectId,
        kind: "video",
        role: "export",
        refId: title ?? "成片",
        path: `demo://${id}`,
        bytes: 4_200_000 * Math.max(1, mediaIds.length),
        mime: "video/mp4",
        createdAt: new Date().toISOString(),
      } as Media);
      return { mediaId: id, url: `/api/media/${id}/download`, bytes: 4_200_000 * Math.max(1, mediaIds.length), mode: "copy", segments: mediaIds.length };
    },
    async pack(projectId, items, title) {
      await delay(700);
      const id = uid("m");
      store.media.push({
        id,
        projectId,
        kind: "archive",
        role: "export",
        refId: title ?? "素材包",
        path: `demo://${id}.zip`,
        bytes: 12_000_000 + (items?.length ?? 0) * 1_200_000,
        mime: "application/zip",
        createdAt: new Date().toISOString(),
      } as Media);
      return { mediaId: id, url: `/api/media/${id}/download`, bytes: 12_000_000 + (items?.length ?? 0) * 1_200_000, segments: items?.length, mode: title ?? "assets" };
    },
    async edl(projectId, shots, title) {
      await delay(200);
      let cursor = 0;
      const lines = [`TITLE: ${title ?? projectId}`, "FCM: NON-DROP FRAME", ""];
      (shots ?? []).forEach((s, i) => {
        lines.push(`${String(i + 1).padStart(3, "0")}  SHOT_${String(s.index).padStart(3, "0")}   V     C        00:00:00:00 ${tc(s.durationSec)} ${tc(cursor)} ${tc(cursor + s.durationSec)}`);
        cursor += s.durationSec;
      });
      return { format: "edl", text: lines.join("\n") };
    },
    async xml(projectId, shots, title) {
      await delay(200);
      const body = (shots ?? []).map((s) => `  <clip><name>SHOT ${String(s.index).padStart(3, "0")}</name><duration>${s.durationSec}</duration></clip>`).join("\n");
      return { format: "xml", text: `<?xml version="1.0"?>\n<sequence id="${projectId}" name="${title ?? projectId}">\n${body}\n</sequence>\n` };
    },
    async jianying() {
      throw new Error("剪映草稿还没接通：需要装 pyJianYingDraft（PLAN.md §11 M6）。现在先用 EDL / XML。");
    },
  },
};

function submitJobs(refIds: string[], instanceId: string, kind: Job["kind"], projectId: string | null, workflowId?: string, priority = 100): Job[] {
  const inst = store.instances.find((i) => i.id === instanceId);
  const out: Job[] = [];
  for (const ref of refIds) {
    const j: Job = {
      id: uid("job"),
      projectId,
      kind,
      state: "queued",
      priority,
      title: kind === "workflow_test" ? `试运行 · ${ref}` : `${ref.replace("shot_", "镜 ")} · ${kind === "image" ? "图像" : "视频"}`,
      instanceId,
      workflowId: workflowId ?? null,
      promptId: crypto.randomUUID(),
      progress: inst?.protocol === "rh_task" ? { unavailable: true, stage: "排队中" } : { value: 0, max: 40 },
      queuePos: 1,
      attempts: 1,
      outputMediaIds: [],
      log: [
        {
          ts: new Date().toISOString(),
          level: "info",
          msg:
            inst?.protocol === "rh_task"
              ? "已注入 seed（RH 会强制重置）；结果链接约 1 天过期，完成即转存"
              : "client_id 已同时用于 /prompt 与 /ws?clientId",
        },
      ],
    };
    store.jobs.unshift(j);
    out.push(j);
    setTimeout(() => {
      j.state = "running";
      j.startedAt = new Date().toISOString();
      j.queuePos = null;
    }, 1500 + Math.random() * 1500);
  }
  return out;
}

/** 演示模式的一次生成：复用 submitJobs + ticker，产物由 finishJob 造 */
async function mockGenerate(reqs: GenerateRequest[]): Promise<Job[]> {
  await delay(200);
  ensureTicker();
  const out: Job[] = [];
  for (const r of reqs) {
    const kind: Job["kind"] = r.kind ?? (r.template.includes("video") ? "video" : "image");
    const ref = r.meta?.refId ?? r.title ?? r.template;
    const instanceId = r.instanceId ?? store.instances.find((i) => i.placement === "local")?.id ?? store.instances[0]?.id ?? "";
    const j = submitJobs([ref], instanceId, kind, r.projectId, undefined, r.priority ?? 100)[0];
    j.title = r.title ?? `${kind === "video" ? "视频" : "图片"} · ${ref}`;
    out.push(j);
  }
  return clone(out);
}

/** 秒 → HH:MM:SS:FF，EDL 用 */
function tc(seconds: number): string {
  const t = Math.max(0, seconds);
  const pad = (n: number) => String(Math.floor(n)).padStart(2, "0");
  return `${pad(t / 3600)}:${pad((t % 3600) / 60)}:${pad(t % 60)}:${pad(Math.round((t % 1) * 30))}`;
}

function buildImportReport(json: string, instanceId?: string): ImportReport {
  let parsed: unknown;
  try {
    parsed = JSON.parse(json);
  } catch {
    return {
      valid: false,
      sourceFormat: "api",
      errors: [{ message: "不是合法 JSON" }],
      warnings: [],
      unknownNodes: [],
      runninghubOnlyNodes: [],
      missingModels: [],
      missingCustomNodes: [],
      slotCount: 0,
    };
  }
  const obj = parsed as Record<string, unknown>;
  const isUi = Array.isArray(obj?.nodes);
  const classes = isUi
    ? (obj.nodes as { type?: string }[]).map((n) => n.type ?? "").filter(Boolean)
    : Object.keys(obj ?? {}).map((k) => (obj[k] as { class_type?: string })?.class_type ?? "").filter(Boolean);

  const KNOWN = new Set([
    "UNETLoader", "CLIPLoader", "VAELoader", "KSamplerSelect", "BasicScheduler", "RandomNoise", "BasicGuider",
    "SamplerCustomAdvanced", "VAEDecode", "VAEDecodeAudio", "CreateVideo", "SaveVideo", "SaveImage",
    "MiniMaxH3ImageToVideo", "MiniMaxH3SigmaShift", "MiniMaxH3ReferenceToVideo", "EmptyMiniMaxH3LatentAV",
    "TextEncodeQwenImage21", "EmptySD3LatentImage", "CLIPTextEncode", "VAEEncode",
  ]);
  const RH_ONLY = new Set(["RHLoraLoader", "RHImage2Video", "RHUpscale"]);
  const COMMUNITY = new Set(["PathchSageAttentionKJ", "ComfyMathExpression", "ResolutionSelector", "PrimitiveFloat", "MiniMaxH3TurboSampler"]);

  const unknown = classes.filter((c) => !KNOWN.has(c));
  const rh = unknown.filter((c) => RH_ONLY.has(c));
  const community = unknown.filter((c) => !RH_ONLY.has(c) && COMMUNITY.has(c));
  const other = unknown.filter((c) => !RH_ONLY.has(c) && !COMMUNITY.has(c));
  const inst = store.instances.find((i) => i.id === instanceId);
  const needsH3 = classes.includes("MiniMaxH3ImageToVideo") || classes.includes("MiniMaxH3ReferenceToVideo");
  const warnings: string[] = [];
  if (isUi) warnings.push("这是 UI 格式，我们已转成 API 格式；widgets_values 的顺序依赖 object_info 的 input_order");
  if (community.length) warnings.push(`这些是社区节点，缺失会在 POST /prompt 的 node_errors 里报出来：${community.join(", ")}`);
  if (needsH3 && inst?.protocol === "comfy_native" && inst.baseUrl.includes("127.0.0.1")) {
    warnings.push("目标实例是本机 ComfyUI，但当前未探活成功 —— H3 需要 ComfyUI ≥ 0.30.0");
  }
  return {
    valid: unknown.length === 0,
    sourceFormat: isUi ? "ui" : "api",
    errors: other.map((c) => ({ classType: c, message: `实例上不存在该节点：${c}` })),
    warnings,
    unknownNodes: unknown,
    runninghubOnlyNodes: rh,
    missingModels: needsH3
      ? [
          { folder: "diffusion_models", filename: "minimax_h3_fl2va_pruned_int8_convrot.safetensors" },
          { folder: "text_encoders", filename: "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors" },
        ]
      : [],
    missingCustomNodes: community,
    slotCount: isUi ? (obj.nodes as unknown[])?.length ?? 0 : Object.keys(obj ?? {}).length,
    estimated: { seconds: 5, frames: 124, width: 864, height: 480, steps: 25 },
    installPlan: community.length ? [`Comfy-Manager 安装：${community.join(" / ")}`] : undefined,
  };
}
