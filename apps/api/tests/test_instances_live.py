"""实例配置的 CRUD + RBAC，打真实 PostgreSQL。

断言的是安全与一致性语义，不是「接口能通」：
  - apiKey 只进不出：任何响应里都不该出现明文
  - 改配置必须让注册表里的客户端一起换掉，否则探活还在打旧地址
  - viewer 不能改配置也不能派发；editor 能派发、不能改配置
  - 有未收口任务的实例不许删 —— 删了任务就指向一个不存在的主键
"""

from __future__ import annotations

import uuid
from pathlib import Path

import httpx
import pytest
from sqlalchemy import text

from app.db import session_factory
from app.main import app

pytestmark = pytest.mark.live

ADMIN_PW = "a-x9kQ2!mn-pass"
RH_KEY = "rh-secret-key-DO-NOT-LEAK"


@pytest.fixture
async def client():
    # 手动跑 lifespan：实例 CRUD 要写 app.state.registry，光有 ASGITransport 不会触发它。
    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


@pytest.fixture(autouse=True)
async def clean():
    tables = ("instance_locks", "jobs", "gen_instances", "media", "refresh_tokens", "audit_log", "users")
    async with session_factory()() as s:
        for table in tables:
            await s.execute(text(f"DELETE FROM {table}"))
        await s.commit()
    yield
    # 收尾也要清：残留的 running/dispatching 任务会被下一次真后端启动的派发循环
    # 当成真活去 ComfyUI 提交（本机实测踩过，还连带触发单卡仲裁把文本模型停了）。
    async with session_factory()() as s:
        for table in tables:
            await s.execute(text(f"DELETE FROM {table}"))
        await s.commit()


async def _admin(c: httpx.AsyncClient) -> dict:
    """要一张管理员票。

    lifespan 会在「环回 + 库空」时预置演示账号 admin/1234，所以到测试这里 bootstrap
    可能已经晚了 —— 断言的是「我是管理员」，不是「我是那个建号的人」。
    """
    r = await c.post("/api/auth/bootstrap", json={"username": "admin", "password": ADMIN_PW})
    if r.status_code == 200:
        return {"Authorization": f"Bearer {r.json()['access']}"}
    assert r.status_code == 409, r.text
    r = await c.post("/api/auth/login", json={"username": "admin", "password": "1234"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access']}"}


async def _mk_user(c: httpx.AsyncClient, headers: dict, username: str, role: str) -> dict:
    r = await c.post(
        "/api/users",
        headers=headers,
        json={"username": username, "displayName": username, "role": role, "password": f"a-{username}-good-pass"},
    )
    assert r.status_code == 200, r.text
    login = await c.post("/api/auth/login", json={"username": username, "password": f"a-{username}-good-pass"})
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access']}"}


async def _create(c, headers, **over):
    # 前端发的是 camelCase（§10 契约），这里就按 camelCase 发，顺带把入参别名也测了
    body = {
        "name": over.pop("name", f"本机 4090 {uuid.uuid4().hex[:6]}"),
        "protocol": "comfy_native",
        "placement": "local",
        "baseUrl": "http://127.0.0.1:8188",
        **over,
    }
    return await c.post("/api/instances", headers=headers, json=body)


async def test_instance_crud_roundtrip_and_live_registry(client):
    h = await _admin(client)
    r = await _create(client, h, local_output_root="F:/H3/comfyui/ComfyUI/output")
    assert r.status_code == 201, r.text
    created = r.json()
    inst_id = created["id"]
    assert inst_id.isdigit(), "id 由库里的序列生成，前端当字符串用"
    assert created["isDefault"] is True

    # 注册表必须立刻有它，否则要重启后端才能派发
    listed = (await client.get("/api/instances", headers=h)).json()
    assert [i["id"] for i in listed] == [inst_id]

    u = await client.patch(f"/api/instances/{inst_id}", headers=h, json={"name": "改过名的实例"})
    assert u.status_code == 200, u.text
    assert u.json()["name"] == "改过名的实例"
    assert u.json()["placement"] == "local" and u.json()["protocol"] == "comfy_native", "只改名字不该动别的字段"

    d = await client.delete(f"/api/instances/{inst_id}", headers=h)
    assert d.status_code == 204
    assert (await client.get("/api/instances", headers=h)).json() == []


async def test_api_key_never_leaves_the_server(client):
    h = await _admin(client)
    r = await _create(
        client,
        h,
        name="云端代理实例",
        placement="cloud_runninghub",
        base_url="https://www.runninghub.cn/proxy/" + RH_KEY,
        api_key=RH_KEY,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert RH_KEY not in str(body), f"响应里漏了 key：{body}"
    assert body["apiKeySet"] is True and "api_key" not in body and "apiKeyMasked" not in body

    listed = (await client.get("/api/instances", headers=h)).json()
    assert RH_KEY not in str(listed)
    assert RH_KEY not in listed[0]["baseUrl"]

    # 库里存的是 Fernet 密文，不是明文
    async with session_factory()() as s:
        enc = (await s.execute(text("SELECT api_key_enc FROM gen_instances WHERE id=:i"), {"i": int(body["id"])})).scalar()
    assert enc and RH_KEY not in enc

    # PATCH 省略 api_key 必须不动已存的 key（前端只改名字是常态）
    u = await client.patch(
        f"/api/instances/{body['id']}",
        headers=h,
        json={"name": "云端代理实例改名", "baseUrl": "https://www.runninghub.cn/proxy/" + RH_KEY, "placement": "cloud_runninghub"},
    )
    assert u.status_code == 200, u.text
    assert u.json()["apiKeySet"] is True, "省略 apiKey 字段却把已存的 key 清了"
    assert u.json()["name"] == "云端代理实例改名"


async def test_patching_back_the_masked_url_does_not_corrupt_it(client):
    """设置页常把 GET 回来的整条对象 PATCH 回去。baseUrl 是脱敏过的，
    照单收下就等于把真实地址写成一串 <REDACTED>。"""
    h = await _admin(client)
    created = (await _create(client, h, name="会被回写的实例")).json()
    echoed = {**created, "id": None, "name": "改名了"}
    echoed.pop("id")
    r = await client.patch(f"/api/instances/{created['id']}", headers=h, json=echoed)
    assert r.status_code == 200, r.text
    assert r.json()["baseUrl"] == "http://127.0.0.1:8188", r.json()

    rh = (await _create(client, h, name="RH 代理实例", placement="cloud_runninghub", baseUrl="https://www.runninghub.cn/proxy/abcdefgh12345678", apiKey="abcdefgh12345678")).json()
    assert "<REDACTED>" in rh["baseUrl"], rh["baseUrl"]
    again = await client.patch(f"/api/instances/{rh['id']}", headers=h, json={**{k: v for k, v in rh.items() if k != "id"}, "name": "回写测试"})
    assert again.status_code == 200, again.text
    assert again.json()["baseUrl"] == rh["baseUrl"], "脱敏 URL 被当成新地址写进去了"

    bogus = await client.patch(f"/api/instances/{rh['id']}", headers=h, json={"baseUrl": "https://other.cn/proxy/<REDACTED>"})
    assert bogus.status_code == 400 and "<REDACTED>" in bogus.json()["detail"]


async def test_rh_task_is_refused_with_the_working_alternative(client):
    h = await _admin(client)
    r = await _create(client, h, name="RH 任务 API", protocol="rh_task", base_url="https://www.runninghub.cn")
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert "/proxy/" in detail and "comfy_native" in detail, detail


async def test_duplicate_name_conflicts(client):
    h = await _admin(client)
    name = f"重名实例 {uuid.uuid4().hex[:6]}"
    assert (await _create(client, h, name=name)).status_code == 201
    r = await _create(client, h, name=name)
    assert r.status_code == 409, r.text
    assert name in r.json()["detail"]


async def test_local_output_root_must_exist(client):
    h = await _admin(client)
    r = await _create(client, h, name="目录不存在", localOutputRoot="F:/H3/nope-not-here")
    assert r.status_code == 400 and "目录不存在" in r.json()["detail"]


async def test_default_instance_is_unique(client):
    h = await _admin(client)
    first = (await _create(client, h, name="默认一", is_default=True)).json()
    second = (await _create(client, h, name="默认二", is_default=True)).json()
    listed = {i["id"]: i["isDefault"] for i in (await client.get("/api/instances", headers=h)).json()}
    assert listed[first["id"]] is False and listed[second["id"]] is True


async def test_roles_gate_config_and_dispatch(client):
    h = await _admin(client)
    editor = await _mk_user(client, h, "editor1", "editor")
    viewer = await _mk_user(client, h, "viewer1", "viewer")

    assert (await _create(client, editor, name="编辑建的")).status_code == 403
    assert (await _create(client, viewer, name="观众建的")).status_code == 403
    assert (await _create(client, h, name="管理员建的")).status_code == 201

    # 读：登录了就能看；没登录在有库模式下必须挡
    assert (await client.get("/api/instances")).status_code == 401
    assert (await client.get("/api/instances", headers=editor)).status_code == 200

    inst_id = (await client.get("/api/instances", headers=editor)).json()[0]["id"]
    assert (await client.delete(f"/api/instances/{inst_id}", headers=editor)).status_code == 403
    assert (await client.delete(f"/api/instances/{inst_id}", headers=viewer)).status_code == 403


async def test_delete_blocked_by_unsettled_job(client):
    h = await _admin(client)
    inst_id = (await _create(client, h, name="有任务的实例")).json()["id"]
    async with session_factory()() as s:
        # state=running + prompt_id 为空 → 派发循环会跳过它（不提交、不收口），
        # 正好用来验证「还有未收口任务不许删」这条守卫本身。
        await s.execute(
            text(
                """
                INSERT INTO jobs(uuid, kind, state, priority, title, instance_id, params, progress, attempts, max_attempts, output, cost, log)
                VALUES (:u,'image','running',100,'卡住的',:i,'{}'::jsonb,'{}'::jsonb,0,3,'{}','{}','{}')
                """
            ),
            {"u": str(uuid.uuid4()), "i": int(inst_id)},
        )
        await s.commit()
    r = await client.delete(f"/api/instances/{inst_id}", headers=h)
    assert r.status_code == 409 and "未收口" in r.json()["detail"]

    async with session_factory()() as s:
        await s.execute(text("DELETE FROM jobs WHERE instance_id=:i"), {"i": int(inst_id)})
        await s.commit()
    assert (await client.delete(f"/api/instances/{inst_id}", headers=h)).status_code == 204


async def test_probe_persists_reachability(client):
    """设置页那个「本地可用连接」绿点读的是库里的列，不是前端临时状态。"""
    h = await _admin(client)
    inst_id = (await _create(client, h, name="可探活的实例")).json()["id"]
    r = await client.post(f"/api/instances/{inst_id}/probe", headers=h)
    assert r.status_code == 200, r.text
    report = r.json()
    assert report["ok"] is True, report
    assert report["native"]["h3"], "本机实例应带 MiniMaxH3 节点"

    async with session_factory()() as s:
        row = (
            await s.execute(
                text("SELECT last_probe_ok, last_probe_at IS NOT NULL AS has_time FROM gen_instances WHERE id=:i"),
                {"i": int(inst_id)},
            )
        ).mappings().first()
    assert row["last_probe_ok"] is True and row["has_time"] is True


async def test_dry_probe_does_not_store_anything(client):
    h = await _admin(client)
    r = await client.post("/api/instances/dry-probe", headers=h, json={"base_url": "http://127.0.0.1:8188"})
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
    assert (await client.get("/api/instances", headers=h)).json() == [], "试连不该落库"


async def test_media_bytes_are_served_from_our_own_root(client):
    """产物只能从我们自己的 media_root 读。path 来自库里、库里的值来自实例返回的文件名，
    所以目录穿越必须在读接口上挡死。"""

    from app.models import Media

    h = await _admin(client)
    root = Path(app.state.settings.media_root)
    rel = f"jobs-test/{uuid.uuid4().hex[:6]}.png"
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"\x89PNG\r\n\x1a\nfake-png-bytes")

    try:
        async with session_factory()() as s:
            ok = Media(kind="image", role="output", path=rel, bytes_=target.stat().st_size, mime="image/png")
            bad = Media(kind="image", role="output", path="../outside.txt")
            s.add_all([ok, bad])
            await s.commit()
            mid, bad_id = ok.id, bad.id

        assert (await client.get(f"/api/media/{mid}/raw")).status_code == 401, "有库模式不许匿名读产物"
        r = await client.get(f"/api/media/{mid}/raw", headers=h)
        assert r.status_code == 200 and r.headers["content-type"] == "image/png", r.headers
        assert len(r.content) == target.stat().st_size

        ranged = await client.get(f"/api/media/{mid}/raw", headers={**h, "Range": "bytes=0-3"})
        assert ranged.status_code == 206, ranged.status_code

        dl = await client.get(f"/api/media/{mid}/download", headers=h)
        assert "attachment" in dl.headers["content-disposition"], dl.headers

        traversal = await client.get(f"/api/media/{bad_id}/raw", headers=h)
        assert traversal.status_code == 404, "media_root 之外的路径绝不能发出去"
    finally:
        target.unlink(missing_ok=True)


async def test_cancel_queued_job_without_touching_the_instance(client):
    """排队中的任务取消 = 只收口库里的行。

    这里故意用一个空注册表：派发循环只认注册表里的实例，注册表空了就不会有人去
    真 ComfyUI 上提交这张假图 —— 否则这个测试会烧一次 GPU。
    """
    from app.config import get_settings
    from app.gen.registry import InstanceRegistry

    h = await _admin(client)
    inst_id = (await _create(client, h, name="用来取消的实例")).json()["id"]
    # 先停掉 lifespan 起的派发循环，再把注册表换成空的
    await app.state.dispatcher.stop()
    app.state.registry = InstanceRegistry(get_settings())
    app.state.registry._configs.clear()  # noqa: SLF001 —— 无库引导的实例也不算进来

    job_uuid = str(uuid.uuid4())
    async with session_factory()() as s:
        await s.execute(
            text(
                """
                INSERT INTO jobs(uuid, kind, state, priority, title, instance_id, params, progress, attempts, max_attempts, output, cost, log)
                VALUES (:u,'video','queued',100,'要取消的',:i,'{}'::jsonb,'{}'::jsonb,0,3,'{}','{}','{}')
                """
            ),
            {"u": job_uuid, "i": int(inst_id)},
        )
        await s.commit()

    r = await client.post(f"/api/jobs/{job_uuid}/cancel", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["state"] == "canceled" and body["terminal"] is True, body
    assert body["error"]["type"] == "canceled", body["error"]

    again = await client.post(f"/api/jobs/{job_uuid}/cancel", headers=h)
    assert again.status_code == 400 and "已经收口" in again.json()["detail"], again.text

    async with session_factory()() as s:
        pid = (await s.execute(text("SELECT prompt_id FROM jobs WHERE uuid=:u"), {"u": job_uuid})).scalar_one()
        locks = (await s.execute(text("SELECT count(*) FROM instance_locks WHERE job_id=(SELECT id FROM jobs WHERE uuid=:u)"), {"u": job_uuid})).scalar_one()
    assert pid is None, "没派发过就不该有 prompt_id"
    assert locks == 0


async def test_viewer_cannot_enqueue(client):
    h = await _admin(client)
    inst_id = (await _create(client, h, name="排队门禁实例")).json()["id"]
    viewer = await _mk_user(client, h, "viewer2", "viewer")
    r = await client.post(
        "/api/jobs", headers=viewer, json={"instance_id": inst_id, "graph": {"1": {"class_type": "Noop", "inputs": {}}}}
    )
    assert r.status_code == 403, r.text
