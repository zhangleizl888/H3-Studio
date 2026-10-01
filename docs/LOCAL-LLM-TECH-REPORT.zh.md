# Web 应用的本地 LLM 文本生成 — 技术报告

> **本文件是中文版**，逐句翻译自同目录的 `LOCAL-LLM-TECH-REPORT.md`（英文原文，1440 行，未作改动）。
> 两份文件行号一一对应：标题 99 个、代码块 118 个围栏行、表格列数与 ✅/⚠️/🔴 置信标记数量完全一致。
> 代码、命令、参数、端点路径、环境变量、模型标签、URL、端口一律保持英文原样，未翻译。
> 翻译**没有**修正原文的任何事实判断。已知一处过时：原文写"当前 Ollama 版本 v0.33.3"，实际最新为 **v0.34.4**（本机装的是 0.32.13）。以 `PLAN.md` 的核查记录为准。

**日期：** 2026-09-29
**范围：** Ollama 本地 API、面向中文创意写作 / 分镜 / prompt 工程的本地文本模型选型、Windows 上的 ComfyUI，以及统一抽象层之下的替代 LLM 后端。

---

## 0. 信息来源与置信度

本报告按照验证方式来区分各项论断，因为 2026 年的模型领域被大量 AI 生成的 SEO 内容严重污染，不同站点之间甚至自相矛盾。

| 标记 | 含义 |
| --- | --- |
| ✅ **已核实** | 本次会话直接从一手来源获取（`docs.ollama.com` OpenAPI 规范、`docs.comfy.org`、`github.com/ggml-org/llama.cpp` README、`docs.vllm.ai`、`lmstudio.ai/docs`、`ollama.com/library`）。 |
| ⚠️ **部分核实** | 一手来源确认该模型系列存在，但未确认具体论断（尺寸、显存、排名）。 |
| 🔴 **未核实** | 仅出现在二手 / SEO / 聚合类来源中。当作假设对待，在据此构建任何东西之前先本地验证。 |

> **你要求的那次从 `raw.githubusercontent.com` 抓取 `docs/api.md` 失败了**（传输错误）。Ollama 已把他们的 API 参考迁移到 `docs.ollama.com`，该站会为每个端点输出一份实时的 OpenAPI 3.1 规范 —— 这严格更优，本报告正是基于它构建的。GitHub 上遗留的 `docs/api.md` 现在只是一个占位桩，内容为：*"Note: Ollama's API docs are moving to https://docs.ollama.com/api"*。

**撰写时的当前 Ollama 版本：v0.33.3**（发布于 2026-09-02）。✅

---

# 1. Ollama 本地 API

## 1.1 主机、端口与环境变量

### 默认绑定 ✅

```
127.0.0.1:11434
```

Ollama 默认只绑定 loopback 回环地址。OpenAPI 规范中声明 `servers: [{ url: "http://localhost:11434" }]`。

### 环境变量 ✅

| 变量 | 默认值 | 用途 |
| --- | --- | --- |
| `OLLAMA_HOST` | `127.0.0.1:11434` | 绑定地址**以及端口**。接受 `host:port` 或完整 URL（`https://localhost:443`）。 |
| `OLLAMA_ORIGINS` | `localhost, 127.0.0.1, 0.0.0.0` 加上 `app://`、`file://`、`tauri://` | **逗号分隔**的 CORS 允许列表。 |
| `OLLAMA_MODELS` | `~/.ollama/models`（Win：`%USERPROFILE%\.ollama\models`） | 模型 blob 存储目录。需在首次 pull 之前设置。 |
| `OLLAMA_CONTEXT_LENGTH` | `auto`（取决于显存 —— 见 1.6） | 默认的 `num_ctx`。 |
| `OLLAMA_KEEP_ALIVE` | `5m` | `keep_alive` 的全局默认值。`0` = 立即卸载，`-1` = 永久驻留。 |
| `OLLAMA_NUM_PARALLEL` | `1` | 并发请求槽位数。**每个槽位都会分配自己的一份 KV 缓存。** |
| `OLLAMA_MAX_LOADED_MODELS` | `0`（无限制） | 限制每块 GPU 上常驻模型的数量。 |
| `OLLAMA_MAX_QUEUE` | `512` | 所有槽位都繁忙时的队列深度。 |
| `OLLAMA_LOAD_TIMEOUT` | `5m` | 模型加载超时时间。 |
| `OLLAMA_GPU_OVERHEAD` | `0`（字节） | 每块 GPU 的显存预留量。设为 `2147483648` 可预留 2 GiB。 |
| `OLLAMA_SCHED_SPREAD` | `false` | 把各层分散到所有 GPU 上。 |
| `OLLAMA_FLASH_ATTENTION` | `false` | Flash-attention 后端。 |
| `OLLAMA_KV_CACHE_TYPE` | `f16` | KV 缓存量化格式。接受 `f16`、`q8_0`、`q4_0`。**在 8–16 GB 显存的卡上跑长上下文的关键抓手。** |
| `OLLAMA_MULTIUSER_CACHE` | `false` | 在多用户之间获得更高的 prompt 缓存命中率。 |
| `OLLAMA_NO_CLOUD` | `false` | 设为 `1` 可彻底禁用云端模型 + 联网搜索。 |
| `OLLAMA_DEBUG` | `false` | `1` = debug，`2` = trace。 |
| `OLLAMA_VULKAN` | `false` | 实验性 Vulkan 后端（AMD 的兜底方案）。 |
| `OLLAMA_NOPRUNE` | `false` | 启动时不回收（GC）孤立的 blob。 |
| `OLLAMA_REMOTES` | `ollama.com` | 允许远程 pull 的主机白名单。 |
| `OLLAMA_LLM_LIBRARY` | auto | 覆盖 runner 库路径。 |
| `HTTPS_PROXY` | — | 出站 pull 的代理。**不要设置 `HTTP_PROXY`** —— 它会破坏本地客户端监听器。 |
| `CUDA_VISIBLE_DEVICES` | — | 限定可用的 NVIDIA GPU。`-1` = 仅使用 CPU。 |
| `GGML_VK_VISIBLE_DEVICES` | — | 限定可用的 Vulkan GPU；在 iGPU/dGPU 混合的系统上设为独显索引。 |
| `HSA_OVERRIDE_GFX_VERSION` | — | AMD 架构覆盖（Linux）。 |

**CORS 行为（那个确切的大坑）：** Ollama 默认允许 `127.0.0.1` 和 `0.0.0.0` 来源。运行在 `http://localhost:3000` 上的 Web 应用会被**拦截**，直到你这样设置：

```powershell
# PowerShell — session only
$env:OLLAMA_ORIGINS = "http://localhost:3000"
ollama serve
```

```powershell
# PowerShell — persistent (user scope). Quit the tray app first, then relaunch.
[Environment]::SetEnvironmentVariable("OLLAMA_ORIGINS", "http://localhost:3000", "User")
```

允许所有浏览器扩展（一个已知的坑 —— 见 1.7）：
```powershell
$env:OLLAMA_ORIGINS = "chrome-extension://*,moz-extension://*,safari-web-extension://*"
```

此前悬而未决的问题已有定论：`OLLAMA_ORIGINS` 是在默认值的基础上**追加**，而不是替换它们（ollama/ollama#6389，该 issue 仍处于 open 状态）。它并不是严格的覆盖。

---

## 1.2 `GET /api/tags` — 列出本地已安装的模型 ✅

```http
GET http://localhost:11434/api/tags
```

响应：
```json
{
  "models": [
    {
      "name": "gemma4",
      "model": "gemma4",
      "modified_at": "2025-10-03T23:34:03.409490317-07:00",
      "size": 9608350245,
      "digest": "c6eb396dbd5992bbe3f5cdb947e8bbc0ee413d7c17e2beaae69f5d569cf982eb",
      "details": {
        "format": "gguf",
        "family": "gemma4",
        "families": ["gemma4"],
        "parameter_size": "8.0B",
        "quantization_level": "Q4_K_M"
      }
    }
  ]
}
```

由云端支撑的条目上会多出这些字段：`remote_model`、`remote_host`。

**配套端点：**
- `GET /api/version` → `{"version":"0.12.6"}` —— 用于做能力门控。
- `GET /api/ps` → 当前常驻内存的模型，包含 `PROCESSOR` 拆分情况和 `CONTEXT`。这是检测 CPU 卸载的唯一可靠办法。

```bash
ollama ps
```
```
NAME             ID              SIZE      PROCESSOR    CONTEXT    UNTIL
gemma4:latest    c6eb396dbd59    9.6 GB    100% GPU     131072     2 minutes from now
```

---

## 1.3 `POST /api/chat` — 完整请求结构 ✅

```jsonc
{
  "model": "qwen3:8b",              // required
  "messages": [                     // required
    { "role": "system",    "content": "You are a 分镜师..." },
    { "role": "user",      "content": "把这段小说拆成 8 个分镜" },
    { "role": "assistant", "content": "...", "tool_calls": [ /* ... */ ] },
    { "role": "tool",      "content": "..." },
    { "role": "user",      "content": "参考这张图", "images": ["<base64>"] }
  ],

  "tools": [
    {
      "type": "function",
      "function": {
        "name": "get_current_weather",
        "description": "Get the current weather for a location",
        "parameters": {
          "type": "object",
          "properties": {
            "location":       { "type": "string", "description": "City, e.g. 上海" },
            "format":         { "type": "string", "enum": ["celsius", "fahrenheit"] }
          },
          "required": ["location", "format"]
        }
      }
    }
  ],

  "format": { /* "json" OR a JSON Schema object — see 1.5 */ },

  "options": {
    "seed": 42,
    "temperature": 0.9,
    "top_k": 60,
    "top_p": 0.92,
    "min_p": 0.05,
    "stop": ["<|im_end|>", "\n\n\n"],
    "num_ctx": 32768,
    "num_predict": 2048
  },

  "stream": false,
  "think": false,                  // true | false | "low" | "high" | null
  "keep_alive": "30m",             // string duration OR number; 0 = unload now
  "logprobs": true,                // returns per-token logprobs
  "top_logprobs": 3                // alternatives per position
}
```

`role` 的取值是 `system | user | assistant | tool` 之一。
`think` 接受 `true | false | null | <string>`。**明确不支持数字型的思考等级。** 通过 `/api/show` 来发现各模型合法的字符串名称及其默认值。

### 1.3.1 `stream: false` 响应 ✅

```http
HTTP/1.1 200 OK
Content-Type: application/json
```

```json
{
  "model": "gemma4",
  "created_at": "2025-10-17T23:14:07.414671Z",
  "message": {
    "role": "assistant",
    "content": "Hello! How can I help you today?"
  },
  "done": true,
  "done_reason": "stop",
  "total_duration": 174560334,
  "load_duration": 101397084,
  "prompt_eval_count": 11,
  "prompt_eval_cached_count": 0,
  "prompt_eval_duration": 13074791,
  "eval_count": 18,
  "eval_duration": 52479709
}
```

启用后 `message` 上的可选字段：`thinking`（推理轨迹）、`tool_calls[]`、`images[]`。顶层可选字段：`logprobs[]`。

**所有 duration 字段的单位都是纳秒。** ✅ 这是集成过程中最常见的那个 bug。派生指标：

```ts
const tokPerSec = eval_count / (eval_duration      / 1e9);
const ttftMs    = load_duration / 1e6 + prompt_eval_duration / 1e6;
const prefillTps= prompt_eval_count / (prompt_eval_duration / 1e9);
const cacheHit  = prompt_eval_cached_count / prompt_eval_count;
```

`done_reason` 观测到的取值：`"stop"`（自然 EOS 或命中 stop 序列）、`"length"`（达到 `num_predict`），以及 `"load"`（出现在不带 prompt 的纯预热调用上）。

### 1.3.2 `stream: true` 响应 ✅

```http
HTTP/1.1 200 OK
Content-Type: application/x-ndjson
```

> ⚠️ **这是 NDJSON，不是 SSE。** 这里**没有 `data: ` 前缀**，也**没有空行分隔符**。不要使用 `EventSource`/SSE 解析器。请改用 `fetch` + `ReadableStream` 并按 `\n` 切分，或者用 `response.body` 的逐行读取器。

每一行都是一个 `ChatStreamEvent`：

```jsonl
{"model":"gemma4","created_at":"2025-10-26T17:15:24.097767Z","message":{"role":"assistant","content":"That"},"done":false}
{"model":"gemma4","created_at":"2025-10-26T17:15:24.109172Z","message":{"role":"assistant","content":"'"},"done":false}
{"model":"gemma4","created_at":"2025-10-26T17:15:24.121485Z","message":{"role":"assistant","content":"s"},"done":false}
{"model":"gemma4","created_at":"2025-10-26T17:15:24.132802Z","message":{"role":"assistant","content":" a"},"done":false}
{"model":"gemma4","created_at":"2025-10-26T17:15:24.143931Z","message":{"role":"assistant","content":" fantastic"},"done":false}
{"model":"gemma4","created_at":"2025-10-26T17:15:24.155176Z","message":{"role":"assistant","content":" question"},"done":false}
{"model":"gemma4","created_at":"2025-10-26T17:15:24.166576Z","message":{"role":"assistant","content":"!"},"done":true,"done_reason":"stop"}
```

关键的分块传输（streaming）规则：✅
- `stream` 的默认值是 **`true`**。想要拿到缓冲式响应，必须显式发送 `"stream": false`。
- 除最后一片外，每个分块的 `done` 都是 `false`。
- **最后一片携带统计数据**（`done_reason`、`total_duration`、`eval_count` 等），并且通常 `content` 为**空**。要跨分块累加 `content`；只在 `done: true` 的那一帧上读取指标。
- 在 `think: true` 时，`message.thinking` 会与 `message.content` 并行流出。它们是两个相互独立的字段 —— 不要把它们合并。
- 工具调用以 `message.tool_calls` 的部分片段形式流式返回。

```ts
// Correct NDJSON reader for Ollama
async function* ollamaNdjson(res: Response) {
  const reader = res.body!.getReader();
  const dec = new TextDecoder();
  let buf = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let i;
    while ((i = buf.indexOf("\n")) >= 0) {
      const line = buf.slice(0, i).trim();
      buf = buf.slice(i + 1);
      if (line) yield JSON.parse(line);
    }
  }
  if (buf.trim()) yield JSON.parse(buf);
}
```

---

## 1.4 `POST /api/generate` — 文本补全 ✅

单轮、原始 prompt、不套用 chat template。与 `/api/chat` 的差异如下：

| | `/api/generate` | `/api/chat` |
| --- | --- | --- |
| 文本字段 | `prompt`（FIM 时另有 `suffix`） | `messages[]` |
| 系统提示词 | 顶层的 `system` 字符串 | `role: "system"` 消息 |
| 响应文本 | `response` | `message.content` |
| 思考轨迹 | 顶层 `thinking` | `message.thinking` |
| 绕过模板 | `raw: true` | 不适用 |

`generate` 独有的附加字段：`raw`（跳过全部 prompt 模板化）、`suffix`（中间填充 FIM）、`system`。

预热 / 卸载技巧 —— 不需要 prompt：
```bash
# Load only
curl http://localhost:11434/api/generate -d '{"model":"gemma4"}'
# Unload immediately
curl http://localhost:11434/api/generate -d '{"model":"gemma4","keep_alive":0}'
```

其响应结构与 `ChatResponse` 一致，只是用 `response` 取代了 `message`。

**针对 Web 应用的建议：** 所有地方都使用 `/api/chat`。`/api/generate` 只用于 FIM、原始模型试验，以及预热/卸载。

---

## 1.5 向量化：`/api/embed`（当前）与 `/api/embeddings`（旧版）

> 你要求的是 `/api/embeddings`。**它已被弃用，而且其契约在两处存在破坏性差异。**请实现 `/api/embed`。

### `POST /api/embed` ✅

```jsonc
{
  "model": "embeddinggemma",              // required
  "input": "Why is the sky blue?",       // string OR string[]
  "truncate": true,                      // default true; false => 400 on overlong input
  "dimensions": 128,                     // optional Matryoshka truncation
  "keep_alive": "5m",
  "options": { /* same ModelOptions */ }
}
```

```json
{
  "model": "embeddinggemma",
  "embeddings": [
    [0.010071029, -0.0017594862, 0.05007221, "...", -0.031952348]
  ],
  "total_duration": 14143917,
  "load_duration": 1019500,
  "prompt_eval_count": 8
}
```

`embeddings` 始终是 `number[][]` — **即使输入是单个字符串也返回数组**。向量已经过 **L2 归一化**（单位长度）✅，因此普通点积等价于余弦相似度。

### `POST /api/embeddings`（已弃用）✅

以下差异会让你踩坑：
1. 输入键是 **`prompt`**，而不是 `input`。
2. **只接受单个字符串** — 传入数组会得到 `{"embedding": []}`（静默返回空，并不报错）✅ *（已在 ollama/ollama#7242 与 #13020 中确认）*。
3. 响应为 `{"embedding": number[]}` — 一个**扁平**向量，键名为单数。
4. 其 L2 归一化方式与 `/api/embed` 并不相同。

```json
{"embedding": [0.5670403838157654, 0.009260174818336964, 0.23178744316101074, "..."]}
```

若要兼容 OpenAI 客户端，请改用 **`/v1/embeddings`** ✅。

**推荐的向量化模型** ✅：`embeddinggemma`（300M）、`qwen3-embedding`、`all-minilm`、`bge-m3`（中文/多语言效果最佳）。

---

## 1.6 `POST /api/show` — 能力发现 ✅

```json
{"model": "gpt-oss", "verbose": false}
```

```json
{
  "parameters": "temperature 0.7\nnum_ctx 2048\n",
  "license": "...",
  "capabilities": ["completion", "thinking", "vision"],
  "thinking": {
    "values": [false, true],
    "default": true
  },
  "modified_at": "2025-08-14T15:49:43.634137516-07:00",
  "details": {
    "parent_model": "",
    "format": "gguf",
    "family": "gemma4",
    "families": ["gemma4"],
    "parameter_size": "8.0B",
    "quantization_level": "Q4_K_M"
  },
  "template": "<start_of_turn>...",
  "model_info": {
    "gemma4.context_length": 131072,
    "gemma4.block_count": 42,
    "general.architecture": "gemma4",
    "...": "..."
  }
}
```

为什么你的应用**必须**在启动时调用它，而不是硬编码：
- `capabilities` 会告诉你 `tools`、`vision`、`thinking` 是否真的可用。
- `thinking.values` / `thinking.default` 是该模型**唯一**合法的 `think` 取值。`values: [false]` 的模型完全没有思考能力。
- `model_info["*.context_length"]` 才是模型真正的最大值 — 超出后会静默截断。
- `details.parameter_size` / `quantization_level` 用于驱动模型选择器中的显存估算。

`"verbose": true` 会加入体积极大的 `model_info` 字段（分词器词表、rope 表）。

---

## 1.7 `options` 参考

依据 OpenAPI 的 `ModelOptions` schema 核实，该 schema 为 `additionalProperties: true` — 此处未列出的 llama.cpp 采样器参数仍然会被接受。

| 键 | 类型 | 说明 |
| --- | --- | --- |
| `seed` | int | 与固定的 `temperature`/`top_k` 配合使用时可复现输出。 |
| `temperature` | float | 默认 0.7。**结构化输出用 0.0–0.3；创意正文用 0.9–1.1。** |
| `top_k` | int | 硬性 top-K 截断。中文正文常用 40–80；0 表示禁用。 |
| `top_p` | float | 核采样。常用 0.9–0.95。 |
| `min_p` | float | 最低概率下限；对抑制重复很有效。 |
| `stop` | string \| string[] | 停止序列。**中文聊天模板请一并包含该模型的 EOS/stop 词元** 。 |
| `num_ctx` | int | 上下文窗口。见下方 1.6 的警告。 |
| `num_predict` | int | 生成的最大词元数。`-1` = 不限制。 |
| `repeat_penalty` | float | 不在 schema 中但会被接受。中文约 1.05–1.15。 |
| `repeat_last_n` | int | 不在 schema 中但会被接受。 |
| `mirostat`、`mirostat_tau`、`mirostat_eta` | num | 不在 schema 中但会被接受。 |

### ⚠️ `num_ctx` 陷阱 — 本报告中最重要的一点 ✅

**Ollama 依据总显存而非模型大小来自动选择 `num_ctx`：**

| 显存 | 默认 `num_ctx` |
| --- | --- |
| < 24 GiB | **4,096** |
| 24–48 GiB | 32,768 |
| ≥ 48 GiB | 262,144 |

在 RTX 4060 Ti 16 GB 或 3060 12 GB 上，一次 `qwen3:8b` 的创意写作会话默认从 **4k 上下文**开始。仅一份故事大纲加上几个分镜就会超出该上限，并在句子中间被静默截断。Ollama 自己的指引是：*“需要大上下文的任务……应至少设置为 64000 词元。”*

三种修复方式，按推荐程度排序：

```jsonc
// 1. Per-request (best for a multi-tenant web app)
{"model":"qwen3:8b","messages":[...],"options":{"num_ctx":32768}}
```

```powershell
# 2. Server-wide default
$env:OLLAMA_CONTEXT_LENGTH = "32768"; ollama serve
```

```
# 3. Bake it into a model (survives every client)
FROM qwen3:8b
PARAMETER num_ctx 32768
PARAMETER temperature 0.95
PARAMETER top_p 0.92
```
```bash
ollama create storywriter -f Modelfile
```

**显存预算** — 权重 + KV 缓存。KV 缓存随 `num_ctx` 和 `OLLAMA_NUM_PARALLEL` 线性增长：

| 模型（Q4_K_M） | 权重 | 4k ctx | 16k ctx | 32k ctx |
| --- | --- | --- | --- | --- |
| `qwen3:8b` | 5.2 GB | ~5.6 GB | ~7.0 GB | ~8.8 GB |
| `qwen3:14b` | 9.3 GB | ~9.7 GB | ~11.1 GB | ~12.9 GB |
| `gemma3:12b` | 8.1 GB | ~8.5 GB | ~9.9 GB | ~11.7 GB |

🔴 *（估算值 — KV 列由算术推算得出，请用 `ollama ps` 核实。）*

如果显存吃紧，设置 `OLLAMA_KV_CACHE_TYPE=q8_0` 并把 `OLLAMA_NUM_PARALLEL=1`，可大致减半 KV 缓存，而对正文质量的影响可忽略不计。

---

## 1.8 通过 `format` 实现结构化输出

`format` 是一个**顶层字段，不在 `options` 内部**，接受字符串或内联的 JSON Schema 对象。✅

### 模式 A — `format: "json"`（schema 未知）

```json
{
  "model": "gpt-oss",
  "messages": [{"role":"user","content":"用一句话介绍加拿大。"}],
  "stream": false,
  "format": "json"
}
```

### 模式 B — `format: { <JSON Schema> }`（受语法约束）✅

分镜提取就该用这一种。Ollama 会把该 schema 转换为解码语法，因此输出**保证**可以被解析 — 采样器是被约束的，而不只是靠提示词。

```json
{
  "model": "gpt-oss",
  "messages": [
    {"role":"user","content":"把这段小说拆成 8 个分镜。\n\n本章 JSON Schema:\n{\"type\":\"object\",\"properties\":{\"shots\":{\"type\":\"array\",\"items\":{\"type\":\"object\"}}},\"required\":[\"shots\"]}"}
  ],
  "stream": false,
  "options": {"temperature": 0, "seed": 42},
  "format": {
    "type": "object",
    "properties": {
      "shots": {
        "type": "array",
        "minItems": 8,
        "maxItems": 8,
        "items": {
          "type": "object",
          "properties": {
            "shot_id":    { "type": "integer" },
            "slug":       { "type": "string",  "description": "英文短标识, snake_case" },
            "duration_s": { "type": "number",  "description": "镜头时长(秒)" },
            "shot_size":  { "type": "string",  "enum": ["特写","近景","中景","全景","远景"] },
            "camera":     { "type": "string",  "description": "运镜方式" },
            "location":   { "type": "string" },
            "time_of_day":{ "type": "string",  "enum": ["黎明","白天","黄昏","夜晚"] },
            "action":     { "type": "string" },
            "dialogue":   { "type": "string" },
            "image_prompt": { "type": "string", "description": "英文, for SDXL/FLUX" },
            "negative_prompt": { "type": "string" }
          },
          "required": ["shot_id","slug","duration_s","shot_size","camera","location","time_of_day","action","image_prompt"],
          "additionalProperties": false
        }
      }
    },
    "required": ["shots"],
    "additionalProperties": false
  }
}
```

说明：✅
- Ollama 的官方建议：*“最好同时把 JSON Schema 以字符串形式写进提示词，用来锚定模型的答复。”* 语法只约束句法，不约束语义。
- 做确定性提取时把 `temperature` 设为 0。
- 在 `/api/generate` 上行为完全一致。
- **在 Ollama Cloud 上不受支持。**
- 在 OpenAI 兼容 API 上通过 `response_format` 得到相同的行为。

库封装辅助：

```python
from pydantic import BaseModel
from ollama import chat

class Shot(BaseModel):
    shot_id: int
    slug: str
    duration_s: float
    shot_size: str

class Storyboard(BaseModel):
    shots: list[Shot]

res = chat(model="qwen3:8b",
           messages=[{"role":"user","content":"拆解成 8 个分镜..."}],
           format=Storyboard.model_json_schema(),
           options={"temperature": 0})
board = Storyboard.model_validate_json(res.message.content)
```

```ts
import * as z from "zod";
const Shot = z.object({ shot_id: z.number(), slug: z.string(), duration_s: z.number() });
const res = await ollama.chat({ model: "qwen3:8b", messages, format: z.toJSONSchema(Storyboard) });
const board = Storyboard.parse(JSON.parse(res.message.content));
```

**对于创意写作类应用，请使用两遍式模式：**先用低温度、受 schema 约束的一遍提取分镜列表，再对每个分镜用高温度、非结构化的一遍撰写实际正文与对白。

---

## 1.9 OpenAI 兼容端点 ✅

基础 URL：**`http://localhost:11434/v1`**

| 端点 | 方法 | 状态 |
| --- | --- | --- |
| `/v1/chat/completions` | POST | ✅ 流式、JSON 模式、seed、工具、视觉、推理强度控制 |
| `/v1/completions` | POST | ✅ `prompt` **只接受字符串**；无 `best_of`/`echo`/`n` |
| `/v1/models` | GET | ✅ |
| `/v1/models/{model}` | GET | ✅ |
| `/v1/embeddings` | POST | ✅ 支持字符串与 string[] 输入、`encoding_format`、`dimensions` |
| `/v1/responses` | POST | ✅ 于 **v0.13.3** 新增；仅无状态（无 `previous_response_id`/`conversation`） |
| `/v1/messages` | POST | ✅ Anthropic Messages 兼容 |

`/v1/models` 说明：✅ `created` = 模型的最后修改时间；`owned_by` = ollama 用户名，默认为 `"library"`。

OpenAI SDK 要求提供 API key 值，但 **Ollama 在本地会忽略它**：
```python
from openai import OpenAI
client = OpenAI(base_url="http://localhost:11434/v1/", api_key="ollama")
```

`/v1/chat/completions` 支持的请求字段 ✅：
`model`、`messages`（文本 + base64 图片内容数组）、`frequency_penalty`、`presence_penalty`、`response_format`、`seed`、`stop`、`stream`、`stream_options.include_usage`、`temperature`、`top_p`、`max_tokens`、`tools`、`reasoning_effort`、`reasoning.effort`、`think`。

**不支持：**`tool_choice`、`logit_bias`、`user`、`n`，以及 **`logprobs`**（尽管原生的 `/api/chat` 支持它 — 这是一处真实存在的不对称）。

对于没有元数据的模型，推理强度的别名：`"minimal"` → `"low"`；`"xhigh"`/`"ultra"` → `"max"`。`"none"` → 关闭思考。

有两项能力 OpenAI API 无法表达，因此你**必须**使用 Modelfile：上下文大小，以及默认的 OpenAI 模型名。

```bash
ollama cp llama3.2 gpt-3.5-turbo     # alias an existing model to an OpenAI name
```

---

## 1.10 Windows 安装与以服务模式运行 ✅

### 环境要求
- Windows 10 22H2 或更高（家庭版或专业版）
- 若要使用 NVIDIA 加速，需要 NVIDIA 驱动 **551.61+**
- AMD：ROCm v7 / HIP7 技术栈，或支持 Vulkan 的 Radeon 驱动（Vulkan 是默认回退方案，也是 RDNA2 / RX 6800 级显卡的推荐路径）
- 可执行文件需 ≥ 4 GB 可用空间；模型还需额外数十至上百 GB
- 无需管理员权限 — 安装到你的用户主目录

### 标准安装
```powershell
# Default location
OllamaSetup.exe

# Custom install dir
OllamaSetup.exe /DIR="d:\some\location"
```

模型存放位置通过 `OLLAMA_MODELS` 用户环境变量重新指定（**必须在首次拉取模型之前设置**）。

**Windows 文件系统对照表**（对排查支持问题非常有用）：

| 路径 | 内容 |
| --- | --- |
| `%LOCALAPPDATA%\Ollama` | 日志：`app.log`、`server.log`、`upgrade.log` |
| `%LOCALAPPDATA%\Programs\Ollama` | 可执行文件（已加入用户 `PATH`） |
| `%USERPROFILE%\.ollama` | 模型 + 配置 |
| `%TEMP%\ollama*` | 临时可执行文件 |

```powershell
# PowerShell API smoke test
(Invoke-WebRequest -method POST -Body '{"model":"llama3.2","prompt":"Why is the sky blue?","stream":false}' -uri http://localhost:11434/api/generate).Content | ConvertFrom-json
```

> 注意：如果你重设了 `OLLAMA_MODELS`，卸载程序**不会**删除已下载的模型。

### 以 Windows 服务方式运行

GUI/托盘应用已经会自动启动，但若用于无头或嵌入式场景，请下载**独立 CLI** 而不是安装应用：

- `ollama-windows-amd64.zip` — CLI + NVIDIA GPU 库
- `ollama-windows-amd64-rocm.zip` — AMD 直接替换包（同一目录）
- `ollama-windows-amd64-mlx.zip` — MLX/CUDA 变体

然后用 **NSSM**（`https://nssm.cc/`）把 `ollama serve` 注册为服务，或使用 `sc.exe`/`WinSW`。

```powershell
# NSSM example
nssm install Ollama "C:\ollama\ollama.exe" "serve"
nssm set Ollama AppDirectory "C:\ollama"
nssm set Ollama AppEnvironmentExtra OLLAMA_HOST=0.0.0.0:11434 OLLAMA_ORIGINS=http://localhost:3000 OLLAMA_MODELS=D:\models\ollama
nssm set Ollama Start SERVICE_AUTO_START
nssm start Ollama
```

注意：
- 请使用 `Start SERVICE_AUTO_START`，而不是 `SERVICE_WIN32_OWN_PROCESS`，这样它在没有已登录会话时也能启动。
- **会话 0 的注意事项：**在 Windows 服务中做 GPU 推理很不稳定。对 CUDA 而言，用“无论用户是否登录都运行”的计划任务通常更可靠。如果服务启动了但模型跑在 CPU 上，原因就在这里 — 用 `ollama ps` 检查 `PROCESSOR`。
- 升级独立安装版之前，先删除旧目录。
- 卸载：设置 → 应用和功能（添加或删除程序）。

---

# 2. 用于中文创意写作 / 分镜脚本 / 提示词工程的本地文本模型

## 2.1 ⚠️ 在相信任何 2026 模型排行榜之前先读这一节

我实时查询了 `ollama.com/search`。以下是 **2026 年 9 月**的情况：

**可在本地拉取且支持良好的模型家族**（标签多、有本地尺寸梯度、没有 `cloud` 独占标记）：✅
`qwen3`、`gemma3`/`gemma4`、`llama3.x`、`deepseek-r1`、`deepseek-v3.1`、`granite4.x`、`minicpm-v4.5/4.6`、`glm-4.7-flash`、`muse-glimmer`（30B）、`nemotron-3.5-lightning`（30B-A3B）、`ornith`、`gpt-oss`、`qwen3-embedding`、`embeddinggemma`、`bge-m3`。

**当前 Ollama 条目被路由到云端的模型家族** ⚠️ —— 搜索列表里会显示 `cloud` 能力标签，而且通常只有 1–3 个标签：
`glm-5.2`、`glm-5.3`、`glm-5.3-flash`、`deepseek-v4-pro`、`deepseek-v4.1-flash`、`kimi-k3`、`minimax-m3`、`qwen3.8`、`qwen3.8-flash-next`。🔴 带 `cloud` 标签意味着在免费档位上 `ollama pull` 拿不到本地权重。

**后果：** 我找到的每一份 2026 年"最佳开放 LLM"排行榜的头部模型（DeepSeek V4 Pro、Kimi K2.6/K3、GLM-5.x、Qwen3.5-397B、Qwen3.8）都**无法在消费级 GPU 上运行**——参数量 100B–700B。做本地部署时不要围绕它们来设计架构。

我在 Ollama 库中**没有找到专门的中文分镜/创意写作模型**。`ollama.com/search?q=chinese creative writing` 返回*零个模型*；`?q=storyboard` 只返回一个无关的、5 次拉取量的自定义人格模型。🔴 实际可行的答案是：**使用一个强力的通用中文指令模型，并用 schema 约束它**，如 §1.8 所示。

**先验证再投入。** 这只花 30 秒，却能消除所有疑虑：
```bash
ollama pull <tag>
ollama show <tag>            # capabilities, context length, thinking values
ollama run <tag> "写一个三幕式大纲，200字"
```

## 2.2 按显存档位推荐的标签

下文的尺寸与上下文窗口数据来自 `ollama.com/library` 页面 ✅。显存数字是 🔴 估算值（权重 + 约 10% 开销 + 在所述上下文长度下的 KV 缓存）——**请用 `ollama ps` 实测**。

### 8 GB 显存（RTX 3060 12G、4060 8G、4060 Ti 16G 共享显存、3050）

| 标签 | 尺寸 | 最大上下文 | 显存 @16k（估算） | 用途 |
| --- | --- | --- | --- | --- |
| `qwen3:4b` | 2.5 GB | 256K | ~3.4 GB | 最低可用配置；提示词工程助手 |
| `qwen3:8b` | 5.2 GB | 40K | ~7.0 GB | **默认选择。**中文质量/显存比最佳 |
| `gemma3:4b` | 3.3 GB | 128K | ~4.2 GB | 视觉 + 140 种语言，结构能力强 |
| `gemma3:12b` | 8.1 GB | 128K | ~9.9 GB | 8k 上下文可装入 12 GB；在 12 GB 卡上 4k 约需 10.5 GB |
| `qwen3-embedding` / `bge-m3` | 小 | — | <2 GB | 检索；**中文用 bge-m3** |

### 12 GB 显存（RTX 3060 12G、4070）

| 标签 | 尺寸 | 最大上下文 | 显存 @16k（估算） | 用途 |
| --- | --- | --- | --- | --- |
| `qwen3:14b` | 9.3 GB | 40K | ~11.1 GB | **最佳平衡点。**文笔明显优于 8B |
| `gemma3:12b` | 8.1 GB | 128K | ~9.9 GB | 12 GB 以内视觉 + 长上下文的最佳选择 |
| `qwen3:8b` @ 32k ctx | 5.2 GB | 40K | ~8.8 GB | 为共享同一 GPU 的 ComfyUI 留出余量 |

### 16 GB 显存（4060 Ti 16G、4070 Ti Super、4080、A4000）

| 标签 | 尺寸 | 最大上下文 | 显存 @32k（估算） | 用途 |
| --- | --- | --- | --- | --- |
| `gemma3:27b` | 17 GB | 128K | ~19 GB @8k | **16 GB 预算不够。**社区 Q4 低量化 GGUF（~13–15 GB）在 8k 下可装入 |
| `qwen3:14b` @ 32k | 9.3 GB | 40K | ~12.9 GB | 较为宽裕，可留出 3 GB 给并发的 ComfyUI 任务 |
| `qwen3:30b` | 19 GB | 256K | ~21 GB @8k | **超出预算。**社区 Q3（~15 GB）在 4–8k 下可装入 |
| `gpt-oss:20b` | — | — | ~13.5 GB @Q4 | 指令遵循能力强；默认开启思考——请关掉它 |
| `muse-glimmer` / `nemotron-3.5-lightning` | 30B MoE，3–4B 激活 | — | ~16–18 GB | 常驻智能体、工具调用；虽有 30B 但**速度很快** |
| `granite4.1:8b` | — | — | ~6 GB | 同类最佳的**结构化 JSON**输出；schema 遵循度极佳 |

### 档位说明

- **MoE ≠ 小模型。**`qwen3:30b` 有 30.5B 参数，Q4 下约 19 GB。它的 3B *激活*参数让它**快**，而不是**小**。🔴
- **`qwen3:8b` 是混合思考模型。**思考默认开启。写创意文本时你几乎总是应该设 `"think": false`——推理轨迹既烧 token 又让文风变平。
- **QAT 变体** `gemma3:4b-it-qat`、`gemma3:12b-it-qat` 以约 1/3 的显存保住 BF16 级质量。✅（厂商宣称）
- **社区 GGUF 量化**是 16 GB 的逃生通道：直接拉取 `hf.co/<user>/<model>-GGUF:Q3_K_M`。已验证可用的写法 ✅：`ollama run hf.co/Qwen/Qwen3-30B-A3B-GGUF:Q4_K_M`。

## 2.3 为什么 `qwen3` 在这类工作负载上领先

以下为 `qwen3` 模型库卡片原文 ✅：

> *"卓越的人类偏好对齐能力，在**创意写作**、角色扮演、多轮对话和指令遵循方面表现出色，可提供更自然、更引人入胜、更具沉浸感的对话体验。"*
> *"支持 100 多种语言和方言，具备强劲的多语言指令遵循与翻译能力。"*

`qwen3:8b` 和 `qwen3:14b` 是唯一一对既在厂商自己的卡片里被宣称擅长创意写作、*而且*权重能装进消费级 GPU 的模型。

## 2.4 采样预设

| 任务 | temp | top_p | top_k | min_p | repeat_penalty | num_predict |
| --- | --- | --- | --- | --- | --- | --- |
| 分镜提取（schema） | **0** | 0.9 | 40 | 0.0 | 1.0 | 2048 |
| 对白 / 正文生成 | **1.0** | 0.95 | 80 | 0.05 | 1.08 | 1024–2048 |
| 大纲 / 节拍表 | 0.8 | 0.92 | 60 | 0.05 | 1.05 | 1024 |
| 提示词改写 | 0.7 | 0.9 | 40 | 0.0 | 1.0 | 512 |
| 角色一致性检查 | 0.3 | 0.9 | 40 | 0.0 | 1.0 | 1024 |
| 嵌入 / 分类 | 0.0 | — | — | — | — | 1 |

相比英文，中文需要略高一点的 `repeat_penalty`（1.05–1.15）；否则中日韩模型会在字符 n-gram 上打转。务必设置 `seed`，并把它和输出一起记录下来。

## 2.5 面向 Web 应用的运维建议

1. **每次请求都带 `keep_alive: "30m"`。**在 16 GB 卡上，镜头之间反复卸载模型要付出 10–60 秒的模型加载时间。这是杠杆效应最大的单项设置。
2. **`OLLAMA_NUM_PARALLEL=1`**，除非你的显存很宽裕。每个并行槽位都会复制一份 KV 缓存。
3. **在服务端设置 `OLLAMA_CONTEXT_LENGTH=32768`**，而不只是按请求设置——那些你控制不了的客户端会悄悄拿到 4k。
4. **轮询 `GET /api/ps`**，向用户展示实时显存、上下文长度，以及模型是否正在卸载到 CPU（`PROCESSOR` 的 GPU 占比 < 100% 就应弹出警告横幅）。
5. **为每种用例建一个 Modelfile**，让 temperature/上下文/聊天模板都放在服务端：
   ```
   FROM qwen3:14b
   PARAMETER num_ctx 32768
   PARAMETER temperature 1.0
   PARAMETER top_p 0.95
   PARAMETER repeat_penalty 1.08
   SYSTEM 你是一位资深分镜师…
   ```
6. **为与 ComfyUI 的 GPU 争用留出预算。**Ollama 和 ComfyUI 会争抢同一块显存。要么选 `qwen3:8b`（≤8 GB），要么在渲染前先卸载模型：
   ```bash
   curl -X POST http://localhost:11434/api/generate -d '{"model":"qwen3:14b","keep_alive":0}'
   ```

---

# 3. Windows 上的 ComfyUI

## 3.1 三种安装路径 ✅

| 路径 | 最适合 | 说明 |
| --- | --- | --- |
| **ComfyUI Portable**（Windows） | 想试用 ComfyUI、希望与系统零耦合的用户 | 内置 `python_embeded`，分 NVIDIA 版或纯 CPU 版，始终跟进最新提交，**完全可移植——删掉文件夹即卸载** |
| **Comfy Desktop** | 日常创意工作 | 支持 Windows/macOS/Linux。出厂即**预先启用** ComfyUI-Manager。默认跟随 *stable* 通道 |
| **手动安装（git + venv）** | CI、自动化、自定义节点开发 | 覆盖所有 GPU 厂商：NVIDIA、AMD、Intel、Apple Silicon、Ascend NPU、Cambricon MLU |

**不存在官方 ComfyUI Docker 镜像。** 🔴 Docker Hub 上有社区镜像，但不受支持。

### 路径 A — Portable（Windows）
解压 `ComfyUI_windows_portable`，运行 `run_nvidia_gpu.bat`（或 `run_cpu.bat`）。自带 **Python 3.13 + PyTorch CUDA 13.0**。通过 `update/update_comfyui.bat` 更新。

目录结构：
```
ComfyUI_windows_portable/
├── python_embeded/            # embedded interpreter
├── run_nvidia_gpu.bat
├── run_cpu.bat
├── run_amd_gpu.bat
├── update/
│   ├── update.py
│   └── update_comfyui.bat
└── ComfyUI/
    ├── main.py
    ├── custom_nodes/
    ├── models/
    ├── input/
    ├── output/
    └── user/
```

### 路径 B — 手动安装（git + venv）
```powershell
# 0. Prereqs: Git, Miniconda, and Microsoft Visual C++ Redistributable on Windows
# 1. Isolated env
conda create -n comfyenv
conda activate comfyenv

# 2. Source
git clone https://github.com/Comfy-Org/ComfyUI.git
cd ComfyUI

# 3a. NVIDIA — CUDA 13.0 stable
pip install torch torchvision torchaudio --extra-index-url https://download.pytorch.org/whl/cu130
# 3b. NVIDIA nightly (cu132)
pip install --pre torch torchvision torchaudio --index-url https://download.pytorch.org/whl/nightly/cu132
# 3c. AMD Linux — ROCm 7.2
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/rocm7.2

# 4. ComfyUI deps
pip install -r requirements.txt

# 5. Run
python main.py
```

**Windows AMD（ROCm 10.0）** ✅ —— 多架构 wheel，无需单独安装 HIP SDK：
```powershell
pip install --index-url https://stable.repo.amd.com/rocm/whl-next/ `
  "torch[device-all]==2.13.0+rocm10.0.0" "torchvision[device-all]==0.28.0+rocm10.0.0" "torchaudio==2.11.0.2+rocm10.0.0"
```
| GPU | 设备 extra |
| --- | --- |
| RX 9070 / XT、Radeon AI PRO R9700 | `device-gfx1201` |
| RX 9060 / XT | `device-gfx1200` |
| RX 7900 XT / XTX | `device-gfx1100` |
| RX 7700 XT / 7800 XT | `device-gfx1101` |
| RX 7600 / XT | `device-gfx1102` |
| Ryzen AI Max / Max+（Strix Halo） | `device-gfx1151` |
把 **两处** `device-all` 都替换掉可以缩小安装体积。支持的架构：RDNA 2/3/3.5/4。

**版本矩阵** ✅：
- Python **推荐 3.13**（支持得非常好）
- Python 3.12 —— 如果某个自定义节点的依赖在 3.13 上出问题，这是很好的退路
- Python 3.14 —— 能用，但部分自定义节点有问题；某些依赖在自由线程构建下会重新启用 GIL
- 支持 PyTorch 2.7+；NVIDIA 20 系及以上需要 cu130+
- **Chrome 143+** —— 更早版本存在已知的画面异常与性能问题

### `requirements.txt`（当前内容）✅
```
comfyui-frontend-package==1.49.6
comfyui-workflow-templates==0.11.48
comfyui-embedded-docs==0.5.10
torch
torchsde
torchvision
torchaudio
numpy>=1.25.0
einops
transformers>=4.50.3
tokenizers>=0.13.3
sentencepiece
safetensors>=0.4.2
aiohttp>=3.11.8
yarl>=1.18.0
pyyaml
Pillow
scipy
tqdm
psutil
alembic
SQLAlchemy>=2.0.0
filelock
av>=17.0.0
comfy-kitchen==0.2.31
comfy-aimdo==0.4.15
requests
simpleeval>=1.0.0
blake3
# non-essential:
kornia>=0.7.1
spandrel
pydantic~=2.0
pydantic-settings~=2.0
PyOpenGL>=3.1.8
comfy-angle
```

**每次 `git pull` 之后都要重新执行 `pip install -r requirements.txt`** —— requirements 会跟着 ComfyUI 本身一起变化。

## 3.2 启动参数 ✅

以下内容逐字取自 `comfy/cli_args.py`（经由官方参数参考页）。

### 网络与服务端

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `--listen [IP]` | `127.0.0.1` | 支持逗号分隔的列表。**不带取值时 → `0.0.0.0,::`（全部 IPv4+IPv6）。** |
| `--port` | **`8188`** | 监听端口。 |
| `--enable-cors-header [ORIGIN]` | 禁用 | 启用 CORS。可指定来源；不带取值时用 `*`，即允许所有来源。 |
| `--max-upload-size` | `100` | 单位为 MB。 |
| `--enable-compress-response-body` | 禁用 | 压缩 HTTP 响应体。 |
| `--tls-keyfile PATH` | — | 启用 HTTPS；需要配合 `--tls-certfile`。 |
| `--tls-certfile PATH` | — | " |

### 目录
| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `--base-directory PATH` | ComfyUI 根目录 | 作为 models、custom_nodes、input、output、temp、user 的基准目录。 |
| `--extra-model-paths-config PATH` | — | 加载一个或多个 `extra_model_paths.yaml`；可重复使用。 |
| `--output-directory PATH` | — | 覆盖 `--base-directory`。 |
| `--temp-directory PATH` | — | " |
| `--input-directory PATH` | — | " |
| `--user-directory PATH` | — | 绝对路径；该路径必须已存在且可读。 |

### 启动与浏览器
| 参数 | 说明 |
| --- | --- |
| `--auto-launch` | 启动时在默认浏览器中打开 ComfyUI。 |
| `--disable-auto-launch` | 不打开。**会覆盖 `--windows-standalone-build`。** |
| `--windows-standalone-build` | Portable 的便捷模式；**会把 `auto_launch` 设为 `true`**。 |

> ⚠️ 无头服务器请用：`python main.py --windows-standalone-build --disable-auto-launch`。

### 设备
`--cuda-device ID`（逗号分隔或 `all`）· `--default-device ID` · `--cuda-malloc` / `--disable-cuda-malloc` · `--directml [DEVICE]` · `--oneapi-device-selector`

> ⚠️ **在 Windows 上 ComfyUI 默认只使用 GPU 0。**传入 `--cuda-device all` 可恢复多 GPU 支持。

### 显存模式（互斥）
`--gpu-only` · `--highvram` · `--lowvram`（把文本编码器放到 CPU 上跑）· `--novram` · `--cpu`
另外还有：`--reserve-vram GB` · `--async-offload [NUM_STREAMS]`（默认 2，在 NVIDIA 上已启用）· `--disable-async-offload` · `--disable-dynamic-vram` / `--enable-dynamic-vram` · `--fast-disk`（NVMe）· `--disable-smart-memory` · `--disable-pinned-memory` · `--mmap-torch-files` / `--disable-mmap`

### 精度（每组内部互斥）
- **全局：**`--force-fp32` · `--force-fp16`（同时设置 `--fp16-unet`）
- **UNet：**`--fp32-unet` · `--fp64-unet` · `--bf16-unet` · `--fp16-unet` · `--fp8_e4m3fn-unet` · `--fp8_e5m2-unet` · `--fp8_e8m0fnu-unet`
- **VAE：**`--fp16-vae`（⚠️ 可能导致画面全黑）· `--fp32-vae` · `--bf16-vae` · `--cpu-vae`
- **文本编码器：**`--fp8_e4m3fn-text-enc` · `--fp8_e5m2-text-enc` · `--fp16-text-enc` · `--fp32-text-enc` · `--bf16-text-enc`
- **注意力：**`--use-split-cross-attention` · `--use-quad-cross-attention` · `--use-pytorch-cross-attention` · `--use-sage-attention` · `--use-flash-attention` · `--disable-xformers` · `--force-upcast-attention` / `--dont-upcast-attention`

### 缓存（互斥）
`--cache-ram [GB] [GB]`（默认；活跃缓存 = 内存的 10%，最小 2 GB，最大 10 GB）· `--cache-classic` · `--cache-lru N` · `--cache-none`

### 调试
`--disable-all-custom-nodes` · `--whitelist-custom-nodes FOLDER...` · `--disable-api-nodes`（阻断前端访问互联网）· `--disable-metadata` · `--verbose [LEVEL]` · `--log-stdout` · `--dont-print-server` · `--multi-user` · `--quick-test-for-ci`（启动后随即退出——用于 CI）

### ComfyUI-Manager
`--enable-manager` · `--enable-manager-legacy-ui` · `--disable-manager-ui`（需要 `--enable-manager`）

### 前端 / API
`--front-end-version [owner]/[repo]@latest` · `--front-end-root PATH` · `--comfy-api-base`（默认 `https://api.comfy.org`）· `--database-url`（默认 `sqlite:///<ComfyUI>/user/comfyui.db`；可用 `sqlite:///:memory:`）· `--enable-assets` · `--feature-flag KEY[=VALUE]` · `--list-feature-flags`

### 你问到的那些参数
```powershell
# LAN access on all interfaces
python main.py --listen
python main.py --listen 0.0.0.0
python main.py --listen 127.0.0.1,192.168.1.50

# Custom port
python main.py --port 8288

# CORS — allow your web app
python main.py --enable-cors-header "http://localhost:3000"
python main.py --enable-cors-header "*"          # any origin; dev only

# Windows Portable: edit run_nvidia_gpu.bat
.\python_embeded\python.exe -s ComfyUI\main.py --listen --windows-standalone-build
pause

# Headless + CORS + right-sized VRAM for a 12 GB card shared with an LLM
python main.py --disable-auto-launch --enable-cors-header "*" `
               --port 8188 --lowvram --reserve-vram 2
```

> ⚠️ 在公网接口上用 `--enable-cors-header "*"`，与 `OLLAMA_ORIGINS=*` 属于同一级别的暴露风险：用户访问的任何网站都能驱动你的 GPU 并读取你的输出。请把它限定为你应用自己的来源，或者在前面加一层带 TLS 的反向代理。

## 3.3 模型目录 ✅

```
ComfyUI/models/
├── checkpoints/         # all-in-one SD/SDXL/Flux bundles (.safetensors)
├── diffusion_models/    # standalone DiT/UNet weights (Flux, Wan, Hunyuan, Qwen-Image…)
├── unet/                # legacy alias — diffusion_models and unet BOTH map to the "diffusion_models" key
├── text_encoders/       # CLIP-L/G, T5-XXL, umT5, Qwen text encoders
├── clip/                # also maps to the "text_encoders" key (legacy)
├── clip_vision/         # CLIP vision towers for image-to-image / reference
├── vae/                 # AE / VAE
├── vae_approx/          # TAESD fast previews
├── controlnet/
├── t2i_adapter/
├── loras/               # LyCORIS also resolved from here
├── style_models/
├── embeddings/          # textual inversion
├── hypernetworks/
├── upscale_models/
├── latent_upscale_models/
├── gligen/
├── diffusers/
├── photomaker/
├── classifiers/
├── model_patches/
├── audio_encoders/
├── background_removal/
└── datasets/
```

**关于 `unet` 与 `diffusion_models`、`clip` 与 `text_encoders` 的问题——权威答案** ✅，出自 `extra_model_paths.yaml.example`：

```yaml
text_encoders: |
    models/text_encoders/
    models/clip/            # <- clip/ is an alias INTO text_encoders
diffusion_models: |
    models/unet/            # <- unet/ is an alias INTO diffusion_models
    models/diffusion_models/
```

所以加载器用的键是 `text_encoders` 和 `diffusion_models`；`models/clip/` 和 `models/unet/` 只是**搜索路径别名**，并不是独立的类别。把一个 T5-XXL 放进 `text_encoders/` 或 `clip/` 任一处，它都会出现在同一个下拉列表里。

其他目录：
```
ComfyUI/
├── input/      # Load Image uploads
├── output/     # Save Image; merge nodes write merged models to output/checkpoints/
├── temp/
├── user/
│   └── default/workflows/   # saved workflows
└── custom_nodes/
```

在界面里找到 models 文件夹：点击 logo → **Help → Open folder → Open models folder**。
桌面版应用的默认数据根目录：`C:\Users\<user>\Documents\ComfyUI\`。

### 外部模型存储 ✅
把 `ComfyUI/extra_model_paths.yaml.example` 复制为 `ComfyUI/extra_model_paths.yaml`：
```yaml
comfyui:
    base_path: D:/AI/comfyui/
    is_default: true
    checkpoints: models/checkpoints/
    text_encoders: |
                 models/text_encoders/
                 models/clip/
    diffusion_models: |
                 models/unet/
                 models/diffusion_models/
    vae: models/vae/
    loras: models/loras/
    controlnet: |
                 models/controlnet/
                 models/t2i_adapter/
my_custom_nodes:
  custom_nodes: /d/extra_custom_nodes
```
桌面版应用的配置文件：`C:\Users\<user>\AppData\Roaming\ComfyUI\extra_models_config.yaml`（macOS：`~/Library/Application Support/ComfyUI/`）。不要直接覆盖自动生成的配置——先备份。
**编辑后必须重启**。

## 3.4 ComfyUI-Manager ✅

**ComfyUI-Manager 已并入 ComfyUI 核心**，用一个参数即可启用，不再需要克隆进 `custom_nodes`。

```powershell
# Portable
.\python_embeded\python.exe -m pip install -r ComfyUI\manager_requirements.txt
.\python_embeded\python.exe -s ComfyUI\main.py --windows-standalone-build --enable-manager
pause

# Manual
pip install -r manager_requirements.txt
python main.py --enable-manager
```

| 参数 | 说明 |
| --- | --- |
| `--enable-manager` | 启用 Manager |
| `--enable-manager-legacy-ui` | 旧版 Manager UI（需要 `--enable-manager`） |
| `--disable-manager-ui` | 关闭 UI 与相关接口，保留后台任务（需要 `--enable-manager`） |

**Comfy Desktop 出厂就已启用 Manager** —— 什么都不用做。

旧式安装（只有当你想要一份由 git 管理的副本时才需要）：
```powershell
cd ComfyUI\custom_nodes
git clone https://github.com/Comfy-Org/ComfyUI-Manager comfyui-manager
cd comfyui-manager
pip install -r requirements.txt
```
该文件夹**必须**正好位于 `ComfyUI/custom_nodes/comfyui-manager` —— 不能多套一层目录，也不能以压缩包形式原地放着。

**新 UI 的限制** ✅：
- 只能从注册表（`registry.comfy.org`）安装。未注册的节点无法通过 UI 安装。
- 出于安全考虑，新 UI 中**禁用了基于 Git 的安装**。未注册的节点请手动 `git clone`。
- 脚本化操作用 `cm-cli.py`（V2.24）：`show|install|uninstall|update|disable|enable|fix`，另有 `snapshot` / `snapshot-list` 用于生成可复现的环境快照。

**手动安装依赖：**
```powershell
# Portable — from the ComfyUI_windows_portable root
python_embeded\python.exe -m pip install -r ComfyUI\custom_nodes\<node>\requirements.txt
# Portable — generic package
.\python_embeded\python.exe -m pip install <package>
```

好用的调试组合：`python main.py --disable-all-custom-nodes --whitelist-custom-nodes ComfyUI-Manager`，用来证明基础安装本身是干净的。

## 3.5 把 ComfyUI 接入 Web 应用 ✅

ComfyUI 的 HTTP API（端口 8188）：
| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `POST` | `/prompt` | 把 workflow 加入队列。请求体：`{"prompt": {api-format graph}, "client_id": "..."}` → `{"prompt_id": "..."}` |
| `GET` | `/history/{prompt_id}` | 结果/状态 |
| `GET` | `/view` | 获取输出的图片 |
| `WS` | `/ws?clientId=...` | 进度事件（`executing`、`progress`、`executed`、`execution_error`） |
| `POST` | `/upload/image` | multipart 图片上传 |
| `POST` | `/interrupt` | 取消正在运行的任务 |
| `GET` | `/object_info` | 完整的节点 schema —— **据此生成带类型的客户端** |
| `GET` | `/system_stats` | 显存/设备信息 |

推荐的流水线（与 §2.5 对应）：
1. 第一轮 LLM 推理 → 受 schema 约束的 `Storyboard` JSON（§1.8）。
2. 第二轮 LLM 推理（逐镜头、流式）→ 精修后的文本。
3. 提交一个 ComfyUI workflow，为每个镜头批量配一条 `Load Diffusion Model` + `CLIPTextEncode` 链路。
4. 把 LLM 的 token 流式送到界面；轮询 `/ws` 获取渲染进度。

运行时带上 `--enable-cors-header "http://localhost:3000"`，**或者**把 `/prompt` 和 `/ws` 通过你自己的后端做代理。代理更好：它让 ComfyUI 留在回环地址上，避免暴露一个无鉴权的 GPU 端点。

---

# 4. 备选后端与统一抽象

## 4.1 对比表 ✅

| 后端 | 默认 host:port | OpenAI 兼容基础 URL | 端点 | 认证 |
| --- | --- | --- | --- | --- |
| **Ollama** | `127.0.0.1:11434` | `http://localhost:11434/v1` | `/chat/completions`, `/completions`, `/models`, `/models/{m}`, `/embeddings`, `/responses`, `/messages` | 本地环境下被忽略；SDK 要求为非空值 |
| **llama.cpp `llama-server`** | `127.0.0.1:8080` | `http://127.0.0.1:8080/v1` | `/chat/completions`, `/completion`, `/models`, `/embeddings`, `/rerank`, `/v1/responses`, `/v1/messages` | 可选 `--api-key` |
| **LM Studio** | `127.0.0.1:1234` | `http://localhost:1234/v1` | `/chat/completions`, `/completions`, `/embeddings`, `/models`, `/responses`, `/messages` | 默认无认证；可选 API 令牌 |
| **vLLM** | `127.0.0.1:8000` | `http://localhost:8000/v1` | `/chat/completions`, `/completions`, `/models`, `/embeddings`, `/responses`, `/audio/transcriptions`, `/rerank` | `--api-key`（推荐） |

这四者在**默认端口上互不重叠**，因此一台开发机可以同时运行它们全部。⚠️ 8080 端口是很常见的冲突源——在认定 llama.cpp 正在运行之前先检查一遍。

## 4.2 llama.cpp `llama-server` ✅

```bash
llama-server -m models/7B/ggml-model.gguf -c 2048
# → listens on 127.0.0.1:8080
```

| 参数 | 默认值 | 环境变量 |
| --- | --- | --- |
| `-m, --model PATH` | — | `LLAMA_ARG_MODEL` |
| `--host HOST` | `127.0.0.1` | `LLAMA_ARG_HOST` |
| `--port PORT` | **`8080`** | `LLAMA_ARG_PORT` |
| `--hf-repo` / `--hf-file` | — | 直接从 Hub 下载 GGUF |
| `-c, --ctx-size N` | — | `LLAMA_ARG_CTX_SIZE` |
| `-ngl, --n-gpu-layers N` | — | 卸载层数；`999` = 全部 |
| `-a, --alias STRING` | — | `LLAMA_ARG_ALIAS` — REST API 中显示的模型名 |
| `-np, --parallel N` | — | `LLAMA_ARG_N_PARALLEL` |
| `--jinja` | 关闭 | 使用 GGUF 自带的 Jinja 对话模板（**工具调用必需**） |
| `--api-key KEY` | 无 | `LLAMA_ARG_API_KEY` |
| `--path PATH` | — | `LLAMA_ARG_STATIC_PATH` — 静态文件服务 |
| `--api-prefix PREFIX` | `/` | 在某个路径前缀下提供服务 |
| `--metrics` | 关闭 | `LLAMA_ARG_ENDPOINT_METRICS` — Prometheus |
| `--slots` / `--no-slots` | 开启 | `LLAMA_ARG_ENDPOINT_SLOTS` |
| `--props` | 关闭 | `LLAMA_ARG_ENDPOINT_PROPS` — `POST /props` |
| `--reuse-port` | 关闭 | 允许同一端口上使用多个套接字 |
| `--embd-normalize N` | `2`（欧氏距离） | `-1`=无 `0`=最大绝对值 int16 `1`=曼哈顿距离 `>2`=p 范数 |

**命令行参数优先于环境变量。** ✅

值得注意的服务器特性 ✅：OpenAI 兼容的 chat/completions/**responses**/embeddings、Anthropic Messages 兼容、重排序（rerank）端点、带连续批处理的并行解码、通过 OAI chat 端点实现多模态、监控端点、**受 schema 约束的 JSON 响应格式**（通过 `response_format` 中的 `json_schema`）、类似 Claude 的 assistant prefill、工具调用、推测解码。

**路由模式** ✅ — 不带 `-m` 启动即可自动发现模型目录：
```bash
llama-server --models-dir ~/models --no-models-autoload --jinja --host 127.0.0.1 --port 8080 -ngl 999 -c 32768
```
健康检查：`curl http://127.0.0.1:8080/health`；列出模型：`curl http://127.0.0.1:8080/models`。

Docker：
```bash
docker run -p 8080:8080 -v /path/to/models:/models --gpus all \
  ghcr.io/ggml-org/llama.cpp:server -m /models/model.gguf -c 512 --host 0.0.0.0 --port 8080 --n-gpu-layers 99
```
Windows 预编译版：`llama-server.exe -m models\7B\ggml-model.gguf -c 2048`。

## 4.3 LM Studio ✅

```bash
lms server start          # default http://localhost:1234
lms get ibm/granite-4-micro
```
或使用 GUI：左侧边栏 → **Server** 标签页 → **Start Server**（绿色状态会显示当前可用的基础 URL）。

- 默认基础 URL 为 **`http://localhost:1234/v1`**；请在启动*之前*在 Server 标签页中修改端口。
- **默认无需 API 密钥。** 启用 "Require Authentication" 后会生成令牌，以 `Authorization: Bearer $LM_API_TOKEN`（或 `x-api-key`）发送。
- 设置面板中的 **"Start server on application launch"** → 重启后依然生效，适合无头环境。
- 切换模型无需重启；`/v1/models` 会立即更新。不支持运行两个实例——请使用不同端口。

**两套 API 接口：**
1. **OpenAI 兼容** — `/v1/models`（GET）、`/v1/responses`、`/v1/chat/completions`、`/v1/embeddings`、`/v1/completions`
2. **LM Studio 原生 REST**（`REST API v0`）— 功能更丰富：`/api/v1/chat`（**有状态** — 无需重发历史）、`/api/v1/models/download`、`/api/v1/models/download/status/{job_id}`，另外还有按模型的统计信息（tok/s、TTFT）与元数据（已加载 vs 未加载、最大上下文、量化方式）。

```python
from openai import OpenAI
client = OpenAI(base_url="http://localhost:1234/v1")
```

Anthropic 兼容 ✅：`ANTHROPIC_BASE_URL=http://localhost:1234`、`ANTHROPIC_AUTH_TOKEN=lmstudio`，然后执行 `claude --model openai/gpt-oss-20b`。

模型 ID 必须与 `/v1/models` 返回的内容一致（例如 `ibm/granite-4-micro`、`openai/gpt-oss-20b`）— 不存在静态列表。

## 4.4 vLLM ✅

```bash
vllm serve Qwen/Qwen2.5-1.5B-Instruct
# → http://localhost:8000
```

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `--host` | `127.0.0.1` | |
| `--port` | **`8000`** | |
| `--api-key KEY` | 无 | **即使在本地也要设置** — 它是唯一一个默认无认证且只绑定回环地址的后端 |
| `--allowed-origins` | `['*']` | ⚠️ 完全开放。请设置为 `['http://localhost:3000']` |
| `--allowed-methods` | `['*']` | |
| `--allowed-headers` | — | JSON 字符串 |
| `--allow-credentials` | `False` | |
| `--served-model-name NAME` | 模型 id | 以另一个名称对外提供服务 |
| `--chat-template PATH` | 分词器自带的 | 模型没有模板时必需；缺少它**所有 chat 请求都会报错** |
| `--data-parallel-supervisor-port` | `9256` | 多端口负载均衡模式下的健康检查端口 |
| `--generation-config` | HF 仓库自带的 | 用 `--generation-config vllm` 可忽略仓库的 `generation_config.json` |
| `--lora-modules` | — | 提供 LoRA 适配器服务 |
| `--disable-log-stats` / `--enable-log-requests` | | |

配置文件：`vllm serve --config config.yaml`。**优先级：命令行 > 配置文件 > 默认值。**

Python 的额外参数放入 `extra_body`，例如 `extra_body={"top_k": 50}` ✅。

```python
from openai import OpenAI
client = OpenAI(base_url="http://localhost:8000/v1", api_key="token-abc123")
```

**vLLM 属于另一类部署形态。** 它是生产级推理服务引擎：连续批处理、PagedAttention、张量并行。它需要真正的 NVIDIA 数据中心级 GPU（A100/H100 级别），并且要有足够显存容纳权重加上目标并发下的 KV 缓存。在 16 GB 的消费级显卡上，它可以低并发地跑单个 7–8B 模型，但对单用户的本地应用而言它是错误的工具。把它作为"自托管服务器"目标来提供，而不是"能在我的笔记本上跑"的目标。

## 4.5 统一抽象设计

### 后端注册表（配置驱动）

```ts
export type BackendId = "ollama" | "llamacpp" | "lmstudio" | "vllm";

export interface BackendConfig {
  id: BackendId;
  label: string;
  baseUrl: string;          // OpenAI-compat base, trailing slash
  apiKey?: string;          // required by SDKs, ignored by Ollama/LM Studio
  healthPath: string;       // /api/version | /health | /api/v1/models | /health
  modelsPath: string;       // /api/tags | /models | /v1/models | /v1/models
  defaults: {
    temperature: number;
    top_p: number;
    max_tokens: number;
  };
  capabilities: {
    jsonSchema: boolean;    // native response_format json_schema
    seed: boolean;
    tools: boolean;
    vision: boolean;
    logprobs: boolean;      // Ollama native only
    parallel: boolean;      // server can batch
  };
  notes?: string;
}

export const BACKENDS: Record<BackendId, BackendConfig> = {
  ollama: {
    id: "ollama", label: "Ollama",
    baseUrl: "http://localhost:11434/v1/",
    apiKey: "ollama",                       // required, ignored
    healthPath: "/api/version",             // native, outside /v1
    modelsPath: "/api/tags",                // native — richer than /v1/models
    defaults: { temperature: 0.9, top_p: 0.95, max_tokens: 2048 },
    capabilities: { jsonSchema: true, seed: true, tools: true, vision: true, logprobs: true, parallel: true },
    notes: "Set num_ctx per request; default is 4k under 24 GiB VRAM. Prefer keep_alive:'30m'.",
  },
  llamacpp: {
    id: "llamacpp", label: "llama.cpp",
    baseUrl: "http://127.0.0.1:8080/v1/",
    healthPath: "/health",
    modelsPath: "/models",
    defaults: { temperature: 0.9, top_p: 0.95, max_tokens: 2048 },
    capabilities: { jsonSchema: true, seed: true, tools: true, vision: true, logprobs: true, parallel: true },
    notes: "Start with --jinja or tool calling breaks. Multi-model router: omit -m, use --models-dir.",
  },
  lmstudio: {
    id: "lmstudio", label: "LM Studio",
    baseUrl: "http://localhost:1234/v1/",
    healthPath: "/api/v1/models",
    modelsPath: "/v1/models",
    defaults: { temperature: 0.9, top_p: 0.95, max_tokens: 2048 },
    capabilities: { jsonSchema: true, seed: true, tools: true, vision: true, logprobs: false, parallel: true },
    notes: "Stateful /api/v1/chat available. Toggle 'Start server on application launch' for headless.",
  },
  vllm: {
    id: "vllm", label: "vLLM",
    baseUrl: "http://localhost:8000/v1/",
    apiKey: "token-abc123",
    healthPath: "/health",
    modelsPath: "/v1/models",
    defaults: { temperature: 0.7, top_p: 1.0, max_tokens: 2048 },
    capabilities: { jsonSchema: true, seed: true, tools: true, vision: true, logprobs: false, parallel: true },
    notes: "Requires --api-key even locally. Default --allowed-origins is ['*'] — lock it down.",
  },
};
```

### 接口

```ts
export interface LLMMessage { role: "system"|"user"|"assistant"|"tool"; content: string; images?: string[]; }
export interface GenerateOpts {
  model: string;
  messages: LLMMessage[];
  temperature?: number; top_p?: number; top_k?: number; min_p?: number;
  maxTokens?: number; stop?: string[]; seed?: number;
  repeatPenalty?: number; numCtx?: number; keepAlive?: string;
  jsonSchema?: object;          // -> response_format: { type:"json_schema", json_schema:{ schema, strict:true } }
  tools?: unknown[];
  think?: boolean | string;
  signal?: AbortSignal;
}
export interface Usage {
  promptTokens?: number; completionTokens?: number;
  tokensPerSec?: number; ttftMs?: number; prefillTokensPerSec?: number; cacheHitRatio?: number;
}

export interface LLMBackend {
  listModels(): Promise<{ id: string; family?: string; params?: string; quant?: string; sizeBytes?: number }[]>;
  generate(o: GenerateOpts): Promise<{ text: string; thinking?: string; usage: Usage; raw: unknown }>;
  stream(o: GenerateOpts): AsyncIterable<{ delta: string; done: boolean; usage?: Usage }>;
  unload?(model: string): Promise<void>;   // Ollama only: keep_alive: 0
  show?(model: string): Promise<{ capabilities: string[]; contextLength: number; thinking?: { values: (boolean|string)[]; default: boolean|string } }>;
}
```

### 按能力门控的调用点

```ts
// 1. num_ctx / OLLAMA_CONTEXT_LENGTH are Ollama-native and have NO OpenAI equivalent.
//    Bake them into a Modelfile instead, or you'll be stuck at 4k.
const ollamaOnly = backend.id === "ollama";
const res = await backend.generate({
  model, messages, numCtx: ollamaOnly ? 32768 : undefined,
  keepAlive: ollamaOnly ? "30m" : undefined,
  seed: 42, temperature: 0,
  jsonSchema: StoryboardSchema,
});

// 2. logprobs exist on native /api/chat but NOT on any backend's /v1/chat/completions.
const canLogprobs = backend.capabilities.logprobs;

// 3. thinking models: always disable for creative prose.
think: false,
```

### 统一请求路由

```ts
// All backends speak the same OpenAI body. Only baseUrl/apiKey differ.
const res = await fetch(`${cfg.baseUrl}chat/completions`, {
  method: "POST",
  headers: { "Content-Type": "application/json", ...(cfg.apiKey ? { Authorization: `Bearer ${cfg.apiKey}` } : {}) },
  body: JSON.stringify({
    model: "qwen3:8b",
    messages,
    stream: true,
    temperature: 1.0, top_p: 0.95, max_tokens: 2048, seed: 42,
    ...(schema ? { response_format: { type: "json_schema", json_schema: { name: "storyboard", schema, strict: true } } } : {}),
  }),
  signal: ac.signal,
});
```

**这是 SSE，不是 NDJSON** — 与 Ollama 原生 API 不同。如果你使用 `backend.stream()`，就需要一个能处理 `data: ` 前缀和 `data: [DONE]` 哨兵标记的解析器。

### 启动探测顺序
1. `GET {healthPath}` — 服务器是否已就绪？
2. `GET {modelsPath}` — 枚举模型。对 Ollama 还要对每个模型调用 `POST /api/show`，以获取 `capabilities`、`model_info["*.context_length"]` 和 `thinking.values` ✅。
3. `GET {baseUrl}models` — 交叉校验 OpenAI 层是否一致。
4. 将结果持久化到 localStorage，这样应用下次可以瞬间打开。
5. 轮询 `ollama ps`（或后端的等价接口）获取实时显存/上下文，并在 `PROCESSOR` 显示 CPU 卸载时给出警告。

---

# 5. 速查表

```
Ports
  11434  Ollama (default 127.0.0.1)          /v1
  8080   llama-server (default 127.0.0.1)     /v1
  1234   LM Studio (default 127.0.0.1)        /v1
  8000   vLLM (default 127.0.0.1)             /v1
  8188   ComfyUI (default 127.0.0.1)          /prompt /ws /view /history
  9256   vLLM data-parallel supervisor

Ollama
  GET  /api/version  /api/tags  /api/ps  /v1/models  /v1/models/{model}
  POST /api/chat  /api/generate  /api/embed  /api/embeddings(legacy: uses `prompt`)  /api/show
       /v1/chat/completions  /v1/completions  /v1/embeddings  /v1/responses  /v1/messages

Env (Ollama)     OLLAMA_HOST OLLAMA_ORIGINS OLLAMA_MODELS OLLAMA_CONTEXT_LENGTH
                 OLLAMA_KEEP_ALIVE OLLAMA_NUM_PARALLEL OLLAMA_MAX_LOADED_MODELS
                 OLLAMA_MAX_QUEUE OLLAMA_LOAD_TIMEOUT OLLAMA_GPU_OVERHEAD OLLAMA_SCHED_SPREAD
                 OLLAMA_FLASH_ATTENTION OLLAMA_KV_CACHE_TYPE OLLAMA_MULTIUSER_CACHE
                 OLLAMA_NO_CLOUD OLLAMA_DEBUG OLLAMA_VULKAN OLLAMA_NOPRUNE OLLAMA_REMOTES
Env (llama.cpp)  LLAMA_ARG_MODEL LLAMA_ARG_HOST LLAMA_ARG_PORT LLAMA_ARG_CTX_SIZE
                 LLAMA_ARG_N_PARALLEL LLAMA_ARG_ALIAS LLAMA_ARG_API_KEY LLAMA_ARG_METRICS
ComfyUI flags    --listen --port 8188 --enable-cors-header --enable-manager
                 --disable-auto-launch --windows-standalone-build --lowvram --reserve-vram

All Ollama durations are NANOSECONDS. Ollama streams NDJSON; OpenAI-compat streams SSE.
```

---

# 6. 待定事项 / 上线前需要验证的内容

1. **在目标机器上重新核验模型标签。** `ollama.com` 的列表每周都在变动。我在 §2.1 中确认可本地拉取的那些系列，其时效截至 2026-09-29；凡是标记为 `cloud` 的，都需要用 `ollama pull` 实测确认。
2. **实测显存，不要相信表格。** 在每一档配置上发起一次有代表性的 32k 上下文请求，然后用 `ollama ps` 查看。我的 §2.2 显存列是算术推算，不是实测结果。
3. **Windows 服务 + CUDA（Session 0）。** 先测试再定方案。如果模型静默回退到 CPU，优先使用计划任务而不是 NSSM。
4. **混合 iGPU/dGPU 上的 `GGML_VK_VISIBLE_DEVICES`** — 把它设为独立显卡的索引，否则 Ollama 可能选中不稳定的 Vulkan 核显。
5. **vLLM 的 `--allowed-origins` 默认是 `['*']`**，而 `OLLAMA_ORIGINS=*` / `--enable-cors-header "*"` 同样是任何网站都能访问的、无认证的 GPU 端点。三者都要收敛作用范围。
6. **对话模板漂移。** `think` 的字符串取值由模型自行定义 — 务必从 `/api/show` 读取，而不是硬编码 `"low"`。llama.cpp 需要 `--jinja`；vLLM 需要 `--chat-template`，否则 chat 请求会直接失败。
