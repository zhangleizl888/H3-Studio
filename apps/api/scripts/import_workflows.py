r"""批量导入 E:\下载 里的工作流到工作流库（走 HTTP 接口，不碰库）。

同一份工作流常常有两份导出：`xxx.json`（画布版）与 `xxx_api.json`（API 版）。
两份一起交给导入向导：API 版当可执行图，画布版存成 ui_graph 给编辑器 —— 省掉一次
有损的 UI↔API 往返。只有画布版的（加速版图文一键生视频）就只传一份。

用法（后端要在跑）：
    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/import_workflows.py \
        --src "E:/下载" --username admin --password 12345
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

# 名称 / 优先级 / 标签 / 说明。priority 决定同类任务里谁先被自动选：
# 越「轻」越具体的越该先上，60 秒多素材那种重活儿留给人手动选。
PLAN = [
    ("加速版-MiniMax+H3｜图文一键生视频", "MiniMax H3 图文一键生视频（加速版）", 120, ["导入", "H3", "首尾帧出片"],
     "首帧 + 尾帧 + 提示词直接出一条 H3 短片，全程 EasyCache 加速。导演台逐镜头出片的主路径。"),
    ("MiniMax_H3动作迁移图生视频_自定义背景人物全能参考", "MiniMax H3 动作迁移·全能参考", 110, ["导入", "H3", "动作迁移"],
     "给一段参考视频（含它自己的音轨）+ 场景图 + 人物图，把动作节奏迁移到新画面上。"),
    ("双模双采资源面板短剧助手_MiniMax_H3_全能参考生成视频", "双模双采短剧助手·全能参考生成视频", 100, ["导入", "H3", "短剧", "多素材"],
     "素材面板混装图/视频/音频，六路参考进 H3，另带一条 Qwen 图片分支用来换背景换人物。"),
    ("MiniMax_H3全能参考60秒-图音视频多素材拼接工作流", "MiniMax H3 全能参考 60 秒·多素材拼接", 90, ["导入", "H3", "长片", "拼接"],
     "四段 15 秒参考生视频再拼成一条约 60 秒的成片。显存和耗时都很重，慎用自动选。"),
    ("声音克隆二合一｜Index_TTS2_+_Qwen3_TTS｜音色参考｜语气情感控制", "声音克隆二合一（IndexTTS2 + Qwen3-TTS）", 120, ["导入", "TTS", "音色克隆"],
     "参考音频 10 秒截段 → Whisper 转写参考文本 → Qwen3-TTS 音色克隆出目标台词。配音用。"),
    ("klein克莱因一键生成人物设定图+服装拆解", "Klein 一键人物设定图 + 服装拆解", 120, ["导入", "图片", "设定图", "服装拆解"],
     "一张人物图出三视图设定稿，并把身上的服装拆成单品图。角色定妆/服化道用。"),
    ("FLUX克莱因指令编辑", "Klein 指令编辑（FLUX）", 110, ["导入", "图片", "指令编辑"],
     "按自然语言指令改图（换背景/换服装/局部替换），带参考图。改图用。"),
]


def _api(base: str, path: str, *, token: str | None = None, data: bytes | None = None,
         headers: dict[str, str] | None = None, method: str = "GET") -> tuple[int, Any]:
    req = urllib.request.Request(base.rstrip("/") + path, data=data, method=method)
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
            return exc.code, raw


def _multipart(fields: dict[str, str], files: dict[str, tuple[str, bytes]]) -> tuple[str, bytes]:
    """手搓 multipart，不引第三方依赖。"""
    import uuid

    bound = f"----h3import{uuid.uuid4().hex}"
    out = bytearray()
    for key, val in fields.items():
        out += f"--{bound}\r\nContent-Disposition: form-data; name=\"{key}\"\r\n\r\n{val}\r\n".encode("utf-8")
    for key, (fname, payload) in files.items():
        out += (f"--{bound}\r\nContent-Disposition: form-data; name=\"{key}\"; filename=\"{fname}\"\r\n"
                f"Content-Type: application/json\r\n\r\n").encode("utf-8")
        out += payload + b"\r\n"
    out += f"--{bound}--\r\n".encode("utf-8")
    return f"multipart/form-data; boundary={bound}", bytes(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8788")
    ap.add_argument("--src", default="E:/下载")
    ap.add_argument("--username", default="admin")
    ap.add_argument("--password", default="12345")
    ap.add_argument("--instance", default=None, help="实例 id，缺省用第一台")
    ap.add_argument("--only", default=None, help="只导名字里含这个串的工作流")
    ap.add_argument("--fresh", action="store_true", help="先删掉库里所有导入过的工作流再导（重扫规则变了时用）")
    args = ap.parse_args()
    src = Path(args.src)

    code, tok = _api(args.base, "/api/auth/login", data=json.dumps(
        {"username": args.username, "password": args.password}).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    if code != 200 or not isinstance(tok, dict):
        print(f"登录失败 {code}：{tok}")
        return 2
    token = tok.get("access") or tok.get("accessToken") or (tok.get("data") or {}).get("accessToken")
    if not token:
        print(f"登录返回里没有 access token：{json.dumps(tok, ensure_ascii=False)[:300]}")
        return 2

    code, inst = _api(args.base, "/api/instances", token=token)
    instances = inst if isinstance(inst, list) else (inst or {}).get("instances") or []
    instance_id = args.instance or (str(instances[0]["id"]) if instances else None)
    if not instance_id:
        print("后端里没有可用的生成实例，导入向导需要它的 /object_info 来校验节点与权重")
        return 2
    print(f"用实例 {instance_id}（{instances[0].get('name') if instances else '?'}）导入 {len(PLAN)} 条工作流\n")

    if args.fresh:
        code, lst = _api(args.base, "/api/workflows", token=token)
        for w in [x for x in (lst or []) if not x.get("isBuiltin")]:
            c2, _ = _api(args.base, f"/api/workflows/{w['id']}", token=token, method="DELETE")
            print(f"清空旧条目 {w['id']} {w['name'][:28]} → {c2}")
        print()

    failed = 0
    for stem, name, priority, tags, desc in PLAN:
        if args.only and args.only not in stem:
            continue
        api_p = src / f"{stem}_api.json"
        ui_p = src / f"{stem}.json"
        main_p = api_p if api_p.exists() else ui_p
        if not main_p.exists():
            print(f"× {name}：找不到 {main_p}")
            failed += 1
            continue
        files = {"file": (main_p.name, main_p.read_bytes())}
        if api_p.exists() and ui_p.exists():
            files["ui_file"] = (ui_p.name, ui_p.read_bytes())
        fields = {"name": name, "description": desc, "priority": str(priority),
                  "tags": ",".join(tags), "instance_id": str(instance_id)}
        ctype, body = _multipart(fields, files)
        code, out = _api(args.base, "/api/workflows/import", token=token, data=body,
                         headers={"Content-Type": ctype}, method="POST")
        if code not in (200, 201):
            print(f"× {name}：导入失败 {code} → {json.dumps(out, ensure_ascii=False)[:400] if isinstance(out, dict) else str(out)[:400]}")
            failed += 1
            continue
        wf, rep = out["workflow"], out.get("report") or {}
        print(f"√ {name}  id={wf['id']}  种类={wf['taskKind']}  执行侧={wf['executesOn']}  "
              f"节点={wf['nodeCount']}  改写={len(wf['adaptations'])} 处  缺口={len(wf['gaps'])} 个  "
              f"信号={len(wf['signals'])} 个")
        for g in wf["gaps"][:4]:
            print(f"    缺口 #{g['node']} {g['class_type']} —— {g.get('pack') or g.get('reason')}")
        for a in wf["adaptations"][:3]:
            print(f"    改写 [{a['action']}] #{a['node']} {a['from']} → {a['to'] or '—'}")
        if len(wf["adaptations"]) > 3:
            print(f"    …另有 {len(wf['adaptations']) - 3} 处改写")
        for m in wf["pendingMedia"][:4]:
            print(f"    待指素材：{m}")
        print()
    print("导入完成" if not failed else f"有 {failed} 条没导进去")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
