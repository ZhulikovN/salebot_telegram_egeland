"""
Воркер для обработки сообщений Salebot ↔ amoCRM.

Основной цикл:
1. Берет задачу из глобальной Redis очереди
2. Захватывает блокировку диалога
3. Добавляет сообщение в очередь диалога
4. Обрабатывает ВСЕ сообщения диалога по порядку (FIFO)
5. Освобождает блокировку
"""

import asyncio
import logging
import signal
import sys

from typing import Any

from app.services.conversation_manager import ConversationManager
from app.settings import settings
from app.utils.redis_connection import get_redis
from app.workers.queue import (
    close_queue,
    get_queue_size,
    pop_conversation_messages,
    pop_task,
    push_conversation_message,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

logger = logging.getLogger(__name__)

# Флаг для graceful shutdown
shutdown_requested = False


def handle_shutdown_signal(signum: int, frame: Any) -> None:
    """
    Обработчик сигнала завершения (SIGTERM, SIGINT).
    
    Args:
        signum: Номер сигнала
        frame: Текущий stack frame
    """
    global shutdown_requested
    logger.info("Shutdown signal received (%s), finishing current task...", signum)
    shutdown_requested = True


async def acquire_lock(lock_key: str, ttl: int = 60) -> bool:
    """
    Захватить блокировку для обработки диалога.

    Args:
        lock_key: Ключ блокировки в Redis
        ttl: Время жизни блокировки в секундах (по умолчанию 60 сек)

    Returns:
        True если блокировка захвачена, False если уже занята
    """
    try:
        redis = get_redis()
        acquired = await redis.set(lock_key, "1", nx=True, ex=ttl)
        if acquired:
            logger.debug("Lock acquired: %s", lock_key)
        else:
            logger.debug("Lock already held: %s", lock_key)
        return bool(acquired)
    except Exception as e:
        logger.error("Failed to acquire lock %s: %s", lock_key, e)
        return False


async def release_lock(lock_key: str) -> None:
    """
    Освободить блокировку.

    Args:
        lock_key: Ключ блокировки в Redis
    """
    try:
        redis = get_redis()
        await redis.delete(lock_key)
        logger.debug("Lock released: %s", lock_key)
    except Exception as e:
        logger.error("Failed to release lock %s: %s", lock_key, e)


async def process_salebot_message(data: dict) -> None:
    """
    Обработать сообщение от Salebot.

    Алгоритм:
    1. Добавить сообщение в очередь конкретного диалога
    2. Попытаться захватить блокировку диалога
    3. Если блокировка занята → выйти (другой воркер обработает)
    4. Если блокировка захвачена → взять ВСЕ сообщения из очереди диалога
    5. Обработать их по порядку (FIFO)
    6. Освободить блокировку

    Args:
        data: Данные сообщения от Salebot
    """
    platform_id = data.get("platform_id")
    bot_name = data.get("bot_name")
    
    if not platform_id or not bot_name:
        logger.error("Missing platform_id or bot_name in salebot message: %s", data)
        return
    
    # Идентификатор диалога: пара клиент+бот (каждый бот — отдельный диалог)
    conversation_key = f"{platform_id}:{bot_name}"
    lock_key = f"lock:conversation:{conversation_key}"
    
    # Добавляем сообщение в очередь конкретного диалога
    try:
        await push_conversation_message(conversation_key, {
            "type": "salebot",
            "platform_id": platform_id,
            "bot_name": bot_name,
            "salebot_client_id": data.get("salebot_client_id"),
            "client_name": data.get("client_name"),
            "message_text": data.get("message_text"),
            "attachments": data.get("attachments") or [],
            "tg_username": data.get("tg_username"),
            "utm_data": data.get("utm_data"),
        })
        
        # Увеличиваем счётчик необработанных сообщений
        redis = get_redis()
        counter_key = f"counter:salebot:{conversation_key}"
        await redis.incr(counter_key)
        await redis.expire(counter_key, 3600)  # TTL 1 час
        
        logger.debug("Message added to conversation queue: %s", conversation_key)
    except Exception as e:
        logger.error("Failed to add message to conversation queue: %s", e)
        return
    
    # Пытаемся захватить блокировку (TTL 300 сек для медленных запросов к AmoCRM)
    if not await acquire_lock(lock_key, ttl=300):
        logger.debug(
            "Conversation %s is locked by another worker, skipping (will be processed by that worker)",
            conversation_key,
        )
        return  # ← НЕ ЖДЁМ! Другой воркер обработает все сообщения
    
    manager = None
    
    try:
        # Инициализируем менеджер
        manager = ConversationManager()
        
        total_processed = 0
        
        logger.info(
            "Starting Salebot processing loop: conversation_key=%s",
            conversation_key,
        )
        
        # ЦИКЛ: Обрабатываем пока очередь не опустеет
        while True:
            # Берём ВСЕ сообщения из очереди диалога
            messages = await pop_conversation_messages(conversation_key)
            
            logger.debug(
                "Popped %d messages from queue: %s",
                len(messages),
                conversation_key,
            )
            
            if not messages:
                # Проверяем счётчик: есть ли необработанные сообщения?
                counter = await redis.get(counter_key)
                counter = int(counter) if counter else 0
                
                logger.debug(
                    "Queue empty, checking counter: %d for %s",
                    counter,
                    conversation_key,
                )
                
                if counter > 0:
                    # Есть необработанные, ждём 100ms и повторяем
                    logger.debug(
                        "Counter=%d, waiting for messages: %s",
                        counter,
                        conversation_key,
                    )
                    await asyncio.sleep(0.1)
                    continue
                
                # ДВОЙНАЯ ПРОВЕРКА (защита от extreme race condition)
                await asyncio.sleep(0.05)
                counter = await redis.get(counter_key)
                counter = int(counter) if counter else 0
                
                if counter > 0:
                    logger.debug(
                        "Counter=%d after double-check, continuing: %s",
                        counter,
                        conversation_key,
                    )
                    continue
                
                # Счётчик = 0, точно всё обработано
                logger.debug("No more messages in conversation queue: %s", conversation_key)
                break
            
            logger.info(
                "Processing %d Salebot message(s) for conversation %s (batch %d)",
                len(messages),
                conversation_key,
                total_processed // 10 + 1,
            )
            
            # Обрабатываем каждое сообщение по порядку (FIFO)
            for msg in messages:
                try:
                    conversation_id = await manager.handle_salebot_message(
                        platform_id=msg["platform_id"],
                        bot_name=msg["bot_name"],
                        salebot_client_id=msg["salebot_client_id"],
                        client_name=msg["client_name"],
                        message_text=msg["message_text"],
                        attachments=msg.get("attachments") or [],
                        tg_username=msg.get("tg_username"),
                        utm_data=msg.get("utm_data"),
                    )
                    
                    logger.debug(
                        "Salebot message processed: conversation_id=%s",
                        conversation_id,
                    )
                    
                    # Уменьшаем счётчик после успешной обработки
                    new_counter = await redis.decr(counter_key)
                    logger.debug(
                        "Salebot counter decremented: %s (now %d)",
                        conversation_key,
                        new_counter,
                    )
                    
                    total_processed += 1
                    
                    # Небольшая задержка между сообщениями (rate limit)
                    await asyncio.sleep(0.1)
                    
                except Exception as e:
                    logger.error(
                        "Error processing Salebot message in batch: %s",
                        e,
                        exc_info=True,
                    )
                    # НЕ уменьшаем счётчик при ошибке (сообщение не обработано)
                    # Продолжаем обработку следующих сообщений
        
        logger.info(
            "Batch processing completed for conversation %s: %d total messages processed",
            conversation_key,
            total_processed,
        )
        
    except Exception as e:
        logger.error(
            "Error processing Salebot batch: %s",
            e,
            exc_info=True,
        )
    finally:
        if manager:
            await manager.close()
        await release_lock(lock_key)


async def process_amojo_message(data: dict) -> None:
    """
    Обработать сообщение от amoCRM (ответ менеджера).

    Алгоритм:
    1. Добавить сообщение в очередь конкретного диалога
    2. Попытаться захватить блокировку диалога
    3. Если блокировка занята → выйти (другой воркер обработает)
    4. Если блокировка свободна → обработать ВСЕ сообщения из очереди

    Args:
        data: Данные сообщения от amoCRM
    """
    conversation_id = data.get("conversation_id")
    
    if not conversation_id:
        logger.error("Missing conversation_id in amojo message: %s", data)
        return
    
    # 1. Добавляем сообщение в очередь диалога (FIFO)
    await push_conversation_message(conversation_id, data)
    
    # 2. Увеличиваем счётчик необработанных сообщений
    redis = get_redis()
    counter_key = f"counter:conversation:{conversation_id}"
    new_counter = await redis.incr(counter_key)
    await redis.expire(counter_key, 3600)  # TTL 1 час
    
    logger.info(
        "Amojo message queued: conversation_id=%s, counter=%d",
        conversation_id,
        new_counter,
    )
    
    lock_key = f"lock:conversation:{conversation_id}"
    
    # 3. Пытаемся захватить блокировку (TTL 300 сек для медленных запросов к AmoCRM)
    if not await acquire_lock(lock_key, ttl=300):
        logger.debug(
            "Conversation %s is locked by another worker, skipping (will be processed by that worker)",
            conversation_id,
        )
        return  # ← НЕ ЖДЁМ! Другой воркер обработает все сообщения
    
    manager = None
    
    try:
        # Инициализируем менеджер
        manager = ConversationManager()
        
        total_processed = 0
        
        logger.info(
            "Starting Amojo processing loop: conversation_id=%s",
            conversation_id,
        )
        
        # ЦИКЛ: Обрабатываем пока очередь не опустеет
        while True:
            # Берём ВСЕ сообщения из очереди диалога
            messages = await pop_conversation_messages(conversation_id)
            
            logger.debug(
                "Popped %d Amojo messages from queue: %s",
                len(messages),
                conversation_id,
            )
            
            if not messages:
                # Проверяем счётчик: есть ли необработанные сообщения?
                counter_value = await redis.get(counter_key)
                counter = int(counter_value) if counter_value else 0
                
                logger.debug(
                    "Queue empty, checking counter: %d for %s",
                    counter,
                    conversation_id,
                )
                
                if counter > 0:
                    # Есть необработанные, ждём 100ms и повторяем
                    logger.debug(
                        "Counter=%d, waiting for messages: %s",
                        counter,
                        conversation_id,
                    )
                    await asyncio.sleep(0.1)
                    continue
                
                # ДВОЙНАЯ ПРОВЕРКА (защита от extreme race condition)
                await asyncio.sleep(0.05)
                counter_value = await redis.get(counter_key)
                counter = int(counter_value) if counter_value else 0
                
                if counter > 0:
                    logger.debug(
                        "Counter=%d after double-check, continuing: %s",
                        counter,
                        conversation_id,
                    )
                    continue
                
                # Счётчик = 0, точно всё обработано
                logger.debug("No more messages in conversation queue: %s", conversation_id)
                break
            
            logger.info(
                "Processing %d amojo message(s) for conversation %s (batch %d)",
                len(messages),
                conversation_id,
                total_processed // 10 + 1,
            )
            
            # Обрабатываем каждое сообщение по порядку (FIFO)
            for msg in messages:
                try:
                    await manager.handle_amojo_message(
                        conversation_id=msg["conversation_id"],
                        message_text=msg["message_text"],
                        message_type=msg.get("message_type", "text"),
                        media_url=msg.get("media_url"),
                    )
                    
                    logger.debug(
                        "Amojo message processed: conversation_id=%s",
                        msg["conversation_id"],
                    )
                    
                    # Уменьшаем счётчик после успешной обработки
                    new_counter = await redis.decr(counter_key)
                    logger.debug(
                        "Amojo counter decremented: %s (now %d)",
                        conversation_id,
                        new_counter,
                    )
                    
                    total_processed += 1
                    
                    # Небольшая задержка между сообщениями (rate limit)
                    await asyncio.sleep(0.1)
                    
                except Exception as e:
                    logger.error(
                        "Error processing Amojo message in batch: %s",
                        e,
                        exc_info=True,
                    )
                    # НЕ уменьшаем счётчик при ошибке (сообщение не обработано)
                    # Продолжаем обработку следующих сообщений
        
        logger.info(
            "Batch processing completed for conversation %s: %d total messages processed",
            conversation_id,
            total_processed,
        )
        
    except Exception as e:
        logger.error(
            "Error processing amojo batch: %s",
            e,
            exc_info=True,
        )
    finally:
        if manager:
            await manager.close()
        await release_lock(lock_key)


async def process_task(task: dict) -> None:
    """
    Обработать задачу из очереди.

    Args:
        task: Задача в формате {"type": "...", "data": {...}}
    """
    task_type = task.get("type")
    data = task.get("data", {})
    
    logger.info("PROCESS_TASK: type=%s, data_keys=%s", task_type, list(data.keys()))
    
    if task_type == "salebot_message":
        logger.info("PROCESS_TASK: calling process_salebot_message")
        await process_salebot_message(data)
    elif task_type == "amojo_message":
        logger.info("PROCESS_TASK: calling process_amojo_message")
        await process_amojo_message(data)
    else:
        logger.error("Unknown task type: %s", task_type)


async def main() -> None:
    """
    Основной цикл воркера.

    Бесконечный цикл:
    1. Берет задачу из глобальной Redis очереди
    2. Если задача есть - обрабатывает
    3. Логирует размер очереди каждые 10 секунд
    4. При ошибке - логирует и продолжает работу
    5. При получении SIGTERM/SIGINT - завершает текущую задачу и останавливается
    """
    global shutdown_requested
    
    # Регистрируем обработчики сигналов для graceful shutdown
    signal.signal(signal.SIGTERM, handle_shutdown_signal)
    signal.signal(signal.SIGINT, handle_shutdown_signal)
    
    logger.info("=" * 60)
    logger.info("Salebot ↔ amoCRM Worker started")
    logger.info("Redis: %s:%s", settings.REDIS_HOST, settings.REDIS_PORT)
    logger.info("AmoCRM: %s", settings.AMOCRM_SUBDOMAIN)
    logger.info("=" * 60)
    
    iteration = 0
    
    while not shutdown_requested:
        try:
            task = await pop_task(timeout=5)
            
            if task:
                logger.info("Received task: %s", task.get("type"))
                try:
                    await process_task(task)
                except Exception as e:
                    logger.error("Task processing failed: %s", e, exc_info=True)
            
            iteration += 1
            
            # Мониторинг очереди каждые 10 итераций
            if iteration % 10 == 0:
                queue_size = await get_queue_size()
                logger.debug("Queue size: %d tasks", queue_size)
                
                # Алерт если очередь большая
                if queue_size > 1000:
                    logger.warning("Queue backlog detected: %d tasks!", queue_size)
                elif queue_size > 500:
                    logger.info("Queue growing: %d tasks", queue_size)
            
            if not task:
                await asyncio.sleep(0.1)
                
        except KeyboardInterrupt:
            logger.info("KeyboardInterrupt received, stopping worker...")
            shutdown_requested = True
        except Exception as e:
            logger.error("Worker loop error: %s", e, exc_info=True)
            await asyncio.sleep(5)
    
    logger.info("Shutting down gracefully...")
    
    # Закрыть соединения с БД
    from app.db.storage import get_conversation_storage
    try:
        storage = get_conversation_storage()
        await storage.close()
        logger.info("✓ Database connections closed")
    except Exception as e:
        logger.error("Error closing database: %s", e)
    
    # Закрыть Redis
    await close_queue()
    logger.info("Worker stopped")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Worker interrupted by user")
        sys.exit(0)
    except Exception as e:
        logger.error("Fatal error: %s", e, exc_info=True)
        sys.exit(1)
