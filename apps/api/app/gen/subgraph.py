"""子图扁平化：把 definitions.subgraphs 里的实例节点摊平成普通 UI 节点。

为什么要单独做这一步：ComfyUI 官方工作流模板现在基本都是子图形态 —— 外层只有一个
`type` 为 UUID 的实例节点，真正的 UNETLoader / KSampler / VAEDecode 全藏在
`definitions.subgraphs[]` 里。不展开的话，导入向导对任何官方模板都会直接失败。

这个模块只做**结构变换**，不解释节点语义（那是 workflow.py 的活），产出物仍然是一个
合法的 UI 图，因此可以复用已经过测试的 ui→api 转换器，也让这里的行为容易单独验证。
"""

from __future__ import annotations

from typing import Any

from .workflow import ConversionError, build_index, normalize_link


def flatten_subgraphs(ui_graph: dict[str, Any], object_info: dict[str, Any] | None = None) -> dict[str, Any]:
    """返回一个不含子图的 UI 图。没有子图时原样返回（同一对象的浅拷贝）。"""

    defs = ui_graph.get("definitions") or {}
    sub_defs = defs.get("subgraphs") or []
    if not sub_defs:
        return dict(ui_graph)

    by_id = {str(sg.get("id")): sg for sg in sub_defs if isinstance(sg, dict) and sg.get("id")}
    nodes = list(ui_graph.get("nodes") or [])
    links = [list(x) for x in (ui_graph.get("links") or [])]

    instance_nodes = [n for n in nodes if str(n.get("type")) in by_id]
    if not instance_nodes:
        # 定义了子图但没被任何节点引用（未使用的定义），可以直接丢掉
        out = dict(ui_graph)
        out["definitions"] = {k: v for k, v in defs.items() if k != "subgraphs"}
        return out

    max_id = max([int(n["id"]) for n in nodes if str(n.get("id", "")).lstrip("-").isdigit()] or [0], default=0)
    next_node_id = max(max_id, 0) + 1
    max_link_id = max([int(l[0]) for l in links if _is_numbered_link(l)] or [0], default=0)
    # 内部连线会分配新 id。子图自己的 link 编号和外层是重叠的（实测外层有 650/672，
    # 子图内有 656-668），所以新 id 必须整体高于「外层 + 所有子图」已用过的最大号。
    all_inner_link_ids = [
        int(n[0])
        for sg_def in by_id.values()
        for n in (normalize_link(l) for l in (sg_def.get("links") or []))
        if n is not None and str(n[0]).isdigit()
    ]
    next_link_id = max([max_link_id, *(all_inner_link_ids or [0])], default=0) + 1

    new_nodes: list[dict[str, Any]] = [n for n in nodes if str(n.get("type")) not in by_id]
    collected_overrides: dict[str, dict[str, Any]] = {}
    instance_ids = {str(n["id"]) for n in instance_nodes}
    # 与实例节点相连的旧线要在 _flatten_one 里重接到内部落点，这里先剔除
    outer_links = [list(n) for n in (normalize_link(l) for l in links) if n is not None]
    kept_links: list[list[Any]] = [l for l in outer_links if str(l[3]) not in instance_ids and str(l[1]) not in instance_ids]

    for instance in instance_nodes:
        sg = by_id[str(instance["type"])]
        inner_new_nodes, rebuilt_links, overrides, next_node_id, next_link_id = _flatten_one(
            instance=instance,
            sg=sg,
            links=links,
            start_node_id=next_node_id,
            start_link_id=next_link_id,
        )
        new_nodes.extend(inner_new_nodes)
        kept_links.extend(rebuilt_links)
        for key, patch in overrides.items():
            collected_overrides.setdefault(key, {}).update(patch)

    out = dict(ui_graph)
    out["nodes"] = new_nodes
    out["links"] = kept_links
    out["last_node_id"] = max([int(n["id"]) for n in new_nodes] or [0])
    out["last_link_id"] = max([int(l[0]) for l in kept_links] or [0]) if kept_links else 0
    out["definitions"] = {k: v for k, v in defs.items() if k != "subgraphs"}
    out["_flattened_subgraphs"] = [str(i["type"]) for i in instance_nodes]
    # 子图实例上那些「表面上属于实例、实际属于内部某节点某个输入」的值，
    # 用覆盖表显式带出去，由 ui_to_api 在生成 API 图之后按输入名精确写入。
    out["_subgraph_overrides"] = collected_overrides
    return out


def _flatten_one(
    *,
    instance: dict[str, Any],
    sg: dict[str, Any],
    links: list[list[Any]],  # 已归一化
    start_node_id: int,
    start_link_id: int,
) -> tuple[list[dict[str, Any]], list[list[Any]], dict[str, dict[str, Any]], int, int]:
    instance_id = str(instance["id"])
    inner_nodes = list(sg.get("nodes") or [])
    # 子图内部 links 是 dict（{id, origin_id, origin_slot, target_id, target_slot, type}），
    # 外层 links 是数组。归一化成统一的元组形状，下面的索引逻辑才只有一种写法。
    inner_links = [n for n in (normalize_link(x) for x in (sg.get("links") or [])) if n is not None]
    input_node_id = str((sg.get("inputNode") or {}).get("id"))
    output_node_id = str((sg.get("outputNode") or {}).get("id"))

    # ① 给内部节点分配全局唯一 id
    id_map: dict[str, int] = {}
    cursor = start_node_id
    for n in inner_nodes:
        id_map[str(n["id"])] = cursor
        cursor += 1

    # ② 子图暴露的入参 → 内部落点 (inner_id, socket_index)
    inner_by_id = {str(n["id"]): n for n in inner_nodes}
    forward: list[tuple[int, int] | None] = []
    for spec in sg.get("inputs") or []:
        target: tuple[int, int, str] | None = None
        for lid in spec.get("linkIds") or []:
            link = _find_link(inner_links, lid)
            if not link:
                continue
            if str(link[1]) != input_node_id:
                continue
            target_inner = str(link[3])
            if target_inner not in id_map:
                continue
            inner = inner_by_id.get(target_inner)
            sockets = (inner or {}).get("inputs") or []
            slot_index = int(link[4])
            if slot_index >= len(sockets) or not sockets[slot_index].get("name"):
                continue
            # 三元组：新节点 id、槽位序号、输入名。
            # 有了输入名，实例上的控件值就能按名字精确覆盖，不需要猜 widgets_values 的位置。
            target = (id_map[target_inner], slot_index, str(sockets[slot_index]["name"]))
        forward.append(target)

    # ③ 子图暴露的出参 → 内部来源 (inner_id, slot_index)
    out_source: dict[int, tuple[int, int]] = {}
    for out_index, spec in enumerate(sg.get("outputs") or []):
        for lid in spec.get("linkIds") or []:
            link = _find_link(inner_links, lid)
            if not link:
                continue
            if str(link[3]) != output_node_id:
                continue
            origin_inner = str(link[1])
            if origin_inner in id_map:
                out_source[out_index] = (id_map[origin_inner], int(link[2]))

    # ④ 内部连线重映射；连着 input/output 伪节点的连线后面单独处理，这里剔除
    link_id_map: dict[Any, int] = {}
    link_cursor = start_link_id
    rebuilt_links: list[list[Any]] = []
    for link in inner_links:
        origin, target = str(link[1]), str(link[3])
        if origin not in id_map and target not in id_map:
            continue
        new_id = link_cursor
        link_cursor += 1
        link_id_map[link[0]] = new_id
        rebuilt_links.append(
            [
                new_id,
                id_map.get(origin, link[1]),
                int(link[2]),
                id_map.get(target, link[3]),
                int(link[4]),
                link[5] if len(link) > 5 else "NODE",
            ]
        )

    # ⑤ 复制内部节点，重写 id 与其 socket 上记录的 link id
    new_nodes: list[dict[str, Any]] = []
    for n in inner_nodes:
        clone = dict(n)
        clone["id"] = id_map[str(n["id"])]
        clone["inputs"] = [
            {**dict(s), "link": link_id_map.get(s.get("link"))} if s.get("link") in link_id_map else dict(s)
            for s in (n.get("inputs") or [])
        ]
        # 来自 inputNode 的输入槽此刻 link 为空，第 ⑥/⑦ 步会填上外层连线；
        # 但内部没有对应 link 的槽位要清掉旧值，避免脏引用
        for socket in clone["inputs"]:
            if socket.get("link") is not None and socket["link"] not in link_id_map.values():
                socket["link"] = None
        clone["outputs"] = [
            {**dict(s), "links": [link_id_map[l] for l in (s.get("links") or []) if l in link_id_map]}
            for s in (n.get("outputs") or [])
        ]
        new_nodes.append(clone)

    by_new_id = {nn["id"]: nn for nn in new_nodes}

    # ⑥ 外层连进实例输入槽的线 → 直接接到内部落点上
    instance_sockets = instance.get("inputs") or []
    instance_name_to_index = {str(s.get("name")): i for i, s in enumerate(instance_sockets)}
    sg_input_names = [str(spec.get("name")) for spec in (sg.get("inputs") or [])]

    for outer_link in links:
        if str(outer_link[3]) != instance_id:
            continue
        socket_index = int(outer_link[4])
        # 优先按 socket 名对齐子图声明的入参，名字对不上再按位置
        name = None
        if socket_index < len(instance_sockets):
            name = str(instance_sockets[socket_index].get("name"))
        sg_index: int | None = None
        if name is not None and name in sg_input_names:
            sg_index = sg_input_names.index(name)
        elif socket_index < len(sg_input_names):
            sg_index = socket_index
        if sg_index is None or sg_index >= len(forward) or forward[sg_index] is None:
            continue
        target_id, target_slot, _target_name = forward[sg_index]
        # 保留外层原本的 link id：外层节点的 inputs[].link / outputs[].links 里存的就是它，
        # 换新 id 会让那些引用变成悬空指针（表现为「引用了不存在的 link」）。
        rebuilt_links.append(
            [outer_link[0], outer_link[1], int(outer_link[2]), target_id, target_slot, outer_link[5] if len(outer_link) > 5 else "NODE"]
        )
        node = by_new_id.get(target_id)
        if node and target_slot < len(node["inputs"]):
            node["inputs"][target_slot]["link"] = outer_link[0]

    # ⑦ 外层从实例输出槽引出的线 → 起点改成内部来源节点
    for outer_link in links:
        if str(outer_link[1]) != instance_id:
            continue
        slot = int(outer_link[2])
        if slot not in out_source:
            continue
        origin_id, origin_slot = out_source[slot]
        rebuilt_links.append(
            [outer_link[0], origin_id, origin_slot, outer_link[3], int(outer_link[4]), outer_link[5] if len(outer_link) > 5 else "NODE"]
        )
        node = by_new_id.get(origin_id)
        if node and origin_slot < len(node["outputs"]):
            node["outputs"][origin_slot].setdefault("links", []).append(outer_link[0])

    # ⑧ 实例节点上的 widget 值 → 按「输入名」精确覆盖到内部节点，绝不猜位置。
    # 覆盖表交给转换器在生成 API 图之后统一应用，比改写 widgets_values 可靠得多。
    overrides: dict[str, dict[str, Any]] = {}
    widget_inputs = [i for i, spec in enumerate(sg.get("inputs") or []) if _looks_like_widget(str(spec.get("type", "")))]
    widget_values = instance.get("widgets_values") or []
    if len(widget_values) == len(widget_inputs):
        for position, sg_index in enumerate(widget_inputs):
            if sg_index >= len(forward) or forward[sg_index] is None:
                continue
            value = widget_values[position]
            if value is None:
                continue
            target_id, _slot, input_name = forward[sg_index]
            overrides.setdefault(str(target_id), {})[input_name] = value

    return new_nodes, rebuilt_links, overrides, cursor, link_cursor


def _place_widget_value(node: dict[str, Any], input_name: str, value: Any, sg: dict[str, Any], position: int) -> None:
    """把子图实例上的一行值写进内部节点的 widgets_values 对应位置。

    内部节点自己的 widgets_values 是权威顺序，这里按输入名在其控件序列里定位；
    定位不到就跳过（保留内部默认值），绝不瞎猜位置。
    """
    widgets = node.setdefault("widgets_values", [])
    order = _widget_names(node)
    if input_name in order:
        index = order.index(input_name)
        while len(widgets) <= index:
            widgets.append(None)
        widgets[index] = value
    elif len(widgets) == 1:
        # 只有一个控件的节点（如只暴露 prompt 的文本节点）位置歧义最小
        widgets[0] = value


def _widget_names(node: dict[str, Any]) -> list[str]:
    """从节点声明的 socket 名里挑出控件位。

    UI 格式没有 object_info 时无法百分百区分控件与连线槽，这里用「inputs 里 link 为空的项」
    作为控件顺序的近似，并保留原有 widgets_values 长度作为边界。
    """
    return [str(s.get("name")) for s in (node.get("inputs") or []) if s.get("link") is None]


def _looks_like_widget(type_name: str) -> bool:
    from .workflow import _STATIC_LINK_TYPES

    return type_name.upper() not in _STATIC_LINK_TYPES


def _is_numbered_link(link: Any) -> bool:
    """link 条目通常是 [id, origin, oSlot, target, tSlot, type]，但新版/损坏文件可能是
    dict 或长度不足。这里只认「首元素是整数」的合法形状，其余一律跳过而不是抛异常。"""
    return isinstance(link, (list, tuple)) and len(link) >= 5 and str(link[0]).isdigit()


def _find_link(links: list[list[Any]], link_id: Any) -> list[Any] | None:
    for link in links:
        if link and link[0] == link_id:
            return link
    return None


def list_subgraph_names(ui_graph: dict[str, Any]) -> list[str]:
    defs = ui_graph.get("definitions") or {}
    return [str(sg.get("name") or sg.get("id")) for sg in (defs.get("subgraphs") or []) if isinstance(sg, dict)]
