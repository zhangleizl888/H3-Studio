"""工作流按实例的「默认权重」绑定，外加导入文件名。

workflows.graph 里写死的是导入那台机器上的文件名。同一条图换到另一台 ComfyUI
（自建 48G、RunningHub 原生代理）上，权重目录往往既不同精度也不同命名，
生成时只能靠 comfy_native.align_graph 去猜一个 —— 猜这件事本身就有过事故。
这张表让「这条工作流在这台机器上用哪个底模/编码器/VAE/LoRA」成为一条显式记录，
模型编辑弹窗写它，队列派发时读它，删实例时按外键级联清掉。

source_file 是顺手补的：库里存的图是改写过的，用户认这条工作流只能靠当初那个文件名。
"""

from typing import Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "f3d81a6c07be"
down_revision: Union[str, None] = "c8a1f5d2b904"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


_JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.add_column("workflows", sa.Column("source_file", sa.String(length=200), nullable=True))
    op.create_table(
        "workflow_model_bindings",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("workflow_ref", sa.String(length=64), nullable=False),
        sa.Column("instance_id", sa.BigInteger(), nullable=False),
        sa.Column("overrides", _JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("source", sa.String(length=16), nullable=False, server_default="manual"),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["instance_id"], ["gen_instances.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workflow_ref", "instance_id", name="uq_workflow_binding_ref_instance"),
        sa.CheckConstraint("source in ('manual','sync')", name="ck_workflow_binding_source"),
    )
    op.create_index("ix_workflow_binding_instance", "workflow_model_bindings", ["instance_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_workflow_binding_instance", table_name="workflow_model_bindings")
    op.drop_table("workflow_model_bindings")
    op.drop_column("workflows", "source_file")
