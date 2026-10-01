# Local LLM Text Generation for a Web App — Technical Report

**Date:** 2026-09-29
**Scope:** Ollama local API, local text-model selection for Chinese creative writing / storyboarding / prompt engineering, ComfyUI on Windows, and alternative LLM backends behind a unified abstraction.

---

## 0. Sourcing & Confidence

This report separates claims by how they were verified, because the 2026 model landscape is heavily polluted by AI-generated SEO content that contradicts itself across sites.

| Marker | Meaning |
| --- | --- |
| ✅ **VERIFIED** | Fetched directly from a primary source this session (`docs.ollama.com` OpenAPI spec, `docs.comfy.org`, `github.com/ggml-org/llama.cpp` README, `docs.vllm.ai`, `lmstudio.ai/docs`, `ollama.com/library`). |
| ⚠️ **PARTIAL** | Primary source confirms the family exists but not the specific claim (sizes, VRAM, rankings). |
| 🔴 **UNVERIFIED** | Appears only in secondary/SEO/aggregator sources. Treat as a hypothesis, verify locally before building on it. |

> **The `raw.githubusercontent.com` fetch of `docs/api.md` you requested failed** (transport error). Ollama has moved their API reference to `docs.ollama.com`, which emits a live OpenAPI 3.1 spec per endpoint — that is strictly better and is what this report is built from. The legacy `docs/api.md` on GitHub is now a stub that says: *"Note: Ollama's API docs are moving to https://docs.ollama.com/api"*.

**Current Ollama version at time of writing: v0.33.3** (published 2026-09-02). ✅

---

# 1. Ollama Local API

## 1.1 Host, port, and environment variables

### Default bind ✅

```
127.0.0.1:11434
```

Ollama binds to loopback only by default. The OpenAPI spec declares `servers: [{ url: "http://localhost:11434" }]`.

### Environment variables ✅

| Variable | Default | Purpose |
| --- | --- | --- |
| `OLLAMA_HOST` | `127.0.0.1:11434` | Bind address **and port**. Accepts `host:port` or a full URL (`https://localhost:443`). |
| `OLLAMA_ORIGINS` | `localhost, 127.0.0.1, 0.0.0.0` + `app://`, `file://`, `tauri://` | **Comma-separated** CORS allow-list. |
| `OLLAMA_MODELS` | `~/.ollama/models` (Win: `%USERPROFILE%\.ollama\models`) | Model blob store. Set before first pull. |
| `OLLAMA_CONTEXT_LENGTH` | `auto` (VRAM-dependent — see 1.6) | Default `num_ctx`. |
| `OLLAMA_KEEP_ALIVE` | `5m` | Global default for `keep_alive`. `0` = unload immediately, `-1` = forever. |
| `OLLAMA_NUM_PARALLEL` | `1` | Concurrent request slots. **Each slot allocates its own KV cache.** |
| `OLLAMA_MAX_LOADED_MODELS` | `0` (unlimited) | Cap resident models per GPU. |
| `OLLAMA_MAX_QUEUE` | `512` | Queue depth when all slots busy. |
| `OLLAMA_LOAD_TIMEOUT` | `5m` | Model load timeout. |
| `OLLAMA_GPU_OVERHEAD` | `0` (bytes) | Per-GPU VRAM reservation. Set to `2147483648` to reserve 2 GiB. |
| `OLLAMA_SCHED_SPREAD` | `false` | Spread layers across all GPUs. |
| `OLLAMA_FLASH_ATTENTION` | `false` | Flash-attention backend. |
| `OLLAMA_KV_CACHE_TYPE` | `f16` | KV cache quant. Accepts `f16`, `q8_0`, `q4_0`. **Key lever for long-context on 8–16 GB cards.** |
| `OLLAMA_MULTIUSER_CACHE` | `false` | Better prompt-cache hit rate across users. |
| `OLLAMA_NO_CLOUD` | `false` | `1` disables cloud models + web search entirely. |
| `OLLAMA_DEBUG` | `false` | `1` = debug, `2` = trace. |
| `OLLAMA_VULKAN` | `false` | Experimental Vulkan backend (AMD fallback). |
| `OLLAMA_NOPRUNE` | `false` | Don't GC orphaned blobs on startup. |
| `OLLAMA_REMOTES` | `ollama.com` | Allow-list of remote pull hosts. |
| `OLLAMA_LLM_LIBRARY` | auto | Override runner library path. |
| `HTTPS_PROXY` | — | Outbound pull proxy. **Do not set `HTTP_PROXY`** — it breaks the local client listener. |
| `CUDA_VISIBLE_DEVICES` | — | Restrict NVIDIA GPUs. `-1` = CPU only. |
| `GGML_VK_VISIBLE_DEVICES` | — | Restrict Vulkan GPUs; set to discrete index on mixed iGPU/dGPU systems. |
| `HSA_OVERRIDE_GFX_VERSION` | — | AMD arch override (Linux). |

**CORS behaviour (the exact gotcha):** Ollama allows `127.0.0.1` and `0.0.0.0` origins by default. A web app on `http://localhost:3000` is **blocked** until you set it:

```powershell
# PowerShell — session only
$env:OLLAMA_ORIGINS = "http://localhost:3000"
ollama serve
```

```powershell
# PowerShell — persistent (user scope). Quit the tray app first, then relaunch.
[Environment]::SetEnvironmentVariable("OLLAMA_ORIGINS", "http://localhost:3000", "User")
```

Allow all browser extensions (a known footgun — see 1.7):
```powershell
$env:OLLAMA_ORIGINS = "chrome-extension://*,moz-extension://*,safari-web-extension://*"
```

Open question resolved: `OLLAMA_ORIGINS` **appends** to the defaults rather than replacing them (ollama/ollama#6389, still open). It is not a strict override.

---

## 1.2 `GET /api/tags` — list locally installed models ✅

```http
GET http://localhost:11434/api/tags
```

Response:
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

Extra fields on cloud-backed entries: `remote_model`, `remote_host`.

**Companion endpoints:**
- `GET /api/version` → `{"version":"0.12.6"}` — use for capability gating.
- `GET /api/ps` → models currently resident, with `PROCESSOR` split and `CONTEXT`. This is the only reliable way to detect CPU offload.

```bash
ollama ps
```
```
NAME             ID              SIZE      PROCESSOR    CONTEXT    UNTIL
gemma4:latest    c6eb396dbd59    9.6 GB    100% GPU     131072     2 minutes from now
```

---

## 1.3 `POST /api/chat` — full request shape ✅

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

`role` is one of `system | user | assistant | tool`.
`think` accepts `true | false | null | <string>`. **Numeric thinking levels are explicitly NOT supported.** Discover the valid string names and default per-model via `/api/show`.

### 1.3.1 `stream: false` response ✅

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

Optional fields on `message` when enabled: `thinking` (reasoning trace), `tool_calls[]`, `images[]`. Optional top-level: `logprobs[]`.

**All duration fields are NANOSECONDS.** ✅ This is the single most common integration bug. Derived metrics:

```ts
const tokPerSec = eval_count / (eval_duration      / 1e9);
const ttftMs    = load_duration / 1e6 + prompt_eval_duration / 1e6;
const prefillTps= prompt_eval_count / (prompt_eval_duration / 1e9);
const cacheHit  = prompt_eval_cached_count / prompt_eval_count;
```

`done_reason` observed values: `"stop"` (natural EOS or stop sequence), `"length"` (hit `num_predict`), and `"load"` (for a bare warm-up call with no prompt).

### 1.3.2 `stream: true` response ✅

```http
HTTP/1.1 200 OK
Content-Type: application/x-ndjson
```

> ⚠️ **This is NDJSON, not SSE.** There is **no `data: ` prefix** and **no blank-line delimiter**. Do not use an `EventSource`/SSE parser. Use `fetch` + `ReadableStream` and split on `\n`, or `response.body` line reader.

Each line is a `ChatStreamEvent`:

```jsonl
{"model":"gemma4","created_at":"2025-10-26T17:15:24.097767Z","message":{"role":"assistant","content":"That"},"done":false}
{"model":"gemma4","created_at":"2025-10-26T17:15:24.109172Z","message":{"role":"assistant","content":"'"},"done":false}
{"model":"gemma4","created_at":"2025-10-26T17:15:24.121485Z","message":{"role":"assistant","content":"s"},"done":false}
{"model":"gemma4","created_at":"2025-10-26T17:15:24.132802Z","message":{"role":"assistant","content":" a"},"done":false}
{"model":"gemma4","created_at":"2025-10-26T17:15:24.143931Z","message":{"role":"assistant","content":" fantastic"},"done":false}
{"model":"gemma4","created_at":"2025-10-26T17:15:24.155176Z","message":{"role":"assistant","content":" question"},"done":false}
{"model":"gemma4","created_at":"2025-10-26T17:15:24.166576Z","message":{"role":"assistant","content":"!"},"done":true,"done_reason":"stop"}
```

Key stream rules: ✅
- `stream` defaults to **`true`**. You must send `"stream": false` explicitly for a buffered response.
- Every chunk has `done: false` except the last.
- **The final chunk carries the stats** (`done_reason`, `total_duration`, `eval_count`, …) and usually an **empty `content`**. Accumulate `content` across chunks; read metrics only from the `done: true` frame.
- With `think: true`, `message.thinking` streams in parallel with `message.content`. They are separate fields — don't merge them.
- Tool calls stream as partial `message.tool_calls` fragments.

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

## 1.4 `POST /api/generate` — completion ✅

Single-turn, raw-prompt, no chat template. Differences from `/api/chat`:

| | `/api/generate` | `/api/chat` |
| --- | --- | --- |
| Text field | `prompt` (+ `suffix` for FIM) | `messages[]` |
| System prompt | top-level `system` string | `role: "system"` message |
| Response text | `response` | `message.content` |
| Thinking trace | top-level `thinking` | `message.thinking` |
| Bypasses template | `raw: true` | n/a |

Additional `generate`-only fields: `raw` (skip all prompt templating), `suffix` (fill-in-the-middle), `system`.

Warm-up / unload trick — no prompt needed:
```bash
# Load only
curl http://localhost:11434/api/generate -d '{"model":"gemma4"}'
# Unload immediately
curl http://localhost:11434/api/generate -d '{"model":"gemma4","keep_alive":0}'
```

Response shape mirrors `ChatResponse` with `response` in place of `message`.

**Recommendation for a web app:** use `/api/chat` everywhere. `/api/generate` is only for FIM, raw-model experimentation, and warm-up/unload.

---

## 1.5 Embeddings: `/api/embed` (current) vs `/api/embeddings` (legacy)

> You asked for `/api/embeddings`. **It is deprecated and its contract differs in two breaking ways.** Implement `/api/embed`.

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

`embeddings` is always `number[][]` — **even for a single string**. Vectors are **L2-normalized** (unit length) ✅, so plain dot product == cosine similarity.

### `POST /api/embeddings` (deprecated) ✅

Differences that will bite you:
1. Input key is **`prompt`**, not `input`.
2. **Single string only** — passing an array yields `{"embedding": []}` (silent empty, not an error) ✅ *(confirmed in ollama/ollama#7242 and #13020)*.
3. Response is `{"embedding": number[]}` — a **flat** vector, singular key.
4. Not L2-normalized identically to `/api/embed`.

```json
{"embedding": [0.5670403838157654, 0.009260174818336964, 0.23178744316101074, "..."]}
```

For OpenAI-client compatibility use **`/v1/embeddings`** instead ✅.

**Recommended embedding models** ✅: `embeddinggemma` (300M), `qwen3-embedding`, `all-minilm`, `bge-m3` (best Chinese/multilingual).

---

## 1.6 `POST /api/show` — capability discovery ✅

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

Why your app **must** call this at startup, not hardcode:
- `capabilities` tells you whether `tools`, `vision`, or `thinking` are actually usable.
- `thinking.values` / `thinking.default` are the **only** valid `think` values for that model. A model with `values: [false]` has no thinking at all.
- `model_info["*.context_length"]` is the model's true max — exceeding it silently truncates.
- `details.parameter_size` / `quantization_level` drive the VRAM estimator in the model picker.

`"verbose": true` adds very large `model_info` fields (tokenizer vocab, rope tables).

---

## 1.7 `options` reference

Verified from the OpenAPI `ModelOptions` schema, which is `additionalProperties: true` — llama.cpp sampler params not listed here are still accepted.

| Key | Type | Notes |
| --- | --- | --- |
| `seed` | int | Reproducible output when combined with fixed `temperature`/`top_k`. |
| `temperature` | float | Default 0.7. **0.0–0.3 for structured output; 0.9–1.1 for creative prose.** |
| `top_k` | int | Hard top-K cut. 40–80 typical for Chinese prose; 0 disables. |
| `top_p` | float | Nucleus. 0.9–0.95 typical. |
| `min_p` | float | Min probability floor; effective for taming repetition. |
| `stop` | string \| string[] | Stop sequences. **Include the model's EOS/stop tokens** for Chinese chat templates. |
| `num_ctx` | int | Context window. See 1.6 warning below. |
| `num_predict` | int | Max generated tokens. `-1` = unlimited. |
| `repeat_penalty` | float | Not in the schema but accepted. ~1.05–1.15 for Chinese. |
| `repeat_last_n` | int | Not in the schema but accepted. |
| `mirostat`, `mirostat_tau`, `mirostat_eta` | num | Not in the schema but accepted. |

### ⚠️ The `num_ctx` trap — the most important thing in this report ✅

**Ollama auto-selects `num_ctx` from total VRAM, not model size:**

| VRAM | Default `num_ctx` |
| --- | --- |
| < 24 GiB | **4,096** |
| 24–48 GiB | 32,768 |
| ≥ 48 GiB | 262,144 |

On an RTX 4060 Ti 16 GB or a 3060 12 GB, a `qwen3:8b` creative-writing session starts at **4k context**. A single story outline plus a few shot panels will blow past that and get silently truncated mid-sentence. Ollama's own guidance: *"Tasks which require large context … should be set to at least 64000 tokens."*

Three ways to fix it, in order of preference:

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

**VRAM budgeting** — weights + KV cache. KV cache scales linearly with `num_ctx` and with `OLLAMA_NUM_PARALLEL`:

| Model (Q4_K_M) | Weights | 4k ctx | 16k ctx | 32k ctx |
| --- | --- | --- | --- | --- |
| `qwen3:8b` | 5.2 GB | ~5.6 GB | ~7.0 GB | ~8.8 GB |
| `qwen3:14b` | 9.3 GB | ~9.7 GB | ~11.1 GB | ~12.9 GB |
| `gemma3:12b` | 8.1 GB | ~8.5 GB | ~9.9 GB | ~11.7 GB |

🔴 *(estimates — the KV column is arithmetic, verify with `ollama ps`.)*

If you are tight, set `OLLAMA_KV_CACHE_TYPE=q8_0` and `OLLAMA_NUM_PARALLEL=1` to roughly halve KV cache at negligible prose-quality cost.

---

## 1.8 Structured output via `format`

`format` is a **top-level field, not inside `options`**, and accepts either a string or an inline JSON Schema object. ✅

### Mode A — `format: "json"` (schema unknown)

```json
{
  "model": "gpt-oss",
  "messages": [{"role":"user","content":"用一句话介绍加拿大。"}],
  "stream": false,
  "format": "json"
}
```

### Mode B — `format: { <JSON Schema> }` (grammar-constrained) ✅

This is the one to use for storyboard extraction. Ollama converts the schema into a decoding grammar, so the output is **guaranteed** parseable — the sampler is constrained, not just prompted.

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

Notes: ✅
- Ollama's documented advice: *"It is ideal to also pass the JSON schema as a string in the prompt to ground the model's response."* Grammar constrains syntax, not semantics.
- Set `temperature: 0` for deterministic extraction.
- Works identically on `/api/generate`.
- **Not supported on Ollama Cloud.**
- Same behaviour via `response_format` on the OpenAI-compat API.

Library helpers:

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

**For a creative-writing app, use the two-mode pattern:** a low-temperature schema-constrained pass to extract the shot list, then a high-temperature unstructured pass per shot for the actual prose/dialogue.

---

## 1.9 OpenAI-compatible endpoints ✅

Base URL: **`http://localhost:11434/v1`**

| Endpoint | Method | Status |
| --- | --- | --- |
| `/v1/chat/completions` | POST | ✅ streaming, JSON mode, seed, tools, vision, reasoning control |
| `/v1/completions` | POST | ✅ `prompt` accepts **string only**; no `best_of`/`echo`/`n` |
| `/v1/models` | GET | ✅ |
| `/v1/models/{model}` | GET | ✅ |
| `/v1/embeddings` | POST | ✅ string & string[] input, `encoding_format`, `dimensions` |
| `/v1/responses` | POST | ✅ added in **v0.13.3**; stateless only (no `previous_response_id`/`conversation`) |
| `/v1/messages` | POST | ✅ Anthropic Messages compat |

`/v1/models` notes: ✅ `created` = model last-modified time; `owned_by` = ollama username, defaulting to `"library"`.

The OpenAI SDK requires an API key value but **Ollama ignores it locally**:
```python
from openai import OpenAI
client = OpenAI(base_url="http://localhost:11434/v1/", api_key="ollama")
```

Supported `/v1/chat/completions` request fields ✅:
`model`, `messages` (text + base64 image content arrays), `frequency_penalty`, `presence_penalty`, `response_format`, `seed`, `stop`, `stream`, `stream_options.include_usage`, `temperature`, `top_p`, `max_tokens`, `tools`, `reasoning_effort`, `reasoning.effort`, `think`.

**NOT supported:** `tool_choice`, `logit_bias`, `user`, `n`, and **`logprobs`** (even though the native `/api/chat` supports it — a genuine asymmetry).

Reasoning aliases for models without metadata: `"minimal"` → `"low"`; `"xhigh"`/`"ultra"` → `"max"`. `"none"` → thinking off.

Two features the OpenAI API can't express, so you **must** use a Modelfile: context size, and default OpenAI model names.

```bash
ollama cp llama3.2 gpt-3.5-turbo     # alias an existing model to an OpenAI name
```

---

## 1.10 Windows install & running as a service ✅

### Requirements
- Windows 10 22H2+ (Home or Pro)
- NVIDIA driver **551.61+** for NVIDIA acceleration
- AMD: ROCm v7 / HIP7 stack, or a Vulkan-capable Radeon driver (Vulkan is the default fallback and the recommended path for RDNA2 / RX 6800-class cards)
- ≥ 4 GB free for the binary; tens–hundreds of GB more for models
- No Administrator rights required — installs to your home directory

### Standard install
```powershell
# Default location
OllamaSetup.exe

# Custom install dir
OllamaSetup.exe /DIR="d:\some\location"
```

Model storage is relocated with the `OLLAMA_MODELS` user env var (**set before first pull**).

**Windows filesystem map** (very useful for support):

| Path | Contents |
| --- | --- |
| `%LOCALAPPDATA%\Ollama` | Logs: `app.log`, `server.log`, `upgrade.log` |
| `%LOCALAPPDATA%\Programs\Ollama` | Binaries (added to user `PATH`) |
| `%USERPROFILE%\.ollama` | Models + config |
| `%TEMP%\ollama*` | Temp executables |

```powershell
# PowerShell API smoke test
(Invoke-WebRequest -method POST -Body '{"model":"llama3.2","prompt":"Why is the sky blue?","stream":false}' -uri http://localhost:11434/api/generate).Content | ConvertFrom-json
```

> Note: if you relocate `OLLAMA_MODELS`, the uninstaller will **not** remove the downloaded models.

### Running as a Windows service

The GUI/tray app already auto-starts, but for headless/embedded use, download the **standalone CLI** instead of installing the app:

- `ollama-windows-amd64.zip` — CLI + NVIDIA GPU libs
- `ollama-windows-amd64-rocm.zip` — AMD drop-in (same directory)
- `ollama-windows-amd64-mlx.zip` — MLX/CUDA variant

Then register `ollama serve` as a service with **NSSM** (`https://nssm.cc/`), or `sc.exe`/`WinSW`.

```powershell
# NSSM example
nssm install Ollama "C:\ollama\ollama.exe" "serve"
nssm set Ollama AppDirectory "C:\ollama"
nssm set Ollama AppEnvironmentExtra OLLAMA_HOST=0.0.0.0:11434 OLLAMA_ORIGINS=http://localhost:3000 OLLAMA_MODELS=D:\models\ollama
nssm set Ollama Start SERVICE_AUTO_START
nssm start Ollama
```

Notes:
- Use `Start SERVICE_AUTO_START`, not `SERVICE_WIN32_OWN_PROCESS`, so it starts without a logged-in session.
- **Session 0 caveat:** GPU inference in a Windows service is fragile. A scheduled task with "Run whether user is logged on or not" is usually more reliable for CUDA. If the service starts but the model loads on CPU, this is why — check `ollama ps` for `PROCESSOR`.
- Remove old directories before upgrading a standalone install.
- Uninstall: Settings → Add or remove programs.

---

# 2. Local text models for Chinese creative writing / storyboarding / prompt engineering

## 2.1 ⚠️ Read this before trusting any 2026 model ranking

I queried `ollama.com/search` live. The picture for **September 2026**:

**Families that are locally pullable and well-supported** (many tags, local size ladders, no `cloud`-only marker): ✅
`qwen3`, `gemma3`/`gemma4`, `llama3.x`, `deepseek-r1`, `deepseek-v3.1`, `granite4.x`, `minicpm-v4.5/4.6`, `glm-4.7-flash`, `muse-glimmer` (30B), `nemotron-3.5-lightning` (30B-A3B), `ornith`, `gpt-oss`, `qwen3-embedding`, `embeddinggemma`, `bge-m3`.

**Families whose current Ollama entries are cloud-routed** ⚠️ — the search listing shows a `cloud` capability tag and typically only 1–3 tags:
`glm-5.2`, `glm-5.3`, `glm-5.3-flash`, `deepseek-v4-pro`, `deepseek-v4.1-flash`, `kimi-k3`, `minimax-m3`, `qwen3.8`, `qwen3.8-flash-next`. 🔴 A cloud tag means `ollama pull` will not give you local weights on the free tier.

**Consequence:** the top of every 2026 "best open LLM" leaderboard I found (DeepSeek V4 Pro, Kimi K2.6/K3, GLM-5.x, Qwen3.5-397B, Qwen3.8) is **not runnable on a consumer GPU** — 100B–700B params. Do not architect around them for a local deployment.

I found **no dedicated Chinese storyboard/creative-writing model** on the Ollama library. `ollama.com/search?q=chinese creative writing` returns *no models*; `?q=storyboard` returns one unrelated 5-pull custom personality. 🔴 The practical answer is: **use a strong general Chinese instruct model and constrain it with a schema**, as in §1.8.

**Verify before you commit.** This is 30 seconds and removes all doubt:
```bash
ollama pull <tag>
ollama show <tag>            # capabilities, context length, thinking values
ollama run <tag> "写一个三幕式大纲，200字"
```

## 2.2 Recommended tags by VRAM tier

Sizes and context windows below are ✅ from `ollama.com/library` pages. VRAM figures are 🔴 estimates (weights + ~10% overhead + KV cache at the stated context) — **measure with `ollama ps`**.

### 8 GB VRAM (RTX 3060 12G, 4060 8G, 4060 Ti 16G shared, 3050)

| Tag | Size | Max ctx | VRAM @16k (est.) | Use |
| --- | --- | --- | --- | --- |
| `qwen3:4b` | 2.5 GB | 256K | ~3.4 GB | Minimum viable; prompt-engineering assistant |
| `qwen3:8b` | 5.2 GB | 40K | ~7.0 GB | **Default pick.** Best Chinese quality/VRAM ratio |
| `gemma3:4b` | 3.3 GB | 128K | ~4.2 GB | Vision + 140 languages, strong structure |
| `gemma3:12b` | 8.1 GB | 128K | ~9.9 GB | Fits 12 GB at 8k; ~10.5 GB at 4k on 12 GB cards |
| `qwen3-embedding` / `bge-m3` | small | — | <2 GB | Retrieval; **bge-m3 for Chinese** |

### 12 GB VRAM (RTX 3060 12G, 4070)

| Tag | Size | Max ctx | VRAM @16k (est.) | Use |
| --- | --- | --- | --- | --- |
| `qwen3:14b` | 9.3 GB | 40K | ~11.1 GB | **Sweet spot.** Noticeably better prose than 8B |
| `gemma3:12b` | 8.1 GB | 128K | ~9.9 GB | Best vision + long context under 12 GB |
| `qwen3:8b` @ 32k ctx | 5.2 GB | 40K | ~8.8 GB | Leave headroom for ComfyUI sharing the GPU |

### 16 GB VRAM (4060 Ti 16G, 4070 Ti Super, 4080, A4000)

| Tag | Size | Max ctx | VRAM @32k (est.) | Use |
| --- | --- | --- | --- | --- |
| `gemma3:27b` | 17 GB | 128K | ~19 GB @8k | **Over budget at 16 GB.** Q4 low-quant community GGUF (~13–15 GB) fits at 8k |
| `qwen3:14b` @ 32k | 9.3 GB | 40K | ~12.9 GB | Comfortable, leaves 3 GB for a concurrent ComfyUI job |
| `qwen3:30b` | 19 GB | 256K | ~21 GB @8k | **Over budget.** Community Q3 (~15 GB) fits at 4–8k |
| `gpt-oss:20b` | — | — | ~13.5 GB @Q4 | Strong instruction-following; thinking on by default — disable it |
| `muse-glimmer` / `nemotron-3.5-lightning` | 30B MoE, 3–4B active | — | ~16–18 GB | Always-on agents, tool use; **fast** despite 30B |
| `granite4.1:8b` | — | — | ~6 GB | Best-in-class **structured JSON** output; excellent schema adherence |

### Tier notes

- **MoE ≠ small.** `qwen3:30b` is 30.5B params, ~19 GB at Q4. Its 3B *active* params make it **fast**, not **small**. 🔴
- **`qwen3:8b` is a hybrid thinking model.** Thinking is on by default. For creative prose you almost always want `"think": false` — the reasoning trace burns tokens and flattens voice.
- **QAT variants** `gemma3:4b-it-qat`, `gemma3:12b-it-qat` preserve BF16-level quality at ~3× lower memory. ✅ (vendor claim)
- **Community GGUF quantizations** are the escape hatch for 16 GB: pull `hf.co/<user>/<model>-GGUF:Q3_K_M` directly. Verified working pattern ✅: `ollama run hf.co/Qwen/Qwen3-30B-A3B-GGUF:Q4_K_M`.

## 2.3 Why `qwen3` leads for this workload

Verbatim from the `qwen3` library card ✅:

> *"Superior human preference alignment, excelling in **creative writing**, role-playing, multi-turn dialogues, and instruction following, to deliver a more natural, engaging, and immersive conversational experience."*
> *"Support of 100+ languages and dialects with strong capabilities for multilingual instruction following and translation."*

`qwen3:8b` and `qwen3:14b` are the only pair where the vendor's own card claims creative-writing leadership *and* the weights fit a consumer GPU.

## 2.4 Sampling presets

| Task | temp | top_p | top_k | min_p | repeat_penalty | num_predict |
| --- | --- | --- | --- | --- | --- | --- |
| Storyboard extraction (schema) | **0** | 0.9 | 40 | 0.0 | 1.0 | 2048 |
| Dialogue / prose generation | **1.0** | 0.95 | 80 | 0.05 | 1.08 | 1024–2048 |
| Outline / beat sheet | 0.8 | 0.92 | 60 | 0.05 | 1.05 | 1024 |
| Prompt rewriting | 0.7 | 0.9 | 40 | 0.0 | 1.0 | 512 |
| Character-consistency check | 0.3 | 0.9 | 40 | 0.0 | 1.0 | 1024 |
| Embedding / classification | 0.0 | — | — | — | — | 1 |

Chinese needs a slightly higher `repeat_penalty` (1.05–1.15) than English; CJK models loop on character n-grams otherwise. Always set `seed` and log it alongside the output.

## 2.5 Operational recommendations for a web app

1. **`keep_alive: "30m"` on every request.** Unloading between shots costs 10–60 s of model load on a 16 GB card. The single highest-leverage setting.
2. **`OLLAMA_NUM_PARALLEL=1`** unless you have VRAM to spare. Each parallel slot duplicates the KV cache.
3. **Set `OLLAMA_CONTEXT_LENGTH=32768` at the server**, not just per request — clients you don't control will silently get 4k.
4. **Poll `GET /api/ps`** to show the user real-time VRAM, context, and whether the model is offloading to CPU (`PROCESSOR` < 100% GPU = a warning banner).
5. **Create a Modelfile per use case** so temperature/context/chat-template live server-side:
   ```
   FROM qwen3:14b
   PARAMETER num_ctx 32768
   PARAMETER temperature 1.0
   PARAMETER top_p 0.95
   PARAMETER repeat_penalty 1.08
   SYSTEM 你是一位资深分镜师…
   ```
6. **Budget for GPU contention with ComfyUI.** Ollama and ComfyUI will fight for the same VRAM. Pick either `qwen3:8b` (≤8 GB) or unload before rendering:
   ```bash
   curl -X POST http://localhost:11434/api/generate -d '{"model":"qwen3:14b","keep_alive":0}'
   ```

---

# 3. ComfyUI on Windows

## 3.1 Three install paths ✅

| Path | Best for | Notes |
| --- | --- | --- |
| **ComfyUI Portable** (Windows) | Trying ComfyUI; users who want zero system coupling | Embedded `python_embeded`, NVIDIA or CPU-only, always latest commits, **fully portable — delete the folder to uninstall** |
| **Comfy Desktop** | Daily creative work | Windows/macOS/Linux. Ships with ComfyUI-Manager **pre-enabled**. Tracks the *stable* channel by default |
| **Manual (git + venv)** | CI, automation, custom node dev | All GPU vendors: NVIDIA, AMD, Intel, Apple Silicon, Ascend NPU, Cambricon MLU |

There is **no official ComfyUI Docker image.** 🔴 Community images exist on Docker Hub but are unsupported.

### Path A — Portable (Windows)
Extract `ComfyUI_windows_portable` and run `run_nvidia_gpu.bat` (or `run_cpu.bat`). Ships with **Python 3.13 + PyTorch CUDA 13.0**. Update via `update/update_comfyui.bat`.

Directory map:
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

### Path B — Manual (git + venv)
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

**Windows AMD (ROCm 10.0)** ✅ — multi-arch wheels, no separate HIP SDK:
```powershell
pip install --index-url https://stable.repo.amd.com/rocm/whl-next/ `
  "torch[device-all]==2.13.0+rocm10.0.0" "torchvision[device-all]==0.28.0+rocm10.0.0" "torchaudio==2.11.0.2+rocm10.0.0"
```
| GPU | Device extra |
| --- | --- |
| RX 9070 / XT, Radeon AI PRO R9700 | `device-gfx1201` |
| RX 9060 / XT | `device-gfx1200` |
| RX 7900 XT / XTX | `device-gfx1100` |
| RX 7700 XT / 7800 XT | `device-gfx1101` |
| RX 7600 / XT | `device-gfx1102` |
| Ryzen AI Max / Max+ (Strix Halo) | `device-gfx1151` |
Swap **both** occurrences of `device-all` to shrink the install. Supported archs: RDNA 2/3/3.5/4.

**Version matrix** ✅:
- Python **3.13 recommended** (very well supported)
- Python 3.12 — good fallback if a custom node's deps break on 3.13
- Python 3.14 — works, but some custom nodes have issues; free-threaded builds re-enable the GIL in some deps
- PyTorch 2.7+ supported; cu130+ required on NVIDIA 20-series+
- **Chrome 143+** — earlier versions have known visual-glitch and perf issues

### `requirements.txt` (current) ✅
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

**Always re-run `pip install -r requirements.txt` after `git pull`** — requirements change with ComfyUI itself.

## 3.2 Startup flags ✅

Verbatim from `comfy/cli_args.py` via the official flags reference.

### Network & server

| Flag | Default | Description |
| --- | --- | --- |
| `--listen [IP]` | `127.0.0.1` | Comma-separated list supported. **With no value → `0.0.0.0,::` (all IPv4+IPv6).** |
| `--port` | **`8188`** | Port to listen on. |
| `--enable-cors-header [ORIGIN]` | disabled | Enable CORS. Optional origin, or `*` for all origins when no value given. |
| `--max-upload-size` | `100` | MB. |
| `--enable-compress-response-body` | disabled | Compress HTTP response bodies. |
| `--tls-keyfile PATH` | — | Enables HTTPS; requires `--tls-certfile`. |
| `--tls-certfile PATH` | — | " |

### Directories
| Flag | Default | Description |
| --- | --- | --- |
| `--base-directory PATH` | ComfyUI root | Base for models, custom_nodes, input, output, temp, user. |
| `--extra-model-paths-config PATH` | — | Load one or more `extra_model_paths.yaml`; repeatable. |
| `--output-directory PATH` | — | Overrides `--base-directory`. |
| `--temp-directory PATH` | — | " |
| `--input-directory PATH` | — | " |
| `--user-directory PATH` | — | Absolute; path must exist and be readable. |

### Launch & browser
| Flag | Description |
| --- | --- |
| `--auto-launch` | Open ComfyUI in the default browser on startup. |
| `--disable-auto-launch` | Don't. **Overrides `--windows-standalone-build`.** |
| `--windows-standalone-build` | Portable convenience mode; **sets `auto_launch = true`**. |

> ⚠️ For a headless server: `python main.py --windows-standalone-build --disable-auto-launch`.

### Devices
`--cuda-device ID` (comma-separated or `all`) · `--default-device ID` · `--cuda-malloc` / `--disable-cuda-malloc` · `--directml [DEVICE]` · `--oneapi-device-selector`

> ⚠️ **On Windows ComfyUI uses GPU 0 only by default.** Pass `--cuda-device all` to restore multi-GPU.

### VRAM modes (mutually exclusive)
`--gpu-only` · `--highvram` · `--lowvram` (runs text encoders on CPU) · `--novram` · `--cpu`
Plus: `--reserve-vram GB` · `--async-offload [NUM_STREAMS]` (default 2, enabled on NVIDIA) · `--disable-async-offload` · `--disable-dynamic-vram` / `--enable-dynamic-vram` · `--fast-disk` (NVMe) · `--disable-smart-memory` · `--disable-pinned-memory` · `--mmap-torch-files` / `--disable-mmap`

### Precision (mutually exclusive within each group)
- **Global:** `--force-fp32` · `--force-fp16` (also sets `--fp16-unet`)
- **UNet:** `--fp32-unet` · `--fp64-unet` · `--bf16-unet` · `--fp16-unet` · `--fp8_e4m3fn-unet` · `--fp8_e5m2-unet` · `--fp8_e8m0fnu-unet`
- **VAE:** `--fp16-vae` (⚠️ may cause black images) · `--fp32-vae` · `--bf16-vae` · `--cpu-vae`
- **Text enc:** `--fp8_e4m3fn-text-enc` · `--fp8_e5m2-text-enc` · `--fp16-text-enc` · `--fp32-text-enc` · `--bf16-text-enc`
- **Attention:** `--use-split-cross-attention` · `--use-quad-cross-attention` · `--use-pytorch-cross-attention` · `--use-sage-attention` · `--use-flash-attention` · `--disable-xformers` · `--force-upcast-attention` / `--dont-upcast-attention`

### Cache (mutually exclusive)
`--cache-ram [GB] [GB]` (default; active = 10% of RAM, min 2 GB, max 10 GB) · `--cache-classic` · `--cache-lru N` · `--cache-none`

### Debugging
`--disable-all-custom-nodes` · `--whitelist-custom-nodes FOLDER...` · `--disable-api-nodes` (blocks frontend internet) · `--disable-metadata` · `--verbose [LEVEL]` · `--log-stdout` · `--dont-print-server` · `--multi-user` · `--quick-test-for-ci` (startup then exit — use in CI)

### ComfyUI-Manager
`--enable-manager` · `--enable-manager-legacy-ui` · `--disable-manager-ui` (needs `--enable-manager`)

### Frontend / API
`--front-end-version [owner]/[repo]@latest` · `--front-end-root PATH` · `--comfy-api-base` (default `https://api.comfy.org`) · `--database-url` (default `sqlite:///<ComfyUI>/user/comfyui.db`; use `sqlite:///:memory:`) · `--enable-assets` · `--feature-flag KEY[=VALUE]` · `--list-feature-flags`

### The flags you asked about
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

> ⚠️ `--enable-cors-header "*"` on a public interface is the same class of exposure as `OLLAMA_ORIGINS=*`: any website the user visits can drive your GPU and read your outputs. Scope it to your app's origin, or put a reverse proxy with TLS in front.

## 3.3 Model directories ✅

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

**The `unet` vs `diffusion_models` and `clip` vs `text_encoders` question — the authoritative answer** ✅, from `extra_model_paths.yaml.example`:

```yaml
text_encoders: |
    models/text_encoders/
    models/clip/            # <- clip/ is an alias INTO text_encoders
diffusion_models: |
    models/unet/            # <- unet/ is an alias INTO diffusion_models
    models/diffusion_models/
```

So the loader keys are `text_encoders` and `diffusion_models`; `models/clip/` and `models/unet/` are **search-path aliases**, not separate categories. Drop a T5-XXL in either `text_encoders/` or `clip/` and it appears in the same dropdown.

Other directories:
```
ComfyUI/
├── input/      # Load Image uploads
├── output/     # Save Image; merge nodes write merged models to output/checkpoints/
├── temp/
├── user/
│   └── default/workflows/   # saved workflows
└── custom_nodes/
```

Find the models folder from the UI: click the logo → **Help → Open folder → Open models folder**.
Desktop app default data root: `C:\Users\<user>\Documents\ComfyUI\`.

### External model storage ✅
Copy `ComfyUI/extra_model_paths.yaml.example` → `ComfyUI/extra_model_paths.yaml`:
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
Desktop app config: `C:\Users\<user>\AppData\Roaming\ComfyUI\extra_models_config.yaml` (macOS: `~/Library/Application Support/ComfyUI/`). Don't overwrite the generated config — back it up.
**Restart required** after editing.

## 3.4 ComfyUI-Manager ✅

**ComfyUI-Manager has moved into ComfyUI core** and is enabled with a flag rather than cloned into `custom_nodes`.

```powershell
# Portable
.\python_embeded\python.exe -m pip install -r ComfyUI\manager_requirements.txt
.\python_embeded\python.exe -s ComfyUI\main.py --windows-standalone-build --enable-manager
pause

# Manual
pip install -r manager_requirements.txt
python main.py --enable-manager
```

| Flag | Description |
| --- | --- |
| `--enable-manager` | Enable Manager |
| `--enable-manager-legacy-ui` | Legacy Manager UI (requires `--enable-manager`) |
| `--disable-manager-ui` | Disable UI + endpoints, keep background tasks (requires `--enable-manager`) |

**Comfy Desktop ships with Manager pre-enabled** — nothing to do.

Legacy install (only if you want a git-managed copy):
```powershell
cd ComfyUI\custom_nodes
git clone https://github.com/Comfy-Org/ComfyUI-Manager comfyui-manager
cd comfyui-manager
pip install -r requirements.txt
```
The folder **must** land at `ComfyUI/custom_nodes/comfyui-manager` — not one level deeper, not zipped in place.

**New-UI limitations** ✅:
- Only installs from the registry (`registry.comfy.org`). Unregistered nodes can't be installed through the UI.
- **Git-based install is disabled** in the new UI for security. Use manual `git clone` for unregistered nodes.
- Use `cm-cli.py` (V2.24) for scripting: `show|install|uninstall|update|disable|enable|fix`, plus `snapshot` / `snapshot-list` for reproducible environment snapshots.

**Dependency install by hand:**
```powershell
# Portable — from the ComfyUI_windows_portable root
python_embeded\python.exe -m pip install -r ComfyUI\custom_nodes\<node>\requirements.txt
# Portable — generic package
.\python_embeded\python.exe -m pip install <package>
```

Useful debugging combo: `python main.py --disable-all-custom-nodes --whitelist-custom-nodes ComfyUI-Manager` to prove the base install is clean.

## 3.5 Wiring ComfyUI into a web app ✅

ComfyUI's HTTP API (port 8188):
| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/prompt` | Queue a workflow. Body: `{"prompt": {api-format graph}, "client_id": "..."}` → `{"prompt_id": "..."}` |
| `GET` | `/history/{prompt_id}` | Result/status |
| `GET` | `/view` | Fetch output images |
| `WS` | `/ws?clientId=...` | Progress events (`executing`, `progress`, `executed`, `execution_error`) |
| `POST` | `/upload/image` | Multipart image upload |
| `POST` | `/interrupt` | Cancel the running job |
| `GET` | `/object_info` | Full node schema — **generate a typed client from this** |
| `GET` | `/system_stats` | VRAM/device info |

Recommended pipeline (matches §2.5):
1. LLM pass 1 → schema-constrained `Storyboard` JSON (§1.8).
2. LLM pass 2 (per shot, streaming) → refined prose.
3. Submit one ComfyUI workflow with a `Load Diffusion Model` + `CLIPTextEncode` chain per shot, batched.
4. Stream LLM tokens to the UI; poll `/ws` for render progress.

Run with `--enable-cors-header "http://localhost:3000"` **or** proxy `/prompt` and `/ws` through your own backend. Proxying is better: it keeps ComfyUI on loopback and avoids exposing an unauthenticated GPU endpoint.

---

# 4. Alternative backends & unified abstraction

## 4.1 Comparison table ✅

| Backend | Default host:port | OpenAI-compat base URL | Endpoints | Auth |
| --- | --- | --- | --- | --- |
| **Ollama** | `127.0.0.1:11434` | `http://localhost:11434/v1` | `/chat/completions`, `/completions`, `/models`, `/models/{m}`, `/embeddings`, `/responses`, `/messages` | Ignored locally; required non-empty by SDKs |
| **llama.cpp `llama-server`** | `127.0.0.1:8080` | `http://127.0.0.1:8080/v1` | `/chat/completions`, `/completion`, `/models`, `/embeddings`, `/rerank`, `/v1/responses`, `/v1/messages` | Optional `--api-key` |
| **LM Studio** | `127.0.0.1:1234` | `http://localhost:1234/v1` | `/chat/completions`, `/completions`, `/embeddings`, `/models`, `/responses`, `/messages` | None by default; optional API token |
| **vLLM** | `127.0.0.1:8000` | `http://localhost:8000/v1` | `/chat/completions`, `/completions`, `/models`, `/embeddings`, `/responses`, `/audio/transcriptions`, `/rerank` | `--api-key` (recommended) |

All four are **non-overlapping on default ports**, so a dev machine can run all of them simultaneously. ⚠️ Port 8080 is a common collision — check before assuming llama.cpp is running.

## 4.2 llama.cpp `llama-server` ✅

```bash
llama-server -m models/7B/ggml-model.gguf -c 2048
# → listens on 127.0.0.1:8080
```

| Flag | Default | Env var |
| --- | --- | --- |
| `-m, --model PATH` | — | `LLAMA_ARG_MODEL` |
| `--host HOST` | `127.0.0.1` | `LLAMA_ARG_HOST` |
| `--port PORT` | **`8080`** | `LLAMA_ARG_PORT` |
| `--hf-repo` / `--hf-file` | — | downloads GGUF directly from the Hub |
| `-c, --ctx-size N` | — | `LLAMA_ARG_CTX_SIZE` |
| `-ngl, --n-gpu-layers N` | — | offload layers; `999` = all |
| `-a, --alias STRING` | — | `LLAMA_ARG_ALIAS` — model name in the REST API |
| `-np, --parallel N` | — | `LLAMA_ARG_N_PARALLEL` |
| `--jinja` | off | use the GGUF's Jinja chat template (**needed for tool calling**) |
| `--api-key KEY` | none | `LLAMA_ARG_API_KEY` |
| `--path PATH` | — | `LLAMA_ARG_STATIC_PATH` — static file serving |
| `--api-prefix PREFIX` | `/` | serve under a path prefix |
| `--metrics` | off | `LLAMA_ARG_ENDPOINT_METRICS` — Prometheus |
| `--slots` / `--no-slots` | on | `LLAMA_ARG_ENDPOINT_SLOTS` |
| `--props` | off | `LLAMA_ARG_ENDPOINT_PROPS` — `POST /props` |
| `--reuse-port` | off | allow multiple sockets on one port |
| `--embd-normalize N` | `2` (euclidean) | `-1`=none `0`=max abs int16 `1`=taxicab `>2`=p-norm |

**Command-line args take precedence over env vars.** ✅

Notable server features ✅: OpenAI-compatible chat/completions/**responses**/embeddings, Anthropic Messages compat, reranking endpoint, parallel decoding with continuous batching, multimodal via the OAI chat endpoint, monitoring endpoints, **schema-constrained JSON response format** (via `json_schema` in `response_format`), assistant prefill like Claude, tool use, speculative decoding.

**Router mode** ✅ — start without `-m` to auto-discover a models directory:
```bash
llama-server --models-dir ~/models --no-models-autoload --jinja --host 127.0.0.1 --port 8080 -ngl 999 -c 32768
```
Health check: `curl http://127.0.0.1:8080/health`; list: `curl http://127.0.0.1:8080/models`.

Docker:
```bash
docker run -p 8080:8080 -v /path/to/models:/models --gpus all \
  ghcr.io/ggml-org/llama.cpp:server -m /models/model.gguf -c 512 --host 0.0.0.0 --port 8080 --n-gpu-layers 99
```
Windows prebuilt: `llama-server.exe -m models\7B\ggml-model.gguf -c 2048`.

## 4.3 LM Studio ✅

```bash
lms server start          # default http://localhost:1234
lms get ibm/granite-4-micro
```
Or GUI: left rail → **Server** tab → **Start Server** (green status shows the live base URL).

- Default base URL **`http://localhost:1234/v1`**; change the port in the Server tab *before* starting.
- **No API key required by default.** Enable "Require Authentication" for a token, sent as `Authorization: Bearer $LM_API_TOKEN` (or `x-api-key`).
- **"Start server on application launch"** in the settings panel → survives restarts, good for headless.
- Switching models requires no restart; `/v1/models` updates immediately. Running two instances is unsupported — use distinct ports.

**Two API surfaces:**
1. **OpenAI-compatible** — `/v1/models` (GET), `/v1/responses`, `/v1/chat/completions`, `/v1/embeddings`, `/v1/completions`
2. **Native LM Studio REST** (`REST API v0`) — richer: `/api/v1/chat` (**stateful** — no need to resend history), `/api/v1/models/download`, `/api/v1/models/download/status/{job_id}`, plus per-model stats (tok/s, TTFT) and metadata (loaded vs unloaded, max context, quantization).

```python
from openai import OpenAI
client = OpenAI(base_url="http://localhost:1234/v1")
```

Anthropic compat ✅: `ANTHROPIC_BASE_URL=http://localhost:1234`, `ANTHROPIC_AUTH_TOKEN=lmstudio`, then `claude --model openai/gpt-oss-20b`.

Model IDs must match what `/v1/models` returns (e.g. `ibm/granite-4-micro`, `openai/gpt-oss-20b`) — there is no static list.

## 4.4 vLLM ✅

```bash
vllm serve Qwen/Qwen2.5-1.5B-Instruct
# → http://localhost:8000
```

| Flag | Default | Notes |
| --- | --- | --- |
| `--host` | `127.0.0.1` | |
| `--port` | **`8000`** | |
| `--api-key KEY` | none | **Set this even locally** — it's the one backend that defaults to no auth and binds loopback |
| `--allowed-origins` | `['*']` | ⚠️ wide open. Set `['http://localhost:3000']` |
| `--allowed-methods` | `['*']` | |
| `--allowed-headers` | — | JSON string |
| `--allow-credentials` | `False` | |
| `--served-model-name NAME` | model id | serve under a different name |
| `--chat-template PATH` | tokenizer's | required if the model has none; without it **all chat requests error** |
| `--data-parallel-supervisor-port` | `9256` | health port in multi-port LB mode |
| `--generation-config` | HF repo's | `--generation-config vllm` to ignore the repo's `generation_config.json` |
| `--lora-modules` | — | serve LoRA adapters |
| `--disable-log-stats` / `--enable-log-requests` | | |

Config file: `vllm serve --config config.yaml`. **Precedence: CLI > config file > defaults.**

Python extras go in `extra_body`, e.g. `extra_body={"top_k": 50}` ✅.

```python
from openai import OpenAI
client = OpenAI(base_url="http://localhost:8000/v1", api_key="token-abc123")
```

**vLLM is a different class of deployment.** It's a production serving engine: continuous batching, PagedAttention, tensor parallelism. It needs a real NVIDIA datacenter GPU (A100/H100-class) with enough VRAM for the weights plus KV cache at target concurrency. On a 16 GB consumer card it works for a single 7–8B model with low concurrency, but it's the wrong tool for a single-user local app. Offer it as a "self-hosted server" target, not a "runs on my laptop" target.

## 4.5 Unified abstraction design

### Backend registry (config-driven)

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

### Interface

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

### Capability-gated call sites

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

### Unified request routing

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

**This is SSE, not NDJSON** — unlike Ollama's native API. If you use `backend.stream()`, you need a parser that handles `data: ` prefixes and a `data: [DONE]` sentinel.

### Startup probe order
1. `GET {healthPath}` — is the server up?
2. `GET {modelsPath}` — enumerate. For Ollama also call `POST /api/show` per model to get `capabilities`, `model_info["*.context_length"]`, and `thinking.values` ✅.
3. `GET {baseUrl}models` — cross-check that the OpenAI layer agrees.
4. Persist the result in localStorage so the app opens instantly next time.
5. Poll `ollama ps` (or the backend equivalent) for live VRAM/context and surface a warning if `PROCESSOR` shows CPU offload.

---

# 5. Quick reference

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

# 6. Open items / what to verify before shipping

1. **Re-verify model tags on the target machine.** `ollama.com` listings shift weekly. The families I confirmed as locally pullable in §2.1 are current as of 2026-09-29; anything marked `cloud` needs `ollama pull` testing to confirm.
2. **Measure real VRAM, don't trust the table.** `ollama ps` after a representative 32k-context request on each tier. My §2.2 VRAM column is arithmetic, not measurement.
3. **Windows service + CUDA (Session 0).** Test before committing. Prefer a scheduled task over NSSM if the model silently falls back to CPU.
4. **`GGML_VK_VISIBLE_DEVICES` on mixed iGPU/dGPU** — set it to the discrete index or Ollama may pick an unstable Vulkan iGPU.
5. **`--allowed-origins` on vLLM defaults to `['*']`**, and `OLLAMA_ORIGINS=*` / `--enable-cors-header "*"` are all unauthenticated GPU endpoints reachable by any website. Scope all three.
6. **Chat-template drift.** `think` string values are model-defined — always read them from `/api/show` rather than hardcoding `"low"`. llama.cpp needs `--jinja`; vLLM needs `--chat-template` or chat requests fail outright.
