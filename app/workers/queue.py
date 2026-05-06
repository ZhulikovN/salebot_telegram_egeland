"""Очереди задач на Redis с гарантией порядка сообщений."""

import asyncio
import json
import logging
from typing import Any

from app.utils.redis_connection import get_redis

logger = logging.getLogger(__name__)


async def push_task(task_type: str, data: dict[str, Any]) -> None:
    """
    Добавить задачу в глобальную очередь.

    Задача добавляется в общую очередь для распределения между воркерами.

    Args:
        task_type: Тип задачи (salebot_message, amojo_message)
        data: Данные задачи

    Raises:
        Exception: Если не удалось добавить задачу в Redis
    """
    try:
        logger.info("PUSH_TASK STARTED: type=%s", task_type)
        task = {"type": task_type, "data": data}
        redis = get_redis()
        logger.info("PUSH_TASK: got redis client")
        await redis.rpush("tasks:global", json.dumps(task))
        logger.info(
            "PUSH_TASK SUCCESS: type=%s, conversation_id=%s",
            task_type,
            data.get("conversation_id", "unknown"),
        )
    except Exception as e:
        logger.error("PUSH_TASK FAILED: %s - %s", task_type, e, exc_info=True)
        raise


async def pop_task(timeout: int = 5) -> dict[str, Any] | None:
    """
    Взять задачу из глобальной очереди (блокирующая операция).

    Ожидает появления задачи до timeout секунд.

    Args:
        timeout: Таймаут ожидания в секундах

    Returns:
        Словарь с задачей или None если таймаут
    """
    try:
        redis = get_redis()
        logger.info("POP_TASK: calling blpop with timeout=%d", timeout)
        result = await redis.blpop("tasks:global", timeout=timeout)
        logger.info("POP_TASK: blpop returned: %s", "data" if result else "None")

        if result:
            raw_data = result[1]
            logger.info("POP_TASK: raw_data=%s", raw_data[:200] if isinstance(raw_data, (str, bytes)) else raw_data)
            task = json.loads(raw_data)
            logger.info("POP_TASK: parsed task type=%s", task.get("type"))
            return task

        return None
    except asyncio.TimeoutError:
        logger.info("POP_TASK: timeout")
        return None
    except Exception as e:
        logger.error("POP_TASK FAILED: %s", e, exc_info=True)
        return None


async def push_conversation_message(
    conversation_id: str,
    message_data: dict[str, Any],
) -> None:
    """
    Добавить сообщение в очередь конкретного диалога.

    Гарантирует FIFO порядок сообщений для одного диалога.

    Args:
        conversation_id: UUID диалога
        message_data: Данные сообщения
    """
    try:
        redis = get_redis()
        queue_key = f"queue:conversation:{conversation_id}"
        await redis.rpush(queue_key, json.dumps(message_data))
        logger.debug(
            "Message pushed to conversation queue: %s",
            conversation_id,
        )
    except Exception as e:
        logger.error(
            "Failed to push message to conversation %s: %s",
            conversation_id,
            e,
        )
        raise


async def pop_conversation_messages(
    conversation_id: str,
) -> list[dict[str, Any]]:
    """
    Взять ВСЕ сообщения из очереди диалога.

    Атомарно извлекает все сообщения, гарантируя их порядок.

    Args:
        conversation_id: UUID диалога

    Returns:
        Список сообщений в порядке FIFO
    """
    try:
        redis = get_redis()
        queue_key = f"queue:conversation:{conversation_id}"

        messages = []
        while True:
            result = await redis.lpop(queue_key)
            if not result:
                break
            messages.append(json.loads(result))

        logger.debug(
            "Popped %d messages from conversation %s",
            len(messages),
            conversation_id,
        )
        return messages

    except Exception as e:
        logger.error(
            "Failed to pop messages from conversation %s: %s",
            conversation_id,
            e,
        )
        return []


async def get_queue_size() -> int:
    """
    Получить текущий размер глобальной очереди задач.

    Returns:
        Количество задач в очереди или 0 при ошибке
    """
    try:
        redis = get_redis()
        size = await redis.llen("tasks:global")
        return size
    except Exception as e:
        logger.error("Failed to get queue size: %s", e)
        return 0


async def close_queue() -> None:
    """
    Закрыть соединение с Redis (graceful shutdown).

    Вызывается при остановке воркера или API.
    """
    from app.utils.redis_connection import RedisConnection

    try:
        await RedisConnection.close()
    except Exception as e:
        logger.error("Failed to close Redis connection: %s", e)
