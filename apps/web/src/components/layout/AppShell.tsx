import { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate, useParams } from "react-router-dom";
import {
  Clapperboard,
  Images,
  LogOut,
  Moon,
  Layers,
  Plus,
  ScrollText,
  Settings2,
  Film,
  Sun,
  Cpu,
  ListChecks,
  TextCursorInput,
  Waypoints,
  History,
  Trash2,
} from "lucide-react";
import { Button, MachChip, Spinner } from "../ui";
import { SplitHandle, usePane } from "../SplitPane";
import ModelConfigModal from "../ModelConfigModal";
import { useInstances, useJobs, useProjects } from "../../lib/hooks";
import { useApp } from "../../state/app";
import { useApi } from "../../lib/apiClient";
import { cn } from "../../lib/utils";

const STAGES = [
  { key: "script", label: "剧本创作", to: "script", icon: TextCursorInput, phase: "Phase 01" },
  { key: "assets", label: "场景角色", to: "assets", icon: Images, phase: "Phase 02" },
  { key: "director", label: "AI工作台", to: "director", icon: Clapperboard, phase: "Phase 03" },
  { key: "export", label: "制片导出", to: "export", icon: Film, phase: "Phase 04" },
  { key: "prompts", label: "资产管理", to: "prompts", icon: ListChecks, phase: "Advanced" },
  { key: "queue", label: "任务队列", to: "queue", icon: Waypoints, phase: "Local" },
] as const;

export function AppShell() {
  const { id } = useParams();
  const { pathname } = useLocation();
  const { data: projects } = useProjects();
  const { navCollapsed, setNavCollapsed, theme, toggleTheme, user, setUser } = useApp();
  const [modelConfig, setModelConfig] = useState(false);
  const navPane = usePane("shell.nav", 236, 200, 460);
  // 拖动期间必须关掉 transition-[width]，否则栏宽会黏在指针后面慢半拍
  const [navDragging, setNavDragging] = useState(false);
  const api = useApi();
  const nav = useNavigate();
  const project = projects?.find((p) => p.id === id);

  return (
    <div className="app-bg relative flex h-full min-h-screen text-ink">
      <div className="grid-overlay" aria-hidden />
      <aside
        style={navPane.style}
        className={cn(
          "relative z-10 flex flex-none flex-col border-r border-hairline bg-slate/70 backdrop-blur-2xl",
          !navDragging && "transition-[width]",
          navCollapsed ? "w-[56px]" : "w-[var(--pane-w)]",
        )}
      >
        {!navCollapsed && <SplitHandle pane={navPane} side="left" label="导航栏宽度" onDragChange={setNavDragging} />}
        <div className={cn("flex h-14 items-center gap-2.5 border-b border-hairline px-4", navCollapsed && "justify-center px-0")}>
          <span className="grid h-9 w-9 flex-none place-items-center rounded-tile bg-gradient-to-br from-chrome to-chrome-2 text-chrome-ink shadow-lg shadow-chrome/20">
            <Layers className="h-[18px] w-[18px]" />
          </span>
          {!navCollapsed && (
            <span className="leading-tight">
              <span className="block text-subtitle font-semibold tracking-tight">
                H3 <span className="text-ink-dim">Studio</span>
              </span>
              <span className="label-mono block">Creative Pipeline</span>
            </span>
          )}
        </div>

        {/* 流水线：只有进了某个项目才出现，且是当前项目的阶段 */}
        {project && !navCollapsed && <div className="label-mono border-b border-hairline px-4 py-2.5">当前项目</div>}
        <nav className="flex flex-col gap-2 px-3 py-3">
          {project &&
            STAGES.map((s) => (
              <SideLink key={s.key} to={`/p/${project.id}/${s.to}`} label={s.label} icon={s.icon} collapsed={navCollapsed} active={pathname.includes(`/${s.to}`)} />
            ))}
          {project && !navCollapsed && <div className="my-1 border-t border-hairline" />}
          <SideLink to="/" label="仪表盘" icon={Film} collapsed={navCollapsed} active={pathname === "/"} />
          <SideLink to="/workflows" label="工作流库" icon={ScrollText} collapsed={navCollapsed} />
          <SideLink to="/history" label="生成历史" icon={History} collapsed={navCollapsed} active={pathname.startsWith("/history")} />
          <SideLink to="/trash" label="生成回收站" icon={Trash2} collapsed={navCollapsed} active={pathname.startsWith("/trash")} />
          <SideLink to="/settings/gen" label="设置" icon={Settings2} collapsed={navCollapsed} active={pathname.startsWith("/settings")} />
          <button
            onClick={() => setModelConfig(true)}
            type="button"
            aria-label="模型配置"
            title="模型配置"
            className={cn("flex items-center gap-3 rounded-ctl px-3 py-2.5 text-note text-ink-dim hover:bg-sheen hover:text-ink", navCollapsed && "justify-center px-0")}
          >
            <span className="grid h-8 w-8 shrink-0 place-items-center rounded-tile bg-sheen">
              <Cpu className="h-4 w-4" />
            </span>
            {!navCollapsed && "模型配置"}
          </button>
        </nav>

        <div className="mt-auto flex flex-col gap-1 border-t border-hairline p-3">
          <button
            onClick={() => setNavCollapsed(!navCollapsed)}
            type="button"
            aria-label={navCollapsed ? "展开侧栏" : "收起侧栏"}
            className="flex items-center gap-2 rounded-ctl px-2 py-1.5 text-note text-ink-mute hover:bg-sheen hover:text-ink"
            title={navCollapsed ? "展开侧栏" : "收起侧栏"}
          >
            {navCollapsed ? <Plus className="h-3.5 w-3.5" /> : <Minus className="h-3.5 w-3.5" />}
            {!navCollapsed && "收起"}
          </button>
          <button
            onClick={toggleTheme}
            type="button"
            aria-label={theme === "dark" ? "切换到浅色主题" : "切换到深色主题"}
            className="flex items-center gap-2 rounded-ctl px-2 py-1.5 text-note text-ink-mute hover:bg-sheen hover:text-ink"
          >
            {theme === "dark" ? <Sun className="h-3.5 w-3.5" /> : <Moon className="h-3.5 w-3.5" />}
            {!navCollapsed && (theme === "dark" ? "浅色" : "深色")}
          </button>
          <button
            onClick={async () => {
              await api.auth.logout();
              setUser(null);
              nav("/login");
            }}
            type="button"
            aria-label={user ? `${user.displayName}，退出登录` : "退出登录"}
            className={cn("flex items-center gap-2 rounded-ctl px-2 py-1.5 text-note text-ink-mute hover:bg-sheen hover:text-ink", navCollapsed && "justify-center")}
            title={`${user?.displayName} 退出`}
          >
            <LogOut className="h-3.5 w-3.5" />
            {!navCollapsed && user?.displayName}
          </button>
        </div>
      </aside>

      <div className="relative z-10 flex min-w-0 flex-1 flex-col">
        <Topbar />
        <main className="min-h-0 flex-1 overflow-y-auto">
          <Outlet />
        </main>
      </div>
      <ModelConfigModal open={modelConfig} onClose={() => setModelConfig(false)} projectId={id} />
    </div>
  );
}

function Minus({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 14 14" className={cn("h-3.5 w-3.5", className)} aria-hidden>
      <path d="M3 7h8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
    </svg>
  );
}

function SideLink({
  to,
  label,
  icon: Icon,
  collapsed,
  active,
}: {
  to: string;
  label: string;
  icon: typeof Film;
  collapsed: boolean;
  active?: boolean;
}) {
  return (
    <NavLink
      to={to}
      title={collapsed ? label : undefined}
      className={cn(
        "flex items-center gap-3 rounded-panel px-3 py-2.5 text-note font-semibold tracking-tight transition-colors",
        collapsed && "justify-center px-0",
        active
          ? "border border-chrome/40 bg-gradient-to-r from-chrome/20 via-chrome-2/15 to-chrome/5 text-ink shadow-lg shadow-chrome/10"
          : "text-ink-dim hover:border-hairline hover:bg-sheen hover:text-ink",
      )}
    >
      <span className={cn("grid h-8 w-8 flex-none place-items-center rounded-tile", active ? "bg-chrome/15 text-chrome" : "bg-sheen text-ink-mute")}>
        <Icon className="h-4 w-4" />
      </span>
      {!collapsed && <span className="truncate">{label}</span>}
    </NavLink>
  );
}

/** 顶栏右侧的实例健康条 —— 团队最需要一眼知道的是「哪台机器能用、有几台在花钱」 */
function Topbar() {
  const { id } = useParams();
  const api = useApi();
  const { data: projects } = useProjects();
  const { data: instances } = useInstances();
  const { data: jobs } = useJobs(id ?? null);
  const project = projects?.find((p) => p.id === id);
  const [busy, setBusy] = useState(false);
  const nav = useNavigate();

  const running = jobs?.filter((j) => j.state === "running").length ?? 0;
  const queued = jobs?.filter((j) => j.state === "queued").length ?? 0;
  const up = instances?.filter((i) => i.lastProbeOk).length ?? 0;
  const total = instances?.length ?? 0;

  return (
    <header className="flex h-11 flex-none items-center gap-3 border-b border-rule-soft bg-panel px-3">
      <div className="flex min-w-0 items-baseline gap-2">
        {project ? (
          <>
            <button onClick={() => nav("/")} className="truncate text-body font-semibold hover:underline">
              {project.name}
            </button>
            <span className="text-note text-ink-mute">{project.synopsis}</span>
          </>
        ) : (
          <span className="text-body font-semibold">{id ? "项目" : "工作台"}</span>
        )}
      </div>

      <div className="ml-auto flex items-center gap-3">
        {(running > 0 || queued > 0) && (
          <button
            onClick={() => id && nav(`/p/${id}/queue`)}
            className="flex items-center gap-1.5 rounded-ctl border border-rule px-2 py-1 text-note text-ink-dim hover:bg-raised"
          >
            <Spinner className="h-3 w-3 text-state-running" />
            <span className="mono">{running}</span> 在跑
            {queued > 0 && (
              <>
                ·<span className="mono"> {queued}</span> 排队
              </>
            )}
          </button>
        )}

        <div className="flex items-center gap-2">
          <span className="label">
            可用 <span className="mono">{up}/{total}</span>
          </span>
          {instances?.slice(0, 4).map((i) => (
            <span key={i.id} title={`${i.name} — ${i.lastProbeOk ? "可用" : i.lastError ?? "未探活"}`}>
              <MachChip placement={i.placement} />
            </span>
          ))}
        </div>

        <Button
          size="sm"
          variant="quiet"
          loading={busy}
          onClick={async () => {
            setBusy(true);
            if (instances) await Promise.all(instances.map((i) => api.instances.probe(i.id)));
            setBusy(false);
          }}
        >
          重新探活
        </Button>
      </div>
    </header>
  );
}

/** 桌面工具：窄屏不裁剪布局，直接说明原因 */
export function PcWall({ children }: { children: React.ReactNode }) {
  const [w, setW] = useState(window.innerWidth);
  useEffect(() => {
    const on = () => setW(window.innerWidth);
    window.addEventListener("resize", on);
    return () => window.removeEventListener("resize", on);
  }, []);
  if (w < 1024) {
    return (
      <div className="grid min-h-screen place-items-center bg-slate p-8 text-center">
        <div className="max-w-sm space-y-2">
          <h1 className="text-title font-semibold">需要 1024 像素以上的桌面浏览器</h1>
          <p className="text-body leading-relaxed text-ink-mute">
            这里的时间轴、关键帧对比和参数面板都要横向空间才成立。手机端只适合看进度，请回到电脑前继续。
          </p>
        </div>
      </div>
    );
  }
  return <>{children}</>;
}
