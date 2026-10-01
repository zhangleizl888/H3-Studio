"""真跑一次 Qwen-Image 2.1 出图，验证：权重发现 → 建图 → /prompt → 产物落盘。

用法：
  .venv/Scripts/python.exe scripts/smoke_image.py                      # 纯文生图
  .venv/Scripts/python.exe scripts/smoke_image.py --ref 某张图.png      # 带参考图（角色一致性）
  .venv/Scripts/python.exe scripts/smoke_image.py --width 1344 --height 768
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.gen.builtin_graphs import discover_qwen_weights, qwen_image_graph  # noqa: E402
from app.gen.comfy_native import ComfyNativeClient  # noqa: E402
from app.logging_setup import get_logger, setup_logging  # noqa: E402

log = get_logger("smoke.image")

BASE = "http://127.0.0.1:8188"
OUT = Path("F:/H3/data/media/smoke")

PROMPT = (
    "真人影视质感，东亚女性二十岁出头，乌黑长发挽成低髻，深墨青色绣暗纹云鹤长袍，"
    "立于宫殿廊柱阴影中，一手收袖中，目光冷静打量远处。"
    "日光自右侧高窗斜射，尘埃在光柱里浮动，胶片质感，电影级构图与景深，8K。"
)
NEGATIVE = "低分辨率，模糊，肢体畸形，多余手指，卡通，插画，过曝，文字水印"


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompt", default=PROMPT)
    ap.add_argument("--negative", default=NEGATIVE)
    ap.add_argument("--width", type=int, default=1024)
    ap.add_argument("--height", type=int, default=1024)
    ap.add_argument("--steps", type=int, default=25)
    ap.add_argument("--cfg", type=float, default=1.0)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--ref", action="append", default=[], help="本地参考图路径，可重复")
    args = ap.parse_args()

    setup_logging("INFO")
    OUT.mkdir(parents=True, exist_ok=True)

    client = ComfyNativeClient(BASE, local_output_root=r"F:/H3/comfyui/ComfyUI/output", timeout_s=1800)
    try:
        weights = await discover_qwen_weights(client)
        if weights.missing:
            print(f"[失败] 缺权重：{weights.missing}")
            return 1

        refs: list[str] = []
        for p in args.ref:
            src = Path(p)
            if not src.exists():
                print(f"[失败] 参考图不存在：{src}")
                return 1
            refs.append(await client.upload(src))
        print(f"参考图：{refs or '（无，纯文生图）'}")

        graph = qwen_image_graph(
            weights,
            prompt=args.prompt,
            negative_prompt=args.negative,
            width=args.width,
            height=args.height,
            steps=args.steps,
            cfg=args.cfg,
            seed=args.seed,
            filename_prefix="h3smoke/img",
            ref_images=refs,
        )
        missing = await client.missing_models(graph)
        if missing:
            print(f"[失败] 建图后仍缺模型：{missing}")
            return 1

        client_id = str(uuid.uuid4())
        prompt_id = str(uuid.uuid4())
        size = f"{args.width}x{args.height}" if not refs else "跟随参考图"
        print(f"提交：{size} / {args.steps} 步 / cfg {args.cfg} / seed {args.seed}")

        done = asyncio.Event()
        last: dict[str, float] = {}

        async def watch() -> None:
            async for etype, payload in client.events(client_id):
                if etype == "progress":
                    val = (payload.get("value") or 0) / max(payload.get("max") or 1, 1)
                    print(f"  进度 {val:.0%}", end="\r", flush=True)
                    last["pct"] = val
                if etype == "execution_error":
                    print(f"\n[节点报错] {payload.get('node_type')}: {payload.get('exception_message')}")
                    done.set()
                    return
                if etype == "executing" and payload.get("node") is None and payload.get("prompt_id") == prompt_id:
                    print()
                    done.set()
                    return

        task = asyncio.create_task(watch())
        t0 = time.perf_counter()
        sub = await client.submit(graph, client_id=client_id, job_ref=prompt_id)
        print(f"已入队 prompt_id={prompt_id} number={sub.queue_number}")
        try:
            await asyncio.wait_for(done.wait(), timeout=1700)
        except asyncio.TimeoutError:
            print("[失败] 等不到终止信号")
            return 1
        finally:
            task.cancel()

        elapsed = time.perf_counter() - t0
        snap = await client.snapshot(sub)
        print(f"耗时 {elapsed:.1f}s，状态={snap.state.value}")
        if snap.error:
            print(f"[失败] {snap.error}")
            return 1
        if not snap.outputs:
            print("[失败] 没有产物")
            return 1
        for ref in snap.outputs:
            data = await client.fetch_output(ref)
            dest = OUT / Path(ref.filename).name
            dest.write_bytes(data)
            print(f"[成功] {dest}（{len(data) / 1024:.0f} KB）")
        return 0
    finally:
        await client.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
