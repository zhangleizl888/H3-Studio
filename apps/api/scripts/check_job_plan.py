"""参数表与派发门禁的自检（不占显存、不连实例）。

跑法：F:/H3/apps/api/.venv/Scripts/python.exe -X utf8 apps/api/scripts/check_job_plan.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import job_plan as jp  # noqa: E402

INST = {"1": jp.InstanceInfo(id="1", label="本机 ComfyUI", placement="local", protocol="comfy_native", probe_ok=True)}
SIX = "\n".join(
    [
        "subject_definitions:", "<Subject 1> 林晚", "summary:", "x", "retention_analysis:", "y",
        "detailed_description:", "[Shot 1] 0.00-6.00s. 中景", "overall_soundscape:", "雨声", "non_diegetic_music:", "N/A",
        "constraints:", "no subtitles",
    ]
)

GOOD = {"template": "h3_video", "kind": "video", "title": "出片 · 镜 1", "instanceId": "1", "meta": {"promptMode": "hybrid"},
        "slots": {"prompt": SIX, "first_frame": 67, "seconds": 6, "width": 864, "height": 480, "turbo": True, "steps": 8, "seed": 123}}
BAD = {"template": "h3_video", "kind": "video", "title": "出片 · 镜 2", "instanceId": "1",
       "slots": {"prompt": "太短", "seconds": 20, "width": 1344, "height": 768, "steps": 60, "first_frame": 9999}}
IMG = {"template": "qwen_image", "kind": "image", "title": "定妆", "instanceId": "1",
       "slots": {"prompt": "一段足够长的画面提示词文本", "width": 1024, "height": 1024, "steps": 25, "seed": 7}}
GRAPH = {"kind": "workflow_test", "title": "试运行", "instanceId": "1", "graph": {"1": {"class_type": "EmptyLatentImage"}}}
NOINST = {"template": "h3_video", "kind": "video", "title": "没指定实例", "slots": {"prompt": SIX, "seconds": 6}}

out = jp.plan_jobs([GOOD, BAD, IMG, GRAPH, NOINST], instances=INST, known_media={67})
rows, totals = out["rows"], out["totals"]
for r in rows:
    eta = r["derived"].get("etaSeconds")
    print(f"[{r['index']}] {r['title']:<10} blocked={str(r['blocked']):<5} 帧={r['derived'].get('frameCount')} 实秒={r['derived'].get('realSeconds')} 预计={f'{eta}s' if eta else '—'} 问题={r['problems']}")

checks = [
    ("合法那条不该被拦", rows[0]["blocked"] is False),
    ("6 秒吸附到 17k+5 = 158 帧", rows[0]["derived"]["frameCount"] == 158),
    ("预计耗时按锚点外推（158 帧≈20.8 分钟）", 1100 < rows[0]["derived"]["etaSeconds"] < 1400),
    ("越界那条必须拦", rows[1]["blocked"] is True),
    ("时长/步数/媒体/占位文本/本机分辨率都被点名", len(rows[1]["problems"]) >= 5),
    ("出图那条有 ETA 且没有帧数", rows[2]["derived"]["etaSeconds"] and "frameCount" not in rows[2]["derived"]),
    ("自拼 graph 只说明校验范围，不拦人", rows[3]["blocked"] is False and "graph" in rows[3]["problems"][0]),
    ("全场只有一台实例时允许不指明", rows[4]["blocked"] is False),
    ("总额不含被拦那条的耗时", totals["etaSeconds"] == sum(int(r["derived"].get("etaSeconds") or 0) for r in rows if not r["blocked"])),
    ("标注这是外推不是实测", totals["etaIsEstimate"] is True),
]

# 两台实例又不指明用哪一台：必须拦，猜错实例的代价是白烧十几分钟
multi = jp.plan_jobs([NOINST], instances={**INST, "2": jp.InstanceInfo(id="2", label="云端 48G", placement="cloud_self")}, known_media=set())["rows"][0]
checks.append(("多实例却没指明 → 拦", multi["blocked"] is True))
print("多实例不指明 →", multi["problems"])

slow = jp.plan_jobs([dict(GOOD, slots={**GOOD["slots"], "steps": 24})], instances=INST, known_media={67})["rows"][0]
print("Turbo 档给 24 步 →", slow["problems"])
checks.append(("Turbo 档超 8 步必须提示白多花时间", any("Turbo" in p for p in slow["problems"])))

CHAIN = {"template": "h3_chain", "kind": "video_chain", "title": "长片续拍", "instanceId": "1",
         "slots": {"segments": [{"prompt": "第一段", "seconds": 6}, {"prompt": "第二段", "seconds": 6}], "archive_dir": "h3s-p1", "guide_frames": "12", "turbo": True, "steps": 8}}
chain = jp.plan_jobs([CHAIN], instances=INST, known_media=set())["rows"][0]
print("续拍链（guide_frames 非法 + 链头没起始帧）→", chain["problems"])
checks.append(("guide_frames 不在枚举里必须拦", chain["blocked"] is True and any("引导帧数" in p for p in chain["problems"])))
checks.append(("链头没有起始帧要提示退成 t2v", any("t2v" in p for p in chain["problems"])))
chain_ok = jp.plan_jobs([dict(CHAIN, slots={**CHAIN["slots"], "guide_frames": "22", "first_frame": None})], instances=INST, known_media=set())["rows"][0]
checks.append(("合法档不该被拦", chain_ok["blocked"] is False))
fails = [name for name, ok in checks if not ok]
print("\n" + ("全部通过：%d 项" % len(checks) if not fails else "失败项：%s" % fails))

# 模式形状：选了 hybrid 却提交三段式文本，必须警告
three = dict(GOOD, slots={**GOOD["slots"], "prompt": "integrated_multimodal_description: 画面\noverall_soundscape: 雨\nnon_diegetic_music: N/A"})
shape = jp.plan_jobs([three], instances=INST, known_media={67})["rows"][0]
print("三段式冒充 hybrid →", shape["problems"])
print("媒体库不可查（无库模式）不该乱拦：", jp.plan_jobs([GOOD], instances=INST, known_media=None)["rows"][0]["blocked"])
sys.exit(1 if fails else 0)
