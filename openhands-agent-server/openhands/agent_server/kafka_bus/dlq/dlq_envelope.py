"""
死信信封 — 消费失败消息的标准结构。

所有重试耗尽的消息统一包装为 DLQEnvelope，投递到 DLQ topic，
由 Portal 消费后写入 DB 表，供后续查询/重放。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class DLQEnvelope:
    """
    死信信封。

    包含原始消息的完整上下文 + 失败原因，用于后续排查和重放。
    """

    topic: str  # 来源 topic（已 resolve 的实际名称）
    message_id: str  # 原始消息 ID
    group: str  # 消费组: middleware / portal
    biz_code: str  # 业务编码（event_type 或自定义）
    event_type: str  # 事件类型
    payload: dict[str, Any]  # 完整原始 payload
    biz_error_message: str  # 业务执行异常信息
    source_app: str = ""  # 来源应用: portal / k8s_middleware
    cluster: str = ""  # 来源集群
    env: str = ""  # 环境: prod / pre / pre2
    first_failed_at: str = ""  # 首次失败时间 (ISO)

    def to_json(self) -> str:
        """序列化为 JSON 字符串"""
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_dict(cls, d: dict) -> DLQEnvelope:
        """从 dict 反序列化"""
        valid_keys = cls.__dataclass_fields__.keys()
        return cls(**{k: v for k, v in d.items() if k in valid_keys})
