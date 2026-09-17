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
import re
import signal
import sys

from typing import Any

from app.config.bot_routing import (
    EL_OGE_DIAGNOSTIKA_CONSULT_PIPELINE_ID,
    EL_OGE_DIAGNOSTIKA_CONSULT_STATUS_ID,
    EL_OGE_DIAGNOSTIKA_GRADE_ENUM,
)
from app.services.amocrm_client import RetryableAmoCRMError
from app.services.conversation_manager import ConversationManager
from app.services.salebot_client import RetryableSalebotError
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

# Максимальное количество повторных попыток при временных ошибках AmoCRM (502/503/504)
_MAX_RETRY_ATTEMPTS = 5
# Задержка перед повторной попыткой (секунды)
_RETRY_DELAY = 30


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
            "is_bot_message": data.get("is_bot_message", False),
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
            has_retries = False
            for msg in messages:
                try:
                    if msg.get("is_bot_message"):
                        # Сообщение от бота — пересылаем в amojo с отправителем "Бот"
                        await manager.handle_bot_message(
                            platform_id=msg["platform_id"],
                            bot_name=msg["bot_name"],
                            message_text=msg["message_text"] or "",
                        )
                        logger.debug(
                            "Bot message forwarded: platform_id=%s, bot=%s",
                            msg["platform_id"],
                            msg["bot_name"],
                        )
                    else:
                        # Сообщение от клиента — стандартная обработка
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
                        if conversation_id is None:
                            logger.info(
                                "handle_salebot_message returned None for platform_id=%s, bot=%s "
                                "— message skipped (no keyword match or creation error)",
                                msg["platform_id"],
                                msg["bot_name"],
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

                except RetryableAmoCRMError as e:
                    retry_count = msg.get("_retry_count", 0)
                    if retry_count >= _MAX_RETRY_ATTEMPTS:
                        logger.error(
                            "Salebot message dropped after %d retries (AmoCRM unavailable): %s",
                            _MAX_RETRY_ATTEMPTS,
                            e,
                        )
                        await redis.decr(counter_key)
                        total_processed += 1
                    else:
                        msg["_retry_count"] = retry_count + 1
                        await push_conversation_message(conversation_key, msg)
                        logger.warning(
                            "Salebot message requeued (attempt %d/%d) due to temporary AmoCRM error: %s",
                            retry_count + 1,
                            _MAX_RETRY_ATTEMPTS,
                            e,
                        )
                        has_retries = True

                except Exception as e:
                    logger.error(
                        "Error processing Salebot message in batch: %s",
                        e,
                        exc_info=True,
                    )
                    # Уменьшаем счётчик даже при ошибке: сообщение уже извлечено
                    # из очереди и не может быть обработано повторно.
                    new_counter = await redis.decr(counter_key)
                    logger.debug(
                        "Salebot counter decremented after error: %s (now %d)",
                        conversation_key,
                        new_counter,
                    )
                    total_processed += 1

            if has_retries:
                logger.info(
                    "Waiting %ds before retrying requeued messages: %s",
                    _RETRY_DELAY,
                    conversation_key,
                )
                await asyncio.sleep(_RETRY_DELAY)
        
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
            has_retries = False
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

                except RetryableAmoCRMError as e:
                    retry_count = msg.get("_retry_count", 0)
                    if retry_count >= _MAX_RETRY_ATTEMPTS:
                        logger.error(
                            "Amojo message dropped after %d retries (AmoCRM unavailable): %s",
                            _MAX_RETRY_ATTEMPTS,
                            e,
                        )
                        await redis.decr(counter_key)
                        total_processed += 1
                    else:
                        msg["_retry_count"] = retry_count + 1
                        await push_conversation_message(conversation_id, msg)
                        logger.warning(
                            "Amojo message requeued (attempt %d/%d) due to temporary AmoCRM error: %s",
                            retry_count + 1,
                            _MAX_RETRY_ATTEMPTS,
                            e,
                        )
                        has_retries = True

                except RetryableSalebotError as e:
                    retry_count = msg.get("_retry_count", 0)
                    if retry_count >= _MAX_RETRY_ATTEMPTS:
                        logger.error(
                            "Amojo message dropped after %d retries (Salebot unavailable): %s",
                            _MAX_RETRY_ATTEMPTS,
                            e,
                        )
                        await redis.decr(counter_key)
                        total_processed += 1
                    else:
                        msg["_retry_count"] = retry_count + 1
                        await push_conversation_message(conversation_id, msg)
                        logger.warning(
                            "Amojo message requeued (attempt %d/%d) due to temporary Salebot error: %s",
                            retry_count + 1,
                            _MAX_RETRY_ATTEMPTS,
                            e,
                        )
                        has_retries = True

                except Exception as e:
                    logger.error(
                        "Error processing Amojo message in batch: %s",
                        e,
                        exc_info=True,
                    )
                    # Уменьшаем счётчик даже при ошибке: сообщение уже извлечено
                    # из очереди и не может быть обработано повторно.
                    # Без декремента счётчик остаётся > 0 при пустой очереди
                    # и воркер зависает в бесконечном цикле sleep(0.1).
                    new_counter = await redis.decr(counter_key)
                    logger.debug(
                        "Amojo counter decremented after error: %s (now %d)",
                        conversation_id,
                        new_counter,
                    )
                    total_processed += 1

            if has_retries:
                logger.info(
                    "Waiting %ds before retrying requeued messages: %s",
                    _RETRY_DELAY,
                    conversation_id,
                )
                await asyncio.sleep(_RETRY_DELAY)
        
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


def _parse_grade(grade: Any) -> int | None:
    """
    Извлечь номер класса (7-11) из значения любого формата — int, "9",
    "9 класс", "9-й" и т.п. Возвращает None, если не удалось распарсить.
    """
    if isinstance(grade, bool):
        return None
    if isinstance(grade, int):
        return grade
    if isinstance(grade, str):
        match = re.search(r"\d+", grade)
        if match:
            return int(match.group())
    return None


async def process_lead_event(data: dict) -> None:
    """
    Обработать событие "заявка на консультацию" от сервера el_oge_diagnostika_bot.

    Сделка уже существует (создана с первого касания, lead_id найден заранее
    в app/api/lead_webhook.py по своей БД). Здесь:
    1. Переносим сделку в целевую воронку/этап (move_lead).
    2. Заполняем поля сделки: "Класс" (select, settings.FIELD_GRADE),
       "Промокод" (текст, settings.FIELD_PROMO_CODE), UTM-метки (те же поля,
       что и при создании сделки).
    3. Добавляем примечание со ВСЕЙ информацией из события целиком (тип
       обращения, класс, предмет(ы), результат диагностики, промокод,
       источник, UTM, экран нажатия, их внутренний ID заявки) — включая
       класс/промокод/UTM, которые уже пытались записать в поля выше. Это
       намеренная страховка: если запись в поле сорвётся (ошибка AmoCRM,
       нераспознанное значение класса и т.п.), данные не потеряются —
       менеджер всё равно увидит их текстом в примечании.

    Каждый шаг обёрнут в собственный try/except — сбой в одном (например,
    неизвестное значение класса или упавший запрос на промокод) не должен
    мешать выполнению остальных шагов и добавлению примечания. move_lead
    и update_lead_enum сами не бросают исключений при ошибке AmoCRM
    (логируют предупреждение) — это осознанно best-effort операция,
    повторный вызов с тем же lead_id безопасен.

    Перед любыми операциями проверяем, жива ли сделка (get_lead) — та же
    проверка, что используется в обычном потоке сообщений
    (ConversationManager.handle_salebot_message). lead_id, найденный заранее
    в app/api/lead_webhook.py по БД, может быть устаревшим: та проверка
    выполняется лениво, только когда пишет клиент, а не по расписанию —
    если клиент давно не писал, а сделку тем временем удалили/слили через
    NOVA-merge, наш lead_id может ссылаться на несуществующую сделку. В этом
    случае переоткрываем диалог тем же механизмом (_reopen_conversation),
    что и обычный поток, и используем свежий lead_id для всех операций ниже.

    Args:
        data: {"bot_name", "platform_id", "lead_id", "kind", "grade", "subject",
            "subjects", "result", "promo", "source", "utm", "place",
            "external_lead_id", "created"}
    """
    bot_name = data.get("bot_name")
    platform_id = data.get("platform_id")
    lead_id = data.get("lead_id")

    if not lead_id or not platform_id or not bot_name:
        logger.error(
            "LEAD_EVENT: missing lead_id/platform_id/bot_name, data=%s", data
        )
        return

    # Переиспользуем ConversationManager (а не отдельный AmoCRMClient), чтобы
    # получить доступ к тем же storage/amocrm/salebot/amojo клиентам и к
    # _reopen_conversation — той же логике переоткрытия, что и в обычном
    # потоке сообщений.
    manager = ConversationManager()
    amocrm = manager.amocrm
    try:
        # 0. Проверяем, жива ли сделка, и переоткрываем диалог при необходимости.
        lead = await amocrm.get_lead(lead_id)
        if lead is None or lead == {}:
            reason = "absorbed (204)" if lead == {} else "not found (404/error)"
            logger.warning(
                "LEAD_EVENT: lead %s %s, reopening conversation: platform_id=%s, bot=%s",
                lead_id, reason, platform_id, bot_name,
            )
            conversation = await manager.storage.get_by_platform_id(platform_id, bot_name)
            if not conversation:
                logger.error(
                    "LEAD_EVENT: conversation not found while reopening, "
                    "platform_id=%s, bot=%s — skip event",
                    platform_id, bot_name,
                )
                return

            if conversation.lead_id and conversation.lead_id != lead_id:
                # Обычный поток сообщений уже переоткрыл диалог раньше нас
                # (клиент успел написать) — просто берём свежий lead_id из БД.
                logger.info(
                    "LEAD_EVENT: conversation already reopened elsewhere, "
                    "using fresh lead_id=%s (was %s)",
                    conversation.lead_id, lead_id,
                )
                lead_id = conversation.lead_id
            else:
                reopened = await manager._reopen_conversation(
                    conversation=conversation,
                    platform_id=platform_id,
                    bot_name=bot_name,
                    salebot_client_id=conversation.salebot_client_id,
                    client_name=conversation.client_name or "Ученик",
                    tg_username=conversation.tg_username,
                    utm_data=None,
                )
                if not reopened or not reopened.lead_id:
                    logger.error(
                        "LEAD_EVENT: failed to reopen conversation, platform_id=%s, "
                        "bot=%s — skip event",
                        platform_id, bot_name,
                    )
                    return
                lead_id = reopened.lead_id
                logger.info("LEAD_EVENT: conversation reopened, new lead_id=%s", lead_id)

        # 1. Перенос в целевую воронку/этап.
        await amocrm.move_lead(
            lead_id=lead_id,
            pipeline_id=EL_OGE_DIAGNOSTIKA_CONSULT_PIPELINE_ID,
            status_id=EL_OGE_DIAGNOSTIKA_CONSULT_STATUS_ID,
        )

        # 2. Класс — select-поле 809893. Отдельный try, чтобы неизвестное
        # значение класса или сбой запроса не блокировали промокод/UTM/примечание.
        grade = data.get("grade")
        if grade:
            grade_num = _parse_grade(grade)
            enum_id = EL_OGE_DIAGNOSTIKA_GRADE_ENUM.get(grade_num) if grade_num else None
            if enum_id:
                try:
                    await amocrm.update_lead_enum(lead_id, settings.FIELD_GRADE, enum_id)
                except Exception as e:
                    logger.warning(
                        "LEAD_EVENT: failed to set grade field, lead_id=%s, grade=%r, error=%s",
                        lead_id, grade, e,
                    )
            else:
                logger.warning(
                    "LEAD_EVENT: unknown/unparsable grade value, lead_id=%s, grade=%r — "
                    "field not set (still goes into note below via kind/subject context)",
                    lead_id, grade,
                )

        # 3. Промокод — текстовое поле 793154.
        promo = data.get("promo")
        if promo:
            try:
                await amocrm.update_lead(lead_id, {settings.FIELD_PROMO_CODE: promo})
            except Exception as e:
                logger.warning(
                    "LEAD_EVENT: failed to set promo field, lead_id=%s, error=%s", lead_id, e
                )

        # 4. UTM — переиспользуем те же поля, что и при создании сделки.
        utm = data.get("utm")
        if isinstance(utm, dict):
            utm_field_map = {
                settings.FIELD_UTM_SOURCE: utm.get("utm_source"),
                settings.FIELD_UTM_MEDIUM: utm.get("utm_medium"),
                settings.FIELD_UTM_CAMPAIGN: utm.get("utm_campaign"),
                settings.FIELD_UTM_TERM: utm.get("utm_term"),
                settings.FIELD_UTM_CONTENT: utm.get("utm_content"),
            }
            utm_fields = {field_id: value for field_id, value in utm_field_map.items() if value}
            if utm_fields:
                try:
                    await amocrm.update_lead(lead_id, utm_fields)
                except Exception as e:
                    logger.warning(
                        "LEAD_EVENT: failed to set UTM fields, lead_id=%s, error=%s", lead_id, e
                    )

        # 5. Примечание — дублируем ВСЮ информацию из события текстом, включая
        # класс/промокод/UTM (которые выше уже пытались записать в поля).
        # Сделано намеренно как страховка: если запись в поле выше не удалась
        # (ошибка AmoCRM, неизвестное значение класса и т.п.) — данные всё
        # равно не потеряются, менеджер увидит их в примечании.
        note_lines = ["Заявка на консультацию (el_oge_diagnostika_bot)"]

        kind = data.get("kind")
        if kind:
            note_lines.append(f"Тип: {kind}")

        if grade:
            note_lines.append(f"Класс: {grade}")

        subject = data.get("subject")
        if subject:
            note_lines.append(f"Предмет: {subject}")

        subjects = data.get("subjects")
        if subjects:
            note_lines.append(f"Все предметы: {', '.join(str(s) for s in subjects)}")

        result = data.get("result")
        if result:
            note_lines.append(f"Результат теста: {result}")

        if promo:
            note_lines.append(f"Промокод: {promo}")

        source = data.get("source")
        if source:
            note_lines.append(f"Источник: {source}")

        if isinstance(utm, dict):
            utm_str = ", ".join(f"{k}={v}" for k, v in utm.items() if v)
            if utm_str:
                note_lines.append(f"UTM: {utm_str}")

        place = data.get("place")
        if place:
            note_lines.append(f"Экран: {place}")

        external_lead_id = data.get("external_lead_id")
        if external_lead_id is not None:
            note_lines.append(f"ID заявки (их сторона): {external_lead_id}")

        await amocrm.add_lead_note(lead_id, "\n".join(note_lines))

        logger.info(
            "LEAD_EVENT processed: bot=%s, platform_id=%s, lead_id=%s",
            bot_name,
            platform_id,
            lead_id,
        )
    except Exception as e:
        logger.error(
            "LEAD_EVENT failed: bot=%s, platform_id=%s, lead_id=%s, error=%s",
            bot_name,
            platform_id,
            lead_id,
            e,
            exc_info=True,
        )
    finally:
        await manager.close()


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
    elif task_type == "lead_event":
        logger.info("PROCESS_TASK: calling process_lead_event")
        await process_lead_event(data)
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
