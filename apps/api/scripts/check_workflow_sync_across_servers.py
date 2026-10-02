"""跨 Server 的工作流同步验收（需要本机 ComfyUI 在跑 + 后端在 8788）。

跑法：
    cd apps/api && PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/check_workflow_sync_across_servers.py

本机只有一台 ComfyUI，所以「第二台」是**同一个 ComfyUI 的另一条登记**（名字带「验收」，跑完删掉）。
这样能真验到的：同名沿用、按族换档、认不出就留空、图上没这个节点、缺节点整条 blocked、
未绑定位对齐、整库 sync-all、内置模板也一起对齐。
验不到的：两台节点集真不一样的机器之间的对齐 —— 那要接上第二台真机再跑一遍。

夹具是直接写库造的（PUT 接口会先按实例校验，塞不进「这台没有的那个文件」），
用完删临时实例（绑定按外键级联）与临时工作流条目。
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

BASE = "http://127.0.0.1:8788"
TEMP_INSTANCE = "验收 · 第二台（跑完删）"
TEMP_WORKFLOW = "验收 · 跨台同步"
# 本机没有、但按族判据认得出该配成哪个文件的（2026-10-03 实测：clip 那个会换到 *_int8_convrot，
# VAE 那个会换到 minimax_h3_video_vae_fp16）
DIVERGENT = "qwen3vl_32b_minimax_h3_int8.safetensors"
DIVERGENT_VAE = "minimax_h3_video_vae.safetensors"
# 另一个族，本机一个候选都对不上 —— 必须留空，不许就近凑
FOREIGN = "anima_baseV10_fp16.safetensors"
FAILS: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(("  ✓ " if ok else "  ✗ ") + name + (f" — {detail}" if detail else ""))
    if not ok:
        FAILS.append(name)


def run_db(coro):
    """每个夹具操作各起一个 asyncio 循环，所以用完必须把引擎单例丢掉。

    SQLAlchemy 的异步连接池绑在创建它的那个循环上，留着下次用就是
    asyncpg 的 'NoneType' object has no attribute 'send'（见 app/db.reset_engine 的说明）。
    """
    from app.db import reset_engine

    try:
        return asyncio.run(coro)
    finally:
        reset_engine()


async def _insert_binding(ref: str, instance_id: int, overrides: dict) -> None:
    from app.db import session_factory
    from app.workflow_bindings import save

    async with session_factory()() as s:
        await save(s, ref, instance_id, overrides, source="manual")
        await s.commit()


async def _patch_graph(ref: str, mutate) -> dict:
    """改临时条目的图。必须 deepcopy：浅拷贝时内层 inputs 还是 ORM 对象里那个同一个字典，
    改完再赋回去等于「新旧值相等」，SQLAlchemy 直接不发 UPDATE，夹具看着成功其实没写进去。"""
    import copy

    from sqlalchemy import select

    from app.db import session_factory
    from app.models import Workflow

    async with session_factory()() as s:
        row = (await s.execute(select(Workflow).where(Workflow.id == int(ref)))).scalars().first()
        graph = copy.deepcopy(row.graph or {})
        mutate(graph)
        row.graph = graph
        await s.commit()
        await s.flush()
        s.expire(row)
        fresh = (await s.execute(select(Workflow).where(Workflow.id == int(ref)))).scalars().first()
        return dict(fresh.graph or {})


async def _cleanup(temp_ref: str) -> None:
    """删临时条目 + 扫孤儿绑定。

    临时实例必须走接口删（DELETE /api/instances/{id}）：ORM 删了行，运行中的注册表里那台还在，
    设置页就会列出一台点不动的鬼实例 —— 注册表只有接口那条路才会同步 drop。
    """
    from sqlalchemy import delete, select, text

    from app.db import session_factory
    from app.models import Workflow, WorkflowModelBinding

    async with session_factory()() as s:
        if temp_ref:
            row = (await s.execute(select(Workflow).where(Workflow.id == int(temp_ref)))).scalars().first()
            if row is not None:
                await s.delete(row)
        # 之前哪次跑挂了留下的孤儿：ref 是数字但工作流已经不在了
        await s.execute(text(
            "DELETE FROM workflow_model_bindings b WHERE b.workflow_ref ~ '^[0-9]+$' "
            "AND NOT EXISTS (SELECT 1 FROM workflows w WHERE w.id = b.workflow_ref::bigint)"
        ))
        left = (await s.execute(text("SELECT count(*) FROM workflow_model_bindings"))).scalar()
        await s.commit()
        print(f"  清理后绑定表剩 {left} 行")


def main() -> int:
    c = httpx.Client(base_url=BASE, trust_env=False, timeout=240)
    tok = c.post("/api/auth/login", json={"username": "admin", "password": "12345"})
    if tok.status_code != 200:
        print(f"登录失败：{tok.text[:200]}")
        return 1
    c.headers["Authorization"] = f"Bearer {tok.json()['access']}"

    instances = c.get("/api/instances").json()
    if not instances:
        print("没有登记实例，验不了")
        return 1
    a = instances[0]
    print(f"源实例 {a['id']} · {a['name']} · {a['baseUrl']}")

    temp_ref = ""
    temp_id: str | None = None
    try:
        # ── 造第二台：同一个 ComfyUI 的另一条登记 ──
        created = c.post("/api/instances", json={
            "name": TEMP_INSTANCE, "protocol": "comfy_native", "placement": a["placement"],
            "baseUrl": a["baseUrl"], "isDefault": False,
        })
        if created.status_code not in (201, 200):
            print(f"建第二台失败：{created.status_code} {created.text[:200]}")
            return 1
        temp_id = str(created.json()["id"])
        print(f"临时第二台：{temp_id} · {TEMP_INSTANCE}")

        # ── 造临时条目：拿库里一条真工作流的导出导进来 ──
        src = next((w for w in c.get("/api/workflows").json() if not w.get("isBuiltin") and w.get("taskKind") == "video"), None)
        if src is None:
            print("库里没有 video 类型的工作流，换一个判据")
            return 1
        payload = c.get(f"/api/workflows/{src['id']}/export", params={"format": "api"}).json()["json"]
        imported = c.post("/api/workflows/import",
                          files={"file": (f"{TEMP_WORKFLOW}_api.json", payload, "application/json")},
                          data={"name": TEMP_WORKFLOW, "instance_id": a["id"], "tags": "验收,临时"})
        if imported.status_code != 201:
            print(f"导入临时条目失败：{imported.status_code} {imported.text[:240]}")
            return 1
        temp_ref = str(imported.json()["workflow"]["id"])
        print(f"临时工作流 #{temp_ref} · {TEMP_WORKFLOW}")

        models = c.get(f"/api/workflows/{temp_ref}/models", params={"instance_id": a["id"]}).json()
        slots = models["slots"]
        clip = next((s for s in slots if s["field"] == "clip_name"), slots[0])
        vae = next((s for s in slots if s["field"] == "vae_name" and s["key"] != clip["key"]), None)
        print("  权重位：" + "、".join(f"{s['key']}({s['role']})" for s in slots))

        # 夹具：源绑定里放三种值 —— 该换档的、该留空的、图上根本没这个节点的
        run_db(_insert_binding(temp_ref, int(a["id"]), {
            clip["key"]: DIVERGENT,
            f"{clip['node']}.clip_name_alias": FOREIGN,
            "9999.unet_name": FOREIGN,
        }))

        # 夹具：把 VAE 位的「图内默认」改成本机没有的名字，这才逼得出 aligned 这条路
        if vae:
            node, field = vae["node"], vae["field"]
            run_db(_patch_graph(temp_ref, lambda g: g[node]["inputs"].__setitem__(field, DIVERGENT_VAE)))
            after = next((s for s in c.get(f"/api/workflows/{temp_ref}/models", params={"instance_id": a["id"]}).json()["slots"]
                          if s["key"] == vae["key"]), None)
            check("夹具生效：图里那个位现在写的是本机没有的名字", bool(after) and after["current"] == DIVERGENT_VAE
                  and after["missing"] is True, json.dumps(after, ensure_ascii=False)[:200] if after else "那一位没了")

        print("\n① 单条同步：四类结论各就各位")
        one = c.post(f"/api/workflows/{temp_ref}/bindings/sync",
                     json={"sourceInstanceId": a["id"], "targetInstanceIds": [temp_id], "alignUnbound": True})
        check("同步 200", one.status_code == 200, f"{one.status_code} {one.text[:200]}")
        r = (one.json().get("results") or [{}])[0]
        check("同名/换档：源里那个文件那台没有 → 按族换成该台真有的",
              any(x["key"] == clip["key"] and x["to"] in ([o for o in next(s for s in slots if s['key'] == clip['key'])["options"]]) for x in r.get("converted", [])),
              json.dumps(r.get("converted"), ensure_ascii=False)[:220])
        check("另一个族认不出 → 留空并说明，绝不凑一个",
              any(x["key"].endswith("clip_name_alias") or FOREIGN in x.get("reason", "") for x in r.get("skipped", []))
              and not any(FOREIGN in v for v in (r.get("applied") or [])),
              json.dumps(r.get("skipped"), ensure_ascii=False)[:260])
        check("图上没有这个节点 → 跳过并说明", any("图上没有这个节点" in x.get("reason", "") for x in r.get("skipped", [])))
        if vae:
            check("未绑定的位：图里写死的这台没有 → 对齐成该台真有的（aligned）",
                  any(x["key"] == vae["key"] for x in r.get("aligned", [])),
                  json.dumps(r.get("aligned"), ensure_ascii=False)[:220])
        bound = c.get(f"/api/workflows/{temp_ref}/bindings").json()["bindings"]
        on_temp = next((b for b in bound if b["instanceId"] == temp_id), None)
        check("目标机上真的写了绑定行，且标着 sync 来源", bool(on_temp) and on_temp["source"] == "sync",
              json.dumps(on_temp, ensure_ascii=False)[:220])
        check("写入项 = 换档 + 对齐 + 照搬（不含跳过的）",
              bool(on_temp) and len(on_temp["overrides"]) == r.get("written", -1) == len(r.get("converted", [])) + len(r.get("aligned", [])) + len(r.get("applied", [])),
              f"written={r.get('written')} overrides={len(on_temp['overrides']) if on_temp else None}")

        print("\n② 关掉对齐：只搬源里显式挑的那些位")
        off = c.post(f"/api/workflows/{temp_ref}/bindings/sync",
                     json={"sourceInstanceId": a["id"], "targetInstanceIds": [temp_id], "alignUnbound": False}).json()
        r2 = (off.get("results") or [{}])[0]
        check("alignUnbound=false 时 aligned 为空", not r2.get("aligned"), json.dumps(r2, ensure_ascii=False)[:200])

        print("\n③ 缺节点的台：整条标 blocked，但绑定照样写进去")
        async def add_fake_node(ref: str) -> None:
            from sqlalchemy import select

            from app.db import session_factory
            from app.models import Workflow

            async with session_factory()() as s:
                row = (await s.execute(select(Workflow).where(Workflow.id == int(ref)))).scalars().first()
                g = dict(row.graph or {})
                g["777"] = {"class_type": "验收假节点XYZ", "inputs": {"foo": "bar"}}
                g["778"] = {"class_type": "验收假节点ABC", "inputs": {}}
                row.graph = g
                await s.commit()

        run_db(add_fake_node(temp_ref))
        chk = c.post(f"/api/workflows/{temp_ref}/check", json={"instanceIds": [temp_id]}).json()
        rep = (chk.get("reports") or [{}])[0]
        check("体检认出台上没有的节点", any("验收假节点" in p for p in rep.get("problems", [])),
              json.dumps(rep.get("problems"), ensure_ascii=False)[:220])
        three = c.post(f"/api/workflows/{temp_ref}/bindings/sync",
                       json={"sourceInstanceId": a["id"], "targetInstanceIds": [temp_id], "alignUnbound": True}).json()
        r3 = (three.get("results") or [{}])[0]
        check("同步把缺节点的台整条标灰", r3.get("blocked") is True and len(r3.get("missingNodes") or []) == 2,
              json.dumps({"blocked": r3.get("blocked"), "missingNodes": r3.get("missingNodes")}, ensure_ascii=False)[:200])

        print("\n④ 整库 sync-all")
        allr = c.post("/api/workflows/sync-all", json={
            "sourceInstanceId": a["id"], "targetInstanceIds": [temp_id],
            "alignUnbound": True, "onlyMissing": False, "includeBuiltin": True,
        })
        check("sync-all 200", allr.status_code == 200, f"{allr.status_code} {allr.text[:200]}")
        if allr.status_code == 200:
            body = allr.json()
            names = {w["workflowName"] for w in body["workflows"]}
            check("库里每条 + 内置模板都过了一遍", TEMP_WORKFLOW in names and any("内置" in n or "Qwen-Image" in n or "MiniMax H3" in n for n in names),
                  f"{body['totals']['workflows']} 条 · 写 {body['totals']['written']} 项 · 对齐 {body['totals']['aligned']} · 跳过 {body['totals']['skipped']} · blocked {body['totals']['blocked']}")
            builtin_rows = [w for w in body["workflows"] if str(w["workflowId"]).startswith("builtin:")]
            # 内置模板的权重是 discover_* 现问现配的，那台上真的有 → 正确的行为是「一个都不写」，
            # 而不是为了好看编一条绑定出来把档位焊死
            check("内置模板也过了一遍，且该台本来就能用的位一个都不写",
                  len(builtin_rows) >= 1 and all(rr.get("ok") for w in builtin_rows for rr in w["results"])
                  and sum(rr.get("written", 0) for w in builtin_rows for rr in w["results"]) == 0
                  and sum(len(rr.get("aligned", [])) for w in builtin_rows for rr in w["results"]) == 0,
                  json.dumps([{"id": w["workflowId"], "written": sum(x.get("written", 0) for x in w["results"])} for w in builtin_rows], ensure_ascii=False)[:220])
            again = c.post("/api/workflows/sync-all", json={
                "sourceInstanceId": a["id"], "targetInstanceIds": [temp_id], "onlyMissing": True, "includeBuiltin": True}).json()
            untouched = [w for w in again["workflows"] if w.get("untouched")]
            check("onlyMissing 会把已配过的条目原样跳过", len(untouched) > 0,
                  f"{len(untouched)}/{len(again['workflows'])} 条 untouched")

        print("\n⑤ 两台各用各的绑定（派发那条路已在 check_workflow_bindings.py 里真跑过，这里不再占显存）")
        opts = next(s for s in slots if s["key"] == clip["key"])["options"]
        two = [o for o in opts if o != DIVERGENT][:2]
        if len(two) == 2:
            run_db(_insert_binding(temp_ref, int(a["id"]), {clip["key"]: two[0]}))
            run_db(_insert_binding(temp_ref, int(temp_id), {clip["key"]: two[1]}))

            async def loads() -> tuple[dict, dict]:
                from app.db import session_factory
                from app.workflow_bindings import load

                async with session_factory()() as s:
                    return await load(s, temp_ref, a["id"]), await load(s, temp_ref, temp_id)

            on_a, on_b = run_db(loads())
            check("同一个 (条目, 实例) 键取回各自的绑定，互不串台",
                  on_a.get(clip["key"]) == two[0] and on_b.get(clip["key"]) == two[1],
                  f"A={json.dumps(on_a, ensure_ascii=False)} B={json.dumps(on_b, ensure_ascii=False)}")
            eff = {}
            for iid in (a["id"], temp_id):
                rep = c.post(f"/api/workflows/{temp_ref}/check", json={"instanceIds": [iid]}).json()["reports"][0]
                eff[iid] = next((s["effective"] for s in rep["models"] if s["key"] == clip["key"]), None)
            check("体检按实例报出各自的「真会用的文件」", eff[a["id"]] == two[0] and eff[temp_id] == two[1],
                  json.dumps(eff, ensure_ascii=False)[:200])
        else:
            check("这一位没有两个可用候选，跨台各用各的没验成", False, f"options={opts}")
    finally:
        print("\n⑥ 清理夹具")
        run_db(_cleanup(temp_ref))
        if temp_id:
            gone = c.delete(f"/api/instances/{temp_id}")
            print(f"  删临时实例 {temp_id} → {gone.status_code} {gone.text[:80]}")
        still = [i["name"] for i in c.get("/api/instances").json()]
        check("临时实例已删掉", not any(TEMP_INSTANCE in n for n in still), f"现在 {still}")

    print("\n" + ("全部通过" if not FAILS else f"没通过 {len(FAILS)} 项：{'、'.join(FAILS)}"))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
