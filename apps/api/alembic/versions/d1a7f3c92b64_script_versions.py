"""剧本正文的版本历史表。

在此之前服务端完全不持有剧本：`POST /api/llm/run` 同步把正文交给前端，前端直接盖掉
`Project.data.rawScript`，上一版当场消失。而「V1/V2 历史 → 回收站 → 满 N 天真删」
这三条都必须活到浏览器之外，否则清一次 IndexedDB 就等于没做过回收站。

序号（seq）不复用：进了回收站的版本照样占着自己的号，界面才不会出现
「删掉 V2 之后 V3 变成 V2」这种和用户对不上账的显示。
"""

from typing import Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "d1a7f3c92b64"
down_revision: Union[str, None] = "9c4f2b7ad105"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    op.create_table(
        "script_versions",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("uuid", sa.String(length=36), nullable=False),
        # 软引用浏览器里的项目 id，和 media.project_key 同一套路
        sa.Column("project_key", sa.String(length=64), nullable=False),
        sa.Column("owner_id", sa.BigInteger(), nullable=True),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False, server_default="ai-write"),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column(
            "snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("uuid"),
        sa.UniqueConstraint("project_key", "seq", name="uq_script_versions_project_seq"),
        sa.CheckConstraint("source IN ('ai-write','storyboard','manual')", name="ck_script_versions_source"),
    )
    op.create_index(
        "uq_script_versions_one_current",
        "script_versions",
        ["project_key"],
        unique=True,
        postgresql_where=sa.text("is_current AND deleted_at IS NULL"),
    )
    op.create_index("ix_script_versions_project_key", "script_versions", ["project_key"], unique=False)
    op.create_index("ix_script_versions_owner_id", "script_versions", ["owner_id"], unique=False)
    op.create_index("ix_script_versions_deleted_at", "script_versions", ["deleted_at"], unique=False)
    op.create_index("ix_script_versions_project_created", "script_versions", ["project_key", "created_at"], unique=False)


def downgrade() -> None:
    # 表上的索引随表一起走，不需要逐个 drop
    op.drop_table("script_versions")
