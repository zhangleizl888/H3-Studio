"""对真实 PostgreSQL 跑剧本版本历史的规矩。

断言的是三条容易做错、做错了用户就会丢东西的语义，不是「接口能通」：

  - **序号烧死**：删掉 V2 之后 V3 仍然是 V3。把 `deleted_at IS NULL` 写进带窗口的 WHERE
    就会重排 —— 用户从回收站恢复出来的"V2"会和他记忆里的 V2 不是同一份。
  - **writtenAt 的信任边界**：只有手动存版和补存旧正文能带自定义时间，其余一律服务端时钟，
    否则会出现「V3 的生成时间比 V2 还早」。
  - **当前版唯一，且删掉当前版要退到剩下 seq 最大的一版**：软删行若留着 is_current，
    会占住部分唯一索引 uq_script_versions_one_current 的坑，下一次 setCurrent 直接 500。

不起 lifespan（那会再多起一个 QueueDispatcher，和正在用的后端抢同一张队列表），
也不碰演示账号：临时建一个 editor 用户，用完连同它的审计一起删。
"""

from __future__ import annotations

import base64
import datetime as dt
import shutil
import uuid
from pathlib import Path

import httpx
import pytest
from sqlalchemy import text

from app.api.routes_versions import _seq_key
from app.config import get_settings
from app.db import session_factory
from app.main import app
from app.models import Role, User
from app.security import decode_access, hash_password, issue_access

pytestmark = pytest.mark.live

# 这些路由要读 request.app.state.settings，而那是 lifespan 里设的；不跑 lifespan 就得手工补
app.state.settings = get_settings()

FIVE_DAYS_AGO = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=5)).isoformat()


@pytest.fixture
async def client():
    uname = f"vt_{uuid.uuid4().hex[:8]}"
    async with session_factory()() as s:
        user = User(
            username=uname,
            display_name="版本历史测试",
            password_hash=hash_password("x", enforce_policy=False),
            role=Role.editor,
        )
        s.add(user)
        await s.commit()
        await s.refresh(user)
        token, _ttl = issue_access(user)
        uid = user.id
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test", headers={"Authorization": f"Bearer {token}"}) as c:
        yield c
    async with session_factory()() as s:
        await s.execute(text("DELETE FROM audit_log WHERE actor=:u"), {"u": uname})
        await s.execute(text("DELETE FROM users WHERE id=:i"), {"i": uid})
        await s.commit()


@pytest.fixture
async def pk():
    """每个用例一个项目键，收尾清干净，别给开发库攒残留。"""
    key = f"versions-test-{uuid.uuid4().hex[:8]}"
    yield key
    async with session_factory()() as s:
        await s.execute(text("DELETE FROM script_versions WHERE project_key=:p"), {"p": key})
        await s.execute(text("DELETE FROM media WHERE project_key=:p"), {"p": key})
        # 发号器落在 app_settings 上，测试项目也要一起收走，别攒一堆孤儿键
        await s.execute(text("DELETE FROM app_settings WHERE key=:k"), {"k": _seq_key(key)})
        await s.commit()
    shutil.rmtree(Path(get_settings().media_root) / key, ignore_errors=True)


async def _create(c: httpx.AsyncClient, **body) -> dict:
    r = await c.post("/api/script-versions", json=body)
    assert r.status_code == 201, r.text
    return r.json()


async def _list(c: httpx.AsyncClient, key: str, **params) -> list[dict]:
    r = await c.get("/api/script-versions", params={"project_key": key, **params})
    assert r.status_code == 200, r.text
    return r.json()


async def test_first_save_backfills_the_older_text_as_v1(client, pk):
    """需求里的"再次生成就把之前那版存成历史"：补存的旧正文当 V1，时间用它的写作时间，
    但入库时间（created_at）必须是刚才 —— 审计列不许被客户端时间污染。"""
    v2 = await _create(
        client,
        projectKey=pk,
        text="第二幕：皇后把诏书压在镇纸下。",
        source="ai-write",
        backfillFrom={"text": "第一幕：未央宫偏殿，日。", "writtenAt": FIVE_DAYS_AGO},
    )
    assert v2["version"] == 2 and v2["isCurrent"] is True

    rows = await _list(client, pk)
    assert [x["version"] for x in rows] == [2, 1]
    v1 = rows[1]
    assert v1["writtenAt"][:10] == FIVE_DAYS_AGO[:10]
    assert v1["backfilled"] is True, "补存的 V1 要能被界面标出来，它的时间不是生成时间"
    assert v1["createdAt"][:10] == dt.datetime.now(dt.timezone.utc).isoformat()[:10]


async def test_written_at_only_trusted_for_manual(client, pk):
    """storyboard/ai-write 带过去时间必须被夹到当下；manual 才采纳，且只许往过去。"""
    gen = await _create(client, projectKey=pk, text="拆解出来的正文。", source="storyboard", writtenAt=FIVE_DAYS_AGO)
    assert gen["writtenAt"] > FIVE_DAYS_AGO, "非 manual 的过去时间必须被服务端时钟覆盖"

    manual = await _create(client, projectKey=pk, text="手打的正文。", source="manual", writtenAt=FIVE_DAYS_AGO)
    assert manual["writtenAt"][:10] == FIVE_DAYS_AGO[:10]

    future = await _create(client, projectKey=pk, text="未来时间。", source="manual", writtenAt=(dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=30)).isoformat())
    assert future["writtenAt"][:4] == dt.datetime.now(dt.timezone.utc).isoformat()[:4], "往未来的 writtenAt 要夹回当下"


async def test_trashing_current_promotes_newest_and_never_renumbers(client, pk):
    """三条最容易做错的规矩，一次验完。"""
    v1 = await _create(client, projectKey=pk, text="V1 正文", source="ai-write")
    v2 = await _create(client, projectKey=pk, text="V2 正文", source="storyboard")
    v3 = await _create(client, projectKey=pk, text="V3 正文", source="ai-write")
    assert [v1["version"], v2["version"], v3["version"]] == [1, 2, 3]

    r = await client.delete(f"/api/script-versions/{v3['uuid']}")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["wasCurrent"] is True
    assert d["current"]["version"] == 2, "删掉当前版要退到剩下 seq 最大的一版"
    assert d["retentionDays"] == 100
    assert d["daysLeft"] == 100, "刚删完就显示 99 天会让人以为一天都没存住"

    live = await _list(client, pk)
    assert [x["version"] for x in live] == [2, 1]
    assert live[0]["versionCount"] == 3, "被删的那版继续占着总数，序号不复用"

    with_del = await _list(client, pk, include_deleted="true")
    revived = next(x for x in with_del if x["version"] == 3)
    assert revived["uuid"] == v3["uuid"] and revived["isCurrent"] is False


async def test_restore_does_not_steal_current(client, pk):
    """恢复只做一件事：回到历史。抢当前版得用户自己点「设为当前」。"""
    v1 = await _create(client, projectKey=pk, text="V1 正文", source="ai-write")
    v2 = await _create(client, projectKey=pk, text="V2 正文", source="ai-write")
    assert v2["isCurrent"] is True
    # v1 那份响应是「它还是当前版」那一刻的快照，不能拿它断言现在的状态 —— 要重新查
    rows = {x["version"]: x for x in await _list(client, pk)}
    assert rows[1]["isCurrent"] is False and rows[2]["isCurrent"] is True

    r = await client.post(f"/api/script-versions/{v1['uuid']}/restore")
    assert r.status_code == 400, "没在回收站里的版本谈不上恢复，接口要拒"

    await client.delete(f"/api/script-versions/{v1['uuid']}")
    back = await client.post(f"/api/script-versions/{v1['uuid']}/restore")
    assert back.status_code == 200, back.text
    assert back.json()["promotedToCurrent"] is False, "V2 还是当前版，恢复 V1 不许把它顶掉"


async def test_purge_requires_trash_and_reuses_nothing(client, pk):
    """彻底删除必须先进过回收站；删掉的号此后不再出现。"""
    v1 = await _create(client, projectKey=pk, text="V1 正文", source="ai-write")
    v2 = await _create(client, projectKey=pk, text="V2 正文", source="ai-write")

    r = await client.delete(f"/api/script-versions/{v2['uuid']}/purge")
    assert r.status_code == 400, "没进回收站就 purge 等于绕过回收站"

    await client.delete(f"/api/script-versions/{v2['uuid']}")
    assert (await client.delete(f"/api/script-versions/{v2['uuid']}/purge")).status_code == 204
    assert (await client.post(f"/api/script-versions/{v2['uuid']}/current")).status_code == 404

    v3 = await _create(client, projectKey=pk, text="V3 正文", source="ai-write")
    assert v3["version"] == 3, "V2 被彻底删掉了，但 2 这个号不能回收再用"

    rows = await _list(client, pk, include_deleted="true")
    assert sorted(x["version"] for x in rows) == [1, 3]


async def test_rejects_junk(client, pk):
    assert (await client.post("/api/script-versions", json={"projectKey": pk, "text": "x", "source": "bogus"})).status_code == 400
    assert (await client.post("/api/script-versions", json={"projectKey": pk, "text": "   ", "source": "ai-write"})).status_code == 400
    assert (await client.get("/api/script-versions")).status_code == 400, "不给 project_key 就等于让人拉全库历史"


async def test_viewer_cannot_delete(client, pk):
    """删除版本和花 GPU 一样有后果，viewer 必须挡在外面。"""
    v1 = await _create(client, projectKey=pk, text="V1 正文", source="ai-write")
    # 从自己的 bearer 里取 sub，别用 username LIKE —— 并发跑测试时会撞到别人的临时账号
    uid_ = int(decode_access(client.headers["Authorization"].split(" ", 1)[1])["sub"])
    async with session_factory()() as s:
        await s.execute(text("UPDATE users SET role='viewer' WHERE id=:i"), {"i": uid_})
        await s.commit()
    r = await client.delete(f"/api/script-versions/{v1['uuid']}")
    assert r.status_code == 403, r.text


# ───────────────── 图片 / 视频：media 行的分组版本视图 ─────────────────

_PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFAAH/q842iWwAAAAASUVORK5CYII="
)


async def _seed_media(pk: str, role: str, ref_id: str, count: int, *, upload: bool = False) -> list[int]:
    """造 count 行产物 + 真文件，落在 media_root/<项目键>/ 下。

    非要真文件，是因为这条链上有两个断言不是纯 SQL 的：软删后还能不能读、
    彻底删除有没有真的把磁盘收回来。
    """
    root = Path(get_settings().media_root) / pk
    root.mkdir(parents=True, exist_ok=True)
    ids: list[int] = []
    async with session_factory()() as s:
        for _ in range(count):
            name = f"{uuid.uuid4().hex}.png"
            (root / name).write_bytes(_PX)
            mid = await s.scalar(
                text(
                    "INSERT INTO media (uuid, project_key, kind, role, ref_id, path, bytes, origin)"
                    " VALUES (:u, :p, 'image', :role, :ref, :path, :bytes, CAST(:origin AS jsonb)) RETURNING id"
                ),
                {
                    "u": str(uuid.uuid4()),
                    "p": pk,
                    "role": role,
                    "ref": ref_id,
                    "path": f"{pk}/{name}",
                    "bytes": len(_PX),
                    "origin": '{"source":"upload"}' if upload else '{"jobTitle":"定妆 · 测试"}',
                },
            )
            ids.append(int(mid))
        await s.commit()
    return ids


async def _media_rows(client, pk: str, **params) -> list[dict]:
    r = await client.get("/api/media-versions", params={"project_key": pk, **params})
    assert r.status_code == 200, r.text
    return r.json()


async def test_media_numbers_survive_trashing(client, pk):
    """删掉 V2 之后 V3 仍然是 V3 —— 软删条件写进窗口那一层就会重排。"""
    ids = await _seed_media(pk, "character", "char-1", 3)
    rows = await _media_rows(client, pk, role="character", ref_id="char-1")
    assert [x["version"] for x in rows] == [3, 2, 1], "最新在前，号按 id 升序"
    assert rows[0]["versionCount"] == 3

    d = (await client.delete(f"/api/media/{ids[1]}")).json()
    assert d["promoteCandidateId"] == str(ids[2]), "候选版要取同组最新存活的那一版，前端靠它接着显示"
    assert d["groupRemaining"] == 2
    assert d["version"] == 2, "删除响应里那一版的号要和窗口算出来的一致（分组键两处必须同一个口径）"

    again = await _media_rows(client, pk, role="character", ref_id="char-1")
    assert [x["version"] for x in again] == [3, 1], "V2 没了，V3 不许改叫 V2"
    assert again[0]["versionCount"] == 3, "总数要连着回收站里那版一起数"
    withdel = await _media_rows(client, pk, role="character", ref_id="char-1", include_deleted="true")
    assert [x["version"] for x in withdel] == [3, 2, 1]


async def test_trashed_media_readable_only_with_the_flag(client, pk):
    """活行查询默认读不到软删行，但回收站必须能预览 —— 否则用户没法在恢复前确认那是啥。"""
    ids = await _seed_media(pk, "video", "shot-9", 1)
    assert (await client.get(f"/api/media/{ids[0]}/raw")).status_code == 200
    await client.delete(f"/api/media/{ids[0]}")
    assert (await client.get(f"/api/media/{ids[0]}/raw")).status_code == 404
    assert (await client.get(f"/api/media/{ids[0]}/raw", params={"trashed": "true"})).status_code == 200

    t = (await client.get("/api/trash", params={"project_key": pk})).json()
    assert [i["key"] for i in t["items"]] == [f"media:{ids[0]}"]
    assert t["items"][0]["daysLeft"] == 100
    assert t["items"][0]["title"] == "定妆 · 测试", "项目实体不在这台浏览器上时，标题只能靠 origin.jobTitle"

    back = await client.post(f"/api/media/{ids[0]}/restore")
    assert back.status_code == 200, back.text
    assert (await client.get(f"/api/media/{ids[0]}/raw")).status_code == 200


async def test_media_purge_frees_the_file(client, pk):
    ids = await _seed_media(pk, "scene", "scene-1", 1)
    assert (await client.delete(f"/api/media/{ids[0]}/purge")).status_code == 400, "绕过回收站的彻底删除不该存在"
    await client.delete(f"/api/media/{ids[0]}")
    on_disk = list((Path(get_settings().media_root) / pk).glob("*.png"))
    r = (await client.delete(f"/api/media/{ids[0]}/purge")).json()
    assert r["deleted"] == 1 and r["bytes"] > 0
    assert not on_disk[0].exists(), "说了要回收磁盘，不能只删数据行"
    async with session_factory()() as s:
        assert (await s.scalar(text("SELECT count(*) FROM media WHERE id=:i"), {"i": ids[0]})) == 0


async def test_uploads_and_exports_are_not_versions(client, pk):
    """用户自己传的参考图不是"生成的第 N 版"，混进回收站会让人以为产物被删了。"""
    await _seed_media(pk, "character", "char-1", 1)
    await _seed_media(pk, "ref_image", "char-1", 1, upload=True)
    rows = await _media_rows(client, pk)
    assert [x["role"] for x in rows] == ["character"]
    assert (await client.get("/api/trash", params={"project_key": pk})).json()["items"] == []


async def test_media_versions_needs_a_scope(client, pk):
    """不给 project_key 又不开 all_projects，等于让人拉全库产物 —— 必须拒。"""
    assert (await client.get("/api/media-versions")).status_code == 400
    assert (await client.get("/api/trash")).status_code == 400
    assert (await client.get("/api/media-versions", params={"project_key": pk, "bucket": "script"})).status_code == 400


async def test_export_refuses_trashed_media(client, pk):
    """被删的成片不许再被 ffmpeg 拼进导出（_resolve 少了软删过滤就是这个结局）。

    断言到 404 和原因，而不是「非 200」：本机 ffmpeg 是装好的（Gyan.FFmpeg 9.0.1），
    所以这条路唯一能失败的地方就是那条软删过滤。
    """
    ids = await _seed_media(pk, "video", "shot-1", 2)
    await client.delete(f"/api/media/{ids[0]}")
    r = await client.post(f"/api/projects/{pk}/export/merge", json={"mediaIds": [str(ids[0])], "title": "不该成功"})
    assert r.status_code == 404, r.text
    assert "回收站" in r.json()["detail"], r.text
