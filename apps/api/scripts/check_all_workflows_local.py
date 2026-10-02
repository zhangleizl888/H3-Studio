"""全库可用性体检：每条工作流对着本机 ComfyUI 问一遍，可选真机跑。

跑法（后端在 8788、本机 ComfyUI 在跑）：
    cd apps/api && PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/check_all_workflows_local.py
    # 再真机跑指定的几条（会占显存，一条出片 6–8 分钟）：
    ... scripts/check_all_workflows_local.py --run 30 31

静态那一遍回答的是「这台机器的节点与权重配不配得上这张图」：
  节点缺哪个、图里写死的权重名这台有没有、绑定挑的那个文件这台有没有、是不是半截下载、
  有没有解析出任务信号（没信号就不会被自动选挑中）。
它**不等于**「跑得出东西」—— 图形状对但语义错（比如条件节点接错）只有真跑才知道，
所以 verifiedAt（队列在真出产物后写的）单独列出来，别把绿勾当成就跑通了。
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

BASE = "http://127.0.0.1:8788"


def client() -> httpx.Client:
    c = httpx.Client(base_url=BASE, trust_env=False, timeout=600)
    r = c.post("/api/auth/login", json={"username": "admin", "password": "12345"})
    r.raise_for_status()
    c.headers["Authorization"] = f"Bearer {r.json()['access']}"
    return c


def media_list(c: httpx.Client) -> list[dict]:
    rows = c.get("/api/media", params={"limit": 300, "all": "true"}).json()
    items = rows if isinstance(rows, list) else rows.get("items") or []
    return [m for m in items if not m.get("deletedAt")]


def sweep(c: httpx.Client, instance_id: str) -> list[dict]:
    rows = []
    for w in c.get("/api/workflows").json():
        r = c.post(f"/api/workflows/{w['id']}/check", json={"instanceIds": [instance_id]})
        if r.status_code != 200:
            rows.append({"id": w["id"], "name": w["name"], "mode": w.get("modeLabel") or "", "error": f"体检接口 {r.status_code}：{r.text[:120]}"})
            continue
        rep = r.json()["reports"][0]
        models = rep.get("models") or []
        rows.append({
            "id": w["id"], "name": w["name"], "mode": r.json().get("modeLabel") or "",
            "reachable": rep.get("reachable"), "ok": rep.get("ok"), "problems": rep.get("problems") or [],
            "nodes": rep.get("nodeCount"), "gaps": len(rep.get("gaps") or []),
            "slots": len(models), "missingModels": [m for m in models if m.get("missing")],
            "weightProblems": rep.get("weightProblems") or [],
            "signals": rep.get("signals") or [], "verifiedAt": rep.get("verifiedAt"),
            "autoSelect": rep.get("autoSelect"), "priority": rep.get("priority"),
            "bindings": {k: v for k, v in (w.get("bindings") or {}).items()},
        })
    return rows


def report(rows: list[dict]) -> int:
    bad = 0
    print(f"{'条目':>16} {'名称':28} {'模式':6} {'节点':>4} {'权重位':>6} {'缺':>3} {'信号':>4} {'真跑过':>6}")
    for r in rows:
        if r.get("error"):
            bad += 1
            print(f"{r['id']:>16} {r['name'][:28]:28} —— 体检失败：{r['error']}")
            continue
        miss = len(r["missingModels"])
        flag = "✓" if r["ok"] else "✗"
        # 内置模板不是库里的行，verifiedAt 这列对它没有意义 —— 报「没跑过」是假话，
        # 它每天都在被项目页的出图/出片按钮用。真跑过的证据在任务与媒体上。
        is_builtin = str(r["id"]).startswith("builtin:")
        if is_builtin:
            verified = "不适用"
        elif r["verifiedAt"]:
            try:
                verified = dt.datetime.fromisoformat(r["verifiedAt"]).astimezone().strftime("%m-%d %H:%M")
            except ValueError:
                verified = r["verifiedAt"][:10]
        else:
            verified = "没跑过"
        print(f"{r['id']:>16} {r['name'][:28]:28} {r['mode']:6} {r['nodes'] or '-':>4} {r['slots']:>6} {miss:>3} "
              f"{len(r['signals']):>4} {verified:>10} {flag}")
        for p in r["problems"]:
            print(f"        · {p[:200]}")
        for m in r["missingModels"]:
            print(f"        这台没有 {m['key']}（{m['role']}）要的是 {m.get('effective') or '空'!r}；可换成：{'、'.join((m.get('options') or [])[:4])}")
        for x in r["weightProblems"]:
            print(f"        半截权重：{x[:160]}")
        if not r["ok"]:
            bad += 1
    print(f"\n静态体检：{len(rows) - bad}/{len(rows)} 条在本机没有障碍")
    print("  「真跑过」那一列只对有库行的工作流有意义：内置模板的图是派发时现拼的，没有行可以记时间戳。")
    return bad


def run_one(c: httpx.Client, instance_id: str, wid: str, media: dict) -> bool:
    """真跑一条：素材用库里现成的（按 image/video/audio 各挑一条），产物落没落回来说话。"""
    w = c.get(f"/api/workflows/{wid}").json()
    names = {s.get("name") for s in (w.get("signals") or [])}
    slots: dict = {"prompt": "一个角色在雨夜的街边抬头看向镜头", "seconds": 3, "steps": 8, "seed": 7}
    if "width" in names:
        slots.update({"width": 480, "height": 864})
    if "aspect_ratio" in names:
        slots.update({"aspect_ratio": "9:16", "megapixels": 0.4})
    if media.get("image"):
        if "first_frame" in names:
            slots["first_frame"] = media["image"]
        if "ref_images" in names:
            slots["ref_images"] = [media["image"]]
    if media.get("video") and "ref_videos" in names:
        slots["ref_videos"] = [media["video"]]
    if media.get("audio") and "ref_audios" in names:
        slots["ref_audios"] = [media["audio"]]
    body = {"instanceId": instance_id, "workflowId": int(wid), "kind": w.get("taskKind") or "video",
            "title": f"全库可用性 · {w['name']}", "slots": slots}
    r = c.post("/api/jobs", json=body)
    if r.status_code != 200:
        print(f"  #{wid} {w['name']}：入队被拦 {r.status_code} {r.text[:220]}")
        return False
    jid = r.json()["id"]
    print(f"  #{wid} {w['name']} → 任务 {jid}，等它跑（本机出片一条 6–8 分钟）…", flush=True)
    last = ""
    while True:
        time.sleep(10)
        j = c.get(f"/api/jobs/{jid}").json()
        state = j.get("state")
        prog = (j.get("progress") or {}).get("stage") or ""
        if f"{state}/{prog}" != last:
            last = f"{state}/{prog}"
            print(f"    {state} {prog} 第 {j.get('attempts')} 次", flush=True)
        if state in ("succeeded", "failed", "canceled"):
            break
    media_ids = j.get("mediaIds") or []
    if state != "succeeded":
        err = j.get("error") or {}
        print(f"  ✗ #{wid} {state}：{err.get('message','')[:240]}")
        if err.get("hint"):
            print(f"      处置：{str(err['hint'])[:200]}")
        return False
    print(f"  ✓ #{wid} 跑通，产物 {media_ids}")
    # 媒体详情没有单条接口（/api/media/{id} 是 404，只有 /raw 与 /download），所以从列表里捞
    rows = media_list(c)
    for mid in media_ids[:3]:
        m = next((x for x in rows if str(x.get("id")) == str(mid)), {})
        print(f"      media {mid} {m.get('kind')} {m.get('width')}×{m.get('height')} {m.get('durationMs')}ms {m.get('role') or ''}")
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", nargs="*", default=[], help="真机跑的库条目 id（会占显存）")
    args = ap.parse_args()

    c = client()
    insts = c.get("/api/instances").json()
    if not insts:
        print("没有登记实例")
        return 1
    inst = next((i for i in insts if i["placement"] == "local"), insts[0])
    print(f"对照实例：{inst['id']} · {inst['name']} · {inst['baseUrl']}（上次探活 {'通过' if inst.get('lastProbeOk') else '失败/未知'}）\n")

    bad = report(sweep(c, str(inst["id"])))

    if args.run:
        print("\n真机跑：")
        items = media_list(c)
        pick = {}
        # 优先挑明确标了 ref_* 角色的上传素材；视频挑**最长的那条** ——
        # #31 全能参考 60 秒那条要把参考片切成四段（0/15/30/45s），喂一条 5 秒的短片会在
        # MiniMaxH3ReferenceToVideo 里对零帧画布求比例，报一句没头没脑的 division by zero。
        for want, kinds in (("image", ("image",)), ("video", ("video",)), ("audio", ("audio",))):
            pool = [m for m in items if m.get("kind") in kinds]
            refs = [m for m in pool if str(m.get("role") or "").startswith("ref")] or pool
            hit = max(refs, key=lambda m: int(m.get("durationMs") or 0)) if refs else None
            if hit:
                pick[want] = hit["id"]
        print(f"  素材：{pick}")
        for wid in args.run:
            if not run_one(c, str(inst["id"]), str(wid), pick):
                bad += 1

    print("\n" + ("全部可用（静态无障碍）" if bad == 0 else f"{bad} 条有障碍"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
