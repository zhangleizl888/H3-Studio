r"""把 work/ 里这份工作流包导入到任意一套 H3 Studio 后端（stdlib only，不装第三方包）。

读同目录的 manifest.json，逐条走导入向导 `POST /api/workflows/import`：
API 版当可执行图，画布版一起传成 ui_file —— 省掉一次有损的 UI↔API 往返。
导入时会拿指定实例的 /object_info 重扫槽位、对齐权重名、记录缺口，所以**必须先有一台
在线的生成实例**（本机 ComfyUI 或 RunningHub）。

用法（后端已在跑，默认 http://127.0.0.1:8788）：
    python work/import_workflows.py --username admin --password 12345
    python work/import_workflows.py --token h3_xxx --instance 597 --only h3-
    python work/import_workflows.py --dry-run          # 只看会导哪些
    python work/import_workflows.py --fresh            # 先删库里所有非内置条目再导

同名默认跳过（--replace 改成先删后加）。内置三条（Qwen-Image 出图 / H3 出片 / H3 长片续拍）
不在包里，它们由后端代码生成，见 apps/api/app/gen/builtin_graphs.py。
"""

from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent


def _api(base: str, path: str, *, token: str | None = None, data: bytes | None = None,
         headers: dict[str, str] | None = None, method: str = "GET") -> tuple[int, Any]:
    req = urllib.request.Request(base.rstrip("/") + path, data=data, method=method)
    req.add_header("Accept", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            body = r.read().decode("utf-8", errors="replace")
            return r.status, (json.loads(body) if body else None)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, raw[:400]


def _multipart(fields: dict[str, str], files: dict[str, tuple[str, bytes]]) -> tuple[str, bytes]:
    bound = f"----h3work{uuid.uuid4().hex}"
    out = bytearray()
    for key, val in fields.items():
        out += f"--{bound}\r\nContent-Disposition: form-data; name=\"{key}\"\r\n\r\n{val}\r\n".encode("utf-8")
    for key, (fname, payload) in files.items():
        out += (f"--{bound}\r\nContent-Disposition: form-data; name=\"{key}\"; filename=\"{fname}\"\r\n"
                f"Content-Type: application/json\r\n\r\n").encode("utf-8")
        out += payload + b"\r\n"
    out += f"--{bound}--\r\n".encode("utf-8")
    return f"multipart/form-data; boundary={bound}", bytes(out)


def _login(base: str, username: str, password: str) -> str | None:
    code, out = _api(base, "/api/auth/login", data=json.dumps(
        {"username": username, "password": password}).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    if code != 200 or not isinstance(out, dict):
        print(f"× 登录失败 {code}：{json.dumps(out, ensure_ascii=False)[:200] if isinstance(out, dict) else out}")
        return None
    return out.get("access")


def main() -> int:
    ap = argparse.ArgumentParser(description="导入 work/ 工作流包")
    ap.add_argument("--base", default=os.environ.get("H3_SERVER", "http://127.0.0.1:8788"))
    ap.add_argument("--token", default=os.environ.get("H3_TOKEN"), help="agent token（优先于账号口令）")
    ap.add_argument("--username", default="admin")
    ap.add_argument("--password", default="12345")
    ap.add_argument("--instance", default=None, help="用哪台生成实例校验节点/权重，缺省取第一台")
    ap.add_argument("--only", default=None, help="只导 slug 含这个串的条目")
    ap.add_argument("--replace", action="store_true", help="同名先删后加（改过包里的手写元数据时用）")
    ap.add_argument("--fresh", action="store_true", help="先删库里全部非内置条目再导")
    ap.add_argument("--dry-run", action="store_true", help="只打印计划，不入库")
    args = ap.parse_args()

    manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    entries = manifest["workflows"]
    if args.only:
        entries = [e for e in entries if args.only in e["slug"]]

    token = args.token or _login(args.base, args.username, args.password)
    if not token:
        return 2

    code, instances = _api(args.base, "/api/instances", token=token)
    if code != 200 or not instances:
        print(f"× 读实例列表失败 {code}：{instances}")
        print("  导入向导要拿实例的 /object_info 校验节点与权重，先起一台 ComfyUI 再在「设置 → Server 与工作流」里加进来。")
        return 2
    instance_id = args.instance or str(instances[0]["id"])
    inst = next((i for i in instances if str(i["id"]) == str(instance_id)), instances[0])
    print(f"目标后端 {args.base}｜实例 {inst['id']}（{inst['name']} · {inst['placement']} · {inst['baseUrl']}）")
    print(f"包内 {len(entries)} 条（{manifest['package']}）\n")

    if args.dry_run:
        for e in entries:
            files = " + ".join(f"{k}:{ROOT / v}" for k, v in e["files"].items())
            print(f"[dry] {e['slug']:<24} {e['name']}｜priority={e['priority']}｜{files}")
        return 0

    if args.fresh:
        code, lib = _api(args.base, "/api/workflows", token=token)
        for w in [x for x in (lib or []) if not x.get("isBuiltin")]:
            _api(args.base, f"/api/workflows/{w['id']}", token=token, method="DELETE")
            print(f"已删旧条目 {w['id']} {w['name'][:30]}")
        print()

    code, lib = _api(args.base, "/api/workflows", token=token)
    existing = {w["name"]: w["id"] for w in (lib or []) if not w.get("isBuiltin")}

    failed = skipped = 0
    for e in entries:
        api_path = ROOT / e["files"]["api"]
        if not api_path.exists():
            print(f"× {e['name']}：缺文件 {api_path}")
            failed += 1
            continue
        if e["name"] in existing:
            if args.replace:
                _api(args.base, f"/api/workflows/{existing[e['name']]}", token=token, method="DELETE")
            else:
                print(f"- {e['name']}：库里已有，跳过（--replace 可覆盖）")
                skipped += 1
                continue
        files = {"file": (api_path.name, api_path.read_bytes())}
        ui = e["files"].get("ui")
        if ui and (ROOT / ui).exists():
            files["ui_file"] = (Path(ui).name, (ROOT / ui).read_bytes())
        fields = {"name": e["name"], "description": e.get("description") or "",
                  "priority": str(e.get("priority") if e.get("priority") is not None else 100),
                  "tags": ",".join(e.get("tags") or ["导入"]), "instance_id": str(instance_id)}
        ctype, body = _multipart(fields, files)
        code, out = _api(args.base, "/api/workflows/import", token=token, data=body,
                         headers={"Content-Type": ctype}, method="POST")
        if code not in (200, 201):
            print(f"× {e['name']}：导入失败 {code} → {json.dumps(out, ensure_ascii=False)[:300] if isinstance(out, dict) else out}")
            failed += 1
            continue
        wf, rep = out["workflow"], out.get("report") or {}
        gaps = wf.get("gaps") or []
        tail = f"缺口={len(gaps)}" + (f"（{json.dumps(gaps, ensure_ascii=False)[:160]}）" if gaps else "")
        print(f"√ {e['name']}  id={wf['id']}  种类={wf['taskKind']}  执行侧={wf['executesOn']}  "
              f"节点={wf['nodeCount']}  槽位={wf['slotCount']}  改写={len(wf.get('adaptations') or [])} 处  {tail}")
        for w in (rep.get("warnings") or [])[:3]:
            print(f"    ! {w}")
    print(f"\n完成：入库 {len(entries) - failed - skipped} 条，跳过 {skipped} 条，失败 {failed} 条")
    print("下一步：界面「工作流库」里逐条试运行（会真占显存），跑通后 verifiedAt 才有值。")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
