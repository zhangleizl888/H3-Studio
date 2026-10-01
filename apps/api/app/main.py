"""FastAPI 入口。

当前范围（数据库未接入）：健康检查 + 实例探活 + 提交/查询任务。
路由形状就是 PLAN §10 的契约，前端切 VITE_USE_MOCK=false 即可直接对接。
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, UploadFile
from pydantic import BaseModel, Field

from .api.routes_instances import router as instances_router
from .api.routes_auth import router as auth_router
from .api.routes_export import router as export_router
from .api.routes_jobs import router as jobs_router
from .api.routes_llm import router as llm_router
from .api.routes_parse import router as parse_router
from .api.routes_system import router as system_router
from .api.routes_versions import router as versions_router
from .api.routes_workflows import router as workflows_router
from .config import get_settings
from .gen.registry import InstanceRegistry
from .gpu_arbiter import GpuArbiter, env_enabled
from .logging_setup import get_logger, redact, setup_logging
from .purge import TrashPurger
from .queue import QueueDispatcher

log = get_logger("app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    setup_logging(settings.log_level)
    app.state.settings = settings

    if settings.database_url:
        # 表由 Alembic 建，这里只从库里读实例做引导
        app.state.registry = await InstanceRegistry.from_database(settings)
        from .api.routes_auth import seed_loopback_admin
        from .db import session_factory

        async with session_factory()() as s:
            seeded = await seed_loopback_admin(s, settings)
        if seeded is not None:
            log.info("已备好演示账号，前端会自动登录（关掉它：VITE_DEV_AUTOLOGIN=false）")
    else:
        app.state.registry = InstanceRegistry(settings)
        log.warning("未配置 H3_DATABASE_URL：实例从 %s 引导，队列与用户功能不可用", settings.instances_file)

    app.state.gpu = GpuArbiter(enabled=env_enabled(settings.gpu_arbiter))
    if settings.database_url:
        await app.state.gpu.load_persisted()
        if app.state.gpu.stopped:
            log.warning("上次让卡后没恢复文本模型，正在尝试拉起（端口 %s）", app.state.gpu.stopped.get("port"))
            await app.state.gpu.restore_llm()

    app.state.dispatcher = QueueDispatcher(app.state.registry, gpu=app.state.gpu)
    app.state.trash = TrashPurger()
    if settings.database_url:
        await app.state.dispatcher.start()
        # 回收站到期清理是另一个任务，不挂进派发循环（理由写在 TrashPurger 的 docstring 里）
        await app.state.trash.start()
    log.info(
        "后端启动；实例：%s；单卡仲裁：%s",
        ", ".join(app.state.registry.ids) or "（无）",
        "开" if app.state.gpu.enabled else "关（H3_GPU_ARBITER=off）",
    )
    try:
        yield
    finally:
        await app.state.trash.stop()
        await app.state.dispatcher.stop()
        await app.state.registry.close()


app = FastAPI(title="H3 Studio API", version="0.1.0", lifespan=lifespan)
app.include_router(auth_router, prefix="/api")
app.include_router(instances_router, prefix="/api")
app.include_router(jobs_router, prefix="/api")
app.include_router(llm_router, prefix="/api")
app.include_router(parse_router, prefix="/api")
app.include_router(workflows_router, prefix="/api")
app.include_router(system_router, prefix="/api")
app.include_router(versions_router, prefix="/api")
app.include_router(export_router, prefix="/api")


@app.get("/healthz")
async def healthz() -> dict[str, Any]:
    s: Any = app.state.settings
    return {
        "ok": True,
        "database": bool(s.database_url),
        "instances": len(app.state.registry.ids),
        "mediaRoot": str(s.media_root),
    }


__all__ = ["app", "InstanceRegistry", "get_settings"]
