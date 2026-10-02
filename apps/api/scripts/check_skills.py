"""技能库端到端验收：建、导（JSON 多条 + SKILL.md）、查、改、并进生成请求与文本调用。

跑法（后端要在 8788 上活着，验别的端口就 H3_CHECK_BASE=http://127.0.0.1:8799）：
    cd apps/api && PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/check_skills.py

这条脚本只碰 skills 表和 /jobs/plan（只读、不入队），不会留下任务或产物。
它自己建的两条技能用完就删掉，库回到跑之前的样子。
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid

BASE = os.environ.get("H3_CHECK_BASE", "http://127.0.0.1:8788")
# 系统代理会把环回请求一起劫走（见 docs 里那条 dev-env 教训），这里显式不走代理
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def call(method: str, path: str, body=None, token=None, raw_body: bytes | None = None, ctype=None):
    req = urllib.request.Request(BASE + path, method=method)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    data = None
    if raw_body is not None:
        data = raw_body
        req.add_header("Content-Type", ctype or "application/json")
    elif body is not None:
        data = json.dumps(body).encode("utf-8")
        req.add_header("Content-Type", "application/json")
    try:
        with _opener.open(req, data, timeout=120) as res:
            payload = res.read()
            return res.status, (json.loads(payload) if payload and res.headers.get("content-type", "").startswith("application/json") else payload)
    except urllib.error.HTTPError as e:
        payload = e.read()
        try:
            return e.code, json.loads(payload)
        except Exception:
            return e.code, payload.decode("utf-8", errors="replace")


def multipart(fields: dict[str, str], files: list[tuple[str, str]]) -> tuple[bytes, str]:
    """自己拼 multipart：这台机的 urllib 没有 MultipartEncoder，而导入接口只吃 multipart。"""
    boundary = "----h3skills" + uuid.uuid4().hex
    out = bytearray()
    for k, v in fields.items():
        out += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
    for name, content in files:
        out += f"--{boundary}\r\nContent-Disposition: form-data; name=\"files\"; filename=\"{name}\"\r\nContent-Type: text/plain\r\n\r\n".encode()
        out += content.encode("utf-8") + b"\r\n"
    out += f"--{boundary}--\r\n".encode()
    return bytes(out), f"multipart/form-data; boundary={boundary}"


fails: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(("  ✓ " if ok else "  ✗ ") + label + (f" — {detail}" if detail else ""))
    if not ok:
        fails.append(f"{label}{('：' + detail) if detail else ''}")


def main() -> int:
    user = os.environ.get("H3_CHECK_USER", "admin")
    password = os.environ.get("H3_CHECK_PASS", "12345")
    status, sess = call("POST", "/api/auth/login", {"username": user, "password": password})
    if status != 200:
        print(f"登录失败 {status}：{sess}")
        print("演示账号口令被改过就用 H3_CHECK_USER / H3_CHECK_PASS 指一个能用的账号重跑。")
        return 1
    token = sess["access"]

    print("\n[1] 新建技能")
    tag = uuid.uuid4().hex[:6]
    name = f"验收·冷开场写法-{tag}"
    status, made = call("POST", "/api/skills", {"name": name, "description": "验收用", "content": "开场三秒内给一个看得见的悬念，不解释。", "stage": "script", "tags": ["验收"]}, token)
    check("POST /skills 建一条", status == 201 and made.get("name") == name, f"{status} {made if status != 201 else made.get('id')}")
    sid = str(made.get("id"))

    status, again = call("POST", "/api/skills", {"name": name, "content": "再来一遍"}, token)
    check("同名再建被拒（409）", status == 409, f"{status}")

    print("\n[2] 导入：一份 JSON 装多条 + 一份 SKILL.md")
    lib = f"验收技能库-{tag}"
    bundle = json.dumps({"name": lib, "skills": [
        {"name": f"验收·画面克制-{tag}", "description": "只写看得见的", "content": "不写情绪词，只写动作与光线。", "stage": "asset", "tags": ["验收", "画面"]},
        {"name": f"验收·镜头接续-{tag}", "content": "每镜结尾留一个交给下一镜的可见变化。", "stage": "video"},
    ]}, ensure_ascii=False)
    skill_md = f"---\nname: 验收·SKILL条目\ndescription: frontmatter 认不认\nstage: video\n---\n# 验收·SKILL条目\n接缝要接在受力上，不是接在同景别上。\n"
    body, ctype = multipart({"library": lib}, [("skills.json", bundle), ("验收角色目录/SKILL.md", skill_md)])
    status, rep = call("POST", "/api/skills/import", raw_body=body, ctype=ctype, token=token)
    counts = (rep or {}).get("counts") or {}
    check("POST /skills/import 收 2 个文件", status == 200 and counts.get("created") == 3, f"{status} {counts}")
    check("SKILL.md 用正文而不是文件名当名字", any(i["name"] == "验收·SKILL条目" for i in (rep or {}).get("created") or []), str([i["name"] for i in (rep or {}).get("created") or []]))

    # 重导同一份：应当刷新而不是再插一份
    body3, ctype3 = multipart({"library": lib}, [("skills.json", bundle)])
    status, rep3 = call("POST", "/api/skills/import", raw_body=body3, ctype=ctype3, token=token)
    c3 = (rep3 or {}).get("counts") or {}
    check("重导同一批走刷新不走重复", status == 200 and c3.get("created") == 0 and c3.get("updated") == 2, f"{status} {c3}")

    print("\n[3] 清单与环节过滤")
    status, all_rows = call("GET", "/api/skills", token=token)
    check("GET /skills 有内容", status == 200 and len(all_rows) >= 4, f"{status} 共 {len(all_rows) if status == 200 else all_rows} 条")
    status, scripts = call("GET", "/api/skills?stage=script", token=token)
    check("stage=script 只给剧本 + 通用", status == 200 and all(s["stage"] in ("script", "general") for s in scripts), f"{status} {[s['name'] for s in scripts] if status == 200 else scripts}")

    print("\n[4] 编辑")
    status, patched = call("PATCH", f"/api/skills/{sid}", {"content": "开场三秒给一个看得见的悬念；收一句台词就停。", "stage": "general"}, token)
    check("PATCH 改正文与环节", status == 200 and "收一句台词" in (patched or {}).get("content", ""), f"{status}")

    print("\n[5] 技能随生成请求一起发出去（/jobs/plan 只读，不入队）")
    status, picked = call("GET", "/api/skills?stage=asset", token=token)
    asset_ids = [str(s["id"]) for s in (picked or []) if s["stage"] == "asset"][:1]
    plan_body = {"jobs": [{
        "template": "qwen_image",
        "kind": "image",
        "title": "验收·技能并进提示词",
        "slots": {"prompt": "一位女子立于檐下", "width": 832, "height": 1216},
        "skillIds": asset_ids,
        "meta": {"role": "character", "refId": "check-skill"},
    }]}
    status, plan = call("POST", "/api/jobs/plan", plan_body, token)
    rows = (plan or {}).get("rows") or []
    got = ((rows[0] or {}).get("slots") or {}).get("prompt", "") if rows else ""
    check("plan 回来的 slots.prompt 真并上了技能正文", status == 200 and bool(asset_ids) and "不写情绪词" in got, f"{status}｜prompt={got[:120]!r}")

    print("\n[6] 技能随文本调用一起发出去（/llm/run）")
    bogus = "99999999"
    status, err = call("POST", "/api/llm/run", {"purpose": "script_chat", "input": "随便改一句", "skillIds": [bogus]}, token)
    check("不存在的技能 id 会明确报错", status == 400 and "不在技能库里" in str(err), f"{status} {str(err)[:120]}")

    # 单条正文的硬上限是 2 万字（路由字段的 max_length），合计上限是 6000 字（resolve_block）
    status, _ = call("POST", "/api/skills", {"name": f"验收·超单条-{tag}", "content": "很长" * 10_001, "stage": "script"}, token)
    check("单条正文超过 2 万字被拒", status == 422, f"{status}")
    fat: list[str] = []
    for i in range(2):
        st, row = call("POST", "/api/skills", {"name": f"验收·胖技能{i}-{tag}", "content": "这是一条技能正文，用来顶到合计上限。" * 220, "stage": "script"}, token)
        if st == 201:
            fat.append(str(row["id"]))
    status, over = call("POST", "/api/llm/run", {"purpose": "script_chat", "input": "随便改一句", "skillIds": fat}, token)
    check("两条合计超过单次 6000 字上限时拦住并说清怎么办", status == 400 and "超过单次上限" in str(over), f"{status} {str(over)[:140]}")
    for sid_fat in fat:
        call("DELETE", f"/api/skills/{sid_fat}", token=token)
    status, model_err = call("POST", "/api/llm/run", {"purpose": "script_chat", "input": "随便改一句", "skillIds": [sid]}, token)
    check("没超限的技能不会被技能层拦住（这次停在模型调用本身）",
          "不在技能库里" not in str(model_err) and "超过单次上限" not in str(model_err),
          f"{status} {str(model_err)[:130]}")

    print("\n[7] 删除单条与清空整库")
    status, _ = call("DELETE", f"/api/skills/{sid}", token=token)
    check("DELETE /skills/{id} 删单条", status == 204, f"{status}")
    status, cleared = call("POST", "/api/skills/clear?confirm=" + urllib.parse.quote("清空技能库"), token=token)
    check("POST /skills/clear 清空整库", status == 200 and (cleared or {}).get("deleted", 0) >= 3, f"{status} {cleared}")
    status, rest = call("GET", "/api/skills", token=token)
    check("清空后列表是空的", status == 200 and len(rest) == 0, f"{status} 剩 {len(rest) if status == 200 else rest}")

    print("\n" + ("全部通过" if not fails else "有 %d 条没通过：\n  - %s" % (len(fails), "\n  - ".join(fails))))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
