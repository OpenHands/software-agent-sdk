"""
Kafka 消息标准结构体 — 统一所有 Kafka 消息的外层格式。

所有 Kafka 消息统一使用 MessageEvent 构造，发送方调用 to_json() 序列化，
消费方 json.loads 后按 event_type 分发处理。

字段说明:
  event_type  — 事件类型字符串，由各业务域自定义枚举
  data        — 业务数据，具体结构由 event_type 决定（user_id 等业务字段放在 data 内）
  source_app  — 来源应用（send() 自动填充: k8s_middleware / portal）
  cluster     — 生产者集群标识（send() 自动填充，用于消息追踪和日志）
  message_id  — 消息唯一 ID（UUID v4，send() 自动填充）
  timestamp   — 消息产生时间 ISO 格式（可选）
  biz_code    — 业务编码（业务主键，如 instance_id / task_id，由生产者填充）
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class MessageEvent:
    """
    Kafka 消息标准结构体（消息信封）。

    使用示例:
        msg = MessageEvent(
            event_type="instance_status_change",
            data={"user_id": 1000000123, "instance_id": 1, "status": "running"},
            biz_code="1",
        )
        await kafka_message_bus.send(topic, msg)
    """

    data: dict[str, Any]
    event_type: str | None = None  # 消息类型
    source_app: str | None = None  # 来源app
    cluster: str | None = None  # 集群
    message_id: str | None = None  # 消息id
    timestamp: str | None = None  # 消息时间
    biz_code: str | None = None  # 业务code
    topic: str | None = None  # topic
    env: str | None = None  # 环境
    attempt: int | None = None  # 重试次数
    error_message: str | None = None  # 失败消息

    def to_json(self) -> str:
        """序列化为 JSON 字符串，自动移除 None 字段保持消息精简"""
        d = asdict(self)
        return json.dumps(
            {k: v for k, v in d.items() if v is not None},
            ensure_ascii=False,
        )

    @classmethod
    def from_dict(cls, d: dict) -> MessageEvent:
        """从 dict 反序列化，忽略未知字段"""
        valid_keys = cls.__dataclass_fields__.keys()
        return cls(**{k: v for k, v in d.items() if k in valid_keys})
