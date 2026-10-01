"""UI↔API 转换与 slot 抽取。

分两类：
  - 纯逻辑用例：用手写 object_info，覆盖每个已知坑（不依赖实例）
  - 往返用例（live）：拿真实实例的 object_info，把内置 H3 图 api→ui→api 转回来必须一致
"""

from __future__ import annotations

import pytest

from app.gen.workflow import (
    ConversionError,
    api_to_ui,
    apply_slots,
    build_index,
    extract_slots,
    to_node_overrides,
    ui_to_api,
)


def hidden_widget_inputs(api, node_id, index) -> set[str]:
    """允许在 UI 往返中丢失的输入：被声明为 hidden/socketless 的控件。

    SaveVideo.codec 就是这种（COMFY_DYNAMICCOMBO_V3 + hidden:true）——
    它是合法 API 输入，但 UI 导出里没有任何位置承载它，所以往返必丢。
    这是格式限制，不是转换器缺陷；前提是转换器必须为此告警。
    """
    info = index.get(api[node_id]["class_type"])
    if info is None:
        return set()
    return set(info.hidden_widget_names()) & set(api[node_id]["inputs"])

# ───────── 手写 object_info 夹具 ─────────
# 形状取自本机真实实例 /object_info：
#   连线输入   -> ["MODEL", {...}]     首元素是节点数据类型名
#   基础控件   -> ["INT", {...}]        首元素是基础类型名
#   下拉控件   -> ["COMBO", {"options": [...]}]  或旧式 [["a","b"], {...}]

OBJ = {
    "KSampler": {
        "input": {
            "required": {
                "model": ["MODEL", {"tooltip": "the model"}],
                "seed": ["INT", {"default": 0, "min": 0, "max": 18446744073709551615, "control_after_generate": True}],
                "steps": ["INT", {"default": 20, "min": 1, "max": 10000}],
                "sampler_name": ["COMBO", {"options": ["euler", "res_multistep", "ipndm_v"]}],
            },
            "optional": {"denoise": ["FLOAT", {"default": 1.0, "min": 0.0, "max": 100.0, "step": 0.1}]},
        },
        "input_order": {"all": ["model", "seed", "steps", "sampler_name", "denoise"]},
        "output": ["LATENT"],
        "output_name": ["LATENT"],
    },
    "EmptyLatentImage": {
        "input": {
            "required": {
                "width": ["INT", {"default": 512, "min": 16, "max": 8192}],
                "height": ["INT", {"default": 512, "min": 16, "max": 8192}],
                "batch_size": ["INT", {"default": 1, "min": 1, "max": 4096}],
            }
        },
        "input_order": {"all": ["width", "height", "batch_size"]},
        "output": ["LATENT"],
        "output_name": ["LATENT"],
    },
    "SaveImage": {
        "input": {
            "required": {
                "images": ["IMAGE", {"tooltip": "images to save"}],
                "filename_prefix": ["STRING", {"default": "ComfyUI"}],
            }
        },
        "input_order": {"all": ["images", "filename_prefix"]},
        "output": [],
        "output_name": [],
    },
    # autogrow 组：object_info 里只出现组名 ref_images，UI 图里的却是点号子名
    "SomeRef": {
        "input": {
            "required": {"clip": ["CLIP", {}], "ref_image_size": ["COMBO", {"options": ["match", "max"]}]},
            "optional": {"ref_images": ["IMAGE", {}]},
        },
        "input_order": {"required": ["clip", "ref_image_size"], "optional": ["ref_images"]},
        "output": ["CONDITIONING", "LATENT"],
        "output_name": ["positive", "latent"],
    },
}


def ui(nodes, links):
    return {"nodes": nodes, "links": links, "groups": [], "version": 0.4}


def test_ui_to_api_resolves_links_and_widgets_in_order():
    graph = ui_to_api(
        ui(
            [
                {"id": 1, "type": "EmptyLatentImage", "widgets_values": [1024, 512, 1], "inputs": [], "outputs": [{"name": "LATENT", "links": [10]}]},
                {
                    "id": 2,
                    "type": "KSampler",
                    "widgets_values": [42, 25, "res_multistep", 1.0],
                    "inputs": [{"name": "model", "link": None, "type": "MODEL"}, {"name": "seed", "link": None}, {"name": "denoise", "link": None}],
                    "outputs": [{"name": "LATENT", "links": [11]}],
                },
                {"id": 3, "type": "SaveImage", "widgets_values": ["out/"], "inputs": [{"name": "images", "link": 11}], "outputs": []},
            ],
            [[11, 2, 0, 3, 0, "LATENT"]],
        ),
        OBJ,
    )
    assert graph["1"]["inputs"] == {"width": 1024, "height": 512, "batch_size": 1}
    assert graph["2"]["inputs"]["seed"] == 42
    assert graph["2"]["inputs"]["steps"] == 25
    assert graph["2"]["inputs"]["sampler_name"] == "res_multistep"
    assert graph["2"]["inputs"]["denoise"] == 1.0
    assert graph["3"]["inputs"]["images"] == ["2", 0]


def test_ui_to_api_rejects_unknown_class_type():
    with pytest.raises(ConversionError) as exc:
        ui_to_api(ui([{"id": 7, "type": "NotInstalled", "widgets_values": [], "inputs": [], "outputs": []}], []), OBJ)
    assert exc.value.class_type == "NotInstalled"
    assert exc.value.node_id == "7"


def test_ui_to_api_rejects_uuid_subgraph_node_instead_of_guessing():
    uuid_type = "3f2a9b1c-4d5e-6f70-8192-a3b4c5d6e7f8"
    with pytest.raises(ConversionError) as exc:
        ui_to_api(ui([{"id": 5, "type": uuid_type, "widgets_values": [], "inputs": [], "outputs": []}], []), OBJ)
    assert "子图" in str(exc.value)


def test_ui_to_api_rejects_proxy_widgets():
    node = {
        "id": 9,
        "type": "KSampler",
        "widgets_values": [1],
        "inputs": [{"name": "model", "link": None}],
        "outputs": [],
        "properties": {"proxyWidgets": [["3", "seed"]]},
    }
    with pytest.raises(ConversionError) as exc:
        ui_to_api(ui([node], []), OBJ)
    assert "proxyWidgets" in str(exc.value)


def test_ui_to_api_mode_bypass_passes_first_input_through():
    graph = ui_to_api(
        ui(
            [
                {"id": 1, "type": "EmptyLatentImage", "widgets_values": [8, 8, 1], "inputs": [], "outputs": [{"links": [1]}]},
                {"id": 2, "type": "KSampler", "mode": 4, "widgets_values": [], "inputs": [{"name": "model", "link": 1}], "outputs": [{"links": [2]}]},
                {"id": 3, "type": "SaveImage", "widgets_values": ["x"], "inputs": [{"name": "images", "link": 2}], "outputs": []},
            ],
            [[1, 1, 0, 2, 0, "LATENT"], [2, 2, 0, 3, 0, "LATENT"]],
        ),
        OBJ,
    )
    # bypass 节点不该再带自己的 widget 值
    assert graph["2"]["inputs"]["model"] == ["1", 0]


def test_ui_to_api_errors_on_dangling_link():
    with pytest.raises(ConversionError):
        ui_to_api(
            ui([{"id": 2, "type": "KSampler", "widgets_values": [1, 1, "euler"], "inputs": [{"name": "model", "link": 99}], "outputs": []}], []),
            OBJ,
        )


def test_ui_to_api_errors_on_widgets_values_length_mismatch():
    """值数量比控件少 = 导出时的节点定义和当前实例不一致，绝不能猜。"""
    with pytest.raises(ConversionError) as exc:
        ui_to_api(
            ui([{"id": 2, "type": "KSampler", "widgets_values": [1], "inputs": [{"name": "model", "link": None}], "outputs": []}], []),
            OBJ,
        )
    assert "widgets_values" in str(exc.value)


def test_ui_to_api_maps_dotted_autogrow_names_back_to_source_nodes():
    """UI 图里是 ref_images.ref_image_0，object_info 里只有组名 ref_images。"""
    graph = ui_to_api(
        ui(
            [
                {"id": 7, "type": "EmptyLatentImage", "widgets_values": [64, 64, 1], "inputs": [], "outputs": [{"name": "LATENT", "links": [1]}]},
                {
                    "id": 4,
                    "type": "SomeRef",
                    "widgets_values": ["match"],
                    "inputs": [{"name": "clip", "link": None}, {"name": "ref_images.ref_image_0", "link": 1}],
                    "outputs": [],
                },
            ],
            [[1, 7, 0, 4, 1, "IMAGE"]],
        ),
        OBJ,
    )
    assert graph["4"]["inputs"]["ref_images.ref_image_0"] == ["7", 0]
    assert graph["4"]["inputs"]["ref_image_size"] == "match"


def test_api_to_ui_uses_integer_ids_on_both_sides():
    api = {
        "1": {"class_type": "EmptyLatentImage", "inputs": {"width": 8, "height": 8, "batch_size": 1}},
        "2": {"class_type": "KSampler", "inputs": {"model": ["1", 0], "seed": 5, "steps": 10, "sampler_name": "euler"}},
    }
    out = api_to_ui(api, OBJ)
    ids = {n["id"] for n in out["nodes"]}
    assert all(isinstance(i, int) for i in ids), ids
    for link in out["links"]:
        assert isinstance(link[1], int) and isinstance(link[3], int), link
    # 目标节点的 socket 上必须回填了 link id
    sampler = next(n for n in out["nodes"] if n["type"] == "KSampler")
    assert any(s["link"] for s in sampler["inputs"])


def test_api_to_ui_accepts_non_numeric_keys():
    api = {"abc-def": {"class_type": "SaveImage", "inputs": {"images": ["1", 0], "filename_prefix": "x"}}}
    out = api_to_ui(api, OBJ)
    assert isinstance(out["nodes"][0]["id"], int)


def test_extract_slots_skips_links_and_reports_widget_bounds():
    api = {
        "2": {
            "class_type": "KSampler",
            "inputs": {"model": ["1", 0], "seed": 42, "steps": 25, "sampler_name": "euler", "denoise": 1.0},
        }
    }
    slots = {s.address: s for s in extract_slots(api, OBJ)}
    assert "2.model" not in slots, "连线不是可填槽位"
    assert slots["2.seed"].type == "int" and slots["2.seed"].max == 18446744073709551615
    assert slots["2.sampler_name"].options == ["euler", "res_multistep", "ipndm_v"]
    assert slots["2.denoise"].required is False


def test_apply_slots_rejects_unknown_address():
    api = {"2": {"class_type": "KSampler", "inputs": {"seed": 1}}}
    with pytest.raises(ConversionError):
        apply_slots(api, {"2.no_such_input": 5})
    with pytest.raises(ConversionError):
        apply_slots(api, {"99.seed": 5})
    assert apply_slots(api, {"2.seed": 77})["2"]["inputs"]["seed"] == 77


def test_to_node_overrides_refuses_link_values():
    """RunningHub 的 nodeInfoList 里塞连线会把图结构写坏。"""
    with pytest.raises(ConversionError):
        to_node_overrides({"2.model": ["1", 0]})
    assert to_node_overrides({"2.seed": 42}) == [{"nodeId": "2", "fieldName": "seed", "fieldValue": 42}]


# ───────── 往返：用真实实例的 object_info ─────────


@pytest.mark.live
async def test_round_trip_against_real_object_info():
    import uuid

    from app.gen.builtin_graphs import discover_h3_weights, h3_video_graph
    from app.gen.comfy_native import ComfyNativeClient

    client = ComfyNativeClient("http://127.0.0.1:8188")
    try:
        weights = await discover_h3_weights(client)
        if weights.missing:
            pytest.skip(f"实例缺 H3 权重：{weights.missing}")
        api = h3_video_graph(weights, prompt="测试", width=864, height=480, length=124, steps=8, seed=42, use_turbo=True)
        info = await client.object_info()

        ui_graph = api_to_ui(api, info)
        assert len(ui_graph["nodes"]) == len(api)
        warnings_out: list[str] = []
        back = ui_to_api(ui_graph, info, report=warnings_out)
        info_index = build_index(info)

        assert set(back) == set(api)
        for node_id, node in api.items():
            assert back[node_id]["class_type"] == node["class_type"], node_id
            for bag_name, original in node["inputs"].items():
                # 连线引用必须逐条原样还原
                if isinstance(original, list):
                    assert back[node_id]["inputs"].get(bag_name) == original, (node_id, bag_name, original)
                elif bag_name in hidden_widget_inputs(api, node_id, info_index):
                    # UI 格式装不下 hidden 控件的值，允许丢，但必须留下指名道姓的告警，
                    # 否则用户会以为 codec 设置生效了
                    assert any(bag_name in w for w in warnings_out), (node_id, bag_name, warnings_out)
                else:
                    # widget 值：原来写了的必须一致；原来省略的允许被回填成节点默认值
                    assert bag_name in back[node_id]["inputs"], (node_id, bag_name)
                    assert back[node_id]["inputs"][bag_name] == original, (node_id, bag_name, original, back[node_id]["inputs"][bag_name])

        # 反向：回填只增不减。hidden 控件除外 —— 它们的值在 UI 格式里根本没有槽位，
        # 上面已经断言过「丢了必须有指名道姓的告警」，这里不能再要求它们存活。
        for node_id, node in api.items():
            remaining = set(node["inputs"]) - hidden_widget_inputs(api, node_id, info_index)
            assert remaining <= set(back[node_id]["inputs"]), node_id
    finally:
        await client.close()


@pytest.mark.live
async def test_slot_extraction_on_real_h3_graph():
    from app.gen.builtin_graphs import discover_h3_weights, h3_video_graph
    from app.gen.comfy_native import ComfyNativeClient

    client = ComfyNativeClient("http://127.0.0.1:8188")
    try:
        weights = await discover_h3_weights(client)
        if weights.missing:
            pytest.skip("缺权重")
        api = h3_video_graph(weights, prompt="x", width=864, height=480, length=124, steps=25, seed=1)
        info = await client.object_info()
        slots = {s.address: s for s in extract_slots(api, info)}
        # 导演台要能直接驱动这四个
        for addr in ("131.prompt", "131.width", "131.height", "131.length", "129.noise_seed", "124.steps"):
            assert addr in slots, (addr, sorted(slots))
        assert slots["131.length"].type in ("int",)
        assert slots["124.steps"].default == 25
    finally:
        await client.close()
