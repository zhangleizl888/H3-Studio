"""原生 ComfyUI 协议客户端。

同时服务三种部署：本机 :8188、cloudflared 隧道后的自建云端、RunningHub 的 /proxy/{key} 网关。
实现细节逐条对应 PLAN §5.1 的 9 条防御规则，那些坑都是核实过的，不是想当然。
"""

from __future__ import annotations

import asyncio
import json
import struct
import uuid
from pathlib import Path
from typing import Any, AsyncIterator
from urllib.parse import urlencode, urlsplit, urlunsplit

import httpx
import websockets

from ..logging_setup import get_logger, redact
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
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers=self._headers,
            timeout=httpx.Timeout(timeout_s, connect=15.0, read=60.0),
            follow_redirects=True,
        )
        self._ws_backoff_max = ws_backoff_max_s
        self._local_output_root = Path(local_output_root) if local_output_root else None
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
            head = next(iter(errors.items()))
            node_id, node_err = head
            detail = node_err.get("errors", [{}])[0] if isinstance(node_err, dict) and node_err.get("errors") else {}
            raise GenError(
                detail.get("detail", "节点校验失败"),
                kind="comfy_validation",
                node_id=node_id,
                node_type=detail.get("node_type"),
            )
        return Submission(
            job_ref=data["prompt_id"],
            client_id=client_id,
            queue_number=data.get("number"),
            raw=data,
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
                    if field_name not in _MODEL_WIDGETS:
                        continue
                    # 候选清单的形状是 [["a.safetensors", "b.safetensors"], {cfg}]
                    if not (isinstance(spec, list) and spec and isinstance(spec[0], list)):
                        continue
                    choices = [c for c in spec[0] if isinstance(c, str)]
                    value = (node.get("inputs") or {}).get(field_name)
                    if isinstance(value, str) and value and value not in choices:
                        hint = f"{class_type}.{field_name} 要的是 {value!r}，实例上没有"
                        near = _closest(value, choices)
                        if near:
                            hint += f"；最接近的可用文件是 {near!r}（大小写或后缀可能不同）"
                        missing.append(f"[{node_id}] {hint}")
        return missing

    async def align_graph(self, graph: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, str]]]:
        """把图里写死的权重名换成本实例真实存在的文件。

        官方模板常按「另一个精度档」引用文本编码器（本机实测：模板要 qwen3vl_8b_int8_convrot，
        实例上只有 qwen3vl_8b_bf16）。对齐后返回改动清单，让用户看得见我们改了什么。
        """
        info = await self.object_info()
        out = {k: {"class_type": v["class_type"], "inputs": dict(v.get("inputs", {})), **({"_meta": v["_meta"]} if "_meta" in v else {})} for k, v in graph.items()}
        changes: list[dict[str, str]] = []
        for node_id, node in out.items():
            class_type = node["class_type"]
            meta = info.get(class_type) or {}
            bag = meta.get("input") or {}
            for group in ("required", "optional"):
                for field_name, spec in (bag.get(group) or {}).items():
                    if field_name not in _MODEL_WIDGETS:
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
                    resolved = await self.resolve_model_name(class_type, field_name, value, context=context)
                    if resolved and resolved in choices:
                        node["inputs"][field_name] = resolved
                        changes.append({"node": node_id, "field": field_name, "from": value, "to": resolved})
        return out, changes

    async def resolve_model_name(self, class_type: str, field_name: str, wanted: str, *, context: str | None = None) -> str | None:
        """把文档里的理想文件名换成本实例真实存在的那个。找不到返回 None。

        context 是同一节点上声明「用途」的那个输入的值 —— CLIPLoader 的 `type`
        （"qwen_image" / "minimax"）。有它就必须先用它筛：本机实测踩过，模板要
        `qwen3vl_8b_int8_convrot` 而实例没有，只按「同族 + 精度优先」会挑中
        16.3GB 的 `qwen3vl_8b_bf16`，而真正该用的是 8.8GB 的
        `qwen3.5_9b_qwen_image_2.1_pe_t2i.int8_convrot`（名字里就写着 qwen_image）。
        选错的不只是大小，是编码器本身。
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
        token = (context or "").lower().replace("_", "").replace("-", "")
        keyed = [c for c in choices if token and token in c.lower().replace("_", "").replace("-", "")]
        family = _model_family(wanted)
        same_family = [c for c in choices if _model_family(c) == family and family]

        wants_edit = any(tok in wanted.lower() for tok in ("i2i", "edit", "ref"))

        def rank(c: str) -> tuple[int, int, int]:
            low = c.lower()
            lighter = 0 if any(x in low for x in ("int8", "fp8", "nvfp4", "awq")) else 1
            # t2i / i2i 是两个不同用途的编码器，长度一样时会并列。
            # 请求里没提 edit/i2i 就默认要文生图那个，别让并列随机决定画质。
            role = 0 if (wants_edit == (("i2i" in low) or ("edit" in low))) else 1
            return (lighter, role, len(c))

        # 用途词优先于「同族」：族名相近但用途不对的编码器，加载进来也是废图
        for pool in (keyed, same_family, choices):
            if pool:
                return sorted(pool, key=rank)[0]
        return _closest(wanted, choices)

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


_MODEL_WIDGETS = {"unet_name", "ckpt_name", "clip_name", "vae_name", "model_name", "lora_name"}

# 同一模型的不同精度档。判断「是不是同一个模型」时要先去掉这些词，
# 否则 qwen3vl_8b_int8_convrot 与 qwen3vl_8b_bf16 会被算成不相似（本机实测就撞上了）。
_PRECISION_TOKENS = (
    "int8convrot", "int8", "fp8scaled", "fp8", "nvfp4awq", "nvfp4", "awq",
    "bf16", "fp16", "fp32", "f16", "f32", "safetensors", "ckpt", "bin", "pt",
)


def _model_family(name: str) -> str:
    """把文件名压成「模型家族」指纹：去分隔符、去精度词、去扩展名痕迹。"""
    base = name.lower().replace("_", "").replace("-", "").replace(".", "")
    for token in _PRECISION_TOKENS:
        base = base.replace(token, "")
    return base


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
    return out


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
