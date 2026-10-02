"""冻结产物（h3-backend.exe / h3-backend）的唯一入口。

windowed 模式下 `sys.stdout` / `sys.stderr` 是 None，而 `setup_logging` 用的是
`StreamHandler(sys.stdout)`、uvicorn 也往 stdout 写 —— 不接住就会在第一条日志上炸，
而且炸得没有痕迹（logging 内部吞异常）。所以这里先把标准流接到日志文件。

Electron 壳拉起时是带着重定向句柄的（stdout 不是 None），这条路径不会触发；
真正走它的是「用户直接双击后端 exe」这种排查场景。
"""

from __future__ import annotations

import sys

from app import runtime
from app.desktop import main


def run() -> int:
    runtime.ensure_dirs()
    if sys.stdout is None or sys.stderr is None:
        sink = (runtime.logs_dir() / "desktop-backend.log").open("a", buffering=1, encoding="utf-8", errors="replace")
        if sys.stdout is None:
            sys.stdout = sink  # type: ignore[assignment]
        if sys.stderr is None:
            sys.stderr = sink  # type: ignore[assignment]
    return main(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(run())
