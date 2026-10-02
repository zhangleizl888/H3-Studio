# build-win.ps1 —— 一条命令出 Windows 11 安装包（H3Studio-Setup-<版本>.exe）。
#
# 链条：前端构建 → 3.12 打包环境 → PyInstaller 冻结后端 → electron-builder 出 NSIS 安装器。
# 为什么脚本自己管 venv：pgserver 只发 cp312 的轮子，而开发用的 apps/api/.venv 是 3.13，
# 拿开发环境冻结会出「本机跑得通、装机包缺模块」这种最难查的坑。
# 为什么前端必须由后端托管：桌面壳要 load 一个真 URL，见 apps/api/app/main.py 的 mount_web_client。
#
# 用法：
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\build-win.ps1
#   ... -Dir            只出 dist/win-unpacked（不生成安装器，自查最快）
#   ... -SkipFrontend   前端没改就跳过
#   ... -SkipBackend    后端没改就跳过冻结
#
# 未签名交付：装完首次运行 Windows 11 会弹 SmartScreen「未知发布者」，点「更多信息 → 仍要运行」。
# 拿到代码签名证书后把下面的 $SignConfig 填上即可，electron-builder 会自己调 signtool。
param(
    [switch]$SkipFrontend,
    [switch]$SkipBackend,
    [switch]$Dir,
    [string]$Version = ""
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Packaging = Join-Path $Root '.packaging'
$Api = Join-Path $Root 'apps\api'
$Web = Join-Path $Root 'apps\web'
$Desktop = Join-Path $Root 'apps\desktop'

function Step($msg) { Write-Host "`n=== $msg ===" -ForegroundColor Cyan }
function Need($exe) {
    if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { throw "找不到 $exe —— 先装它再来打包" }
}

Need 'pnpm'
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { throw "找不到 uv —— 打包环境靠它锁 Python 3.12（scoop install uv / pip install uv）" }

# ---------------------------------------------------------------- 1. 前端
if (-not $SkipFrontend) {
    Step '构建前端（生产模式：真后端 + 不自动登录）'
    $env:VITE_USE_MOCK = 'false'
    $env:VITE_DEV_AUTOLOGIN = 'false'
    Push-Location $Web
    pnpm exec tsc -b
    if ($LASTEXITCODE -ne 0) { Pop-Location; throw '前端类型检查失败' }
    pnpm exec vite build
    if ($LASTEXITCODE -ne 0) { Pop-Location; throw '前端构建失败' }
    Pop-Location
}
# 为什么是 `pnpm exec vite build` 而不是 `pnpm build`：这台机器上 `pnpm build` 会只弹一个
# cmd 横幅就返回 0（实测），于是一个陈旧的 dist 被当成刚构建的产物打进包里 ——
# 装出来的界面是 mock 夹具（张雷、长夜出租车），而构建全程「成功」。这条闸就是为它加的。
$DistAssets = Join-Path $Web 'dist\assets'
if (-not (Test-Path (Join-Path $Web 'dist\index.html'))) { throw 'apps/web/dist/index.html 不存在，前端没构建成功' }
if (Get-ChildItem $DistAssets -Filter 'mockApi-*.js' -ErrorAction SilentlyContinue) {
    throw 'dist 里有 mockApi 分块 —— 这是原型模式的产物，不能打进装机包（VITE_USE_MOCK 没生效）'
}
Write-Host ("前端入口：{0}" -f ((Select-String -Path (Join-Path $Web 'dist\index.html') -Pattern 'assets/index-[\w-]+\.js' -AllMatches).Matches.Value -join ','))

# ---------------------------------------------------------------- 2. 打包环境
Step '准备 Python 3.12 打包环境（.packaging）'
if (-not (Test-Path (Join-Path $Packaging 'Scripts\python.exe'))) {
    uv venv --python 3.12 $Packaging
    if ($LASTEXITCODE -ne 0) { throw "uv venv 失败" }
}
uv pip install --python (Join-Path $Packaging 'Scripts\python.exe') '-e' "$Api[desktop]" pyinstaller
if ($LASTEXITCODE -ne 0) { throw '打包环境装依赖失败' }

# ---------------------------------------------------------------- 3. 冻结后端
if (-not $SkipBackend) {
    Step 'PyInstaller 冻结后端（onedir）'
    Push-Location $Api
    & (Join-Path $Packaging 'Scripts\python.exe') -m PyInstaller --noconfirm --clean desktop.spec
    $code = $LASTEXITCODE
    Pop-Location
    if ($code -ne 0) { throw '后端冻结失败' }
}
$Backend = Join-Path $Api 'dist\h3-backend\h3-backend.exe'
if (-not (Test-Path $Backend)) { throw "没有 $Backend —— 冻结没出产物" }
Write-Host ("后端产物：{0:N0} 字节" -f (Get-Item $Backend).Length)

# ---------------------------------------------------------------- 4. 桌面壳与安装器
Step 'electron-builder 出包'
# 这台机器从 GitHub 拉 Electron 二进制会挂住（实测 25 分钟零字节），所以镜像走 env；
# apps/desktop/.npmrc 里也有一份，但 electron-builder 的 nsis/winCodeSign 工具链只认这个环境变量。
if (-not $env:ELECTRON_MIRROR) { $env:ELECTRON_MIRROR = 'https://npmmirror.com/mirrors/electron/' }
if (-not $env:ELECTRON_BUILDER_BINARIES_MIRROR) { $env:ELECTRON_BUILDER_BINARIES_MIRROR = 'https://npmmirror.com/mirrors/electron-builder-binaries/' }
Push-Location $Desktop
if ($Version) { $env:H3_BUILD_VERSION = $Version }
if ($Dir) {
    pnpm exec electron-builder --win dir
} else {
    pnpm exec electron-builder --win nsis
}
$code = $LASTEXITCODE
Pop-Location
if ($code -ne 0) { throw 'electron-builder 失败' }

Step '产物'
Get-ChildItem (Join-Path $Root 'dist\desktop') -File | Where-Object { $_.Extension -in '.exe', '.yml', '.blockmap' } |
    ForEach-Object { Write-Host ("  {0}  {1:N1} MB" -f $_.Name, ($_.Length / 1MB)) }
Write-Host @"

下一步（真机验收，别跳）：
  1. 双击 dist\desktop\H3Studio-Setup-*.exe 装到默认位置（当前用户，不需要管理员）
  2. 从开始菜单打开 H3 Studio —— 本机实测冷启动 13 秒到后端在线（含 initdb + 建表）；
     上次是被强杀关的，还要多做崩溃恢复，实测 26 秒，属正常
  3. 登录页应当已经填好 admin / 12345，直接点「进入」
  4. 数据在 %LOCALAPPDATA%\H3 Studio（卸载不会动它）；日志在它下面 logs\
  5. 没接 ComfyUI 的话首启向导会弹出来要求配实例 —— 那是预期的，不是装坏了
  6. 退出走托盘的「退出 H3 Studio」：它会让后端自己停，并顺手把内嵌 PostgreSQL 停干净
"@ -ForegroundColor DarkGray
