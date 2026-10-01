import { useState } from "react";
import { ShieldCheck, UserRoundPlus, UsersRound } from "lucide-react";
import { Badge, Button, Empty, Field, Input, Modal, Panel, Select } from "../../components/ui";
import { useUserMutations, useUsers } from "../../lib/hooks";
import type { Role, User } from "../../lib/types";
import { cn, fmtMoney, fmtTime, ago } from "../../lib/utils";
import { useApp } from "../../state/app";

const ROLE_LABEL: Record<Role, string> = { admin: "管理员", editor: "编辑", viewer: "只读" };

/** 权限只有这三句话，不往外扩 */
const ROLE_RULE: Record<Role, string> = {
  admin: "可改实例与模型配置",
  editor: "可派发任务与改项目",
  viewer: "只读",
};

const COMMON_WEAK = ["password", "passw0rd", "admin123", "123456", "111111", "000000", "qwerty", "h3studio"];

/** 弱口令判定与后端首启规则用同一套：不达标就不给建号 */
function pwProblems(pw: string, username: string): string[] {
  if (!pw) return ["还没填密码"];
  const out: string[] = [];
  const low = pw.toLowerCase();
  if (pw.length < 10) out.push(`至少 10 位，现在 ${pw.length} 位`);
  if (/^\d+$/.test(pw)) out.push("不能是纯数字");
  if (/^(.)\1+$/.test(pw)) out.push("不能是同一个字符一直重复");
  if (COMMON_WEAK.some((w) => low.includes(w))) out.push("命中常见弱口令表");
  if (username.trim() && low.includes(username.trim().toLowerCase())) out.push("不能包含用户名");
  if (pw !== pw.trim()) out.push("开头或结尾有空格");
  return out;
}

export default function Users() {
  const { data: users, error: listError } = useUsers();
  const mut = useUserMutations();
  const me = useApp((s) => s.user);
  const [createOpen, setCreateOpen] = useState(false);
  const [deleting, setDeleting] = useState<User | null>(null);

  const activeAdmins = (users ?? []).filter((u) => u.role === "admin" && u.isActive).length;

  function patch(id: string, body: Partial<User>) {
    mut.update.mutate({ id, body });
  }

  return (
    <div className="space-y-4 p-4">
      <header className="flex items-end justify-between">
        <div>
          <h1 className="text-title font-semibold">用户与配额</h1>
          <p className="text-note text-ink-mute">
            这套工具跑在内网，一台机器多人同时用。角色决定你能改配置还是只能派发任务。
          </p>
        </div>
        <Button size="sm" icon={<UserRoundPlus className="h-3.5 w-3.5" />} onClick={() => setCreateOpen(true)}>
          新建用户
        </Button>
      </header>

      {/* ① 三种角色：一行一个，能做什么写死 */}
      <Panel
        title={
          <span className="flex items-center gap-2">
            <ShieldCheck className="h-3.5 w-3.5" />
            三种角色
          </span>
        }
        actions={<span className="text-caption text-ink-mute">密码强度与首启建号是同一条规则</span>}
      >
        <ul className="grid gap-2 sm:grid-cols-3">
          {(["admin", "editor", "viewer"] as const).map((r) => (
            <li key={r} className="space-y-1 rounded-ctl border border-rule-soft bg-slate px-2.5 py-2">
              <div className="flex items-baseline gap-2">
                <span className="mono text-note">{r}</span>
                <span className="text-caption text-ink-mute">{ROLE_LABEL[r]}</span>
              </div>
              <p className="text-note leading-snug text-ink-dim">{ROLE_RULE[r]}</p>
            </li>
          ))}
        </ul>
        <p className="mt-2 text-caption leading-snug text-ink-mute">
          首次启动时必须先建一个 admin 才进得来设置页，同一个密码规则在那一步就会生效：弱口令直接拒绝，不给建号。
          这里建号也照同一条规则拦。
        </p>
      </Panel>

      {/* ② 用户表 */}
      <Panel
        title={
          <span className="flex items-center gap-2">
            <UsersRound className="h-3.5 w-3.5" />
            用户
            {users && <Badge>{users.length}</Badge>}
          </span>
        }
        actions={<span className="text-caption text-ink-mute">今日数据来自任务记账，按自然日重置</span>}
        dense
      >
        <div className="border-b border-rule-soft px-3 py-1.5 text-caption leading-snug text-ink-mute">
          RunningHub 按秒计费，所以并发上限与每日消费上限要能按人调。
          <span className="text-ink">说清楚：这两项目前只是记账字段。</span>
          后端队列调度器还没实现，超限的任务照样会被派发 —— 现在只能靠这里写着、靠人看着。
        </div>

        {listError ? (
          <div className="px-3 py-6 text-center text-note text-state-fail">
            用户列表读不出来：{listError.message}
          </div>
        ) : !users ? (
          <div className="px-3 py-6 text-center text-note text-ink-mute">正在读取用户列表。</div>
        ) : users.length === 0 ? (
          <div className="p-3">
            <Empty
              title="还没有任何用户"
              hint="这台机器第一次打开时必须先建一个 admin，弱口令会被拒绝。"
              action={
                <Button size="sm" variant="primary" onClick={() => setCreateOpen(true)}>
                  新建管理员
                </Button>
              }
            />
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full border-collapse text-note">
              <thead>
                <tr className="border-b border-rule text-left">
                  <th className="label px-2 py-1.5 font-normal">显示名</th>
                  <th className="label px-2 py-1.5 font-normal">用户名</th>
                  <th className="label px-2 py-1.5 font-normal">角色</th>
                  <th className="label px-2 py-1.5 font-normal">状态</th>
                  <th className="label w-[112px] px-2 py-1.5 font-normal">同时在跑上限</th>
                  <th className="label w-[132px] px-2 py-1.5 font-normal">每日消费上限</th>
                  <th className="label px-2 py-1.5 text-right font-normal">今日任务</th>
                  <th className="label px-2 py-1.5 text-right font-normal">今日花费</th>
                  <th className="label px-2 py-1.5 font-normal">上次登录</th>
                  <th className="label px-2 py-1.5 text-right font-normal">操作</th>
                </tr>
              </thead>
              <tbody>
                {users.map((u) => (
                  <UserRow
                    key={u.id}
                    u={u}
                    isMe={u.id === me?.id}
                    isLastActiveAdmin={u.role === "admin" && u.isActive && activeAdmins <= 1}
                    busy={mut.update.isPending}
                    onPatch={patch}
                    onRemove={() => setDeleting(u)}
                  />
                ))}
              </tbody>
            </table>
          </div>
        )}

        {mut.update.isError && (
          <div className="border-t border-rule-soft px-3 py-2 text-note leading-snug text-state-fail">
            改动没写进去：{mut.update.error?.message ?? "后端没应答"}。表格里的值会退回后端记着的那份。
          </div>
        )}
      </Panel>

      {createOpen && (
        <CreateUserModal
          onClose={() => setCreateOpen(false)}
          onCreate={async (body) => {
            await mut.create.mutateAsync(body);
            setCreateOpen(false);
          }}
          creating={mut.create.isPending}
          error={mut.create.error?.message ?? null}
        />
      )}

      {deleting && <DeleteUserModal user={deleting} onClose={() => setDeleting(null)} />}
    </div>
  );
}

function UserRow({
  u,
  isMe,
  isLastActiveAdmin,
  busy,
  onPatch,
  onRemove,
}: {
  u: User;
  isMe: boolean;
  isLastActiveAdmin: boolean;
  busy: boolean;
  onPatch: (id: string, body: Partial<User>) => void;
  onRemove: () => void;
}) {
  const quota = u.quota ?? { concurrentJobs: 0 };
  return (
    <tr className={cn("border-b border-rule-soft align-middle", !u.isActive && "opacity-60")}>
      <td className="px-2 py-1.5">
        <span className="text-body">{u.displayName}</span>
        {isMe && <span className="ml-1.5 text-caption text-ink-mute">（你）</span>}
      </td>
      <td className="px-2 py-1.5 mono text-note text-ink-dim">{u.username}</td>
      <td className="px-2 py-1.5">
        <Badge>{u.role}</Badge>
      </td>
      <td className="px-2 py-1.5">
        <span
          className={cn("text-note", u.isActive ? "text-ink-dim" : "text-state-canceled")}
          title={u.isActive ? undefined : "停用只挡登录，不影响他已经跑完的历史"}
        >
          {u.isActive ? "启用中" : "已停用"}
        </span>
      </td>
      <td className="px-2 py-1.5">
        <QuotaCell
          value={quota.concurrentJobs}
          disabled={!u.isActive || u.role === "viewer"}
          disabledHint={
            !u.isActive ? "先启用这个人，上限才有意义" : "只读账号不派发任务，没有并发上限"
          }
          onCommit={(n) => onPatch(u.id, { quota: { ...quota, concurrentJobs: n ?? 0 } })}
        />
      </td>
      <td className="px-2 py-1.5">
        <QuotaCell
          value={quota.dailyMoneyLimit ?? null}
          blankMeansUnlimited
          disabled={!u.isActive}
          disabledHint="先启用这个人，上限才有意义"
          onCommit={(n) => onPatch(u.id, { quota: { ...quota, dailyMoneyLimit: n } })}
        />
      </td>
      <td className="px-2 py-1.5 text-right mono">{u.usage?.jobsToday ?? "—"}</td>
      <td className="px-2 py-1.5 text-right mono">{fmtMoney(u.usage?.moneyToday)}</td>
      <td className="px-2 py-1.5 whitespace-nowrap">
        <span className="mono text-note text-ink-dim">{fmtTime(u.lastLoginAt)}</span>
        <span className="ml-1 text-caption text-ink-mute">{ago(u.lastLoginAt)}</span>
      </td>
      <td className="px-2 py-1.5 text-right">
        <span className="inline-flex items-center gap-1.5">
          {u.isActive ? (
            <Button
              size="sm"
              variant="quiet"
              disabled={busy || isMe || isLastActiveAdmin}
              title={
                isMe
                  ? "不能停用自己：内网工具没有恢复入口"
                  : isLastActiveAdmin
                    ? "还要留一个启用中的 admin，否则没人能改配置"
                    : "只挡他登录，已产出的任务与媒体都留着"
              }
              onClick={() => onPatch(u.id, { isActive: false })}
            >
              停用
            </Button>
          ) : (
            <Button size="sm" variant="quiet" disabled={busy} onClick={() => onPatch(u.id, { isActive: true })}>
              重新启用
            </Button>
          )}
          <Button
            size="sm"
            variant="danger"
            disabled={isMe || isLastActiveAdmin}
            title={isMe ? "不能删自己" : isLastActiveAdmin ? "至少留一个启用中的 admin" : "要删就删，删之前先想清楚能不能停用"}
            onClick={onRemove}
          >
            删除
          </Button>
        </span>
      </td>
    </tr>
  );
}

/** 上限就地改：回车或离开输入格就提交，空值对消费上限表示「不限」 */
function QuotaCell({
  value,
  onCommit,
  disabled,
  disabledHint,
  blankMeansUnlimited,
}: {
  value: number | null;
  onCommit: (n: number | null) => void;
  disabled?: boolean;
  disabledHint?: string;
  blankMeansUnlimited?: boolean;
}) {
  const [draft, setDraft] = useState(value === null ? "" : String(value));
  const [saved, setSaved] = useState(false);
  const trimmed = draft.trim();
  const parsed = trimmed === "" ? null : Number(trimmed);
  const invalid = trimmed !== "" && (!Number.isFinite(parsed) || (parsed as number) < 0);

  function commit() {
    if (invalid || disabled) return;
    onCommit(parsed);
    setSaved(true);
    setTimeout(() => setSaved(false), 1200);
  }

  if (disabled) {
    return (
      <span className="mono text-note text-ink-mute" title={disabledHint}>
        {value === null ? "不限" : value}
      </span>
    );
  }

  return (
    <span className="inline-flex items-center gap-1">
      {blankMeansUnlimited && <span className="text-caption text-ink-mute">¥</span>}
      <Input
        className="mono h-6 w-[70px] text-note"
        value={draft}
        inputMode="decimal"
        placeholder={blankMeansUnlimited ? "不限" : "0"}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter") commit();
        }}
      />
      {invalid ? (
        <span className="text-caption text-state-fail">要非负数字</span>
      ) : saved ? (
        <span className="text-caption text-ink-mute" title="已经发给后端；没写进去会在表下方说明">
          已提交
        </span>
      ) : null}
    </span>
  );
}

function CreateUserModal({
  onClose,
  onCreate,
  creating,
  error,
}: {
  onClose: () => void;
  onCreate: (body: { username: string; displayName: string; role: Role; password: string }) => Promise<void>;
  creating: boolean;
  error: string | null;
}) {
  const [username, setUsername] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [role, setRole] = useState<Role>("editor");
  const [password, setPassword] = useState("");
  const [showPw, setShowPw] = useState(false);

  const problems = pwProblems(password, username);
  const nameBad = username.trim() !== "" && !/^[a-z0-9._-]{2,32}$/.test(username.trim());
  const canSubmit = username.trim() !== "" && displayName.trim() !== "" && problems.length === 0 && !nameBad;

  return (
    <Modal
      open
      onClose={onClose}
      title="新建用户"
      width={520}
      footer={
        <>
          <Button variant="quiet" onClick={onClose}>
            取消
          </Button>
          <Button
            variant="primary"
            loading={creating}
            disabled={!canSubmit}
            onClick={() =>
              onCreate({
                username: username.trim(),
                displayName: displayName.trim(),
                role,
                // 建号即视为已通知本人；首次登录应由后端强制改密
                password,
              })
            }
          >
            建号
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <Field label="用户名" hint="登录用的名字，小写字母、数字、点、下划线、连字符，2–32 位。建好后不改。">
          <Input
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            className="mono"
            placeholder="liyu"
            autoComplete="off"
          />
        </Field>
        <Field label="显示名" hint="出现在列表和任务记录里的名字，用中文就行。">
          <Input value={displayName} onChange={(e) => setDisplayName(e.target.value)} placeholder="李昱" />
        </Field>
        <Field label="角色" hint={`${ROLE_LABEL[role]} —— ${ROLE_RULE[role]}。`}>
          <Select value={role} onChange={(e) => setRole(e.target.value as Role)} className="w-full">
            {(["admin", "editor", "viewer"] as const).map((r) => (
              <option key={r} value={r}>
                {r} · {ROLE_RULE[r]}
              </option>
            ))}
          </Select>
        </Field>
        <Field
          label="初始密码"
          hint="建号只把这个人加进列表，不代他登录。把密码当面或走内网聊天给他，让他自己改。"
        >
          <div className="flex items-center gap-1.5">
            <Input
              type={showPw ? "text" : "password"}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="mono flex-1"
              placeholder="至少 10 位，别用口令表里的"
              autoComplete="new-password"
            />
            <Button size="sm" variant="quiet" onClick={() => setShowPw((v) => !v)}>
              {showPw ? "隐藏" : "显示"}
            </Button>
          </div>
        </Field>

        {/* 弱口令判定过程摊开写，不只报一句「太弱」 */}
        <div
          className={cn(
            "rounded-ctl border px-2.5 py-2 text-note leading-snug",
            password === ""
              ? "border-rule-soft bg-slate text-ink-mute"
              : problems.length > 0
                ? "border-state-fail/40 bg-state-fail/8 text-state-fail"
                : "border-rule-soft bg-slate text-ink-dim",
          )}
        >
          {password === "" ? (
            <span>密码不足 10 位、纯数字、连续重复、命中常见弱口令表或包含用户名，都会被拒绝。</span>
          ) : problems.length > 0 ? (
            <ul className="space-y-0.5">
              <li className="font-medium">弱口令，不给建号：</li>
              {problems.map((p) => (
                <li key={p}>· {p}</li>
              ))}
            </ul>
          ) : (
            <span>这条密码够用。首次启动建 admin 走的是同一套判定。</span>
          )}
        </div>

        {nameBad && (
          <div className="rounded-ctl border border-state-fail/40 bg-state-fail/8 px-2.5 py-1.5 text-note leading-snug text-state-fail">
            用户名格式不合规则，用不到空格的小写字母数字组合。
          </div>
        )}

        {error && (
          <div className="rounded-ctl border border-state-fail/45 bg-state-fail/10 px-2.5 py-2 text-note text-state-fail">
            {error}
          </div>
        )}

        <p className="text-caption leading-snug text-ink-mute">
          后端接口目前只收用户名、显示名、角色和这一个初始密码；并发与消费上限建完在表里改（默认 2，只读账号 0）。
          这条初始密码只在请求里走一次 —— 真后端必须自己再判一遍强度并只存哈希，前端的拦截不算安全边界。
        </p>
      </div>
    </Modal>
  );
}

function DeleteUserModal({ user, onClose }: { user: User; onClose: () => void }) {
  const mut = useUserMutations();
  const [typed, setTyped] = useState("");
  return (
    <Modal
      open
      onClose={onClose}
      title={`删除用户 ${user.displayName}`}
      width={440}
      footer={
        <>
          <Button variant="quiet" onClick={onClose}>
            取消
          </Button>
          <Button
            variant="danger"
            loading={mut.remove.isPending}
            disabled={typed !== user.username}
            onClick={() => mut.remove.mutate(user.id, { onSuccess: onClose })}
          >
            确认删除
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <p className="text-note leading-relaxed text-ink-dim">
          要删的是 <span className="text-ink">{user.displayName}</span>
          （<span className="mono">{user.username}</span>，{ROLE_LABEL[user.role]}）。删掉之后他的密码、上限和用量记录一起没了，
          历史任务里的归属会变成认不出的 id。
        </p>
        <p className="text-note leading-snug text-ink-mute">
          他跑过的任务产出的媒体文件不会因为删用户而被清理。只是不想让他再登录的话，用停用。
        </p>
        <Field label="输入用户名以确认" hint="这一步是防止手滑，删完没有恢复入口。">
          <Input value={typed} onChange={(e) => setTyped(e.target.value)} className="mono" placeholder={user.username} />
        </Field>
        {mut.remove.error && (
          <div className="rounded-ctl border border-state-fail/45 bg-state-fail/10 px-2.5 py-2 text-note text-state-fail">
            {mut.remove.error.message}
          </div>
        )}
      </div>
    </Modal>
  );
}
