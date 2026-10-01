import { Clapperboard, PenLine, RotateCw } from "lucide-react";
import { Button } from "../../../components/ui";

/**
 * 续写/改写走后端第五个用途 script_write（apps/api/app/llm.py）：
 * 它的 schema 只有 { text }，是唯一能吐剧本正文的口子 —— 另外四个用途都被 strict
 * schema 框成结构体，把指令塞进去只会拿回一段 JSON。
 */
interface Props {
  value: string;
  onChange: (v: string) => void;
  saveState: "idle" | "saving" | "saved";
  busy: string | null;
  hasShots: boolean;
  onGenerate: () => void;
  onAiWrite: (mode: "continue" | "rewrite") => void;
}

export function ScriptEditor({ value, onChange, saveState, busy, hasShots, onGenerate, onAiWrite }: Props) {
  const lines = value ? value.split("\n").length : 0;

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="flex h-11 flex-wrap items-center gap-2 border-b border-rule-soft px-4">
        <span className="mr-1 h-2 w-2 flex-none rounded-full bg-chrome" aria-hidden />
        <h2 className="text-body font-semibold tracking-wide text-ink">剧本编辑器</h2>
        <div className="ml-auto flex items-center gap-2">
          <Button
            icon={<PenLine className="h-3.5 w-3.5" />}
            loading={busy === "AI 续写中"}
            disabled={!!busy && busy !== "AI 续写中"}
            title={busy && busy !== "AI 续写中" ? "上一步还在跑，等它结束" : "接着现有剧情往下写约 300 字"}
            onClick={() => onAiWrite("continue")}
          >
            AI续写
          </Button>
          <Button
            icon={<RotateCw className="h-3.5 w-3.5" />}
            loading={busy === "AI 改写中"}
            disabled={!!busy && busy !== "AI 改写中"}
            title={busy && busy !== "AI 改写中" ? "上一步还在跑，等它结束" : "情节与人物不变，改写得更贴画面"}
            onClick={() => onAiWrite("rewrite")}
          >
            AI改写
          </Button>
          <span className="label-mono hidden sm:inline">Markdown supported</span>
        </div>
      </header>

      <div className="min-h-0 flex-1 p-4">
        <textarea
          className="h-full min-h-[420px] w-full resize-none rounded-panel border border-rule-soft bg-slate/60 p-6 text-subtitle leading-[2] text-ink placeholder:text-ink-mute"
          value={value}
          spellCheck={false}
          placeholder={"把故事大纲或整篇剧本贴进来。\n\n支持 Markdown：【第一幕】场景-时间 这类分场标题会被拆解模型当成场景与节拍读。"}
          onChange={(e) => onChange(e.target.value)}
        />
      </div>

      <footer className="flex h-9 flex-none items-center gap-4 border-t border-rule-soft px-4 text-caption text-ink-mute">
        <Button
          size="sm"
          variant="quiet"
          icon={<Clapperboard className="h-3 w-3" />}
          loading={!!busy}
          title={busy ? "上一次生成还没结束，先等它跑完" : "拆解剧本 → 回写角色/场景 → 规划分镜镜头表"}
          onClick={onGenerate}
        >
          {hasShots ? "重新生成 分镜脚本" : "生成 分镜脚本"}
        </Button>
        <span className="mono ml-auto tabular-nums">{value.length} 字符</span>
        <span className="mono tabular-nums">{lines} 行</span>
        <span className={saveState === "saving" ? "text-ink-dim" : "text-state-ok"}>
          {saveState === "saving" ? "保存中…" : "已自动保存"}
        </span>
      </footer>
    </div>
  );
}
