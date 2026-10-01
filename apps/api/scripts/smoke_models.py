"""图片 + 视频真跑一遍：Qwen-Image-2.1 出图、MiniMax H3 出片，都走队列（不是直投）。

跑法（后端与 ComfyUI 要先起着）：
  F:/H3/apps/api/.venv/Scripts/python.exe apps/api/scripts/smoke_models.py

为什么走队列：直投 /instances/{id}/submit 不记账，产物也不会转存进 media；
只有队里跑一遍才算真的把「派发 → 执行 → 转存 → 可读回」这条链接上了。
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
import uuid
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.gen.builtin_graphs import discover_h3_weights, h3_length, h3_t2v_graph  # noqa: E402
from app.gen.comfy_native import ComfyNativeClient  # noqa: E402
from app.gen.subgraph import flatten_subgraphs  # noqa: E402
from app.gen.workflow import ui_to_api  # noqa: E402

API = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8788").rstrip("/")
COMFY = "http://127.0.0.1:8188"
MEDIA_ROOT = Path("F:/H3/data/media")
TEMPLATE = r"F:\H3\comfyui\.venv\Lib\site-packages\comfyui_workflow_templates_json\templates\image_qwen_image_2_1_t2i.json"
PROMPT = "雨夜废弃地铁站，一名穿深灰风衣的女侦探手持透明雨伞站在积水的站台入口，冷青色灯光，中景，低角度，湿地面反光"


async def build_qwen_graph(client: ComfyNativeClient) -> tuple[dict, list[str]]:
    """官方模板 → API 图 → 对齐权重 → 换我们的提示词。"""
    raw = json.loads(Path(TEMPLATE).read_text(encoding="utf-8"))
    info = await client.object_info()
    warns: list[str] = []
    graph = ui_to_api(flatten_subgraphs(raw, info), info, report=warns)
    graph, aligned = await client.align_graph(graph)
    for node in graph.values():
        t = node["class_type"]
        if t == "TextEncodeQwenImage21":
            node["inputs"]["prompt"] = PROMPT
            node["inputs"]["negative_prompt"] = "文字、水印、多余手指、畸形"
        elif t == "EmptyLatentImage":
            node["inputs"].update({"width": 1024, "height": 1024})
        elif t == "KSampler":
            node["inputs"].update({"steps": 20, "seed": 20260930})
        elif t == "SaveImageAdvanced":
            node["inputs"]["filename_prefix"] = "h3studio/qwen"
    return graph, warns + [f"权重对齐：{json.dumps(a, ensure_ascii=False)}" for a in aligned]


async def wait_job(c: httpx.AsyncClient, h: dict, job_id: str, budget_s: int) -> dict:
    t0 = time.time()
    last = None
    while time.time() - t0 < budget_s:
        snap = (await c.get(f"/api/jobs/{job_id}", headers=h)).json()
        if snap["state"] != last:
            last = snap["state"]
            print(f"    state={last} attempts={snap['attempts']} 已等 {time.time()-t0:.0f}s", flush=True)
        if snap["terminal"]:
            return snap
        await asyncio.sleep(4)
    raise SystemExit(f"任务 {job_id[:8]} 在 {budget_s}s 内没收口")


async def main() -> int:
    async with httpx.AsyncClient(base_url=API, timeout=120.0) as c:
        tok = (await c.post("/api/auth/login", json={"username": "admin", "password": "1234"})).json()["access"]
        h = {"Authorization": f"Bearer {tok}"}
        instances = (await c.get("/api/instances", headers=h)).json()
        local = next((i for i in instances if i["baseUrl"].rstrip("/") == COMFY), None)
        if local is None:
            local = (await c.post(
                "/api/instances",
                headers=h,
                json={"name": "本机 ComfyUI", "protocol": "comfy_native", "placement": "local", "baseUrl": COMFY,
                      "localOutputRoot": "F:/H3/comfyui/ComfyUI/output", "isDefault": True},
            )).json()
        inst = local["id"]
        print(f"实例 id={inst}")

        client = ComfyNativeClient(COMFY)
        try:
            qwen_graph, warns = await build_qwen_graph(client)
            weights = await discover_h3_weights(client)
        finally:
            await client.close()
        print("图准备完毕：")
        for w in warns:
            print("   ·", w[:180])

        # ① 图片：Qwen-Image-2.1
        r = await c.post("/api/jobs", headers=h, json={"instanceId": inst, "graph": qwen_graph, "kind": "image", "title": "冒烟 Qwen 出图", "priority": 50})
        assert r.status_code == 200, r.text
        job = r.json()
        print(f"\n① 图片任务入队 {job['id'][:8]}")
        snap = await wait_job(c, h, job["id"], 900)
        print(f"   → {snap['state']} 产物 mediaIds={snap['mediaIds']}")
        for o in snap["outputs"]:
            f = MEDIA_ROOT / (o["path"] or "")
            print(f"   · {o['path']} exists={f.exists()} size={f.stat().st_size if f.exists() else 0}")
        if snap["state"] != "succeeded":
            print("   错误:", json.dumps(snap.get("error"), ensure_ascii=False)[:400])
            return 1

        # ② 视频：MiniMax H3（2 秒 56 帧，本机实测约 7 分钟）
        length = h3_length(2.0)
        h3_graph = h3_t2v_graph(weights, prompt=PROMPT, width=864, height=480, length=length, steps=8, seed=20260930, filename_prefix="h3studio/h3", use_turbo=True)
        r = await c.post("/api/jobs", headers=h, json={"instanceId": inst, "graph": h3_graph, "kind": "video", "title": f"冒烟 H3 {length}帧", "priority": 60})
        assert r.status_code == 200, r.text
        job2 = r.json()
        print(f"\n② 视频任务入队 {job2['id'][:8]}（{length} 帧）")
        snap2 = await wait_job(c, h, job2["id"], 1500)
        print(f"   → {snap2['state']} 产物 mediaIds={snap2['mediaIds']}")
        for o in snap2["outputs"]:
            f = MEDIA_ROOT / (o["path"] or "")
            print(f"   · {o['path']} exists={f.exists()} size={f.stat().st_size/1024:.0f} KB")
        if snap2["state"] != "succeeded":
            print("   错误:", json.dumps(snap2.get("error"), ensure_ascii=False)[:400])
            return 1

        # ③ 回读：产物必须能从我们自己的接口取出来
        for s in (snap, snap2):
            mid = s["mediaIds"][0]
            got = await c.get(f"/api/media/{mid}/raw", headers=h)
            print(f"\n③ media/{mid}/raw → {got.status_code} {got.headers.get('content-type')} {len(got.content)} 字节")
        print("\n图片 + 视频两条链路都通。")
        return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
