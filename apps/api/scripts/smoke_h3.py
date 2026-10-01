"""真跑一次 H3 文生视频，验证整条链路：
权重发现 → 建图 → /prompt → WS 事件 → /history → 产物落盘。

用法：
  .venv/Scripts/python.exe scripts/smoke_h3.py [--seconds 5] [--turbo]
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.gen.builtin_graphs import discover_h3_weights, h3_length, h3_video_graph  # noqa: E402
from app.gen.comfy_native import ComfyNativeClient  # noqa: E402
from app.logging_setup import get_logger, setup_logging  # noqa: E402

log = get_logger("smoke")

BASE = "http://127.0.0.1:8188"
OUT = Path("F:/H3/data/media/smoke")

PROMPT = (
    "integrated_multimodal_description: 夜色里的城市辅路，湿沥青反着昏黄路牌的光。"
    "一辆空出租车从画面右侧缓缓驶来，雨丝在车灯前掠过。镜头缓慢推近。<d>[中文] 那个地方八年前就拆了。</d>\n"
    "overall_soundscape: 细雨敲打车顶，远处高架胎噪，雨刮器机械声\n"
    "non_diegetic_music: 低音大提琴单音，极慢"
)


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=5.0)
    ap.add_argument("--turbo", action="store_true", default=True)
    ap.add_argument("--no-turbo", dest="turbo", action="store_false")
    ap.add_argument("--width", type=int, default=864)
    ap.add_argument("--height", type=int, default=480)
    args = ap.parse_args()

    setup_logging("INFO")
    OUT.mkdir(parents=True, exist_ok=True)

    # 帧数必须由同一个公式算，且向上吸附到 17k+5
    length = h3_length(args.seconds)
    steps = 8 if args.turbo else 25

    client = ComfyNativeClient(BASE, local_output_root=r"F:/H3/comfyui/ComfyUI/output", timeout_s=3600)
    try:
        weights = await discover_h3_weights(client)
        if weights.missing:
            print(f"[失败] 实例缺权重：{weights.missing}")
            print(f"       UNET 候选：{await _raw_choices(client, 'UNETLoader', 'unet_name')}")
            return 1

        graph = h3_video_graph(
            weights,
            prompt=PROMPT,
            width=args.width,
            height=args.height,
            length=length,
            steps=steps,
            seed=42,
            filename_prefix="h3smoke/smoke",
            use_turbo=args.turbo,
        )

        missing = await client.missing_models(graph)
        if missing:
            print(f"[失败] 建图后仍报缺权重：{missing}")
            return 1

        client_id = str(uuid.uuid4())
        prompt_id = str(uuid.uuid4())
        print(f"提交：{length} 帧 / {steps} 步 / {args.width}x{args.height} / turbo={args.turbo}")

        events: list[str] = []
        done = asyncio.Event()

        async def watch() -> None:
            async for etype, payload in client.events(client_id):
                events.append(etype)
                if etype == "progress":
                    pct = (payload.get("value") or 0) / max(payload.get("max") or 1, 1)
                    print(f"  进度 {pct:.0%}  节点 {payload.get('node')}", flush=True)
                if etype == "executing" and payload.get("node") is None and payload.get("prompt_id") == prompt_id:
                    done.set()
                    return

        task = asyncio.create_task(watch())
        t0 = time.perf_counter()
        sub = await client.submit(graph, client_id=client_id, job_ref=prompt_id)
        print(f"已入队 prompt_id={prompt_id} number={sub.queue_number}")

        try:
            await asyncio.wait_for(done.wait(), timeout=3300)
        except asyncio.TimeoutError:
            print("[失败] 55 分钟没等到终止信号")
            print(f"       收到事件：{events[:20]}…")
            return 1
        finally:
            task.cancel()

        elapsed = time.perf_counter() - t0
        snap = await client.snapshot(sub)
        print(f"耗时 {elapsed:.1f}s，状态={snap.state.value}，事件数={len(events)}")
        if snap.error:
            print(f"[失败] {snap.error}")
            return 1
        if not snap.outputs:
            print("[失败] history 里没有产物")
            return 1

        ref = snap.outputs[0]
        data = await client.fetch_output(ref)
        dest = OUT / ref.filename
        dest.write_bytes(data)
        print(f"[成功] 产物：{dest}（{len(data)/1024/1024:.2f} MB）")
        print(f"       节点输出键：images（SaveVideo 也是这个键）→ {ref.filename}")
        return 0
    finally:
        await client.close()


async def _raw_choices(client, class_type, field):
    info = await client.object_info(class_type)
    spec = ((info.get(class_type) or {}).get("input") or {}).get("required", {}).get(field)
    return spec[0] if isinstance(spec, list) and spec and isinstance(spec[0], list) else []


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
