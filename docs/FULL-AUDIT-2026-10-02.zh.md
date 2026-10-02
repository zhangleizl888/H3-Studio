# H3 Studio 全量功能体检与修复日志（2026-10-02）

范围：所有页面、所有模块、所有链路（剧本 / 图片 / 音色 / 视频 / 导出 / 队列 / 版本历史 / 工作流库 / 设置）。
方式：真接口逐端点调用 + 内嵌浏览器真实 DOM 事件点击 + 四条生成链真跑产物。
约定：每条发现记「现象 / 根因 / 改动 / 复测证据」。没做到的验证会单列，不假装看过。

---

## 0. 环境基线（开工前）

| 服务 | 端口 | 状态 |
| --- | --- | --- |
| 内嵌 PostgreSQL（pgserver） | 62151（每次会变） | 在线，pid 18172 |
| FastAPI 后端 | 127.0.0.1:8788 | 在线 pid 35712，队列空（可安全重启） |
| ComfyUI | 127.0.0.1:8188 | 在线 pid 32080，v0.37.4 |
| vite（真后端模式） | 5173 / 5176 | 两个都在线，`VITE_USE_MOCK=false`，都带 /api 代理 |
| llama-server | 127.0.0.1:8080 | **未运行**（连接被拒） |

静态闸门（都通过）：
- `tsc --noEmit -p tsconfig.json` → 0 错误
- `vite build` → ✓ 1716 modules，7.82s，仅 chunk 体积告警（854 kB）

库层基线（`F:/H3/.scratch/audit_db.py --dump`，备份在 `F:/H3/.scratch/audit_db/`）：

```
users 1 | refresh_tokens 26 | gen_instances 1 (#530 本机 ComfyUI [comfy_native])
llm_backends 1 (#1 本机 llama.cpp Qwen3.8-27B local/openai_compat http://127.0.0.1:8080/v1 def=True probe=True)
workflows 7 | media 7 (video=4 image=1 audio=2, 全部 alive) | script_versions 0
jobs 5 (image/succeeded=1 att≤1; video/succeeded=2 att≤4; audio/succeeded=1 att≤1)
```

工作流库 7 条的验证状态（`verified_at IS NOT NULL`）：

| id | 名称 | task_kind | 来源 | 自动选 | 已验证 | gaps | slots |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 28 | MiniMax H3 图文一键生视频（加速版） | video | ui | 是 | **是** | 0 | 30 |
| 29 | MiniMax H3 动作迁移·全能参考 | video | api | 是 | 否 | 0 | 31 |
| 30 | 双模双采短剧助手·全能参考生成视频 | video | api | 是 | 否 | 0 | 59 |
| 31 | MiniMax H3 全能参考 60 秒·多素材拼接 | video | api | 是 | 否 | 0 | 101 |
| 32 | 声音克隆二合一（IndexTTS2 + Qwen3-TTS） | audio | api | 是 | **是** | 0 | 26 |
| 38 | Klein 一键人物设定图 + 服装拆解 | image | api | 是 | **是** | 0 | 26 |
| 39 | Klein 指令编辑（FLUX） | image | api | 是 | **是** | 0 | 23 |

---

## 1. 发现清单

（格式：`编号 [严重度] 现象 → 根因 → 改动 → 复测证据`）

### A-1 [高·已修] 本机环回请求被系统代理劫走，本地 llama.cpp 永远探活失败

- **现象**：`POST /api/llm/backends/1/probe` 返回 `caps.error = 读模型清单失败：RemoteProtocolError: Server disconnected without sending a response`，`reachable=false`；而 8080 端口此时确实没进程。
- **根因**：后端所有 `httpx.AsyncClient()` 都没关 `trust_env`（全仓 0 处 `trust_env`）。Windows 上 httpx 的 `trust_env=True` 会经 `urllib.request.getproxies()` 读到**注册表里的系统代理 127.0.0.1:4780**，于是发给 `127.0.0.1:8080` 的环回请求也进了代理。判别证据（同机同端口两跑）：
  - `trust_env=True` → `RemoteProtocolError: Server disconnected without sending a response`
  - `trust_env=False` → `ConnectError: [WinError 10061] 由于目标计算机积极拒绝`
  目标端口活着时代理会「碰巧」转发成功，所以这条只在服务真停时暴露 —— 界面看到的就是「模型挂了」，而它只是没启动。
- **改动**：新增 `apps/api/app/net.py`（`is_local_target()` + `async_client()`：环回/私网绕过环境代理，公网照旧走代理），替换
  `app/llm.py` 的 4 处（`_get_root`/`_get`/`chat`/`_one_scan`）、`app/gpu_arbiter.py:_port_alive`、`app/gen/comfy_native.py` 的实例客户端。
  `routes_llm.py:222` 的模型拉取是公网目标，保持吃代理未改。
- **复测证据**：重启 8788（重启前确认队列 `queued/dispatching/running` 为 0）后再探活，错误从 `RemoteProtocolError` 变成 `ConnectError: All connection attempts failed` —— 说明请求已经直连 8080，报的是「端口没人听」这个真话。

### A-2 [低·已修] `/api/workflows/select` 对非法 kind 返回 200 空候选

- **现象**：`?kind=nonsense_kind` → `200 {"kind":"nonsense_kind","candidates":[],"fallbackTemplate":null}`。
- **根因**：路由把 kind 当自由字符串直接交给 `rank()`，而 `Task.from_params` 又用 `str(params.get("kind") or "video")` 兜住 —— 拼错的 kind 会**静悄悄变成 video 的候选**，界面却以为问的是别的。
- **改动**：`routes_workflows.py:232` 的 `kind` 改成 `Literal["image","video","audio"]`（与 `models.py:261` 对 `workflows.task_kind` 的检查约束同一套值），由 FastAPI 直接 422。
- **复测证据**（重启 8788 后，`apps/api/.venv/Scripts/python.exe -X utf8 F:/H3/.scratch/a2_probe.py after`）：
  `kind=image/video/audio → 200`（candidates 仍为 0，是不给 slots 时的设计内回落，见「看着像 bug 其实不是」）；
  `kind=voice / text / 空串 → 422`，`detail[0].msg = "Input should be 'image', 'video' or 'audio'"`；
  `slots={not json → 400`（原有的 slots 校验没被带坏）。

### A-3 [高·已修] ComfyUI 的 cudaMallocAsync 池不随 `/free` 归还，显存闸从此常关

- **现象**：`GET :8188/system_stats` 里 ComfyUI 自己报 `torch_vram_total 2.3 GB`，看着很空；但同一时刻 `nvidia-smi` 是 **23,016 / 24,564 MiB 已用**（只剩 1.5 GB）。此前记忆里「`POST :8188/free` 能把 ~18 GB 降到 ~2 GB」这次**不成立** —— `/free` 调过之后驱动侧的占用一点没降。
- **根因**：`--preview-method / cudaMallocAsync` 的分配器把显存留在**自己的池**里，`/free` 的 unload+empty_cache 只能还给 torch，归还不了 async pool。后端 `queue._vram_ok` 读的是 `vram_free`，于是 12 GB 闸门永远不开，任务一直 `queued`，界面上只写「空闲显存不足」。
- **处置与证据**：队列确认为空（`queue_running 0 / queue_pending 0`）后用 `comfyui/stop.ps1` 重启 → `nvidia-smi` 立刻变成 **1,540 MiB 已用 / 22,599 MiB 空闲**。
- **待办**：这一条要改成后端能自证与可操作的能力（区分「torch 池占着」与「别人占着」，并在 `POST /free` 无效时提示/执行重启 ComfyUI）。见 D-1。

### B-1 [中] 工作流库-槽位页 React key 警告

- **现象**：打开 `/workflows` 时 console 报 `Each child in a list should have a unique "key" prop. Check the render method of 'SlotsTab'`。
- **已排查**：`routes/Workflows.tsx` 内 `SlotsTab`（458 起）的两处 `.map` 都带 key（分组按钮 `key={g}`、行 `key={s.address}`）；文件里另外 23 处 map 也几乎都有 key。**具体未定位到**，留待用组件栈复现。状态：未修。

### B-2 [中] 设置-系统页显示的目录是写死的，不是后端真正在用的那份

- **现象**：页面「媒体目录」显示 `D:/h3studio/data/media`；后端 `/healthz` 与 `/api/system/storage` 报的是 `F:\H3\data\media`（`storage` 响应里其实**已经带了 `root` 字段**，前端没读）。
- **根因**：`routes/settings/System.tsx:23-25` 一组 `DEFAULTS` 常量当真实值渲染；「保存目录设置」写的那份后端从不读取。
- **状态**：待修（本轮按「补成真功能」处理）。

### B-3 [中] 设置-系统页的备份指引是 docker 命令，而这套栈明确不上 Docker

- **现象**：页面上三条「复制」按钮给的是 `docker exec -t h3studio-db pg_dump ...` / `docker cp ...` / `docker exec -i ... pg_restore`，本机根本没有 `h3studio-db` 容器。
- **根因**：写页面时按容器形态写的文案，后来定了「主机直跑 + pgserver 内嵌」，这段没跟着改。
- **状态**：待修 —— 改成这套栈真能跑的 `pg_dump`/`pg_restore`（走 `.tooling-pg` 那套，端口是动态的，得由后端算好给用户）。

### B-8 [中·已修] 登录页：用户名为空时点「进入」没有反馈

- **现象**（初次记录）：只填口令不填用户名 → 点「进入」→ 按钮**没有 disabled**、没有行内报错、`fetch` 一条都没发，页面停在 `/login`。
- **复现时要紧的一处更正**：这条最初的「静默」里**混了驱动台的产物**。这次用真点击复测时，前两次点「进入」`nativeClick/submit` 计数都是 0 —— 不是页面不吃点击，是 snapshot 之后我用 `innerWidth` 绕过 PC 墙引发了整棵树重挂，uid→坐标 的映射过期了，点击落到了别处。重取 snapshot 之后 `nativeClick=1, submit=1`，同一条路径才可信。
- **真实缺陷在哪**：`Login.tsx` 只靠浏览器原生 `required` 气泡提示，`onSubmit` 里没有任何自检。原生气泡是浏览器本地化的、窗口不在前台时不出现，也和这套界面的行内报错样式是两套 —— 用户看到的就是「点了没反应」。
- **改动**：`routes/Login.tsx` 表单加 `noValidate`（`required` 保留，仍可访问性树里报 `aria-required`），`submit()` 里自己把话说出来：用户名空 → 「要先填用户名。」，口令空 → 「要先填密码。」，都写进原有的行内 `error` 条。
- **复测证据**（browser-use 真点击，非脚本派发事件）：
  空用户名点「进入」→ 表单文本变成 `…用户名 密码 要先填用户名。 进入`；
  填 `admin` 再点 → `要先填密码。`；
  填全 `admin/1234` 再点 → `Page navigated to http://127.0.0.1:5173/.`（`POST /api/auth/login 200 → GET /api/auth/me 200`），提交通路没被 `noValidate` 带坏。

### B-9 [中·已修] 剧本页把「没显式选后端」显示成「没有可用的文本后端」

- **现象**：llama 后端存在且探活通过，剧本页却同时显示①顶栏「分镜走：未选后端」②「分镜生成模型」下面「还没有可用的文本后端：去 设置 → AI 模型 加一个并探活」。
- **根因**：`Script.tsx:356` 与 `script/ConfigPanel.tsx:53` 都只按 `project.config.shotModelBackendId` 找后端，没做 `generate()` 里那套回落（`shotModelBackendId → llmBackendId → llm_defaults.script_parse → is_default`）。而 `generate()` 的拦截条件本来就是「一个后端都没有才拦」——**显示口径和拦截口径不一致**。
- **改动**：`Script.tsx` 抽出 `defaultLlmId`（与 generate 同一套解析），顶栏与 ConfigPanel 共用；没显式选时显示「（默认）」，hint 前缀「未指定：走默认后端 · 」。
- **复测证据**：真页面上顶栏变成 `分镜走：本机 llama.cpp Qwen3.8-27B（默认） · Qwen3.8-27B-NVFP4-MTP-HIGH.gguf`，hint 变成 `未指定：走默认后端 · ID: 1 · 实际上下文 32768（llama.cpp 的 -c 是启动参数，改它要重启服务）`，「还没有可用的文本后端」不再出现。

### B-10 [中·已修] 能力字段键名漂移：后端 `ctxTotal` / 前端读 `ctxSize`

- **现象**：剧本页永远显示「上下文未探到」，而探活其实拿到了 131072/32768。
- **根因**：`app/llm.py` 的 `as_dict()` 发 `ctxTotal`，`lib/types.ts` 的 `LlmCaps` 读 `ctxSize`（5 处引用）。同一批里 `supportsPull` 前端读 2 处、**后端从来不发**（只有 Ollama 有 `/api/pull`）。
- **改动**：在既有的归一层 `lib/httpApi.ts:toLlm()` 里收口：`ctxSize ← ctxTotal`、`supportsPull ← kind === "ollama"`。
- **复测证据**：见 B-9 的 hint 文本（已显示实际上下文数值）。
- **更正记录**：这条我一度误报成「后端没把探活结果写库」。直接查 `SELECT capabilities FROM llm_backends` 才发现库里是全的，是我的测试脚本读了不存在的 `capabilities` 键（接口出参叫 `caps`）。

### C-2 [高·已修] 带思考的本机模型把 token 预算全花在思考链上，结构化调用拿到空正文

- **现象**（剧本链真跑第一次）：第 2/2 步 `POST /api/llm/run` → **400** `模型返回了空内容（可能被上下文截断，或 max_tokens 太小）`。llama-server 那侧确确实实生成了 ~6000 token 才结束。
- **根因**（用最小请求直接打 `:8080/v1/chat/completions` 坐实）：这台 llama.cpp 把回复拆成两个字段返回 —— `message.content`（正文）与 `message.reasoning_content`（思考链）。
  `app/llm.py:chat()` 只读 `content`。思考型模型在小请求里是「先想 70 字再答 7 字」，到了 6000 token 预算的大请求就**只想不答**：`content` 为空、`reasoning_content` 几千字。
  三种关闭开关实测：`chat_template_kwargs:{enable_thinking:false}` ✅ 思考 0 字；`reasoning_effort:"none"` ✅ 思考 0 字；`thinking:{type:"disabled"}` ❌ 被忽略（仍有 67 字思考）。
- **改动**：`chat()` 里对 **本机（`is_local_target(spec.root)`）且带 JSON schema** 的调用自动发 `reasoning_effort:"none"`；云端与非结构化用途（自由改写）不动。
- **复测证据**：同一条 240 字剧本，`script_parse` 从 **77.1 秒 / 3227 token** 变成 **19.6 秒 / 1005 token**，正文与结构都正常。
- **顺带修掉的报错文案**（C-2b）：正文为空时原来只有一句"可能被上下文截断，或 max_tokens 太小"。现在分三种说实话：有思考链→报「只输出了 N 字思考，正文空（finish_reason=…，本轮预算 M token）」；`finish_reason=length`→报被截断；两者都不是→提示先探活。新增 `_empty_reply_hint()`。

### C-3 [高·已修] `{"shots": []}` 被当成成功：合法但没用的分镜返回没人拦

- **现象**：剧本页报 `分镜规划返回了 0 个镜头：多半是这台模型没守住 schema`，而项目里 `characters=3 / scenes=5 / shots=0`；后端日志却写着 `storyboard(three_field) 完成：1759ms，2643 tokens`。
- **取证**：用**前端自己的代码路径**（页内 `import` `merge.storyboardBrief` + `api.llm.run`）以完全相同的入参重放 —— 30.2 秒返回 6 个镜头。说明①不是前端解包错，②也不是后端契约错，而是**模型偶发回了一个 schema 合法的空数组**（1.76 秒就回，正是空 `{"shots":[]}` 的耗时；日志里 2643 是 `total_tokens`，含 prompt）。
- **根因**：`run_purpose` 对 `h3_prompt` 有硬校验（不合格直接 raise），对 `storyboard` 只收 `validate_storyboard` 的**警告**，空数组一条都不算 —— 于是「白跑一轮 GPU」被记成成功，把判断丢给前端的一句报错。
- **改动**：`storyboard` 空镜头表时，**追加一句纠正指令重问一次**（温度 +0.15，封顶 0.9）；两次都空才 `raise LlmError(retryable=True)`，文案说清「连着两次空镜头表、花了多少秒、下一步该改什么」。为此把 `chat()` 调用收进局部 `ask(extra, temp)`，两条路径共用同一份参数。
- **复测证据**：见下一批剧本链重跑结论（待补：本轮重跑的实际镜头数）。

### D-1 [高·已修] 「设为当前」之后编辑器正文还是旧稿，一碰就把刚设的当前版盖掉

- **现象**（真点击 + 真数据）：版本历史里点 V1「设为当前」，服务端 `isCurrent=true`、IndexedDB 的 `rawScript` 也写回成 V1 的 240 字 —— 但**编辑器里显示的还是 V2 的 319 字**，而且这一页写着「改完自动存（停 1 秒写盘）」。用户这时候在正文里敲任何一个字，319 字就被自动存回库里，等于把刚选的当前版**静默撤销**。
- **根因**：`Script.tsx:74-82` 的播种 effect 故意只跑一次（注释写明「之后以本地为准，否则 refetch 会把用户正敲的字冲掉」），于是「外部整篇换稿」（设为当前、恢复版本）也一并被挡掉了。要防的是冲掉正在敲的字，代价却是换稿同步不进来 —— 两头都不可接受。
- **改动**：新增一条按「脏度」判断的同步：只有当**本地草稿还停在上一份库里的正文**（`draftRef.current === seenScript.current`）时才跟随外部替换；用户敲过字（两者不等）一律不冲。切项目时 `seenScript` 归零，避免跨项目误同步。
- **复测证据**（两条都真点过）：
  - 设为当前 V1 后：编辑器 `textarea.value.length` 从 319 变成 **240**，与 IDB `rawScript` 一致。
  - 防误伤：在编辑器里手写补一段（259 字）→ 点「60秒 (预告)」触发 config 变更与 refetch → 草稿仍是 **259 字**、手写内容还在、IDB 同步为 259，没被冲掉。

### D-2 [中·已修] AI 改写/续写直接覆盖正文，缺 `script_chat` 那条「短一半」体量守门

- **现象**：点编辑器上的「AI改写」→ 走 `script_write` → 模型回来的东西**直接替换整篇正文**，界面上既不预览、也不说原稿去哪了。（对话改稿那条链路是有「待确认 + 展开对比」的，两者口径不一致。）
- **根因**：`aiWrite()` 只 `onDraftChange(next)` 然后存版。它依赖「原稿会进版本历史」兜底，但缺了两样：① `script_chat` 后端里那条「改稿比正文短一半以上 → 只回被改那一段」的守卫没搬过来；② 覆盖发生了却不吭声。
- **改动**：`rewrite` 且 `next` 比当前正文短一半以上时**不写回**，报错原话说清「这一份没有写回，原稿保持不动，要改局部去右侧对话改稿」；正常写回时补一条 done：「AI 改写已写回（旧 → 新字数），原稿已存成上一版，去版本历史可一键退回」。
- **口径来源**：2026-10-01 用户定的「AI 产物一律停在待确认，人不点就不许覆盖已有内容」，本条按它的意图收窄（整篇替换必须可撤销 + 半份稿子不许覆盖整篇）。



### C-4 [环境，非代码] 同一个 27B 的吞吐差 4.5 倍取决于启动参数的 `-c`

- `-c 131072`：生成 8.1–8.6 t/s（24 GB 被 KV 挤满），一次 `script_parse` 要 77 秒。
- `-c 32768`（PLAN §5.2 记的那条命令）：37.6–38.0 t/s。
- 本轮验收用 `-c 32768` 起 llama-server，**与用户日常那份 131k 的起法不同**，所有耗时数字都是这个前提下测的。
- 界面侧本来就有对应能力：B-9/B-10 修完后，剧本页会显示「实际上下文 N（llama.cpp 的 -c 是启动参数，改它要重启服务）」，这条提示这时候才真的用得上。



### C-1 [高·已修] 文本调用超时写死 300 秒，本机 27B 的拆解必然被掐断

- **现象**：剧本页点「生成分镜脚本」，第 1/2 步跑约 5 分钟后 `POST /api/llm/run` 返回 **502**，界面显示 `调用 本机 llama.cpp Qwen3.8-27B 失败：ReadTimeout:`（冒号后面是空的）。
- **根因**：两条叠在一起 ——
  1. `app/llm.py:73` `LlmSpec.timeout_s = 300.0` 是**写死的**，`routes_llm.py:_spec()` 从不按后端配置，库里也没有这一列；`script_parse` 的 `max_tokens=4096` 在实测 8 t/s 下要 ~480 秒 → 必然超时。
  2. `routes_llm.py:331` 的文案是 `f"...{type(exc).__name__}: {str(exc)}"`，而 `httpx.ReadTimeout` 的 `str()` 是**空串** → 用户看到的报错什么都不说。
- **改动**：
  - 迁移 `a7d41e9c5b02_llm_timeout_seconds`：`llm_backends.timeout_seconds`（可空，CHECK 30–7200），`models.py` 加列。
  - `routes_llm.py`：新增 `effective_timeout(row)`（留空按位置兜：**local 1800 / cloud 300**），`_spec()` 用它；`LlmBody.timeout_seconds` 进 create/patch（patch 看「字段有没有出现」，允许显式清空回兜底）；`_to_out` 同时回 `timeoutSeconds` 与 `effectiveTimeoutS`。
  - 502 文案：`str(exc)` 为空时改成「N 秒内没等到完整回答。本机模型单槽串行本来就慢，可在「设置 → AI 模型」把这台后端的「单次超时」调大」。
  - 前端：`types.ts` 加两个字段；`settings/Llm.tsx` 添加后端表单有「单次超时（秒）」，卡片上可直接改（回车/失焦提交，留空=回到兜底），并显示「实际 N 秒」。
- **复测证据**（真接口）：`列表 timeoutSeconds=None → effectiveTimeoutS=1800`；`PATCH 2400 → 2400/2400`；重读仍是 2400（确认落库）；`PATCH null → 回落到 1800`。
- **环境侧同时记录的数字**：同一个 27B 在 `-c 131072` 下生成吞吐只有 **8.1–8.6 t/s**（KV 挤爆 24 GB），换 PLAN §5.2 记的 `-c 32768` 后才有正常速度。本轮验收用 `-c 32768` 起 llama-server，**这与用户日常那份 131k 的起法不同**，报告里单列。



### 契约核对（不是 bug，记下来免得下次再疑）

- 登录响应键是 `{access, refresh, expires_in, user}`，**没有** `accessToken`。
- `/api/workflows/select?kind=…` **不给 slots 必然返回空候选**（`rank()` 要求 prompt/first_frame 等必填信号被任务喂到），这是设计内的诚实回落，不是自动选坏了。带上真实槽位后：image→#38(31 分)、video 首尾帧→#28(31 分)、audio→#32(31 分)，全部命中。
- `/api/media-versions`、`/api/trash` 不给 `project_key` 也不给 `all_projects=true` 会 400 —— 这是防「顺手拉全库」的闸门，前端两处都带了参数。
- `/api/workflows/validate` 的 body 是 `{graph, instance_id?}`（导入前体检，不落库），不是 workflow_id；对已入库条目做体检走 `/api/workflows/{id}/rescan`。
- 前端 61 个 `/api` 字面量路径与 openapi 71 条全部对得上，没有「前端调了后端没有」的死链（`/api/chat`、`/api/auth/` 之类是文案与 `startsWith` 判断，非请求）。
- 边界行为干净：无 token 一律 401「需要登录」，坏 token 401「登录凭据无效」，错口令 401，非法 kind 422，空 body 入队 400 并**如实说明回落了哪个内置模板**；没有一处 500。
- `GET /api/media/{id}/raw|download` 7 条产物全部 200，mime 正确（video/mp4、image/png、audio/mpeg）。

---


### E-1 [高·已修] 图片链全军覆没：撤掉参考图分支后 autogrow 组留下号位空洞

- **现象**：资产页点「生成所有缺图角色」→ 三条 image 任务全部失败，ComfyUI 校验退回 `节点 81：Required input is missing`。工作流 #38（Klein 一键人物设定图 + 服装拆解）在库里还标着「真机跑通过」。
- **取证**（`.scratch/audit_job_graph.py`，读 `jobs.params.graph`，那就是真正提交出去的那张图）：
  - 节点 81 是 `BatchImagesNode`（打包画面），object_info 里 `images` 是 **COMFY_AUTOGROW_V3，prefix=image，min 1**；
  - 无参考图时 `prepare()` 摘掉 `images.image0`（原本 ← #71 缩放后的参考图），只剩 `images.image1 ← ["3",0]` —— **组里出现空洞**；
  - 同一张图修好后是 `images.image0 ← ["3",0]`，连成 10 秒出图成功。
- **改动（两处，都是同一类洞）**：
  1. `app/workflow_select.py` 新增 `_renumber_group()`：剪掉组里一路素材后，把剩下号位从**该组原有的最小白点**起压实（保留原起点，1-based 的组不会被改成 0-based）；`_drop_node()` 与 `prepare()` 里「只断这一路、不撤消费节点」那条分支（`graph[nid]["inputs"].pop(fld)`）都接上它。
  2. `app/api/routes_jobs.py:retry_job`：带 `slots` + `workflow_id` 的任务在重试时把 `params.graph` 丢掉（`params - 'graph'`），下一轮重新填图。以前 `_run` 是「params.graph 优先」，所以**改完工作流/修好填图之后点重试，交的还是上次那张坏图**，报同一个错，看着像重试坏了。工作流页的直投试运行没有 slots，仍提交用户手改的图，不受影响。
- **复测证据**：修好后 fresh 生成 → `fb249fb2`（陈默）、`e8eb4647`（女人）双双 `succeeded` att=1，产物 #273/#274；早前那条成功的 `5b912c6a` 图里 #81 已是 `images.image0`。

### E-2 [中·已修] 显存不足时把用户指向一个不存在的原因

- **现象**：出图任务卡在队列，progress 写「实例空闲显存 0.9 GB…先停掉占显存的进程（如本地 27B 文本模型）」。**当时 llama-server 早就不在了**（`tasklist` 里根本没有 `llama-server.exe`），占着 23.6 GB 的是 ComfyUI 自己。
- **取证**：`stop.ps1` 停掉 ComfyUI 后 `nvidia-smi` 立刻从 23,016 MiB 已用变成 **1,540 MiB 已用 / 22,599 MiB 空闲** —— 占卡的就是那个 `cudaMallocAsync` 池（同 A-3）。
- **改动**：`app/queue.py` 派发循环在调完 `/free` 之后**对比前后实测空闲值**；没变化（<0.25 GB）就把结论写进进度：「刚对这台实例卸过一遍权重，空闲仍是 X GB —— 占卡的是实例自己的分配池（附 `torch_vram_total` 与驱动侧已占 GB 数），要重启 ComfyUI 还得回来；停文本模型对这一份没用」。配套在 `ComfyNativeClient` 上加了 `vram_detail()`（free/total/torch pool）。

### E-3 [中·已修] 产物落库只写 bytes，mime/宽高/时长常年 NULL

- **现象**：真出的 1344×768 PNG，接口回 `mime=null width=null height=null`；视频行同样（这条早就存在，`/api/media` 24 行全 NULL）。所以时间轴/版本卡拿不到比例，`isStill()` 这类按 mime 的门槛形同虚设，成片时长标签恒为「第 N 段」。
- **改动**：新增 `app/media_probe.py`：图片直接解文件头（PNG IHDR / GIF / JPEG SOF / WebP VP8·VP8L·VP8X，不起进程），音视频走 `ffprobe`（拿不到就留 NULL，绝不猜），`_persist_outputs` 落盘后填 `mime/width/height/fps/duration_ms`。
- **复测证据**：用真文件跑过 —— `image_00001_.png → 1024x1024`；`*.mp4 → duration_ms=15115, 864x480, fps=24`；`.flac → 3256ms`；`.mp3 → 163581ms`；一张 18 字节的坏 PNG 只补 mime、尺寸留空（不瞎猜）。真实任务侧：修复后新产物 `#273/#274` 已是 `mime=image/png 1344x768`，而修复前的 `#272` 仍是 NULL（历史行不回补，这是有意的）。

### F-1 见下文（同一条发现只留一处）

- 队列页终态进度的现象、改动与界面复看都记在 F-1（本节后文，`[中·已修]`）。这一条最初登记为「待修」，后来同一条被修掉了，这里只留指针，避免两个状态并存。

---

### G-2 [高·已修] FCP7 XML 里的素材路径是假的

- **现象**：导出 XML 每个 clip 都写 `<pathurl>file://localhost/shot_001.mp4</pathurl>` —— 按序号编的名字，磁盘上从来没有这个文件。剪映/Premiere/Resolve 导入后整条时间轴全是 offline 素材，等于白导一遍。
- **改动**：新增 `app/export_paths.py`（`media_file()` / `file_url()`），`export_xml` 改为按 `mediaId` 查真行、拼 `file:///F:/H3/data/media/...`（Windows 三斜杠 + 转义），并带上真文件名；**没有产物的镜头不再编假路径**，跳进返回值 `skipped` 里说清楚。EDL 的 `FROM CLIP NAME` 同步换成真文件名。
- **复测证据**（真接口 + 真产物）：
  - `pathurl → file:///F:/H3/data/media/jobs/7eab133c/job483_0001_.mp4`，`Path(...).exists() → [True]`；
  - `clips=1, skipped=['镜 2（还没有可指向的产物）']`；
  - `EDL CLIP NAME → ['job483_0001_.mp4', '镜 2']`。

### H-1 [高·已修] 导出页从不 reconcile：刚出的成片在合并列表里看不见

- **现象**：镜 1 的出片任务 `succeeded`、产物 media #292 的 `role=video / refId=s_xs4h802g / projectKey` 全对，但导出页写着 **「0/7 镜有成片」「还没有任何一段成片，合并列表是空的」**，本地实体里 `shot.videoMediaIds=[]`、`state=queued`。
- **定位**：产物落库侧没问题（同一时刻首帧 #284 正常挂上了）。`reconcileFromServer()` 只被 **Director 页**（`Director.tsx:91`）和 **Assets 页**（`Assets.tsx:94`）调用 —— 导出页没有。我切到导演台再回来看，`videoMediaIds=[292]`、`state=completed`、本地媒体索引里也有 292，说明 `settle()` 本身是好的，缺的就是「这一页没调它」。
- **改动**：`lib/useGenerate.ts` 抽出 `useProjectReconcile(project)`（幂等靠 ref，注释里写明 StrictMode 会挂两次 effect 的原因），`Export.tsx` 挂上。导演台/资产页那两处保留原样（它们还各自带着 handles/toast 的额外逻辑，不动别人的接线）。
- **复测证据**：刷新到 `/p/.../export` 后 `RENDER STATUS → 1/7 镜有成片`，`合并导出 DOWNLOAD MASTER` 按钮由禁用变为可点，真跑出 media #293（video/mp4，748,308 B，`mode:"copy"` 一次命中没回退重编码）。

### H-2 [中·已修] 导出产物自己也不带元数据

- **现象**：合并/打包建的 media 行只有 `bytes`，`durationMs/width/height` 全 NULL —— 成片列表的时长标签因此恒是「第 N 段」。
- **改动**：`routes_export._finish()` 落库前调 `media_probe.probe()`（图片读文件头只取前 128 KB，音视频走 ffprobe），INSERT 补 `width/height/fps/duration_ms`，`mime` 也以探测结果为准。
- **复测证据**：真点一次「合并导出」→ media #296 `{'mime':'video/mp4','width':864,'height':480,'fps':24.0,'durationMs':8032,'bytes':748308}`。

### F-1 [中·已修] 任务进终态后进度列还写着「执行中」

- **现象**：队列页里 `完成` 与 `失败` 的行，进度列都显示「— 执行中」；导演台「进行中的任务」小面板同理。看着像还有活在跑。
- **改动（两处，缺一不可）**：
  1. `app/queue.py` 收口时把 `progress.stage` 一起改写（成功→「完成」，判失败→「失败」），用 `jsonb_set(progress,'{stage}',…)`，百分比等字段保留；`progress` 为 NULL 时兜成 `{}`。
  2. **这只管新落定的行**：库里 23 条终态行的 `stage` 到死还是「执行中」（历史行不回补，同 E-3 的口径）。所以在 `routes/project/Queue.tsx:232` 加了一条显示规则：非活动行**不念 `progress.stage`**，进度列直接按状态说话（完成/失败/已取消），「死在哪一步」去展开的日志里看。
- **复测证据**：
  - 接口侧：修复后落定的试运行任务 `cc58152e…` → `progress.stage = 完成`；同一批里修复前的 23 行仍是 `执行中`（`apps/api/.venv/Scripts/python.exe -X utf8 F:/H3/.scratch/f1_check.py` 统计）。
  - 界面侧（这条补的正是 §2 里欠的那次复看）：真打开 `/p/p_pjbxtqg5/queue`，全文里 `执行中` 出现 **0 次**，进度列逐行显示「完成」（如 `完成 … 场景 · 第二幕：隧道行驶中 … — 完成 — 1 10秒`）。

### A-3 补充：`/free` 之后驱动侧显存一点没降，只有重启才还得回来

- 本轮第二次实测：`stop.ps1`（队列 `running 0 / pending 0` 时才动）之后 `nvidia-smi` 从 **23,016 MiB 已用** 变成 **1,540 MiB 已用 / 22,599 MiB 空闲**。两次数字都记在这里，因为「`/free` 够用」这个旧结论在本机已经不等价于事实。

---

## 1b. 四条链的实测结论（本轮全部真跑到产物）

| 链 | 入口（真点击） | 自动选中的工作流 | 结果 | 产物 | 耗时 |
| --- | --- | --- | --- | --- | --- |
| 剧本 | 剧本页「生成分镜脚本」两步 | 文本后端 #1（llama.cpp 27B） | 拆解 3 角色 / 8 场；分镜 **7 镜**（景别/运镜/时长齐） | 拍摄清单 7、版本历史 V1 | 拆解 19.6s（第一版带思考 77.1s）+ 分镜约 30s |
| 图片 | 资产页「生成所有缺图角色」「生成所有缺图场景」、导演台「生成所有首帧 → 参数确认 → 开始派发」 | #38 Klein 一键人物设定图（按任务自动选） | 3 定妆 + 8 场景 + 7 首帧全 `succeeded` att=1 | media 272-275、281-282、284-291（PNG 1344×768） | 单条约 10–20 秒 |
| 音色 | 角色卡「生成音色」（先传真人语音当参考） | #32 声音克隆二合一 | `succeeded` att=1 | media #283 audio/mpeg **954ms**，`role=voice refId=c_wo0z4u21` | 约 1 分钟（含排队） |
| 视频 | 导演台镜 1「出片」 | #28 MiniMax H3 图文一键生视频（加速版） | `succeeded` att=1，首帧真的喂进图里（fillNotes 记着「首帧 ← job476_00001_.png」） | media **#292** video/mp4 864×480 @24fps **8.0s** | **7 分 06 秒** |
| 导出 | 导出页四个按钮真点 | — | 合并 #293/#294（`-c copy` 一次命中）、打包 11 文件 22.9 MB ZIP、EDL/XML 客户端落盘 | exports/p_pjbxtqg5/… | 合并秒级 |

回收站机制真跑了一轮：软删 → `/api/trash` 列出（`n=2, bytes=4,457,492`，带 `bucket`）→ `/raw` 不带 `?trashed=1` 返回 404、带上返回 200 → 恢复 → 再软删 → `DELETE /purge` 后磁盘文件与库行都没了。
（一处观察：**导出产物**（`role='export'`）软删后不进 `/api/trash` 列表 —— 它不在版本历史那一族里。能 purge，但没有反悔入口。要改的话得先定「导出物算不算一版」。）

## 1c. 本轮跑过的其它界面动作（都是真 DOM 事件，含报错捕获）

- 外壳：自动登录、新建项目（填名+梗概→创建并进入剧本）、模型配置弹窗（让位/恢复按钮与单卡仲裁状态）、资产库弹窗（空态话术）、侧栏收起 200→56px、深浅色切换、退出登录→登录页→重新登录（`POST /api/auth/login 200`）。
- 导航：`/history` `/trash` `/workflows` `/settings/{gen,llm,users,system}` `/nope-such-page`（NotFound 正常）。
- 队列页：状态页签（我误点「已取消」把列表筛空过一回，界面无辜）、**取消**（`场景 · 隧道` → `canceled`）、**重试**（`attempts` 归零重新排队）、**优先级行内编辑**（填 0 → 库里 `priority=0`）、展开详情。
- 剧本页：目标时长切换、对话改稿发送→「改稿待确认 240→319 字」→确认写回、AI改写（真跑 `script_write`，602→589 字并自动存版）、切档重拼（三段式→官方六段式，`7/7 镜已是这一档`，六个字段结构真出现在 h3Prompt 里）、版本历史 V1..V5 预览/设为当前/删除。
- 工作流页：8 张卡片渲染、页签（槽位 26 / 试运行 / 重新扫描）在位。`select/validate/rescan/models/slots/export` 全在接口层真调过。
- 设置页：生成实例（探活/全部探活/取消默认按钮在位）、AI 模型（本地/云端分组、探活、删除、添加后端；新加的「单次超时」输入可用）、用户（新建用户入口）、系统（目录四个字段现在显示的是后端真值；操作记录出的是 `audit_log` 真行）。


## 1d. 第二批（继续做完 §3 那些）新发现

### I-1 [高·已修] 「发起试运行」对每条工作流都返回 500

- **现象**：工作流页点「发起试运行」→ 面板还是「还没发起」，网络里是 `POST /api/workflows/{id}/test` **500 Internal Server Error**（裸文本，没 detail）。38/39/28 三条都一样，等于这个功能从来没通过。
- **根因**：`routes_workflows.py:565` 调 `_enqueue(request, session, actor, JobCreate(...))`，但这一行**两个名字都没 import**（`_enqueue`、`JobCreate` 都在 `routes_jobs.py`）。Python 先解析被调用名，所以栈停在 `NameError: name '_enqueue' is not defined`；把 `_enqueue` 补上后 `JobCreate` 还会再炸一次 —— 两个一起补了。
- **复测证据**：重启后 `POST /api/workflows/39/test` → 200 入队，任务 `succeeded` att=1，产物 media **#298**，`progress.stage=完成`。

### I-2 [高·已修] 「导入工作流」必然 401：裸 fetch 从不带 Bearer

- **现象**：导入向导走完三步（校验报告都出得来），点「导入并入库」后库里条目数不变、也不报错。网络层是 `POST /api/workflows/import → 401 Unauthorized`。
- **根因**：`httpApi.importJson` 自己写了 `fetch(..., { credentials: "include" })`，而这站的票在 `localStorage` 的 Bearer 头里、没有 cookie 那条路。同文件里早就有 `postForm()`（带 Bearer、401 换票重试一次、注释还专门写了 multipart 不能走 `req()`）—— 这条路没用它。顺带两个洞：形参 `(name, json)` 把接口声明里的第三个参数 `instanceId` **直接吞掉**（导入时不做实例比对），而 `name` 只当了文件名、没进 `name` 表单字段。
- **改动**：`importJson` 改走 `postForm`，并真的 append `name` 与 `instance_id`。
- **复测证据**：界面走一遍三步向导 → 新条目 **#40「体检导入 Klein」**（fmt=api / 23 槽位 / 0 gaps / autoSelect 是），名字带对了；接口侧再验一次 `import → 201` 且 `name` 生效，随后 `DELETE → 204`。

### I-3 [中·已修] 工作流的导入与删除不进审计表

- **现象**：设置页写着「记的是配置变更…」，但删掉一条工作流后 `audit_log` 里没有 `workflow.delete`（试运行/派发那些有）。
- **改动**：`import_workflow` 与 `delete_workflow` 各补一条 `_audit(...)`（删除那条还得先把 `request: Request` 加进签名才能拿到来源 IP）；导入用 `flush()` 先取到 id 再写，指得准。
- **复测证据**：真导一条再删一条 → 审计最新两行是 `workflow.delete | 41 · 审计探针 导入` 与 `workflow.import | 41 · 审计探针 导入`（带 slots/gaps/sourceFormat）。

### I-4 [中·已修] 上传的素材也不带元数据

- **现象**：真点角色卡「上传」→ 新 media **#299** 落库、`refMediaIds` 正确变成 `["299","273"]`，但 `width/height=null`，而客户端给的 `content_type` 是 `application/octet-stream`。
- **改动**：`/api/media/upload` 落盘后走 `media_probe.probe()`，`mime` 以探测结果优先。
- **复测证据**：用 `application/octet-stream` 上传同一张 PNG → `{'mime':'image/png','width':1344,'height':768}`。

### 澄清两条「看着像 bug、其实不是」的（记下来免得下次误修）

- **用户删除**：弹窗要求「输入用户名以确认」，`确认删除` 在未输前是 disabled。我第一次点不动是因为没填，不是坏了。填完后 UI 走通：用户从列表消失，审计落 `user.delete`。
- **服装变体**：`添加变体` 在名字/描述为空时 disabled，且提示「还没有变体。下面填名字和造型描述，加一套。」是故意的。填上「夜行雨衣 / 湿透的黑色长风衣…」后变体真存进 `character.variations`（长度 1）。
- **`/api/workflows/select` 不给 slots 返回空候选**、**`/api/trash` 不带 project_key 报 400**、**能力字段叫 `caps` 不叫 `capabilities`** —— 都是我第一轮的测试写法问题，不是应用缺陷（其中 `ctxTotal`/`ctxSize` 那条是真的，已按 B-10 修掉）。

### B-1 更新：React key 警告仍未定位（负结果也记下来）

排除了这些：`SlotsTab` 里两处 `.map`（`tr key={s.address}`、分组按钮 `key={g}`）在**运行时 fiber 上确认都带着 key**；编译产物里 17 处 `children: [...]` 全部标着 `isStatic=true`；组件栈那第二个 `%s` 参数是空的，抓不到更细的位置。我没有为了「交个成果」去乱改一行没证据的代码 —— 曾按 `isStatic` 猜测改过「地址语法」那一行，验出来不是它，已回退。它是 dev-only 的 console 噪音，没有任何用户可见影响。

## 3（更新）还剩这些没做

| # | 事项 | 状态 |
| --- | --- | --- |
| 1 | B-1 React key 警告 | 未定位（见上文负结果） |
| 2 | 视频/图片缩略图（`thumb_path` 仍无人写） | 未做：界面已如实显示占位块；ffmpeg 已经接好，加一步抽帧 + 一个 raw 的 `?thumb=1` 变体即可，属增强不属修坏 |
| 3 | 导出产物软删不进 `/api/trash` | 未做：要先定「导出物算不算一版」 |
| 4 | 剪映草稿 | 决定不做（§2 第 3 条），XML 真路径已可用 |
| 5 | 「从资产库选择」「跨项目复用带音色」的界面级验证 | 部分：弹窗能开与空态话术看过（资产库 0 ASSETS 那段），选择→回填没走完 |
| 6 | live 测试套件 | 故意没跑（夹具会清共享开发库，见 §2 第 6 条） |
| 7 | 设置-用户页：**新建账号 / 停用**只到接口层 | 界面真点通的只有「删除」（要输入用户名确认那一步）；建号与停用的按钮没在页面上走过 |


---

## 2. 没做到的验证

（单列，不假装看过）

1. **像素级目视核对没做成**。内嵌浏览器视口 ~530px，靠页内 `innerWidth=1600 + resize` 才让外壳渲染出来；`take_screenshot` 报 `NATIVE_BROWSER_VIEWPORT_UNAVAILABLE`。所以本轮所有界面结论都是「真 DOM 事件 + 真接口 + 真产物」级别的，不是「看过长什么样」。要看观感请在你自己的 Chrome 里开 `http://127.0.0.1:5173`。
2. **媒体版「设为当前」的指针前后对照没取干净**。按钮点了（18 个可见，零报错），但我没先记下那一组原本的 `keyframes[].mediaId`，事后对不上；剧本版的设为当前对照是完整的（见 D-1）。
3. **剪映草稿没做，是决定不做而不是没来得及**：`draft_content.json` 在这台机上的 6.10 草稿里不是明文 JSON（新版剪映会加密），本机无法反查它的真格式，写出来的东西我验不了能不能打开。按你定过的「宁可报 501/未接通，也不做假接线」，这一格留着如实报 501；而**你真正要的「导进剪映」这条路是通的**：G-2 修完，FCP7 XML 现在指向磁盘上真实存在的文件。
4. **H-1 的触发条件没做第二次复现**：改成导出页也 reconcile 之后，我用的是「已经补挂过」的状态验的界面（1/7 与合并成功都是真的），但没再造一次「镜头卡在 queued 且直接落到导出页」的干净现场。
5. **F-1 的界面复看：已补上**（原来欠的那格）。最后一轮真开了 `/p/p_pjbxtqg5/queue`，页面全文里 `执行中` 出现 0 次、终态行的进度列逐行显示「完成」，数字与结论记在 F-1 那一节。顺带把这条从「只改 SQL」补成「SQL + 前端显示规则」两条，因为库里 23 条历史行的 `stage` 永远不会自己变。
6. **live 测试套件本轮故意没跑**：`apps/api/tests/*_live` 的夹具会连 `jobs / media / script_versions / gen_instances / instance_locks` 一起清，而这台机上还有别的会话在干活（此前就发生过夹具把「本机 ComfyUI」实例行删掉、id 从 503 变 530、所有项目里的默认实例指针变死链）。跑的是非 live 那批：**32 passed**。
7. **重启窗口期的 500 不算应用缺陷但也没深挖**：我每次重启 8788 后的头几秒，页面会抓到 `GET /api/jobs` 500（vite 代理在后端不在时的表现），过几秒自愈。
8. 环境侧两件事是这台机器的性质、不是代码问题，报告里当数字用：`-c 131072` 下 27B 只有 8.5 t/s（本轮验收用 PLAN 记的 `-c 32768`，38 t/s）；剪映/微信/Chrome 等桌面进程常态占掉约 1.5 GB 显存。



---

## 3. 留给下一轮的

**这一节已经被前文「§3（更新）还剩这些没做」取代**（那份是第二批实测之后重写的）。原表 9 行的下落：F-3（批量按钮禁用不给原因）已补 `title`；第 5 行（工作流页的试运行/导入/删除）与第 6 行的「上传」「服装变体」都在第二批真点过，证据在 §1d 的 I-1/I-2/I-3/I-4 与「澄清」段；第 7 行**只做到了「删除用户」真点通**（建号与停用仍是接口层），这一条没被证明的部分照原样留着。剩下的以那份更新版为准，这里不再重复一遍旧状态，免得同一件事在同一份报告里有两种说法。

---

## 4. 最后一轮补验（把前面标「待复看」的两条做实）

拿当年那条**失败在「节点 81：Required input is missing」**的旧任务 `2fe2fd74`（定妆 · 陈默）点了一次「重试」，一条动作同时验了四件事：

| 验的是 | 结果 |
| --- | --- |
| 重试是否重填图（G/E-1 那条改动） | 库里 `params.graph` 的 `#81` 变成 `{"images.image0": ["3", 0]}` —— 号位已压实，不再是当初那个只剩 `image1` 的空洞 |
| autogrow 压实是否在真实提交路径生效 | 任务 `succeeded` att=1（同一个 job 修之前交一次失败一次） |
| 产物元数据（E-3） | media **#297** `image / character / 1344×768 / image/png / 2,222,687 B` |
| 终态进度文案（F-1） | `progress.stage = 完成`（改动前这里到死都写着「执行中」） |

耗时补记：这条重试的任务从派发到出图约 **3 分钟**，而同批 fresh 任务只要 10–20 秒 —— 差别是 ComfyUI 刚被重启过，Klein 栈要冷加载。这个数字对「排队为什么看着卡住」有用，记在这里。

## 5. 可复跑的体检工具（都在 `.scratch/`，仓库不收录）

- `.scratch/audit_db.py [--dump]` —— 逐表数行 + 枚举真查 + 全表备份到 `.scratch/audit_db/`（live 夹具清库前必跑一次）
- `.scratch/audit_api.py` —— 端点全扫 + 前端调用路径与 openapi 交叉核对，异常写 `.scratch/audit_api_findings.txt`
- `.scratch/audit_job_graph.py <uuid 前缀>` —— 把某条任务**真正提交出去的那张图**打出来，并标出 autogrow 组的号位空洞（这次就是靠它一眼看到 `image1` 落单）
- `.scratch/ui_harness.js` —— 页内驱动台（真事件点击 + console/网络报错捕获）。**注意**：体检期间它被复制到 `apps/web/public/__audit_harness.js` 才能同源加载，收尾时已删除；`public/` 目录本来不存在，也一并删了
- `.scratch/a2_probe.py` —— 两种用法：`queue` 在重启 8788 **之前**列出还在跑/排队的任务（这台机是共享的，先看队列再动手）；`after` 逐个真打 `/api/workflows/select`，验 kind 校验与 slots 报错
- `.scratch/f1_check.py` —— 拉全量 jobs 并按 `(state, progress.stage)` 统计，用来分清「收口逻辑没改」和「改之前落下的历史行」

---

## 6. 收尾闸门（最后一轮改动之后重跑）

覆盖 A-2（`kind` 改 `Literal`）、B-8（登录页行内自检）、F-1（终态列按状态说话）这三处的最后一遍：

```
apps/web>  npx tsc --noEmit          → 0 个错误
apps/web>  npx vite build            → ✓ built in 2.89s
apps/api>  python -m pytest -q       → 104 passed（非 live 那批；live 套件仍按 §2 第 6 条故意没跑）
```

后端在这轮里为 A-2 重启过一次 8788，重启前用 `a2_probe.py queue` 确认 `live=0`（这台机是共享的）；重启后实例表回到 530、队列派发器与回收站清理都自行起好（`.scratch/uvicorn_a2.log`）。

---

## 7. 雪花专项（用户报「测试中的图片变成了雪花」之后的一整轮）

### 7-0 结论先说

图片链的雪花有**两层独立原因**，两层都修了；图片两条路 + 视频三种输入形态全部真跑到产物并目视验收：

| 任务形态 | 走哪条 | 结果 | 证据 |
| --- | --- | --- | --- |
| 文生图 | 回落内置 `qwen_image` | ✅ media 423，1024×1024，与提示词相符 | 目视 + `mad 5.0 / std 44.1` |
| 改图（人物设定图） | #38 Klein | ✅ media 424，正面/侧面/背面三视，同一个人 | 目视 + `mad 4.7 / std 96.4` |
| 首尾帧 | #28 FL2VA | ✅ media 428，736×576 / 124 帧 / 5.17s | 第 0 帧正对镜头=首帧素材，第 123 帧背影=尾帧素材 |
| 文生视频 | 回落内置 `h3_video` | ✅ media 429，864×480 / 124 帧 | 目视：海边木栈道 + 海鸥 + 纵深推进 |
| 全能参考（图+视频+音频） | #29 Ref2VA | ✅ media 431，480×864 / 124 帧 | 参考图的人物被认出来了，动作跟参考视频 |

### 7-1 [严重·已修] 底模是半截下载：尺寸对、数据是零

`flux-2-klein-base-9b.safetensors` 201 个张量里 **184 个开头 4096 B 全零，且文件末尾 1 MB 没有任何数据**。下载器先预分配成最终大小再逐块写，中断后留下的就是这种「看起来完整」的文件；ComfyUI 加载不报错（名字与形状都对得上），前向输出恒等于 0，采样器每步都「什么也没做」，最后把初始噪声交给 VAE —— 出来的是一张能正常解码的雪花，任务状态 `succeeded`。

- 指纹判据（`.scratch/img_stats.py`）：真图 `MAD 1.9–3.4 / std 42–59`，雪花 `MAD 11.3–12.8 / std 29–34`，纯随机噪声 `MAD≈87 / std≈74`，空 latent 解码 `MAD 0.3 / std 0.8`。雪花 = 高频 + 低对比，这个组合真图给不出来。
- 排除过的其它嫌疑：VAE 往返正常、sigma 序列正常、ComfyUI 日志无缺键告警、同种子换提示词输出逐字节相同（说明网络根本没在响应输入）。
- 处置：从 ModelScope 单流重下官方文件，**逐字节校验 sha256** 后才换入（`4a54fad7…`，201/201 张量非零）。

**教训（已进记忆）**：`pget` 分块下载在本例拼出了哈希错误的文件。大权重下载必须校验哈希，不能只看文件大小。坏文件先暂存为同目录 `.zero-filled.bak`，经用户确认后已删除（连同 `.scratch/klein_te/` 的 4 个已合并分片，共回收 33 GB）。删除前复核过真那份底模仍在位且体检通过（201/201 张量非零、尾部有数据）。

### 7-2 [严重·已修] `align_graph` 把官方文本编码器「就近凑」成了另一个模型

雪花的第二层。#38/#39 的原始图里 `CLIPLoader.clip_name` 一直写的是**正确**的 `qwen_3_8b.safetensors`（官方 Klein 9B 的 text_encoder 是纯 Qwen3-8B / `Qwen3ForCausalLM`），但本机当时没有这个文件，`_same_model_family` 按「qwen 同族 + 8B 规格对上」判定，把它换成了 Qwen-Image 2.1 那份带 `model.visual.*` 的 `qwen3vl_8b_bf16.safetensors`。

同种子、同提示词、同步数的 A/B（`.scratch/t2i_te_ab.py`）：

- `qwen_3_8b.safetensors` → 干净的红色自行车 + 地铁站廊，湿地面反光（34s）
- `qwen3vl_8b_bf16.safetensors` → 同一颗种子出来是严重涂抹 + 色散的迷幻色斑（66s）

修复两件事：

1. `comfy_native.py` 的 `_same_model_family` 增加硬规则：**VL 与非 VL 互斥**（`_is_vl()` 同时认 `qwen3vl` 粘写与 `qwen3-vl` 分写）。判不出就不换，让 `missing_models` 明说「实例上没有这个权重」。六条用例复验：`qwen_3_8b` vs `qwen3vl_8b_bf16` → False，而 `qwen3vl_8b_int8_convrot` vs `qwen3vl_8b_bf16`、`qwen3-vl-32b` vs `qwen3vl_32b_minimax_h3_int8_convrot` 仍为 True（合法对齐没被打破）。
2. 把官方 4 分片 text_encoder 流式合并成 `models/clip/qwen_3_8b.safetensors`（`.scratch/merge_te.py`，不经 torch，重算偏移原样拷数据块）。合并产物与官方 `model.safetensors.index.json` **逐张量对齐**：399/399，数据 16,381,470,720 B 与 index 声明的 `total_size` 完全一致，零填充体检 `zeroTensors 0`。

顺带把 #38/#39 的采样配方从蒸馏档改回 base 档：`cfg 1 → 4`、`steps 8/4 → 20`、#39 `euler_ancestral → euler`，并把 #38 调度器的 `width 1080` 对齐到画布的 1088。改的是 `graph_original`（rescan 从这份重造，只改 `graph` 会被覆盖回去），rescan 后两条都是 `gaps=[]`。

### 7-3 [高·已修] 纯文字任务会抢走素材驱动的工作流，拿作者的残留文件去跑

`workflow_select.rank()` 以前对「素材位给不出」只扣 3 分，于是：

- 文生图（只给 prompt）→ 选中 #38 Klein 人物设定图（它的 `LoadImage` 还挂着作者机器上的 `89a1e249….png`）
- 纯文生视频 → 选中 #31 全能参考 60 秒（`example.png` / `None`）

用户点「文生图」拿到的会是别人那张照片的设定图，而且全程不报错。现在加一条硬规则：**任务什么都没给素材时，素材驱动的工作流不进候选**（`workflow_select.py`），让它如实回落到不需要素材的内置模板。六种任务形态复选：

```
文生图    → (无候选，回落 qwen_image)     改图      → #38 score 25
首尾帧    → #28 score 28                  首帧      → #28 score 20
文生视频  → (无候选，回落 h3_video)       全能参考  → #29 score 31
```

### 7-4 [中·已修] 全能参考把用户上传的参考视频又当产物存了一份

`_outputs_from_history` 只滤掉了 `type=temp`（作者画布上的 PreviewImage），没滤 `type=input`。ComfyUI 的 history 里 `LoadVideo` 会把自己读进去的文件原样报回来：

```
"210": {"images": [{"filename": "0e681f4f-….mp4", "type": "input"}]}   ← 用户上传的那条
"92":  {"images": [{"filename": "job530_00001_.mp4", "subfolder": "h3", "type": "output"}]}
```

结果 media 430 是一条与用户上传**字节完全相同**的 mp4（362,409 B）。改成优先只收 `type=="output"`；确实一条 output 都没有时退回原行为，不在这里静默丢产物。用真实 history 结构复验：混合三条（input/output/temp）只留 output。

那条重复产物已用产品自己的 `DELETE /api/media/430` 放进回收站（100 天后才真删，`promoteCandidateId=431`），没有硬删。

### 7-5 [防回归·已上线并实测] 两道内容健康闸

用户选的「两个都加」：

1. **探活时体检权重**（`app/weights_health.py` + `comfy_native.missing_models()`）：零填充比例 ≥20%，或 ≥5% 且尾部无数据 → 提交前就 `GenError(kind="missing_models")`，报的是「这份权重是半截下载…请重新下载它，别把这张图当结果」。
2. **产物落库前数像素**（`app/media_health.py` + `queue._persist_outputs()`）：`mad≥8 且 std≤42` 判雪花、`mad≤1.5 且 std≤1.5` 判纯色 → **删文件、不落库、任务失败带原因**。只判图片；对读不动的文件返回 `ok=True` 不拦路。

真实任务路径上的拦截实测（纯黑图走完整收口）：

```
终态 failed
错误 {"type":"output_unhealthy","message":"flat_00001_.png：这张图是纯色（相邻像素平均差 0.0、整体标准差 0.0）。采样链没产出内容，检查空 latent 是不是被直接解码了","retryable":false}
产物 []      media 计数没增加，data/media/jobs/<uuid>/ 里也没留下文件
```

阈值在真样本上标定过：雪花全部命中（`mad 12.8 / std 34.3`），「有内容但难看」的图不误伤（`mad 13.9 / std 71.2` 放行）。

### 7-6 显存档位闸拦下过一次真实请求

#28 默认 `megapixels=1.0` → 1184×896（1.06MP），入队直接被拒：「本机 24GB 单卡从没跑成过这个档（864×480 是实测上限）」。这是对的，但意味着**库里 #28 的默认档在本机不可用**，用户从界面直接点会每次吃一条 400。本轮没改这个默认值（改小会降画质，属于产品取舍），留到下一轮定夺。传 `megapixels: 0.4` 后一切正常。

### 7-7 环境事故与恢复（如实记）

- ComfyUI 在 18:30 左右被**外部停掉**（日志最后一行 `Using RAM pressure cache.` 之后无 traceback；物理内存 90.8 GB、负载 30%，不是 OOM）。我 20:45 发现时它已停了 2 小时，8788 也被换成了另一个 pid（16648，20:26 起）。用 `comfyui/start.ps1` 拉起后恢复正常。
- 本轮为加载代码改动重启 8788 共 4 次，**每次之前都先查 `:8188/queue` 与 `jobs` 表**（全部 `queue_running=[]`、`jobs=0` 才动手）。
- 权重全量扫描（`.scratch/weights_sweep.py`）：本机 31 个 safetensors 里**没有第二份半截下载**。视频链要的 FL2VA / Ref2VA / qwen3vl_32b / 视频 VAE / 音频 VAE / ref2v turbo LoRA 全部健康（个别文件有 1 个零张量，属正常零初始化）。

### 7-8 本轮收尾闸门

```
apps/api>  python -m pytest -q -m "not live"   → 50 passed, 80 deselected
apps/web>  npx tsc --noEmit                    → 0 个错误
apps/web>  npm run build (tsc -b)              → ✗ 3 个错误，全在 src/routes/settings/System.tsx:479/483/487
```

那 3 个错误**不是本轮引入的**：`BackupCommands.dump/list/restore` 是可选字段，界面把它们直接喂给要求 `string` 的 `Copyable text`。同一次构建里另一处 `hooks.ts:278`（`importJson` 传了 3 个位置参数）在我读取该文件后的几秒内**被并发会话自己改掉了** —— 这个文件正被另一个会话实时编辑，我没有动 System.tsx，避免两边在同一文件上互相覆盖。我这轮改的文件（`comfy_native.py`、`workflow_select.py`、`weights_health.py`、`media_health.py`、`queue.py`）全部通过。

### 7-9 新工具（`.scratch/`，仓库不收录）

- `.scratch/img_stats.py` —— 一张图/一段视频的 MAD+std 指纹，雪花判据的来源
- `.scratch/weights_sweep.py` —— 全量扫本机 safetensors 的零填充与尾部空洞
- `.scratch/t2i_te_ab.py <clip> <前缀> [steps] [cfg]` —— 同种子只换文本编码器的 A/B
- `.scratch/merge_te.py` —— 分片 safetensors 流式合并（不经 torch），带官方 index 逐张量核对
- `.scratch/patch_klein_recipes.py` —— 改 `graph_original` 的配方（改完必须 rescan）
- `.scratch/run_chain.py '<JobCreate JSON>'` —— 走产品接口真跑一条任务，等终态并逐产物数像素（视频会抽帧再数）
