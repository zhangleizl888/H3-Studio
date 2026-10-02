# H3 Studio

**A local-first AI short-drama production desk**: from a novel or a synopsis to a finished cut —
script → characters & scenes → per-shot generation in the director stage → edit & export — all on your
own machine. The generation side attaches to ComfyUI (local or cloud RunningHub), the language side
attaches to any OpenAI-compatible local or hosted model, and the whole capability set is exposed to
agents over three surfaces: REST + MCP + CLI.

- Frontend: React 19 + Vite 6 + TypeScript 5.8 + Tailwind 4 (pnpm workspace)
- Backend: FastAPI + SQLAlchemy 2 (async) + PostgreSQL + Alembic, Python 3.12+
- License: Apache-2.0 (see `LICENSE`)

> **This repository contains no ComfyUI, no model weights and no reference projects.** You must bring
> your own ComfyUI install (see *Dependencies → Generation side*); there is no `comfyui/` layer in the
> software tree — it is an external runtime, not part of this project.

Chinese documentation: [`README.zh.md`](README.zh.md) · workflow bundle: [`work/README.md`](work/README.md)

---

## 1. Feature modules

### 1.1 Pages (`apps/web`)

Every route is behind an auth guard; below 1024px no layout is provided (desktop production tool).
The sidebar advances through four phases.

| Scope | Module | Route | What you do there |
|---|---|---|---|
| Global | Login / first admin | `/login` | Sign in; create the first admin on an empty database; change your own password from the left rail once signed in |
| Global | Dashboard · project library | `/` | Create/enter projects, cross-project asset library, model config, pipeline status card (queue / instances / disk) |
| Global | Workflow library | `/workflows` | Import wizard, parameter slots, equivalent-rewrite log, per-task auto-selection, test run, RunningHub projection, export |
| Global | Skill library | `/skills` | Prompt skills: create, bulk import (`.json`/`.md`/`.txt`), edit body, clear |
| Global | Generation history | `/history` | Script / image / video buckets per project, set-current, restore |
| Global | Generation trash | `/trash` | Preview soft-deleted versions, restore, two-click permanent delete (auto-purged after 100 days by default) |
| Global | Settings · Servers & workflows | `/settings/gen` | Instance CRUD / probes / pings; workflow × server weight bindings and per-server health check |
| Global | Settings · AI models | `/settings/llm` | Text backends (Ollama / llama.cpp / LM Studio / vLLM / any OpenAI-compatible) and purpose→backend mapping |
| Global | Settings · Users & quotas | `/settings/users` | admin / editor / viewer roles, concurrency and daily budget, weak-password blocking |
| Global | Settings · System | `/settings/system` | Storage report and GC, directory settings (media / tmp / ComfyUI output / ffmpeg), audit trail, backup commands |
| Phase 01 | Script writing | `/p/:id/script` | Script / shot list / version history tabs, config panel, floating rewrite assistant (line-level diff awaiting confirmation) |
| Phase 02 | Characters & scenes | `/p/:id/assets` | Costume shots, wardrobe variants, scene concepts, voice cloning (reference audio + transcript), import from asset library |
| Phase 03 | AI workbench (director stage) | `/p/:id/director` | Shot grid, keyframes, per-shot generation, timeline, long-take chaining, dispatch parameter table |
| Project | Task queue | `/p/:id/queue` | Cancel, retry, priority jump, filter by state/kind/instance |
| Project | Prompt management | `/p/:id/prompts` | Central editing of character / scene / keyframe / video prompts |
| Phase 04 | Production export | `/p/:id/export` | Merge mp4, asset packing, EDL/XML/JianYing timelines, per-shot preview, render logs |

Shared components: model config modal (global / chat / image / video tabs), version history panel,
generation preset picker (workflow × instance × weights), skill picker, workflow import wizard,
draggable split panes.

### 1.2 Backend capabilities (`apps/api`)

| Module | Code | Responsibility |
|---|---|---|
| Instances & protocols | `gen/registry.py`, `gen/comfy_native.py`, `api/routes_instances.py` | Local ComfyUI (HTTP + WS, `comfy_native`) and cloud RunningHub (`rh_task`); probe/ping, capability probe, circuit breaker |
| Job queue | `queue.py`, `job_plan.py`, `api/routes_jobs.py` | plan → batch dispatch → progress stream (WS/SSE) → artifact collection → cancel/retry/jump; free-VRAM gate |
| Single-GPU arbiter | `gpu_arbiter.py` | Evicts the text-model process before a local run and restores it afterwards; surfaced in Settings → System |
| Workflow engine | `gen/workflow.py`, `gen/local_adapt.py`, `gen/subgraph.py` | UI↔API conversion, author-private nodes rewritten to core equivalents, half-wired branches pruned, slot extraction |
| Per-task auto-selection | `workflow_select.py`, `api/routes_workflows.py` | Chooses a workflow by output kind + graph input signals + instance capability, ordered by `priority` |
| Weight slots | `gen/model_slots.py`, `workflow_bindings.py` | Which loader node eats which file and what else that instance offers; three-layer workflow × instance default bindings |
| Health checks | `weights_health.py`, `media_health.py` | Missing nodes / weights / half-finished downloads per server; media files reconciled against the DB |
| Built-in templates | `gen/builtin_graphs.py`, `gen/templates.py` | Three graphs generated at runtime (Qwen-Image image, H3 video, H3 long-take chain); weight names resolved fuzzily from `/object_info` |
| Version history | `api/routes_versions.py`, `purge.py` | Script/image/video V1..Vn plus generation trash; expiry purge |
| Production export | `api/routes_export.py`, `export_paths.py` | ffmpeg merge, asset packing, EDL/XML/JianYing project |
| Text models | `llm.py`, `api/routes_llm.py` | OpenAI-compatible backend probing and calls; purposes `script_write` / `script_parse` / `storyboard` / `visualize` / `h3_prompt` |
| Script parsing | `parse.py`, `api/routes_parse.py` | Novel/script upload → characters, scenes, shot skeleton |
| Director state | `director.py` | Server-side shot ↔ keyframe ↔ continuation-chain state and dispatch orchestration |
| Style presets | `styles.py` | Built-in style library |
| Auth & security | `security.py`, `crypto.py`, `api/routes_auth.py`, `routes_tokens.py` | Argon2 passwords, JWT session + refresh, agent tokens with three scopes (read/dispatch/admin), audit |
| Agent access | `mcp/server.py`, `mcp/tools.py`, `cli/` | MCP server (stdio / streamable-http) + `h3` CLI + LAN/public exposure |
| Storage & paths | `runtime.py`, `paths.py`, `api/routes_system.py` | Writable data root (development = the repo's `data/`; `H3_HOME` overrides it and is read from the **process environment only**, a `.env` line has no effect); media/tmp/ffmpeg directories editable from the UI — those land in `app_settings['paths']` in the database, **not** back into `.env`, and media/tmp only change at next start while ffmpeg applies immediately; storage report, GC, backup |
| Static client hosting | `main.py` → `mount_web_client` | When `apps/web/dist` exists the backend serves the finished UI itself, same origin as `/api` — no CORS setup, no vite proxy. Unknown paths fall back to `index.html`; `/api`, `/docs`, `/redoc`, `/openapi.json` stay reserved and return JSON 404s instead of HTML |
| Single-process start | `desktop.py` (`h3 desktop`) | Brings up the embedded database, migrations and the HTTP service in one process, and writes the port it actually got to `desktop.json` (`8788` may already be taken). Needs the `[desktop]` extra; the installer layer (shell + frozen spec) is not distributed with this repository |

### 1.3 Three external surfaces

The same backend capability, three ways in: **REST** (web and scripts), **MCP** (agent clients),
**CLI** (`h3`, used by humans and agents alike). What you expose is always the MCP port
(`8790` by default) — never `8788`, which is the REST body including `/docs` and user endpoints.

---

## 2. Source tree

```
H3/
├── apps/
│   ├── web/                 # React SPA (Vite + TS + Tailwind 4)
│   └── api/                 # FastAPI backend + h3 CLI + MCP server
│       ├── app/
│       │   ├── api/         # 11 route modules (the REST contract)
│       │   ├── gen/         # ComfyUI protocol, workflow engine, local adaptation, built-in templates
│       │   ├── cli/         # h3 commands, setup/doctor, writing client MCP config
│       │   ├── mcp/         # MCP server and its 73 tools
│       │   └── *.py         # queue / gpu_arbiter / workflow_select / purge …
│       ├── alembic/         # 9 migrations, starting from 9 tables
│       └── scripts/         # pg.py (embedded PG), check_* (offline acceptance), e2e_*, smoke_*
├── work/                    # Workflow bundle: 7 graphs + manifest + import/export scripts (work/README.md)
├── docs/                    # AGENT-ACCESS.zh.md / LOCAL-LLM-TECH-REPORT.md / FULL-AUDIT-*.zh.md
├── PLAN.md                  # Full design and contract document (§10 is the REST contract)
├── pnpm-workspace.yaml
└── LICENSE                  # Apache-2.0
```

The following are **not in the repository** (gitignored; rebuild them per machine):

| Path | What it is | How it appears |
|---|---|---|
| `data/media`, `data/tmp` | Artifacts, uploads, exports, scratch | Created by the backend at startup |
| `data/pgdata` | Embedded PostgreSQL cluster | `apps/api/scripts/pg.py boot` |
| `apps/api/.venv`, `apps/api/.env` | Python environment and local config | See section 5 |
| `.tooling-pg/` | 3.12 environment that only runs the embedded PG | See 5.2 |
| `comfyui/` (if present) | One machine's ComfyUI copy | External runtime — install your own, never commit it |
| `reference/` | Clones of third-party projects studied for design | Read-only reference |

---

## 3. Dependencies

### 3.1 Hardware

| Item | Minimum | Notes |
|---|---|---|
| GPU | A single 24 GB card | H3 / Qwen-Image keep tens of GB of weights resident. The dispatcher refuses to submit below `H3_MIN_FREE_VRAM_GB` (12 by default) |
| VRAM exclusivity | — | A 24 GB card cannot hold "text model + video weights" at once, hence the single-GPU arbiter yields the LLM process before a local run |
| Disk | ≥ 200 GB free | Weights in tens of GB each; trash items are truly deleted only after 100 days |
| RAM | 32 GB | Video merging and keyframe decoding |
| OS | Windows or Linux | Commands below are PowerShell-first; all repo paths resolve relative to the repository root |

### 3.2 Required software

| Software | Version | Purpose | Verified against |
|---|---|---|---|
| Python | **3.12+** | Backend and `h3` CLI | 3.13.3 (`apps/api/.venv`) |
| Node.js | 20+ | Frontend build | 22.23.2 |
| pnpm | 9/10 | Workspace package manager | 10.33.0 |
| PostgreSQL | 14+ | The only persistence layer | Not needed if you use the embedded `pgserver` (5.2) |
| ffmpeg | 6+ | Export merging, media probing | 9.0.1; on `PATH` or an absolute path in Settings → System |

Backend Python dependencies (`apps/api/pyproject.toml`):
`fastapi` · `uvicorn[standard]` · `httpx` · `websockets` · `sqlalchemy[asyncio]` · `asyncpg` ·
`alembic` · `pydantic-settings` · `argon2-cffi` · `pyjwt` · `python-multipart` · `cryptography` ·
`mcp>=2,<3`; `[dev]` extra: `pytest`, `pytest-asyncio`, `aiosqlite`; `[desktop]` extra: `pgserver`,
`psutil` — only the single-process start in 1.2 needs them, and `pgserver` ships cp312 wheels only, so
that environment must be Python 3.12.

Frontend runtime: `react`/`react-dom` 19.2, `react-router-dom` 7.1, `@tanstack/react-query` 5,
`zustand` 5, `recharts` 2, `lucide-react`, `@dnd-kit/*`; build: `vite` 6, `typescript` ~5.8,
`tailwindcss` 4.

### 3.3 Generation side (external, not bundled)

| Item | Requirement |
|---|---|
| ComfyUI | **0.37.4 onward (this project's verified baseline)**. The graphs here use core nodes such as `MiniMaxH3*`, `EasyCache`, `ComfyMathExpression`, `QwenImage21Cache`, `EmptyFlux2LatentImage`, `Flux2Scheduler`, `SaveAudioMP3`, `TrimAudioDuration`; older builds report unknown nodes at import time |
| Binding | Loopback only (e.g. `127.0.0.1:8188`). **Do not pass `--enable-cors-header`**: it replaces ComfyUI's loopback CSRF protection with permissive CORS, and this project always calls ComfyUI server-side — the browser never connects directly |
| Custom node packs | `ComfyUI-Whisper` (`Apply Whisper`) and `ComfyUI-Qwen-TTS` (`FB_Qwen3TTSVoiceClone`) — needed only by the voice-clone graph; `ComfyUI-minimaxH3-SequenceForge` (`H3SeamlessChainSampler` / `H3SeamDoctor`, no extra Python deps) — needed only by the built-in long-take chain. Install the first two by directory name via ComfyUI Manager and pip-install each pack's `requirements.txt` into ComfyUI's environment |
| Model weights | Table below; filenames must match **character for character** (import weight alignment never approximates) |
| Cloud alternative | RunningHub instances (`protocol=rh_task`, `placement=cloud_runninghub`): billed per second, so set `retainSeconds` to release them |

Weights (relative to `<ComfyUI>/models/`):

| Directory | File | Used by |
|---|---|---|
| `unet/` | `MiniMax_H3_FL2VA_pruned_int8_convrot.safetensors` | H3 first/last-frame video |
| `unet/` | `MiniMax_H3_Ref2VA_pruned_int8_convrot.safetensors` | H3 reference-to-video (motion transfer, drama assistant, 60 s stitch) |
| `unet/` | `flux-2-klein-base-9b.safetensors` | Both Klein graphs |
| `diffusion_models/` | `qwen_image_2.1_int8_convrot.safetensors` (or `…_bf16`) | Built-in Qwen-Image |
| `clip/` | `qwen3vl_32b_minimax_h3_int8_convrot.safetensors` | All H3 graphs |
| `clip/` | `qwen_3_8b.safetensors` | Both Klein graphs |
| `clip/` | `qwen3vl_8b_bf16.safetensors` or `qwen3.5_9b_qwen_image_2.1_pe_{t2i,i2i}.int8_convrot.safetensors` | Built-in Qwen-Image |
| `vae/` | `minimax_h3_video_vae_fp16.safetensors`, `minimax_h3_audio_vae_fp32.safetensors` | All H3 graphs (separate video and audio VAE) |
| `vae/` | `flux2-vae.safetensors`, `qwen_image_2.1_vae_bf16.safetensors` | Klein / Qwen-Image |
| `loras/` | `minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors` (plus `fl2v_turbo_4step_v1.0_768p` on the first/last-frame side) | Speed tier, optional |
| `stt/whisper/` | `large-v3.pt` (`tiny`…`turbo` all selectable) | Voice clone: transcribing the reference audio |
| `qwen-tts/` | `Qwen3-TTS-12Hz-0.6B-Base`, `Qwen3-TTS-12Hz-0.6B-CustomVoice`, `Qwen3-TTS-Tokenizer-12Hz` | Voice clone: target line |

### 3.4 Text models (optional, but no script/storyboard work without them)

Any OpenAI-compatible endpoint works: `llama.cpp`'s `llama-server`, `Ollama`, `LM Studio`, `vLLM`, or a
hosted API. Five purposes map independently to backends: `script_write`, `script_parse`, `storyboard`,
`visualize`, `h3_prompt`. A 27B-class model alone fills a 24 GB card, so it is **mutually exclusive**
with the generation side — the single-GPU arbiter yields it automatically.

---

## 4. Port map

| Port | Owner | Notes |
|---|---|---|
| `5173` | Vite dev server | Proxies `/api` and `/ws` to `8788` in real mode |
| `8788` | FastAPI backend (REST itself) | Includes `/docs`; web and trusted scripts only |
| `8790` | MCP streamable-http | This is what you expose to LAN/public, not `8788` |
| `8188` | Local ComfyUI (external) | Loopback only |
| dynamic | Embedded PostgreSQL | Assigned by `pgserver` and written back to `apps/api/.env` |

---

## 5. Install and run

### 5.1 Backend

```powershell
git clone <your-fork-url> H3 && cd H3

python -m venv apps/api/.venv            # Python 3.12+
apps/api/.venv/Scripts/python.exe -m pip install -U pip
apps/api/.venv/Scripts/python.exe -m pip install -e apps/api
apps/api/.venv/Scripts/python.exe -m pip install "pytest>=8" "pytest-asyncio>=0.24" aiosqlite   # dev/tests, optional
```

The `-e` step produces the `h3` command (`apps/api/.venv/Scripts/h3.exe`); everything below uses it.
On Linux the paths are `apps/api/.venv/bin/…`.

Copy the config template: `apps/api/.env.example` → `apps/api/.env` (fields in section 6).

### 5.2 Database

**A. Embedded PostgreSQL (recommended; touches nothing system-wide)** — needs a 3.12 environment
because `pgserver` only publishes cp312 wheels:

```powershell
py -3.12 -m venv .tooling-pg             # install a 3.12 first if you have none
.tooling-pg/Scripts/python.exe -m pip install pgserver
.tooling-pg/Scripts/python.exe apps/api/scripts/pg.py boot    # creates role h3 + db h3studio, writes the URL into apps/api/.env
.tooling-pg/Scripts/python.exe apps/api/scripts/pg.py status   # | stop | uri | psql "SELECT 1"
```

Data lives in `data/pgdata`; the port is assigned dynamically and written back to `.env` (don't hand-edit
it). Deleting that directory is a complete uninstall.

**B. Existing PostgreSQL**: create role and database, put the URL into `H3_DATABASE_URL` in
`apps/api/.env` (`postgresql+asyncpg://user:pass@127.0.0.1:5432/h3studio`) and skip A. The backup helper
shells out to `pg_dump`; on standalone installs set `H3_PG_DUMP_PATH` to an absolute path.

### 5.3 Schema + backend

```powershell
cd apps/api
.venv/Scripts/alembic.exe upgrade head          # 9 migrations, 9 initial tables
.venv/Scripts/h3.exe serve                       # foreground backend (--reload for hot reload)
curl http://127.0.0.1:8788/healthz               # {"ok":true,"database":true,"instances":0,…}
```

### 5.4 Frontend

```powershell
pnpm install
$env:VITE_USE_MOCK = "false"     # critical: must be a process env var — vite.config.ts reads process.env
pnpm --filter h3-studio-web dev  # http://127.0.0.1:5173
```

⚠ Putting `VITE_USE_MOCK=false` into `apps/web/.env` only affects `import.meta.env` (client side);
it does **not** enable the `/api` and `/ws` proxy, whose switch reads `process.env` in `vite.config.ts`.
Setting it in the shell covers both.

Production build: `pnpm --filter h3-studio-web build` (`tsc -b && vite build` → `apps/web/dist`). Once that
directory exists the backend mounts it, so `h3 serve` alone gives you the whole product at
`http://127.0.0.1:8788/` — one process, one port, no vite in the loop. If `apps/web/dist` is absent the
mount is skipped and `8788` is API-only. The mount is decided when the backend starts, so build the
frontend *before* section 5.3 or restart the backend afterwards. `pnpm --filter h3-studio-web preview`
is the vite-side preview, handy while you are still iterating on the UI against a running backend.

### 5.5 Sign in

On a loopback-only dev setup the backend prepares a demo account and the frontend auto-signs in:
**`admin` / `12345`** (created only on loopback, and only while the database has no accounts at all).
If that demo `admin` still carries the previous demo password `1234`, a loopback startup rotates it to
`12345`; a password somebody actually changed is never touched. Disable auto-login with
`$env:VITE_DEV_AUTOLOGIN="false"`. After you set your own password, point `VITE_DEV_PASS` at it or turn
auto-login off. On an unseeded empty database use `h3 bootstrap` or the login page; once any account
exists, the seed stops applying.

Any time after signing in, the **改密 / Change password** entry in the left rail lets you change your own
password: it verifies the current one first, and every browser session of that account — including the page
you are on — is invalidated, so you sign back in with the new password. CLI and agent `agent token`s are
untouched. The password bar has two rules only: at least 4 characters, and not on the common-password list.

### 5.6 Add a generation instance

Settings → Servers & workflows → new instance, or:

```powershell
h3 instance create --data '{"name":"local comfy","baseUrl":"http://127.0.0.1:8188","placement":"local","protocol":"comfy_native"}'
h3 instance probe <id>       # real probe, pulls /object_info
h3 instance ping <id>        # lightweight liveness (system_stats + queue only)
```

RunningHub: set `protocol=rh_task`, `placement=cloud_runninghub`, the API key and `site`, plus
`retainSeconds` because it bills per second.

### 5.7 Import the workflow bundle

```powershell
python work/import_workflows.py --dry-run
python work/import_workflows.py --username admin --password 12345
```

Per-graph weight requirements, how to read the import report, and why the three built-ins are not in the
bundle: [`work/README.md`](work/README.md).

### 5.8 Attach a text model

Settings → AI models → new backend with the OpenAI-compatible base URL (e.g.
`http://127.0.0.1:8080/v1`) and model name; after a successful probe, map the five purposes to it.
CLI: `h3 llm create --data '{…}'` / `h3 llm probe <id>` / `h3 llm local-scan` (finds inference servers
already running locally) / `h3 llm put-defaults`.

### 5.9 One-shot (chains 5.1–5.5 plus agent wiring)

```powershell
h3 setup --dry-run          # show what it would do
h3 setup --client qoder     # env → deps → embedded DB → migrations → backend → agent token → client MCP config
h3 doctor                   # backend / DB / instances / text models / credentials / MCP port / ffmpeg / clients
```

---

## 6. Runtime configuration

`apps/api/.env`, prefix `H3_` (template: `apps/api/.env.example`):

| Variable | Default | Meaning |
|---|---|---|
| `H3_DATABASE_URL` | empty | Empty = database-less mode: instance client only; queue / users / agent tokens unavailable |
| `H3_HOST` / `H3_PORT` | `127.0.0.1` / `8788` | Binding beyond loopback prints a warning — expose MCP instead |
| `H3_MEDIA_ROOT` / `H3_TMP_ROOT` | `data/media` / `data/tmp` | The default the backend uses while Settings → System has stored nothing. A directory picked in the UI overrides it from the next start on and lives in `app_settings['paths']` — it is never written back into `.env`. Relative paths resolve against the backend's working directory (`apps/api/`) |
| `H3_INSTANCES_FILE` | `apps/api/instances.dev.json` | Bootstrap source in database-less mode; with a DB, `gen_instances` is authoritative |
| `H3_HOME` | `<repo>/data` | Root of everything writable: media, tmp, embedded `pgdata`, logs (plus the secret key in the single-process form; in development the key deliberately stays at `apps/api/.secret.key`). Read from the **process environment only** — a `H3_HOME=` line in `.env` is ignored, since this path has to be known before `.env` is located |
| `H3_JWT_SECRET` | empty | Empty generates `.secret-jwt_key.txt`; a value shorter than 32 bytes refuses to start |
| `H3_JWT_TTL_S` / `H3_REFRESH_TTL_S` | `900` / `2592000` | Session and refresh lifetimes |
| `H3_MIN_FREE_VRAM_GB` | `12` | No image/video job is submitted below this free VRAM |
| `H3_GPU_ARBITER` | `true` | Single-GPU arbiter yields the text model before local runs |
| `H3_TRASH_RETENTION_DAYS` | `100` | Generation trash expiry; also `H3_TRASH_PURGE_INTERVAL_S` (6 h) and `H3_TRASH_PURGE_LIMIT` (500) |
| `H3_PG_DUMP_PATH` | empty | Required absolute path on standalone PostgreSQL installs, otherwise backup suggestions stay empty |
| `H3_COMFY_POLL_INTERVAL_S` / `H3_WS_BACKOFF_MAX_S` | `2` / `30` | ComfyUI polling and WS reconnect backoff |
| `H3_LOG_LEVEL` | `INFO` | Logs go to stdout (visible under `h3 serve`; a detached backend writes `~/.h3/logs/api.log`); every line is redacted for tokens/API keys |

`/healthz` confirms backend + database; `h3 config` shows the CLI's local config (`~/.h3/config.json`, redacted).

---

## 7. Agent access (MCP / CLI)

```bash
h3 mcp install --client qoder      # writes client MCP config, backing up the file first; one target per client
h3 mcp tools                       # list tools
h3 mcp test                        # real handshake + one read-only tool call
h3 token create --name guest --scope read   # a separate, revocable key per colleague/agent
h3 expose lan                      # LAN :8790 with a paste-ready config
h3 expose cloud                    # ephemeral public domain via cloudflared (must be installed)
h3 expose off                      # tear it down
```

Local agents do not need any exposure: the stdio command line in the MCP config reads
`~/.h3/config.json` itself, so no token lands in the client's config file. The tool surface has 73 tools
covering status / workflow_select / job_plan / job_submit / job_wait / media_* / script_* / export_* and
friends; a typical round is `status → workflow_select → job_plan → job_submit → job_get → media_location`.
Details in [`docs/AGENT-ACCESS.zh.md`](docs/AGENT-ACCESS.zh.md) (Chinese).

---

## 8. Operations

| Task | Command / screen |
|---|---|
| Live picture (instance reachability, queue backlog, VRAM and yield state, disk) | `h3 status` |
| Health check (each red line names the next command) | `h3 doctor` (`--strict` to exit non-zero) |
| Storage report and GC | Settings → System, or `h3 system storage` / `h3 system gc` |
| Manually yield / restore the text model | Settings → System, or `h3 system gpu-yield` / `h3 system gpu-restore` |
| Backup | Commands surfaced by Settings → System (`pg_dump` + media directory) |
| Audit trail | Settings → System, or `h3 system audit` |
| Call any endpoint directly | `h3 routes` then `h3 api GET /api/jobs` |

---

## 9. Development and verification

```powershell
pnpm --filter h3-studio-web typecheck    # tsc -b --noEmit
pnpm --filter h3-studio-web build        # tsc -b && vite build

cd apps/api
.venv/Scripts/pytest.exe                  # offline unit tests (asyncio auto, aiosqlite)
.venv/Scripts/pytest.exe -m "not live"    # skip everything needing a real instance

# offline acceptance (no server required; pure functions or DB only)
.venv/Scripts/python.exe scripts/check_workflow_bindings.py
.venv/Scripts/python.exe scripts/check_workflow_autoselect.py
.venv/Scripts/python.exe scripts/check_director_pipeline.py
.venv/Scripts/python.exe scripts/check_job_plan.py
.venv/Scripts/python.exe scripts/check_skills.py

# live end-to-end (needs an online instance and real weights; marked `live`)
.venv/Scripts/python.exe scripts/e2e_queue.py
.venv/Scripts/python.exe scripts/e2e_generate.py
.venv/Scripts/python.exe scripts/e2e_director.py
.venv/Scripts/python.exe scripts/e2e_chain_video.py
.venv/Scripts/python.exe scripts/e2e_voice_clone.py
.venv/Scripts/python.exe scripts/run_job.py --kind image --slots '{"prompt":"…"}'   # slots are named by signal
```

Non-negotiables when changing code (rationale in `docs/FULL-AUDIT-2026-10-02.zh.md`): weight names are
never approximated, auto-selection must compact input slots after pruning, retries cannot reuse a stale
graph, changing a default also touches `graph_original`, artifacts are only `type=output`, and a new page
must register with the reconciliation pass.

---

## 10. Troubleshooting

| Symptom | Check first |
|---|---|
| Frontend shows mock data / `/api` returns 404 | Is the backend up? Is `VITE_USE_MOCK=false` set in the **process environment** (see 5.4)? |
| `http://127.0.0.1:8788/` gives JSON 404 instead of the UI | `apps/web/dist` was missing when the backend started — build the frontend, then restart (the mount is decided at startup) |
| Login 401 / demo account gone | The seed only creates the demo account while the DB is empty; `admin/12345` exists on loopback only — if you changed your password, sign in with the new one |
| Jobs stuck in `queued` | `h3 status` for the VRAM gate (`H3_MIN_FREE_VRAM_GB`) and instance circuit breaker; make sure the text model isn't holding the card |
| Import reports unknown nodes | ComfyUI older than the verified 0.37.4 baseline, or a custom node pack is missing |
| Missing weight at runtime / file-not-found mid-run | Compare filenames character for character against `requiredWeights` in `work/manifest.json` — casing, underscores and `.1` all count |
| Voice clone won't run | Are `ComfyUI-Whisper` and `ComfyUI-Qwen-TTS` plus their Python deps installed into ComfyUI's environment? Are `stt/whisper/large-v3.pt` and `qwen-tts/*` present? |
| Long-take chaining unavailable | `ComfyUI-minimaxH3-SequenceForge` missing — affects only this feature, per-shot generation is fine |
| Export fails | Is `ffmpeg` on PATH, or set an absolute path in Settings → System |
| Backup screen shows no commands | `H3_PG_DUMP_PATH` unset (embedded `pgserver` ships its own `pg_dump`) |
| Agent sees no tools | `h3 mcp test` for a real handshake; reload the client (Qoder `/mcp reload`, Claude Desktop: fully quit and reopen) |
| Plaintext secrets in logs | Should not happen — `logging_setup` redacts tokens/API keys; treat it as an incident if you find one |

---

## 11. Documentation index

| Document | Contents |
|---|---|
| `PLAN.md` | Full design and interface contract (§10 is the REST contract) |
| `work/README.md` | Workflow bundle: 7 graphs, weight and node requirements, import and verification |
| `docs/AGENT-ACCESS.zh.md` | MCP / CLI access and exposure policy (Chinese) |
| `docs/LOCAL-LLM-TECH-REPORT.md` | Local text-model selection and benchmarks (Chinese version alongside) |
| `docs/FULL-AUDIT-2026-10-02.zh.md` | Full audit record incl. code-level invariants (Chinese) |

## 12. License

Apache License 2.0 — see `LICENSE`. Model weights, ComfyUI and the custom node packs carry their own
licenses and are not included when distributing this repository.
