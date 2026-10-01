"""argon2 口令哈希 + JWT + RBAC。

三条硬规定：
  1. 口令只存 argon2id 哈希，绝不存明文、绝不打日志。
  2. 首次启动必须建 admin，弱口令直接拒 —— 这是局域网多用户工具的最低门槛。
  3. 角色只决定「能不能改配置 / 能不能派发」，不靠前端隐藏按钮实现。
"""

from __future__ import annotations

import datetime as dt
import hmac
import secrets
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error, InvalidHashError, VerifyMismatchError
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings
from .db import get_session
from .models import RefreshToken, Role, User

# argon2 默认参数在 2024+ 已是 OWASP 量级；这里显式写出来，免得将来换库时静默降级
# type 默认就是 argon2id；不要显式传 type=None，PasswordHasher 会直接 TypeError
_hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2, hash_len=32)

MIN_PASSWORD_LEN = 10
# 局域网里最常见的弱口令，直接列死比"长度够就行"有效
BLOCKED_PASSWORDS = {
    "password", "1234567890", "admin123", "h3studio1", "comfyui123", "iloveyou1",
}


class WeakPassword(ValueError):
    pass


def password_policy_error(candidate: str) -> str | None:
    if len(candidate) < MIN_PASSWORD_LEN:
        return f"口令至少 {MIN_PASSWORD_LEN} 位（局域网常驻服务，4-8 位会被内网爆破）"
    lowered = candidate.lower()
    if lowered in BLOCKED_PASSWORDS:
        return "这个口令太常见，换一个"
    if len(set(candidate)) < 5:
        return "口令字符种类太少，换一个"
    return None


def hash_password(candidate: str, *, enforce_policy: bool = True) -> str:
    error = password_policy_error(candidate)
    if error and enforce_policy:
        raise WeakPassword(error)
    return _hasher.hash(candidate)


def verify_password(hash_: str, candidate: str) -> bool:
    try:
        return _hasher.verify(hash_, candidate)
    except (VerifyMismatchError, InvalidHashError, Argon2Error):
        return False
    except Exception:
        # 校验路径上的任何意外都按"登录失败"收，绝不把哈希或内部信息带进响应
        return False


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def issue_access(user: User) -> tuple[str, int]:
    settings = get_settings()
    ttl = settings.jwt_ttl_s
    secret = settings.resolved_jwt_secret()
    payload = {
        "sub": str(user.id),
        "username": user.username,
        "role": user.role.value if isinstance(user.role, Role) else user.role,
        "iat": int(_now().timestamp()),
        "exp": int(_now().timestamp()) + ttl,
        # 会话标识，将来做吊销列表用
        "jti": secrets.token_hex(8),
    }
    return jwt.encode(payload, secret, algorithm="HS256"), ttl


def decode_access(token: str) -> dict[str, Any]:
    try:
        return jwt.decode(token, get_settings().resolved_jwt_secret(), algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "登录已过期，请重新登录")
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "登录凭据无效")


def new_refresh_token() -> str:
    return secrets.token_urlsafe(48)


def hash_token(value: str) -> str:
    # 刷新令牌本身就是凭据，库里只放摘要
    return hmac.new(get_settings().resolved_jwt_secret().encode(), value.encode(), "sha256").hexdigest()


async def issue_refresh_token(session: AsyncSession, user: User, *, user_agent: str | None, ip: str | None) -> str:
    raw = new_refresh_token()
    session.add(
        RefreshToken(
            user_id=user.id,
            token_hash=hash_token(raw),
            expires_at=_now() + dt.timedelta(seconds=get_settings().refresh_ttl_s),
            user_agent=(user_agent or "")[:400] or None,
            ip=ip,
        )
    )
    await session.flush()
    return raw


async def rotate_refresh_token(session: AsyncSession, raw: str) -> tuple[User, str]:
    """刷新即换发。旧令牌被二次使用 = 疑似泄露，吊销该用户全部会话并报错。"""
    record = await session.scalar(select(RefreshToken).where(RefreshToken.token_hash == hash_token(raw)))
    if record is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "会话无效，请重新登录")
    if record.revoked_at is not None:
        await session.execute(
            RefreshToken.__table__.update()
            .where(RefreshToken.user_id == record.user_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=_now())
        )
        # 必须先提交再抛：get_session 的 session_scope 在异常时会 rollback，
        # 不先 commit 的话这次"吊销全部会话"会被静默回滚，安全动作等于没做。
        await session.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "检测到刷新令牌被重复使用，已注销全部会话，请重新登录")
    if record.expires_at <= _now():
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "会话已过期，请重新登录")

    user = await session.get(User, record.user_id)
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "账号不可用")

    record.revoked_at = _now()
    new_raw = await issue_refresh_token(session, user, user_agent=record.user_agent, ip=record.ip)
    return user, new_raw


async def current_user(request: Request, session: AsyncSession = Depends(get_session)) -> User:
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "需要登录")
    claims = decode_access(header[7:].strip())
    user = await session.get(User, int(claims["sub"]))
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "账号不存在或已停用")
    request.state.user_id = user.id
    return user


def require_roles(*roles: Role):
    allowed = {r.value for r in roles}

    async def dependency(user: User = Depends(current_user)) -> User:
        value = user.role.value if isinstance(user.role, Role) else user.role
        if value not in allowed:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"需要 {'/'.join(sorted(allowed))} 角色，当前是 {value}")
        return user

    return dependency


admin_only = require_roles(Role.admin)
editor_or_admin = require_roles(Role.admin, Role.editor)


def role_gate(roles: set[str] | None = None):
    """两种模式共用一套路由的门禁。

    无库模式是单人本机开发，users 表还不存在，要求登录等于把这条路堵死；
    有库模式按角色放行 —— roles=None 只要求「登录了」，否则再查角色。
    """

    async def dependency(request: Request, session: AsyncSession = Depends(get_session)) -> User | None:
        if not get_settings().database_url:
            return None
        user = await current_user(request, session)
        if roles is not None:
            value = user.role.value if isinstance(user.role, Role) else user.role
            if value not in roles:
                raise HTTPException(status.HTTP_403_FORBIDDEN, f"需要 {'/'.join(sorted(roles))} 角色，当前是 {value}")
        return user

    return dependency


# 谁能改实例/AI 后端配置，谁只能派发任务。viewer 一律不能派发（会花真钱）。
login_gate = role_gate(None)
admin_gate = role_gate({"admin"})
dispatch_gate = role_gate({"admin", "editor"})

