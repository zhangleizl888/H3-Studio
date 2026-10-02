"""`h3` 命令行。

命令分三层，越往下越通用：
1. 引导层：setup / doctor / quickstart / serve / status —— 装、体检、起服务、看现场。
2. 流程层：instance / workflow / llm / job / media / script / trash / export / parse / system
   —— 一条镜头从选工作流到出片到导出，每步都有对应手。
3. 兜底层：`h3 routes` + `h3 api METHOD PATH` —— 后端 90 来个端点全可达，
   新加端点不必先改 CLI。

所有子命令都吃 `--json`（原样吐 JSON，给脚本和智能体用），默认输出是对人友好的表格。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Sequence

from . import expose as expose_mod
from . import mcpconfig, setup as setup_mod
from .client import H3Client, H3Error
from .store import DEFAULT_MCP_PORT, DEFAULT_SERVER, Config, config_path, load, redact, save


# ---------------------------------------------------------------- 输出


def _out(args: argparse.Namespace, value: Any, *, columns: Sequence[str] | None = None) -> None:
    if getattr(args, "json", False):
        print(json.dumps(value, ensure_ascii=False, indent=2, default=str))
        return
    if value is None:
        print("（空）")
        return
    if isinstance(value, list):
        _table(value, columns)
        return
    if isinstance(value, dict):
        _kv(value)
        return
    print(value)


def _kv(row: dict[str, Any]) -> None:
    width = max((len(str(k)) for k in row), default=8)
    for k, v in row.items():
        if isinstance(v, (dict, list)):
            v = json.dumps(v, ensure_ascii=False, default=str)
        print(f"{str(k).ljust(width)}  {v}")


def _table(rows: list[Any], columns: Sequence[str] | None = None) -> None:
    if not rows:
        print("（没有记录）")
        return
    if not all(isinstance(r, dict) for r in rows):
        for r in rows:
            print(r)
        return
    cols = list(columns or rows[0].keys())
    cells = [[_cell(r.get(c)) for c in cols] for r in rows]
    widths = [max(len(cols[i]), *(len(row[i]) for row in cells)) for i in range(len(cols))]
    print("  ".join(cols[i].ljust(widths[i]) for i in range(len(cols))))
    print("  ".join("-" * widths[i] for i in range(len(cols))))
    for row in cells:
        print("  ".join(row[i].ljust(widths[i]) for i in range(len(cols))))


def _cell(v: Any) -> str:
    if v is None:
        return "-"
    if isinstance(v, (dict, list)):
        return (json.dumps(v, ensure_ascii=False, default=str))[:60]
    return str(v)[:60]


def _load_body(spec: str | None) -> Any:
    """--data '{"a":1}' 或 --data @file.json。"""
    if not spec:
        return None
    if spec.startswith("@"):
        return json.loads(Path(spec[1:]).read_text(encoding="utf-8"))
    return json.loads(spec)


def _params(pairs: Sequence[str] | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for pair in pairs or []:
        if "=" not in pair:
            raise SystemExit(f"--param 要写成 key=value，收到 {pair!r}")
        k, v = pair.split("=", 1)
        out[k] = v
    return out


# ---------------------------------------------------------------- 客户端外壳


def _config(args: argparse.Namespace) -> Config:
    cfg = load()
    if getattr(args, "server", None):
        cfg.server = args.server.rstrip("/")
    if getattr(args, "token", None):
        cfg.token = args.token
    return cfg


async def _with_client(args: argparse.Namespace, fn: Callable[[H3Client], Any]) -> Any:
    cfg = _config(args)
    async with H3Client(cfg, timeout=float(getattr(args, "timeout", None) or 120.0)) as client:
        return await fn(client)


# ---------------------------------------------------------------- 引导层


def cmd_setup(args: argparse.Namespace) -> int:
    login = (args.username, args.password) if args.username and args.password else None
    return setup_mod.run(
        _config(args),
        dry=args.dry_run,
        scopes=args.scope,
        login=login,
        clients=args.client,
        qoder_scope=args.qoder_scope,
        url=args.url,
        force_token=args.force,
        restart_backend=args.restart_backend,
        ttl_days=args.ttl_days,
    )


def cmd_doctor(args: argparse.Namespace) -> int:
    cfg = _config(args)
    problems = 0

    def line(ok: bool, label: str, detail: str = "") -> None:
        nonlocal problems
        if not ok:
            problems += 1
        print(f"{'✓' if ok else '✗'} {label}{('：' + detail) if detail else ''}")

    health = expose_mod.proc.http_get_json(cfg.server + "/healthz")
    line(bool(health), f"后端 {cfg.server}", "不在线。跑 h3 serve 或 h3 setup" if not health else f"库={health.get('database')} 实例={health.get('instances')} 媒体根={health.get('mediaRoot')}")
    if not health:
        return 1
    line(bool(health.get("database")), "数据库已接入", "无库模式下队列/用户/agent token 都不可用：设 H3_DATABASE_URL 后重跑 h3 setup" if not health.get("database") else "")

    try:
        instances = asyncio.run(_with_client(args, lambda c: c.get("/api/instances"))) or []
    except H3Error as exc:
        line(False, "读实例列表", f"{exc.status} {exc.detail}")
        instances = []
    if instances:
        def mark(i: dict[str, Any]) -> str:
            ok = i.get("lastProbeOk")
            return "通" if ok else ("不通" if ok is False else "未测")

        line(any(i.get("lastProbeOk") for i in instances), f"实例 {len(instances)} 台",
             "；".join(f"{i.get('name')}={mark(i)}{'/熔断' if i.get('circuitOpen') else ''}" for i in instances))
    else:
        line(False, "没有可用实例", "h3 instance create --name local --base-url http://127.0.0.1:8188")

    async def _llm(c: H3Client) -> Any:
        backends = await c.get("/api/llm/backends")
        defaults = await c.get("/api/llm/defaults")
        return backends, defaults

    try:
        backends, defaults = asyncio.run(_with_client(args, _llm))
        line(bool(backends), f"文本后端 {len(backends or [])} 个", "没配文本模型，剧本/分镜类工具会失败" if not backends else "；".join(f"{b.get('name')}({b.get('scope')})" for b in backends[:5]))
        line(bool(defaults), "用途→后端映射", "未设默认，llm_run 会退回第一个后端" if not defaults else "")
    except H3Error as exc:
        line(False, "读文本后端", f"{exc.status} {exc.detail}")

    if cfg.token:
        try:
            who = asyncio.run(_with_client(args, lambda c: c.get("/api/agent-tokens/whoami")))
            line(True, "agent token 有效", f"{who.get('tokenName')} scope={'/'.join(who.get('scopes') or [])} 属主={who.get('user', {}).get('username')}")
        except H3Error as exc:
            line(False, "agent token", f"{exc.status} {exc.detail}")
    else:
        line(bool(cfg.auth.get("access")), "凭据", "没登录也没 token：h3 login 或 h3 token create" if not cfg.auth.get("access") else "用的是浏览器式 JWT（会过期），常驻智能体建议 h3 token create")

    port = int(cfg.expose.get("mcpPort") or cfg.mcp_port or DEFAULT_MCP_PORT)
    listening = expose_mod.proc.port_listening(port)
    line(True if not cfg.expose.get("mode") else listening, f"MCP 端口 {port}", "在听" if listening else "没在听（本机智能体走 stdio，不需要它）")
    if cfg.expose.get("mode") == "cloud":
        line(bool(cfg.expose.get("tunnelUrl")), "公网隧道", cfg.expose.get("tunnelUrl") or "起来了但没有域名，看 ~/.h3/logs/cloudflared.log")
    line(bool(expose_mod.find_cloudflared()), "cloudflared", "装了才能 h3 expose cloud" if not expose_mod.find_cloudflared() else "可用")
    line(bool(shutil_which("ffmpeg")), "ffmpeg", "导出成片/打包需要它（或在 系统设置 里指路径）")

    installed = [t.id for t in mcpconfig.all_targets(setup_mod.REPO_ROOT) if t.exists and mcpconfig.SERVER_NAME in (mcpconfig._read(t.path).get(t.key) or {})]
    line(bool(installed), "客户端已注册", "；".join(installed) if installed else "还没有：h3 mcp install 或 h3 setup")
    print(f"\n{'体检有问题项 ' + str(problems) + ' 条' if problems else '一切正常'}（配置 {config_path()}）")
    return 1 if problems and args.strict else 0


def shutil_which(name: str) -> str | None:
    import shutil

    return shutil.which(name)


def cmd_serve(args: argparse.Namespace) -> int:
    cfg = _config(args)
    host, _, port = setup_mod._host_port(cfg.server)
    if args.host:
        host = args.host
    if args.port:
        port = args.port
    if host not in {"127.0.0.1", "localhost", "::1"}:
        print(f"⚠ 即将监听 {host}：这是后端 REST 本体（含 /docs 与用户接口）。给智能体接入请只暴露 MCP（h3 expose），除非你确实要把网页端开放到局域网。")
    argv = [sys.executable, "-X", "utf8", "-m", "uvicorn", "app.main:app", "--host", host, "--port", str(port), *(["--reload"] if args.reload else [])]
    print("[h3] " + " ".join(argv))
    return subprocess.run(argv, cwd=str(mcpconfig.api_root())).returncode


def cmd_desktop(args: argparse.Namespace) -> int:
    """桌面形态：一个进程把内嵌 PG、迁移、HTTP 服务带起来，并把真端口写进 desktop.json。

    和 `h3 serve` 的区别不是「多了个参数」，是**数据来源换了**：serve 读 apps/api/.env（开发机那份），
    desktop 只认 H3_HOME 与真实环境变量。装机的 exe 里根本没有 .env。
    """
    from .. import desktop as desktop_mod

    os.environ["H3_DESKTOP"] = "1"
    if args.data_root:
        os.environ["H3_HOME"] = str(Path(args.data_root).expanduser().resolve())
    if args.port:
        os.environ["H3_PORT"] = str(args.port)
    return desktop_mod.main(["--host", args.host])


def cmd_status(args: argparse.Namespace) -> int:
    async def _go(c: H3Client) -> Any:
        health = await c.get("/healthz")
        instances = await c.get("/api/instances")
        queued = await c.get("/api/jobs", params={"state": "queued", "limit": 50})
        running = await c.get("/api/jobs", params={"state": "running", "limit": 50})
        out: dict[str, Any] = {"backend": health, "instances": instances, "queued": len(queued or []), "running": running or []}
        for key, path in (("gpu", "/api/system/gpu"), ("storage", "/api/system/storage")):
            try:
                out[key] = await c.get(path)
            except Exception as exc:
                out[key] = {"unavailable": str(exc)}
        return out

    data = asyncio.run(_with_client(args, _go))
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2, default=str))
        return 0
    print(f"后端      {data['backend']['mediaRoot']}  实例 {data['backend']['instances']} 台  库 {'已接' if data['backend']['database'] else '未接'}")
    for i in data["instances"] or []:
        print(f"  实例 {i.get('id'):<6} {i.get('name'):<18} {i.get('placement'):<16} 探活={'通' if i.get('lastProbeOk') else ('不通' if i.get('lastProbeOk') is False else '未测')}{' 熔断' if i.get('circuitOpen') else ''}")
    print(f"队列      排队 {data['queued']}　进行中 {len(data['running'])}")
    for j in data["running"]:
        prog = j.get("progress") or {}
        print(f"  跑着    {j.get('id')}  {j.get('title') or j.get('kind')}  {prog.get('stage') or ''} {prog.get('value') or '-'}/{prog.get('max') or '-'}")
    gpu = data.get("gpu") or {}
    if "unavailable" not in gpu:
        print(f"显存/让位  {json.dumps(gpu, ensure_ascii=False, default=str)[:220]}")
    return 0


def cmd_quickstart(args: argparse.Namespace) -> int:
    print(QUICKSTART)
    return 0


QUICKSTART = """\
H3 Studio 智能体接入 —— 六条命令走完

1) h3 setup
   环境、依赖、内嵌 PG、迁移、后端进程、agent token、客户端 MCP 配置，一次做完。
   先想看不动手：h3 setup --dry-run
   只要本地智能体（推荐起点）：h3 setup --client qoder

2) h3 doctor
   体检。红色那行会直接告诉你下一步跑什么。

3) h3 status
   现场：实例通不通、队列积压、显存与让位状态。

4) h3 mcp test
   真跑一次 MCP 握手，列出全部工具名。这一步过了，客户端里就一定能有。

5) 客户端里重载
   Qoder：/mcp reload；Claude Desktop：完全退出再开；Cursor：Settings → MCP → Refresh。

6) 让智能体开干
   典型一轮：status → workflow_select → job_plan → job_submit → job_get（或 job_wait）→ media_location。

要给别人 / 给云端智能体接：
   h3 token create --name guest --scope read        # 单独一把，能随时收
   h3 expose lan                                     # 局域网 :8790，打印可粘贴配置
   h3 expose cloud                                   # cloudflared 临时公网域名
   h3 expose off                                     # 收回

本地智能体不需要对外：MCP 配置里那条 stdio 命令行会自己读 ~/.h3/config.json，token 不落进客户端配置文件。
"""


# ---------------------------------------------------------------- 凭据


def cmd_login(args: argparse.Namespace) -> int:
    cfg = _config(args)
    username = args.username or input("用户名：").strip()
    password = args.password or getpass_getpass("口令：")
    # 保存的是浏览器式会话；发钥匙那步要拿这次登录的 access token 去问后端，
    # 所以 client 直接吃这份内存里的 cfg，不能让 _with_client 再去读一遍磁盘上的旧 token
    if args.no_token:
        cfg.token = None

    async def _go() -> Any:
        async with H3Client(cfg, timeout=60.0) as client:
            return await client.login(username, password)

    try:
        data = asyncio.run(_go())
    except H3Error as exc:
        print(f"登录失败：{exc.detail}", file=sys.stderr)
        return 1
    if args.no_token:
        cfg.token = None
    save(cfg)
    user = data.get("user") or {}
    print(f"已登录 {user.get('username')}（{user.get('role')}），会话写入 {config_path()}")
    print("注意：JWT 会过期（默认 15 分钟，靠 refresh 续）。常驻智能体用 h3 token create 发一把长期钥匙。")
    return 0


def getpass_getpass(prompt: str) -> str:
    import getpass

    return getpass.getpass(prompt)


def cmd_bootstrap(args: argparse.Namespace) -> int:
    """空库时的第一个账号。后端只在该库一个用户都没有时接受这条请求。"""
    cfg = _config(args)
    username = args.username or "admin"
    password = args.password or getpass_getpass(f"给 {username} 设一个口令（至少 4 位）：")

    async def _go() -> Any:
        async with H3Client(cfg, timeout=30.0) as client:
            needed = await client.get("/api/auth/setup-required", auth=False)
            if not needed.get("needed"):
                raise H3Error(409, "库里已经有账号了，直接用 h3 login（口令忘了要改库，CLI 不猜）")
            data = await client.post("/api/auth/bootstrap", {"username": username, "password": password}, auth=False)
            cfg.auth = {
                "access": data["access"],
                "refresh": data["refresh"],
                "expiresAt": time.time() + int(data.get("expiresIn", 900)),
                "user": data.get("user"),
            }
            cfg.token = None
            return data

    try:
        data = asyncio.run(_go())
    except H3Error as exc:
        print(f"建号失败：{exc.detail}", file=sys.stderr)
        return 1
    save(cfg)
    print(f"已创建 {username}（{(data.get('user') or {}).get('role')}）并登录，凭据写入 {config_path()}")
    print("对外暴露之前请先改这个口令：h3 api PATCH /api/users/<id> --data '{\"password\":\"...\"}'")
    return 0


def cmd_whoami(args: argparse.Namespace) -> int:
    try:
        data = asyncio.run(_with_client(args, lambda c: c.get("/api/agent-tokens/whoami")))
    except H3Error as exc:
        print(f"问不到身份：{exc.detail}", file=sys.stderr)
        return 1
    _out(args, data)
    return 0


def cmd_logout(args: argparse.Namespace) -> int:
    cfg = _config(args)
    cfg.auth = {}
    if args.revoke_token:
        cfg.token = None
    save(cfg)
    print("已清掉本地会话" + ("（含 agent token）" if args.revoke_token else ""))
    return 0


def cmd_config_show(args: argparse.Namespace) -> int:
    _out(args, redact(_config(args)))
    return 0


def cmd_token_create(args: argparse.Namespace) -> int:
    cfg = _config(args)
    scopes = args.scope or ["read", "dispatch"]

    async def _go(c: H3Client) -> Any:
        if args.username:
            await c.login(args.username, args.password or getpass_getpass("管理员口令："))
        return await c.post("/api/agent-tokens", {"name": args.name, "scopes": scopes, "ttlDays": args.ttl_days})

    try:
        data = asyncio.run(_with_client(args, _go))
    except H3Error as exc:
        print(f"发钥匙失败：{exc.detail}", file=sys.stderr)
        return 1
    print(json.dumps({k: v for k, v in data.items() if k != "token"}, ensure_ascii=False, indent=2, default=str))
    print(f"\ntoken（只显示这一次）：{data['token']}")
    if args.store:
        cfg.token = data["token"]
        save(cfg)
        print(f"已写入 {config_path()}")
    else:
        print("没写本地配置（--store 才会存）。给别人用就这一行复制过去。")
    return 0


def cmd_token_list(args: argparse.Namespace) -> int:
    data = asyncio.run(_with_client(args, lambda c: c.get("/api/agent-tokens")))
    _out(args, data, columns=["id", "name", "owner", "scopes", "lastUsedAt", "expiresAt", "revokedAt"])
    return 0


def cmd_token_revoke(args: argparse.Namespace) -> int:
    data = asyncio.run(_with_client(args, lambda c: c.post(f"/api/agent-tokens/{args.token_id}/revoke")))
    _out(args, data)
    if not args.json:
        print(f"已吊销 {data.get('name')}（ID {args.token_id}）。持有它的智能体下一次调用就会 401。")
    return 0


# ---------------------------------------------------------------- MCP


def cmd_mcp_serve(args: argparse.Namespace) -> int:
    from ..mcp.serve import run_http, run_stdio

    cfg = _config(args)
    if not (cfg.token or cfg.auth.get("access")):
        print("[h3] 还没有凭据：stdio 会立刻 401。先跑 h3 setup 或 h3 login", file=sys.stderr)
    if args.transport == "stdio":
        return run_stdio(cfg, log_level=args.log_level)
    return run_http(
        cfg,
        host=args.host,
        port=args.port or cfg.mcp_port,
        path=args.path,
        allow_hosts=args.allow_host or [],
        allow_all_hosts=args.allow_all_hosts,
        log_level=args.log_level,
    )


def cmd_mcp_config(args: argparse.Namespace) -> int:
    entry: dict[str, Any]
    if args.url:
        entry = mcpconfig.http_entry(args.url, args.token if args.token is not None else _config(args).token)
        label = f"streamable-http {args.url}"
    else:
        entry = mcpconfig.stdio_entry()
        label = f"stdio（{mcpconfig.api_root()}）"
    body = entry if args.raw else {mcpconfig.SERVER_NAME: entry}
    print(json.dumps(body if args.raw else {"mcpServers": body}, ensure_ascii=False, indent=2))
    print(f"\n# {label}", file=sys.stderr)
    if args.client and args.client != "none":
        print("# 直接写入：h3 mcp install --client " + args.client, file=sys.stderr)
    return 0


def cmd_mcp_install(args: argparse.Namespace) -> int:
    repo = setup_mod.REPO_ROOT
    chosen: list[mcpconfig.Target] = []
    for w in args.client or ["auto"]:
        if w == "auto":
            chosen += mcpconfig.auto_detected(repo, qoder_scope=args.qoder_scope)
        elif w == "qoder":
            chosen.append({"user": mcpconfig.qoder_user(), "project": mcpconfig.qoder_project(repo)}.get(args.qoder_scope, mcpconfig.qoder_local(repo)))
        elif w == "claude":
            chosen.append(mcpconfig.claude_desktop())
        elif w == "cursor":
            chosen.append(mcpconfig.cursor())
        elif w == "windsurf":
            chosen.append(mcpconfig.windsurf())
        elif w == "none":
            print(mcpconfig.render_json(args.url and mcpconfig.http_entry(args.url, _config(args).token) or mcpconfig.stdio_entry()))
            return 0
    if not chosen:
        print("没检测到任何客户端配置目录。用 --client qoder|claude|cursor|windsurf 指定，或 h3 mcp config 打印片段自己粘", file=sys.stderr)
        return 1
    entry = mcpconfig.http_entry(args.url, _config(args).token) if args.url else mcpconfig.stdio_entry()
    for t in chosen:
        res = mcpconfig.install(t, entry) if not args.remove else mcpconfig.install(t, entry, remove=True)
        if not res.get("changed"):
            print(f"· {t.label}：{res.get('reason')}（{res['path']}）")
            continue
        print(f"✓ {t.label}：{res['action']} → {res['path']}")
        if res.get("backup"):
            print(f"  备份 {res['backup']}")
        print(f"  生效方式：{res['reload']}")
    return 0


def cmd_mcp_tools(args: argparse.Namespace) -> int:
    from ..mcp.server import build_server

    async def _go() -> Any:
        mcp = build_server()
        return [(t.name, (t.description or "").splitlines()[0][:70]) for t in await mcp.list_tools()]

    rows = asyncio.run(_go())
    if args.json:
        print(json.dumps([{"name": n, "summary": s} for n, s in rows], ensure_ascii=False, indent=2))
    else:
        for name, summary in rows:
            print(f"{name:<26} {summary}")
        print(f"\n共 {len(rows)} 个工具（h3 mcp config 拿到接入配置）")
    return 0


def cmd_mcp_test(args: argparse.Namespace) -> int:
    """真起一个子进程做 MCP 握手：initialize → tools/list → 调一个只读工具。"""
    from .mcptest import handshake_stdio

    cfg = _config(args)
    code, report = handshake_stdio(cfg, call_tool=args.call)
    print(report)
    return code


# ---------------------------------------------------------------- 暴露


def cmd_expose(args: argparse.Namespace) -> int:
    cfg = _config(args)
    port = args.port or cfg.mcp_port
    if args.action == "lan":
        return expose_mod.expose_lan(cfg, port=port, detach=args.detach, print_only=args.print_only)
    if args.action == "cloud":
        return expose_mod.expose_cloud(cfg, port=port, detach=args.detach, install_cloudflared=args.install_cloudflared)
    if args.action == "off":
        return expose_mod.expose_off(cfg)
    return expose_mod.expose_status(cfg)


# ---------------------------------------------------------------- 通用 REST 出口


def cmd_api(args: argparse.Namespace) -> int:
    body = _load_body(args.data) if args.data else None
    params = _params(args.param)

    async def _go(c: H3Client) -> Any:
        return await c.request(args.method.upper(), args.path, json_body=body, params=params)

    try:
        data = asyncio.run(_with_client(args, _go))
    except H3Error as exc:
        print(f"{exc.status} {exc.detail}", file=sys.stderr)
        return 1
    _out(args, data)
    return 0


def cmd_routes(args: argparse.Namespace) -> int:
    async def _go(c: H3Client) -> Any:
        return await c.get("/openapi.json", auth=False)

    spec = asyncio.run(_with_client(args, _go))
    rows = []
    for path, ops in (spec.get("paths") or {}).items():
        for verb, op in ops.items():
            if verb not in {"get", "post", "patch", "put", "delete"}:
                continue
            if args.filter and args.filter not in path:
                continue
            rows.append({"method": verb.upper(), "path": path, "summary": (op.get("summary") or "").strip()[:60]})
    rows.sort(key=lambda r: (r["path"], r["method"]))
    _out(args, rows, columns=["method", "path", "summary"])
    if not args.json:
        print(f"\n共 {len(rows)} 条。任何一条都能打：h3 api GET {rows[0]['path'] if rows else '/healthz'}", file=sys.stderr)
    return 0


# ---------------------------------------------------------------- 表驱动的子命令


def _path_of(template: str, args: argparse.Namespace) -> str:
    out = template
    for key, value in vars(args).items():
        ph = "{" + key + "}"
        if ph in out:
            out = out.replace(ph, str(value))
    if "{" in out:
        raise SystemExit(f"路径里还有未替换的占位符：{out}")
    return out


def _make_handler(spec: dict[str, Any]) -> Callable[[argparse.Namespace], int]:
    def handler(args: argparse.Namespace) -> int:
        path = _path_of(spec["path"], args)
        params = {}
        for name in spec.get("query", []):
            value = getattr(args, name, None)
            if value is not None:
                params[name] = value
        body = _load_body(args.data) if getattr(args, "data", None) else None
        if spec.get("body_defaults"):
            merged = dict(spec["body_defaults"])
            merged.update(body or {})
            body = {k: v for k, v in merged.items() if v is not None}

        async def _go(c: H3Client) -> Any:
            return await c.request(spec["method"], path, json_body=body, params=params)

        try:
            data = asyncio.run(_with_client(args, _go))
        except H3Error as exc:
            print(f"{exc.status} {exc.detail}", file=sys.stderr)
            return 1
        if spec.get("silent") and data is None:
            print("完成")
            return 0
        _out(args, data, columns=spec.get("columns"))
        return 0

    return handler


# method, path, help, query params, body flags, columns
SIMPLE: list[dict[str, Any]] = [
    # 实例
    dict(group="instance", name="list", method="GET", path="/api/instances", help="列出生成实例", columns=["id", "name", "protocol", "placement", "baseUrl", "lastProbeOk", "circuitOpen", "isDefault"]),
    dict(group="instance", name="probe", method="POST", path="/api/instances/{instance_id}/probe", help="真连一次实例"),
    dict(group="instance", name="ping", method="POST", path="/api/instances/{instance_id}/ping", help="轻量在线探测（只问 system_stats+queue，不拉 object_info）"),
    dict(group="instance", name="object-info", method="GET", path="/api/instances/{instance_id}/object-info", help="取该实例的节点/权重清单", query=["class_type"]),
    dict(group="instance", name="create", method="POST", path="/api/instances", help="登记实例（admin）", data=True),
    dict(group="instance", name="update", method="PATCH", path="/api/instances/{instance_id}", help="改实例（admin）", data=True),
    dict(group="instance", name="delete", method="DELETE", path="/api/instances/{instance_id}", help="删实例（admin）", silent=True),
    # 工作流
    dict(group="workflow", name="list", method="GET", path="/api/workflows", help="内置模板 + 已导入工作流", columns=["id", "name", "kind", "autoSelect", "verifiedAt", "priority"]),
    dict(group="workflow", name="get", method="GET", path="/api/workflows/{workflow_id}", help="一条工作流详情"),
    dict(group="workflow", name="select", method="GET", path="/api/workflows/select", help="这种任务会自动挑中哪条工作流", query=["kind", "instance_id"]),
    dict(group="workflow", name="slots", method="GET", path="/api/workflows/{workflow_id}/slots", help="参数槽清单"),
    dict(group="workflow", name="models", method="GET", path="/api/workflows/{workflow_id}/models", help="需要哪些权重、实例上有没有", query=["instance_id"]),
    dict(group="workflow", name="bindings", method="GET", path="/api/workflows/{workflow_id}/bindings", help="每台实例各自的默认权重绑定"),
    dict(group="workflow", name="put-binding", method="PUT", path="/api/workflows/{workflow_id}/bindings", help="设某台实例的默认权重（admin，值必须是那台报出来的文件名）", data=True),
    dict(group="workflow", name="del-binding", method="DELETE", path="/api/workflows/{workflow_id}/bindings/{instance_id}", help="清掉某台的绑定=恢复图内默认（admin）", silent=True),
    dict(group="workflow", name="sync-bindings", method="POST", path="/api/workflows/{workflow_id}/bindings/sync", help="把一台配好的默认权重搬到别处（admin）", data=True),
    dict(group="workflow", name="check", method="POST", path="/api/workflows/{workflow_id}/check", help="逐台体检：节点/权重/半截下载/能不能跑", data=True),
    dict(group="workflow", name="replace-graph", method="POST", path="/api/workflows/{workflow_id}/graph", help="换掉这条的图并按实例重扫（admin）", data=True),
    dict(group="workflow", name="export", method="GET", path="/api/workflows/{workflow_id}/export", help="导出 JSON", query=["format"]),
    dict(group="workflow", name="validate", method="POST", path="/api/workflows/validate", help="校验图", data=True),
    dict(group="workflow", name="rescan", method="POST", path="/api/workflows/{workflow_id}/rescan", help="重扫工作流", query=["instance_id"]),
    dict(group="workflow", name="update", method="PATCH", path="/api/workflows/{workflow_id}", help="改元数据", data=True),
    dict(group="workflow", name="delete", method="DELETE", path="/api/workflows/{workflow_id}", help="删工作流（admin）", silent=True),
    dict(group="workflow", name="test", method="POST", path="/api/workflows/{workflow_id}/test", help="试跑（真占显存）", data=True),
    # 文本模型
    dict(group="llm", name="backends", method="GET", path="/api/llm/backends", help="文本后端清单", query=["scope"], columns=["id", "name", "scope", "kind", "baseUrl", "model", "isDefault"]),
    dict(group="llm", name="models", method="GET", path="/api/llm/models", help="某后端可用模型", query=["backend_id"]),
    dict(group="llm", name="defaults", method="GET", path="/api/llm/defaults", help="用途→后端映射"),
    dict(group="llm", name="put-defaults", method="PUT", path="/api/llm/defaults", help="设用途→后端映射（admin）", data=True),
    dict(group="llm", name="local-scan", method="GET", path="/api/llm/local-scan", help="扫本机在跑的推理服务"),
    dict(group="llm", name="probe", method="POST", path="/api/llm/backends/{backend_id}/probe", help="探某后端能力"),
    dict(group="llm", name="create", method="POST", path="/api/llm/backends", help="新增后端（admin）", data=True),
    dict(group="llm", name="update", method="PATCH", path="/api/llm/backends/{backend_id}", help="改后端（admin）", data=True),
    dict(group="llm", name="delete", method="DELETE", path="/api/llm/backends/{backend_id}", help="删后端（admin）", silent=True),
    # 任务
    dict(group="job", name="list", method="GET", path="/api/jobs", help="列任务", query=["state", "project_key", "limit"], columns=["id", "state", "kind", "title", "instanceId", "progress", "workflowName", "mediaIds"]),
    dict(group="job", name="get", method="GET", path="/api/jobs/{job_id}", help="一个任务的现状与产物"),
    dict(group="job", name="plan", method="POST", path="/api/jobs/plan", help="入队前预检", data=True),
    dict(group="job", name="cancel", method="POST", path="/api/jobs/{job_id}/cancel", help="取消"),
    dict(group="job", name="retry", method="POST", path="/api/jobs/{job_id}/retry", help="重试"),
    dict(group="job", name="update", method="PATCH", path="/api/jobs/{job_id}", help="改优先级/标题", data=True),
    # 媒体与版本
    dict(group="media", name="list", method="GET", path="/api/media", help="列媒体", query=["project_key", "role", "ref_id", "kind", "ids", "limit"], columns=["id", "kind", "role", "refId", "projectKey", "path", "createdAt"]),
    dict(group="media", name="versions", method="GET", path="/api/media-versions", help="同一卡片的版本历史", query=["project_key", "role", "ref_id"]),
    dict(group="media", name="trash", method="DELETE", path="/api/media/{media_id}", help="产物进回收站"),
    dict(group="media", name="restore", method="POST", path="/api/media/{media_id}/restore", help="从回收站恢复"),
    dict(group="media", name="purge", method="DELETE", path="/api/media/{media_id}/purge", help="彻底删除（真删文件）", silent=True),
    dict(group="trash", name="list", method="GET", path="/api/trash", help="回收站清单", query=["project_key", "limit"]),
    dict(group="script", name="list", method="GET", path="/api/script-versions", help="剧本版本", query=["project_key", "limit"], columns=["id", "version", "isCurrent", "source", "createdAt"]),
    dict(group="script", name="current", method="POST", path="/api/script-versions/{version_id}/current", help="设为当前版"),
    dict(group="script", name="trash", method="DELETE", path="/api/script-versions/{version_id}", help="剧本版本进回收站"),
    dict(group="script", name="restore", method="POST", path="/api/script-versions/{version_id}/restore", help="恢复剧本版本"),
    dict(group="script", name="purge", method="DELETE", path="/api/script-versions/{version_id}/purge", help="彻底删除剧本版本", silent=True),
    # 系统
    dict(group="system", name="paths", method="GET", path="/api/system/paths", help="目录设置"),
    dict(group="system", name="set-paths", method="PUT", path="/api/system/paths", help="改目录（admin）", data=True),
    dict(group="system", name="storage", method="GET", path="/api/system/storage", help="磁盘与媒体占用"),
    dict(group="system", name="gc", method="POST", path="/api/system/gc", help="清孤儿媒体（admin）", data=True, body_defaults={"dryRun": True}),
    dict(group="system", name="audit", method="GET", path="/api/system/audit", help="审计流水（admin）", query=["limit", "action"]),
    dict(group="system", name="backup", method="GET", path="/api/system/backup", help="备份命令建议（admin）"),
    dict(group="system", name="gpu", method="GET", path="/api/system/gpu", help="单卡仲裁状态"),
    dict(group="system", name="gpu-restore", method="POST", path="/api/system/gpu/restore", help="拉回被让开的文本模型（admin）"),
    dict(group="system", name="gpu-yield", method="POST", path="/api/system/gpu/yield", help="主动让显存（admin）"),
    dict(group="system", name="styles", method="GET", path="/api/styles", help="风格预设"),
    dict(group="user", name="list", method="GET", path="/api/users", help="用户清单（admin）"),
    dict(group="user", name="create", method="POST", path="/api/users", help="建用户（admin）", data=True),
    dict(group="user", name="update", method="PATCH", path="/api/users/{user_id}", help="改用户（admin）", data=True),
    dict(group="user", name="delete", method="DELETE", path="/api/users/{user_id}", help="删用户（admin）", silent=True),
]


# ---------------------------------------------------------------- 需要真逻辑的子命令


def cmd_job_submit(args: argparse.Namespace) -> int:
    body = _load_body(args.data) or {}
    body.update({k: v for k, v in {"kind": args.kind, "template": args.template, "title": args.title, "projectKey": args.project_key, "instanceId": args.instance_id, "priority": args.priority}.items() if v is not None})
    for flag, key in (("slots", "slots"), ("models", "models"), ("meta", "meta")):
        value = getattr(args, flag.replace("-", "_"), None)
        if value:
            body[key] = _load_body(value)

    async def _go(c: H3Client) -> Any:
        if args.batch:
            return await c.post("/api/jobs/batch", {"jobs": body.get("jobs") or json.loads(Path(args.batch).read_text(encoding="utf-8"))})
        return await c.post("/api/jobs", body)

    try:
        data = asyncio.run(_with_client(args, _go))
    except H3Error as exc:
        print(f"{exc.status} {exc.detail}", file=sys.stderr)
        return 1
    _out(args, data, columns=["id", "state", "kind", "title", "workflowName", "instanceId"])
    if not args.json and not args.wait:
        jobs = data.get("jobs") if isinstance(data, dict) and "jobs" in data else [data]
        ids = [j.get("id") for j in jobs or [] if isinstance(j, dict) and j.get("id")]
        if ids:
            print(f"\n已入队 {len(ids)} 条：h3 job watch {' '.join(ids[:5])}", file=sys.stderr)
        rejected = (data or {}).get("errors") if isinstance(data, dict) else None
        if rejected:
            print(f"被拦下 {len(rejected)} 条：{json.dumps(rejected, ensure_ascii=False)[:400]}", file=sys.stderr)
    if args.wait:
        ids = [j.get("id") for j in (data.get("jobs") if isinstance(data, dict) else [data]) or [] if isinstance(j, dict) and j.get("id")]
        for jid in ids:
            final = asyncio.run(_with_client(args, lambda c, jid=jid: c.wait_for_job(jid, timeout_s=args.timeout, on_update=lambda s: print(f"  {jid} {s.get('state')} {s.get('progress')}", file=sys.stderr) if not args.json else None)))
            print(json.dumps(final, ensure_ascii=False, default=str))
    return 0


def cmd_job_watch(args: argparse.Namespace) -> int:
    seen: set[str] = set()

    def show(state: dict[str, Any]) -> None:
        seen.add(str(state.get("id")))
        print(json.dumps({k: state.get(k) for k in ("id", "state", "progress", "mediaIds", "error")}, ensure_ascii=False, default=str))

    async def _go(c: H3Client) -> Any:
        for jid in args.job_ids:
            await c.wait_for_job(jid, timeout_s=args.timeout, on_update=show)
        return None

    asyncio.run(_with_client(args, _go))
    return 0


def cmd_media_download(args: argparse.Namespace) -> int:
    cfg = _config(args)
    dest = Path(args.out) if args.out else Path.cwd() / f"media-{args.media_id}"

    async def _go(c: H3Client) -> Any:
        return await c.download(f"/api/media/{args.media_id}/raw", dest, params={"trashed": 1} if args.trashed else None)

    try:
        out = asyncio.run(_with_client(args, _go))
    except H3Error as exc:
        print(f"{exc.status} {exc.detail}", file=sys.stderr)
        return 1
    print(str(out))
    return 0


def cmd_media_upload(args: argparse.Namespace) -> int:
    fields = {k: v for k, v in {"project_key": args.project_key, "role": args.role, "ref_id": args.ref_id}.items() if v}

    async def _go(c: H3Client) -> Any:
        return await c.upload("/api/media/upload", Path(args.path), fields=fields)

    try:
        data = asyncio.run(_with_client(args, _go))
    except H3Error as exc:
        print(f"{exc.status} {exc.detail}", file=sys.stderr)
        return 1
    _out(args, data)
    return 0


def cmd_parse(args: argparse.Namespace) -> int:
    async def _go(c: H3Client) -> Any:
        return await c.upload("/api/parse-script", Path(args.path))

    try:
        data = asyncio.run(_with_client(args, _go))
    except H3Error as exc:
        print(f"{exc.status} {exc.detail}", file=sys.stderr)
        return 1
    _out(args, data)
    return 0


def cmd_script_save(args: argparse.Namespace) -> int:
    text = Path(args.file).read_text(encoding="utf-8") if args.file else sys.stdin.read()
    body: dict[str, Any] = {"projectKey": args.project_key, "text": text, "source": args.source, "setCurrent": not args.no_current}
    if args.backfill:
        payload = _load_body(args.backfill)
        body["backfillFrom"] = payload if isinstance(payload, dict) else {"text": payload}

    async def _go(c: H3Client) -> Any:
        return await c.post("/api/script-versions", body)

    try:
        data = asyncio.run(_with_client(args, _go))
    except H3Error as exc:
        print(f"{exc.status} {exc.detail}", file=sys.stderr)
        return 1
    _out(args, data)
    return 0


def cmd_llm_run(args: argparse.Namespace) -> int:
    raw = Path(args.file).read_text(encoding="utf-8") if args.file else args.input
    if not raw:
        print("给 --input 或 --file（剧本正文）", file=sys.stderr)
        return 2
    body = {k: v for k, v in {
        "purpose": args.purpose,
        "input": raw,
        "backendId": args.backend_id,
        "model": args.model,
        "script": args.script,
        "mode": args.mode,
        "targetSec": args.target_sec,
        "pace": args.pace,
        "durationSec": args.duration_sec,
        "aspect": args.aspect,
        "style": args.style,
    }.items() if v is not None}
    if args.messages:
        body["messages"] = _load_body(args.messages)

    async def _go(c: H3Client) -> Any:
        return await c.post("/api/llm/run", body)

    try:
        data = asyncio.run(_with_client(args, _go))
    except H3Error as exc:
        print(f"{exc.status} {exc.detail}", file=sys.stderr)
        return 1
    _out(args, data)
    return 0


def cmd_workflow_import(args: argparse.Namespace) -> int:
    extra = {"ui_file": Path(args.ui_file)} if args.ui_file else None
    fields = {k: v for k, v in {"name": args.name, "description": args.description, "instance_id": args.instance_id, "priority": str(args.priority) if args.priority is not None else None, "tags": args.tags}.items() if v is not None}

    async def _go(c: H3Client) -> Any:
        return await c.upload("/api/workflows/import", Path(args.path), fields=fields, extra_files=extra)

    try:
        data = asyncio.run(_with_client(args, _go))
    except H3Error as exc:
        print(f"{exc.status} {exc.detail}", file=sys.stderr)
        return 1
    _out(args, data)
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    kind = args.format

    async def _go(c: H3Client) -> Any:
        if kind == "merge":
            body = {"mediaIds": [int(x) for x in args.media_ids.split(",")], "title": args.title, "reencode": args.reencode}
            return await c.post(f"/api/projects/{args.project_key}/export/merge", body)
        if kind == "pack":
            items = _load_body(args.items or args.timeline)
            return await c.post(f"/api/projects/{args.project_key}/export/pack", {"items": items, "title": args.title})
        shots = _load_body(args.timeline)
        if isinstance(shots, dict):
            shots = shots.get("shots", shots)
        return await c.post(f"/api/projects/{args.project_key}/export/{kind}", {"shots": shots, "title": args.title})

    try:
        data = asyncio.run(_with_client(args, _go))
    except H3Error as exc:
        print(f"{exc.status} {exc.detail}", file=sys.stderr)
        return 1
    _out(args, data)
    return 0


# ---------------------------------------------------------------- 装配


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="h3",
        description="H3 Studio 命令行：AI 视频生成工作台的运维、流程驱动与智能体接入",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=QUICKSTART,
    )
    p.add_argument("--server", help=f"后端地址（默认 {DEFAULT_SERVER}，或 ~/.h3/config.json / H3_SERVER）")
    p.add_argument("--token", help="agent token（覆盖配置与 H3_TOKEN）")
    p.add_argument("--json", action="store_true", help="原样输出 JSON")
    p.add_argument("--timeout", type=float, help="单次请求超时秒数（默认 120）")
    p.add_argument("--version", action="version", version="h3-cli 0.1.0（MCP 工具面 0.1.0）")
    sub = p.add_subparsers(dest="cmd", metavar="命令")

    # --- 引导
    s = sub.add_parser("setup", help="一键：环境→依赖→库→迁移→起服务→发钥匙→写客户端 MCP 配置")
    s.add_argument("--dry-run", action="store_true", help="只报告将做什么")
    s.add_argument("--scope", action="append", choices=["read", "dispatch", "admin"], help="agent token 权限档，可多次；默认 read+dispatch")
    s.add_argument("--ttl-days", type=int, help="钥匙有效期天数；不给就是不过期")
    s.add_argument("--client", action="append", choices=["auto", "qoder", "claude", "cursor", "windsurf"], help="往哪些客户端写配置，可多次；不指定就是自动探测")
    s.add_argument("--qoder-scope", choices=["local", "user", "project"], default="local", help="Qoder 的落点：local=本项目 gitignored（默认）, user=全局, project=随 git 提交")
    s.add_argument("--username", help="用这个账号登录并发钥匙（默认当前凭据）")
    s.add_argument("--password")
    s.add_argument("--url", help="写进客户端的是远程 HTTP 地址而不是本机 stdio，例如 http://192.168.1.20:8790/mcp")
    s.add_argument("--force", action="store_true", help="已有 token 也重发一把")
    s.add_argument("--restart-backend", action="store_true", help="后端在跑也重起一次")
    s.set_defaults(fn=cmd_setup)

    s = sub.add_parser("doctor", help="体检：后端/库/实例/文本模型/凭据/端口/客户端配置")
    s.add_argument("--strict", action="store_true", help="有问题项就返回非零（CI 用）")
    s.set_defaults(fn=cmd_doctor)

    s = sub.add_parser("serve", help="起后端（前台）")
    s.add_argument("--host", help="监听地址；默认按配置的 server 地址。非环回会关掉演示账号")
    s.add_argument("--port", type=int)
    s.add_argument("--reload", action="store_true")
    s.set_defaults(fn=cmd_serve)

    s = sub.add_parser("desktop", help="桌面形态一键起：内嵌 PG → 迁移 → 起服务 → 写 desktop.json（装机的 exe 走的就是这条路）")
    s.add_argument("--host", default="127.0.0.1", help="只建议环回；要给局域网用请还是走 h3 serve")
    s.add_argument("--data-root", help="数据目录（媒体/内嵌库/日志/密钥）；不给就按平台落到用户数据目录")
    s.add_argument("--port", type=int, help="优先用这个端口，被占了自动换随机口")
    s.set_defaults(fn=cmd_desktop)

    s = sub.add_parser("status", help="现场一屏：实例、队列、显存、磁盘")
    s.set_defaults(fn=cmd_status)

    sub.add_parser("quickstart", help="打印接入引导").set_defaults(fn=cmd_quickstart)

    # --- 凭据
    s = sub.add_parser("login", help="用账号口令登录（写 JWT 会话到 ~/.h3/config.json）")
    s.add_argument("-u", "--username")
    s.add_argument("-p", "--password")
    s.add_argument("--no-token", action="store_true", help="登录时忽略本地已有 agent token")
    s.set_defaults(fn=cmd_login)

    s = sub.add_parser("whoami", help="当前凭据是谁、能干什么")
    s.set_defaults(fn=cmd_whoami)

    s = sub.add_parser("bootstrap", help="空库时建第一个账号并登录（有账号了会拒）")
    s.add_argument("-u", "--username", default="admin")
    s.add_argument("-p", "--password")
    s.set_defaults(fn=cmd_bootstrap)

    s = sub.add_parser("logout", help="清本地会话")
    s.add_argument("--revoke-token", action="store_true", help="连本地 agent token 一起丢（不吊销服务端那份）")
    s.set_defaults(fn=cmd_logout)

    sub.add_parser("config", help="看本地配置（脱敏）").set_defaults(fn=cmd_config_show)

    t = sub.add_parser("token", help="agent token：发/查/吊销").add_subparsers(dest="action", metavar="动作")
    s = t.add_parser("create", help="发一把（admin）")
    s.add_argument("--name", required=True)
    s.add_argument("--scope", action="append", choices=["read", "dispatch", "admin"], default=None)
    s.add_argument("--ttl-days", type=int)
    s.add_argument("--store", action="store_true", help="同时写进本地配置（给别人用时别加）")
    s.add_argument("-u", "--username", help="先用这个账号登录再发")
    s.add_argument("-p", "--password")
    s.set_defaults(fn=cmd_token_create, scopes=["read", "dispatch"])
    s = t.add_parser("list", help="列出所有钥匙（admin）")
    s.set_defaults(fn=cmd_token_list)
    s = t.add_parser("revoke", help="吊销")
    s.add_argument("token_id", type=int)
    s.set_defaults(fn=cmd_token_revoke)

    # --- MCP
    m = sub.add_parser("mcp", help="MCP server：serve / config / install / tools / test").add_subparsers(dest="action", metavar="动作")
    s = m.add_parser("serve", help="起 MCP server（默认 stdio）")
    s.add_argument("--transport", choices=["stdio", "http"], default="stdio")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int)
    s.add_argument("--path", default="/mcp")
    s.add_argument("--allow-host", action="append", help="HTTP 模式下允许的主机名/Host 头，可多次")
    s.add_argument("--allow-all-hosts", action="store_true", help="关掉 DNS 重绑定保护（只剩 Bearer 门禁）")
    s.add_argument("--log-level", default="INFO")
    s.set_defaults(fn=cmd_mcp_serve)
    s = m.add_parser("config", help="打印可直接粘贴的 mcpServers 片段")
    s.add_argument("--client", default="none", choices=["none", "qoder", "claude", "cursor", "windsurf"])
    s.add_argument("--url", help="给远程接入用：streamable-http 地址")
    s.add_argument("--token", help="配 --url 时写进 headers 的 token；传空串则留占位符")
    s.add_argument("--raw", action="store_true", help="只输出条目本体，不套 mcpServers")
    s.set_defaults(fn=cmd_mcp_config)
    s = m.add_parser("install", help="写入客户端 MCP 配置（改前自动备份）")
    s.add_argument("--client", action="append", choices=["auto", "qoder", "claude", "cursor", "windsurf", "none"], help="写哪些客户端，可多次；不指定就是自动探测")
    s.add_argument("--qoder-scope", choices=["local", "user", "project"], default="local")
    s.add_argument("--url", help="写远程 HTTP 入口而不是本机 stdio")
    s.add_argument("--remove", action="store_true", help="卸载：把条目从配置里摘掉")
    s.set_defaults(fn=cmd_mcp_install)
    s = m.add_parser("tools", help="列出 MCP 工具")
    s.set_defaults(fn=cmd_mcp_tools)
    s = m.add_parser("test", help="真跑一次 MCP 握手并调用一个只读工具")
    s.add_argument("--call", default="health", help="要试调的工具名（默认 health）")
    s.set_defaults(fn=cmd_mcp_test)

    # --- 暴露
    s = sub.add_parser("expose", help="对外暴露：lan / cloud / status / off")
    s.add_argument("action", choices=["lan", "cloud", "status", "off"])
    s.add_argument("--port", type=int, help=f"MCP 端口，默认 {DEFAULT_MCP_PORT}")
    s.add_argument("--detach", action="store_true", help="后台跑，不占终端")
    s.add_argument("--print-only", action="store_true", help="只打印要怎么连，不真起")
    s.add_argument("--install-cloudflared", action="store_true", help="缺 cloudflared 时用 winget 装（会弹 UAC）")
    s.set_defaults(fn=cmd_expose)

    # --- 表驱动
    groups: dict[str, Any] = {}

    def grp(name: str, help_: str):
        if name not in groups:
            groups[name] = sub.add_parser(name, help=help_).add_subparsers(dest="action", metavar="子命令")
        return groups[name]

    for spec in SIMPLE:
        gp = grp(spec["group"], GROUP_HELP[spec["group"]])
        s = gp.add_parser(spec["name"], help=spec["help"])
        for q in spec.get("query", []):
            qname = q.replace("_", "-")
            s.add_argument(f"--{qname}", dest=q, type=int if q in {"limit"} else str, help=f"查询参数 {q}")
        if spec.get("data") or str(spec["name"]) in {"create", "update", "set-paths", "put-defaults"}:
            s.add_argument("--data", help='请求体 JSON，或 @文件（{"name":"local","baseUrl":"http://127.0.0.1:8188"}）')
        s.set_defaults(fn=_make_handler(spec))

    # 带 {占位符} 的补位置参数
    for spec in SIMPLE:
        gp = groups[spec["group"]]
        s = gp.choices[spec["name"]]
        for ph in _placeholders(spec["path"]):
            s.add_argument(ph, help=f"路径参数 {ph}")

    # --- 有逻辑的命令
    jg = grp("job", GROUP_HELP["job"])
    s = jg.add_parser("submit", help="提交生成任务（dispatch）")
    s.add_argument("--kind", choices=["image", "video", "video_chain", "upscale", "audio", "workflow_test"])
    s.add_argument("--template", help="内置模板名，或 auto 让工作流库挑")
    s.add_argument("--title")
    s.add_argument("--project-key", help="浏览器里那个项目 id（产物归组用）")
    s.add_argument("--instance-id")
    s.add_argument("--priority", type=int)
    s.add_argument("--slots", help='JSON，如 {"6.text":"雨夜长街"}')
    s.add_argument("--models", help='JSON，如 {"127.unet_name":"..."}')
    s.add_argument("--meta", help='JSON，如 {"role":"keyframe_start","refId":"shot-3"}')
    s.add_argument("--data", help="整份请求体 JSON 或 @文件（覆盖上面这些）")
    s.add_argument("--batch", help="@jobs.json：一次提交多条")
    s.add_argument("--wait", action="store_true", help="阻塞到终态")
    s.add_argument("--timeout", type=float, default=3600)
    s.set_defaults(fn=cmd_job_submit)
    s = jg.add_parser("watch", help="等若干任务到终态")
    s.add_argument("job_ids", nargs="+")
    s.add_argument("--timeout", type=float, default=3600)
    s.set_defaults(fn=cmd_job_watch)

    mg = grp("media", GROUP_HELP["media"])
    s = mg.add_parser("download", help="下载产物到本地文件")
    s.add_argument("media_id", type=int)
    s.add_argument("-o", "--out")
    s.add_argument("--trashed", action="store_true", help="回收站里的东西也要能取（后端需 ?trashed=1 才放行）")
    s.set_defaults(fn=cmd_media_download)
    s = mg.add_parser("upload", help="上传素材")
    s.add_argument("path")
    s.add_argument("--project-key")
    s.add_argument("--role", default="ref_image")
    s.add_argument("--ref-id")
    s.set_defaults(fn=cmd_media_upload)

    sg = grp("script", GROUP_HELP["script"])
    s = sg.add_parser("save", help="存一版剧本正文（stdin 或 --file）")
    s.add_argument("--project-key", required=True)
    s.add_argument("--file")
    s.add_argument("--source", default="cli")
    s.add_argument("--no-current", action="store_true", help="存版本但不设为当前版")
    s.add_argument("--backfill", help='@旧稿.txt 或 {"text":...,"writtenAt":...}：首次存版把旧稿一起交上来')
    s.set_defaults(fn=cmd_script_save)

    lg = grp("llm", GROUP_HELP["llm"])
    s = lg.add_parser("run", help="跑一个文本用途（拆解/分镜/提示词…）")
    s.add_argument("--purpose", required=True, choices=["script_parse", "storyboard", "visualize", "h3_prompt", "script_write", "script_chat", "embed"])
    s.add_argument("--input")
    s.add_argument("--file", help="从文件读输入（剧本正文常走这条）")
    s.add_argument("--backend-id")
    s.add_argument("--model")
    s.add_argument("--script", help="script_chat 用：编辑器里的当前正文")
    s.add_argument("--messages", help='script_chat 用：[{"role":"user","content":"..."}] 或 @文件')
    s.add_argument("--mode", choices=["three_field", "six_section", "wenwu", "hybrid"])
    s.add_argument("--target-sec", dest="target_sec", type=int)
    s.add_argument("--pace")
    s.add_argument("--duration-sec", dest="duration_sec", type=float)
    s.add_argument("--aspect")
    s.add_argument("--style")
    s.set_defaults(fn=cmd_llm_run)

    wg = grp("workflow", GROUP_HELP["workflow"])
    s = wg.add_parser("import", help="从本地 JSON 导入（admin）")
    s.add_argument("path")
    s.add_argument("--ui-file", help="同时给一份 UI 格式，便于在 ComfyUI 里打开")
    s.add_argument("--name")
    s.add_argument("--description")
    s.add_argument("--instance-id")
    s.add_argument("--priority", type=int)
    s.add_argument("--tags", help="逗号分隔")
    s.set_defaults(fn=cmd_workflow_import)

    xg = grp("export", GROUP_HELP["export"])
    s = xg.add_parser("run", help="导出：merge / pack / edl / xml / jianying")
    s.add_argument("format", choices=["merge", "pack", "edl", "xml", "jianying"])
    s.add_argument("--project-key", required=True)
    s.add_argument("--media-ids", help="merge 用：逗号分隔的 mediaId，按顺序拼")
    s.add_argument("--timeline", help='分镜表 JSON 或 @文件：[{"index":1,"durationSec":5,"mediaId":12}]；pack 时它当 items')
    s.add_argument("--items", help="pack 用：[{'mediaId':1,'path':'characters/a.png'}]")
    s.add_argument("--title")
    s.add_argument("--reencode", action="store_true")
    s.set_defaults(fn=cmd_export)

    s = sub.add_parser("parse", help="上传剧本文件做结构化解析（角色/场景/镜头骨架）")
    s.add_argument("path")
    s.set_defaults(fn=cmd_parse)

    s = sub.add_parser("routes", help="列后端所有端点（配合 h3 api 打通达）")
    s.add_argument("--filter", help="只列路径含这个子串的端点，例如 --filter jobs")
    s.set_defaults(fn=cmd_routes)
    s = sub.add_parser("api", help="直接打任意端点")
    s.add_argument("method")
    s.add_argument("path")
    s.add_argument("--data", help="请求体 JSON 或 @文件")
    s.add_argument("--param", action="append", help="查询参数 key=value，可多次")
    s.set_defaults(fn=cmd_api)

    p.set_defaults(fn=lambda a: (a.print_help(), 0)[1])
    return p


GROUP_HELP = {
    "instance": "生成实例（ComfyUI / RunningHub）",
    "workflow": "工作流库与自动选择",
    "llm": "文本模型后端",
    "job": "任务队列",
    "media": "产物媒体",
    "script": "剧本版本历史",
    "trash": "生成回收站",
    "system": "系统设置、显存、审计、备份",
    "user": "用户（admin）",
    "export": "制片导出",
}


def _placeholders(path: str) -> list[str]:
    import re

    return re.findall(r"\{(\w+)\}", path)


def _force_utf8() -> None:
    """控制台脚本 `h3.exe` 不带 -X utf8，中文 Windows 上会按 GBK 输出，Git Bash 里就是乱码。

    统一改成 UTF-8：现代终端（Windows Terminal / PowerShell 7 / Git Bash）都吃这套；
    真的遇到老 cmd.exe 的代码页问题，用 `set PYTHONUTF8=1` 或 chcp 65001。
    """
    if sys.platform != "win32":
        return
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        try:
            if stream is not None and (stream.encoding or "").replace("-", "").lower() not in {"utf8", "utf-8"}:
                stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def main(argv: list[str] | None = None) -> int:
    _force_utf8()
    parser = build_parser()
    args = parser.parse_args(argv)
    fn = getattr(args, "fn", None)
    if fn is None:
        parser.print_help()
        return 0
    try:
        return int(fn(args) or 0)
    except H3Error as exc:
        print(f"{exc.status} {exc.detail}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"参数不对：{exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
    except FileNotFoundError as exc:
        print(f"找不到文件：{exc.filename}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
