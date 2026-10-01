"""用假的 chat 传输跑真代码：验证 schema 选择、system 拼装、校验与报错文案。

不占显存：llama-server 没起，ComfyUI 正卡着 24GB，这一步只证明管道本身通。
"""

import asyncio
import json
import sys

sys.path.insert(0, ".")

from app import llm  # noqa: E402

seen: dict = {}

GOOD = {
    "integrated": "林晚收伞坐下，镜头缓推",
    "soundscape": "雨声、远处电车铃",
    "music": "N/A",
    "subjectDefinitions": "<Subject 1> 林晚：25岁，黑色短发",
    "summary": "6 秒一镜：等待→确认→落空。",
    "retentionAnalysis": "identity fully_preserved from <Picture 1>",
    "detailedDescription": "[Shot 1] 0.00-6.00s. 中景, 推近. 她收伞坐下. 呼吸停半拍. 摄影机缓推确认她的表情.",
    "constraints": "photorealistic, no subtitles, no extra characters",
    "sceneDescription": "本片 6 秒，画幅 16:9。生命核：林晚。<Picture 1> 是起始帧。",
    "shotBlocks": ["镜头1（0-6s）：中景推近\n    摄像机状态：缓推\n    画面内容：她收伞坐下\n    音频：雨声\n    人物台词：无"],
}


def required_of(schema: dict) -> dict:
    out: dict = {}
    for k in schema["required"]:
        if k in GOOD:
            out[k] = GOOD[k]
        else:
            out[k] = [] if schema["properties"][k]["type"] == "array" else "占位"
    return out


async def fake_chat(spec, messages, **kw):
    seen["system"] = messages[0]["content"]
    seen["user"] = messages[1]["content"]
    seen["max_tokens"] = kw.get("max_tokens")
    seen["schema_required"] = kw["schema"]["required"]
    payload = {"shots": GOOD_SHOTS} if kw["schema_name"] == "storyboard" else required_of(kw["schema"])
    return llm.ChatResult(text=json.dumps(payload, ensure_ascii=False), usage={"total_tokens": 42}, model="stub")


GOOD_SHOTS = [
    {"index": i, "sceneName": "3号车站", "characterNames": ["林晚"], "action": f"第{i}镜事件", "dialogue": "",
     "visualPrompt": "中景，冷青", "durationSec": 8, "cameraMovement": "推近", "shotSize": "中景"}
    for i in (1, 2, 3)
]

llm.chat = fake_chat
spec = llm.LlmSpec(base_url="http://stub/v1")

for mode in ("three_field", "six_section", "wenwu", "hybrid"):
    res = asyncio.run(llm.run_purpose(spec, "h3_prompt", {"input": "镜头卡：中景推近", "mode": mode, "durationSec": 6, "aspect": "16:9", "style": "live-action"}))
    print(f"[{mode}] 段数={len(res['data'])} max_tokens={seen['max_tokens']} system含方法论={'生命核' in seen['system']} 返回mode={res['mode']}")
    print(f"    user 里时间预算：{[l for l in seen['user'].splitlines() if '时间预算' in l or '画幅' in l][:1]}")

# 缺 constraints 的 hybrid 必须报错，而不是把残缺提示词交给用户去烧显存
async def bad_chat(spec2, messages, **kw):
    payload = {k: GOOD[k] for k in kw["schema"]["required"] if k != "constraints"}
    return llm.ChatResult(text=json.dumps(payload, ensure_ascii=False), usage={}, model="stub")


llm.chat = bad_chat
try:
    asyncio.run(llm.run_purpose(spec, "h3_prompt", {"input": "x", "mode": "hybrid"}))
    print("[失败] 该报错却没报错")
except llm.LlmError as e:
    print("[hybrid 缺段] 报错文案：", e.message[:120])

llm.chat = fake_chat
res = asyncio.run(llm.run_purpose(spec, "storyboard", {"input": "剧本", "targetSec": 60}))
print("[storyboard] 镜数=", len(res["data"]["shots"]), "警告=", res.get("warnings"))
print("    prompt 里的规模句：", [l for l in seen["user"].splitlines() if "镜头规模" in l][0])
res2 = asyncio.run(llm.run_purpose(spec, "storyboard", {"input": "剧本", "targetSec": 12}))
print("[storyboard 12s] 警告=", res2.get("warnings"))
res3 = asyncio.run(llm.run_purpose(spec, "script_parse", {"input": "剧本"}))
print("[script_parse] system 不含方法论 =", "生命核" not in seen["system"], "max_tokens=", seen["max_tokens"])

# 真机回归：这台 27B 实测会把 shotBlocks 出成「每行一个元素」，拼装按行 join 等价，不能判失败；
# hybrid 的 constraints 实测会崩成几十条并禁掉自己用过的运镜，必须拦下来。
from app.director import validate_h3_output  # noqa: E402

flat = {
    "integrated": "主旨", "soundscape": "环境声", "music": "N/A", "sceneDescription": "本片 6 秒…",
    "shotBlocks": ["镜头1（0-2.5s）：中景·慢推", "    摄像机状态：缓推", "    画面内容：收伞坐下", "    音频：布料摩擦", "    人物台词：无", "镜头2（2.5-6s）：近景", "    画面内容：视线落向铁轨", "    人物台词：“还是没来。”"],
}
print("[真机形状 wenwu] 应通过 →", validate_h3_output("wenwu", flat) or "通过")
print("[真机形状 wenwu] 散文应被抓 →", bool(validate_h3_output("wenwu", dict(flat, shotBlocks=["一段没有编号也没有秒数的散文"]))))
runaway = "photorealistic grain, " + ", ".join(f"no item {i}" for i in range(40))
hybrid_ok = {**GOOD, "mode": "hybrid", "constraints": "photorealistic 35mm grain, no readable text, no subtitles, no identity drift"}
hybrid_bad = {**GOOD, "constraints": runaway}
print("[真机形状 hybrid] 5–10 条应通过 →", validate_h3_output("hybrid", hybrid_ok) or "通过")
print("[真机形状 hybrid] 41 条应被抓 →", validate_h3_output("hybrid", hybrid_bad))
