import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Button, Field, Input } from "../components/ui";
import { useApi } from "../lib/apiClient";
import { useApp } from "../state/app";

export default function Login() {
  const api = useApi();
  const nav = useNavigate();
  const setUser = useApp((s) => s.setUser);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const isMock = import.meta.env.VITE_USE_MOCK !== "false";

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const { user } = await api.auth.login(username.trim(), password);
      setUser(user);
      nav("/", { replace: true });
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid min-h-screen grid-cols-1 bg-slate lg:grid-cols-[1fr_420px]">
      {/* 左侧不放标语，放这台机器实际要做的事 —— 让人在登录前就知道工具边界 */}
      <section className="hidden flex-col justify-between border-r border-rule-soft bg-inset p-8 lg:flex">
        <div className="text-body font-semibold tracking-tight text-ink-dim">
          H3 <span className="text-ink-mute">Studio</span>
        </div>
        <div className="space-y-5">
          <h1 className="max-w-[22ch] text-display leading-[1.2] font-semibold tracking-tight">
            剧本到成片，中间每一步都记在同一台机器上。
          </h1>
          <ol className="max-w-[46ch] space-y-1.5 text-body leading-relaxed text-ink-dim">
            <li>
              <span className="mono text-ink-mute">01</span> 剧本拆解、分镜、H3 提示词改写走本地文本模型
            </li>
            <li>
              <span className="mono text-ink-mute">02</span> 关键帧与视频走 ComfyUI：本机、自建云、或 RunningHub
            </li>
            <li>
              <span className="mono text-ink-mute">03</span> 任务在服务端排队，关掉页面照样跑完
            </li>
            <li>
              <span className="mono text-ink-mute">04</span> 产物落本地磁盘，不存会过期的外链
            </li>
          </ol>
        </div>
        <p className="max-w-[52ch] text-note leading-relaxed text-ink-mute">
          界面不用装饰性颜色：色相只表示这台片子出自哪台机器，状态靠亮度和图形。这样你在判断画面本身时，不会被界面蒙色。
        </p>
      </section>

      <section className="flex items-center justify-center p-6">
        <form onSubmit={submit} className="w-full max-w-[330px] space-y-4">
          <div>
            <h2 className="text-title font-semibold">登录工作台</h2>
            <p className="mt-0.5 text-note text-ink-mute">内网多用户；角色决定你能不能改实例配置和派发任务。</p>
          </div>

          <Field label="用户名">
            <Input value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" placeholder="admin" required />
          </Field>
          <Field label="密码">
            <Input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" placeholder="至少 4 位" required />
          </Field>

          {error && (
            <div className="rounded-ctl border border-state-fail/45 bg-state-fail/10 px-2.5 py-2 text-note text-state-fail">
              {error}
            </div>
          )}

          <Button type="submit" variant="primary" loading={busy} className="w-full">
            进入
          </Button>

          {isMock && (
            <p className="text-caption leading-snug text-ink-mute">
              当前是界面原型模式（后端未接入）。用户名 <span className="mono">admin</span>、<span className="mono">liyu</span>、
              <span className="mono">zhous</span>，密码任意 ≥4 位。
            </p>
          )}
        </form>
      </section>
    </div>
  );
}
