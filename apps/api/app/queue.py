"""队列派发器（PostgreSQL FOR UPDATE SKIP LOCKED）。

不引入 Redis/Celery：目标是本机与局域网，一台 GPU 每分钟个位数任务，DB 队列足够，
而且天然与业务数据同事务、崩溃后状态不会和队列不一致。

三条不变量：
  1. 每个实例同时只跑一个任务（ComfyUI 本身串行；RH /proxy 也只支持并发 1）。
     实例之间真正并行 —— 本地图 + 云端视频是这套系统的主要吞吐来源。
  2. 拿到 prompt_id 立刻落库。之后浏览器关不关、后端重启与否，都不影响任务收口。
  3. 「并发满了/余额不足」不是任务失败。前者退避重排队，后者熔断实例。
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import random
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .db import session_factory
from .gen.base import GenError, JobState
from .gen.registry import InstanceRegistry
from .logging_setup import get_logger, redact
from .models import GenInstance, InstanceLock, Job, JobState as JS, User

log = get_logger("queue")

# 平台侧「资源紧张」，不是任务错了 —— 必须退避重排队
RETRYABLE_KINDS = {"http", "timeout", "connection", "rate_limited", "queue_full", "unavailable"}
# 余额/权限类：重试只会继续烧钱，直接熔断该实例
FATAL_INSTANCE_KINDS = {"insufficient_balance", "auth", "forbidden"}
# 这几类任务的产物就是文件本身：没有文件等于没做成
MEDIA_KINDS = {"image", "video", "video_chain", "upscale"}

CLAIM_SQL = text(
    """
    -- 取一个属于该实例、处于 queued/dispatching 的任务。
    -- SKIP LOCKED 是关键：多个派发循环并发跑时，别的循环已锁住的行直接跳过，
    -- 不会互相等成串行，也不会抢到同一个任务。
    SELECT j.id, j.uuid, j.kind, j.attempts, j.max_attempts, j.priority, j.params, j.title, j.instance_id, j.project_key
      FROM jobs j
      LEFT JOIN instance_locks l ON l.instance_id = j.instance_id
     WHERE j.instance_id = :instance_id
       AND l.instance_id IS NULL
       AND j.state IN ('queued','dispatching')
       AND NOT EXISTS (
             SELECT 1 FROM gen_instances g
              WHERE g.id = j.instance_id AND g.circuit_open
           )
     ORDER BY j.priority, j.id
     FOR UPDATE OF j SKIP LOCKED
     LIMIT 1
    """
)


class QueueDispatcher:
    def __init__(self, registry: InstanceRegistry, *, tick_s: float = 1.0, gpu: Any | None = None) -> None:
        self.registry = registry
        self.tick_s = tick_s
        self.gpu = gpu
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._tracked: dict[str, asyncio.Task[None]] = {}
        # 显存探活按实例缓存，别每个 tick 打一次 /system_stats
        self._vram_cache: dict[str, tuple[float, float | None]] = {}
        self._held_logged: dict[str, float] = {}
        self.vram_ttl_s = 20.0
        # 正在占卡的文本模型调用数。见 gpu_gate / _instance_runnable。
        self._llm_inflight = 0

    # ── 生命周期 ──

    async def start(self) -> None:
        await self.recover()
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(), name="queue-dispatcher")
        log.info("队列派发器已启动")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        for task in list(self._tracked.values()):
            task.cancel()
        self._tracked.clear()
        # 关后端时如果还欠着一次恢复，就地补做：否则用户下次开机发现文本模型不见了，
        # 而且只有进程列表能证明是我们停的
        await self._restore_if_idle()

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                claimed = await self.dispatch_once()
                await self.reconcile_running()
            except Exception as exc:
                # 派发循环绝不能因为一次异常就死掉：那会让整个工作台静默停止产出
                log.exception("派发循环异常：%s", redact(str(exc))[:200])
                claimed = 0
            await asyncio.sleep(self.tick_s if claimed else min(self.tick_s * 3, 5.0))

    # ── 崩溃恢复 ──

    async def recover(self) -> int:
        """重启后收口：没拿到 prompt_id 的直接回队列；拿到了就去实例问真实状态。"""
        async with session_factory()() as s:
            rows = (
                await s.execute(
                    text(
                        """
                        SELECT id, uuid, prompt_id, state FROM jobs
                         WHERE state IN ('dispatching','running')
                        """
                    )
                )
            ).mappings().all()
            orphaned = 0
            for row in rows:
                if not row["prompt_id"]:
                    await s.execute(
                        text("UPDATE jobs SET state='queued', progress='{}'::jsonb WHERE id=:id"),
                        {"id": row["id"]},
                    )
                    orphaned += 1
            # 放掉三类失效锁：无主锁、指向已不存在任务的锁、以及**刚被退回队列的任务**的锁。
            # 漏掉最后一种会让实例永久占死 —— 任务在队列里等，锁却还以为有人在跑。
            await s.execute(
                text(
                    """
                    DELETE FROM instance_locks
                     WHERE job_id IS NULL
                        OR job_id NOT IN (SELECT id FROM jobs)
                        OR job_id IN (SELECT id FROM jobs WHERE state NOT IN ('dispatching','running'))
                    """
                )
            )
            await s.commit()
            if orphaned:
                log.info("重启恢复：%d 个任务没有 prompt_id，已退回队列", orphaned)
            return orphaned

    async def reconcile_running(self) -> int:
        """给在跑的任务对账。WS 掉线、后端重启都走这条路收口。"""
        async with session_factory()() as s:
            rows = (
                await s.execute(
                    text(
                        """
                        SELECT j.id, j.uuid, j.prompt_id, j.client_id, j.instance_id, j.attempts, j.max_attempts,
                               j.project_key, j.params
                          FROM jobs j WHERE j.state IN ('running','dispatching')
                        """
                    )
                )
            ).mappings().all()
        settled = 0
        for row in rows:
            if not row["prompt_id"]:
                continue
            snap = await self._snapshot(row)
            if snap is None:
                continue
            await self._settle(row, snap)
            settled += 1
        return settled

    async def _snapshot(self, row: Any):
        from .gen.base import Submission

        try:
            client = self.registry.client(str(row["instance_id"]))
            return await client.snapshot(Submission(job_ref=row["prompt_id"], client_id=row["client_id"]))
        except Exception as exc:
            log.warning("对账失败 job=%s：%s", row["uuid"], redact(str(exc))[:160])
            return None

    # ── 认领与派发 ──

    async def dispatch_once(self) -> int:
        """为每个可用实例尝试认领一个任务。返回认领数（测试里用它做确定性推进）。"""
        claimed = 0
        await self._restore_if_idle()
        for instance_id in self.registry.ids:
            if not self._instance_runnable(instance_id):
                continue
            if any(t.done() is False and getattr(t, "_h3_job_instance", None) == instance_id for t in self._tracked.values()):
                continue  # 该实例已有任务在盯，保持串行
            # 没活就别碰显存和进程：显存不足会触发「卸模型 + 停文本模型」，
            # 空队列时也这么干就会变成每半分钟把用户的 llama-server 停掉又拉起一次
            if not await self._has_work_for(instance_id):
                continue
            free_ok, why = await self._vram_ok(instance_id)
            if not free_ok and self._is_local(instance_id):
                # 第一步是让 ComfyUI 把上一单的权重卸掉：它常驻的 Qwen-Image 栈有 23.6 GB，
                # 不卸就是「上一单的模型挡住下一单」，跟文本模型没关系。
                try:
                    await self.registry.client(instance_id).free()
                    await asyncio.sleep(1.5)
                    self._vram_cache.pop(instance_id, None)
                    free_ok, why = await self._vram_ok(instance_id)
                except Exception as exc:
                    log.debug("卸模型失败（继续按显存不足处理）：%s", redact(str(exc))[:120])
            if not free_ok and self.gpu is not None and self._is_local(instance_id):
                # 还是不够才是「文本模型占着卡」的信号。让位动作原本写在 _run 里，
                # 可任务认领不了就永远走不到那一步 —— 所以这里必须先让一次再判。
                stopped = await self.gpu.yield_llm(reason=f"本地实例显存不足：{why or ''}".strip())
                if stopped:
                    await asyncio.sleep(2.0)  # 进程退出到显存真的回到池子里有一两秒延迟
                    self._vram_cache.pop(instance_id, None)  # 刚测过的数字已经作废
                    free_ok, why = await self._vram_ok(instance_id)
            if not free_ok:
                await self._hold_note(instance_id, why)
                continue
            job = await self._claim(instance_id)
            if job is None:
                continue
            claimed += 1
            task = asyncio.create_task(self._run(job, instance_id), name=f"job-{job['uuid'][:8]}")
            task._h3_job_instance = instance_id  # type: ignore[attr-defined]
            self._tracked[job["uuid"]] = task
            task.add_done_callback(lambda _t, u=job["uuid"]: self._tracked.pop(u, None))
        return claimed

    async def _vram_ok(self, instance_id: str) -> tuple[bool, str | None]:
        """出图/出片要常驻十 GB 级权重，显存不够时提交进去不会报错，只会假死。

        本机实测：27B 文本模型占满 24GB 卡时，Qwen-Image 在 Model Initializing 卡了
        28 分钟，ComfyUI 连 HTTP 都不响应 —— 所以宁可卡在队列外并说清原因。
        """
        client = self.registry.client(instance_id)
        getter = getattr(client, "free_vram_gb", None)
        if getter is None:
            return True, None  # 桩客户端或协议不支持：不拿「探不到」当「不够」
        now = asyncio.get_running_loop().time()
        cached = self._vram_cache.get(instance_id)
        if cached and now - cached[0] < self.vram_ttl_s:
            free = cached[1]
        else:
            try:
                free = await getter()
            except Exception:
                free = None
            self._vram_cache[instance_id] = (now, free)
        if free is None:
            return True, None
        floor = self.registry.settings.min_free_vram_gb
        if free < floor:
            return False, f"实例空闲显存 {free:.1f} GB，低于出图/出片所需的 {floor:.0f} GB"
        return True, None

    async def _hold_note(self, instance_id: str, why: str | None) -> None:
        """把「为什么没动」写进队首任务的 progress，并限流打日志（每个实例 30s 一次）。"""
        now = time.monotonic()
        if now - self._held_logged.get(instance_id, 0) > 30:
            self._held_logged[instance_id] = now
            log.warning("实例 %s 暂停派发：%s", instance_id, why)
        async with session_factory()() as s:
            await s.execute(
                text(
                    """
                    UPDATE jobs SET progress=:p
                     WHERE id = (SELECT id FROM jobs WHERE instance_id=:i AND state='queued'
                                  ORDER BY priority, id LIMIT 1)
                    """
                ),
                {"i": int(instance_id), "p": _json({"stage": f"{why}；先停掉占显存的进程（如本地 27B 文本模型）或改用云端实例"})},
            )
            await s.commit()

    def _instance_runnable(self, instance_id: str) -> bool:
        # 熔断状态只看数据库列（CLAIM_SQL 已用 NOT EXISTS 过滤 circuit_open）；
        # 再维护一份内存集合就会和库里不一致，重启后更是直接归零。
        if self._has_active_on(instance_id):
            return False
        # 文本模型正在占卡时，本机实例先别认领新任务：一张 4090 装不下 27B 文本模型
        # 再加 Qwen-Image/H3 的权重栈，硬挤的结果是双方一起换页到不可用。云端实例不受影响。
        if self._llm_inflight and self._is_local(instance_id):
            return False
        return True

    def _is_local(self, instance_id: str) -> bool:
        try:
            return self.registry.config(str(instance_id)).placement == "local"
        except Exception:
            return False

    # ── 单卡互斥 ──

    def local_render_active(self) -> bool:
        """本机 ComfyUI 上是否正有任务在算。文本模型入口用它挡并发。"""
        local = {i for i in self.registry.ids if self._is_local(i)}
        return any(getattr(t, "_h3_job_instance", None) in local and not t.done() for t in self._tracked.values())

    @asynccontextmanager
    async def gpu_gate(self) -> AsyncIterator[None]:
        """把一次文本模型调用标记成「正在占卡」，期间本机实例不认领图/视频任务。"""
        self._llm_inflight += 1
        try:
            yield
        finally:
            self._llm_inflight -= 1

    def _has_active_on(self, instance_id: str) -> bool:
        return any(getattr(t, "_h3_job_instance", None) == instance_id and not t.done() for t in self._tracked.values())

    async def _claim(self, instance_id: str) -> dict[str, Any] | None:
        async with session_factory()() as s:
            async with s.begin():
                row = (await s.execute(CLAIM_SQL, {"instance_id": int(instance_id)})).mappings().first()
                if row is None:
                    return None
                job = dict(row)
                # 认领与加锁必须同事务，否则两个循环可能同时认为拿到了同一个任务
                s.add(InstanceLock(instance_id=int(instance_id), job_id=job["id"]))
                await s.execute(
                    text("UPDATE jobs SET state='dispatching', attempts=attempts+1 WHERE id=:id"),
                    {"id": job["id"]},
                )
                job["claimed"] = True
                return job

    async def submit_job(self, *, kind, title, params, instance_id, project_key=None, owner_id=None, workflow_id=None, priority=100) -> Job:
        """入队。真正的提交发生在派发循环里，这样关页面、重启都不影响执行。"""
        if owner_id is not None:
            async with session_factory()() as s:
                owner = await s.get(User, owner_id)
                if owner is not None and (owner.role.value if hasattr(owner.role, "value") else owner.role) == "viewer":
                    raise GenError("viewer 角色不能派发任务", kind="forbidden")
        async with session_factory()() as s:
            job = Job(
                uuid=str(uuid.uuid4()),
                kind=kind,
                title=title,
                params=params,
                instance_id=int(instance_id),
                project_key=project_key,
                owner_id=owner_id,
                workflow_id=workflow_id,
                priority=priority,
                state=JS.queued,
            )
            s.add(job)
            await s.commit()
            await s.refresh(job)
            return job

    async def _build_from_template(self, client: Any, params: dict[str, Any], job_id: int) -> dict[str, Any]:
        """按模板建图，并把成品图写回 params.graph。

        写回不是为了好看：重试与排查时必须能看到当时真正提交给 ComfyUI 的那张图，
        否则「槽位是对的、节点是错的」这类问题永远只能靠复现。
        """
        from .config import get_settings
        from .gen.templates import BuildContext, build_graph

        ctx = BuildContext(client=client, media_root=Path(get_settings().media_root))
        graph = await build_graph(ctx, params["template"], params.get("slots") or {})
        async with session_factory()() as s:
            await s.execute(
                text("UPDATE jobs SET params = jsonb_set(params, '{graph}', CAST(:graph AS jsonb), true) WHERE id=:id"),
                {"id": job_id, "graph": json.dumps(graph, ensure_ascii=False)},
            )
            await s.commit()
        return graph

    async def _run(self, job: dict[str, Any], instance_id: str) -> None:
        from .gen.base import Submission

        try:
            client = self.registry.client(instance_id)
            params: dict[str, Any] = job["params"] or {}
            # 本地实例开跑前先把文本模型让开：一张卡装不下两套权重栈
            if self.gpu is not None and self._is_local(instance_id):
                await self.gpu.yield_llm(reason=f"本地任务 {str(job['uuid'])[:8]}")
            graph = params.get("graph")
            if not graph and params.get("template"):
                graph = await self._build_from_template(client, params, job["id"])
            if not graph:
                raise GenError("任务缺少 graph：既没有直接给图，也没有给 template", kind="client_validation")
            missing = await client.missing_models(graph)
            if missing:
                raise GenError("；".join(missing), kind="missing_models")

            client_id = str(uuid.uuid4())
            prompt_id = str(uuid.uuid4())
            sub = await client.submit(graph, client_id=client_id, job_ref=prompt_id)
            async with session_factory()() as s:
                await s.execute(
                    text(
                        """
                        UPDATE jobs SET state='running', prompt_id=:pid, client_id=:cid,
                                        started_at=now(), queue_pos=NULL
                         WHERE id=:id
                        """
                    ),
                    {"id": job["id"], "pid": sub.job_ref, "cid": client_id},
                )
                await s.commit()
            snap = await self._await_terminal(client, sub, job)
            await self._settle(job, snap)
        except GenError as exc:
            await self._settle(job, {"error": exc})
        except Exception as exc:
            # TimeoutError / CancelledError 的 str() 是空的，只取消息会得到「重排：」这种没用的话
            detail = str(exc).strip() or type(exc).__name__
            await self._settle(job, {"error": GenError(f"{type(exc).__name__}: {detail}"[:400], kind="unknown", retryable=True)})
        finally:
            async with session_factory()() as s:
                await s.execute(text("DELETE FROM instance_locks WHERE instance_id=:i AND job_id=:j"), {"i": int(instance_id), "j": job["id"]})
                await s.commit()
            if self.gpu is not None and self._is_local(instance_id):
                # 本地队列排空了才把文本模型拉回来 —— 逐条恢复会让六个镜头的批量渲染
                # 在每两条之间白装一次 17.5 GB 权重
                if not self._local_work_left(str(job["uuid"])) and await self._queued_local_count() == 0:
                    await self.gpu.restore_llm()

    async def _has_work_for(self, instance_id: str) -> bool:
        """这个实例名下有没有排队中的任务。显存闸门与让位都要先问它一句。"""
        async with session_factory()() as s:
            n = await s.scalar(
                text(
                    """
                    SELECT count(*) FROM jobs
                     WHERE instance_id = CAST(:i AS bigint) AND state IN ('queued','dispatching')
                    """
                ),
                {"i": int(instance_id)},
            )
        return int(n or 0) > 0

    async def _restore_if_idle(self) -> None:
        """本地没活了却还欠着一次恢复，就补做。

        只靠 _run 的 finally 不够：任务被判失败、被取消、或派发循环在
        「已让位、未起跑」之间异常退出，都会把用户的文本模型留在停掉的状态。
        让位是替用户做的决定，恢复就不能只挂在成功路径上。
        """
        if self.gpu is None or not getattr(self.gpu, "stopped", None):
            return
        if self.local_render_active() or await self._queued_local_count() > 0:
            return
        await self.gpu.restore_llm()

    def _local_work_left(self, exclude_uuid: str) -> bool:
        local = {i for i in self.registry.ids if self._is_local(i)}
        for uuid_, task in self._tracked.items():
            if uuid_ == exclude_uuid or task.done():
                continue
            if getattr(task, "_h3_job_instance", None) in local:
                return True
        return False

    async def _queued_local_count(self) -> int:
        async with session_factory()() as s:
            n = await s.scalar(
                text(
                    """
                    SELECT count(*) FROM jobs j JOIN gen_instances g ON g.id = j.instance_id
                     WHERE g.placement = 'local' AND j.state IN ('queued','dispatching','running')
                    """
                )
            )
        return int(n or 0)

    async def _await_terminal(self, client, sub, job: dict[str, Any]):
        from .gen.base import JobState as GS

        timeout = float((job["params"] or {}).get("timeout_s") or 3600)
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            snap = await client.snapshot(sub)
            if snap.state is GS.SUCCEEDED:
                return snap
            if snap.state is GS.FAILED:
                return snap
            if snap.state is GS.CANCELED:
                return snap
            await asyncio.sleep(2.0)
        return {"error": GenError("等待实例返回终态超时", kind="timeout", retryable=True)}

    async def _touch(self, job: dict[str, Any], snap: Any) -> None:
        """非终态只做进度对账：写 progress/queue_pos，绝不写 finished_at。"""
        progress = {
            "value": snap.progress.value,
            "max": snap.progress.max,
            "node": snap.progress.node,
            "stage": snap.progress.stage,
            "percentageUnavailable": snap.progress.percentage_unavailable,
        }
        # 只往前走：对账看到 'queued' 时不能把内存里正在跑的任务写回队列，
        # 否则锁还在、实例上还在算，却又有第二个循环来认领它。
        promote = snap.state is JS.running
        sql = (
            "UPDATE jobs SET state='running', progress=:p, queue_pos=:q, started_at=COALESCE(started_at, now())"
            " WHERE id=:id AND state='dispatching'"
            if promote
            else "UPDATE jobs SET progress=:p, queue_pos=:q WHERE id=:id AND state IN ('dispatching','running')"
        )
        async with session_factory()() as s:
            await s.execute(text(sql), {"id": job["id"], "p": _json(progress), "q": snap.queue_position})
            await s.commit()

    async def _settle(self, job: dict[str, Any], result: Any) -> None:
        """把结果写回库，并按错误性质决定 重试 / 熔断 / 判失败。"""
        # 非终态绝不能收口。对账循环每个 tick 都会把 RUNNING 快照喂进来，
        # 那时 outputs 天然为空 —— 不挡这道就会把还在跑的任务写成「成功、零产物」，
        # 而实例那边还在继续算。
        state = getattr(result, "state", None)
        if state is not None and not state.terminal:
            await self._touch(job, result)
            return

        error = None
        outputs: list[int] = []
        cost: dict[str, Any] = {}

        if isinstance(result, dict) and "error" in result:
            exc = result["error"]
            error = exc.as_dict() if isinstance(exc, GenError) else {"type": "unknown", "message": str(exc)}
        elif hasattr(result, "error") and getattr(result, "error", None):
            payload = dict(result.error)
            payload.setdefault("type", "comfy_execution_error")
            # 原始 ComfyUI traceback 的 retryable 判断靠节点报错关键词，宁可保守
            payload["retryable"] = bool(payload.get("retryable"))
            error = payload
        elif hasattr(result, "outputs"):
            outputs = await self._persist_outputs(job, result.outputs)
            cost = getattr(result, "cost", None) or {}

        if error is None and state is not None and state.value == "canceled":
            # 取消是一种结果，不是失败，也不是成功：别让后面的 no_output 判断把它改写成「零产物」
            error = {"type": "canceled", "message": "已在实例上取消", "retryable": False}

        if error is None and not outputs and _kind(job) in MEDIA_KINDS:
            # 实例报了成功、产物却一个都没有：对生产来说这就是失败，别让时间轴上是空的。
            # 不重试 —— 重跑一遍同样没文件，只是白烧显存；要重试的是用户改完工作流之后。
            error = {"type": "no_output", "message": "执行完成但没有产出任何文件，请检查保存节点与实例上的输出目录", "retryable": False}

        async with session_factory()() as s:
            if error is None:
                # output 是 bigint[]，显式 CAST 才不会被判成未知类型
                await s.execute(
                    text(
                        """
                        UPDATE jobs SET state='succeeded', finished_at=now(),
                                        output=CAST(:o AS bigint[]), cost=:c, error=NULL
                         WHERE id=:id
                        """
                    ),
                    {"id": job["id"], "o": outputs or [], "c": _json(cost)},
                )
                await s.commit()
                return

            retryable = bool(error.get("retryable")) or error.get("type") in RETRYABLE_KINDS
            kind = error.get("type", "unknown")
            attempts = int(job.get("attempts") or 1)
            max_attempts = int(job.get("max_attempts") or 3)

            if kind in FATAL_INSTANCE_KINDS and job.get("instance_id"):
                # 熔断而不是继续派：按秒计费的实例烧钱是不可逆的
                await s.execute(text("UPDATE gen_instances SET circuit_open=true, last_error=:e WHERE id=:i"), {"i": int(job["instance_id"]), "e": error.get("message", "")[:500]})
                log.warning("实例 %s 已熔断：%s", job["instance_id"], error.get("message"))
            elif kind in {"rate_limited", "queue_full"} and job.get("instance_id"):
                await s.execute(
                    text("UPDATE gen_instances SET last_error=:e WHERE id=:i"),
                    {"i": int(job["instance_id"]), "e": error.get("message", "")[:500]},
                )

            if retryable and attempts < max_attempts:
                backoff = min(120, 2 ** attempts * 5) + random.randint(0, 3)
                await s.execute(
                    text(
                        f"""
                        UPDATE jobs SET state='queued', error=:e, progress='{{}}'::jsonb,
                                        priority = CASE WHEN :fast THEN 0 ELSE priority END
                         WHERE id=:id
                        """
                    ),
                    {"id": job["id"], "e": _json(error), "fast": False},
                )
                log.info("任务 %s 可重试（第 %d/%d 次），约 %ds 后重排：%s", str(job["uuid"])[:8], attempts, max_attempts, backoff, error.get("message"))
            else:
                await s.execute(
                    text("UPDATE jobs SET state='failed', finished_at=now(), error=:e WHERE id=:id"),
                    {"id": job["id"], "e": _json(error)},
                )
                log.info("任务 %s 判失败：%s", str(job["uuid"])[:8], error.get("message"))
            await s.commit()

    async def _persist_outputs(self, job: dict[str, Any], refs: list[Any]) -> list[int]:
        """产物一律落到 media 表并存进我们自己的目录。

        RunningHub 的结果链接约 1 天过期，存外链等于埋 404 —— 这里必须下载转存。
        """
        from .models import Media

        ids: list[int] = []
        instance_id = str(job["instance_id"])
        client = self.registry.client(instance_id)
        settings = self.registry.settings
        # meta 由派发方填（{"role":"character_ref","refId":"char-3"}），
        # 没有它产物就只是一堆文件，资产库与「这张图是谁的定妆照」再也对不上。
        tag: dict[str, Any] = (job.get("params") or {}).get("meta") or {}
        is_video = lambda name: str(name).lower().endswith((".mp4", ".webm", ".mov"))
        async with session_factory()() as s:
            for ref in refs or []:
                data = await client.fetch_output(ref)
                rel = f"jobs/{job['uuid'][:8]}/{ref.filename}"
                # 同一个 job 可能被收口两次（_run 的终态 + 对账循环也看到终态），
                # 不去重就会给一个文件配两行 media，资产库与素材包里就是成倍的重复片段
                existing = await s.scalar(text("SELECT id FROM media WHERE path = :p LIMIT 1"), {"p": rel})
                if existing is not None:
                    ids.append(int(existing))
                    continue
                dest = settings.media_root / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(data)
                m = Media(
                    project_key=job.get("project_key"),
                    kind="video" if is_video(ref.filename) else "image",
                    role=tag.get("role") or ("video" if is_video(ref.filename) else "output"),
                    ref_id=tag.get("refId") or tag.get("ref_id"),
                    path=rel,
                    bytes_=len(data),
                    origin={
                        "instance_id": instance_id,
                        "subfolder": ref.subfolder,
                        "filename": ref.filename,
                        "type": ref.type,
                        "node_id": ref.node_id,
                    },
                    meta={k: v for k, v in tag.items() if k not in {"role", "refId"}} or {},
                )
                s.add(m)
                await s.flush()
                ids.append(m.id)
            await s.commit()
        return ids

def _json(value: Any) -> str:
    import json

    return json.dumps(value if value is not None else {}, ensure_ascii=False)


def _kind(job: dict[str, Any]) -> str:
    """kind 从裸 SQL 回来是字符串，从 ORM 回来是枚举 —— 比较前先归一。"""
    value = job.get("kind")
    return str(getattr(value, "value", value))
