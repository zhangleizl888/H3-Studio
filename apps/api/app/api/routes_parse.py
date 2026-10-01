"""剧本文件解析：把外部稿子读成纯文本，供剧本助手当「换稿」提案。

只读不写：这里不落盘、不建 media 行 —— 解析结果停在「待确认」，
进不进编辑器由人决定（见 apps/web/src/routes/project/script/ChatDock.tsx）。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from ..parse import ParseError, parse_script
from ..security import login_gate

router = APIRouter(tags=["parse"])


@router.post("/parse-script")
async def parse_script_route(file: UploadFile = File(...), _: Any = Depends(login_gate)) -> dict[str, Any]:
    """解一份稿子。读不了就把「为什么读不了 + 下一步怎么办」原话回给前端。"""
    try:
        return parse_script(file.filename or "", await file.read()).as_dict()
    except ParseError as exc:
        raise HTTPException(400, "；".join([exc.message, *exc.hints])) from exc
