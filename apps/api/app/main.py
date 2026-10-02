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
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import runtime
from .api.routes_instances import router as instances_router
from .api.routes_auth import router as auth_router
from .api.routes_export import router as export_router
from .api.routes_jobs import router as jobs_router
from .api.routes_llm import router as llm_router
from .api.routes_parse import router as parse_router
from .api.routes_skills import router as skills_router
from .api.routes_system import router as system_router
from .api.routes_tokens import router as tokens_router
from .api.routes_versions import router as versions_router
from .api.routes_workflows import router as workflows_router
from .config import get_settings
from .desktop import router as desktop_router
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
        # 「设置 → 系统」里存下来的目录要先装进进程内 Settings，再建注册表/队列：
        # 它们都拿这个单例，晚一步就各用各的（页面显示 D:/…、后端实际写 F:/…）
        from .paths import apply_at_startup

        applied = await apply_at_startup()
        if applied:
            log.info("目录设置已从库里生效：%s", applied)
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
app.include_router(skills_router, prefix="/api")
app.include_router(system_router, prefix="/api")
app.include_router(tokens_router, prefix="/api")
app.include_router(versions_router, prefix="/api")
app.include_router(export_router, prefix="/api")

app.include_router(desktop_router, prefix="/api")


def mount_web_client(app: FastAPI) -> Path | None:
    r"""把前端构建产物挂到后端自身，让「一个进程 + 一个端口」就能开出完整界面。

    以前前端只能靠 vite dev server（`/api` 代理），打包后没有这条路：Electron 需要一个真 URL
    去 load，浏览器直连局域网也需要一个入口。这里挂上之后两者都走同源，不用配 CORS。

    只服务精确存在的文件（`is_relative_to` 兜住 `../`），其余路径回 index.html 交给前端路由；
    `/api`、`/docs` 这些保留前缀必须 404，不能拿 HTML 冒充 JSON 响应。
    """
    dist = runtime.web_dist()
    if dist is None:
        return None
    dist = dist.resolve()
    index = dist / "index.html"
    assets = dist / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    reserved = ("api", "docs", "redoc", "openapi.json")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str) -> FileResponse:
        # 先按真实文件给（favicon、robots.txt 这类就在 dist 根上），再谈保留前缀与回退
        if full_path:
            candidate = (dist / full_path).resolve()
            if candidate.is_file() and candidate.is_relative_to(dist):
                return FileResponse(candidate)
        if full_path.split("/", 1)[0] in reserved:
            raise HTTPException(404, f"没有这个接口：/{full_path}")
        return FileResponse(index)

    return dist


@app.get("/healthz")
async def healthz() -> dict[str, Any]:
    s: Any = app.state.settings
    return {
        "ok": True,
        "database": bool(s.database_url),
        "instances": len(app.state.registry.ids),
        "mediaRoot": str(s.media_root),
    }


# catch-all 必须在所有真路由之后注册：先注册的话 /healthz 会被它吃掉
mount_web_client(app)


__all__ = ["app", "InstanceRegistry", "get_settings"]
