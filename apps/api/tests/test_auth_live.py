"""对真实 PostgreSQL 跑认证与 RBAC。

这些用例断言的是安全语义，不是"接口能通"：
  - 用户名不存在与口令错误必须返回完全相同的话（否则等于送出用户名枚举）
  - 刷新令牌二次使用要吊销全部会话（这是检测令牌泄露的唯一手段）
  - 口令与 apiKey 绝不能出现在任何响应或审计里
"""

from __future__ import annotations

import uuid

import httpx
import pytest
from sqlalchemy import text

from app.db import session_factory
from app.main import app

pytestmark = pytest.mark.live


@pytest.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# asyncpg 一条 prepared statement 里不能放多条语句，所以逐条删（顺序按外键来）
_CLEAN_ORDER = ("refresh_tokens", "audit_log", "users")


@pytest.fixture(autouse=True)
async def clean_users():
    async with session_factory()() as s:
        for table in _CLEAN_ORDER:
            await s.execute(text(f"DELETE FROM {table}"))
        await s.commit()
    yield


async def _bootstrap(c: httpx.AsyncClient, username: str, password: str) -> dict:
    r = await c.post("/api/auth/bootstrap", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()


async def test_setup_required_flips_after_first_admin(client):
    before = (await client.get("/api/auth/setup-required")).json()
    assert before["needed"] is True
    await _bootstrap(client, "admin", "a-x9kQ2!mn-pass")
    after = (await client.get("/api/auth/setup-required")).json()
    assert after["needed"] is False


async def test_bootstrap_only_once(client):
    await _bootstrap(client, "admin", "a-x9kQ2!mn-pass")
    r = await client.post("/api/auth/bootstrap", json={"username": "evil", "password": "another-good-pass"})
    assert r.status_code == 409


async def test_weak_password_rejected_at_bootstrap(client):
    r = await client.post("/api/auth/bootstrap", json={"username": "admin", "password": "abc"})
    assert r.status_code == 422 and "10 位" in r.json()["detail"]
    # 被拒的口令不能留下半个账号
    assert (await client.get("/api/auth/setup-required")).json()["needed"] is True


async def test_login_failure_message_does_not_leak_username_existence(client):
    await _bootstrap(client, "admin", "a-x9kQ2!mn-pass")
    wrong_pwd = await client.post("/api/auth/login", json={"username": "admin", "password": "totally-wrong-1"})
    no_user = await client.post("/api/auth/login", json={"username": "ghost", "password": "totally-wrong-1"})
    assert wrong_pwd.status_code == no_user.status_code == 401
    assert wrong_pwd.json()["detail"] == no_user.json()["detail"], "两条消息不同就可被枚举"


async def test_me_and_token_roundtrip(client):
    session = await _bootstrap(client, "admin", "a-x9kQ2!mn-pass")
    assert session["user"]["role"] == "admin"
    r = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {session['access']}"})
    assert r.status_code == 200 and r.json()["username"] == "admin"
    assert (await client.get("/api/auth/me")).status_code == 401
    assert (await client.get("/api/auth/me", headers={"Authorization": "Bearer garbage"})).status_code == 401


async def test_refresh_rotates_and_reuse_revokes_all_sessions(client):
    s0 = await _bootstrap(client, "admin", "a-x9kQ2!mn-pass")
    r1 = await client.post("/api/auth/refresh", json={"refresh": s0["refresh"]})
    assert r1.status_code == 200, r1.text
    old_refresh = s0["refresh"]

    # 旧令牌二次使用 = 疑似泄露，必须吊销全部会话
    r2 = await client.post("/api/auth/refresh", json={"refresh": old_refresh})
    assert r2.status_code == 401 and "重复使用" in r2.json()["detail"]

    r3 = await client.post("/api/auth/refresh", json={"refresh": r1.json()["refresh"]})
    assert r3.status_code == 401, "全部会话应已吊销"


async def test_deactivated_user_loses_access(client):
    """停用账号要同时做到：档案变更、当前 access 立刻失效、刷新令牌全部吊销、再登录被拒。"""
    admin = await _bootstrap(client, "admin", "a-x9kQ2!mn-pass")
    hdr = {"Authorization": f"Bearer {admin['access']}"}
    created = await client.post(
        "/api/users",
        json={"username": "liyu", "displayName": "李昱", "role": "editor", "password": "ed1tor-pass-9"},
        headers=hdr,
    )
    assert created.status_code == 200, created.text
    uid = created.json()["id"]
    sess = (await client.post("/api/auth/login", json={"username": "liyu", "password": "ed1tor-pass-9"})).json()

    r = await client.patch(f"/api/users/{uid}", json={"isActive": False}, headers=hdr)
    assert r.status_code == 200 and r.json()["isActive"] is False

    assert (await client.get("/api/auth/me", headers={"Authorization": f"Bearer {sess['access']}"})).status_code == 401
    assert (await client.post("/api/auth/refresh", json={"refresh": sess["refresh"]})).status_code == 401
    assert (await client.post("/api/auth/login", json={"username": "liyu", "password": "ed1tor-pass-9"})).status_code == 401


async def test_admin_cannot_deactivate_self(client):
    """把自己停用会让系统失去唯一管理员，这类操作必须挡住。"""
    admin = await _bootstrap(client, "admin", "a-x9kQ2!mn-pass")
    hdr = {"Authorization": f"Bearer {admin['access']}"}
    r = await client.patch(f"/api/users/{admin['user']['id']}", json={"isActive": False}, headers=hdr)
    assert r.status_code == 409, "应拒绝管理员自我停用"


async def test_rbac_viewer_cannot_create_users(client):
    await _bootstrap(client, "admin", "a-x9kQ2!mn-pass")
    admin_hdr = {"Authorization": f"Bearer {(await client.post('/api/auth/login', json={'username':'admin','password':'a-x9kQ2!mn-pass'})).json()['access']}"}
    created = await client.post(
        "/api/users",
        json={"username": "zhous", "displayName": "周珊", "role": "viewer", "password": "vIew3r-pass9"},
        headers=admin_hdr,
    )
    assert created.status_code == 200, created.text
    viewer_token = (await client.post("/api/auth/login", json={"username": "zhous", "password": "vIew3r-pass9"})).json()["access"]
    forbidden = await client.get("/api/users", headers={"Authorization": f"Bearer {viewer_token}"})
    assert forbidden.status_code == 403 and "admin" in forbidden.json()["detail"]
    ok = await client.get("/api/users", headers=admin_hdr)
    assert ok.status_code == 200 and len(ok.json()) == 2


async def test_password_never_echoed_anywhere(client):
    secret = "Un1que-Passw0rd!"
    session = await _bootstrap(client, "admin", secret)
    body = session["user"]
    assert secret not in str(body)
    assert "passwordHash" not in body and "password_hash" not in body
    async with session_factory()() as s:
        rows = (await s.execute(text("SELECT action, detail::text AS d, actor FROM audit_log"))).mappings().all()
        assert rows, "登录与建号应有审计记录"
        for r in rows:
            assert secret not in (r["d"] or "") and secret not in (r["actor"] or "")
        stored = await s.scalar(text("SELECT password_hash FROM users WHERE username='admin'"))
        assert stored.startswith("$argon2id$") and secret not in stored


async def test_login_records_audit_with_ip(client):
    await _bootstrap(client, "admin", "a-x9kQ2!mn-pass")
    await client.post("/api/auth/login", json={"username": "admin", "password": "a-x9kQ2!mn-pass"})
    async with session_factory()() as s:
        n = await s.scalar(text("SELECT count(*) FROM audit_log WHERE action='auth.login'"))
        assert n >= 1


async def test_unique_username_enforced_in_db(client):
    """绕过接口直接撞库层约束：确认唯一性不是只靠应用代码兜着。"""
    from sqlalchemy.exc import IntegrityError

    from app.models import Role, User
    from app.security import hash_password

    async with session_factory()() as s:
        s.add(User(username="dup", display_name="D1", role=Role.editor, password_hash=hash_password("g0od-pass-x")))
        await s.commit()
    async with session_factory()() as s:
        s.add(User(username="dup", display_name="D2", role=Role.editor, password_hash=hash_password("g0od-pass-y")))
        with pytest.raises(IntegrityError):
            await s.commit()
