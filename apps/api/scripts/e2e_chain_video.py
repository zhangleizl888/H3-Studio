"""端到端验收「长片无缝续拍」：走 app 自己的登录与入队接口，跑一条多段承接链。

这一段验的是后端模板 h3_chain（ComfyUI 里的 H3SeamlessChainSampler 节点）：
建图 → 提交 → 轮询到终态 → 确认产物进了 media 表。

    PYTHONIOENCODING=utf-8 apps/api/.venv/Scripts/python.exe apps/api/scripts/e2e_chain_video.py <存档目录> <段数>

同一个「存档目录」重复跑、段数递增时，前面那些段应从 latent 存档秒级回放，只采样新增的那一段：
本机实测 864×480、每段 2 秒、turbo 8 步，两段全新生成 475s，第三段续跑 135s。
"""
import sys
import time

import httpx

BASE = "http://127.0.0.1:8788/api"
ARCHIVE = sys.argv[1] if len(sys.argv) > 1 else f"e2e-chain-{int(time.time())}"
NSEG = max(1, min(4, int(sys.argv[2])) if len(sys.argv) > 2 else 2)

# 每段都写成官方三字段：插件见到 integrated_multimodal_description: 就原样直通，不再二次包装
SEGMENTS = [
    ("integrated_multimodal_description: [Shot 1] 少女站在湿漉漉的屋檐下抬头看雨，镜头以小幅度缓慢推近。\n"
     "overall_soundscape: 细雨落在石板上的声音，远处一声檐铃"),
    ("integrated_multimodal_description: [Shot 1] 她伸出手，一滴雨落在掌心，指尖亮起一点微光，镜头继续推近。\n"
     "overall_soundscape: 雨声贴近，一声轻微呼吸"),
    ("integrated_multimodal_description: [Shot 1] 微光顺着手腕爬上肩头，她抬头望向巷子尽头，镜头随之上摇。\n"
     "overall_soundscape: 雨声渐弱，一声悠远的笛"),
    ("integrated_multimodal_description: [Shot 1] 巷子尽头的灯笼亮起，她迈步向前，雨丝在灯光里发亮，镜头跟随。\n"
     "overall_soundscape: 脚步踏在湿石板上，雨声与远处笛声交织"),
]


def login(c: httpx.Client) -> dict:
    r = c.post("/auth/login", json={"username": "admin", "password": "12345"})
    r.raise_for_status()
    return {"Authorization": f"Bearer {r.json()['access']}"}


def main() -> int:
    c = httpx.Client(base_url=BASE, timeout=40)
    h = login(c)
    rows = c.get("/instances", headers=h).json()
    rows = rows if isinstance(rows, list) else rows.get("items", [])
    live = [i for i in rows if i.get("lastProbeOk")]
    if not live:
        print("没有可用实例，先去 设置 → 生成实例 把 ComfyUI 起来")
        return 1
    print(f"用实例: {live[0]['id']} {live[0].get('name')}")

    body = {
        "template": "h3_chain",
        "kind": "video_chain",
        "instanceId": str(live[0]["id"]),
        "projectId": "e2e-chain",
        "title": f"E2E · 长片续拍 {NSEG} 段（{ARCHIVE}）",
        "meta": {"role": "video", "refId": f"e2e-{NSEG}"},
        "slots": {
            "segments": [{"prompt": p, "seconds": 2.0} for p in SEGMENTS[:NSEG]],
            "archive_dir": ARCHIVE,
            "seconds": 2.0,
            "guide_frames": "22",
            "width": 864,
            "height": 480,
            "turbo": True,
            "steps": 8,
            "seed": 7,
            "filename_prefix": f"h3/链{NSEG}段",
        },
    }
    r = c.post("/jobs", json=body, headers=h)
    if r.status_code >= 400:
        print("入队失败:", r.status_code, r.text[:400])
        return 1
    jid = r.json()["id"]
    print(f"任务 {jid} 已入队，{NSEG} 段")

    started, last = time.time(), ""
    while time.time() - started < 3600:
        try:
            g = c.get(f"/jobs/{jid}", headers=h)
            if g.status_code == 401:
                h = login(c)  # access token TTL 只有 900s，长任务中途必过期
                continue
            g.raise_for_status()
        except httpx.HTTPError as e:
            print("轮询出错，重试:", type(e).__name__)
            time.sleep(10)
            continue
        j = g.json()
        line = f"{j.get('state')} 尝试={j.get('attempts')} 产物={j.get('mediaIds')}"
        if j.get("error"):
            line += f" 错误={str(j['error'].get('message'))[:200]}"
        if line != last:
            print(f"[{int(time.time() - started):>5}s] {line}", flush=True)
            last = line
        if j.get("state") in ("succeeded", "failed", "cancelled"):
            if j["state"] != "succeeded" or not j.get("mediaIds"):
                return 2
            # 媒体详情没有 GET /api/media/{id}（只有 /raw 与 /download），要按列表找
            rows = c.get("/media?limit=20", headers=h).json()
            rows = rows if isinstance(rows, list) else rows.get("items", [])
            mine = next((m for m in rows if str(m.get("id")) == str(j["mediaIds"][0])), {})
            print(f"成片 media={mine.get('id')} kind={mine.get('kind')} path={mine.get('path')} bytes={mine.get('bytes')}")
            print(f"链存档: ComfyUI/output/h3_projects/{ARCHIVE}/（seg_NNN.mp4 是每段，seg_NNN.pt 是续拍用的 latent）")
            return 0
        time.sleep(15)
    print("一小时未收口，去 ComfyUI 队列看")
    return 3


sys.exit(main())
