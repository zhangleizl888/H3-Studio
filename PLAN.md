# H3 Studio — 本地优先 AI 短剧/漫剧生产平台 · 详细方案

> 版本 v1.2 · 2026-09-29（v1.0 自动调研生成 → v1.1 逐条源码核查修正 → v1.2 加入 RunningHub、llama.cpp，并把 AI 模型设置拆为本地/云端）
> 参考基座：`bo961386926/manga-studio`（产品形态）+ `ComfyUI` / `MiniMax-H3`（生成内核）+ `TheTerrasque/minimax-h3-frontend`（工程形态）+ `RunningHub`（云端生成通道）
> **待你确认的选型（不是已确认，是建议默认值，可推翻）**：React 19 + Vite + TS / FastAPI / PostgreSQL / cloudflared 命名隧道连云端 ComfyUI / 局域网团队多用户
> 其中影响最大、最需要你先表态的三件事：① **要不要多用户**（决定是否有 auth/RBAC/配额这一整套，单机自用可以砍掉约 30% 后端工作量）；② **PostgreSQL 还是 SQLite**（单机自用 SQLite 更省事，但 `SKIP LOCKED` 队列要换实现）；③ **是否现在就上 Docker**（Windows 原生跑也行）
> 另有一件与 RunningHub 直接相关、决定 M1b 能不能开工：**你有 RunningHub 账号 / apiKey 吗？愿意按秒计费吗？** 如果没有，云端那一路只剩自建（AutoDL + cloudflared），M1b 要改写成"仅自建"。注意 RunningHub 免费账号被 `801` 直接拒，工作流 API 至少要**消费级-会员**；模型 API 与 LLM API 还要**企业级-共享** key。



---

## 0. 一句话目标

一个跑在你自己机器上的 Web 工作台：**剧本 → 角色/场景资产 → 分镜关键帧 → MiniMax H3 视频 → 成片导出**，
文本环节（剧本解析、分镜拆解、提示词优化、H3 提示词改写）走**本地文本模型（Ollama / llama.cpp / LM Studio / vLLM）**或**云端 LLM**，
图像与视频走**本地 ComfyUI**、**自建云端 ComfyUI**（cloudflared 命名隧道，固定域名）、或 **RunningHub**（官方 `/proxy/{key}` 原生 ComfyUI 网关 + 专有 Task API），可导入任意 ComfyUI 工作流并参数化驱动。


---

## 0.1 本机实测环境（决定很多默认值）

| 项 | 实测 | 影响 |
|---|---|---|
| GPU | **RTX 4090，24,564 MiB（23.99 GiB）** | H3 走 int8_convrot / nvfp4 档；显存正好卡在 Ollama `<24 GiB` 档 → 默认 4k 上下文，必须显式传 `num_ctx` |
| Ollama | **已装 v0.32.13，但当前未运行**（`localhost:11434` 连不上） | 建议升到 v0.34.x；启动脚本要包含拉起 Ollama |
| ComfyUI | **本机未安装 / 8188 无响应** | M1 的第一步是把 ComfyUI 跑起来（≥0.30 才有 H3 节点；**当前最新 v0.37.4**，建议 0.37.x 以拿到 `MiniMaxH3AddGuide`(0.34) 和 `FunControlNetApply`(0.36)） |
| Node / Python | v22.23.2 / 3.13.3 | Vite 6 + FastAPI 都可用；注意 ComfyUI 在 Py3.13 的依赖需新版本 |
| PostgreSQL | **未安装**（`psql` / `pg_isready` 都不在 PATH） | 要么装 PG16，要么改用 SQLite → 见开头待确认事项 ② |
| ffmpeg | **已装 9.0.1**（winget Gyan.FFmpeg） | 拼接/抽帧/缩略图可直接用 |
| cloudflared | **未安装** | 云端 ComfyUI 那条链路的前置 |
| RunningHub | **未确认是否已有账号/apiKey** | `/proxy/{key}` 是白送的云端 ComfyUI，**注册一次就能让 M1b 提前跑通**，建议尽早开；⚠️ 免费账号被 `801` 直接拒，工作流/应用 API 需**消费级-会员**，模型 API 与 LLM API 需**企业级-共享** |
| llama.cpp `llama-server` | **未检测**（8080 端口常被其他程序占用） | 用户明确要求支持本地 llama.cpp → 判别必须走 `/health`+`/props` 形状而非端口；GGUF 目录与显存预算需要单独规划（见 §5.2） |

| F: 盘可用空间 | **494 GB 可用（总 4.8T，已用 90%）** | 放得下 H3 双 UNET 的 120 GB，但只剩 1/10 余量；`data/media/` 长视频会很快吃掉空间 → 媒体 GC 策略（§12）从"低优先"升级为"必须早做" |


## 0.2 事实核查状态

本方案的 v1.0 由自动调研生成，v1.1 对**所有承重技术断言**做了逐条源码核对（ComfyUI `server.py`/`execution.py`/`nodes.py`/`comfy_extras/nodes_minimax_h3.py`/`cli_args.py`、ModelScope + HF 文件清单实际字节数、SequenceForge/AIMixer 源码、docs.ollama.com）。

**通信层 17 条断言里 13 条完全正确**，含 `server.py:1241` 的 `/api` 前缀复制、`execution.py:736` 的 `client_id→sid`、`/object_info` 未知类返回 `200 {}`、`SaveVideo` 输出键是 `images`、`/view` 的 `preview=webp;90`、`--listen` 默认 `127.0.0.1` —— 连行号都对得上。这些是本方案的骨架，可以放心。

**以下是 v1.1 已修正的 9 处**，其中 #1、#2、#9 会让功能直接跑不通：

| # | 原文 | 事实 |
|---|---|---|
| 1 | `class_type: "ModelSamplingMiniMaxH3"`，输入 `shift` | 真实 `class_type` 是 **`MiniMaxH3SigmaShift`**（前者只是 display_name），输入名 `shift_video` / `shift_audio` |
| 2 | 骨架自称"全官方节点"，含 `ResolutionSelector` / `PrimitiveFloat` / `ComfyMathExpression` | 这三个是**社区节点**。已改为宽高与 length 由后端算成字面量，真·core-only |
| 3 | `minimax_h3_latent_upscaler_3d_fp16.safetensors` | 该名不存在；真实是 `..._3d_conv_v1_fp16...`，且只在 `LBH-123-AI` 仓库 |
| 4 | "`/queue`、`/history` 已 deprecated" | 并没有（唯一的 deprecation 中间件只针对前端 `/scripts/ui`）；`/api/jobs*` 是增量能力，含 `POST /api/jobs/{id}/cancel` |
| 5 | "`execution_success` 只有 master 有" | 自 v0.3.10 就在，但**条件发送**（需 client_id 且正常完成） |
| 6 | 调研阶段假设存在 `POST /upload/audio` | **不存在**；音频/视频一律走 `/upload/image`（服务端不校验扩展名）。已在速查表加醒目提示，避免实现时顺手写错 |
| 7 | "模型总计约 40GB" | 单底模集 42.5 GB；要角色一致性就得双 UNET → 84.9 GB，磁盘留 120 GB |
| 8 | steps=25 / 864×480 被说成"不可协商的官方基线" | 是 SequenceForge/AIMixer 默认；官方模板出厂 4 步 Turbo、预览档 0.4MP，全质量档 0.98MP→1344×768（`MAX_PIXELS=768*1344`） |
| 9 | "客户端统一打 `/api/...`，本地新版才认，云端老版也能通" —— **兼容性方向写反了** | 无前缀路径新旧都有，`/api/*` 只在新版有。已改为**默认打无前缀**，只有 `/api/jobs*` 三个是原生 /api 路由且需新版 |

另外两处**元问题**，比技术错误更值得你警惕：

- v1.0 开头写着"**已确认选型**"，但实际上**没有任何选型跟你确认过** —— FastAPI / PostgreSQL / 多用户 / cloudflared 命名隧道全是自动生成的建议默认值。已在开头改为"待确认"，并列出影响最大的三个决策点。
- v1.0 把 `docs/LOCAL-LLM-TECH-REPORT.md` 从仓库根目录移进了 `docs/`，这也是自动过程做的改动，不是你要求的。

仍未核实/存疑的点已就地标注（剪映草稿目录绝对路径、`/api/jobs` 的字段完整性）。**所有标 ⚠️ 的都要在 M-1/M1 用真实实例验证一次。**

### v1.2 新增内容（RunningHub / llama.cpp）的核查深度说明

- **RunningHub**：字段与端点来自官方文档站逐页抓取（该站是 Apifox 渲染，每个页面可用 `…/doc-XXXXX.md`、`…/api-XXXXX.md` 取到原始 markdown），并交叉验证了官方 GitHub CLI `HM-RunningHub/RH_CLI` 与 `ComfyUI_RH_APICall` 的**源码**（`http.py`/`poll.py`/`media.py`/`app/client.py`/`model/client.py`）。
  **唯一一次真实网络验证**是 `GET /proxy/INVALIDKEY123/object_info` → 200 + 真实约 30MB 的 `object_info`。
  ⚠️ 因此：`/proxy/{key}` 上 `/prompt`、`/view`、`/upload/image`、`/ws` 的行为是「官方文档原话 + EasyAI/Krita 第三方集成证实」，**我没有亲手验**。M-1 的第一件事就是拿真 key 打这四条。
  文档里标"开发中"的 `api-key/list`、`queue/status` 要按可能变动处理；`POST /openapi/v2/aiapp/list` 只在官方 CLI 源码里出现、公开文档没有 → 当作不稳定契约。
- **llama.cpp**：端口、flag、`/health` `/props` `/slots`、router 模式、`response_format.json_schema`、Docker 镜像与 Windows 预编译名，全部来自 `docs/LOCAL-LLM-TECH-REPORT.md` 中标 ✅ VERIFIED 的部分（一手来源 `github.com/ggml-org/llama.cpp` README）。
- **Ollama** 的 VRAM 分档、NDJSON、`/api/embed`、`format` 传 JSON Schema：docs.ollama.com 一手确认。旧 `/api/embeddings` "数组静默返回空"有 ollama/ollama#7242、#13020 两个 issue 佐证（v1.1 曾标为未证实，现升级）。
- 顺带修正 v1.0 报告里的一处过时：**"当前 Ollama 版本 v0.33.3"** 已过时，最新是 **v0.34.4**；本机装的是 **0.32.13**，落后两个小版本。



---

## 1. 参考项目调研结论（决定了本方案的每个设计）


### 1.1 manga-studio —— 抄产品形态，**不抄代码**

| 学到 | 结论 |
|---|---|
| 五段式流水线 `script / assets / director / export / prompts` 互斥视图，单一根 `ProjectState`，1 秒防抖自动保存 | ✅ 采纳产品骨架，但**改掉存储方式** |
| 全部 AI 调用走浏览器 → Express `/api/ai-forward` 开放转发代理 | ❌ **这是 SSRF 漏洞**（`server/index.js:198` 接受任意 `targetUrl`）。本方案：浏览器**永不直连** ComfyUI/LLM，一律走后端受控适配器 |
| 图片/视频以 base64 data-URL 存进 `ProjectState` JSONB，导致每次自动保存写几 MB 的单行，逼出 `express.json({limit:'200mb'})` | ❌ **坚决不抄**。本方案：媒体落盘，DB 只存路径与元数据 |
| 三层并存的服务代码（`geminiService.ts` 3278 行 / `modelService.ts` / `adapters/*`）+ 两套互相矛盾的 `VISUAL_STYLE_PROMPTS` | ❌ 本方案单一 adapter 层，风格表唯一来源 |
| `POST /history` 的 Sora 任务用 `setTimeout(5000)` 在浏览器里轮询，关标签页任务就丢；"后台任务"只是切页面不卸载组件 | ❌ 本方案：服务端队列，浏览器可随时关闭/刷新/换人 |
| 视频进度条是 `setInterval` 假进度 | ❌ 本方案：透传 ComfyUI WS 的真实 `progress` 事件 |
| 14 个硬编码风格预设 + `public/styles/*.png` | ✅ 采纳（换成可扩展的 DB 表） |
| `docker-compose.yaml` 引用 `service_healthy` 但 postgres 没写 `healthcheck` | ⚠️ 我们的 compose 要写对 |
| `server/shot1_db.mp4` 7.7MB 测试文件进了仓库；`view_configs.js` 打印 key 前 10 位 | ⚠️ .gitignore + key 全量脱敏 |
| 1024px 以下直接墙掉移动端 | ✅ 保留（ComfyUI 本地场景本来就是桌面） |

**关键事实：manga-studio 里没有任何 ComfyUI / Ollama / WebSocket / 工作流 JSON 代码**（全仓 grep `comfy|8188|ollama|11434|WebSocket` 零命中）。它只调云 API。所以本方案是**形态复用、内核重写**。

### 1.2 ComfyUI HTTP/WS 契约 —— 本方案的通信层地基

（来源：`ComfyUI/server.py`、`execution.py`、`comfy/cli_args.py`、`comfy_execution/protocol.py`）

| 事实 | 对设计的影响 |
|---|---|
| `server.py:1241` **遍历已有路由、每条再复制一份加 `/api` 前缀** | 方向要搞清楚：**无前缀路径（`/prompt`、`/view`…）在新旧版本都存在**，`/api/*` 只在较新版本有。所以客户端**默认打无前缀**才能同时兼容本地新版与云端老版；`/api/jobs` 这类原生 `/api` 路由会被再复制成 `/api/api/jobs`（无害但别踩）。WS 永远是无前缀的 `/ws?clientId=` |
| OSS 服务端**没有任何鉴权** | 前端绝不能直连；`--enable-cors-header` 等于拆掉 loopback CSRF 防护，必须由后端代理 |
| 默认中间件是 `create_origin_only_middleware`（CSRF 防护），**只有**传了 `--enable-cors-header` 才换成宽松 CORS | 我们的后端走服务端 HTTP，不吃这套；不需要开 CORS |
| `POST /prompt` 的 `client_id` 会写进 `extra_data`，成为 WS 广播的 `sid`（`execution.py:736`） | **不传 `client_id` = 收不到任何执行事件**。这是最容易踩的坑 |
| 终止判据用 `{"type":"executing","data":{"node":null}}`（由 `main.py` 发出） | `execution_success` **自 v0.3.10 就存在**，但只在"带了 client_id 且正常跑完"时才发；出错/中断路径没有。所以判完成用前者，后者只当加速提示 |
| 断线重连：同 `clientId` 会踢掉旧 socket；连上即补发当前 `executing` 节点 | 刷新页面能无缝恢复进度 |
| 二进制帧：`>I event` + payload。1=预览图, 3=TEXT, 4=带元数据预览图 | 预览图解码要单独实现 |
| `GET /object_info/<未知节点>` 返回 **200 + `{}`**，不是 404 | 校验工作流必须**判空**，不能判状态码 |
| `SaveVideo` 的 `/history` 输出键是 **`"images"`**（不是 `video`），且多一个 `"animated":[true]` | 取结果时按 `images` 取，别写 `video` |
| `GET /view` 支持 `preview=webp;90` 服务端降采样 | 缩略图/预览图别在前端解码原图 |
| `POST /queue`、`/history` **并没有**被标 deprecated（`server.py` 里唯一的 deprecation 中间件只针对前端 `/scripts/ui` 与 `/extensions/core/`）；但更新更厚的 **`/api/jobs`** 确实存在：`GET /api/jobs`（列表+状态+分页+排序）、`GET /api/jobs/{id}`、`POST /api/jobs/{id}/cancel`、`POST /api/jobs/cancel` | 取消优先 `POST /interrupt {"prompt_id":...}`（无 body 时全局中断），并轮询兜底；历史列表可用 `/api/jobs` |
| `prompt_id` 可以客户端预生成（规范小写 UUID），回显 | 我们的 job id 可以直接当 prompt_id 用，省一次映射 |

### 1.3 MiniMax H3 生态 —— 节点与参数基线

**模型本质**：音视频联合生成，一次扩散同时出画面 + 立体声。双 UNET：`fl2va`（首尾帧+音频）/ `ref2va`（参考驱动+音频）。ComfyUI ≥ **0.30.0** 原生支持（PR #15224/#15228）。

**参数基线**（注意出处，别一律当"官方钦定"）：

```
sampler = res_multistep   scheduler = simple   cfg = 1.0
sigma shift: shift_video = 12.0   shift_audio = 3.0     ← 节点 class_type 是 MiniMaxH3SigmaShift
steps = 25        ← SequenceForge / AIMixer 的默认；官方模板出厂是 4（Turbo），
                     官方文档正文写"默认 20 步，追求质量可调到 25"
画布 = 0.4MP 16:9 → 864×480   ← 这是官方模板的"预览档"；
     全质量档是 0.98MP → 1344×768，代码里硬上限 MAX_PIXELS = 768*1344
fps = 24    5s = 124 帧（官方 tooltip：训练区间约 124–362 帧）
```

> ⚠️ **`ModelSamplingMiniMaxH3` 不是 class_type**，它只是 `MiniMaxH3SigmaShift` 这个节点的 `display_name`。写进 API 图会直接 400 `missing_node_type`。输入名是 `shift_video` / `shift_audio`（不是 `shift`）。

**核心 H3 节点清单**（`comfy_extras/nodes_minimax_h3.py`，≥0.30.0 起存在）：

| class_type | 说明 | 引入版本 |
|---|---|---|
| `MiniMaxH3ImageToVideo` | t2v / i2v / fl2v 全靠它：required `clip, vae, prompt, width, height, length`；optional `first_frame, last_frame`；输出 `CONDITIONING` + `LATENT`。**没有** `MiniMaxH3TextToVideo` 这个节点 | 0.30 |
| `MiniMaxH3SigmaShift` | 就是上面那个 sigma shift | 0.30 |
| `EmptyMiniMaxH3LatentAV` | 空 AV latent（width/height/length） | 0.30 |
| `MiniMaxH3ReferenceToVideo` | r2v：required 多一个 `audio_vae`，`ref_image` / `ref_video` 是 autogrow 动态 combo | 0.30 |
| `MiniMaxH3AddGuide` | 引导帧/续拍锚点 | **0.34** |
| `MiniMaxH3FunControlNetApply` | ControlNet | **0.36** |

`CLIPLoader` 的 `type` combo 里确实有 `minimax`（`nodes.py:1020`，0.30 起）。

**帧数公式（必须硬编码进我们的校验层）** —— 合法帧数是 `17k+5`（5,22,39,56,73,90,107,124…）。这是**模型侧约束**，官方节点的 `align_frame_count` 会自动吸附，但吸附方向不一致，我们必须自己算：

```python
# 官方核心节点 + AIMixer：向上取整（while n % 17 != 5: n += 1）
n = max(5, round(sec * 24))
length = n + (5 - (n % 17)) % 17
# SequenceForge 的 _snap_seconds 是「就近取整」：k = round((f-5)/17)
```

> 结论：我们的 UI 自己算长度并直接写进 `length`，**不要依赖节点静默吸附** —— 否则同一段 6 秒分镜在两条链路上会得到不同结果，用户看到的时长和实际渲染时长对不上。
>
> ⚠️ **上面那行公式在 Python 里对，在 JavaScript / TypeScript 里是错的。**
> JS 的 `%` 对负操作数返回负值：`(5 - 144 % 17) % 17` = `(5-8)%17` = `-3`，于是 6 秒算出 **141 帧（向下）**，而官方节点给 **158 帧（向上）**。
> 实现阶段真的踩过，前端因此把每个镜头都悄悄剪短。TS 侧要写成：
> ```ts
> const n = Math.max(5, Math.round(sec * 24));
> const length = n <= 5 ? 5 : 17 * Math.ceil((n - 5) / 17) + 5;
> ```

**Turbo LoRA 必须严格配对步数**，否则音频崩：
- `minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16` → 8 步，**配 fl2va 底模**
- `minimax_h3_fl2v_turbo_4step_v1.0_768p_comfyui_bf16` → 4 步，**配 fl2va 底模**，768p
- `minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16` → 4 步，**必须配 ref2va 底模**（配 fl2va 必错）

**模型清单（上游 ModelScope `Comfy-Org/minimax-H3` 的名义文件名）**：

| 角色 | 上游文件名 | 名义目录 | 大小 |
|---|---|---|---|
| UNET t2v/i2v/fl2v | `minimax_h3_fl2va_pruned_int8_convrot.safetensors` | `models/diffusion_models/` | 20.97 GB |
| UNET r2v/v2v/rv2v | `minimax_h3_ref2va_pruned_int8_convrot.safetensors` | `models/diffusion_models/` | 20.97 GB |
| 文本编码器 (Qwen3-VL 32B) | `qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors` | `models/text_encoders/` | 15.69 GB |
| 视频 VAE | `minimax_h3_video_vae_fp16.safetensors` | `models/vae/` | 5.21 GB |
| 音频 VAE | `minimax_h3_audio_vae_fp32.safetensors` | `models/vae/` | 0.61 GB |

> ### ⚠️ 实现阶段实测：上面这张表**不能当成查找路径来硬编码**（v1.2 修正）
>
> 本机 `F:\H3\comfyui\ComfyUI\models` 的真实情况与上表有**三处不一致**，且每一处都足以让任务失败：
>
> | | 上表（上游名义） | 本机实测 |
> |---|---|---|
> | UNET 目录 | `diffusion_models/` | **`unet/`** |
> | UNET 文件名 | `minimax_h3_fl2va_...` | **`MiniMax_H3_FL2VA_pruned_int8_convrot.safetensors`**（大驼峰） |
> | 文本编码器目录 | `text_encoders/` | **`clip/`**（且本机是 `bf16` 51.5 GB 与 `int8_convrot` 27.14 GB 两个档，没有 nvfp4） |
>
> 原因是 ComfyUI 对 `unet`↔`diffusion_model`、`clip`↔`text_encoders` **两组目录都扫并合并进同一个候选清单**，
> 所以 `/object_info` 的 `UNETLoader.unet_name` 会同时列出放在 `unet/` 里的大驼峰文件。
> 之前依据本表去 `diffusion_models/` 找文件，得出了「H3 权重没下载」的**错误结论** —— 实际早已就位（本机 models 目录实测 206 GB）。
>
> **因此本项目的规则是：模型文件名一律由 `/object_info` 的候选清单解析，绝不允许硬编码或按磁盘目录名推断。**
> 已实现为 `app/gen/builtin_graphs.py::discover_h3_weights()` + `ComfyNativeClient.resolve_model_name()`，
> 以及 `missing_models()`：它比对的是实例真实给的候选，报错时还会给出「最接近的可用文件名」。

| 潜空间放大器（二采/Refine） | `minimax_h3_latent_upscaler_3d_conv_v1_fp16.safetensors` | `models/latent_upscale_models/` | 690,592,672 B ≈ 691 MB（HF `LBH-123-AI/Minimax_h3_latent_Upscaler`，仓库里还有一层 `minimax_h3_latent_upscaler_3d_conv_v1/` 子目录；另有 `_bf16` 与 `_fp32.pth` 1.38GB） |

> ⚠️ 上一版方案写的 `minimax_h3_latent_upscaler_3d_fp16.safetensors` **不存在**，Comfy-Org 仓库里连 `latent_upscale_models` 目录都没有 —— 只能从 LBH-123-AI 拿，文件名必须带 `_conv_v1`。

**文本编码器有三个精度档，别混**（实测字节数）：`bf16` 51,506,295,256 B（51.5GB）／`int8_convrot` 27,141,342,152 B（27.14GB）／`nvfp4_awq` 15,687,142,551 B（15.69GB，官方模板注释写"14.61 GB"是 GiB）。视频 VAE 也有 `int8` 档：`minimax_h3_video_vae_int8_convrot` 2,811,065,184 B。

**Turbo LoRA 实测各 1,956,193,000 B（≈1.82 GiB）**，位于 `models/loras/`。官方出厂模板的搭配是：t2v/i2v/fl2v 工作流 = `fl2va_pruned_int8_convrot` + `fl2v_turbo_8step`；r2v 工作流 = `ref2va_pruned_int8_convrot` + `ref2v_turbo_4step`。

**真实体积（务必按十进制/二进制分开说，之前"约 40GB"含糊）**：
- 单底模最小可用集（fl2va int8 + nvfp4 TE + 两个 VAE）= **42.48 GB 十进制 ≈ 39.6 GiB**；再加一个 Turbo LoRA → 44.4 GB；再加 upscaler → 45.1 GB
- **要同时跑 r2v（角色一致性）就必须两个 UNET 都下 → 84.9 GB**，磁盘请留 **120 GB**


> ⚠️ `CLIPLoader` 的 `type` **必须**是 `minimax`。
> ⚠️ 目录名**两种都行**：ComfyUI 把 `models/unet/` 与 `models/diffusion_models/`（以及 `clip/` 与 `text_encoders/`）合并成同一份候选清单。
> 本机 H3 权重实际放在 `unet/` 和 `clip/`。所以不要断言"必须是 diffusion_models"，也不要按目录名去找文件——**以 `/object_info` 的清单为唯一事实**（见上方 v1.2 修正框）。
> ⚠️ 腾讯云文章的模型表整体有误：音频 VAE 写的 1.2GB（实为 605MB）、文本编码器写 18GB（实为 27.1GB）、`minimax_h3_ref2va_lightx2v_turbo_4step_v0.1.safetensors` 这个文件在 Comfy-Org 里**不存在**（正确名是 `ref2v`）。**只当部署思路参考，不当下载清单。**

**官方 H3 提示词三段式**（`h3-prompt-writing` skill 规范）：
```
integrated_multimodal_description: <画面 + 动作 + 台词（<d>中文</d>）>
overall_soundscape:              <环境声 / 音效>
non_diegetic_music:              <非画内配乐>
```
参考标签：`<Picture N>` ≤9、`<Video K>` ≤3、`<Audio J>` ≤3（**上限由官方节点的 autogrow 强制**：`prefix="ref_image_" max=9`、`ref_video_ max=3`、`ref_audios max=3`；SequenceForge 里同值写死为 `REF_CAPS`）。SequenceForge 用正则 `_OFFICIAL_FIELD_RE` 检测这三个字段，命中则原样透传不重写。
台词写法：说话人在 `<d>` 外，`<d>` 内只放语言标签 + 实际台词 —— `<d>[中文] 我在下一站下车。</d>`。
`N/A` 语义要分清（别当万能"静音"）：`overall_soundscape: N/A` **仅用于用户明确要求全程无声**；`non_diegetic_music: N/A` 只表示"没有非画内配乐"，环境声照旧。**留空 ≠ N/A**，SequenceForge 文档专门警告过。
> 补充：Ref2VA 的官方规范其实是**六段**（多了 `subject_definitions` / `summary` / `retention_analysis` / `detailed_description`）。我们的 r2v 路径要按六段生成，三段式只适用于 t2v/i2v/fl2v。机器可读规范在 SequenceForge 仓库 `总提示词框格式规范skill/SKILL.md`（10,110 B）。

**r2v 的 Autogrow 点名是带点的**（极易踩坑）：
```
ref_images.ref_image_0 .. ref_images.ref_image_8        (IMAGE)
ref_videos.ref_video_0 .. ref_video_2                    (IMAGE 帧序列，不是文件)
ref_video_audios.ref_video_audio_0 .. _2                 (AUDIO)
ref_audios.ref_audio_0 .. _2                             (AUDIO)
ref_image_size                                            ("match"|"max")
```

### 1.4 AIMixer / SequenceForge / TheTerrasque 的可借鉴点

| 来源 | 借鉴 | 不借鉴 |
|---|---|---|
| **AIMixer Director** | `timeline_data` v4 schema（分镜时间轴 + segment 数组）、Director pack `.mmxpack.zip` 导入导出、`POST /minimax/director/detect_shots` 智能分镜（PySceneDetect）、`/minimax/director/export_pack` 一键打包 | 它前端 JS 630KB 塞在 ComfyUI 的 `web/js` 里，我们不复用 |
| **AIMixer** 的 8 路输出 | `images / audio / fps / frame_count / source_images / report / images_pre_refine / images_pre_face_refine` —— **官方 example workflow 只有 6 路，比代码落后一个版本**。我们以 `object_info` 实况为准，不信 example |
| **SequenceForge** | 段间无缝续拍：直接从**采样出的 AV latent** 切尾帧（不解码不重编码 → 零色漂），用 `minimax_keyframes` anchor `frame_index=0` 每步重注入，桥帧门控 5/22/39/56（推荐 22）；`H3SeamDoctor` 六类接缝诊断 | V3 Extension API 复杂度过高，我们走经典 `NODE_CLASS_MAPPINGS` |
| **TheTerrasque** | Django-q2 单 worker = 严格 FIFO（ComfyUI 本身一次只跑一个）；`Clip` 用 `UniqueConstraint(project, order) DEFERRABLE INITIALLY DEFERRED` 让 reorder 不撞；`/api/jobs/<id>/cancel`；`COMFYUI_EXTRAS` 开关语法 `slug` / `slug=0\|1\|2`；文件夹树 `generate / queue / director / admin` | Django 换 FastAPI；`Q_CLUSTER_WORKERS=1` 换 DB 队列 |
| **TheTerrasque** 的坑 | `Clip.continues_audio` 字段是在真实渲染出现"连续音频变成 gibberish"**之后**才加的 —— 说明音频连续性不可靠，改为把上一段音频当普通 `ref_audio` 喂进去 | — |
| **TFboy1 skill** | **按 `class_type` 定位、绝不硬编码 node id** 的路由规则表；`jobs/seg_XX_job.json` 断点续跑；`ensure_cloudflared.py` 探测链路三级降级（本地:8188 → 已有隧道 → AutoDL） | 已 6 周未更新；模板依赖 `PathchSageAttentionKJ`（注意拼写）、`ResolutionSelector`、`ComfyMathExpression` 等社区节点 + 非官方 LoRA |
| **TFboy1** 硬件表 | <12GB 不建议本地；12–16GB int8/nvfp4 + 4 步 Turbo + 0.4MP 短片；16–24GB 推荐；24GB+ 高配。模型总计约 40GB，磁盘留 60GB | — |
| **comfy-mcp** | **slot 寻址**（`6.text`、`115/75.strength`）做参数化注入；`validate_workflow` 把无效当正常返回而非异常；`unknown_nodes` 检测；`list_workflow_notes` 拒绝 API 格式 | 它是 **stdio + shell out 到 comfy-cli**，我们直连 HTTP |

### 1.5 cloudflared

```
# 快速隧道（临时，trycloudflare.com，ha-connections 被强制为 1）
cloudflared tunnel --url http://localhost:8188
# 命名隧道（生产，固定域名）—— 本方案走这条
cloudflared tunnel login
cloudflared tunnel create h3-studio
cloudflared tunnel route dns h3-studio comfy.example.com
cloudflared tunnel run --token-file /path/to/token h3-studio
```

`config.yml`（`FileManager` 会**热重载**）：
```yaml
tunnel: <UUID>
credentials-file: /path/cert.pem
ingress:
  - hostname: comfy.example.com
    service: http://127.0.0.1:8188
    originRequest:
      httpHostHeader: 127.0.0.1:8188   # 关键：默认会把 Host 变成域名，ComfyUI 认不出
      noTLSVerify: true
  - service: http_status:404             # 必须存在且必须在最后
```

> 关键点：cloudflared 是**透明 HTTP 代理**，`/ws?clientId=` 的 Upgrade 天然可用，无需额外配置。
> 但 ComfyUI 的 WS 绑定单个 `clientId`，**重连会踢掉旧连接** → 我们必须做连接复用 + 指数退避重连。

### 1.6 jianying-editor-skill

**它不是 MCP server**（全仓 grep `mcp|fastmcp|modelcontextprotocol` 零命中），是 Agent Skill：SKILL.md + `rules/` + `scripts/` + `vendor/pyJianYingDraft`。
输出是**剪映专业版草稿目录**，不是渲染好的视频：

```
<草稿名>/
  draft_info.json        # v5.9+ schema ← 主输出
  draft_content.json     # <5.9 回退，draft_info 不存在时读
  draft_meta_info.json   # 缺失 = 草稿损坏
```

`JyProject.save()` 流程：`script.save()` → `_patch_cloud_material_ids()` → `_force_activate_adjustments()` → `os.utime()`（破 OS 路径缓存）。
`overwrite=True` 时若草稿损坏会**自动删目录重建**；遇 `PermissionError`（剪映占着文件）重试 3 次 × 2s。
**字幕位置**必须走 `clip_settings=draft.ClipSettings(transform_y=-0.8)`，不是顶层 `transform_y`。

**本方案用法**：不引入整个 skill，只 vendor `vendor/pyJianYingDraft` + 我们自己写一个 `draft_builder.py` 子进程服务，把我们的 timeline 序列化成草稿。Windows 平台可顺带用它的 Windows UI Automation 自动导出；导出失败不影响草稿产出。

### 1.7 RunningHub —— 第三条生成通道（重要：它有两个完全不同的入口）

RunningHub 是国内一个托管 ComfyUI 的平台。调研后最关键的结论是：**它同时提供两种接入面，我们的架构两种都要支持，但优先用第一种。**

#### ① 原生 ComfyUI 代理（`/proxy/{apiKey}`）—— 几乎不用改代码

```
24 GB 显存档：https://www.runninghub.cn/proxy/{你的apiKey}
48 GB 显存档：https://www.runninghub.cn/proxy-plus/{你的apiKey}
```

官方文档原话：**"该地址功能等同于本地部署的 ComfyUI 地址：`http://127.0.0.1:8188`"**。密钥在**路径里**，不需要 Authorization 头。已被 EasyAI / SillyTavern / Krita AI Diffusion 以"自定义 ComfyUI 服务器"的方式接入。
实测（用非法 key 探 `GET /proxy/INVALIDKEY123/object_info`）：**返回 HTTP 200 + 约 30MB 的真实 ComfyUI `object_info`**，`KSampler` 的采样器列表还是 RunningHub 扩展过的（含 `res_multistep`、`euler_cfg_pp`、`ipndm_v`…），前面是 Tencent EdgeOne。→ 这是个活的 ComfyUI 网关，**我们现有的 `ComfyInstanceClient` 可以原样连**：`/prompt`、`/ws?clientId=`、`/history`、`/view`、`/upload/image`、`/object_info` 全都能用。
（⚠️ 除 `object_info` 外，其余路由我是"官方文档 + 第三方集成证实"，未亲手验；M-1 必须实测一遍。）

限制：无排队/计费/webhook 接口可用，Krita 侧反馈**并发不支持 → batch 必须 =1**；没有 84G 档对应的 `/proxy-ultra/`（NOT FOUND）。

#### ② 专有 Task API —— 生产批任务走这条

| 项 | 事实 |
|---|---|
| 提交 | `POST https://www.runninghub.cn/task/openapi/create` |
| 请求体 | `apiKey`、`workflowId`(string)、`nodeInfoList[]`、`workflow`(**字符串化的 API 格式图**，指定则忽略 workflowId)、`instanceType`、`addMetadata`、`webhookUrl`、`usePersonalQueue`、`retainSeconds`(10–180s，仅企业共享) |
| 两种都给图的方式 | ✅ **可以提交自己的图**：`workflow` 字段就是 ComfyUI API 格式 JSON 的字符串。但 schema 里 `workflowId` 仍标 required，官方示例永远两个都发 → **实现成"workflowId 为主、workflow 为兜底"** |
| **覆盖入参不是 patch 图** | `nodeInfoList: [{nodeId, fieldName, fieldValue}]`，`fieldName` 必须是 **api 格式里 `inputs` 的 key**，`fieldValue` 要保持原始线格式。`[["7",0]]` 这种是连线，别碰。**前端专属 widget（如 KSampler 的 `control_after_generate`）在 api 格式里不存在，设不了** |
| ⚠️ **seed 会被强制重置** | 文档明写"API 调用会强制重置 seed 值" → **要一致性就必须每次显式把 `seed` 放进 `nodeInfoList`**。这条对我们的角色一致性策略是致命的，别漏 |
| 轮询 | `POST /openapi/v2/query` `{taskId}` → **顶层未包裹**：`{taskId,status,errorCode,errorMessage,results:[{url,outputType,text}],clientId,promptTips,failedReason,usage}`。状态枚举就四个：`QUEUED｜RUNNING｜SUCCESS｜FAILED` |
| 进度 | `netWssUrl` 是签名的、会过期的真 ComfyUI WS 票据，但文档**两次明写"当前不稳定，不推荐使用"** → 只当 best-effort，权威来源是轮询。官方 CLI：`POLL_INTERVAL=5s`、`MAX_POLL=1200s` |
| 结果 | `results[].url` 是 **Tencent COS 签名链接**；上传返回的 `download_url` **只有效约 1 天** → **一律立刻下载落盘**，绝不在 DB 里存外链（§4 的 media 表设计正好规避了这点） |
| 错误 | `failedReason` 直接是**原始 ComfyUI 节点 traceback**（`exception_type/node_name/node_id/current_inputs/current_outputs`）→ 我们的"缺哪个节点/哪个控件不合法"文案有真实数据源 |
| 取消 | `POST /task/openapi/cancel` `{apiKey,taskId}`。任务级**没有** retry，只能重新提交 |
| 鉴权 | 单把不透明 apiKey，**无签名/HMAC**。但要**同时**传：body 里的 `apiKey` + header `Authorization: Bearer`（`/openapi/v2/*` 只认 Bearer），且 `Host` 必须精确。信封不对称：v2 上传返回 `message`，老接口返回 `msg` |
| key 类型 | 消费级-会员 / 企业级-共享 / 企业级-独占。**模型 API 与 LLM API 只允许企业级-共享**（否则 `1014`）；免费账号直接被 `801` 拒 |
| 队列/并发 | `GET /openapi/v2/queue/status` → `concurrentLimit`（示例 30）、`runningCount`、`queuedCount`；企业共享默认并发 **100**；个人队列 ≤1000 任务。超并发报 `421/1520`，独占机耗尽 `415`（提示等 30–120s）→ **这些要映射成退避重试，不是失败** |
| 计费 | 企业共享按秒计费（不调用不收费）；`/task/openapi/outputs` 返回 `consumeCoins/consumeMoney/taskCostTime` → **要落进我们的 job 表做成本审计**；有 `POST /openapi/v2/price-preview/<route>` 预估 |
| 显存档位 | `instanceType`：`default`=24G / `plus`=48G / `ultra`=84G（填错报 `435`）→ 直接对应用户"云端 ComfyUI"的选择器 |
| **导入工作流** | ✅ `POST /api/openapi/getJsonApiFormat` `{apiKey, workflowId}` → `data.prompt` = **API 格式图字符串**（含 `_meta.title`）。这就是"从 RunningHub 工作流库拉进我们应用"的通道 |
| ⚠️ 前置条件 | `810 WORKFLOW_NOT_SAVED_OR_NOT_RUNNING`：**workflowId 必须先在网页里保存并手动跑通过一次**才能被 API 调用 |
| 上传 | 现行 `POST /openapi/v2/media/upload/binary`（multipart 字段名 `file`，Bearer），返回 `{type, download_url, fileName, size}`；`fileName` 是服务器相对路径（`api/<sha256>.png`）直接喂给 `LoadImage`。**单文件 30MB 上限**。**不存在** `/upload/audio`，音视频同接口。LoRA 走 `getLoraUploadUrl` + PUT 预签名 COS，且**只有 `RHLoraLoader` 认它** |
| 应用 API | `POST /task/openapi/ai-app/run` `{webappId, nodeInfoList, ...}`。`webappId` 是 **int64**（workflowId 是 string，类型陷阱）。真正的价值是 `GET /api/webapp/apiCallDemo?apiKey=&webappId=` → 返回一份**机器可读的表单 schema**：每个节点带 `nodeName`、`fieldType`(STRING/INT/IMAGE/VIDEO/AUDIO/LIST)、`fieldData`(枚举或 widget spec 含 default)、中英描述 → 可直接自动生成为了 UI。缺点：明说"调用本接口生成的结果不带工作流信息"，**不可复现** → 我们只用它做"发现参数"，实际渲染走工作流 API |
| 站点隔离 | `www.runninghub.cn`（国内，手机验证码注册）与 `www.runninghub.ai`（全球，邮箱/Google）**路径与字段完全相同，但 key、余额、已上传素材不互通**。base_url 必须是实例配置项 |
| 模型可用性 | ✅ **MiniMax H3** 与 **Qwen Image** 都在其模型 API 列表里（`rhart-video/minimax-h3-rh-enhanced/i2va`、`qwen-image/text-to-image-2512`、`qwen-image/edit-2511` 等，另有 `hailuo-h3/context-ir-*` 提示词增强端点）。→ **H3 即使本机显存不够，也能用 RunningHub 跑**，这是本方案的一条重要退路 |
| NOT FOUND | 智能体/Agent API、MCP server、任何签名方案、`/proxy-ultra/`、`saveOutput`/`resultType`/`frontJson`/`nodeIdInputs` 这些字段**不存在**，别按别的教程写 |

**三个入口的取舍（本方案策略）**：

| 场景 | 走哪个 | 理由 |
|---|---|---|
| 本地 ComfyUI | 原生 HTTP/WS | 唯一全可控、免费、可直读产物目录 |
| 自建云端 ComfyUI（AutoDL 等） | 原生 HTTP/WS + cloudflared 隧道 | 同一套客户端代码 |
| RunningHub，交互式单张出图/调试 | **`/proxy/{key}` 原生模式** | 零改造，有真实 WS 进度，能直接复用我们的 UI↔API 转换与 slot 引擎 |
| RunningHub，批量渲染/要计费审计/要 webhook/要选显存档位 | **专有 Task API** | 排队、并发、成本、traceback、`retainSeconds` |
| 从 RunningHub 工作流库导入 | `getJsonApiFormat` | 拿到的是 API 格式，直接进我们的 §6 引擎 |

> 设计上这意味着：**抽象层要按"协议"分，不是按"服务商"分。** 一个实例声明自己是 `comfy_native` 还是 `rh_task`，前者走 `ComfyNativeClient`（本地/隧道/RunningHub proxy 三者共用），后者走 `RunningHubTaskClient`。`/proxy/{key}` 的存在使得 RunningHub 有 80% 的功劳是白送的。

### 1.8 本地文本模型：Ollama 只是其中一种，llama.cpp 要当一等公民

`docs/LOCAL-LLM-TECH-REPORT.md`（本报告的事实来源之一，已核实）给出的本地后端矩阵：

| 后端 | 默认地址 | OpenAI 兼容 base | 关键端点 | 鉴权 |
|---|---|---|---|---|
| **Ollama** | `127.0.0.1:11434` | `http://localhost:11434/v1` | `/api/chat` `/api/tags` `/api/generate` `/api/embed` `/v1/*` | 无 |
| **llama.cpp `llama-server`** | `127.0.0.1:8080` | `http://127.0.0.1:8080/v1` | `/health` `/models` `/props` `/slots` `/v1/chat/completions` `/completion` `/embeddings` `/v1/rerank` | 可选 `--api-key` |
| **LM Studio** | `127.0.0.1:1234` | `http://localhost:1234/v1` | `/chat/completions` `/embeddings` `/models` | 默认无，可选 token |
| **vLLM** | `127.0.0.1:8000` | `http://127.0.0.1:8000/v1` | 同上 + `/audio/transcriptions` | `--api-key`（建议） |

四者默认端口**不冲突**，可同机并跑。⚠️ **8080 是常见冲突端口**（一堆工具占它）→ 探测到 8080 上有服务不能断定就是 llama.cpp，必须用 `GET /health` + `GET /props` 判别。

**llama.cpp 的三个必须掌握的点**（决定了我们的支持方式）：

1. **Router 模式** = 一个进程服务多个 GGUF，按需加载，这是它唯一配得上 Ollama 的用法：
   ```bash
   llama-server --models-dir D:/models/gguf --no-models-autoload --jinja \
                --host 127.0.0.1 --port 8080 -ngl 999 -c 32768
   ```
   → 我们的"本地模型管理"页面对 llama.cpp 就退化成：**指定 models-dir，列 `/models`，切模型 = 换 `model` 字段**。不用像 Ollama 那样有 pull API（GGUF 从 HF 手动下或用 `--hf-repo/--hf-file`）。
2. **`--jinja` 不开就没法做 tool calling**；**结构化 JSON 靠 `response_format: {type:"json_schema", json_schema:{...}}`**（官方支持 schema 约束），所以 llama.cpp 的结构化输出能力**比 Ollama 的 `format` 更接近 OpenAI**，能共用一个 `openai_compat` 实现。
3. **上下文是启动参数 `-c/--ctx-size`，不是每请求参数**。这跟 Ollama 的 `options.num_ctx` 每请求可传**根本不同** → 我们的 `LLMClient` 接口必须能表达"这个后端的能力差异"：llama.cpp 上如果检测到 `-c 2048` 而剧本大纲需要 16k，**要显式报"请重启 llama-server 并加大 -c"**，而不是像 Ollama 那样静默截断。这是 §5.2 能力降级的核心一条。
4. 命令行参数**优先于**环境变量；`--api-prefix` 允许挂在子路径下（配合我们的反代）；Windows 有预编译 `llama-server.exe`；Docker 镜像 `ghcr.io/ggml-org/llama.cpp:server`。


---

### 1.9 oh-my-minimaxh3-director —— 导演方法论与 H3 提示词模式（2026-10-01 新增参考）

仓库 `TFboy1/oh-my-minimaxh3-director`（MIT，136 star），已整份落到 `reference/oh-my-minimaxh3-director/`
（codeload zipball，35 个文件 346 KB，不含 .git；来源与许可证见其 `LICENSE`）。
**它是 agent skill 形态**：没有前端、没有数据库，靠 `SKILL.md` 指挥 agent 按阶段跑 10 个 CLI 脚本，
状态落一堆 JSON 文件。所以我们能借的是**方法论 + 契约 + 校验规则**，不是它的进程模型。

它的链路：探测 ComfyUI → 剧本分镜（段=一个 H3 视频，段内 2–3 镜靠提示词驱动）→ 人物四视图参考图
→ 提示词三模式 → 工作流路由与参数表 → 参数确认 → 批量提交/监控/下载 → 剪映拼合。

**值得借（按性价比）**：

| 借什么 | 它的做法 | 落点 |
|---|---|---|
| 导演方法论 | `wenwu-director.md`：生命核/镜头脉冲/表演肌理三域、八条生命通道、叙事温度→镜头密度公式、FACS AU 强度 A–E、景别 7 档 + 机位 11 + 运镜 15 + 转场 14 + 技法 8 | `app/director.py`（已做） |
| 提示词三模式 + 决策留痕 | `official`/`wenwu`/`hybrid`，写提示词前必须先定 `meta.prompt_mode` + `prompt_mode_reason`，禁止静默走默认 | `config.h3PromptMode` + `h3PromptReason`（已做，`three_field` 为本机实测档） |
| 提交前参数总表 | `build_workflows` 出 `params.json` → 表格给用户确认，改完重跑构建 | 待做（§11 下一里程碑） |
| 后端硬校验 | 时长 5–15s、steps 4–40、seed 范围、aspect 枚举、参考图存在、提示词非空；再校验六段齐全/`[Shot` 时间码/`constraints:` 块 | `director.validate_h3_output`（已做）+ 分镜规模警告（已做） |
| 跨镜文字锚点 | 每次切换必须传递一个锚点（动作方向/视线/同一道光/同一道具/同一轮廓/同一段声音/同一股受力），并**禁止引用上一段尾帧** | 已进方法论与 `action` 字段描述；结构化落 Shot 留给「衔接」那一步 |
| class_type 广播覆盖 | 按 class_type 找**所有**节点逐个改，字段缺失即报错，不写死节点 ID | `gen/workflow.py` 的用户导入工作流路径（待做）。**注意它有个 bug**：`set_widget` 只看 key 存在就赋值，参数是连线来的（值为 `[id, slot]`）会被覆盖成字面量、直接断链，移植时必须补 `isinstance(v, list)` 守卫 |
| 复用用户已有工作流优先 | `scan_workflows.py` 扫多目录并标注 API/UI 格式、模式、LoadImage 槽位数、Turbo 标记、模型文件名，列表让用户选 | Workflows 页的分类与派发选路（待做） |
| 水位契约解耦 | 独立 watcher 写 `jobs/resource_state.json`（`level: ok/warn/stop`，默认 90/95%），消费方每轮自查 | 我们的 `queue._vram_ok` 已有闸门；缺持续超标的显式等级与「约定关机时间」（待做） |
| API 报错排查表 | `api-and-mcp.md`：`node_errors` 是唯一权威校验，连续 3 次无变化就停下报告 | 已体现在 `comfy_native` 的错误分类里，可补成一页 docs |

**它的 README 有夸大的地方，别照文档抄**（都是读完脚本才发现的）：

- 宣传「90% 警告 / 95% 停止防 OOM」，实际 `monitor_resources` 触发 stop **只是让监控脚本自己 sleep 跳过本轮**，对 ComfyUI 队列没有任何 `/interrupt`/暂停提交动作；
- 文档里的 `run_plan.json`（unattended/shutdown_at/agreed_by_user）**代码里没有读写**，关机靠 `--allow-shutdown` 手敲；
- 内存采集写死本机 `GlobalMemoryStatusEx`，文档说的「远程 `system.ram_*`」没有实现，非 Windows 直接崩；
- 参考图去重缓存是**进程内 dict 且每段各调一次**，跨段同图仍会重传；
- 拼合脚本对所有相邻片段插同一个「叠化 0.3s」，没有节奏驱动；
- `check_hardware.py` 收了 `ram_gb` 却没参与判定，也不看 CPU。

**我们已经比它强的，不要倒退**：PG 事务队列（`SKIP LOCKED` + 实例互斥 + 熔断 + 崩溃恢复 + SSE）远强于裸 JSON 断点；24GB 单卡的显存互斥让位（`gpu_arbiter` + `_restore_if_idle` 看门狗）它完全没有；权重文件名从 `/object_info` 反查这条我们比它彻底（它靠人对照模板改名）。

**第①步（导演方法论 + 四模式）已落地**，数字见 §11.1.2。**第②步（提交前参数总表）已落地**：`app/job_plan.py` 是唯一的判据，`POST /api/jobs/plan` 出表、`/jobs` 与 `/jobs/batch` 走同一套准入（预览和真派发不会说两套话），导演台的批量按钮先弹参数表再派发，数值就地改完自动重问；表上的耗时一律标注是**按 §11.1.1 锚点外推、非实测**。剩下的顺序是 ③跨镜衔接锚点真正被生成逻辑消费 → ④水位契约 / 剪映拼合 / 首次运行向导。

---

## 2. 总体架构

```
┌──────────────────────────────────────────────────────────────────────────┐
│  浏览器（React 19 + Vite + TS + Tailwind）                              │
│  仪表盘 / 剧本 / 资产 / 导演 / 队列 / 工作流 / 设置 / 导出                │
└───────────────────────────┬──────────────────────────────────────────────┘
                            │  ① HTTP: /api/*  ② WebSocket: /ws/task
                            ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  FastAPI (uvicorn, 单进程 + 多协程)                                     │
│  ┌────────────┬─────────────┬──────────────┬────────────────────────┐   │
│  │ auth       │ project API │ job API      │ media API (Range/流式) │   │
│  │ JWT+RBAC   │ CRUD        │ 提交/取消/重试│ 落盘/预览/代理          │   │
│  ├────────────┴─────────────┴──────────────┴────────────────────────┤   │
│  │  QueueDispatcher  (PostgreSQL FOR UPDATE SKIP LOCKED, 每实例串行)   │   │
│  ├───────────────┬──────────────────┬──────────────────────────────┤   │
│  │ GenManager      │ LLMManager       │ PipelineManager              │   │
│  │ 按**协议**分派  │ 本地组 / 云端组   │ image / video / chain / export│   │
│  │ ComfyNativeClient ← 三种部署共用                                   │   │
│  │ RunningHubTaskClient ← 专有 API（排队/计费/webhook/显存档位）       │   │
│  │ OllamaClient + OpenAICompatClient（llama.cpp/LMStudio/vLLM/云）    │   │
│  └───────────────┴──────────────────┴──────────────────────────────┘   │
└──────┬─────────────────────┬─────────────────────┬──────────────────────┘
       │                     │                     │
  ┌────▼────────────────────┐│              ┌──────▼──────────────────────┐
  │ 协议 A：原生 ComfyUI     ││              │ PostgreSQL                   │
  │ ─────────────────────── ││              │ 媒体目录 data/media/ (落盘)  │
  │ ① 本地      :8188       ││              │ ffmpeg 临时区 data/tmp/      │
  │ ② 自建云端 +cloudflared ││              └─────────────────────────────┘
  │    https://comfy.example││
  │ ③ RunningHub            ││
  │    /proxy/{key}   (24G) ││   ← 官方："功能等同于 http://127.0.0.1:8188"
  │    /proxy-plus/{key}(48G)││
  └────▲────────────────────┘│
       │  同一套客户端代码，零分支
  ┌────┴────────────────────┐│
  │ 协议 B：RH Task API      ││
  │ POST /task/openapi/create││  轮询 /openapi/v2/query（WS 官方称不稳定）
  │ instanceType default/plus/ultra = 24G/48G/84G
  └─────────────────────────┘│
                             │
  ┌──────────────────────────▼───────────────────────────────┐
  │ 文本模型 · 本地                                           │
  │ Ollama :11434  │  llama.cpp :8080/v1  │  LM Studio :1234/v1│
  │ llama.cpp router 模式：--models-dir 挂多个 GGUF，按需加载   │
  └──────────────────────────┬───────────────────────────────┘
                             │
  ┌──────────────────────────▼───────────────────────────────┐
  │ 文本模型 · 云端：任意 OpenAI 兼容端点 + RH LLM API(企业共享) │
  └──────────────────────────────────────────────────────────┘
```

**核心设计原则 1：浏览器永远不直连 ComfyUI / RunningHub / LLM。**
理由：(1) ComfyUI 无鉴权 + CORS 需拆 CSRF 防护；(2) 云端 URL 与 **RunningHub apiKey 属于凭据**，绝不能进前端 bundle；(3) 媒体要落盘不要 base64；(4) 统一的任务审计、取消、配额、成本统计只能在后端做。

**核心设计原则 2：生成侧抽象按「协议」分，不按「服务商」分。**
`comfy_native` 一个实现同时吃 本地 ComfyUI / cloudflared 隧道后的自建云端 / RunningHub `/proxy/{key}`；`rh_task` 单独一个实现。**不要把 RunningHub 写成第三个 if 分支** —— 那样会把它 80% 的兼容性白白扔掉。

---

## 3. 技术选型

| 层 | 选型 | 版本 | 说明 |
|---|---|---|---|
| 前端框架 | React + TypeScript | 19.2 / 5.8 | 沿用 manga-studio，生态最成熟 |
| 构建 | Vite | 6.x | dev 5173 / preview 4173 |
| 路由 | react-router-dom | 7.x | |
| 数据层 | @tanstack/react-query | 5.x | 服务端状态 + WS 失效重取 |
| 样式 | Tailwind CSS v4 | | **构建期装，不用 manga-studio 的 CDN script 方案** |
| 图标 | lucide-react | | |
| 状态 | Zustand | | 只放 UI 态，项目态走 react-query |
| 拖拽 | @dnd-kit | | 导演台时间轴、资产分镜 |
| 图表 | recharts | | 队列/耗时统计 |
| 后端 | FastAPI + Uvicorn | 0.115+ | async WS 客户端与 ComfyUI 匹配 |
| 数据校验 | Pydantic v2 | | |
| ORM | SQLAlchemy 2.0 async + asyncpg | | |
| 迁移 | Alembic | | |
| 队列 | PostgreSQL `SKIP LOCKED` | | **不引入 Redis/Celery**（单机 GPU 场景，进程内 asyncio + DB 队列足够，运维面最小） |
| DB | PostgreSQL 16 | | |
| 媒体处理 | ffmpeg (系统) + PyAV | | 拼接、转码、抽帧、缩略图 |
| 认证 | JWT (access+refresh) + Argon2 | | |
| 反代 | nginx（生产） | | |

**为什么 FastAPI 而不是 Django**：ComfyUI 侧是长连接 WS + 大量并发小请求（`/view` 拉媒体），asyncio 模型天然匹配；FastAPI 自带 OpenAPI，前端类型可自动生成。

**为什么不用 Celery**：目标是本地/局域网部署。引入 Redis + 2 个常驻进程 + 监控，运维成本远大于收益。PostgreSQL 队列在这个量级（单机 GPU 每分钟个位数任务）绰绰有余，且天然与业务数据同事务。

---

## 4. 数据模型（PostgreSQL）

```sql
-- ── 用户与租户 ─────────────────────────────────────────────
CREATE TABLE users (
  id            BIGSERIAL PRIMARY KEY,
  username      TEXT UNIQUE NOT NULL,
  display_name  TEXT NOT NULL,
  email         TEXT UNIQUE,
  password_hash TEXT NOT NULL,             -- Argon2id
  role          TEXT NOT NULL DEFAULT 'editor',  -- admin | editor | viewer
  is_active     BOOLEAN NOT NULL DEFAULT TRUE,
  preferences   JSONB NOT NULL DEFAULT '{}',
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_login_at TIMESTAMPTZ
);
CREATE TABLE refresh_tokens (
  id BIGSERIAL PRIMARY KEY, user_id BIGINT REFERENCES users(id) ON DELETE CASCADE,
  token_hash TEXT UNIQUE NOT NULL, expires_at TIMESTAMPTZ NOT NULL,
  revoked_at TIMESTAMPTZ, user_agent TEXT, ip TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── 生成实例（按协议建模，不按服务商建模）──────────────────
CREATE TABLE gen_instances (
  id           BIGSERIAL PRIMARY KEY,
  name         TEXT UNIQUE NOT NULL,        -- "本机 8188" / "云端 A40" / "RH 24G 共享"
  protocol     TEXT NOT NULL,               -- comfy_native | rh_task      ← 决定用哪个 Client
  placement    TEXT NOT NULL,               -- local | cloud_self | cloud_runninghub
                                             -- （仅用于 UI 分组与"本地/云端"筛选，不参与分派逻辑）
  base_url     TEXT NOT NULL,               -- http://127.0.0.1:8188
                                            -- https://comfy.example.com
                                            -- https://www.runninghub.cn/proxy/{key}   (protocol=comfy_native)
                                            -- https://www.runninghub.cn                (protocol=rh_task)
  ws_url       TEXT,                        -- 留空则由 base_url 推导 ws/wss；rh_task 恒为空（走轮询）
  site         TEXT NOT NULL DEFAULT 'cn',   -- 仅 RunningHub：cn(runninghub.cn) | global(runninghub.ai)
                                             -- ⚠️ 两站 key/余额/已上传素材**互不通用**，必须独立配置
  api_key_enc  BYTEA,                        -- Fernet 加密。RunningHub apiKey 走这里；本地实例为 NULL
  instance_type TEXT,                        -- 仅 RunningHub：default(24G) | plus(48G) | ultra(84G)
  auth_style   TEXT NOT NULL DEFAULT 'none', -- none | proxy_in_path | body_api_key | bearer | both
                                             -- RH: /proxy → proxy_in_path；/task/openapi → body_api_key；/openapi/v2 → bearer
  retain_seconds INT,                        -- 仅 RH 企业共享：10–180，保热实例，额外计费
  webhook_url  TEXT,                          -- 仅 RH：回调地址（需公网可达；本机部署一般用不上）
  is_default   BOOLEAN NOT NULL DEFAULT FALSE,
  local_output_root TEXT,                   -- 仅 placement=local：ComfyUI output 目录（直接读盘，省一次下载）
  local_input_root  TEXT,
  tunnel_name  TEXT,                        -- 仅 cloud_self：cloudflared 隧道名（UI 展示用）
  capabilities JSONB NOT NULL DEFAULT '{}', -- 缓存的 /system_stats + /object_info 摘要 + 能力探针结果
  quota        JSONB NOT NULL DEFAULT '{}', -- RH: {concurrentLimit,runningCount,queuedCount,remainCoins,remainMoney}
  cost_total   JSONB NOT NULL DEFAULT '{}', -- 累计 consumeCoins / consumeMoney / 秒数（成本审计）
  last_probe_at TIMESTAMPTZ,
  last_probe_ok BOOLEAN,
  last_error    TEXT,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_gen_placement ON gen_instances(placement, is_default);

CREATE TABLE llm_backends (
  id           BIGSERIAL PRIMARY KEY,
  name         TEXT UNIQUE NOT NULL,        -- "本地 Ollama" / "本地 llama.cpp" / "LM Studio" / "云端 API"
  scope        TEXT NOT NULL,               -- local | cloud      ← 设置页分组的唯一依据
  kind         TEXT NOT NULL,               -- ollama | openai_compat
      -- kind=openai_compat 覆盖：llama.cpp llama-server / LM Studio / vLLM / 任意云端 OpenAI 兼容端点 / RH LLM API
      -- 判定为 llama.cpp 的依据：GET /health 200 且 GET /props 返回 llama.cpp 形状（8080 端口常被别的东西占，不能只看端口）
      -- 判定为 RH LLM API 的依据：scope=cloud 且 capabilities.enterprise_shared_only=true（否则 1014）
  base_url     TEXT NOT NULL,               -- http://127.0.0.1:11434 | :8080/v1 | :1234/v1 | :8000/v1
  api_key_enc  BYTEA,                       -- Fernet 加密，绝不出后端。Ollama/LM Studio 通常为空
  chat_path    TEXT NOT NULL DEFAULT '/chat/completions',
  stream_style TEXT NOT NULL DEFAULT 'sse', -- sse | ndjson    ← ollama 原生口必须是 ndjson，不能与 openai_compat 共用解析器
  capabilities JSONB NOT NULL DEFAULT '{}', -- {models:[], ctx_size:<int|null>, has_json_schema:bool,
                                            --  has_vision:bool, has_tools:bool, ctx_is_per_request:bool,
                                            --  enterprise_shared_only:bool}
      -- ⚠️ ctx_is_per_request：Ollama=true（options.num_ctx 每请求可传）；llama.cpp=false（-c 是启动参数）
      --    这条差异决定了「上下文不够」时是自动重试还是提示用户重启进程，见 §5.2
  max_ctx      INT,                          -- 探测到的实际上下文上限；llama.cpp 从 /props 或 /slots 读
  is_default   BOOLEAN NOT NULL DEFAULT FALSE,
  last_probe_at TIMESTAMPTZ, last_probe_ok BOOLEAN, last_error TEXT,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_llm_scope ON llm_backends(scope, is_default);


-- ── 工作流 ─────────────────────────────────────────────────
CREATE TABLE workflows (
  id           BIGSERIAL PRIMARY KEY,
  name         TEXT NOT NULL,
  description  TEXT,
  tags         TEXT[] NOT NULL DEFAULT '{}',
  family       TEXT NOT NULL DEFAULT 'image',  -- image | video | audio | upscale | custom
  source_format TEXT NOT NULL,                 -- api | ui
  graph        JSONB NOT NULL,                 -- 归一化后的 API 格式
  ui_graph     JSONB,                          -- 原 UI 格式（保留，导出用）
  object_info_hash TEXT,                       -- 校验时的 /object_info 版本指纹
  node_summary JSONB NOT NULL DEFAULT '{}',    -- {class_type: [node_ids]} 供前端高亮
  slots        JSONB NOT NULL DEFAULT '[]',     -- [{address,name,type,default,widget,...}]
  requirements JSONB NOT NULL DEFAULT '{}',     -- {models:[{folder,filename}], custom_nodes:[...], pip:[...]}
  is_builtin   BOOLEAN NOT NULL DEFAULT FALSE,
  created_by   BIGINT REFERENCES users(id),
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── 项目 ───────────────────────────────────────────────────
CREATE TABLE projects (
  id          BIGSERIAL PRIMARY KEY,
  owner_id    BIGINT REFERENCES users(id) ON DELETE SET NULL,
  name        TEXT NOT NULL,
  synopsis    TEXT,
  stage       TEXT NOT NULL DEFAULT 'script',   -- script|assets|director|export
  config      JSONB NOT NULL DEFAULT '{}',       -- 视觉风格/画幅/全局参数/seed策略
  data        JSONB NOT NULL DEFAULT '{}',       -- ScriptData/Character/Scene/Shot 结构（无媒体）
  stats       JSONB NOT NULL DEFAULT '{}',       -- 统计缓存
  archived    BOOLEAN NOT NULL DEFAULT FALSE,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_projects_owner ON projects(owner_id, updated_at DESC);

-- ── 媒体（只存路径，绝不存 base64） ─────────────────────────
CREATE TABLE media (
  id           BIGSERIAL PRIMARY KEY,
  project_id   BIGINT REFERENCES projects(id) ON DELETE CASCADE,
  owner_id     BIGINT REFERENCES users(id) ON DELETE SET NULL,
  kind         TEXT NOT NULL,        -- image | video | audio | ref_image | ref_video | ref_audio
  role         TEXT,                 -- character | variation | scene | keyframe_start | keyframe_end | video | thumbnail
  ref_id       TEXT,                 -- 业务侧 ID，如 character:xxx / shot:002
  path         TEXT NOT NULL,        -- 相对 data/media/ 的路径
  thumb_path   TEXT,
  width INT, height INT, fps NUMERIC(6,3), duration_ms INT,
  bytes BIGINT, mime TEXT, sha256 TEXT,
  origin       JSONB NOT NULL DEFAULT '{}',
  -- {instance_id, subfolder, filename, type, prompt_id, node_id, template_version}
  meta         JSONB NOT NULL DEFAULT '{}',
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_media_project ON media(project_id, role, ref_id);

-- ── 任务队列 ───────────────────────────────────────────────
CREATE TYPE job_state AS ENUM
  ('queued','dispatching','running','succeeded','failed','canceled');
CREATE TYPE job_kind AS ENUM
  ('llm_chat','image','video','video_chain','upscale','detect_shots','assemble','workflow_test');

CREATE TABLE jobs (
  id            BIGSERIAL PRIMARY KEY,
  project_id    BIGINT REFERENCES projects(id) ON DELETE CASCADE,
  owner_id      BIGINT REFERENCES users(id) ON DELETE SET NULL,
  kind          job_kind NOT NULL,
  state         job_state NOT NULL DEFAULT 'queued',
  priority      INT NOT NULL DEFAULT 100,     -- 数字小者先跑；用户手动插队用 0
  title         TEXT NOT NULL,
  instance_id   BIGINT REFERENCES gen_instances(id),
  llm_backend_id BIGINT REFERENCES llm_backends(id),
  workflow_id   BIGINT REFERENCES workflows(id),
  prompt_id     TEXT,                          -- ComfyUI prompt_id，断点续跑锚点
  client_id     TEXT,                          -- ComfyUI WS sid
  params        JSONB NOT NULL DEFAULT '{}',
  progress      JSONB NOT NULL DEFAULT '{}',   -- {value,max,node,stage,eta}
  queue_pos     INT,
  attempts      INT NOT NULL DEFAULT 0,
  max_attempts  INT NOT NULL DEFAULT 3,
  error         JSONB,                        -- {type,message,node_id,node_type,traceback_tail}
  output        JSONB NOT NULL DEFAULT '[]',   -- [media_id,...]
  log           JSONB NOT NULL DEFAULT '[]',   -- 环形，最多 200 条
  started_at    TIMESTAMPTZ, finished_at TIMESTAMPTZ,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_jobs_pick  ON jobs(state, priority, id) WHERE state IN ('queued','dispatching');
CREATE INDEX idx_jobs_owner ON jobs(owner_id, created_at DESC);
CREATE INDEX idx_jobs_proj  ON jobs(project_id, kind, created_at DESC);

-- 长任务串行锁（每 ComfyUI 实例只允许一个前台任务）
CREATE TABLE instance_locks (
  instance_id BIGINT PRIMARY KEY REFERENCES gen_instances(id) ON DELETE CASCADE,
  job_id      BIGINT REFERENCES jobs(id) ON DELETE SET NULL,
  acquired_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── 审计 ───────────────────────────────────────────────────
CREATE TABLE audit_log (
  id BIGSERIAL PRIMARY KEY, user_id BIGINT, actor TEXT,
  action TEXT NOT NULL, target TEXT, detail JSONB DEFAULT '{}',
  ip TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- 日志脱敏：key 一律 <first4>****<last4>，绝不落原文

-- ── 风格预设（manga-studio 14 个，迁到表里） ───────────────
CREATE TABLE visual_styles (
  id BIGSERIAL PRIMARY KEY, key TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
  prompt_zh TEXT NOT NULL, prompt_en TEXT NOT NULL,
  thumb_path TEXT, sort_order INT DEFAULT 0, is_active BOOLEAN DEFAULT TRUE
);
```

**`projects.data` 里放什么**（参考 manga-studio 的 `types.ts` 但砍掉 base64 字段）：

```ts
{
  characters: [{ id, name, description, refMediaIds: string[],
                 variations: [{ id, name, desc, refMediaIds: string[] }],
                 seed, locked, promptNote }],
  scenes:     [{ id, name, description, refMediaIds: string[] }],
  shots:      [{ id, index, sceneId, characterIds: string[],
                 description, action, dialogue,
                 cameraMovement,          // 枚举 + 运镜指南文本
                 startFrameMediaId, endFrameMediaId,
                 videoMediaIds: string[], // SequenceForge 多段
                 durationSec, aspectRatio, seed, locked,
                 h3Prompt: { integrated, soundscape, music },
                 state: 'idle'|'queued'|'generating'|'completed'|'failed' }],
  renderLogs: [{ ts, shotId, kind, status, model, durationMs, jobId, error }]
}
```

---

## 5. 通信层设计

### 5.1 GenManager（两种协议客户端）

```python
class GenClient(Protocol):                    # ← 队列层只认这个协议，不认服务商
    async def probe(self) -> Capabilities
    async def upload(self, file: Path, **kw) -> Ref          # Ref = 可塞进节点入参的东西
    async def submit(self, payload, *, client_id, job_id) -> Submission
    async def poll(self, sub: Submission) -> JobSnapshot      # 归一化状态 + 进度 + 产物
    async def events(self, sub) -> AsyncIterator[Event]       # 可为空异步生成器（rh_task 走纯轮询）
    async def cancel(self, sub) -> bool
    async def fetch_output(self, ref) -> bytes                # /view 或 COS 签名 URL

class ComfyNativeClient(GenClient):            # 本地 :8188 / 自建云端(cloudflared) / RH /proxy/{key}
    async def object_info(self, class_type: str|None=None) -> dict
    async def system_stats(self) -> dict
    async def submit(self, graph: dict, *, client_id: str, prompt_id: str|None=None)
    async def history(self, prompt_id: str) -> dict
    async def view_url(self, filename, subfolder, type, preview=None) -> str
    async def interrupt(self, prompt_id: str|None=None) -> None
    async def free(self, unload_models=True, free_memory=True) -> None
    async def queue(self) -> dict
    # WS
    async def events(self) -> AsyncIterator[ComfyEvent]   # 单例循环，指数退避重连
    async def resolve(self, prompt_id, timeout) -> dict   # WS 终止信号 + /history 兜底

class RunningHubTaskClient(GenClient):         # 专有 API：排队/计费/webhook/显存档位/traceback
    async def submit(self, *, workflow_id: str|None, graph_json_str: str|None,
                     node_info_list: list[NodeOverride], instance_type: str,
                     retain_seconds: int|None, webhook_url: str|None) -> taskId
        # ⚠️ graph 要 **json.dumps 成字符串**放进 `workflow` 字段；workflowId 在 schema 里仍 required
    async def poll(self, task_id) -> V2Query            # /openapi/v2/query，5s 一次，上限 20min
    async def cancel(self, task_id) -> bool              # /task/openapi/cancel
    async def outputs(self, task_id) -> list[CosUrl]     # 立刻转存，链接约 1 天就失效
    async def queue_status(self) -> QueueStatus          # /openapi/v2/queue/status → concurrentLimit
    async def account_status(self) -> Account             # /uc/openapi/accountStatus → remainCoins/Money
    async def import_workflow(self, workflow_id) -> str   # /api/openapi/getJsonApiFormat → API 格式图
    async def app_schema(self, web_app_id: int) -> FormSchema  # /api/webapp/apiCallDemo → 自动生表单
```

`NodeOverride = {nodeId: str, fieldName: str, fieldValue: Any}` —— **这是 RH 的入参覆盖模型，跟我们 §6 的 slot 引擎是两个东西**。转换关系：我们的 slot 地址 `131.prompt` → `{nodeId:"131", fieldName:"prompt", fieldValue:...}`。slot 引擎仍然自己 patch API 图（给 comfy_native 用），只对 RH 额外投影出 `nodeInfoList`。
两条必须内建的规则：**（a）`fieldValue` 必须保持原始线格式**（`[["7",0]]` 这类连线不许动，前端 widget 如 `control_after_generate` 在 api 格式里不存在、设不了）；**（b）每次都显式注入 `seed`** —— RH 文档明写"API 调用会强制重置 seed 值"，不注入就永远拿不到可复现的重绘。

**必须实现的 9 条防御规则**（每条都来自调研中的真实坑，且已逐条对源码核对）：

1. `submit()` **永远带 `client_id`**，且与 WS URL 的 `clientId` 一致 —— 否则 `server.client_id=None`，收不到任何事件
2. `object_info` 校验必须**判空 dict**（未知节点返回 200 `{}`），不能判 404
3. 输出提取按键 `"images"` 取（`SaveVideo` 的 history 键是 `images` + `animated:[true]`）
4. WS 终止判据是 `executing` 且 `data.node is None`；`execution_success` 虽然自 v0.3.10 就存在，但**只在带 client_id 且正常完成时发**，出错/中断路径没有 —— 不能用它做唯一判据，只能作为提前结束的加速信号
5. 同一 `clientId` 重连会踢旧连接 → 客户端单例 + `backoff = min(30, 2**n)` 退避 + 重连后从 `executing` 事件自动 resync
6. 二进制帧解析：`>I` 大端事件码（1 预览图 / 2 未编码预览 / 3 TEXT / 4 带元数据预览），先解 4 字节再判别；type 4 还要再解 4 字节 metadata 长度
7. 提交前先 `validate_prompt`（本地跑一遍：缺失 model 文件、缺 class_type、`17k+5` 帧数、Turbo 步数配对）——省掉一次往返失败
8. `prompt_id` 用客户端预生成的 UUID（规范小写连字符）—— `validate_job_id()` 会拒非规范形式并返回 400 `invalid_prompt_id`；与我们的 `jobs.id` 双向映射
9. 音频/参考视频**没有** `/upload/audio` 端点，一律走 `/upload/image`（multipart `image` 字段），返回 `{name, subfolder, type}`

**多实例并行**：`GenManager` 按 `gen_instances.protocol` 为每行建一个客户端。每实例一个 `asyncio.Semaphore(1)`（本地 ComfyUI 本身一次只跑一个）；RH `/proxy` 模式**也必须 =1**（Krita 集成实测并发不支持）；RH Task API 模式并发上限由平台给（`queue/status` 的 `concurrentLimit`，企业共享默认 100），所以**这一档才真正能并行批量**。本地 + 自建云 + RH 三者可同时跑 → 本地出图、RH 出 H3 视频，是我们最重要的吞吐来源。

**RunningHub 专属的 5 条硬规则**：

| 规则 | 原因 |
|---|---|
| `results[].url` / `download_url` **拿到就下载落盘** | COS 签名链接约 1 天失效；DB 里只存我们自己的 `media.path` |
| 提交前必须已 `803` 自检 `nodeInfoList` | nodeId/fieldName 不匹配直接 `803 APIKEY_INVALID_NODE_INFO`，浪费一次排队 |
| 工作流必须提示用户"先在网页保存并跑通一次" | 否则 `810 WORKFLOW_NOT_SAVED_OR_NOT_RUNNING` |
| `421/1520/415/1003` 一律退避重试，不判失败 | 这些是**并发/队列满**，不是任务错误；`415` 文档还建议等 30–120s |
| `416/812`（余额不足）判 fatal 并停止派发该实例所有任务 | 重试只会继续烧钱。要把 `accountStatus` 的 `remainMoney` 显示在仪表盘 |

> ⚠️ **`site` 不是可有可无的字段**：`runninghub.cn` 与 `runninghub.ai` 路径与字段完全相同，但 key、余额、上传素材**互不通用**，且官方要求 `Host` 必须精确。一个实例一行记录，绝不共用。


**媒体落地策略**：

| 情况 | 策略 |
|---|---|
| 本地实例 + 配了 `local_output_root` | 产物已在磁盘，**直接软链/移动**到 `data/media/`，零网络开销 |
| 本地实例 + 未配 | `GET /view` 拉下来落盘 |
| 自建云端 / RH `/proxy` | `GET /view` 拉下来落盘（并用 `preview=webp;90` 额外生成缩略图） |
| **RH Task API** | 拿 `results[].url`（Tencent COS 签名）**立即下载**；`/openapi/v2/query` 的 `text` 类输出直接入库。**绝不把外链写进 `media.path`** —— 约 1 天就 404 |
| 任何实例的前端预览 | `/api/media/{id}/raw` 走我们后端，**支持 HTTP Range**（视频拖进度条必需——manga-studio 就是因为浏览器直吃大体积 base64 而进度条挂掉） |


### 5.2 LLMManager（分「本地组」与「云端组」两组后端）

统一接口，能力按后端自动降级，**并按 `scope` 分组**（设置页、任务默认值、故障隔离都按这两组走）：

```python
class LLMClient(Protocol):
    async def list_models(self) -> list[ModelInfo]
    async def probe(self) -> Capabilities      # {ctx_size, has_json_schema, has_vision,
                                               #  has_tools, ctx_is_per_request}
    async def chat(self, messages, *, temperature, max_tokens,
                   ctx_size: int | None, json_schema: dict | None,
                   stream: bool) -> AsyncIterator[str]
    async def embed(self, texts: list[str]) -> list[list[float]]

class OllamaClient(LLMClient):      # kind=ollama；原生 /api/chat + NDJSON + /api/embed + /api/pull
class OpenAICompatClient(LLMClient):# kind=openai_compat；SSE + /v1/chat/completions + /v1/embeddings
    # 服务：llama.cpp llama-server、LM Studio、vLLM、任意云端 OpenAI 兼容端点
    # 同一份代码，差别只在 base_url / api_key / 是否支持 response_format.json_schema
class RunningHubLLMClient(OpenAICompatClient):
    # 企业级-共享 key 限定；不支持则报 1014，UI 要能把这条错误原样显示出来
```

**四个真实陷阱**：

| 陷阱 | 处理 |
|---|---|
| Ollama 的 `num_ctx` **按显存分档自动选**，官方规则：`<24 GiB → 4k`、`24–48 GiB → 32k`、`≥48 GiB → 256k`。长剧本大纲会在 4k 处被截断（截断本身不一定报错，风险真实但官方文档未明文写"silent"） | 每次请求**显式传 `options.num_ctx`**（默认 16384），并在设置页显式暴露；另提供 Modelfile 固化与 `OLLAMA_CONTEXT_LENGTH` 全局默认两条路。**⚠️ 本机实测 RTX 4090 = 24,564 MiB = 23.99 GiB，正好落在 `<24 GiB` 档 → 默认只有 4k 上下文**，不显式传就必被截断；且 Ollama 需要较新版本（当前 v0.34.x；本机装的 0.32.13 建议升级） |
| **llama.cpp 的上下文是启动参数 `-c/--ctx-size`，不是每请求参数** | `capabilities.ctx_is_per_request=False`。请求要 16k 而探测到 `-c 2048` 时，**不能像 Ollama 那样塞进 options 假装生效**，必须显式报错："请重启 llama-server 并加大 `-c`，当前 XX"。同时给出一键复制的启动命令（含 `--models-dir`、`--jinja`、`-ngl 999`） |
| Ollama 流式是 **NDJSON**（无 `data:` 前缀），OpenAI 兼容层（含 llama.cpp/LM Studio/vLLM）才是 SSE | `stream()` 内部按 `stream_style` 选解析器，绝不共用一套 |
| 嵌入端点：旧 `/api/embeddings` 单值才有效，**收数组静默返回 `{"embedding": []}`**（ollama/ollama#7242、#13020 已确认），且响应是扁平 `{"embedding": number[]}` | 只用 `/api/embed`（返回恒为 `number[][]` 且 **L2 归一化** → 点积即余弦相似）。要 OpenAI 客户端兼容就走 `/v1/embeddings` |

**llama.cpp 接入要点**（本地组的第二个主力，用户明确要求支持）：

- **推荐 Router 模式**，一个进程挂一个目录、按需加载多个 GGUF，体验才接近 Ollama：
  `llama-server --models-dir D:/models/gguf --no-models-autoload --jinja --host 127.0.0.1 --port 8080 -ngl 999 -c 32768`
  → 我们的"本地模型管理"对它退化为：列 `/models`、切模型只换 `model` 字段、**没有 pull API**（GGUF 需手动从 HF 下，或用 `--hf-repo/--hf-file` 首次拉取）。UI 要按后端类型显示不同的"获取模型"入口，不能假设都能 pull。
- **探活不能只看端口**：8080 是公共冲突端口。判据是 `GET /health` 200 **且** `GET /props` 返回 llama.cpp 的形状；`/slots` 还能读出当前加载的模型与真实 ctx 余量，是我们显示"上下文还剩多少"的数据来源。
- `--jinja` 不开 → 没有 tool calling；结构化输出走 `response_format:{type:"json_schema", json_schema:{...}}`（官方支持 schema 约束），**这点比 Ollama 的 `format` 更接近 OpenAI**，所以 llama.cpp 归到 `openai_compat` 而非单独实现。
- 其他：命令行参数优先于环境变量；`--api-prefix` 可挂子路径配合反代；`--api-key` 可选（存 `api_key_enc`）；Windows 预编译 `llama-server.exe`；Docker `ghcr.io/ggml-org/llama.cpp:server`。

**结构化输出**：Ollama 用 `format: <json schema>`；OpenAI 兼容（含 llama.cpp）用 `response_format: {type:"json_schema", ...}`。两者都做「JSON 修复」兜底：剥 ```json 围栏、去前后废话、单括号补全（本地小模型尤其需要）。

**默认模型建议**（面向中文创作/分镜/提示词工程，8–16GB 显存可跑）：

| 场景 | Ollama | llama.cpp（对应 GGUF） |
|---|---|---|
| 默认主力 | `qwen3:8b` | Qwen3-8B Q4_K_M |
| 显存紧 | `qwen3:4b` | Qwen3-4B Q4_K_M |
| 显存富余 / 要更好中文 | `qwen3:14b` | Qwen3-14B Q4_K_M |
| 纯推理辅助 | `deepseek-r1:8b` | DeepSeek-R1-8B Q4_K_M |
| 视觉理解（读参考图） | `qwen3-vl:8b` | Qwen3-VL-8B MMXX（需 `--mmproj`） |
| 向量（角色/分镜相似检索） | `bge-m3`（中文最佳）／`qwen3-embedding`／`embeddinggemma`(300M)／`all-minilm` | 对应 GGUF + `--embd-normalize`（默认 2=欧氏） |

> ⚠️ 2026 年的各种"最强开源模型"榜（DeepSeek V4、Kimi K2.6/K3、GLM-5.x、Qwen3.5-397B）普遍 100B–700B，**消费级显卡跑不了**，Ollama 库里多数还标着 `cloud`。**不要为了"最新"牺牲可运行性。**

**本地组还支持**：LM Studio（`:1234/v1`，`lms server start`，默认无鉴权、可选 token）、vLLM（`:8000/v1`，建议 `--api-key`）。与 Ollama/llama.cpp **默认端口互不冲突**，可同机并跑 → 设置页允许同时存在多个本地后端，用 `scope=local + is_default` 选主。
**云端组**：任意 OpenAI/Anthropic 兼容端点（DashScope、DeepSeek、火山 Ark、MiniMax…）+ RunningHub LLM API（企业级-共享限定）。四者共用 `OpenAICompatClient`，换后端只改 `base_url` + `api_key`。


### 5.3 QueueDispatcher

```python
# 单个 dispatch 循环
async def _loop(self):
    while True:
        async with self.db.begin() as s:                       # REPEATABLE READ
            # ① 拿实例锁（串行）
            inst = await s.scalar(select(GenInstance)
                 .join(InstanceLock, is_(None)).order_by(GenInstance.id).limit(1)
                 .with_for_update(skip_locked=True))
            if inst is None: await asyncio.sleep(1); continue
            # ② 取该实例可派发的任务
            job = await s.scalar(select(Job)
                 .where(Job.instance_id==inst.id,
                        Job.state.in_(('queued','dispatching')),
                        Job.priority <= ...)
                 .order_by(Job.priority, Job.id)
                 .with_for_update(skip_locked=True))
            ...
```

要点：
- **一个实例一个锁** → 本地/云端真正并行，实例内严格串行
- `max_attempts=3`，`400/401/403` 立即失败不重试；`5xx/超时/连接断开` 指数退避重试
- `prompt_id` 一旦拿到就写库 → 浏览器刷新/换设备/关标签页都不影响，服务端继续轮询
- 恢复：进程重启时把 `dispatching/running` 但无 `prompt_id` 的置回 `queued`；有 `prompt_id` 的直接接 `/history` 查状态
- 取消：`POST /interrupt {"prompt_id": id}`，云端超时则退化为 `POST /queue {"delete":[id]}`，两者都失败则标记 `canceled` 并记日志（不假装成功）
- 配额：`viewer` 只能看；`editor` 每人同时在跑任务上限（如 2）+ 每日配额（`user_quotas` 表或直接读 `users.preferences`）；`admin` 无限制

### 5.4 前端进度

浏览器 ↔ 后端：`/ws/task`（一条连接，订阅自己可见的 job）。
后端内部：ComfyUI WS 事件 → 归一化成 `{job_id, type, value, max, node, stage}` → 广播给相关订阅者。

`progress` 事件的 `node` 用于在前端把进度条挂到正确的镜头上；`state` ∈ `pending|running|finished|error`。断线重连后前端先 `GET /api/jobs?ids=...` 补一次全量快照再接增量。

---

## 6. 工作流引擎（核心差异化能力）

### 6.1 两种格式与双向转换

| | API 格式（`/prompt` 收） | UI 格式（Ctrl+S 存） |
|---|---|---|
| 节点寻址 | 顶层 key 是节点 id 字符串 | `nodes[]` 里的 `id` 字段 |
| 类型字段 | `class_type` | `type` |
| 连线 | `inputs.x = ["srcId", slotIndex]` | `links[] = [linkId, originId, originSlot, targetId, targetSlot, type]` **＋** 每个 socket 的 `inputs[].link` / `outputs[].links` 双向引用 |
| 控件值 | 内联进 `inputs`，按控件名 | 位置数组 `widgets_values[]`，**强依赖顺序** |
| 布局 | 无 | `pos / size / order / mode / flags / title / groups` |
| 子图 | 展开内联 | `definitions.subgraphs[]`，实例节点的 `type` 是 **UUID** |

**ComfyUI 服务端没有格式转换器**（官方流程是在前端 File→Load 然后 File→Export Workflow (API)）。**所以我们必须自己写一个。** 这是本方案技术上最难、也最有价值的一块。

#### `ui → api` 转换算法

```
1. 拉 /object_info 建索引：class_type → {input_order: [...], input: {...}, output: [...]}
2. 建 link 表：linkId → (originId, originSlot, targetId, targetSlot, type)
3. 遍历 nodes：
   a. 按 class_type 取 input_order
   b. 遍历 node.inputs：
      - name 含 '.' → Autogrow 点名，拆出 (group, field, index)，只把实际存在的 index 放进 inputs
      - 指向已连线的 socket → 从 link 表取 (originId, originSlot)
      - 未连线但 order 落在 widget 区间 → 从 widgets_values 按 order 取值
   c. 处理 Converted Widget（properties.proxyWidgets 指向别处的 widgets_values）
   d. 处理 mode=4（bypass）/ mode=2（muted）→ 输出该节点的"旁路映射"给执行层
   e. 处理 Subgraph 实例（type 是 UUID）→ 递归展开 definitions.subgraphs
4. 校验：对每个必填输入，检查是否有连线或有默认值；class_type 是否在 object_info 中
5. 输出 API 格式 + node_summary + requirements
```

**必须处理的 6 个坑**：
1. `properties.proxyWidgets`（控件被搬到别处，如"强度"被算子占用）
2. `mode`: 0=always, 2=muted(输出直通输入), 4=bypass
3. Autogrow 的点命名 `ref_images.ref_image_0` —— 只能放**实际存在**的 index，塞 0..8 会报 validation error
4. `widgets_values` 顺序必须严格按 `input_order`（服务端 `object_info` 给的 `input_order` 是权威）
5. 子图 UUID 节点要展开
6. `is_output_list` / `output_is_list` 的多输出节点（取哪一路要由 slot 决定）

#### `api → ui` 转换算法

```
1. 节点 id / class_type / inputs 一一映射
2. 遍历所有 ["srcId", slot] 反推出 link 列表，双向引用回填
3. 按控件名从 inputs 里挑出 widget 值，按 object_info 的 input_order 排成 widgets_values
4. 无 id 的节点补 Uuid 风格 id（ComfyUI 前端接受任意字符串 id）
5. 布局：最长路径分层（Kahn 拓扑分层）→ 同层节点网格排布，节点尺寸按输入数估算
6. extra: {ds:{scale:0.75, offset:[x,y]}}
```

### 6.2 Slot 参数化（借鉴 comfy-mcp）

导入工作流后自动扫描出**所有可被外部驱动的槽位**：

```json
{
  "address": "131.prompt",           // 或 "6.text" / "115.0" / "115.aspect_ratio"
  "path": "ref_images.ref_image_0",  // Autogrow 完整名
  "name": "H3 提示词",
  "type": "string",
  "default": "...",
  "widget": true,
  "min": null, "max": null, "step": null, "options": null,
  "group": "输入",                    // 输入|采样|输出
  "required": true
}
```

**地址语法**（跟 comfy-mcp 保持一致，便于生态互操作）：
- `nodeId.inputName` —— 命名输入
- `nodeId.index` —— 位置输入（控件）
- `parentId/childId.inputName` —— 子图内部（子图 id 用 `parent/child`）
- 通配：`*:inputName` —— 同名控件全部替换（AutoGrow 组必备）

**变体生成（vary）**：给一个 slot 传值数组 → 展开成 N 个工作流（comfy-mcp 的 `vary_workflow` 行为），A/B 选片用。

### 6.3 导入流程

```
上传 .json → 后端：
  ① 判格式（有 nodes 数组 = UI；否则 API）
  ② 归一化为 API 格式
  ③ GET /object_info 校验：
     - 未知 class_type → 报 unknown_nodes（附 Comfy-Manager registry 建议）
     - 缺失模型文件 → 报缺哪些 models/{folder}/{file}（用 GET /models/{folder} 对比）
     - 缺 pip 依赖 → 报 requirements（用 comfy-mcp 的 workflow_deps 逻辑）
  ④ 抽取 slots、推断 family、估算默认分辨率/时长/步数
  ⑤ 存 workflows 表；若"一键安装缺失项"被勾选，生成 install_plan（交给用户确认）
  ⑥ 返回校验报告 + slots + 缺失清单（前端渲染成可点的安装列表）
```

**另外两个导入来源（不用上传文件）**：

| 来源 | 通道 | 注意点 |
|---|---|---|
| **RunningHub 工作流库** | `POST /api/openapi/getJsonApiFormat` `{apiKey, workflowId}` → `data.prompt` 已是 **API 格式图字符串** | 省掉 §6.1 的 UI→API 转换。但图里含 RunningHub 特有字段（`speak_and_recognation` 等）与我们不认识的 RH 专属节点（`RHLoraLoader` 等）→ 校验时要单独标成 `runninghub_only_nodes`，**告诉用户这份图只能在 RH 实例上跑**，别伪装成可移植 |
| **RunningHub 应用（AI 应用）** | `GET /api/webapp/apiCallDemo?apiKey=&webappId=` → 返回**机器可读表单 schema**：`nodeInfoList[]` 每项带 `nodeName`/`fieldType`(STRING/INT/IMAGE/VIDEO/AUDIO/LIST)/`fieldData`(枚举或 widget spec 含 `default`)/中英描述 | 直接用它**自动生成本地表单 UI**，比让用户手填 nodeId 强得多。但应用产出的结果**不带工作流信息、不可复现** → 只用于"发现参数"，真正渲染仍优先工作流 ID。`webappId` 是 **int64**，别跟 string 的 `workflowId` 混用同一个字段 |

> slot 引擎对 RH 的投影：我们的 slot 地址 `131.prompt` ⇄ `nodeInfoList [{nodeId:"131", fieldName:"prompt", fieldValue:...}]`。同一份 slot 定义喂两种执行后端，**这是让"导入任意工作流"在 RH 上也成立的唯一办法**。


**绝不自动安装任何东西**——只列清单 + 给命令。comfy-mcp 对 `install_node` / `update_comfyui` 都做用户确认（elicitation），我们照做。

### 6.4 内置工作流（随应用分发）

| key | 用途 | 依赖 |
|---|---|---|
| `h3_t2v` | H3 文生视频（T2VA，带音频） | 官方节点 |
| `h3_i2v` | 首帧生视频 | 官方 |
| `h3_fl2v` | 首尾帧生视频 | 官方 |
| `h3_r2v` | 参考图/参考视频/参考音频驱动 | 官方 + Autogrow |
| `h3_turbo_t2v` | 8 步 Turbo | + fl2v turbo LoRA |
| `h3_turbo_r2v` | 4 步 Turbo | + ref2v turbo LoRA（**必须配 ref2va 底模**） |
| `qwen_image_21_t2i` | Qwen-Image-2.1 文生图 | Comfy-Org/Qwen-Image-2.1 |
| `qwen_image_21_edit` | Qwen-Image-2.1 图编辑（≤10 参考图） | 同上 |
| `h3_chain_segment` | SequenceForge 式单段无缝续拍 | SequenceForge 节点 |
| `h3_seam_doctor` | 接缝六维诊断 | SequenceForge |
| `detect_shots` | PySceneDetect 智能分镜 | AIMixer 风格端点或本项目自研 |
| `assemble` | 时间轴拼接成片 | 我们的 ffmpeg 管线 |

**API 格式骨架**（以此为模板生成其余，务必用 `class_type` 定位而非硬编码 id —— TFboy1 的教训）：

> ⚠️ **上一版这里写错了**：骨架里混进了 `ResolutionSelector`、`PrimitiveFloat`、`ComfyMathExpression` 三个**非 core 节点**（社区包），却被标注成"全官方节点安全版"。
> 正确做法：**宽、高、length 全部由我们后端算好，以字面量写进图里** —— 我们已经有 `h3_length()`，`ResolutionSelector` 的那点算术不值得为它引入三个社区依赖。`MiniMaxH3ImageToVideo` 同时输出 CONDITIONING 与 LATENT，连空 latent 节点都不需要。

修正后的**真·core-only** 骨架（仍以 `class_type` 定位，不硬编码 id —— TFboy1 的教训）：

```json
{
  "127": {"class_type":"UNETLoader","inputs":{
      "unet_name":"minimax_h3_fl2va_pruned_int8_convrot.safetensors","weight_dtype":"default"}},
  "128": {"class_type":"CLIPLoader","inputs":{
      "clip_name":"qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors","type":"minimax","device":"default"}},
  "119": {"class_type":"VAELoader","inputs":{"vae_name":"minimax_h3_video_vae_fp16.safetensors"}},
  "120": {"class_type":"VAELoader","inputs":{"vae_name":"minimax_h3_audio_vae_fp32.safetensors"}},
  "131": {"class_type":"MiniMaxH3ImageToVideo","inputs":{
      "clip":["128",0],"vae":["119",0],
      "width":864,"height":480,"length":124,
      "prompt":"integrated_multimodal_description: ...\noverall_soundscape: ...\nnon_diegetic_music: ..."}},
      // 可选："first_frame":["KF",0]  "last_frame":["LF",0]  → i2v / fl2v
  "137": {"class_type":"MiniMaxH3SigmaShift","inputs":{
      "model":["127",0],"shift_video":12.0,"shift_audio":3.0}},
  "135": {"class_type":"KSamplerSelect","inputs":{"sampler_name":"res_multistep"}},
  "124": {"class_type":"BasicScheduler","inputs":{
      "model":["137",0],"scheduler":"simple","steps":25,"denoise":1}},
  "129": {"class_type":"RandomNoise","inputs":{"noise_seed":0}},
  "126": {"class_type":"BasicGuider","inputs":{
      "model":["137",0],"conditioning":["131",0]}},
  "125": {"class_type":"SamplerCustomAdvanced","inputs":{
      "noise":["129",0],"guider":["126",0],"sampler":["135",0],
      "sigmas":["124",0],"latent_image":["131",1]}},
  "122": {"class_type":"VAEDecode","inputs":{"samples":["125",0],"vae":["119",0]}},
  "121": {"class_type":"VAEDecodeAudio","inputs":{"samples":["125",0],"vae":["120",0]}},
  "130": {"class_type":"CreateVideo","inputs":{"images":["122",0],"audio":["121",0],"fps":24,"bit_depth":8}},
  "92":  {"class_type":"SaveVideo","inputs":{
      "video":["130",0],"filename_prefix":"h3/seg","format":"auto","codec":"auto"}}
}
```

> 这份仍然要在 M1 用真实实例过一遍 `POST /prompt`（`node_errors` 为空即通过）——**节点输入名以 `/object_info` 实况为准**，不信我、不信官方 example workflow（AIMixer 那条教训：example 比代码落后一个版本）。
> r2v 路径换 `MiniMaxH3ReferenceToVideo`（required 多一个 `audio_vae`，参考用 autogrow 点名）。
> TFboy1 的模板用了 `PathchSageAttentionKJ`（注意拼写）、`MiniMaxH3TurboSampler` 等社区节点，若缺失会在 400 `node_errors` 报出来——前端渲染成"缺少节点包 xxx，点击安装"。


---

## 7. 业务管线

### 7.1 剧本解析（LLM，本地 Ollama）

```
原始文本 → LLM(结构化输出) → ScriptData
  { logline, synopsis, characters[], scenes[], shots[] }
```

- 用 Pydantic 定义 schema，传给后端的 `json_schema`，强制结构化输出
- 分段处理：长剧本先按场次切块，逐块解析再合并（避免 4k 上下文截断 + 显存陷阱）
- 失败重试 2 次，仍失败则降级为**纯规则切分**（按 `场景N` / `INT.` / `EXT.` 正则），保证"永远有东西可用"
- 全部调用记录 token 用量与耗时到 `renderLogs`

### 7.2 资产（角色 / 场景）

- 角色卡：LLM 从剧本抽取外形描述 → 结构化（年龄段、体型、发型、服装、配色、标志特征）
- **一致性策略**：为每个角色锁定一个 `seed`；参考图上传后可作为后续所有出图的 `ref_image`
- 服装变体（variation）：同一角色不同造型，共享底层 seed
- 图像生成走 `qwen_image_21_edit`（多参考图 + 编辑）比纯 T2I 的一致性高得多
- 提示词注入：全局风格前缀 + 角色一致性硬约束块（"STRICT REPLICATION ONLY" 那段规则）—— 放在模板引擎而不是散落在代码里

### 7.3 导演台（时间轴）

时间轴数据结构（借鉴 AIMixer `timeline_data` v4，但去掉它的"导演台状态 JSON 塞进 widget"的怪癖——我们走 API 通道）：

```json
{
  "version": 1,
  "totalFrames": 124, "frameRate": 24.0,
  "width": 864, "height": 480,
  "output": { "mode":"fixed","longEdge":864,
              "continuityEnabled":false,"continuityOverlapFrames":22 },
  "segments": [{
    "id":"s1","start":0,"frameCount":124,"durationSec":5,
    "taskType":"t2v",                    // t2v|i2v|fl2v|r2v
    "prompt":"", "negativePrompt":"",
    "refs":{"images":[],"videos":[],"audios":[]},
    "firstFrame":null, "lastFrame":null,
    "template":"h3_turbo_t2v", "seed":0,
    "state":"idle"
  }]
}
```

- **续拍（continuity）**：段 N 的输出自动成为段 N+1 的"引导帧"（重叠 22 帧，走 `minimax_keyframes` anchor）。默认开启，因为我们做的是短剧
- **音频连续性不追求自动延展** —— TheTerrasque 实测"连续音频变 gibberish"。改为：把上一段的音频作为普通 `ref_audio` 喂给下一段
- 拖拽排序用 `@dnd-kit`，后端用 `DEFERRABLE INITIALLY DEFERRED` 的 `UNIQUE(project_id, order)`，避免 reorder 事务内撞唯一键

### 7.4 H3 提示词生成（本地 LLM 的主要用武之地）

```
结构化分镜信息 → LLM → 三段式 H3 提示词
  integrated_multimodal_description:  [画面描述] [镜头运动] [角色动作] <d>台词</d>
  overall_soundscape:                环境声 + 拟音
  non_diegetic_music:                配乐风格
  + 参考标签 <Picture 1> ... <Audio 1>
```

LLM 的 `system prompt` 里内置官方规范：三段式结构、参考图上限 9 / 参考视频 3 / 参考音频 3、中文台词必须包 `<d>…</d>`（SequenceForge 会自动转，我们前端也做同样转换双保险）。
输出后**正则自检**：三段标题齐全、无超限标签、无未闭合 `<d>`。不合格则重试，再不合格保留用户手改。

### 7.5 渲染执行

```
选中镜头/全部镜头 → 批量入队（优先级：手动 > 当前镜头 > 顺序）
  → QueueDispatcher 派发到 local/cloud
  → ComfyUI 提交（先本地校验，省一次失败往返）
  → WS 事件 → 进度实时回传
  → 产物落盘 data/media/ + 建 media 行
  → 更新 shots[].state
```

批量生成**不是** manga-studio 那种浏览器里的 `for` 循环 + `sleep(3000)`。任务在服务端，关标签页照跑，回来还在。

### 7.6 导出

| 产物 | 方式 |
|---|---|
| 单镜 MP4 | 直接取 media |
| 整片 MP4 | ffmpeg concat（先 `-c copy`，流不兼容时回退 `-c:v libx264 -preset fast -crf 23 -c:a aac`），并发度=1（GPU 忙） |
| 剪映草稿 | 子进程调 `draft_builder.py` → `draft_info.json` |
| 源素材包 | ZIP：`characters/` `scenes/` `shots/shot_001_start.jpg` `shots/shot_001_end.jpg` `videos/shot_001.mp4` |
| 时间轴 JSON | 自有格式，可再导入 |

---

## 8. 前端页面

| 路由 | 内容 |
|---|---|
| `/login` | 登录（首次启动强制创建 admin） |
| `/` 仪表盘 | 项目卡片网格 · 队列概览 · 各实例健康/VRAM · 最近产物 |
| `/p/:id/script` | 剧本原文 ↔ 结构化结果双栏 · 角色/场景/分镜列表 · LLM 重解析/续写/改写 |
| `/p/:id/assets` | 角色卡 / 服装变体 / 场景卡 · 参考图拖拽上传 · 批量出图 · 资产库复用 |
| `/p/:id/director` | **核心页**。三栏：镜头列表 · 关键帧对比（首帧/尾帧，支持"复制上一镜尾帧到下一镜首帧"）· 参数面板（画幅/时长/步数/seed/Turbo/模板/实例）· 下方时间轴（可拖拽、可缩放、进度条叠加） |
| `/p/:id/queue` | 全局队列：状态/进度/取消/重试/改优先级/日志详情 |
| `/workflows` | 工作流库：列表 · 导入向导（校验报告 + 缺失清单）· 槽位编辑器 · 试运行 · 变体批量 · 导出 UI/API |
| `/export/:id` | 成片预览 · 时间轴可视化 · 剪映草稿 · 素材包 · 提交后轮转 |
| `/settings/gen` | **生成实例**（见 §8.1）：本地 ComfyUI / 自建云端(cloudflared) / RunningHub（`/proxy` 原生 与 Task API 两种协议、站点 cn↔global、显存档位、余额与并发） |
| `/settings/llm` | **AI 模型设置**，明确分 **「本地 AI 模型」** 与 **「云端 AI 模型」** 两块（见 §8.2） |
| `/settings/users` | 用户、角色、配额（仅多用户模式需要；单机自用可整块隐藏） |
| `/settings/system` | 存储路径、ffmpeg 路径、媒体清理与占用、日志、审计 |

### 8.1 生成实例页

三个分组卡片，每组都是「列表 + 新增 + 探活」：

| 分组 | 字段 | 探活要看到什么 |
|---|---|---|
| **本地 ComfyUI** | `base_url`(默认 `http://127.0.0.1:8188`)、`local_output_root`、`local_input_root` | `/system_stats` 200 + `/object_info` 节点数 + H3 能力探针四项 + **磁盘上模型文件是否齐全**（本地独有的优势：能直接比对 `local_output_root` 与 §1.3 的清单，逐文件打勾/缺失） |
| **自建云端 ComfyUI** | `base_url`(https)、`tunnel_name`、cloudflared 状态 | 同上（不含模型文件比对；`/view` 首字节延迟单独显示） |
| **RunningHub** | `protocol`(原生代理 / Task API)、`site`(cn/global)、`apiKey`、`instanceType`(24G/48G/84G)、`retainSeconds` | 原生代理 → `/proxy/{key}/object_info`；Task API → `/openapi/v2/queue/status`（`concurrentLimit/runningCount/queuedCount`）+ `/uc/openapi/accountStatus`（`remainCoins/remainMoney`）并**把余额显示在仪表盘** |

**每个实例都必须显示**：当前是否可用、上次探活时间与错误原文、能力摘要（有没有 H3 节点 / 有没有 Qwen-Image / 自定义节点数量）、累计花费（RH 才有）、`Semaphore` 当前占用。
**`instanceType` 与 `placement` 要在导演台的实例选择器里直接可见** —— 用户选"云端跑 H3"时，看到的应该是"RH 48G（余额 ¥xx，并发 30）"这种信息，不是一个抽象名字。

### 8.2 AI 模型设置页（本地 / 云端两块 + 本地可用连接自动发现）

用户明确要求：**按「本地 AI 模型」和「云端 AI 模型」分开**。落成两个 section + 一个自动发现面板。

**① 本地可用连接（自动发现，放在页面最上方）**
后端 `GET /api/llm/local-scan` 并发探这几个环回端口，**用响应形状判别而不是端口号**（8080 是最常见的冲突端口）：

| 端点 | 判别为 | 依据 |
|---|---|---|
| `:11434/api/tags` | Ollama | 返回 `{"models":[{"name","size","modified_at"}]}` 形状 |
| `:8080/health` + `/props` | llama.cpp `llama-server` | `/health` 200 且 `/props` 是 llama.cpp 形状（有 `model_path`/`webui` 字段）；`/slots` 再读实际 ctx 占用 |
| `:1234/v1/models` | LM Studio | OpenAI 形状 + 该端口默认无鉴权 |
| `:8000/v1/models` | vLLM | OpenAI 形状 |

发现的每一项给「一键添加为本地后端」按钮（预填 `scope=local`、`kind`、`base_url`），避免用户手抄端口。
⚠️ **端口全空时要显示"未检测到任何本地推理服务"并附各家启动命令**，不能只报"连接失败" —— 用户最需要知道的是"Ollama 装了但没起"这种状态（本机实测正是如此）。

**② 本地 AI 模型**
- 列表：名称、后端类型（Ollama / llama.cpp / LM Studio / vLLM）、`base_url`、状态灯、**实际上下文上限**、能力勾选（JSON Schema / 视觉 / tool calling）、是否默认。
- 按后端类型显示**不同的模型管理 UI**：Ollama → `/api/tags` 列表 + `POST /api/pull` 拉取；**llama.cpp → 只列 `/models`，无 pull**，改成"指定 `--models-dir` + 给 GGUF 下载命令 + 复制推荐启动命令行"；LM Studio/vLLM → 只列，不管下载。
- **上下文一栏要区分来源**：Ollama 显示"每请求可传 `num_ctx`（当前默认 16384）"；llama.cpp 显示"由启动参数 `-c` 决定，当前 XXXX —— 不够请重启加大 `-c`"。这条差异必须让用户看见，否则剧本解析失败时没人知道为什么。

**③ 云端 AI 模型**
- 同上但 `scope=cloud`；额外显示：鉴权方式、key 前 4 后 4 脱敏、**能力与限制提示**（如 RunningHub LLM API 标注"仅企业级-共享 key 可用，否则报 1014"）。
- 云端后端故障**不得连带拖垮本地路径**：任何 LLM 调用先按任务的 `llm_backend_id`，缺失则回落到同 `scope` 的默认后端，两个 `scope` 都不可用时才报错。

**④ 全局默认值**（放页面底部）：剧本解析 / 分镜拆解 / H3 提示词改写 / 向量检索 四个用途各自选默认后端与模型，并显示"选它跑长剧本会不会爆上下文"的预估提示。


**UI 要点**：
- 暗色为默认（视频工作台场景），沿用 manga-studio 的 `data-theme` 切换
- 1024px 以下显示"请用 PC 浏览器"墙（沿用 manga-studio 的判断：ComfyUI 场景本来是桌面）
- 长任务全部在队列页可关页面，关掉不影响
- 所有生成按钮显示预估耗时（用 `benchmark_render_times` 风格的滚动统计）
- 全局错误提示组件（manga-studio 的 `GlobalAlert`），但错误文案由后端返回（能说清"哪个节点的哪个控件不合法"），不再靠前端猜

---

## 9. 部署

### 9.1 目录

```
F:\H3\
├── apps/
│   ├── api/                    FastAPI
│   ├── web/                    React+Vite
│   └── pipeline/               draft_builder.py / h3_prompt_check.py 等子进程脚本
├── deploy/
│   ├── docker-compose.yml      db + api + web + cloudflared(可选)
│   ├── nginx.conf
│   ├── start-dev.ps1
│   ├── start-prod.ps1
│   └── cloudflared/
│       ├── config.example.yml
│       └── install-tunnel.ps1
├── data/
│   ├── media/<project_id>/...
│   ├── tmp/
│   ├── db/
│   └── vendor/pyJianYingDraft/
└── docs/
    ├── PLAN.md                    ← 本文件（现状在仓库根，建议移到这里）
    ├── LOCAL-LLM-TECH-REPORT.md   ← 技术报告·英文原文（现存，1440 行）
    ├── LOCAL-LLM-TECH-REPORT.zh.md← 技术报告·中文全译（现存，行号一一对应）
    ├── SETUP-COMFYUI.md
    ├── SETUP-OLLAMA.md
    ├── SETUP-LLAMACPP.md          ← GGUF 目录 + router 模式启动参数（v1.2 新增）
    ├── SETUP-RUNNINGHUB.md        ← 拿 key / cn vs global / proxy 与 Task API 两条路（v1.2 新增）
    ├── SETUP-TUNNEL.md
    └── MODELS.md

```

### 9.2 Windows 一键启动（开发/内网）

```powershell
# 前置：ComfyUI(≥0.30，建议 0.37.x)、Ollama(建议升 v0.34.x)、PostgreSQL16、ffmpeg、cloudflared(可选)
#   本机现状：ffmpeg 9.0.1 ✅ / Ollama v0.32.13 ⚠️(未运行) / ComfyUI ❌ / PostgreSQL ❌ / cloudflared ❌
.\deploy\start-dev.ps1
#  ① alembic upgrade head
#  ② uvicorn apps.api:app --host 127.0.0.1 --port 8788 --reload
#  ③ pnpm -C apps/web dev  → 5173，/api /ws 代理到 8788
#  ④ 首次运行走 /setup 创建 admin + 填实例地址
```

### 9.3 Docker Compose（生产/团队）

```yaml
services:
  db:    { image: postgres:16-alpine, volumes: [pgdata:/var/lib/postgresql/data],
           healthcheck: { test: ["CMD-SHELL","pg_isready -U h3"], interval: 10s, timeout: 5s, retries: 5 } }
  api:   { build: ./apps/api, environment: { DATABASE_URL, COMFY_DEFAULT_URL, LLM_DEFAULT_URL },
           depends_on: { db: { condition: service_healthy } },  # ← manga-studio 忘了写 healthcheck
           volumes: ["./data:/app/data", "C:/ComfyUI/output:/comfy_output:ro"] }
  web:   { build: ./apps/web, ports: ["8080:80"], depends_on: [api] }   # nginx
  tunnel:{ image: cloudflare/cloudflared, profiles: ["tunnel"],
           command: tunnel --no-autoupdate run --token-file /run/secrets/token }
```

⚠️ Windows 挂载 ComfyUI 输出目录用 `C:/ComfyUI/output:/comfy_output:ro`（WSL/路径转义坑）。

### 9.4 云端 ComfyUI 接法

**远端 GPU 主机（AutoDL / 恒源 / 4090 云主机）上：**
```bash
python main.py --listen 127.0.0.1 --port 8188
cloudflared tunnel login && cloudflared tunnel create h3-cloud
cloudflared tunnel route dns h3-cloud comfy.yourdomain.com
cloudflared tunnel run h3-cloud            # 或 --token-file
```
`config.yml` 记得 `originRequest.httpHostHeader: 127.0.0.1:8188`。

**本机设置页填：** `https://comfy.yourdomain.com` → 保存 → 探活（应看到 `object_info` 200 + `system_stats`）。

**同一套 `ComfyNativeClient` 代码，无任何分支。** 差别只在 `local_output_root`（云端为空 → 一律走 `/view` 下载）与 `placement`。

### 9.4.1 RunningHub 接法（几乎零成本获得一个云端 ComfyUI）

**最快路径（原生代理）** —— 设置页新建实例，`protocol=comfy_native`，`base_url` 直接填：
```
https://www.runninghub.cn/proxy/{你的apiKey}          # 24G
https://www.runninghub.cn/proxy-plus/{你的apiKey}      # 48G
```
不需要 Authorization 头（key 在路径里），我们现有的 `/prompt`、`/ws?clientId=`、`/history`、`/view`、`/upload/image` 全部照用。**batch 必须 =1。**

**生产路径（Task API）** —— `protocol=rh_task`，`base_url=https://www.runninghub.cn`，`site=cn`，鉴权按 `auth_style` 走（body `apiKey` + `Authorization: Bearer` 双发，`Host` 必须精确）。要 `workflowId` 的话，先在 RunningHub 网页里把工作流**保存并手动跑通一次**（否则 `810`）。

**两条路径的 key、额度、模型库共用同一个账号**，所以我们允许两种实例同时配置：调试用 proxy、批量用 task。

### 9.4.2 站点选择（cn vs global）

| | `www.runninghub.cn` | `www.runninghub.ai` |
|---|---|---|
| 注册 | 手机号 + 短信验证 | 邮箱 / Google |
| key、钱包、已上传素材 | **互不通用** | 同左 |
| 文档 | `runninghub-api-doc-cn` | `runninghub-api-doc-en` |
| CDN | 腾讯 EdgeOne/COS（广州+北京） | 同族但独立 |

→ 一个 `site` 一行实例记录，UI 上明确标注当前站与余额，禁止把一个 key 填到另一个站的实例上。


### 9.5 安全边界（必须写进文档与 UI 提示）

| 风险 | 缓解 |
|---|---|
| ComfyUI **无鉴权** | 远端 ComfyUI **绝不 `--listen 0.0.0.0` 暴露公网**；只绑 loopback，由 cloudflared 转发，域名加 Cloudflare Access（`required: true` + OTP） |
| `--enable-cors-header` 拆掉 loopback CSRF | 我们是服务端调用，**不需要开**。如果非要开，只允许我们的域名：`--enable-cors-header https://h3.yourdomain.com` |
| 我们的 API 被用来打内网（SSRF） | 实例地址**只有 admin 能配**，配完存进 `gen_instances`；`view_url` / `upload` 只接受该表里的 base_url 拼出来的 URL。**不提供任意 URL 转发接口**（manga-studio 的 `/api/ai-forward` 就是反面教材） |
| API Key 泄漏 | Fernet 加密入库；`/api/settings/*` 一律返回 `abcd****wxyz`；审计日志同格式；**绝不进前端 bundle、绝不进日志** |
| ⚠️ **RunningHub 的 apiKey 在 URL 路径里**（`/proxy/{key}/prompt`） | 这是新泄漏面：URL 会进 nginx/uvicorn access log、异常堆栈、`Referer`。必须：(1) 我们后端日志对 `/{32+位hex}/` 这类路径段做正则脱敏；(2) 前端永不接收完整 base_url，只收实例 id；(3) 我们的 `/api/media/*` 代理不透传 RH URL；(4) 出错时把 URL 里的 key 替换成 `<REDACTED>` 再落 `jobs.error` |
| 花钱型接口要防失控 | RH Task API 按秒计费 → 每日/每人消费上限（`user_quotas`）+ 余额低于阈值停止向该实例派发 + 仪表盘常驻余额。本地实例不受此限 |

| 媒体目录穿越 | 落盘路径由服务端生成 UUID 文件名；`/api/media/{id}/raw` 只按主键查，不接受用户路径 |
| 视频大文件 | Range 请求 + 边缘缓存头；超阈值拒绝上传 |
| 首次启动 | 强制创建 admin，弱密码拒绝；`JWT_SECRET` 首次自动生成并写 `.env`（0600） |

---

## 10. API 概要

```
# 认证
POST   /api/auth/login            → {access, refresh, user}
POST   /api/auth/refresh
POST   /api/auth/logout
GET    /api/auth/me

# 生成实例（protocol = comfy_native | rh_task；placement = local | cloud_self | cloud_runninghub）
GET    /api/instances?placement=&protocol=
POST   /api/instances                      (admin)  {name, protocol, placement, base_url, site,
                                                     apiKey?, instanceType?, retainSeconds?, ...}
PATCH  /api/instances/{id}                 (admin)
DELETE /api/instances/{id}                 (admin)
POST   /api/instances/{id}/probe           → 按 protocol 分派：
        comfy_native → {ok, system_stats, node_count, h3:{MiniMaxH3ImageToVideo:...}, missing_models[]}
        rh_task      → {ok, queue:{concurrentLimit,runningCount,queuedCount},
                        account:{remainCoins,remainMoney,currency}, apiKeyType}
GET    /api/instances/{id}/models?folder=diffusion_models    （RH task 无此路由 → 走 /openapi/v2/resource/list）
GET    /api/instances/{id}/object-info[/{class}]             （仅 comfy_native）
GET    /api/instances/{id}/cost                              → 累计 consumeCoins/consumeMoney/秒
POST   /api/instances/{id}/rh/import        {workflowId}    → getJsonApiFormat，导入库内工作流
POST   /api/instances/{id}/rh/app-schema    {webappId:int}  → apiCallDemo 的表单 schema
GET    /api/instances/{id}/rh/price-preview {route}

# LLM（scope = local | cloud —— 前端设置页就按这个字段分两块）
GET    /api/llm/backends?scope=local
GET    /api/llm/local-scan                 → 并发探 11434/8080/1234/8000，**按响应形状判别**，
                                             返回 [{port, detected_as, base_url, health, models_n, ctx_size?}]
                                             全空时返回 {found:[], hints:[各家启动命令]}
POST   /api/llm/backends                   (admin) {scope, kind, base_url, apiKey?, name}
POST   /api/llm/backends/{id}/probe        → {ok, models[], ctx_size, has_json_schema, has_vision,
                                             has_tools, ctx_is_per_request}
POST   /api/llm/backends/{id}/pull         仅 kind=ollama；llama.cpp 调用返回 400 并说明"请用 --models-dir + 手动下 GGUF"
GET    /api/llm/models?backend_id=
PUT    /api/llm/defaults                   {script_parse, storyboard, h3_prompt, embed} → 各 {backend_id, model}

# 工作流
GET    /api/workflows
POST   /api/workflows/import               multipart(json) → {workflow, report}
POST   /api/workflows/validate             {graph, instance_id} → {valid, errors[], warnings[],
                                             unknown_nodes[], runninghub_only_nodes[], missing_models[]}
GET    /api/workflows/{id}
PATCH  /api/workflows/{id}
DELETE /api/workflows/{id}
POST   /api/workflows/{id}/export?format=api|ui
POST   /api/workflows/{id}/vary            {slot, values[]} → [graph…]
POST   /api/workflows/{id}/test            → 提交 jobs[kind=workflow_test]
GET    /api/workflows/{id}/slots
GET    /api/workflows/{id}/node-overrides?instance_id=   → 把 slots 投影成 RH 的 nodeInfoList 骨架


# 项目 / 资产
GET/POST/PATCH/DELETE  /api/projects[/{id}]
GET/POST/DELETE        /api/projects/{id}/media
POST   /api/projects/{id}/duplicate

# 任务
GET    /api/jobs?project_id=&state=&owner=
POST   /api/jobs                          → 提交渲染（内部解析 workflow + slots）
POST   /api/jobs/{id}/cancel
POST   /api/jobs/{id}/retry
PATCH  /api/jobs/{id}                     {priority}
GET    /api/jobs/{id}/log

# 媒体
GET    /api/media/{id}/raw                （支持 Range）
GET    /api/media/{id}/thumb
GET    /api/media/{id}/download           （Content-Disposition）

# 导出
POST   /api/projects/{id}/export/merge
POST   /api/projects/{id}/export/jianying
POST   /api/projects/{id}/export/pack

# 实时
WS     /ws/task?token=&jobs=1,2,3
```

---

## 11. 里程碑

| 阶段 | 内容 | 验收 |
|---|---|---|
| **M-1 环境**（本机实测缺 3 件） | 装 ComfyUI 0.37.x + 下 H3 模型；起 Ollama（建议升 v0.34.x）；定 PG/SQLite；装 cloudflared（若自建云）；**注册 RunningHub 并拿到 apiKey** | `GET /object_info/MiniMaxH3ImageToVideo` 非空；`/system_stats` 200；`ollama ps` 有模型；**`GET https://www.runninghub.cn/proxy/{key}/object_info` 返回真实图**；用 curl 手跑一条 core-only 骨架出 5 秒带音频视频 |
| **M0 骨架** | 仓库结构、Vite+FastAPI 骨架、PostgreSQL + Alembic 全量迁移、登录鉴权、RBAC、审计 | 能登录、建项目、改密码 |
| **M1 连接层·原生协议** | `GenClient` 抽象 + `ComfyNativeClient`（HTTP + WS + 重连 + 事件归一化）、QueueDispatcher、`/ws/task` 进度 | 本地 ComfyUI 提交一张图、看到真实进度、刷新不丢 |
| **M1b 连接层·RunningHub** | `RunningHubTaskClient`（create/query/cancel/上传/落盘、5s 轮询、错误码→退避映射）+ `/proxy/{key}` 原生模式验证 + 余额与并发显示 + **URL 里的 apiKey 日志脱敏** | **同一份工作流图，分别在本机、RH proxy、RH task 三条链路都跑出结果**，且成本数字被正确记账 |
| **M1c 文本层** | `OllamaClient` + `OpenAICompatClient`（llama.cpp/LM Studio/vLLM/云端共用）、`/api/llm/local-scan` 自动发现、`ctx_is_per_request` 能力分支、NDJSON vs SSE 双解析 | 三个本地后端逐个跑通剧本解析；llama.cpp 上 `-c` 不够时**给出的是重启提示而不是静默截断** |
| **M2 工作流引擎** | UI↔API 双向转换、slot 抽取、导入向导（校验+缺失清单）、**slot→`nodeInfoList` 投影**、`getJsonApiFormat` 从 RH 库导入、`apiCallDemo` 自动生成表单、试运行、变体、导出 | 能导入 AIMixer / SequenceForge / 官方 example / **一个 RunningHub 库工作流**并成功跑通 |
| **M3 图片管线** | 剧本解析（本地 LLM 结构化输出）、资产页、Qwen-Image-2.1 t2i/edit、一致性 seed 策略（**RH 上必须显式注入 seed**） | 一段剧本 → 角色卡 + 场景图 + 分镜首帧 |
| **M4 视频管线** | H3 t2v/i2v/fl2v/r2v、Turbo、实例选择（本地/自建云/RH 24G/48G/84G）、失败重试、产物落盘（**外链约 1 天失效 → 立即下载**） | 5 秒视频出片，带音频，7:1 变体全通；本机和 RH 各出一条 |
| **M5 导演台** | 时间轴、拖拽排序、续拍、接缝诊断、关键帧首尾锁定、批量渲染、真实进度 | 6 镜短剧，一键全渲，不关页面 |
| **M6 导出** | ffmpeg 合成、剪映草稿、素材包、时间轴 JSON | 出完整 MP4 + 可在剪映打开的草稿 |
| **M7 硬化** | 多用户配额、**消费上限与余额告警**、审计 UI、媒体 GC、备份脚本、cloudflared 一键脚本、压力测试、文档 | 第二台机器能独立跑起来 |

### 11.1 本机已跑通的部分（2026-09-30 实测，不是计划）

| 项 | 实际状态 |
|---|---|
| M0 数据库层 | 嵌入式 PostgreSQL（`pgserver`，数据目录 `F:/H3/data/pgdata`，端口启动时分配、连接串自动写进 `apps/api/.env`）；Alembic `5a243df1fb4d_initial_9_tables` 落 9 张表 + `alembic_version`；PG 原生 enum `job_state/job_kind/user_role`；部分索引 `ix_jobs_pick` 专供派发 |
| 认证 | argon2id（t=3 / m=64MiB / p=2 / 32B 摘要）、HS256 JWT（密钥文件自动生成，不足 32 字节直接拒绝启动）、刷新令牌换发 + 二次使用即吊销全部会话、admin/editor/viewer 三级 |
| M1 队列 | `FOR UPDATE OF j SKIP LOCKED` 派发循环 + `instance_locks` 实例互斥 + 崩溃恢复 + 错误分类（退避重排 / 判失败 / 熔断）全部对真库跑通 |
| 端到端验收 | `apps/api/scripts/e2e_queue.py`：建 admin → 登录 → 建 editor/viewer → RBAC 门禁 → 实例落库 → 探活写回 → 入队 → 派发执行 → 产物转存 `media` + 磁盘 → `GET /api/media/{id}` 回读（含 Range 与 attachment）。`fast` 模式 3s 复验链路，`h3` 模式出真视频 |
| **H3 真实耗时** | 864×480、**56 帧（2.0s）**、Turbo 8 步，RTX 4090 24GB：**443s（7.4 分钟）**，产物 0.24 MB mp4。线性外推：124 帧（5s）约 16 分钟/镜。这是「预估耗时」与批量渲染排队提示的基准数据，也说明 1344×768 全质量档在本机不可用 |
| 测试 | `apps/api`：64 个用例（含 4 个 live 文件），`pnpm typecheck && pnpm build` 前端全绿 |

### 11.1.1 三类生成实测（2026-09-30 二次验收，`apps/api/scripts/e2e_generate.py`）

| 用途 | 参数 | 实测耗时 | 备注 |
|---|---|---|---|
| 文本 · 剧本拆解 | 507 字中文剧本 → `script_parse` | **30s**（预热）/ 167s（冷） | 输出 5 角色 / 2 场景 / 8 节拍，`json_schema strict` 守约 |
| 文本 · 分镜规划 | 上面的结构 + 目标 30s | **22s** | 4 镜、合计 25s；角色/场景名不发明 |
| 图片 · Qwen-Image 2.1 t2i | 1024²、25 步、cfg 1、euler/simple | **28s**（权重常驻）/ 257s（冷启动）/ 155s（与文本模型抢卡） | 产物 1.5 MB PNG |
| 图片 · 参考图换装（角色一致性） | 1 张参考图、`resolution=768`、`QwenImage21Cache=int8` | **23.6s** | 同一张脸/同一发髻金簪耳坠，换成枣红绣金云龙裙 —— 定妆与服装变体的主路径 |
| 视频 · H3 首尾帧（fl2v） | 864×480、56 帧（2.0s）、turbo 8 步 | **368s** | 产物 212 KB mp4，带音轨；比 §11.1 的 443s 基线略快（首尾帧同图时序列更短） |

**这一轮踩出来的三条新规矩：**

1. **一张 24 GB 卡装不下「27B 文本模型 + 图像/视频模型」，必须让位。** 实测让 llama-server（常驻 17.5 GB）与 Qwen-Image 栈（TE 16.7 + DiT 6.9 = 23.6 GB）同时在卡上时：一次带参考图的出图 25 分钟没走完一步，ComfyUI 连 HTTP 都不响应，而 llama-server 的 `/slots` 显示 `n_prompt_tokens_processed=0` —— 两边一起停在原地。处置写在 `app/gpu_arbiter.py`：本地实例开跑前停文本模型（先记下完整命令行，队列空了原样拉起），文本开跑前对 ComfyUI 调 `/free` 卸权重。可用 `H3_GPU_ARBITER=off` 整条关掉。
2. **显存闸门必须先看有没有活。** 曾经每 27 秒「停 llama-server → 拉起 → 再停」振荡：因为 `_vram_ok` 跑在 `_claim` 前面，空队列也去检查显存、不足就让位。现在 `_has_work_for()` 挡在前面。
3. **卸模型 ≠ 只让文本模型让位。** 上一单常驻的 Qwen-Image 权重本身就有 23.6 GB，会挡住下一单 H3；所以让位顺序是「先 `client.free()` 卸 ComfyUI，再停文本模型」。

三条踩出来的硬规矩（都已落到代码里）：

1. **非终态快照绝不能收口。** 对账循环每个 tick 都会把 `RUNNING` 喂进 `_settle()`，那时 `outputs` 天然为空 —— 不挡住就会把还在算的任务写成「succeeded + 零产物」，界面一片绿、时间轴全空。第一次端到端验收就是这么抓出来的（ComfyUI 仍在 `queue_running`，库里已经 succeeded）。
2. **「执行完成但零产物」是失败，不是成功，且不自动重试**（重跑一遍同样没文件，只是白烧 7 分钟显存）。判据写在 `queue._settle()` 与 `comfy_native._no_product()` 两处收口点。
3. **别拉全量 `/object_info`。** 964 个节点是几 MB，实例正在出片时必然撞 60s read 超时并导致任务被无意义重排；`missing_models()` 改成按 `class_type` 逐个拉小请求。

**2026-10-01 从 UI 复验（整套栈曾被闲置/重启全灭，重启后逐项再跑）：** 全部走浏览器点按钮，不经脚本。
- 文本：`生成 分镜脚本` 点下去 → `script_parse` 拆出 4 角色 / 2 场 → `storyboard` 出 4 镜（首尾帧提示词 677–680 字）写进 IndexedDB。
- 图片：角色卡「重新生成图片」→ 队列 → arbiter 停 llama → ComfyUI 出图 → media 65/66 落库并回挂 `character.refMediaIds` → `/api/media/{id}/raw` 回读 200 + Range 206。**冷启动首单被显存闸挡回一次、attempt 2 才成**（TE 16.7 GB staged 时只剩 ~3.7 GB 空闲），属正常，别当 bug。
- 视频：镜头抽屉先「生成」首帧（media 67）解锁「出片」→ H3 出片 → media 68（`video_00004_.mp4`，1.07 MB，`ftypisom` 头）→ 渲染完 arbiter 复原 llama。冷 H3 首帧出片约 **7 分钟**，同样 att=2。
- 内嵌浏览器后台标签节流 2s 轮询，长任务会错过终态卡在 `queued`；刷新页面触发 `reconcileFromServer` 即补挂（实测刷新后 shot 立刻 `completed`）。
- 合并成片：点「合并导出 DOWNLOAD MASTER」→ 后端 `ffmpeg -f concat -c copy` → media 69（`exports/…/浏览器实测-7187_….mp4`，1.07 MB，h264+aac、15.1s）→ 前端「合并结果」面板显示 copy 模式 + 合成 1 段 + 下载链。**这一跑揪出并修掉两个 500：** ① `routes_export._resolve` 用 `SELECT * FROM media` 配 `.scalars()` 拿到的是首列 int 而非 `Media` 实体，`m.id` 直接 `AttributeError` —— 改成 `select(Media).where(Media.id.in_(…))`；② `routes_jobs.media_raw` 把文件名原样塞进 `Content-Disposition`，合并片用项目名命名含中文 → HTTP 头 latin-1 编不了 `UnicodeEncodeError` —— 改成 RFC 5987（ASCII 兜底 `filename=` + 百分号编码 `filename*=UTF-8''…`）。`/download` 走 `FileResponse(filename=)` 由 Starlette 自己 quote，本来就没事。


---

### 11.1.2 H3 提示词四模式实测（2026-10-01，`apps/api/scripts/e2e_director.py`，模型 = llama.cpp Qwen3.8-27B-NVFP4-MTP-HIGH）

**先说前提，这条比数字重要：跑文本模型必须把 ComfyUI 整个停掉，`POST /free` 卸权重不够。**
只 `/free` 而 ComfyUI 进程还挂在卡上时，27B 的 131k KV cache 溢出到内存，实测 **0.66 tok/s**，
一次 hybrid 要一个多小时；停干净（`comfyui/stop.ps1`）再拉起 llama-server，才有 **14–41 tok/s**。

| 模式 | 输出预算 | 实测 | 结论 |
|---|---|---|---|
| `three_field` 三段式 | 1400 | **5.1s / 169 字** | 过校验，可直接出片 |
| `six_section` 官方六段式 | 3600 | **14.6s / 1774 字** | 过校验 |
| `wenwu` 中文导演分镜块 | 3000 | **40.0s / 516 token** | 内容达标（AU4-B、光位、呼吸、`<Subject 1>` 素材职责都写了）。但它把 `shotBlocks` 出成**每行一个数组元素**而不是每镜一个元素——拼装按行 join，两种形状等价，校验器已改成看合并后的文本 |
| `hybrid` 六段外壳 × 导演内容 | 4600 | **58.1s** | 八条生命通道逐条核对（形/场/视/力/念/息/言/续各一行）、`[Shot N]` + 时间码、AU4-C/AU17-B 全部写到位。**但 `constraints` 崩成 54 条 / 923 字**，还禁掉了自己刚写的慢推 → 提示词已加「5–10 条、不许自相矛盾」，校验器加了失控拦截（真机那份样本现在会被拦下）。**这条收紧还没真机复验** |
| `storyboard` 分镜规划 | 6000 | **14.9s** | 目标 24s → 3 镜 × 8s，景别/运镜全部落在新词表（中近景、手持、推近、静止），后端规模与时长自检**零警告** |

验收分两层，都能复跑：`check_director_pipeline.py` 用假传输跑真代码（不占显存，验 schema 选择、
方法论只注入两个用途、缺段报错文案、分镜规模警告）；`e2e_director.py` 跑真模型（自己让卡、自己收尾）。
另外记一笔环境事实：`app_settings.gpu_arbiter` 里那条可复现命令行**在恢复之后会被清掉**，
所以脚本不能长期依赖它复现 llama-server——真机验收第二遍时就没取到，只能按已知参数重新起。

---

### 11.2 五阶段功能对照（2026-09-30 还原后复审，取代当日的「代码审计结论」）

口径：**已实现** = 页面有入口且真的改到数据；**部分** = 有 UI 没落地逻辑；**未** = 代码里没这个概念。
标「实测」的项有当日跑通的证据（浏览器数据层 / `e2e_generate.py` / 队列产物回读）；其余为代码接通 + 页面渲染 + 写入 IndexedDB，未逐项再跑一次完整生成。
本表是上一版审计（当时绝大多数为「部分 / 未」）的对照翻新：manga-studio 截图里的页面与未截图功能均已落到「真」链路（IndexedDB + FastAPI + 本机 ComfyUI / llama.cpp），`VITE_USE_MOCK=false` 下不再有空路由。

| # | 功能 | 状态 | 证据 / 落地位置 |
|---|---|---|---|
| 1 | 智能剧本拆解 | 已实现（真·实测） | `script_parse` 回写 `project.data.script/characters/scenes`；浏览器点「生成分镜脚本」实测拆出 4 角色 / 2 场并落 IDB |
| 2 | 分镜规划（按目标时长出镜头表） | 已实现（真·实测） | `storyboard` 用途按 `targetDurationSec` 出镜头；实测 60s → 4 镜头，每镜带首/尾帧提示词（677–680 字）与 `h3Prompt` |
| 3 | 视觉化翻译（文字→画面提示词） | 已实现 | `buildKeyframePrompt` 客户端拼三段式；后端另有 `visualize` 用途；`prompts.ts` 15 风格 + 27 运镜指南 |
| 4 | 手动描述角色/场景/镜头 | 已实现（本地 IDB） | `PromptEditor` / `NewForms`，手填提示词直接进实体 |
| 5 | 项目配置 | 已实现 | `ProjectConfig` 补齐目标时长 / 语言 / 视觉风格 / 图片·视频模板 / 增强开关；`ConfigPanel` 全暴露 |
| 6 | 角色定妆 | 已实现（真·实测） | `characterRequest` → 队列出图 → `attachResult` 落 `character.refMediaIds`；实测 media 64 回挂并刷新后仍在 |
| 7 | 服装变体 | 已实现 | `WardrobeModal` 可增改 desc、`variationRequest` 出图、镜头 `variationByChar` 选变体 |
| 8 | 概念场景图 | 已实现（真） | `sceneRequest` → `scene.refMediaIds`；`SceneCard` 出图入口 |
| 9 | 图片上传 | 已实现（真） | `POST /api/media/upload`（multipart→media_root/uploads）+ `media.put`/`localStores.putUpload` 接通 |
| 10 | 批量生成 | 已实现（真） | `runBatch`/`generateBatch` → `POST /api/jobs/batch`，一次排多镜 |
| 11 | 镜头管理：网格 + 工作台 | 已实现 | `Director.tsx` 镜头网格 + `ShotDrawer` 详情抽屉 + `Timeline` |
| 12 | 关键帧生成（首/尾帧） | 已实现（真） | `keyframeRequest` 带 `:start`/`:end` 帧角色，`refFor` 把帧类型传进 `qwen_image_graph` |
| 13 | 上下文参考（场景+角色+妆容+变体） | 已实现（真·实测） | `characterIds`/`variationByChar` 有绑定 UI；参考图作为 `LoadImage` 进图（实测带参考图出图 23.6s） |
| 14 | 镜头分割（长镜拆子镜） | 已实现 | `splitShot.ts` + `parentShotId`/`subShotIds`，长镜拆子镜并继承上下文 |
| 15 | 视频生成 + 渲染日志 | 已实现（真·实测） | `h3_video_graph` 首尾帧 fl2v 走队列；实测 864×480/56 帧 turbo **368s** 出带音轨 mp4（§11.1.1）；`RenderLog` 读写 |
| 16 | 时间轴预览 | 已实现 | `Timeline.tsx` 按 `durationSec`/`frameCount` 画块，放真片缩略 |
| 17 | 逐段视频播放 | 已实现 | `PreviewModal` 内 `<video>` 走 `api.media.url`（Range 206 已验） |
| 18 | 渲染日志集中查看 | 已实现 | `RenderLogsModal` 读 `project.data.renderLogs`（含资源类型/模型/提示词），不再只写不读 |
| 19 | 资产下载 | 已实现（真） | 前端 `download()` 请求 `/api/media/{id}/download`；合并/打包产物 `adoptById` 回挂 |
| 20-23 | 角色/场景/关键帧/视频提示词统一管理 | 已实现 | `Prompts.tsx` + 资产管理页四类提示词统一编排，关键帧级提示词真实存在（`keyframe.visualPrompt`） |
| 24 | 资产搜索与筛选 | 已实现 | 资产管理页按角色/场景/镜头/状态检索；`AssetLibraryModal` 跨项目复用 |
| — | 资产管理独立页面 | 已实现 | `STAGES` 含「资产管理」，`/p/:id` 下独立路由 + 资产库弹窗 |

复审结论：上一版列出的「真实模式下会打空的路由」（`/api/llm/*`、`/api/workflows*`、`/api/styles`、`/api/system/storage|gc|gpu`、
`/api/projects/{id}/export/*`、`POST /api/jobs/{id}/retry`、`PATCH /api/jobs/{id}`、`DELETE /api/users/{id}`）现已全部实现并挂载（后端 54 条路由）。
三类生成当日均跑通：文本（5 用途，4–167s）、图片（文生图 + 参考图一致性，`e2e_generate.py` 复验）、视频（H3 fl2v 368s）。
单卡显存互斥由 `gpu_arbiter` 自动「让卡 / 复原」化解（§11.1.1 三条硬规矩）。**未逐项再验**的是各弹窗/分割/搜索的完整生成回归——它们代码接通、页面渲染、写 IDB 正常，但没有每个都再跑一次出图/出片。

### 11.3 UI 风格与演示账号

- **形态语言取自 manga-studio**（`reference/manga-studio`）：深色玻璃层 + 青/天渐变装饰色 + 大圆角
  （控件 10 / 面板 16 / 弹窗 28）+ mono 大写微标签 + 三层光斑底 + 48px 网格覆盖层。
  token 全在 `apps/web/src/index.css` 的 `@theme` 里，组件不写死颜色。
- **一条不能让步的规矩**：装饰色相（`--color-chrome*`）只服务界面本身；数据色相（`--color-mach-local/self/rh`）
  只回答「这块内容是哪个实例产出的」。manga-studio 通篇 cyan 是纯装饰，照抄会让「哪台机器、有没有在花钱」
  这个本项目最重要的信号失去颜色。
- 它是 Tailwind CDN + 无 token 层、无表格、无焦点样式、无响应式（<1024px 直接劝退）；我们保留 token 层、
  表格/时间轴、`:focus-visible` 与 reduced-motion，不把这几项对齐掉。
- **演示账号**：后端仅在「监听环回 + 库里一个账号都没有」时预置 `admin / 1234`，前端自动登录到工作台。
  口令策略在 `seed_loopback_admin` 里被显式绕过并有告警；`--host` 不是环回就不建号，必须走
  `POST /api/auth/bootstrap`。给团队用之前设 `VITE_DEV_AUTOLOGIN=false` 并改口令。

---

## 12. 已知风险与对策

| 风险 | 等级 | 对策 |
|---|---|---|
| H3 体积：**单底模可用集 42.5 GB**，加上角色一致性所需的 ref2va 双 UNET 共 **84.9 GB**，磁盘要留 120 GB；12GB 显存只能跑短片低分辨率 | 高 | 设置页按显存给"推荐配置档"；内置 0.4MP/864×480 预览档与 0.98MP/1344×768 全质量档（硬上限 768×1344 像素面积）；模型管理页显示每台机器缺哪些文件并给下载命令（ModelScope 优先） |
| UI→API 转换覆盖不全（proxyWidgets / 子图 / Autogrow） | 高 | 用 AIMixer 5 个 + SequenceForge + 官方 example 做回归测试集；转换失败**明确报错并给 node_id**，不做静默降级 |
| ComfyUI 版本漂移破坏契约（`/api` 前缀、`/object_info` 结构、WS 消息类型） | 中 | 启动时做**能力探针**（打几个关键路由 + 逐个探测 `MiniMaxH3ImageToVideo` / `MiniMaxH3SigmaShift` / `MiniMaxH3AddGuide`(0.34+) / `MiniMaxH3FunControlNetApply`(0.36+) 是否可用，据此裁剪可选功能）；`gen_instances.object_info_hash` 变了就重扫工作流；**H3 最低 0.30.0（PR #15224，2026-08-03 合入），本机目标装到当前最新 v0.37.4** |
| 官方 example workflow 落后于代码 | 中 | 只信 `/object_info`，不信 example；内置工作流由我们自己在当前版本重存 |
| Ollama `num_ctx` 默认 4096 静默截断长剧本 | 中 | 每次请求显式传 `num_ctx`；长剧本分块解析；设置页暴露"当前后端实际上下文" |
| 云端 ComfyUI 断连/超时 | 中 | WS 指数退避重连；`prompt_id` 落库可恢复；失败重试 3 次；UI 显式区分"ComfyUI 报错"与"网络断了" |
| 并发用户抢同一台 GPU | 中 | 每实例串行 + 每人并发上限 + 优先级队列 + 排队位置可见 |
| 剪映占文件导致草稿保存失败 | 低 | 照搬 skill 的 `PermissionError` 3 次 × 2s 重试 + 自动修复损坏草稿 |
| **RH 强制重置 seed** → 角色一致性/重绘完全不可复现 | 高 | 每次提交都把 `seed` 显式写进 `nodeInfoList`；job 记录里存实际使用的 seed；UI 上"锁定 seed"开关在 RH 实例旁标注"不注入就会被重置" |
| **RH 结果链接约 1 天过期** | 高 | 完成即下载到 `data/media/`，DB 永不存外链；下载失败要重试且任务不能标 succeeded |
| **RH 的 apiKey 在 URL 路径里** → access log / 异常堆栈泄漏凭据 | 高 | 日志与 `jobs.error` 正则脱敏路径段；前端只拿实例 id 不拿 base_url；不透传 |
| **RH `netWssUrl` 官方称"不稳定，不推荐"** → 不能靠它做进度 | 中 | 以 `/openapi/v2/query` 轮询为权威（5s/20min），WS 只当加速；进度条要能显示"平台不给百分比"这种降级态 |
| **RH 并发/余额类错误被当失败** | 中 | `421/1520/415/1003` → 退避重排队；`416/812` → 该实例熔断 + 告警，不再派发 |
| **RH `/proxy` 模式并发不支持** | 中 | 该实例 `Semaphore(1)` 强制；批量任务优先路由到 Task API 实例 |
| **cn / global 两站 key 与素材不通用** | 中 | `site` 是实例一等字段；探活失败时把"是不是拿 cn 的 key 连了 ai 站"列进可能原因 |
| **llama.cpp 上下文由启动参数决定**，请求无法提升 | 中 | `ctx_is_per_request=false`；检测不足时**报错并给可复制的重启命令**，不静默截断。剧本解析另有分块兜底（§7.1） |
| 8080 端口被非 llama.cpp 程序占用 → 误判 | 中 | 判别必须走 `/health` + `/props` 形状，不能只看端口通不通 |
| RunningHub 是付费外部依赖，可能改契约/涨价/停服 | 中 | 三条链路（本地 / 自建云 / RH）平权，任何一条挂了对应实例自动摘除、任务重排到其它实例；成本与用量常驻仪表盘；**核心功能必须能在只有本地 ComfyUI 时完整跑通** |

| 剪映草稿 schema 随版本变（v5.9+ `draft_info.json`） | 低 | 优先 `draft_info.json`，存在则不读 `draft_content.json`；`draft_meta_info.json` 缺失视为损坏并重建 |

---

## 13. 明确不做（v1）

- 移动端适配（沿用 manga-studio 的 1024px 墙）
- 内置视频播放器做多轨剪辑（交给剪映）
- ComfyUI 云端服务的付费 API（`comfy-mcp` 的 `partner_generate`）——只做自建/自租
- 内置 ComfyUI 一键安装（只给命令与清单）
- 视频二采/放大（`MiniMaxH3DirectorRefine` / `latent_upscale`）—— 预留 slot 与模型目录，M7 后再做
- 多人实时协同编辑（同一项目两人同时改会后写覆盖；先做"锁定/只读"）
- **RunningHub 的 390 个模型 API 端点**（`/openapi/v2/<family>/<variant>`，请求键是 `"84##value"` 这种 `nodeId##fieldName` 扁平映射，与 `nodeInfoList` 又是第三套形状）—— v1 只走工作流 API + 应用 API + `/proxy`，模型 API 最多在设置页留一个"连通性测试"入口，不进管线
- **RunningHub LLM API 不作为文本默认路径**（要企业级-共享 key，且无流式文档）；本地 Ollama/llama.cpp 是主，云端 OpenAI 兼容是备
- 剪映自动导出的 UI Automation 集成（只产草稿，不动它的鼠标键盘）


---

## 附：关键 API/常量速查

```python
# H3 帧数：合法值 = 17k+5
def h3_length(sec: float, fps: int = 24) -> int:
    n = max(5, round(sec * fps))
    return n + (5 - (n % 17)) % 17

# 采样基线（steps=25 是 SequenceForge/AIMixer 默认，官方模板出厂 4 步 Turbo）
BASE = dict(steps=25, sampler="res_multistep", scheduler="simple", cfg=1.0,
            shift_video=12.0, shift_audio=3.0, fps=24, length=124)
CANVAS = {"preview": (864, 480, 0.4),        # 官方模板默认
          "full":    (1344, 768, 0.98)}      # 硬上限 MAX_PIXELS = 768*1344

# Turbo：步数必须严格相等
TURBO = {"fl2v_8step": ("minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors", 8, "fl2va"),
         "fl2v_4step": ("minimax_h3_fl2v_turbo_4step_v1.0_768p_comfyui_bf16.safetensors", 4, "fl2va"),
         "ref2v_4step": ("minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors", 4, "ref2va")}

# ComfyUI 路由（**默认打无前缀**：新旧版本都有；/api/* 只在新版有）
POST /upload/image      multipart: image, overwrite, type, subfolder   （音频也走这里）
POST /prompt            {prompt, client_id, prompt_id?, extra_data?}
GET  /history/{prompt_id}   → {prompt_id: {outputs, status:{status_str, completed}}}
GET  /view?filename&subfolder&type&preview
GET  /object_info[/{class}]  未知返回 200 {}
GET  /system_stats      GET /models[/{folder}]    GET /queue
POST /interrupt  {prompt_id?}    POST /free {unload_models, free_memory}
# 以下三个是「原生 /api 路由」，只有新版有（老版本要退化成上面的 /queue /history）
GET  /api/jobs?status=&limit=&offset=&sort_by=&sort_order=
GET  /api/jobs/{job_id}    POST /api/jobs/{job_id}/cancel    POST /api/jobs/cancel
     ⚠️ 前缀复制逻辑会把原生 /api 路由再生成一份，于是有 /api/api/jobs 这种怪形状 —— 别依赖它
     ⚠️ WS **没有** /api 版本，永远是 /ws?clientId=
WS   /ws?clientId={uuid}   （POST /prompt 必须带同一 client_id）
     终止判据：{"type":"executing","data":{"node":null}}
     二进制帧：>I event(1 预览/2 未编码预览/3 TEXT/4 带元数据预览) + payload

# Ollama
GET  :11434/api/tags
POST :11434/api/chat  {model, messages, stream, format?, options:{temperature, num_ctx, seed}}
     format 可直接放 JSON Schema（官方 structured outputs）
POST :11434/api/embed  {model, input}     ← 官方 API 索引里只有 /api/embed；/api/embeddings 已从文档消失
     （旧端点"数组输入静默返回空"是社区说法，未找到官方依据 —— 我们只用 /api/embed 即可）
流式是 NDJSON（application/x-ndjson，每行一个 JSON，无 data: 前缀）
/v1/chat/completions 是 OpenAI 兼容层，用 OpenAI SDK 直连即可

# llama.cpp llama-server（本地第二个主力；kind=openai_compat，但 ctx 是启动参数）
:8080/health  :8080/props  :8080/slots  :8080/models  :8080/v1/chat/completions  /completion  /v1/embeddings  /v1/rerank
推荐启动（router 模式，一个进程挂多个 GGUF）：
  llama-server --models-dir D:/models/gguf --no-models-autoload --jinja \
               --host 127.0.0.1 --port 8080 -ngl 999 -c 32768
⚠️ 无 pull API；--jinja 不开没 tool calling；结构化输出用 response_format.json_schema
⚠️ 8080 是公共冲突端口，判定必须是 /health+/props 形状，不能只看端口通

# RunningHub（站点 cn=runninghub.cn / global=runninghub.ai，key·余额·素材互不通用）
## 协议 A：原生 ComfyUI 代理（key 在路径里，无 Authorization 头；batch 必须=1）
  https://www.runninghub.cn/proxy/{apiKey}          # 24G
  https://www.runninghub.cn/proxy-plus/{apiKey}     # 48G
  → 官方原话"功能等同于 http://127.0.0.1:8188"，/prompt /ws /history /view /upload/image 照用
## 协议 B：专有 Task API（apiKey 进 body + Bearer 双发，Host 必须精确）
  POST /task/openapi/create      {apiKey, workflowId, nodeInfoList:[{nodeId,fieldName,fieldValue}],
                                  workflow:<API格式图的JSON字符串>, instanceType, webhookUrl,
                                  usePersonalQueue, retainSeconds:10..180}
  POST /openapi/v2/query         {taskId} → {taskId,status,results:[{url,outputType,text}],
                                  failedReason,<-原始ComfyUI traceback>, usage}
  POST /task/openapi/cancel      {apiKey,taskId}
  POST /task/openapi/ai-app/run  {webappId:<int64!>, nodeInfoList, ...}
  GET  /api/webapp/apiCallDemo   ?apiKey=&webappId= → 机器可读表单 schema（fieldType/fieldData/default）
  POST /api/openapi/getJsonApiFormat {apiKey,workflowId} → data.prompt = API 格式图（导入通道）
  POST /openapi/v2/media/upload/binary  multipart 'file'（Bearer only，返回 message 不是 msg）
       → {type,download_url(~1天失效!),fileName,size}   单文件 30MB 上限；无 /upload/audio
  POST /api/openapi/getLoraUploadUrl → 预签名COS PUT；只有 RHLoraLoader 认它
  GET  /openapi/v2/queue/status  → concurrentLimit/runningCount/queuedCount（文档标"开发中"）
  GET  /uc/openapi/accountStatus → remainCoins/remainMoney/currency
# 状态枚举恒为 QUEUED|RUNNING|SUCCESS|FAILED；轮询 5s、上限 20min；netWssUrl 官方称"不稳定不推荐"
# 错误码：801免费账号拒 803 nodeInfo不匹配 810 工作流未保存/未跑过 415/421/1520 并发满(退避)
#         416/812 余额不足(熔断) 1003 限流 1014 非企业共享key 435 instanceType错 437/433 校验失败
# ⚠️ API 调用会强制重置 seed → 每次都要显式注入 seed
# ⚠️ 结果 URL 约 1 天过期 → 立即下载落盘，DB 不存外链

```

---

## 附：调研产物

- `docs/LOCAL-LLM-TECH-REPORT.md` —— Ollama / ComfyUI on Windows / LLM 后端抽象的完整技术报告（**英文原文**，1440 行）
- `docs/LOCAL-LLM-TECH-REPORT.zh.md` —— 上面这份的**中文全译**（1440 行，行号一一对应，代码与命令保持英文）
- ⚠️ 这两份都在本次会话中被移动/新增过：`LOCAL-LLM-TECH-REPORT.md` 原本在仓库根目录，被自动过程移进了 `docs/`（内容未改）。要恢复原位告诉我。

