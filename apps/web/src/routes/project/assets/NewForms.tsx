/**
 * 新建角色 / 新建场景的表单。
 *
 * 剧本页解析回来的角色只有名字和一句话描述，这里补的是「能拼出一致性提示词」的那几项：
 * 性别、年龄段、性格（进演员表不进画面）、以及结构化外形 traits。
 * 另加一段生成选择：这个角色/场景用哪条工作流、哪台实例、哪颗权重 —— 建立时就定，
 * 比事后在卡片上找入口顺手，也免得整部片被项目级那一个开关绑死。
 */

import { useState } from "react";
import { Plus } from "lucide-react";
import { Button, Field, Input, Select, Textarea } from "../../../components/ui";
import { GenPresetPicker } from "../../../components/GenPresetPicker";
import type { Character, GenPreset, Project, Scene, VoiceProfile } from "../../../lib/types";
import { uid } from "../../../lib/utils";
import { Sheet } from "./common";

const GENDERS = ["女", "男", "其他", "未设定"];
const TIMES = ["日间", "午后", "黄昏", "夜间", "清晨", "未设定"];

/** 建号时只收音色的「说法」与选择，参考音频要等卡片上的媒体链路 —— 那里才落得了盘 */
const EMPTY_VOICE: VoiceProfile = { refAudioIds: [], sampleMediaIds: [] };

type Traits = NonNullable<Character["traits"]>;
/** traits.age 与角色的年龄段重复，表单只留后者 */
const TRAIT_FIELDS: { key: Exclude<keyof Traits, "age">; label: string; placeholder: string }[] = [
  { key: "build", label: "体型", placeholder: "纤细挺拔 / 壮实" },
  { key: "hair", label: "发型发色", placeholder: "乌黑长发高挽，碎发垂鬓" },
  { key: "costume", label: "服装", placeholder: "深青色绣云鹤纹宫装" },
  { key: "palette", label: "配色", placeholder: "青金 + 暗红" },
  { key: "signature", label: "标志细节", placeholder: "丹凤眼、右颊痣、随身玉佩" },
];

export function NewCharacterSheet({ project, onClose, onCreate }: { project: Project; onClose: () => void; onCreate: (c: Character) => void }) {
  const [name, setName] = useState("");
  const [gender, setGender] = useState("女");
  const [age, setAge] = useState("");
  const [personality, setPersonality] = useState("");
  const [desc, setDesc] = useState("");
  const [traits, setTraits] = useState<Partial<Record<Exclude<keyof Traits, "age">, string>>>({});
  const [preset, setPreset] = useState<GenPreset | undefined>();
  const [voice, setVoice] = useState<VoiceProfile>(EMPTY_VOICE);

  const submit = () => {
    const n = name.trim();
    if (!n) return;
    const t: Traits = {};
    for (const f of TRAIT_FIELDS) {
      const v = traits[f.key]?.trim();
      if (v) (t as Record<string, string>)[f.key] = v;
    }
    const hasVoice = !!(voice.timbre?.trim() || voice.testText?.trim() || voice.preset?.workflow || voice.preset?.models);
    onCreate({
      id: uid("c"),
      name: n,
      desc: desc.trim(),
      gender,
      age: age.trim(),
      personality: personality.trim(),
      traits: t,
      refMediaIds: [],
      variations: [],
      seed: Math.floor(Math.random() * 1e9),
      locked: false,
      preset,
      voice: hasVoice ? { ...voice } : undefined,
      status: "pending",
    });
  };

  return (
    <Sheet
      open
      onClose={onClose}
      width={620}
      eyebrow="New Character"
      title="新建角色"
      footer={
        <>
          <Button variant="quiet" onClick={onClose}>
            取消
          </Button>
          <Button variant="primary" icon={<Plus className="h-3.5 w-3.5" />} disabled={!name.trim()} onClick={submit}>
            创建角色
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <div className="grid gap-3 sm:grid-cols-[1fr_110px_140px]">
          <Field label="名字">
            <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="例如 柳如霜" autoFocus />
          </Field>
          <Field label="性别">
            <Select value={gender} onChange={(e) => setGender(e.target.value)} className="w-full">
              {GENDERS.map((g) => (
                <option key={g} value={g}>
                  {g}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="年龄段" hint="写「二十出头」这种相对说法">
            <Input value={age} onChange={(e) => setAge(e.target.value)} placeholder="二十出头" />
          </Field>
        </div>

        <Field label="性格与说话方式" hint="进演员表与对白，不进画面提示词">
          <Textarea rows={2} value={personality} onChange={(e) => setPersonality(e.target.value)} placeholder="克制、话少，习惯用反问代替表态" className="w-full" />
        </Field>

        <Field label="外形一句话" hint="会拼进 Core Identity 段">
          <Textarea rows={2} value={desc} onChange={(e) => setDesc(e.target.value)} placeholder="身形纤细挺拔，气质清冷克制" className="w-full" />
        </Field>

        <div>
          <div className="label-mono mb-1.5">结构化外形（拼一致性提示词用）</div>
          <div className="grid gap-2 sm:grid-cols-2">
            {TRAIT_FIELDS.map((f) => (
              <Field key={f.key} label={f.label}>
                <Input value={traits[f.key] ?? ""} onChange={(e) => setTraits((p) => ({ ...p, [f.key]: e.target.value }))} placeholder={f.placeholder} className="w-full" />
              </Field>
            ))}
          </div>
        </div>
        <div className="rounded-ctl border border-hairline bg-inset p-2">
          <div className="label-mono mb-1.5 text-ink-dim">出这张定妆照用什么（不选就跟项目的默认走）</div>
          <GenPresetPicker project={project} kind="image" value={preset} onChange={setPreset} compact />
        </div>

        <div className="rounded-ctl border border-hairline bg-inset p-2">
          <div className="label-mono mb-1.5 text-ink-dim">音色 · 这一句怎么说（参考音频到角色卡片上挂）</div>
          <div className="grid gap-2 sm:grid-cols-2">
            <Field label="音色描述">
              <Input value={voice.timbre ?? ""} onChange={(e) => setVoice((v) => ({ ...v, timbre: e.target.value }))} placeholder="沙哑低沉、语速偏慢" className="w-full" />
            </Field>
            <Field label="试念台词">
              <Input value={voice.testText ?? ""} onChange={(e) => setVoice((v) => ({ ...v, testText: e.target.value }))} placeholder={`留空则用「我是${name.trim() || "这个角色"}。」`} className="w-full" />
            </Field>
          </div>
          <div className="mt-2">
            <GenPresetPicker project={project} kind="audio" value={voice.preset} onChange={(p) => setVoice((v) => ({ ...v, preset: p }))} compact />
          </div>
        </div>

        <p className="text-caption leading-snug text-ink-mute">
          创建后可以直接点卡片上的「生成角色图片」—— 留空的字段会由提示词模板补默认说法，改提示词请进卡片里的编辑区。
        </p>
      </div>
    </Sheet>
  );
}

export function NewSceneSheet({ project, onClose, onCreate }: { project: Project; onClose: () => void; onCreate: (s: Scene) => void }) {
  const [name, setName] = useState("");
  const [location, setLocation] = useState("");
  const [time, setTime] = useState("日间");
  const [atmosphere, setAtmosphere] = useState("");
  const [desc, setDesc] = useState("");
  const [preset, setPreset] = useState<GenPreset | undefined>();

  const submit = () => {
    const n = name.trim() || location.trim();
    if (!n) return;
    onCreate({
      id: uid("sc"),
      name: n,
      desc: desc.trim(),
      location: location.trim() || n,
      time,
      atmosphere: atmosphere.trim(),
      refMediaIds: [],
      preset,
      status: "pending",
    });
  };

  return (
    <Sheet
      open
      onClose={onClose}
      width={620}
      eyebrow="New Location"
      title="新建场景"
      footer={
        <>
          <Button variant="quiet" onClick={onClose}>
            取消
          </Button>
          <Button variant="primary" icon={<Plus className="h-3.5 w-3.5" />} disabled={!name.trim() && !location.trim()} onClick={submit}>
            创建场景
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <div className="grid gap-3 sm:grid-cols-[1fr_1fr_140px]">
          <Field label="场景名">
            <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="内廷·未央宫偏殿" autoFocus />
          </Field>
          <Field label="地点" hint="环境参考图的实际空间">
            <Input value={location} onChange={(e) => setLocation(e.target.value)} placeholder="未央宫偏殿内" />
          </Field>
          <Field label="时段">
            <Select value={time} onChange={(e) => setTime(e.target.value)} className="w-full">
              {TIMES.map((t) => (
                <option key={t} value={t}>
                  {t === "日间" ? "日间" : t}
                </option>
              ))}
            </Select>
          </Field>
        </div>

        <Field label="氛围" hint="会拼进 Lighting 段">
          <Textarea rows={2} value={atmosphere} onChange={(e) => setAtmosphere(e.target.value)} placeholder="帷幕低垂、香烟袅袅，暗流涌动的疑心与权谋" className="w-full" />
        </Field>

        <Field label="环境描述" hint="留空则用场景名顶上">
          <Textarea rows={2} value={desc} onChange={(e) => setDesc(e.target.value)} placeholder="深色楠木梁柱与盘龙斗拱层叠，青铜香炉、玉盏陈列" className="w-full" />
        </Field>

        <div className="rounded-ctl border border-hairline bg-inset p-2">
          <div className="label-mono mb-1.5 text-ink-dim">出这张场景概念图用什么（不选就跟项目的默认走）</div>
          <GenPresetPicker project={project} kind="image" value={preset} onChange={setPreset} compact />
        </div>

        <p className="text-caption leading-snug text-ink-mute">
          场景图默认无人物或只留远景剪影 —— 要人物和景一起出，请在提示词里自己写明。
        </p>
      </div>
    </Sheet>
  );
}
