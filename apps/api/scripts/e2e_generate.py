"""生成链路端到端验收：文本 / 图片 / 视频三条路各真跑一次。

与 e2e_queue.py 的区别：那个验的是「队列层」（认领、派发、收口、Range 读回），
用的是 8×8 空图这种不碰真模型的骨架；这个验的是**页面按钮背后那条路** ——
template + slots → 服务端建图（权重从 /object_info 解析、参考图从媒体库读盘上传）
→ 派发 → 产物落库落盘 → 按 id 读回。

用法（后端与 ComfyUI 必须在跑）：
  .venv/Scripts/python.exe scripts/e2e_generate.py text      # 剧本拆解
  .venv/Scripts/python.exe scripts/e2e_generate.py image     # Qwen-Image 出图
  .venv/Scripts/python.exe scripts/e2e_generate.py video     # H3 首尾帧出片（约 8 分钟）
  .venv/Scripts/python.exe scripts/e2e_generate.py all
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

BASE = "http://127.0.0.1:8788"
MEDIA_ROOT = Path("F:/H3/data/media")
PROJECT = "e2e-generate"

SCRIPT = """【第一幕】内廷-未央宫偏殿-日

（帷幕低垂，日光被绛纱滤成晕黄。殿中香烟袅袅。）

柳如霜（低声自语）：
陛下连夜批红，朝中风向……竟变得如此快。

（宫婢甲在殿外通传求见。柳如霜收起奏折，示意她进来，压低声音问御膳房的事。）

【第二幕】御花园-午后

（芍药成海。周皇后明黄凤袍入场，与沈贵妃寒暄试探。柳如霜上前阻止皇后用安神酥。）
"""

IMAGE_PROMPT = (
    "真人影视质感，东亚女性二十岁出头，乌黑长发挽成低髻，深墨青色绣暗纹云鹤长袍，"
    "立于宫殿廊柱阴影中，一手收袖中。日光自右侧窗斜射，尘埃在光柱里浮动，胶片质感，8K。"
)
VIDEO_PROMPT = (
    "integrated_multimodal_description: 同一位女子从案前缓缓起身，转头望向右侧高窗，衣料随动作下垂；"
    "镜头缓慢推近。\noverall_soundscape: 殿内寂静，远处箜篌与衣料摩擦声\nnon_diegetic_music: 低音大提琴单音，极慢"
)

results: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str) -> None:
    results.append((name, ok, detail))
    print(f"{'[通过]' if ok else '[失败]'} {name} —— {detail}", flush=True)


def login(client: httpx.Client) -> dict[str, str]:
    r = client.post(f"{BASE}/api/auth/login", json={"username": "admin", "password": "1234"})
    if r.status_code >= 400:
        raise SystemExit(f"登录失败（{r.status_code}）：{r.text[:200]}。先起后端并确认演示账号 admin/1234")
    return {"Authorization": "Bearer " + r.json()["access"]}


def local_instance(client: httpx.Client, h: dict[str, str]) -> str:
    rows = client.get(f"{BASE}/api/instances", headers=h).json()
    comfy = [x for x in rows if x.get("protocol") == "comfy_native" and x.get("placement") == "local"]
    if comfy:
        return str(comfy[0]["id"])
    r = client.post(
        f"{BASE}/api/instances",
        headers=h,
        json={
            "name": "本机 ComfyUI",
            "protocol": "comfy_native",
            "placement": "local",
            "baseUrl": "http://127.0.0.1:8188",
            "localOutputRoot": "F:/H3/comfyui/ComfyUI/output",
            "isDefault": True,
        },
    )
    if r.status_code >= 400:
        raise SystemExit(f"登记本机实例失败：{r.text[:200]}")
    return str(r.json()["id"])


def wait_job(client: httpx.Client, h: dict[str, str], job_id: str, timeout_s: int) -> tuple[dict, float]:
    t0 = time.monotonic()
    last = ""
    while time.monotonic() - t0 < timeout_s:
        job = client.get(f"{BASE}/api/jobs/{job_id}", headers=h).json()
        state = job.get("state")
        prog = job.get("progress") or {}
        line = f"{state} {prog.get('value', '-')}/{prog.get('max', '-')} {prog.get('stage') or ''}"
        if line != last:
            print(f"    {time.monotonic() - t0:6.0f}s  {line}", flush=True)
            last = line
        if state in {"succeeded", "failed", "canceled"}:
            return job, time.monotonic() - t0
        time.sleep(3)
    return {"state": "timeout", "error": {"message": f"{timeout_s}s 内没到终态"}}, time.monotonic() - t0


def check_text(client: httpx.Client, h: dict[str, str]) -> None:
    t0 = time.monotonic()
    r = client.post(f"{BASE}/api/llm/run", headers=h, json={"purpose": "script_parse", "input": SCRIPT}, timeout=900)
    dt = time.monotonic() - t0
    if r.status_code >= 400:
        record("文本 · 剧本拆解", False, f"{r.status_code} {r.text[:200]}")
        return
    data = r.json()["data"]
    chars = [c["name"] for c in data.get("characters", [])]
    scenes = [s["name"] for s in data.get("scenes", [])]
    ok = len(chars) >= 2 and len(scenes) >= 1
    record("文本 · 剧本拆解", ok, f"{dt:.0f}s，角色 {chars}，场景 {scenes}，节拍 {len(data.get('beats', []))} 段")

    t0 = time.monotonic()
    r = client.post(
        f"{BASE}/api/llm/run",
        headers=h,
        json={"purpose": "storyboard", "input": json.dumps(data, ensure_ascii=False), "targetSec": 30, "pace": "紧凑"},
        timeout=900,
    )
    dt = time.monotonic() - t0
    if r.status_code >= 400:
        record("文本 · 分镜规划", False, f"{r.status_code} {r.text[:200]}")
        return
    shots = r.json()["data"].get("shots", [])
    total = sum(float(s.get("durationSec") or 0) for s in shots)
    record("文本 · 分镜规划", len(shots) >= 3, f"{dt:.0f}s，{len(shots)} 镜，合计 {total:.0f}s（目标 30s）")


def upload_frame(client: httpx.Client, h: dict[str, str], path: Path, role: str) -> int:
    with path.open("rb") as fh:
        r = client.post(
            f"{BASE}/api/media/upload",
            headers=h,
            params={"project_key": PROJECT, "role": role, "ref_id": "e2e-shot-1"},
            files={"file": (path.name, fh, "image/png")},
        )
    if r.status_code >= 400:
        raise SystemExit(f"上传 {path.name} 失败：{r.text[:200]}")
    return int(r.json()["id"])


def check_image(client: httpx.Client, h: dict[str, str], instance_id: str) -> int | None:
    body = {
        "template": "qwen_image",
        "slots": {"prompt": IMAGE_PROMPT, "width": 1024, "height": 1024, "steps": 25, "cfg": 1.0, "seed": 2026},
        "kind": "image",
        "title": "e2e · 角色图",
        "projectKey": PROJECT,
        "instanceId": instance_id,
        "meta": {"role": "character", "refId": "e2e-char-1"},
    }
    r = client.post(f"{BASE}/api/jobs", headers=h, json=body)
    if r.status_code >= 400:
        record("图片 · 入队", False, f"{r.status_code} {r.text[:220]}")
        return None
    job_id = r.json()["id"]
    job, dt = wait_job(client, h, job_id, 1500)
    if job.get("state") != "succeeded":
        record("图片 · 出图", False, f"{job.get('state')}：{(job.get('error') or {}).get('message', '')[:200]}")
        return None
    media_ids = job.get("mediaIds") or []
    if not media_ids:
        record("图片 · 出图", False, "成功但零产物")
        return None
    rows = client.get(f"{BASE}/api/media", headers=h, params={"ids": ",".join(str(m) for m in media_ids)}).json()
    ok_file = bool(rows) and (MEDIA_ROOT / rows[0]["path"]).is_file()
    raw = client.get(f"{BASE}/api/media/{rows[0]['id']}/raw", headers=h) if rows else None
    tagged = bool(rows) and rows[0].get("role") == "character" and rows[0].get("refId") == "e2e-char-1"
    record(
        "图片 · 出图",
        ok_file and raw is not None and raw.status_code == 200 and tagged,
        f"{dt:.0f}s，产物 {rows[0]['path'] if rows else '无'}，{len(raw.content) if raw else 0} 字节可读回，标签 {'对' if tagged else '丢'}",
    )
    return int(media_ids[0]) if media_ids else None


def check_video(client: httpx.Client, h: dict[str, str], instance_id: str, frame_id: int) -> None:
    """首帧用刚出的那张角色图，尾帧用同一张 —— 先验通链路，再谈运动幅度。"""
    body = {
        "template": "h3_video",
        "slots": {
            "prompt": VIDEO_PROMPT,
            "first_frame": frame_id,
            "last_frame": frame_id,
            "width": 864,
            "height": 480,
            "seconds": 2,
            "turbo": True,
            "steps": 8,
            "seed": 7,
        },
        "kind": "video",
        "title": "e2e · 首尾帧出片",
        "projectKey": PROJECT,
        "instanceId": instance_id,
        "meta": {"role": "video", "refId": "e2e-shot-1"},
    }
    r = client.post(f"{BASE}/api/jobs", headers=h, json=body)
    if r.status_code >= 400:
        record("视频 · 入队", False, f"{r.status_code} {r.text[:220]}")
        return
    job_id = r.json()["id"]
    job, dt = wait_job(client, h, job_id, 2400)
    if job.get("state") != "succeeded":
        record("视频 · H3 首尾帧", False, f"{job.get('state')}：{(job.get('error') or {}).get('message', '')[:220]}")
        return
    media_ids = job.get("mediaIds") or []
    rows = client.get(f"{BASE}/api/media", headers=h, params={"ids": ",".join(str(m) for m in media_ids)}).json()
    if not rows:
        record("视频 · H3 首尾帧", False, "成功但媒体表里没有行")
        return
    path = MEDIA_ROOT / rows[0]["path"]
    rng = client.get(f"{BASE}/api/media/{rows[0]['id']}/raw", headers={**h, "Range": "bytes=0-1023"})
    record(
        "视频 · H3 首尾帧",
        path.is_file() and path.stat().st_size > 10_000 and rng.status_code == 206,
        f"{dt:.0f}s，{path.name} {path.stat().st_size / 1024 / 1024:.2f} MB，Range 读回 {rng.status_code}",
    )


def main() -> int:
    mode = (sys.argv[1] if len(sys.argv) > 1 else "all").lower()
    print(f"验收模式：{mode}（后端 {BASE}）", flush=True)
    with httpx.Client(timeout=900) as client:
        try:
            health = client.get(f"{BASE}/healthz").json()
        except Exception as exc:
            print(f"后端没起来：{exc}")
            return 1
        print(f"后端 ok，数据库={health['database']}，媒体根={health['mediaRoot']}", flush=True)
        h = login(client)
        instance_id = local_instance(client, h)
        print(f"本机实例：{instance_id}", flush=True)

        if mode in {"text", "all"}:
            check_text(client, h)
        frame_id: int | None = None
        if mode in {"image", "video", "all"}:
            frame_id = check_image(client, h, instance_id)
        if mode in {"video", "all"}:
            if frame_id is None:
                # 单独跑 video 时复用上一次出图，或退到 smoke 目录里的现成图
                candidates = sorted(MEDIA_ROOT.glob("smoke/img_*.png"), key=lambda p: p.stat().st_mtime, reverse=True)
                if not candidates:
                    record("视频 · H3 首尾帧", False, "没有可当首帧的图（先跑 image，或准备 data/media/smoke/img_*.png）")
                else:
                    frame_id = upload_frame(client, h, candidates[0], "keyframe_start")
                    check_video(client, h, instance_id, frame_id)
            else:
                check_video(client, h, instance_id, frame_id)

    passed = sum(1 for _, ok, _ in results if ok)
    print("\n──────── 汇总 ────────")
    for name, ok, detail in results:
        print(f"  {'✅' if ok else '❌'} {name}：{detail[:150]}")
    print(f"{passed}/{len(results)} 通过")
    return 0 if passed == len(results) and results else 1


if __name__ == "__main__":
    raise SystemExit(main())
