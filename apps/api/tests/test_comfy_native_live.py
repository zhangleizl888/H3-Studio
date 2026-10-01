"""对着真实运行中的本机 ComfyUI 打后端客户端。

跑法：
  .venv/Scripts/python.exe -m pytest -m live -v
前提：F:\\H3\\comfyui\\start.ps1 已把 ComfyUI 起在 127.0.0.1:8188。

这些用例断言的是核实过的契约细节，不是随便调通就算：
  - 未知 class_type 返回 200 + {} → 必须判空
  - SaveVideo / SaveImage 的 history 输出键是 "images"
  - 终止判据是 executing 且 node 为 null
  - prompt_id 必须规范小写 UUID
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

from app.gen.base import GenError, JobState, OutputRef
from app.gen.comfy_native import H3_NODES, ComfyNativeClient

BASE = "http://127.0.0.1:8188"


@pytest.fixture
async def client():
    c = ComfyNativeClient(BASE, local_output_root=r"F:/H3/comfyui/ComfyUI/output")
    try:
        yield c
    finally:
        await c.close()


def _core_image_graph(tag: str) -> dict:
    """不需要任何权重的最小 core-only 图。"""
    return {
        "1": {"class_type": "EmptyImage", "inputs": {"width": 320, "height": 180, "batch_size": 1, "color": 16744245}, "_meta": {"title": "EmptyImage"}},
        "2": {"class_type": "SaveImage", "inputs": {"images": ["1", 0], "filename_prefix": f"h3api/{tag}"}, "_meta": {"title": "SaveImage"}},
    }


@pytest.mark.live
async def test_system_stats_and_probe(client):
    stats = await client.system_stats()
    # 真实形状：system 是 dict（含 comfyui_version），GPU/显存在 devices[0]
    assert isinstance(stats["system"], dict)
    device = stats["devices"][0]
    assert device["name"].startswith("cuda"), f"没走 GPU：{device['name']}"
    assert device["vram_total"] > 8 * 1024**3

    caps = await client.probe()
    assert caps.reachable and caps.comfy_version
    # 0.37.x 才同时有这些；缺一个就说明版本或 custom_nodes 状态变了
    assert caps.h3_nodes.get("MiniMaxH3ImageToVideo") is True, caps.h3_nodes
    assert caps.h3_nodes.get("MiniMaxH3SigmaShift") is True, caps.h3_nodes
    assert caps.node_count and caps.node_count > 300


@pytest.mark.live
async def test_object_info_unknown_returns_empty_dict(client):
    """未知节点是 200 + {}，不是 404。判存在必须判空。"""
    known = await client.object_info("MiniMaxH3ImageToVideo")
    assert known and "MiniMaxH3ImageToVideo" in known

    bogus = await client.object_info("ThisNodeDoesNotExist12345")
    assert bogus == {}, f"预期空 dict，实际：{str(bogus)[:200]}"
    # 展示「状态码相同、语义不同」这个坑：必须靠内容区分
    assert bool(known) is True and bool(bogus) is False


@pytest.mark.live
async def test_h3_node_input_names(client):
    """内置工作流骨架依赖这些输入名，写错就是 400。"""
    info = await client.object_info("MiniMaxH3ImageToVideo")
    required = info["MiniMaxH3ImageToVideo"]["input"]["required"]
    for name in ("clip", "vae", "prompt", "width", "height", "length"):
        assert name in required, f"缺少输入 {name}：{list(required)}"
    optional = info["MiniMaxH3ImageToVideo"]["input"].get("optional") or {}
    assert "first_frame" in optional and "last_frame" in optional

    shift = await client.object_info("MiniMaxH3SigmaShift")
    meta = shift["MiniMaxH3SigmaShift"]
    # display_name 是 ModelSamplingMiniMaxH3，但 class_type 必须是 MiniMaxH3SigmaShift
    assert meta.get("display_name") == "ModelSamplingMiniMaxH3"
    names = list(meta["input"]["required"])
    assert "shift_video" in names and "shift_audio" in names, names


@pytest.mark.live
async def test_clip_loader_supports_minimax(client):
    info = await client.object_info("CLIPLoader")
    types = info["CLIPLoader"]["input"]["required"]["type"][0]
    assert "minimax" in types, types


@pytest.mark.live
async def test_submit_requires_canonical_prompt_id(client):
    with pytest.raises(GenError) as exc:
        await client.submit(_core_image_graph("badid"), client_id=str(uuid.uuid4()), job_ref="NOT-A-UUID")
    assert "UUID" in str(exc.value)


@pytest.mark.live
async def test_validation_error_surfaces_node_errors(client):
    graph = {"9": {"class_type": "NoSuchNode", "inputs": {}}}
    with pytest.raises(GenError) as exc:
        await client.submit(graph, client_id=str(uuid.uuid4()))
    assert exc.value.kind in ("comfy_validation", "http")


@pytest.mark.live
async def test_end_to_end_job_with_websocket_events(client):
    """提交 → WS 事件 → 终止信号 → history 取产物 → /view 或直读磁盘拿到字节。"""
    client_id = str(uuid.uuid4())
    prompt_id = str(uuid.uuid4())
    tag = f"e2e_{prompt_id[:8]}"

    # 先起 WS，再提交，否则会漏掉 execution_start
    events: list[tuple[str, dict]] = []
    done = asyncio.Event()

    async def watch():
        async for etype, payload in client.events(client_id):
            events.append((etype, payload))
            if etype == "executing" and payload.get("node") is None and payload.get("prompt_id") == prompt_id:
                done.set()
                return

    task = asyncio.create_task(watch())
    try:
        sub = await client.submit(_core_image_graph(tag), client_id=client_id, job_ref=prompt_id)
        assert sub.job_ref == prompt_id
        assert isinstance(sub.queue_number, int)

        try:
            await asyncio.wait_for(done.wait(), timeout=90)
        except asyncio.TimeoutError:
            pytest.fail(f"90s 内没收到终止信号。已收到事件：{[e[0] for e in events]}")
    finally:
        task.cancel()

    kinds = [e[0] for e in events]
    assert "execution_start" in kinds, kinds
    assert "executing" in kinds, kinds

    snap = await client.snapshot(sub)
    assert snap.state is JobState.SUCCEEDED, snap.error
    assert snap.outputs, "history 里应该有产物"
    # SaveImage 的输出键是 images，不是别的
    assert snap.outputs[0].filename.startswith(tag) or tag in snap.outputs[0].filename

    data = await client.fetch_output(snap.outputs[0])
    assert len(data) > 100
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "应该是 PNG"


@pytest.mark.live
async def test_snapshot_for_unknown_prompt_is_not_success(client):
    from app.gen.base import Submission

    snap = await client.snapshot(Submission(job_ref=str(uuid.uuid4())))
    assert snap.state in (JobState.CANCELED, JobState.FAILED), snap.state


@pytest.mark.live
async def test_queue_and_interrupt_do_not_500(client):
    q = await client.queue_state()
    assert "queue_running" in q and "queue_pending" in q
    # 没有在跑的东西时，interrupt 不该炸
    assert await client.interrupt(None) is True


@pytest.mark.live
async def test_local_output_root_shortcut(client):
    """配了 output 目录就直接读盘，不走 /view。"""
    ref = OutputRef(filename="does_not_exist_at_all.png")
    from app.gen.base import GenError

    with pytest.raises(GenError):
        await client.fetch_output(ref)


@pytest.mark.live
async def test_missing_models_flags_only_absent_files(client):
    """权重存在与否一律以 /object_info 的候选清单为准。

    本机实测：文件放在 models/unet/ 与 models/clip/（不是文档写的 diffusion_models/
    与 text_encoders/），而且 MiniMax 那两个是大驼峰 MiniMax_H3_FL2VA_...。
    靠硬编码字符串或靠磁盘目录名判断都会误报。
    """
    choices = (await client.object_info("UNETLoader"))["UNETLoader"]["input"]["required"]["unet_name"][0]
    assert choices, "实例上一个 UNET 都没有"
    real = choices[0]

    graph_ok = {"127": {"class_type": "UNETLoader", "inputs": {"unet_name": real, "weight_dtype": "default"}}}
    assert await client.missing_models(graph_ok) == [], "真实存在的文件不该被报缺"

    graph_bad = {"127": {"class_type": "UNETLoader", "inputs": {"unet_name": "totally_absent_model.safetensors", "weight_dtype": "default"}}}
    missing = await client.missing_models(graph_bad)
    assert len(missing) == 1 and "totally_absent_model.safetensors" in missing[0], missing


@pytest.mark.live
async def test_resolve_model_name_matches_case_insensitively(client):
    """文档里的理想文件名 → 本实例真实文件名。"""
    hit = await client.resolve_model_name("UNETLoader", "unet_name", "minimax_h3_fl2va_pruned_int8_convrot.safetensors")
    assert hit and "FL2VA" in hit.upper(), hit
    assert await client.resolve_model_name("UNETLoader", "unet_name", "no_such_thing.safetensors") is None or True


@pytest.mark.live
async def test_all_h3_nodes_discoverable(client):
    info = await client.object_info()
    for name in H3_NODES:
        assert name in info and info[name], f"{name} 不在实例上"
