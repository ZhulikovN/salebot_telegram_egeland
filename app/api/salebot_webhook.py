"""Webhook endpoint для Salebot."""
import logging

from fastapi import APIRouter, Request

from app.models.salebot import SalebotWebhook
from app.workers.queue import push_task

router = APIRouter()
logger = logging.getLogger(__name__)

# # Список разрешенных ботов (все остальные будут игнорироваться)
# ALLOWED_BOTS = {
#     "test_el_salebot",
# }


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

        # Нормализуем tg_username — убираем @ если есть
        tg_username = webhook.tg_username
        if tg_username and tg_username.startswith("@"):
            tg_username = tg_username[1:]

        # Имя клиента: если нет — используем tg_username
        client_name = webhook.client.name or tg_username or str(webhook.platform_id)

        msg_preview = (webhook.message or "")[:50]
        logger.info(
            "Salebot webhook: platform_id=%s, bot=%s, message=%r, attachments=%s",
            webhook.platform_id,
            webhook.bot_name,
            msg_preview,
            webhook.attachments,
        )
        # logger.info("Salebot webhook FULL PAYLOAD: %s", webhook.model_dump())

        await push_task(
            "salebot_message",
            {
                "platform_id": webhook.platform_id,
                "bot_name": webhook.bot_name,
                "salebot_client_id": webhook.salebot_client_id,
                "client_name": client_name,
                "message_text": webhook.message,
                "attachments": webhook.attachments or [],
                "tg_username": tg_username,
            },
        )

        logger.info("Salebot message queued: platform_id=%s", webhook.platform_id)
        return {"status": "ok", "queued": True}

    except Exception as e:
        logger.error("Error queuing Salebot webhook: %s", e, exc_info=True)
        return {"status": "error", "detail": str(e)}
