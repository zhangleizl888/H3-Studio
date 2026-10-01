"""生成后端协议层。按协议分派，不按服务商分派。"""

from .base import (
    Capabilities,
    GenClient,
    GenError,
    JobSnapshot,
    JobState,
    OutputRef,
    Progress,
    Submission,
)
from .comfy_native import H3_NODES, ComfyNativeClient
from .registry import InstanceRegistry

__all__ = [
    "Capabilities",
    "ComfyNativeClient",
    "GenClient",
    "GenError",
    "H3_NODES",
    "InstanceRegistry",
    "JobSnapshot",
    "JobState",
    "OutputRef",
    "Progress",
    "Submission",
]
