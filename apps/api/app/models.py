"""服务端 9 张表。

按 A 方案，创作数据（项目/角色/场景/镜头/渲染日志）在浏览器 IndexedDB 里，
服务端**不建 projects 表**；因此媒体只能用 project_key 软引用客户端项目 id，
而不是外键。这条约束是刻意的：换后端不会被创作数据绑死。

服务端持有的 9 张：users、refresh_tokens、gen_instances、llm_backends、
workflows、jobs、instance_locks、media、audit_log。
"""

from __future__ import annotations

import datetime as dt
import enum
import uuid
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, ENUM, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class Role(str, enum.Enum):
    admin = "admin"
    editor = "editor"
    viewer = "viewer"


class JobState(str, enum.Enum):
    queued = "queued"
    dispatching = "dispatching"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    canceled = "canceled"


class JobKind(str, enum.Enum):
    llm_chat = "llm_chat"
    image = "image"
    video = "video"
    video_chain = "video_chain"
    upscale = "upscale"
    detect_shots = "detect_shots"
    assemble = "assemble"
    workflow_test = "workflow_test"


JOB_STATE_PG = ENUM(JobState, name="job_state", created_by_metadata=True)
JOB_KIND_PG = ENUM(JobKind, name="job_kind", created_by_metadata=True)
ROLE_PG = ENUM(Role, name="user_role", created_by_metadata=True)


class TimestampMixin:
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    email: Mapped[str | None] = mapped_column(String(256), unique=True)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[Role] = mapped_column(ROLE_PG, nullable=False, server_default=Role.editor.value)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    # 并发上限与每日消费上限：RunningHub 按秒计费，没有这两条就会失控
    max_concurrent_jobs: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("2"))
    daily_money_limit: Mapped[float | None] = mapped_column(Numeric(12, 2))
    preferences: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    last_login_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (CheckConstraint("max_concurrent_jobs >= 0", name="ck_users_concurrent_nonneg"),)


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    replaced_by: Mapped[int | None] = mapped_column(ForeignKey("refresh_tokens.id", ondelete="SET NULL"))
    user_agent: Mapped[str | None] = mapped_column(Text)
    ip: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class GenInstance(Base, TimestampMixin):
    """生成实例。分派看 protocol，不看服务商。"""

    __tablename__ = "gen_instances"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    protocol: Mapped[str] = mapped_column(String(32), nullable=False)  # comfy_native | rh_task
    placement: Mapped[str] = mapped_column(String(32), nullable=False)  # local | cloud_self | cloud_runninghub
    base_url: Mapped[str] = mapped_column(Text, nullable=False)
    ws_url: Mapped[str | None] = mapped_column(Text)
    # Fernet 密文。绝不返回给前端，绝不写日志（见 logging_setup.redact）
    api_key_enc: Mapped[bytes | None] = mapped_column(String(512))
    auth_style: Mapped[str] = mapped_column(String(24), nullable=False, server_default="none")
    site: Mapped[str | None] = mapped_column(String(16))  # cn | global
    instance_type: Mapped[str | None] = mapped_column(String(16))  # default | plus | ultra
    retain_seconds: Mapped[int | None] = mapped_column(Integer)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    local_output_root: Mapped[str | None] = mapped_column(Text)
    tunnel_name: Mapped[str | None] = mapped_column(String(128))
    capabilities: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    quota: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    cost_total: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    # 余额不足时熔断：置灰该实例，不再派发，避免继续烧钱
    circuit_open: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    last_probe_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    last_probe_ok: Mapped[bool | None] = mapped_column(Boolean)
    last_error: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint("protocol in ('comfy_native','rh_task')", name="ck_gen_protocol"),
        CheckConstraint("placement in ('local','cloud_self','cloud_runninghub')", name="ck_gen_placement"),
        CheckConstraint("retain_seconds is null or (retain_seconds between 10 and 180)", name="ck_gen_retain"),
        Index("ix_gen_placement_default", "placement", "is_default"),
    )


class LlmBackend(Base, TimestampMixin):
    """文本模型后端。scope=local / cloud 是设置页分组的唯一依据。"""

    __tablename__ = "llm_backends"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    scope: Mapped[str] = mapped_column(String(16), nullable=False)  # local | cloud
    kind: Mapped[str] = mapped_column(String(24), nullable=False)  # ollama | openai_compat
    base_url: Mapped[str] = mapped_column(Text, nullable=False)
    api_key_enc: Mapped[bytes | None] = mapped_column(String(512))
    chat_path: Mapped[str] = mapped_column(String(64), nullable=False, server_default="/chat/completions")
    # ollama 原生口是 NDJSON，openai 兼容口是 SSE，解析器不能共用
    stream_style: Mapped[str] = mapped_column(String(12), nullable=False, server_default="sse")
    capabilities: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    last_probe_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    last_probe_ok: Mapped[bool | None] = mapped_column(Boolean)
    last_error: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint("scope in ('local','cloud')", name="ck_llm_scope"),
        CheckConstraint("kind in ('ollama','openai_compat')", name="ck_llm_kind"),
        Index("ix_llm_scope_default", "scope", "is_default"),
    )


class Workflow(Base, TimestampMixin):
    """共享工作流库。graph 存归一化后的 API 格式；ui_graph 保留原始导出以便回导。"""

    __tablename__ = "workflows"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    uuid: Mapped[str] = mapped_column(String(36), unique=True, nullable=False, default=_uuid)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    tags: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default=text("'{}'"))
    family: Mapped[str] = mapped_column(String(24), nullable=False, server_default="video")
    source_format: Mapped[str] = mapped_column(String(8), nullable=False)  # api | ui
    graph: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    ui_graph: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    slots: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    requirements: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    # /object_info 变了（升级、装节点）就要重扫槽位与校验
    object_info_hash: Mapped[str | None] = mapped_column(String(64))
    is_builtin: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (Index("ix_workflows_family_name", "family", "name"),)


class Media(Base):
    """生成产物。只存路径与元数据，绝不存 base64，也绝不存 RunningHub 的外链（约 1 天过期）。"""

    __tablename__ = "media"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    uuid: Mapped[str] = mapped_column(String(36), unique=True, nullable=False, default=_uuid)
    # 软引用：项目数据在浏览器 IndexedDB 里，服务端不持有项目实体
    project_key: Mapped[str | None] = mapped_column(String(64), index=True)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    role: Mapped[str | None] = mapped_column(String(32))
    ref_id: Mapped[str | None] = mapped_column(String(128))
    path: Mapped[str] = mapped_column(Text, nullable=False)  # 相对 media_root
    thumb_path: Mapped[str | None] = mapped_column(Text)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    fps: Mapped[float | None] = mapped_column(Numeric(6, 3))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    bytes_: Mapped[int | None] = mapped_column("bytes", BigInteger)
    mime: Mapped[str | None] = mapped_column(String(128))
    sha256: Mapped[str | None] = mapped_column(String(64))
    origin: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    # 软删除：项目删除后 7 天才真清，期间可撤销
    deleted_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (Index("ix_media_project_role", "project_key", "role", "ref_id"),)


class Job(Base):
    """任务。prompt_id 一旦拿到就落库，因此刷新/换设备/关页面都不影响继续跑。"""

    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    uuid: Mapped[str] = mapped_column(String(36), unique=True, nullable=False, default=_uuid)
    project_key: Mapped[str | None] = mapped_column(String(64), index=True)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    kind: Mapped[JobKind] = mapped_column(JOB_KIND_PG, nullable=False)
    state: Mapped[JobState] = mapped_column(JOB_STATE_PG, nullable=False, server_default=JobState.queued.value)
    # 小的先跑；用户手动插队用 0
    priority: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("100"))
    title: Mapped[str] = mapped_column(Text, nullable=False)
    instance_id: Mapped[int | None] = mapped_column(ForeignKey("gen_instances.id", ondelete="SET NULL"))
    llm_backend_id: Mapped[int | None] = mapped_column(ForeignKey("llm_backends.id", ondelete="SET NULL"))
    workflow_id: Mapped[int | None] = mapped_column(ForeignKey("workflows.id", ondelete="SET NULL"))
    prompt_id: Mapped[str | None] = mapped_column(String(36), unique=True)
    client_id: Mapped[str | None] = mapped_column(String(36))
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    progress: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    queue_pos: Mapped[int | None] = mapped_column(Integer)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("3"))
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    output: Mapped[list[int]] = mapped_column(ARRAY(BigInteger), nullable=False, server_default=text("'{}'"))
    cost: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    log: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        # 派发循环就靠这个部分索引：只扫没跑完的，且按优先级+入队顺序
        Index(
            "ix_jobs_pick",
            "instance_id",
            "priority",
            "id",
            unique=False,
            postgresql_where=text("state IN ('queued','dispatching')"),
        ),
        Index("ix_jobs_owner_recent", "owner_id", "created_at"),
        Index("ix_jobs_project_kind", "project_key", "kind", "created_at"),
        CheckConstraint("attempts >= 0", name="ck_jobs_attempts_nonneg"),
    )

    def append_log(self, level: str, msg: str) -> None:
        """环形日志，最多 200 条，防止长任务把 JSONB 撑爆。"""
        entries = list(self.log or [])
        entries.append({"ts": dt.datetime.now(dt.timezone.utc).isoformat(), "level": level, "msg": msg})
        self.log = entries[-200:]


class InstanceLock(Base):
    """每个实例同时只允许一个前台任务（ComfyUI 本身一次只跑一个；RH /proxy 也只支持并发 1）。"""

    __tablename__ = "instance_locks"

    instance_id: Mapped[int] = mapped_column(ForeignKey("gen_instances.id", ondelete="CASCADE"), primary_key=True)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"))
    acquired_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    actor: Mapped[str | None] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    target: Mapped[str | None] = mapped_column(String(200))
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    ip: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)

    __table_args__ = (UniqueConstraint("id", name="uq_audit_id"),)


class AppSetting(Base):
    """全局键值设置。目前只装「每个用途用哪个文本后端 + 哪个模型」。

    为什么不塞进 llm_backends 的某一列：那是「用途→后端」的映射，跨行，
    放在任意一行后端记录上都会让「删掉那行」顺手删掉一份全局配置。
    """

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
