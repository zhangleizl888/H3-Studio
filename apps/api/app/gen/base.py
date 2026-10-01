"""生成后端的统一抽象。

分派依据是**协议**，不是服务商：
  - comfy_native —— 本地 ComfyUI、cloudflared 后的自建云、RunningHub 的 /proxy/{key} 网关，共用一个实现
  - rh_task      —— RunningHub 专有任务 API（排队、按秒计费、显存档位、只给状态不给百分比）
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Protocol, runtime_checkable


class JobState(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"

    @property
    def terminal(self) -> bool:
        return self in (JobState.SUCCEEDED, JobState.FAILED, JobState.CANCELED)


@dataclass
class OutputRef:
    """一个产物在来源实例上的坐标。"""

    filename: str
    subfolder: str = ""
    type: str = "output"
    node_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"filename": self.filename, "subfolder": self.subfolder, "type": self.type, "node_id": self.node_id}


@dataclass
class Submission:
    """提交后拿到的句柄，是断点续跑与取消的锚点。"""

    job_ref: str  # 原生=pre-generated prompt_id；rh_task=taskId
    client_id: str | None = None
    queue_number: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class Progress:
    value: float | None = None
    max: float | None = None
    node: str | None = None
    node_title: str | None = None
    # 有些平台根本不给百分比，界面上必须明说，不能画假进度条
    percentage_unavailable: bool = False
    stage: str | None = None


@dataclass
class JobSnapshot:
    state: JobState
    progress: Progress = field(default_factory=Progress)
    outputs: list[OutputRef] = field(default_factory=list)
    error: dict[str, Any] | None = None
    queue_position: int | None = None
    cost: dict[str, Any] | None = None


@dataclass
class Capabilities:
    reachable: bool
    protocol: str
    comfy_version: str | None = None
    node_count: int | None = None
    gpu: str | None = None
    vram_total_gb: float | None = None
    vram_free_gb: float | None = None
    h3_nodes: dict[str, bool] = field(default_factory=dict)
    missing_models: list[str] = field(default_factory=list)
    # 这个协议能不能给真实进度
    supports_progress: bool = True
    supports_websocket: bool = True
    raw: dict[str, Any] = field(default_factory=dict)


class GenError(Exception):
    """带处置语义的错误：队列层据此决定「退避重试」还是「判失败」。"""

    def __init__(
        self,
        message: str,
        *,
        kind: str = "unknown",
        retryable: bool = False,
        node_id: str | None = None,
        node_type: str | None = None,
        code: str | None = None,
        traceback_tail: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.kind = kind
        self.retryable = retryable
        self.node_id = node_id
        self.node_type = node_type
        self.code = code
        self.traceback_tail = traceback_tail

    def as_dict(self) -> dict[str, Any]:
        return {
            "type": self.kind,
            "message": self.message,
            "retryable": self.retryable,
            "node_id": self.node_id,
            "node_type": self.node_type,
            "code": self.code,
            "traceback_tail": self.traceback_tail,
        }


@runtime_checkable
class GenClient(Protocol):
    """队列层只认这个协议。"""

    protocol: str

    async def probe(self) -> Capabilities: ...

    async def upload(self, path, *, type: str = "input", subfolder: str = "") -> str: ...

    async def submit(self, graph: dict[str, Any], *, client_id: str, job_ref: str | None = None) -> Submission: ...

    async def snapshot(self, sub: Submission) -> JobSnapshot: ...

    def events(self, sub: Submission) -> AsyncIterator[tuple[str, dict[str, Any]]]: ...

    async def cancel(self, sub: Submission) -> bool: ...

    async def fetch_output(self, ref: OutputRef) -> bytes: ...

    async def close(self) -> None: ...
