"""h3 CLI/MCP 的本地配置与凭据。

放在 `~/.h3/config.json`（不是仓库里）：这台机器上跑的是真实显存和真实计费，
一把 admin scope 的钥匙混进 git 或混进项目目录都可能被顺手带走。

优先级：命令行参数 > 环境变量（H3_SERVER / H3_TOKEN）> 配置文件。
"""

from __future__ import annotations

import json
import os
import stat
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_SERVER = "http://127.0.0.1:8788"
DEFAULT_MCP_PORT = 8790


def config_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("USERPROFILE") or Path.home())
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
        # POSIX 上没有 USERPROFILE 概念，统一落到 ~/.h3 与 Windows 观感一致
        if "XDG_CONFIG_HOME" not in os.environ:
            base = Path.home()
    return base / ".h3"


def config_path() -> Path:
    return config_dir() / "config.json"


@dataclass
class Config:
    server: str = DEFAULT_SERVER
    token: str | None = None
    mcp_port: int = DEFAULT_MCP_PORT
    # 浏览器式登录（JWT）留下的会话；agent token 优先，没 token 才用它
    auth: dict[str, Any] = field(default_factory=dict)
    expose: dict[str, Any] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def has_credential(self) -> bool:
        return bool(self.token or self.auth.get("access"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "server": self.server,
            "token": self.token,
            "mcpPort": self.mcp_port,
            "auth": self.auth,
            "expose": self.expose,
            **self.extra,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Config":
        known = {"server", "token", "mcpPort", "auth", "expose"}
        return cls(
            server=str(raw.get("server") or DEFAULT_SERVER).rstrip("/"),
            token=raw.get("token"),
            mcp_port=int(raw.get("mcpPort") or DEFAULT_MCP_PORT),
            auth=dict(raw.get("auth") or {}),
            expose=dict(raw.get("expose") or {}),
            extra={k: v for k, v in raw.items() if k not in known},
        )


def load() -> Config:
    path = config_path()
    raw: dict[str, Any] = {}
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # 配置文件坏了要说清楚，别静默变成「未登录」
            print(f"警告：{path} 解析失败（{exc}），本次按空配置运行", file=sys.stderr)
            raw = {}
    cfg = Config.from_dict(raw)
    if os.environ.get("H3_SERVER"):
        cfg.server = os.environ["H3_SERVER"].rstrip("/")
    if os.environ.get("H3_TOKEN"):
        cfg.token = os.environ["H3_TOKEN"]
    return cfg


def save(cfg: Config) -> Path:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    if sys.platform != "win32":
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    return path


def redact(cfg: Config) -> dict[str, Any]:
    """给 `h3 config show` / 诊断输出用的脱敏视图。"""
    d = cfg.to_dict()
    if d.get("token"):
        d["token"] = d["token"][:9] + "…" + d["token"][-4:]
    if d.get("auth", {}).get("access"):
        d["auth"] = {"access": "…(jwt)", "expiresAt": d["auth"].get("expiresAt")}
    return d
