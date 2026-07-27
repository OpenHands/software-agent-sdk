"""
DLQ 服务 — 死信队列的业务编排层。

提供:
  - send_to_dlq(): 将失败消息包装为 DLQEnvelope 投递到死信 topic
  - replay(): 重放单条死信（发到 Kafka + 乐观标记 success）
  - replay_batch(): 批量重放
"""

import json
from datetime import datetime

from openhands.agent_server.kafka_bus.dlq.dlq_envelope import DLQEnvelope
from openhands.agent_server.kafka_bus.kafka_bus import kafka_message_bus
from openhands.agent_server.kafka_bus.kafka_topic import KafkaTopic
from openhands.agent_server.kafka_bus.message_event import MessageEvent
from openhands.sdk.logger import get_logger
from openhands.sdk.utils import env_util


logger = get_logger(__name__)


async def send_to_dlq(
    event: MessageEvent, group: str, source_app: str = "openhands-agent"
) -> bool:
    """将失败消息包装为 DLQEnvelope 投递到死信 topic"""
    try:
        if not event:
            logger.error("[DLQService] 消息为空，无法投递死信")
            return False
        if not event.message_id:
            logger.error(
                f"[DLQService] 消息 ID 为空，无法投递死信: topic={event.topic}"
            )
            return False
        if not event.topic:
            logger.error(
                f"[DLQService] 消息主题为空，无法投递死信: topic={event.topic}"
            )
            return False

        now = datetime.utcnow().isoformat()
        envelope = DLQEnvelope(
            topic=event.topic,
            message_id=event.message_id,
            group=group,
            biz_code=event.biz_code or "",
            event_type=event.event_type or "",
            payload=json.loads(event.to_json()),
            biz_error_message=event.error_message or "",
            source_app=source_app,
            cluster=env_util.get_cluster_region(),
            env=env_util.get_env_value(),
            first_failed_at=now,
        )

        dlq_msg = MessageEvent(
            event_type="dlq_message", data=json.loads(envelope.to_json())
        )
        sent = await kafka_message_bus.send(KafkaTopic.DLQ, dlq_msg)
        if not sent:
            logger.error(f"[DLQService] 死信投递失败: topic={event.topic}")
            return False
        logger.info(
            f"[DLQService] 死信已投递: topic={event.topic},"
            f" message_id={envelope.message_id}"
        )
        return True
    except Exception as e:
        logger.error(f"[DLQService] 死信投递失败: {e}", exc_info=True)
        return False
