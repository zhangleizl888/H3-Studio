"""生成回收站「满 N 天真删」的实测。

这条不许靠 docstring 作证：`POST /system/gc` 以前写着"超过 7 天"，查询里却一行天数都没筛，
任何软删行都会被立刻清掉。所以这里造五种行 —— 超期(101 天)、差一天(99 天)、还在用的活行、
路径越界的行、文件已经丢了行 —— 跑完之后逐条断言，证明筛子真的在筛：

  - 超期的：文件和库里的行都没了，字节数被报出来
  - 差一天的：文件和行都还在（这才叫保留期）
  - 活行：绝对不能被碰
  - 越界行：既不删文件也不删行，进 skipped
  - 丢文件的：算 orphans，行清掉

以及 `dry_run=True` 那一趟必须什么都不会动。
"""

from __future__ import annotations

import base64
import datetime as dt
import shutil
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text

from app.db import session_factory
from app.purge import (
    STAMP_KEY,
    TrashPurger,
    expired_media_ids,
    expired_script_version_ids,
    last_purge,
    media_root,
    purge_media_rows,
    purge_script_versions,
)

pytestmark = pytest.mark.live

_PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFAAH/q842iWwAAAAASUVORK5CYII="
)
_KEYS = ("expired", "almost", "live", "orphan", "escape")


def _backdated(days: int) -> dt.datetime:
    # asyncpg 的时间参数必须是真 datetime —— 传 ISO 字符串会在 CAST 之前就报 invalid input
    return dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)


@pytest.fixture
async def world():
    """一个项目键 + 五种 media 行 + 两行剧本版本，用完连文件一起清。"""
    pk = f"trash-test-{uuid.uuid4().hex[:8]}"
    root = media_root() / pk
    root.mkdir(parents=True, exist_ok=True)
    # 越界探针放在 media_root 外面：真去动它才叫证明守门人拦住了
    outside = media_root().parent / f"{pk}-outside.png"
    outside.write_bytes(_PX)
    made: dict[str, object] = {"pk": pk, "root": root, "outside": outside}

    async def add(*, deleted_days: int | None, rel: str | None = None) -> int:
        path = rel or f"{pk}/{uuid.uuid4().hex}.png"
        if rel is None:
            (root / Path(path).name).write_bytes(_PX)
        async with session_factory()() as s:
            mid = await s.scalar(
                text(
                    "INSERT INTO media (uuid, project_key, kind, role, ref_id, path, bytes, deleted_at, origin)"
                    " VALUES (:u, :p, 'image', 'character', 'char-1', :path, :bytes,"
                    " CAST(:d AS timestamptz), '{\"jobTitle\":\"回收站探针\"}'::jsonb) RETURNING id"
                ),
                {
                    "u": str(uuid.uuid4()),
                    "p": pk,
                    "path": path,
                    "bytes": len(_PX),
                    "d": _backdated(deleted_days) if deleted_days is not None else None,
                },
            )
            await s.commit()
        return int(mid)

    made["expired"] = await add(deleted_days=101)
    made["almost"] = await add(deleted_days=99)
    made["live"] = await add(deleted_days=None)
    made["orphan"] = await add(deleted_days=101, rel=f"{pk}/这个文件从来没写过.png")
    made["escape"] = await add(deleted_days=101, rel=f"../{pk}-outside.png")

    async with session_factory()() as s:
        for seq, days in ((1, 101), (2, 99)):
            sid = await s.scalar(
                text(
                    "INSERT INTO script_versions (uuid, project_key, seq, source, text, is_current, deleted_at)"
                    " VALUES (:u, :p, :q, 'manual', :t, false, CAST(:d AS timestamptz)) RETURNING id"
                ),
                {
                    "u": str(uuid.uuid4()),
                    "p": pk,
                    "q": seq,
                    "t": f"第 {seq} 版剧本正文",
                    "d": _backdated(days),
                },
            )
            made["script_expired" if seq == 1 else "script_almost"] = int(sid)
        await s.commit()

    yield made

    async with session_factory()() as s:
        await s.execute(text("DELETE FROM media WHERE project_key=:p"), {"p": pk})
        await s.execute(text("DELETE FROM script_versions WHERE project_key=:p"), {"p": pk})
        await s.execute(text("DELETE FROM audit_log WHERE target LIKE :p"), {"p": f"project:{pk}%"})
        await s.commit()
    shutil.rmtree(root, ignore_errors=True)
    outside.unlink(missing_ok=True)


async def _paths(world) -> dict[str, str]:
    async with session_factory()() as s:
        return {k: str(await s.scalar(text("SELECT path FROM media WHERE id=:i"), {"i": world[k]})) for k in _KEYS}


async def _exists(table: str, ident: int) -> bool:
    async with session_factory()() as s:
        return bool(await s.scalar(text(f"SELECT count(*) FROM {table} WHERE id=:i"), {"i": ident}))


async def test_expired_ids_only_pick_rows_over_the_line(world):
    """保留期这道筛子本身：超期的在名单里，差一天的和活行都不在。"""
    ids = await expired_media_ids(days=100)
    assert world["expired"] in ids
    assert world["almost"] not in ids, "差一天就被清掉的话，「保留 100 天」是句空话"
    assert world["live"] not in ids
    assert world["escape"] in ids  # 到期了，但下面会证明它删不掉
    sids = await expired_script_version_ids(days=100)
    assert world["script_expired"] in sids and world["script_almost"] not in sids


async def test_purge_removes_file_and_row_but_respects_the_line(world):
    """真删的机制。

    `purge_media_rows` 的约定是"调用方负责只把该删的 id 传进来"，所以这里只喂
    超期 + 孤儿 + 越界三条；差一天和活行能不能被选中，是上一个用例（筛子）的责任。
    """
    paths = await _paths(world)
    report = await purge_media_rows([world["expired"], world["orphan"], world["escape"]])

    assert report.deleted == 1, "超期那条算真删，孤儿那条另计"
    assert report.bytes >= len(_PX)
    assert not (media_root() / paths["expired"]).exists(), "说了要回收磁盘，就不能只删数据行"

    assert not await _exists("media", world["expired"])
    assert not await _exists("media", world["orphan"]), "库里有行、盘上没文件的要当孤儿清掉"
    assert await _exists("media", world["escape"]), "路径越界的行要留着，只记 skipped"
    assert report.orphans == 1
    assert report.skipped == [world["escape"]]
    assert world["outside"].exists(), "越界探针指向的那个工作区外文件必须安然无恙"
    # 没被喂进来的两条必须完好 —— 顺带证明这个函数不会自己扩大打击面
    assert await _exists("media", world["almost"]) and (media_root() / paths["almost"]).exists()
    assert await _exists("media", world["live"]) and (media_root() / paths["live"]).exists()


async def test_purge_dry_run_reports_without_touching_anything(world):
    paths = await _paths(world)
    report = await purge_media_rows([world[k] for k in ("expired", "orphan", "escape")], dry_run=True)
    assert report.dry_run is True
    assert report.deleted == 1, "预演也得报出会回收多少，否则这按钮等于没用"
    assert await _exists("media", world["expired"]), "预演不许删行"
    assert (media_root() / paths["expired"]).exists(), "预演不许删文件"


async def test_script_version_purge_respects_the_line(world):
    """剧本版本没有磁盘文件，删行就是全删；差一天的同样不许动。"""
    assert await purge_script_versions([world["script_expired"], world["script_almost"]], dry_run=True) == 2
    assert await _exists("script_versions", world["script_expired"])
    assert await purge_script_versions([world["script_expired"]]) == 1
    assert not await _exists("script_versions", world["script_expired"])
    assert await _exists("script_versions", world["script_almost"])


async def test_purger_dry_run_records_stamp_without_deleting(world):
    """常驻任务预演：报告要有、东西一个不能少、上次执行要落到 app_settings 让设置页说得出话。"""
    purger = TrashPurger(interval_s=10 * 365 * 86400)  # 别真等到下一轮，这个用例只跑一次
    report = await purger.run_once(dry_run=True, reason="test")
    assert "error" not in report, report
    assert report["dryRun"] is True
    assert await _exists("media", world["expired"]), "试运行不该删掉任何东西"
    stamp = await last_purge()
    assert stamp.get("reason") == "test" and stamp.get("lastRunAt")
    async with session_factory()() as s:
        # 把这把"上次执行"的椅子放回原处：真实后端启动时会自己重写
        await s.execute(text("DELETE FROM app_settings WHERE key=:k"), {"k": STAMP_KEY})
        await s.commit()
