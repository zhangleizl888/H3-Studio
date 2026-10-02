"""技能库表。

「技能」= 一段可复用的写法要求，挂在角色/场景提示词框、视频提示词框与剧本助手输入框旁边，
选中之后随生成一起发给模型。放在服务端而不是 IndexedDB：它是跨项目共享的库，
而项目实体按 A 方案留在浏览器里 —— 存在项目里的那些叫草稿，不叫库。

name 建了唯一约束：导入一批技能时按名字 upsert，重导一次是刷新而不是越堆越多。
"""

from typing import Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "c8a1f5d2b904"
down_revision: Union[str, None] = "b6f2c90a41de"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    op.create_table(
        "skills",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("uuid", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("stage", sa.String(length=16), nullable=False, server_default="general"),
        sa.Column("tags", postgresql.ARRAY(sa.Text()), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("origin", sa.String(length=16), nullable=False, server_default="manual"),
        sa.Column("source", sa.String(length=200), nullable=True),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("uuid"),
        sa.UniqueConstraint("name", name="uq_skills_name"),
        sa.CheckConstraint("stage in ('general','script','asset','video')", name="ck_skills_stage"),
        sa.CheckConstraint("origin in ('manual','imported')", name="ck_skills_origin"),
    )
    op.create_index("ix_skills_stage_name", "skills", ["stage", "name"], unique=False)


def downgrade() -> None:
    op.drop_table("skills")
