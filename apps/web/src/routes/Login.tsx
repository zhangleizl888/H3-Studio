import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { LOGIN_NOTICE_KEY } from "../components/ChangePasswordModal";
import { Button, Field, Input } from "../components/ui";
import { demoAccount, devAutoLogin } from "../lib/tokens";
import { useApi } from "../lib/apiClient";
import { useApp } from "../state/app";

export default function Login() {
  const api = useApi();
  const nav = useNavigate();
  // 改口令成功后是从这里被弹出来的，那句说明要跟着走一趟，不能只写在弹窗里。
  // 读一次就抹掉：再刷新这页就不该重复播报。
  const [notice] = useState<string | null>(() => {
    try {
      const n = sessionStorage.getItem(LOGIN_NOTICE_KEY);
      if (n) sessionStorage.removeItem(LOGIN_NOTICE_KEY);
      return n;
    } catch {
      return null;
    }
  });
  const setUser = useApp((s) => s.setUser);
  // 装机的第一屏要「看得见门钥匙」：字段直接预填演示账号，而不是留个空框让人猜。
  // 自动登录关掉（桌面包就是）时这页才是唯一的入口，预填比提示管用。
  const [username, setUsername] = useState(demoAccount.username);
  const [password, setPassword] = useState(demoAccount.password);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const isMock = import.meta.env.VITE_USE_MOCK !== "false";

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    // 靠浏览器原生的 required 气泡提示，在这台机器上等于没说：窗体不在前台时那个气泡
    // 根本不出现，点「进入」看着就是没反应。改成界面自己把话说出来。
    if (!username.trim() || !password) {
      setError(!username.trim() ? "要先填用户名。" : "要先填密码。");
      return;
    }
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
    <div className="app-bg relative grid min-h-screen grid-cols-1 lg:grid-cols-[1fr_420px]">
      <div className="grid-overlay" aria-hidden />
      {/* 左侧不放标语，放这台机器实际要做的事 —— 让人在登录前就知道工具边界 */}
      <section className="glass hidden flex-col justify-between p-8 lg:flex">
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
        <form noValidate onSubmit={submit} className="w-full max-w-[330px] space-y-4">
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

          {notice && !error && (
            <div className="rounded-ctl border border-rule-soft bg-slate px-2.5 py-2 text-note leading-snug text-ink-dim">{notice}</div>
          )}

          {error && (
            <div role="alert" className="rounded-ctl border border-state-fail/45 bg-state-fail/10 px-2.5 py-2 text-note text-state-fail">
              {error}
            </div>
          )}

          <Button type="submit" variant="primary" loading={busy} className="w-full">
            进入
          </Button>

          {isMock ? (
            <p className="text-caption leading-snug text-ink-mute">
              当前是界面原型模式（后端未接入）。用户名 <span className="mono">admin</span>、<span className="mono">liyu</span>、
              <span className="mono">zhous</span>，密码任意 ≥4 位。
            </p>
          ) : (
            <p className="text-caption leading-snug text-ink-mute">
              默认账号 <span className="mono">{demoAccount.username}</span> /{" "}
              <span className="mono">{demoAccount.password}</span>，已经替你填好，直接点「进入」。它只在服务只听环回、
              且库里一个账号都没有时才会被预置{devAutoLogin ? "；开发模式还会自动登进去" : "；登进来后请在左栏「改密」改掉它"}。
              库里已经有账号、或 <span className="mono">--host</span> 挂了局域网地址，就没有这个默认口令。
            </p>
          )}
        </form>
      </section>
    </div>
  );
}
