"""运行配置（env 前缀 H3_，读 apps/api/.env）。

database_url 为空时后端只跑「无库模式」：实例从 instances_file 引导，
用户与队列功能不可用。表建好后实例的权威来源是 gen_instances，
instances_file 只作为开发期的引导源和迁移源。
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

Placement = Literal["local", "cloud_self", "cloud_runninghub"]
Protocol = Literal["comfy_native", "rh_task"]


class InstanceConfig(BaseModel):
    """与 gen_instances 表一一对应的运行期实例配置。

    extra="forbid"：字段名写错时宁可启动就炸。之前这里是默认的 ignore，
    from_database 传了 retain_seconds / tunnel_name 两个当时不存在的字段，
    被静默丢掉，而队列层一直以为拿到的是完整配置。
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    protocol: Protocol = "comfy_native"
    placement: Placement = "local"
    base_url: str
    ws_url: str | None = None
    api_key: str | None = None
    site: Literal["cn", "global"] = "cn"
    instance_type: Literal["default", "plus", "ultra"] | None = None
    # RunningHub 按秒计费：到点必须自动释放实例，别让浏览器关页面变成一直在烧钱
    retain_seconds: int | None = None
    local_output_root: Path | None = None
    tunnel_name: str | None = None
    is_default: bool = False
    timeout_s: float = 1800.0


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="H3_", extra="ignore")

    host: str = "127.0.0.1"
    port: int = 8788

    # 数据库留给 M0：连接串为空时后端只跑「无库」模式（健康检查 + 实例客户端）
    database_url: str | None = None
    media_root: Path = Path("F:/H3/data/media")
    tmp_root: Path = Path("F:/H3/data/tmp")

    # 留空表示"首次启动时自动生成并写进 .secret.key 同级的 .jwt_key 文件"。
    # 之前这里是个 23 字节的字面量，低于 HS256 建议的 32 字节，pyjwt 会直接告警。
    jwt_secret: str = ""
    jwt_ttl_s: int = 900
    refresh_ttl_s: int = 30 * 86_400

    # 引导用实例文件；表建好后这个就是迁移源
    instances_file: Path = Path("F:/H3/apps/api/instances.dev.json")

    # 派发闸门：出图/出片要常驻十 GB 级权重，空闲不足就别提交
    # （本机实测：27B 文本模型占满 24GB 卡时，Qwen-Image 卡在 Model Initializing 28 分钟）
    min_free_vram_gb: float = 12.0

    log_level: str = "INFO"
    comfy_poll_interval_s: float = 2.0
    ws_backoff_max_s: float = 30.0

    # 单卡仲裁：本地实例开跑前先把文本模型进程让开（见 gpu_arbiter 的实测依据）
    gpu_arbiter: bool = True

    # 生成回收站：软删到的版本满这么多天才真删文件。100 天是留给用户反悔的，
    # 但 F 盘只剩 ~494 GB（总 4.8T 已用 90%），所以到期必须自动兑现，不能等人手动清。
    trash_retention_days: int = 100
    trash_purge_interval_s: int = 6 * 3600
    # 单次扫描上限：首趟可能积压几万行，不限会把停止流程卡住
    trash_purge_limit: int = 500

    def resolved_jwt_secret(self) -> str:
        if self.jwt_secret:
            if len(self.jwt_secret.encode()) < 32:
                raise RuntimeError("H3_JWT_SECRET 不足 32 字节，不能作为 HS256 密钥")
            return self.jwt_secret
        from .crypto import load_or_create_secret

        return load_or_create_secret("jwt_key").decode()

    def bootstrap_instances(self) -> list[InstanceConfig]:
        if not self.instances_file.exists():
            return []
        raw = json.loads(self.instances_file.read_text(encoding="utf-8"))
        return [InstanceConfig(**item) for item in raw]


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.media_root.mkdir(parents=True, exist_ok=True)
    s.tmp_root.mkdir(parents=True, exist_ok=True)
    return s
