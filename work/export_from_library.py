r"""把工作流库里的导入型条目导出成 work/ 包（图 JSON + manifest.json）。

和 import_workflows.py 配成一对：库里改了元数据或重扫了图，就重新导出一遍，包才是最新的。
只用标准库 + HTTP，需要后端在跑。

用法：
    python work/export_from_library.py                       # 导全部非内置条目
    python work/export_from_library.py --only h3- --base http://127.0.0.1:8788
    python work/export_from_library.py --comfyui D:/ComfyUI  # 用这份源码分辨「核心节点 vs 自定义节点包」

--comfyui 给的是某个 ComfyUI 源码目录（本仓库不携带它）。给了它才能填 manifest 的 customNodes
一栏（在 comfy_extras / nodes*.py / comfy_api 里找不到的类名就算第三方节点）；不给就留空，
只把全部 class_type 记在 nodeClasses 里，导入侧不受影响。

slug 是文件名用的短横线名，第一次导出会按名称自动生成；之后请加 --keep-slugs，
或者手工改本表 —— 换 slug 等于换文件名，历史 diff 会断。
"""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
import urllib.request
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
ROOT = REPO / "work"

# 手工兜底：名字直译不出好 slug 的，在这里定死
SLUG_OVERRIDES = {
    "MiniMax H3 图文一键生视频（加速版）": "h3-firstlast-fast",
    "MiniMax H3 动作迁移·全能参考": "h3-motion-ref-omni",
    "双模双采短剧助手·全能参考生成视频": "h3-drama-omni-ref",
    "MiniMax H3 全能参考 60 秒·多素材拼接": "h3-omni-ref-60s-stitch",
    "声音克隆二合一（IndexTTS2 + Qwen3-TTS）": "voice-clone-qwen3-tts",
    "Klein 一键人物设定图 + 服装拆解": "klein-character-sheet",
    "Klein 指令编辑（FLUX）": "klein-instruct-edit",
}


def _api(base: str, path: str, *, data: bytes | None = None, token: str | None = None,
         method: str = "GET") -> Any:
    req = urllib.request.Request(base.rstrip("/") + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=300) as r:
        body = r.read().decode("utf-8", errors="replace")
    return json.loads(body) if body else None


def _login(base: str, username: str, password: str) -> str:
    return _api(base, "/api/auth/login", data=json.dumps(
        {"username": username, "password": password}).encode("utf-8"), method="POST")["access"]


def _slug(name: str) -> str:
    if name in SLUG_OVERRIDES:
        return SLUG_OVERRIDES[name]
    ascii_part = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    if len(ascii_part) >= 6:
        return ascii_part[:60]
    # 纯中文名：拿 NFKD 分解后剩下的可读片段拼一个，实在没有就用库里的 id
    decomposed = unicodedata.normalize("NFKD", name)
    latin = re.sub(r"[^a-z0-9]+", "-", decomposed.lower()).strip("-")
    return latin[:60] or f"wf-{abs(hash(name)) % 100000}"


class _CoreIndex:
    """可选：拿一份 ComfyUI 源码来分辨核心节点与第三方节点。"""

    def __init__(self, comfyui_root: Path | None):
        self.blob = ""
        if not comfyui_root or not comfyui_root.exists():
            return
        chunks: list[str] = []
        for sub in ("comfy_extras", "comfy", "comfy_api", "comfy_config"):
            d = comfyui_root / sub
            if d.exists():
                chunks += [p.read_text(encoding="utf-8", errors="replace") for p in d.rglob("*.py")]
        for p in comfyui_root.glob("nodes*.py"):
            chunks.append(p.read_text(encoding="utf-8", errors="replace"))
        self.blob = "\n".join(chunks)

    def available(self) -> bool:
        return bool(self.blob)

    def is_core(self, class_type: str) -> bool:
        return class_type in self.blob


def main() -> int:
    ap = argparse.ArgumentParser(description="导出 work/ 工作流包")
    ap.add_argument("--base", default="http://127.0.0.1:8788")
    ap.add_argument("--token", default=None, help="agent token（优先于账号口令）")
    ap.add_argument("--username", default="admin")
    ap.add_argument("--password", default="12345")
    ap.add_argument("--instance", default=None, help="用哪台实例解析权重槽位，缺省取第一台在线的")
    ap.add_argument("--only", default=None, help="只导 id 或 slug 含这个串的条目")
    ap.add_argument("--keep-slugs", action="store_true", help="沿用现有 manifest.json 里的 slug 映射")
    ap.add_argument("--comfyui", default=None, help="某份 ComfyUI 源码目录，用来分辨第三方节点")
    args = ap.parse_args()

    token = args.token or _login(args.base, args.username, args.password)
    instances = _api(args.base, "/api/instances", token=token)
    if not instances:
        print("后端里没有实例；导出的 manifest 需要一台在线实例来解析权重槽位。")
        return 2
    live = [i for i in instances if i.get("lastProbeOk")]
    inst = next((i for i in live or instances if str(i["id"]) == str(args.instance)), (live or instances)[0])

    old_slugs: dict[str, str] = {}
    carry: dict[str, Any] = {}
    if (ROOT / "manifest.json").exists():
        try:
            prev = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
            old_slugs = {e["name"]: e["slug"] for e in prev.get("workflows") or []}
            # 这两节是手工维护的（内置工作流不在库里，前置条件也不在），重新导出别把它们冲掉
            carry = {k: prev[k] for k in ("builtinWorkflows", "prerequisites") if k in prev}
        except Exception:
            pass

    core = _CoreIndex(Path(args.comfyui) if args.comfyui else None)
    out_dir = ROOT / "workflows"
    out_dir.mkdir(parents=True, exist_ok=True)

    entries: list[dict[str, Any]] = []
    for w in _api(args.base, "/api/workflows", token=token):
        if w.get("isBuiltin"):
            continue
        wid = str(w["id"])
        detail = _api(args.base, f"/api/workflows/{wid}", token=token)
        slug = (old_slugs.get(detail["name"]) if args.keep_slugs else None) \
            or SLUG_OVERRIDES.get(detail["name"]) or old_slugs.get(detail["name"]) \
            or _slug(detail["name"])
        if args.only and args.only not in wid and args.only not in slug:
            continue
        api_graph = json.loads(_api(args.base, f"/api/workflows/{wid}/export?format=api", token=token)["json"])
        (out_dir / f"{slug}.api.json").write_text(
            json.dumps(api_graph, ensure_ascii=False, indent=2), encoding="utf-8")
        files = {"api": f"workflows/{slug}.api.json"}
        try:
            ui = _api(args.base, f"/api/workflows/{wid}/export?format=ui", token=token)["json"]
        except Exception:
            ui = None
        if ui:
            (out_dir / f"{slug}.ui.json").write_text(
                json.dumps(json.loads(ui), ensure_ascii=False, indent=2), encoding="utf-8")
            files["ui"] = f"workflows/{slug}.ui.json"
        try:
            models = _api(args.base, f"/api/workflows/{wid}/models?instance_id={inst['id']}", token=token)
        except Exception:
            models = {"slots": []}
        classes = sorted({v.get("class_type") for v in api_graph.values() if isinstance(v, dict)})
        entries.append({
            "slug": slug,
            "name": detail["name"],
            "description": detail.get("description"),
            "tags": detail.get("tags") or [],
            "taskKind": detail.get("taskKind"),
            "family": detail.get("family"),
            "mode": detail.get("mode"),
            "modeLabel": detail.get("modeLabel"),
            "priority": detail.get("priority"),
            "autoSelect": detail.get("autoSelect"),
            "executesOn": detail.get("executesOn"),
            "sourceFormat": detail.get("sourceFormat"),
            "verifiedAt": detail.get("verifiedAt"),
            "files": files,
            "nodes": len(api_graph),
            "slotCount": detail.get("slotCount"),
            "requiredWeights": [
                {"key": s["key"], "role": s["role"], "classType": s["classType"],
                 "file": s["current"], "missing": s["missing"]} for s in models.get("slots") or []
            ],
            "customNodes": sorted(c for c in classes if not core.is_core(c)) if core.available() else [],
            "nodeClasses": classes,
            "coreScan": core.available(),
            "adaptations": len(detail.get("adaptations") or []),
            "pendingMedia": detail.get("pendingMedia") or [],
            "signals": [{"name": s.get("name"), "type": s.get("type"), "label": s.get("label"),
                         "required": s.get("required")} for s in detail.get("signals") or []],
        })

    manifest = {
        "package": "H3 Studio 工作流包",
        "packageEn": "H3 Studio workflow bundle",
        "schema": 1,
        "exportedFrom": {
            "library": args.base,
            "workflowCount": len(entries),
            "note": "导出机实例：%s（%s · %s）。requiredWeights 记的是那台机器 /object_info 报出来的真实"
                    "文件名；换机器时这些名字必须一字不差地存在（导入的权重对齐不做近似匹配），"
                    "或在「设置 → Server 与工作流」里重绑。"
                    % (inst.get("name"), inst.get("placement"), inst.get("baseUrl")),
        },
        "note": "这里的 *.api.json 是已经过本机等价改写、可直接执行的那一份；"
                "作者原始导出与逐条改写清单在数据库 workflows.graph_original / adaptations 两列。",
        "noteEn": "Each *.api.json is the adapted, directly executable graph. The author's original "
                  "export and the per-node rewrite log live in workflows.graph_original / adaptations.",
        "workflows": entries,
    }
    manifest.update({k: v for k, v in carry.items() if k not in manifest})
    (ROOT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"导出 {len(entries)} 条 → work/manifest.json"
          + ("" if core.available() else "（没给 --comfyui，customNodes 留空，节点类名见 nodeClasses）"))
    for e in entries:
        print(f"  {e['slug']:<26} {e['name'][:34]:<36} nodes={e['nodes']:<3} "
              f"ui={'y' if 'ui' in e['files'] else 'n'} weights={len(e['requiredWeights'])} "
              f"custom={','.join(e['customNodes']) or '-'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
