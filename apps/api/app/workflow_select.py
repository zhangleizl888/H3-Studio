"""按前端任务自动选工作流，并把任务填成一张可提交的图。

工作流库里的每条都带着导入时算好的 `task_kind` / `signals` / `gaps`（见
gen/workflow_inputs.py），所以「这张任务该用哪条工作流」是能机械回答的：
先按产出种类和「这台实例跑不跑得动」筛掉，再按「任务给得出、这条要得东西」打分。

选中之后的填图也在这里：素材按顺序落到各个加载节点，没给到的加载节点**删掉**——
ComfyUI 会执行 prompt 里列出的每一个节点，留一个空文件名的 LoadImage 上去，
整个任务会以「image 必须是以下之一」失败，那看起来像是程序的锅。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable

from .gen.base import GenError
from .gen.builtin_graphs import h3_length



@dataclass
class Task:
    """前端一次生成请求能给出来的东西。多出来的字段没有对应信号就是白给。"""

    kind: str = "video"                       # image | video | audio
    prompt: str = ""
    negative_prompt: str = ""
    media: dict[str, list[int]] = field(default_factory=dict)  # 信号名 -> media id 列表
    params: dict[str, Any] = field(default_factory=dict)       # seconds / steps / width / height / seed
    placement: str = "local"                  # 目标实例在哪：local | cloud_self | cloud_runninghub

    @classmethod
    def from_params(cls, params: dict[str, Any]) -> "Task":
        slots = params.get("slots") or {}
        media = {}
        for key in ("first_frame", "last_frame", "ref_images", "ref_videos", "ref_audios",
                    "ref_video_audios", "guide_frame", "identity_frame"):
            val = slots.get(key)
            if val in (None, "", [], {}):
                continue
            ids = val if isinstance(val, list) else [val]
            media[key] = [int(i) for i in ids if str(i).isdigit()]
        params_out = {k: v for k, v in slots.items()
                      if k in ("seconds", "frames", "steps", "width", "height", "seed", "cfg", "denoise",
                               "turbo", "aspect_ratio", "megapixels", "language", "ref_text", "temperature",
                               "x_vector_only")}
        return cls(kind=str(params.get("kind") or "video"), prompt=str(slots.get("prompt") or ""),
                   negative_prompt=str(slots.get("negative_prompt") or ""), media=media, params=params_out,
                   placement=str(params.get("instance_placement") or "local"))

    def provided(self) -> set[str]:
        got = set()
        if self.prompt.strip():
            got.add("prompt")
        if self.negative_prompt.strip():
            got.add("negative_prompt")
        got |= {k for k, v in self.media.items() if v}
        got |= {k for k, v in self.params.items() if v not in (None, "", [], {})}
        return got

    def needs(self) -> set[str]:
        """这条任务在语义上「必须有人吃」的东西。给了参考视频却没工作流用，等于白跑。"""
        need = set()
        if self.kind == "video":
            need.add("prompt")
        if self.media.get("first_frame") and self.media.get("last_frame"):
            need.add("last_frame")
        return need


@dataclass
class Scored:
    workflow: dict[str, Any]
    score: int
    reasons: list[str]
    missing: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.workflow.get("id"), "name": self.workflow.get("name"), "score": self.score,
                "reasons": self.reasons, "missing": self.missing, "taskKind": self.workflow.get("task_kind"),
                "executesOn": self.workflow.get("executes_on"), "priority": self.workflow.get("priority")}


def wf_row(row: Any) -> dict[str, Any]:
    """ORM 行 → 选择器要的字典。队列和路由共用这一份，别各写各的字段拼法。"""
    return {
        "id": row.id,
        "name": row.name,
        "task_kind": row.task_kind,
        "executes_on": row.executes_on,
        "signals": list(row.signals or []),
        "gaps": list(row.gaps or []),
        "pending_media": list(row.pending_media or []),
        "auto_select": bool(row.auto_select),
        "priority": int(row.priority or 0),
        "verified_at": row.verified_at,
        "requirements": row.requirements or {},
        "graph": row.graph,
    }


def _placement_ok(row: dict[str, Any], placement: str) -> bool:
    on = str(row.get("executes_on") or "any")
    if on == "any":
        return True
    if on == "local":
        return placement in ("local", "cloud_self")
    return placement == "cloud_runninghub"


def rank(rows: list[dict[str, Any]], task: Task, *, limit: int = 5) -> list[Scored]:
    """给候选工作流排序。返回空列表表示库里没有能干的，调用方要回落内置模板。"""
    out: list[Scored] = []
    provided = task.provided()
    for row in rows:
        if not row.get("auto_select", True):
            continue
        reasons: list[str] = []
        if str(row.get("task_kind")) != task.kind:
            continue
        if row.get("gaps"):
            reasons.append(f"这台实例还缺 {len(row['gaps'])} 处（{row['gaps'][0].get('class_type')}）")
            continue
        if not _placement_ok(row, task.placement):
            reasons.append(f"这条只能在 {row.get('executes_on')} 上跑")
            continue
        signals = {s["name"]: s for s in (row.get("signals") or [])}
        if not signals:
            reasons.append("没解析出任务信号")
            continue
        need = [n for n in ("prompt", "first_frame") if signals.get(n, {}).get("required")]
        missing = [n for n in need if n not in provided]
        if missing:
            reasons.append("缺必填输入：" + "、".join(missing))
            continue
        # 素材驱动的工作流一条素材都没收到时，图上那些 LoadImage/LoadVideo 会留着模板作者的
        # 默认文件跑完 —— 用户点「文生图」拿到的却是别人那张照片的设定图。这类任务该回落到
        # 真正不需要素材的内置模板，而不是靠扣分侥幸排到最后。
        given_media = {k for k, v in task.media.items() if v}
        wants_media = any(s.get("type") in ("image", "video", "audio") for s in signals.values()) or bool(
            row.get("pending_media"))
        if wants_media and not given_media:
            reasons.append("这条要素材驱动，本次任务什么都没给（会用图里残留的作者默认文件）")
            continue
        score = 0
        consumed = [n for n in provided if n in signals]
        score += 3 * len(consumed)
        reasons.append("吃任务给的：" + "、".join(consumed) if consumed else "只用任务的一部分输入")
        # 越具体的越优先：给了参考视频/音频，能用的工作流比只能首尾帧的值钱
        for name in ("ref_videos", "ref_audios", "ref_images", "last_frame"):
            if name in provided and name in signals:
                score += 4 if name == "ref_videos" else 2
        # 任务要首尾帧（导演台的主路径）而这条只有首帧位，会丢尾帧
        if task.media.get("last_frame") and "last_frame" not in signals:
            score -= 6
            reasons.append("没有尾帧输入，首尾帧衔接会退化成单帧")
        pending = row.get("pending_media") or []
        unfillable = [a for a in pending if not _coverable(a, signals, task)]
        if unfillable:
            score -= 3
            reasons.append(f"{len(unfillable)} 个素材位任务给不出")
        score += int(row.get("priority") or 0) // 10
        if row.get("verified_at"):
            score += 5
            reasons.append("这条在本机真跑通过")
        out.append(Scored(workflow=row, score=score, reasons=reasons, missing=[]))
    out.sort(key=lambda s: (-s.score, -(s.workflow.get("priority") or 0), str(s.workflow.get("name"))))
    return out[:limit]


def _coverable(address: str, signals: dict[str, dict[str, Any]], task: Task) -> bool:
    """待指素材能不能从这次任务里拿到东西 —— 按信号地址前缀对上就行。"""
    nid = address.split(".", 1)[0]
    for sig in signals.values():
        if sig.get("type") not in ("image", "video", "audio"):
            continue
        for a in list(sig.get("addresses") or []) + list(sig.get("also") or []):
            if str(a).split(".", 1)[0] == nid and sig["name"] in task.media:
                return True
    return False


class SlotStyle:
    """槽位两种写法的区分：地址写法（工作流页高级用法）与信号写法（项目生成按钮）。"""

    @staticmethod
    def is_address_like(key: str) -> bool:
        head, _, tail = key.partition(".")
        return bool(tail) and head.isdigit()


def _required_inputs(object_info: dict[str, Any], class_type: str) -> set[str]:
    return set(((object_info.get(class_type) or {}).get("input") or {}).get("required", {}) or {})


def _renumber_group(ins: dict[str, Any], base: str, lost_index: int) -> None:
    """autogrow 组被剪掉一路素材后把号位压实，保住组的最小白点。

    `images.image0/image1` 这种展平写法，ComfyUI 校验的是**从组原有最小白点起连续**的号位：
    剪掉 image0 只剩 image1，组里看着还有东西，提交却以「节点 81：Required input is missing」退回
    （本机 Klein 人物设定图在无参考图时就是这么全军覆没的）。
    """
    pat = re.compile(rf"^{re.escape(base)}\.([a-zA-Z_]*?)(\d+)$")
    members: list[tuple[str, int, Any]] = []
    for k in list(ins):
        m = pat.match(k)
        if m:
            members.append((k, int(m.group(2)), ins[k]))
    if not members:
        return
    floor = min(min(idx for _, idx, _ in members), lost_index)
    prefix = pat.match(members[0][0]).group(1)
    for k, _, v in members:
        ins.pop(k, None)
    for i, (_, _, v) in enumerate(sorted(members, key=lambda x: x[1])):
        ins[f"{base}.{prefix}{floor + i}"] = v


def _drop_node(graph: dict[str, Any], nid: str, notes: list[str], object_info: dict[str, Any],
               dropped: set[str] | None = None) -> None:
    """删掉一个节点，并把「因此缺了必填输入」的下游一起删掉。

    不级联就会留下一个必填输入被断掉的节点 —— ComfyUI 在提交校验阶段就以
    「Prompt outputs failed validation｜image」退回来，任务白排队。
    """
    stack = [str(nid)]
    while stack:
        cur = stack.pop()
        nd = graph.pop(cur, None)
        if nd is None:
            continue
        if dropped is not None:
            dropped.add(cur)
        for cnid, cnd in list(graph.items()):
            ins = cnd.get("inputs") or {}
            for fname, val in list(ins.items()):
                hit = isinstance(val, list) and len(val) == 2 and str(val[0]) == cur
                dict_hit = isinstance(val, dict) and any(
                    isinstance(x, list) and len(x) == 2 and str(x[0]) == cur for x in val.values())
                if not (hit or dict_hit):
                    continue
                if hit:
                    ins.pop(fname, None)
                else:
                    for k, x in list(val.items()):
                        if isinstance(x, list) and len(x) == 2 and str(x[0]) == cur:
                            val.pop(k, None)
                    if not val:
                        ins.pop(fname, None)
                base = fname.split(".", 1)[0]
                # 剪的是 autogrow 组里的一路素材：先把剩下的号位压实，别让组中间出现空洞
                m_idx = re.match(r"^[A-Za-z_]*(\d+)$", fname.split(".", 1)[1] if "." in fname else "")
                if m_idx and any(str(k).split(".", 1)[0] == base for k in ins):
                    _renumber_group(ins, base, int(m_idx.group(1)))
                if base not in _required_inputs(object_info, cnd.get("class_type") or ""):
                    continue
                # autogrow 素材组在 required 里是整组必填，但少一路素材是正常事：
                # 「只给 1 张参考图」不该把整条生成链拆掉（本机实测就连带删了 SaveVideo，
                # 之后写 filename_prefix 撞上「槽位指着不存在的节点」）。组里还剩别的位就不级联。
                if "." in fname and any(str(k).split(".", 1)[0] == base for k in ins):
                    continue
                stack.append(cnid)
        notes.append(f"撤掉 #{cur}（{nd.get('class_type')}）")


def _add_node(graph: dict[str, Any], class_type: str, inputs: dict[str, Any], title: str) -> str:
    nums = [int(k) for k in graph if str(k).isdigit()]
    nid = str(max(nums, default=0) + 1)
    graph[nid] = {"class_type": class_type, "inputs": inputs, "_meta": {"title": title}}
    return nid


def _attach_media(graph: dict[str, Any], addr: str, sig_type: str, name: str, notes: list[str],
                  object_info: dict[str, Any], dropped: set[str], index: int, label: str) -> None:
    """把一份素材接到「消费节点上的组位」：插加载节点，再把组位改成连线。

    参考视频要的是帧，所以得像导入时那样 LoadVideo → GetVideoComponents(0)，
    音轨（出口 1）留给 ref_video_audios 那一路用；直接写文件名会被 ComfyUI 退回。
    """
    nid, _, fld = str(addr).partition(".")
    nd = graph.get(nid)
    if nd is None:
        return
    prev = (nd.get("inputs") or {}).get(fld)
    if sig_type == "video":
        load = _add_node(graph, "LoadVideo", {"file": name}, f"参考视频 {index}")
        gvc = _add_node(graph, "GetVideoComponents", {"video": [load, 0]}, "拆出帧与音轨")
        ref, kind_note = [gvc, 0], "帧"
    elif sig_type == "audio":
        ref, kind_note = [_add_node(graph, "LoadAudio", {"audio": name}, f"参考音频 {index}"), 0], "音频"
    else:
        ref, kind_note = [_add_node(graph, "LoadImage", {"image": name}, f"参考图 {index}"), 0], "图"
    nd.setdefault("inputs", {})[fld] = ref
    notes.append(f"{label} 第 {index} 个 ← {name}（{kind_note}，新增加载节点）")
    if isinstance(prev, list) and len(prev) == 2 and not _has_consumer(graph, str(prev[0])):
        _drop_node(graph, str(prev[0]), notes, object_info, dropped)


async def prepare(row: dict[str, Any], task: Task, *, ctx: Any,
                  filename_prefix: str | None = None) -> tuple[dict[str, Any], list[str]]:
    """把任务落到工作流自己的输入点上，产出一张可提交的图。

    ctx 是 gen.templates.BuildContext：素材按 media id 从媒体库取文件、上传到目标实例的
    input 目录（同实例同文件只传一次），这套语义与内置模板完全一致 —— 两条路必须给同一个
    实例送同一份文件，不然同一个参考图会在实例里存两份、缓存也就对不上。
    """
    import copy

    graph = copy.deepcopy(row.get("graph") or {})
    object_info = row.get("objectInfo") or await ctx.client.object_info()
    notes: list[str] = []
    signals = row.get("signals") or []
    by_name = {s["name"]: s for s in signals}
    dropped: set[str] = set()

    def set_addr(addr: str, value: Any) -> None:
        nid, _, fld = str(addr).partition(".")
        node = graph.get(nid)
        if node is None:
            # 这个槽位所在节点是本次填图自己撤掉的（素材没给到、分支没人引用），不是实例漂移
            if nid in dropped:
                return
            raise GenError(f"工作流「{row.get('name')}」的槽位 {addr} 指着一个不存在的节点 "
                           "（实例上的 /object_info 变了？请重新扫描这条工作流）", kind="workflow_drift")
        node.setdefault("inputs", {})[fld] = value

    for name, value in (("prompt", task.prompt), ("negative_prompt", task.negative_prompt)):
        sig = by_name.get(name)
        if sig and value:
            for addr in sig["addresses"]:
                set_addr(addr, value)
            notes.append(f"{sig.get('label') or name} 写入 {len(sig['addresses'])} 处")

    mapped = translate_size(task, by_name)
    for key, value in mapped.items():
        if key == "turbo":
            continue
        sig = by_name.get(key)
        if sig is None and key == "seconds":
            sig = by_name.get("frames")
            if sig:
                value = h3_length(float(value))
                notes.append(f"时长 {task.params['seconds']} 秒按 17k+5 吸附成 {value} 帧")
        if sig is None:
            continue
        for addr in sig["addresses"]:
            set_addr(addr, value)

    for sig in signals:
        if sig.get("type") not in ("image", "video", "audio"):
            continue
        addrs = list(sig["addresses"] or [])
        ids = list(task.media.get(sig["name"]) or [])
        if sig["name"] == "ref_images" and not ids:
            ids = list(task.media.get("identity_frame") or [])
        for i, addr in enumerate(addrs):
            if i < len(ids):
                name = await ctx.to_input(int(ids[i]))
                nid, _, fld = str(addr).partition(".")
                if "." in fld and (graph.get(nid) or {}).get("inputs", {}).get(fld) is not None:
                    # 素材挂在消费节点的组位上（136.ref_audios.ref_audio_0）：这一位要的是
                    # AUDIO/IMAGE 数据流，写文件名进去提交就会被退回，所以插一个加载节点接上。
                    # 「全能参考吃任务给的音色」走的就是这条路。
                    _attach_media(graph, addr, sig["type"], name, notes, object_info, dropped, i + 1,
                                  str(sig.get("label") or sig["name"]))
                    continue
                set_addr(addr, name)
                notes.append(f"{sig.get('label')} 第 {i + 1} 个 ← {name}")
            else:
                nid, _, fld = str(addr).partition(".")
                ref = ((graph.get(nid) or {}).get("inputs") or {}).get(fld)
                if "." in fld and isinstance(ref, list) and len(ref) == 2:
                    # 这是素材组里的一路（136.ref_audios.ref_audio_0），消费节点不是加载器：
                    # 只断这一路，绝不能把消费节点整块撤掉。
                    # 本机实测踩过：把 MiniMaxH3ReferenceToVideo 当加载器撤了，整条生成链一起消失，
                    # 任务最后以「任务缺少 graph」失败，看起来像是填图代码没填。
                    src = str(ref[0])
                    graph[nid]["inputs"].pop(fld, None)
                    # 上游只在没人接它了才撤 —— GetVideoComponents 有两个出口（画面帧 / 声音），
                    # 不给参考音频时把整个分解节点撤掉，会连带把已经接好的参考视频也断掉。
                    if not _has_consumer(graph, src):
                        _drop_node(graph, src, notes, object_info, dropped)
                    continue
                _drop_node(graph, nid, notes, object_info, dropped)
        if len(ids) > len(addrs):
            notes.append(f"{sig.get('label')} 给了 {len(ids)} 个，这条只有 {len(addrs)} 个位置，多出来的没用上")

    pref = by_name.get("filename_prefix")
    if pref and filename_prefix:
        for addr in pref["addresses"]:
            set_addr(addr, filename_prefix)

    # 撤掉素材位之后可能留下没人引用的采样支路：ComfyUI 会执行 prompt 里列出的每个节点，
    # 空文件名的加载器或没人用的采样器都会把整个任务顶死，所以这里再收一遍。
    # 撤素材之后，凡是够不到任何保存/预览出口的节点都不该再提交：ComfyUI 会执行 prompt
    # 里列出的每一个节点，留一条断了的支路就是白跑甚至白报错。
    anchors = [n for n, d in graph.items() if str(d.get("class_type") or "").startswith(("Save", "Preview"))]
    if not anchors:
        raise GenError(
            f"工作流「{row.get('name')}」填图之后一个保存/预览节点都不剩 —— 任务给的素材比这条要的少，"
            "换一条能吃下这批素材的工作流（或把素材补齐）",
            kind="workflow_drift",
        )
    keep: set[str] = set()
    for a in anchors:
        keep |= _upstream(graph, a)
    for nid in [n for n in graph if n not in keep]:
        ct = (graph[nid].get("class_type") or "")
        graph.pop(nid, None)
        notes.append(f"删掉够不到任何出口的 {ct} #{nid}")
    return graph, notes


def _upstream(graph: dict[str, Any], nid: str) -> set[str]:
    seen: set[str] = set()
    stack = [str(nid)]
    while stack:
        cur = stack.pop()
        if cur in seen or cur not in graph:
            continue
        seen.add(cur)
        for val in (graph[cur].get("inputs") or {}).values():
            refs = val.values() if isinstance(val, dict) else [val]
            for ref in refs:
                if isinstance(ref, list) and len(ref) == 2:
                    stack.append(str(ref[0]))
    return seen


def _has_consumer(graph: dict[str, Any], nid: str) -> bool:
    for other in graph.values():
        for val in (other.get("inputs") or {}).values():
            if isinstance(val, list) and len(val) == 2 and str(val[0]) == nid:
                return True
            if isinstance(val, dict) and any(isinstance(x, list) and len(x) == 2 and str(x[0]) == nid
                                             for x in val.values()):
                return True
    return False




_RATIO_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*[:xX]\s*(\d+(?:\.\d+)?)")


def translate_size(task: Task, by_name: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """把前端说的尺寸话翻译成这条工作流自己的说法（画幅档位 + 兆像素）。

    导入的工作流十有八九用 ResolutionSelector 定尺寸，图上根本没有可直接写的 width/height
    控件。硬塞 width/height 会「参数表看着生效、提交上去没改动」，所以按同一套语义换算
    （1 MP = 1024×1024，与核心节点的公式一致）。
    两种任务说法都要认：给宽×高，或给「9:16」这种裸比例 —— 裸比例必须落成节点候选清单里的
    那一串写法（"9:16 (Portrait Widescreen)"），否则提交时被 ComfyUI 当枚举值退回。
    """
    params = dict(task.params or {})
    ar_sig, mp_sig = by_name.get("aspect_ratio"), by_name.get("megapixels")
    if ar_sig is None or mp_sig is None:
        return params

    w, h = params.get("width"), params.get("height")
    want: float | None = None
    if w and h:
        want = float(w) / float(h)
    else:
        m = _RATIO_RE.match(str(params.get("aspect_ratio") or ""))
        if m:
            want = float(m.group(1)) / float(m.group(2))
    if want is not None:
        best, best_err = None, None
        for opt in (ar_sig.get("options") or []):
            mo = _RATIO_RE.match(str(opt))
            if not mo:
                continue
            err = abs((float(mo.group(1)) / float(mo.group(2))) - want) / want
            if best_err is None or err < best_err:
                best, best_err = opt, err
        if best:
            if w and h:
                params.pop("width", None)
                params.pop("height", None)
                params["megapixels"] = round(float(w) * float(h) / (1024 * 1024), 3)
            params["aspect_ratio"] = best
        else:
            return params

    lo = float(mp_sig.get("min") or 0.1)
    hi = float(mp_sig.get("max") or 16.0)
    if "megapixels" in params:
        params["megapixels"] = round(max(lo, min(hi, float(params["megapixels"]))), 3)
    task.params = params
    return params
