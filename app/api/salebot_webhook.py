"""Webhook endpoint для Salebot."""
import logging

from fastapi import APIRouter, Request

from app.models.salebot import SalebotWebhook
from app.workers.queue import push_task

router = APIRouter()
logger = logging.getLogger(__name__)


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
        # Диагностический лог: показывает ВСЁ что приходит от Salebot (is_input=1 и is_input=0)
        logger.info(
            "SALEBOT_RAW: id=%s, is_input=%s, message_id=%s, platform_id=%s, bot=%s, message=%r",
            webhook.id,
            webhook.is_input,
            webhook.message_id,
            webhook.platform_id,
            webhook.bot_name,
            (webhook.message or "")[:80],
        )

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

        is_bot = not webhook.is_from_client

        # Эхо сообщения менеджера: is_input=0 и message_id=None.
        # Автосообщения бота из воронки имеют message_id=<число>.
        # Менеджерские эхи имеют message_id=None — игнорируем их.
        if is_bot and webhook.message_id is None:
            logger.info(
                "Manager echo suppressed: platform_id=%s, bot=%s, message=%r",
                webhook.platform_id,
                webhook.bot_name,
                (webhook.message or "")[:60],
            )
            return {"status": "ignored", "reason": "manager_echo"}

        # Сообщения бота без текста не несут смысла в amoCRM
        if is_bot and not webhook.message:
            logger.debug("Bot message without text, ignoring: %s", webhook.id)
            return {"status": "ignored", "reason": "bot_message_no_text"}

        msg_preview = (webhook.message or "")[:50]
        logger.info(
            "Salebot webhook: platform_id=%s, bot=%s, is_bot=%s, message=%r",
            webhook.platform_id,
            webhook.bot_name,
            is_bot,
            msg_preview,
        )

        if not is_bot:
            logger.info(
                "UTM_TRACE: platform_id=%s, bot=%s, utm=%s",
                webhook.platform_id,
                webhook.bot_name,
                webhook.utm_data,
            )

        queue_name = "tasks:bot" if is_bot else "tasks:priority"
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
                "utm_data": webhook.utm_data,
                "is_bot_message": is_bot,
            },
            queue_name=queue_name,
        )

        logger.info(
            "Salebot message queued: platform_id=%s, is_bot=%s",
            webhook.platform_id,
            is_bot,
        )
        return {"status": "ok", "queued": True}

    except Exception as e:
        logger.error("Error queuing Salebot webhook: %s", e, exc_info=True)
        return {"status": "error", "detail": str(e)}
