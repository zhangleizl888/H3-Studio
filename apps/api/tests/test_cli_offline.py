"""离线用例：不需要后端与数据库，只验「写配置」与「权限档位」这两件会伤人的事。

- 客户端配置写入：必须备份、必须保留别人的设置、必须幂等、卸载要干净。
- scope 档位：读/派发/管理三档的推导与收口。
- MCP 工具面：注册成功、每个工具都有说明（智能体靠这行字决定调不调）。
- Host 白名单：默认值不能把环回自己拒掉（这条真踩过）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.cli import mcpconfig
from app.cli.store import Config, redact
from app.security import SCOPE_ADMIN, SCOPE_DISPATCH, SCOPE_READ, normalize_scopes, scope_required_for


# ---------------------------------------------------------------- scope


def test_normalize_scopes_补齐低档():
    assert normalize_scopes([SCOPE_ADMIN]) == [SCOPE_READ, SCOPE_DISPATCH, SCOPE_ADMIN]
    assert normalize_scopes([SCOPE_DISPATCH]) == [SCOPE_READ, SCOPE_DISPATCH]
    # 什么都没要 = 最低档 read。"默认给到 dispatch" 是 CLI/接口的选择，不是这个原语的
    normalize_scopes_default = normalize_scopes(None)
    assert normalize_scopes_default == [SCOPE_READ]


def test_normalize_scopes_拒绝拼错():
    with pytest.raises(ValueError):
        normalize_scopes(["wyd"])


@pytest.mark.parametrize(
    "roles, expected",
    [
        (None, SCOPE_READ),
        ({"admin"}, SCOPE_ADMIN),
        ({"admin", "editor"}, SCOPE_DISPATCH),
        ({"viewer"}, SCOPE_READ),
    ],
)
def test_scope_required_for(roles, expected):
    assert scope_required_for(roles) == expected


# ---------------------------------------------------------------- 客户端配置


@pytest.fixture
def target(tmp_path: Path) -> mcpconfig.Target:
    return mcpconfig.Target(id="t", label="测试客户端", path=tmp_path / "settings.json")


def test_install_建文件并写条目(target):
    entry = mcpconfig.stdio_entry()
    res = mcpconfig.install(target, entry)
    assert res["changed"] and res["action"] == "added" and res["backup"] is None
    raw = json.loads(target.path.read_text(encoding="utf-8"))
    assert raw["mcpServers"][mcpconfig.SERVER_NAME]["args"][-2:] == ["--transport", "stdio"]


def test_install_保住别人的设置并留下备份(target):
    target.path.write_text(json.dumps({"theme": "dark", "mcpServers": {"other": {"command": "x"}}}), encoding="utf-8")
    res = mcpconfig.install(target, mcpconfig.stdio_entry())
    raw = json.loads(target.path.read_text(encoding="utf-8"))
    assert raw["theme"] == "dark"
    assert set(raw["mcpServers"]) == {"other", mcpconfig.SERVER_NAME}
    assert res["backup"] and Path(res["backup"]).exists()
    # 备份里必须还是改前的原样
    assert mcpconfig.SERVER_NAME not in json.loads(Path(res["backup"]).read_text(encoding="utf-8"))["mcpServers"]


def test_install_幂等(target):
    entry = mcpconfig.stdio_entry()
    mcpconfig.install(target, entry)
    again = mcpconfig.install(target, entry)
    assert not again["changed"] and again["reason"] == "已是目标内容"


def test_install_同名条目是更新不是新增(target):
    mcpconfig.install(target, {"command": "old"})
    res = mcpconfig.install(target, {"command": "new"})
    assert res["action"] == "updated"
    assert json.loads(target.path.read_text(encoding="utf-8"))["mcpServers"][mcpconfig.SERVER_NAME] == {"command": "new"}


def test_remove_只摘自己(target):
    target.path.write_text(json.dumps({"mcpServers": {"keep": {"command": "k"}}}), encoding="utf-8")
    mcpconfig.install(target, mcpconfig.stdio_entry())
    res = mcpconfig.install(target, {}, remove=True)
    assert res["changed"] and res["action"] == "removed"
    assert set(json.loads(target.path.read_text(encoding="utf-8"))["mcpServers"]) == {"keep"}


def test_坏JSON_拒写不覆盖(target):
    target.path.write_text("{这不是 JSON", encoding="utf-8")
    with pytest.raises(mcpconfig.ConfigWriteError):
        mcpconfig.install(target, mcpconfig.stdio_entry())
    assert target.path.read_text(encoding="utf-8") == "{这不是 JSON"


def test_stdio条目不带token(target):
    """本机接入的配置里绝不能有密钥：子进程自己读 ~/.h3/config.json。"""
    blob = json.dumps(mcpconfig.stdio_entry())
    assert "h3_at_" not in blob and "token" not in blob.lower()


def test_http条目才带token():
    entry = mcpconfig.http_entry("http://10.0.0.5:8790/mcp", "h3_at_abc")
    assert entry["headers"]["Authorization"] == "Bearer h3_at_abc"


# ---------------------------------------------------------------- Host 白名单


def test_默认白名单包含环回():
    from app.mcp.serve import PING_PATH, _allowed_hosts

    hosts = _allowed_hosts("127.0.0.1", 8790, [])
    assert "127.0.0.1:8790" in hosts and "localhost:8790" in hosts
    # 隧道/局域网域名补端口，已带端口的原样保留
    hosts = _allowed_hosts("0.0.0.0", 8790, ["abc.trycloudflare.com", "10.1.2.3:9000"])
    assert "abc.trycloudflare.com:8790" in hosts and "10.1.2.3:9000" in hosts
    assert PING_PATH == "/mcp/health"


# ---------------------------------------------------------------- 配置存储


def test_config_脱敏视图不含完整token():
    cfg = Config(server="http://h:1", token="h3_at_" + "x" * 40, auth={"access": "jwt-token-value"})
    blob = json.dumps(redact(cfg), ensure_ascii=False)
    assert "x" * 20 not in blob and "jwt-token-value" not in blob


def test_config_未知键透传不丢():
    cfg = Config.from_dict({"server": "http://h:1", "custom": 7})
    assert cfg.extra["custom"] == 7 and cfg.to_dict()["custom"] == 7


# ---------------------------------------------------------------- MCP 工具面


def test_mcp_工具面注册完整且有说明():
    import asyncio

    from app.mcp.server import build_server

    tools = asyncio.run(build_server().list_tools())
    names = {t.name for t in tools}
    assert len(tools) > 60
    for expected in ("status", "job_submit", "job_wait", "workflow_select", "llm_run", "api_request", "api_catalog"):
        assert expected in names, expected
    assert all((t.description or "").strip() for t in tools), "有工具没写说明"
    assert all(t.name == t.name.lower() and " " not in t.name for t in tools)
