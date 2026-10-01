r"""真跑一条任务（走 /api/jobs 的自动选路径），轮询到终态并打印用了哪条工作流。

    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/run_job.py \
        --kind image --slots '{"prompt":"人物设定图","ref_images":[75],"width":832,"height":1216}'

冷启动第一枪常被显存闸挡回 queued（ComfyUI 要先装卸权重），那是正常的：脚本会一直轮到终态。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8788"


def call(base: str, path: str, *, token: str | None = None, body=None, method: str = "GET"):
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urllib.request.Request(base.rstrip("/") + path, data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            raw = r.read().decode("utf-8", errors="replace")
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, raw[:500]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=BASE)
    ap.add_argument("--username", default="admin")
    ap.add_argument("--password", default="1234")
    ap.add_argument("--kind", default="video", choices=["image", "video", "audio", "workflow_test"])
    ap.add_argument("--slots", default="{}", help="信号写法的槽位 JSON")
    ap.add_argument("--workflow", default=None, help="指定工作流库 id（不给就是自动选）")
    ap.add_argument("--instance", default=None)
    ap.add_argument("--title", default="验收跑")
    ap.add_argument("--wait", type=int, default=3600, help="最多等多少秒")
    args = ap.parse_args()

    _, tok = call(args.base, "/api/auth/login", body={"username": args.username, "password": args.password}, method="POST")
    token = (tok or {}).get("access")
    if not token:
        print("登录失败:", tok)
        return 2
    slots = json.loads(args.slots)
    body = {"kind": args.kind, "template": "auto", "slots": slots, "title": args.title}
    if args.workflow:
        body["workflowId"] = int(args.workflow)
    if args.instance:
        body["instanceId"] = args.instance

    _, plan = call(args.base, "/api/jobs/plan", token=token, body={"jobs": [body]}, method="POST")
    row = (plan or {}).get("rows", [{}])[0]
    d = row.get("derived") or {}
    print(f"参数表：命中={d.get('workflowName') or row.get('template')}（{d.get('chosenBy')}） "
          f"尺寸={d.get('width')}×{d.get('height')} 帧={d.get('frames')} 步={d.get('steps')} 预估={d.get('etaSeconds')}s")
    for p in row.get("problems") or []:
        print("   问题:", p)
    if row.get("blocked"):
        print("被参数表拦下，没有入队")
        return 1

    code, out = call(args.base, "/api/jobs", token=token, body=body, method="POST")
    if code not in (200, 201):
        print(f"入队失败 {code}: {json.dumps(out, ensure_ascii=False)[:500]}")
        return 1
    jid = out["id"]
    print(f"入队 → {jid}  命中={out.get('workflowName')} 选法={out.get('chosenBy')}")

    t0 = time.time()
    last = ""
    while time.time() - t0 < args.wait:
        time.sleep(15)
        _, j = call(args.base, f"/api/jobs/{jid}", token=token)
        if not isinstance(j, dict):
            print("  读任务失败:", str(j)[:200])
            continue
        prog = j.get("progress") or {}
        line = (f"  [{int(time.time() - t0):>4}s] {j.get('state')} att={j.get('attempts')} "
                f"{str(prog.get('message') or prog.get('stage') or '')[:70]}")
        if line != last:
            print(line, flush=True)
            last = line
        if j.get("state") in ("succeeded", "failed", "canceled"):
            print(f"终态：{j.get('state')} 产物 media={j.get('mediaIds') or j.get('output')} "
                  f"用时 {int(time.time() - t0)}s")
            if j.get("error"):
                print("  错误:", json.dumps(j["error"], ensure_ascii=False)[:600])
            fill = ((j.get("params") or {}).get("fill") or {})
            notes = j.get("fillNotes") or fill.get("fillNotes")
            if notes:
                print("  填图:", " / ".join(notes)[:400])
            return 0 if j.get("state") == "succeeded" else 1
    print(f"等了 {args.wait}s 还没到终态（任务 {jid} 还在跑，可用 /api/jobs/{jid} 继续看）")
    return 3


if __name__ == "__main__":
    sys.exit(main())
