import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Button, Field, Input, Modal } from "./ui";
import { useApi } from "../lib/apiClient";
import { devAutoLogin } from "../lib/tokens";
import { MIN_PASSWORD_LEN, passwordProblems } from "../lib/password";
import { useApp } from "../state/app";
import { cn } from "../lib/utils";

/** 改密成功后留给登录页的那句话走 sessionStorage，不走 router state：
 *  setUser(null) 会让 RequireAuth 抢先 `<Navigate to="/login">` 一次，那一跳不带 state，会把提示洗掉。 */
export const LOGIN_NOTICE_KEY = "h3studio.loginNotice";

/**
 * 改自己的登录口令。
 *
 * 只在打开时挂载（AppShell 里用 `{open && <…>}`），关掉即卸载：
 * 三个口令输入框留在 state 里，下次打开还带着上一次填的明文，这不能留。
 */
export default function ChangePasswordModal({ onClose }: { onClose: () => void }) {
  const api = useApi();
  const nav = useNavigate();
  const user = useApp((s) => s.user);
  const setUser = useApp((s) => s.setUser);
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [again, setAgain] = useState("");
  const [show, setShow] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const touched = next !== "" || again !== "";
  const problems: string[] = [];
  if (touched) {
    problems.push(...passwordProblems(next));
    if (next !== "" && next === current) problems.push("新口令和当前口令一样");
    if (again !== "" && again !== next) problems.push("两次输入不一样");
  }
  const canSubmit = current !== "" && next !== "" && again !== "" && problems.length === 0;

  async function submit() {
    if (busy) return;
    if (current === "") {
      setError("要先填当前口令。");
      return;
    }
    if (!canSubmit) {
      setError(problems.length > 0 ? `新口令不合规则：${problems.join("；")}` : "要把当前口令、新口令和确认项都填上。");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await api.auth.changePassword(current, next);
      // 后端已经把该账号的会话全部吊销、本地票也清掉了：直接落到登录页，别让人以为还登着
      sessionStorage.setItem(LOGIN_NOTICE_KEY, "口令已更新，这个账号在所有浏览器里的登录都作废了，请用新口令重新登录。");
      setUser(null);
      onClose();
      nav("/login", { replace: true });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const type = show ? "text" : "password";
  const onEnter = (e: React.KeyboardEvent) => e.key === "Enter" && submit();

  return (
    <Modal
      open
      onClose={onClose}
      title="修改登录口令"
      width={460}
      footer={
        <>
          <Button variant="quiet" onClick={onClose}>
            取消
          </Button>
          <Button variant="primary" loading={busy} disabled={!canSubmit} onClick={submit}>
            改口令
          </Button>
        </>
      }
    >
      <div className="space-y-3 p-3">
        <p className="text-note leading-snug text-ink-mute">
          当前账号 <span className="mono text-ink-dim">{user?.username}</span>（{user?.displayName}，{user?.role}）。
          改口令要先证明你知道现在这一个。
        </p>

        <Field label="当前口令">
          <Input type={type} value={current} onChange={(e) => setCurrent(e.target.value)} className="mono" autoComplete="current-password" onKeyDown={onEnter} />
        </Field>
        <Field label="新口令" hint={`至少 ${MIN_PASSWORD_LEN} 位；password、admin123 这类常见口令会被拒。`}>
          <Input type={type} value={next} onChange={(e) => setNext(e.target.value)} className="mono" autoComplete="new-password" onKeyDown={onEnter} placeholder={`至少 ${MIN_PASSWORD_LEN} 位`} />
        </Field>
        <Field label="再输一次新口令">
          <Input type={type} value={again} onChange={(e) => setAgain(e.target.value)} className="mono" autoComplete="new-password" onKeyDown={onEnter} />
        </Field>

        <div className="flex items-center justify-between">
          <Button size="sm" variant="ghost" onClick={() => setShow((v) => !v)}>
            {show ? "隐藏输入" : "显示输入"}
          </Button>
          <span
            className={cn(
              "text-caption leading-snug",
              !touched ? "text-ink-mute" : problems.length > 0 ? "text-state-fail" : "text-ink-dim",
            )}
          >
            {!touched ? "新口令要填两次，两次一样才给提交。" : problems.length > 0 ? problems.join("；") : "这条口令够用。"}
          </span>
        </div>

        {error && (
          <div role="alert" className="rounded-ctl border border-state-fail/45 bg-state-fail/10 px-2.5 py-2 text-note text-state-fail">
            {error}
          </div>
        )}

        <div className="rounded-ctl border border-rule-soft bg-slate px-2.5 py-2 text-note leading-snug text-ink-dim">
          改完会立刻发生两件事：这个账号在<b className="font-semibold text-ink">所有浏览器里的登录都失效</b>（包括你正在用的这一页），
          以及审计里留下一条 <span className="mono">auth.password_changed</span>。
          CLI 和智能体用的 <span className="mono">agent token</span> 不受影响 —— 那是显式发出去的长期钥匙，改口令不该把它们踢下线。
        </div>

        {devAutoLogin && user?.username === devAutoLogin.username && (
          <p className="text-caption leading-snug text-ink-mute">
            这台机器开着自动登录，用的是默认演示凭据 <span className="mono">admin</span> /{" "}
            <span className="mono">{devAutoLogin.password}</span>。改成别的口令后，把
            <span className="mono"> VITE_DEV_PASS</span> 设成新口令，或者用
            <span className="mono"> VITE_DEV_AUTOLOGIN=false</span> 关掉自动登录，否则每次都要手动输。
          </p>
        )}
      </div>
    </Modal>
  );
}
