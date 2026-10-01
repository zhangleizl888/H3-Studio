"""导演方法论与 H3 提示词模式。

这一层只管「提示词该怎么写」，不碰节点、不碰队列。

来源：参考项目 TFboy1/oh-my-minimaxh3-director（MIT）的
`references/wenwu-director.md`（WenWu 生镜引擎）与 `references/storyboard-schema.md`
（模式选择与镜头卡）。搬进本项目时按本机事实改了四处：

  1. 三段式是本机已经出过片的形状（PLAN.md §11.1：H3 fl2v 864×480 / 56 帧 / 368s，
     产物带音轨），所以留成默认模式 `three_field`，而不是参考项目的六段式；
  2. `integrated` 在四种模式下都必须出，且只写一句话主旨——镜头列表、PromptCard、
     关键帧兜底都在读它，不能因为换模式就变成空值；正文深度放在各自段落字段里；
  3. 参考项目把跨镜衔接完全交给文字锚点、并禁止引用上一段尾帧；我们两条路线并存
     （尾帧续拍已有实现），所以这里只把「交镜锚点」写成要求，不做结构约束；
  4. 输出字段名一律 camelCase，和 `apps/web/src/lib/types.ts` 的 `H3Prompt` 对齐，
     模型回来的 dict 直接就是那个形状，中间不再翻译一次。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

PromptMode = Literal["three_field", "six_section", "wenwu", "hybrid"]
Temperature = Literal["高动态", "细腻文戏", "混合片"]

PROMPT_MODES: tuple[str, ...] = ("three_field", "six_section", "wenwu", "hybrid")


@dataclass(frozen=True)
class ModeSpec:
    """一个模式的：叫什么、什么时候用、出多少字、给 UI 的耗时预期。"""

    key: PromptMode
    name: str
    language: str
    when: str
    max_tokens: int
    eta: str

    @property
    def budget_note(self) -> str:
        return f"{self.name}｜{self.language}｜{self.eta}"


MODES: dict[str, ModeSpec] = {
    "three_field": ModeSpec(
        key="three_field",
        name="三段式（本机实测）",
        language="中文为主，字段名英文",
        when="快速批量出片、无强风格要求；单卡 24GB 上的默认档",
        max_tokens=1400,
        eta="一镜约 20–60 秒",
    ),
    "six_section": ModeSpec(
        key="six_section",
        name="官方六段式",
        language="英文（中文台词原样保留在 <d>[中文] …</d> 内）",
        when="需要身份锁定与保留关系核对的镜头：变身、换装、多角色同框",
        max_tokens=3600,
        eta="一镜约 1–3 分钟",
    ),
    "wenwu": ModeSpec(
        key="wenwu",
        name="中文导演分镜块",
        language="中文",
        when="点名要导演级分镜：逐镜定时、表演与声音都写在镜头里",
        max_tokens=3000,
        eta="一镜约 1–2 分钟",
    ),
    "hybrid": ModeSpec(
        key="hybrid",
        name="六段外壳 × 导演级内容",
        language="英文外壳 + 中文台词（推荐用于精修）",
        when="宣传片 / PV / 广告 / 强风格短片；简报里已给定风格、色彩、音乐编排",
        max_tokens=4600,
        eta="一镜约 2–4 分钟",
    ),
}

# ───────── 镜头语言与枚举 ─────────

#: 景别 7 档：中文强制，不许写 ECU/CU 这类英文缩写。分镜枚举与提示词用词都走这一套
SHOT_SIZES: tuple[str, ...] = ("极端特写", "近景", "中近景", "中景", "中远景", "全景", "远景")

#: 分镜枚举用的运镜词：与 apps/web/src/lib/prompts.ts 的 CAMERA_ALIASES 同源，改一处必须改两处。
#: 上面那套 15 词运镜库是**写作措辞**（提示词里写「低角度 OTS·慢推」这类复合机位），
#: 这里这套才是镜头卡上能被界面选中、能被 cameraGuide() 解析成构图指引的值。
UI_CAMERA_MOVES: tuple[str, ...] = (
    "固定", "推近", "拉远", "横移", "跟随", "升降", "手持", "摇镜", "环绕", "俯拍", "仰拍", "过肩", "第一人称", "荷兰角", "变焦推近",
)

#: 镜头密度公式：只有用户没指定镜头结构时才用
_PACING: dict[str, tuple[float, float, float]] = {
    # 温度: (单镜最短秒, 单镜最长秒, 分母)
    "高动态": (0.4, 1.5, 1.0),
    "细腻文戏": (2.0, 4.0, 3.0),
    "混合片": (1.5, 3.0, 2.0),
}

#: H3 一次生成的合法时长区间（参考项目后端校验同值：5–15 秒）。
#: 我们的一镜 = 一个 H3 任务，所以分镜表按这个区间出，段内的节奏另算。
SHOT_DURATION_MIN = 5.0
SHOT_DURATION_MAX = 15.0


def shot_density(temperature: str, total_sec: float) -> tuple[int, int, int]:
    """按叙事温度算**一条视频内部**的镜头脉冲数区间：(下限, 期望, 上限)。

    期望值取 round(T/分母)，上下限由单镜时长上下限推出来。
    注意层级：这不是「60 秒要拆成多少个生成任务」，那是 storyboard_hint 管的事。
    """
    lo_s, hi_s, div = _PACING.get(temperature, _PACING["混合片"])
    want = max(1, round(total_sec / div))
    return max(1, round(total_sec / hi_s)), want, max(1, round(total_sec / lo_s))


def pacing_hint(temperature: str, total_sec: float) -> str:
    lo, want, hi = shot_density(temperature, total_sec)
    lo_s, hi_s, _ = _PACING.get(temperature, _PACING["混合片"])
    return f"{total_sec:.0f} 秒的{temperature}：内部 {lo}–{hi} 个镜头脉冲（期望约 {want} 个），单脉冲 {lo_s:.1f}–{hi_s:.1f} 秒"


def pulse_options(seconds: float) -> str:
    """把三种叙事温度的段内节奏都列出来，让模型按镜头卡的实际内容挑，而不是我替它定。"""
    return "；".join(pacing_hint(t, seconds) for t in ("高动态", "细腻文戏", "混合片"))


def storyboard_hint(total_sec: float) -> str:
    """分镜表的规模：一镜 = 一个 H3 任务，时长锁在 5–15 秒。"""
    lo = max(1, round(total_sec / SHOT_DURATION_MAX))
    hi = max(lo, round(total_sec / SHOT_DURATION_MIN))
    return (
        f"目标 {total_sec:.0f} 秒：{lo}–{hi} 镜，每镜 {SHOT_DURATION_MIN:.0f}–{SHOT_DURATION_MAX:.0f} 秒"
        f"（H3 单次生成只能吃这么长，超出会被后端拒）。段内的快慢靠提示词里的镜头脉冲控制，不要靠把镜头切碎。"
    )


# ───────── 方法论正文（喂给模型；界面上的模式说明在 lib/prompts.ts 的 H3_PROMPT_MODES）─────────

METHODOLOGY_HEADER = "【本项目的导演规范】以下规则是硬要求，与上面的助手身份一起生效："

METHODOLOGY = """你是这条片子的导演，同时承担叙事导演、摄影指导、表演指导、动作设计和声音导演。
把模型当成「在镜头里做选择的人」，不是「执行动作的工具」。成片必须具备三种生命迹象：
1) 注意力在流动——人物先看见、听见或感觉到某件事，随后才反应；
2) 动作有前因后果——每个动作改变身体、关系、空间、物体或下一步选择；
3) 镜头有观看动机——摄影机换位置是为了看见新信息，不是为了展示运镜术语。

【创作者指令优先级】用户说死的先执行，再自动补全：
明确拍摄结构（一镜到底/固定机位）→ 明确镜头数量与顺序 → 明确时长、画幅、素材职责、
台词与必须发生的事件 → 题材适配与默认镜头密度 → 普通审美补全。
用户指定了几镜就必须精确几镜，不增不减，也不许被默认密度覆盖；要求一镜到底时只写一个
连续镜头，用走位、景深、遮挡、转向和摄影机路径制造内部段落，不得暗拆成多个 CUT。

【生镜三域】
· 生命核：这段视频「始终是谁、在哪里、为什么」。锁定脸、年龄、体型、发型、服装、饰品、
  惯用手；产品的比例结构材料颜色；场景的时间天气主光方向入口障碍关键道具；跨镜持续存在的
  伤痕污渍雨水破损能量状态与物体归属；开始状态—转折状态—最终状态。只写会影响成片的细节。
· 镜头脉冲：观众注意力的一次次跳动。每镜走「观察理由→画面入口→新信息→主体变化→摄影机
  响应→结果落点→下一镜的牵拉物」。允许硬切、遮挡切换、动作匹配、视线匹配、声桥、光线闪白、
  形状匹配、尺度跳切。同一动作不许换几个景别重复描述；重要反应宁可多停半秒。
· 表演肌理：把情绪写成身体能完成的过程。每阶段都要有行动目的（隐藏、试探、确认、挽留、
  拒绝、拖延、保护、诓骗、夺回控制、寻找出口、完成告别），走「刺激抵达→本能反应刚要出现→
  主动压住→保护层失效→身体泄露→作出选择→余波」。先有身体变化，情绪才成为观众的结论。
  无人物时改写「状态肌理」：结构受力、装配方向、能量流动、材质反馈、稳定结果。

【八条生命通道】形（身份与材质不漂）、场（空间关系与距离带来的亲密/威胁/控制/逃逸）、
视（摄影机的注意力；远景给身处何处、中景给身体在做什么、近景给如何控制自己、特写只留给
决定性证据）、力（准备→发力→接触→受力→恢复或转化，余力成为下一个动作的原动力）、
念（没说出口的目的，落成停顿/眼神/距离/呼吸/手部动作，不写成旁白）、
息（压力逐步穿透保护层：控制稳定→刺激累积→呼吸改变→表情支撑松动→真实反应越过保护层→峰值
→重新控制或彻底放弃；呼吸可以先于台词暴露人物）、
言（台词按开口前/说话中/说完后三段组织；双引号只包真正说出口的原文，用户没给台词就不猜）、
续（每次切换至少传递一个锚点：动作方向/视线目标/同一道光/同一道具/同一轮廓/同一段声音/同一股
受力；禁止无理由跳切；结尾要留下看得见的结果）。

【叙事温度与段内节奏】不看题材名称，看「观众必须看清的变化」：位移、碰撞、追逐、拆解、变形、
爆发、环境破坏、控制权快速交换 → 高动态（单脉冲 0.4–1.5 秒，约 T/1.0 个）；关系、认知、犹豫、
隐瞒、试探、台词、呼吸、视线、情绪保护层的松动 → 细腻文戏（单脉冲 2–4 秒，约 T/3.0 个）；
两者都在 → 混合片（单脉冲 1.5–3 秒，约 T/2.0 个），并确定哪一类承担高潮，文戏段落主动减速。
这里的「脉冲」是一条视频内部的镜头节拍数，不是要把它拆成多少次生成任务。

【面部微调（FACS AU）】以自然表演为主体，AU 只做精确注释。强度 A 几乎不可察、B 轻微、
C 清晰、D 强烈、E 极限；日常近景用 A–C，D 只出现在真正峰值，E 通常不用。
AU1 眉内侧抬（请求/悲伤泄露）、AU2 眉外侧抬（注意力突升）、AU4 眉下压（专注/压抑愤怒或疼痛）、
AU5 上睑抬（警觉或惊讶升级）、AU6 面颊抬（真实笑意的一部分）、AU7 眼睑收紧（怀疑/强忍）、
AU12 嘴角上提（配 AU6 才像真笑）、AU15 嘴角向下（悲伤失去遮掩）、AU17 颏部收紧（忍住啜泣）、
AU20 嘴角横拉（害怕/控制嘴）、AU23/24 双唇收紧压紧（拒绝/把话咽回去）、AU25/26 双唇分开下颌
放松（吸气/开口/惊讶升级）。AU 组合必须来自同一个心理原因；与自然动作冲突时保留自然动作、删掉 AU。

【素材契约】用 H3 实际标签 <Picture N> / <Video N> / <Audio N>，并写清每个素材的职责：
身份、服装、物体、场景、构图、风格、关键帧、动作、摄影、故事板、完整音频复用、部分音频复用、
待编辑源视频。图片负责锁住可见信息；音频只承担用户明确指定的复用与功能，不能从未听过的音频里
编造台词。角色一致性用 <Subject N> 声明身份，且身份描述必须与四视图/定妆图一致。

【镜头语言】景别只能用 极端特写/近景/中近景/中景/中远景/全景/远景；机位从
平视/俯拍/仰拍/荷兰角/主观视角/过肩/上帝视角/虫视角/侧面剖视/双人构图/三人构图 里选；
运镜从 摇/仰俯/推/拉/横移/升降/摇臂/斯坦尼康/手持/跟拍/推拉变焦/急摇/无人机/高速摄影/静止 里选，
速度修饰写「慢推/急推/丝滑横移/急摇接切/Flash Cut(≤1秒)」；转场默认硬切，特殊转场从
匹配剪辑/撞切/J切/L切/交叉剪辑/跳切/叠化/淡入淡出/圈入圈出/划像/急摇接切/闪白闪黑 里选。

【交付前自检（不要把检查过程写进输出）】身份是否从第一帧稳到最后一帧；每镜是否带来新信息或
不可逆变化；摄影机是否因观看需要而动；动作是否有准备/接触/受力/结果；人物是否有目的-保护-泄露-
选择-余波；台词是否有开口前/中/后；AU 是否只作辅助且不过重；时间段首尾相接且总时长精确等于要求
秒数；有没有用「快速展示」「多个特写」这类笼统词逃避镜头设计；参考素材是否各司其职没有身份混合；
有没有重复形容、矛盾情绪、无动机运镜、无结果动作。最终文本要像导演在指导一场真实拍摄，不是参数清单。

【硬切与默认】默认无字幕、无 BGM、原生对白直出；不需要配乐时 music / non_diegetic_music 必须写
N/A，否则模型会自己加音乐。"""


# ───────── 每种模式的写法规范（拼在 system 后面）─────────

MODE_SPECS: dict[str, str] = {
    "three_field": """输出模式：三段式（H3 节点真正读的三段）。
· integrated：画面主体 + 运动 + 镜头语言，一段写清「谁在这镜里做什么、摄影机怎么拍、景别机位」，
  60–160 字，中文；有台词就把台词用 <d>[中文]……</d> 原样写在段末，并说明口型与说话人。
· soundscape：环境声 + 动作音 + 声桥，30–80 字；只要人声与真实环境声，不要写「配乐」。
· music：非剧情配乐的编排（BPM、乐器、进出与停止时间点）；没有配乐就写 N/A。""",
    "six_section": """输出模式：官方六段式（英文书写，中文台词原样保留在 <d>[Chinese] …</d> 内）。
六个字段各自独立成段，顺序与语义固定：
· subjectDefinitions：每个 <Subject N> 一行，锁身份/年龄/体型/发型/服装/材质/配色，以及
  <Picture N> 素材各自负责锁住什么（identity / costume / scene / prop / composition）。
· summary：这一段的最终发展线，3–5 句，含时间线走向。
· retention_analysis：逐主体写保留关系，取值 fully_preserved / partially_preserved /
  attribute_transfer / weak_reference，并说明保留的是哪些属性。
· detailed_description：编号镜头脉冲，形如
  [Shot 1] 0.00-2.50s. <景别与机位>. <本镜唯一事件>. <主体可见变化>. <摄影机如何回应>.
  <用什么把注意力交给下一镜>。时间段必须首尾相接，总时长精确等于给定秒数；
  多镜头必须显式写 "three clearly separated shots with visible cuts between them.
  Do not render this as one continuous take."
· soundscape：环境/动作/原生对白，说话人编号与 subjectDefinitions 一致。
· music：无 BGM 写 N/A。
integrated 只写一句不超过 60 字的画面主旨（列表显示用），不要重复 detailed_description。""",
    "wenwu": """输出模式：中文导演分镜块（按本机跑通的中文形状写）。
· sceneDescription：开篇一次交代完——时长与画幅、影片类型、生命核（始终是谁/在哪/为什么）、
  素材职责（<Picture 1> 是什么）、最终发展线。80–200 字。
· shotBlocks：数组，每个元素是一镜的完整定时块，形状严格照这个模子：
  「镜头1（0-3s）：<景别+机位+运镜>」
  「    摄像机状态：<机位/对焦/景深/光线，固定还是移动>」
  「    画面内容：<本镜唯一事件 + 主体可见变化>」
  「    音频：<环境声/动作音/有无 BGM>」
  「    人物台词（清晰口语）："<原文>"」——没有台词就把这一行写成「    人物台词：无」
  时间必须首尾相接、总秒数等于给定时长；每镜都要有独立的观察任务。
· integrated：一句不超过 60 字的画面主旨（列表显示用）。
· soundscape / music：整段的声音落点与配乐（无 BGM 写 N/A）。""",
    "hybrid": """输出模式：hybrid（官方六段式外壳 × 导演级内容，本项目推荐用于精修）。
外壳与 six_section 完全相同（六个字段、英文、<Picture N>/<Subject N> 标签、[Shot N] 时间码），
但每个字段的内容深度按方法论写：
· subjectDefinitions = 生命核：身份 + 色彩系统（每个角色/势力一个色相，不许漏色或重色）+
  平面设计与材质语言 + 素材职责。
· summary = 最终发展线：本段 2–3 镜的完整时间线概要。
· retention_analysis = 八条生命通道逐条核对（形/场/视/力/念/息/言/续各写一行结论）。
· detailed_description = 镜头脉冲：起止时间 + 景别机位 + 唯一事件 + 主体变化 + 摄影机回应 +
  交镜锚点；人物片在相应镜头里织入表演肌理（心理目的、保护层、呼吸、台词触发、AU 强度如
  AU1-B、AU24-B），无人片织入状态肌理。开头写一句贯穿全片的风格句（含颗粒/镜头质感/色彩取向）。
· soundscape = 环境层 + 动作音 + 声桥；
· music = 编排：BPM、乐器、进入/变化/停止时间点；没有写 N/A。
· constraints：结尾必填的风格与负向约束块，英文逗号分隔，**只写 5–10 条**：媒介/风格取向
  （如 pure 2D、photorealistic 35mm grain）+ 画面文字禁令 + 身份漂移禁令。不许罗列整套负向清单，
  更不许禁止本镜自己选过的景别、机位或运镜（写了 slow push in 就不能再出现 no panning / no zoom）。
  例如：pure 2D, no 3D shading, no readable text, no subtitles, no identity drift。
integrated 只写一句不超过 60 字的画面主旨（列表显示用）。""",
}

#: [Shot 1] / [shot-2] 这类编号镜头标记
_SHOT_MARK = re.compile(r"\[\s*shot[\s_-]*\d+", re.I)
#: 时间码：0.00-6.00s / 0:00-0:06 / 第0-6秒 / 6–9s
_TIMECODE = re.compile(r"\d{1,3}(?:[:.]\d{1,2})?\s*[-–—~至]\s*\d{1,3}(?:[:.]\d{1,2})?\s*(?:s|秒)?|\d{1,3}\s*(?:s|秒)\b", re.I)


def mode_or_default(mode: str | None) -> str:
    """没给模式就用三段式：IDB 里的旧项目根本没有这个字段。"""
    return mode if mode in MODES else "three_field"


def _prop(name: str, desc: str) -> dict[str, Any]:
    return {"type": "string", "description": desc}


def _str_array(name: str, desc: str) -> dict[str, Any]:
    return {"type": "array", "items": {"type": "string"}, "description": desc}


def h3_schema(mode: str) -> dict[str, Any]:
    """按模式给出 H3 提示词的输出 schema。字段名就是前端 H3Prompt 的字段名。"""
    m = mode_or_default(mode)
    props: dict[str, Any] = {
        "integrated": _prop("integrated", "一句话画面主旨，不超过 60 字，供镜头列表与兜底显示"),
    }
    required = ["integrated"]
    if m == "three_field":
        props["soundscape"] = _prop("soundscape", "环境声 + 动作音 + 声桥，30–80 字")
        props["music"] = _prop("music", "非剧情配乐编排；没有就写 N/A")
        required += ["soundscape", "music"]
    elif m in ("six_section", "hybrid"):
        props["subjectDefinitions"] = _prop("subjectDefinitions", "<Subject N>/<Picture N> 的身份、材质、配色与素材职责")
        props["summary"] = _prop("summary", "本段最终发展线，3–5 句")
        props["retentionAnalysis"] = _prop("retentionAnalysis", "逐主体的保留关系与保留属性")
        props["detailedDescription"] = _prop("detailedDescription", "[Shot N] 时间码 + 景别机位 + 唯一事件 + 主体变化 + 摄影机回应 + 交镜锚点")
        props["soundscape"] = _prop("soundscape", "overall_soundscape：环境层 + 动作音 + 声桥，说话人编号与主体定义一致")
        props["music"] = _prop("music", "non_diegetic_music：BPM/乐器/进出与停止时间；无 BGM 写 N/A")
        required += ["subjectDefinitions", "summary", "retentionAnalysis", "detailedDescription", "soundscape", "music"]
        if m == "hybrid":
            props["constraints"] = _prop("constraints", "结尾风格与负向约束块，英文逗号分隔")
            required.append("constraints")
    else:  # wenwu
        props["sceneDescription"] = _prop("sceneDescription", "开篇：时长与画幅、影片类型、生命核、素材职责、最终发展线")
        props["shotBlocks"] = _str_array("shotBlocks", "逐镜定时块，每条形如「镜头1（0-3s）：…」带摄像机状态/画面内容/音频/人物台词")
        props["soundscape"] = _prop("soundscape", "整段的声音落点")
        props["music"] = _prop("music", "配乐；无 BGM 写 N/A")
        required += ["sceneDescription", "shotBlocks", "soundscape", "music"]
    return {"type": "object", "properties": props, "required": required, "additionalProperties": False}


def h3_user_prompt(mode: str, *, brief: str, seconds: float, aspect: str, style: str) -> str:
    m = mode_or_default(mode)
    return (
        f"画幅 {aspect}；本镜时长 {seconds:g} 秒；视觉风格 {style or '按剧本'}。\n"
        f"时间预算：所有内部镜头时段必须首尾相接，累加精确等于 {seconds:g} 秒，不许留空洞。\n"
        f"段内节奏（按镜头卡内容判定叙事温度后挑一档）：{pulse_options(seconds)}。\n\n"
        f"{MODE_SPECS[m]}\n\n---\n镜头卡与上下文：\n{brief}"
    )


def validate_h3_output(mode: str, data: dict[str, Any]) -> list[str]:
    """结构化自检：缺段就报，不要让用户拿一份残缺提示词去烧 6 分钟显存。"""
    m = mode_or_default(mode)
    problems: list[str] = []
    if not str(data.get("integrated") or "").strip():
        problems.append("缺 integrated（一句话画面主旨）")
    for k in ("soundscape", "music"):
        if not str(data.get(k) or "").strip():
            problems.append(f"缺 {k}")

    if m in ("six_section", "hybrid"):
        for k in ("subjectDefinitions", "summary", "retentionAnalysis", "detailedDescription"):
            if not str(data.get(k) or "").strip():
                problems.append(f"缺 {k}")
        body = str(data.get("detailedDescription") or "")
        if body and not _SHOT_MARK.search(body):
            problems.append("detailed_description 里没有 [Shot N] 编号镜头")
        if body and not _TIMECODE.search(body):
            problems.append("detailed_description 认不出逐镜时间码（形如 0.00-6.00s 或 0:00-0:06）")
        if m == "hybrid":
            cons = str(data.get("constraints") or "").strip()
            if not cons:
                problems.append("hybrid 必须有 constraints（风格与负向约束块）")
            elif len(cons) > 400 or cons.count(",") > 12:
                problems.append(f"constraints 失控（{cons.count(',') + 1} 条 / {len(cons)} 字），压到 5–10 条，且别禁止本镜自己用过的运镜")
    elif m == "wenwu":
        if not str(data.get("sceneDescription") or "").strip():
            problems.append("缺 sceneDescription（开篇时长/类型/生命核/素材职责）")
        blocks = data.get("shotBlocks") or []
        if not isinstance(blocks, list) or not blocks:
            problems.append("shotBlocks 是空的")
        else:
            # 这台 27B 实测会把「镜头块」和块内每一行都各当成一个数组元素。拼装时按行 join，
            # 两种写法出来的文本是一样的，所以校验看合并结果，不苛求数组粒度。
            joined = "\n".join(str(b) for b in blocks)
            if not re.search(r"镜头\s*\d+", joined):
                problems.append("shotBlocks 里没有「镜头N」编号")
            if not _TIMECODE.search(joined):
                problems.append("shotBlocks 认不出时间头（形如 镜头1（0-2.5s））")
    return problems


def validate_storyboard(shots: list[dict[str, Any]], total_sec: float) -> list[str]:
    """分镜表的规模与时间预算自检。只出警告，不拦人——分镜重做一次很便宜，出片烧 6 分钟才贵。"""
    problems: list[str] = []
    n = len(shots or [])
    if not n:
        return ["模型没有给出任何镜头"]
    if total_sec > 0:
        lo = max(1, round(total_sec / SHOT_DURATION_MAX))
        hi = max(lo, round(total_sec / SHOT_DURATION_MIN))
        if not (lo <= n <= hi):
            problems.append(f"镜头数 {n} 不在 {storyboard_hint(total_sec).split('：')[1].split('，')[0]} 的区间里")
        summed = sum(float(s.get("durationSec") or 0) for s in shots)
        if abs(summed - total_sec) / total_sec > 0.25:
            problems.append(f"每镜秒数累加 {summed:.1f}s 与目标 {total_sec:.0f}s 偏差超过 25%")
    for i, s in enumerate(shots):
        d = float(s.get("durationSec") or 0)
        if not (SHOT_DURATION_MIN <= d <= SHOT_DURATION_MAX):
            problems.append(f"第{i + 1}镜 {d:g}s 不在 H3 单次生成的 {SHOT_DURATION_MIN:.0f}–{SHOT_DURATION_MAX:.0f} 秒里")
        if not str(s.get("action") or "").strip():
            problems.append(f"第{i + 1}镜没有动作")
        if str(s.get("shotSize") or "").strip() and s["shotSize"] not in SHOT_SIZES:
            problems.append(f"第{i + 1}镜景别「{s['shotSize']}」不在七档里")
        if str(s.get("cameraMovement") or "").strip() and s["cameraMovement"] not in UI_CAMERA_MOVES:
            problems.append(f"第{i + 1}镜运镜「{s['cameraMovement']}」不在镜头卡词表里")
    return problems
