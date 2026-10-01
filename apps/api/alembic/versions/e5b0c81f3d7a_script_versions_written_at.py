"""script_versions 加 written_at：给版本列表用的"这版正文何时写就"。

只有 created_at 是不够的：首次生成时会把用户已有的正文补存成 V1，它和新产生的 V2
在同一次请求里插入，于是 V1 的 created_at 反而 ≥ V2 —— 按时间排序的界面看到
"V1 比 V2 晚"就把它整个不显示了（用户报的"剧本 V1 不显示时间"根因在这）。
created_at 保持审计语义不动，显示时间走这一列。
"""

from typing import Union

from alembic import op
import sqlalchemy as sa

revision: str = "e5b0c81f3d7a"
down_revision: Union[str, None] = "d1a7f3c92b64"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    # 先可空建列、回填、再收紧：已有行没有"写就时间"可考，只能认定它等于入库时间
    op.add_column("script_versions", sa.Column("written_at", sa.DateTime(timezone=True), nullable=True))
    op.execute("UPDATE script_versions SET written_at = created_at WHERE written_at IS NULL")
    op.alter_column(
        "script_versions",
        "written_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    )


def downgrade() -> None:
    op.drop_column("script_versions", "written_at")
