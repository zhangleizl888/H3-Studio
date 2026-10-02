"""认证与用户路由。

规则：口令只存 argon2id 哈希；首次启动没有 admin 时必须先建一个；
口令只卡「≥4 位 + 不在常见弱口令表」；刷新令牌一次一换，二次使用即吊销全部会话。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..logging_setup import get_logger
from ..models import AuditLog, Role, User
from ..security import (
    WeakPassword,
    admin_only,
    current_user,
    hash_password,
    issue_access,
    issue_refresh_token,
    rotate_refresh_token,
    verify_password,
)

log = get_logger("api.auth")
router = APIRouter(tags=["auth"])


class LoginBody(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class SessionOut(BaseModel):
    access: str
    refresh: str
    expires_in: int
    user: dict[str, Any]


def _user_dict(u: User) -> dict[str, Any]:
    return {
        "id": u.id,
        "username": u.username,
        "displayName": u.display_name,
        "role": u.role.value if isinstance(u.role, Role) else u.role,
        "isActive": u.is_active,
        "maxConcurrentJobs": u.max_concurrent_jobs,
        "dailyMoneyLimit": float(u.daily_money_limit) if u.daily_money_limit is not None else None,
        "preferences": u.preferences or {},
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


DEMO_USERNAME = "admin"
DEMO_PASSWORD = "12345"
# 历代演示口令。环回启动时如果演示账号还停在某一代演示口令上，就地升到 DEMO_PASSWORD ——
# 只匹配这几个一次性口令，被人真正改过的口令碰不到；这样「默认口令」改版本后
# 老库不用重建，README 的读者也不用先猜上一版是什么。
LEGACY_DEMO_PASSWORDS = ("1234",)
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


async def seed_loopback_admin(session: AsyncSession, settings: Any) -> User | None:
    """开源演示用的首个账号：库里一个用户都没有、且服务只监听环回时，建 admin / 12345。

    只要 host 不是环回（真要给一个团队用），这里什么都不做，必须由人显式 bootstrap。
    库里已经有账号时只做一件事：把还停在旧演示口令上的演示 admin 升到当前演示口令。
    """
    from sqlalchemy import func as sa_func

    if settings.host not in LOOPBACK_HOSTS:
        if not await session.scalar(select(sa_func.count()).select_from(User)):
            log.warning("库里没有账号，但服务监听在 %s（不是环回）：不自动创建演示管理员，请调 POST /api/auth/bootstrap", settings.host)
        return None

    if not await session.scalar(select(sa_func.count()).select_from(User)):
        admin = User(
            username=DEMO_USERNAME,
            display_name="管理员",
            password_hash=hash_password(DEMO_PASSWORD),
            role=Role.admin,
        )
        session.add(admin)
        await session.commit()
        await session.refresh(admin)
        log.warning("已创建演示账号 %s / %s（仅环回）。给团队用之前请改口令并关掉自动登录。", DEMO_USERNAME, DEMO_PASSWORD)
        return admin

    admin = await session.scalar(select(User).where(User.username == DEMO_USERNAME))
    if admin is None:
        return None
    for stale in LEGACY_DEMO_PASSWORDS:
        if verify_password(admin.password_hash, stale):
            admin.password_hash = hash_password(DEMO_PASSWORD)
            await session.commit()
            await session.refresh(admin)
            log.warning("演示账号 %s 还停在旧演示口令 %s 上，已升到 %s（仅环回）。", DEMO_USERNAME, stale, DEMO_PASSWORD)
            return admin
    return None


@router.get("/auth/setup-required")
async def setup_required(session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    """首屏用这个判断要不要走「创建管理员」向导，而不是让用户去猜。"""
    count = await session.scalar(select(func.count()).select_from(User))
    return {"needed": not count, "reason": None if count else "还没有任何账号，需要创建第一个管理员"}


@router.post("/auth/bootstrap", response_model=SessionOut)
async def bootstrap(body: LoginBody, request: Request, session: AsyncSession = Depends(get_session)) -> SessionOut:
    """只在库为空时可用一次：建 admin 并直接登录。"""
    if await session.scalar(select(func.count()).select_from(User)):
        raise HTTPException(409, "系统已初始化，管理员账号请由现有管理员创建")
    try:
        admin = User(
            username=body.username.strip() or "admin",
            display_name=(body.username.strip() or "admin"),
            password_hash=hash_password(body.password),
            role=Role.admin,
        )
    except WeakPassword as exc:
        raise HTTPException(422, str(exc)) from exc
    session.add(admin)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise HTTPException(409, "用户名已存在") from exc
    await _audit(session, user_id=admin.id, actor=admin.username, action="auth.bootstrap", target=f"user:{admin.id}", request=request)
    await session.commit()
    await session.refresh(admin)
    return await _issue(session, admin, request)


async def _issue(session: AsyncSession, user: User, request: Request | None) -> SessionOut:
    access, ttl = issue_access(user)
    refresh = await issue_refresh_token(
        session,
        user,
        user_agent=request.headers.get("user-agent") if request else None,
        ip=request.client.host if request and request.client else None,
    )
    return SessionOut(access=access, refresh=refresh, expires_in=ttl, user=_user_dict(user))


@router.post("/auth/login", response_model=SessionOut)
async def login(body: LoginBody, request: Request, session: AsyncSession = Depends(get_session)) -> SessionOut:
    user = await session.scalar(select(User).where(User.username == body.username.strip()))
    # 用户名不存在与口令错误必须返回完全一样的话，否则等于送出用户名枚举
    ok = bool(user and user.is_active and verify_password(user.password_hash, body.password))
    if not ok:
        if user is not None:
            await _audit(session, user_id=user.id, actor=user.username, action="auth.login_failed", target=f"user:{user.id}", request=request)
            await session.commit()
        raise HTTPException(401, "用户名或口令不正确")
    assert user is not None
    from datetime import datetime, timezone

    user.last_login_at = datetime.now(timezone.utc)
    await _audit(session, user_id=user.id, actor=user.username, action="auth.login", target=f"user:{user.id}", request=request)
    await session.commit()
    await session.refresh(user)
    return await _issue(session, user, request)


@router.post("/auth/refresh", response_model=SessionOut)
async def refresh(body: dict[str, str], request: Request, session: AsyncSession = Depends(get_session)) -> SessionOut:
    raw = (body or {}).get("refresh", "")
    user, new_refresh = await rotate_refresh_token(session, raw)
    await session.commit()
    access, ttl = issue_access(user)
    return SessionOut(access=access, refresh=new_refresh, expires_in=ttl, user=_user_dict(user))


@router.post("/auth/logout")
async def logout(body: dict[str, str], session: AsyncSession = Depends(get_session)) -> dict[str, bool]:
    from datetime import datetime, timezone

    from ..models import RefreshToken
    from ..security import hash_token

    token = (body or {}).get("refresh", "")
    if token:
        await session.execute(
            RefreshToken.__table__.update()
            .where(RefreshToken.token_hash == hash_token(token), RefreshToken.revoked_at.is_(None))
            .values(revoked_at=datetime.now(timezone.utc))
        )
    await session.commit()
    return {"ok": True}


@router.get("/auth/me")
async def me(user: User = Depends(current_user)) -> dict[str, Any]:
    return _user_dict(user)


class ChangePasswordBody(BaseModel):
    currentPassword: str = Field(min_length=1, max_length=256)
    newPassword: str = Field(min_length=1, max_length=256)


@router.post("/auth/change-password")
async def change_password(
    body: ChangePasswordBody,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict[str, Any]:
    """改自己的口令：先验当前口令，换完哈希把这个账号的浏览器会话全部吊销。

    改完必须重新登录是刻意的 —— JWT 里查不到浏览器手上那张 refresh token，
    做不到「只踢别人、留自己」。agent token 不动：那是显式发出去的长期钥匙，
    改口令不该把接在上面的智能体和 CLI 一起踢下线。
    """
    if not verify_password(user.password_hash, body.currentPassword):
        await _audit(session, user_id=user.id, actor=user.username, action="auth.password_change_failed", target=f"user:{user.id}", request=request)
        await session.commit()
        raise HTTPException(401, "当前口令不正确")
    if body.newPassword == body.currentPassword:
        raise HTTPException(422, "新口令和当前口令一样")
    try:
        user.password_hash = hash_password(body.newPassword)
    except WeakPassword as exc:
        raise HTTPException(422, str(exc)) from exc

    from datetime import datetime, timezone

    from ..models import RefreshToken

    revoked = (
        await session.execute(
            RefreshToken.__table__.update()
            .where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=datetime.now(timezone.utc))
        )
    ).rowcount
    await _audit(session, user_id=user.id, actor=user.username, action="auth.password_changed", target=f"user:{user.id}", request=request, detail={"sessionsRevoked": revoked})
    await session.commit()
    log.info("口令已更新：user=%s，吊销会话 %s 条（agent token 未动）", user.username, revoked)
    return {"ok": True, "sessionsRevoked": revoked}


class UserCreate(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    displayName: str = Field(min_length=1, max_length=128)
    role: Role = Role.editor
    password: str = Field(min_length=1, max_length=256)
    maxConcurrentJobs: int = Field(2, ge=0, le=32)
    dailyMoneyLimit: float | None = Field(None, ge=0)


@router.get("/users")
async def list_users(session: AsyncSession = Depends(get_session), _: User = Depends(admin_only)) -> list[dict[str, Any]]:
    rows = (await session.execute(select(User).order_by(User.id))).scalars().all()
    return [_user_dict(u) for u in rows]


@router.post("/users")
async def create_user(body: UserCreate, request: Request, session: AsyncSession = Depends(get_session), actor: User = Depends(admin_only)) -> dict[str, Any]:
    try:
        pwd = hash_password(body.password)
    except WeakPassword as exc:
        raise HTTPException(422, str(exc)) from exc
    u = User(
        username=body.username.strip(),
        display_name=body.displayName.strip(),
        role=body.role,
        password_hash=pwd,
        max_concurrent_jobs=body.maxConcurrentJobs,
        daily_money_limit=body.dailyMoneyLimit,
    )
    session.add(u)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise HTTPException(409, "用户名已存在") from exc
    await _audit(session, user_id=actor.id, actor=actor.username, action="user.create", target=f"user:{u.id}", request=request)
    await session.commit()
    await session.refresh(u)
    return _user_dict(u)


@router.patch("/users/{user_id}")
async def update_user(user_id: int, body: dict[str, Any], request: Request, session: AsyncSession = Depends(get_session), actor: User = Depends(admin_only)) -> dict[str, Any]:
    u = await session.get(User, user_id)
    if u is None:
        raise HTTPException(404, "用户不存在")
    if body.get("isActive") is False and u.id == actor.id:
        # 管理员把自己停用 = 系统失去唯一可管理者，且现场无法恢复
        raise HTTPException(409, "不能停用自己的账号")
    # 改口令必须单独走改密接口，这里只允许改档案与配额
    for key, attr in (("displayName", "display_name"), ("role", "role"), ("isActive", "is_active"), ("maxConcurrentJobs", "max_concurrent_jobs"), ("dailyMoneyLimit", "daily_money_limit")):
        if key in body:
            setattr(u, attr, body[key])
    if not u.is_active:
        from ..models import RefreshToken

        from datetime import datetime, timezone

        await session.execute(
            RefreshToken.__table__.update().where(RefreshToken.user_id == u.id, RefreshToken.revoked_at.is_(None)).values(revoked_at=datetime.now(timezone.utc))
        )
    await _audit(session, user_id=actor.id, actor=actor.username, action="user.update", target=f"user:{u.id}", request=request, detail={k: v for k, v in body.items() if k != "password"})
    await session.commit()
    await session.refresh(u)
    return _user_dict(u)


@router.delete("/users/{user_id}", status_code=204)
async def delete_user(user_id: int, request: Request, session: AsyncSession = Depends(get_session), actor: User = Depends(admin_only)) -> None:
    """删除账号。

    jobs/media 的 owner_id 是 SET NULL、刷新令牌是 CASCADE：删完历史留在库里但不再归任何人，
    这是刻意的 —— 审计与产物不该跟着一个账号一起消失。想「保留历史只断访问」请用停用。
    """
    u = await session.get(User, user_id)
    if u is None:
        raise HTTPException(404, "用户不存在")
    if u.id == actor.id:
        raise HTTPException(409, "不能删除自己的账号；请先用另一个管理员账号操作")
    await session.delete(u)
    await _audit(session, user_id=actor.id, actor=actor.username, action="user.delete", target=f"user:{u.id}", request=request, detail={"username": u.username})
    await session.commit()
