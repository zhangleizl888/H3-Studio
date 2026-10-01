/**
 * 剧本页右下角的悬浮改稿助手。
 *
 * 折叠时是一个浮标，点开是一整块对话面板：会话历史、多轮指令、待确认的改稿。
 *
 * 一条底线：模型返回的 scriptText **绝不自动进编辑器**。它是一份完整正文，
 * 写回等于整篇替换，而 27B 最常见的违约是只回被改的那一段 —— 所以必须停在
 * 「待确认」，让人看完行级差异再点。后端量过体量会带 warnings 回来，这里原样显示。
 *
 * 会话历史存在 project.data.scriptChats（A 方案：项目实体留在 IndexedDB，
 * 跟着导出导入走，服务端不建表）。
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { Check, Eye, FileUp, GitCompare, MessageSquareText, Paperclip, Plus, Send, Trash2, TriangleAlert, X } from "lucide-react";
import { Badge, Button, Select, Spinner, Textarea } from "../../../components/ui";
import { useApi } from "../../../lib/apiClient";
import { useLlmRun } from "../../../lib/hooks";
import type { ScriptChatMessage, ScriptChatSession } from "../../../lib/types";
import { cn, uid } from "../../../lib/utils";
import { diffLines, takeHunks } from "./diff";
import { errText } from "./merge";

const CHAT_ETA = "改稿要整篇回吐：本机 27B 单槽串行，稿子越长越慢，几分钟是常态";

/** 超过这个秒数，界面上补一句「慢不等于卡住」—— 拿不到步数，只能靠话说清楚 */
const SLOW_MS = 90_000;

/** 后端 parse.py 认的扩展名。写在这儿是为了让 file picker 少让人撞一次 400 */
const ACCEPT = ".txt,.md,.fdx,.fountain,.csv,.tsv,.json,.html,.htm,.docx";

const STARTERS = ["把第二场的对白收得更克制，别解释情绪", "结尾再加一个看得见的余波", "通读一遍，指出哪一场没有新信息"];

interface Props {
  projectId: string;
  sessions: ScriptChatSession[];
  /** 编辑器里的当前正文。发送时快照下来，写回前用它判断中间有没有人手改过 */
  script: string;
  backendId?: string;
  /** 分镜生成正在跑：这台模型单槽，对话得排队，先禁掉并说明为什么 */
  blocked: string | null;
  onPersist: (sessions: ScriptChatSession[]) => void;
  onWriteBack: (text: string) => void;
}

const now = () => new Date().toISOString();
const titleOf = (text: string) => text.replace(/\s+/g, " ").trim().slice(0, 20) || "新对话";
const nf = (n: number) => n.toLocaleString("zh-CN");

/**
 * 从这次请求发出起真实走了多久。
 * 后端是一次 POST、没有流也没有步数，所以这里只报「已等几秒」—— 对应规格里
 * Loading State 的「点阵 + 时间读数」，不画假进度条。
 */
function useElapsed(running: boolean) {
  const [ms, setMs] = useState(0);
  useEffect(() => {
    if (!running) {
      setMs(0);
      return;
    }
    const t0 = Date.now();
    const id = setInterval(() => setMs(Date.now() - t0), 100);
    return () => clearInterval(id);
  }, [running]);
  return ms;
}

/** 3×3 点阵：按对角「V」字序错开 90ms、650ms 一轮，读起来像机器在动而不是一张静图 */
function PixelGrid({ className }: { className?: string }) {
  return (
    <span aria-hidden className={cn("pixel-grid", className)}>
      {Array.from({ length: 9 }, (_, i) => {
        const delay = ((i % 3) + Math.abs(Math.floor(i / 3) - 1)) * 90;
        return <i key={i} style={{ ["--d" as string]: `${delay}ms` }} />;
      })}
    </span>
  );
}

/** 逐件错开入场用的序号。只错开前几件：长会话里最新一条不该为排队而迟到 */
const stagger = (i: number) => ({ ["--i" as string]: Math.min(i, 4) });

export function ChatDock(p: Props) {
  const api = useApi();
  const llmRun = useLlmRun(p.projectId);
  const [open, setOpen] = useState(false);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [attaching, setAttaching] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const [expanded, setExpanded] = useState<string | null>(null);
  const scroller = useRef<HTMLDivElement>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  // sending 给 UI 看，重入判定要一个回调里读得到的最新值
  const sendingRef = useRef(false);

  const sessions = p.sessions;
  const active = sessions.find((s) => s.id === activeId) ?? sessions[0] ?? null;

  useEffect(() => {
    if (!open || !scroller.current) return;
    scroller.current.scrollTop = scroller.current.scrollHeight;
  }, [open, active?.messages.length, sending]);

  const pending = active?.messages.filter((m) => candidateOf(m) && !m.outcome).length ?? 0;
  const waited = useElapsed(sending);

  function persist(session: ScriptChatSession) {
    const next = { ...session, updatedAt: now() };
    p.onPersist([next, ...sessions.filter((s) => s.id !== next.id)]);
  }

  function createSession(): ScriptChatSession {
    const s: ScriptChatSession = { id: uid("cs"), title: titleOf(input), createdAt: now(), updatedAt: now(), messages: [] };
    p.onPersist([s, ...sessions]);
    setActiveId(s.id);
    return s;
  }

  function removeSession(id: string) {
    const left = sessions.filter((s) => s.id !== id);
    p.onPersist(left);
    if (activeId === id) setActiveId(left[0]?.id ?? null);
  }

  function patchMessage(msgId: string, patch: Partial<ScriptChatMessage>) {
    if (!active) return;
    persist({ ...active, messages: active.messages.map((m) => (m.id === msgId ? { ...m, ...patch } : m)) });
  }

  async function send() {
    const ask = input.trim();
    if (!ask || sendingRef.current || p.blocked) return;
    const session = active ?? createSession();
    const userMsg: ScriptChatMessage = { id: uid("cm"), role: "user", text: ask, ts: now() };
    const withUser: ScriptChatSession = { ...session, title: session.messages.length ? session.title : titleOf(ask), messages: [...session.messages, userMsg] };
    persist(withUser);
    sendingRef.current = true;
    setSending(true);
    setInput("");
    const baseChars = p.script.length;
    const history = session.messages.map((m) => ({ role: m.role, content: m.text }));
    try {
      const res = await llmRun.mutateAsync({ purpose: "script_chat", input: ask, opts: { backendId: p.backendId, messages: history, script: p.script } });
      const d = (res.data ?? {}) as { reply?: unknown; scriptText?: unknown };
      const body = String(d.scriptText ?? "");
      const answer: ScriptChatMessage = {
        id: uid("cm"),
        role: "assistant",
        text: String(d.reply ?? "").trim() || "（模型没给说明，只给了正文）",
        ts: now(),
        latencyMs: res.latencyMs,
        scriptText: body.trim() ? body : undefined,
        baseChars,
        warnings: res.warnings,
      };
      persist({ ...withUser, messages: [...withUser.messages, answer] });
    } catch (e) {
      const answer: ScriptChatMessage = { id: uid("cm"), role: "assistant", text: "", ts: now(), error: errText(e) };
      persist({ ...withUser, messages: [...withUser.messages, answer] });
    } finally {
      sendingRef.current = false;
      setSending(false);
    }
  }

  /** 待确认的候选正文：AI 改稿和上传换稿走同一套确认与写回，不给其中一条开后门 */
  function candidateOf(msg: ScriptChatMessage): string | null {
    return msg.upload?.text ?? msg.scriptText ?? null;
  }

  function applyWriteBack(msg: ScriptChatMessage) {
    const text = candidateOf(msg);
    if (!text || msg.outcome) return;
    const drifted = msg.baseChars !== undefined && msg.baseChars !== p.script.length;
    const tip = drifted
      ? `这份稿子生成之后编辑器又变了（当时 ${nf(msg.baseChars as number)} 字，现在 ${nf(p.script.length)} 字）。\n仍要用它整篇替换当前正文？`
      : `确认写回？当前正文 ${nf(p.script.length)} 字会被整篇替换成 ${nf(text.length)} 字。`;
    if (!window.confirm(tip)) return;
    p.onWriteBack(text);
    patchMessage(msg.id, { outcome: "applied" });
  }

  /**
   * 上传一份稿子。解析结果只落成「换稿待确认」，绝不直接进编辑器 ——
   * 读进来的完全可能是另一部戏，自动覆盖就是拿别人的稿子盖掉用户正在写的。
   */
  async function attach(file: File | null | undefined) {
    if (!file || sendingRef.current) return;
    setAttaching(file.name);
    const session = active ?? createSession();
    try {
      const parsed = await api.parse.script(file);
      const msg: ScriptChatMessage = { id: uid("cm"), role: "user", text: `上传了 ${parsed.name}`, ts: now(), baseChars: p.script.length, upload: parsed };
      persist({ ...session, title: session.messages.length ? session.title : `换稿：${parsed.name}`, messages: [...session.messages, msg] });
    } catch (e) {
      const msg: ScriptChatMessage = { id: uid("cm"), role: "user", text: `上传了 ${file.name}`, ts: now(), error: errText(e) };
      persist({ ...session, messages: [...session.messages, msg] });
    } finally {
      setAttaching(null);
      if (fileInput.current) fileInput.current.value = "";
    }
  }

  /* 浮标和面板同时留在 DOM 里，靠透明度+位移换场。
     之前是条件渲染，面板「啪」地出现、收起时「啪」地消失 —— 规格里这一段是一次连续运动。
     收起的一方给 inert：既拿掉 Tab 顺序，也把它从读屏里摘出去（会话历史还在，收起不等于清空）。 */
  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        title={p.blocked ?? (sending ? "助手正在整篇重出这份稿子" : "和助手对话改这份剧本")}
        inert={open}
        className={cn(
          "fixed bottom-6 right-6 z-40 flex items-center gap-2 rounded-full border border-rule bg-panel/95 px-4 py-2.5 text-note text-ink shadow-[0_10px_30px_rgba(0,0,0,.5)]",
          "origin-bottom-right transition-[opacity,transform,border-color] duration-300 ease-glide",
          "hover:-translate-y-px hover:border-chrome/45 active:scale-[0.97]",
          open && "pointer-events-none translate-y-2 scale-[0.9] opacity-0",
        )}
      >
        {sending ? <PixelGrid className="text-chrome" /> : <MessageSquareText className="h-4 w-4 flex-none text-chrome" />}
        {sending ? "助手在改稿" : "对话改稿"}
        {pending > 0 && <span className="anim-pop-in rounded-full bg-state-fail px-1.5 text-micro text-slate">{pending} 待确认</span>}
      </button>

      <section
        aria-label="剧本对话助手"
        inert={!open}
        onDragOver={(e) => {
          if (!e.dataTransfer?.types?.includes("Files")) return;
          e.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragOver(false);
          void attach(e.dataTransfer?.files?.[0]);
        }}
        className={cn(
          "fixed bottom-6 right-6 z-40 flex h-[min(72vh,640px)] w-[min(520px,calc(100vw-3rem))] flex-col overflow-hidden rounded-panel border bg-panel shadow-[0_22px_60px_rgba(0,0,0,.6)]",
          "origin-bottom-right transition-[opacity,transform,border-color] duration-400 ease-glide",
          dragOver ? "border-chrome" : "border-rule",
          !open && "pointer-events-none translate-y-3 scale-[0.97] opacity-0",
        )}
      >
        <input ref={fileInput} type="file" accept={ACCEPT} className="hidden" onChange={(e) => void attach(e.target.files?.[0])} />

        <header className="flex flex-none items-center gap-2 border-b border-rule-soft px-3 py-2">
          <MessageSquareText className="h-4 w-4 flex-none text-chrome" />
          <h2 className="text-body font-semibold text-ink">剧本助手</h2>
          <Select
            className="ml-auto h-6 max-w-[190px] flex-none text-note"
            aria-label="历史会话"
            value={active?.id ?? ""}
            onChange={(e) => setActiveId(e.target.value || null)}
            disabled={!sessions.length}
          >
            {!sessions.length && <option value="">（还没有会话）</option>}
            {sessions.map((s) => (
              <option key={s.id} value={s.id}>
                {s.title}·{s.messages.length} 条
              </option>
            ))}
          </Select>
          <Button size="sm" variant="quiet" icon={<Plus className="h-3 w-3" />} onClick={createSession} title="开一段新会话">
            新会话
          </Button>
          <Button size="sm" variant="quiet" icon={<X className="h-3.5 w-3.5" />} onClick={() => setOpen(false)} aria-label="收起对话面板" title="收起（历史还在）" />
        </header>

        <div ref={scroller} className="min-h-0 flex-1 space-y-3 overflow-y-auto px-3 py-3">
          {!active || !active.messages.length ? (
            <div className="space-y-2">
              <p className="anim-fade-up text-note leading-snug text-ink-dim">说一句要怎么改这份稿子。助手回的是整篇正文，你看完差异再决定写不写回编辑器。</p>
              <div className="flex flex-wrap gap-1.5">
                {STARTERS.map((s, i) => (
                  <button
                    key={s}
                    type="button"
                    onClick={() => setInput(s)}
                    style={stagger(i + 1)}
                    className="anim-pop-in origin-bottom-left rounded-ctl border border-rule bg-raised px-2 py-1 text-caption text-ink-dim transition-[color,background-color,border-color,transform] duration-160 ease-std hover:-translate-y-px hover:border-chrome/45 hover:text-ink active:scale-[0.98]"
                  >
                    {s}
                  </button>
                ))}
              </div>
              <p className="anim-fade-up text-caption leading-snug text-ink-mute" style={stagger(3)}>{CHAT_ETA}。</p>
            </div>
          ) : (
            active.messages.map((m, i) => (
              // 逐条错开入场；包一层是因为 Bubble 的根节点自己管左右对齐
              <div key={m.id} className="anim-fade-up" style={stagger(i)}>
                <Bubble
                  msg={m}
                  script={p.script}
                  expanded={expanded === m.id}
                  onToggle={() => setExpanded(expanded === m.id ? null : m.id)}
                  onApply={applyWriteBack}
                  onDiscard={(id) => patchMessage(id, { outcome: "discarded" })}
                />
              </div>
            ))
          )}
          {/* Loading State 原型：点阵 + 真实已等秒数。秒数每 100ms 变一次，绝不能进读屏，
              所以整行只报一句静态状态，计时那两行各自 aria-hidden。 */}
          {sending && (
            <div role="status" className="anim-fade-up flex items-start gap-2.5 rounded-tile border border-rule-soft bg-sheen px-2.5 py-2">
              <PixelGrid className="mt-1 text-chrome" />
              <div className="min-w-0 space-y-0.5">
                <p className="shimmer-text text-body font-medium">模型正在整篇重出这份稿子</p>
                <p aria-hidden className="mono text-note text-ink-mute">
                  已等 {(waited / 1000).toFixed(1)} 秒 · 单槽串行，别重复提交
                </p>
                {waited > SLOW_MS && <p aria-hidden className="text-caption leading-snug text-ink-mute">超过一分半是常态，慢不等于卡住。</p>}
              </div>
            </div>
          )}
        </div>

        {p.blocked && (
          <div className="anim-fade-up flex flex-none items-start gap-2 border-t border-state-fail/40 bg-state-fail/10 px-3 py-2 text-caption leading-snug text-state-fail">
            <TriangleAlert className="mt-px h-3 w-3 flex-none" />
            <span>{p.blocked}</span>
          </div>
        )}

        <footer className="flex-none space-y-1.5 border-t border-rule-soft px-3 py-2">
          <Textarea
            rows={3}
            className="w-full"
            value={input}
            placeholder="说要改哪里。Ctrl+Enter 发送"
            disabled={sending || !!p.blocked}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
                e.preventDefault();
                void send();
              }
            }}
          />
          <div className="flex items-center gap-2">
            <Button
              size="sm"
              variant="quiet"
              icon={attaching ? <Spinner className="h-3 w-3" /> : <Paperclip className="h-3 w-3" />}
              disabled={sending || !!p.blocked || !!attaching}
              onClick={() => fileInput.current?.click()}
              title={`上传一份稿子，解析成「换稿提案」：${ACCEPT}`}
            >
              {attaching ? `正在读 ${attaching}` : "传稿子"}
            </Button>
            <span className="label-mono truncate">
              {p.script.length ? `正文 ${nf(p.script.length)} 字` : "正文还是空的：助手会按你这句话新写"}
            </span>
            {active && active.messages.length > 0 && (
              <Button size="sm" variant="ghost" icon={<Trash2 className="h-3 w-3" />} onClick={() => removeSession(active.id)} title="删掉这段会话">
                删除
              </Button>
            )}
            <Button
              className="ml-auto"
              size="sm"
              variant="primary"
              icon={<Send className="h-3 w-3" />}
              loading={sending}
              disabled={!input.trim() || !!p.blocked}
              onClick={() => void send()}
            >
              发送
            </Button>
          </div>
          <p className="text-micro leading-tight text-ink-mute">
            能读：{ACCEPT.split(",").join(" ")}。也可以直接把文件拖进这个面板；读进来只出提案，点确认才换掉正文。
          </p>
        </footer>

        {dragOver && (
          <div className="anim-fade-in pointer-events-none absolute inset-0 z-10 flex items-center justify-center bg-scrim/70">
            <span className="anim-pop-in flex items-center gap-2 rounded-panel border border-chrome bg-panel px-4 py-3 text-note text-ink">
              <FileUp className="h-4 w-4 text-chrome" /> 松手就解析成换稿提案（不会直接覆盖正文）
            </span>
          </div>
        )}
      </section>
    </>
  );
}

function Bubble({
  msg,
  script,
  expanded,
  onToggle,
  onApply,
  onDiscard,
}: {
  msg: ScriptChatMessage;
  script: string;
  expanded: boolean;
  onToggle: () => void;
  onApply: (m: ScriptChatMessage) => void;
  onDiscard: (id: string) => void;
}) {
  const up = msg.upload;
  const candidate = up?.text ?? msg.scriptText ?? null;
  const same = !!candidate && candidate === script;
  const notes = [...(up?.notes ?? []), ...(msg.warnings ?? [])];
  const isUser = msg.role === "user";

  return (
    <div className={isUser ? "ml-auto max-w-[85%] space-y-1.5" : "mr-auto max-w-[92%] space-y-1.5"}>
      {msg.error ? (
        <div className="flex items-start gap-1.5 rounded-tile border border-state-fail/45 bg-state-fail/10 px-2.5 py-1.5 text-note leading-snug text-state-fail">
          <TriangleAlert className="mt-px h-3.5 w-3.5 flex-none" />
          <span className="min-w-0 break-words whitespace-pre-wrap">
            {isUser ? `${msg.text} —— ` : ""}
            {msg.error}
          </span>
        </div>
      ) : (
        <div className={isUser ? "rounded-tile border border-rule bg-raised px-2.5 py-1.5" : "rounded-tile border border-rule-soft bg-sheen px-2.5 py-1.5"}>
          <span className="label-mono mb-0.5 flex items-center gap-1 text-micro text-ink-mute">
            {up && <FileUp className="h-2.5 w-2.5" />}
            {new Date(msg.ts).toLocaleTimeString()}
            {msg.latencyMs ? ` · ${(msg.latencyMs / 1000).toFixed(0)} 秒` : ""}
          </span>
          <p className="whitespace-pre-wrap break-words text-note leading-snug text-ink">{msg.text}</p>
        </div>
      )}

      {notes.map((n) => (
        <p key={n} className="flex items-start gap-1.5 text-caption leading-snug text-ink-mute">
          <TriangleAlert className="mt-px h-3 w-3 flex-none opacity-60" />
          <span>{n}</span>
        </p>
      ))}
      {up?.overModelCap && (
        <p className="text-caption leading-snug text-state-fail">
          这份 {nf(up.chars)} 字超过模型单次上限 {nf(up.modelCap)} 字：能写回编辑器，但对话改稿会被后端拒，要改得先分场分段。
        </p>
      )}

      {candidate && !msg.error && (
        <div
          className={cn(
            // 规格里 Approval Card 的处理：卡片随「展开 / 落定」换形状与底色，用过渡而不是跳档。
            // 三种状态互斥写在一条分支里 —— cn 只做拼接，两个 bg-* 同时挂上就只能靠 CSS 出现顺序猜谁赢。
            "border px-2.5 py-2 transition-[border-radius,background-color,border-color] duration-300 ease-std",
            msg.outcome === "applied"
              ? "rounded-tile border-state-ok/45 bg-state-ok/10"
              : msg.outcome === "discarded"
                ? "rounded-tile border-rule bg-sheen"
                : expanded
                  ? "rounded-panel border-chrome/50 bg-chrome/[0.1]"
                  : "rounded-tile border-chrome/35 bg-chrome/[0.07]",
          )}
        >
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-note font-semibold text-ink">{up ? "换稿待确认" : "改稿待确认"}</span>
            <span className="mono text-micro text-ink-mute">
              {up ? `${up.format} · ${nf(up.chars)} 字 · ${nf(up.lines)} 行${up.encoding ? ` · ${up.encoding}` : ""}` : `${nf(script.length)} 字 → ${nf(candidate.length)} 字`}
            </span>
            {msg.outcome === "applied" ? (
              <Badge tone="good" className="anim-pop-in ml-auto">
                已写回
              </Badge>
            ) : msg.outcome === "discarded" ? (
              <Badge className="anim-pop-in ml-auto">已放弃</Badge>
            ) : (
              <span className="ml-auto flex items-center gap-1.5">
                <Button size="sm" variant="quiet" icon={expanded ? <X className="h-3 w-3" /> : <GitCompare className="h-3 w-3" />} onClick={onToggle}>
                  {expanded ? "收起对比" : "展开对比"}
                </Button>
                <Button size="sm" variant="default" icon={<Eye className="h-3 w-3" />} onClick={() => onDiscard(msg.id)} title="不写回；这条留着，以后还能确认">
                  放弃
                </Button>
                <Button size="sm" variant="primary" icon={<Check className="h-3 w-3" />} disabled={same} onClick={() => onApply(msg)}>
                  {same ? "与当前正文一致" : up ? "确认换稿" : "确认写回"}
                </Button>
              </span>
            )}
          </div>
          {/* 展开用 0fr→1fr：差异高度事先不知道（几行到 120 行都有），没法写死一个 max-height */}
          {expanded && (
            <div className="anim-open">
              <div>
                <Diff script={script} next={candidate} />
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/** 行级差异：改动的行全列，未改的行只作上下文 */
function Diff({ script, next }: { script: string; next: string }) {
  const { shown, hidden } = useMemo(() => takeHunks(diffLines(script, next)), [script, next]);
  const changed = shown.filter((l) => l.t !== "same").length;
  return (
    <div className="mt-2 max-h-[280px] overflow-y-auto rounded-ctl border border-rule bg-inset p-2">
      <p className="label-mono mb-1 text-micro text-ink-mute">
        逐行对比（当前正文 ↔ 改稿）· 显示 {changed} 处{hidden > 0 ? `，还有 ${hidden} 处未展开` : ""}
      </p>
      <pre className="whitespace-pre-wrap break-words font-mono text-caption leading-[1.7]">
        {shown.map((l, i) => (
          <span
            key={i}
            className={
              l.t === "add" ? "block bg-state-ok/12 text-state-ok" : l.t === "del" ? "block bg-state-fail/12 text-state-fail" : "block text-ink-mute"
            }
          >
            {`${l.t === "add" ? "+ " : l.t === "del" ? "- " : "  "}${l.text || " "}`}
          </span>
        ))}
      </pre>
    </div>
  );
}
