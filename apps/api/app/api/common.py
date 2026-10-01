"""路由层共用的小件。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class CamelModel(BaseModel):
    """入参接受 camelCase（§10 的 JSON 契约），Python 侧字段名保持 snake_case。

    populate_by_name=True 让内部调用与测试仍能用原名构造；
    FastAPI 默认按 alias 序列化响应，所以同一个模型两头都是 camelCase。
    """

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, from_attributes=True)
