"""文本模型层。

两条口分开实现，不合并成一个「通用客户端」：
  · openai_compat —— llama.cpp(-l) / vLLM / LM Studio / GitCC / 各家云 API
  · ollama        —— Ollama 原生 /api/chat，NDJSON 而不是 SSE，模型清单在 /api/tags

两个必须让调用方看得见的差别（本机实测）：
  1. llama-server 的上下文是 **启动参数** `-c 131072`，请求里给 `num_ctx` 不会生效
     → 能力位 ctx_is_per_request=False，设置页要显示「实际上下文 131072，改它得重启服务」，
        而不是悄悄截断剧本。
  2. 结构化输出：这台 llama-server 的 response_format=json_schema + strict 是真的守约
     （实测返回合法 JSON），所以剧本拆解/分镜可以直接吃 schema；探测不支持时才退回
     「让模型输出 JSON 文本 + 我们容错解析」。

密钥只从 llm_backends.api_key_enc 解密拿到，绝不写进日志（logging_setup.redact 兜底）。
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Literal

from .director import (
    METHODOLOGY,
    METHODOLOGY_HEADER,
    MODES,
    SHOT_SIZES,
    UI_CAMERA_MOVES,
    h3_schema,
    h3_user_prompt,
    mode_or_default,
    storyboard_hint,
    validate_h3_output,
    validate_storyboard,
)
from .logging_setup import get_logger, redact
from .net import async_client, is_local_target

log = get_logger("llm")

LlmKind = Literal["ollama", "openai_compat"]
Purpose = Literal["script_parse", "storyboard", "visualize", "h3_prompt", "script_write", "script_chat", "embed"]

# 单次喂给模型的剧本上限。128k 上下文要留给输出与模板，别把整本书塞进去。
MAX_INPUT_CHARS = 24_000
#: script_chat 带历史，喂进去的 token 比单轮多得多，历史只留最近这几轮
CHAT_HISTORY_TURNS = 10


class LlmError(Exception):
    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.message = message
        self.retryable = retryable

    def as_dict(self) -> dict[str, Any]:
        return {"type": "llm_error", "message": self.message, "retryable": self.retryable}


def _empty_reply_hint(reasoning: str, finish: Any, max_tokens: int) -> str:
    """正文为空的真实原因有三种，以前只报一句"max_tokens 太小"，把最需要说的那种藏了。"""
    n = len(reasoning.strip())
    if n:
        why = finish or "未知"
        return (
            f"模型只输出了 {n} 字的思考过程，正文是空的（finish_reason={why}，本轮预算 {max_tokens} token）。"
            "带思考的本机模型会把预算全花在思考链上 —— 要结构化输出的调用已自动关思考，"
            "若是自由改写用途，可把该后端的输出预算调大。"
        )
    if finish == "length":
        return f"回答被截断在 {max_tokens} token，正文没写完就说结束了。把该后端的输出预算调大再试。"
    return "模型返回了空内容（没吐思考、也没吐正文）。先探活这台后端，确认它真的载入了模型。"


@dataclass
class LlmSpec:
    """一次调用需要的东西：地址、口型、密钥、模型名。"""

    base_url: str
    kind: LlmKind = "openai_compat"
    api_key: str | None = None
    model: str | None = None
    chat_path: str = "/chat/completions"
    timeout_s: float = 300.0

    @property
    def root(self) -> str:
        return self.base_url.rstrip("/")

    @property
    def is_ollama_native(self) -> bool:
        # Ollama 的 /v1 口也是 OpenAI 兼容的；只有裸口才是 NDJSON
        return self.kind == "ollama" and not self.root.endswith("/v1")


@dataclass
class LlmCaps:
    reachable: bool = False
    kind: str = "openai_compat"
    models: list[str] = field(default_factory=list)
    ctx_total: int | None = None
    slots: int | None = None
    ctx_is_per_request: bool = False
    has_json_schema: bool = False
    has_vision: bool = False
    has_tools: bool = False
    stream_style: str = "sse"
    error: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "reachable": self.reachable,
            "kind": self.kind,
            "models": self.models,
            "ctxTotal": self.ctx_total,
            "slots": self.slots,
            "ctxIsPerRequest": self.ctx_is_per_request,
            "hasJsonSchema": self.has_json_schema,
            "hasVision": self.has_vision,
            "hasTools": self.has_tools,
            "streamStyle": self.stream_style,
            "error": self.error,
        }


@dataclass
class ChatResult:
    text: str
    usage: dict[str, Any] = field(default_factory=dict)
    latency_ms: int = 0
    model: str | None = None


def _headers(spec: LlmSpec) -> dict[str, str]:
    h = {"Content-Type": "application/json"}
    if spec.api_key:
        h["Authorization"] = f"Bearer {spec.api_key}"
    return h


async def _get_root(spec: LlmSpec, path: str, timeout: float = 12.0) -> Any:
    """打根路径的端点（/props、/api/tags）：base_url 可能带 /v1 前缀，得先剥掉。"""
    root = spec.root
    if root.endswith("/v1"):
        root = root[: -len("/v1")]
    async with async_client(f"{root}{path}", timeout=timeout) as c:
        r = await c.get(f"{root}{path}", headers=_headers(spec))
        r.raise_for_status()
        return r.json()


async def _get(spec: LlmSpec, path: str, timeout: float = 12.0) -> Any:
    async with async_client(f"{spec.root}{path}", timeout=timeout) as c:
        r = await c.get(f"{spec.root}{path}", headers=_headers(spec))
        r.raise_for_status()
        return r.json()


async def list_models(spec: LlmSpec) -> list[str]:
    try:
        if spec.is_ollama_native:
            data = await _get(spec, "/api/tags")
            return [m.get("name", "") for m in data.get("models", []) if m.get("name")]
        data = await _get(spec, "/models")
        rows = data.get("data") or data.get("models") or []
        return [m.get("id") or m.get("name") for m in rows if (m.get("id") or m.get("name"))]
    except Exception as exc:
        raise LlmError(f"读模型清单失败：{type(exc).__name__}: {str(exc)[:160]}") from exc


async def probe(spec: LlmSpec) -> LlmCaps:
    """探一次能力。设置页的绿点、以及「能不能按请求给上下文」都从这里来。"""
    caps = LlmCaps(kind=spec.kind, stream_style="ndjson" if spec.is_ollama_native else "sse")
    try:
        caps.models = await list_models(spec)
        caps.reachable = True
    except Exception as exc:
        caps.error = redact(str(exc))[:200] or type(exc).__name__
        return caps

    if not spec.is_ollama_native:
        # llama.cpp 的 /props 在**根路径**上，不在 /v1 下面：写成 /v1/props 会 404，
        # 于是 n_ctx/槽位/视觉能力全部读不到，还会把「上下文不可按请求改」误判成可改。
        try:
            props = await _get_root(spec, "/props")
            dgs = props.get("default_generation_settings") or {}
            caps.ctx_total = dgs.get("n_ctx") or props.get("n_ctx")
            caps.slots = props.get("total_slots")
            modal = props.get("modalities") or {}
            caps.has_vision = bool(modal.get("vision"))
            caps.has_tools = bool((props.get("chat_template_caps") or {}).get("supports_parallel_tool_calls"))
            # -c 是启动参数：能读到 /props 就说明这台是 llama.cpp，上下文不可按请求调
            caps.ctx_is_per_request = False
            caps.raw = {"model_alias": props.get("model_alias"), "model_path": props.get("model_path")}
        except Exception:
            caps.ctx_is_per_request = True  # 不是 llama.cpp：多半是 vLLM/云，上下文随请求走
    else:
        caps.ctx_is_per_request = True  # Ollama 的 options.num_ctx 每次请求都吃
        try:
            stats = await _get_root(spec, "/api/ps")
            for m in stats.get("models", []):
                caps.ctx_total = (m.get("parameters") or {}).get("num_ctx") or caps.ctx_total
        except Exception:
            pass

    caps.has_json_schema = await _probe_json_schema(spec)
    return caps


async def _probe_json_schema(spec: LlmSpec) -> bool:
    """真的问一次，而不是看它声不支持：strict 违约的模型一大把。"""
    try:
        res = await chat(
            spec,
            [{"role": "user", "content": "输出 ok"}],
            schema={"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"], "additionalProperties": False},
            schema_name="probe",
            max_tokens=32,
            temperature=0,
        )
        json.loads(res.text)
        return True
    except Exception:
        return False


async def chat(
    spec: LlmSpec,
    messages: list[dict[str, Any]],
    *,
    model: str | None = None,
    schema: dict[str, Any] | None = None,
    schema_name: str = "result",
    max_tokens: int = 2048,
    temperature: float = 0.4,
) -> ChatResult:
    used = model or spec.model or (await list_models(spec))[0] if not (model or spec.model) else (model or spec.model)
    t0 = time.monotonic()
    reasoning: str = ""
    finish: Any = None
    body: dict[str, Any] = {"messages": messages, "max_tokens": max_tokens, "temperature": temperature, "stream": False}
    if schema:
        body["response_format"] = {"type": "json_schema", "json_schema": {"name": schema_name, "strict": True, "schema": schema}}

    async with async_client(spec.root, timeout=spec.timeout_s) as c:
        if spec.is_ollama_native:
            body.update({"model": used, "format": schema if schema else "json"} if schema else {"model": used})
            r = await c.post(f"{spec.root}/api/chat", headers=_headers(spec), json={"model": used, "messages": messages, "stream": False, "options": {"temperature": temperature, "num_predict": max_tokens, **({"format": schema} if schema else {})}})
            r.raise_for_status()
            data = r.json()
            text = (data.get("message") or {}).get("content", "")
            usage = {"prompt_tokens": data.get("prompt_eval_count"), "completion_tokens": data.get("eval_count")}
        else:
            body["model"] = used
            if schema and is_local_target(spec.root):
                # 要机器可解析的结构化输出时，别让思考链把整段 max_tokens 吃光：
                # 本机 Qwen3.8-27B 实测会把 6000 预算全花在 reasoning_content 上，content 空着回来
                body["reasoning_effort"] = "none"
            path = spec.chat_path if spec.chat_path.startswith("/") else f"/{spec.chat_path}"
            base = spec.root if spec.root.endswith("/v1") else f"{spec.root}/v1"
            r = await c.post(f"{base}{path}", headers=_headers(spec), json=body)
            if r.status_code >= 400:
                raise LlmError(f"{r.status_code} {redact(r.text)[:300]}", retryable=r.status_code in (408, 429, 500, 502, 503, 504))
            r.raise_for_status()
            data = r.json()
            choices = data.get("choices") or [{}]
            msg = choices[0].get("message") or {}
            text = msg.get("content") or ""
            reasoning = msg.get("reasoning_content") or ""
            finish = choices[0].get("finish_reason")
            usage = data.get("usage") or {}

    if not text.strip():
        raise LlmError(_empty_reply_hint(reasoning, finish, max_tokens))
    return ChatResult(text=text.strip(), usage=usage, latency_ms=int((time.monotonic() - t0) * 1000), model=used)


# ───────── 用途：剧本拆解 / 分镜规划 / 视觉化翻译 / H3 提示词（四模式，见 director.py）─────────

_SCHEMAS: dict[str, dict[str, Any]] = {
    "script_parse": {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "logline": {"type": "string", "description": "一句话故事，不超过 40 字"},
            "genre": {"type": "array", "items": {"type": "string"}},
            "characters": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "desc": {"type": "string", "description": "外形与气质，够画出来：年龄/体型/发型/服装/主色/标志物"},
                        "traits": {
                            "type": "object",
                            "properties": {
                                "age": {"type": "string"},
                                "build": {"type": "string"},
                                "hair": {"type": "string"},
                                "costume": {"type": "string"},
                                "palette": {"type": "string"},
                                "signature": {"type": "string"},
                            },
                            "required": ["age", "build", "hair", "costume", "palette", "signature"],
                            "additionalProperties": False,
                        },
                    },
                    "required": ["name", "desc", "traits"],
                    "additionalProperties": False,
                },
            },
            "scenes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}, "desc": {"type": "string", "description": "环境、光线、时段、氛围"}},
                    "required": ["name", "desc"],
                    "additionalProperties": False,
                },
            },
            "beats": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"text": {"type": "string"}, "sceneName": {"type": "string"}},
                    "required": ["text", "sceneName"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["title", "logline", "genre", "characters", "scenes", "beats"],
        "additionalProperties": False,
    },
    "storyboard": {
        "type": "object",
        "properties": {
            "shots": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "index": {"type": "integer"},
                        "sceneName": {"type": "string"},
                        "characterNames": {"type": "array", "items": {"type": "string"}},
                        "action": {
                            "type": "string",
                            "description": "这一镜里发生的唯一事件，一个动作一句话；顺带写清主体可见变化，以及用什么把注意力交给下一镜（动作方向/视线/光线/道具/轮廓/声音/受力）",
                        },
                        "dialogue": {"type": "string"},
                        "visualPrompt": {"type": "string", "description": "画面提示词：构图/景别/机位/光线/色温/运动"},
                        "durationSec": {"type": "number"},
                        "cameraMovement": {"type": "string", "enum": list(UI_CAMERA_MOVES)},
                        "shotSize": {"type": "string", "enum": list(SHOT_SIZES)},
                        "continuityAnchor": {
                            "type": "string",
                            "description": "本镜从上一镜接住什么：动作方向 / 视线目标 / 同一道光 / 同一个道具 / 同一种轮廓 / 同一段声音 / 同一股受力，写成一句可见的话；第一镜写 N/A",
                        },
                    },
                    "required": ["index", "sceneName", "characterNames", "action", "dialogue", "visualPrompt", "durationSec", "cameraMovement", "shotSize", "continuityAnchor"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["shots"],
        "additionalProperties": False,
    },
    "visualize": {
        "type": "object",
        "properties": {"visualPrompt": {"type": "string"}, "negative": {"type": "string"}},
        "required": ["visualPrompt", "negative"],
        "additionalProperties": False,
    },
    "script_write": {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "续写或改写后的剧本正文，沿用输入里的格式、人物称呼与语气"}
        },
        "required": ["text"],
        "additionalProperties": False,
    },
    # 多轮对话改稿：reply 给人看，scriptText 是「确认后整篇替换编辑器」的那一份
    "script_chat": {
        "type": "object",
        "properties": {
            "reply": {"type": "string", "description": "一两句中文，说清这一轮改了什么、为什么；不超过 80 字，不要复述剧情"},
            "scriptText": {
                "type": "string",
                "description": "改后的完整剧本正文，未改动的部分一字不差带上；这一轮不需要动稿本时返回空串",
            },
        },
        "required": ["reply", "scriptText"],
        "additionalProperties": False,
    },
    # h3_prompt 的 schema 跟着提示词模式走，不在这里写死：见 director.h3_schema
}

_SYSTEM_BASE = (
    "你是短剧/漫剧的制作助手，把中文故事整理成可直接投产的结构。"
    "只输出符合给定 JSON schema 的对象，不要解释、不要代码围栏。"
    "角色外形必须具体到能画出来（年龄、体型、发型、服装、主色调、标志物），"
    "场景描述必须含环境/光线/时段。所有文字用简体中文。"
)

#: 哪些用途需要导演方法论。方法论有 3000 多字（约 2–3k token 预填充），
#: 只在这两个用途上注入：script_parse / visualize / script_write 用不着镜头语言，
#: 白白多等十几秒预填充。实测耗时记在 PLAN.md §11.1。
_METHODOLOGY_PURPOSES = ("storyboard", "h3_prompt")

_PROMPTS: dict[str, str] = {
    "script_parse": "把下面的故事大纲或正文拆成剧本结构：标题、一句话故事、类型、角色（含外形）、场景、节拍。节拍按叙事顺序给，标上它发生在哪个场景。\n\n---\n{input}",
    "storyboard": (
        "根据这份剧本结构生成分镜镜头表。目标总时长约 {target} 秒，节奏按「{pace}」掌握。\n"
        "镜头规模与每镜时长：{density}\n"
        "每镜给：动作（这一镜的唯一事件）、台词（没有就空串）、画面提示词、时长秒、运镜、景别、"
        "接住上一镜的锚点（动作方向/视线/同一道光/同一道具/同一轮廓/同一段声音/同一股受力，写成一句可见的话；首镜写 N/A）。"
        "时长秒累加等于目标时长；每镜都要有独立的观察任务，不许把同一动作换景别重复描述，也不许无理由跳切。"
        "角色名与场景名必须来自给定列表，不要发明新角色。\n\n---\n{input}"
    ),
    "script_write": "第一行是要求（例如「接着往下写，约 300 字」或「把这段改得更克制」），其余是剧本正文。按要求只输出剧本正文本身：沿用已有的场次标题格式（【第N幕】场景-时间）、动作行用全角括号包裹、台词写成「角色（神态）：」换行后接台词；不要解释、不要代码围栏、不要加 markdown 标题。\n\n---\n{input}",
    "visualize": "把下面这段中文描述翻译成适合图像生成模型的画面提示词：写主体、动作、构图与景别、光线、色调、材质与氛围；不要写「很美」「震撼」这类评价词，不要否定句。\n\n---\n{input}",
}

#: 多轮改稿的规矩。和 script_write 的区别是这里带对话历史，且必须回吐整篇正文好让前端做差异预览
_SCRIPT_CHAT_RULES = """你是这部短剧的编剧助手，正在和创作者一起改这一份稿子。
【当前剧本正文】是唯一的稿本依据：场次标题格式（【第N幕】场景-时间）、动作行用全角括号包裹、
「角色（神态）：」换行后接台词的写法、人称、人物称呼与语气一律沿用，不许另起一套格式。
对话历史里往轮的改稿已经落进这份正文，不要重复执行往轮的指令。
reply：用一两句中文说清这一轮改了什么、为什么这么改，不超过 80 字，不要复述剧情、不要客套。
scriptText：改后的【完整】正文，从第一行到最后一行，没有改动的部分一字不差带上；
这一轮只是问答、不需要动稿本时返回空串。
绝不允许只返回被改的那一段，也不许用「（其余不变）」这类占位代替没改的部分。"""


def _script_chat_system(script: str) -> str:
    return f"{_SCRIPT_CHAT_RULES}\n\n【当前剧本正文】\n{script.strip() or '（还没有正文，按创作者这一轮的要求新写）'}"


def _system_for(purpose: str, mode: str) -> str:
    """拼这次调用的 system。导演方法论只喂给分镜与 H3 提示词两个用途。"""
    if purpose not in _METHODOLOGY_PURPOSES:
        return _SYSTEM_BASE
    parts = [_SYSTEM_BASE, METHODOLOGY_HEADER, METHODOLOGY]
    if purpose == "h3_prompt":
        parts.append(f"本次输出模式：{MODES[mode].name}（{MODES[mode].language}）。{MODES[mode].when}。")
    return "\n\n".join(parts)


def _loads(text: str) -> dict[str, Any]:
    """模型偶尔在 JSON 外面裹一层围栏或前后废话，容错一次；再不行就如实报错。"""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if fenced:
        return json.loads(fenced.group(1))
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        return json.loads(text[start : end + 1])
    raise LlmError(f"模型没有按 schema 输出 JSON，开头是：{text[:120]!r}")


async def run_purpose(spec: LlmSpec, purpose: Purpose, payload: dict[str, Any], *, model: str | None = None) -> dict[str, Any]:
    """跑一个用途，返回结构化 dict。前端拿到就直接写进 IndexedDB。

    h3_prompt 的 schema、system、预算都跟着 payload 的 mode 走；其它用途不受影响。
    script_chat 是唯一带多轮 messages 的用途，正文走 payload.script 而不是 input。
    """
    is_h3 = purpose == "h3_prompt"
    if not is_h3 and purpose not in _SCHEMAS:
        raise LlmError(f"不认识的用途：{purpose}")
    text = payload.get("input") or ""
    if len(text) > MAX_INPUT_CHARS:
        raise LlmError(f"输入 {len(text)} 字超过单次上限 {MAX_INPUT_CHARS} 字，请先分块")
    if not text.strip():
        raise LlmError("输入是空的")

    target = payload.get("targetSec") or 60
    mode = mode_or_default(payload.get("mode")) if is_h3 else "three_field"
    if is_h3:
        spec_mode = MODES[mode]
        prompt = h3_user_prompt(
            mode,
            brief=text,
            seconds=float(payload.get("durationSec") or target),
            aspect=payload.get("aspect") or "16:9",
            style=payload.get("style") or "",
        )
        schema = h3_schema(mode)
        msgs = [{"role": "system", "content": _system_for(purpose, mode)}, {"role": "user", "content": prompt}]
        max_tokens = spec_mode.max_tokens
        temperature = 0.7
    elif purpose == "script_chat":
        script = str(payload.get("script") or "")
        if len(script) > MAX_INPUT_CHARS:
            raise LlmError(
                f"当前剧本 {len(script)} 字，超过单次上限 {MAX_INPUT_CHARS} 字。"
                "改稿要整篇回吐，超限只会拿到一份被截断的正文 —— 请先分场分段改，或把稿子压短。"
            )
        turns = [
            {"role": str(m.get("role")), "content": str(m.get("content") or "").strip()}
            for m in (payload.get("messages") or [])
            if str(m.get("role")) in ("user", "assistant") and str(m.get("content") or "").strip()
        ][-CHAT_HISTORY_TURNS:]
        msgs = [{"role": "system", "content": _script_chat_system(script)}, *turns, {"role": "user", "content": text}]
        schema = _SCHEMAS[purpose]
        # scriptText 是整篇正文，输出预算按稿子长度推；按单轮问答的 2500 给只会截断成半份稿
        max_tokens = min(30_000, max(2_500, int(len(script) * 1.2) + 1_500))
        temperature = 0.7
    else:
        prompt = _PROMPTS[purpose].format(input=text, target=target, pace=payload.get("pace") or "均匀", density=storyboard_hint(float(target)))
        msgs = [{"role": "system", "content": _system_for(purpose, mode)}, {"role": "user", "content": prompt}]
        schema = _SCHEMAS[purpose]
        max_tokens = {"script_parse": 4096, "storyboard": 6000, "visualize": 1024, "script_write": 2500}[purpose]
        temperature = 0.85 if purpose == "script_write" else 0.35 if purpose in {"script_parse", "storyboard"} else 0.7

    # 技能是这次额外指定的写法要求，贴在 system 尾巴上：四个用途共用这一条路。
    # 不放进 user 那段 —— 那里是待处理的稿本/素材描述，混进去模型会把它当正文一起改写。
    block = str(payload.get("skillsBlock") or "").strip()
    if block:
        msgs = [
            {**m, "content": f"{m['content']}\n\n{block}"} if i == 0 and m.get("role") == "system" else m
            for i, m in enumerate(msgs)
        ]

    async def ask(extra: list[dict[str, Any]] | None = None, temp: float | None = None):
        r = await chat(
            spec,
            [*msgs, *(extra or [])],
            model=model,
            schema=schema,
            schema_name=purpose,
            max_tokens=max_tokens,
            temperature=temperature if temp is None else temp,
        )
        return r, _loads(r.text)

    res, data = await ask()

    warnings: list[str] = []
    if is_h3:
        problems = validate_h3_output(mode, data)
        if problems:
            raise LlmError(
                f"{MODES[mode].name} 回来的结构不合格：" + "；".join(problems)
                + f"。这次花了 {res.latency_ms / 1000:.0f} 秒，白跑：可以换三段式，或把镜头卡里的动作与时长写得更具体再试。"
            )
    elif purpose == "storyboard":
        if not (data.get("shots") or []):
            # `{"shots": []}` 是"合法但没用"：schema 守住了，镜头表是空的，本机小模型偶发会这样。
            # 当成功返回的话，前端只会看到"0 个镜头"，而这一轮的等待与显存已经白花了。
            log.warning("storyboard 回了空镜头表，追加一句纠正再问一次")
            res, data = await ask(
                [{"role": "user", "content": "上一轮 shots 是空数组。这次必须直接给出至少 3 个镜头的 JSON，不要解释。"}],
                temp=min(0.9, temperature + 0.15),
            )
        if not (data.get("shots") or []):
            raise LlmError(
                f"这台模型连着两次只回空镜头表（shots: []，第二次花了 {res.latency_ms / 1000:.0f} 秒）。"
                "换提示词模式对此环节没用，请换一个更守 JSON 约定的文本后端，或把目标时长调大一些再试。",
                retryable=True,
            )
        warnings = validate_storyboard(data.get("shots") or [], float(target))
    elif purpose == "script_chat":
        # 模型最常见的违约是只回被改的那一段。整篇写回会把没改的段落一起抹掉，所以先量一下体量
        new = str(data.get("scriptText") or "")
        if new.strip() and len(new) < len(script) * 0.5:
            warnings.append(
                f"改稿只有 {len(new)} 字，比当前正文 {len(script)} 字短一半以上："
                "多半是模型只回了被改的那一段。展开对比逐段看过再确认写回，否则没改的段落会被一起抹掉。"
            )

    log.info("llm %s(%s) 完成：%dms，%s tokens", purpose, mode, res.latency_ms, res.usage.get("total_tokens", "?"))
    out: dict[str, Any] = {"purpose": purpose, "data": data, "model": res.model, "latencyMs": res.latency_ms, "usage": res.usage}
    if is_h3:
        out["mode"] = mode
    if warnings:
        out["warnings"] = warnings
    return out


# ───────── 本地可用连接扫描 ─────────

_CANDIDATES = (
    (11434, "ollama"),
    (8080, "llamacpp"),
    (1234, "lmstudio"),
    (8000, "vllm"),
)


async def _one_scan(port: int, hint: str) -> dict[str, Any] | None:
    spec = LlmSpec(base_url=f"http://127.0.0.1:{port}/v1", kind="openai_compat")
    try:
        async with async_client(f"http://127.0.0.1:{port}/v1/models", timeout=4.0) as c:
            r = await c.get(f"http://127.0.0.1:{port}/v1/models")
            if r.status_code == 401:
                # 有服务但要 key：如实报，别把它当成「没装」
                return {"port": port, "detectedAs": hint, "baseUrl": f"http://127.0.0.1:{port}/v1", "health": "needs_key", "modelsN": 0}
            r.raise_for_status()
            data = r.json()
        rows = data.get("data") or data.get("models") or []
        names = [m.get("id") or m.get("name") for m in rows if (m.get("id") or m.get("name"))]
        detected = hint
        try:
            props = (await _get_root(spec, "/props")) if port == 8080 else {}
            if props.get("total_slots") is not None:
                detected = "llamacpp"
        except Exception:
            pass
        return {"port": port, "detectedAs": detected, "baseUrl": f"http://127.0.0.1:{port}/v1", "health": "ok", "modelsN": len(names), "models": names[:8]}
    except Exception:
        return None


async def scan_local() -> dict[str, Any]:
    """并发探四个常见端口。找不到就给各家启动命令，而不是留一片空白。"""
    found = [x for x in await asyncio.gather(*(_one_scan(p, h) for p, h in _CANDIDATES)) if x]
    hints = []
    if not found:
        hints = [
            "Ollama：ollama serve（默认 11434），再 ollama pull qwen2.5:14b",
            "llama.cpp：llama-server -m 模型.gguf --host 127.0.0.1 --port 8080 -c 32768 --api-key sk-xxx",
            "LM Studio：开 Developer 模式并 Enable Local Server（默认 1234）",
            "vLLM：python -m vllm.entrypoints.openai.api_server --model 名称 --port 8000",
        ]
    return {"found": found, "hints": hints}
