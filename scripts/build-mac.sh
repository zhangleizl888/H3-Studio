#!/usr/bin/env bash
# build-mac.sh —— 在 Apple Silicon 的 Mac 上出 H3 Studio 的 arm64 安装包（.dmg）。
#
# 这条链**不能在 Windows 上代跑**：PyInstaller 不做交叉编译，electron-builder 打 mac 目标
# 也要真的 macOS 工具链（codesign / hdiutil）。所以这里写清前置条件，在有 Mac 的机器上执行。
#
# 前置：
#   - Apple Silicon（M1 及以上）；x86_64 会直接拒掉，别打半套包出来
#   - Xcode Command Line Tools：xcode-select --install
#   - uv（锁 Python 3.12 —— pgserver 只发到 cp312，且 mac 侧有 arm64 轮子）
#   - pnpm 10+
#
# 用法：
#   bash scripts/build-mac.sh                 # 未签名 dmg + zip
#   APPLE_IDENTITY="Developer ID Application: 你的名字 (TEAMID)" \
#   APPLE_ID=you@example.com APPLE_APP_SPECIFIC_PASSWORD=xxxx-xxxx-xxxx-xxxx APPLE_TEAM_ID=TEAMID \
#   bash scripts/build-mac.sh                 # 签名 + 公证
set -euo pipefail

Root="$(cd "$(dirname "$0")/.." && pwd)"
Packaging="$Root/.packaging"
SkipFrontend="${SKIP_FRONTEND:-0}"
SkipBackend="${SKIP_BACKEND:-0}"

step() { printf '\n\033[36m=== %s ===\033[0m\n' "$*"; }

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "这个脚本只能在 macOS 上跑（PyInstaller 与 electron-builder 都不做跨平台编译）。" >&2
  exit 1
fi
if [[ "$(uname -m)" != "arm64" ]]; then
  echo "当前不是 arm64。M1 之外的构建会被 electron-builder 打成 x86_64，装到 M1 上要靠 Rosetta，" >&2
  echo "而内嵌 PostgreSQL 的二进制轮子是按构建机架构选的 —— 请先在 Apple Silicon 上构建。" >&2
  exit 1
fi
for tool in pnpm uv; do
  command -v "$tool" >/dev/null 2>&1 || { echo "缺少 $tool —— 先装它再来打包" >&2; exit 1; }
done

# ---------------------------------------------------------------- 1. 前端
if [[ "$SkipFrontend" != "1" ]]; then
  step "构建前端（生产模式：真后端 + 不自动登录）"
  ( cd "$Root/apps/web" && VITE_USE_MOCK=false VITE_DEV_AUTOLOGIN=false pnpm exec tsc -b && VITE_USE_MOCK=false VITE_DEV_AUTOLOGIN=false pnpm exec vite build )
fi
[[ -f "$Root/apps/web/dist/index.html" ]] || { echo "apps/web/dist/index.html 不存在，前端没构建成功" >&2; exit 1; }
# 打进包里的前端必须是真后端那一份：mock 分块还在 = VITE_USE_MOCK 没生效，
# 装出来看到的是原型夹具数据而不是工作台（Windows 侧实测踩过，两边共用这道闸）
if compgen -G "$Root/apps/web/dist/assets/mockApi-*.js" > /dev/null; then
  echo "apps/web/dist/assets 里还有 mockApi-*.js → 那是界面原型模式的产物，不能打进装机包" >&2
  exit 1
fi

# ---------------------------------------------------------------- 2. 打包环境
step "准备 Python 3.12 打包环境（.packaging）"
if [[ ! -x "$Packaging/bin/python" ]]; then
  uv venv --python 3.12 "$Packaging"
fi
uv pip install --python "$Packaging/bin/python" -e "$Root/apps/api[desktop]" pyinstaller

# ---------------------------------------------------------------- 3. 冻结后端
if [[ "$SkipBackend" != "1" ]]; then
  step "PyInstaller 冻结后端（onedir）"
  ( cd "$Root/apps/api" && "$Packaging/bin/python" -m PyInstaller --noconfirm --clean desktop.spec )
fi
[[ -x "$Root/apps/api/dist/h3-backend/h3-backend" ]] || { echo "缺 apps/api/dist/h3-backend/h3-backend" >&2; exit 1; }

# ---------------------------------------------------------------- 4. 出 dmg
step "electron-builder 出 arm64 dmg"
BuilderArgs=(--mac --arm64)
if [[ -n "${APPLE_IDENTITY:-}" ]]; then
  # 覆盖 electron-builder.yml 里的 identity: null（那是为未签名交付准备的）
  BuilderArgs+=(
    --config.mac.identity="$APPLE_IDENTITY"
    --config.mac.notarize=true
    --config.mac.hardenedRuntime=true
  )
  export APPLE_ID APPLE_APP_SPECIFIC_PASSWORD APPLE_TEAM_ID
else
  echo "未设 APPLE_IDENTITY —— 出未签名包。装到别人机器上会被 Gatekeeper 拦，"
  echo "首次打开要右键→打开，或 xattr -dr com.apple.quarantine '/Applications/H3 Studio.app'"
fi
( cd "$Root/apps/desktop" && pnpm exec electron-builder "${BuilderArgs[@]}" )

step "产物"
ls -lh "$Root/dist/desktop" | grep -E '\.(dmg|zip)$' || true
cat <<'TXT'

下一步（真机验收，别跳）：
  1. 挂载 dist/desktop/H3Studio-*-arm64.dmg，把 H3 Studio 拖进 /Applications
  2. 打开它 —— Windows 侧实测冷启动 13 秒到后端在线（含 initdb + 建表），
     上次被强杀过还要做崩溃恢复，实测 26 秒；Mac 上请把这一步的实测耗时记回来
  3. 登录页应当已经填好 admin / 12345，直接点「进入」
  4. 数据在 ~/Library/Application Support/H3 Studio，日志在它下面 logs/
  5. 退出走托盘「退出 H3 Studio」，然后确认 pgrep -x postgres 里没有指向该数据目录的进程
     （pgserver 只在「全局持有者名单只剩自己」时才停服，被强杀留下的死 PID 会让它不停；
      后端 app/desktop.py:stop_database 已用随包 pg_ctl 兜底）
  6. M1 上没有 CUDA：本地图/视频生成不会可用，这是预期。要出图就在首启向导里
     连一台远程 ComfyUI（http://<host>:8188）或 RunningHub 的原生代理地址
     （pgserver 在 macOS 上只走 UNIX socket（-h ""），连接串由 app/desktop.py 按平台拼）
TXT
