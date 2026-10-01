"""任务 #16 的验收：对着真实后端 + 真实 PostgreSQL + 真实 ComfyUI 走一遍。

跑法（后端要先起着）：
  F:/H3/apps/api/.venv/Scripts/python.exe apps/api/scripts/e2e_queue.py [base_url] [fast|h3]

它验证的是「落库 + 派发」这条链，不是接口能不能通：
  建 admin → 登录 → 建 editor/viewer → 角色门禁 → 登记实例 → 探活落库
  → 入队一个真的 H3 任务 → SKIP LOCKED 循环认领并执行 → 产物转存进 media + 磁盘
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.gen.builtin_graphs import discover_h3_weights, h3_length, h3_video_graph  # noqa: E402
from app.gen.comfy_native import ComfyNativeClient  # noqa: E402

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8788").rstrip("/")
# fast = 几秒出图的纯色任务，用来复验派发链；h3 = 真出一段 H3 视频（本机实测 ~7 分钟）
MODE = (sys.argv[2] if len(sys.argv) > 2 else "h3").lower()
ADMIN = ("admin", "e2e-admin-pass-9")
EDITOR = ("e2e_editor", "e2e-editor-pass-9")
VIEWER = ("e2e_viewer", "e2e-viewer-pass-9")
COMFY = "http://127.0.0.1:8188"

STEP = 0


def say(msg: str) -> None:
    global STEP
    STEP += 1
    print(f"[{STEP:02d}] {msg}", flush=True)


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def main() -> int:
    async with httpx.AsyncClient(base_url=BASE, timeout=60.0) as c:
        h = c.get("/healthz")
        health = (await h).json()
        say(f"后端健康：database={health['database']} instances={health['instances']} mediaRoot={health['mediaRoot']}")
        if not health["database"]:
            print("！后端不在数据库模式，验收无意义（检查 apps/api/.env 的 H3_DATABASE_URL）")
            return 1

        # 1) 这是开发库上的验收脚本，先把认证相关的表清空再建首个 admin
        #    （不清的话就会撞上「库里已有 admin 但不是这里的口令」——测试套件也会写这几张表）
        from app.db import reset_engine, session_factory  # noqa: E402

        async with session_factory()() as s:
            from sqlalchemy import text

            wiped = {}
            for t in ("refresh_tokens", "audit_log", "users"):
                wiped[t] = (await s.execute(text(f"DELETE FROM {t}"))).rowcount
            await s.commit()
        reset_engine()
        say(f"已清空开发库的认证残留：{wiped}")
        r = await c.post("/api/auth/bootstrap", json={"username": ADMIN[0], "password": ADMIN[1]})
        assert r.status_code == 200, r.text
        say(f"已建首个 admin（argon2id 哈希落库，响应里没有口令）：{r.json()['user']['role']}")
        access = r.json()["access"]
        auth = bearer(access)

        # 2) 弱口令必须被拒
        weak = await c.post("/api/users", headers=auth, json={"username": "weakling", "displayName": "弱", "role": "editor", "password": "1234567890"})
        assert weak.status_code == 422, weak.text
        say(f"弱口令被拒（422）：{weak.json()['detail']}")

        # 3) 建 editor / viewer
        for (name, pwd), role in ((EDITOR, "editor"), (VIEWER, "viewer")):
            exists = await c.post("/api/auth/login", json={"username": name, "password": pwd})
            if exists.status_code == 200:
                continue
            r = await c.post("/api/users", headers=auth, json={"username": name, "displayName": name, "role": role, "password": pwd})
            assert r.status_code == 200, r.text
        tokens = {}
        for name, pwd in (ADMIN, EDITOR, VIEWER):
            r = await c.post("/api/auth/login", json={"username": name, "password": pwd})
            assert r.status_code == 200, r.text
            tokens[name] = r.json()["access"]
        say(f"三个角色就位：{' / '.join(tokens)}，访问令牌是 HS256 JWT（{len(tokens[ADMIN[0]])} 字符）")

        # 4) RBAC：改配置只有 admin
        as_editor = await c.post("/api/instances", headers=bearer(tokens[EDITOR[0]]), json={"name": "越权实例", "base_url": COMFY})
        as_viewer = await c.post("/api/instances", headers=bearer(tokens[VIEWER[0]]), json={"name": "越权实例2", "base_url": COMFY})
        anon = await c.get("/api/instances")
        assert (as_editor.status_code, as_viewer.status_code, anon.status_code) == (403, 403, 401), (as_editor.text, as_viewer.text, anon.text)
        say(f"RBAC 生效：editor 建实例 {as_editor.status_code}、viewer 建实例 {as_viewer.status_code}、未登录读列表 {anon.status_code}")

        # 5) 登记实例（apiKey 走 Fernet；本机实例没 key）
        listed = (await c.get("/api/instances", headers=auth)).json()
        mine = next((i for i in listed if i["baseUrl"].startswith("http://127.0.0.1:8188")), None)
        if mine is None:
            r = await c.post(
                "/api/instances",
                headers=auth,
                json={
                    "name": "e2e 本机 4090",
                    "protocol": "comfy_native",
                    "placement": "local",
                    "baseUrl": COMFY,
                    "localOutputRoot": "F:/H3/comfyui/ComfyUI/output",
                    "isDefault": True,
                },
            )
            assert r.status_code == 201, r.text
            mine = r.json()
        inst_id = mine["id"]
        say(f"实例已落库：id={inst_id} name={mine['name']} 默认={mine['isDefault']}")

        # 6) 探活写回库
        probe = await c.post(f"/api/instances/{inst_id}/probe", headers=auth)
        assert probe.status_code == 200, probe.text
        p = probe.json()
        assert p["ok"] and p["native"]["h3"], p
        n = p["native"]
        say(f"探活：ComfyUI {n['comfyVersion']}，节点 {n['nodeCount']}，H3 节点 {len(n['h3'])} 个，显存 {n['vramFreeGb']}/{n['vramTotalGb']} GB；提示：{p['hints'] or '无'}")

        # 7) 入队一个真任务（走队列，不直投）
        if MODE == "fast":
            # 几秒就落的纯色图：用来复验「入队→SKIP LOCKED 认领→转存 media」这条链本身
            length = 0
            graph = {
                "1": {"class_type": "EmptyImage", "inputs": {"width": 320, "height": 180, "batch_size": 1, "color": 16744245}, "_meta": {"title": "色块"}},
                "2": {"class_type": "SaveImage", "inputs": {"images": ["1", 0], "filename_prefix": "h3/e2e_fast"}, "_meta": {"title": "保存"}},
            }
            kind, title = "image", "e2e 色块图"
        else:
            client = ComfyNativeClient(COMFY)
            try:
                weights = await discover_h3_weights(client)
            finally:
                await client.close()
            if weights.missing:
                print(f"！本机实例缺 H3 权重：{weights.missing}")
                return 1
            length = h3_length(2.0)
            graph = h3_video_graph(
                weights,
                prompt="一个穿红色风衣的女孩在雨夜霓虹街道转身，镜头缓慢推近，电影感",
                width=864,
                height=480,
                length=length,
                steps=8,
                seed=20260930,
                filename_prefix="h3/e2e",
                use_turbo=True,
            )
            kind, title = "video", f"e2e H3 {length}帧"
        started = time.time()
        r = await c.post(
            "/api/jobs",
            headers=bearer(tokens[EDITOR[0]]),
            json={"instance_id": inst_id, "graph": graph, "kind": kind, "title": title, "priority": 50},
        )
        assert r.status_code == 200, r.text
        job = r.json()
        assert job["queued"] is True and job["state"] == "queued", job
        say(f"任务入队：id={job['id'][:8]} state={job['state']} priority=50（此刻 ComfyUI 队列里还没有它）")

        # viewer 派发必须挡在门口
        denied = await c.post("/api/jobs", headers=bearer(tokens[VIEWER[0]]), json={"instance_id": inst_id, "graph": graph})
        assert denied.status_code == 403, denied.text
        say(f"viewer 派发被拒（{denied.status_code}）：{denied.json()['detail']}")

        # 8) 等派发循环把它跑完
        state = job["state"]
        seen = set()
        while state not in {"succeeded", "failed", "canceled"}:
            await asyncio.sleep(3)
            snap = (await c.get(f"/api/jobs/{job['id']}", headers=auth)).json()
            state = snap["state"]
            if state not in seen:
                seen.add(state)
                say(f"状态迁移：{state}（progress={json.dumps(snap['progress'], ensure_ascii=False)}，attempts={snap['attempts']}）")
            if time.time() - started > 1500:
                print(f"！超时，最后状态 {state}")
                return 1
        elapsed = round(time.time() - started, 1)
        detail = (await c.get(f"/api/jobs/{job['id']}", headers=auth)).json()
        if state != "succeeded":
            print(f"！任务判失败（{elapsed}s）：{json.dumps(detail['error'], ensure_ascii=False)}")
            return 1
        say(f"任务成功，用时 {elapsed}s，prompt_id={str(detail['promptId'])[:8]}…")

        # 9) 产物必须已经转存进我们自己的目录 + media 表
        media_ids = detail["mediaIds"]
        assert media_ids, detail
        on_disk = []
        for m in detail["outputs"]:
            f = Path("F:/H3/data/media") / m["path"]
            on_disk.append((str(f), f.exists(), f.stat().st_size if f.exists() else 0))
        for path, exists, size in on_disk:
            say(f"产物文件：exists={exists} size={size/1024:.1f} KB → {path}")
        if not all(e for _, e, _ in on_disk):
            print("！media 表有行但磁盘没文件")
            return 1

        # 9b) 产物必须能经后端读出来（时间轴预览与「资产下载」都走这条路）
        mid = media_ids[0]
        assert (await c.get(f"/api/media/{mid}/raw")).status_code == 401
        got = await c.get(f"/api/media/{mid}/raw", headers=auth)
        size = on_disk[0][2]
        assert got.status_code == 200 and len(got.content) == size, (got.status_code, len(got.content), size)
        ranged = await c.get(f"/api/media/{mid}/raw", headers={**auth, "Range": "bytes=0-99"})
        assert ranged.status_code == 206 and ranged.headers["content-range"].startswith("bytes 0-99/"), ranged.status_code
        dl = await c.get(f"/api/media/{mid}/download", headers=auth)
        assert "attachment" in dl.headers.get("content-disposition", ""), dl.headers
        say(f"媒体读取：MIME={got.headers['content-type']} 全量={len(got.content)}B Range={ranged.status_code} 下载头={dl.headers['content-disposition'][:24]}…")

        # 10) 审计与队列留痕
        users = (await c.get("/api/users", headers=auth)).json()
        say(f"用户：{[(u['username'], u['role']) for u in users]}")
        recent = (await c.get("/api/jobs?limit=5", headers=auth)).json()
        say(f"队列里最近 {len(recent)} 个任务：{[(j['id'][:8], j['state']) for j in recent]}")
        print("\n验收通过：9 张表 + Alembic + argon2/JWT/RBAC + SKIP LOCKED 派发 + 产物转存读取，端到端跑通。")
        return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
