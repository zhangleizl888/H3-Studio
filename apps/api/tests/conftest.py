"""测试夹具：隔离事件循环与连接池。

SQLAlchemy 的异步引擎是模块级单例，而 pytest-asyncio 默认每个用例一个新事件循环。
不在用例结束时重置引擎，第二个用例就会拿到绑在已关闭循环上的连接，
报「Event loop is closed」或 asyncpg 内部 'NoneType' object has no attribute 'send'。
"""

from __future__ import annotations

import pytest

from app.db import reset_engine


@pytest.fixture(autouse=True)
def _reset_engine_between_tests():
    reset_engine()
    yield
    reset_engine()
