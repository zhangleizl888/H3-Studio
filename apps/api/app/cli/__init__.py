"""`h3` 命令行包。

`python -m app.cli ...`（MCP 配置里用的就是这个）与控制台脚本 `h3` 等价。
"""

from __future__ import annotations

from .main import main

__all__ = ["main"]
