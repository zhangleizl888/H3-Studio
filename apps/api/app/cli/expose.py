"""对外暴露：默认只监听环回，两条命令升级到局域网 / 公网隧道。

为什么暴露的是 MCP 端口（默认 8790）而不是后端 8788：
- 智能体要的是 MCP 的工具面，不是 REST；把 8788 摊出去等于连 `/docs`、用户管理一起给外人。
- MCP 进程访问后端走 127.0.0.1，后端保持只监听环回，前端页面与 REST 契约都不受影响。
- 门禁收在一处：Bearer agent token + 每把钥匙可单独吊销。

cloudflared 那条用的是 quick tunnel（`--url`），拿随机 trycloudflare 域名、无需账号。
它每次重启换域名，所以定位是「临时给人/给云端智能体接一次」，不是常驻生产入口。
"""

from __future__ import annotations

import re
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from . import proc
from .mcpconfig import api_root, http_entry, render_json
from .store import Config, save

TUNNEL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
CLOUDFLARED_HINT = r"""没找到 cloudflared。装一个（任选）：
  winget install --id Cloudflare.cloudflared -e
  choco install cloudflared
  或到 https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/downloads/ 下载后把目录加进 PATH
装好再跑一次 h3 expose cloud。（--install-cloudflared 可以让我替你跑 winget 那条，需要联网且会弹 UAC）"""


def log_dir() -> Path:
    from .store import config_dir

    d = config_dir() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def find_cloudflared() -> str | None:
    return shutil.which("cloudflared") or shutil.which("cloudflared.exe")


def _mcp_argv(cfg: Config, *, host: str, port: int) -> list[str]:
    import sys

    return [sys.executable, "-m", "app.cli", "mcp", "serve", "--transport", "http", "--host", host, "--port", str(port)]


def _state(cfg: Config) -> dict[str, Any]:
    return dict(cfg.expose or {})


def _set_state(cfg: Config, **kw: Any) -> None:
    st = dict(cfg.expose or {})
    st.update(kw)
    st["updatedAt"] = datetime.now().isoformat(timespec="seconds")
    cfg.expose = {k: v for k, v in st.items() if v is not None}
    save(cfg)


def ensure_mcp_running(cfg: Config, *, host: str, port: int, detach: bool) -> tuple[int | None, bool]:
    """返回 (pid, 是否由本次新起)。已经在听的端口就不重复起。"""
    if proc.port_listening(port, host="127.0.0.1"):
        return (int((cfg.expose or {}).get("mcpPid") or 0) or None, False)
    if not detach:
        # 前台模式由调用方自己 exec
        return (None, False)
    pid = proc.spawn_detached(
        _mcp_argv(cfg, host=host, port=port),
        log_path=log_dir() / f"mcp-{port}.log",
        cwd=api_root(),
        env={"PYTHONPATH": str(api_root()), "H3_SERVER": cfg.server, **({"H3_TOKEN": cfg.token} if cfg.token else {})},
    )
    if not proc.wait_port(port, timeout=25):
        raise RuntimeError(f" MCP 入口没能在 25 秒内监听 {host}:{port}，看日志 {log_dir() / f'mcp-{port}.log'}")
    return (pid, True)


def expose_lan(cfg: Config, *, port: int, detach: bool, print_only: bool) -> int:
    host = "0.0.0.0"
    lan_ips = _lan_ips()
    if print_only:
        _print_lan_plan(cfg, port, lan_ips)
        return 0
    pid, started = ensure_mcp_running(cfg, host=host, port=port, detach=True) if detach else (None, False)
    if not detach:
        from ..mcp.serve import run_http

        _print_lan_plan(cfg, port, lan_ips)
        run_http(cfg, host=host, port=port, allow_hosts=lan_ips, allow_all_hosts=True)
        return 0
    _set_state(cfg, mode="lan", mcpPort=port, mcpPid=pid, tunnelUrl=None, tunnelPid=None)
    print(f"已把 MCP 入口绑到所有网卡的 :{port}{'（新起进程 PID %s）' % pid if started else '（复用已在跑的进程）'}")
    _print_lan_plan(cfg, port, lan_ips)
    return 0


def _lan_ips() -> list[str]:
    import socket

    out: list[str] = []
    try:
        # 不真发包，只借路由表问一句「出去走哪个本机地址」
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        out.append(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in out and not ip.startswith("127."):
                out.append(ip)
    except OSError:
        pass
    return out


def _print_lan_plan(cfg: Config, port: int, ips: list[str]) -> None:
    token = cfg.token or "h3_at_在此粘贴你的 agent token"
    ip = ips[0] if ips else "<本机局域网IP>"
    print("\n给同网段的智能体的接入配置（streamable-http）：")
    print(render_json(http_entry(f"http://{ip}:{port}/mcp", token)))
    print("⚠ 这条配置里带明文 token：一旦被写进客户端配置文件，就等于把生成流程交给拿到该文件的人。")
    print("  用独立发放、可单独吊销的 agent token，收工跑 h3 expose off。")


def expose_cloud(cfg: Config, *, port: int, detach: bool, install_cloudflared: bool) -> int:
    binary = find_cloudflared()
    if not binary and install_cloudflared:
        code = _winget_install_cloudflared()
        if code != 0:
            print(CLOUDFLARED_HINT)
            return code
        binary = find_cloudflared()
    if not binary:
        print(CLOUDFLARED_HINT)
        return 3

    pid, started = ensure_mcp_running(cfg, host="127.0.0.1", port=port, detach=True)
    if started:
        print(f"已拉起本机 MCP 入口 127.0.0.1:{port}（PID {pid}）")
    else:
        print(f"复用已在跑的本机 MCP 入口 127.0.0.1:{port}")

    log = log_dir() / "cloudflared.log"
    argv = [binary, "tunnel", "--url", f"http://127.0.0.1:{port}", "--no-autoupdate"]
    if detach:
        tpid = proc.spawn_detached(argv, log_path=log, cwd=api_root())
        url = _wait_tunnel_url(log)
        _set_state(cfg, mode="cloud", mcpPort=port, mcpPid=pid, tunnelPid=tpid, tunnelUrl=url)
        return _report_cloud(cfg, url, port)

    from subprocess import Popen

    print("[cloudflared] 前台运行中（Ctrl+C 结束隧道）…")
    with log.open("ab") as fh:
        proc_obj = Popen(argv, stdout=fh, stderr=fh, cwd=str(api_root()))
        try:
            url = _wait_tunnel_url(log)
            _set_state(cfg, mode="cloud", mcpPort=port, mcpPid=pid, tunnelPid=proc_obj.pid, tunnelUrl=url)
            _report_cloud(cfg, url, port)
            proc_obj.wait()
        except KeyboardInterrupt:
            proc_obj.terminate()
        finally:
            _set_state(cfg, mode=None if (cfg.expose or {}).get("mode") != "cloud" else "cloud", tunnelUrl=None, tunnelPid=None)
    return 0


def _winget_install_cloudflared() -> int:
    winget = shutil.which("winget")
    if not winget:
        print("没有 winget，装不了 cloudflared。")
        return 4
    print("[setup] 正在通过 winget 安装 cloudflared（会弹 UAC）…")
    import subprocess

    return subprocess.run([winget, "install", "--id", "Cloudflare.cloudflared", "-e", "--accept-package-agreements", "--accept-source-agreements"], shell=False).returncode


def _wait_tunnel_url(log: Path, *, timeout: float = 40.0) -> str | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            text = log.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        found = TUNNEL_RE.findall(text[-8000:])
        if found:
            return found[-1]
        time.sleep(1.0)
    return None


def _report_cloud(cfg: Config, url: str | None, port: int) -> int:
    if not url:
        print("40 秒内没从日志里看到 trycloudflare 域名，隧道可能没起来：")
        print(f"  看 {log_dir() / 'cloudflared.log'}")
        return 5
    token = cfg.token or "h3_at_在此粘贴你的 agent token"
    print(f"\n隧道已就绪：{url}/mcp")
    print(render_json(http_entry(f"{url}/mcp", token)))
    print(f"日志：{log_dir() / 'cloudflared.log'}；收工执行 h3 expose off")
    print("⚠ 这是公网可达地址。trycloudflare 域名每次重启会变；token 一旦泄露，拿到它的人能提交生成任务（会花你的显存/云费）。")
    return 0


def expose_status(cfg: Config) -> int:
    st = _state(cfg)
    port = int(st.get("mcpPort") or cfg.mcp_port)
    listening = proc.port_listening(port)
    print(f"模式：{st.get('mode') or 'loopback（默认，未对外暴露）'}")
    print(f"MCP 端口 {port}：{'在听' if listening else '没在听'}")
    pid = st.get("mcpPid")
    if pid:
        print(f"  MCP 进程 {pid}：{'存活' if proc.pid_alive(int(pid)) else '已退出'}")
    tpid, curl = st.get("tunnelPid"), st.get("tunnelUrl")
    if tpid:
        print(f"cloudflared 进程 {tpid}：{'存活' if proc.pid_alive(int(tpid)) else '已退出'}")
    if curl:
        print(f"隧道地址：{curl}")
    print(f"后端 {cfg.server}：{'在听' if proc.http_get_json(cfg.server + '/healthz') else '没响应'}")
    print("本机的智能体不需要对外暴露：用 h3 mcp config --client qoder 写 stdio 配置即可")
    return 0


def expose_off(cfg: Config, *, keep_stdio_hint: bool = True) -> int:
    st = _state(cfg)
    done: list[dict[str, Any]] = []
    if st.get("tunnelPid"):
        done.append(proc.kill(int(st["tunnelPid"]), expect="cloudflared"))
    if st.get("mcpPid"):
        done.append(proc.kill(int(st["mcpPid"]), expect="app.cli"))
    _set_state(cfg, mode=None, mcpPid=None, tunnelPid=None, tunnelUrl=None)
    for row in done:
        mark = "已停" if row.get("killed") else "未停"
        print(f"· PID {row.get('pid')} {mark}：{row.get('reason') or row.get('cmdline', '')[:120]}")
    print("对外暴露已收回。后端仍然只在环回，本地 stdio 接入不受影响。")
    if keep_stdio_hint:
        print("要接入智能体跑 h3 mcp config --client qoder")
    return 0
