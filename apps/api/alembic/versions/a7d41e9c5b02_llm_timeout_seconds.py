"""llm_backends 加 timeout_seconds：单次文本调用的超时，按后端各配一份。

以前这个值写死在 `LlmSpec.timeout_s = 300`，谁都改不了。本机 27B 是单槽串行，
实测 8 t/s 左右，script_parse 的 4096 token 预算要跑 8 分钟 —— 300 秒必然把
**还在正常生成**的调用掐成 502 ReadTimeout，而且报错里连"几秒超时"都不说。
默认留空：路由按 scope 兜（local 1800 / cloud 300），已有后端行为不变。
"""

from typing import Union

from alembic import op
import sqlalchemy as sa

revision: str = "a7d41e9c5b02"
down_revision: Union[str, None] = "e5b0c81f3d7a"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    op.add_column("llm_backends", sa.Column("timeout_seconds", sa.Integer(), nullable=True))
    op.create_check_constraint("ck_llm_timeout_range", "llm_backends", "timeout_seconds BETWEEN 30 AND 7200")


def downgrade() -> None:
    op.drop_constraint("ck_llm_timeout_range", "llm_backends", type_="check")
    op.drop_column("llm_backends", "timeout_seconds")
