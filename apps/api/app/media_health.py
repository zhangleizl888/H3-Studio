"""产物内容体检：认出「能解码、但不是图」的输出。

为什么要有这个：权重是半截下载（见 `weights_health`）时，网络每步输出恒为 0，
采样器等于没跑，最后交给 VAE 的是初始噪声 —— 出来的是一张**能正常解码的雪花**，
宽高、字节数、mime 全都对，任务标 succeeded。2026-10-02 那轮体检里，Klein 的
定妆/场景/首帧全这么"成功"过，接口层一点异常都看不出来。

判据是像素层面的相邻差（MAD）与整体标准差，本机实测分得很开：

| 内容 | MAD | std |
| --- | --- | --- |
| 真出的图（定妆、场景、首帧） | 1.9 – 3.4 | 42 – 59 |
| 雪花（噪声 latent 被 VAE 解码） | 11.3 – 12.8 | 29 – 34 |
| 纯随机噪声（未经 VAE） | ~87 | ~74 |
| 纯色（空 latent 直接解码） | 0.3 | 0.8 |

雪花的特点是**高频但低对比**：MAD 远高于正常图、std 又低于正常图。
所以两条一起看，而不是只卡一个 MAD —— 真实的高纹理内容（碎石、雨夜、胶片颗粒）
MAD 也会上去，但它的 std 跟着上去。
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from . import runtime

# 正常图与雪花之间留出来的余量：实测正常 ≤3.4、雪花 ≥11.3
MAD_SUSPECT = 8.0
STD_CEILING = 42.0
# 整张几乎纯色（空 latent 直接解码就是这个）
FLAT_MAD = 1.5
FLAT_STD = 1.5
# 灰度裸帧最大读多少字节（4K 单帧约 830 万，再大就只抽中间一块）
MAX_SCAN = 8_400_000


async def ffmpeg_exe() -> str | None:
    """先看「设置 → 系统」里存的可执行文件，再退到 PATH。"""
    from . import paths as path_store

    ff = (await path_store.load_overrides()).get("ffmpeg")
    if ff and Path(ff).is_file():
        return str(ff)
    return runtime.ffmpeg_binary()


def _gray_frame(exe: str, path: Path, w: int, h: int) -> bytes | None:
    try:
        p = subprocess.run([exe, "-loglevel", "error", "-i", str(path),
                            "-vf", "format=gray", "-f", "rawvideo", "-"],
                           capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    if p.returncode != 0:
        return None
    frame = p.stdout
    return frame if len(frame) >= w * h else None


def image_health(path: Path, width: int | None, height: int | None, *, exe: str | None = None) -> dict[str, Any] | None:
    """返回 {"mad","std","ok","why"}；判不了就 None（不拦路，也不假装判过）。"""
    if not (width and height) or int(width) <= 0 or int(height) <= 0:
        return None
    exe = exe or runtime.ffmpeg_binary()
    if not exe:
        return None
    w, h = int(width), int(height)
    if w * h > MAX_SCAN:                       # 超大的只按能扫下的那块判，够用
        h = max(1, MAX_SCAN // w)
    frame = _gray_frame(exe, path, w, h)
    if frame is None:
        return None

    # 抽行抽列：整帧几百万字节没必要逐个像素走
    row_step = max(1, h // 64)
    col_step = max(1, (w - 1) // 128) if w > 2 else 1
    diffs: list[int] = []
    vals: list[int] = []
    for y in range(0, h, row_step):
        base = y * w
        for x in range(0, max(0, w - 1), col_step):     # x+1 不能越到下一行去
            a = frame[base + x]
            diffs.append(abs(a - frame[base + x + 1]))
            vals.append(a)
    if not diffs:
        return None
    mad = sum(diffs) / len(diffs)
    mean = sum(vals) / len(vals)
    std = (sum((v - mean) ** 2 for v in vals) / len(vals)) ** 0.5
    out: dict[str, Any] = {"mad": round(mad, 1), "std": round(std, 1), "ok": True, "why": None}
    if mad >= MAD_SUSPECT and std <= STD_CEILING:
        out.update(ok=False, why=(f"这张图是雪花噪声（相邻像素平均差 {mad:.1f}、整体标准差 {std:.1f}）。"
                                  "多半是权重没真的加载进去：模型每步输出≈0，采样器等于没跑，"
                                  "交出去的就是初始噪声。先按「设置 → 系统」体检那份底模"))
    elif mad <= FLAT_MAD and std <= FLAT_STD:
        out.update(ok=False, why=(f"这张图是纯色（相邻像素平均差 {mad:.1f}、整体标准差 {std:.1f}）。"
                                  "采样链没产出内容，检查空 latent 是不是被直接解码了"))
    return out
