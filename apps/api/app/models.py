"""服务端表。

按 A 方案，创作数据（项目/角色/场景/镜头/渲染日志）在浏览器 IndexedDB 里，
服务端**不建 projects 表**；因此媒体只能用 project_key 软引用客户端项目 id，
而不是外键。这条约束是刻意的：换后端不会被创作数据绑死。

服务端持有的：users、refresh_tokens、api_tokens、gen_instances、llm_backends、workflows、skills、
jobs、instance_locks、media、script_versions、audit_log、app_settings。

`script_versions` 是唯一的例外：它存的是**生成出来的剧本正文本身**，属于创作数据，
但它存在的理由（版本历史与回收站要活到浏览器之外）恰恰是 IndexedDB 给不了的。
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
    # ScriptVersion 有一列就叫 text，会在类体里遮蔽同名函数；那列的 DDL 片段一律走这个别名
    text as sa_text,
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
    # 库里的工作流可以直接产音频（声音克隆那条），kind 必须有它自己的位置
    audio = "audio"


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


class ApiToken(Base):
    """智能体/CLI 用的长期凭据（`h3_at_...`）。

    为什么不复用 refresh_tokens：那条链路是给浏览器的，轮换、撤销、单飞刷新都跟着
    「一个人坐在屏幕前」的假设走；智能体要的是「一把能给别人、能单独吊销、能限权」的钥匙。
    只存 sha256，明文只在创建那一次出现。
    """

    __tablename__ = "api_tokens"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    # ["read"] / ["read","dispatch"] / ["read","dispatch","admin"]：只能收窄，不会越过属主的角色
    scopes: Mapped[list[str]] = mapped_column(ARRAY(String(16)), nullable=False, server_default=text("'{}'"))
    expires_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
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
    # 单次调用超时：留空按 scope 兜（local 1800 / cloud 300）。本机 27B 出 4096 token 要 8 分钟，
    # 写死 300 秒会把还在正常生成的调用掐成 ReadTimeout
    timeout_seconds: Mapped[int | None] = mapped_column(Integer)
    last_probe_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    last_probe_ok: Mapped[bool | None] = mapped_column(Boolean)
    last_error: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint("scope in ('local','cloud')", name="ck_llm_scope"),
        CheckConstraint("kind in ('ollama','openai_compat')", name="ck_llm_kind"),
        CheckConstraint("timeout_seconds BETWEEN 30 AND 7200", name="ck_llm_timeout_range"),
        Index("ix_llm_scope_default", "scope", "is_default"),
    )


class Workflow(Base, TimestampMixin):
    """共享工作流库。

    graph 是**本机可执行**的那一份：导入时已经把作者私有节点/云端专有节点换成核心等价节点
    （见 gen/local_adapt.py），改写清单存在 adaptations 里供界面展示；graph_original 留原始导出，
    换实例或补装节点后可以重新改写。ui_graph 保留可视化形态以便回导 ComfyUI。
    """

    __tablename__ = "workflows"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    uuid: Mapped[str] = mapped_column(String(36), unique=True, nullable=False, default=_uuid)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    tags: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default=text("'{}'"))
    family: Mapped[str] = mapped_column(String(24), nullable=False, server_default="video")
    # 任务种类按产出判：image | video | audio（自动选工作流的第一层过滤）
    task_kind: Mapped[str] = mapped_column(String(16), nullable=False, server_default="video")
    # 这张图吃什么：提示词/首帧/参考图/参考视频/音频/尺寸……（自动选工作流的匹配依据）
    signals: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    # 能在哪种实例上跑：local | cloud_runninghub | any
    executes_on: Mapped[str] = mapped_column(String(24), nullable=False, server_default="any")
    # 本机还缺的节点/权重（空才代表真的能跑）
    gaps: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    # 导入时做过的等价改写，逐条可审计
    adaptations: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    # 指向作者机器素材的控件：使用时必须由前端重新指定，不算缺东西
    pending_media: Mapped[list[str]] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    graph_original: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # 导入时那个文件叫什么。库里存的图是改写过的，文件名是用户认这条工作流的唯一线索
    source_file: Mapped[str | None] = mapped_column(String(200))
    # 允许被「按任务自动选」选中；关掉就只能手动指定
    auto_select: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    # 同一类任务有多条合格工作流时，数字大的先选
    priority: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("100"))
    # 是否在这台实例上真跑通过（试运行成功后由队列写回）
    verified_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
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

    __table_args__ = (
        Index("ix_workflows_family_name", "family", "name"),
        Index("ix_workflows_kind_auto", "task_kind", "auto_select"),
        CheckConstraint("task_kind in ('image','video','audio')", name="ck_workflows_task_kind"),
        CheckConstraint("executes_on in ('local','cloud_runninghub','any')", name="ck_workflows_executes_on"),
    )


class WorkflowModelBinding(Base, TimestampMixin):
    """一条工作流在**某一台实例**上默认用哪些权重。

    为什么不写进 workflows.graph 那一列：同一条图在本机用 4step Turbo、在自建 48G 云上
    用 8step，这是每台机器各自的事实，写进图里就等于「换一台机器就得换一份库条目」。
    也不写成 workflows 上的一个 jsonb：绑定要按实例独立读写、独立删除（实例被删要跟着清，
    换图要按节点剪位），按行才做得到，整列重写会把并发下的更新互相盖掉。

    workflow_ref 用字符串而不是外键：内置模板的图在后端现拼，它的默认权重同样要按实例分
    （存成 "builtin:h3_video"）。代价是删工作流时得记得一起删 —— 见 routes_workflows。
    """

    __tablename__ = "workflow_model_bindings"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    workflow_ref: Mapped[str] = mapped_column(String(64), nullable=False)
    instance_id: Mapped[int] = mapped_column(ForeignKey("gen_instances.id", ondelete="CASCADE"), nullable=False)
    # {"127.unet_name": "MiniMax_H3_...safetensors"}：键与任务参数 models 同形（节点号.字段名）
    overrides: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    # manual = 人在模型编辑里挑的；sync = 从别的实例同步过来的（值可能已被换成该台真有的文件）
    source: Mapped[str] = mapped_column(String(16), nullable=False, server_default="manual")
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("workflow_ref", "instance_id", name="uq_workflow_binding_ref_instance"),
        Index("ix_workflow_binding_instance", "instance_id"),
        CheckConstraint("source in ('manual','sync')", name="ck_workflow_binding_source"),
    )


class Skill(Base, TimestampMixin):
    """技能库：一段可复用的写法要求，挂在提示词框旁边随生成一起发给模型。

    和工作流库的分工：工作流管「这张图怎么算出来」（节点图与槽位），技能管「这段提示词
    要按什么写法来」。它是全局共享的库，不属于某个项目，所以留在服务端 —— 项目实体在
    浏览器 IndexedDB 里，而换一台机器还在的技能库才是库。

    实体里只存 skill_ids（见前端 Character/Scene/Shot 的 skillIds），正文在提交那一刻由
    后端读出来并进提示词槽：这样技能改了立刻生效，也不会把一份副本焊死在旧项目上。
    """

    __tablename__ = "skills"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    uuid: Mapped[str] = mapped_column(String(36), unique=True, nullable=False, default=_uuid)
    name: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    # 技能正文：发给模型的附加要求，原样存，不做任何加工
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # 挂在哪一类提示词框上：general 全都能选，其余只在对应环节出现
    stage: Mapped[str] = mapped_column(String(16), nullable=False, server_default="general")
    tags: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default=text("'{}'"))
    # manual = 页面上新建；imported = 从文件导进来（source 记它来自哪个文件/哪一批）
    origin: Mapped[str] = mapped_column(String(16), nullable=False, server_default="manual")
    source: Mapped[str | None] = mapped_column(String(200))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint("stage in ('general','script','asset','video')", name="ck_skills_stage"),
        CheckConstraint("origin in ('manual','imported')", name="ck_skills_origin"),
        Index("ix_skills_stage_name", "stage", "name"),
    )


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


class ScriptVersion(Base):
    """剧本正文的版本历史：一行 = 一次生成完成时的那份正文。

    为什么唯独剧本要进服务端：产物里只有它此前完全没有归宿 —— `POST /llm/run` 同步
    返回文本，前端直接盖掉 `Project.data.rawScript`，上一版当场消失。而「V1/V2 历史 +
    回收站 + 到期真删」这三条都必须活到浏览器之外，否则清一次 IndexedDB 就等于
    没做过回收站。项目实体本身仍在浏览器里（A 方案不变），这里只留生成的那一份。
    """

    __tablename__ = "script_versions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    uuid: Mapped[str] = mapped_column(String(36), unique=True, nullable=False, default=_uuid)
    # 与 media 同样的软引用：指向浏览器里的项目 id，服务端不持有项目结构
    project_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    # 组内序号从 1 开始，进了回收站也照样占着自己的号 —— 删掉 V2 之后 V3 仍然是 V3，
    # 否则用户对着「V2」恢复出来的东西会和他记忆里的 V2 不是同一份
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    # 这一版怎么来的：ai-write 续写/改写、storyboard 拆解+分镜、manual 切换前给手改兜底
    source: Mapped[str] = mapped_column(String(16), nullable=False, server_default="ai-write")
    text: Mapped[str] = mapped_column(Text, nullable=False)
    # 拆解结果快照 {script,characters,scenes,shots}；纯续写没有，所以给空对象而不是 NULL
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=sa_text("'{}'::jsonb"))
    # 工作正文来自哪一版由这个标记回答（媒体那边不用同类标记，因为媒体的「当前」是项目实体里的指针）
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=sa_text("false"))
    deleted_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    # 给用户看的时间。created_at 是审计列（这行何时进库），不能挪也不能改；但首次生成时补存的 V1
    # 与 V2 在同一次请求里插入，拿 created_at 当"这版正文何时写就"会让 V1 看起来比 V2 还晚，
    # 按时间排序的界面看到这种结果就直接不显示了 —— 所以显示时间必须是这一列。
    written_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("project_key", "seq", name="uq_script_versions_project_seq"),
        CheckConstraint("source IN ('ai-write','storyboard','manual')", name="ck_script_versions_source"),
        # 一个项目最多一个当前版；回收站里的行不占这个名额（恢复回来若撞上已有 current 由接口负责改判）
        Index(
            "uq_script_versions_one_current",
            "project_key",
            unique=True,
            postgresql_where=sa_text("is_current AND deleted_at IS NULL"),
        ),
        Index("ix_script_versions_project_created", "project_key", "created_at"),
    )


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
