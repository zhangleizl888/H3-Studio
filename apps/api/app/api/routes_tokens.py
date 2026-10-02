"""agent token 路由：给智能体/CLI 发钥匙、查钥匙、吊销钥匙。

为什么 admin-only：这把钥匙的权限上限就是属主的角色，能发钥匙的人等于能给系统再开一个
自己。属主是 admin 时钥匙也是 admin —— 所以这条路本身只许 admin 走。
无库模式（`H3_DATABASE_URL` 为空）里根本没有 users/api_tokens 表，直接拒。
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import get_session
from ..logging_setup import get_logger
from ..models import ApiToken, AuditLog, Role, User
from ..security import (
    current_user,
    hash_token,
    login_gate,
    new_agent_token,
    normalize_scopes,
)
from .common import CamelModel

log = get_logger("api.tokens")
router = APIRouter(tags=["agent-tokens"])

MAX_TOKENS_PER_USER = 20


class TokenCreate(CamelModel):
    name: str = Field(min_length=1, max_length=128, description="这把钥匙叫什么，例如 director-agent")
    scopes: list[str] = Field(default=["read", "dispatch"])
    ttl_days: int | None = Field(None, ge=1, le=3650, description="不给就是不过期")


def _require_db() -> None:
    if not get_settings().database_url:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "无库模式没有用户体系，agent token 不可用")


def _out(t: ApiToken, owner: str) -> dict[str, Any]:
    return {
        "id": t.id,
        "name": t.name,
        "scopes": list(t.scopes or []),
        "owner": owner,
        "createdAt": t.created_at,
        "expiresAt": t.expires_at,
        "lastUsedAt": t.last_used_at,
        "revokedAt": t.revoked_at,
    }


async def _audit(session: AsyncSession, *, user_id: int | None, actor: str | None, action: str, target: str | None, request: Request | None, detail: dict | None = None) -> None:
    session.add(
        AuditLog(
            user_id=user_id,
            actor=actor,
            action=action,
            target=target,
            detail=detail or {},
            ip=(request.client.host if request and request.client else None),
        )
    )


@router.get("/agent-tokens/whoami")
async def whoami(
    request: Request,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """拿当前凭据问一句「我是谁、我能干什么」。CLI 用它验证钥匙是否还有效。

    只有 agent token 才带得出 token_scopes；浏览器 JWT 走的是角色，不带 scope。
    """
    scopes = getattr(request.state, "token_scopes", None)
    token_id = getattr(request.state, "agent_token_id", None)
    rec = None
    if token_id is not None:
        rec = (await session.execute(select(ApiToken).where(ApiToken.id == int(token_id)))).scalar_one_or_none()
    return {
        "user": {
            "id": user.id,
            "username": user.username,
            "role": user.role.value if isinstance(user.role, Role) else user.role,
        },
        "via": "agent-token" if scopes is not None else "jwt",
        "tokenId": int(token_id) if token_id is not None else None,
        "tokenName": rec.name if rec else None,
        "scopes": sorted(scopes) if scopes is not None else None,
        "expiresAt": rec.expires_at if rec else None,
    }


@router.get("/agent-tokens")
async def list_tokens(
    _: User = Depends(login_gate),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    _require_db()
    rows = (
        await session.execute(
            select(ApiToken, User.username)
            .join(User, User.id == ApiToken.user_id)
            .order_by(ApiToken.id.desc())
            .limit(200)
        )
    ).all()
    return [_out(t, owner) for t, owner in rows]


@router.post("/agent-tokens", status_code=201)
async def create_token(
    body: TokenCreate,
    request: Request,
    actor: User | None = Depends(login_gate),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    _require_db()
    if actor is None:  # pragma: no cover - 无库模式已被 _require_db 拦下
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "需要登录")
    role = actor.role.value if isinstance(actor.role, Role) else actor.role
    if role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "只有 admin 能发 agent token")

    try:
        scopes = normalize_scopes(body.scopes)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))

    live = (
        await session.execute(
            select(ApiToken).where(ApiToken.user_id == actor.id, ApiToken.revoked_at.is_(None))
        )
    ).scalars().all()
    if len(live) >= MAX_TOKENS_PER_USER:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"这个账号已有 {len(live)} 把在用的钥匙（上限 {MAX_TOKENS_PER_USER}），先吊销旧的",
        )

    raw = new_agent_token()
    rec = ApiToken(
        user_id=actor.id,
        name=body.name,
        token_hash=hash_token(raw),
        scopes=scopes,
        expires_at=(
            dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=body.ttl_days) if body.ttl_days else None
        ),
    )
    session.add(rec)
    await session.flush()
    await _audit(
        session,
        user_id=actor.id,
        actor=actor.username,
        action="agent_token.create",
        target=f"api_token:{rec.id}",
        request=request,
        detail={"name": rec.name, "scopes": scopes},
    )
    log.info("已发 agent token %s（%s，scope=%s）", rec.id, rec.name, "/".join(scopes))
    # 明文只在这一次返回；库里只有摘要，之后谁都问不回来
    return {**_out(rec, actor.username), "token": raw}


@router.post("/agent-tokens/{token_id}/revoke")
async def revoke_token(
    token_id: int,
    request: Request,
    actor: User | None = Depends(login_gate),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    _require_db()
    if actor is None:  # pragma: no cover
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "需要登录")
    role = actor.role.value if isinstance(actor.role, Role) else actor.role
    if role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "只有 admin 能吊销 agent token")

    rec = (await session.execute(select(ApiToken).where(ApiToken.id == token_id))).scalar_one_or_none()
    if rec is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "没有这把钥匙")
    if rec.revoked_at is None:
        rec.revoked_at = dt.datetime.now(dt.timezone.utc)
        await _audit(
            session,
            user_id=actor.id,
            actor=actor.username,
            action="agent_token.revoke",
            target=f"api_token:{rec.id}",
            request=request,
            detail={"name": rec.name},
        )
        await session.flush()
    return _out(rec, actor.username)
