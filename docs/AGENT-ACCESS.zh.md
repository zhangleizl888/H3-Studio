# H3 Studio 智能体接入（MCP + CLI）

> 面向两类读者：**给这台工作台接上智能体的人**（看 §1–§4），
> 以及**要驱动流程的智能体本身**（看 §5–§7 与 `h3 quickstart`）。

## 0. 三层结构

```
   智能体客户端（Qoder / Claude Desktop / Cursor / Windsurf）
        │  MCP：stdio（同机）或 streamable-http（跨机/隧道）
        ▼
   h3 mcp serve ──────────── 只讲 MCP，不碰业务逻辑
        │  HTTP + Bearer agent token（用「调用方那把」，不是进程那把）
        ▼
   后端 FastAPI  http://127.0.0.1:8788 ── 唯一事实源：派发循环、GPU 仲裁、库
        │
        ▼
   ComfyUI / RunningHub 实例、文本模型、媒体目录

   人用的入口：网页前端（浏览器 IndexedDB 里存创作实体）
   运维入口：h3 CLI（和 MCP 走同一条 HTTP，绝不 import app.main 起第二个调度器）
```

MCP 进程**不 import 后端**。后端那个进程带着队列派发循环和单卡仲裁器，多起一个就是两个调度器抢同一张卡。

---

## 1. 一键接入（推荐路径）

```bash
cd F:/H3/apps/api
.venv/Scripts/python.exe -m pip install -e .     # 装出 h3 命令（h3 setup 也会自动做这步）
h3 setup                                         # 环境→依赖→内嵌 PG→迁移→起服务→发钥匙→写客户端配置
```

先想看清要动什么，再动手：

```bash
h3 setup --dry-run
```

`h3 setup` 做的六件事，全部幂等，任一步失败就停下并告诉你缺什么：

| 步 | 动作 | 会不会改东西 |
| --- | --- | --- |
| 1 | 仓库结构 / `.env` 是否齐 | 否 |
| 2 | venv + 依赖（含 MCP SDK）+ `h3` 命令本体 | 装包 |
| 3 | 数据库：探 `.env` 里的连接串；不通就用 `.tooling-pg` 启内嵌 PG 并回写 `.env` | 起进程、改 `.env` |
| 4 | `alembic upgrade head`（含 `api_tokens` 表） | 建表 |
| 5 | 后端没在跑就后台拉起（日志 `~/.h3/logs/api.log`） | 起进程 |
| 6 | 发 agent token 写进 `~/.h3/config.json`，再把 MCP 条目写进检测到的客户端配置 | 写文件（改前备份） |

常用变体：

```bash
h3 setup --client qoder --qoder-scope local      # 只接本项目（默认落点，不进 git）
h3 setup --scope admin --ttl-days 90             # 发一把有管理权、90 天过期的钥匙
h3 setup --url http://192.168.1.20:8790/mcp      # 写远程 HTTP 入口而不是本机 stdio
h3 setup --force                                 # 已有 token 也重发一把
```

跑完检查：

```bash
h3 doctor        # 后端/库/实例/文本模型/凭据/端口/ffmpeg/客户端注册，逐条打勾
h3 status        # 现场：实例探活、队列、显存与让位
h3 mcp test      # 真起一个 MCP 子进程走完 initialize → tools/list → 调一次 health
```

客户端里重载：Qoder 执行 `/mcp reload`；Claude Desktop 要完全退出再开；Cursor 在 Settings → MCP 里刷新。

---

## 2. 凭据模型（这块是对外暴露的承重墙）

库里新增 `api_tokens` 表：`h3_at_` 开头、只存 HMAC-SHA256 摘要、**明文只在创建那次返回**。

**scope 三档，只能收窄不能越权**：

| scope | 能做什么 | 对应后端门禁 |
| --- | --- | --- |
| `read` | 只查 | `login_gate` |
| `dispatch` | + 入队、取消、重试、试跑、软删产物 | `dispatch_gate`（admin/editor 角色） |
| `admin` | + 改实例、AI 后端、用户、目录、GC、发/吊销钥匙 | `admin_gate` |

- 请求 `["admin"]` 会自动补成 `read,dispatch,admin`：能改配置却读不了状态的工具是坏工具。
- 角色的天花板仍然有效：挂在 viewer 账号上的 `admin` scope 依然进不了 admin 端点。
- token 找不到属主（账号被删/停用）→ 401，不是降级放行。
- 每次使用只在 5 分钟内记一次 `lastUsedAt`，审计页不被刷爆。

```bash
h3 token create --name director-bot --scope read --scope dispatch   # 发给别人时别加 --store
h3 token list
h3 token revoke 3
h3 whoami            # 我是谁、哪档 scope、什么时候过期
```

**为什么不复用浏览器的 JWT 做常驻凭据**：那是 15 分钟一换人、跟着某个屏幕前的会话走的。
MCP 的 HTTP 入口因此**只接受 agent token**，拿 JWT 来连会被明确拒绝并告诉你该跑哪条命令。

---

## 3. 接入引导：各家客户端写在哪

`h3 mcp install` 只动配置文件里的 `mcpServers` 那一片，改前把原文件复制成 `*.bak-时间戳`，
写完回读校验；文件不是合法 JSON 就**拒写**而不是覆盖。

| 客户端 | 落点 | 说明 |
| --- | --- | --- |
| Qoder CLI（本项目） | `<repo>/.qoder/settings.local.json` | 默认。已加进 `.gitignore` |
| Qoder CLI（全局） | `~/.qoder-cn/settings.json` | `--qoder-scope user` |
| Qoder CLI（随 git 提交） | `<repo>/.qoder/settings.json` | `--qoder-scope project`，会进版本库，别放密钥 |
| Claude Desktop | `%APPDATA%\Claude\claude_desktop_config.json` | |
| Cursor | `~/.cursor/mcp.json` | |
| Windsurf | `~/.codeium/windsurf/mcp_config.json` | |

自动探测**每家最多一个落点**，不会把同一条 MCP 注册三遍。

**stdio 配置里不放 token**：子进程自己读 `~/.h3/config.json`。所以写进客户端 JSON 的只有一条
命令行 + `PYTHONPATH`，没有第二份可重放凭据落盘。要打印片段自己粘：

```bash
h3 mcp config                      # 本机 stdio
h3 mcp config --url https://xxx/mcp --token h3_at_yyy --raw
h3 mcp install --client cursor     # 或 --remove 卸载
```

---

## 4. 对外暴露（默认环回，两条命令升级）

被暴露的**永远是 MCP 端口（默认 8790）**，不是后端 8788。后端继续只听环回，
`/docs`、用户接口、前端 REST 契约都不出门。

```bash
h3 expose status            # 现在哪一档、端口在不在听、隧道域名、后端是否在线
h3 expose lan               # 绑 0.0.0.0:8790，打印局域网可粘贴配置
h3 expose lan --detach      # 后台跑
h3 expose cloud             # cloudflared quick tunnel → https://xxx.trycloudflare.com/mcp
h3 expose cloud --install-cloudflared   # 缺 cloudflared 时让 winget 装（会弹 UAC）
h3 expose off               # 停掉自己拉起的进程并清状态
```

三档的威胁模型，说清楚再按：

- **环回（默认）**：只有本机能连。给同机智能体用 `h3 mcp install` 就够，不用 expose。
- **局域网**：同网段任何人都能*尝试*连接，但没 token 就是 401。配置片段里带明文 token ——
  给出去的那一刻就该当成「这把钥匙泄露给这个人了」，用独立发的、能单删的 token。
- **公网隧道**：`trycloudflare` 随机域名，每次重启换名字，无需 Cloudflare 账号。
  定位是「临时给云端智能体/同事接一次」，不是常驻生产入口。收工 `h3 expose off`。

DNS 重绑定保护默认只放行本机地址；绑局域网或走隧道时用 `--allow-host abc.trycloudflare.com`
加白名单（会自动补端口）。`--allow-all-hosts` 是整个关掉，只剩 Bearer 一道门，会打警告。

---

## 5. MCP 工具面（73 个）

`h3 mcp tools` 看全量清单。命名 `snake_case`，每个都带一行说明——智能体靠这行决定调不调，
所以说明里写的是「什么时候该用我」，不是「我返回什么」。

| 组 | 代表工具 |
| --- | --- |
| 现场 | `health` `status` `gpu_status` `gpu_restore` `system_paths` `storage_report` `storage_gc` `audit_log` `backup_commands` `style_presets` |
| 实例 | `instance_list` `instance_probe` `instance_object_info` `instance_create/update/delete` |
| 工作流 | `workflow_list/get/select/slots/models/validate/import/rescan/export/update/delete/test` |
| 文本模型 | `llm_backends` `llm_run` `llm_defaults` `llm_set_default` `llm_models` `llm_local_scan` `llm_pull` |
| 任务 | `job_plan` `job_submit` `job_batch_submit` `job_list` `job_get` `job_wait` `job_cancel` `job_retry` `job_update` |
| 产物 | `media_list` `media_location` `media_download` `media_upload` `media_versions` `media_trash/restore/purge` `trash_list` |
| 剧本版本 | `script_versions` `script_version_save/set_current/trash/restore/purge` `project_trash` |
| 解析导出 | `parse_script` `export_merge` `export_pack` `export_timeline` |
| 组合流程 | `storyboard_from_script`（拆解→分镜两步串好）`generate_and_wait`（批量入队并等终态） |
| 通用出口 | `api_catalog`（列 OpenAPI 端点）`api_request`（打任意端点） |

显式工具覆盖主干流程，长尾走 `api_request` —— 新加端点不必先改 MCP，也就不会有一份
和 OpenAPI 平行漂移的清单。破坏性动作（`media_purge`、`script_version_purge`、`project_trash`）
都要求显式 `confirm=True`。

---

## 6. 智能体该知道的四条约束

1. **单卡互斥**：一张 24GB 卡装不下「27B 文本模型 + 图像/视频模型」。派发本地生成任务前后端会
   自动让显存（可能停掉 llama-server，队列空了再看门狗拉回）。这段时间文本调用返回 409，等它恢复，
   别硬重试。
2. **生成是串行的、且慢**：`job_batch_submit` 是排队不是并发；出片分钟到十几分钟量级。
   除非确实要阻塞等，否则用 `job_submit` + `job_get` 抽查，别用 `job_wait` 占住一整轮。
3. **创作实体在浏览器 IndexedDB 里**：服务端没有 projects 表。`projectKey` 只是软引用，用于产物
   归组、版本历史、导出归档。要分镜表本身，得由调用方作为参数交进来（`export_timeline` 就是这样）。
4. **AI 产物停在「待确认」**：任何写回用户既有稿子的动作都要人在页面上确认。工具能存剧本版本
   （`script_version_save`），但不要用工具绕过预览确认那条口径。

---

## 7. 命令行速查

```
h3 setup | doctor | status | serve | quickstart
h3 bootstrap | login | logout | whoami | config
h3 token create|list|revoke
h3 mcp serve|config|install|tools|test
h3 expose lan|cloud|status|off
h3 instance …          list probe object-info create update delete
h3 workflow …          list get select slots models validate import rescan export update delete test
h3 llm …               backends models defaults put-defaults local-scan probe create update delete run
h3 job …               submit（--batch @jobs.json 一次多条，--wait 阻塞） plan list get watch cancel retry update
h3 media …             list download upload versions trash restore purge
h3 script …            list save current trash restore purge
h3 trash list
h3 system …            paths set-paths storage gc audit backup gpu gpu-restore styles
h3 user …              list create update delete
h3 export run merge|pack|edl|xml|jianying --project-key K …
h3 parse <文件>
h3 routes --filter jobs
h3 api GET "/api/jobs" --param state=queued
```

全局参数：`--server` `--token` `--json` `--timeout`。所有子命令都吃 `--json`（原样吐 JSON，
给人写脚本、给智能体解析都比表格稳）。

---

## 8. 故障排查

| 现象 | 原因与处置 |
| --- | --- |
| `Invalid Host header` | DNS 重绑定白名单没含你访问的地址。`h3 mcp serve --transport http --allow-host <域名或IP>`，客户端要用那个 Host |
| MCP HTTP 返回 401 `missing_bearer` | 客户端配置里没带 `Authorization`。发钥匙 `h3 token create`，或写远程条目 `h3 mcp config --url … --token …` |
| 401 `agent token 无效/已吊销/已过期` | 按消息重发一把；`h3 token list` 看现在有哪些 |
| 401 `账号不存在或已停用` | 属主被删了（跑过 `tests/test_auth_live.py` 会 `DELETE FROM users`）。重新 `h3 bootstrap` 或页面建号，再 `h3 token create --store` |
| 403 `scope 不含 dispatch/admin` | 用对应档重发：`h3 token create --scope admin` |
| 客户端里工具不出现 | `/mcp reload`（Qoder）或完全重启客户端；先 `h3 mcp test` 确认不是服务端问题 |
| `h3: command not found` | 没装命令本体：`cd apps/api && .venv/Scripts/python.exe -m pip install -e .`；或直接 `python -m app.cli …` |
| `h3 api GET /healthz` 报「路径要以 / 开头」 | Git Bash 把 `/healthz` 重写成了 `C:/Program Files/Git/healthz`。设 `MSYS_NO_PATHCONV=1`，或在 PowerShell/cmd 里跑 |
| 中文输出乱码 | `h3` 已强制 UTF-8；老 cmd.exe 请 `chcp 65001` 或 `set PYTHONUTF8=1` |
| 改了代码 MCP 还是旧工具 | stdio 是每次由客户端重新拉子进程，重载连接即可；HTTP 模式要重启 `h3 mcp serve` |

---

## 9. 这一批没做什么（下一批的入口）

- **项目快照镜像**：让服务端持有一份可读写的项目实体镜像，智能体才能真正端到端跑
  「剧本 → 分镜 → 关键帧 → 出片 → 时间轴」。当前 `export_timeline` / `storyboard_from_script`
  要调用方自己把分镜表交进来，就是这个缺口的形状。
- 生成任务的 SSE 订阅（现在 `job_wait` 是轮询；`/api/jobs/{id}/stream` 那条流后端已有，
  但任务收口在派发循环，轮询才是权威）。
- 常驻服务化（Windows 计划任务 / 开机自启）与多用户各自的 agent token 配额。
