r"""按任务自动选工作流的验收脚本（打真接口，不连库）。

    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/check_workflow_autoselect.py
        --base http://127.0.0.1:8788 --username admin --password 1234

三步：① 工作流库现在长什么样；② 三种任务分别会挑中谁（含没挑中时的回落说明）；
③ 挑完之后的参数表（尺寸换算、耗时外推、被显存闸门拦住的那类）。
加 --run 才真入队跑一条（烧显存，默认不跑）。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

BASE = "http://127.0.0.1:8788"


def call(base: str, path: str, *, token: str | None = None, body: Any = None, method: str = "GET") -> tuple[int, Any]:
    url = base.rstrip("/") + path
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=240) as r:
            raw = r.read().decode("utf-8", errors="replace")
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, raw[:400]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=BASE)
    ap.add_argument("--username", default="admin")
    ap.add_argument("--password", default="1234")
    ap.add_argument("--run", action="store_true", help="真入队跑一条（会占显存几分钟）")
    ap.add_argument("--first-frame", type=int, default=75, help="用来填首帧的 media id")
    ap.add_argument("--last-frame", type=int, default=74)
    ap.add_argument("--ref-video", type=int, default=82)
    args = ap.parse_args()

    code, tok = call(args.base, "/api/auth/login", body={"username": args.username, "password": args.password},
                     method="POST")
    if code != 200:
        print(f"登录失败 {code}: {tok}")
        return 2
    token = tok["access"]

    code, wfs = call(args.base, "/api/workflows", token=token)
    lib = [w for w in (wfs or []) if not w.get("isBuiltin")]
    print(f"工作流库（{len(lib)} 条导入的）：")
    for w in lib:
        sig = ",".join(sorted({s["name"] for s in w.get("signals") or []}))
        print(f"  #{w['id']:<3} {w['name']:<34} {w['taskKind']:<6} {w['executesOn']:<12} "
              f"节点{w['nodeCount']:<4} 改写{len(w.get('adaptations') or []):<3} 缺口{len(w.get('gaps') or [])} "
              f"prio={w['priority']} verified={'是' if w.get('verifiedAt') else '否'}")
        print(f"       信号：{sig[:150]}")
    print()

    tasks = [
        ("首尾帧出片 2 秒 864×480", {"prompt": "一只橘猫在窗台上打哈欠，镜头缓慢推近",
                                 "first_frame": args.first_frame, "last_frame": args.last_frame,
                                 "seconds": 2, "width": 864, "height": 480, "steps": 8}),
        ("只给一张参考图 + 时长", {"prompt": "雨夜霓虹街角，人物回头", "ref_images": [args.first_frame], "seconds": 5}),
        ("给一段参考视频做动作迁移", {"prompt": "把这段舞蹈换到新场景与新人物",
                                   "ref_videos": [args.ref_video], "ref_images": [args.first_frame], "seconds": 5}),
        ("配音（音频任务）", {"prompt": "鱼和熊掌不可兼得！但是解渴呀！", "ref_audios": [args.ref_video]}),
    ]
    for label, slots in tasks:
        kind = "audio" if "配音" in label else "video"
        path = f"/api/workflows/select?kind={kind}&slots={urllib.parse.quote(json.dumps(slots, ensure_ascii=False))}"
        code, res = call(args.base, path, token=token)
        print(f"任务「{label}」→ {code}")
        if code != 200 or not isinstance(res, dict):
            print("   接口异常：", str(res)[:200])
            continue
        cand = res.get("candidates") or []
        if not cand:
            print(f"   库里没有能干的（provided={res.get('provided')}）→ 回落内置模板 {res.get('fallbackTemplate')}")
            continue
        for c in cand:
            print(f"   {c['score']:>4} 分  {c['name']:<34} {' / '.join(c.get('reasons') or [])[:110]}")
        print()

    plan_body = {"jobs": [{"kind": "video", "template": "auto", "title": "自动选验收",
                           "slots": tasks[0][1]}]}
    code, plan = call(args.base, "/api/jobs/plan", token=token, body=plan_body, method="POST")
    print(f"参数表 /jobs/plan → {code}")
    for row in (plan or {}).get("rows") or []:
        d = row.get("derived") or {}
        print(f"  条目 {row['index']}：template={row.get('template')} 命中={d.get('workflowName')} "
              f"（{d.get('chosenBy')}） 尺寸={d.get('width')}×{d.get('height')} mp={d.get('mp')} "
              f"帧={d.get('frames')} 步={d.get('steps')} 预估={d.get('etaSeconds')}s")
        print(f"     依据：{str(d.get('etaBasis'))[:150]}")
        for p in row.get("problems") or []:
            print(f"     问题：{p}")
        print(f"     blocked={row.get('blocked')}")
    print(f"总额：{json.dumps((plan or {}).get('totals'), ensure_ascii=False)}")

    if args.run:
        code, out = call(args.base, "/api/jobs", token=token, body=plan_body["jobs"][0], method="POST")
        print(f"\n入队 /jobs → {code}: {json.dumps(out, ensure_ascii=False)[:300]}")
        if code not in (200, 201) or not isinstance(out, dict):
            return 1
        job_id = out.get("id")
        for _ in range(90):
            time.sleep(20)
            code, job = call(args.base, f"/api/jobs/{job_id}", token=token)
            st = (job or {}).get("state")
            prog = (job or {}).get("progress") or {}
            print(f"  [{int(time.time()) % 100000}] state={st} pos={job.get('queuePos')} "
                  f"att={job.get('attempts')} {str(prog.get('message') or prog.get('stage') or '')[:90]}")
            if st in ("succeeded", "failed", "canceled"):
                print("  产物:", job.get("mediaIds") or job.get("output"))
                if st != "succeeded":
                    print("  错误:", json.dumps(job.get("error"), ensure_ascii=False)[:400])
                break
    return 0


if __name__ == "__main__":
    sys.exit(main())
