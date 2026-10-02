r"""体检一条本地工作流文件：走后端真接口 /api/workflows/validate，看改写、缺口、信号。

    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/inspect_workflow_file.py "E:/下载/xxx_api.json"
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8788"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--base", default=BASE)
    ap.add_argument("--instance", default=None)
    ap.add_argument("--login", default="admin:12345")
    args = ap.parse_args()

    user, _, pwd = args.login.partition(":")
    req = urllib.request.Request(f"{args.base}/api/auth/login", data=json.dumps(
        {"username": user, "password": pwd}).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        token = json.load(r)["access"]

    raw = open(args.path, encoding="utf-8").read()
    body = {"graph": raw}
    if args.instance:
        body["instance_id"] = args.instance
    req = urllib.request.Request(f"{args.base}/api/workflows/validate", data=json.dumps(body).encode("utf-8"),
                                 method="POST", headers={"Content-Type": "application/json",
                                                         "Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=240) as r:
            rep = json.load(r)
    except urllib.error.HTTPError as exc:
        print(f"validate 返回 {exc.code}：{exc.read().decode('utf-8', errors='replace')[:900]}")
        return 1

    print(f"来源格式={rep.get('sourceFormat')} 种类={rep.get('taskKind')} 执行侧={rep.get('executesOn')} "
          f"valid={rep.get('valid')} 改写={len(rep.get('adaptations') or [])} 缺口={len(rep.get('gaps') or [])}")
    res = rep.get("resolution")
    print("分辨率提示:", json.dumps(res, ensure_ascii=False) if res else "（无）")
    print("出口:", [o["class_type"] for o in rep.get("outputs") or []])
    print("\n权重对齐：")
    for c in rep.get("alignment") or []:
        print(f"   #{c['node']} {c['field']}: {c['from']} → {c['to']}")
    print("\n改写清单：")
    for a in rep.get("adaptations") or []:
        print(f"   [{a['action']}] #{a['node']} {a['from']} → {a['to'] or '—'}  {a['detail'][:150]}")
    print("\n缺口：")
    for g in rep.get("gaps") or []:
        print(f"   #{g['node']} {g['class_type']}: {g['reason'][:120]}" + (f"  【{g['pack']}】" if g.get("pack") else ""))
    print("\n待重指素材:", rep.get("pendingMedia") or [])
    print("\n任务信号：")
    for s in rep.get("signals") or []:
        print(f"   {s['name']:<16} {s['type']:<6} 必填={int(s['required'])} 多位={int(s['many'])} "
              f"{s['addresses'][:3]} 当前值={str(s['value'])[:34]!r}")
    for w in rep.get("warnings") or []:
        print("   !", str(w)[:170])
    return 0


if __name__ == "__main__":
    sys.exit(main())
