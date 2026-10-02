# -*- mode: python ; coding: utf-8 -*-
r"""H3 Studio 桌面后端的 PyInstaller 配置（onedir）。

为什么 onedir 不用 onefile：内嵌 PostgreSQL 是一棵 40MB 的可执行二进制树，onefile 每次启动
都要把整个包解到临时目录（冷启动十几秒起），而且「从临时目录里跑 exe」正是杀软最爱拦的行为。
还有一条更硬的：onefile 会往子进程环境里塞 `_MEIPASS2`，我们却要 spawn 那棵树的
`initdb.exe` / `postgres.exe` —— 它们会被当成 PyInstaller 产物二次解包，起不来。
onedir 的代价是产物是一个目录 —— 交给 electron-builder 的 extraResources 刚好。

构建顺序是硬要求（这里直接拦下来，不让出半套包）：
  1. pnpm --filter h3-studio-web build      → apps/web/dist
  2. 本 spec                                → apps/api/dist/h3-backend/
  3. electron-builder                        → 把上一步整个目录搬进 resources/backend
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

ROOT = Path(SPECPATH)  # apps/api
WEB_DIST = ROOT.parent / "web" / "dist"

if not (WEB_DIST / "index.html").exists():
    raise SystemExit(
        "缺少前端构建产物 apps/web/dist/index.html —— 先构建前端（生产模式）："
        "cd apps/web && VITE_USE_MOCK=false pnpm exec vite build。后端要把 dist 挂成静态站，桌面壳才有东西可加载。"
    )
if list((WEB_DIST / "assets").glob("mockApi-*.js")):
    # 打进装机包的前端必须是真后端那一份。mock 分块还在 = 构建时 VITE_USE_MOCK 没生效，
    # 装出来会看到原型夹具数据（张雷、长夜出租车）而不是工作台 —— 这条实测踩过。
    raise SystemExit("apps/web/dist 里还有 mockApi-*.js → 那是界面原型模式的产物，不能打进装机包")

datas = [
    # 迁移脚本在运行时按文件路径加载（alembic 用自己的源码加载器），必须原样带着
    (str(ROOT / "alembic"), "alembic"),
    # runtime.web_dist() 在冻结态读的是随包资源根下的 web/
    (str(WEB_DIST), "web"),
]
# pginstall/ 整棵 Postgres 二进制树（initdb / postgres / pg_dump 与 share/ 里的系统目录定义）
datas += collect_data_files("pgserver")

hiddenimports = []
for pkg in (
    "pgserver",
    "asyncpg",
    "uvicorn",
    "alembic",
    "sqlalchemy.dialects.postgresql",
    "pydantic_settings",
):
    hiddenimports += collect_submodules(pkg)
# pgserver 的 C 扩展是顶层模块，不显式点名不会被收进来
hiddenimports += ["_postgresql", "httpx", "psutil"]

binaries = collect_dynamic_libs("argon2_cffi_bindings")

a = Analysis(
    [str(ROOT / "scripts" / "desktop_entry.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    # mcp 只有 h3 CLI 用得到：留在包里白增几十 MB，还会把 pydantic 的可选依赖拽进来
    excludes=["tkinter", "mcp", "pytest"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="h3-backend",
    debug=False,
    strip=False,
    upx=False,
    # 不弹控制台窗；stdout/stderr 由 scripts/desktop_entry.py 接到日志文件上
    console=False,
    icon=str(ROOT.parent / "desktop" / "build" / "icon.ico"),
)

coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="h3-backend")
