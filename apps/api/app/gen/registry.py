"""实例注册表：instance_id → GenClient。

两条引导路径：无库模式读 instances_file，有库模式读 gen_instances。
上层（队列、路由）只认 GenClient 协议，所以换引导源不影响其余代码。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ..config import InstanceConfig, Settings, get_settings
from ..logging_setup import get_logger
from .base import Capabilities, GenClient
from .comfy_native import ComfyNativeClient

if TYPE_CHECKING:  # 只在类型层面依赖 ORM，运行时不把 db 层拉进 gen 包
    from ..models import GenInstance

log = get_logger("gen.registry")


def config_from_row(row: GenInstance) -> InstanceConfig:
    """gen_instances 一行 → 运行期配置。apiKey 只在这里解密，解密结果绝不出网。"""
    from ..crypto import decrypt, from_b64

    return InstanceConfig(
        id=str(row.id),
        name=row.name,
        protocol=row.protocol,  # type: ignore[arg-type]
        placement=row.placement,  # type: ignore[arg-type]
        base_url=row.base_url,
        ws_url=row.ws_url,
        api_key=decrypt(from_b64(row.api_key_enc)),
        site=row.site or "cn",  # type: ignore[arg-type]
        instance_type=row.instance_type,  # type: ignore[arg-type]
        retain_seconds=row.retain_seconds,
        is_default=bool(row.is_default),
        local_output_root=Path(row.local_output_root) if row.local_output_root else None,
        tunnel_name=row.tunnel_name,
    )


def _client_key(cfg: InstanceConfig) -> tuple:
    """参与构造客户端的字段。改这些才需要重连，只改名字不用。"""
    return (cfg.protocol, cfg.base_url, cfg.ws_url, cfg.api_key, cfg.local_output_root, cfg.timeout_s)


class InstanceRegistry:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._clients: dict[str, GenClient] = {}
        self._configs: dict[str, InstanceConfig] = {}
        for cfg in self.settings.bootstrap_instances():
            self._configs[cfg.id] = cfg

    @classmethod
    async def from_database(cls, settings: Settings) -> "InstanceRegistry":
        """从 gen_instances 表引导。表不存在时明确失败，而不是静默退回文件引导。"""
        from sqlalchemy import select

        from ..db import session_factory
        from ..models import GenInstance

        obj = cls(settings)
        async with session_factory()() as s:
            try:
                rows = (await s.execute(select(GenInstance).order_by(GenInstance.id))).scalars().all()
            except Exception as exc:
                raise RuntimeError(
                    f"读 gen_instances 失败（{type(exc).__name__}）。先执行 alembic upgrade head 建表"
                ) from exc
        # 有库时文件引导必须清空：instances.dev.json 里的 id（如 inst_local）不是合法的
        # gen_instances 主键，留着会让派发循环对着它 int() 崩掉，还会多算一个并行槽位。
        obj._configs.clear()
        for row in rows:
            cfg = config_from_row(row)
            obj._configs[cfg.id] = cfg
        return obj

    @property
    def ids(self) -> list[str]:
        return list(self._configs)

    async def upsert(self, cfg: InstanceConfig) -> None:
        """配置写库后同步进来。只有影响连接的字段变了才重建客户端。

        无脑关掉旧客户端会打断正在跑的任务：那条 WebSocket 属于同一个实例，
        而我们可能只是改了名字或默认标记。
        """
        old = self._configs.get(cfg.id)
        self._configs[cfg.id] = cfg
        if old is not None and _client_key(old) == _client_key(cfg):
            return
        stale = self._clients.pop(cfg.id, None)
        if stale is not None:
            await stale.close()

    async def drop(self, instance_id: str) -> None:
        self._configs.pop(instance_id, None)
        stale = self._clients.pop(instance_id, None)
        if stale is not None:
            await stale.close()

    async def sync_rows(self, rows) -> None:
        """用库里的全量行覆盖内存配置。

        is_default / circuit_open 这类标记是「连带」改掉的（把 A 设成默认会清掉 B），
        单条 upsert 看不见连带，留着就会出现「内存里还是默认、库里已经不是」的不一致。
        """
        seen: set[str] = set()
        for row in rows:
            cfg = config_from_row(row)
            seen.add(cfg.id)
            await self.upsert(cfg)
        for gone in [i for i in self._configs if i not in seen]:
            await self.drop(gone)

    def config(self, instance_id: str) -> InstanceConfig:
        if instance_id not in self._configs:
            raise KeyError(f"未登记的实例：{instance_id}（已登记：{', '.join(self._configs) or '无'}）")
        return self._configs[instance_id]

    def client(self, instance_id: str) -> GenClient:
        if instance_id in self._clients:
            return self._clients[instance_id]
        cfg = self.config(instance_id)
        client = self._build(cfg)
        self._clients[instance_id] = client
        return client

    def _build(self, cfg: InstanceConfig) -> GenClient:
        if cfg.protocol == "comfy_native":
            return ComfyNativeClient(
                cfg.base_url,
                api_key=cfg.api_key,
                timeout_s=cfg.timeout_s,
                ws_backoff_max_s=self.settings.ws_backoff_max_s,
                local_output_root=cfg.local_output_root,
            )
        # rh_task 客户端在 M1b 实现；这里明确拒绝，不能假装支持
        raise NotImplementedError(
            f"实例 {cfg.id} 声明 protocol=rh_task（RunningHub 专有任务 API），该客户端尚未实现。"
            "暂时请改用 RunningHub 的原生代理接法：base_url 填 https://www.runninghub.cn/proxy/{apiKey} 并把 protocol 设为 comfy_native。"
        )

    async def probe_all(self) -> dict[str, Capabilities]:
        out: dict[str, Capabilities] = {}
        for cid in self.ids:
            try:
                out[cid] = await self.client(cid).probe()
            except Exception as exc:  # 一个实例连不上不该拖垮整个探活
                log.warning("实例 %s 探活异常：%s", cid, exc)
                out[cid] = Capabilities(reachable=False, protocol=self.config(cid).protocol, raw={"error": str(exc)[:200]})
        return out

    async def close(self) -> None:
        for client in self._clients.values():
            await client.close()
        self._clients.clear()
