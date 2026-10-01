/**
 * 剧本版本列表：一行 = 一版正文（含回收站里的）。
 *
 * 剧本页与「生成历史」页共用这一份 —— 两边各自实现的话，"切版本前先把用户手改的那份
 * 存成一版" 这类保护迟早只有一边有。切版本的动作全部走 useScriptVersionActions.switchTo，
 * 组件里不许自己拼 set-current + 写回正文。
 */

import { useState } from "react";
import { Badge, Button, Empty, Modal, Panel } from "./ui";
import { ConfirmSheet, type ConfirmRequest } from "../routes/project/assets/common";
import { useProject, useScriptVersionActions, useScriptVersions, useVersionMutations } from "../lib/hooks";
import type { ScriptVersionRow } from "../lib/types";

/** 快照里有多少实体。0 就整段不说，别显示「0 角 0 场 0 镜」装成有内容 */
const snapshotNote = (v: ScriptVersionRow) => {
  const s = v.snapshot ?? {};
  const n = [s.characters?.length ?? 0, s.scenes?.length ?? 0, s.shots?.length ?? 0];
  return n.some((x) => x > 0) ? ` · 快照 ${n[0]} 角 / ${n[1]} 场 / ${n[2]} 镜` : "";
};

export function ScriptVersionList({ projectId, title }: { projectId: string; title?: string }) {
  const { data: project } = useProject(projectId);
  const { data: rows } = useScriptVersions(projectId, true);
  const muts = useVersionMutations(projectId);
  // 切版本不能在这里自己拼：它会漏掉「用户手改过就先存一版」，那一漏就是吃掉手打的字
  const actions = useScriptVersionActions(projectId);
  const [reading, setReading] = useState<ScriptVersionRow | null>(null);
  const [confirm, setConfirm] = useState<ConfirmRequest | null>(null);
  const [switching, setSwitching] = useState(false);
  // hook 必须全排在早退之前，否则渲染分支一变 hook 顺序就变，整页崩
  const ordered = [...(rows ?? [])].sort((a, b) => b.version - a.version);
  const head = title ?? project?.name ?? projectId;

  if (!ordered.length) {
    return (
      <Panel title={head}>
        <Empty title="没有版本记录" hint="AI 续写、改写、助手写回、拆解成功之后各会自动存一版；项目里早就写好的正文会在第一次存版时补成 V1。" />
      </Panel>
    );
  }

  const jump = (v: ScriptVersionRow) => {
    if (!project) return;
    setSwitching(true);
    void actions
      .switchTo(v, {
        workingText: project.data.rawScript,
        workingWrittenAt: project.data.scriptWrittenAt ?? project.updatedAt,
        currentText: (rows ?? []).find((x) => x.isCurrent)?.text ?? null,
      })
      .catch((e) => console.error("设为当前失败：", e))
      .finally(() => setSwitching(false));
  };

  return (
    <Panel title={head} actions={<span className="label mono">{ordered.filter((v) => !v.deletedAt).length} 版可用</span>}>
      <ul className="divide-y divide-hairline overflow-hidden rounded-ctl border border-rule-soft">
        {ordered.map((v) => (
          <li key={v.uuid} className="flex items-center gap-2.5 px-2 py-1.5">
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-1.5">
                <span className="mono text-note text-ink">V{v.version}</span>
                {v.isCurrent && <Badge tone="good">当前</Badge>}
                {v.deletedAt && <Badge tone="warn">在回收站 · 还剩 {v.daysLeft ?? 0} 天</Badge>}
                {v.backfilled && <Badge>补记</Badge>}
                <span className="label">{v.source}</span>
              </div>
              <p className="truncate text-caption text-ink-mute" title={v.text}>
                {v.text.replace(/\s+/g, " ").slice(0, 90) || "（空）"}
              </p>
              <p className="text-caption text-ink-mute">
                {/* 显示时间用 writtenAt：补存的 V1 拿的是正文上次被写的时间，不是入库时间 */}
                {new Date(v.writtenAt).toLocaleString()} · {v.text.length} 字
                {snapshotNote(v)}
                {v.backfilled ? " · 补记（非生成时刻）" : ""}
              </p>
            </div>
            <div className="flex flex-none items-center gap-1">
              <Button size="sm" variant="ghost" onClick={() => setReading(v)}>
                预览
              </Button>
              {v.deletedAt ? (
                <Button size="sm" variant="quiet" onClick={() => void muts.restoreScript.mutateAsync({ uuid: v.uuid, projectId }).catch((e) => console.error("恢复失败：", e))}>
                  恢复
                </Button>
              ) : (
                <>
                  <Button
                    size="sm"
                    variant={v.isCurrent ? "ghost" : "default"}
                    disabled={v.isCurrent || !project || switching}
                    title={project ? "把这一版写回编辑器（手改过的正文会先存成一版）" : "这个项目不在本机浏览器里"}
                    onClick={() => jump(v)}
                  >
                    {switching ? "切换中…" : "设为当前"}
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() =>
                      setConfirm({
                        title: `把 V${v.version} 移进生成回收站`,
                        confirmLabel: "移进回收站",
                        danger: true,
                        body: (
                          <p className="text-note">
                            编辑器里现在的正文<span className="text-ink">一个字都不会动</span>；这一版只是从历史里挪进回收站，
                            {v.daysLeft ?? 100} 天后自动删除。
                          </p>
                        ),
                        onConfirm: () => {
                          void muts.trashScript.mutateAsync({ uuid: v.uuid, projectId }).catch((e) => console.error("移进回收站失败：", e));
                          setConfirm(null);
                        },
                      })
                    }
                  >
                    删除
                  </Button>
                </>
              )}
            </div>
          </li>
        ))}
      </ul>

      <Modal open={!!reading} onClose={() => setReading(null)} width={860} title={reading ? `V${reading.version} 正文 · ${reading.text.length} 字` : "正文"}>
        <p className="mb-2 label">{reading ? `${new Date(reading.writtenAt).toLocaleString()} · ${reading.source}${reading.backfilled ? " · 补记（时间不是生成时刻）" : ""}` : ""}</p>
        <pre className="max-h-[62vh] overflow-auto whitespace-pre-wrap break-words rounded-ctl bg-inset p-3 text-note leading-relaxed">{reading?.text}</pre>
      </Modal>
      <ConfirmSheet request={confirm} onClose={() => setConfirm(null)} />
    </Panel>
  );
}
