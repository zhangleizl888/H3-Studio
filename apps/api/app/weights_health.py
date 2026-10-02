"""权重文件体检：认出「尺寸对但数据是零」的半截下载。

为什么要有这个：HF/ModelScope 的下载器会先把文件**预分配**成最终大小再逐块写。
中断之后留在盘上的就是一个尺寸完全正确、内容却大片是零的 safetensors。
ComfyUI 加载它不报错（张量名与形状都对得上），于是前向输出恒等于 0，
采样器每一步都「什么也没做」，最后把初始噪声交给 VAE —— 出来的是一张雪花，
任务状态是 succeeded。2026-10-02 的体检就是靠这条抓出 `flux-2-klein-base-9b.safetensors`
只有 17/201 个张量有数据。

判据取两个信号一起看：
- 每个张量开头 4KB 是否全零（正常权重不会成片全零）；
- 文件末尾 1MB 有没有任何非零字节（预分配没写完的铁证）。
只读头部与抽样，一个 18 GB 的文件也是几十毫秒的事。
"""

from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import Any

SAMPLE_BYTES = 4096
TAIL_BYTES = 1 << 20
# 单个文件最多抽查多少个张量：上千张量的合并包也够代表性，且不让体检变成 IO 大头
MAX_TENSORS = 1200

# (路径, mtime_ns, 大小) → 结论。同一份权重在探活/入队/校验里会被反复问，没必要每次重读
_CACHE: dict[tuple[str, int, int], dict[str, Any]] = {}


def safetensors_health(path: Path) -> dict[str, Any]:
    """返回 {"ok","tensors","zeroTensors","tailEmpty","error"}。读不动就 ok=True（不拦路）。"""
    try:
        size = path.stat().st_size
        mtime = path.stat().st_mtime_ns
    except OSError as exc:
        return {"ok": True, "tensors": 0, "zeroTensors": 0, "tailEmpty": False, "error": str(exc)}
    key = (str(path), mtime, size)
    hit = _CACHE.get(key)
    if hit is not None:
        return hit

    out: dict[str, Any] = {"ok": True, "tensors": 0, "zeroTensors": 0, "tailEmpty": False, "error": None}
    try:
        with path.open("rb") as f:
            head_len = struct.unpack("<Q", f.read(8))[0]
            if head_len <= 0 or head_len > 200_000_000:
                out["error"] = f"safetensors 头部长度异常（{head_len}）"
                return out
            head = json.loads(f.read(head_len))
        names = [k for k in head if k != "__metadata__"]
        if not names:
            out["error"] = "头部里没有张量"
            return out
        picked = names[:MAX_TENSORS]
        zero = 0
        with path.open("rb") as f:
            base = 8 + head_len
            for name in picked:
                off = head[name].get("data_offsets") or [0, 0]
                f.seek(base + int(off[0]))
                if not any(f.read(SAMPLE_BYTES)):
                    zero += 1
            f.seek(max(0, size - TAIL_BYTES))
            tail_empty = not any(f.read())
        out.update({
            "tensors": len(picked),
            "zeroTensors": zero,
            "tailEmpty": tail_empty,
            "ok": not _suspect(zero, len(picked), tail_empty),
        })
    except Exception as exc:  # 读坏了不是体检的错，别拿它拦任务
        out["error"] = f"{type(exc).__name__}: {exc}"
    if len(_CACHE) > 256:
        _CACHE.clear()
    _CACHE[key] = out
    return out


def _suspect(zero: int, total: int, tail_empty: bool) -> bool:
    """零填充比例过高，或者「有点零 + 末尾完全没数据」，都按半截下载处理。"""
    if total <= 0:
        return False
    ratio = zero / total
    return ratio >= 0.20 or (ratio >= 0.05 and tail_empty)


def describe(path: Path, health: dict[str, Any]) -> str:
    return (f"{path.name}：{health['zeroTensors']}/{health['tensors']} 个张量开头 "
            f"{SAMPLE_BYTES} B 全零" + ("，且文件末尾没有写入数据" if health["tailEmpty"] else ""))


def models_root(output_root: Path | None) -> Path | None:
    """从实例的 output 目录推出 models 目录（ComfyUI 的固定布局：ComfyUI/output、ComfyUI/models）。

    ComfyUI 的 /models/{folder} 只给文件名不给路径，所以本机实例才做得成这个体检。
    """
    if not output_root:
        return None
    try:
        root = Path(output_root).parent / "models"
    except OSError:
        return None
    return root if root.is_dir() else None


def find_weight(root: Path, filename: str) -> Path | None:
    """在 models 目录里按文件名找权重（含子目录，最深两层）。找不到返回 None。"""
    name = Path(filename).name
    if not name:
        return None
    direct = list(root.glob(name))
    if direct:
        return direct[0]
    for sub in sorted(p for p in root.iterdir() if p.is_dir()):
        try:
            hits = list(sub.glob(name)) + list(sub.glob(f"*/*{name}"))
        except OSError:
            continue
        for h in hits:
            if h.is_file():
                return h
    return None


def check_weight_file(root: Path | None, filename: str) -> str | None:
    """给一条图上的权重引用：有问题返回一句人话，没问题返回 None。"""
    if not root or not filename:
        return None
    path = Path(filename) if Path(filename).is_absolute() else find_weight(root, filename)
    if not path or not path.is_file():
        return None
    if path.suffix.lower() != ".safetensors":
        return None  # ckpt/bin 的布局不是 safetensors，这套判据套不上就不下结论
    h = safetensors_health(path)
    if h["ok"]:
        return None
    return ("这份权重是半截下载（" + describe(path, h) + "）。"
            "它喂进模型后输出恒为 0，采样等于没跑，只会得到一张能正常解码的雪花 —— 请重新下载它，别把这张图当结果。")
