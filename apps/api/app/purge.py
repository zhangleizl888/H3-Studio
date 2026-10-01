"""生成回收站的真删：把软删满保留期的行连磁盘文件一起清掉。

`unlink` 只写这一份，三个调用方共用：单条「彻底删除」、系统页的 `POST /system/gc`、
以及到期自动扫（`TrashPurger`）。之前这三处各写一遍的结局就是 `gc` 里那条「注释说 7 天、
查询完全不筛天数」的谎。

顺序是刻意的：**先删文件，再删数据行**。指向已删文件的行是谎言（点进去 404），
没有行的文件是泄漏（`/system/gc` 的孤儿分支正是在数这个）。宁可泄漏也不要谎言。
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import text as sa_text

from .config import get_settings
from .db import session_factory
from .logging_setup import get_logger
from .models import AuditLog

log = get_logger("purge")


@dataclass
class PurgeReport:
    deleted: int = 0
    bytes: int = 0
    orphans: int = 0  # 库里有行、磁盘上已经没文件（本来就该清掉）
    locked: int = 0  # 文件被占用（Windows 上杀软/播放器占着是常态）：留行，下一轮再试
    skipped: list[int] = field(default_factory=list)  # 路径越界，绝不碰
    dry_run: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "deleted": self.deleted,
            "bytes": self.bytes,
            "orphans": self.orphans,
            "locked": self.locked,
            "skipped": len(self.skipped),
            "dryRun": self.dry_run,
        }


def media_root() -> Path:
    return Path(get_settings().media_root).resolve()


def _handle_file(root: Path, rel: str, *, dry_run: bool) -> tuple[str, int]:
    """处理一个文件。返回 (状态, 字节数)。阻塞调用，必须从 to_thread 里进来。

    `dry_run` 只做「能不能删、能回收多少」的判断，一个字节都不动。以前这里预演也会
    unlink，结果删掉了文件却留着数据行 —— 那正是本文件开头说"宁可泄漏也不要谎言"要防的事。
    """
    target = (root / rel).resolve()
    # path 是库里的字符串，而库里的值来自实例返回的文件名 —— 目录穿越在读接口上挡过，
    # 在删除这条路上必须再挡一次
    if not target.is_relative_to(root):
        return "skipped", 0
    try:
        if not target.is_file():
            return "missing", 0
        size = target.stat().st_size
        if not dry_run:
            target.unlink(missing_ok=True)
            _prune_empty_dirs(target.parent, root)
        return "removed", size
    except PermissionError:
        return "locked", 0
    except OSError as exc:
        log.warning("处理 %s 失败：%s", rel, exc)
        return "locked", 0


def _prune_empty_dirs(leaf: Path, root: Path) -> None:
    """产物是按 jobs/<job-uuid8>/ 存的，文件删完那个目录就空了，顺手收掉。"""
    cur = leaf
    while cur != root and cur.is_relative_to(root):
        try:
            next(cur.iterdir())
            return  # 还有东西，别往上走了
        except (StopIteration, OSError):
            try:
                cur.rmdir()
            except OSError:
                return
            cur = cur.parent


async def purge_media_rows(ids: list[int], *, dry_run: bool = False) -> PurgeReport:
    """按 media id 真删。调用方负责只把「该删的」id 传进来（已进回收站且到期，或用户点了彻底删）。"""
    report = PurgeReport(dry_run=dry_run)
    if not ids:
        return report
    root = media_root()
    async with session_factory()() as s:
        rows = (
            await s.execute(
                sa_text("SELECT id, path, bytes FROM media WHERE id = ANY(:ids) ORDER BY id"),
                {"ids": list(ids)},
            )
        ).mappings().all()

    doomed: list[int] = []
    for r in rows:
        state, size = await asyncio.to_thread(_handle_file, root, str(r["path"]), dry_run=dry_run)
        if state == "skipped":
            report.skipped.append(int(r["id"]))
            log.warning("媒体 %s 路径越界，已拒绝删除：%s", r["id"], r["path"])
            continue
        if state == "locked":
            report.locked += 1
            continue
        report.bytes += int(size or r["bytes"] or 0)
        if state == "missing":
            report.orphans += 1
        else:
            report.deleted += 1
        if not dry_run:
            doomed.append(int(r["id"]))

    if doomed:
        async with session_factory()() as s:
            await s.execute(sa_text("DELETE FROM media WHERE id = ANY(:ids)"), {"ids": doomed})
            await s.commit()
    return report


STAMP_KEY = "trash_purge"


class TrashPurger:
    """到期回收的常驻任务。和 QueueDispatcher 一样的 start/stop 形状，但必须是**另一个**任务。

    为什么不挂进派发循环（那条路省事得多）：那个循环是单卡仲裁的承重结构 ——
    `_restore_if_idle()` 必须是 `dispatch_once` 的第一条语句，`_has_work_for()` 必须继续
    前置判空，这里出过「空队列时每 27 秒停一次再拉起 llama-server」的事故。多塞一步就多一次
    「后来的人把它包进 try 或提前 return」的机会；而回收要做阻塞磁盘 I/O，塞进 tick 会污染
    本就微妙的派发时序。
    """

    def __init__(self, *, interval_s: int | None = None) -> None:
        s = get_settings()
        self.interval_s = s.trash_purge_interval_s if interval_s is None else interval_s
        self.days = s.trash_retention_days
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self.last: dict[str, Any] = {}

    async def start(self) -> None:
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(), name="trash-purger")
        log.info("生成回收站清理已启动（保留 %d 天，每 %.1f 小时扫一趟）", self.days, self.interval_s / 3600)

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass

    async def _loop(self) -> None:
        # 开机先跑一趟：机器可能已经关了几周，100 天的承诺正是在这一刻兑现的
        await self.run_once(reason="startup")
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval_s)
            except TimeoutError:
                pass  # 到点了，跑一趟
            if self._stop.is_set():
                break
            await self.run_once(reason="interval")

    async def run_once(self, *, dry_run: bool = False, reason: str = "manual") -> dict[str, Any]:
        try:
            report = await purge_expired(dry_run=dry_run, days=self.days)
        except Exception as exc:
            # 回收这条路绝不能把宿主任务弄死：它失败的最坏结果应该是"晚点再清"，不是停摆
            log.exception("到期回收失败（%s）：%s", reason, exc)
            return {"error": str(exc), "reason": reason, "dryRun": dry_run}
        stamp = {"lastRunAt": dt.datetime.now(dt.timezone.utc).isoformat(), "reason": reason, **report}
        self.last = stamp
        await _record_stamp(stamp, audit=not dry_run, detail=report)
        if report["considered"]["media"] or report["considered"]["scriptVersions"]:
            log.info(
                "到期回收（%s%s）：删 %d 个文件 / %.1f MB，孤儿行 %d，被占用 %d，越界 %d，剧本 %d",
                reason,
                "·试运行" if dry_run else "",
                report["deleted"],
                report["bytes"] / 1048576,
                report["orphans"],
                report["locked"],
                report["skipped"],
                report["scriptsDeleted"],
            )
        return report


async def _record_stamp(stamp: dict[str, Any], *, audit: bool, detail: dict[str, Any]) -> None:
    """把上次执行的时间与结果留在 app_settings，让设置页能说清「上次自动回收是什么时候、
    释放了多少」—— 而不是像现在这样摆几行写死的假审计记录。
    """
    async with session_factory()() as s:
        await s.execute(
            sa_text(
                "INSERT INTO app_settings(key, value, updated_at) VALUES(:k, CAST(:v AS jsonb), now())"
                " ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=now()"
            ),
            {"k": STAMP_KEY, "v": json.dumps(stamp, ensure_ascii=False)},
        )
        # 自动任务删的是用户没看着的文件，必须留痕；试运行什么都不删，所以不记
        if audit:
            s.add(AuditLog(actor="system", action="trash.purge_expired", target="trash", detail=detail))
        await s.commit()


async def last_purge() -> dict[str, Any]:
    async with session_factory()() as s:
        raw = await s.scalar(sa_text("SELECT value FROM app_settings WHERE key=:k"), {"k": STAMP_KEY})
    return raw or {}


async def expired_media_ids(days: int | None = None, limit: int | None = None) -> list[int]:
    """回收站里待够天数的媒体行。"""
    s = get_settings()
    return await _expired(
        "SELECT id FROM media",
        days if days is not None else s.trash_retention_days,
        limit if limit is not None else s.trash_purge_limit,
    )


async def expired_script_version_ids(days: int | None = None, limit: int | None = None) -> list[int]:
    s = get_settings()
    return await _expired(
        "SELECT id FROM script_versions",
        days if days is not None else s.trash_retention_days,
        limit if limit is not None else s.trash_purge_limit,
    )


async def _expired(select_sql: str, days: int, limit: int) -> list[int]:
    async with session_factory()() as s:
        rows = (
            await s.execute(
                sa_text(
                    f"{select_sql} WHERE deleted_at IS NOT NULL AND deleted_at < now() - make_interval(days => :d)"
                    " ORDER BY id LIMIT :n"
                ),
                {"d": days, "n": limit},
            )
        ).scalars().all()
    return [int(r) for r in rows]


async def purge_script_versions(ids: list[int], *, dry_run: bool = False) -> int:
    """剧本版本没有磁盘文件，删行就是全删。返回处理条数。"""
    if not ids or dry_run:
        return len(ids or [])
    async with session_factory()() as s:
        await s.execute(sa_text("DELETE FROM script_versions WHERE id = ANY(:ids)"), {"ids": list(ids)})
        await s.commit()
    return len(ids)


async def purge_expired(*, dry_run: bool = False, days: int | None = None) -> dict[str, Any]:
    """一趟到期清理：媒体连文件、剧本只删行。

    `days` 是显式参数而不是去改那份 `@lru_cache` 的配置 —— 验收要造「超期」和「差一天」两条
    对照数据来证明天数过滤真的生效，而改配置既脏也测不到。
    """
    media_ids = await expired_media_ids(days=days)
    script_ids = await expired_script_version_ids(days=days)
    report = await purge_media_rows(media_ids, dry_run=dry_run)
    scripts = await purge_script_versions(script_ids, dry_run=dry_run)
    return {
        **report.as_dict(),
        "scriptsDeleted": scripts,
        "considered": {"media": len(media_ids), "scriptVersions": len(script_ids)},
    }
