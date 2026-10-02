"""把 H3 的 MCP 入口写进各家智能体客户端的配置。

两个刻意的选择：

1. **stdio 配置里不放 token。** 子进程自己读 `~/.h3/config.json`，所以写进客户端的
   JSON 只有一条命令行 —— 不会把一把 admin 钥匙抄进可能被 dotfiles 同步、
   或被提交进仓库的第二份文件里。
2. **改文件前先备份，且不整篇重写。** 这三份 settings.json 里还装着别的设置，
   我们只动 `mcpServers` 那一片。
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

SERVER_NAME = "h3-studio"


def api_root() -> Path:
    """apps/api 目录（`app` 包的父目录）。

    冻结产物里 `__file__` 在 `_MEIPASS` 临时目录，往上跳三级跳出来的是随包资源根，
    所以判据交给 runtime，不在这个函数里数层数。
    """
    from .. import runtime

    return runtime.api_root()


def _home() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("USERPROFILE") or Path.home())
    return Path.home()


def _appdata() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA") or (_home() / "AppData" / "Roaming"))
    return _home() / "AppData"


@dataclass
class Target:
    id: str
    label: str
    path: Path
    # Qoder CLI 三份 settings 与其余客户端一样，条目都放在顶层 mcpServers 下
    key: str = "mcpServers"

    @property
    def installed(self) -> bool:
        """配置目录在 = 这个客户端大概率装过。没有目录也允许显式写入（用户可能要装）。"""
        return self.path.exists() or self.path.parent.exists()

    @property
    def exists(self) -> bool:
        return self.path.exists()


def qoder_user() -> Target:
    return Target("qoder-user", "Qoder CLI（全局，所有项目可用）", _home() / ".qoder-cn" / "settings.json")


def qoder_local(repo: Path) -> Target:
    return Target("qoder-local", "Qoder CLI（本项目，.qoder/settings.local.json，不进 git）", repo / ".qoder" / "settings.local.json")


def qoder_project(repo: Path) -> Target:
    return Target("qoder-project", "Qoder CLI（本项目共享，会随 git 提交）", repo / ".qoder" / "settings.json")


def claude_desktop() -> Target:
    return Target("claude", "Claude Desktop", _appdata() / "Claude" / "claude_desktop_config.json")


def cursor() -> Target:
    return Target("cursor", "Cursor", _home() / ".cursor" / "mcp.json")


def windsurf() -> Target:
    return Target("windsurf", "Windsurf", _home() / ".codeium" / "windsurf" / "mcp_config.json")


def all_targets(repo: Path) -> list[Target]:
    return [qoder_user(), qoder_local(repo), qoder_project(repo), claude_desktop(), cursor(), windsurf()]


def auto_detected(repo: Path, *, qoder_scope: str = "local") -> list[Target]:
    """自动探测：每家客户端最多一个落点。

    三个 Qoder 落点一起写会出现同一个 server 注册三遍；而 project 那份是要随 git
    提交的，绝不该出现在「自动」里 —— 要用它得显式 --qoder-scope project。
    """
    qoder_choice = {"user": qoder_user, "project": qoder_project}.get(qoder_scope, lambda _repo: qoder_local(_repo))
    out: list[Target] = []
    picked = qoder_choice(repo)
    if picked.installed or qoder_user().installed:
        out.append(picked)
    for other in (claude_desktop(), cursor(), windsurf()):
        if other.installed:
            out.append(other)
    return out


# ---------------------------------------------------------------- 条目构造


def stdio_entry(*, python: str | None = None, root: Path | None = None) -> dict[str, Any]:
    """本地同机接入：客户端直接起一个 stdio 子进程。"""
    py = python or sys.executable
    base = root or api_root()
    return {
        "command": py,
        "args": ["-m", "app.cli", "mcp", "serve", "--transport", "stdio"],
        "env": {"PYTHONPATH": str(base)},
        "cwd": str(base),
    }


def http_entry(url: str, token: str | None = None) -> dict[str, Any]:
    """远程接入：智能体连的是 MCP 的 streamable-http 入口（不是后端 8788）。"""
    entry: dict[str, Any] = {"type": "http", "url": url}
    if token:
        entry["headers"] = {"Authorization": f"Bearer {token}"}
    return entry


# ---------------------------------------------------------------- 读写


class ConfigWriteError(RuntimeError):
    pass


def _read(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ConfigWriteError(f"{path} 不是合法 JSON（{exc}）；先修好它再写入，不做覆盖") from exc
    if not isinstance(raw, dict):
        raise ConfigWriteError(f"{path} 顶层不是对象，拒写")
    return raw


def _backup(path: Path) -> Path | None:
    if not path.exists():
        return None
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = path.with_suffix(path.suffix + f".bak-{stamp}")
    shutil.copy2(path, dest)
    return dest


def install(target: Target, entry: dict[str, Any], *, name: str = SERVER_NAME, remove: bool = False) -> dict[str, Any]:
    """把一个条目写进 target。remove=True 是卸载。"""
    target.path.parent.mkdir(parents=True, exist_ok=True)
    raw = _read(target.path)
    servers = dict(raw.get(target.key) or {})
    before = servers.get(name)
    backup: Path | None = None

    if remove:
        if name not in servers:
            return {"target": target.id, "path": str(target.path), "changed": False, "reason": "本来就没有"}
        servers.pop(name)
        backup = _backup(target.path)
    else:
        if before == entry:
            return {"target": target.id, "path": str(target.path), "changed": False, "reason": "已是目标内容"}
        servers[name] = entry
        backup = _backup(target.path)

    raw[target.key] = servers
    target.path.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "target": target.id,
        "label": target.label,
        "path": str(target.path),
        "changed": True,
        "action": "removed" if remove else ("updated" if before else "added"),
        "backup": str(backup) if backup else None,
        "reload": reload_hint(target),
    }


def reload_hint(target: Target) -> str:
    if target.id.startswith("qoder"):
        return "在 qodercli 里执行 /mcp reload（或重启会话），/mcp 可确认是否已加载"
    if target.id == "claude":
        return "完全退出 Claude Desktop 再打开（托盘图标也要退）"
    if target.id == "cursor":
        return "Cursor → Settings → MCP → Refresh"
    return "重启该客户端的 MCP 连接"


def render_json(entry: dict[str, Any], *, name: str = SERVER_NAME) -> str:
    """给「只想自己粘贴」的人：一段可直接粘进 mcpServers 的 JSON。"""
    return json.dumps({"mcpServers": {name: entry}}, ensure_ascii=False, indent=2)
