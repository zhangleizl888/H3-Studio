"""结构化日志 + 凭据脱敏。

RunningHub 的原生代理把 apiKey 放在 URL 路径里（/proxy/{key}），这意味着 key 会顺着
access log、异常信息、httpx 的 request.url 一路漏出去。所有落日志的文本都必须过一遍 redact。
"""

from __future__ import annotations

import logging
import re
import sys

# /proxy/<key> 与 /proxy-plus/<key>，key 至少 8 位
_PROXY_PATH_RE = re.compile(r"(/proxy(?:-plus)?/)[A-Za-z0-9_\-]{8,}")
# 任何 apiKey=/key=<value> 形式的查询串
_QUERY_KEY_RE = re.compile(r"([?&](?:apiKey|key|access_token|token)=)[^&\s]+")
# JSON 里的 "apiKey": "..."
_JSON_KEY_RE = re.compile(r'("apiKey"\s*:\s*")[^"]+(")')
# Bearer
_BEARER_RE = re.compile(r"(Bearer\s+)[A-Za-z0-9._\-]{8,}")


def redact(text: str) -> str:
    """把所有已知凭据载体替换成占位符。幂等。"""
    if not text:
        return text
    out = _PROXY_PATH_RE.sub(r"\1<REDACTED>", text)
    out = _QUERY_KEY_RE.sub(r"\1<REDACTED>", out)
    out = _JSON_KEY_RE.sub(r"\1<REDACTED>\2", out)
    out = _BEARER_RE.sub(r"\1<REDACTED>", out)
    return out


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


def mask_secret(value: str | None) -> str:
    """给 UI 用的显示形式：前 4 后 4，绝不返回原文。"""
    if not value:
        return ""
    if len(value) <= 8:
        return "****"
    return f"{value[:4]}****{value[-4:]}"


def setup_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        RedactingFormatter(
            fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
            datefmt="%H:%M:%S",
        )
    )
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    # httpx 会把完整 URL 打进日志，正是泄漏面
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("websockets").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
