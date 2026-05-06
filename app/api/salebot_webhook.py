"""Webhook endpoint для Salebot."""
import logging

from fastapi import APIRouter, Request

from app.models.salebot import SalebotWebhook
from app.workers.queue import push_task

router = APIRouter()
logger = logging.getLogger(__name__)

# Список разрешенных ботов (все остальные будут игнорироваться)
ALLOWED_BOTS = {
    "Retention 25-26",
    "ПГ 2к26 зеро игнор",
    "ElAuthBot",
    "Неоплаты Физика 2к26",
    "Неоплаты Обществознание 5 месяц",
    "Неоплаты Химия",
    "Неоплаты Литература",
    "Неоплаты Проф. мат (Маша)",
    "Неоплаты Биология (Женя)",
    "Неоплаты Проф. мат (Саша)",
    "Неоплаты Биология (Геля)",
    "Неоплаты Информатика 5 месяц",
}


@router.post("/webhook/salebot")
async def salebot_webhook(webhook: SalebotWebhook, request: Request) -> dict[str, str | bool]:
    """
    Webhook для входящих сообщений от Salebot.

    Быстро добавляет задачу в Redis очередь и возвращает 200 OK.
    Воркер обработает сообщение асинхронно.

    Args:
        webhook: Данные от Salebot
        request: FastAPI Request

    Returns:
        Статус обработки
    """
    try:
        # Проверяем что сообщение от клиента
        if not webhook.is_from_client:
            logger.info("Message from bot, ignoring: %s", webhook.id)
            return {"status": "ignored", "reason": "message_from_bot"}

        # Игнорируем служебные события (вход/выход из групповых чатов)
        if webhook.message in ("new_chat_member", "left_chat_member"):
            logger.info(
                "Service event ignored: platform_id=%s, bot=%s, event=%s",
                webhook.platform_id,
                webhook.bot_name,
                webhook.message,
            )
            return {"status": "ignored", "reason": "service_event"}

        # Игнорируем неразрешенных ботов (только ALLOWED_BOTS разрешены)
        if webhook.bot_name not in ALLOWED_BOTS:
            logger.info(
                "Unknown bot ignored: platform_id=%s, bot=%s, message=%s",
                webhook.platform_id,
                webhook.bot_name,
                webhook.message[:50] if len(webhook.message) > 50 else webhook.message,
            )
            return {"status": "ignored", "reason": "unknown_bot"}

        logger.info(
            "Salebot webhook: platform_id=%s, bot=%s, message=%s",
            webhook.platform_id,
            webhook.bot_name,
            webhook.message[:50] if len(webhook.message) > 50 else webhook.message,
        )

        # Добавляем задачу в очередь (быстро!)
        logger.info("Pushing salebot task to queue: %s:%s", webhook.platform_id, webhook.bot_name)
        
        await push_task(
            "salebot_message",
            {
                "platform_id": webhook.platform_id,
                "bot_name": webhook.bot_name,
                "salebot_client_id": webhook.salebot_client_id,
                "client_name": webhook.client.name,
                "message_text": webhook.message,
                "tg_username": webhook.tg_username,
            },
        )

        logger.info("Salebot message queued: platform_id=%s", webhook.platform_id)
        return {"status": "ok", "queued": True}

    except Exception as e:
        logger.error("Error queuing Salebot webhook: %s", e, exc_info=True)
        # Возвращаем 200 даже при ошибке (чтобы Salebot не повторял запрос)
        return {"status": "error", "detail": str(e)}
