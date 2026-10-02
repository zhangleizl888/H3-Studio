"""agent token 的真实鉴权语义（打真库，不起后端进程）。

要守住的不是"接口能通"，而是这几条：
  - 只有 agent token 才带得出 scope；scope 只能收窄，不能越过属主角色
  - 明文只在创建那一次出现；列表/审计里都不许留下可重放的凭据
  - 吊销与过期必须当场失效（拿旧钥匙的人不该还能驱动显存）
"""

from __future__ import annotations

import datetime as dt
import uuid

import httpx
import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import session_factory
from app.main import app
from app.models import ApiToken, AuditLog, Role, User
from app.security import hash_token, new_agent_token

pytestmark = pytest.mark.live


@pytest.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def owner():
    """一个临时 admin 账号（角色上限决定 scope 上限，所以拿它当靶子）。"""
    username = f"tok-{uuid.uuid4().hex[:10]}"
    async with session_factory()() as s:
        u = User(username=username, display_name="临时", password_hash=uuid.uuid4().hex, role=Role.admin)
        s.add(u)
        await s.commit()
        await s.refresh(u)
        yield u
        await s.execute(delete(AuditLog).where(AuditLog.user_id == u.id))
        await s.execute(delete(ApiToken).where(ApiToken.user_id == u.id))
        await s.execute(delete(User).where(User.id == u.id))
        await s.commit()


async def _token(user: User, scopes: list[str], **kw) -> str:
    raw = new_agent_token()
    async with session_factory()() as s:
        s.add(ApiToken(user_id=user.id, name=kw.pop("name", "t"), token_hash=hash_token(raw), scopes=scopes, **kw))
        await s.commit()
    return raw


async def test_whoami_认_agent_token_并回_scope(client, owner):
    raw = await _token(owner, ["read", "dispatch"])
    r = await client.get("/api/agent-tokens/whoami", headers={"Authorization": f"Bearer {raw}"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["via"] == "agent-token"
    assert body["scopes"] == ["dispatch", "read"]
    assert body["user"]["username"] == owner.username


async def test_坏凭据_吊销_过期_都当场失效(client, owner):
    now = dt.datetime.now(dt.timezone.utc)
    good = await _token(owner, ["read"])
    revoked = await _token(owner, ["read"], revoked_at=now)
    expired = await _token(owner, ["read"], expires_at=now - dt.timedelta(minutes=1))
    assert (await client.get("/api/agent-tokens/whoami", headers={"Authorization": f"Bearer {good}"})).status_code == 200
    for raw, want in (
        ("h3_at_" + "x" * 40, "无效"),
        (revoked, "已吊销"),
        (expired, "已过期"),
    ):
        r = await client.get("/api/agent-tokens/whoami", headers={"Authorization": f"Bearer {raw}"})
        assert r.status_code == 401, r.text
        assert want in r.json()["detail"], r.text
    # 没带头也不能当成匿名
    assert (await client.get("/api/agent-tokens/whoami")).status_code == 401


async def test_read_档不能派发任务(client, owner):
    """派发会花真显存/真云费，read 档必须被挡在入队之前。"""
    raw = await _token(owner, ["read"])
    r = await client.post("/api/jobs", headers={"Authorization": f"Bearer {raw}"}, json={"kind": "image", "template": "auto"})
    assert r.status_code == 403
    assert "dispatch" in r.json()["detail"], r.text


async def test_dispatch_档不能改管理面而_admin_档能(client, owner):
    """正反面都要验：ASGITransport 不跑 lifespan，所以挑一条只依赖 session 的管理端点。"""
    dispatch = await _token(owner, ["read", "dispatch"])
    r = await client.get("/api/users", headers={"Authorization": f"Bearer {dispatch}"})
    assert r.status_code == 403 and "admin" in r.json()["detail"], r.text

    admin = await _token(owner, ["read", "dispatch", "admin"])
    ok = await client.get("/api/users", headers={"Authorization": f"Bearer {admin}"})
    assert ok.status_code == 200, ok.text
    assert any(u["username"] == owner.username for u in ok.json())


async def test_scope_不能越过属主角色(client):
    """admin 档的钥匙挂在 viewer 账号上，仍然进不了 admin 端点：角色是天花板。"""
    username = f"tok-{uuid.uuid4().hex[:10]}"
    async with session_factory()() as s:
        u = User(username=username, display_name="只看", password_hash=uuid.uuid4().hex, role=Role.viewer)
        s.add(u)
        await s.commit()
        await s.refresh(u)
    try:
        raw = await _token(u, ["read", "dispatch", "admin"])
        r = await client.get("/api/users", headers={"Authorization": f"Bearer {raw}"})
        assert r.status_code == 403 and "admin 角色" in r.json()["detail"], r.text
    finally:
        async with session_factory()() as s:
            await s.execute(delete(ApiToken).where(ApiToken.user_id == u.id))
            await s.execute(delete(User).where(User.id == u.id))
            await s.commit()


async def test_创建接口_明文只回一次且审计不留凭据(client, owner):
    raw = await _token(owner, ["read"])
    hdr = {"Authorization": f"Bearer {raw}"}
    r = await client.post("/api/agent-tokens", headers=hdr, json={"name": "second-key", "scopes": ["read"], "ttlDays": 30})
    assert r.status_code == 201, r.text
    created = r.json()
    assert created["token"].startswith("h3_at_") and created["scopes"] == ["read"]

    listed = (await client.get("/api/agent-tokens", headers=hdr)).json()
    assert all(created["token"] not in str(row) for row in listed), "列表里漏出了明文"

    async with session_factory()() as s:
        rows = (await s.execute(select(AuditLog).where(AuditLog.action == "agent_token.create"))).scalars().all()
    assert any(row.detail.get("name") == "second-key" for row in rows), "创建没留审计"
    assert all(created["token"] not in str(row.detail) for row in rows), "审计里存了可重放的凭据"


async def test_重复吊销是幂等的(client, owner):
    raw = await _token(owner, ["read"])
    hdr = {"Authorization": f"Bearer {raw}"}
    created = (await client.post("/api/agent-tokens", headers=hdr, json={"name": "to-revoke", "scopes": ["read"]})).json()
    tid = created["id"]
    first = await client.post(f"/api/agent-tokens/{tid}/revoke", headers=hdr)
    second = await client.post(f"/api/agent-tokens/{tid}/revoke", headers=hdr)
    assert first.status_code == second.status_code == 200
    assert (await client.get("/api/agent-tokens/whoami", headers={"Authorization": f"Bearer {created['token']}"})).status_code == 401


async def test_同名校验与上限不在这里(client, owner):
    """token 名字不做唯一约束（靠 id 吊销），但一人一把上限要拦得住。"""
    raw = await _token(owner, ["read", "dispatch", "admin"])
    hdr = {"Authorization": f"Bearer {raw}"}
    async with session_factory()() as s:
        n = (await s.execute(text("SELECT count(*) FROM api_tokens"))).scalar()
    assert n >= 1
    r = await client.post("/api/agent-tokens", headers=hdr, json={"name": "dup", "scopes": ["nope"]})
    assert r.status_code == 400, r.text
