# H3 Studio

**本地优先的 AI 短剧生产台**：从小说/梗概到成片，剧本 → 角色与场景 → 导演台逐镜头出片 → 剪辑导出，
一条链跑在自己机器上。生成侧挂 ComfyUI（本机或云端 RunningHub），文本侧挂任意 OpenAI 兼容的本地或云端模型，
并且把整套能力以 REST + MCP + CLI 三个面暴露给智能体。

- 前端：React 19 + Vite 6 + TypeScript 5.8 + Tailwind 4（pnpm workspace）
- 后端：FastAPI + SQLAlchemy 2 (async) + PostgreSQL + Alembic，Python 3.12+
- 许可：Apache-2.0（见 `LICENSE`）

> **本仓库不包含 ComfyUI 本体、模型权重和参考项目。** 生成侧要自备一套 ComfyUI（见「依赖 → 生成侧」），
> 软件目录里也没有 `comfyui/` 这一层——它是外部运行时，不是本项目的一部分。

英文文档：[`README.md`](README.md) · 工作流包：[`work/README.zh.md`](work/README.zh.md)

---

## 1. 功能模块

### 1.1 页面层（`apps/web`）

全站有登录守卫；窄于 1024px 不提供排版（生产台按桌面设计）。侧栏按四阶段推进。

| 范围 | 模块 | 路由 | 做什么 |
|---|---|---|---|
| 全局 | 登录 / 首个管理员 | `/login` | 账号口令登录；空库时建第一个管理员；登录后左栏「改密」自助改口令 |
| 全局 | 仪表盘 · 项目库 | `/` | 新建/进入项目，跨项目资产库、模型配置、产线状态卡（队列/实例/磁盘） |
| 全局 | 工作流库 | `/workflows` | 导入向导、参数槽、等价改写清单、按任务自动选、试运行、RunningHub 投影、导出 |
| 全局 | 技能库 | `/skills` | 提示词技能：新建、批量导入（`.json`/`.md`/`.txt`）、改正文、清空 |
| 全局 | 生成历史 | `/history` | 剧本/图片/视频三桶版本，按项目筛选、设为当前、恢复 |
| 全局 | 生成回收站 | `/trash` | 软删版本预览、恢复、两次点击彻底删除（默认满 100 天自动真删） |
| 全局 | 设置 · Server 与工作流 | `/settings/gen` | 生成实例增删改/探活/ping；工作流 × Server 的默认权重绑定与逐台体检 |
| 全局 | 设置 · AI 模型 | `/settings/llm` | 文本后端（Ollama / llama.cpp / LM Studio / vLLM / 任意 OpenAI 兼容）与「用途 → 后端」映射 |
| 全局 | 设置 · 用户与配额 | `/settings/users` | admin / editor / viewer 三角色、并发与日预算、弱口令拦截 |
| 全局 | 设置 · 系统 | `/settings/system` | 存储报表与清理、目录设置（媒体/临时/ComfyUI output/ffmpeg）、审计流水、备份命令 |
| 项目 01 | 剧本创作 | `/p/:id/script` | 剧本 / 拍摄清单 / 版本历史三 tab，右侧配置面板与悬浮改稿助手（行级 diff 待确认） |
| 项目 02 | 场景角色 | `/p/:id/assets` | 角色定妆、服装变体、场景概念图、音色克隆（参考音频 + 原文），资产库导入 |
| 项目 03 | AI 工作台（导演台） | `/p/:id/director` | 镜头网格、关键帧、逐镜头出片、时间轴、长片续拍、派发参数总表 |
| 项目 — | 任务队列 | `/p/:id/queue` | 取消、重试、优先级插队，按状态/类型/实例筛选 |
| 项目 — | 提示词管理 | `/p/:id/prompts` | 角色/场景/关键帧/视频四类提示词集中编辑 |
| 项目 04 | 制片导出 | `/p/:id/export` | 合并 mp4、素材打包、EDL/XML/剪映时间轴、逐镜预览、渲染日志 |

跨页共用：模型配置弹窗（全局/对话/图片/视频）、版本历史面板、生成选择（工作流 × 实例 × 权重）、
技能选择、工作流导入向导、可拖分栏。

### 1.2 后端能力层（`apps/api`）

| 模块 | 代码 | 职责 |
|---|---|---|
| 实例与协议 | `gen/registry.py`、`gen/comfy_native.py`、`api/routes_instances.py` | 本机 ComfyUI（HTTP + WS，`comfy_native`）与云端 RunningHub（`rh_task`）两种协议；探活/ping、能力探针、熔断 |
| 任务队列 | `queue.py`、`job_plan.py`、`api/routes_jobs.py` | 计划→批量派发→进度流（WS/SSE）→产物回收→取消/重试/插队；空闲显存闸门 |
| 单卡仲裁 | `gpu_arbiter.py` | 本地实例开跑前让文本模型进程退场，跑完再拉起；状态在「设置 → 系统」 |
| 工作流引擎 | `gen/workflow.py`、`gen/local_adapt.py`、`gen/subgraph.py` | UI↔API 双向转换、作者私有节点 → 核心节点等价改写、半截分支剪除、槽位抽取 |
| 按任务自动选 | `workflow_select.py`、`api/routes_workflows.py` | 按产出种类 + 图的输入信号 + 实例能力挑工作流，`priority` 定序 |
| 权重槽位 | `gen/model_slots.py`、`workflow_bindings.py` | 哪个加载节点吃哪个文件、这台实例上还有哪些候选；工作流 × 实例的默认权重三层绑定 |
| 体检 | `weights_health.py`、`media_health.py` | 缺节点/缺权重/半截下载逐台报告；媒体文件与库记录对账 |
| 内置模板 | `gen/builtin_graphs.py`、`gen/templates.py` | Qwen-Image 出图、H3 出片、H3 长片续拍三张现场生成的图（权重名按 `/object_info` 模糊解析） |
| 版本历史 | `api/routes_versions.py`、`purge.py` | 剧本/图片/视频 V1..Vn 与生成回收站；到期真删 |
| 制片导出 | `api/routes_export.py`、`export_paths.py` | ffmpeg 合并、素材打包、EDL/XML/剪映工程 |
| 文本模型 | `llm.py`、`api/routes_llm.py` | OpenAI 兼容后端探测与调用；`script_write` / `script_parse` / `storyboard` / `visualize` / `h3_prompt` 五类用途映射 |
| 剧本解析 | `parse.py`、`api/routes_parse.py` | 上传小说/剧本 → 角色、场景、镜头骨架 |
| 导演台状态 | `director.py` | 镜头 ↔ 关键帧 ↔ 承接链的服务端状态与派发编排 |
| 风格预设 | `styles.py` | 内置风格库 |
| 安全与账户 | `security.py`、`crypto.py`、`api/routes_auth.py`、`routes_tokens.py` | Argon2 口令、JWT 会话 + 刷新、agent token 三档 scope（read/dispatch/admin）、审计 |
| 智能体接入 | `mcp/server.py`、`mcp/tools.py`、`cli/` | MCP server（stdio / streamable-http）+ `h3` CLI + 局域网/公网暴露 |
| 存储与目录 | `runtime.py`、`paths.py`、`api/routes_system.py` | 可写数据根（开发态就是仓库 `data/`；`H3_HOME` 覆盖它，且**只认真实环境变量**，写进 `.env` 那一行无效）；媒体/临时/ffmpeg 目录在界面里改，改完落在库里的 `app_settings['paths']`，**不会回写 `.env`**，且媒体/临时要下次启动才换、ffmpeg 存完立刻生效；存储报表、GC、备份 |
| 前端静态托管 | `main.py` 的 `mount_web_client` | 只要 `apps/web/dist` 存在，后端自己就把构建好的界面挂在自己这个端口上，与 `/api` 同源——不用配 CORS、不用 vite 代理。未匹配的路径回 `index.html`，但 `/api`、`/docs`、`/redoc`、`/openapi.json` 是保留前缀，给的是 JSON 404 而不是 HTML |
| 单进程起法 | `desktop.py`（`h3 desktop`） | 一个进程把「内嵌库 → 迁移 → HTTP 服务」全带起来，并把真正拿到的端口写进 `desktop.json`（`8788` 可能已被开发版占着）。要装 `[desktop]` 那组依赖；安装包装层（桌面壳 + 冻结 spec）不随本仓库分发 |

### 1.3 三个对外面

同一个后端能力，三种用法：**REST**（网页与脚本）、**MCP**（智能体客户端）、**CLI**（`h3`，人和智能体都用）。
对外暴露的永远是 MCP 端口（默认 `8790`），不是 `8788`——`8788` 是带 `/docs` 与用户接口的后端本体。

---

## 2. 代码目录结构

```
H3/
├── apps/
│   ├── web/                # React SPA（Vite + TS + Tailwind 4）
│   └── api/               # FastAPI 后端 + h3 CLI + MCP server
│       ├── app/
│       │   ├── api/       # 11 个路由模块（REST 契约）
│       │   ├── gen/       # ComfyUI 协议、工作流引擎、等价改写、内置模板
│       │   ├── cli/       # h3 命令、setup/doctor、客户端 MCP 配置写入
│       │   ├── mcp/       # MCP server 与 70+ 工具
│       │   └── *.py       # queue / gpu_arbiter / workflow_select / purge …
│       ├── alembic/       # 9 个迁移，初始 9 表 → 工作流绑定/技能/版本历史/app_settings
│       └── scripts/       # pg.py（内嵌 PG 启停）、check_*（离线验收）、e2e_*（真机）、smoke_*
├── work/                  # 工作流包：7 张图 + manifest + 导入器/导出器，见 work/README.zh.md
├── docs/                  # AGENT-ACCESS.zh.md / LOCAL-LLM-TECH-REPORT.zh.md / FULL-AUDIT-*.zh.md
├── PLAN.md                # 设计与契约全文（§10 是接口契约）
├── pnpm-workspace.yaml
└── LICENSE                # Apache-2.0
```

以下内容**不在仓库里**（`.gitignore` 已排除，换机器需要重建）：

| 路径 | 是什么 | 怎么来 |
|---|---|---|
| `data/media`、`data/tmp` | 产物、上传、导出、临时文件 | 后端启动自动建目录 |
| `data/pgdata` | 内嵌 PostgreSQL 的数据目录 | `apps/api/scripts/pg.py boot` |
| `apps/api/.venv`、`apps/api/.env` | Python 环境与本机配置 | 见「5. 安装」 |
| `.tooling-pg/` | 只跑内嵌 PG 的 3.12 环境 | 见「5.2」 |
| `comfyui/`（若有） | 某台机器上的 ComfyUI 副本 | 外部运行时，自己装，别提交 |
| `reference/` | 借鉴用的第三方仓库副本 | 只读参考，不入库 |

---

## 3. 依赖

### 3.1 硬件

| 项 | 最低 | 说明 |
|---|---|---|
| GPU | 24 GB 显存的单张卡 | H3 / Qwen-Image 出图出片常驻十 GB 级权重。派发闸门默认空闲显存 < 12 GB 不提交（`H3_MIN_FREE_VRAM_GB`） |
| 显存互斥 | — | 24 GB 卡放不下「文本模型 + 出片权重」同时常驻，所以有单卡仲裁：本地实例开跑前先把文本模型让开 |
| 磁盘 | ≥ 200 GB 空闲 | 权重按十 GB 计，产物按条计；回收站默认留 100 天才真删 |
| 内存 | 32 GB | 视频合并与关键帧解码 |
| 系统 | Windows / Linux 均可 | 文档里的命令以 Windows PowerShell 为主；仓库路径全部按 repo-relative 解析 |

### 3.2 必装软件

| 软件 | 版本 | 用途 | 实测参考 |
|---|---|---|---|
| Python | **3.12+** | 后端与 `h3` CLI | 3.13.3（`apps/api/.venv`） |
| Node.js | 20+ | 前端构建 | 22.23.2 |
| pnpm | 9/10 | workspace 包管理 | 10.33.0 |
| PostgreSQL | 14+ | 唯一持久层 | 用内嵌 `pgserver` 就不必系统装（见 5.2） |
| ffmpeg | 6+ | 导出合并、媒体探测 | 9.0.1；要在 PATH 里，或在「设置 → 系统」指路径 |

后端 Python 依赖（`apps/api/pyproject.toml`）：
`fastapi` · `uvicorn[standard]` · `httpx` · `websockets` · `sqlalchemy[asyncio]` · `asyncpg` ·
`alembic` · `pydantic-settings` · `argon2-cffi` · `pyjwt` · `python-multipart` · `cryptography` ·
`mcp>=2,<3`；开发依赖 `[dev]`：`pytest`、`pytest-asyncio`、`aiosqlite`；`[desktop]`：`pgserver`、`psutil`
—— 只有 1.2 那条单进程起法要它们，而 `pgserver` 的 wheel 只出到 cp312，所以那套环境必须是 Python 3.12。

前端运行时：`react`/`react-dom` 19.2、`react-router-dom` 7.1、`@tanstack/react-query` 5、`zustand` 5、
`recharts` 2、`lucide-react`、`@dnd-kit/*`；构建：`vite` 6、`typescript` ~5.8、`tailwindcss` 4。

### 3.3 生成侧（外部，仓库不带）

| 项 | 要求 |
|---|---|
| ComfyUI | **0.37.4 起（本项目的实测基线）**。本项目的图用到 `MiniMaxH3*`、`EasyCache`、`ComfyMathExpression`、`QwenImage21Cache`、`EmptyFlux2LatentImage`、`Flux2Scheduler`、`SaveAudioMP3`、`TrimAudioDuration` 等核心节点，老版本会在导入体检时报 unknown node |
| 监听 | 只绑回环（如 `127.0.0.1:8188`）。**不要开 `--enable-cors-header`**：那会把 ComfyUI 的环回 CSRF 防护换成宽松 CORS，而本项目一律由后端服务端调用，前端不直连 |
| 自定义节点包 | `ComfyUI-Whisper`（`Apply Whisper`）、`ComfyUI-Qwen-TTS`（`FB_Qwen3TTSVoiceClone`）——只有声音克隆那条要用；`ComfyUI-minimaxH3-SequenceForge`（`H3SeamlessChainSampler`/`H3SeamDoctor`）——只有内置长片续拍要用，零额外 Python 依赖。前两个在 ComfyUI Manager 里按目录名搜索安装，各自 `requirements.txt` 装进 ComfyUI 的 Python 环境 |
| 模型权重 | 见下表；文件名必须一字不差（导入的权重对齐**不做近似匹配**） |
| 云端替代 | RunningHub 实例（协议 `rh_task`、`placement=cloud_runninghub`）：按秒计费，必须设 `retainSeconds` 到期释放 |

权重清单（相对 `<ComfyUI>/models/`）：

| 目录 | 文件 | 谁用 |
|---|---|---|
| `unet/` | `MiniMax_H3_FL2VA_pruned_int8_convrot.safetensors` | H3 首尾帧出片 |
| `unet/` | `MiniMax_H3_Ref2VA_pruned_int8_convrot.safetensors` | H3 参考生视频（含动作迁移、短剧助手、60 秒拼接） |
| `unet/` | `flux-2-klein-base-9b.safetensors` | Klein 设定图 / 指令编辑 |
| `diffusion_models/` | `qwen_image_2.1_int8_convrot.safetensors`（或 `…_bf16`） | 内置 Qwen-Image 出图 |
| `clip/` | `qwen3vl_32b_minimax_h3_int8_convrot.safetensors` | 全部 H3 图 |
| `clip/` | `qwen_3_8b.safetensors` | 两条 Klein |
| `clip/` | `qwen3vl_8b_bf16.safetensors` 或 `qwen3.5_9b_qwen_image_2.1_pe_{t2i,i2i}.int8_convrot.safetensors` | 内置 Qwen-Image |
| `vae/` | `minimax_h3_video_vae_fp16.safetensors`、`minimax_h3_audio_vae_fp32.safetensors` | 全部 H3 图（视频流 + 音频流两个 VAE） |
| `vae/` | `flux2-vae.safetensors`、`qwen_image_2.1_vae_bf16.safetensors` | Klein / Qwen-Image |
| `loras/` | `minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors`（首尾帧侧另有 `fl2v_turbo_4step_v1.0_768p`） | 加速档位，可选 |
| `stt/whisper/` | `large-v3.pt`（`tiny`…`turbo` 皆可换档） | 声音克隆：转写参考音频 |
| `qwen-tts/` | `Qwen3-TTS-12Hz-0.6B-Base`、`Qwen3-TTS-12Hz-0.6B-CustomVoice`、`Qwen3-TTS-Tokenizer-12Hz` | 声音克隆：目标台词 |

### 3.4 文本模型（可选，但没有它就做不了剧本/分镜）

任何 OpenAI 兼容端点都能接：`llama.cpp` 的 `llama-server`、`Ollama`、`LM Studio`、`vLLM`，或云端 API。
五个用途各自可指不同后端：`script_write`（写剧本）、`script_parse`（拆解）、`storyboard`（分镜）、
`visualize`（画面化）、`h3_prompt`（出片提示词）。27B 级模型常驻就要吃满 24 GB 卡，
所以它与生成侧是**互斥**关系，由单卡仲裁自动让位。

---

## 4. 端口一览

| 端口 | 是谁 | 备注 |
|---|---|---|
| `5173` | Vite 开发服务器 | 真接后端时把 `/api`、`/ws` 代理到 `8788` |
| `8788` | FastAPI 后端（REST 本体） | 含 `/docs`；只给网页端和可信脚本 |
| `8790` | MCP streamable-http | 局域网/公网暴露的是它，不是 `8788` |
| `8188` | 本机 ComfyUI（外部） | 只绑回环 |
| 动态 | 内嵌 PostgreSQL | 由 `pgserver` 分配并回写 `apps/api/.env` |

---

## 5. 安装与启动

### 5.1 后端

```powershell
git clone <你的仓库地址> H3 && cd H3

python -m venv apps/api/.venv            # Python 3.12+
apps/api/.venv/Scripts/python.exe -m pip install -U pip
apps/api/.venv/Scripts/python.exe -m pip install -e apps/api
apps/api/.venv/Scripts/python.exe -m pip install "pytest>=8" "pytest-asyncio>=0.24" aiosqlite   # 开发/测试用，可省
```

`-e` 这一步会装出 `h3` 命令（`apps/api/.venv/Scripts/h3.exe`），后面所有运维都靠它。

复制一份配置：`apps/api/.env.example` → `apps/api/.env`（字段见第 6 节）。

### 5.2 数据库

**A. 内嵌 PostgreSQL（推荐，不动系统）**——需要一个 3.12 的环境，因为 `pgserver` 的 wheel 只出到 cp312：

```powershell
py -3.12 -m venv .tooling-pg          # 没有 3.12 就先从 python.org / winget 装一个
.tooling-pg/Scripts/python.exe -m pip install pgserver
.tooling-pg/Scripts/python.exe apps/api/scripts/pg.py boot    # 建库 h3 + 角色 h3，并把连接串写进 apps/api/.env
.tooling-pg/Scripts/python.exe apps/api/scripts/pg.py status   # | stop | uri | psql "SELECT 1"
```

数据目录在 `data/pgdata`，端口由 pgserver 动态分配（会写回 `.env`，别手改）。整目录删掉就是彻底卸载。

**B. 已有独立 PostgreSQL**：建库建角色，把连接串写进 `apps/api/.env` 的 `H3_DATABASE_URL`
（形如 `postgresql+asyncpg://user:pass@127.0.0.1:5432/h3studio`），跳过 A。备份功能要用 `pg_dump`，
在 `.env` 里给 `H3_PG_DUMP_PATH` 指绝对路径。

### 5.3 建表 + 起后端

```powershell
cd apps/api
.venv/Scripts/alembic.exe upgrade head          # 9 个迁移，初始 9 张表
.venv/Scripts/h3.exe serve                       # 前台起后端（--reload 可开热重载）
curl http://127.0.0.1:8788/healthz               # {"ok":true,"database":true,"instances":0,…}
```

### 5.4 前端

```powershell
pnpm install
$env:VITE_USE_MOCK = "false"     # 关键：必须给进程环境，vite.config.ts 读的是 process.env
pnpm --filter h3-studio-web dev   # http://127.0.0.1:5173
```

⚠ 把 `VITE_USE_MOCK=false` 写进 `apps/web/.env` 只影响 `import.meta.env`（客户端读得到），
**不会**打开 `/api`、`/ws` 代理——代理开关在 `vite.config.ts` 里读的是 `process.env`。只在 shell 里设，
两边就都对了。

生产构建：`pnpm --filter h3-studio-web build`（`tsc -b && vite build`，产物 `apps/web/dist`）。这个目录一旦存在，
后端就会把它挂上，所以只跑 `h3 serve` 就能在 `http://127.0.0.1:8788/` 打开完整界面——一个进程一个端口，中间没有 vite。
`apps/web/dist` 不存在时这一步直接跳过，`8788` 就只是 API。挂载是在后端启动时定下来的，所以要么先建前端再走 5.3，
要么建完重启后端。`pnpm --filter h3-studio-web preview` 是 vite 那侧的预览，边改界面边连着在跑的后端时更顺手。

### 5.5 登录

只绑回环的开发模式下，后端会备好演示账号并在前端自动登录：**`admin` / `12345`**（这个账号只在环回、且库里一个账号都没有时才建）。
库里那个演示 `admin` 的口令还停在上一代的 `1234` 时，环回启动会把它升到 `12345`；被人真正改过的口令不会被碰。
不想自动登录：`$env:VITE_DEV_AUTOLOGIN="false"`。改过自己的口令之后，把 `VITE_DEV_PASS` 设成新口令，或者干脆关掉自动登录。
空库且没被 seed 时，用 `h3 bootstrap` 或登录页建第一个管理员；一旦库里有了账号，seed 就不再生效。

登录之后左栏「改密」可以随时改自己的口令：先验当前口令，改完这个账号在**所有浏览器里的登录都作废**（包括当前这一页），
要用新口令重新进来；CLI 与智能体用的 `agent token` 不受影响。口令门槛只有两条：≥4 位、不在常见弱口令表里。

### 5.6 加生成实例

「设置 → Server 与工作流 → 新建实例」，或命令行：

```powershell
h3 instance create --data '{"name":"本机 ComfyUI","baseUrl":"http://127.0.0.1:8188","placement":"local","protocol":"comfy_native"}'
h3 instance probe <id>       # 真探活，读 /object_info
h3 instance ping <id>        # 轻量在线探测（只问 system_stats + queue）
```

RunningHub 云实例：协议选 `rh_task`、`placement=cloud_runninghub`、填 API Key 与 `site`，
并按秒计费设 `retainSeconds`。

### 5.7 导入工作流

```powershell
python work/import_workflows.py --dry-run
python work/import_workflows.py --username admin --password 12345
```

细节（每条要哪些权重、导入报告怎么读、内置三条为何不在包里）见 [`work/README.zh.md`](work/README.zh.md)。

### 5.8 配文本模型

「设置 → AI 模型 → 新建后端」填 OpenAI 兼容地址（如 `http://127.0.0.1:8080/v1`）与模型名，探活通过后
把五个用途映射到它。命令行：`h3 llm create --data '{…}'` / `h3 llm probe <id>` / `h3 llm local-scan`
（扫本机在跑的推理服务）/ `h3 llm put-defaults`（设「用途 → 后端」映射）。

### 5.9 一键（把 5.1–5.5 + 智能体接入串起来）

```powershell
h3 setup --dry-run          # 先看要做什么
h3 setup --client qoder     # 环境→依赖→内嵌库→迁移→起后端→发 agent token→写客户端 MCP 配置
h3 doctor                   # 体检：后端/库/实例/文本模型/凭据/MCP 端口/ffmpeg/客户端注册
```

---

## 6. 运行配置

`apps/api/.env`，前缀 `H3_`（模板见 `apps/api/.env.example`）：

| 变量 | 默认 | 说明 |
|---|---|---|
| `H3_DATABASE_URL` | 空 | 空 = 无库模式：只有实例客户端，队列/用户/agent token 全不可用 |
| `H3_HOST` / `H3_PORT` | `127.0.0.1` / `8788` | 监听非环回会打印警告，给智能体请走 MCP |
| `H3_MEDIA_ROOT` / `H3_TMP_ROOT` | `data/media` / `data/tmp` | 界面上没存过目录时后端用的就是这一份。「设置 → 系统」里选过的从下次启动起覆盖它，存在库的 `app_settings['paths']` 里，**不回写 `.env`** |
| `H3_INSTANCES_FILE` | `apps/api/instances.dev.json` | 无库模式的引导源；有库后库里的 `gen_instances` 才是权威 |
| `H3_HOME` | `<仓库>/data` | 所有可写东西的根：媒体、临时、内嵌 `pgdata`、日志（单进程形态连密钥也在里面；开发态的密钥故意留在 `apps/api/.secret.key`，挪走等于让库里已加密的 apiKey 全部解不开）。**只认真实环境变量**——`.env` 里写 `H3_HOME=` 是故意不生效的，因为这个路径要在找到 `.env` 之前就定下来 |
| `H3_JWT_SECRET` | 空 | 空则自动生成 `.secret-jwt_key.txt`；给了但短于 32 字节会拒绝启动 |
| `H3_JWT_TTL_S` / `H3_REFRESH_TTL_S` | `900` / `2592000` | 会话与刷新有效期 |
| `H3_MIN_FREE_VRAM_GB` | `12` | 空闲显存不足就不提交出图/出片任务 |
| `H3_GPU_ARBITER` | `true` | 单卡仲裁：本地实例开跑前让文本模型退场 |
| `H3_TRASH_RETENTION_DAYS` | `100` | 生成回收站到期真删；`H3_TRASH_PURGE_INTERVAL_S`（6 小时）、`H3_TRASH_PURGE_LIMIT`（500） |
| `H3_PG_DUMP_PATH` | 空 | 独立 PostgreSQL 的机器上要指绝对路径，否则备份命令给不出 |
| `H3_COMFY_POLL_INTERVAL_S` / `H3_WS_BACKOFF_MAX_S` | `2` / `30` | ComfyUI 轮询与重连退避 |
| `H3_LOG_LEVEL` | `INFO` | 日志走标准输出（`h3 setup` 起的后台进程落在 `~/.h3/logs/api.log`），所有落盘文本都对 token / apiKey 脱敏 |

`/healthz` 用来确认后端与库是否接上；`h3 config` 看 CLI 本地配置（`~/.h3/config.json`，脱敏）。

---

## 7. 智能体接入（MCP / CLI）

```powershell
h3 mcp install --client qoder      # 写客户端 MCP 配置，改前自动备份；只写一个落点
h3 mcp tools                       # 列工具
h3 mcp test                        # 真跑一次握手并调用一个只读工具
h3 token create --name guest --scope read   # 给同事/云端智能体单独一把，能随时吊销
h3 expose lan                      # 局域网 :8790 并打印可粘贴配置
h3 expose cloud                    # cloudflared 临时公网域名（要装 cloudflared）
h3 expose off                      # 收回
```

本机智能体走 stdio 就够了，配置里那条命令行自己读 `~/.h3/config.json`，token 不会落进客户端配置文件。
工具面共 73 个，覆盖 status / workflow_select / job_plan / job_submit / job_wait / media_* / script_* /
export_* 等；一轮典型流程是 `status → workflow_select → job_plan → job_submit → job_get → media_location`。
细节见 [`docs/AGENT-ACCESS.zh.md`](docs/AGENT-ACCESS.zh.md)。

---

## 8. 日常运维

| 要做什么 | 命令 / 界面 |
|---|---|
| 看现场（实例通不通、队列积压、显存与让位、磁盘） | `h3 status` |
| 体检（红字会直接告诉你下一步跑什么） | `h3 doctor`（加 `--strict` 有问题就非零退出） |
| 存储报表与 GC | 设置 → 系统，或 `h3 system storage` / `h3 system gc` |
| 手动触发让位/恢复文本模型 | 设置 → 系统，或 `h3 system gpu-yield` / `h3 system gpu-restore` |
| 备份 | 设置 → 系统给出的 `pg_dump` + 媒体目录命令 |
| 审计流水 | 设置 → 系统，或 `h3 system audit` |
| 直接打任意端点 | `h3 routes` 列端点，`h3 api GET /api/jobs` |

---

## 9. 开发与验证

```powershell
pnpm --filter h3-studio-web typecheck    # tsc -b --noEmit
pnpm --filter h3-studio-web build        # tsc -b && vite build

cd apps/api
.venv/Scripts/pytest.exe                 # 离线单测（asyncio auto，aiosqlite）
.venv/Scripts/pytest.exe -m "not live"    # 跳过要真实例的

# 离线验收（不起服务，只吃库或只跑纯函数）
.venv/Scripts/python.exe scripts/check_workflow_bindings.py
.venv/Scripts/python.exe scripts/check_workflow_autoselect.py
.venv/Scripts/python.exe scripts/check_director_pipeline.py
.venv/Scripts/python.exe scripts/check_job_plan.py
.venv/Scripts/python.exe scripts/check_skills.py

# 真机端到端（要在线实例与真权重；标记 -m live）
.venv/Scripts/python.exe scripts/e2e_queue.py
.venv/Scripts/python.exe scripts/e2e_generate.py
.venv/Scripts/python.exe scripts/e2e_director.py
.venv/Scripts/python.exe scripts/e2e_chain_video.py
.venv/Scripts/python.exe scripts/e2e_voice_clone.py
.venv/Scripts/python.exe scripts/run_job.py --kind image --slots '{"prompt":"…"}'   # 槽位按信号名写
```

改代码时的几条硬约束（细节与依据见 `docs/FULL-AUDIT-2026-10-02.zh.md`）：工作流图的权重名不做就近匹配、
自动选会剪输入位必须压实、重试不能复用旧图、改默认值要同步 `graph_original`、产物只认 `type=output`、
停 ComfyUI 前先看队列。

---

## 10. 故障排查

| 症状 | 先查 |
|---|---|
| 前端全是假数据 / `/api` 404 | 后端起了吗；`VITE_USE_MOCK=false` 是否设进了**进程环境**（见 5.4） |
| `http://127.0.0.1:8788/` 给的是 JSON 404 而不是界面 | 后端启动时 `apps/web/dist` 还不存在——先构建前端再重启（挂载是在启动那一刻定的） |
| 登录 401 / 演示账号不见了 | 库里已有账号后 seed 就不再建演示账号；`admin/12345` 只在环回有效，自己改过口令就按新的登，忘了就 `h3 bootstrap` 之外的路走真实账号 |
| 任务一直 queued 不派发 | `h3 status` 看显存闸门（`H3_MIN_FREE_VRAM_GB`）与实例熔断；确认没被文本模型占满 |
| 导入工作流报 unknown node | ComfyUI 版本低于实测基线 0.37.4，或缺自定义节点包 |
| 导入报缺权重 / 跑一半报找不到文件 | 权重文件名与 `work/manifest.json` 里 `requiredWeights` 逐字符对；大小写、下划线、`.1` 都算 |
| 声音克隆跑不起来 | `ComfyUI-Whisper` 与 `ComfyUI-Qwen-TTS` 两个包及其 Python 依赖是否装进了 ComfyUI 的环境；`stt/whisper/large-v3.pt`、`qwen-tts/*` 是否齐 |
| 长片续拍不可用 | 缺 `ComfyUI-minimaxH3-SequenceForge`（只影响这一条，逐镜头出片不受影响） |
| 导出失败 | `ffmpeg` 在不在 PATH，或在「设置 → 系统」指绝对路径 |
| 备份按钮没命令 | `H3_PG_DUMP_PATH` 未指（内嵌 PG 自带一份 `pg_dump`，独立安装要自己给路径） |
| 智能体看不到工具 | `h3 mcp test` 真跑一次握手；客户端要重载（Qoder `/mcp reload`、Claude Desktop 完全退出再开） |
| 日志里出现明文密钥 | 不会：`logging_setup` 对 token / apiKey 脱敏；如发现请当事故处理 |

---

## 11. 文档索引

| 文档 | 内容 |
|---|---|
| `PLAN.md` | 设计与接口契约全文（§10 是 REST 契约） |
| `work/README.zh.md` | 工作流包：7 张图、权重与节点要求、导入与校验 |
| `docs/AGENT-ACCESS.zh.md` | MCP / CLI 接入与暴露策略 |
| `docs/LOCAL-LLM-TECH-REPORT.zh.md` | 本地文本模型的选型与实测 |
| `docs/FULL-AUDIT-2026-10-02.zh.md` | 全量体检记录（含代码级不变量） |

## 12. 许可

Apache License 2.0，见 `LICENSE`。模型权重、ComfyUI 与各自定义节点包受其各自的许可证约束，
分发本仓库时不包含它们。
