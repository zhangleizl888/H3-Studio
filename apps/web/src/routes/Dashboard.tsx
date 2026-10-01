import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Archive, CircleHelp, Cpu, FolderPlus, Layers, Monitor, Moon, Plus, Sun } from "lucide-react";
import { Button, Field, Input, Modal, Panel, Textarea } from "../components/ui";
import { SplitHandle, usePane } from "../components/SplitPane";
import ModelConfigModal from "../components/ModelConfigModal";
import { useProjects } from "../lib/hooks";
import { useApi } from "../lib/apiClient";
import { useApp } from "../state/app";
import { AssetLibraryModal } from "./dashboard/AssetLibraryModal";
import { ProjectCard } from "./dashboard/ProjectCard";
import { StudioStatus } from "./dashboard/StudioStatus";
import { THEME_MODE_LABEL, useThemeCycle } from "./dashboard/useThemeCycle";

/**
 * 项目库 / STUDIO LOBBY —— 工作台的第一屏。
 *
 * 主视觉是项目卡本身：这里要回答的是「我今天要干哪个片子」，
 * 产线状态（队列、实例、磁盘）压到下面当次级信息，别抢。
 */
export default function Dashboard() {
  const pane = usePane("dashboard.side", 286, 220, 460);
  const nav = useNavigate();
  const api = useApi();
  const { data: projects } = useProjects();
  const user = useApp((s) => s.user);
  const { mode, cycle } = useThemeCycle();
  const [creating, setCreating] = useState(false);
  const [library, setLibrary] = useState(false);
  const [modelConfig, setModelConfig] = useState(false);
  const [help, setHelp] = useState(false);

  const list = projects ?? [];
  const ThemeIcon = mode === "auto" ? Monitor : mode === "light" ? Sun : Moon;

  return (
    <div className="relative min-h-full px-5 py-7 text-ink xl:px-9">
      <div className="mx-auto flex max-w-[1560px] items-start gap-7">
        {/* ───────── 左：入口栏 ───────── */}
        <aside style={pane.style} className="sticky top-7 hidden w-[var(--pane-w)] flex-none lg:block">
          <SplitHandle pane={pane} side="left" label="入口栏宽度" />
          <div className="glass rounded-sheet p-5 shadow-2xl shadow-black/30">
            <div className="flex items-center gap-2.5">
              <span className="grid h-8 w-8 flex-none place-items-center rounded-tile bg-gradient-to-br from-chrome to-chrome-2 text-chrome-ink">
                <Layers className="h-4 w-4" aria-hidden />
              </span>
              <span className="leading-tight">
                <span className="block text-body font-semibold tracking-tight">
                  H3 <span className="text-ink-dim">Studio</span>
                </span>
                <span className="label-mono block">Creative Pipeline</span>
              </span>
            </div>

            <div className="label-mono mt-6 text-chrome/70">Studio Lobby</div>
            <h1 className="mt-2 text-display font-semibold leading-none tracking-tight">项目库</h1>
            <p className="mt-3 text-note leading-relaxed text-ink-mute">
              从故事草稿到制片导出，集中管理你的短剧项目和可复用视觉资产。
            </p>

            <div className="mt-6 space-y-2">
              <LobbyPrimary icon={<Plus className="h-4 w-4" aria-hidden />} onClick={() => setCreating(true)}>
                新建项目
              </LobbyPrimary>
              <LobbyAction icon={<Archive className="h-4 w-4" aria-hidden />} onClick={() => setLibrary(true)}>
                资产库
              </LobbyAction>
              <LobbyAction icon={<Cpu className="h-4 w-4" aria-hidden />} onClick={() => setModelConfig(true)}>
                模型配置
              </LobbyAction>
              <LobbyAction icon={<CircleHelp className="h-4 w-4" aria-hidden />} onClick={() => setHelp(true)}>
                帮助
              </LobbyAction>
              <LobbyAction icon={<ThemeIcon className="h-4 w-4" aria-hidden />} onClick={cycle}>
                {THEME_MODE_LABEL[mode]}
              </LobbyAction>
            </div>

            <div className="mt-6 border-t border-hairline pt-4">
              <p className="text-caption leading-relaxed text-ink-mute">
                左侧导航整合了项目创建、资产库和配置入口，方便快速切换。
              </p>
              {user && <p className="label-mono mt-3">Operator · {user.displayName}</p>}
            </div>
          </div>
        </aside>

        {/* ───────── 右：项目网格 ───────── */}
        <main className="min-w-0 flex-1 space-y-7">
          <header className="lg:hidden">
            <div className="label-mono text-chrome/70">Studio Lobby</div>
            <h1 className="mt-1.5 text-display font-semibold tracking-tight">项目库</h1>
            <div className="mt-4 grid grid-cols-2 gap-2">
              <LobbyPrimary icon={<Plus className="h-3.5 w-3.5" aria-hidden />} onClick={() => setCreating(true)}>
                新建项目
              </LobbyPrimary>
              <LobbyAction icon={<Archive className="h-3.5 w-3.5" aria-hidden />} onClick={() => setLibrary(true)}>
                资产库
              </LobbyAction>
              <LobbyAction icon={<Cpu className="h-3.5 w-3.5" aria-hidden />} onClick={() => setModelConfig(true)}>
                模型配置
              </LobbyAction>
              <LobbyAction icon={<ThemeIcon className="h-3.5 w-3.5" aria-hidden />} onClick={cycle}>
                {THEME_MODE_LABEL[mode]}
              </LobbyAction>
            </div>
          </header>

          <section className="space-y-4">
            <div className="flex flex-wrap items-baseline gap-3">
              <h2 className="text-title font-semibold tracking-tight">全部项目</h2>
              <span className="label-mono">{list.length} projects</span>
              <span className="ml-auto text-caption text-ink-mute">项目与资产只存在这台浏览器；换机器要先在导出页打包。</span>
            </div>

            <div className="grid grid-cols-1 gap-5 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
              <button
                onClick={() => setCreating(true)}
                className="group flex h-[244px] flex-col items-center justify-center gap-5 rounded-sheet border border-dashed border-chrome/25 bg-sheen backdrop-blur-xl transition-colors hover:border-chrome/60 hover:bg-chrome/10"
              >
                <span className="grid h-14 w-14 place-items-center rounded-panel border border-chrome/25 bg-chrome/10 text-chrome transition-colors group-hover:bg-chrome/20">
                  <FolderPlus className="h-5 w-5" aria-hidden />
                </span>
                <span className="label-mono">Create New Project</span>
              </button>

              {list.map((p) => (
                <ProjectCard key={p.id} project={p} />
              ))}
            </div>
          </section>

          <StudioStatus projectId={list[0]?.id} />
        </main>
      </div>

      <NewProjectModal
        open={creating}
        onClose={() => setCreating(false)}
        onCreate={async (name, synopsis) => {
          const p = await api.projects.create(name, synopsis);
          setCreating(false);
          nav(`/p/${p.id}/script`);
        }}
      />
      <AssetLibraryModal open={library} onClose={() => setLibrary(false)} projects={list} />
      <ModelConfigModal open={modelConfig} onClose={() => setModelConfig(false)} />
      <HelpModal open={help} onClose={() => setHelp(false)} />
    </div>
  );
}

function LobbyPrimary({ icon, children, onClick }: { icon: React.ReactNode; children: React.ReactNode; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className="flex w-full items-center gap-2.5 rounded-panel bg-gradient-to-r from-chrome to-chrome-2 px-4 py-3 text-body font-semibold tracking-tight text-chrome-ink shadow-lg shadow-chrome/20 transition-[filter] hover:brightness-110"
    >
      {icon}
      {children}
    </button>
  );
}

function LobbyAction({ icon, children, onClick }: { icon: React.ReactNode; children: React.ReactNode; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className="flex w-full items-center gap-2.5 rounded-panel border border-hairline bg-sheen px-4 py-3 text-body text-ink-dim transition-colors hover:border-chrome/35 hover:bg-sheen hover:text-ink"
    >
      <span className="text-ink-mute">{icon}</span>
      {children}
    </button>
  );
}

function NewProjectModal({
  open,
  onClose,
  onCreate,
}: {
  open: boolean;
  onClose: () => void;
  onCreate: (name: string, synopsis: string) => Promise<void>;
}) {
  const [name, setName] = useState("");
  const [synopsis, setSynopsis] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      await onCreate(name.trim(), synopsis.trim());
      setName("");
      setSynopsis("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "创建失败");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="新建项目"
      footer={
        <>
          <Button variant="quiet" onClick={onClose} disabled={busy}>
            取消
          </Button>
          <Button variant="primary" loading={busy} disabled={!name.trim()} onClick={() => void submit()}>
            创建并进入剧本
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <Field label="项目名">
          <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="长夜出租车 · 第 1 集" autoFocus />
        </Field>
        <Field label="一句话梗概" hint="拆解剧本时当全局上下文用；也是项目卡上那两行字。别写太长。">
          <Textarea
            rows={3}
            value={synopsis}
            onChange={(e) => setSynopsis(e.target.value)}
            placeholder="末班车上，司机发现后座乘客与自己失踪十年的搭档同貌。"
          />
        </Field>
        {error && <p className="text-note text-state-fail">{error}</p>}
        <p className="text-caption leading-snug text-ink-mute">
          建好之后默认落在「剧本创作」阶段：贴整段剧本文本，本地模型会拆成角色、场景和节拍。
        </p>
      </div>
    </Modal>
  );
}

function HelpModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  return (
    <Modal open={open} onClose={onClose} title="这个项目库怎么用" width={680} footer={<Button variant="primary" onClick={onClose}>知道了</Button>}>
      <div className="space-y-4">
        <Panel title="五个阶段，一条片子">
          <ol className="space-y-2 text-note leading-relaxed text-ink-dim">
            <li>
              <span className="text-ink">① 剧本创作</span> —— 贴剧本文本，拆成角色 / 场景 / 节拍，再排分镜。
            </li>
            <li>
              <span className="text-ink">② 场景角色</span> —— 定妆照与场景图，外形写成结构化 traits，可一键存进资产库跨项目复用。
            </li>
            <li>
              <span className="text-ink">③ AI工作台</span> —— 首尾帧 + H3 三段式提示词，逐镜派发、看进度、重试。
            </li>
            <li>
              <span className="text-ink">④ 制片导出</span> —— 拼成片、打包素材，出 EDL / XML 进剪映或达芬奇。
            </li>
            <li>
              <span className="text-ink">⑤ 资产管理</span> —— 提示词与产物的总账，回头改风格不用重跑全片。
            </li>
          </ol>
        </Panel>

        <Panel title="色标只回答一个问题：这块东西是谁产的">
          <div className="space-y-2 text-note text-ink-dim">
            <p>
              <span className="machchip" data-mach="local"><i />本机</span> 免费、可控，产物能直接读盘；
              <span className="machchip ml-2" data-mach="cloud_self"><i />自建云</span> 自己的卡加隧道；
              <span className="machchip ml-2" data-mach="cloud_runninghub"><i />RunningHub</span> 按秒计费，琥珀色同时也是「在花钱」的颜色。
            </p>
            <p className="text-note text-ink-mute">图形和颜色同时出现，不靠颜色单独表意。</p>
          </div>
        </Panel>

        <Panel title="一台卡上的显存接力">
          <p className="text-note leading-relaxed text-ink-dim">
            本机只有一张卡时，24 GB 装不下「文本模型 + 图像/视频底模」。开启单卡仲裁后，派发本地产物会先停掉文本模型进程，出完片再拉回来。
            手动让位与恢复在「模型配置 → 全局配置」里，开关本身由后端环境变量 <span className="mono text-ink">H3_GPU_ARBITER</span> 决定。
          </p>
        </Panel>

        <Panel title="东西存在哪、坏了看哪里">
          <ul className="space-y-1.5 text-note leading-relaxed text-ink-dim">
            <li>· 项目、资产库、上传的参考图：这台浏览器的 IndexedDB。换浏览器或换机器只能靠导出文件。</li>
            <li>· 生成实例、文本后端、工作流、任务队列：后端服务，局域网内多台机器共用。</li>
            <li>· 任务失败的原因和处置建议在项目「任务队列」页；实例连不上先点顶栏的「重新探活」。</li>
          </ul>
        </Panel>
      </div>
    </Modal>
  );
}
