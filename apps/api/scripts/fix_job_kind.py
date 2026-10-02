"""查/补 job_kind 枚举取值。

    PYTHONIOENCODING=utf-8 apps/api/.venv/Scripts/python.exe apps/api/scripts/fix_job_kind.py

ALTER TYPE ... ADD VALUE 在 alembic 的事务里「跑过」不等于留下了：同一事务回滚就一起没了。
这里用 AUTOCOMMIT 单独补，并把当前取值打出来核对。
"""
import asyncio
import sys

sys.path.insert(0, "F:/H3/apps/api")

from sqlalchemy import text  # noqa: E402

from app.db import engine  # noqa: E402

WANT = ["llm_chat", "image", "video", "video_chain", "upscale", "detect_shots", "assemble",
        "workflow_test", "audio"]
QUERY = "SELECT e.enumlabel FROM pg_type t JOIN pg_enum e ON e.enumtypid = t.oid ORDER by e.enumsortorder"


async def main() -> int:
    conn = await engine().connect()
    # AsyncConnection.execution_options 是 awaitable（返回连接本身），不是异步上下文管理器
    conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
    try:
        have = list((await conn.execute(text(QUERY))).scalars().all())
        print("当前 job_kind：", have)
        for v in WANT:
            if v in have:
                continue
            await conn.execute(text(f"ALTER TYPE job_kind ADD VALUE IF NOT EXISTS '{v}'"))
            print("  补上", v)
        print("补完 job_kind：", list((await conn.execute(text(QUERY))).scalars().all()))
    finally:
        await conn.close()
    return 0


sys.exit(asyncio.run(main()))
