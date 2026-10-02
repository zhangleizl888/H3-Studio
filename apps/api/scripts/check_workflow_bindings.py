"""工作流按实例权重绑定的端到端验收（真后端 + 真 ComfyUI，不碰测试夹具）。

跑法（后端在 8788、pgserver 已 boot）：
    cd apps/api && PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/check_workflow_bindings.py

验的是五件互相独立的事：
① ping 只打 /system_stats + /queue，不覆盖 capabilities；
② 绑定能存能读能删，写之前逐个核（挑一个这台没有的文件必须 400）；
③ 体检报出来的「真会用的文件」= 绑定值优先于图里写死的；
④ 同步的规则说清楚：同名沿用、认不出族就留空，绝不就近凑；
⑤ 派发时绑定真的落进提交给 ComfyUI 的那张图（看到 params.graph 后立刻取消，不占显存）。
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

BASE = "http://127.0.0.1:8788"
FAILS: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(("  ✓ " if ok else "  ✗ ") + name + (f" — {detail}" if detail else ""))
    if not ok:
        FAILS.append(name)


def _read_params(job_uuid: str) -> dict:
    """任务接口故意不回 params（整张图太大），要看得直接读库。

    连接串由 apps/api/.env 给（pgserver 端口每次会变），所以这里复用 app 自己的配置层。
    """
    import asyncio

    from sqlalchemy import select

    from app.db import session_factory
    from app.models import Job

    async def one() -> dict:
        async with session_factory()() as s:
            row = (await s.execute(select(Job).where(Job.uuid == job_uuid))).scalars().first()
            return dict(row.params or {}) if row else {}

    return asyncio.run(one())


def main() -> int:
    c = httpx.Client(base_url=BASE, trust_env=False, timeout=180)
    r = c.post("/api/auth/login", json={"username": "admin", "password": "12345"})
    if r.status_code != 200:
        print(f"登录失败 {r.status_code}：{r.text[:200]}")
        return 1
    c.headers["Authorization"] = f"Bearer {r.json()['access']}"

    instances = c.get("/api/instances").json()
    if not instances:
        print("没有登记实例，后面全部验不了")
        return 1
    inst = instances[0]
    iid = inst["id"]
    print(f"实例 {iid} · {inst['name']} · {inst['baseUrl']}")

    print("\n① 轻量在线探测 ping")
    ping = c.post(f"/api/instances/{iid}/ping")
    check("ping 返回 200 且带队列计数", ping.status_code == 200 and "running" in ping.json(),
          f"{ping.status_code} {ping.text[:120]}")
    caps_before = c.get("/api/instances").json()[0].get("capabilities") or {}
    check("ping 没有把 capabilities 写成空", bool((c.get('/api/instances').json()[0].get('capabilities') or {}).get('nodeCount')),
          f"nodeCount={caps_before.get('nodeCount')}")

    workflows = [w for w in c.get("/api/workflows").json() if not w.get("isBuiltin")]
    if not workflows:
        print("库里没有工作流")
        return 1
    wf = next((w for w in workflows if w["taskKind"] == "image"), workflows[0])
    print(f"\n工作流 #{wf['id']} · {wf['name']} · 模式 {wf.get('modeLabel')} · 文件 {wf.get('sourceFile')} · {wf.get('jsonBytes')} B")
    check("列表回包带 mode/绑定摘要", isinstance(wf.get("mode"), str) and isinstance(wf.get("bindings"), dict))

    models = c.get(f"/api/workflows/{wf['id']}/models", params={"instance_id": iid})
    check("模型清单 200", models.status_code == 200, f"{models.status_code} {models.text[:160]}")
    slots = models.json().get("slots") or []
    print(f"  权重位 {len(slots)} 个：" + "、".join(f"{s['role']} {s['key']}" for s in slots))
    if not slots:
        print("  没有可换的位，后面验不了")
        return 1
    slot = max(slots, key=lambda s: len(s["options"]))
    other = next((o for o in slot["options"] if o != slot["current"]), slot["current"])
    print(f"  拿 {slot['key']}（{slot['classType']}.{slot['field']}）：图里 {slot['current']} → 绑成 {other}")

    print("\n② 绑定读写")
    bad = c.put(f"/api/workflows/{wf['id']}/bindings",
                json={"instanceId": iid, "overrides": {slot["key"]: "这个文件绝对不存在.safetensors"}})
    check("挑了这台没有的文件 → 400 且说人话", bad.status_code == 400 and "没有" in bad.text,
          f"{bad.status_code} {bad.text[:180]}")
    saved = c.put(f"/api/workflows/{wf['id']}/bindings", json={"instanceId": iid, "overrides": {slot["key"]: other}})
    check("保存合法绑定", saved.status_code == 200 and saved.json()["overrides"].get(slot["key"]) == other, saved.text[:160])
    listed = c.get(f"/api/workflows/{wf['id']}/bindings").json()["bindings"]
    check("绑定列表读得回来", any(b["instanceId"] == iid and b["overrides"].get(slot["key"]) == other for b in listed),
          json.dumps(listed, ensure_ascii=False)[:200])

    print("\n③ 体检")
    chk = c.post(f"/api/workflows/{wf['id']}/check", json={"instanceIds": [iid]})
    check("体检 200", chk.status_code == 200, chk.text[:160])
    rep = (chk.json().get("reports") or [{}])[0]
    bound_slot = next((s for s in rep.get("models") or [] if s["key"] == slot["key"]), None)
    check("体检里的「真会用的文件」= 绑定值", bool(bound_slot) and bound_slot["effective"] == other and bound_slot["bound"] == other,
          json.dumps(bound_slot, ensure_ascii=False)[:220] if bound_slot else "没有这一位")
    check("体检说这台可达并给出问题清单", rep.get("reachable") is True and isinstance(rep.get("problems"), list),
          f"reachable={rep.get('reachable')} problems={rep.get('problems')}")

    print("\n④ 同步（只有一台时不该乱搬）")
    sync = c.post(f"/api/workflows/{wf['id']}/bindings/sync",
                  json={"sourceInstanceId": iid, "targetInstanceIds": [iid, "999999"]})
    check("同步接口 200 且逐台给结论", sync.status_code == 200 and len(sync.json()["results"]) >= 1, sync.text[:200])
    if sync.status_code == 200:
        res = sync.json()["results"]
        check("源自身不写回、不存在的台报失败不崩", all(r["instanceId"] != iid for r in res)
              and any(r.get("ok") is False for r in res if r["instanceId"] == "999999"),
              json.dumps(res, ensure_ascii=False)[:240])

    print("\n⑤ 派发时绑定真的进图")
    job = c.post("/api/jobs", json={
        "instanceId": iid, "workflowId": int(wf["id"]), "kind": wf["taskKind"],
        "title": "绑定验收 · 立刻取消", "slots": {"prompt": "验收占位提示词", "width": 512, "height": 512, "seconds": 3, "steps": 4},
    })
    check("入队 200", job.status_code == 200, f"{job.status_code} {job.text[:260]}")
    if job.status_code == 200:
        jid = job.json()["id"]
        params: dict = {}
        state = ""
        for _ in range(60):
            time.sleep(2)
            params = _read_params(jid)
            state = (c.get(f"/api/jobs/{jid}").json() or {}).get("state", "")
            if params.get("graph") or state in {"failed", "succeeded", "canceled"}:
                break
        c.post(f"/api/jobs/{jid}/cancel")
        graph = params.get("graph") or {}
        node = str(slot["node"])
        in_graph = ((graph.get(node) or {}).get("inputs") or {}).get(slot["field"])
        check("params.bindingModels 记下了绑定", (params.get("bindingModels") or {}).get(slot["key"]) == other,
              json.dumps(params.get("bindingModels"), ensure_ascii=False)[:160])
        check("提交给 ComfyUI 的图里就是绑定那个文件", in_graph == other, f"图上 {in_graph!r} · 任务状态 {state}")
        notes = json.dumps((params.get("fill") or {}), ensure_ascii=False)
        check("填图审计里看得见换过哪个权重", other[:24] in notes, notes[:240])
        print(f"  任务 {jid} 已取消")

    print("\n⑥ 清干净")
    cleared = c.delete(f"/api/workflows/{wf['id']}/bindings/{iid}")
    left = c.get(f"/api/workflows/{wf['id']}/bindings").json()["bindings"]
    check("删除绑定 204 且列表里没了", cleared.status_code == 204 and not any(b["instanceId"] == iid for b in left),
          json.dumps(left, ensure_ascii=False)[:160])

    print("\n⑦ 本地导入与替换 JSON 通道（用库里现成那条的导出当文件，脚本可反复跑）")
    payload = c.get(f"/api/workflows/{wf['id']}/export", params={"format": "api"}).json()["json"]
    bad_ui = c.post("/api/workflows/import",
                    files={"file": ("验收_api.json", payload, "application/json"),
                           "ui_file": ("验收_ui.json", payload, "application/json")},
                    data={"name": "验收 · 导入通道", "instance_id": iid})
    check("把 API 版当画布版传进来 → 400 说清楚要什么", bad_ui.status_code == 400 and "画布" in bad_ui.text,
          f"{bad_ui.status_code} {bad_ui.text[:160]}")
    imported = c.post("/api/workflows/import",
                      files={"file": ("验收 · 导入通道_api.json", payload, "application/json")},
                      data={"name": "验收 · 导入通道", "instance_id": iid, "priority": "70", "tags": "验收,临时"})
    check("导入 201", imported.status_code == 201, f"{imported.status_code} {imported.text[:200]}")
    new_id = ""
    if imported.status_code == 201:
        new = imported.json()["workflow"]
        new_id = str(new["id"])
        check("导入后认得出模式与文件名", bool(new.get("modeLabel")) and "验收" in (new.get("sourceFile") or ""),
              f"{new.get('modeLabel')} · {new.get('sourceFile')} · {new.get('nodeCount')} 节点")
        rep = c.post(f"/api/workflows/{new_id}/graph", json={"graph": payload, "instanceId": iid})
        check("替换 JSON 200 并给出改动说明", rep.status_code == 200 and bool(rep.json()["report"]["notes"]),
              f"{rep.status_code} {json.dumps(rep.json().get('report',{}).get('notes',[]),ensure_ascii=False)[:200]}")
        gone = c.delete(f"/api/workflows/{new_id}")
        check("删掉验收条目", gone.status_code == 204, str(gone.status_code))
        new_id = ""

    print("\n" + ("全部通过" if not FAILS else f"没通过 {len(FAILS)} 项：{'、'.join(FAILS)}"))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
