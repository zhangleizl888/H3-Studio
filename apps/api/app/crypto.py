"""实例 apiKey / LLM apiKey 的可逆加密。

这些 key 必须能还原（要拿去调 ComfyUI / RunningHub），所以不能像口令那样只存哈希。
Fernet 密钥单独放文件而不是塞进 .env：这样 .env 可以安全地进版本库，密钥文件不进。

轮换：改 H3_SECRET_KEY 或删掉密钥文件会让已存 key 无法解密 —— 那时应提示重新录入，
而不是悄悄存一份明文。
"""

from __future__ import annotations

import base64
import os
from functools import lru_cache
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from .config import get_settings
from .logging_setup import get_logger

log = get_logger("crypto")


@lru_cache
def _key_path() -> Path:
    return Path(os.environ.get("H3_SECRET_FILE") or (Path(__file__).resolve().parents[1] / ".secret.key"))


def _fernet() -> Fernet:
    env_key = os.environ.get("H3_SECRET_KEY")
    if env_key:
        return Fernet(env_key.encode())
    path = _key_path()
    if path.exists():
        return Fernet(path.read_bytes())
    key = Fernet.generate_key()
    path.write_bytes(key)
    try:
        os.chmod(path, 0o600)
    except OSError:  # Windows 上 chmod 语义有限，失败不影响使用
        pass
    log.warning("已生成 Fernet 密钥 %s —— 该文件绝不能进版本库", path)
    return Fernet(key)


def load_or_create_secret(name: str) -> bytes:
    """读取或生成一个随机密钥文件（十六进制 64 字符 = 32 字节）。"""
    path = _key_path().with_name(f"{_key_path().stem}-{name}.txt")
    if path.exists():
        return path.read_bytes().strip()
    value = base64.urlsafe_b64encode(os.urandom(32)).rstrip(b"=")
    path.write_bytes(value)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    log.warning("已生成随机密钥 %s（勿进版本库）", path)
    return value


def encrypt(plaintext: str | None) -> bytes | None:
    if not plaintext:
        return None
    return _fernet().encrypt(plaintext.encode())


def decrypt(token: bytes | str | None) -> str | None:
    if not token:
        return None
    raw = token.encode() if isinstance(token, str) else token
    try:
        return _fernet().decrypt(raw).decode()
    except InvalidToken:
        # 密钥换过或数据损坏。这里绝不退化成「当成明文返回」——那会把密文当 key 发出去
        log.error("解密失败：H3 密钥可能已轮换。请重新录入该实例的 apiKey。")
        return None


def b64(token: bytes | None) -> str | None:
    """密文 → 存 Text 列的字符串。没有 key 就是 NULL，不是空串。"""
    if not token:
        return None
    return base64.b64encode(token).decode()


def from_b64(text: str | None) -> bytes | None:
    if not text:
        return None
    return base64.b64decode(text.encode())
