"""文本模型设置接口。

一块页面两种口：scope=local（llama.cpp / Ollama / LM Studio / vLLM）与 scope=cloud
（GitCC 一类 OpenAI 兼容云 API），分组只看 scope 这一个字段，不看地址猜。

三条硬规矩：
  1. apiKey 只进不出 —— 库里 Fernet 密文，读接口只回 apiKeySet。
  2. 能力是探出来的，不是配出来的：ctx_is_per_request 决定「上下文不够」时
     给的是「改启动参数重启」还是「本次请求带 num_ctx」。
  3. /llm/run 同步返回结构化结果。项目数据在浏览器 IndexedDB 里（A 方案），
     解析结果直接回给前端写盘，不绕任务队列。
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import Field
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..crypto import b64, decrypt, encrypt, from_b64
from ..db import get_session
from ..llm import LlmError, LlmSpec, Purpose, chat, list_models, probe, run_purpose, scan_local
from ..logging_setup import get_logger, redact
from ..models import AppSetting, LlmBackend
from ..security import admin_gate, login_gate
from .common import CamelModel

log = get_logger("api.llm")
router = APIRouter(tags=["llm"])

PURPOSES = ("script_parse", "storyboard", "visualize", "h3_prompt", "script_write", "embed")


class LlmBody(CamelModel):
    name: str | None = Field(None, min_length=1, max_length=128)
    scope: Literal["local", "cloud"] | None = None
    kind: Literal["ollama", "openai_compat"] | None = None
    base_url: str | None = None
    api_key: str | None = None
    chat_path: str | None = None
    model: str | None = None
    is_default: bool | None = None


class LlmCreate(LlmBody):
    name: str = Field(..., min_length=1, max_length=128)  # type: ignore[assignment]
    scope: Literal["local", "cloud"]  # type: ignore[assignment]
    kind: Literal["ollama", "openai_compat"]  # type: ignore[assignment]
    base_url: str  # type: ignore[assignment]


class RunBody(CamelModel):
    purpose: Purpose
    input: str = Field(..., min_length=1)
    backend_id: int | None = None
    model: str | None = None
    target_sec: int | None = Field(None, ge=5, le=1800)
    pace: str | None = None
    # 只有 h3_prompt 读这几个：提示词模式 + 本镜的时间预算与画幅风格（不给就退回三段式）
    mode: Literal["three_field", "six_section", "wenwu", "hybrid"] | None = None
    duration_sec: float | None = Field(None, ge=1, le=60)
    aspect: str | None = None
    style: str | None = None


def _to_out(row: LlmBackend) -> dict[str, Any]:
    caps = dict(row.capabilities or {})
    caps["models"] = caps.get("models") or []
    return {
        "id": str(row.id),
        "name": row.name,
        "scope": row.scope,
        "kind": row.kind,
        "baseUrl": redact(row.base_url),
        "apiKeySet": bool(row.api_key_enc),
        "chatPath": row.chat_path,
        "streamStyle": row.stream_style,
        "model": caps.get("model"),
        "caps": caps,
        "isDefault": bool(row.is_default),
        "lastProbeAt": row.last_probe_at.isoformat() if row.last_probe_at else None,
        "lastProbeOk": row.last_probe_ok,
        "lastError": row.last_error,
    }


def _spec(row: LlmBackend) -> LlmSpec:
    caps = row.capabilities or {}
    return LlmSpec(
        base_url=row.base_url,
        kind=row.kind,  # type: ignore[arg-type]
        # 库里存的是「Fernet 密文的 base64 文本」，必须先 from_b64 再 decrypt：
        # 直接把文本 encode 会给 Fernet 喂双层编码，解不出来还不报错，只剩一个 401。
        api_key=decrypt(from_b64(row.api_key_enc)),
        model=caps.get("model"),
        chat_path=row.chat_path,
    )


async def _row(session: AsyncSession, backend_id: str) -> LlmBackend:
    row = await session.get(LlmBackend, int(backend_id)) if backend_id.isdigit() else None
    if row is None:
        raise HTTPException(404, f"文本后端 {backend_id} 不存在")
    return row


@router.get("/llm/backends")
async def list_backends(scope: str | None = None, _: Any = Depends(login_gate), session: AsyncSession = Depends(get_session)) -> list[dict[str, Any]]:
    stmt = select(LlmBackend).order_by(LlmBackend.id)
    if scope:
        stmt = stmt.where(LlmBackend.scope == scope)
    return [_to_out(r) for r in (await session.execute(stmt)).scalars()]


@router.post("/llm/backends", status_code=201)
async def create_backend(body: LlmCreate, request: Request, actor: Any = Depends(admin_gate), session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    if "://" not in body.base_url:
        raise HTTPException(400, "base_url 必须是 http(s):// 开头的完整地址（例：http://127.0.0.1:8080/v1）")
    row = LlmBackend(
        name=body.name,
        scope=body.scope,
        kind=body.kind,
        base_url=body.base_url.rstrip("/"),
        api_key_enc=b64(encrypt(body.api_key)),
        chat_path=body.chat_path or ("/api/chat" if body.kind == "ollama" else "/chat/completions"),
        stream_style="ndjson" if body.kind == "ollama" and not body.base_url.rstrip("/").endswith("/v1") else "sse",
        capabilities={"model": body.model} if body.model else {},
    )
    session.add(row)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise HTTPException(409, f"名称 {body.name!r} 已存在") from exc
    # 第一个后端直接当默认，省掉「配完还要再点一下设为默认」这一步
    if not (await session.scalar(select(AppSetting).where(AppSetting.key == "llm_defaults"))):
        await session.execute(
            text("INSERT INTO app_settings(key, value) VALUES ('llm_defaults', '{}'::jsonb) ON CONFLICT (key) DO NOTHING")
        )
    await session.commit()
    await session.refresh(row)
    log.info("文本后端 %s（%s/%s）已登记，操作人：%s", row.name, row.scope, row.kind, getattr(actor, "username", "-"))
    return _to_out(row)


@router.patch("/llm/backends/{backend_id}")
async def update_backend(backend_id: str, body: LlmBody, request: Request, actor: Any = Depends(admin_gate), session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    row = await _row(session, backend_id)
    given = body.model_fields_set
    for field in ("name", "scope", "kind", "chat_path", "is_default"):
        if field in given and getattr(body, field) is not None:
            setattr(row, field, getattr(body, field))
    if "base_url" in given and body.base_url:
        row.base_url = body.base_url.rstrip("/")
    # apiKey 的判据只有「字段有没有出现」：省略=不动，显式 null=清掉，给值=重新加密
    if "api_key" in given:
        row.api_key_enc = b64(encrypt(body.api_key)) if body.api_key else None
    if "model" in given:
        caps = dict(row.capabilities or {})
        caps["model"] = body.model
        row.capabilities = caps
    await session.commit()
    await session.refresh(row)
    return _to_out(row)


@router.delete("/llm/backends/{backend_id}", status_code=204)
async def delete_backend(backend_id: str, actor: Any = Depends(admin_gate), session: AsyncSession = Depends(get_session)) -> None:
    row = await _row(session, backend_id)
    await session.delete(row)
    await session.commit()


@router.post("/llm/backends/{backend_id}/probe")
async def probe_backend(backend_id: str, actor: Any = Depends(login_gate), session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    row = await _row(session, backend_id)
    caps = await probe(_spec(row))
    merged = caps.as_dict()
    merged["model"] = (row.capabilities or {}).get("model") or (caps.models[0] if caps.models else None)
    row.capabilities = merged
    row.last_probe_ok = caps.reachable
    row.stream_style = caps.stream_style
    await session.execute(text("UPDATE llm_backends SET last_probe_at=now() WHERE id=:i"), {"i": row.id})
    row.last_error = caps.error
    await session.commit()
    await session.refresh(row)
    return _to_out(row)


@router.get("/llm/models")
async def models_of(backend_id: str, _: Any = Depends(login_gate), session: AsyncSession = Depends(get_session)) -> list[str]:
    row = await _row(session, backend_id)
    try:
        return await list_models(_spec(row))
    except LlmError as exc:
        raise HTTPException(400, exc.message) from exc


@router.post("/llm/backends/{backend_id}/pull")
async def pull_model(backend_id: str, body: dict[str, str], _: Any = Depends(admin_gate), session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    row = await _row(session, backend_id)
    spec = _spec(row)
    if not spec.is_ollama_native:
        raise HTTPException(
            400,
            f"{row.kind} 口没有 pull：llama.cpp 请用 --models-dir 指向模型目录并自己下 GGUF"
            "（ModelScope/HF 下载后放进目录重启），云端 OpenAI 兼容口则由服务商提供模型。",
        )
    async with __import__("httpx").AsyncClient(timeout=600.0) as c:
        r = await c.post(f"{spec.root}/api/pull", json={"name": body.get("model", "")})
        r.raise_for_status()
    return {"ok": True}


@router.get("/llm/defaults")
async def get_defaults(_: Any = Depends(login_gate), session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    row = await session.get(AppSetting, "llm_defaults")
    return (row.value if row else {}) or {}


@router.put("/llm/defaults")
async def put_defaults(body: dict[str, Any], actor: Any = Depends(admin_gate), session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    for purpose, item in body.items():
        if purpose not in PURPOSES:
            raise HTTPException(400, f"不认识的用途 {purpose}，可选：{'、'.join(PURPOSES)}")
        if not isinstance(item, dict) or "backendId" not in item:
            raise HTTPException(400, f"{purpose} 需要 {{backendId, model}}")
    current = await session.get(AppSetting, "llm_defaults")
    merged = dict((current.value if current else {}) or {})
    merged.update(body)
    if current is None:
        session.add(AppSetting(key="llm_defaults", value=merged))
    else:
        current.value = merged
    await session.commit()
    return merged


@router.post("/llm/backends/{backend_id}/default")
async def set_default(backend_id: str, purpose: str | None = None, actor: Any = Depends(admin_gate), session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    row = await _row(session, backend_id)
    if purpose:
        current = await session.get(AppSetting, "llm_defaults")
        merged = dict((current.value if current else {}) or {})
        merged[purpose] = {"backendId": str(row.id), "model": (row.capabilities or {}).get("model")}
        if current is None:
            session.add(AppSetting(key="llm_defaults", value=merged))
        else:
            current.value = merged
    else:
        await session.execute(text("UPDATE llm_backends SET is_default=false"))
        row.is_default = True
    await session.commit()
    return {"ok": True}


@router.post("/llm/run")
async def run(body: RunBody, request: Request, _: Any = Depends(login_gate), session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    """同步跑一个用途。结果回给前端直接写进 IndexedDB 的项目里。"""
    row = await _row(session, str(body.backend_id)) if body.backend_id else None
    if row is None:
        setting = await session.get(AppSetting, "llm_defaults")
        pick = ((setting.value if setting else {}) or {}).get(body.purpose)
        if pick:
            row = await _row(session, str(pick.get("backendId")))
        else:
            row = (await session.execute(select(LlmBackend).where(LlmBackend.is_default == True).limit(1))).scalars().first()  # noqa: E712
    if row is None:
        raise HTTPException(400, "还没有可用的文本模型：先去 设置 → AI 模型 加一个，或给某个用途指定默认后端")

    dispatcher = getattr(request.app.state, "dispatcher", None)
    # 本机一张卡：图/视频正在算的时候调文本模型，两边会互相把权重挤出显存
    # （实测一次带参考图的出图因此 15 分钟没跑完，ComfyUI 连 HTTP 都不响应）。
    if dispatcher is not None and getattr(dispatcher, "local_render_active", lambda: False)():
        raise HTTPException(
            409,
            "本机 GPU 正在出图/出片，这时调文本模型两边都会慢到不可用。等这条任务结束再试（队列页能看到进度）。",
        )

    # 反过来也一样：文本模型开跑前让本机 ComfyUI 先把权重卸掉，给它腾出显存
    await _free_local_comfyui(request)

    payload = {
        "input": body.input,
        "targetSec": body.target_sec,
        "pace": body.pace,
        "mode": body.mode,
        "durationSec": body.duration_sec,
        "aspect": body.aspect,
        "style": body.style,
    }
    try:
        if dispatcher is None:
            return await run_purpose(_spec(row), body.purpose, payload, model=body.model)
        async with dispatcher.gpu_gate():
            return await run_purpose(_spec(row), body.purpose, payload, model=body.model)
    except LlmError as exc:
        raise HTTPException(400, exc.message) from exc
    except Exception as exc:
        log.exception("llm/run 失败")
        raise HTTPException(502, f"调用 {row.name} 失败：{type(exc).__name__}: {redact(str(exc))[:200]}") from exc


async def _free_local_comfyui(request: Request) -> None:
    """让本机 ComfyUI 卸掉权重腾显存。失败不打断文本调用 —— 它只是让事情快一点。"""
    registry = getattr(request.app.state, "registry", None)
    if registry is None:
        return
    for iid in registry.ids:
        try:
            cfg = registry.config(iid)
            if cfg.placement != "local" or cfg.protocol != "comfy_native":
                continue
            await registry.client(iid).free()
        except Exception as exc:
            log.debug("让 ComfyUI %s 释放显存失败（忽略）：%s", iid, str(exc)[:120])


@router.get("/llm/local-scan")
async def local_scan(_: Any = Depends(login_gate)) -> dict[str, Any]:
    return await scan_local()

