"""Webhook endpoint для raw Telegram updates от сервера коллеги.

Коллега держит Telegram webhook для el_oge_diagnostika_bot (и потенциально
других ботов). При каждом входящем апдейте он делает POST сюда с полным
raw Telegram update JSON. Мы парсим его и создаём/обновляем deal+contact+chat
в AmoCRM — без участия Salebot как посредника.

Аутентификация: заголовок X-Webhook-Secret.
Если TELEGRAM_WEBHOOK_SECRET не задан в .env — endpoint недоступен (403).
"""
import logging
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request

from app.config.bot_routing import LOW_PRIORITY_BOT_NAMES
from app.settings import settings
from app.workers.queue import LOW_PRIORITY_QUEUE, push_task

router = APIRouter()
logger = logging.getLogger(__name__)

# sentinel: нет Salebot-клиента, сообщение пришло через raw Telegram webhook.
# conversation_manager проверяет это значение и пропускает Salebot-специфичные
# вызовы (save_variables, send_message).
_NO_SALEBOT_CLIENT_ID = 0


@router.post("/webhook/telegram/{bot_name}")
async def telegram_webhook(
    bot_name: str,
    request: Request,
    x_webhook_secret: str | None = Header(default=None),
) -> dict[str, str | bool]:
    """
    Принять raw Telegram update от сервера коллеги.

    Извлекает platform_id, текст/callback_data, username и имя клиента.
    Кидает задачу salebot_message в очередь с salebot_client_id=0.
    """
    if not settings.TELEGRAM_WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="Endpoint disabled")
    if x_webhook_secret != settings.TELEGRAM_WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="Invalid secret")

    try:
        update: dict[str, Any] = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    try:
        sender: dict[str, Any] | None = None
        message_text: str | None = None

        if "message" in update:
            msg = update["message"]
            sender = msg.get("from") or {}
            message_text = msg.get("text") or msg.get("caption")
        elif "callback_query" in update:
            cq = update["callback_query"]
            sender = cq.get("from") or {}
            # callback data — это и есть текст нажатой кнопки
            message_text = cq.get("data")

        if not sender:
            logger.info(
                "TG_WEBHOOK: no sender in update, bot=%s, update_id=%s — skipping",
                bot_name,
                update.get("update_id"),
            )
            return {"status": "ignored", "reason": "no_sender"}

        platform_id = str(sender.get("id", ""))
        if not platform_id or platform_id == "0":
            return {"status": "ignored", "reason": "no_platform_id"}

        tg_username: str | None = sender.get("username")
        if tg_username and tg_username.startswith("@"):
            tg_username = tg_username[1:]

        first_name = sender.get("first_name") or ""
        last_name = sender.get("last_name") or ""
        client_name = f"{first_name} {last_name}".strip() or tg_username or platform_id

        logger.info(
            "TG_WEBHOOK: bot=%s, platform_id=%s, update_id=%s, text=%r",
            bot_name,
            platform_id,
            update.get("update_id"),
            (message_text or "")[:60],
        )

        queue_name = LOW_PRIORITY_QUEUE if bot_name in LOW_PRIORITY_BOT_NAMES else "tasks:priority"

        await push_task(
            "salebot_message",
            {
                "platform_id": platform_id,
                "bot_name": bot_name,
                "salebot_client_id": _NO_SALEBOT_CLIENT_ID,
                "client_name": client_name,
                "message_text": message_text,
                "attachments": [],
                "tg_username": tg_username,
                "utm_data": {},
                "is_bot_message": False,
            },
            queue_name=queue_name,
        )

        logger.info("TG_WEBHOOK queued: bot=%s, platform_id=%s", bot_name, platform_id)
        return {"status": "ok", "queued": True}

    except Exception as e:
        logger.error(
            "Error processing TG webhook: bot=%s, error=%s", bot_name, e, exc_info=True
        )
        return {"status": "error", "detail": str(e)}
