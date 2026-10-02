r"""产物落盘时顺手把「这张文件到底是什么」读出来。

media 表早有 width/height/mime/duration_ms/fps 这些列，但 `_persist_outputs` 以前只写 bytes，
于是：时间轴与版本卡拿不到宽高（只能等图片下完才知道比例）、`isStill()` 这类按 mime 的门槛形同虚设、
成片列表的时长标签恒为「第 N 段」。图片走文件头（不起进程，几微秒），
音视频走 ffprobe（拿不到就留 NULL，绝不猜）。
"""

from __future__ import annotations

import json
import shutil
import struct
import subprocess
from pathlib import Path
from typing import Any

_MIME = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
    ".gif": "image/gif", ".mp4": "video/mp4", ".webm": "video/webm", ".mov": "video/quicktime",
    ".mp3": "audio/mpeg", ".wav": "audio/wav", ".flac": "audio/flac", ".ogg": "audio/ogg",
    ".m4a": "audio/mp4",
}

_IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp", ".gif")
_AV_EXT = (".mp4", ".webm", ".mov", ".mp3", ".wav", ".flac", ".ogg", ".m4a")


def _png_size(b: bytes) -> tuple[int, int] | None:
    if len(b) > 24 and b[:8] == b"\x89PNG\r\n\x1a\n" and b[12:16] == b"IHDR":
        w, h = struct.unpack(">II", b[16:24])
        return (w, h) if w and h else None
    return None


def _gif_size(b: bytes) -> tuple[int, int] | None:
    if len(b) > 10 and b[:6] in (b"GIF87a", b"GIF89a"):
        w, h = struct.unpack("<HH", b[6:10])
        return (w, h) if w and h else None
    return None


def _jpeg_size(b: bytes) -> tuple[int, int] | None:
    if len(b) < 4 or b[:2] != b"\xff\xd8":
        return None
    i = 2
    while i + 9 < len(b):
        if b[i] != 0xFF:
            i += 1
            continue
        marker = b[i + 1]
        # SOF0..SOF3、SOF5..SOF7、SOF9..SOF11、SOF13..SOF15 才是带尺寸的帧头
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            h, w = struct.unpack(">HH", b[i + 5:i + 9])
            return (w, h) if w and h else None
        seg = struct.unpack(">H", b[i + 2:i + 4])[0] if i + 4 <= len(b) else 0
        if seg < 2:
            break
        i += 2 + seg
    return None


def _webp_size(b: bytes) -> tuple[int, int] | None:
    if len(b) < 30 or b[:4] != b"RIFF" or b[8:12] != b"WEBP":
        return None
    fourcc = b[12:16]
    try:
        if fourcc == b"VP8X":
            w = int.from_bytes(b[24:27], "little") + 1
            h = int.from_bytes(b[27:30], "little") + 1
            return (w, h)
        if fourcc == b"VP8L":
            bits = int.from_bytes(b[21:25], "little")
            return ((bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1)
        if fourcc == b"VP8 " and len(b) > 40:
            w = struct.unpack("<H", b[38:40])[0] & 0x3FFF
            h = struct.unpack("<H", b[40:42])[0] & 0x3FFF
            return (w, h) if w and h else None
    except Exception:
        return None
    return None


def image_size(b: bytes) -> tuple[int, int] | None:
    for probe in (_png_size, _gif_size, _jpeg_size, _webp_size):
        got = probe(b)
        if got:
            return got
    return None


def _ffprobe(path: Path) -> dict[str, Any]:
    """有 ffprobe 才问，问不到就空着。导合成本来就依赖 ffmpeg，这台机上通常有。"""
    exe = shutil.which("ffprobe")
    if not exe:
        return {}
    try:
        out = subprocess.run(
            [exe, "-v", "error", "-show_format", "-show_streams", "-print_format", "json", str(path)],
            capture_output=True, text=True, timeout=20, encoding="utf-8", errors="replace",
        )
        info = json.loads(out.stdout or "{}")
    except Exception:
        return {}
    rows: dict[str, Any] = {}
    try:
        if info.get("format", {}).get("duration"):
            rows["duration_ms"] = int(float(info["format"]["duration"]) * 1000)
    except (TypeError, ValueError):
        pass
    for st in info.get("streams") or []:
        if st.get("codec_type") == "video" and "width" not in rows:
            rows["width"], rows["height"] = int(st["width"]), int(st["height"])
            rate = str(st.get("avg_frame_rate") or st.get("r_frame_rate") or "")
            if "/" in rate:
                num, _, den = rate.partition("/")
                try:
                    fps = float(num) / float(den)
                    if fps > 0:
                        rows["fps"] = round(fps, 3)
                except (ValueError, ZeroDivisionError):
                    pass
            break
        if st.get("codec_type") == "audio" and "duration_ms" not in rows and st.get("duration"):
            try:
                rows["duration_ms"] = int(float(st["duration"]) * 1000)
            except (TypeError, ValueError):
                pass
    return rows


def probe(path: Path, data: bytes) -> dict[str, Any]:
    """返回能塞进 Media(...) 的字段。读不出来的键一律不出现，让列保持 NULL。"""
    ext = path.suffix.lower()
    out: dict[str, Any] = {}
    mime = _MIME.get(ext)
    if mime:
        out["mime"] = mime
    if ext in _IMAGE_EXT:
        size = image_size(data)
        if size:
            out["width"], out["height"] = size
    elif ext in _AV_EXT:
        out.update(_ffprobe(path))
    return out
