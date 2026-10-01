"""工作流库加「按任务自动选」需要的字段。

导入时就把这张图能说清楚的话写下来：产什么（task_kind）、要什么（signals）、
这台实例上缺什么（gaps）、我们替它改了什么（adaptations）。
没有这几列，队列只能按内置模板建图，工作流库永远参与不了自动分派。
"""

from typing import Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "9c4f2b7ad105"
down_revision: Union[str, None] = "7b3e91c4a5d2"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


_JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    # job_kind 是 PG 原生枚举：加值必须单独走 ALTER TYPE，而且不能在同一事务里就被使用
    op.execute("ALTER TYPE job_kind ADD VALUE IF NOT EXISTS 'audio'")
    op.add_column("workflows", sa.Column("task_kind", sa.String(16), nullable=False, server_default="video"))
    op.add_column("workflows", sa.Column("signals", _JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")))
    op.add_column("workflows", sa.Column("executes_on", sa.String(24), nullable=False, server_default="any"))
    op.add_column("workflows", sa.Column("gaps", _JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")))
    op.add_column("workflows", sa.Column("adaptations", _JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")))
    op.add_column("workflows", sa.Column("pending_media", _JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")))
    op.add_column("workflows", sa.Column("graph_original", _JSONB, nullable=True))
    op.add_column("workflows", sa.Column("auto_select", sa.Boolean(), nullable=False, server_default=sa.text("true")))
    op.add_column("workflows", sa.Column("priority", sa.Integer(), nullable=False, server_default=sa.text("100")))
    op.add_column("workflows", sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_workflows_kind_auto", "workflows", ["task_kind", "auto_select"])
    op.create_check_constraint("ck_workflows_task_kind", "workflows",
                               "task_kind in ('image','video','audio')")
    op.create_check_constraint("ck_workflows_executes_on", "workflows",
                               "executes_on in ('local','cloud_runninghub','any')")


def downgrade() -> None:
    op.drop_constraint("ck_workflows_executes_on", "workflows", type_="check")
    op.drop_constraint("ck_workflows_task_kind", "workflows", type_="check")
    op.drop_index("ix_workflows_kind_auto", table_name="workflows")
    for col in ("verified_at", "priority", "auto_select", "graph_original",
                "pending_media", "adaptations", "gaps", "executes_on", "signals", "task_kind"):
        op.drop_column("workflows", col)
