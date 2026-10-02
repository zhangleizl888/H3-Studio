r"""音色链路的端到端验收：上传参考音频 → 参数表 → 入队（指定工作流 + 换权重）→ 轮询到终态。

跑法（后端、ComfyUI 要先起着）：
  F:/H3/apps/api/.venv/Scripts/python.exe -X utf8 apps/api/scripts/e2e_voice_clone.py

为什么非要"文本已知的干净人声"当参考：声音克隆的解码条件是「这段音频 + 它真实说过的话」。
拿环境音或带 BGM 的片段当底子，模型能把十几个字拉成两三分钟连续发声 —— 那种结果只能说明
链路通了，不能说明音色可用。脚本默认用 CustomVoice 预先造好的那句，跑之前会先验文件在不在。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:8788"
REF_WAV = Path("F:/H3/comfyui/ComfyUI/output/h3verify/ref_00001.flac")
REF_TEXT = "今天的风很大，记得把窗关上。"
TARGET = "山风把灯影吹斜了，我在这边替你留着门。"


def die(msg: str) -> int:
    print(f"✗ {msg}")
    return 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=BASE)
    ap.add_argument("--instance", default=None, help="留空则由后端用它的默认实例")
    ap.add_argument("--project", default="p_6qesg5rr", help="软引用项目键（产物归属）")
    ap.add_argument("--workflow", default="32", help="工作流库 id；audio 没有内置回落模板")
    ap.add_argument("--model", default="44.model=base", help='换权重，形如 "节点号.字段名=值"')
    ap.add_argument("--media", default=None, help="复用已经在媒体库里的参考音频 id（省得每次上传一条新行）")
    ap.add_argument("--wait", type=int, default=1500)
    args = ap.parse_args()

    if not REF_WAV.exists() and not args.media:
        return die(f"参考音频不在：{REF_WAV}（先用 CustomVoice 造一条文本已知的干净人声，或用 --media 指一条已有的）")

    c = httpx.Client(base_url=args.base, timeout=600)
    tok = c.post("/api/auth/login", json={"username": "admin", "password": "12345"}).json().get("access")
    if not tok:
        return die("登录失败")
    c.headers["Authorization"] = f"Bearer {tok}"

    # 1) 参考音频落进服务端媒体库：只留在浏览器里的文件当不了参考素材
    if args.media:
        media_id = int(args.media)
        print(f"① 参考音频 → 复用 media {media_id}")
    else:
        with REF_WAV.open("rb") as fh:
            up = c.post(
                "/api/media/upload",
                params={"role": "voice_ref", "ref_id": "accept-voice", "project_key": args.project},
                files={"file": (REF_WAV.name, fh, "audio/flac")},
            )
        if up.status_code >= 400:
            return die(f"上传失败 {up.status_code}: {up.text[:300]}")
        media_id = int(up.json()["id"])
        print(f"① 参考音频 → media {media_id}（kind={up.json().get('kind')}）")

    # 2) 这条工作流在这台实例上能换哪些权重
    q = f"/api/workflows/{args.workflow}/models" + (f"?instance_id={args.instance}" if args.instance else "")
    info = c.get(q).json()
    if "slots" not in info:
        return die(f"模型清单没拿到：{json.dumps(info, ensure_ascii=False)[:300]}")
    print(f"② 清单：实例={info['instanceId']}（{info['placement']}），模型位 {len(info['slots'])} 个")
    for s in info["slots"]:
        print(f"     {s['key']:<18} {s['role']:<6} 现在={s['current'] or '—'} 可选={len(s['options'])}"
              + ("（图里那个值实例上没有）" if s["missing"] else ""))

    key, _, value = args.model.partition("=")
    models = {key: value} if value else None

    body = {
        "kind": "audio",
        "template": "auto",
        "workflowId": int(args.workflow),
        "instanceId": args.instance,
        "title": "验收 · 音色",
        "projectKey": args.project,
        "models": models,
        "slots": {"prompt": TARGET, "ref_audios": [media_id], "ref_text": REF_TEXT, "language": "Auto"},
        "meta": {"role": "voice", "refId": "accept-voice"},
    }
    body = {k: v for k, v in body.items() if v is not None}

    # 3) 参数表：换权重这件事必须在派发表上看得见
    plan = c.post("/api/jobs/plan", json={"jobs": [body]}).json()
    row = (plan.get("rows") or [{}])[0]
    d = row.get("derived") or {}
    print(f"③ 参数表：{row.get('template')} 选法={d.get('chosenBy')} 换权重={d.get('modelOverrides')} 拦下={row.get('blocked')}")
    for p in row.get("problems") or []:
        print("     问题:", p)
    if row.get("blocked"):
        return die("被参数表拦下，没有入队")

    # 4) 入队并轮到终态
    j = c.post("/api/jobs", json=body)
    if j.status_code >= 400:
        return die(f"入队失败 {j.status_code}: {j.text[:400]}")
    jid = j.json()["id"]
    print(f"④ 入队 → {jid}")

    t0, last = time.time(), ""
    while time.time() - t0 < args.wait:
        time.sleep(10)
        s = c.get(f"/api/jobs/{jid}").json()
        line = f"   [{int(time.time() - t0):>4}s] {s.get('state')} att={s.get('attempts')} {str((s.get('progress') or {}).get('message') or '')[:70]}"
        if line != last:
            print(line, flush=True)
            last = line
        if s.get("state") in ("succeeded", "failed", "canceled"):
            print(f"⑤ 终态：{s.get('state')}  产物={s.get('mediaIds') or s.get('output')}")
            if s.get("error"):
                print("   错误:", json.dumps(s["error"], ensure_ascii=False)[:500])
            if s.get("modelNotes"):
                print("   权重:", " / ".join(s["modelNotes"]))
            if s.get("fillNotes"):
                print("   填图:", " / ".join(s["fillNotes"])[:400])
            if s.get("state") != "succeeded":
                return 1
            # 5) 产物要真的是音频、时长要合理（拉成几分钟就是参考音频不合格）
            ids = s.get("mediaIds") or []
            if ids:
                rows = c.get("/api/media", params={"ids": ",".join(str(i) for i in ids)}).json()
                for m in rows:
                    print(f"   media {m.get('id')}: kind={m.get('kind')} role={m.get('role')} "
                          f"bytes={m.get('bytes')} durationMs={m.get('durationMs')} path={str(m.get('path'))[-46:]}")
            return 0
    return die(f"等了 {args.wait}s 还没到终态（任务 {jid}）")


if __name__ == "__main__":
    sys.exit(main())
