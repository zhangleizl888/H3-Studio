"""原生 ComfyUI 协议客户端。

同时服务三种部署：本机 :8188、cloudflared 隧道后的自建云端、RunningHub 的 /proxy/{key} 网关。
实现细节逐条对应 PLAN §5.1 的 9 条防御规则，那些坑都是核实过的，不是想当然。
"""

from __future__ import annotations

import asyncio
import json
import re
import struct
import uuid
from pathlib import Path
from typing import Any, AsyncIterator
from urllib.parse import urlencode, urlsplit, urlunsplit

import httpx
import websockets

from .. import weights_health
from ..logging_setup import get_logger, redact
from ..net import async_client
from .base import Capabilities, GenError, JobSnapshot, JobState, OutputRef, Progress, Submission

log = get_logger("gen.comfy")

# 已知 H3 核心节点；能力探针据此裁剪功能，而不是提交后靠 400 猜
H3_NODES = (
    "MiniMaxH3ImageToVideo",
    "MiniMaxH3SigmaShift",  # display_name 才是 ModelSamplingMiniMaxH3
    "MiniMaxH3ReferenceToVideo",
    "MiniMaxH3AddGuide",
    "EmptyMiniMaxH3LatentAV",
)

# /history 输出里代表产物的键。SaveVideo 也落在 "images" 下（不是 "video"）
_OUTPUT_KEYS = ("images", "gifs", "audio", "video")

# WS 二进制帧事件码（comfy.api.util.protocol）
_EV_PREVIEW_IMAGE = 1
_EV_UNENCODED_PREVIEW = 2
_EV_TEXT = 3
_EV_PREVIEW_WITH_METADATA = 4


def _ws_url(base_url: str) -> str:
    """从 base_url 推导 WS 地址，保留路径前缀 —— RunningHub 的 key 就在前缀里。"""
    p = urlsplit(base_url)
    scheme = "wss" if p.scheme in ("https", "wss") else "ws"
    path = p.path.rstrip("/") + "/ws"
    return urlunsplit((scheme, p.netloc, path, "", ""))


class ComfyNativeClient:
    """一个实例一个客户端实例。WS 按 clientId 单例复用，避免互相踢下线。"""

    protocol = "comfy_native"

    def __init__(
        self,
        base_url: str,
        *,
        api_key: str | None = None,
        timeout_s: float = 1800.0,
        ws_backoff_max_s: float = 30.0,
        local_output_root: Path | None = None,
    ) -> None:
        # api_key 走 header 的还是走路径的，由调用方给的 base_url 决定；这里不猜
        self.base_url = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._client = async_client(
            self.base_url,
            base_url=self.base_url,
            headers=self._headers,
            timeout=httpx.Timeout(timeout_s, connect=15.0, read=60.0),
            follow_redirects=True,
        )
        self._ws_backoff_max = ws_backoff_max_s
        self._local_output_root = Path(local_output_root) if local_output_root else None
        # 本机实例才做得成权重体检：ComfyUI 的 /models 只给文件名不给路径
        self._models_root = weights_health.models_root(self._local_output_root)
        # 同一 clientId 重连会踢掉旧连接，所以每个 clientId 只允许一条 socket
        self._sockets: dict[str, websockets.ClientConnection] = {}

    # ───────── HTTP ─────────

    async def object_info(self, class_type: str | None = None) -> dict[str, Any]:
        """未知 class_type 返回的是 200 + {}，不是 404 —— 校验必须判空。"""
        path = "/object_info" if class_type is None else f"/object_info/{class_type}"
        last: Exception | None = None
        for attempt in range(3):
            try:
                r = await self._client.get(path)
                r.raise_for_status()
                return r.json()
            except (httpx.ReadError, httpx.ReadTimeout, httpx.RemoteProtocolError) as exc:
                # 全量 object_info 体积大（本机约 30MB），实例忙时容易读断；退避重试
                last = exc
                await asyncio.sleep(1.5 * (attempt + 1))
        raise GenError(f"读取 /object_info 失败：{type(last).__name__}", kind="http", retryable=True)

    async def system_stats(self) -> dict[str, Any]:
        r = await self._client.get("/system_stats")
        r.raise_for_status()
        return r.json()

    async def free_vram_gb(self) -> float | None:
        """这台实例还剩多少显存可用（GiB）。取不到就返回 None，不拿它当「不够」。

        用 ComfyUI 自己报的 vram_free 而不是 nvidia-smi：云端实例（自建 / RH 代理）
        摸不到对方的进程表，而这个数字对三种部署都拿得到。
        """
        try:
            stats = await self.system_stats()
        except Exception:
            return None
        for dev in stats.get("devices") or []:
            free = dev.get("vram_free")
            if free:
                return round(float(free) / 1024**3, 2)
        return None

    async def vram_detail(self) -> dict[str, float] | None:
        """空闲 / 总额 / 本实例自己记在 torch 账上的显存（GiB），用来分辨「谁占着卡」。"""
        try:
            stats = await self.system_stats()
        except Exception:
            return None
        for dev in stats.get("devices") or []:
            if not dev.get("vram_free"):
                continue
            return {
                "free_gb": round(float(dev["vram_free"]) / 1024**3, 2),
                "total_gb": round(float(dev.get("vram_total") or 0) / 1024**3, 2),
                "pool_gb": round(float(dev.get("torch_vram_total") or 0) / 1024**3, 2),
            }
        return None

    async def queue_state(self) -> dict[str, Any]:
        r = await self._client.get("/queue")
        r.raise_for_status()
        return r.json()

    async def history(self, prompt_id: str | None = None) -> dict[str, Any]:
        path = "/history" if prompt_id is None else f"/history/{prompt_id}"
        r = await self._client.get(path)
        r.raise_for_status()
        return r.json()

    async def upload(self, path: Path, *, type: str = "input", subfolder: str = "") -> str:
        """注意：ComfyUI 没有 /upload/audio。音频、视频一律走 /upload/image。"""
        payload = Path(path).read_bytes()
        files = {"image": (Path(path).name, payload)}
        data = {"type": type, "subfolder": subfolder, "overwrite": "true"}
        r = await self._client.post("/upload/image", files=files, data=data)
        if r.status_code >= 400:
            raise GenError(f"上传失败 {r.status_code}: {redact(r.text[:300])}", kind="upload")
        body = r.json()
        name = body.get("name")
        if not name:
            raise GenError(f"上传响应里没有 name：{body}", kind="upload")
        return name

    async def submit(self, graph: dict[str, Any], *, client_id: str, job_ref: str | None = None) -> Submission:
        """提交。

        两条硬性要求：
          1. 必须带 client_id，且与 WS 的 clientId 一致 —— 否则服务端 client_id=None，
             除广播类事件外**一条执行事件都收不到**。
          2. prompt_id 若由我们预生成，必须是规范小写连字符 UUID，否则 400 invalid_prompt_id。
        """
        body: dict[str, Any] = {"prompt": graph, "client_id": client_id}
        if job_ref:
            if not _is_canonical_uuid(job_ref):
                raise GenError(f"prompt_id 必须是规范小写 UUID，收到：{job_ref}", kind="client_validation")
            body["prompt_id"] = job_ref

        r = await self._client.post("/prompt", json=body)
        if r.status_code >= 400:
            raise self._validation_error(r)
        data = r.json()
        errors = data.get("node_errors") or {}
        if errors:
            raise self._node_errors_error(errors)
        return Submission(
            job_ref=data["prompt_id"],
            client_id=client_id,
            queue_number=data.get("number"),
            raw=data,
        )

    def _node_errors_error(self, errors: dict[str, Any]) -> GenError:
        """把 ComfyUI 的 node_errors 摊成一句能照着改的话。

        「节点校验失败」这四个字对修图毫无帮助 —— 真正的信息在每条 error 的
        details/extra_info 里（例如 Required input is missing: images.image1）。
        """
        parts: list[str] = []
        first_node: str | None = None
        for node_id, node_err in list(errors.items())[:4]:
            if first_node is None:
                first_node = node_id
            items = node_err.get("errors") or [{}] if isinstance(node_err, dict) else [{}]
            for item in items[:3]:
                if not isinstance(item, dict):
                    continue
                extra = item.get("extra_info") or {}
                head = item.get("detail") or item.get("message") or "校验不通过"
                if extra.get("details"):
                    head = f"{head}｜{extra['details']}"
                label = f"节点 {node_id}"
                if extra.get("class_type"):
                    label += f"（{extra['class_type']}）"
                parts.append(f"{label}：{head}")
        return GenError(
            "；".join(parts) if parts else "节点校验失败",
            kind="comfy_validation",
            node_id=first_node,
        )

    def _validation_error(self, r: httpx.Response) -> GenError:
        try:
            body = r.json()
        except Exception:
            return GenError(f"POST /prompt 返回 {r.status_code}: {redact(r.text[:300])}", kind="http")
        err = body.get("error") or {}
        node_errors = body.get("node_errors") or {}
        node_id = next(iter(node_errors), None)
        # err.details 才是真正的可操作信息（例如「Required input is missing: bit_depth」），
        # 只回 message 的话用户看到的就是「Prompt outputs failed validation」这句废话。
        detail = err.get("message") or f"提交被拒绝（HTTP {r.status_code}）"
        if err.get("details"):
            detail = f"{detail}｜{err['details']}"
        if node_errors:
            first = next(iter(node_errors.values()), {})
            inner = (first.get("errors") or [{}])[0] if isinstance(first, dict) else {}
            if inner.get("details"):
                detail += f"｜{inner['details']}"
        return GenError(
            detail,
            kind="comfy_validation",
            node_id=node_id,
            node_type=err.get("node_type"),
            traceback_tail=err.get("details"),
        )

    async def view_bytes(self, ref: OutputRef, *, preview: str | None = None) -> bytes:
        params: dict[str, str] = {"filename": ref.filename, "subfolder": ref.subfolder, "type": ref.type}
        if preview:
            params["preview"] = preview  # 例如 "webp;80"，服务端降采样，别在前端解码原图
        r = await self._client.get("/view", params=params)
        if r.status_code >= 400:
            raise GenError(f"/view 取不到 {ref.filename}（HTTP {r.status_code}）", kind="http")
        return r.content

    async def fetch_output(self, ref: OutputRef) -> bytes:
        # 本地实例配了 output 目录就直接读盘，省一次网络往返
        if self._local_output_root and ref.type == "output":
            candidate = self._local_output_root / (ref.subfolder or "") / ref.filename
            if candidate.exists():
                return candidate.read_bytes()
        return await self.view_bytes(ref)

    async def interrupt(self, prompt_id: str | None = None) -> bool:
        """优先 /interrupt；不带 body 时是全局中断。"""
        r = await self._client.post("/interrupt", json={"prompt_id": prompt_id} if prompt_id else {})
        return r.status_code < 400

    async def free(self, *, unload_models: bool = True, free_memory: bool = True) -> bool:
        r = await self._client.post("/free", json={"unload_models": unload_models, "free_memory": free_memory})
        return r.status_code < 400

    # ───────── 状态 ─────────

    async def snapshot(self, sub: Submission) -> JobSnapshot:
        """不依赖 WS 的兜底路径。WS 断了、被踢了、或者客户端根本没连上都靠它。"""
        hist = await self.history(sub.job_ref)
        entry = hist.get(sub.job_ref)
        if entry is None:
            q = await self.queue_state()
            running = [x for x in q.get("queue_running", []) if _matches_prompt(x, sub.job_ref)]
            pending = [x for x in q.get("queue_pending", []) if _matches_prompt(x, sub.job_ref)]
            if running:
                return JobSnapshot(state=JobState.RUNNING, progress=Progress(stage="执行中"))
            if pending:
                return JobSnapshot(state=JobState.QUEUED, queue_position=len(pending))
            # 既不在历史也不在队列：可能被 /queue delete 掉，或实例重启过
            return JobSnapshot(state=JobState.CANCELED, error={"type": "lost", "message": "实例上查不到这个 prompt_id"})

        status = (entry.get("status") or {})
        if status.get("status_str") == "error" or status.get("completed") is False and status.get("status_str") == "error":
            msgs = status.get("messages") or []
            return JobSnapshot(state=JobState.FAILED, error=_extract_exec_error(msgs))

        outputs = _outputs_from_history(entry)
        if status.get("completed") or outputs:
            return JobSnapshot(state=JobState.SUCCEEDED, outputs=outputs)
        return JobSnapshot(state=JobState.RUNNING, progress=Progress(stage="执行中"))

    async def resolve(self, sub: Submission, *, timeout_s: float = 1800.0) -> JobSnapshot:
        """WS 事件为主，/history 兜底，直到终态或超时。"""
        deadline = asyncio.get_running_loop().time() + timeout_s
        async for event in self.events(sub.client_id or ""):
            etype, payload = event
            if etype == "execution_error":
                return JobSnapshot(state=JobState.FAILED, error=payload)
            if etype == "executing" and payload.get("prompt_id") == sub.job_ref and payload.get("node") is None:
                # 终止信号到了 ≠ history 里已经有产物：ComfyUI 是先发事件、后写 history。
                # 这时候空手返回就成了「成功但零产物」，所以重读几次；仍然读不到就是失败，不能报成功。
                return await self._settle_after_signal(sub)
            if asyncio.get_running_loop().time() > deadline:
                break
        # WS 没给出结论：退回轮询
        snap = await self.snapshot(sub)
        if snap.state.terminal:
            return self._no_product(snap) or snap
        return JobSnapshot(
            state=JobState.FAILED,
            error={"type": "timeout", "message": f"{timeout_s}s 内没有拿到终态", "retryable": True},
        )

    def _no_product(self, snap: JobSnapshot) -> JobSnapshot | None:
        """报成功却一个文件都没有 —— 那是失败，不是成功。

        用户看到的应该是「这一镜没出东西」，不是队列里一片绿色然后时间轴上是空的。
        """
        if snap.state is JobState.SUCCEEDED and not snap.outputs:
            return JobSnapshot(
                state=JobState.FAILED,
                error={
                    "type": "no_output",
                    "message": "实例报告执行完成，但没有产出任何文件",
                    "retryable": False,
                },
            )
        return None

    async def _settle_after_signal(self, sub: Submission) -> JobSnapshot:
        for _ in range(8):
            snap = await self.snapshot(sub)
            if snap.state in (JobState.SUCCEEDED, JobState.FAILED):
                return self._no_product(snap) or snap
            await asyncio.sleep(0.5)
        return JobSnapshot(
            state=JobState.FAILED,
            error={
                "type": "no_output",
                "message": "收到执行完成信号，但 history 里始终查不到这个 prompt_id（可能被 /queue delete 掉，或实例重启过）",
                "retryable": True,
            },
        )

    async def events(self, client_id: str) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        """一条 clientId 一条 socket，指数退避重连，产出归一化事件。

        终止判据是 {"type":"executing","data":{"node":null}}。
        execution_success 自 v0.3.10 就存在，但只在「带 client_id 且正常跑完」时发，
        出错/中断路径没有 —— 所以它只能当加速提示，不能当唯一判据。
        """
        if not client_id:
            raise GenError("events() 需要 client_id，否则服务端不会给你任何非广播事件", kind="client_validation")

        url = f"{_ws_url(self.base_url)}?{urlencode({'clientId': client_id})}"
        backoff = 1.0
        queue: asyncio.Queue[tuple[str, dict[str, Any]] | None] = asyncio.Queue()

        async def pump() -> None:
            nonlocal backoff
            while True:
                try:
                    async with websockets.connect(url, max_size=None, additional_headers=dict(self._headers)) as ws:
                        self._sockets[client_id] = ws
                        backoff = 1.0
                        async for raw in ws:
                            if isinstance(raw, (bytes, bytearray)):
                                evt = _decode_binary(raw)
                                if evt:
                                    await queue.put(evt)
                                continue
                            msg = json.loads(raw)
                            await queue.put((msg.get("type", "unknown"), msg.get("data") or {}))
                except asyncio.CancelledError:
                    await queue.put(None)
                    raise
                except Exception as exc:  # 连接抖动：退避重连，重连后服务端会补发当前 executing 节点
                    log.warning("WS 断开(%s)，%.1fs 后重连：%s", client_id[:8], backoff, redact(str(exc))[:160])
                    await asyncio.sleep(backoff)
                    backoff = min(self._ws_backoff_max, backoff * 2)

        task = asyncio.create_task(pump())
        try:
            while True:
                item = await queue.get()
                if item is None:
                    return
                yield item
        finally:
            task.cancel()
            self._sockets.pop(client_id, None)

    # ───────── 探活 ─────────

    async def probe(self) -> Capabilities:
        try:
            stats = await self.system_stats()
        except Exception as exc:
            return Capabilities(reachable=False, protocol=self.protocol, raw={"error": redact(str(exc))[:300]})

        # /system_stats 的真实形状是 {"system": {...}, "devices": [...]}
        # —— system 是 dict，显存信息在 devices[0]，不是 system 里面。
        sysinfo = stats.get("system")
        if isinstance(sysinfo, list):  # 老版本回包形状
            sysinfo = sysinfo[0] if sysinfo else {}
        sysinfo = sysinfo or {}
        device = (stats.get("devices") or [{}])[0] if isinstance(stats.get("devices"), list) else {}

        vram_total = device.get("vram_total") or sysinfo.get("vram_total")
        vram_free = device.get("vram_free") or sysinfo.get("vram_free")
        info = await self.object_info()
        h3 = {n: _node_present(info, n) for n in H3_NODES}

        return Capabilities(
            reachable=True,
            protocol=self.protocol,
            comfy_version=sysinfo.get("comfyui_version"),
            node_count=len(info),
            gpu=device.get("name") or sysinfo.get("name"),
            vram_total_gb=round(vram_total / 1024**3, 2) if vram_total else None,
            vram_free_gb=round(vram_free / 1024**3, 2) if vram_free else None,
            h3_nodes=h3,
            raw={"system_stats": stats},
        )

    async def missing_models(self, graph: dict[str, Any]) -> list[str]:
        """提交前本地判一遍缺哪个权重，省一次注定失败的排队。

        只信 /object_info 给的候选清单 —— 磁盘目录名有两个扫描路径（unet/ 与 diffusion_models/
        都会被收进 unet_name 候选），文件名大小写也可能和文档不一致，靠猜必错。

        按 class_type 逐个问，不拉全量：全量 /object_info 是几 MB，实例正在出片时
        会撞 read 超时（本机实测：队列第一次派发就是这么被打回重排的）。
        """
        missing: list[str] = []
        class_types = sorted({str(node.get("class_type") or "") for node in graph.values()} - {""})
        payloads = await asyncio.gather(*(self.object_info(ct) for ct in class_types))
        info = {ct: (payload.get(ct) or {}) for ct, payload in zip(class_types, payloads)}
        for node_id, node in graph.items():
            class_type = node.get("class_type") or ""
            meta = info.get(class_type) or {}
            bag = meta.get("input") or {}
            for group in ("required", "optional"):
                for field_name, spec in (bag.get(group) or {}).items():
                    if field_name not in MODEL_WIDGETS:
                        continue
                    # 候选清单的形状是 [["a.safetensors", "b.safetensors"], {cfg}]
                    if not (isinstance(spec, list) and spec and isinstance(spec[0], list)):
                        continue
                    choices = [c for c in spec[0] if isinstance(c, str)]
                    value = (node.get("inputs") or {}).get(field_name)
                    if not (isinstance(value, str) and value):
                        continue
                    if value not in choices:
                        hint = f"{class_type}.{field_name} 要的是 {value!r}，实例上没有"
                        near = _closest(value, choices)
                        if near:
                            hint += f"；最接近的可用文件是 {near!r}（大小写或后缀可能不同）"
                        missing.append(f"[{node_id}] {hint}")
                        continue
                    # 文件名在清单里 ≠ 这份权重能用：半截下载是「尺寸对、数据是零」，
                    # 加载不报错，模型输出恒为 0，最后交出一张雪花还标成功（见 weights_health）。
                    bad = await asyncio.to_thread(weights_health.check_weight_file, self._models_root, value)
                    if bad:
                        missing.append(f"[{node_id}] {class_type}.{field_name} {bad}")
        return missing

    async def weight_file_problems(self, values: list[str]) -> list[str]:
        """这几个文件名在这台机器的盘上是不是半截货。

        工作流体检要单独问它一次：missing_models() 判的是「图里写死的值」，
        而体检要判的是**换成绑定值之后**的那一份，那时候图还没改。
        只有配了 output 目录的本机实例答得出（远端没有本地盘可看），其余返回空。
        """
        if not self._models_root:
            return []
        checked = sorted({str(v) for v in values if isinstance(v, str) and v.lower().endswith(".safetensors")})
        problems = await asyncio.gather(
            *(asyncio.to_thread(weights_health.check_weight_file, self._models_root, v) for v in checked)
        )
        return [f"{v}：{p}" for v, p in zip(checked, problems) if p]

    async def align_graph(self, graph: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, str]]]:
        """把图里写死的权重名换成本实例真实存在的文件。

        官方模板常按「另一个精度档」引用文本编码器（本机实测：模板要 qwen3vl_8b_int8_convrot，
        实例上只有 qwen3vl_8b_bf16）。对齐后返回改动清单，让用户看得见我们改了什么。
        """
        info = await self.object_info()
        out = {k: {"class_type": v["class_type"], "inputs": dict(v.get("inputs", {})), **({"_meta": v["_meta"]} if "_meta" in v else {})} for k, v in graph.items()}
        changes: list[dict[str, str]] = []
        # 图里用哪个 H3 条件节点，决定该配哪一种模式的底模/LoRA：
        # 作者的合并版（名字里 fl2va+ref2va 都写）与社区微调版在本机都只有官方单模底模可配。
        classes = {str(v.get("class_type") or "") for v in out.values()}
        mode_hint = "ref" if classes & _REF_MODE_NODES else ("fl" if classes & _FL_MODE_NODES else None)
        for node_id, node in out.items():
            class_type = node["class_type"]
            meta = info.get(class_type) or {}
            bag = meta.get("input") or {}
            for group in ("required", "optional"):
                for field_name, spec in (bag.get(group) or {}).items():
                    if field_name not in MODEL_WIDGETS:
                        continue
                    if not (isinstance(spec, list) and spec and isinstance(spec[0], list)):
                        continue
                    choices = [c for c in spec[0] if isinstance(c, str)]
                    value = node["inputs"].get(field_name)
                    if not isinstance(value, str) or not value or value in choices:
                        continue
                    # 同一节点上的 `type` 输入声明了这颗权重的用途（CLIPLoader.type=qwen_image）。
                    # 带着它去解析，才不会把「同族但用途不对」的大文件当成最近匹配。
                    context = node["inputs"].get("type") if isinstance(node["inputs"].get("type"), str) else None
                    resolved = await self.resolve_model_name(class_type, field_name, value,
                                                             context=context, mode=mode_hint)
                    if resolved and resolved in choices:
                        node["inputs"][field_name] = resolved
                        changes.append({"node": node_id, "field": field_name, "from": value, "to": resolved})
        return out, changes

    async def resolve_model_name(self, class_type: str, field_name: str, wanted: str, *,
                                 context: str | None = None, mode: str | None = None) -> str | None:
        """把文档里的理想文件名换成本实例真实存在的那个。**族不对就返回 None，不许就近凑一个。**

        这里出过事故：模糊匹配按「精度词最少」排序，本机最接近的 int8 权重是
        `qwen_image_2.1_int8_convrot`，于是把 `MiniMax-H3-Ref2VA-int8_convrot` 配成了文生图模型 ——
        提交上去不报“缺文件”，报的是采样阶段一堆看不懂的错。宁可留着原名让 missing_models
        明说「实例上没有这个权重」。

        context 是同一节点上声明「用途」的那个输入的值 —— CLIPLoader 的 `type`
        （"qwen_image" / "minimax"）。它只在**已经判定同族**之后用来加排序分，
        不能当筛选的唯一依据：本机实测 `type=qwen_image` 的候选里那些
        `qwen3.5_9b_..._pe_t2i/pe_i2i` 是提示词扩写器，不是 DiT 的文本编码器。
        """
        info = await self.object_info(class_type)
        meta = info.get(class_type) or {}
        spec = (meta.get("input") or {}).get("required", {}).get(field_name)
        if not (isinstance(spec, list) and spec and isinstance(spec[0], list)):
            return None
        choices = [c for c in spec[0] if isinstance(c, str)]
        if wanted in choices:
            return wanted
        lowered = wanted.lower().replace("_", "")
        for c in choices:
            if c.lower().replace("_", "") == lowered:
                return c
        wants_edit = any(tok in wanted.lower() for tok in ("i2i", "edit", "ref"))
        token = (context or "").lower().replace("_", "").replace("-", "")

        eligible = [c for c in choices if _same_model_family(wanted, c, mode=mode)]
        if not eligible:
            return None

        def rank(c: str) -> tuple[int, int, int, int]:
            low = c.lower().replace("_", "").replace("-", "")
            lighter = 0 if any(x in low for x in ("int8", "fp8", "nvfp4", "awq")) else 1
            # 同族里再按用途分：带上了实例声明的 type（minimax/qwen_image）的先来
            keyed = 0 if token and token in low else 1
            # t2i / i2i 是两个不同用途的编码器，长度一样时会并列。
            # 请求里没提 edit/i2i 就默认要文生图那个，别让并列随机决定画质。
            role = 0 if (wants_edit == (("i2i" in low) or ("edit" in low))) else 1
            return (keyed, lighter, role, len(c))

        return sorted(eligible, key=rank)[0]

    async def cancel(self, sub: Submission) -> bool:
        ok = await self.interrupt(sub.job_ref)
        if not ok:  # 老版本或不在跑：退化成从队列里删
            r = await self._client.post("/queue", json={"delete": [int(sub.job_ref)] if sub.job_ref.isdigit() else []})
            ok = r.status_code < 400
        return ok

    async def close(self) -> None:
        for ws in list(self._sockets.values()):
            await ws.close()
        await self._client.aclose()


MODEL_WIDGETS = {"unet_name", "ckpt_name", "clip_name", "vae_name", "model_name", "lora_name"}

# 同一模型的不同精度档。判断「是不是同一个模型」时要先去掉这些词，
# 否则 qwen3vl_8b_int8_convrot 与 qwen3vl_8b_bf16 会被算成不相似（本机实测就撞上了）。
_PRECISION_TOKENS = (
    "int8convrot", "int8", "fp8scaled", "fp8", "nvfp4awq", "nvfp4", "awq",
    "bf16", "fp16", "fp32", "f16", "f32", "safetensors", "ckpt", "bin", "pt",
)


#: 用「全能参考」条件的节点：需要 Ref2VA 底模
_REF_MODE_NODES = {"MiniMaxH3ReferenceToVideo", "RHMiniMaxH3RefGen", "MiniMaxH3FunControlNetApply"}
#: 用「首尾帧」条件的节点：需要 FL2VA 底模
_FL_MODE_NODES = {"MiniMaxH3ImageToVideo", "ComfyCloudMiniMaxH3FirstLastFrameToVideoNode"}

#: 表示「这是哪个模型」的词根。文件名里带这些前缀的词都归到同一个族（qwen3vl / qwen3 / qwen 同族）。
_FAMILY_ROOTS = ("minimax", "qwen", "anima", "kelin", "flux", "sdxl", "sd3", "wan", "hunyuan",
                 "ltx", "mimo", "pixverse", "indextts", "whisper", "tae", "cosmos", "cogview", "ideogram")
#: 族名的补充判定：h3 只作为 MiniMax 的型号词出现，单独出现时也算一个族
_FAMILY_EXTRA = {"h3"}
#: 同一族里的互斥模式词：fl2va(首尾帧) 与 ref2va(全能参考) 是两个不同的权重，装错就出废片。
_MODE_GROUPS = ({"fl2va", "fl2v"}, {"ref2va", "ref2v"})
_MODE_BY_HINT = {"fl": 0, "ref": 1}
#: 互斥的角色词：视频 VAE 与音频 VAE 是两个文件，名字里都写着角色，不能互换。
_ROLE_GROUPS = ({"video"}, {"audio"})
#: 规格词：文件名里写了就必须对上，不能拿 32B 的编码器冒充 8B 的。
#: 不含 remix/hybrid —— 那是「同一模型的微调/合并版」，本机只有官方底模时，
#: 拿底模替代是对的，但必须在改动清单里说清楚（画面风格会变）。
_SPEC_TOKENS = {"32b", "14b", "8b", "7b", "06b", "6b", "12hz", "24k", "upscaler"}


def _name_tokens(name: str) -> set[str]:
    """文件名 → 词集。只按非字母数字切，不能再把 fl2va 拆成 fl/2/va ——
    拆了之后「模式词互斥」这条规则永远命中不了，Ref2VA 会跟 FL2VA 并列（本机实测踩过）。"""
    base = re.sub(r"\.(safetensors|ckpt|pt|pth|bin|gguf|onnx)$", "", str(name).lower())
    toks = {p for p in re.split(r"[^a-z0-9]+", base) if p}
    for token in _PRECISION_TOKENS:
        toks.discard(token)
    toks -= {"comfyui", "comfy", "convrot", "scaled", "pruned", "fast", "e4m3fn", "e5m2",
             "v", "s", "ckpt", "pt", "bin", "gguf", "pth", "onnx"}
    return toks


def _group_of(tokens: set[str], groups: tuple[set[str], ...]) -> set[int]:
    return {i for i, group in enumerate(groups) if tokens & group}


def _group_conflict(w: set[str], c: set[str], groups: tuple[set[str], ...], *, require: bool) -> bool:
    """两组词在三类互斥组里的归属：归属不同即冲突；require=True 时 wanted 有而候选没有也算冲突。"""
    wg, cg = _group_of(w, groups), _group_of(c, groups)
    if wg and cg and wg != cg:
        return True
    return bool(require and wg and not cg)


def _families(tokens: set[str]) -> set[str]:
    """把词集压成群族集合。"""
    out = {t for t in tokens if t in _FAMILY_EXTRA}
    for tok in tokens:
        for root in _FAMILY_ROOTS:
            if tok.startswith(root):
                out.add(root)
                break
    return out


def _is_vl(tokens: set[str]) -> bool:
    """文件名有没有声明「这是带视觉塔的 VL 版」。

    RH 的命名会把 vl 单独切成一个词（qwen3-vl-32b），本机实名是粘在一起的（qwen3vl_32b），
    两种写法都要认出来。
    """
    return "vl" in tokens or any(t.endswith("vl") for t in tokens)


def _same_model_family(wanted: str, candidate: str, *, mode: str | None = None) -> bool:
    """判断两个文件名是不是同一个模型的不同精度/打包档。

    五条硬规则，全部来自本机踩过的坑：大族词必须一致（不许把 Qwen-Image 当 MiniMax H3）、
    VL 与非 VL 互斥（qwen_3_8b 是纯语言模型，qwen3vl_8b 带视觉塔，架构不同）、
    模式词互斥且必须齐（fl2va 与 ref2va 不能互替，ref2v 的 LoRA 不能拿通用 turbo 冒充）、
    角色词互斥（视频 VAE ≠ 音频 VAE）、写明的规格词必须对上（32B 编码器 ≠ 8B 编码器）。
    两边一个共同词都没有就当不同模型。判不出来一律返回 False，
    让 missing_models 明说「实例上没有这个权重」，而不是凑一个跑废片。

    mode 是图里条件节点给的暗示（"fl"=首尾帧、"ref"=全能参考）。作者常用合并版
    （名字里同时写 fl2va 和 ref2va）或社区微调版（remix），本机只有单一模式的官方底模 ——
    这时按图上真正用的能力来定，而不是按文件名的字面差异判「不是同一个模型」。
    """
    w, c = _name_tokens(wanted), _name_tokens(candidate)
    if not w or not c:
        return False
    wf, cf = _families(w), _families(c)
    # 只要求「想要的族在候选里」：RunningHub 把 H3 的编码器叫 qwen3-vl-32b（不带 minimax 字样），
    # 本机的实名是 qwen3vl_32b_minimax_h3_int8_convrot —— 反过来要求候选没有多余族词，
    # 就会把同一个模型按打包命名判成不同模型，白报一次「缺权重」。
    if wf and not (wf <= cf):
        return False
    if cf and not wf:
        return False
    if not (w & c):
        return False
    # 2026-10-02 实测：Klein 9B 的图原本写着 qwen_3_8b，本机当时只有 qwen3vl_8b_bf16，
    # 这条对齐把纯语言模型换成了带视觉塔的那个 —— 出图涂抹色散，但 ComfyUI 一声不响。
    if _is_vl(w) != _is_vl(c):
        return False
    wg = {i for i, group in enumerate(_MODE_GROUPS) if w & group}
    cg = {i for i, group in enumerate(_MODE_GROUPS) if c & group}
    if mode in _MODE_BY_HINT and cg:
        wg = {_MODE_BY_HINT[mode]}
    if wg and cg and wg != cg:
        return False
    if wg and not cg:
        return False
    if _group_conflict(w, c, _ROLE_GROUPS, require=False):
        return False
    # Turbo LoRA 的步数档是硬属性：4step 的适配器挂到要跑 8 步的链上，出片会糊成一团，
    # 但 ComfyUI 那边完全不报错 —— 只能在这里按词卡死。
    w_steps = {t for t in w if re.fullmatch(r"\d+steps?", t)}
    c_steps = {t for t in c if re.fullmatch(r"\d+steps?", t)}
    if w_steps and c_steps and not (w_steps & c_steps):
        return False
    if (w & _SPEC_TOKENS) - (c & _SPEC_TOKENS):
        return False
    # 版本号类词（2512 / 21 / 10）：两边都写了就必须有交集
    wver = {t for t in w if t.isdigit() and len(t) >= 2}
    cver = {t for t in c if t.isdigit() and len(t) >= 2}
    if wver and cver and not (wver & cver):
        return False
    return True


def _closest(wanted: str, choices: list[str]) -> str | None:
    """按去下划线 + 小写的子串相似度找一个最接近的候选，用来给报错加提示。"""
    if not choices:
        return None
    key = wanted.lower().replace("_", "").replace("-", "")
    best, best_score = None, 0.0
    for c in choices:
        cand = c.lower().replace("_", "").replace("-", "")
        common = sum(1 for a, b in zip(key, cand) if a == b)
        score = common / max(len(key), len(cand), 1)
        if score > best_score:
            best, best_score = c, score
    return best if best_score >= 0.6 else None


def _node_present(info: dict[str, Any], class_type: str) -> bool:
    """未知节点是 200 + {}，所以判存在要判「键在且值非空」。"""
    return class_type in info and bool(info[class_type])


def _is_canonical_uuid(value: str) -> bool:
    try:
        return str(uuid.UUID(value)) == value.lower() and value == str(uuid.UUID(value))
    except (ValueError, AttributeError):
        return False


def _matches_prompt(item: Any, prompt_id: str) -> bool:
    """queue 的元素是 [number, prompt_id, graph, inputs, outputs]。"""
    try:
        return item[1] == prompt_id
    except (IndexError, TypeError):
        return False


def _outputs_from_history(entry: dict[str, Any]) -> list[OutputRef]:
    out: list[OutputRef] = []
    for node_id, node_out in (entry.get("outputs") or {}).items():
        for key in _OUTPUT_KEYS:
            for item in node_out.get(key) or []:
                if not isinstance(item, dict) or "filename" not in item:
                    continue
                out.append(
                    OutputRef(
                        filename=item["filename"],
                        subfolder=item.get("subfolder", ""),
                        type=item.get("type", "output"),
                        node_id=node_id,
                    )
                )
    # 作者画布上的 PreviewImage 会一起进 history（type=temp）。有真正存盘产物时把它们剔掉：
    # 不然任务列表里会多出一张作者自己看的对照小图，用户以为是这次生成的东西。
    # LoadVideo/LoadImage 也会把自己读进去的那个文件以 type=input 原样报回来 ——
    # 本机实测：全能参考任务的产物列表里混进一条和用户上传完全相同的 mp4。
    real = [r for r in out if r.type == "output"]
    if real:
        return real
    return [r for r in out if r.type != "temp"] or out


def _extract_exec_error(messages: list[Any]) -> dict[str, Any]:
    for msg in messages:
        if isinstance(msg, list) and msg and msg[0] == "execution_error":
            payload = msg[1] if len(msg) > 1 and isinstance(msg[1], dict) else {}
            return {
                "type": "comfy_execution_error",
                "message": payload.get("exception_message", "执行出错"),
                "node_id": str(payload.get("node_id")),
                "node_type": payload.get("node_type"),
                "traceback_tail": "\n".join((payload.get("traceback") or [])[-3:]) or None,
            }
    return {"type": "error", "message": "任务失败，但没有细节"}


def _decode_binary(raw: bytes | bytearray) -> tuple[str, dict[str, Any]] | None:
    """二进制帧：>I 大端事件码 + payload。type 4 前面还有一层 4 字节的 metadata 长度。"""
    if len(raw) < 4:
        return None
    event = struct.unpack_from(">I", raw, 0)[0]
    if event == _EV_PREVIEW_IMAGE:
        return ("preview_image", {"bytes": len(raw) - 4})
    if event == _EV_UNENCODED_PREVIEW:
        return ("preview_unencoded", {"bytes": len(raw) - 4})
    if event == _EV_PREVIEW_WITH_METADATA:
        body = raw[4:]
        if len(body) < 4:
            return None
        meta_len = struct.unpack_from(">I", body, 0)[0]
        try:
            meta = json.loads(body[4 : 4 + meta_len].decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            meta = {}
        return ("preview_metadata", {"meta": meta, "bytes": len(body) - 4 - meta_len})
    if event == _EV_TEXT:
        body = raw[4:]
        if len(body) < 4:
            return None
        node_len = struct.unpack_from(">I", body, 0)[0]
        node_id = body[4 : 4 + node_len].decode("utf-8", errors="replace")
        text = body[4 + node_len :].decode("utf-8", errors="replace")
        return ("text_output", {"node": node_id, "text": text})
    return ("binary", {"event": event, "bytes": len(raw) - 4})
