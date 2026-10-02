r"""让导出的 XML / EDL 指向**磁盘上真的那个文件**。

以前 FCP7 XML 里写的是 `file://localhost/shot_001.mp4` —— 一个按序号编出来的假名字。
剪映/Premiere/Resolve 导入时会把所有素材报成 offline，等于这张时间轴白导。
（EDL 只有 CLIP NAME，不带路径，问题轻一些，但也顺手用真文件名。）
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from .config import get_settings
from .models import Media


def media_file(m: Media) -> Path:
    return (Path(get_settings().media_root).resolve() / m.path).resolve()


def file_url(m: Media) -> str:
    """Windows 下要的是 file:///F:/H3/data/media/... 这种三斜杠形式，空格与非 ASCII 得转义。"""
    p = str(media_file(m)).replace("\\", "/")
    return f"file:///{quote(p.lstrip('/'), safe='/:@')}"
