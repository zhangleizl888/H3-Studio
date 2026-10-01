"""UI 格式 ↔ API 格式转换，以及 slot 抽取。

ComfyUI 服务端没有转换器（官方流程是在前端 File→Export (API)），所以我们必须自己写。
这是本方案技术上最难的一块，也是「导入任意工作流」能不能成立的关键。

失败策略遵循 PLAN §12：**明确报错并指出 node_id，绝不做静默降级**。
一个悄悄丢掉连线的转换器比一个报错的转换器危险得多。
"""

from __future__ import annotations

import json

from dataclasses import dataclass, field
from typing import Any


# 节点之间传输的数据类型。object_info 里首元素是这些字符串之一 = 需要连线，其余一律按控件处理。
# 反过来列「控件类型」是不行的：COMBO / COMFY_DYNAMICCOMBO_V3 / INT64 / SEED / 各版本新增的
# 动态控件名太多，漏一个就把该控件的值当成连线静默丢掉（本机在 SaveVideo.format 上真的丢了）。
_STATIC_LINK_TYPES = {
    "MODEL", "CLIP", "VAE", "CONDITIONING", "LATENT", "IMAGE", "MASK", "VIDEO", "AUDIO",
    "PIXEL_AREA", "SAMPLER", "SIGMAS", "NOISE", "GUIDER", "CONTROL_NET", "UPSCALE_MODEL",
    "STOCK_SCHEDULER", "BOUNDING_BOX", "DETAILER", "EMBEDS", "TEXT_ENCODER_OUTPUT",
    "WIDGET", "ANY", "*",
}


class ConversionError(Exception):
    def __init__(self, message: str, *, node_id: str | None = None, class_type: str | None = None) -> None:
        super().__init__(message)
        self.node_id = node_id
        self.class_type = class_type

    def as_dict(self) -> dict[str, Any]:
        return {"message": str(self), "node_id": self.node_id, "class_type": self.class_type}


@dataclass
class NodeInfo:
    """object_info 里一个 class_type 的规范化视图。"""

    class_type: str
    required: dict[str, Any] = field(default_factory=dict)
    optional: dict[str, Any] = field(default_factory=dict)
    input_order: list[str] = field(default_factory=list)
    output: list[str] = field(default_factory=list)
    output_name: list[str] = field(default_factory=list)
    data_types: set[str] = field(default_factory=_STATIC_LINK_TYPES.__or__ if False else set)

    def spec_of(self, name: str) -> dict[str, Any] | None:
        if name in self.required:
            return {"spec": self.required[name], "required": True}
        if name in self.optional:
            return {"spec": self.optional[name], "required": False}
        # Autogrow 点名：ref_images.ref_image_3 → 用组名 ref_images 去查
        if "." in name:
            group = name.split(".", 1)[0]
            for bag in (self.required, self.optional):
                for key, val in bag.items():
                    if isinstance(val, (list, tuple)) and len(val) > 1 and isinstance(val[1], dict) and val[1].get("name") == name:
                        return {"spec": val, "required": key in self.required}
                if group in bag:
                    return {"spec": bag[group], "required": group in self.required}
        return None

    def is_widget(self, name: str) -> bool:
        """判别 widget 还是连线。

        object_info 的真实编码（本机实测）：
          - `"unet_name": [["a.safetensors","b.safetensors"], {...}]` → 首元素是**字符串列表**
            = 下拉选项清单，这是 **widget**
          - `"model": ["MODEL", {...}]` → 首元素是**节点数据类型名** = 需要连线
          - `"seed": ["INT", {...}]` → 首元素是**基础类型名** = widget
        把列表当成连线会把整张图的 widget 全部错位 —— 参数会静默跑到别的控件上。
        """
        got = self.spec_of(name)
        if not got:
            return False
        spec = got["spec"]
        if not isinstance(spec, (list, tuple)) or not spec:
            return False
        first = spec[0]
        if isinstance(first, list):
            # [["IMAGE"], {...}] 是「带类型的连线槽」；[["a.safetensors", ...], {...}] 才是下拉选项。
            # 不加这个区分，images.image_1 这类 autogrow 连线会被当成控件，widgets_values 整体错位。
            items = [x for x in first if isinstance(x, str)]
            if items and all(x.upper() in _STATIC_LINK_TYPES for x in items):
                return False
            return True  # combo 选项清单
        if isinstance(first, str):
            return first.upper() not in _STATIC_LINK_TYPES
        return False

    def combo_options(self, name: str) -> list[str] | None:
        got = self.spec_of(name)
        if not got:
            return None
        spec = got["spec"]
        if not isinstance(spec, (list, tuple)) or not spec:
            return None
        if isinstance(spec[0], list):
            return [c for c in spec[0] if isinstance(c, str)]
        if isinstance(spec[0], str) and spec[0].upper() == "COMBO" and len(spec) > 1 and isinstance(spec[1], dict):
            opts = spec[1].get("options")
            if isinstance(opts, list):
                return [str(c) for c in opts]
        return None

    def cfg_of(self, name: str) -> dict[str, Any]:
        got = self.spec_of(name)
        if not got:
            return {}
        spec = got["spec"]
        if isinstance(spec, (list, tuple)) and len(spec) > 1 and isinstance(spec[1], dict):
            return spec[1]
        return {}

    def widget_slots(self) -> list[tuple[str, bool]]:
        """把 input_order 展开成 UI 的 widgets_values 槽位序列。

        两个必须排除的类别：
          1. `socketless` / 纯显示控件 —— 例如 ResolutionSelector 的 `preview`
             （类型 RESOLUTION_PREVIEW，标记 advanced+socketless）。它**不会出现在
             widgets_values 里**，算进槽位就会让数量对不上，进而把值喂给错误的控件。
          2. hidden 输入（prompt / extra_pnginfo 等由服务端注入）。

        带 control_after_generate 的控件在 UI 里占用**两个**值（值 + 'randomize'/'fixed'），
        这是导出格式的一部分，必须展开成两个槽位，否则整体错位。
        """
        out: list[tuple[str, bool]] = []
        for name in self.input_order:
            if not self.is_widget(name):
                continue
            if self.is_socket_group(name):
                continue
            cfg = self.cfg_of(name)
            if cfg.get("socketless") or cfg.get("hidden") or cfg.get("is_output"):
                continue
            out.append((name, False))
            if cfg.get("control_after_generate"):
                out.append((f"{name}.control_after_generate", True))
        return out

    def nested_required(self, field: str, chosen: str) -> dict[str, Any]:
        """COMFY_DYNAMICCOMBO_V3：选中 key 带出来的那组输入。

        本机实测 SaveImageAdvanced.format='png' → 服务端要 bit_depth 与 input_color_space，
        而这两个名字**不在 input_order 里**，UI 导出也不给位置。不补就是
        「Required input is missing: bit_depth」，提交直接 400。
        """
        got = self.spec_of(field)
        if not got:
            return {}
        spec = got["spec"]
        if not isinstance(spec, (list, tuple)) or len(spec) < 2 or not isinstance(spec[1], dict):
            return {}
        for opt in spec[1].get("options") or []:
            if isinstance(opt, dict) and opt.get("key") == chosen:
                bag = (opt.get("inputs") or {}).get("required") or {}
                return {k: v for k, v in bag.items() if isinstance(v, (list, tuple)) and v}
        return {}

    def is_dynamic_combo(self, name: str) -> bool:
        got = self.spec_of(name)
        if not got:
            return False
        spec = got["spec"]
        return isinstance(spec, (list, tuple)) and bool(spec) and isinstance(spec[0], str) and spec[0].upper() == "COMFY_DYNAMICCOMBO_V3"

    def is_socket_group(self, name: str) -> bool:
        """COMFY_AUTOGROW_V3 是「可以长出一排连线」的动态组，不是控件。

        官方 Qwen-Image-2.1 模板实测：TextEncodeQwenImage21.images 就是这个类型，
        而它的 widgets_values 里**没有**它的位置（["", "", 1024] 只有 prompt/负向/resolution）。
        当成控件会让整条序列错位，resolution 被丢掉 → 提交 400。
        """
        got = self.spec_of(name)
        if not got:
            return False
        spec = got["spec"]
        return isinstance(spec, (list, tuple)) and bool(spec) and isinstance(spec[0], str) and spec[0].upper() == "COMFY_AUTOGROW_V3"


    def default_of(self, name: str) -> Any:
        """节点自己声明的默认值。回填它不是猜测 —— ComfyUI 执行缺省输入时用的就是这个值。"""
        got = self.spec_of(name)
        if not got:
            return None
        spec = got["spec"]
        if isinstance(spec, (list, tuple)) and len(spec) > 1 and isinstance(spec[1], dict):
            if "default" in spec[1]:
                return spec[1]["default"]
        return None

    def hidden_widget_names(self) -> set[str]:
        """声明为控件但被 hidden/socketless 排除的名字。

        这类输入在 UI 导出里本来就没有位置（SaveVideo.codec 就是），
        所以 api→ui→api 往返丢掉它是 UI 格式的固有限制，必须如实报告而不是假装无损。
        """
        out: set[str] = set()
        for name in self.input_order:
            if not self.is_widget(name):
                continue
            cfg = self.cfg_of(name)
            if cfg.get("hidden") or cfg.get("socketless") or cfg.get("is_output"):
                out.add(name)
        return out

    def type_name(self, name: str) -> str:
        got = self.spec_of(name)
        if not got:
            return "STRING"
        spec = got["spec"]
        if isinstance(spec, (list, tuple)) and spec:
            if isinstance(spec[0], list):
                return "COMBO"
            if isinstance(spec[0], str):
                return spec[0].upper()
        return "STRING"



def build_index(object_info: dict[str, Any]) -> dict[str, NodeInfo]:
    """把 /object_info 规范化成 class_type → NodeInfo，并收集这个实例上的全部数据类型。"""
    # 只用固定的连接类型集合。不要拿「所有节点的 output 类型」来扩充：
    # core 里有节点输出 STRING / INT / FLOAT，那样会把同名控件反向误判成连线，
    # 值就被当成连线静默丢掉了。
    data_types = set(_STATIC_LINK_TYPES)
    index: dict[str, NodeInfo] = {}
    for class_type, meta in object_info.items():
        if not isinstance(meta, dict):
            continue
        inp = meta.get("input") or {}
        order = ((meta.get("input_order") or {}).get("all")) or []
        if not order:
            order = list((inp.get("required") or {}).keys()) + list((inp.get("optional") or {}).keys())
        index[class_type] = NodeInfo(
            class_type=class_type,
            required=inp.get("required") or {},
            optional=inp.get("optional") or {},
            input_order=list(order),
            output=list(meta.get("output") or []),
            output_name=list(meta.get("output_name") or []),
            data_types=data_types,
        )
    return index


def normalize_link(link: Any) -> tuple[Any, Any, Any, Any, Any, str] | None:
    """把一条 link 归一化成 (id, origin_id, origin_slot, target_id, target_slot, type)。

    ComfyUI 有两种形状，且会在同一份文件里并存（本机实测官方模板就是这样）：
      - 外层 `links`：数组 `[672, 459, 0, 461, 0, "IMAGE"]`
      - 子图内 `links`：对象 `{"id":647,"origin_id":453,"origin_slot":0,"target_id":452,"target_slot":0,"type":"CLIP"}`
    只认数组会让子图的转发关系全部丢失，表现为「连线引用的 link 不存在」或参数静默不生效。
    """
    if isinstance(link, dict):
        link_id = link.get("id")
        origin_id = link.get("origin_id", link.get("originId"))
        origin_slot = link.get("origin_slot", link.get("originSlot"))
        target_id = link.get("target_id", link.get("targetId"))
        target_slot = link.get("target_slot", link.get("targetSlot"))
        ltype = link.get("type", "NODE")
        if link_id is None or origin_id is None or target_id is None:
            return None
        return (link_id, origin_id, _as_int(origin_slot), target_id, _as_int(target_slot), str(ltype))
    if isinstance(link, (list, tuple)) and len(link) >= 5:
        ltype = link[5] if len(link) > 5 else "NODE"
        return (link[0], link[1], _as_int(link[2]), link[3], _as_int(link[4]), str(ltype))
    return None


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _trailing_defaultable(info: NodeInfo, widget_order: list[str], shortfall: int) -> int:
    """从尾部数起，有多少个控件是「有默认值、可以安全补齐」的。"""
    count = 0
    for name in reversed(widget_order):
        if name.endswith(".control_after_generate"):
            continue
        if info.default_of(name) is None:
            break
        count += 1
        if count >= shortfall:
            return count
    return count


# 纯前端节点：只存在于 UI 图里，不在 /object_info 中，也不会进入 API 图。
# MarkdownNote / Note 是画布上的说明文字，必须静默剔除（否则会误报「实例缺节点」，
# 而官方模板每一个都带 MarkdownNote —— 不处理就等于所有模板都导入失败）。
_UI_ONLY_TYPES = {"markdownnote", "note", "notetext"}


def _is_ui_only_node(class_type: Any) -> bool:
    return isinstance(class_type, str) and class_type.lower() in _UI_ONLY_TYPES


def ui_to_api(ui_graph: dict[str, Any], object_info: dict[str, Any], *, report: list[str] | None = None) -> dict[str, Any]:
    """UI 格式 → API 格式（`POST /prompt` 收的那种扁平图）。

    子图要先用 app.gen.subgraph.flatten_subgraphs 摊平，这里只处理摊平后的普通节点。
    传 report=[] 可以拿到「按默认值补齐」这类降级警告，导入向导要原样展示给用户。
    """
    warnings: list[str] = []
    if not isinstance(ui_graph, dict) or "nodes" not in ui_graph:
        raise ConversionError("这不是 UI 格式的工作流：找不到 nodes 数组")

    index = build_index(object_info)
    nodes = ui_graph["nodes"] or []

    # ① link 表：linkId → (源节点, 源槽, 目标节点, 目标槽, 类型)
    links: dict[Any, tuple[str, int, str, int, str]] = {}
    for link in ui_graph.get("links") or []:
        normalized = normalize_link(link)
        if normalized is None:
            continue
        links[normalized[0]] = (str(normalized[1]), int(normalized[2]), str(normalized[3]), int(normalized[4]), str(normalized[5]))

    overrides = ui_graph.get("_subgraph_overrides") or {}
    graph: dict[str, Any] = {}
    for node in nodes:
        node_id = str(node["id"])
        overrides_for_node = overrides.get(node_id)
        class_type = node.get("type")

        # 子图实例的 type 是 UUID。官方模板全是这个形态，必须先摊平再转换
        if isinstance(class_type, str) and _looks_like_uuid(class_type):
            raise ConversionError(
                "这里还有未摊平的子图实例节点。请先调用 app.gen.subgraph.flatten_subgraphs(ui_graph, object_info)，"
                "再把它的结果交给 ui_to_api。",
                node_id=node_id,
                class_type=class_type,
            )
        if _is_ui_only_node(class_type):
            continue  # 画布注释，不进入 API 图
        if isinstance(class_type, str) and class_type.lower() == "reroute":
            raise ConversionError(
                f"节点 {node_id} 是 Reroute（连线中继）。它会改变连线拓扑，丢弃会静默改变图语义 —— "
                "请在 ComfyUI 里删掉中继节点后重新导出。",
                node_id=node_id,
                class_type=class_type,
            )
        info = index.get(class_type)
        if info is None:
            raise ConversionError(f"实例上没有这个节点类型：{class_type}", node_id=node_id, class_type=class_type)

        inputs: dict[str, Any] = {}

        # mode：2=muted（输出直通输入）、4=bypass。这两种要单独旁路，不能当普通节点提交
        mode = node.get("mode", 0)
        if mode in (2, 4):
            # 只把第一个输入原样透传出去；拿不到就报错，避免静默改变语义
            first_input = next((i for i in node.get("inputs") or [] if i.get("link") is not None), None)
            if first_input is None:
                raise ConversionError(f"节点被静默/旁路（mode={mode}）但没有连线，无法确定透传哪一路", node_id=node_id, class_type=class_type)
            src = links.get(first_input["link"])
            if src is None:
                raise ConversionError(f"节点 mode={mode} 但引用的 link 不存在", node_id=node_id, class_type=class_type)
            graph[node_id] = {"class_type": class_type, "inputs": {**inputs, info.input_order[0]: [src[0], src[1]]}, "_meta": {"title": _title(node, class_type)}}
            continue

        # ② 连线：按 socket 上的 link id 反查源
        for socket in node.get("inputs") or []:
            link_id = socket.get("link")
            name = socket.get("name")
            if link_id is None or name is None:
                continue
            src = links.get(link_id)
            if src is None:
                # Convert Widget 等情况下 link 会指向别处；丢了就该报错
                raise ConversionError(f"节点输入 {name} 引用了不存在的 link {link_id}", node_id=node_id, class_type=class_type)
            inputs[name] = [src[0], src[1]]

        # ③ widget：按 input_order 顺序消费 widgets_values
        # control_after_generate 的槽位**永远保留**：官方模板实测，seed 即使被连线提升走，
        # 它的 'fixed'/'randomize' 仍占着 widgets_values 的第二个位置。按「有连线就删槽」
        # 会把 KSampler 的 7 值序列挤成 6 格 —— denoise 拿到 "simple"、尾部 1 被丢弃，
        # 参数整体错位一格：提交不报错，跑出来是错的。
        slots = info.widget_slots()
        with_control = [n for n, _ in slots]
        without_control = [n for n, _ in slots if not n.endswith(".control_after_generate")]
        control_flags = {n for n, is_ctrl in slots if is_ctrl}
        values = node.get("widgets_values") or []
        # 三种导出形态都合法，按长度认领而不是猜：
        #   ① 带 control_after_generate 值（前端默认）② 不带（第三方导出）
        #   ③ 子图把控件提升到实例层后内层少写（值在 _subgraph_overrides 里）
        override_names = set((overrides_for_node or {}).keys())
        candidates: list[tuple[str, list[str]]] = [("with", with_control), ("without", without_control)]
        if override_names:
            candidates.append(("with-promoted", [n for n in with_control if n not in override_names]))
            candidates.append(("without-promoted", [n for n in without_control if n not in override_names]))
        picked = next((c for c in candidates if len(c[1]) == len(values)), None) or min(
            candidates, key=lambda c: abs(len(c[1]) - len(values))
        )
        widget_order = picked[1]
        if picked[0].startswith("without"):
            control_flags = set()
        if _proxy_widget_values(node):
            # 控件被搬到别的节点上（properties.proxyWidgets），值不在本节点里。
            # 猜一个位置会让提交上去的参数悄悄错位，所以明确拒绝。
            raise ConversionError(
                "这个节点有被搬移的控件（properties.proxyWidgets），值不在自己的 widgets_values 里，"
                "无法可靠还原。请在 ComfyUI 里把该控件放回本节点后重新导出。",
                node_id=node_id,
                class_type=class_type,
            )
        if len(values) < len(widget_order) and len(values) >= len(widget_order) - _trailing_defaultable(info, widget_order, len(widget_order) - len(values)):
            # 模板比节点定义早一个版本时，尾部控件不会出现在 widgets_values 里。
            # 用节点自己声明的 default 补齐是 ComfyUI 执行时的同一套语义；这是「警告 + 回填」，不是猜。
            for k in range(len(values), len(widget_order)):
                default = info.default_of(widget_order[k])
                if default is None and widget_order[k] not in control_flags:
                    raise ConversionError(
                        f"节点缺少第 {k} 个控件 {widget_order[k]!r} 的值，且该控件没有声明默认值，无法安全补齐。",
                        node_id=node_id, class_type=class_type,
                    )
                values = values + [default]
            warnings.append(
                f"节点 {node_id}（{class_type}）的 widgets_values 比控件序列少，已用节点默认值补齐尾部 "
                f"{widget_order[len(values)-1:] if len(values)>len(widget_order)-1 else widget_order[-1]}"
            )
        elif len(values) > len(widget_order):
            # 模板包比已安装的节点定义旧/新一个版本（本机实测官方 Qwen 模板就是这样：
            # SaveImageAdvanced 旧版有 bit_depth/color_space 控件，当前版本只声明
            # filename_prefix/format，多出来的值正好在尾部）。
            # 按前缀映射与 ComfyUI 前端加载旧工作流的行为一致，但必须点名报出来 ——
            # 否则用户会以为那些输出设置生效了。
            dropped = list(values[len(widget_order):])
            values = list(values[: len(widget_order)])
            unplaceable = sorted(info.hidden_widget_names())
            reason = (
                f"当前实例把 {unplaceable} 声明为 hidden/socketless 控件，UI 导出里没有它们的槽位"
                if unplaceable
                else f"当前实例该节点只声明控件 {widget_order}"
            )
            warnings.append(
                f"节点 {node_id}（{class_type}）模板里多出 {len(dropped)} 个控件值 {dropped}，已丢弃 —— {reason}。"
                "若这不是你要的设置，请在实例上重新保存这个工作流。"
            )
        elif len(values) != len(widget_order):
            raise ConversionError(
                f"widgets_values 有 {len(values)} 个值，但控件序列是 {len(widget_order)} 个（{widget_order}）。"
                "这通常意味着导出时的 ComfyUI 版本与当前实例的节点定义不一致 —— 按顺序硬塞会把值喂给错误的控件。",
                node_id=node_id,
                class_type=class_type,
            )
        for name, value in zip(widget_order, values):
            if name in control_flags:
                continue  # control_after_generate 是纯前端控件，API 里没有这个输入
            if isinstance(value, dict) and value.get("removed"):
                continue
            if value is None:
                continue  # api_to_ui 用来占位的空槽，别写成 None 提交上去
            inputs[name] = value

        # 必填控件在 UI 导出里根本没有位置（hidden / socketless）—— 例如本机实测的
        # SaveImageAdvanced.bit_depth 与 input_color_space：不补就是「Required input is missing」，
        # 补了又不出声就是替用户做决定。所以：按节点自己声明的 default 补 + 指名道姓告警。
        for name in info.input_order:
            if name in inputs or not info.is_widget(name):
                continue
            got = info.spec_of(name)
            if not got or not got["required"]:
                continue
            default = info.default_of(name)
            if default is None:
                continue  # 节点没给默认值：交给 ComfyUI 报错，我们不编一个参数出来
            inputs[name] = default
            warnings.append(
                f"节点 {node_id}（{class_type}）的必填控件 {name!r} 在 UI 导出里没有位置"
                f"（这个版本把它声明成 hidden/socketless），已按节点默认值 {default!r} 补上；"
                "若这不是你要的设置，请在这台实例上重新保存该工作流。"
            )

        # 动态下拉（COMFY_DYNAMICCOMBO_V3）：选中项带出的嵌套输入必须包在**同一个值**里，
        # 形状是 {"value": "png", "bit_depth": "8-bit", "input_color_space": "sRGB"}。
        # 实测：把它们散成顶层同名输入 → 400 Required input is missing；
        # 而选中项没有嵌套输入时（SaveVideo.codec="auto"）纯字符串又是合法的，所以只在
        # 真的带出嵌套输入时才换成 dict，不动已经验证过的简单形态。
        for name in info.input_order:
            chosen = inputs.get(name)
            if not isinstance(chosen, str) or not info.is_dynamic_combo(name):
                continue
            nested = info.nested_required(name, chosen)
            if not nested:
                continue
            # 嵌套输入分两类，实测行为不同，搞错边会把跑通的图改坏：
            #   · 名字出现在节点 input_order 里的（SaveVideo 的 codec）→ **平铺**提交合法，
            #     图上已经摆着的就不动，缺的按默认值平铺补。
            #   · 压根不在 input_order 里的（SaveImageAdvanced 的 bit_depth /
            #     input_color_space）→ 必须包进 {"value": …, …}，平铺会 400。
            inside = {k: v for k, v in nested.items() if k not in info.input_order}
            missing_flat = {k: v for k, v in nested.items() if k in info.input_order and k not in inputs}
            if not inside and not missing_flat:
                continue
            for sub_name, sub_spec in missing_flat.items():
                d = sub_spec[1].get("default") if len(sub_spec) > 1 and isinstance(sub_spec[1], dict) else None
                if d is not None:
                    inputs[sub_name] = d
            if not inside:
                continue
            merged: dict[str, Any] = {"value": chosen}
            for sub_name, sub_spec in inside.items():
                d = sub_spec[1].get("default") if len(sub_spec) > 1 and isinstance(sub_spec[1], dict) else None
                if d is not None:
                    merged[sub_name] = d
            inputs[name] = merged
            warnings.append(
                f"节点 {node_id}（{class_type}）的 {name} 是动态下拉，选 {chosen!r} 会带出 "
                f"{sorted(inside)}（不在节点输入清单里，UI 导出没有位置）；已按默认值打包成 "
                f"{json.dumps(merged, ensure_ascii=False)} 提交。"
            )

        graph[node_id] = {"class_type": class_type, "inputs": inputs, "_meta": {"title": _title(node, class_type)}}

    for node_id, patch in (ui_graph.get("_subgraph_overrides") or {}).items():
        if node_id not in graph:
            continue
        for input_name, value in patch.items():
            graph[node_id]["inputs"][input_name] = value

    _assert_links_exist(graph)
    if report is not None:
        report.extend(warnings)
    return graph


def _assert_links_exist(graph: dict[str, Any]) -> None:
    for node_id, node in graph.items():
        for name, value in node["inputs"].items():
            if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str) and isinstance(value[1], int):
                if value[0] not in graph:
                    raise ConversionError(f"节点 {node_id} 的输入 {name} 指向不存在的节点 {value[0]}")


def api_to_ui(api_graph: dict[str, Any], object_info: dict[str, Any] | None = None) -> dict[str, Any]:
    """API 格式 → UI 格式。

    布局用拓扑分层粗排，不追求好看，够拖就行。
    节点 id 一律规范化成整数 —— UI 格式里 nodes[].id 与 links[][1]/[3] 必须是同一类型，
    混用 str 和 int 会让前端画不出连线。
    """

    index = build_index(object_info or {})

    key_to_id: dict[str, int] = {}
    used: set[int] = set()

    def as_id(key: str) -> int:
        if key in key_to_id:
            return key_to_id[key]
        if key.isdigit():
            candidate = int(key)
            if candidate not in used:
                used.add(candidate)
                key_to_id[key] = candidate
                return candidate
        # 非数字 key（UUID 等）→ 稳定分配一个未占用的整数
        candidate = 900000 + len(key_to_id)
        while candidate in used:
            candidate += 1
        used.add(candidate)
        key_to_id[key] = candidate
        return candidate

    for key in api_graph:
        as_id(key)

    nodes: list[dict[str, Any]] = []
    links: list[list[Any]] = []
    link_id = 1

    for key, node in api_graph.items():
        node_id = as_id(key)
        class_type = node["class_type"]
        info = index.get(class_type)
        order = info.input_order if info else list(node.get("inputs", {}).keys())

        input_sockets: list[dict[str, Any]] = []
        widgets_values: list[Any] = []

        for name in order:
            if name not in node.get("inputs", {}):
                # API 图常常省略「用默认值的控件」。不回填的话 widgets_values 会比控件少，
                # 再转回 UI 就变成长度不符的假错误 —— 这里用节点自己声明的 default 补齐。
                if info and info.is_widget(name):
                    input_sockets.append({"link": None, "name": name, "type": info.type_name(name)})
                    widgets_values.append(info.default_of(name))
                    if info.cfg_of(name).get("control_after_generate"):
                        widgets_values.append("fixed")
                continue
            value = node["inputs"][name]
            if _is_link_ref(value):
                link_id += 0
                input_sockets.append({"link": link_id, "name": name, "type": "NODE"})
                links.append([link_id, as_id(value[0]), value[1], node_id, len(input_sockets) - 1, "NODE"])
                link_id += 1
            else:
                input_sockets.append({"link": None, "name": name, "type": info.type_name(name) if info else "STRING"})
                widgets_values.append(value)
                if info and info.cfg_of(name).get("control_after_generate") and isinstance(value, (int, float)) and not isinstance(value, bool):
                    widgets_values.append("fixed")  # 我们钉住 seed，所以默认给 fixed 而不是 randomize

        out_names = (info.output_name if info and info.output_name else ["输出"]) or ["输出"]
        out_types = (info.output if info else []) or []
        outputs = [
            {"name": out_names[i] if i < len(out_names) else f"输出 {i}", "type": out_types[i] if i < len(out_types) else "*", "links": []}
            for i in range(max(len(out_names), len(out_types), 1))
        ]

        nodes.append(
            {
                "id": node_id,
                "type": class_type,
                "pos": [_layer_x(key, api_graph, as_id) * 340, (node_id % 7) * 170],
                "size": [270, 30 + 26 * (len(input_sockets) + len(outputs))],
                "flags": {},
                "order": len(nodes),
                "mode": 0,
                "inputs": input_sockets,
                "outputs": outputs,
                "properties": {"Node name for S&R": class_type},
                "widgets_values": widgets_values,
                "title": (node.get("_meta") or {}).get("title"),
            }
        )

    # 回填 outputs 上的 links 数组
    by_id = {n["id"]: n for n in nodes}
    for link in links:
        origin_id, origin_slot, target_id, target_slot = link[1], link[2], link[3], link[4]
        origin = by_id.get(origin_id)
        if origin and origin_slot < len(origin["outputs"]):
            origin["outputs"][origin_slot]["links"].append(link[0])
        target = by_id.get(target_id)
        if target and target_slot < len(target["inputs"]):
            target["inputs"][target_slot]["link"] = link[0]

    return {
        "last_node_id": max((n["id"] for n in nodes), default=0),
        "last_link_id": link_id - 1,
        "nodes": nodes,
        "links": links,
        "groups": [],
        "config": {},
        "extra": {},
        "version": 0.4,
    }


def _is_link_ref(value: Any) -> bool:
    return isinstance(value, list) and len(value) == 2 and isinstance(value[0], str) and isinstance(value[1], int)



# ───────── slot 抽取 ─────────


@dataclass
class Slot:
    address: str  # "<node_id>.<input_name>"
    name: str
    type: str
    default: Any = None
    widget: bool = True
    required: bool = False
    min: float | None = None
    max: float | None = None
    step: float | None = None
    options: list[str] | None = None
    rh_field_name: str | None = None  # RunningHub nodeInfoList 用的 fieldName


def extract_slots(api_graph: dict[str, Any], object_info: dict[str, Any]) -> list[Slot]:
    """扫出所有可被外部驱动的入参。地址语法与 comfy-mcp 保持一致，便于生态互操作。"""

    index = build_index(object_info)
    slots: list[Slot] = []
    for node_id, node in api_graph.items():
        info = index.get(node["class_type"])
        if info is None:
            continue
        for name, value in node.get("inputs", {}).items():
            if _is_link_ref(value):
                continue  # 连线不是可填槽位
            got = info.spec_of(name)
            widget_cfg = {}
            if isinstance(got, dict) and isinstance(got.get("spec"), (list, tuple)) and len(got["spec"]) > 1:
                if isinstance(got["spec"][1], dict):
                    widget_cfg = got["spec"][1]
            kind = info.type_name(name) if got else _py_type(value)
            slots.append(
                Slot(
                    address=f"{node_id}.{name}",
                    name=_pretty(node, name),
                    type=_map_type(kind),
                    default=value,
                    widget=True,
                    required=bool(got and got.get("required")),
                    min=widget_cfg.get("min"),
                    max=widget_cfg.get("max"),
                    step=widget_cfg.get("step"),
                    options=info.combo_options(name),
                    rh_field_name=name,
                )
            )
    return slots


def apply_slots(api_graph: dict[str, Any], values: dict[str, Any]) -> dict[str, Any]:
    """按 slot 地址写值。地址不存在就报错，避免「改了个不存在的槽位，跑出来一模一样」。"""

    out = {k: {"class_type": v["class_type"], "inputs": dict(v.get("inputs", {})), **({"_meta": v["_meta"]} if "_meta" in v else {})} for k, v in api_graph.items()}
    for address, value in values.items():
        if "." not in address:
            raise ConversionError(f"槽位地址不合法：{address}（应为 <node_id>.<input_name>）")
        node_id, name = address.split(".", 1)
        if node_id not in out:
            raise ConversionError(f"槽位 {address} 指向不存在的节点 {node_id}")
        if name not in out[node_id]["inputs"]:
            raise ConversionError(f"节点 {node_id} 没有输入 {name}")
        out[node_id]["inputs"][name] = value
    return out


def to_node_overrides(values: dict[str, Any]) -> list[dict[str, Any]]:
    """投影成 RunningHub 的 nodeInfoList。

    ⚠️ fieldValue 必须保持线格式；连线值（["7",0]）不许出现在这里；
    ⚠️ seed 必须显式注入 —— RunningHub 会强制重置 seed。
    """

    overrides: list[dict[str, Any]] = []
    for address, value in values.items():
        if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str) and isinstance(value[1], int):
            raise ConversionError(f"槽位 {address} 的值是连线，不该被外部驱动")
        node_id, name = address.split(".", 1)
        overrides.append({"nodeId": node_id, "fieldName": name, "fieldValue": value})
    return overrides


# ───────── 辅助 ─────────


def _title(node: dict[str, Any], class_type: str) -> str:
    return node.get("title") or class_type


def _pretty(node: dict[str, Any], name: str) -> str:
    return f"{_title(node, '')} · {name}".strip(" ·")


def _looks_like_uuid(value: str) -> bool:
    return len(value) == 36 and value.count("-") == 4


def _layer_x(node_key: str, graph: dict[str, Any], id_of=None) -> int:
    """最长路径分层，给 UI 布局一个可用的横坐标。"""

    seen: set[str] = set()

    def walk(key: str, d: int) -> int:
        if key in seen:
            return d
        seen.add(key)
        best = d
        for value in (graph.get(key) or {}).get("inputs", {}).values():
            if _is_link_ref(value):
                best = max(best, walk(value[0], d + 1))
        return best

    depth = walk(node_key, 0)
    seen.clear()
    return depth


def _proxy_widget_values(node: dict[str, Any]) -> dict[str, Any]:
    props = (node.get("properties") or {})
    out: dict[str, Any] = {}
    for pw in props.get("proxyWidgets") or []:
        # 形如 ["3", "value"] —— 控件被搬到别处去了，值仍留在源节点的 widgets_values 里
        if isinstance(pw, list) and len(pw) >= 2:
            out[str(pw[1])] = {"origin_id": str(pw[0])}
    return out


def _py_type(value: Any) -> str:
    if isinstance(value, bool):
        return "BOOLEAN"
    if isinstance(value, int):
        return "INT"
    if isinstance(value, float):
        return "FLOAT"
    return "STRING"


def _map_type(kind: str) -> str:
    kind = (kind or "").upper()
    if kind in ("INT", "COMBO", "BOOLEAN", "FLOAT", "STRING", "IMAGE", "VIDEO", "AUDIO"):
        return {"INT": "int", "FLOAT": "float", "BOOLEAN": "bool", "COMBO": "combo", "STRING": "string"}.get(kind, kind.lower())
    return "string"
