"""四种 H3 提示词模式的真机验收（要真模型，不是假传输）。

它只动两样东西，都可逆：
  1. 对 ComfyUI 调 POST /free 卸权重（下一次出图/出片会自己重新加载）；
  2. 用 app_settings 里 gpu_arbiter 存的原始命令行拉起 llama-server；跑完默认停掉，
     因为验收开始前它没在跑。--keep 可以留着。

密钥与完整命令行只在进程内使用，本脚本不打印它们。

用法（apps/api 的 venv）：
  .venv/Scripts/python.exe -X utf8 scripts/e2e_director.py [--comfy http://127.0.0.1:8188] [--keep]
前置：不占显存的那部分检查先跑 scripts/check_director_pipeline.py。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import httpx
from dotenv import dotenv_values

API_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_DIR))

from app import llm  # noqa: E402
from app.director import MODES  # noqa: E402

BRIEF = json.dumps(
    {
        "画幅": "16:9",
        "时长秒": 6,
        "景别": "中景",
        "运镜": "推近",
        "动作": "林晚收起湿伞在长椅坐下，视线落向铁轨，呼吸停半拍",
        "台词": "还是没来。",
        "场景": "老城地铁 3 号站台·深夜",
        "出场": ["林晚（25 岁，纤细，黑色短发，卡其风衣，青灰主色，右嘴角一颗痣）"],
        "起始帧": "深夜空旷站台，冷青主光从右上方落下，林晚立于画面右侧收伞",
    },
    ensure_ascii=False,
)

SCRIPT = "【第一幕】老城地铁 3 号站台-深夜\n（雨。林晚独自等末班车。）\n林晚（低声）：还是没来。\n（她收起湿伞坐下，视线落向铁轨。远处车灯扫过她的脸，随即熄灭。）"


def parse_dsn() -> tuple[str, int, str, str, str]:
    """apps/api/.env 里是异步串（postgresql+asyncpg://user:pw@host:port/db），拆开用同步连接走。"""
    from urllib.parse import unquote, urlparse

    from dotenv import dotenv_values

    url = dotenv_values(API_DIR / ".env").get("H3_DATABASE_URL") or os.environ.get("H3_DATABASE_URL") or ""
    u = urlparse(url)
    return unquote(u.hostname or "127.0.0.1"), u.port or 5432, unquote(u.username or "h3"), unquote(u.password or ""), (u.path or "/").lstrip("/")


async def stored_cmdline() -> tuple[str | None, int | None]:
    """arbiter 停进程时存下的那条命令行——用它才能保证和平时跑的这台一模一样。"""
    import asyncpg

    host, port, user, password, database = parse_dsn()
    conn = await asyncpg.connect(host=host, port=port, user=user, password=password, database=database)
    try:
        row = await conn.fetchrow("SELECT value FROM app_settings WHERE key='gpu_arbiter'")
    finally:
        await conn.close()
    if not row:
        return None, None
    val = row["value"]
    d = json.loads(val) if isinstance(val, str) else val
    stopped = d.get("stopped") or {}
    return stopped.get("cmdline"), stopped.get("port")


def alive(port: int) -> bool:
    try:
        # trust_env=False：这台机配了系统代理，走代理探环回会被直接断连
        return httpx.get(f"http://127.0.0.1:{port}/health", timeout=3, trust_env=False).status_code == 200
    except Exception:
        return False


def free_comfy(url: str) -> str:
    try:
        r = httpx.post(f"{url.rstrip('/')}/free", json={"unload_models": True, "free_memory": True}, timeout=20, trust_env=False)
        return f"{r.status_code} {r.text[:80]}"
    except Exception as exc:
        return f"没调到：{type(exc).__name__}"


def key_of(cmd: str) -> str | None:
    m = re.search(r"--api-key\s+(\S+)", cmd)
    return m.group(1) if m else None


def wait_health(port: int, timeout: float) -> bool:
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if alive(port):
            return True
        time.sleep(3)
    return False


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--comfy", default="http://127.0.0.1:8188")
    ap.add_argument("--keep", action="store_true", help="跑完不停 llama-server")
    ap.add_argument("--skip-storyboard", action="store_true")
    args = ap.parse_args()

    cmd, port = await stored_cmdline()
    if port is None:
        port = 8080
    spawned: subprocess.Popen | None = None

    if alive(port):
        print(f"[环境] llama-server :{port} 已经在跑，不动 ComfyUI")
    else:
        if not cmd:
            print("[中止] :%d 没有 llama-server，库里也没有可复现的启动命令行（gpu_arbiter 为空）" % port)
            return 2
        print(f"[让卡] POST {args.comfy}/free → {free_comfy(args.comfy)}")
        time.sleep(2)
        print("[拉起] 按 arbiter 存的命令行启动（模型加载可能要一分钟以上）")
        spawned = subprocess.Popen(cmd, shell=True)  # noqa: S603,S602 —— 与 gpu_arbiter._spawn 同样的做法
        if not wait_health(port, 240):
            print("[中止] 240 秒内 :%d 没起来；先手动确认这台模型能不能加载" % port)
            if spawned:
                spawned.terminate()
            return 3
        print(f"[就绪] llama-server :{port} health 200")

    spec = llm.LlmSpec(base_url=f"http://127.0.0.1:{port}/v1", api_key=key_of(cmd) if cmd else None, timeout_s=1200)
    caps = await llm.probe(spec)
    print(f"[探活] reachable={caps.reachable} models={caps.models[:1]} 上下文={caps.ctx_total} json_schema={caps.has_json_schema}")
    if not caps.reachable:
        print("[中止] 探活失败：", caps.error)
        if spawned and not args.keep:
            spawned.terminate()
        return 3

    rows: list[tuple[str, int, int, str]] = []
    for mode in ("three_field", "wenwu", "six_section", "hybrid"):
        t0 = time.monotonic()
        try:
            res = await llm.run_purpose(
                spec,
                "h3_prompt",
                {"input": BRIEF, "mode": mode, "durationSec": 6, "aspect": "16:9", "style": "写实电影感"},
            )
        except llm.LlmError as exc:
            print(f"\n########## {mode} 失败（{time.monotonic() - t0:.0f}s）：{exc.message}")
            rows.append((mode, int(time.monotonic() - t0), 0, "失败"))
            continue
        data = res["data"]
        body = "\n".join(f"{k}: {v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)}" for k, v in data.items() if k != "integrated")
        preview = f"{data.get('integrated', '')}\n{body}"
        rows.append((mode, int(time.monotonic() - t0), len(preview), "ok"))
        print(f"\n########## {mode}｜{MODES[mode].name}｜{time.monotonic() - t0:.1f}s｜{len(preview)} 字｜预算 {MODES[mode].max_tokens}")
        print(preview)

    if not args.skip_storyboard:
        t0 = time.monotonic()
        res = await llm.run_purpose(spec, "storyboard", {"input": SCRIPT, "targetSec": 24, "pace": "均匀"})
        print(f"\n########## storyboard｜{time.monotonic() - t0:.1f}s")
        for s in res["data"]["shots"]:
            print(f"  镜{s['index']:>2} {s['durationSec']:g}s {s['shotSize']}/{s['cameraMovement']}｜{s['action'][:44]}")
        print("  警告：", res.get("warnings") or "无")

    print("\n===== 汇总 =====")
    for mode, sec, chars, state in rows:
        print(f"{mode:<12} {sec:>4}s  {chars:>5}字  {state}")

    if spawned and not args.keep:
        # shell=True 起的是 cmd.exe 包着 llama-server，terminate() 只会打掉包装层：
        # 按端口找到真进程再停，不然 17.5GB 会一直挂在卡上
        from app.gpu_arbiter import pid_on_port  # noqa: PLC0415

        spawned.terminate()
        await asyncio.sleep(4)
        real = await asyncio.to_thread(pid_on_port, port)
        if real:
            subprocess.run(["taskkill", "/PID", str(real), "/T", "/F"], capture_output=True)
            await asyncio.sleep(2)
        left = await asyncio.to_thread(pid_on_port, port)
        print(f"[收尾] 已停掉本次拉起的 llama-server（剩余进程：{left or '无'}）；ComfyUI 权重会在下次任务时自动重载")
    elif spawned:
        print("[收尾] --keep：llama-server 继续占着显存，你要出图前记得先让它让位")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
