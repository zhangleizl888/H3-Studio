"""成片导出路由：合并 MP4、素材包、EDL / FCP XML。

服务端不持有项目实体（A 方案），所以「哪些镜头、按什么顺序」由前端在请求体里给全，
后端只负责按 mediaId 找文件。这样换浏览器也不会丢导出能力，但反过来
——后端绝不猜「这个项目应该有哪几个镜头」。
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import uuid
import xml.sax.saxutils as sax
import zipfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select, text

from .. import runtime
from ..config import get_settings
from ..export_paths import file_url as media_url
from ..db import session_factory
from ..logging_setup import get_logger
from ..models import Media
from ..security import login_gate
from .common import CamelModel

log = get_logger("api.export")
router = APIRouter(tags=["export"])

_VIDEO_EXT = (".mp4", ".webm", ".mov", ".mkv")


class MergeBody(CamelModel):
    media_ids: list[int]
    title: str | None = None
    reencode: bool = False


class PackBody(CamelModel):
    items: list[dict[str, Any]] = []  # {"mediaId": 1, "path": "characters/柳如霜.png"}
    title: str | None = None


class ShotRow(CamelModel):
    index: int
    title: str | None = None
    duration_sec: float = 5.0
    media_id: int | None = None
    action: str | None = None
    camera_movement: str | None = None
    scene_name: str | None = None


class TimelineBody(CamelModel):
    shots: list[ShotRow]
    title: str | None = None


async def _media_map(ids: list[int | None]) -> dict[int, Media]:
    """按 id 拿没被软删的媒体行。这里不报错：导出的镜头可能还没出片，缺的要能列进 skipped。"""
    want = [int(i) for i in ids if i]
    if not want:
        return {}
    async with session_factory()() as s:
        return {
            m.id: m
            for m in (
                await s.execute(select(Media).where(Media.id.in_(want), Media.deleted_at.is_(None)))
            ).scalars()
        }


async def _ffmpeg() -> str | None:
    """先看「设置 → 系统」里存的可执行文件，再退到 PATH。

    存了但那个文件已经不存在（挪过盘、卸过软件）就当没存过，不能拿着死路径去起进程。
    """
    from .. import paths as path_store

    ff = (await path_store.load_overrides()).get("ffmpeg")
    if ff and Path(ff).is_file():
        return ff
    return runtime.ffmpeg_binary()


async def _resolve(media_ids: list[int]) -> list[Media]:
    if not media_ids:
        raise HTTPException(400, "没有给任何镜头产物，导不出东西")
    async with session_factory()() as s:
        rows = {
            m.id: m
            for m in (
                await s.execute(select(Media).where(Media.id.in_(media_ids), Media.deleted_at.is_(None)))
            ).scalars()
        }
        # 必须排除软删行：不然用户从回收站里删掉的成片照样会被 ffmpeg 拼进导出，
        # 「删除即从所有地方摘干净」这条就成了空话
    missing = [i for i in media_ids if i not in rows]
    if missing:
        raise HTTPException(404, f"媒体 {missing} 不在库里（可能还没渲染完、已进回收站，或已被清理）")
    ordered = [rows[i] for i in media_ids]
    root = Path(get_settings().media_root).resolve()
    for m in ordered:
        target = (root / m.path).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            raise HTTPException(404, f"媒体 {m.id} 的文件不在磁盘上：{m.path}")
    return ordered


async def _finish(rel: str, size: int, mime: str, kind: str, project_key: str | None, title: str) -> dict[str, Any]:
    media_id = str(uuid.uuid4())
    # 导出产物也要带宽高/时长：成片列表的时长标签以前恒是「第 N 段」，就是因为这行没人填
    from .. import media_probe

    abs_path = Path(get_settings().media_root).resolve() / rel
    head = b""
    try:
        with abs_path.open("rb") as fh:
            head = fh.read(128 * 1024)
    except OSError:
        pass
    props = media_probe.probe(abs_path, head)
    async with session_factory()() as s:
        row = (
            await s.execute(
                text(
                    """
                    INSERT INTO media(uuid, project_key, kind, role, ref_id, path, bytes, mime,
                                      width, height, fps, duration_ms, origin, meta)
                    VALUES (:u, :p, :k, 'export', :r, :path, :b, :mime,
                            :w, :h, :fps, :dur, '{}'::jsonb, :meta)
                    RETURNING id
                    """
                ),
                {
                    "u": media_id,
                    "p": project_key,
                    "k": kind,
                    "r": title[:128],
                    "path": rel,
                    "b": size,
                    "mime": props.get("mime") or mime,
                    "w": props.get("width"),
                    "h": props.get("height"),
                    "fps": props.get("fps"),
                    "dur": props.get("duration_ms"),
                    "meta": json.dumps({"title": title}, ensure_ascii=False),
                },
            )
        ).scalar_one()
        await s.commit()
    mid = int(row)
    return {"mediaId": str(mid), "url": f"/api/media/{mid}/download", "bytes": size, "path": rel}


@router.post("/projects/{project_key}/export/merge")
async def export_merge(project_key: str, body: MergeBody, _: Any = Depends(login_gate)) -> dict[str, Any]:
    """按给定顺序把镜头视频合成一条 MP4。

    先试 `-c copy`（秒级），失败再重编码 —— 各段编码参数一致时 copy 就够了，
    但预览档和全质量档混在一起时 copy 会花屏，只能重编码。
    """
    exe = await _ffmpeg()
    if not exe:
        raise HTTPException(501, "PATH 里没有 ffmpeg，装一个再来（winget install Gyan.FFmpeg），"
                                 "或者在「设置 → 系统 → 目录」里直接指到那个 ffmpeg.exe")
    rows = await _resolve(body.media_ids)
    s = get_settings()
    out_dir = Path(s.media_root) / "exports" / project_key
    out_dir.mkdir(parents=True, exist_ok=True)
    list_file = out_dir / f"concat_{uuid.uuid4().hex[:8]}.txt"
    list_file.write_text("".join(f"file '{(Path(s.media_root) / m.path).as_posix()}'\n" for m in rows), encoding="utf-8")
    dest = out_dir / f"{(body.title or 'master').replace(' ', '_')}_{uuid.uuid4().hex[:6]}.mp4"

    def _run(cmd: list[str]) -> subprocess.CompletedProcess:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=1800, encoding="utf-8", errors="replace")

    copy_cmd = [exe, "-y", "-f", "concat", "-safe", "0", "-i", str(list_file), "-c", "copy", str(dest)]
    proc = await asyncio.to_thread(_run, copy_cmd)
    mode = "copy"
    if proc.returncode != 0 or not dest.is_file() or dest.stat().st_size < 1024:
        log.info("concat -c copy 失败，改用重编码：%s", (proc.stderr or "")[:200])
        re_cmd = [
            exe, "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
            "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(dest),
        ]
        proc = await asyncio.to_thread(_run, re_cmd)
        mode = "reencode"
    list_file.unlink(missing_ok=True)
    if proc.returncode != 0 or not dest.is_file():
        raise HTTPException(502, f"ffmpeg 合成失败（{mode}）：{(proc.stderr or '')[-400:]}")

    rel = str(dest.relative_to(Path(s.media_root)).as_posix())
    out = await _finish(rel, dest.stat().st_size, "video/mp4", "video", project_key, body.title or "成片")
    out["mode"] = mode
    out["segments"] = len(rows)
    return out


@router.post("/projects/{project_key}/export/pack")
async def export_pack(project_key: str, body: PackBody, _: Any = Depends(login_gate)) -> dict[str, Any]:
    """素材包：按前端给的目录树打 zip（角色/场景/关键帧/视频各归各目录）。"""
    s = get_settings()
    root = Path(s.media_root).resolve()
    items = list(body.items)
    if not items:
        # 没给目录树就打包这个项目名下的全部产物
        async with session_factory()() as sess:
            rows = (
                await sess.execute(
                    text("SELECT id, path FROM media WHERE project_key=:p AND deleted_at IS NULL ORDER BY id"),
                    {"p": project_key},
                )
            ).mappings().all()
        items = [{"mediaId": int(r["id"]), "path": Path(str(r["path"])).name} for r in rows]
    ids = [int(i["mediaId"]) for i in items if i.get("mediaId")]
    rows = {m.id: m for m in await _resolve(ids)} if ids else {}
    out_dir = Path(s.media_root) / "exports" / project_key
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / f"assets_{uuid.uuid4().hex[:6]}.zip"

    def _pack() -> int:
        with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
            for i, item in enumerate(items):
                m = rows.get(int(item.get("mediaId") or 0))
                if m is None:
                    continue
                src = (root / m.path).resolve()
                name = str(item.get("path") or Path(m.path).name).replace("\\", "/")
                z.write(src, f"{(body.title or 'assets').replace(' ', '_')}/{i:03d}_{name}")
        return dest.stat().st_size

    size = await asyncio.to_thread(_pack)
    if size < 22:  # 空 zip 的体积
        dest.unlink(missing_ok=True)
        raise HTTPException(400, "素材包里一个文件都没有：先确认镜头产物已经落库")
    rel = str(dest.relative_to(root).as_posix())
    return await _finish(rel, size, "application/zip", "archive", project_key, body.title or "素材包")


def _tc(seconds: float) -> str:
    total = max(0.0, seconds)
    h = int(total // 3600)
    m = int((total % 3600) // 60)
    s = int(total % 60)
    f = int(round((total - int(total)) * 30))
    return f"{h:02d}:{m:02d}:{s:02d}:{f % 30:02d}"


@router.post("/projects/{project_key}/export/edl")
async def export_edl(project_key: str, body: TimelineBody, _: Any = Depends(login_gate)) -> dict[str, Any]:
    """CMX3600 EDL。manga-studio 这里只弹一句「暂未开发」，我们真出。"""
    rows = await _media_map([s.media_id for s in body.shots])
    lines = [f"TITLE: {body.title or project_key}", "FCM: NON-DROP FRAME", ""]
    cursor = 0.0
    for i, shot in enumerate(body.shots, start=1):
        src_in, src_out = cursor, cursor + shot.duration_sec
        record_in, record_out = src_in, src_out
        name = (shot.title or f"SHOT {i:03d}").replace(" ", "_")[:24]
        media = rows.get(shot.media_id) if shot.media_id else None
        clip_name = Path(media.path).name if media else (shot.title or f"shot_{i:03d}")
        lines += [
            f"{i:03d}  {name:<24} V     C        "
            f"{_tc(src_in)} {_tc(src_out)} {_tc(record_in)} {_tc(record_out)}",
            f"* FROM CLIP NAME: {clip_name}",
            f"* SCENE: {shot.scene_name or ''}",
            f"* CAMERA MOVEMENT: {shot.camera_movement or ''}",
            "",
        ]
        cursor = src_out
    return {"format": "edl", "text": "\n".join(lines)}


@router.post("/projects/{project_key}/export/xml")
async def export_xml(project_key: str, body: TimelineBody, _: Any = Depends(login_gate)) -> dict[str, Any]:
    """FCP7 XML（剪映/Premiere/Resolve 都吃这个）。

    pathurl 必须是磁盘上真的那个文件：以前写的是按序号编出来的 `shot_001.mp4`，
    导进去整条时间轴全是 offline 素材，等于白导。没有产物的镜头不硬编假路径，
    而是跳到 skipped 里说清楚。
    """
    rows = await _media_map([s.media_id for s in body.shots])
    seq_in = 0.0
    items = []
    skipped: list[str] = []
    for i, shot in enumerate(body.shots, start=1):
        media = rows.get(shot.media_id) if shot.media_id else None
        if media is None:
            skipped.append(f"镜 {shot.index or i}（还没有可指向的产物）")
            continue
        start_tc = _tc(seq_in)
        end_tc = _tc(seq_in + shot.duration_sec)
        items.append(
            "        <clip-item><name>{name}</name><in>{tin}</in><out>{tout}</out>"
            "<start>{sin}</start><end>{eout}</end>"
            "<file><pathurl>{url}</pathurl><name>{fname}</name></file>"
            "<rate><timebase>30</timebase><ntsc>TRUE</ntsc></rate></clip-item>".format(
                name=sax.escape(shot.title or f"SHOT {i:03d}"),
                tin="00:00:00:00",
                tout=_tc(shot.duration_sec),
                sin=start_tc,
                eout=end_tc,
                url=sax.escape(media_url(media)),
                fname=sax.escape(Path(media.path).name),
            )
        )
        seq_in += shot.duration_sec
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n<xmeml version="4">\n <sequence>\n'
        f"  <name>{sax.escape(body.title or project_key)}</name>\n  <video><trackformat><track>\n"
        + "\n".join(items)
        + "\n  </track></trackformat></video>\n </sequence>\n</xmeml>\n"
    )
    out: dict[str, Any] = {"format": "xml", "text": xml, "clips": len(items)}
    if skipped:
        out["skipped"] = skipped
    return out


@router.post("/projects/{project_key}/export/jianying")
async def export_jianying(project_key: str, body: TimelineBody, _: Any = Depends(login_gate)) -> dict[str, Any]:
    """剪映草稿。依赖 pyJianYingDraft（PLAN §9 里排在 M6），本机还没装 —— 如实报，不给假草稿。"""
    raise HTTPException(
        501,
        "剪映草稿还没接通：需要装 pyJianYingDraft 并按它的草稿目录约定落盘（PLAN.md §11 M6）。"
        "现在可以先用 EDL / XML 导出时间轴，剪映能导入 XML。",
    )
