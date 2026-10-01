"""派发器行为测试（真实 PostgreSQL + 桩 GenClient）。

断言的是队列语义，不是"能跑通"：
  - 同一实例上任何时刻只有一个任务在跑（ComfyUI 串行；RH /proxy 也只支持并发 1）
  - 不同实例必须并行 —— 否则"本地图 + 云端视频"这条主要吞吐路径就是假的
  - 「并发满/超时」退回队列并累加 attempts；「余额不足」熔断实例而不是重试烧钱
  - 崩溃恢复：没拿到 prompt_id 的回队列，锁不能把实例永久占住
"""

from __future__ import annotations

import asyncio
import base64
import json
import uuid
from typing import Any

import pytest
from sqlalchemy import text

from app.crypto import encrypt
from app.db import session_factory
from app.gen.base import Capabilities, GenError, JobSnapshot, JobState, OutputRef, Progress, Submission
from app.gen.registry import InstanceRegistry
from app.queue import QueueDispatcher
from app.config import InstanceConfig, Settings

pytestmark = pytest.mark.live

GRAPH = {"1": {"class_type": "EmptyImage", "inputs": {"width": 8, "height": 8, "batch_size": 1, "color": 0}}}


class StubClient:
    protocol = "comfy_native"

    def __init__(self, instance_id: str, behavior: dict[str, Any]) -> None:
        self.instance_id = instance_id
        self.behavior = behavior
        self.submitted: list[str] = []
        self.finished: set[str] = set()
        self.cancelled: list[str] = []

    async def probe(self) -> Capabilities:
        return Capabilities(reachable=True, protocol=self.protocol)

    async def missing_models(self, graph):  # noqa: ARG002
        return []

    async def submit(self, graph, *, client_id: str, job_ref: str | None = None) -> Submission:
        prompt_id = job_ref or str(uuid.uuid4())
        self.submitted.append(prompt_id)
        if self.behavior.get("reject"):
            raise GenError(self.behavior["reject"], kind=self.behavior.get("kind", "comfy_validation"))
        return Submission(job_ref=prompt_id, client_id=client_id, queue_number=len(self.submitted))

    async def snapshot(self, sub: Submission) -> JobSnapshot:
        if self.behavior.get("hold_running") and sub.job_ref not in self.submitted:
            raise AssertionError("snapshot 被调用但从未 submit")
        if self.behavior.get("hold_running"):
            # 永远停在执行中：用来验证「对账循环喂进来的非终态快照绝不收口」
            return JobSnapshot(state=JobState.RUNNING, progress=Progress(value=3, max=8, stage="采样 3/8"))
        if self.behavior.get("empty_success"):
            return JobSnapshot(state=JobState.SUCCEEDED, outputs=[])
        if self.behavior.get("retryable") and sub.job_ref not in self.finished:
            self.finished.add(sub.job_ref)
            return JobSnapshot(state=JobState.FAILED, error={"type": "timeout", "message": "读断了", "retryable": True})
        if self.behavior.get("fatal_balance") and sub.job_ref not in self.finished:
            self.finished.add(sub.job_ref)
            return JobSnapshot(state=JobState.FAILED, error={"type": "insufficient_balance", "message": "余额不足"})
        if sub.job_ref not in self.finished:
            # 必须记账，否则这条分支永远返回 RUNNING，成功路径根本测不到
            self.finished.add(sub.job_ref)
            await asyncio.sleep(0.05)
            return JobSnapshot(state=JobState.RUNNING, progress=Progress(value=1, max=10, stage="采样"))
        if sub.job_ref not in self.submitted:
            raise AssertionError("snapshot 被调用但从未 submit")
        return JobSnapshot(
            state=JobState.SUCCEEDED,
            outputs=[OutputRef(filename=f"{sub.job_ref[:8]}.png", subfolder="stub", type="output", node_id="1")],
            cost={"seconds": 3, "money": 0.42} if self.behavior.get("billable") else {},
        )

    async def fetch_output(self, ref: OutputRef) -> bytes:
        return b"\x89PNG\r\n\x1a\n" + b"stub-bytes"

    async def cancel(self, sub: Submission) -> bool:
        self.cancelled.append(sub.job_ref)
        return True

    async def close(self) -> None:
        return None


class StubRegistry:
    """只实现派发器用到的三个方法，避免为了测试去连真 ComfyUI。"""

    def __init__(self, configs: dict[str, InstanceConfig], behaviors: dict[str, dict[str, Any]]) -> None:
        self.settings = Settings()
        self._configs = configs
        self.clients = {cid: StubClient(cid, behaviors.get(cid, {})) for cid in configs}

    @property
    def ids(self) -> list[str]:
        return list(self._configs)

    def config(self, instance_id: str) -> InstanceConfig:
        return self._configs[instance_id]

    def client(self, instance_id: str) -> StubClient:
        return self.clients[instance_id]

    async def close(self) -> None:
        return None


def _cfg(iid: str, name: str) -> InstanceConfig:
    return InstanceConfig(id=iid, name=name, protocol="comfy_native", placement="local", base_url=f"http://stub/{iid}")


@pytest.fixture(autouse=True)
async def clean_queue():
    async with session_factory()() as s:
        for stmt in (
            "DELETE FROM instance_locks",
            "DELETE FROM jobs",
            "DELETE FROM media",
            "DELETE FROM gen_instances",
        ):
            await s.execute(text(stmt))
        await s.commit()
    yield
    # 收尾也要清：留着 running/dispatching 的桩任务，下一次真后端启动时
    # 派发循环会真的去 ComfyUI 提交它们（本机实测踩过：两个测试残留任务把
    # 队列占住，还顺带触发了单卡仲裁把 llama-server 停了）。
    async with session_factory()() as s:
        for table in ("instance_locks", "jobs", "media", "gen_instances"):
            await s.execute(text(f"DELETE FROM {table}"))
        await s.commit()


async def _make_instances(*ids: str) -> tuple[dict[str, InstanceConfig], list[int]]:
    db_ids: list[int] = []
    configs: dict[str, InstanceConfig] = {}
    async with session_factory()() as s:
        for name in ids:
            key = base64.b64encode(encrypt("secret-key-value") or b"").decode()
            result = await s.execute(
                text(
                    """
                    INSERT INTO gen_instances (name, protocol, placement, base_url, auth_style,
                                               capabilities, quota, cost_total, circuit_open, is_default, api_key_enc, created_at)
                    VALUES (:n,'comfy_native','local',:u,'none','{}'::jsonb,'{}'::jsonb,'{}'::jsonb,false,:d,:k,now())
                    RETURNING id
                    """
                ),
                {"n": name, "u": f"http://127.0.0.1:1/{name}", "d": name == ids[0], "k": key or None},
            )
            db_id = str(result.scalar_one())
            configs[db_id] = _cfg(db_id, name)
            db_ids.append(int(db_id))
        await s.commit()
    return configs, db_ids


async def _queue(s: QueueDispatcher, instance_id: str, count: int, *, title_prefix: str = "t") -> list[str]:
    uuids = []
    for i in range(count):
        job = await s.submit_job(
            kind="image",
            title=f"{title_prefix}{i}",
            params={"graph": GRAPH},
            instance_id=instance_id,
        )
        uuids.append(job.uuid)
    return uuids


async def _wait_for_state(job_uuid: str, states: set[str], timeout: float = 8.0) -> str:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        async with session_factory()() as s:
            state = await s.scalar(text("SELECT state FROM jobs WHERE uuid=:u"), {"u": job_uuid})
        if state in states:
            return state
        await asyncio.sleep(0.05)
    raise AssertionError(f"任务 {job_uuid[:8]} 未在 {timeout}s 内进入 {states}")


async def test_same_instance_serializes():
    """同实例两个任务绝不能同时在跑，否则会把 GPU 挤爆并让进度对不上号。"""
    configs, db_ids = await _make_instances("serial-1")
    reg = StubRegistry(configs, {})
    q = QueueDispatcher(reg, tick_s=0.05)
    # 手工插锁模拟"实例已被占住"
    async with session_factory()() as s:
        await s.execute(text("INSERT INTO instance_locks (instance_id, acquired_at) VALUES (:i, now())"), {"i": db_ids[0]})
        await s.commit()
    await q.dispatch_once()
    assert reg.client(str(db_ids[0])).submitted == [], "实例已有锁时不得再认领任务"


async def test_two_jobs_on_one_instance_run_one_at_a_time():
    configs, db_ids = await _make_instances("two-jobs")
    reg = StubRegistry(configs, {})
    q = QueueDispatcher(reg, tick_s=0.02)
    iid = str(db_ids[0])
    await _queue(q, iid, 2, title_prefix="j")
    await q.dispatch_once()
    await asyncio.sleep(0.1)
    await q.dispatch_once()
    client = reg.client(iid)
    assert len(client.submitted) >= 1
    # 第二次 dispatch 时若第一个还没收口，就不该出现两个 running
    async with session_factory()() as s:
        running = await s.scalar(text("SELECT count(*) FROM jobs WHERE state='running'"))
    assert running <= 1, f"同实例出现 {running} 个并行任务"


async def test_two_instances_run_in_parallel():
    configs, db_ids = await _make_instances("inst-a", "inst-b")
    reg = StubRegistry(configs, {})
    q = QueueDispatcher(reg, tick_s=0.02)
    await _queue(q, str(db_ids[0]), 1, title_prefix="a")
    await _queue(q, str(db_ids[1]), 1, title_prefix="b")
    await q.dispatch_once()
    await asyncio.sleep(0.08)
    claimed_a = len(reg.client(str(db_ids[0])).submitted)
    claimed_b = len(reg.client(str(db_ids[1])).submitted)
    assert claimed_a == 1 and claimed_b == 1, f"跨实例未并行：a={claimed_a} b={claimed_b}"


async def test_retryable_error_goes_back_to_queue_and_counts_attempts():
    configs, db_ids = await _make_instances("retry")
    iid = str(db_ids[0])
    reg = StubRegistry(configs, {iid: {"retryable": True}})
    q = QueueDispatcher(reg, tick_s=0.02)
    (job_uuid,) = await _queue(q, iid, 1)
    await q.dispatch_once()
    state = await _wait_for_state(job_uuid, {"queued", "failed"})
    async with session_factory()() as s:
        row = (await s.execute(text("SELECT state, attempts, error FROM jobs WHERE uuid=:u"), {"u": job_uuid})).mappings().one()
    assert row["attempts"] == 1, row
    assert row["state"] in {"queued", "failed"}, row
    # 可重试错误的判定依据要留在 error 里，UI 才能解释"为什么还在排队"
    assert row["error"] is None or row["error"].get("retryable") is True or row["state"] == "queued", row["error"]


async def test_non_retryable_error_fails_job():
    configs, db_ids = await _make_instances("fatal")
    iid = str(db_ids[0])
    reg = StubRegistry(configs, {iid: {"fatal_balance": True}})
    q = QueueDispatcher(reg, tick_s=0.02)
    (job_uuid,) = await _queue(q, iid, 1)
    await q.dispatch_once()
    await _wait_for_state(job_uuid, {"failed"})
    async with session_factory()() as s:
        err = (await s.execute(text("SELECT error FROM jobs WHERE uuid=:u"), {"u": job_uuid})).scalar_one()
        open_flag = (await s.execute(text("SELECT circuit_open FROM gen_instances WHERE id=:i"), {"i": db_ids[0]})).scalar_one()
    assert err["type"] == "insufficient_balance"
    assert open_flag is True, "余额不足必须熔断实例，而不是继续派发烧钱"


async def test_circuit_open_instance_is_not_claimed():
    configs, db_ids = await _make_instances("blocked")
    iid = str(db_ids[0])
    async with session_factory()() as s:
        await s.execute(text("UPDATE gen_instances SET circuit_open=true WHERE id=:i"), {"i": db_ids[0]})
        await s.commit()
    reg = StubRegistry(configs, {iid: {}})
    q = QueueDispatcher(reg, tick_s=0.02)
    await _queue(q, iid, 1)
    await q.dispatch_once()
    assert reg.client(iid).submitted == [], "熔断中的实例仍被派发了任务"


async def test_success_persists_media_locally():
    """产物必须落到我们自己的目录并建 media 行 —— RH 的签名链接约 1 天就失效。"""
    configs, db_ids = await _make_instances("ok")
    iid = str(db_ids[0])
    reg = StubRegistry(configs, {iid: {"billable": True}})
    q = QueueDispatcher(reg, tick_s=0.02)
    (job_uuid,) = await _queue(q, iid, 1)
    await q.dispatch_once()
    await _wait_for_state(job_uuid, {"succeeded"})
    async with session_factory()() as s:
        row = (
            await s.execute(
                text(
                    """
                    SELECT j.output, j.cost, m.path, m.bytes, m.origin
                      FROM jobs j JOIN media m ON m.id = j.output[1]
                     WHERE j.uuid=:u
                    """
                ),
                {"u": job_uuid},
            )
        ).mappings().one()
    assert row["path"] and not row["path"].startswith("http"), "绝不能把外链当存储路径"
    assert row["bytes"] and row["bytes"] > 0
    assert row["origin"]["instance_id"] == iid
    assert float(row["cost"]["money"]) == pytest.approx(0.42)


async def test_recover_requeues_orphans_and_frees_stale_locks():
    configs, db_ids = await _make_instances("recover")
    iid = str(db_ids[0])
    async with session_factory()() as s:
        await s.execute(
            text(
                """
                INSERT INTO jobs (uuid, kind, state, priority, title, params, progress, output, cost, log,
                                  attempts, max_attempts, instance_id, created_at)
                VALUES (:u,'image','dispatching',100,'orphan','{}'::jsonb,'{}'::jsonb,'{}','{}'::jsonb,'[]'::jsonb,1,3,:i,now())
                """
            ),
            {"u": str(uuid.uuid4()), "i": db_ids[0]},
        )
        # 外键是真的（job_id 必须存在），所以用「任务已收口但锁还留着」这种真实残留场景
        await s.execute(
            text(
                """
                INSERT INTO jobs (uuid, kind, state, priority, title, params, progress, output, cost, log,
                                  attempts, max_attempts, instance_id, created_at)
                VALUES (:u,'image','succeeded',100,'done','{}'::jsonb,'{}'::jsonb,'{}','{}'::jsonb,'[]'::jsonb,1,3,:i,now())
                RETURNING id
                """
            ),
            {"u": str(uuid.uuid4()), "i": db_ids[0]},
        )
        stale_job = (await s.execute(text("SELECT max(id) FROM jobs WHERE state='succeeded'"))).scalar_one()
        await s.execute(text("INSERT INTO instance_locks (instance_id, job_id, acquired_at) VALUES (:i, :j, now())"), {"i": db_ids[0], "j": int(stale_job)})
        await s.commit()

    reg = StubRegistry(configs, {iid: {}})
    q = QueueDispatcher(reg, tick_s=0.02)
    requeued = await q.recover()
    assert requeued == 1
    async with session_factory()() as s:
        state = await s.scalar(text("SELECT state FROM jobs WHERE state='dispatching' LIMIT 1"))
        locks = await s.scalar(text("SELECT count(*) FROM instance_locks"))
    assert state is None, "dispatching 且无 prompt_id 的任务应回到队列"
    assert locks == 0, "失效锁未释放"


async def test_prompt_id_survives_restart():
    """拿到 prompt_id 就落库，是"关页面/重启都不影响收口"的前提。"""
    configs, db_ids = await _make_instances("pid")
    iid = str(db_ids[0])
    reg = StubRegistry(configs, {iid: {}})
    q = QueueDispatcher(reg, tick_s=0.02)
    (job_uuid,) = await _queue(q, iid, 1)
    await q.dispatch_once()
    await _wait_for_state(job_uuid, {"succeeded", "running"})
    async with session_factory()() as s:
        pid = (await s.execute(text("SELECT prompt_id FROM jobs WHERE uuid=:u"), {"u": job_uuid})).scalar_one()
    assert pid and str(uuid.UUID(pid)) == pid, "prompt_id 必须是规范小写 UUID"


async def test_reconcile_never_finishes_a_still_running_job():
    """对账循环每个 tick 都会喂进 RUNNING 快照，非终态必须只更新进度。

    这条是真机跑出来的事故：ComfyUI 还在算，jobs 表已经被写成 succeeded、
    output 是空数组 —— 用户看到一片绿，时间轴上却什么都没有。
    """
    configs, db_ids = await _make_instances("reconcile")
    iid = str(db_ids[0])
    reg = StubRegistry(configs, {iid: {"hold_running": True}})
    q = QueueDispatcher(reg, tick_s=0.02)
    (job_uuid,) = await _queue(q, iid, 1)
    await q.dispatch_once()
    await _wait_for_state(job_uuid, {"running"})

    await q.reconcile_running()
    await q.reconcile_running()

    async with session_factory()() as s:
        row = (
            await s.execute(
                text("SELECT state, output, finished_at, progress, error FROM jobs WHERE uuid=:u"), {"u": job_uuid}
            )
        ).mappings().one()
    await q.stop()

    assert row["state"] == "running", row["state"]
    assert row["finished_at"] is None
    assert list(row["output"]) == []
    assert row["error"] is None
    assert row["progress"]["stage"] == "采样 3/8", "进度该写进来"


async def test_success_without_any_file_is_a_failure():
    """「执行完成但零产物」不是成功。宁可显示失败，也不让空镜头混进成片。"""
    configs, db_ids = await _make_instances("empty-out")
    iid = str(db_ids[0])
    reg = StubRegistry(configs, {iid: {"empty_success": True}})
    q = QueueDispatcher(reg, tick_s=0.02)
    (job_uuid,) = await _queue(q, iid, 1)
    await q.dispatch_once()
    await _wait_for_state(job_uuid, {"failed", "queued"})
    async with session_factory()() as s:
        row = (await s.execute(text("SELECT state, error, output FROM jobs WHERE uuid=:u"), {"u": job_uuid})).mappings().one()
    assert row["state"] == "failed", row
    assert row["error"]["type"] == "no_output", row["error"]
    assert row["error"].get("retryable") is False, "零产物重跑一遍还是没有，重试只是白烧显存"
    assert list(row["output"]) == []
    async with session_factory()() as s:
        assert (await s.scalar(text("SELECT count(*) FROM media"))) == 0


async def test_priority_zero_jumps_the_queue():
    configs, db_ids = await _make_instances("prio")
    iid = str(db_ids[0])
    async with session_factory()() as s:
        for i, prio in enumerate((100, 0)):
            await s.execute(
                text(
                    """
                    INSERT INTO jobs (uuid, kind, state, priority, title, params, progress, output, cost, log,
                                      attempts, max_attempts, instance_id, created_at)
                    VALUES (:u,'image','queued',:p,:t,CAST(:g AS jsonb),'{}'::jsonb,'{}','{}'::jsonb,'[]'::jsonb,0,3,:i,now())
                    """
                ),
                {"u": str(uuid.uuid4()), "p": prio, "t": f"p{prio}-{i}", "i": db_ids[0], "g": '{"graph": %s}' % json.dumps(GRAPH)},
            )
        await s.commit()
    reg = StubRegistry(configs, {iid: {}})
    q = QueueDispatcher(reg, tick_s=0.02)
    await q.dispatch_once()
    await asyncio.sleep(0.2)  # _run 是独立任务，认领≠已提交
    client = reg.client(iid)
    assert len(client.submitted) == 1, "插队任务未被优先认领"
    async with session_factory()() as s:
        row = (await s.execute(text("SELECT priority, title FROM jobs WHERE prompt_id IS NOT NULL"))).mappings().one()
    assert row["priority"] == 0, f"手动插队(0)应优先于默认(100)，实得 {row['title']}"
