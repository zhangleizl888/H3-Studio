import { useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { Clapperboard, Images, ListChecks, Search, Video } from "lucide-react";
import { Badge, Empty, Input, Select } from "../../components/ui";
import { keys, useProject } from "../../lib/hooks";
import { flushSaves, queueSave } from "../../lib/localStores";
import { VISUAL_STYLES } from "../../lib/prompts";
import { fmtTime } from "../../lib/utils";
import { CollapsibleGroup } from "./prompts/CollapsibleGroup";
import { PromptCard } from "./prompts/PromptCard";
import { GROUP_LABEL, applyDraft, buildRows, countRows, entityExists, filterRows, type PromptDraft, type PromptGroup } from "./prompts/utils";

/**
 * /p/:id/prompts —— 资产管理（提示词统一管理）。
 *
 * 四类提示词（角色 / 场景 / 关键帧 / 视频段）在这里一次看全：
 * 存过的文本按存的显示，没存过的按当前模板现拼并标出来。
 * 编辑写回的是本机的项目数据（IndexedDB）：queueSave 之后立刻 flushSaves，
 * 不等那 1 秒防抖，否则切页就可能吃掉最后一次改动。
 */

const GROUP_ICONS: { key: PromptGroup; icon: typeof Images }[] = [
  { key: "characters", icon: Images },
  { key: "scenes", icon: Clapperboard },
  { key: "keyframes", icon: ListChecks },
  { key: "videos", icon: Video },
];

const FILTERS: { key: "all" | PromptGroup; label: string }[] = [
  { key: "all", label: "全部" },
  { key: "characters", label: "角色" },
  { key: "scenes", label: "场景" },
  { key: "keyframes", label: "关键帧" },
  { key: "videos", label: "视频" },
];

export default function Prompts() {
  const { id } = useParams<{ id: string }>();
  const qc = useQueryClient();
  const { data: project, error: projectErr } = useProject(id);

  const [q, setQ] = useState("");
  const [group, setGroup] = useState<"all" | PromptGroup>("all");
  const [collapsed, setCollapsed] = useState<Set<PromptGroup>>(new Set());
  const [savedAt, setSavedAt] = useState<string | null>(null);
  const [saveErr, setSaveErr] = useState<string | null>(null);

  const rows = useMemo(() => (project ? buildRows(project) : null), [project]);
  const filtered = useMemo(() => {
    if (!rows) return null;
    const pick = (g: PromptGroup) => (group === "all" || group === g ? filterRows(rows[g], q) : []);
    return {
      characters: pick("characters"),
      scenes: pick("scenes"),
      keyframes: pick("keyframes"),
      videos: pick("videos"),
      totals: {
        characters: countRows(rows.characters),
        scenes: countRows(rows.scenes),
        keyframes: countRows(rows.keyframes),
        videos: countRows(rows.videos),
      },
    };
  }, [rows, q, group]);

  const styleName = VISUAL_STYLES.find((s) => s.key === project?.config.visualStyle)?.name ?? project?.config.visualStyle ?? "未设置";
  const totalAll = rows ? countRows(rows.characters) + countRows(rows.scenes) + countRows(rows.keyframes) + countRows(rows.videos) : 0;

  async function save(draft: PromptDraft) {
    if (!project || !id) return;
    if (!entityExists(project, draft.target)) {
      setSaveErr("这条提示词对应的实体已经不在了（可能刚被删），这次改动没有写回。");
      return;
    }
    try {
      queueSave(applyDraft(project, draft));
      await flushSaves();
      await qc.invalidateQueries({ queryKey: keys.project(id) });
      await qc.invalidateQueries({ queryKey: keys.projects });
      setSaveErr(null);
      setSavedAt(new Date().toISOString());
    } catch (e) {
      setSaveErr(`写回失败：${e instanceof Error ? e.message : String(e)}`);
    }
  }

  return (
    <div className="space-y-5 p-4 md:p-8">
      <header className="space-y-3">
        <div>
          <h1 className="text-heading font-bold leading-tight tracking-tight">资产管理</h1>
          <p className="text-note text-ink-mute">查看和编辑所有生成任务的提示词与变量。改动只写在本机浏览器里，换机器要靠制片导出页的项目 JSON。</p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <div className="relative min-w-[260px] flex-1">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-ink-mute" aria-hidden />
            <Input
              className="h-9 w-full pl-8"
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="搜索提示词正文、角色名、场景名、镜头动作…"
              aria-label="搜索提示词"
            />
          </div>
          <Select className="h-9" value={group} onChange={(e) => setGroup(e.target.value as "all" | PromptGroup)} aria-label="按类型筛选">
            {FILTERS.map((f) => (
              <option key={f.key} value={f.key}>
                {f.label}
              </option>
            ))}
          </Select>
          <div className="flex items-center gap-1.5">
            <Badge>{project ? `${totalAll} 条提示词` : "读取中"}</Badge>
            <Badge>当前风格 {styleName}</Badge>
            {savedAt && <Badge tone="good">已写回 {fmtTime(savedAt)}</Badge>}
          </div>
        </div>

        {saveErr && <p className="rounded-ctl border border-state-fail/40 bg-state-fail/10 px-2.5 py-1.5 text-note leading-snug text-state-fail">{saveErr}</p>}
        {projectErr && (
          <p className="rounded-ctl border border-state-fail/40 bg-state-fail/10 px-2.5 py-1.5 text-note leading-snug text-state-fail">
            读不到项目：{projectErr.message}
          </p>
        )}
      </header>

      {!project || !rows || !filtered ? (
        <Empty title="正在读取项目的提示词数据" hint="提示词存在本机浏览器的项目记录里，第一次打开会稍等一下。" />
      ) : totalAll === 0 ? (
        <Empty title="这个项目还没有任何提示词" hint="先在剧本创作里拆出角色与场景，再到 AI 工作台生成分镜；有了实体，这里才有可管理的提示词。" />
      ) : (
        <div className="space-y-6">
          {GROUP_ICONS.map(({ key, icon: Icon }) => (
            <CollapsibleGroup
              key={key}
              title={GROUP_LABEL[key]}
              icon={<Icon className="h-4 w-4" />}
              shown={filtered[key].length}
              total={filtered.totals[key]}
              open={!collapsed.has(key)}
              onToggle={() =>
                setCollapsed((prev) => {
                  const next = new Set(prev);
                  if (next.has(key)) next.delete(key);
                  else next.add(key);
                  return next;
                })
              }
            >
              {filtered[key].map((row) => (
                <PromptCard key={row.key} row={row} project={project} onSave={save} />
              ))}
            </CollapsibleGroup>
          ))}

          <p className="text-caption leading-snug text-ink-mute">
            口径说明：角色 / 服装变体 / 场景 / 关键帧的正文分别落在 character.visualPrompt、variation.visualPrompt、scene.visualPrompt、
            shot.keyframes[].visualPrompt，负向提示词是同名字段；视频段正文是 shot.videoPrompt，结构化提示词按项目的 H3 模式落在 shot.h3Prompt（三段式三字段 / 六段式六字段 / 中文分镜块 sceneDescription+shotBlocks）。
            生成请求优先用这里存过的文本，空着才回退到模板现拼 —— 所以「清空并保存」等于把这条交还给模板。
          </p>
        </div>
      )}
    </div>
  );
}
