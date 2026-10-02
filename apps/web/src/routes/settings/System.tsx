import { useState } from "react";
import {
  AlertTriangle,
  Database,
  Files,
  FolderClock,
  FolderCog,
  FolderOutput,
  HardDrive,
  History,
  Trash2,
  Wrench,
} from "lucide-react";
import { Badge, Button, Copyable, Field, Input, KeyVal, Modal, Panel, StateGlyph, Toggle } from "../../components/ui";
import { useAuditTrail, useInstances, useSavePaths, useStorage, useSystemPaths, useSystemBackup } from "../../lib/hooks";
import { useApi } from "../../lib/apiClient";
import { cn, fmtBytes, fmtTime } from "../../lib/utils";

const GB = 1024 ** 3;

type PathKey = "media" | "tmp" | "comfyOutput" | "ffmpeg";
type Paths = Record<PathKey, string>;

/** 后端记的是动作码（job.retry 这种），界面上给中文；认不出的原样显示，别编一个好看的假名字 */
const AUDIT_LABEL: Record<string, string> = {
  "auth.login": "登录",
  "auth.login_failed": "登录失败",
  "auth.bootstrap": "建首个账号",
  "job.dispatch": "开始派发",
  "job.retry": "重试任务",
  "job.cancel": "取消任务",
  "script.version.create": "存剧本版本",
  "script.version.current": "剧本设为当前版",
  "script.version.trash": "剧本版进回收站",
  "script.version.restore": "剧本版恢复",
  "script.version.purge": "剧本版彻底删除",
  "trash.media": "产物进回收站",
  "trash.restore": "从回收站恢复",
  "trash.project": "项目进回收站",
  "trash.gc": "手动回收",
  "trash.purge_expired": "回收站到期清理",
  "trash.purge_row": "单条彻底删除",
  "user.create": "建用户",
  "user.update": "改用户",
  "user.delete": "删用户",
  "system.paths": "目录设置变更",
};

/** 详情是后端塞的 jsonb：挑几个真有信息量的键说人话，剩下的收成「另 n 项」 */
function auditDetail(d: Record<string, unknown> | null | undefined): string {
  if (!d || !Object.keys(d).length) return "—";
  const parts: string[] = [];
  for (const key of ["message", "reason", "title", "kind", "state", "version", "promoted", "deleted", "bytes", "dryRun"]) {
    if (d[key] !== undefined && d[key] !== null && d[key] !== "") parts.push(`${key}=${String(d[key])}`);
  }
  const rest = Object.keys(d).length - parts.length;
  if (!parts.length) return Object.keys(d).slice(0, 4).join("、");
  return rest > 0 ? `${parts.join(" · ")} · 另 ${rest} 项` : parts.join(" · ");
}

export default function System() {
  const { data: st, error: stError } = useStorage();
  const api = useApi();
  const paths = useServerPaths();
  const { data: serverPaths } = useSystemPaths();
  const { data: audit, isLoading: auditLoading, error: auditError } = useAuditTrail();
  const { data: backup, error: backupError } = useSystemBackup();
  const [dryRun, setDryRun] = useState(true);
  const [report, setReport] = useState<{ dry: boolean; reclaimableBytes: number; orphans: number } | null>(null);
  const [gcBusy, setGcBusy] = useState(false);
  const [gcError, setGcError] = useState<string | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);

  const mediaGb = st ? st.mediaBytes / GB : 0;
  const tmpGb = st ? st.tmpBytes / GB : 0;
  const freeGb = st ? st.freeBytes / GB : 0;
  const totalGb = Math.round(mediaGb + tmpGb + freeGb);

  async function runGc(dry: boolean) {
    setGcBusy(true);
    setGcError(null);
    try {
      setReport({ dry, ...(await api.system.gc(dry)) });
    } catch (e) {
      setGcError((e as Error).message);
    } finally {
      setGcBusy(false);
    }
  }

  return (
    <div className="space-y-4 p-4">
      <header className="flex items-end justify-between">
        <div>
          <h1 className="text-title font-semibold">系统</h1>
          <p className="text-note text-ink-mute">这台机器自己的事：磁盘、目录、清理、备份，以及谁动过什么。</p>
        </div>
        <span className="text-caption text-ink-mute">这里的改动只影响本机，不动任何项目的剧本与镜头</span>
      </header>

      {/* ① 存储 */}
      <Panel
        title={
          <span className="flex items-center gap-2">
            <HardDrive className="h-3.5 w-3.5" />
            存储
          </span>
        }
        actions={<span className="text-caption text-ink-mute">整块盘 {totalGb ? `${totalGb} GB` : "未知"}</span>}
      >
        {stError ? (
          <div className="flex items-start gap-2 text-note text-state-fail">
            <AlertTriangle className="mt-0.5 h-3.5 w-3.5 flex-none" />
            <span>磁盘信息读不出来：{stError.message}</span>
          </div>
        ) : !st ? (
          <div className="text-note text-ink-mute">正在统计磁盘占用。</div>
        ) : (
          <div className="space-y-3">
            <div className="flex h-2 overflow-hidden rounded-hairline bg-track" title={`媒体 ${mediaGb.toFixed(1)} GB · 临时 ${tmpGb.toFixed(1)} GB · 剩余 ${freeGb.toFixed(0)} GB`}>
              <div className="h-full bg-state-idle" style={{ width: `${(mediaGb / totalGb) * 100}%` }} />
              <div className="stripe h-full bg-state-queued" style={{ width: `${(tmpGb / totalGb) * 100}%` }} />
            </div>
            <div className="flex flex-wrap gap-x-4 gap-y-1 text-caption text-ink-mute">
              <span>
                <i aria-hidden className="mr-1 inline-block h-2 w-2 bg-state-idle" />
                媒体 <span className="mono text-ink-dim">{mediaGb.toFixed(1)} GB</span>
              </span>
              <span>
                <i aria-hidden className="mr-1 inline-block h-2 w-2 bg-state-queued" />
                临时 <span className="mono text-ink-dim">{tmpGb.toFixed(1)} GB</span>
              </span>
              <span>
                <i aria-hidden className="mr-1 inline-block h-2 w-2 bg-track" />
                剩余 <span className="mono text-ink-dim">{freeGb.toFixed(0)} GB</span>
              </span>
            </div>

            <div className="grid gap-3 sm:grid-cols-2">
              <KeyVal
                items={[
                  ["媒体总占用", <span className="mono">{fmtBytes(st.mediaBytes)}</span>],
                  ["临时目录", <span className="mono">{fmtBytes(st.tmpBytes)}</span>],
                  ["剩余空间", <span className="mono">{fmtBytes(st.freeBytes)}</span>],
                ]}
              />
              <KeyVal
                items={[
                  ["文件数", <span className="mono">{st.mediaCount}</span>],
                  ["有产物的项目", <span className="mono">{st.byProject.length}</span>],
                  ["媒体目录", <span className="mono truncate">{serverPaths?.media.path ?? st?.root ?? "—"}</span>],
                ]}
              />
            </div>

            {freeGb < 100 && (
              <div className="flex items-start gap-2 rounded-ctl border border-state-fail/40 bg-state-fail/8 px-2.5 py-2 text-note leading-snug">
                <AlertTriangle className="mt-0.5 h-3.5 w-3.5 flex-none text-state-fail" />
                <span className="text-ink-dim">
                  只剩 {freeGb.toFixed(0)} GB。H3 双底模就要 85 GB，一次全质量批次能吃掉几个 GB —— 先清理再派发。
                </span>
              </div>
            )}

            <div>
              <div className="label mb-1 flex items-center gap-1.5">
                <Files className="h-3 w-3" />
                按项目占用
              </div>
              <div className="overflow-x-auto rounded-ctl border border-rule-soft">
                <table className="w-full border-collapse text-note">
                  <thead>
                    <tr className="border-b border-rule text-left">
                      <th className="label px-2 py-1.5 font-normal">项目</th>
                      <th className="label w-[150px] px-2 py-1.5 font-normal">占媒体的比例</th>
                      <th className="label px-2 py-1.5 text-right font-normal">占用</th>
                      <th className="label px-2 py-1.5 text-right font-normal">文件数</th>
                    </tr>
                  </thead>
                  <tbody>
                    {st.byProject.length === 0 ? (
                      <tr>
                        <td colSpan={4} className="px-2 py-3 text-center text-note text-ink-mute">
                          还没有项目落过媒体文件。
                        </td>
                      </tr>
                    ) : (
                      st.byProject.map((p) => (
                        <tr key={p.projectId} className="border-b border-rule-soft align-middle">
                          <td className="px-2 py-1.5">
                            <span className="text-note">{p.name}</span>
                            <span className="mono ml-1.5 text-caption text-ink-mute">{p.projectId}</span>
                          </td>
                          <td className="px-2 py-1.5">
                            <div className="h-1.5 overflow-hidden rounded-hairline bg-track">
                              <div
                                className="h-full bg-ink-dim"
                                style={{ width: `${(p.bytes / Math.max(1, st.mediaBytes)) * 100}%` }}
                              />
                            </div>
                          </td>
                          <td className="mono px-2 py-1.5 text-right">{p.bytes > 0 ? fmtBytes(p.bytes) : "无文件"}</td>
                          <td className="mono px-2 py-1.5 text-right">{p.count}</td>
                        </tr>
                      ))
                    )}
                  </tbody>
                </table>
              </div>
              <p className="mt-1.5 text-caption leading-snug text-ink-mute">
                归档的项目也在这里列着 —— 归档只影响列表筛选，不删任何文件。
              </p>
            </div>
          </div>
        )}
      </Panel>

      {/* ② 媒体回收：预演与真删是两个动作，不合并成一个按钮 */}
      <Panel
        title={
          <span className="flex items-center gap-2">
            <FolderClock className="h-3.5 w-3.5" />
            媒体回收
          </span>
        }
        actions={<span className="text-caption text-ink-mute">回收走 system.gc</span>}
      >
        <div className="space-y-3">
          <Toggle
            checked={dryRun}
            onChange={(v) => {
              setDryRun(v);
              setReport(null);
            }}
            label="只做预演"
            hint="预演只报「能回收多少、有多少孤儿行」，一个字节都不删。"
          />

          <div className="flex flex-wrap items-center gap-2">
            <Button
              size="sm"
              variant={dryRun ? "default" : "primary"}
              loading={gcBusy}
              onClick={() => (dryRun ? runGc(true) : setConfirmOpen(true))}
            >
              {dryRun ? "跑一次预演" : "实际回收"}
            </Button>
            {!dryRun && <Badge>这会真的删文件，要再确认一次</Badge>}
          </div>

          {gcError && (
            <div className="rounded-ctl border border-state-fail/40 px-2.5 py-2 text-note text-state-fail">
              {gcError}
            </div>
          )}

          {report && (
            <div
              className={cn(
                "rounded-ctl border border-rule-soft bg-slate px-2.5 py-2 text-note leading-relaxed text-ink-dim",
                !report.dry && "border-l-2 border-l-ink-dim",
              )}
            >
              <div className="flex items-center gap-2">
                <StateGlyph state={report.dry ? "idle" : "succeeded"} />
                <span className="text-ink">{report.dry ? "预演结果" : "已经回收"}</span>
              </div>
              <div className="mono mt-1 text-note">
                {report.dry ? "可回收" : "本次回收"} {fmtBytes(report.reclaimableBytes)} · 孤儿行 {report.orphans} 个（有记录、没文件）
              </div>
              <p className="mt-1 text-caption leading-snug text-ink-mute">
                {report.dry
                  ? "上面是「如果现在动手会回收多少」。关掉预演、再点一次并确认，才会真删。"
                  : "到期的版本连文件一起删了，孤儿行也清了；没满保留期的和还在用的一律没动。"}
              </p>
            </div>
          )}

          <div className="space-y-1 rounded-ctl border border-rule-soft px-2.5 py-2 text-note leading-snug text-ink-dim">
            <p>
              这一条清的是<span className="text-ink">已经在生成回收站里待满保留期</span>的版本（连文件一起删），
              外加「库里有记录、盘上已经没文件」的孤儿行。<span className="text-ink">在用的关键帧、成片与参考图不动。</span>
            </p>
            <p className="text-ink-mute">
              保留期 <span className="mono">100 天</span>：<span className="mono">H3_TRASH_RETENTION_DAYS</span> 可改。
              到期回收由后端自动跑（开机一趟 + 每 6 小时一趟），不用人守着；这个按钮是"现在就跑一趟"，
              默认预演，数字对不上就别关掉预演。
            </p>
          </div>
        </div>
      </Panel>

      {/* ③ 目录 */}
      <Panel
        title={
          <span className="flex items-center gap-2">
            <FolderCog className="h-3.5 w-3.5" />
            目录
          </span>
        }
      >
        <div className="space-y-3">
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="媒体目录" hint="所有产物落在这里。数据库只存相对 data/media/ 的路径，绝不存外链。" className="sm:col-span-2">
              <Input
                value={paths.value.media}
                onChange={(e) => paths.set("media", e.target.value)}
                className="mono w-full"
                placeholder="留空 = 用后端默认那一份"
              />
            </Field>
            <Field label="临时目录" hint="重试留下的中间帧和切片都在这里，回收主要清这一处。">
              <span className="flex items-center gap-1.5">
                <Trash2 className="h-3.5 w-3.5 flex-none text-ink-mute" />
                <Input
                  value={paths.value.tmp}
                  onChange={(e) => paths.set("tmp", e.target.value)}
                  className="mono flex-1"
                  placeholder="留空 = 用后端默认那一份"
                />
              </span>
            </Field>
            <Field
              label="ComfyUI output 目录"
              hint={`本机实例直读盘，省一次下载。这一格的真源在「设置 → 生成实例」的 ${paths.comfySource} 上，这里只读。`}
            >
              <span className="flex items-center gap-1.5">
                <FolderOutput className="h-3.5 w-3.5 flex-none text-ink-mute" />
                <Input
                  value={paths.value.comfyOutput}
                  readOnly
                  title="改它请去「设置 → 生成实例」"
                  className="mono flex-1 text-ink-mute"
                />
              </span>
            </Field>
            <Field label="ffmpeg 可执行文件" hint="整片合成、抽帧、导出都用这一个二进制。" className="sm:col-span-2">
              <span className="flex items-center gap-1.5">
                <Wrench className="h-3.5 w-3.5 flex-none text-ink-mute" />
                <Input
                  value={paths.value.ffmpeg}
                  onChange={(e) => paths.set("ffmpeg", e.target.value)}
                  className="mono flex-1"
                  placeholder="留空 = 用后端默认那一份"
                />
              </span>
            </Field>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <Button size="sm" variant="primary" disabled={!paths.dirty} onClick={paths.save}>
              保存目录设置
            </Button>
            {paths.error ? (
              <span className="text-caption text-state-fail">{paths.error}</span>
            ) : paths.dirty ? (
              <span className="text-caption text-ink-mute">改动还没存。</span>
            ) : paths.savedAt ? (
              <span className="text-caption text-ink-mute">
                已存到后端（{fmtTime(paths.savedAt)}）。
                {paths.needsRestart.length
                  ? `其中 ${paths.needsRestart.join("、")} 要重启后端才换 —— 旧产物的路径是相对这两个根目录存的，运行中改会让它们指错。`
                  : "ffmpeg 下一次导出就用新值。"}
              </span>
            ) : (
              <span className="text-caption text-ink-mute">上面显示的是后端此刻真正在用的路径。</span>
            )}
            {(paths.dirty || paths.savedAt) && (
              <Button size="sm" variant="quiet" onClick={paths.reset}>
                恢复默认
              </Button>
            )}
          </div>

          <p className="text-caption leading-snug text-ink-mute">
            丑话在前面：这四个路径现在只存在这台浏览器的本地设置里，后端还没读它们。
            要生效得等后端把它们读进媒体落盘与合成逻辑 —— 在那之前改了也不会动磁盘上的任何东西，
            换一台机器或清了浏览器数据也得重填。
          </p>
        </div>
      </Panel>

      {/* ④ 操作记录 */}
      <Panel
        title={
          <span className="flex items-center gap-2">
            <History className="h-3.5 w-3.5" />
            操作记录
          </span>
        }
        actions={<span className="text-caption text-ink-mute">只读。密钥一律掩码成 abcd****wxyz</span>}
        dense
      >
        <div className="border-b border-rule-soft px-3 py-1.5 text-caption leading-snug text-ink-mute">
          记的是配置变更、用户与项目操作、派发与导出、登录失败。每条都带操作者和时间，出事能回溯到谁在哪台机器上做的。
        </div>
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-note">
            <thead>
              <tr className="border-b border-rule text-left">
                <th className="label px-2 py-1.5 font-normal">时间</th>
                <th className="label px-2 py-1.5 font-normal">操作者</th>
                <th className="label px-2 py-1.5 font-normal">动作</th>
                <th className="label px-2 py-1.5 font-normal">对象</th>
                <th className="label px-2 py-1.5 font-normal">详情</th>
              </tr>
            </thead>
            <tbody>
              {auditLoading && (
                <tr>
                  <td colSpan={5} className="px-2 py-3 text-note text-ink-mute">
                    正在读审计表…
                  </td>
                </tr>
              )}
              {auditError && (
                <tr>
                  <td colSpan={5} className="px-2 py-3 text-note text-state-fail">
                    读不到操作记录：{String((auditError as Error).message || auditError)}
                  </td>
                </tr>
              )}
              {audit && audit.items.length === 0 && !auditLoading && (
                <tr>
                  <td colSpan={5} className="px-2 py-3 text-note text-ink-mute">
                    还没有记到任何操作。这里记的是配置变更、派发与重试、目录设置、回收与登录 ——
                    没发生过的事不该出现在这张表上。
                  </td>
                </tr>
              )}
              {audit?.items.map((a, i) => (
                <tr key={`${a.ts}-${a.action}-${i}`} className="border-b border-rule-soft align-top">
                  <td className="mono px-2 py-1.5 whitespace-nowrap text-note text-ink-mute">{fmtTime(a.ts)}</td>
                  <td className="px-2 py-1.5 whitespace-nowrap">
                    {a.actor === "system" ? (
                      <span className="mono text-note text-ink-mute">system</span>
                    ) : (
                      <span className="text-note">{a.actor}</span>
                    )}
                  </td>
                  <td className="px-2 py-1.5 whitespace-nowrap">
                    <Badge>{AUDIT_LABEL[a.action] ?? a.action}</Badge>
                  </td>
                  <td className="mono px-2 py-1.5 text-note text-ink-dim">{a.target}</td>
                  <td className="px-2 py-1.5 text-note leading-snug text-ink-mute">{auditDetail(a.detail)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="px-3 py-2 text-caption leading-snug text-ink-mute">
          这张表读的是后端 `audit_log`（{audit ? `${audit.items.length} / ${audit.total} 条` : "读取中…"}）。
          只记后端真做过的动作：登录与登录失败、派发/重试/取消、版本与回收站、用户与目录设置。
          实例地址返回给前端时 key 已经脱敏，前端再兜一次 —— 原始密钥不进浏览器包、不进日志、不进报错栈。
        </p>
      </Panel>

      {/* ⑤ 备份 */}
      <Panel
        title={
          <span className="flex items-center gap-2">
            <Database className="h-3.5 w-3.5" />
            备份
          </span>
        }
        actions={<span className="text-caption text-ink-mute">{backup?.database ?? "读不到连接串"}</span>}
      >
        {backupError && (
          <p className="px-3 py-2 text-note text-state-fail">读备份命令失败：{String((backupError as Error).message || backupError)}</p>
        )}
        {backup && !backup.available && (
          <p className="px-3 py-2 text-note leading-snug text-ink-mute">{backup.note}</p>
        )}
        {backup?.available && (
          <>
            <ul className="space-y-2.5">
              <li className="space-y-1">
                <div className="text-note text-ink-dim">导数据库（自定义格式，能挑表恢复）</div>
                <Copyable text={backup.dump} className="block rounded-panel bg-inset px-2 py-1 text-note text-ink-dim" />
              </li>
              <li className="space-y-1">
                <div className="text-note text-ink-dim">检查 dump 里都有什么</div>
                <Copyable text={backup.list} className="block rounded-panel bg-inset px-2 py-1 text-note text-ink-dim" />
              </li>
              <li className="space-y-1">
                <div className="text-note text-ink-dim">恢复（--clean --if-exists，库不存在时才需要先建）</div>
                <Copyable text={backup.restore} className="block rounded-panel bg-inset px-2 py-1 text-note text-ink-dim" />
              </li>
            </ul>
            <p className="mt-2 text-caption leading-snug text-ink-mute">
              这三条是后端按<b>当前真实连接串</b>拼的：内嵌 pgserver 的端口每次启动都会变，写死端口的命令第二天就作废。
              pg_dump 用的是 {backup.pgDump}。
            </p>
          </>
        )}
        <p className="mt-2 text-caption leading-snug text-ink-mute">
          备份是两条腿：dump 管用户、任务、媒体索引、剧本版本与工作流，媒体文件本身归文件系统快照管 —— dump 里不含视频。
          备份别和媒体目录放同一块盘，那块盘写满了两边一起没。
        </p>
      </Panel>

      {confirmOpen && (
        <ConfirmGcModal
          onClose={() => setConfirmOpen(false)}
          onConfirm={async () => {
            await runGc(false);
            setConfirmOpen(false);
          }}
          busy={gcBusy}
        />
      )}
    </div>
  );
}

/** 真删不是一键的事：先看清范围，再输入两个字确认 */
function ConfirmGcModal({
  onClose,
  onConfirm,
  busy,
}: {
  onClose: () => void;
  onConfirm: () => Promise<void>;
  busy: boolean;
}) {
  const [typed, setTyped] = useState("");
  return (
    <Modal
      open
      onClose={onClose}
      title="实际回收媒体"
      width={440}
      footer={
        <>
          <Button variant="quiet" onClick={onClose}>
            取消
          </Button>
          <Button variant="danger" loading={busy} disabled={typed.trim() !== "回收"} onClick={onConfirm}>
            开始回收
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <p className="text-note leading-relaxed text-ink-dim">
          这一步会真的删文件：回收站里满 <span className="mono">100 天</span>的那些版本就此没了，也再也恢复不回来。
          在用的关键帧、成片、参考图不动；没满保留期的回收站条目也不动。
        </p>
        <p className="text-note leading-snug text-ink-mute">
          建议先跑一次预演看清范围。数字对不上就先别点。
        </p>
        <Field label="输入「回收」以确认">
          <Input value={typed} onChange={(e) => setTyped(e.target.value)} placeholder="回收" />
        </Field>
      </div>
    </Modal>
  );
}

/** 后端还没读这些路径，先把配置留在本机，别让填过的东西丢 */
/**
 * 目录设置表单：初值一律来自后端「现在真正生效的那一份」（/api/system/paths）。
 * 以前这个 hook 读的是 localStorage + 一组 D:/h3studio/... 常量，于是页面显示的媒体目录
 * 和后端实际写的目录可以完全无关 —— 照着页面去备份会备到一个空目录。
 * ComfyUI output 不归这份设置管（它是「生成实例」上的一条配置），所以那一格只读。
 */
function useServerPaths() {
  const { data: sp } = useSystemPaths();
  const { data: instances } = useInstances();
  const mut = useSavePaths();
  const [edited, setEdited] = useState<Partial<Paths>>({});
  const local = (instances ?? []).find((i) => i.placement === "local");
  const value: Paths = {
    media: edited.media ?? sp?.media.path ?? "",
    tmp: edited.tmp ?? sp?.tmp.path ?? "",
    comfyOutput: local?.localOutputRoot ?? "这台实例没配直读目录（产物走下载）",
    ffmpeg: edited.ffmpeg ?? sp?.ffmpeg.path ?? "",
  };
  const dirty = Object.keys(edited).length > 0;
  const restart = mut.data?.needsRestart ?? sp?.needsRestart ?? [];
  return {
    value,
    dirty,
    comfySource: local ? `${local.name}${local.placement === "local" ? " · 本机实例" : ""}` : "（没有本机实例）",
    ffmpegSource: sp?.ffmpeg.source ?? "—",
    needsRestart: restart,
    savedAt: mut.isSuccess ? new Date().toISOString() : null,
    error: mut.error ? String((mut.error as Error).message || mut.error) : null,
    busy: mut.isPending,
    set: (k: PathKey, v: string) => setEdited((prev) => ({ ...prev, [k]: v })),
    save: () => {
      mut.mutate({ media: value.media.trim() || null, tmp: value.tmp.trim() || null, ffmpeg: value.ffmpeg.trim() || null });
      setEdited({});
    },
    reset: () => {
      mut.mutate({ media: null, tmp: null, ffmpeg: null });
      setEdited({});
    },
  };
}
